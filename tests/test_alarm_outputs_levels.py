"""Alarm outputs (399-15, a siren) and level rules (399-20, a number below / above a threshold with hysteresis) — in the
agent's engine and in the rule's own HA automation. Broken on purpose: a siren never picked, a phone's or an unknown
one, a switch smuggled in as an output, a widened output while the house is closed, a level wobbling at its line, a
value that isn't a number, a threshold with both ways or on a signal."""

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import dump, load_yaml
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hauflo import independent, notify
from custom_components.hauflo.const import CONF_ALLOW_CHANGES, CONF_RANGES, CONF_SECRET, CONF_SERVER, DOMAIN

from .test_alarm_engine import Rig

SIREN, OTHER = "siren.hall", "siren.garage"
BAT, CO2 = "sensor.lock_battery", "sensor.co2_bedroom"


def _sirens(hass: HomeAssistant) -> list[ServiceCall]:
    calls: list[ServiceCall] = []
    hass.services.async_register("siren", "turn_on", calls.append)
    hass.services.async_register("siren", "turn_off", calls.append)
    for s in (SIREN, OTHER):
        hass.states.async_set(s, "off", {"friendly_name": s})
    return calls


def _said(calls: list[ServiceCall]) -> list[tuple[str, list[str] | str]]:
    return [(c.service, c.data["entity_id"]) for c in calls]


@pytest.fixture
async def rig(hass: HomeAssistant):
    r = Rig(hass)
    await r.eng.async_start()
    yield r
    r.eng.async_stop()
    await r.tick(10)


def _leak(hass: HomeAssistant, state: str = "off") -> None:
    hass.states.async_set("binary_sensor.kitchen", state, {"friendly_name": "KITCHEN", "device_class": "moisture"})


# --- 399-15: the siren --------------------------------------------------------------------------------------------


async def test_only_the_saved_siren_sounds_and_accept_silences_it(hass, rig) -> None:
    calls = _sirens(hass)
    _leak(hass)
    await rig.rules({"sources": ["binary_sensor.kitchen"], "outputs": [{"entity_id": SIREN}]})
    assert rig.eng.config["rules"][0]["outputs"] == [{"entity_id": SIREN, "on_start": True, "on_end": True}]
    _leak(hass, "on")
    await rig.tick(4)
    assert _said(calls) == [("turn_on", SIREN)]  # the other siren of the house: never
    assert rig.eng._sounding == {"r1": {SIREN}}
    calls.clear()
    assert await rig.eng.async_ack("r1|binary_sensor.kitchen", "Ivan") is True
    assert _said(calls) == [("turn_off", SIREN)] and rig.eng._sounding == {}


async def test_siren_off_when_back_to_normal(hass, rig) -> None:
    calls = _sirens(hass)
    _leak(hass)
    await rig.rules({"sources": ["binary_sensor.kitchen"], "outputs": [{"entity_id": SIREN}]})
    _leak(hass, "on")
    await rig.tick(4)
    calls.clear()
    _leak(hass, "off")
    await rig.tick(4)
    assert _said(calls) == [("turn_off", SIREN)]


async def test_on_start_only_leaves_it_on(hass, rig) -> None:
    calls = _sirens(hass)
    _leak(hass)
    await rig.rules({"sources": ["binary_sensor.kitchen"], "outputs": [{"entity_id": SIREN, "on_end": False}]})
    _leak(hass, "on")
    await rig.tick(4)
    await rig.eng.async_ack("r1|binary_sensor.kitchen", "Ivan")
    assert _said(calls) == [("turn_on", SIREN)]


async def test_what_may_not_be_an_output_is_refused(hass, rig) -> None:
    calls = _sirens(hass)
    hass.states.async_set("switch.boiler", "on")
    hass.states.async_set("light.hall", "on")
    reg = er.async_get(hass)
    reg.async_get_or_create("siren", "mobile_app", "phone1", suggested_object_id="phone_siren")
    hass.states.async_set("siren.phone_siren", "off")
    _leak(hass)
    bad = [{"entity_id": "switch.boiler"}, {"entity_id": "light.hall"}, {"entity_id": "siren.phone_siren"}, {"entity_id": "siren.nowhere"},
           {"entity_id": "siren.hall; rm -rf"}, {"entity_id": 7}, "siren.hall", {"entity_id": SIREN, "on_start": False, "on_end": False}]
    await rig.rules({"sources": ["binary_sensor.kitchen"], "outputs": bad})
    assert "outputs" not in rig.eng.config["rules"][0]
    _leak(hass, "on")
    await rig.tick(4)
    assert calls == []
    # At most OUTPUTS_MAX, duplicates once.
    many = [{"entity_id": f"siren.s{i}"} for i in range(notify.OUTPUTS_MAX + 3)]
    for o in many:
        hass.states.async_set(o["entity_id"], "off")
    clean = notify._clean_rules({"rules": [{"id": "r", "kind": "leak", "title": "t", "sources": ["binary_sensor.kitchen"], "picked": ["binary_sensor.kitchen"],
                                            "alarm_states": ["on"], "recipients": {"all": True}, "outputs": [many[0], *many]}]}, hass, lambda e: True)
    assert [o["entity_id"] for o in clean["rules"][0]["outputs"]] == [o["entity_id"] for o in many[: notify.OUTPUTS_MAX]]


async def test_switch_never_takes_an_entity_the_rule_did_not_save(hass, rig) -> None:
    calls = _sirens(hass)
    _leak(hass)
    await rig.rules({"sources": ["binary_sensor.kitchen"], "outputs": [{"entity_id": SIREN}]})
    await rig.eng._switch("r1", [OTHER, "switch.boiler", "lock.front"], "on_start", "test")
    await rig.eng._switch("r1", [OTHER], "on_end", "test")
    await rig.eng._switch("nope", [SIREN], "on_start", "test")
    assert calls == []


async def test_a_rule_losing_its_siren_turns_it_off(hass, rig) -> None:
    calls = _sirens(hass)
    _leak(hass)
    await rig.rules({"sources": ["binary_sensor.kitchen"], "outputs": [{"entity_id": SIREN}]})
    _leak(hass, "on")
    await rig.tick(4)
    calls.clear()
    await rig.rules({"sources": ["binary_sensor.kitchen"]})  # the output taken away while it sounds
    await rig.tick(1)
    assert _said(calls) == [("turn_off", SIREN)] and rig.eng._sounding == {}


def test_closed_house_never_gets_a_new_output_or_threshold() -> None:
    base = {"id": "r", "kind": "leak", "title": "t", "sources": ["sensor.b"], "alarm_states": ["on"], "recipients": {"all": True, "people": [], "groups": [], "phones": []}, "priority": "normal", "repeat_min": 0, "enabled": True}
    old = {"rules": [base]}
    assert notify.narrowing({"rules": [{**base, "outputs": [{"entity_id": SIREN, "on_start": True, "on_end": True}]}]}, old) == "r: new output"
    assert notify.narrowing({"rules": [{**base, "thresholds": {"sensor.b": {"below": 20, "step": 5}}}]}, old) == "r: new threshold"
    was = {"rules": [{**base, "outputs": [{"entity_id": SIREN, "on_start": True, "on_end": True}], "thresholds": {"sensor.b": {"below": 20, "step": 5}}}]}
    assert notify.narrowing({"rules": [{**base, "thresholds": {"sensor.b": {"below": 30, "step": 5}}}]}, was) == "r: new threshold"
    assert notify.narrowing({"rules": [base]}, was) is None  # taking them away is narrowing


# --- 399-20: levels -----------------------------------------------------------------------------------------------


def _bat(hass: HomeAssistant, v: str) -> None:
    hass.states.async_set(BAT, v, {"friendly_name": "Замок", "device_class": "battery", "unit_of_measurement": "%"})


async def test_battery_level_with_hysteresis(hass, rig) -> None:
    _bat(hass, "60")
    await rig.rules({"kind": "battery", "title": "Батарея", "sources": [BAT], "alarm_states": ["on"], "thresholds": {BAT: {"below": 20}}})
    assert rig.eng.config["rules"][0]["thresholds"] == {BAT: {"below": 20.0, "step": 5.0}}  # the canon step of a battery
    _bat(hass, "18")
    await rig.tick(4)
    assert rig.titles() == ["🚨 Батарея"] and "Замок (18 % < 20 %)" in rig.messages()[0]
    rig.clear()
    for v in ("22", "19", "24.9"):  # wobbling at the line: the same alarm, nothing said
        _bat(hass, v)
        await rig.tick(4)
    assert rig.pushes == [] and f"r1|{BAT}" in rig.eng.active
    _bat(hass, "25")  # back past the threshold by its step
    await rig.tick(4)
    assert rig.titles() == ["✅ Батарея"] and f"r1|{BAT}" not in rig.eng.active
    rig.clear()
    _bat(hass, "19")  # re-armed: a new alarm
    await rig.tick(4)
    assert rig.titles() == ["🚨 Батарея"]


async def test_above_level_and_not_a_number(hass, rig) -> None:
    hass.states.async_set(CO2, "800", {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm"})
    await rig.rules({"kind": "air", "title": "CO2", "sources": [CO2], "alarm_states": ["on"], "thresholds": {CO2: {"above": 1500}}})
    for v in ("on", "abc", "nan", "inf", "unknown"):
        hass.states.async_set(CO2, v, {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm"})
        await rig.tick(4)
    assert rig.pushes == []
    hass.states.async_set(CO2, "1600", {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm"})
    await rig.tick(4)
    assert rig.titles() == ["🚨 CO2"]
    rig.clear()
    hass.states.async_set(CO2, "1450", {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm"})
    await rig.tick(4)
    assert rig.pushes == []
    hass.states.async_set(CO2, "1400", {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm"})
    await rig.tick(4)
    assert rig.titles() == ["✅ CO2"]


def test_bad_thresholds_are_refused(hass) -> None:
    hass.states.async_set("sensor.x", "5", {"device_class": "pressure"})
    raw = {"sensor.x": {"below": 10, "above": 20}, "binary_sensor.kitchen": {"below": 1}, "sensor.notmine": {"below": 1},
           "sensor.t": {"below": "1e9"}, "sensor.u": {"below": True}, "sensor.v": {"below": "NaN"}, "sensor.w": "20"}
    srcs = ["sensor.x", "binary_sensor.kitchen", "sensor.t", "sensor.u", "sensor.v", "sensor.w"]
    assert notify._clean_thresholds(hass, raw, srcs) == {}
    # A class with no canon: the step is a share of the threshold; a negative step — the default.
    assert notify._clean_thresholds(hass, {"sensor.x": {"above": 1000, "step": -3}}, ["sensor.x"]) == {"sensor.x": {"above": 1000.0, "step": 50.0}}
    assert notify.level_default("battery") == {"below": 20, "step": 5} and notify.level_default("temperature") == {"below": 5, "step": 1}
    assert notify.level_default("door") is None


async def test_status_offers_levels_and_sirens(hass, rig) -> None:
    _sirens(hass)
    _bat(hass, "60")
    hass.states.async_set("sensor.power", "100", {"device_class": "power", "unit_of_measurement": "W"})  # not a level of the canon
    st = rig.eng.status()
    cand = {c["entity_id"]: c for c in st["candidates"]}
    assert cand[BAT]["unit"] == "%" and "sensor.power" not in cand
    assert {o["entity_id"] for o in st["outputs"]} == {SIREN, OTHER} and st["levels"] == notify.LEVELS


# --- in HA's own automation ---------------------------------------------------------------------------------------

PHONES = [{"service": "mobile_app_anna", "name": "Pixel Анны", "os": "android", "person": "person.anna"}]


@pytest.fixture
async def house(hass: HomeAssistant, tmp_path: Path, freezer):
    async def _idle(self):
        return None

    hass.config.config_dir = str(tmp_path)
    hass.config.language = "ru"
    (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
    (tmp_path / "automations.yaml").write_text(dump([]))
    assert await async_setup_component(hass, "automation", {"automation": []})
    assert await async_setup_component(hass, "persistent_notification", {})
    calls = _sirens(hass)
    _bat(hass, "60")
    _leak(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"}, options={CONF_RANGES: ["1/1"], CONF_ALLOW_CHANGES: True})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    engine = entry.runtime_data.engine
    engine.owns = lambda eid: True
    engine.phones = lambda: PHONES  # type: ignore[method-assign]
    pushes: list[ServiceCall] = []
    hass.services.async_register("notify", "mobile_app_anna", pushes.append)
    yield engine, tmp_path, pushes, calls, freezer
    await hass.services.async_call("automation", "turn_off", {"entity_id": "all"}, blocking=True)


async def _later(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


RULE = {"id": "bat", "kind": "battery", "title": "Батарея", "alarm_states": ["on"], "recipients": {"all": True}, "repeat_min": 0, "enabled": True}


def test_rules_without_outputs_keep_their_automation(hass) -> None:
    raw = json.loads((Path(independent.__file__).parent / "translations" / "ru.json").read_text())["exceptions"]
    t = {k.removeprefix("alarm_native_"): v["message"] for k, v in raw.items() if k.startswith("alarm_native_")}
    rule = {**RULE, "kind": "leak", "sources": ["binary_sensor.kitchen"], "picked": ["binary_sensor.kitchen"]}
    plain = independent.automation_for(rule, [], t, "input_boolean.a")
    assert "siren" not in str(plain) and "numeric_state" not in str(plain)
    loud = independent.automation_for({**rule, "outputs": [{"entity_id": SIREN, "on_start": True, "on_end": True}]}, [], t, "input_boolean.a")
    text = str(loud)
    assert text.count("'siren.turn_on'") == 1 and text.count("'siren.turn_off'") >= 5 and "'continue_on_error': True, 'target': {'entity_id': ['siren.hall']}" in text


async def test_level_rule_runs_in_ha_with_numeric_state_and_siren(hass: HomeAssistant, house) -> None:
    engine, config, pushes, calls, freezer = house
    rule = {**RULE, "sources": [BAT, "binary_sensor.kitchen"], "picked": [BAT, "binary_sensor.kitchen"], "thresholds": {BAT: {"below": 20}}, "outputs": [{"entity_id": SIREN}]}
    engine.config = notify._clean_rules({"lang": "ru", "rules": [rule]}, hass, engine.owns)
    engine._apply()
    res = await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    assert res["added"] == ["Батарея"] and not res["engine"], res
    auto = next(a for a in load_yaml(str(config / "automations.yaml")) if a["id"] == independent.automation_id("bat"))
    assert {"trigger": "numeric_state", "entity_id": [BAT], "below": 20.0, "for": {"seconds": independent.HOLD}, "id": "alarm"} in auto["triggers"]
    _bat(hass, "18")
    await _later(hass, freezer, independent.HOLD + 1)
    assert len(pushes) == 1 and "Замок" in pushes[0].data["message"] and "18 %" in pushes[0].data["message"], [p.data for p in pushes]
    assert _said(calls) == [("turn_on", [SIREN])]
    pushes.clear()
    calls.clear()
    for v in ("22", "19", "23"):  # within the step: the same open alarm
        _bat(hass, v)
        await _later(hass, freezer, independent.HOLD + 1)
    assert pushes == [] and calls == []
    # The leak too: one more open alarm, said with both.
    _leak(hass, "on")
    await _later(hass, freezer, independent.HOLD + 1)
    assert len(pushes) == 1 and "KITCHEN" in pushes[0].data["message"] and "Замок" in pushes[0].data["message"]
    pushes.clear()
    calls.clear()
    _bat(hass, "26")
    await _later(hass, freezer, independent.HOLD + 1)
    assert pushes == [] and calls == []  # the leak is still open
    _leak(hass, "off")
    await _later(hass, freezer, independent.HOLD + 1)
    assert len(pushes) == 1 and pushes[0].data["title"] == "✅ Батарея" and _said(calls) == [("turn_off", [SIREN])]
    pushes.clear()
    _bat(hass, "15")  # re-armed
    await _later(hass, freezer, independent.HOLD + 1)
    assert len(pushes) == 1 and "15 %" in pushes[0].data["message"]


async def test_signal_rule_in_ha_sounds_the_siren_until_accepted(hass: HomeAssistant, house) -> None:
    engine, config, pushes, calls, freezer = house
    rule = {**RULE, "id": "leak", "kind": "leak", "title": "Протечка", "sources": ["binary_sensor.kitchen"], "picked": ["binary_sensor.kitchen"], "outputs": [{"entity_id": SIREN}]}
    engine.config = notify._clean_rules({"lang": "ru", "rules": [rule]}, hass, engine.owns)
    engine._apply()
    assert (await independent.async_sync(hass, engine))["added"] == ["Протечка"]
    await hass.async_block_till_done()
    _leak(hass, "on")
    await _later(hass, freezer, independent.HOLD + 1)
    assert _said(calls) == [("turn_on", [SIREN])] and len(pushes) == 1
    calls.clear()
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": independent.ids("leak")["ack"]})
    await hass.async_block_till_done()
    assert ("turn_off", [SIREN]) in _said(calls) and OTHER not in str(_said(calls))
