"""The client's order of a block's items (order.py, owner 28.10): for the whole house, any signed-in person, kept over
deploys; only a block of a Hauflo dashboard that is there now, only its own items, capped; never through the tunnel."""

import types
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import _http_allowed
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN
from custom_components.hauflo.order import MAX_ITEMS, parse_key, slot

LIGHTS = {"type": "custom:hauflo-lights", "title": "Свет", "block": "lights", "entities": ["light.a", {"entity": "light.b"}, "light.c"], "groups": ["light.all"], "scenes": [{"entity": "scene.evening"}], "hauflo_id": "v/x/s/lights:light.a"}
CONFIG = {
    "views": [
        {"path": "floor_1", "sections": [{"type": "grid", "cards": [
            {"type": "custom:hauflo-room", "name": "Кухня", "path": "/hauflo-home/floor_1_kitchen", "lights": ["light.k"]},
            {"type": "custom:hauflo-room", "name": "Спальня", "path": "/hauflo-home/floor_1_bed", "lights": ["light.s"]},
        ]}]},
        {"path": "floor_1_kitchen", "sections": [
            {"type": "grid", "cards": [LIGHTS, {"type": "custom:hauflo-covers", "title": "Шторы", "block": "covers", "entities": ["cover.x", "cover.y"]}]},
            # The tablet's copy of the same block: one order for both.
            {"type": "grid", "cards": [{"type": "vertical-stack", "cards": [{**LIGHTS, "hauflo_id": "v/x/s/lights:light.a#2"}]}]},
            {"type": "grid", "cards": [{"type": "custom:hauflo-area", "area": "boiler"}, {"type": "tile", "entity": "lock.door"}]},
        ]},
    ]
}
KEY = "hauflo-home|floor_1_kitchen|lights:lights:Свет"


class _Board:
    mode = "storage"

    def __init__(self, cfg):
        self.cfg = cfg

    async def async_load(self, force):
        return self.cfg


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    from homeassistant.components.lovelace.const import LOVELACE_DATA  # noqa: PLC0415

    hass.data[LOVELACE_DATA] = types.SimpleNamespace(dashboards={"hauflo-home": _Board(CONFIG), "hauflo-home-copy-202610280400": _Board(CONFIG), "lovelace": _Board(CONFIG)}, resources=None)
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    yield entry


def test_slot_is_the_cards_one() -> None:
    assert slot(LIGHTS) == "lights:lights:Свет"
    assert slot({"type": "custom:hauflo-room", "path": "/x/y", "name": "Кухня"}) == "room:tiles"
    assert slot({"type": "custom:hauflo-climate", "entity": "climate.a", "zones": [{"entity": "climate.b"}]}) == "climate:zones:"
    assert slot({"type": "custom:hauflo-scenes", "kind": "light", "title": "Сценарии"}) == "scenes:light:Сценарии"
    assert slot({"type": "custom:hauflo-area", "area": "boiler"}) == "area:boiler:"
    assert slot({"type": "tile", "entity": "light.a"}) is None
    # A room's untitled light and blind sliders: two blocks.
    assert slot({"type": "custom:hauflo-sliders", "entities": ["light.a", "light.b"]}) == "sliders:light:"
    assert slot({"type": "custom:hauflo-sliders", "entities": [{"entity": "cover.a"}]}) == "sliders:cover:"
    assert slot({"type": "custom:hauflo-status", "title": "Ещё", "entities": ["sensor.a"]}) == "status::Ещё"


def test_keys() -> None:
    assert parse_key(KEY) == ("hauflo-home", "floor_1_kitchen", "lights:lights:Свет")
    for bad in ("lovelace|v|lights::", "hauflo-home-copy-202610280400|v|x::", "hauflo-home|v", "hauflo-home||x", "hauflo-home|v|\nx", "x" * 400, 5, None):
        assert parse_key(bad) is None, bad


async def test_any_person_orders_a_block_for_the_house(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry) -> None:
    client = await hass_client(hass_read_only_access_token)  # the family isn't an admin
    assert (await (await client.get("/api/hauflo/order")).json())["order"] == {}
    res = await client.post("/api/hauflo/order", json={"key": KEY, "items": ["light.c", "light.all", "light.a", "scene.evening"]})
    assert res.status == 200
    assert (await res.json())["order"] == {KEY: ["light.c", "light.all", "light.a", "scene.evening"]}
    other = await hass_client()
    assert (await (await other.get("/api/hauflo/order")).json())["order"][KEY] == ["light.c", "light.all", "light.a", "scene.evening"]
    # Reset: the block is in Hauflo's order again.
    assert (await (await client.post("/api/hauflo/order", json={"key": KEY, "items": []})).json())["order"] == {}


async def test_only_the_blocks_own_items(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    # Not in the block (a lock, the blinds' items), not entity ids, repeats, junk: dropped.
    got = await (await client.post("/api/hauflo/order", json={"key": KEY, "items": ["lock.door", "cover.x", "light.b", "light.b", 5, {"entity": "light.a"}, "<b>", "light.a"]})).json()
    assert got["order"] == {KEY: ["light.b", "light.a"]}
    # Nothing of it left: the same as a reset.
    assert (await (await client.post("/api/hauflo/order", json={"key": KEY, "items": ["lock.door"]})).json())["order"] == {}
    # The room tiles of a floor: their paths.
    tiles = "hauflo-home|floor_1|room:tiles"
    got = await (await client.post("/api/hauflo/order", json={"key": tiles, "items": ["/hauflo-home/floor_1_bed", "/hauflo-home/floor_1_kitchen", "/evil", "light.k"]})).json()
    assert got["order"][tiles] == ["/hauflo-home/floor_1_bed", "/hauflo-home/floor_1_kitchen", "light.k"]


async def test_area_block_holds_the_areas_entities(hass: HomeAssistant, hass_client, entry) -> None:
    area = ar.async_get(hass).async_create("Котельная")
    CONFIG["views"][1]["sections"][2]["cards"][0]["area"] = area.id
    reg = er.async_get(hass)
    reg.async_get_or_create("switch", "zont", "boiler", suggested_object_id="boiler")
    reg.async_update_entity("switch.boiler", area_id=area.id)
    zont = MockConfigEntry(domain="zont")
    zont.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=zont.entry_id, identifiers={("zont", "1")})
    dr.async_get(hass).async_update_device(dev.id, area_id=area.id)
    reg.async_get_or_create("sensor", "zont", "t", suggested_object_id="boiler_t", device_id=dev.id)
    reg.async_get_or_create("sensor", "zont", "other", suggested_object_id="other")
    client = await hass_client()
    key = f"hauflo-home|floor_1_kitchen|area:{area.id}:"
    got = await (await client.post("/api/hauflo/order", json={"key": key, "items": ["sensor.other", "sensor.boiler_t", "switch.boiler"]})).json()
    assert got["order"][key] == ["sensor.boiler_t", "switch.boiler"]


async def test_family_scenes_of_the_blocks_room(hass: HomeAssistant, hass_client, entry) -> None:
    # A scenes row with `scene_room` draws the family's own scenes of that room after the integrator's: they can be ordered.
    CONFIG["views"][1]["sections"][0]["cards"].append({"type": "custom:hauflo-scenes", "block": "scenes", "title": "Сценарии", "rows": [{"kind": "light", "scene_room": "kitchen", "scenes": [{"entity": "scene.evening"}]}]})
    hass.states.async_set("scene.mine", "scening", {"id": "hauflo_fam_kitchen_l_19a2b3c4"})
    hass.states.async_set("scene.other_room", "scening", {"id": "hauflo_fam_bedroom_l_19a2b3c4"})
    client = await hass_client()
    key = "hauflo-home|floor_1_kitchen|scenes:scenes:Сценарии"
    got = await (await client.post("/api/hauflo/order", json={"key": key, "items": ["scene.other_room", "scene.mine", "scene.evening"]})).json()
    assert got["order"][key] == ["scene.mine", "scene.evening"]
    CONFIG["views"][1]["sections"][0]["cards"].pop()


async def test_bad_requests(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    for body in (
        {"key": "hauflo-home|floor_1_kitchen|lights:lights:Нет такого", "items": ["light.a"]},  # no such block
        {"key": "hauflo-home|nope|lights:lights:Свет", "items": ["light.a"]},  # no such view
        {"key": "hauflo-other|floor_1_kitchen|lights:lights:Свет", "items": ["light.a"]},  # no such dashboard
        {"key": "lovelace|floor_1_kitchen|lights:lights:Свет", "items": ["light.a"]},  # not Hauflo's
        {"key": "hauflo-home-copy-202610280400|floor_1_kitchen|lights:lights:Свет", "items": ["light.a"]},  # a copy
        {"key": KEY, "items": "light.a"},
        {"key": KEY},
        {"items": ["light.a"]},
        [],
    ):
        assert (await client.post("/api/hauflo/order", json=body)).status == 400, body
    assert (await client.post("/api/hauflo/order", data="{")).status == 400


async def test_caps(hass: HomeAssistant, hass_client, entry) -> None:
    many = [f"light.l{i}" for i in range(MAX_ITEMS + 50)]
    CONFIG["views"][1]["sections"][0]["cards"][1]["entities"] = many
    client = await hass_client()
    key = "hauflo-home|floor_1_kitchen|covers:covers:Шторы"
    got = await (await client.post("/api/hauflo/order", json={"key": key, "items": list(reversed(many))})).json()
    assert len(got["order"][key]) == MAX_ITEMS and got["order"][key][0] == many[-1]
    CONFIG["views"][1]["sections"][0]["cards"][1]["entities"] = ["cover.x", "cover.y"]


async def test_clear_all(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    await client.post("/api/hauflo/order", json={"key": KEY, "items": ["light.c"]})
    assert (await (await client.post("/api/hauflo/order", json={"clear": True})).json())["order"] == {}


async def test_needs_a_signed_in_person(hass: HomeAssistant, hass_client_no_auth, entry) -> None:
    client = await hass_client_no_auth()
    assert (await client.get("/api/hauflo/order")).status == 401
    assert (await client.post("/api/hauflo/order", json={"key": KEY, "items": ["light.c"]})).status == 401


async def test_tunnel_cannot_order(hass: HomeAssistant) -> None:
    assert not _http_allowed(hass, "POST", "/api/hauflo/order", {"key": KEY, "items": ["light.c"]})
    assert not _http_allowed(hass, "GET", "/api/hauflo/order", "")


async def test_kept_after_a_restart_of_the_integration(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    await client.post("/api/hauflo/order", json={"key": KEY, "items": ["light.c", "light.a"]})
    assert await hass.config_entries.async_unload(entry.entry_id)

    async def idle(self):
        return None

    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert (await (await client.get("/api/hauflo/order")).json())["order"] == {KEY: ["light.c", "light.a"]}
