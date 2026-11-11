"""Р58 — the watchdog of the house's other integrations, adversarial: it must NOT restart what isn't fallen (HA starting,
a short blip, Hauflo itself, KNX, a phone, a helper, a disabled or switched-off entry), must restart what is, slower each
time and never more than the day's cap, one at a time, must say «down» and «back» exactly once, and must survive a
restart of HA and a bad stored file."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState, ConfigFlow
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockModule, mock_config_flow, mock_integration, mock_platform

from custom_components.hauflo import signals, watchdog as W
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

T0 = 1_800_000_000.0
GRACE = W.START_GRACE_S


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
async def wd(hass: HomeAssistant):
    clock = Clock()
    w = W.Watchdog(hass)
    w._now = clock  # type: ignore[method-assign]
    w.clock = clock  # type: ignore[attr-defined]
    told: list[int] = []
    w.on_change = lambda: told.append(1)
    w.told = told  # type: ignore[attr-defined]
    await w.async_start()
    assert w.started_at == T0  # a running HA: «started» at once
    yield w
    w.async_stop()


def _entry(hass: HomeAssistant, domain: str = "weather_x", state: ConfigEntryState = ConfigEntryState.SETUP_ERROR, **kw) -> MockConfigEntry:
    reason = kw.pop("reason", "boom")
    e = MockConfigEntry(domain=domain, title=kw.pop("title", f"{domain} title"), **kw)
    e.add_to_hass(hass)
    e.mock_state(hass, state, reason=reason if state in (ConfigEntryState.SETUP_ERROR, ConfigEntryState.SETUP_RETRY) else None)
    return e


async def _at(hass: HomeAssistant, w: W.Watchdog, t: float) -> None:
    w.clock.t = T0 + t
    w.evaluate()
    await hass.async_block_till_done()


def _issue(hass: HomeAssistant, e) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{W.ISSUE_KIND}_{e.entry_id}")


# --- what is NOT restarted -----------------------------------------------------------------------------------------


async def test_nothing_during_start_grace_nor_before_the_wait(hass: HomeAssistant, wd) -> None:
    e = _entry(hass)
    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)) as rl:
        await _at(hass, wd, GRACE - 1)
        assert wd.recs == {} and not rl.called  # HA starting: nobody judged
        await _at(hass, wd, GRACE)
        assert wd.recs[e.entry_id]["confirmed"] is False and wd.items() == {}  # seen, not yet a fault
        await _at(hass, wd, GRACE + W.WAIT_S["error"] - 1)
        assert wd.items() == {} and not wd.told and not rl.called


async def test_a_short_blip_is_never_told(hass: HomeAssistant, wd) -> None:
    e = _entry(hass)
    await _at(hass, wd, GRACE)
    e.mock_state(hass, ConfigEntryState.LOADED)
    await _at(hass, wd, GRACE + 1)
    assert wd.recs == {} and wd.items() == {} and not wd.told


async def test_never_ours_knx_phone_helper_disabled_or_switched_off(hass: HomeAssistant, wd) -> None:
    mock_integration(hass, MockModule("tpl_x", partial_manifest={"integration_type": "helper"}))
    skipped = [
        _entry(hass, DOMAIN),
        _entry(hass, "knx"),
        _entry(hass, "mobile_app"),
        _entry(hass, "tpl_x"),
        _entry(hass, "off_x"),
        _entry(hass, "dis_x", disabled_by=ConfigEntryDisabler.USER),
        _entry(hass, "nl_x", ConfigEntryState.NOT_LOADED),
        _entry(hass, "sip_x", ConfigEntryState.SETUP_IN_PROGRESS),
        _entry(hass, "mig_x", ConfigEntryState.MIGRATION_ERROR),
    ]
    wd.off = lambda: {"off_x"}
    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)) as rl:
        for t in (GRACE, GRACE + 10_000, GRACE + 100_000):
            await _at(hass, wd, t)
    assert not rl.called and wd.recs == {} and wd.items() == {}
    assert all(e.entry_id not in wd.recs for e in skipped)


async def test_loaded_entry_health_by_its_entities(hass: HomeAssistant, wd) -> None:
    reg = er.async_get(hass)
    empty = _entry(hass, "empty_x", ConfigEntryState.LOADED)  # no entities: nothing to judge by → healthy
    some = _entry(hass, "some_x", ConfigEntryState.LOADED)
    dead = _entry(hass, "dead_x", ConfigEntryState.LOADED)
    for e, n in ((some, 2), (dead, 2)):
        for i in range(n):
            ent = reg.async_get_or_create("sensor", e.domain, f"u{i}", config_entry=e)
            hass.states.async_set(ent.entity_id, STATE_UNAVAILABLE if (e is dead or i == 0) else "21")
    # A disabled entity of the dead one says nothing (it would be «available» if it counted).
    off = reg.async_get_or_create("sensor", "dead_x", "off", config_entry=dead, disabled_by=er.RegistryEntryDisabler.USER)
    hass.states.async_set(off.entity_id, "5")
    assert wd.fault(empty) is None and wd.fault(some) is None
    kind, since = wd.fault(dead)
    last = max(hass.states.get(reg.async_get_entity_id("sensor", "dead_x", f"u{i}")).last_changed.timestamp() for i in range(2))
    assert kind == "unavailable" and since == pytest.approx(last)  # when the LAST one went, not when first seen
    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)) as rl:
        await _at(hass, wd, GRACE)
        # The entities went unavailable at T0-ish (real clock); far past the wait → confirmed at once, judged by last_changed.
        assert set(wd.recs) == {dead.entry_id}
        r = wd.recs[dead.entry_id]
        assert r["kind"] == "unavailable" and r["lastError"] == STATE_UNAVAILABLE
    assert not rl.called or rl.call_args.args == (dead.entry_id,)


# --- restarting, backoff, cap, down, back ------------------------------------------------------------------------


async def test_backoff_cap_down_and_back(hass: HomeAssistant, wd) -> None:
    e = _entry(hass)
    reloads: list[float] = []

    async def reload(eid: str) -> bool:
        reloads.append(wd.clock.t - T0)
        return False  # didn't help

    with patch.object(hass.config_entries, "async_reload", side_effect=reload):
        t = GRACE
        await _at(hass, wd, t)
        confirm = GRACE + W.WAIT_S["error"]
        await _at(hass, wd, confirm)
        assert wd.items()[f"weather_x/{e.entry_id}"]["state"] == W.RESTARTING and wd.told == [1]
        assert reloads == []  # the first only after first_s
        # Walk minute by minute through two days.
        t = confirm
        while t < confirm + 2 * W.WINDOW_S:
            t += W.T["tick_s"]
            await _at(hass, wd, t)
            if len(reloads) == W.ATTEMPTS_MAX:
                cap_at = t
                break
        rec = wd.recs[e.entry_id]
        # Pauses: first_s, ×factor, at most max_s (each reload measured from the one before).
        gaps = [b - a for a, b in zip([confirm, *reloads], reloads)]
        for i, g in enumerate(gaps, 1):
            assert W.backoff(i) <= g < W.backoff(i) + W.T["tick_s"] + 1, (i, g)
        assert rec["state"] == W.DOWN and rec["attempts"] == W.ATTEMPTS_MAX
        issue = _issue(hass, e)
        assert issue is not None and issue.translation_key == W.ISSUE_KIND and issue.translation_placeholders["name"] == "weather_x title"
        # The day's cap: nothing more until the first try leaves the window.
        while t + W.MAX_S < reloads[0] + W.WINDOW_S:
            t += W.MAX_S
            await _at(hass, wd, t)
        assert len(reloads) == W.ATTEMPTS_MAX
        await _at(hass, wd, reloads[0] + W.WINDOW_S + 1)
        assert len(reloads) == W.ATTEMPTS_MAX + 1  # a new day: one more try (backoff at its max)
        assert cap_at > reloads[0]
        # Back by itself (HA's own retry, a fix by hand): «ok» with the time, the issue gone, told once.
        told = len(wd.told)
        e.mock_state(hass, ConfigEntryState.LOADED)
        await hass.async_block_till_done()
        item = wd.items()[f"weather_x/{e.entry_id}"]
        assert item["state"] == W.OK and item["recoveredAt"] and item["since"] is None and item["attempts"] == 0
        assert _issue(hass, e) is None and len(wd.told) == told + 1
        await _at(hass, wd, reloads[0] + W.WINDOW_S + 2)
        assert len(wd.told) == told + 1  # said once
        await _at(hass, wd, reloads[0] + W.WINDOW_S + 2 + W.KEEP_RECOVERED_S + 1)
        assert wd.items() == {}  # an old «back» forgotten


async def test_down_after_n_failed_restarts_before_the_cap(hass: HomeAssistant, wd) -> None:
    e = _entry(hass)
    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=False)) as rl:
        t = GRACE
        await _at(hass, wd, t)
        while rl.call_count < W.DOWN_AFTER:
            t += W.T["tick_s"]
            await _at(hass, wd, t)
        assert wd.recs[e.entry_id]["state"] == W.RESTARTING  # the last one not judged before it settles
        await _at(hass, wd, t + W.SETTLE_S + W.SPACING_S)
        assert wd.recs[e.entry_id]["state"] == W.DOWN and _issue(hass, e) is not None
        assert rl.call_count == W.DOWN_AFTER


async def test_a_restart_that_helps(hass: HomeAssistant, wd) -> None:
    e = _entry(hass, state=ConfigEntryState.SETUP_RETRY)

    async def reload(eid: str) -> bool:
        e.mock_state(hass, ConfigEntryState.LOADED)
        return True

    with patch.object(hass.config_entries, "async_reload", side_effect=reload) as rl:
        await _at(hass, wd, GRACE)
        await _at(hass, wd, GRACE + W.WAIT_S["error"])
        assert wd.items() == {}  # setup_retry: HA retries itself — waits retry_wait_s, not error_wait_s
        await _at(hass, wd, GRACE + W.WAIT_S["retry"])
        assert wd.recs[e.entry_id]["state"] == W.RESTARTING
        await _at(hass, wd, GRACE + W.WAIT_S["retry"] + W.FIRST_S)
        assert rl.call_count == 1
        item = wd.items()[f"weather_x/{e.entry_id}"]
        assert item["state"] == W.OK and item["recoveredAt"] is not None


async def test_one_reload_at_a_time_with_spacing(hass: HomeAssistant, wd) -> None:
    a, b = _entry(hass, "aa_x"), _entry(hass, "bb_x")
    started: list[str] = []

    async def reload(eid: str) -> bool:
        started.append(eid)
        assert wd.busy == eid
        wd.evaluate()  # a tick while reloading: never a second reload
        return False

    with patch.object(hass.config_entries, "async_reload", side_effect=reload):
        await _at(hass, wd, GRACE)
        t = GRACE + W.WAIT_S["error"] + W.FIRST_S
        await _at(hass, wd, t)
        assert len(started) == 1 and wd.busy is None
        await _at(hass, wd, t + W.SPACING_S - 1)
        assert len(started) == 1  # the spacing
        await _at(hass, wd, t + W.SPACING_S)
        assert sorted(started) == sorted([a.entry_id, b.entry_id])


async def test_a_reload_that_raises_or_hangs(hass: HomeAssistant, wd) -> None:
    e = _entry(hass, reason=None)  # HA says no reason: the reload's own error is what the status shows
    with patch.object(hass.config_entries, "async_reload", AsyncMock(side_effect=RuntimeError("token expired"))):
        await _at(hass, wd, GRACE)
        await _at(hass, wd, GRACE + W.WAIT_S["error"] + W.FIRST_S)
    assert wd.recs[e.entry_id]["lastError"] == "token expired" and wd.busy is None
    with patch.object(hass.config_entries, "async_reload", AsyncMock(side_effect=ValueError())), patch.object(W, "RELOAD_TIMEOUT_S", 0):
        await _at(hass, wd, GRACE + W.WAIT_S["error"] + W.FIRST_S + W.SETTLE_S + W.backoff(2))
    assert wd.recs[e.entry_id]["attempts"] == 2 and wd.busy is None


async def test_stop_cancels_a_running_reload(hass: HomeAssistant, wd) -> None:
    import asyncio

    _entry(hass)
    gate = asyncio.Event()

    async def reload(eid: str) -> bool:
        await gate.wait()
        return True

    with patch.object(hass.config_entries, "async_reload", side_effect=reload):
        await _at(hass, wd, GRACE)
        wd.clock.t = T0 + GRACE + W.WAIT_S["error"] + W.FIRST_S
        wd.evaluate()
        await asyncio.sleep(0)
        assert wd.busy is not None
        wd.async_stop()
        await hass.async_block_till_done()
    assert wd.busy is None and wd._unsubs == []


async def test_gone_or_switched_off_is_forgotten_with_its_issue(hass: HomeAssistant, wd) -> None:
    a, b = _entry(hass, "aa_x"), _entry(hass, "bb_x")
    off: set[str] = set()
    wd.off = lambda: off
    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=False)):
        t = GRACE
        await _at(hass, wd, t)
        while not all(wd.recs[x.entry_id]["state"] == W.DOWN for x in (a, b)):
            t += W.T["tick_s"]
            await _at(hass, wd, t)
    assert _issue(hass, a) and _issue(hass, b)
    await hass.config_entries.async_remove(a.entry_id)
    off.add("bb_x")
    told = len(wd.told)
    await _at(hass, wd, t + 1)
    assert wd.recs == {} and _issue(hass, a) is None and _issue(hass, b) is None and len(wd.told) == told + 1


async def test_entry_changed_signal_only_for_watched_loaded(hass: HomeAssistant, wd) -> None:
    e = _entry(hass)
    with patch.object(wd, "evaluate") as ev:
        e.mock_state(hass, ConfigEntryState.LOADED)
        assert not ev.called  # not one the watchdog keeps
        wd.recs[e.entry_id] = W._new(e)
        e.mock_state(hass, ConfigEntryState.SETUP_ERROR)
        assert not ev.called
        e.mock_state(hass, ConfigEntryState.LOADED)
        assert ev.called


# --- kept across an HA restart ------------------------------------------------------------------------------------


async def test_kept_and_bad_records_dropped(hass: HomeAssistant, wd) -> None:
    e = _entry(hass)
    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=False)):
        t = GRACE
        await _at(hass, wd, t)
        while wd.recs[e.entry_id]["state"] != W.DOWN:
            t += W.T["tick_s"]
            await _at(hass, wd, t)
    await wd.async_flush()
    data = await wd.store.async_load()
    data["recs"].update(
        {
            "bad1": "x",
            "bad2": {"domain": 5, "state": "ok"},
            "bad3": {"domain": "d", "state": "weird"},
            "bad4": {"domain": "d", "state": "ok", "kind": "nope"},
            "bad5": {"domain": "d", "state": "down", "confirmed": True},  # confirmed without since / kind
            "ok1": {"domain": "d", "state": "ok", "tries": "x", "attempts": True, "lastError": "e" * 999, "recoveredAt": 5},
        }
    )
    await wd.store.async_save(data)
    w2 = W.Watchdog(hass)
    await w2.async_start()
    w2.async_stop()
    assert set(w2.recs) == {e.entry_id, "ok1"}
    assert w2.recs[e.entry_id]["state"] == W.DOWN and w2.recs[e.entry_id]["attempts"] == wd.recs[e.entry_id]["attempts"]
    assert w2.recs["ok1"]["tries"] == [] and w2.recs["ok1"]["attempts"] == 0 and len(w2.recs["ok1"]["lastError"]) == W.ERROR_LEN
    # An open «down» is in the status right after the restart (the cloud keeps the alarm, no false «back»).
    assert w2.items()[f"weather_x/{e.entry_id}"]["state"] == W.DOWN


# --- the integrator's switch: signals transport --------------------------------------------------------------------


def test_watchdog_switch_on_the_wire() -> None:
    assert signals.watchdog_in({"off": ["zz", "aa", "aa"]}) == {"off": ["aa", "zz"]}
    for bad in (None, [], {"off": "x"}, {"off": ["Bad Domain"]}, {"off": [1]}, {"off": [], "x": 1}, {"off": ["a"] * (signals.WATCHDOG_OFF_MAX + 1)}):
        assert signals.watchdog_in(bad) is None, bad
    got, dropped = signals.signals_in({"scopes": [], "entities": {}, "findings": {}, "watchdog": {"off": ["tuya"]}})
    assert got["watchdog"] == {"off": ["tuya"]} and dropped == 0
    got, dropped = signals.signals_in({"watchdog": {"off": "tuya"}})
    assert "watchdog" not in got and dropped == 1
    assert signals.signals_in({"watchdogs": {}}) is None


async def test_watchdog_off_follows_the_push(hass: HomeAssistant) -> None:
    assert signals.watchdog_off(hass) == set()
    s = await signals.async_setup(hass)
    assert signals.watchdog_off(hass) == set()
    s.apply({"scopes": [], "entities": {}, "findings": {}, "watchdog": {"off": ["tuya"]}}, "0123456789abcdef")
    assert signals.watchdog_off(hass) == {"tuya"}
    await s.store.async_save(s._data())
    s2 = signals.Signals(hass)
    await s2.async_load()
    assert s2.cloud["watchdog"] == {"off": ["tuya"]}  # kept across a restart
    # A push without it: every integration watched again (never kept from an earlier push).
    s.apply({"scopes": [], "entities": {}, "findings": {}}, "0123456789abcdee")
    assert signals.watchdog_off(hass) == set()


# --- in the agent ------------------------------------------------------------------------------------------------


@pytest.fixture
async def agent_entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_in_the_agent_status_features_and_unload(hass: HomeAssistant, agent_entry) -> None:
    agent = agent_entry.runtime_data
    wd = agent.watchdog
    assert wd is not None and wd.on_change == agent.status_now
    st = agent.status()
    assert W.FEATURE in st["features"] and st["integrations"] == {}
    e = _entry(hass)
    wd.recs[e.entry_id] = {**W._new(e), "state": W.DOWN, "confirmed": True, "kind": "error", "since": T0, "attempts": 3}
    assert agent.status()["integrations"][f"weather_x/{e.entry_id}"]["state"] == W.DOWN
    assert wd.off() == set()
    assert await hass.config_entries.async_unload(agent_entry.entry_id)
    await hass.async_block_till_done()
    assert agent.watchdog is None and wd._unsubs == []
    assert e.entry_id in (await wd.store.async_load())["recs"]  # flushed on unload


async def test_a_failing_start_degrades_never_stops_the_agent(hass: HomeAssistant) -> None:
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle), patch.object(W.Watchdog, "async_start", side_effect=RuntimeError("x")):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.runtime_data.watchdog is None and entry.runtime_data.status()["integrations"] == {}


# --- end to end: a real integration, a real reload ----------------------------------------------------------------


async def test_real_flaky_integration_is_brought_back(hass: HomeAssistant, wd) -> None:
    """A real config entry of a real (mock) integration: its setup fails (not ready), HA's own retry keeps failing; the
    watchdog's reload goes through HA's real reload path and, the device back, the entry is loaded and told «back»."""
    up = {"ok": False, "calls": 0}

    async def setup_entry(hass, entry):
        up["calls"] += 1
        if not up["ok"]:
            raise ConfigEntryNotReady("device offline")
        return True

    mock_integration(hass, MockModule("flaky_x", async_setup_entry=setup_entry, async_unload_entry=AsyncMock(return_value=True)))
    mock_platform(hass, "flaky_x.config_flow", None)

    class Flow(ConfigFlow):
        VERSION = 1

    e = MockConfigEntry(domain="flaky_x", title="Flaky")
    e.add_to_hass(hass)
    with mock_config_flow("flaky_x", Flow):
        await hass.config_entries.async_setup(e.entry_id)
        await hass.async_block_till_done()
        await _flaky_rest(hass, wd, e, up)


async def _flaky_rest(hass: HomeAssistant, wd, e, up) -> None:
    assert e.state is ConfigEntryState.SETUP_RETRY
    await _at(hass, wd, GRACE)
    await _at(hass, wd, GRACE + W.WAIT_S["retry"])
    assert wd.items()[f"flaky_x/{e.entry_id}"]["state"] == W.RESTARTING
    assert "device offline" in wd.items()[f"flaky_x/{e.entry_id}"]["lastError"]
    up["ok"] = True
    calls = up["calls"]
    await _at(hass, wd, GRACE + W.WAIT_S["retry"] + W.FIRST_S)
    assert up["calls"] == calls + 1 and e.state is ConfigEntryState.LOADED
    await _at(hass, wd, GRACE + W.WAIT_S["retry"] + W.FIRST_S + 1)
    assert wd.items()[f"flaky_x/{e.entry_id}"]["state"] == W.OK


async def test_cap_says_down_even_when_down_after_is_larger(hass: HomeAssistant, wd) -> None:
    """tuning with down_after_attempts above the day's cap: the cap itself says «down» (never silent for a day)."""
    e = _entry(hass)
    with patch.object(W, "DOWN_AFTER", W.ATTEMPTS_MAX + 5), patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=False)) as rl:
        t = GRACE
        while rl.call_count < W.ATTEMPTS_MAX or wd.recs[e.entry_id]["state"] != W.DOWN:
            t += W.T["tick_s"]
            await _at(hass, wd, t)
            assert t < GRACE + W.WINDOW_S
    assert rl.call_count == W.ATTEMPTS_MAX and _issue(hass, e) is not None


async def test_the_tick_runs_by_itself(hass: HomeAssistant, wd) -> None:
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    with patch.object(wd, "evaluate") as ev:
        async_fire_time_changed(hass, dt_util.utcnow() + W.TICK + timedelta(seconds=1))
        await hass.async_block_till_done()
    assert ev.called
