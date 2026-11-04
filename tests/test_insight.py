"""«Диагностика по своей норме» (insight.py): a zone heating without effect against ITS OWN learned rate, a sensor that
stopped changing against its own daily norm — and none of the false alarms: learning, a window open, the target
changed, the climate unavailable / off / the other season, an HA restart."""

import json

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er

from custom_components.hauflo import insight
from custom_components.hauflo.insight import FLAT_H, LEARN_DAYS, LEARN_MIN, MIN_H, STORE_KEY, STUCK_H, Insights

H = 3600
FLOOR = "climate.floor"
WINDOW = "binary_sensor.window"
TEMP = "sensor.room_temp"
T0 = 1_800_000_000.0  # a fixed clock start


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def season():
    return {"now": None}


@pytest.fixture
async def ins(hass: HomeAssistant, season):
    reg = er.async_get(hass)
    area = ar.async_get(hass).async_create("Гостиная")
    for domain, uid in (("climate", "floor"), ("binary_sensor", "window"), ("sensor", "room_temp")):
        e = reg.async_get_or_create(domain, "test", uid, suggested_object_id=uid)
        reg.async_update_entity(e.entity_id, area_id=area.id)
    hass.states.async_set(WINDOW, "off", {"device_class": "window"})
    engine = Insights(hass, lambda eid: True, lambda: season["now"])
    engine.clock = Clock()
    await engine.async_start()
    yield engine
    engine.async_stop()


def climate(hass: HomeAssistant, cur, tgt=22.0, mode="heat", **attrs):
    hass.states.async_set(FLOOR, mode, {"current_temperature": cur, "temperature": tgt, "friendly_name": "Тёплый пол", **attrs})


async def at(hass: HomeAssistant, ins: Insights, hours: float, cur=None, **kw):
    """The clock to T0 + hours, the climate (if given) set, a tick."""
    ins.clock.t = T0 + hours * H
    if cur is not None:
        climate(hass, cur, **kw)
        await hass.async_block_till_done()
    ins._tick()


async def learn(hass: HomeAssistant, ins: Insights, n=LEARN_MIN, start=0.0) -> float:
    """n clean episodes: 2° below the target, reached in 1 h — the norm 2 °C/h. Returns the hour after them."""
    t = start
    for _ in range(n):
        await at(hass, ins, t, 20.0)
        await at(hass, ins, t + 1, 22.0)
        t += 2
    return t


def det(ins: Insights):
    return ins.detectors[0]


async def test_learning_gives_no_word(hass: HomeAssistant, ins: Insights):
    t = await learn(hass, ins, LEARN_MIN - 1)
    assert det(ins).norm(FLOOR, "heat") is None
    await at(hass, ins, t, 20.0)
    for h in range(1, 12):
        await at(hass, ins, t + h, 20.0)
    assert ins.items() == []


async def test_heating_without_effect_is_said_then_ends(hass: HomeAssistant, ins: Insights):
    calls = []
    ins.on_change = lambda: calls.append(1)
    t = await learn(hass, ins)
    assert det(ins).norm(FLOOR, "heat") == pytest.approx(2.0)
    await at(hass, ins, t, 20.0)
    # Expected 1 h: the word waits for max(MIN_H, 3 × 1 h) and FLAT_H without a rise.
    await at(hass, ins, t + MIN_H - 0.5, 20.1)
    assert ins.items() == []
    await at(hass, ins, t + MIN_H + 0.5, 20.1)
    [item] = ins.items()
    assert item["kind"] == "no_heat" and item["entityId"] == FLOOR and item["key"] == f"no_heat|{FLOOR}"
    assert item["area"] == "Гостиная" and item["name"] == "Тёплый пол"
    assert item["expectedH"] == pytest.approx(1.0) and item["hours"] == pytest.approx(MIN_H + 0.5)
    assert item["change"] == pytest.approx(0.1) and item["flatH"] == FLAT_H
    assert calls  # the status went at once
    # Reached: gone (the cloud says «снова в норме»), and the failed episode isn't learned.
    rates = list(det(ins).norms[FLOOR]["heat"])
    await at(hass, ins, t + 6, 21.9)
    assert ins.items() == []
    assert det(ins).norms[FLOOR]["heat"] == rates


async def test_slowly_rising_is_not_flat(hass: HomeAssistant, ins: Insights):
    t = await learn(hass, ins)
    await at(hass, ins, t, 18.0, tgt=22.0)
    for h in range(1, 10):
        await at(hass, ins, t + h, 18.0 + h * 0.2)  # 0.4° in FLAT_H: slow but moving
    assert ins.items() == []


async def test_window_open_drops_the_episode(hass: HomeAssistant, ins: Insights):
    t = await learn(hass, ins)
    await at(hass, ins, t, 20.0)
    hass.states.async_set(WINDOW, "on", {"device_class": "window"})
    for h in range(1, 8):
        await at(hass, ins, t + h, 20.0)
    assert ins.items() == [] and FLOOR not in det(ins).eps
    # Closed: a fresh episode from then — not the time the window was open.
    hass.states.async_set(WINDOW, "off", {"device_class": "window"})
    await at(hass, ins, t + 8, 20.0)
    assert det(ins).eps[FLOOR]["start"] == T0 + (t + 8) * H
    await at(hass, ins, t + 10, 20.0)
    assert ins.items() == []


async def test_target_change_restarts_and_is_not_learned(hass: HomeAssistant, ins: Insights):
    t = await learn(hass, ins)
    n = len(det(ins).norms[FLOOR]["heat"])
    await at(hass, ins, t, 20.0)
    await at(hass, ins, t + 2.5, 20.0, tgt=23.0)
    await at(hass, ins, t + 4, 20.0, tgt=23.0)
    assert ins.items() == []  # 1.5 h since the new target, not 4 h
    await at(hass, ins, t + 4.5, 20.0, tgt=19.0)  # lowered below the room: no want, dropped
    assert FLOOR not in det(ins).eps and len(det(ins).norms[FLOOR]["heat"]) == n


async def test_unavailable_off_and_other_season_say_nothing(hass: HomeAssistant, ins: Insights, season):
    t = await learn(hass, ins)
    await at(hass, ins, t, 20.0)
    hass.states.async_set(FLOOR, "unavailable", {})
    for h in range(1, 8):
        await at(hass, ins, t + h)
    assert ins.items() == []
    assert FLOOR in det(ins).eps  # frozen, not ended
    await at(hass, ins, t + 8, 20.0, mode="off")
    assert FLOOR not in det(ins).eps
    season["now"] = "cool"
    await at(hass, ins, t + 9, 20.0)
    for h in range(10, 16):
        await at(hass, ins, t + h, 20.0)
    assert FLOOR not in det(ins).eps and ins.items() == []


async def test_idle_thermostat_is_not_heating(hass: HomeAssistant, ins: Insights):
    t = await learn(hass, ins)
    for h in range(0, 8):
        await at(hass, ins, t + h, 20.0, hvac_action="idle")
    assert FLOOR not in det(ins).eps and ins.items() == []
    # It says it heats, then idles (PWM) — still one episode; asked within FLAT_H: said.
    await at(hass, ins, t + 8, 20.0, hvac_action="heating")
    await at(hass, ins, t + 10, 20.0, hvac_action="idle")
    await at(hass, ins, t + 11.5, 20.0, hvac_action="heating")
    assert [i["kind"] for i in ins.items()] == ["no_heat"]


async def test_cooling_mirrors(hass: HomeAssistant, ins: Insights):
    t = 0.0
    for _ in range(LEARN_MIN):
        await at(hass, ins, t, 26.0, tgt=24.0, mode="cool")
        await at(hass, ins, t + 1, 24.0, tgt=24.0, mode="cool")
        t += 2
    await at(hass, ins, t, 26.0, tgt=24.0, mode="cool")
    await at(hass, ins, t + MIN_H + 0.5, 26.0, tgt=24.0, mode="cool")
    [item] = ins.items()
    assert item["kind"] == "no_cool" and item["change"] == pytest.approx(0.0)


async def test_restart_keeps_an_open_word_and_needs_fresh_observation(hass: HomeAssistant, ins: Insights, hass_storage, season):
    t = await learn(hass, ins)
    await at(hass, ins, t, 20.0)
    await at(hass, ins, t + MIN_H + 0.5, 20.0)
    assert len(ins.items()) == 1
    saved = json.loads(json.dumps(ins.data()))
    ins.async_stop()
    # HA restarts: the climate unavailable first (KNX reads the bus after the agent) — the word stays, no «end».
    hass_storage[STORE_KEY] = {"version": 1, "minor_version": 1, "key": STORE_KEY, "data": saved}
    hass.states.async_set(FLOOR, "unavailable", {})
    again = Insights(hass, lambda eid: True, lambda: season["now"])
    again.clock = Clock()
    again.clock.t = T0 + (t + MIN_H + 1) * H
    await again.async_start()
    assert [i["key"] for i in again.items()] == [f"no_heat|{FLOOR}"]
    assert det(again).norm(FLOOR, "heat") == pytest.approx(2.0)  # the norm survived
    again.async_stop()

    # A carried-over episode that wasn't said: nothing right after the restart, even though it is long and flat.
    saved["climate"]["episodes"][FLOOR]["alerted"] = None
    hass_storage[STORE_KEY] = {"version": 1, "minor_version": 1, "key": STORE_KEY, "data": saved}
    third = Insights(hass, lambda eid: True, lambda: season["now"])
    third.clock = Clock()
    base = t + MIN_H + 2
    third.clock.t = T0 + base * H
    await third.async_start()
    for h in (0, 0.5, FLAT_H - 0.5):
        third.clock.t = T0 + (base + h) * H
        climate(hass, 20.0)
        await hass.async_block_till_done()
        third._tick()
        assert third.items() == []
    third.clock.t = T0 + (base + FLAT_H + 0.1) * H
    third._tick()
    assert len(third.items()) == 1  # a full FLAT_H seen since the restart, still flat: said
    # Reached after a restart: not learned (HA was down part of it).
    n = len(det(third).norms[FLOOR]["heat"])
    third.clock.t += H
    climate(hass, 22.0)
    await hass.async_block_till_done()
    third._tick()
    assert third.items() == [] and len(det(third).norms[FLOOR]["heat"]) == n
    third.async_stop()


async def test_bad_store_learns_anew(hass: HomeAssistant, hass_storage, season):
    hass_storage[STORE_KEY] = {"version": 1, "minor_version": 1, "key": STORE_KEY, "data": {"climate": {"norms": {FLOOR: {"heat": ["x", -1, 2]}}, "episodes": {FLOOR: {"dir": "up"}}}, "stuck": "bad"}}
    engine = Insights(hass, lambda eid: True, lambda: season["now"])
    await engine.async_start()
    assert det(engine).norms == {FLOOR: {"heat": [2.0]}} and det(engine).eps == {}
    engine.async_stop()


# --- stuck sensor ------------------------------------------------------------------------------------------------------


async def test_stuck_sensor_after_its_norm(hass: HomeAssistant, ins: Insights):
    attrs = {"device_class": "temperature", "friendly_name": "Температура"}
    v = 20.0
    hass.states.async_set(TEMP, str(v), attrs)
    await hass.async_block_till_done()
    ins._tick()
    # LEARN_DAYS + 2 days (the first and the last are partial), 24 changes each.
    for day in range(LEARN_DAYS + 2):
        for h in range(24):
            ins.clock.t = T0 + (day * 24 + h) * H
            v = round(v + (0.1 if h % 2 else -0.1), 1)
            hass.states.async_set(TEMP, str(v), attrs)
            await hass.async_block_till_done()
            ins._tick()
    stuck = ins.detectors[1]
    assert stuck.norm(TEMP) is not None and stuck.norm(TEMP) >= 6
    last = ins.clock.t
    ins.clock.t = last + (STUCK_H - 1) * H
    ins._tick()
    assert ins.items() == []
    ins.clock.t = last + (STUCK_H + 1) * H
    ins._tick()
    [item] = ins.items()
    assert item["kind"] == "stuck" and item["entityId"] == TEMP and item["hours"] == pytest.approx(STUCK_H + 1)
    # No data is not «застыл».
    hass.states.async_set(TEMP, "unavailable", attrs)
    await hass.async_block_till_done()
    assert ins.items() == []
    # The same value back: still stuck; a new value: gone.
    hass.states.async_set(TEMP, str(v), attrs)
    await hass.async_block_till_done()
    assert len(ins.items()) == 1
    hass.states.async_set(TEMP, str(v + 0.1), attrs)
    await hass.async_block_till_done()
    assert ins.items() == []


async def test_stuck_needs_its_learning_days_and_a_lively_norm(hass: HomeAssistant, ins: Insights):
    attrs = {"device_class": "humidity"}
    hass.states.async_set(TEMP, "40", attrs)
    await hass.async_block_till_done()
    ins._tick()
    # A sensor that changes once a day (a slow-reporting one): its norm is below STUCK_NORM — never «застыл».
    for day in range(LEARN_DAYS + 2):
        ins.clock.t = T0 + day * 24 * H
        hass.states.async_set(TEMP, str(40 + day), attrs)
        await hass.async_block_till_done()
        ins._tick()
    ins.clock.t += 5 * 24 * H
    ins._tick()
    assert ins.items() == []


async def test_not_watched_without_hauflo_or_label(hass: HomeAssistant, season):
    engine = Insights(hass, lambda eid: False, lambda: season["now"])
    await engine.async_start()
    hass.states.async_set(TEMP, "20", {"device_class": "temperature"})
    await hass.async_block_till_done()
    engine._tick()
    assert engine.detectors[1].recs == {}
    engine.async_stop()


def test_thresholds_are_tuning():
    t = insight.TUNING["insight"]
    assert (insight.MIN_H, insight.FACTOR, insight.FLAT_C, insight.STUCK_H) == (t["min_h"], t["factor"], t["flat_c"], t["stuck_h"])


# --- wired into the agent ----------------------------------------------------------------------------------------------


async def _idle(self) -> None:
    return None


async def test_the_agent_sends_its_insights_and_keeps_the_norms(hass: HomeAssistant, hass_storage):
    from unittest.mock import patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "top-secret"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        agent = entry.runtime_data
        assert agent.insights is not None and agent.status()["insights"] == []
        det(agent.insights).norms[FLOOR] = {"heat": [2.0]}
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    assert hass_storage[STORE_KEY]["data"]["climate"]["norms"][FLOOR] == {"heat": [2.0]}
