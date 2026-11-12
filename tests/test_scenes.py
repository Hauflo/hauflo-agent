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
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

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


# --- adaptive scenes (п.60): a light marked «Днём не включать в сценах» stays off while it is light outside ----------------
def _sun(hass: HomeAssistant, elevation: float) -> None:
    hass.states.async_set("sun.sun", "above_horizon" if elevation > 0 else "below_horizon", {"elevation": elevation})


def _fam_eid(hass: HomeAssistant) -> str:
    fam = next(s for s in _file(hass) if str(s["id"]).startswith("hauflo_fam_"))
    return er.async_get(hass).async_get_entity_id("scene", "homeassistant", fam["id"])


async def _two_light_scene(hass: HomeAssistant, client, state: str = "on") -> str:
    """A family scene with both lights in `state`; LAMP marked «Днём не включать в сценах»; both lights off after."""
    for eid in (LIGHT, LAMP):
        hass.states.async_set(eid, state, {"supported_color_modes": ["onoff"]})
    res = await client.post(URL, json={"op": "add", "room": "bed", "kind": "l", "name": "Вечер", "entities": [LIGHT, LAMP]})
    assert res.status == 200, await res.text()
    for eid in (LIGHT, LAMP):
        hass.states.async_set(eid, "off", {"supported_color_modes": ["onoff"]})
    res = await client.post(URL, json={"op": "daylight_skip", "entity_id": LAMP, "on": True})
    assert res.status == 200 and (await res.json()) == {"skip": [LAMP]}, await res.text()
    return _fam_eid(hass)


async def test_apply_by_day_and_by_night(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    eid = await _two_light_scene(hass, client)
    on = async_mock_service(hass, "light", "turn_on")
    day = entry.runtime_data.daylight

    _sun(hass, 30)
    await day.refresh(force=True)
    res = await client.post(URL, json={"op": "apply", "entity_id": eid})
    assert res.status == 200, await res.text()
    assert await res.json() == {"applied": 1, "skipped": [LAMP]}
    assert [c.data["entity_id"] for c in on] == [LIGHT]  # the main light only

    on.clear()
    _sun(hass, -10)
    await day.refresh(force=True)
    res = await client.post(URL, json={"op": "apply", "entity_id": eid})
    assert (await res.json())["skipped"] == []
    assert sorted(c.data["entity_id"] for c in on) == sorted([LAMP, LIGHT])  # dark: the whole scene

    # Unknown outside (no sun, no sensor): the whole scene too.
    on.clear()
    hass.states.async_remove("sun.sun")
    await day.refresh(force=True)
    assert day.on is None
    assert (await (await client.post(URL, json={"op": "apply", "entity_id": eid})).json())["skipped"] == []
    assert sorted(c.data["entity_id"] for c in on) == sorted([LAMP, LIGHT])

    # GET tells the cards the skip list.
    got = (await (await client.get(URL)).json())["daylight"]
    assert got["skip"] == [LAMP] and got["lux"] == 1000 and got["sensor"] is None


async def test_apply_keeps_switching_off(hass: HomeAssistant, hass_client, entry) -> None:
    """A skip light a scene switches OFF is still switched off by day (an «all off» scene must stay an «all off»)."""
    client = await hass_client()
    eid = await _two_light_scene(hass, client, "off")
    off = async_mock_service(hass, "light", "turn_off")
    _sun(hass, 30)
    await entry.runtime_data.daylight.refresh(force=True)
    for e in (LIGHT, LAMP):
        hass.states.async_set(e, "on", {"supported_color_modes": ["onoff"]})
    res = await client.post(URL, json={"op": "apply", "entity_id": eid})
    assert (await res.json())["skipped"] == []
    assert sorted(c.data["entity_id"] for c in off) == sorted([LAMP, LIGHT])


async def test_apply_foreign_scene_is_turn_on(hass: HomeAssistant, hass_client, entry) -> None:
    """The integrator's hand-made / KNX scene: HA's scene.turn_on, nothing filtered, even with a skip light in it."""
    client = await hass_client()
    assert (await client.post(URL, json={"op": "daylight_skip", "entity_id": HAND, "on": True})).status == 200
    _sun(hass, 30)
    await entry.runtime_data.daylight.refresh(force=True)
    theirs = er.async_get(hass).async_get_entity_id("scene", "homeassistant", THEIRS["id"])
    calls = async_mock_service(hass, "scene", "turn_on")
    res = await client.post(URL, json={"op": "apply", "entity_id": theirs})
    assert await res.json() == {"applied": None, "skipped": []}
    assert [c.data["entity_id"] for c in calls] == [theirs]
    er.async_get(hass).async_get_or_create("scene", "knx", "u-knx-scene", suggested_object_id="knx_evening")
    hass.states.async_set("scene.knx_evening", "unknown")
    res = await client.post(URL, json={"op": "apply", "entity_id": "scene.knx_evening"})
    assert res.status == 200 and (await res.json())["skipped"] == []
    assert calls[-1].data["entity_id"] == "scene.knx_evening"
    for bad in ("light.bed_ceiling", "scene.nope", None, 5):
        assert (await client.post(URL, json={"op": "apply", "entity_id": bad})).status == 400, bad


async def test_restricted_may_apply_not_change(hass: HomeAssistant, hass_client, hass_read_only_user, hass_read_only_access_token, entry) -> None:
    admin = await hass_client()
    eid = await _two_light_scene(hass, admin)
    async_mock_service(hass, "light", "turn_on")
    _sun(hass, 30)
    await entry.runtime_data.daylight.refresh(force=True)
    assert (await admin.post("/api/hauflo/house", json={"restricted": [hass_read_only_user.id]})).status == 200
    child = await hass_client(hass_read_only_access_token)
    res = await child.post(URL, json={"op": "apply", "entity_id": eid})
    assert res.status == 200 and (await res.json())["skipped"] == [LAMP]
    assert (await child.post(URL, json={"op": "daylight_skip", "entity_id": LAMP, "on": False})).status == 403
    assert (await child.post(URL, json={"op": "daylight_config", "lux": 500})).status == 403
    assert (await (await child.get(URL)).json())["daylight"]["skip"] == [LAMP]


async def test_config_admin_only(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry) -> None:
    hass.states.async_set("sensor.outdoor_lux", "300", {"unit_of_measurement": "lx"})
    hass.states.async_set("sensor.temp", "5", {"unit_of_measurement": "°C"})
    user = await hass_client(hass_read_only_access_token)  # signed in, not an admin, not restricted
    assert (await user.post(URL, json={"op": "daylight_config", "sensor": "sensor.outdoor_lux"})).status == 403
    admin = await hass_client()
    for body in ({"sensor": "sensor.temp"}, {"lux": 50}, {"lux": 20001}, {"skip": []}, {}, {"sensor": "light.x"}):
        res = await admin.post(URL, json={"op": "daylight_config", **body})
        assert res.status == 400, (body, await res.text())
    # A sensor HA does not have / not in lx (tester D2 / D3): said to the person in their language, with a code.
    for lang, eid, code, word in (("ru", "sensor.nowhere", "daylight_sensor_missing", "нет такого датчика"), ("en", "sensor.temp", "daylight_sensor_unit", "not in lux")):
        res = await admin.post(URL, json={"op": "daylight_config", "sensor": eid, "lang": lang})
        got = await res.json()
        assert res.status == 400 and got["code"] == code and word in got["message"], got
    res = await admin.post(URL, json={"op": "daylight_config", "sensor": "sensor.outdoor_lux", "lux": 200})
    assert res.status == 200, await res.text()
    got = await res.json()
    assert (got["on"], got["source"], got["sensor"], got["lux"]) == (True, "sensor", "sensor.outdoor_lux", 200)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.hauflo_daylight").attributes["source"] == "sensor"
    for body in ({"entity_id": BLIND, "on": True}, {"entity_id": LAMP, "on": "yes"}, {"entity_id": "light.nope", "on": True}):
        assert (await admin.post(URL, json={"op": "daylight_skip", **body})).status == 400, body


async def test_tunnel_cannot_reach_daylight(hass: HomeAssistant) -> None:
    for body in ({"op": "apply", "entity_id": "scene.x"}, {"op": "daylight_skip", "entity_id": LAMP, "on": True}, {"op": "daylight_config", "lux": 500}):
        assert not _http_allowed(hass, "POST", URL, body)


async def test_scene_editor_saving_meanwhile_is_never_lost(hass: HomeAssistant, hass_client, entry) -> None:
    """HA review M-2 (the same in scenes.yaml): HA's scene editor saves between Hauflo's read and write — its change
    stays, Hauflo's goes on top; the backup copy holds the text replaced."""
    from custom_components.hauflo import scenes, yamlfile

    other = {"id": "1700000000009", "name": "Integrator: night", "entities": {HAND: {"state": "off"}}}
    real = yamlfile.write

    def editor_first(path, *args, **kwargs):
        if path.endswith("scenes.yaml"):
            Path(path).write_text(dump([{**THEIRS, "name": "Integrator: evening 2"}, HAUFLO, other]))
        return real(path, *args, **kwargs)

    client = await hass_client()
    with patch.object(yamlfile, "write", editor_first):
        res = await client.post(URL, json={"op": "add", "room": "bed", "kind": "l", "name": "Кино", "entities": [LIGHT]})
    assert res.status == 200, await res.text()
    ids = [s["id"] for s in _file(hass)]
    assert ids[:3] == [THEIRS["id"], HAUFLO["id"], other["id"]] and ids[3].startswith("hauflo_fam_bed_l_")
    assert _file(hass)[0]["name"] == "Integrator: evening 2"
    backup = sorted(Path(hass.config.path()).glob("scenes.yaml.hauflo-bak-*"))[-1].read_text()
    assert "Integrator: night" in backup
    # A hand-formatted file meanwhile: nothing written over it.
    Path(hass.config.path("scenes.yaml")).write_text("# by hand\n- id: x\n  name: X\n  entities: {}\n")
    with pytest.raises(scenes.NotPlain):
        yamlfile.write(hass.config.path("scenes.yaml"), hass.config.path(scenes.BACKUP), 3, [], dump([THEIRS]), scenes.NotPlain, lambda rows: rows)
    assert Path(hass.config.path("scenes.yaml")).read_text().startswith("# by hand")


async def test_refusals_said_in_the_page_language(hass: HomeAssistant, hass_client, entry) -> None:
    """Р83 polish: a name of dots, a 61st scene, a scene deleted meanwhile, a full «Днём не включать» list — sentences of
    the page's language with a code, not «name» / «no such scene»."""
    import json  # noqa: PLC0415

    from custom_components.hauflo import daylight, scenes  # noqa: PLC0415

    client = await hass_client()
    tr = lambda key, lang, **ph: json.loads((Path(scenes.__file__).parent / "translations" / f"{lang}.json").read_text())["exceptions"][key]["message"].format(**ph)  # noqa: E731
    for lang in ("ru", "en"):
        res = await client.post(URL, json={"op": "add", "room": "bed", "kind": "l", "name": "…", "entities": [LIGHT], "lang": lang})
        assert res.status == 400 and await res.json() == {"message": tr("scenes_bad_name", lang), "code": "bad_name"}
        # p.11a 12.11: a name over the limit is refused with the limit, never cut to it.
        res = await client.post(URL, json={"op": "add", "room": "bed", "kind": "l", "name": "x" * (scenes.MAX_NAME + 1), "entities": [LIGHT], "lang": lang})
        assert res.status == 400 and await res.json() == {"message": tr("scenes_name_long", lang, n=scenes.MAX_NAME), "code": "name_long"}
        res = await client.post(URL, json={"op": "add", "room": "bed", "kind": "l", "name": "A", "entities": "light.bed_ceiling", "lang": lang})
        assert res.status == 400 and (await res.json())["code"] == "bad_body"
        for op in ("delete", "rename", "recapture"):  # deleted on another phone meanwhile
            res = await client.post(URL, json={"op": op, "entity_id": "scene.gone", "name": "A", "entities": [LIGHT], "lang": lang})
            assert res.status == 404 and await res.json() == {"message": tr("scenes_no_scene", lang), "code": "no_scene"}, op
        er.async_get(hass).async_get_or_create("scene", "homeassistant", "hauflo_fam_bed_l_abc123", suggested_object_id="left")
        res = await client.post(URL, json={"op": "rename", "entity_id": "scene.left", "name": "A", "lang": lang})  # not in the file
        assert res.status == 404 and await res.json() == {"message": tr("scenes_no_scene", lang), "code": "no_scene"}
        with patch.object(scenes, "MAX_FAMILY", 0):
            res = await client.post(URL, json={"op": "add", "room": "bed", "kind": "l", "name": "Кино", "entities": [LIGHT], "lang": lang})
            assert res.status == 400 and await res.json() == {"message": tr("scenes_too_many", lang, n=0), "code": "too_many"}
        with patch.object(daylight, "MAX_SKIP", 0):
            res = await client.post(URL, json={"op": "daylight_skip", "entity_id": LAMP, "on": True, "lang": lang})
            assert res.status == 400 and await res.json() == {"message": tr("daylight_skip_full", lang, n=0), "code": "skip_full"}
