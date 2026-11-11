"""Р57 — Alice's zones: a merged room's zones are their own HA areas («Кухня-гостиная · Остров», alias «Алиса: Остров») so
Yandex asks «в какой комнате?»; the plan Hauflo tells carries `zones` {zone area → room area} and everything of the agent that
asks an entity's room (order, voice, presence, scenes, guests, recipes, alarms, insights, the cards) gets the room."""

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import order, ownership as own
from custom_components.hauflo.automations import Automations
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN
from custom_components.hauflo.insight import Insights
from custom_components.hauflo.notify import NotifyEngine
from custom_components.hauflo.presence import Presence
from custom_components.hauflo.scenes import FamilyScenes

from .test_voice import _expose, add as add_climate, say  # noqa: F401 — the voice module's helpers


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


@pytest.fixture
def house(hass: HomeAssistant):
    """«Кухня-гостиная» with two zone areas of Alice's; a light in each zone, one by its device's area, one in the room."""
    areas = ar.async_get(hass)
    room = areas.async_create("Кухня-гостиная")
    island = areas.async_create("Кухня-гостиная · Остров", aliases={"Алиса: Остров"})
    play = areas.async_create("Кухня-гостиная · Игровая", aliases={"Алиса: Игровая"})
    bed = areas.async_create("Спальня")
    reg = er.async_get(hass)
    cfg = MockConfigEntry(domain="test")
    cfg.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=cfg.entry_id, identifiers={("t", "play")})
    dr.async_get(hass).async_update_device(dev.id, area_id=play.id)
    for oid, area, kw in (("island", island.id, {}), ("play", None, {"device_id": dev.id}), ("room", room.id, {}), ("bed", bed.id, {})):
        e = reg.async_get_or_create("light", "test", oid, suggested_object_id=oid, config_entry=cfg, **kw)
        reg.async_update_entity(e.entity_id, area_id=area)
        hass.states.async_set(e.entity_id, "on", {"friendly_name": oid})
    reg.async_get_or_create("light", "test", "nowhere", suggested_object_id="nowhere")
    return {"room": room, "island": island, "play": play, "bed": bed}


def _plan(h, **extra):
    return {"create": 1, "zones": {h["island"].id: h["room"].id, h["play"].id: h["room"].id}, **extra}


def test_clean_plan_keeps_area_ids_only() -> None:
    clean = own.clean_plan
    assert "zones" not in clean({"zones": None}) and "zones" not in clean({"zones": ["a", "b"]}) and "zones" not in clean({"zones": {}})
    got = clean({"zones": {"z1": "room", "z2": "z2", "Z3": "room", "z4": 5, "z5": "bad id", 7: "room", "z6": "room"}})["zones"]
    assert got == {"z1": "room", "z6": "room"}  # a self-map, upper case, a non-string, a space — all dropped
    with patch.object(own, "ZONES_MAX", 2):
        assert clean({"zones": {f"z{i}": "room" for i in range(5)}})["zones"] == {"z0": "room", "z1": "room"}


async def test_without_a_plan_an_area_is_itself(hass: HomeAssistant, house) -> None:
    h = house
    assert own.room_area(hass, h["island"].id) == h["island"].id and own.room_area(hass, None) is None
    assert own.entity_room(hass, "light.island") == h["island"].id  # an old backend / no zones: as before
    assert own.entity_room(hass, "light.ghost") is None and own.entity_room(hass, "light.nowhere") is None


async def test_plan_zones_published_kept_over_restart_and_out_of_next_deploy(hass: HomeAssistant, entry, house) -> None:
    h, agent = house, entry.runtime_data
    await agent.ownership.async_set_plan(_plan(h))
    assert hass.data[own.ZONES_KEY] == {h["island"].id: h["room"].id, h["play"].id: h["room"].id}
    assert own.entity_room(hass, "light.island") == h["room"].id and own.entity_room(hass, "light.play") == h["room"].id
    assert own.entity_room(hass, "light.bed") == h["bed"].id
    facts = agent.ownership.facts()
    assert facts["next_deploy"]["create"] == 1 and "zones" not in facts["next_deploy"]  # the map is the agent's, not «what the next deploy does»
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert own.ZONES_KEY not in hass.data
    async def idle(self):
        return None

    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert own.entity_room(hass, "light.play") == h["room"].id  # stored with the plan: a restart keeps it
    await entry.runtime_data.ownership.async_set_plan({"create": 0})  # a plan without zones (zones switched off) drops it
    assert hass.data[own.ZONES_KEY] == {} and own.entity_room(hass, "light.play") == h["play"].id


async def test_every_room_question_gets_the_room(hass: HomeAssistant, entry, house) -> None:
    h, agent = house, entry.runtime_data
    await agent.ownership.async_set_plan(_plan(h))
    room = h["room"].id
    # order: the room's block on the dashboard holds the zones' devices (and a zone's block — its own)
    assert order._area_members(hass, room) == {"light.island", "light.play", "light.room"}
    assert order._area_members(hass, h["island"].id) == {"light.island"}
    # presence: a zone's light is the room's in «имитация присутствия»
    rooms = Presence(hass, lambda eid: True).rooms(["light.island", "light.play", "light.bed", "light.ghost"])
    assert rooms["light.island"] == rooms["light.play"] == (room, "Кухня-гостиная") and rooms["light.bed"][1] == "Спальня"
    assert rooms["light.ghost"] == ("light.ghost", "light.ghost")
    # scenes: a family scene of both zones lands in the room; zones + bedroom — no one room
    scenes = FamilyScenes(hass, lambda eid: True)
    assert scenes._area_of(["light.island", "light.play"]) == room and scenes._area_of(["light.island", "light.bed"]) is None
    # the analogs: recipes, guests, insights, alarms
    assert Automations(hass, lambda eid: True)._area("light.play") == room
    assert agent.guest._area("light.island") == room
    assert Insights(hass, lambda eid: True, lambda: None)._area_id("light.island") == room
    assert NotifyEngine(hass)._where(hass.states.get("light.play")) == "Кухня-гостиная"  # the alarm names the room, not the zone
    assert NotifyEngine(hass)._where(hass.states.get("light.nowhere") or hass.states.async_set("light.nowhere", "on") or hass.states.get("light.nowhere")) == ""


async def test_voice_finds_a_zone_thermostat_by_room_and_by_zone(hass: HomeAssistant, entry, house) -> None:
    h = house
    add_climate(hass, "isl", "Термостат острова", area=h["island"].id, attrs={"current_temperature": 22.0})
    add_climate(hass, "bed", "Термостат спальни", area=h["bed"].id, attrs={"current_temperature": 19.0})
    assert "не нашёл" in await say(hass, "HaufloGetTemperature", "Кухня-гостиная")  # no map yet: the room holds nothing
    await entry.runtime_data.ownership.async_set_plan(_plan(h))
    assert (await say(hass, "HaufloGetTemperature", "Кухня-гостиная")).endswith("22°.")
    assert (await say(hass, "HaufloGetTemperature", "Алиса: Остров")).endswith("22°.")  # the zone by its own alias still works
    assert "не нашёл" in await say(hass, "HaufloGetTemperature", "Алиса: Игровая")


async def test_house_gives_the_cards_the_map(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry, house) -> None:
    h = house
    family = await hass_client(hass_read_only_access_token)
    assert (await (await family.get("/api/hauflo/house")).json())["zones"] == {}  # nothing told: the cards behave as before
    await entry.runtime_data.ownership.async_set_plan(_plan(h))
    assert (await (await family.get("/api/hauflo/house")).json())["zones"] == {h["island"].id: h["room"].id, h["play"].id: h["room"].id}
