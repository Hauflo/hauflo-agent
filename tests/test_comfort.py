"""«Комфортный пол» (0.12.0, docs/comfort-floor.md) on a real HA: the settings are checked by the agent, the floor
follows the ROOM within the limits and not more often than the interval, the house mode moves the ACs once, and all
of it survives a reload. The floor / AC thermostats are KNX-like climates whose service calls are recorded."""

import time
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import area_registry as ar, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

FLOOR = "climate.room2_floor"
FLOOR_B = "climate.room2_floor_b"
AC = "climate.room2_ac"
HAND = "climate.by_hand"
SENSOR = "sensor.room2_temp"
COMFORT = "climate.hauflo_comfort_komnata_2"
MODE = "select.hauflo_house_mode"


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    area = ar.async_get(hass).async_create("Комната 2")
    for eid, uid in ((FLOOR, "u-floor"), (FLOOR_B, "u-floor-b"), (AC, "u-ac"), (HAND, "u-hand")):
        reg.async_get_or_create("climate", "knx", uid, suggested_object_id=eid.split(".")[1])
    reg.async_update_entity(FLOOR, area_id=area.id)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.owned |= {"u-floor", "u-floor-b", "u-ac"}  # «u-hand»: the integrator's own
    entry.area_id = area.id
    yield entry


def _floor(hass, eid, target, state="heat", **kw):
    hass.states.async_set(eid, state, {"temperature": target, "current_temperature": 25.0, "min_temp": 7, "max_temp": 35, "target_temp_step": 0.1, "hvac_modes": ["heat", "off"], "hvac_action": "heating", **kw})


def _ac(hass, target, state="cool"):
    hass.states.async_set(AC, state, {"temperature": target, "current_temperature": 21.0, "min_temp": 16, "max_temp": 30, "hvac_modes": ["heat", "cool", "off"], "hvac_action": "idle"})


@pytest.fixture
def calls(hass: HomeAssistant, entry):
    """The bus writes: climate services on the KNX thermostats, recorded and applied to their state; the comfort
    climate's own service still reaches the agent."""
    made: list[tuple[str, str, object]] = []
    engine = entry.runtime_data.comfort

    async def handler(call: ServiceCall) -> None:
        eid = call.data["entity_id"]
        eid = eid[0] if isinstance(eid, list) else eid
        if eid.startswith("climate.hauflo_comfort_"):
            ent = next(e for e in engine.entities.values() if e.entity_id == eid)
            if call.service == "set_temperature":
                await ent.async_set_temperature(temperature=call.data["temperature"])
            else:
                await ent.async_set_hvac_mode(call.data["hvac_mode"])
            return
        s = hass.states.get(eid)
        value = call.data.get("temperature", call.data.get("hvac_mode"))
        made.append((eid, call.service, value))
        attrs = dict(s.attributes)
        if call.service == "set_temperature":
            attrs["temperature"] = value
            hass.states.async_set(eid, s.state, attrs)
        else:
            hass.states.async_set(eid, value, attrs)

    hass.services.async_register("climate", "set_temperature", handler)
    hass.services.async_register("climate", "set_hvac_mode", handler)
    return made


ROOM = {"id": "r2", "name": "Комната 2", "floors": [FLOOR], "acs": [AC], "sensor": SENSOR, "min": 24, "max": 29}


async def _post(hass_client, body):
    res = await (await hass_client()).post("/api/hauflo/comfort", json=body)
    assert res.status == 200
    return await res.json()


def _due(engine, room="r2"):
    """As if the interval since the last write is over."""
    engine._last_write[room] = time.monotonic() - 10_000
    engine._started = time.monotonic() - 10_000


async def test_settings_checked_by_the_agent(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry) -> None:
    _floor(hass, FLOOR, 26)
    body = {
        "rooms": [
            {**ROOM, "floors": [FLOOR, HAND, "light.x", "climate.hauflo_comfort_r9"], "min": 3, "max": 80},
            {"id": "r3", "name": "Комната 3", "floors": [HAND]},  # nothing of Hauflo's: no room
            {"id": "bad id!", "floors": [FLOOR_B]},
            {"id": "r4", "floors": [FLOOR_B], "min": 26, "max": 26.5},  # less than 1° between the limits
            {"id": "r5", "floors": [FLOOR], "acs": []},  # one floor, one room
        ],
        "interval": 5,
        "winter_ac": 99,
    }
    got = await _post(hass_client, body)
    rooms = got["config"]["rooms"]
    assert [r["id"] for r in rooms] == ["r2"]
    assert rooms[0]["floors"] == [FLOOR] and rooms[0]["acs"] == [AC]
    assert (rooms[0]["min"], rooms[0]["max"]) == (5.0, 35.0)  # hard bounds, whatever was asked
    assert got["config"]["interval"] == 60 and got["config"]["winter_ac"] == 30.0
    dropped = " ".join(got["dropped"])
    for word in ("climate.by_hand is not Hauflo's", "light.x", "climate.hauflo_comfort_r9", "r3: no floor", "bad id", "r4: limits", "r5: climate.room2_floor is driven by another room"):
        assert word in dropped, word
    await hass.async_block_till_done()
    assert got["entities"] == {"r2": COMFORT}
    assert hass.states.get(COMFORT) is not None and hass.states.get(MODE).state == "winter"
    # In the floor's room.
    assert er.async_get(hass).async_get(COMFORT).area_id == entry.area_id
    # A non-admin reads nothing.
    for method in ("get", "post"):
        res = await getattr(await hass_client(hass_read_only_access_token), method)("/api/hauflo/comfort", json={"rooms": []})
        assert res.status == 403
    assert hass.states.get(COMFORT) is not None


async def test_floor_follows_the_room_within_limits(hass: HomeAssistant, hass_client, entry, calls) -> None:
    engine = entry.runtime_data.comfort
    _floor(hass, FLOOR, 26)
    _ac(hass, 24)
    hass.states.async_set(SENSOR, "20.0")
    await _post(hass_client, {"rooms": [ROOM]})
    await hass.async_block_till_done()
    st = hass.states.get(COMFORT)
    assert (st.state, st.attributes["temperature"], st.attributes["current_temperature"], st.attributes["hvac_action"]) == ("heat", 22.0, 20.0, "heating")
    # Just started: nothing written before the interval (KNX first reads the bus).
    await engine._tick(None)
    assert calls == []
    # Cold room (20 < 22): the floor up by the error, 2°.
    _due(engine)
    await engine._tick(None)
    assert calls == [(FLOOR, "set_temperature", 28.0)]
    # At once again: not more often than the interval.
    await engine._tick(None)
    assert len(calls) == 1
    # Still cold: up, but never above the limit (29).
    _due(engine)
    await engine._tick(None)
    assert calls[-1] == (FLOOR, "set_temperature", 29.0)
    _due(engine)
    await engine._tick(None)
    assert len(calls) == 2  # at the limit: nothing more to write
    # At the target (±0.3): left alone.
    hass.states.async_set(SENSOR, "22.2")
    _due(engine)
    await engine._tick(None)
    assert len(calls) == 2
    # Warm (23.5 > 22): down 1.5°, and never below the comfort minimum.
    hass.states.async_set(SENSOR, "23.5")
    _due(engine)
    await engine._tick(None)
    assert calls[-1] == (FLOOR, "set_temperature", 27.5)
    hass.states.async_set(SENSOR, "26")
    for _ in range(5):
        _due(engine)
        await engine._tick(None)
    assert calls[-1] == (FLOOR, "set_temperature", 24.0)
    assert min(v for _, _, v in calls) == 24.0
    # Someone set the floor to 35 at the wall: brought back into the limits.
    _floor(hass, FLOOR, 35)
    hass.states.async_set(SENSOR, "22")
    _due(engine)
    await engine._tick(None)
    assert calls[-1] == (FLOOR, "set_temperature", 29.0)
    # No room temperature: the floor is kept, not moved blind.
    hass.states.async_set(SENSOR, "unavailable")
    n = len(calls)
    _due(engine)
    await engine._tick(None)
    assert len(calls) == n
    # The state listener runs on the next loop turn in older HA (`_async_dispatch_entity_id_event_soon`), at once in newer.
    await hass.async_block_till_done()
    assert hass.states.get(COMFORT).attributes.get("current_temperature") is None


async def test_client_target_and_off(hass: HomeAssistant, hass_client, entry, calls) -> None:
    engine = entry.runtime_data.comfort
    _floor(hass, FLOOR, 26)
    hass.states.async_set(SENSOR, "22.0")
    await _post(hass_client, {"rooms": [ROOM]})
    await hass.async_block_till_done()
    engine._last_write["r2"] = time.monotonic() - 61  # the last write a minute ago
    await hass.services.async_call("climate", "set_temperature", {"entity_id": COMFORT, "temperature": 24}, blocking=True)
    assert hass.states.get(COMFORT).attributes["temperature"] == 24.0
    # Acted on a few seconds later, within a minute of the previous write (not a whole interval).
    await engine.step(engine.rooms()["r2"])
    assert calls == [(FLOOR, "set_temperature", 28.0)]
    # The client switched the room's comfort off: the floor rests at its comfort minimum, never cold.
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": COMFORT, "hvac_mode": "off"}, blocking=True)
    engine._last_write["r2"] = time.monotonic() - 61
    await engine.step(engine.rooms()["r2"])
    assert calls[-1] == (FLOOR, "set_temperature", 24.0)
    assert hass.states.get(COMFORT).state == "off"
    # Out of the client's range: kept in 16–28.
    await engine.set_target("r2", 40)
    assert engine.target("r2") == 28.0


async def test_house_mode_winter_summer(hass: HomeAssistant, hass_client, entry, calls) -> None:
    engine = entry.runtime_data.comfort
    _floor(hass, FLOOR, 28)
    _floor(hass, FLOOR_B, 27, hvac_modes=["heat"])  # a loop without «off»
    _ac(hass, 25, "heat")
    hass.states.async_set(SENSOR, "21.0")
    await _post(hass_client, {"rooms": [{**ROOM, "floors": [FLOOR, FLOOR_B]}]})
    await hass.async_block_till_done()
    await hass.services.async_call("select", "select_option", {"entity_id": MODE, "option": "summer"}, blocking=True)
    assert hass.states.get(MODE).state == "summer"
    # Once, at the switch: the AC cools to the room's target; both floors rest at the comfort minimum.
    assert (AC, "set_hvac_mode", "cool") in calls and (AC, "set_temperature", 22.0) in calls
    assert (FLOOR, "set_temperature", 24.0) in calls and (FLOOR_B, "set_temperature", 24.0) in calls
    assert hass.states.get(COMFORT).state == "cool"
    # Summer: the client's target goes to the AC at once.
    await engine.set_target("r2", 23)
    assert calls[-1] == (AC, "set_temperature", 23.0)
    # «Летом пол выключен»: off where the thermostat can, else its own lowest setpoint.
    calls.clear()
    await _post(hass_client, {"rooms": [{**ROOM, "floors": [FLOOR, FLOOR_B], "summer": "off"}]})
    await engine.set_mode("summer")
    assert (FLOOR, "set_hvac_mode", "off") in calls and (FLOOR_B, "set_temperature", 7.0) in calls
    # Winter: the AC heats, low — silent; the floor back on and regulated within its limits.
    calls.clear()
    await engine.set_mode("winter")
    assert (AC, "set_hvac_mode", "heat") in calls and (AC, "set_temperature", 16.0) in calls
    assert (FLOOR, "set_hvac_mode", "heat") in calls
    assert all(24.0 <= v <= 29.0 for e, s, v in calls if e in (FLOOR, FLOOR_B) and s == "set_temperature")
    assert hass.states.get(COMFORT).state == "heat"
    # Never an «AC against the floor» warning: nothing but climate writes.
    assert {s for _, s, _ in calls} <= {"set_temperature", "set_hvac_mode"}


async def test_survives_a_reload(hass: HomeAssistant, hass_client, entry, calls) -> None:
    _floor(hass, FLOOR, 26)
    hass.states.async_set(SENSOR, "21.0")
    await _post(hass_client, {"rooms": [ROOM]})
    await hass.async_block_till_done()
    engine = entry.runtime_data.comfort
    await engine.set_target("r2", 23.5)
    await engine.set_mode("summer")
    await hass.async_block_till_done()
    with patch("custom_components.hauflo.Agent.run", lambda self: _none()):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    engine = entry.runtime_data.comfort
    assert engine.mode == "summer" and engine.target("r2") == 23.5
    st = hass.states.get(COMFORT)
    assert st is not None and st.state == "cool" and st.attributes["temperature"] == 23.5
    assert hass.states.get(MODE).state == "summer"
    # Settings taken away: the room's climate and the house mode go.
    await _post(hass_client, {"rooms": []})
    await hass.async_block_till_done()
    assert hass.states.get(COMFORT) is None and er.async_get(hass).async_get(COMFORT) is None
    assert hass.states.get(MODE) is None


async def _none():
    return None


async def test_same_named_rooms_get_their_own_climates(hass: HomeAssistant, hass_client, entry) -> None:
    got = await _post(hass_client, {"rooms": [{"id": "a", "name": "Спальня", "floors": [FLOOR]}, {"id": "b", "name": "Спальня", "floors": [FLOOR_B]}]})
    await hass.async_block_till_done()
    assert got["entities"] == {"a": "climate.hauflo_comfort_spalnia", "b": "climate.hauflo_comfort_spalnia_2"}


async def test_entities_have_names_by_translation_and_the_hauflo_device(hass: HomeAssistant, hass_client, entry) -> None:
    """HA core review 29.10: has_entity_name + translation_key (room placeholder) + the service device; HouseMode's
    unique_id scoped to the config entry."""
    from homeassistant.helpers import device_registry as dr

    await _post(hass_client, {"rooms": [ROOM]})
    await hass.async_block_till_done()
    reg = er.async_get(hass)
    climate, select = reg.async_get(COMFORT), reg.async_get(MODE)
    device = dr.async_get(hass).async_get(climate.device_id)
    assert device is not None and device.name == "Hauflo" and (DOMAIN, entry.entry_id) in device.identifiers
    link = reg.async_get(reg.async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_link"))
    assert link.device_id == device.id  # one service device for all the agent's entities
    assert climate.device_id == device.id and select.device_id == device.id
    assert climate.translation_key == "comfort" and climate.has_entity_name
    assert select.unique_id == f"{entry.entry_id}_house_mode"
    assert hass.states.get(COMFORT).attributes["friendly_name"] == "Hauflo Comfort · Комната 2"
    assert hass.states.get(MODE).attributes["friendly_name"] == "Hauflo House mode"
    assert "icon" not in hass.states.get(COMFORT).attributes  # from icons.json, not the state
    # The room renamed in Hauflo: the name follows, the entity_id stays.
    await _post(hass_client, {"rooms": [{**ROOM, "name": "Гостиная"}]})
    await hass.async_block_till_done()
    assert hass.states.get(COMFORT).attributes["friendly_name"] == "Hauflo Comfort · Гостиная"


async def test_old_house_mode_unique_id_keeps_its_entity_id(hass: HomeAssistant, hass_storage) -> None:
    """Before 0.16.17 the house mode's unique_id was a bare «house_mode»: migrated in the registry, so an existing
    install keeps its entity_id (even one renamed by hand) — dashboards and automations on it keep working."""
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    old = reg.async_get_or_create("select", DOMAIN, "house_mode", config_entry=entry, suggested_object_id="rezhim_doma")
    assert old.entity_id == "select.rezhim_doma"
    reg.async_get_or_create("climate", "knx", "u-floor", suggested_object_id=FLOOR.split(".")[1])
    hass_storage["hauflo.comfort"] = {"version": 1, "minor_version": 1, "key": "hauflo.comfort", "data": {"config": {"rooms": [ROOM]}, "mode": "summer"}}
    with patch("custom_components.hauflo.Agent.run", lambda self: _none()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert reg.async_get_entity_id("select", DOMAIN, f"{entry.entry_id}_house_mode") == "select.rezhim_doma"
    assert reg.async_get_entity_id("select", DOMAIN, "house_mode") is None
    assert hass.states.get("select.rezhim_doma").state == "summer"
    assert hass.states.get(MODE) is None  # no second house mode
