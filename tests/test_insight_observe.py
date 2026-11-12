"""«Фоновые наблюдения» (0.34 п.9): the integrator switches the whole diagnosis engine off — no listener, no tick, nothing
computed or listed; on again — it starts from the saved norms like after a restart. The switch comes with the site's
signals (`observe`), only from a cloud that knows the agent takes it (insight.OBSERVE_FEATURE); the «Анализ» header's
summary (devices, since when, habits) comes with the first page."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import signals
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN
from custom_components.hauflo.insight import OBSERVE_FEATURE, STORE_KEY, TICK, Insights

FLOOR = "climate.floor"
T0 = 1_800_000_000.0


def listeners(hass: HomeAssistant) -> int:
    return hass.bus.async_listeners().get(EVENT_STATE_CHANGED, 0)


@pytest.fixture
async def ins(hass: HomeAssistant):
    er.async_get(hass).async_get_or_create("climate", "test", "floor", suggested_object_id="floor")
    engine = Insights(hass, lambda eid: True, lambda: None)
    engine.clock = lambda: T0
    yield engine
    engine.async_stop()


class Timers:
    """async_track_time_interval in insight, recorded: the live ones and a «tick» of every live one — deterministic, no
    wall clock (the frozen-clock firing was flaky in a long run)."""

    def __init__(self) -> None:
        self.live: list[list] = []

    def track(self, _hass, action, interval):
        assert interval == timedelta(seconds=TICK)
        t = [action]
        self.live.append(t)
        return lambda: self.live.remove(t)

    def fire(self) -> None:
        for (action,) in list(self.live):
            action(None)


async def test_off_stops_the_listener_and_the_tick_on_resumes_them(hass: HomeAssistant, ins: Insights, hass_storage):
    state = {"on": True}
    ins.on = lambda: state["on"]
    base = listeners(hass)
    ticks, events, changes = [], [], []
    ins._tick = lambda _now=None: ticks.append(1)  # the instance's own, bound at subscribe
    ins.on_change = lambda: changes.append(1)
    seen = ins.detectors[0].on_event
    timers = Timers()
    with patch("custom_components.hauflo.insight.async_track_time_interval", timers.track):
        await ins.async_start()
        assert ins.running and listeners(hass) == base + 1 and len(timers.live) == 1
        timers.fire()
        assert len(ticks) == 1
        ins.detectors[0].norms[FLOOR] = {"heat": [[2.0, 2.0]]}  # learned so far
        with patch.object(type(ins.detectors[0]), "on_event", lambda self, *a: events.append(a)):
            hass.states.async_set(FLOOR, "heat", {"current_temperature": 20, "temperature": 22})
            await hass.async_block_till_done()
            assert len(events) == 1

            # Off: the bus listener and the tick gone, nothing fed, nothing listed, the norms saved as they are.
            state["on"] = False
            ins.switch()
            assert not ins.running and listeners(hass) == base and timers.live == [] and changes == [1]
            assert ins.items() == [] and ins.summary()["on"] is False
            hass.states.async_set(FLOOR, "heat", {"current_temperature": 21, "temperature": 22})
            await hass.async_block_till_done()
            timers.fire()
            assert len(events) == 1 and len(ticks) == 1
            await hass.async_block_till_done()
            assert hass_storage[STORE_KEY]["data"]["climate"]["norms"][FLOOR] == {"heat": [[2.0, 2.0]]}
            ins.switch()  # the same again: nothing changes, nothing said
            assert changes == [1] and listeners(hass) == base and timers.live == []

        # On: like after a restart — the detectors anew from the saved norms, the listener and the tick back.
        state["on"] = True
        ins.switch()
        assert ins.running and listeners(hass) == base + 1 and len(timers.live) == 1 and changes == [1, 1]
        assert ins.detectors[0] is not seen.__self__ and ins.detectors[0].norms[FLOOR]["heat"]
        ins.switch()
        assert listeners(hass) == base + 1 and len(timers.live) == 1  # never subscribed twice
        timers.fire()
        assert len(ticks) == 2


async def test_started_off_never_subscribes(hass: HomeAssistant, ins: Insights):
    base = listeners(hass)
    ins.on = lambda: False
    await ins.async_start()
    assert not ins.running and listeners(hass) == base and ins.items() == []


async def test_an_unreadable_switch_is_on(hass: HomeAssistant, ins: Insights):
    ins.on = lambda: 1 / 0
    await ins.async_start()
    assert ins.running


def test_observe_in_the_signals_body():
    base = {"scopes": [], "entities": {}, "findings": {}}
    got, dropped = signals.signals_in({**base, "observe": {"on": False}})
    assert got["observe"] == {"on": False} and dropped == 0
    for bad in ({"on": "false"}, {"on": False, "x": 1}, "off", [False], {"on": None}, 0):
        got, dropped = signals.signals_in({**base, "observe": bad})
        assert "observe" not in got and dropped == 1, bad
    got, dropped = signals.signals_in(base)
    assert "observe" not in got


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_pushed_with_the_signals_switches_the_agents_engine(hass: HomeAssistant, entry, hass_client):
    agent = entry.runtime_data
    engine = agent.insights
    engine.detectors[0].norms[FLOOR] = {"heat": [[2.0, 2.0]]}
    admin = await hass_client()
    push = lambda observe, h: admin.post("/api/hauflo/signals", json={"signals": {"scopes": [], "entities": {}, "findings": {}, **({"observe": observe} if observe is not None else {})}, "hash": h})
    assert OBSERVE_FEATURE in agent.status()["features"]
    assert signals.observing(hass) and engine.running
    got = await (await admin.get("/api/hauflo/insights")).json()
    assert got["total"] == 1 and "off" not in got
    assert got["summary"]["on"] is True and got["summary"]["devices"] == 1
    assert got["summary"]["habits"]["on"] is True and got["summary"]["habits"]["found"] == 0
    assert "summary" not in await (await admin.get("/api/hauflo/insights", params={"offset": "1"})).json()
    base = listeners(hass)

    assert (await push({"on": False}, "a" * 16)).status == 200
    await hass.async_block_till_done()
    assert not signals.observing(hass) and not engine.running and listeners(hass) == base - 1
    got = await (await admin.get("/api/hauflo/insights")).json()
    assert got["off"] is True and got["items"] == [] and got["total"] == 0 and got["summary"]["on"] is False
    assert agent.status()["insights"] == []

    # A bad value is dropped: never «off» by mistake, never kept from the push before either (absent = on).
    body = await (await push({"on": "no"}, "b" * 16)).json()
    assert body["dropped"] == 1 and signals.observing(hass) and engine.running
    await push({"on": False}, "c" * 16)
    assert not engine.running
    await push(None, "d" * 16)
    assert signals.observing(hass) and engine.running and listeners(hass) == base
    got = await (await admin.get("/api/hauflo/insights")).json()
    assert "off" not in got and got["total"] == 1


async def test_summary_counts_devices_and_the_first_seen(hass: HomeAssistant, ins: Insights):
    await ins.async_start()
    assert ins.summary() == {"on": True, "devices": 0, "since": None}
    ins.detectors[0].norms[FLOOR] = {"heat": [[2.0, 2.0]]}
    ins.first = {f"climate|{FLOOR}": T0 - 3 * 86400, f"stuck|{FLOOR}": T0 - 86400}
    got = ins.summary()
    assert got["devices"] == 1 and got["since"] == dt_util.utc_from_timestamp(T0 - 3 * 86400).isoformat()


async def test_habits_switch_through_the_insights_route(hass: HomeAssistant, entry, hass_client, hass_read_only_access_token):
    """The app's habits switch (0.34 п.9) goes the tunnel's insights route: admins only, a bool only; off forgets."""
    agent = entry.runtime_data
    admin, user = await hass_client(), await hass_client(hass_read_only_access_token)
    agent.habits.data["days"] = ["2026-11-10"]
    for bad in ({"habits": "off"}, {"habits": None}, {"habits": False, "x": 1}, {"habits": 0}):
        assert (await admin.post("/api/hauflo/insights", json=bad)).status == 400, bad
    assert (await user.post("/api/hauflo/insights", json={"habits": False})).status == 403
    assert agent.habits.enabled
    res = await admin.post("/api/hauflo/insights", json={"habits": False})
    assert res.status == 200 and (await res.json())["summary"]["habits"] == {"on": False, "found": 0}
    assert not agent.habits.enabled and agent.habits.data["days"] == []
    got = await (await admin.get("/api/hauflo/insights")).json()
    assert got["summary"]["habits"]["on"] is False
    res = await admin.post("/api/hauflo/insights", json={"habits": True})
    assert (await res.json())["summary"]["habits"]["on"] is True and agent.habits.enabled
    # The engine switched off: habits still answer (their own engine), the summary says the diagnosis is off.
    await admin.post("/api/hauflo/signals", json={"signals": {"scopes": [], "entities": {}, "findings": {}, "observe": {"on": False}}, "hash": "e" * 16})
    res = await (await admin.post("/api/hauflo/insights", json={"habits": False})).json()
    assert res["summary"]["on"] is False and res["summary"]["habits"]["on"] is False
