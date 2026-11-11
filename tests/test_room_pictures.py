"""Pictures for rooms (room_pictures.py, Р54): the canon by name (RU + EN; utility types show theirs by themselves,
living types only offer), its files really there and small,
the two websocket commands — options of a room's type, and set / clear of the area's picture (only an address of this
home, never for a restricted profile)."""

from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from pytest_homeassistant_custom_component.common import MockConfigEntry
from unittest.mock import patch

from custom_components.hauflo import room_pictures as rp
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

WWW = Path(rp.__file__).parent / "www" / "rooms"
NAMES = {
    "Котельная": "boiler", "Boiler room": "boiler", "Водоузел": "water", "Система протечек": "water", "Water supply": "water",
    "Электрощитовая": "electrical", "Щитовая": "electrical", "Electrical panel": "electrical", "Серверная": "server", "Слаботочка": "server",
    "Server room": "server", "Прачечная": "laundry", "Laundry": "laundry", "Кладовая": "storage", "Pantry": "storage", "Гараж": "garage",
    "Garage": "garage", "Насосная": "pump", "Pump room": "pump", "Вентиляционная": "ventilation", "Ventilation": "ventilation",
    "Тех. помещение бассейна": "pool", "Pool plant room": "pool",
}
LIVING = {
    "Гостиная": "living", "Кухня-гостиная": "living", "Зал": "living", "Living room": "living", "Спальня": "bedroom", "Master bedroom": "bedroom",
    "Гостевая спальня": "guest", "Guest room": "guest", "Детская": "kids", "Kids room": "kids", "Кухня": "kitchen", "Kitchen": "kitchen",
    "Столовая": "dining", "Dining": "dining", "Ванная": "bathroom", "Bathroom": "bathroom", "Санузел": "toilet", "Туалет": "toilet", "WC": "toilet",
    "Прихожая": "hallway", "Холл": "hallway", "Hall": "hallway", "Коридор": "corridor", "Corridor": "corridor", "Кабинет": "office", "Office": "office",
    "Гардеробная": "closet", "Walk-in closet": "closet", "Терраса": "terrace", "Балкон": "terrace", "Patio": "terrace", "Лестница": "stairs", "Stairs": "stairs",
    "Спортзал": "gym", "Gym": "gym", "Сауна": "sauna", "Баня": "sauna", "Sauna": "sauna", "Кинотеатр": "cinema", "Home theater": "cinema",
    "Двор": "garden", "Сад": "garden", "Garden": "garden", "Backyard": "garden",
}


def test_every_type_has_three_real_small_files() -> None:
    assert len(rp.TYPES) == 29 and rp.FALLBACK["id"] == "generic" and not rp.FALLBACK["match"]
    total = 0
    for kind in rp.TYPES:
        assert len(kind["files"]) == 3 and kind["icon"].startswith("mdi:")
        for f in kind["files"]:
            size = (WWW / f).stat().st_size
            assert 0 < size <= 80 * 1024, f
            total += size
    assert total <= 5 * 1024 * 1024
    assert [t["auto"] for t in rp.TYPES] == [True] * 10 + [False] * 19


@pytest.mark.parametrize("name,kind", NAMES.items())
def test_kind_by_name(name: str, kind: str) -> None:
    assert rp.kind_of(name)["id"] == kind
    assert rp.default_picture(name) == f"/hauflo_static/rooms/{kind}-1.webp"


@pytest.mark.parametrize("name,kind", LIVING.items())
def test_living_kind_offered_not_shown(name: str, kind: str) -> None:
    assert rp.kind_of(name)["id"] == kind
    assert rp.default_picture(name) is None


@pytest.mark.parametrize("name", ["Track", "Залив", "Кабанчик", "Посадка", "", None, 5])
def test_unknown_room_has_none(name) -> None:
    assert rp.kind_of(name) is None and rp.default_picture(name) is None


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


async def test_options_and_set(hass: HomeAssistant, hass_ws_client, entry) -> None:
    boiler = ar.async_get(hass).async_create("Котельная")
    living = ar.async_get(hass).async_create("Гостиная")
    track = ar.async_get(hass).async_create("Track")
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": rp.OPTIONS, "area_id": boiler.id})
    got = (await ws.receive_json())["result"]
    assert [o["url"] for o in got] == [f"/hauflo_static/rooms/boiler-{n}.webp" for n in (1, 2, 3)]
    assert got[0]["label"] in ("Option 1", "Вариант 1")
    await ws.send_json({"id": 2, "type": rp.OPTIONS, "area_id": living.id})
    assert [o["url"] for o in (await ws.receive_json())["result"]] == [f"/hauflo_static/rooms/living-{n}.webp" for n in (1, 2, 3)]
    await ws.send_json({"id": 3, "type": rp.OPTIONS, "area_id": track.id})
    assert [o["url"] for o in (await ws.receive_json())["result"]] == [f"/hauflo_static/rooms/generic-{n}.webp" for n in (1, 2, 3)]
    await ws.send_json({"id": 4, "type": rp.OPTIONS, "area_id": "nope"})
    assert (await ws.receive_json())["error"]["code"] == "not_found"
    # Set one of the three, then clear.
    await ws.send_json({"id": 5, "type": rp.SET, "area_id": boiler.id, "picture": got[1]["url"]})
    assert (await ws.receive_json())["success"]
    assert ar.async_get(hass).async_get_area(boiler.id).picture == got[1]["url"]
    await ws.send_json({"id": 6, "type": rp.SET, "area_id": boiler.id, "picture": None})
    assert (await ws.receive_json())["success"]
    assert ar.async_get(hass).async_get_area(boiler.id).picture is None
    # An uploaded image of this HA is fine; another site's address, a path trick, a script are not; no such room.
    await ws.send_json({"id": 7, "type": rp.SET, "area_id": living.id, "picture": "/api/image/serve/abc123/512x512"})
    assert (await ws.receive_json())["success"]
    for n, bad in enumerate(["https://evil.example/x.jpg", "/hauflo_static/../secret", "javascript:alert(1)", "/local//x.png"]):
        await ws.send_json({"id": 10 + n, "type": rp.SET, "area_id": living.id, "picture": bad})
        assert (await ws.receive_json())["error"]["code"] == "invalid_format", bad
    await ws.send_json({"id": 20, "type": rp.SET, "area_id": "nope", "picture": None})
    assert (await ws.receive_json())["error"]["code"] == "not_found"


async def test_restricted_profile_cannot_set(hass: HomeAssistant, hass_ws_client, entry) -> None:
    area = ar.async_get(hass).async_create("Гараж")
    ws = await hass_ws_client(hass)
    with patch("custom_components.hauflo.room_pictures.may_edit", return_value=False):
        await ws.send_json({"id": 1, "type": rp.SET, "area_id": area.id, "picture": None})
        assert (await ws.receive_json())["error"]["code"] == "not_allowed"
