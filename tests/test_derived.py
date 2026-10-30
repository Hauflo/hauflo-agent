"""Derived values (derived.py): the formulas against reference values, units, staleness, the street's sensor, and the
real `sensor.hauflo_value_…` entities made through /api/hauflo/rules — a rule on one goes to its safe state when a source
stops answering."""

from __future__ import annotations

import math
import time
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, StateMachine
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import derived as dv
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

URL = "/api/hauflo/rules"
T, RH = "sensor.bath_t", "sensor.bath_rh"


class Clock:
    def __init__(self) -> None:
        self.t = time.time()

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture
def clock():
    c = Clock()
    real = StateMachine.async_set

    def stamped(self, entity_id, new_state, attributes=None, force_update=False, context=None, state_info=None, timestamp=None):
        return real(self, entity_id, new_state, attributes, force_update, context, state_info, c() if timestamp is None else timestamp)

    with patch("custom_components.hauflo.derived.now", c), patch.object(StateMachine, "async_set", stamped):
        yield c


async def _none() -> None:
    return None


@pytest.fixture
async def entry(hass: HomeAssistant, clock):
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create("switch", "knx", "u-fan", suggested_object_id="bath_fan")
    hass.states.async_set("switch.bath_fan", "off")
    hass.states.async_set(T, "20.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(RH, "50", {"unit_of_measurement": "%", "device_class": "humidity"})
    with patch("custom_components.hauflo.Agent.run", lambda self: _none()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.owned.add("u-fan")
    clock.advance(1000)
    yield entry


# --- formulas -----------------------------------------------------------------------------------------------------------


def test_reference_values() -> None:
    assert dv.dew_point(20, 50) == pytest.approx(9.26, abs=0.01)  # ≈ 9.3 °C
    assert dv.abs_humidity(20, 50) == pytest.approx(8.6, abs=0.05)  # ≈ 8.6 g/m³
    assert dv.dew_point(20, 100) == pytest.approx(20, abs=0.01)  # saturated: the dew point is the air
    assert dv.dew_point(0, 50) == pytest.approx(-9.2, abs=0.1)
    assert dv.abs_humidity(30, 80) == pytest.approx(24.3, abs=0.3)
    for t, rh in ((20, 0), (20, 101), (-300, 50)):
        assert dv.dew_point(t, rh) is None
    assert dv.abs_humidity(20, -1) is None and dv.abs_humidity(20, 0) == 0
    assert dv.slope_per_hour([(0, 1.0), (1800, 2.0), (3600, 3.0)]) == pytest.approx(2.0)
    assert dv.slope_per_hour([(0, 1.0)]) is None and dv.slope_per_hour([(5, 1.0), (5, 2.0)]) is None


def test_units() -> None:
    assert dv.to_celsius(68, "°F") == pytest.approx(20)
    assert dv.to_celsius(293.15, "K") == pytest.approx(20)
    assert dv.to_celsius(20, None) == 20 and dv.to_celsius(20, "%") is None
    assert dv.convert(68, "°F", "°C") == pytest.approx(20) and dv.convert(1, "%", "°C") is None and dv.convert(5, "ppm", "ppm") == 5


def test_settings_checked() -> None:
    ok = dv.clean({"id": "dp", "kind": "dew_point", "name": "Точка росы", "sources": [T, RH]})
    assert ok["max_age"] == dv.CANON["derived"]["max_age_min"] and "window" not in ok
    rise = dv.clean({"id": "r", "kind": "rise", "name": "Рост", "sources": [T], "window": 9999, "max_age": -5})
    assert rise["window"] == dv.CANON["derived"]["rise_window_min"][1] and rise["max_age"] == 0
    for bad, code in (
        ("x", "bad"),
        ({"id": "Bad Id", "kind": "min", "name": "x", "sources": [T, RH]}, "bad_id"),
        ({"id": "a", "kind": "median", "name": "x", "sources": [T, RH]}, "bad_kind"),
        ({"id": "a", "kind": "min", "name": "", "sources": [T, RH]}, "bad_name"),
        ({"id": "a", "kind": "dew_point", "name": "x", "sources": [T]}, "bad_sources"),
        ({"id": "a", "kind": "min", "name": "x", "sources": [T, T]}, "bad_sources"),
        ({"id": "a", "kind": "min", "name": "x", "sources": [T, "sensor.hauflo_value_dp"]}, "bad_sources"),
        ({"id": "a", "kind": "min", "name": "x", "sources": [T, "binary_sensor.door"]}, "bad_sources"),
        ({"id": "a", "kind": "rise", "name": "x", "sources": "sensor.t"}, "bad_sources"),
    ):
        with pytest.raises(dv.Invalid) as err:
            dv.clean(bad)
        assert err.value.code == code, bad


# --- the values in HA ------------------------------------------------------------------------------------------------------


async def test_dew_point_and_abs_humidity_as_real_sensors(hass: HomeAssistant, hass_client, entry, clock) -> None:
    client = await hass_client()
    got = await (await client.post(URL, json={"op": "derived_save", "derived": {"id": "dp", "kind": "dew_point", "name": "Точка росы ванная", "sources": [T, RH]}})).json()
    assert got["entity_id"] == "sensor.hauflo_value_tochka_rosy_vannaia" and got["value"] == pytest.approx(9.26, abs=0.01)
    await client.post(URL, json={"op": "derived_save", "derived": {"id": "ah", "kind": "abs_humidity", "name": "Абсолютная влажность", "sources": [T, RH]}})
    await hass.async_block_till_done()
    dp = hass.states.get("sensor.hauflo_value_tochka_rosy_vannaia")
    assert float(dp.state) == pytest.approx(9.26, abs=0.01) and dp.attributes["unit_of_measurement"] == "°C" and dp.attributes["device_class"] == "temperature"
    ah = hass.states.get("sensor.hauflo_value_absoliutnaia_vlazhnost")
    assert float(ah.state) == pytest.approx(8.6, abs=0.05) and ah.attributes["unit_of_measurement"] == "g/m³" and ah.attributes["device_class"] == "absolute_humidity"
    assert er.async_get(hass).async_get(dp.entity_id).unique_id == f"{entry.entry_id}_derived_dp"
    # Follows its sources; °F sources are converted.
    hass.states.async_set(T, "68", {"unit_of_measurement": "°F"})
    await hass.async_block_till_done()
    assert float(hass.states.get(dp.entity_id).state) == pytest.approx(9.26, abs=0.01)
    hass.states.async_set(T, "25", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert float(hass.states.get(dp.entity_id).state) == pytest.approx(13.85, abs=0.05)
    # A source unavailable / not a number / humidity not in %: the value is unavailable, never an old one.
    for state, attrs in (("unavailable", {}), ("abc", {}), ("50", {"unit_of_measurement": "g/kg"})):
        hass.states.async_set(RH, state, attrs)
        await hass.async_block_till_done()
        assert hass.states.get(dp.entity_id).state == "unavailable", state
    hass.states.async_set(RH, "50", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    # Stale: no report for max_age minutes — unavailable (the tick sees it).
    engine = entry.runtime_data.rules
    clock.advance(dv.CANON["derived"]["max_age_min"] * 60 + 1)
    await engine._tick()
    await hass.async_block_till_done()
    assert hass.states.get(dp.entity_id).state == "unavailable" and engine.derived["dp"].why.startswith("stale")
    # The same values sent again (a KNX sensor's cyclic send: HA «reports», no state change): fresh at once, no tick.
    hass.states.async_set(RH, "50", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    assert hass.states.get(dp.entity_id).state == "unavailable"  # T is still old
    hass.states.async_set(T, "25", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert hass.states.get(dp.entity_id).state != "unavailable" and engine.derived["dp"].why is None
    listed = (await (await client.get(URL)).json())["derived"]
    assert {d["id"] for d in listed} == {"dp", "ah"} and all(d["entity_id"].startswith("sensor.hauflo_value_") for d in listed)


async def test_delta_min_max_avg_rise(hass: HomeAssistant, hass_client, entry, clock) -> None:
    client = await hass_client()
    hass.states.async_set("sensor.out_t", "-5", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set("sensor.ppm", "800", {"unit_of_measurement": "ppm"})
    for d in (
        {"id": "dl", "kind": "delta", "name": "Дельта", "sources": [T, "sensor.out_t"]},
        {"id": "mn", "kind": "min", "name": "Мин", "sources": [T, "sensor.out_t"]},
        {"id": "mx", "kind": "max", "name": "Макс", "sources": [T, "sensor.out_t"]},
        {"id": "av", "kind": "avg", "name": "Сред", "sources": [T, "sensor.out_t"]},
        {"id": "mixed", "kind": "avg", "name": "Смесь", "sources": [T, "sensor.ppm"]},
        {"id": "rs", "kind": "rise", "name": "Рост", "sources": [T], "window": 10},
    ):
        assert (await client.post(URL, json={"op": "derived_save", "derived": d})).status == 200
    await hass.async_block_till_done()
    val = lambda e: hass.states.get(e).state  # noqa: E731
    assert float(val("sensor.hauflo_value_delta")) == 25 and float(val("sensor.hauflo_value_min")) == -5 and float(val("sensor.hauflo_value_maks")) == 20
    assert float(val("sensor.hauflo_value_sred")) == 7.5 and hass.states.get("sensor.hauflo_value_min").attributes["device_class"] == "temperature"
    assert val("sensor.hauflo_value_smes") == "unavailable"  # °C and ppm don't mix
    assert val("sensor.hauflo_value_rost") == "unknown"  # collecting: no number yet, nothing lost
    engine = entry.runtime_data.rules
    for i in range(1, 11):
        clock.advance(60)
        hass.states.async_set(T, str(20 + i * 0.1), {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
    rs = hass.states.get("sensor.hauflo_value_rost")
    assert float(rs.state) == pytest.approx(6.0, abs=0.1) and rs.attributes["unit_of_measurement"] == "°C/h"  # 0.1 °C a minute
    # Nothing changes: the tick keeps sampling, the slope falls to 0.
    for _ in range(12):
        clock.advance(60)
        await engine._tick()
    await hass.async_block_till_done()
    assert abs(float(hass.states.get("sensor.hauflo_value_rost").state)) < 0.5
    hass.states.async_set(T, "unavailable")
    await hass.async_block_till_done()
    assert val("sensor.hauflo_value_rost") == "unavailable" and not engine.derived["rs"].samples


async def test_rule_on_a_derived_value_and_its_safe_state(hass: HomeAssistant, hass_client, entry, clock) -> None:
    client = await hass_client()
    await client.post(URL, json={"op": "derived_save", "derived": {"id": "dp", "kind": "dew_point", "name": "Точка росы", "sources": [T, RH]}})
    await hass.async_block_till_done()
    rule = {"id": "dry", "name": "Осушение", "purpose": "fan", "input": {"entity_id": "sensor.hauflo_value_tochka_rosy"}, "when": "above", "x": 8, "y": 1, "for_min": 0, "actions": [{"do": "on", "entity_id": "switch.bath_fan"}]}
    hass.services.async_register("switch", "turn_on", lambda call: hass.states.async_set("switch.bath_fan", "on", context=call.context))
    res = await client.post(URL, json={"op": "save", "rule": rule})
    assert res.status == 200, await res.text()
    await hass.async_block_till_done()
    engine = entry.runtime_data.rules
    assert engine.st("dry")["active"] and hass.states.get("switch.bath_fan").state == "on"
    # The chain is checked through the value: a thermostat that is a source of the rule's input is never its target.
    er.async_get(hass).async_get_or_create("climate", "knx", "u-room", suggested_object_id="room")
    entry.runtime_data.owned.add("u-room")
    hass.states.async_set("climate.room", "heat", {"current_temperature": 21, "temperature": 21})
    await client.post(URL, json={"op": "derived_save", "derived": {"id": "rm", "kind": "min", "name": "Мин комната", "sources": ["climate.room", T]}})
    assert engine.derived_sources("sensor.hauflo_value_min_komnata") == ["climate.room", T]
    loop = {**rule, "id": "loop", "name": "Петля", "input": {"entity_id": "sensor.hauflo_value_min_komnata"}, "actions": [{"do": "setpoint", "entity_id": "climate.room", "value": 19}]}
    got = await (await client.post(URL, json={"op": "save", "rule": loop})).json()
    assert got["code"] == "loop" and "climate.room" in got["message"]
    # A source stops answering: the derived value is unavailable → the rule's input fault (Repairs), never the old value.
    hass.states.async_set(RH, "unavailable")
    await hass.async_block_till_done()
    assert engine.status("dry") == "fault"
    # In use: a derived value a rule reads can't be deleted.
    assert (await (await client.post(URL, json={"op": "derived_delete", "id": "dp"})).json())["code"] == "in_use"
    assert (await (await client.post(URL, json={"op": "derived_delete", "id": "nope"})).json())["code"] == "no_value"
    assert (await client.post(URL, json={"op": "derived_save", "derived": {"id": "x", "kind": "min", "name": "x", "sources": ["sensor.none", T]}})).status == 400
    await client.post(URL, json={"op": "delete", "id": "dry"})
    assert (await client.post(URL, json={"op": "derived_delete", "id": "dp"})).status == 200
    await hass.async_block_till_done()
    assert hass.states.get("sensor.hauflo_value_tochka_rosy") is None
    with patch.dict(dv.CANON["derived"], {"max": 1}):
        assert (await (await client.post(URL, json={"op": "derived_save", "derived": {"id": "new", "kind": "min", "name": "Новый", "sources": [T, RH]}})).json())["code"] == "limit"


async def test_outdoor_found_by_name_knx_first(hass: HomeAssistant, entry) -> None:
    reg = er.async_get(hass)
    hass.states.async_set("weather.home", "sunny", {"temperature": 3, "temperature_unit": "°C"})
    assert dv.outdoor(hass)["entity_id"] == "weather.home" and dv.outdoor(hass)["forecast"] is True
    reg.async_get_or_create("sensor", "knx", "u-street", suggested_object_id="meteostantsiia")
    hass.states.async_set("sensor.meteostantsiia", "-2", {"unit_of_measurement": "°C", "device_class": "temperature", "friendly_name": "Метеостанция улица"})
    hass.states.async_set("sensor.outdoor_by_hand", "-1", {"unit_of_measurement": "°C", "device_class": "temperature"})  # not KNX, not Hauflo's
    hass.states.async_set("sensor.kitchen_t", "21", {"unit_of_measurement": "°C", "device_class": "temperature"})
    found = dv.outdoor(hass)
    assert found == {"entity_id": "sensor.meteostantsiia", "attribute": None, "forecast": False, "name": "Метеостанция улица"}
    hass.states.async_remove("sensor.meteostantsiia")
    hass.states.async_remove("weather.home")
    assert dv.outdoor(hass) is None
    assert dv.outdoor(hass, owns=lambda e: e == "sensor.outdoor_by_hand")["entity_id"] == "sensor.outdoor_by_hand"


def test_unit_of_attributes(hass: HomeAssistant) -> None:
    hass.states.async_set("weather.w", "sunny", {"temperature": 3, "temperature_unit": "°F", "pressure": 1000, "pressure_unit": "hPa", "wind_speed": 3, "wind_speed_unit": "m/s", "humidity": 60})
    st = hass.states.get("weather.w")
    assert [dv.unit_of(st, a) for a in ("temperature", "humidity", "pressure", "wind_speed", "ozone")] == ["°F", "%", "hPa", "m/s", None]
    hass.states.async_set("climate.c", "heat", {"current_temperature": 20, "current_humidity": 40})
    st = hass.states.get("climate.c")
    assert dv.unit_of(st, "current_temperature") == "°C" and dv.unit_of(st, "current_humidity") == "%" and dv.unit_of(st, "x") is None
    assert dv.read(hass, "climate.c") == (20.0, "°C", None) and dv.read(hass, "climate.none")[2] == "no_entity"
    assert dv.read(hass, "weather.w#humidity")[:2] == (60.0, "%")
    assert math.isnan(float("nan")) and dv.num(float("inf")) is None and dv.num("x") is None
