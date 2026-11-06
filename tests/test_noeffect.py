"""«Исполнитель включён, эффекта нет» (noeffect.py, п.68): a floor loop's valve open and its floor not warming, a thermostat
heating and the air not rising, a fan on and the CO2 / humidity not dropping — said once, quiet while the effect is there,
none of the false alarms (setpoint reached, a gap, a setpoint change, a daily exercise), the pairs found from the mapping
and refined by hand."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er

from custom_components.hauflo.insight import Insights
from custom_components.hauflo.noeffect import HOLD, KINDS, PairEffect

H = 3600
T0 = 1_800_000_000.0
STEP = 0.25  # hours between ticks
FLOOR_W = KINDS["floor"]["window_h"]
SAY = (HOLD - 1) * FLOOR_W + FLOOR_W  # hours of a flat floor before the word: the evidence of HOLD windows
V = "binary_sensor.kitchen_valve_{}"
F = "sensor.kitchen_floor_{}"
CL = "climate.kitchen_floor"
AIR = "climate.kitchen_air"


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> float:
        return self.t


def reg(hass: HomeAssistant, area, domain, uid, name, cls=None):
    r = er.async_get(hass)
    e = r.async_get_or_create(domain, "test", uid, suggested_object_id=uid)
    r.async_update_entity(e.entity_id, area_id=area.id)
    return e.entity_id


@pytest.fixture
async def ins(hass: HomeAssistant):
    area = ar.async_get(hass).async_create("Кухня")
    for k in (1, 2, 3):
        reg(hass, area, "binary_sensor", f"kitchen_valve_{k}", "")
        reg(hass, area, "sensor", f"kitchen_floor_{k}", "")
    reg(hass, area, "climate", "kitchen_floor", "")
    reg(hass, area, "climate", "kitchen_air", "")
    engine = Insights(hass, lambda eid: True, lambda: None)
    engine.clock = Clock()
    await engine.async_start()
    yield engine
    engine.async_stop()


def det(ins: Insights) -> PairEffect:
    return next(d for d in ins.detectors if isinstance(d, PairEffect))


def setup(hass, temps=(23.3, 27.2, 27.3), setpoint=27.0, valves="on"):
    for k, t in enumerate(temps, 1):
        hass.states.async_set(V.format(k), valves, {"device_class": "opening", "friendly_name": f"Клапан 2.{k}"})
        hass.states.async_set(F.format(k), str(t), {"device_class": "temperature", "friendly_name": f"Температура пола {k}"})
    hass.states.async_set(CL, "heat", {"friendly_name": "Тёплый пол", "temperature": setpoint, "current_temperature": 25.0, "hvac_action": "heating"})


async def run(hass, ins, hours, temp1=None, temps=None, step=STEP):
    """Advance `hours` in ticks; temp1(h) — floor 1's value at the hour since the call's start (default: as set)."""
    start = (ins.clock.t - T0) / H
    n = round(hours / step)
    for i in range(1, n + 1):
        h = i * step
        ins.clock.t = T0 + (start + h) * H
        if temp1 is not None:
            hass.states.async_set(F.format(1), str(round(temp1(h), 2)), {"device_class": "temperature", "friendly_name": "Температура пола 1"})
        await hass.async_block_till_done()
        ins._tick()


def said(ins):
    return [i for i in ins.items() if i["kind"] == "no_effect"]


async def test_pairs_come_from_the_mapping(hass, ins):
    setup(hass)
    await run(hass, ins, STEP)
    pairs = {p["effect"]: p["exec"] for p in det(ins).pairs_view()["pairs"] if p["kind"] == "floor"}
    assert pairs == {F.format(k): V.format(k) for k in (1, 2, 3)}
    assert det(ins).pairs[F.format(1)]["target"] == CL


async def test_loop_open_and_cold_is_said_the_others_are_not(hass, ins):
    setup(hass)
    await run(hass, ins, SAY - 1)
    assert said(ins) == []
    await run(hass, ins, 1.5)
    [item] = said(ins)
    assert item["key"] == f"no_effect|{F.format(1)}" and item["role"] == "floor" and item["exec"] == "Клапан 2.1"
    assert item["area"] == "Кухня" and item["name"] == "Температура пола 1"
    assert item["flatH"] == FLOOR_W and item["limit"] == KINDS["floor"]["min_change"]
    assert abs(item["change"]) < 0.5 and item["value"] == 23.3 and item["hours"] >= SAY


async def test_rising_floor_is_quiet(hass, ins):
    setup(hass)
    await run(hass, ins, 2 * SAY, temp1=lambda h: 23.3 + 0.2 * h)  # +1.2 in 6 h
    assert said(ins) == []


async def test_a_rise_in_the_middle_restarts_the_count(hass, ins):
    setup(hass)
    await run(hass, ins, 8)
    await run(hass, ins, 1, temp1=lambda h: 23.3 + 0.8 * h)  # 0.8 in an hour: the effect is there
    await run(hass, ins, SAY - 2, temp1=lambda h: 24.1)
    assert said(ins) == []  # the rise left the window at 15 h; the count began there: the word is at 15 + 6 = 21 h
    await run(hass, ins, 1, temp1=lambda h: 24.1)
    assert said(ins) == []
    await run(hass, ins, 2, temp1=lambda h: 24.1)
    assert len(said(ins)) == 1


async def test_loop_at_its_setpoint_is_not_a_fault(hass, ins):
    setup(hass, temps=(26.5, 27.2, 27.3))  # 0.5 below 27.0: no room left for an effect
    await run(hass, ins, 3 * SAY)
    assert said(ins) == []


async def test_valve_closed_is_not_the_loops_fault(hass, ins):
    setup(hass)
    hass.states.async_set(V.format(1), "off", {"device_class": "opening", "friendly_name": "Клапан 2.1"})
    await run(hass, ins, 3 * SAY)
    assert said(ins) == []


async def test_a_daily_exercise_of_the_valve_does_not_hide_it(hass, ins):
    setup(hass)
    await run(hass, ins, 4)
    hass.states.async_set(V.format(1), "off", {"device_class": "opening", "friendly_name": "Клапан 2.1"})
    await run(hass, ins, 10 / 60, step=10 / 60)  # ten minutes closed
    hass.states.async_set(V.format(1), "on", {"device_class": "opening", "friendly_name": "Клапан 2.1"})
    await run(hass, ins, SAY + 1)
    assert len(said(ins)) == 1


async def test_a_gap_and_a_setpoint_change_restart_the_window(hass, ins):
    setup(hass)
    await run(hass, ins, SAY - 1)
    hass.states.async_set(F.format(1), "unavailable", {"device_class": "temperature", "friendly_name": "Температура пола 1"})
    await run(hass, ins, STEP)  # a gateway restart
    await run(hass, ins, 2, temp1=lambda h: 23.3)
    assert said(ins) == []
    await run(hass, ins, SAY - 4, temp1=lambda h: 23.3)
    hass.states.async_set(CL, "heat", {"friendly_name": "Тёплый пол", "temperature": 28.0, "current_temperature": 25.0, "hvac_action": "heating"})  # a new target
    await run(hass, ins, 3)
    assert said(ins) == []


async def test_it_ends_when_the_effect_appears_and_is_not_said_again_within_a_day(hass, ins):
    setup(hass)
    await run(hass, ins, SAY + 1)
    assert len(said(ins)) == 1
    calls = []
    ins.on_change = lambda: calls.append(1)
    await run(hass, ins, 2, temp1=lambda h: 23.3 + 0.6 * h)  # +1.2 in 2 h: the clear bar
    assert said(ins) == [] and calls
    # Flat again at once: the same pair is not said before `repeat_h` after the first word.
    await run(hass, ins, SAY + 2, temp1=lambda h: 24.5)  # 15 h: due again at 27 h, held back to 12 + 24 = 36 h
    assert said(ins) == []
    await run(hass, ins, 10, temp1=lambda h: 24.5)  # 37 h
    assert len(said(ins)) == 1


async def test_a_flicker_around_the_bar_does_not_clear_it(hass, ins):
    setup(hass)
    await run(hass, ins, SAY + 1)
    assert len(said(ins)) == 1
    await run(hass, ins, 1, temp1=lambda h: 23.3 + 0.55 * h)  # above the start bar, below the clear bar
    await run(hass, ins, 0.5, temp1=lambda h: 23.85)
    assert len(said(ins)) == 1


async def test_an_open_finding_survives_a_restart_without_a_new_word(hass, ins):
    setup(hass)
    await run(hass, ins, SAY + 1)
    [before] = said(ins)
    data = ins.data()
    again = Insights(hass, lambda eid: True, lambda: None)
    again.clock = ins.clock
    for d in again.detectors:
        d.load(data.get(d.name, {}))
    [after] = [i for i in again.items() if i["kind"] == "no_effect"]
    assert after["key"] == before["key"] and after["since"] == before["since"]


async def test_the_valve_feedback_is_not_an_open_window_for_the_air(hass, ins):
    """Горького 3: «Клапан» sensors have device_class opening — they must not silence the thermostat in their room."""
    setup(hass)
    hass.states.async_set(AIR, "heat", {"friendly_name": "Климат", "temperature": 25.0, "current_temperature": 20.0, "hvac_action": "heating"})
    await run(hass, ins, 7)
    [item] = [i for i in said(ins) if i["role"] == "air"]
    assert item["entityId"] == AIR and item["exec"] == "Климат"


async def test_thermostat_air_waits_for_the_zones_own_norm(hass, ins):
    setup(hass)
    hass.states.async_set(AIR, "heat", {"friendly_name": "Климат", "temperature": 25.0, "current_temperature": 20.0, "hvac_action": "heating"})
    clim = next(d for d in ins.detectors if d.name == "climate")
    clim.norms[AIR] = {"heat": [2.0] * 5}  # learned: ClimateEffect judges this zone
    await run(hass, ins, 8)
    assert [i for i in said(ins) if i["role"] == "air"] == []


async def test_ventilation_on_and_co2_not_dropping(hass, ins):
    area = ar.async_get(hass).async_get_area_by_name("Кухня")
    reg(hass, area, "fan", "kitchen_vent", "")
    reg(hass, area, "sensor", "kitchen_co2", "")
    hass.states.async_set("fan.kitchen_vent", "on", {"friendly_name": "Вентиляция"})
    hass.states.async_set("sensor.kitchen_co2", "1400", {"device_class": "carbon_dioxide", "friendly_name": "CO2"})
    await run(hass, ins, 3)
    [item] = [i for i in said(ins) if i["role"] == "co2"]
    assert item["exec"] == "Вентиляция" and item["value"] == 1400 and item["limit"] == KINDS["co2"]["min_change"]
    for v in (1300, 1000, 700):  # dropping fast: the effect is there, and it ends
        hass.states.async_set("sensor.kitchen_co2", str(v), {"device_class": "carbon_dioxide", "friendly_name": "CO2"})
        await run(hass, ins, 0.25)
    assert [i for i in said(ins) if i["role"] == "co2"] == []


async def test_ventilation_with_clean_air_or_falling_co2_is_quiet(hass, ins):
    area = ar.async_get(hass).async_get_area_by_name("Кухня")
    reg(hass, area, "fan", "kitchen_vent", "")
    reg(hass, area, "sensor", "kitchen_co2", "")
    hass.states.async_set("fan.kitchen_vent", "on", {"friendly_name": "Вентиляция"})
    hass.states.async_set("sensor.kitchen_co2", "620", {"device_class": "carbon_dioxide", "friendly_name": "CO2"})
    await run(hass, ins, 4)  # nothing to remove
    assert [i for i in said(ins) if i["role"] == "co2"] == []


async def test_humidity_pair(hass, ins):
    area = ar.async_get(hass).async_get_area_by_name("Кухня")
    reg(hass, area, "fan", "kitchen_extract", "")
    reg(hass, area, "sensor", "kitchen_rh", "")
    hass.states.async_set("fan.kitchen_extract", "on", {"friendly_name": "Вытяжка"})
    hass.states.async_set("sensor.kitchen_rh", "72", {"device_class": "humidity", "friendly_name": "Влажность"})
    await run(hass, ins, 3)
    assert [i["role"] for i in said(ins) if i["entityId"] == "sensor.kitchen_rh"] == ["humidity"]


async def test_numbers_that_do_not_match_are_not_guessed_and_can_be_set_by_hand(hass, ins):
    area = ar.async_get(hass).async_create("Гардероб")
    reg(hass, area, "binary_sensor", "ward_valve_8", "")
    reg(hass, area, "binary_sensor", "ward_valve_10", "")
    reg(hass, area, "sensor", "ward_floor_1", "")
    reg(hass, area, "sensor", "ward_floor_2", "")
    for n, v in (("8", "Клапан 2.8"), ("10", "Клапан 2.10")):
        hass.states.async_set(f"binary_sensor.ward_valve_{n}", "on", {"device_class": "opening", "friendly_name": v})
    for n in (1, 2):
        hass.states.async_set(f"sensor.ward_floor_{n}", "22", {"device_class": "temperature", "friendly_name": f"Температура пола {n}"})
    setup(hass)
    await run(hass, ins, STEP)
    view = det(ins).pairs_view()
    assert {u["effect"] for u in view["unpaired"]} == {"sensor.ward_floor_1", "sensor.ward_floor_2"}
    assert not [p for p in view["pairs"] if p["effect"].startswith("sensor.ward")]
    det(ins).set_pair("sensor.ward_floor_1", "floor", "binary_sensor.ward_valve_8")
    await run(hass, ins, STEP)
    assert any(p["effect"] == "sensor.ward_floor_1" and p["exec"] == "binary_sensor.ward_valve_8" and p["manual"] and p["why"] == "manual" for p in det(ins).pairs_view()["pairs"])
    det(ins).set_pair("sensor.kitchen_floor_2", "floor", "")  # «not a pair»
    await run(hass, ins, STEP)
    assert "sensor.kitchen_floor_2" not in det(ins).pairs


async def test_valve_feedback_does_not_stop_climate_effect_a_real_window_does(hass, ins):
    """One list of «not windows» for every detector: ClimateEffect starts an episode beside valve feedback, not beside a window."""
    setup(hass)
    area = ar.async_get(hass).async_get_area_by_name("Кухня")
    win = reg(hass, area, "binary_sensor", "kitchen_window", "")
    hass.states.async_set(win, "off", {"device_class": "window", "friendly_name": "Окно"})
    hass.states.async_set(AIR, "heat", {"friendly_name": "Климат", "temperature": 25.0, "current_temperature": 20.0, "hvac_action": "heating"})
    clim = next(d for d in ins.detectors if d.name == "climate")
    await run(hass, ins, 1)
    assert AIR in clim.eps  # the valves are «on» and device_class opening: not a window
    clim.eps.clear()
    hass.states.async_set(win, "on", {"device_class": "window", "friendly_name": "Окно"})
    await run(hass, ins, 1)
    assert AIR not in clim.eps  # a real window: no episode


async def test_view_says_why_each_pair_was_found_and_lists_candidates(hass, ins):
    setup(hass)
    hass.states.async_set(AIR, "heat", {"friendly_name": "Воздух", "temperature": 22.0, "current_temperature": 21.0, "hvac_action": "heating"})
    await run(hass, ins, STEP)
    view = det(ins).pairs_view()
    floor = next(p for p in view["pairs"] if p["kind"] == "floor")
    assert floor["why"] == "number" and floor["number"] is not None and floor["area"] == "Кухня" and floor["name"] and floor["execName"]
    air = next(p for p in view["pairs"] if p["effect"] == AIR)
    assert air["why"] == "own"
    assert V.format(1) in {c["id"] for c in view["candidates"]["floor"]} and AIR in {c["id"] for c in view["candidates"]["air"]}


async def test_manual_pairs_survive_a_restart_and_clear_goes_back_to_the_mapping(hass, ins):
    setup(hass)
    await run(hass, ins, STEP)
    d = det(ins)
    d.set_pair(F.format(1), "floor", V.format(3))
    d.set_pair(F.format(2), "floor", "")
    saved = d.dump()
    again = PairEffect(ins)
    again.load(saved)
    assert again.manual == {F.format(1): {"kind": "floor", "exec": V.format(3)}, F.format(2): {"kind": "floor", "exec": ""}}
    again.load({"manual": {"x.y": {"kind": "bogus", "exec": "a.b"}}})  # a bad saved kind is dropped
    assert "x.y" not in again.manual
    assert [i["effect"] for i in d.pairs_view()["ignored"]] == [F.format(2)]
    d.clear_pair(F.format(1))
    d.clear_pair(F.format(2))
    d.clear_pair("sensor.never_set")
    await run(hass, ins, STEP)
    assert d.pairs[F.format(1)]["exec"] == V.format(1) and not d.manual
