"""HA core + SRE review 29.10: reconnects spread out, HA's KNX internals read in one place, Repairs issues for what the
owner must know, stores that survive a version change, a reload that leaves nothing doubled, big frames off the loop."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

import custom_components.hauflo as hauflo
from custom_components.hauflo import issues
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN
from custom_components.hauflo.storage import HaufloStore

RUN = hauflo.Agent.run  # the real link loop (the fixture idles it)


async def _idle(self):
    return None


@pytest.fixture
async def entry(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "top-secret"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


def _issue(hass: HomeAssistant, issue_id: str):
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


# --- 1. reconnect storm ------------------------------------------------------------------------------------------------
def test_reconnect_wait_is_spread_and_a_restart_hint_waits_1_to_30_s() -> None:
    assert hauflo._next_wait(20, False, lambda a, b: a) == 10
    assert hauflo._next_wait(20, False, lambda a, b: b) == 30
    assert hauflo._next_wait(60, True, lambda a, b: a) == 1
    assert hauflo._next_wait(60, True, lambda a, b: b) == 30
    waits = {round(hauflo._next_wait(10, False), 3) for _ in range(50)}
    assert len(waits) > 10 and all(5 <= w <= 15 for w in waits)
    assert all(1 <= hauflo._next_wait(5, True) <= 30 for _ in range(50))


class _WS:
    def __init__(self, frames, close_code=1000):
        self._msgs = [SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(f)) for f in frames]
        self.close_code = close_code
        self.closed = True  # nothing is sent back; the status loop ends at once

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._msgs:
            raise StopAsyncIteration
        return self._msgs.pop(0)

    async def send_str(self, _data):
        return None

    async def close(self):
        return None


class _Session:
    def __init__(self, ws):
        self.ws = ws

    def ws_connect(self, *_a, **_k):
        ws = self.ws

        class _Ctx:
            async def __aenter__(self):
                return ws

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


async def test_after_a_reconnect_hint_the_knock_back_is_spread(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    agent.session = _Session(_WS([{"t": "welcome", "minVersion": "0.1.0"}, {"t": "reconnect"}]))
    seen = []

    def wait(delay, hinted, *_a):
        seen.append((delay, hinted))
        agent.stopped = True
        return 0

    with patch("custom_components.hauflo._next_wait", wait):
        await RUN(agent)
    assert seen == [(5, True)]
    # Hauflo closing with 1012 «service restart» is the same hint, without the frame.
    agent.stopped, seen[:] = False, []
    agent.session = _Session(_WS([], close_code=1012))
    with patch("custom_components.hauflo._next_wait", wait):
        await RUN(agent)
    assert seen == [(5, True)]
    agent.stopped, seen[:] = False, []
    agent.session = _Session(_WS([], close_code=1006))
    with patch("custom_components.hauflo._next_wait", wait):
        await RUN(agent)
    assert seen == [(5, False)]


async def test_unlinked_in_hauflo_is_a_repairs_issue_and_welcome_clears_it(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    agent.session = _Session(_WS([], close_code=4003))
    task = hass.async_create_task(RUN(agent))
    for _ in range(50):
        await asyncio.sleep(0)
        if _issue(hass, issues.UNLINKED):
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _issue(hass, issues.UNLINKED).severity is ir.IssueSeverity.ERROR
    agent.stopped = False
    agent.session = _Session(_WS([{"t": "welcome", "minVersion": "0.1.0", "latest": "0.1.0"}]))
    with patch("custom_components.hauflo._next_wait", lambda *_a: setattr(agent, "stopped", True) or 0):
        await RUN(agent)
    assert _issue(hass, issues.UNLINKED) is None
    assert _issue(hass, issues.OUTDATED) is None


async def test_an_agent_older_than_hauflo_needs_is_a_repairs_issue(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    agent.session = _Session(_WS([{"t": "welcome", "minVersion": "99.0.0", "latest": "99.1.0"}]))
    with patch("custom_components.hauflo._next_wait", lambda *_a: setattr(agent, "stopped", True) or 0):
        await RUN(agent)
    issue = _issue(hass, issues.OUTDATED)
    assert issue is not None and issue.translation_placeholders == {"version": hauflo.VERSION, "minimum": "99.0.0", "latest": "99.1.0"}
    issues.sync_version(hass, hauflo.VERSION, hauflo.VERSION, None)
    assert _issue(hass, issues.OUTDATED) is None


def test_version_compare() -> None:
    assert issues.older("0.16.14", "0.17.0")
    assert issues.older("0.16.9", "0.16.14")
    assert not issues.older("0.16.14", "0.16.14")
    assert not issues.older("1.0.0", "0.99.99")
    assert not issues.older("0.16.14", None)
    assert not issues.older("0.16.14", "garbage")
    assert not issues.older("0.16.14", {"x": 1})


async def test_link_lost_for_6_hours_is_a_repairs_issue_gone_when_back(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    agent._watch_link(False)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=5))
    await hass.async_block_till_done()
    assert _issue(hass, issues.LINK_LOST) is None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=6, minutes=1))
    await hass.async_block_till_done()
    issue = _issue(hass, issues.LINK_LOST)
    assert issue is not None and issue.translation_placeholders["server"] == "app.hauflo.ru"
    agent._set_linked(True)
    await hass.async_block_till_done()
    assert _issue(hass, issues.LINK_LOST) is None
    # Lost again and unloaded before 6 h: no timer left behind.
    agent._set_linked(False)
    assert agent._lost_timer is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert agent._lost_timer is None


async def test_addresses_waiting_for_the_owner_and_a_degraded_feature_are_in_repairs(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    agent._ask_ranges({"7/7/1"})
    assert _issue(hass, issues.RANGES_PENDING).translation_placeholders == {"ranges": "7/7/1", "count": "1", "project": "—"}
    # The owner ticks it in the options: the issue goes.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"allow_changes": True, "ranges": "", "accept_pending": ["7/7/1"]})
    assert result["type"] == "create_entry"
    assert _issue(hass, issues.RANGES_PENDING) is None
    try:
        hauflo.degrade("test feature", RuntimeError("shape"))
        agent.status()
        assert "test feature" in _issue(hass, issues.DEGRADED_FEATURE).translation_placeholders["features"]
    finally:
        issues.DEGRADED.discard("test feature")
    agent.status()
    assert _issue(hass, issues.DEGRADED_FEATURE) is None or "test feature" not in _issue(hass, issues.DEGRADED_FEATURE).translation_placeholders["features"]


async def test_status_says_the_houses_time_zone(hass: HomeAssistant, entry) -> None:
    """Review 29.10 (defect 9): alarm times go out as ISO with offset; the zone to say them in goes with them."""
    await hass.config.async_update(time_zone="Europe/Moscow")
    assert entry.runtime_data.status()["timeZone"] == "Europe/Moscow"


def test_every_issue_has_its_texts_in_both_languages() -> None:
    import pathlib

    base = pathlib.Path(hauflo.__file__).parent
    for name in ("strings.json", "translations/en.json", "translations/ru.json"):
        texts = json.loads((base / name).read_text(encoding="utf-8"))["issues"]
        assert set(texts) == set(issues.ALL), name
        # A fixable issue says its text in its fix flow only (hassfest: description and fix_flow exclude each other).
        assert all(t["title"] and (t.get("description") or t.get("fix_flow")) for t in texts.values())
    en = json.loads((base / "translations/en.json").read_text(encoding="utf-8"))["issues"]
    ru = json.loads((base / "translations/ru.json").read_text(encoding="utf-8"))["issues"]
    import re

    for k in issues.ALL:
        said = lambda t: (t.get("description") or json.dumps(t.get("fix_flow"), ensure_ascii=False)) + t["title"]  # noqa: E731
        assert set(re.findall(r"\{(\w+)\}", said(en[k]))) == set(re.findall(r"\{(\w+)\}", said(ru[k]))), k


# --- 2. KNX private API in one place -------------------------------------------------------------------------------------
def test_knx_data_by_the_module_key_and_by_the_plain_key() -> None:
    mod = SimpleNamespace(config_store=SimpleNamespace(data={"entities": {"light": {}}}))
    with patch.object(hauflo, "_KNX_KEY", ["knx_module_key"]):
        assert hauflo.knx_data(SimpleNamespace(data={"knx_module_key": mod})) is mod  # KNX_MODULE_KEY
        assert hauflo.knx_data(SimpleNamespace(data={"knx": mod})) is mod  # an older HA's plain key
        assert hauflo.knx_data(SimpleNamespace(data={})) is None
        assert hauflo.knx_data(None) is None
        assert hauflo._knx_store(SimpleNamespace(data={"knx": mod})) == {"light": {}}


def test_a_changed_knx_shape_degrades_never_raises() -> None:
    try:
        hass = SimpleNamespace(data={"knx": SimpleNamespace(config_store=SimpleNamespace(data={"entities": ["not", "a", "dict"]}))})
        assert hauflo._knx_store(hass) is None
        assert "knx entity store" in issues.DEGRADED
        hass2 = SimpleNamespace(data={"knx": SimpleNamespace()})  # no config_store at all
        assert hauflo._knx_store(hass2) is None
        # KNX there but unreadable: what the integrator's KNX holds is not known (None) — never «nothing» (set()).
        assert hauflo._foreign_knx_writes(hass2, set()) is None
        assert hauflo._foreign_knx_writes(SimpleNamespace(data={}), set()) == set()
        assert hauflo._owned_knx_writes(hass2, {"u"}) == set()
        assert hauflo._knx_scenes_owned(hass2, {"u"}) == {}
    finally:
        issues.DEGRADED.discard("knx entity store")


# --- 4. storage and reload -----------------------------------------------------------------------------------------------
async def test_a_store_saved_by_a_newer_minor_version_still_loads(hass: HomeAssistant, hass_storage) -> None:
    hass_storage["hauflo.t"] = {"version": 1, "minor_version": 3, "key": "hauflo.t", "data": {"a": 1}}
    assert await HaufloStore(hass, "hauflo.t").async_load() == {"a": 1}


def test_every_hauflo_store_is_a_hauflo_store() -> None:
    import pathlib
    import re

    base = pathlib.Path(hauflo.__file__).parent
    for f in base.glob("*.py"):
        if f.name in ("storage.py", "theme.py"):  # theme.py reads HA's own frontend store
            continue
        assert not re.search(r"(?<!Hauflo)Store\(", f.read_text(encoding="utf-8")), f.name


async def test_reload_keeps_listeners_and_stops_guest_access(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "top-secret"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        # HA's Store keeps a «final write» listener while a delayed save is pending: timing, not our leak.
        listeners = lambda: {k: v for k, v in hass.bus.async_listeners().items() if k != "homeassistant_final_write"}  # noqa: E731
        first = listeners()
        agent = entry.runtime_data
        assert agent.guest is not None
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert agent.guest is None
        assert entry.state is ConfigEntryState.NOT_LOADED
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert listeners() == first
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert listeners() == first
        assert entry.state is ConfigEntryState.LOADED


# --- 5. big frames -------------------------------------------------------------------------------------------------------
async def test_a_big_frame_is_parsed_off_the_event_loop(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    calls = []
    real = hass.async_add_executor_job

    def spy(fn, *args):
        calls.append(fn)
        return real(fn, *args)

    with patch.object(hass, "async_add_executor_job", spy):
        assert await agent._parse(json.dumps({"t": "x"})) == {"t": "x"}
        assert calls == []
        big = {"t": "x", "pad": "a" * (hauflo.BIG_FRAME + 10)}
        assert await agent._parse(json.dumps(big)) == big
        assert calls == [json.loads]
        with pytest.raises(ValueError):
            await agent._parse("{" + "x" * (hauflo.BIG_FRAME + 10))
