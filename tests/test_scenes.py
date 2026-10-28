"""The family's own room scenes from the dashboard (scenes.py, owner 28.10): «+» → «Запомнить как сейчас» saves what the
room's Hauflo lights / blinds do now as a real HA scene `hauflo_fam_…` in scenes.yaml; rename / remember again / delete;
an integrator's Hauflo scene may be renamed / re-captured, «deleted» only hidden; any other scene never touched; any
signed-in person may do it, the tunnel may not."""

from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import dump, load_yaml
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import _http_allowed
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

URL = "/api/hauflo/scenes"
LIGHT = "light.bed_ceiling"
LAMP = "light.bed_lamp"
BLIND = "cover.bed_blind"
GARAGE = "cover.garage_door"
HAND = "light.integrator_hand"  # a KNX light the integrator made by hand — not Hauflo's
THEIRS = {"id": "1700000000000", "name": "Integrator: evening", "entities": {HAND: {"state": "on"}}}
HAUFLO = {"id": "hauflo_bed_bright", "name": "Яркий", "icon": "mdi:lightbulb-group", "entities": {LIGHT: {"state": "on", "brightness": 255}}}


def _file(hass: HomeAssistant) -> list[dict]:
    return load_yaml(hass.config.path("scenes.yaml")) or []


@pytest.fixture
async def entry(hass: HomeAssistant, tmp_path: Path):
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text("scene: !include scenes.yaml\n")
    (tmp_path / "scenes.yaml").write_text(dump([THEIRS, HAUFLO]))
    reg = er.async_get(hass)
    for eid, uid, state, extra in (
        (LIGHT, "u-light", "on", {"brightness": 128, "supported_color_modes": ["brightness"], "color_mode": "brightness"}),
        (LAMP, "u-lamp", "off", {"supported_color_modes": ["onoff"]}),
        (BLIND, "u-blind", "open", {"supported_features": 15 | 128, "current_position": 40, "current_tilt_position": 70}),
        (GARAGE, "u-garage", "closed", {"device_class": "garage", "supported_features": 3}),
        (HAND, "u-hand", "on", {"supported_color_modes": ["onoff"]}),
    ):
        domain, obj = eid.split(".")
        reg.async_get_or_create(domain, "knx", uid, suggested_object_id=obj)
        hass.states.async_set(eid, state, extra)
    assert await async_setup_component(hass, "scene", {"scene": [THEIRS, HAUFLO]})
    await hass.async_block_till_done()

    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.owned |= {"u-light", "u-lamp", "u-blind", "u-garage"}  # «u-hand»: the integrator's
    yield entry


async def test_family_scene_end_to_end(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry) -> None:
    """A non-admin person: add (lights as they are now), rename, remember again, delete — only their scene changes."""
    client = await hass_client(hass_read_only_access_token)
    res = await client.post(URL, json={"op": "add", "room": "bed", "kind": "l", "name": "  Кино ", "entities": [LIGHT, LAMP, HAND]})
    assert res.status == 200, await res.text()
    made = await res.json()
    assert made["id"].startswith("hauflo_fam_bed_l_") and made["name"] == "Кино"
    eid = made["entity_id"]
    assert eid and hass.states.get(eid) is not None
    mine = next(s for s in _file(hass) if s["id"] == made["id"])
    # What the lamps do now; the integrator's own lamp is never in it.
    assert mine["entities"] == {LIGHT: {"state": "on", "brightness": 128}, LAMP: {"state": "off"}}
    assert THEIRS in _file(hass) and HAUFLO in _file(hass)

    assert (await client.post(URL, json={"op": "rename", "entity_id": eid, "name": "Фильм"})).status == 200
    assert next(s for s in _file(hass) if s["id"] == made["id"])["name"] == "Фильм"

    hass.states.async_set(LAMP, "on", {"supported_color_modes": ["onoff"]})
    assert (await client.post(URL, json={"op": "recapture", "entity_id": eid, "entities": [LIGHT, LAMP]})).status == 200
    assert next(s for s in _file(hass) if s["id"] == made["id"])["entities"][LAMP] == {"state": "on"}

    assert (await client.post(URL, json={"op": "delete", "entity_id": eid})).status == 200
    assert [s["id"] for s in _file(hass)] == [THEIRS["id"], HAUFLO["id"]]
    assert er.async_get(hass).async_get(eid) is None
    # Before every write a copy of the file (the last 3).
    assert sorted(p.name for p in Path(hass.config.path()).glob("scenes.yaml.hauflo-bak-*"))


async def test_blinds_position_and_slats(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    res = await client.post(URL, json={"op": "add", "room": "bed", "kind": "c", "name": "Утро", "entities": [BLIND, GARAGE, LIGHT]})
    assert res.status == 200, await res.text()
    made = (await res.json())["id"]
    mine = next(s for s in _file(hass) if s["id"] == made)
    # Only the blind: a garage door never goes into a scene, a light not into a blinds scene.
    assert mine["entities"] == {BLIND: {"state": "open", "current_position": 40, "current_tilt_position": 70}}


async def test_integrator_scenes(hass: HomeAssistant, hass_client, entry) -> None:
    """Hauflo's scene of the integrator: renamed / re-captured in place; «delete» hides it (a deploy would make it again);
    a scene not made by Hauflo is refused."""
    client = await hass_client()
    hauflo = er.async_get(hass).async_get_entity_id("scene", "homeassistant", HAUFLO["id"])
    theirs = er.async_get(hass).async_get_entity_id("scene", "homeassistant", THEIRS["id"])
    assert (await client.post(URL, json={"op": "rename", "entity_id": hauflo, "name": "Вечер у камина"})).status == 200
    assert next(s for s in _file(hass) if s["id"] == HAUFLO["id"])["name"] == "Вечер у камина"
    res = await client.post(URL, json={"op": "delete", "entity_id": hauflo})
    assert res.status == 200 and (await res.json()) == {"hidden": hauflo}
    assert HAUFLO["id"] in [s["id"] for s in _file(hass)]
    assert (await (await client.get(URL)).json())["hidden"] == [hauflo]
    assert (await client.post(URL, json={"op": "unhide", "entity_id": hauflo})).status == 200
    assert (await (await client.get(URL)).json())["hidden"] == []
    for op in ("rename", "recapture", "delete"):
        res = await client.post(URL, json={"op": op, "entity_id": theirs, "name": "x", "entities": [LIGHT]})
        assert res.status == 403, op
    assert THEIRS in _file(hass)


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"op": "add", "room": "bed", "kind": "l", "name": "", "entities": [LIGHT]}, 400),
        ({"op": "add", "room": "bed", "kind": "l", "name": "​​", "entities": [LIGHT]}, 400),
        ({"op": "add", "room": "../x", "kind": "l", "name": "A", "entities": [LIGHT]}, 400),
        ({"op": "add", "room": "bed", "kind": "z", "name": "A", "entities": [LIGHT]}, 400),
        ({"op": "add", "room": "bed", "kind": "l", "name": "A", "entities": [HAND]}, 409),  # nothing of Hauflo's to remember
        ({"op": "add", "room": "bed", "kind": "l", "name": "A", "entities": "light.bed_ceiling"}, 400),
        ({"op": "boom"}, 400),
    ],
)
async def test_refused(hass: HomeAssistant, hass_client, entry, body, status) -> None:
    client = await hass_client()
    before = _file(hass)
    res = await client.post(URL, json=body)
    assert res.status == status, await res.text()
    assert _file(hass) == before


async def test_hand_written_file_left_alone(hass: HomeAssistant, hass_client, entry) -> None:
    path = Path(hass.config.path("scenes.yaml"))
    path.write_text("# my scenes\n" + dump([THEIRS]))
    client = await hass_client()
    res = await client.post(URL, json={"op": "add", "room": "bed", "kind": "l", "name": "A", "entities": [LIGHT]})
    assert res.status == 409 and (await res.json())["code"] == "not_plain"
    assert path.read_text().startswith("# my scenes")


async def test_tunnel_cannot_reach(hass: HomeAssistant) -> None:
    for method, body in (("GET", ""), ("POST", {"op": "add", "room": "bed", "kind": "l", "name": "A", "entities": [LIGHT]})):
        assert not _http_allowed(hass, method, URL, body)
    # Nor the family's scenes through HA's scene editor.
    for method in ("GET", "POST", "DELETE"):
        assert not _http_allowed(hass, method, "/api/config/scene/config/hauflo_fam_bed_l_6720a1b2c3", {"id": "x", "entities": {}})
