"""An alarm rule's hold (owner 09.11 «дребезг: устойчивое on > 60 с»; ISA-18.2 on-delay, HA's own `for:`): the rule's
`hold_s` or the canon by kind / class (tuning notify.hold) — in HA's automation (`for:`) and in the engine (its open
alarms, the flaps Hauflo counts as «дребезжит», the pushes of a rule HA doesn't run)."""

import pytest
from homeassistant.core import HomeAssistant

from custom_components.hauflo import independent, notify
from custom_components.hauflo.tuning import TUNING

from .test_alarm_engine import Rig
from .test_alarms_native import _later, house  # noqa: F401 — the fixture

CANON = TUNING["notify"]["hold"]
DOOR, LEAK, PIR, KNX, CO2 = "binary_sensor.door_hall", "binary_sensor.leak_kitchen", "binary_sensor.pir_hall", "binary_sensor.knx_fault", "sensor.co2_bedroom"


def _house(hass: HomeAssistant) -> None:
    hass.states.async_set(DOOR, "off", {"device_class": "door", "friendly_name": "Дверь"})
    hass.states.async_set(LEAK, "off", {"device_class": "moisture", "friendly_name": "Протечка на кухне"})
    hass.states.async_set(PIR, "off", {"device_class": "motion", "friendly_name": "Движение"})
    hass.states.async_set(KNX, "off", {"friendly_name": "Котёл авария"})
    hass.states.async_set(CO2, "600", {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm", "friendly_name": "CO2"})


def _rule(**r):
    return {"id": "r1", "kind": "custom", "title": "Т", "alarm_states": ["on"], "recipients": {"all": True}, "repeat_min": 0, **r, "picked": r["sources"]}


def test_the_canon_says_by_class_kind_and_level() -> None:
    assert CANON["classes"]["moisture"] == 0 and CANON["classes"]["door"] == 60 and CANON["level_s"] == 120 and CANON["default_s"] == 60
    assert notify.hold_value(0) == 0 and notify.hold_value(90) == 90 and notify.hold_value(CANON["max_s"]) == CANON["max_s"]
    for bad in (None, -1, CANON["max_s"] + 1, 1.5, "60", True, [60]):
        assert notify.hold_value(bad) is None, bad


async def test_clean_rules_keep_the_own_hold_or_take_the_canon(hass: HomeAssistant) -> None:
    _house(hass)
    raw = {"rules": [
        _rule(id="door", sources=[DOOR]), _rule(id="leak", kind="leak", sources=[KNX]), _rule(id="mixed", sources=[DOOR, LEAK]),
        _rule(id="pir", sources=[PIR], hold_s=15), _rule(id="knx", sources=[KNX]), _rule(id="co2", kind="air", sources=[CO2], thresholds={CO2: {"above": 1500}}),
        _rule(id="bad", sources=[DOOR], hold_s=-5), _rule(id="zero", sources=[DOOR], hold_s=0),
    ]}
    got = {r["id"]: (r["hold_s"], r["hold"]) for r in notify._clean_rules(raw, hass, lambda e: True)["rules"]}
    assert got == {
        "door": (None, 60),     # a door: a minute
        "leak": (None, 0),      # a leak rule — at once, even from a classless KNX signal
        "mixed": (None, 0),     # a leak and a door: the shortest — the leak never waits for the door
        "pir": (15, 15),        # the integrator's own
        "knx": (None, 60),      # classless: a minute
        "co2": (None, 120),     # a level: two
        "bad": (None, 60),      # a wrong one is not taken: the canon
        "zero": (0, 0),
    }


async def test_ha_automation_holds_the_alarm_for_the_rule(hass: HomeAssistant) -> None:
    _house(hass)
    t = await independent.texts(hass, False)
    rules = {r["id"]: r for r in notify._clean_rules({"rules": [
        _rule(id="door", sources=[DOOR]), _rule(id="leak", kind="leak", sources=[LEAK]), _rule(id="slow", sources=[DOOR], hold_s=300),
        _rule(id="co2", kind="air", sources=[CO2, DOOR], thresholds={CO2: {"above": 1500}}, hold_s=90),
    ]}, hass, lambda e: True)["rules"]}

    def trig(rule, tid):
        return [x for x in independent.automation_for(rule, [], t, "input_boolean.a", "input_boolean.o")["triggers"] if x["id"] == tid]

    assert [x["for"] for x in trig(rules["door"], "alarm")] == [{"seconds": 60}]
    assert [x["for"] for x in trig(rules["slow"], "alarm")] == [{"seconds": 300}]
    # 0 = at once: HA still keeps its few seconds against a flicker (HOLD).
    assert [x["for"] for x in trig(rules["leak"], "alarm")] == [{"seconds": independent.HOLD}]
    # «back» is held by the back window, not by the alarm's hold.
    assert trig(rules["door"], "back")[0]["for"] == {"seconds": independent.back_after(rules["door"])}
    # A level rule with a signal beside it: one numeric_state (each source's margin) held by the rule's hold, «back»
    # past the step by HOLD.
    assert [x["for"] for x in trig(rules["co2"], "alarm")] == [{"seconds": 90}]
    assert all(x["for"] == {"seconds": independent.HOLD} for x in trig(rules["co2"], "back"))
    # A rule stored before holds (no hold / hold_s): the canon by kind (no HA here — classless).
    old = {k: v for k, v in rules["door"].items() if k not in ("hold", "hold_s")}
    assert [x["for"] for x in trig(old, "alarm")] == [{"seconds": CANON["default_s"]}]
    assert [x["for"] for x in trig({**old, "kind": "smoke"}, "alarm")] == [{"seconds": independent.HOLD}]
    assert [x["for"] for x in trig({**old, "thresholds": {CO2: {"above": 1500.0, "step": 100.0}}, "sources": [CO2]}, "alarm")] == [{"seconds": CANON["level_s"]}]


@pytest.fixture
async def rig(hass: HomeAssistant):
    r = Rig(hass)
    await r.eng.async_start()
    yield r
    r.eng.async_stop()
    await r.tick(10)


async def test_engine_starts_only_what_held(hass, rig) -> None:
    _house(hass)
    await rig.eng.async_set({"rules": [_rule(sources=[DOOR], title="Дверь")]})
    key = f"r1|{DOOR}"
    # Open 30 s, closed: nothing — no alarm, no push, no flap counted.
    hass.states.async_set(DOOR, "on", {"device_class": "door"})
    await rig.tick(30)
    assert rig.eng.active == {} and key in rig.eng._holding and rig.pushes == []
    hass.states.async_set(DOOR, "off", {"device_class": "door"})
    await rig.tick(70)
    assert rig.eng.active == {} and rig.eng._holding == {} and rig.pushes == [] and key not in rig.eng._flaps
    # Flapping under the hold, many times: never «дребезжит», never an alarm.
    for _ in range(5):
        hass.states.async_set(DOOR, "on", {"device_class": "door"})
        await rig.tick(1)
        hass.states.async_set(DOOR, "off", {"device_class": "door"})
        await rig.tick(1)
    await rig.tick(70)
    assert rig.eng.active == {} and rig.pushes == [] and key not in rig.eng._chatter
    # Open and staying: the alarm after the hold, one push per phone.
    hass.states.async_set(DOOR, "on", {"device_class": "door"})
    await rig.tick(59)
    assert rig.eng.active == {}
    await rig.tick(65)
    assert key in rig.eng.active
    await rig.tick(70)  # the engine's group window
    assert len(rig.pushes) == 2
    # No data in the middle of a hold cancels it.
    hass.states.async_set(DOOR, "off", {"device_class": "door"})
    await rig.tick(1)
    hass.states.async_set(DOOR, "on", {"device_class": "door"})
    await rig.tick(1)
    assert key in rig.eng._holding
    hass.states.async_set(DOOR, "unavailable", {"device_class": "door"})
    await rig.tick(1)
    assert key not in rig.eng._holding


async def test_engine_zero_hold_and_a_rule_change_keep_the_time(hass, rig) -> None:
    _house(hass)
    await rig.eng.async_set({"rules": [_rule(sources=[DOOR], title="Дверь", hold_s=0)]})
    hass.states.async_set(DOOR, "on", {"device_class": "door"})
    await rig.tick(4)
    assert f"r1|{DOOR}" in rig.eng.active  # 0 = at once (the engine's group window only)
    # A rule with a hold sent while a source is already in alarm: weighed from the state's own time.
    hass.states.async_set(PIR, "on", {"device_class": "motion"})
    await rig.eng.async_set({"rules": [_rule(id="r2", sources=[PIR], title="Движение", hold_s=120)]})
    assert f"r2|{PIR}" in rig.eng._holding and f"r2|{PIR}" not in rig.eng.active
    await rig.tick(125)
    assert f"r2|{PIR}" in rig.eng.active


async def test_native_door_rule_tells_only_a_held_alarm(hass: HomeAssistant, house) -> None:  # noqa: F811
    entry, engine, config, pushes, closed, freezer = house
    hass.states.async_set(DOOR, "off", {"device_class": "door", "friendly_name": "Дверь"})
    engine.config = notify._clean_rules({"lang": "ru", "rules": [_rule(id="door", title="Дверь", sources=[DOOR])]}, hass, engine.owns)
    engine._apply()
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    hass.states.async_set(DOOR, "on", {"device_class": "door", "friendly_name": "Дверь"})
    await _later(hass, freezer, 30)
    hass.states.async_set(DOOR, "off", {"device_class": "door", "friendly_name": "Дверь"})
    await _later(hass, freezer, 700)
    assert pushes == [] and engine.active == {}  # 30 s open: neither HA nor the engine says a thing
    hass.states.async_set(DOOR, "on", {"device_class": "door", "friendly_name": "Дверь"})
    await _later(hass, freezer, 30)
    assert pushes == []
    await _later(hass, freezer, 31)
    assert len(pushes) == 2 and {p.data["title"] for p in pushes} == {"🚨 Дверь"}  # HA told once per phone, the engine quiet
    assert f"door|{DOOR}" in engine.active  # and its open alarm is in the status (Hauflo's incident)
