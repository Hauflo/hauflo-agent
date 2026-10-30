"""«Комфортный пол» removed (owner 30.10): a house that had it set up updates the agent — its «Комфорт · <комната>»
climates, the «Режим дома» select and its store go; the KNX thermostats it drove stay exactly as they are (no service
call, registry entries untouched), the per-loop floor thermostats too; its house mode becomes the season's last choice."""

from unittest.mock import patch

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import retired
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

FLOOR_LOOP = "climate.bath_floor_loop_1"
FLOOR_LOOP_2 = "climate.bath_floor_loop_2"
AC = "climate.bath_ac"


async def test_comfort_floor_removed_on_update_and_nothing_else_touched(hass: HomeAssistant, hass_storage) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    # What 0.16 left: two room climates, the house mode (old bare unique_id and the entry-scoped one), the KNX loops.
    comfort = [
        reg.async_get_or_create("climate", DOMAIN, "comfort_bath", suggested_object_id="hauflo_comfort_bath", config_entry=entry).entity_id,
        reg.async_get_or_create("climate", DOMAIN, "comfort_hall", suggested_object_id="hauflo_comfort_hall", config_entry=entry).entity_id,
        reg.async_get_or_create("select", DOMAIN, f"{entry.entry_id}_house_mode", suggested_object_id="hauflo_house_mode", config_entry=entry).entity_id,
        reg.async_get_or_create("select", DOMAIN, "house_mode", suggested_object_id="hauflo_house_mode_old").entity_id,
    ]
    knx = [reg.async_get_or_create("climate", "knx", uid, suggested_object_id=uid.replace("climate.", "")).entity_id for uid in (FLOOR_LOOP, FLOOR_LOOP_2, AC)]
    # The agent's own other entities stay.
    keep = reg.async_get_or_create("sensor", DOMAIN, f"{entry.entry_id}_ownership", suggested_object_id="hauflo_ownership", config_entry=entry).entity_id
    for eid, sp in ((knx[0], 27.5), (knx[1], 28.0), (knx[2], 16.0)):
        hass.states.async_set(eid, "heat", {"hvac_modes": ["heat", "off"], "temperature": sp})
    hass_storage["hauflo.comfort"] = {
        "version": 1,
        "minor_version": 1,
        "key": "hauflo.comfort",
        "data": {"config": {"rooms": [{"id": "bath", "name": "Ванная", "floors": [knx[0], knx[1]], "acs": [knx[2]], "min": 24, "max": 29}]}, "mode": "summer", "targets": {"bath": 22}},
    }
    calls: list[ServiceCall] = []
    for svc in ("set_temperature", "set_hvac_mode", "turn_on", "turn_off"):
        hass.services.async_register("climate", svc, lambda call: calls.append(call))
    hass.services.async_register("knx", "send", lambda call: calls.append(call))

    async def idle(self):
        return None

    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert all(reg.async_get(e) is None for e in comfort)
    assert all(reg.async_get(e) is not None for e in [*knx, keep])  # the KNX thermostats and the agent's own stay
    assert "hauflo.comfort" not in hass_storage  # the store is gone
    assert calls == []  # not one write: setpoints and modes stay as they are
    assert [hass.states.get(e).attributes["temperature"] for e in knx] == [27.5, 28.0, 16.0]
    # The house mode carried over: summer → the season's last choice «cool» (the value rules' «season» says summer).
    assert entry.runtime_data.season.season == "cool" and entry.runtime_data.season.rule_season() == "summer"
    assert hass.states.get("select.hauflo_house_mode") is None
    # No climate / select platform of the agent any more.
    assert not [e for e in reg.entities.values() if e.platform == DOMAIN and e.domain in ("climate", "select")]

    # Again (the next restart): nothing to do, nothing breaks.
    got = await retired.async_retire_comfort(hass, entry.entry_id)
    assert got == {"entities": [], "store": False, "mode": None}


async def test_a_chosen_season_is_not_overwritten(hass: HomeAssistant, hass_storage) -> None:
    hass_storage["hauflo.season"] = {"version": 1, "minor_version": 1, "key": "hauflo.season", "data": {"season": "heat"}}
    hass_storage["hauflo.comfort"] = {"version": 1, "minor_version": 1, "key": "hauflo.comfort", "data": {"mode": "summer"}}
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)

    async def idle(self):
        return None

    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.runtime_data.season.season == "heat" and "hauflo.comfort" not in hass_storage
