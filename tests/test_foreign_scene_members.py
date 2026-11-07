"""Other integrations' lights / blinds in Hauflo's scenes and groups (399-6, feature «foreign_scene_members»), red team:
only what the integrator listed, only a light / cover of a real integration, never protected (a lock, the alarm, a
valve, a garage door / gate, a «Switch as X» light over a lock, a group holding a lock), never disabled or hidden by the
family; a group never hides them; their registry stays out of reach, a Hauflo scene / group deleted leaves them as they
were. Real HA group flows, the agent's real view and tunnel checks."""

import json
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hauflo as m
from custom_components.hauflo import foreign
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN
from tests.test_allowlist_knx import knx

FLOW = "/api/config/config_entries/flow"
OPTIONS = "/api/config/config_entries/options/flow"
SCENE = "/api/config/scene/config/hauflo_r1_evening"

GOOD = ["light.matter_lamp", "cover.matter_blind", "light.hidden_by_integration", "light.user_group"]
# entity → why it is refused even when listed
BAD = {
    "light.lamp_over_lock": "protected",  # «Switch as X» light over the front door lock
    "light.group_with_lock": "protected",  # a light group whose members hold a lock
    "cover.garage": "protected",  # its class: a garage door
    "cover.gate_by_name": "gate",  # no class, named as a gate
    "light.off_by_user": "disabled",
    "light.hidden_by_user": "hidden_by_user",
    "light.yaml_one": "no_config_entry",
    "light.template_nothing": "unreadable_helper",  # a template light whose actions name nothing
    "light.knx_by_hand": "platform_not_allowed",  # KNX goes Hauflo's own way, never by the list
    "light.not_loaded": "no_state",
}


async def _idle(self):
    return None


@pytest.fixture
async def house(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    cfg = {d: MockConfigEntry(domain=d, title=d, options=o) for d, o in (("matter", {}), ("knx", {}), ("group", {}), ("template", {}), ("switch_as_x", {"entity_id": "lock.front", "target_domain": "light"}))}
    for c in cfg.values():
        c.add_to_hass(hass)
    reg = er.async_get(hass)

    def ent(eid, platform, entry="matter", state="on", attrs=None, **kw):
        domain, obj = eid.split(".")
        e = reg.async_get_or_create(domain, platform, f"u-{obj}", suggested_object_id=obj, config_entry=cfg[entry] if entry else None, **kw)
        if state is not None:
            hass.states.async_set(e.entity_id, state, attrs or {})
        return e.entity_id

    ent("light.matter_lamp", "matter")
    ent("cover.matter_blind", "matter", state="open", attrs={"device_class": "shade", "friendly_name": "Штора гостиная"})
    ent("light.hidden_by_integration", "matter", hidden_by=er.RegistryEntryHider.INTEGRATION)
    ent("light.user_group", "group", "group", attrs={"entity_id": ["light.matter_lamp"]})
    ent("lock.front", "matter", state="locked")
    ent("alarm_control_panel.home", "matter", state="armed_away")
    ent("valve.water", "matter", state="open")
    ent("switch.relay", "matter", state="off")
    ent("light.lamp_over_lock", "switch_as_x", "switch_as_x")
    ent("light.group_with_lock", "group", "group", attrs={"entity_id": ["light.matter_lamp", "lock.front"]})
    ent("cover.garage", "matter", state="closed", attrs={"device_class": "garage"})
    ent("cover.gate_by_name", "matter", state="closed", attrs={"friendly_name": "Ворота"})
    ent("light.off_by_user", "matter", disabled_by=er.RegistryEntryDisabler.USER, state=None)
    ent("light.hidden_by_user", "matter", hidden_by=er.RegistryEntryHider.USER)
    ent("light.yaml_one", "template", None)
    ent("light.template_nothing", "template", "template")
    ent("light.knx_by_hand", "knx", "knx")
    ent("light.not_loaded", "matter", state=None)
    ent("light.not_listed", "matter")
    ent("light.hauflo_knx", "knx", "knx")
    hass.data["knx"] = knx({"light": {"u-hauflo_knx": {"knx": {"ga": {"write": "1/1/1"}}}}})
    agent = entry.runtime_data
    yield {"agent": agent, "owned": {"u-hauflo_knx"}, "entries": set()}


async def _members(hass_client, ids, status=200):
    res = await (await hass_client()).post("/api/hauflo/foreign", json={"members": ids})
    assert res.status == status, await res.text()
    return await res.json()


def allowed(hass, h, method, path, body=None):
    return m._http_allowed(hass, method, path, json.dumps(body) if body is not None else None, h["entries"], {}, h["owned"])


def scene(*ids):
    return {"entities": {i: {"state": "on" if i.startswith("light.") else "open"} for i in ids}}


async def test_the_list_says_each_refusal_and_bad_bodies_are_refused(hass, hass_client, hass_read_only_access_token, house):
    got = await _members(hass_client, [*GOOD, *BAD])
    assert got["refused"] == BAD
    assert foreign.flags(hass).members == {*GOOD, *BAD}
    for eid in GOOD:
        assert foreign.member_refusal(hass, eid) is None, eid
    assert foreign.member_refusal(hass, "light.not_listed") == "not_in_plan"
    # Never a lock / the alarm / a valve / a relay on the list at all: the whole body refused, the list kept as it was.
    for bad in (["lock.front"], ["alarm_control_panel.home"], ["valve.water"], ["switch.relay"], "light.matter_lamp", [5], ["light.Bad Id"], ["light.x"] * (foreign.MAX_IDS + 1)):
        await _members(hass_client, bad, 400)
    assert foreign.flags(hass).members == {*GOOD, *BAD}
    for eid in ("lock.front", "alarm_control_panel.home", "valve.water", "switch.relay", None, 5):
        assert foreign.member_refusal(hass, eid) == "domain_not_allowed"
    user = await hass_client(hass_read_only_access_token)
    assert (await user.post("/api/hauflo/foreign", json={"members": []})).status == 403
    # The whole set each time: what is left off is out.
    await _members(hass_client, ["cover.matter_blind"])
    assert foreign.member_refusal(hass, "light.matter_lamp") == "not_in_plan"
    assert foreign.member_ok(hass, "cover.matter_blind")
    # Said in the view and in the hello / status.
    got = await (await (await hass_client()).get("/api/hauflo/foreign")).json()
    assert foreign.MEMBERS_FEATURE in got["features"] and foreign.MEMBERS_FEATURE in m.FEATURES
    assert foreign.MEMBERS_FEATURE in house["agent"].status()["features"]


async def test_kept_across_a_restart(hass, hass_client, house):
    await _members(hass_client, ["light.matter_lamp"])
    await hass.async_block_till_done()
    f = foreign.Flags(hass)
    await f.async_load()
    assert f.members == {"light.matter_lamp"}


async def test_a_scene_takes_a_listed_matter_light_and_blind_and_nothing_else(hass, hass_client, house):
    # Before the list: as before 399-6 — only Hauflo's own.
    assert allowed(hass, house, "POST", SCENE, scene("light.hauflo_knx"))
    assert not allowed(hass, house, "POST", SCENE, scene("light.matter_lamp"))
    await _members(hass_client, [*GOOD, *BAD])
    assert allowed(hass, house, "POST", SCENE, scene("light.hauflo_knx", "light.matter_lamp", "cover.matter_blind", "light.user_group"))
    for eid in [*BAD, "light.not_listed", "lock.front", "alarm_control_panel.home", "valve.water", "switch.relay"]:
        assert not allowed(hass, house, "POST", SCENE, scene("light.matter_lamp", eid)), eid
    # One bad entity spoils the whole scene, not just itself; the family's scenes stay theirs.
    assert not allowed(hass, house, "POST", "/api/config/scene/config/hauflo_fam_r1_l_1", scene("light.matter_lamp"))
    assert not allowed(hass, house, "POST", "/api/config/scene/config/my_scene", scene("light.matter_lamp"))
    # Disabled / hidden by the family after it was listed: refused from then on.
    er.async_get(hass).async_update_entity("light.matter_lamp", hidden_by=er.RegistryEntryHider.USER)
    assert not allowed(hass, house, "POST", SCENE, scene("light.matter_lamp"))
    er.async_get(hass).async_update_entity("light.matter_lamp", hidden_by=None, disabled_by=er.RegistryEntryDisabler.USER)
    assert not allowed(hass, house, "POST", SCENE, scene("light.matter_lamp"))
    # A group that later took a lock in: protected from then on.
    hass.states.async_set("light.user_group", "on", {"entity_id": ["lock.front"]})
    assert not allowed(hass, house, "POST", SCENE, scene("light.user_group"))


async def test_a_group_of_listed_lights_by_the_real_flow_never_hides_them(hass, hass_client, house):
    await _members(hass_client, [*GOOD, *BAD])
    menu = await hass.config_entries.flow.async_init("group", context={"source": "user"})
    assert allowed(hass, house, "POST", f"{FLOW}/{menu['flow_id']}", {"next_step_id": "light"})
    form = await hass.config_entries.flow.async_configure(menu["flow_id"], {"next_step_id": "light"})
    assert form["step_id"] == "light"
    fid = form["flow_id"]
    good = {"name": "Люстра", "entities": ["light.matter_lamp", "light.hidden_by_integration", "light.hauflo_knx"], "hide_members": False, "all": False}
    assert allowed(hass, house, "POST", f"{FLOW}/{fid}", good)
    for bad in (
        {**good, "hide_members": True},  # would hide the family's own lamps
        {**good, "entities": ["light.matter_lamp", "cover.matter_blind"]},  # a blind in a light group
        {**good, "entities": ["light.matter_lamp", "light.not_listed"]},
        {**good, "entities": ["light.matter_lamp", "light.group_with_lock"]},
        {**good, "entities": ["light.matter_lamp", "light.lamp_over_lock"]},
        {**good, "entities": ["light.matter_lamp", "lock.front"]},
        {**good, "entities": ["light.matter_lamp", "light.off_by_user"]},
        {**good, "entities": ["light.matter_lamp", "light.hidden_by_user"]},
        {**good, "entities": "light.matter_lamp"},
        {"hide_members": True},
    ):
        assert not allowed(hass, house, "POST", f"{FLOW}/{fid}", bad), bad
    done = await hass.config_entries.flow.async_configure(fid, good)
    assert done["type"] == "create_entry"
    gid = done["result"].entry_id
    house["entries"].add(gid)  # the agent saw it made through the tunnel
    reg = er.async_get(hass)
    assert reg.async_get("light.matter_lamp").hidden_by is None

    # Its options: the same rule (HA's step is the group's type).
    opts = await hass.config_entries.options.async_init(gid)
    assert opts["step_id"] == "light"
    ofid = opts["flow_id"]
    assert allowed(hass, house, "POST", f"{OPTIONS}/{ofid}", {"entities": ["light.matter_lamp", "light.user_group"], "hide_members": False, "all": False})
    assert not allowed(hass, house, "POST", f"{OPTIONS}/{ofid}", {"entities": ["light.matter_lamp"], "hide_members": True, "all": False})
    assert not allowed(hass, house, "POST", f"{OPTIONS}/{ofid}", {"hide_members": True})
    assert not allowed(hass, house, "POST", f"{OPTIONS}/{ofid}", {"entities": ["light.matter_lamp", "light.group_with_lock"], "hide_members": False, "all": False})

    # A cover group: blinds only, of the list.
    menu = await hass.config_entries.flow.async_init("group", context={"source": "user"})
    form = await hass.config_entries.flow.async_configure(menu["flow_id"], {"next_step_id": "cover"})
    assert form["step_id"] == "cover"
    assert allowed(hass, house, "POST", f"{FLOW}/{form['flow_id']}", {"name": "Шторы", "entities": ["cover.matter_blind"], "hide_members": False})
    for eid in ("cover.garage", "cover.gate_by_name", "light.matter_lamp"):
        assert not allowed(hass, house, "POST", f"{FLOW}/{form['flow_id']}", {"name": "Шторы", "entities": ["cover.matter_blind", eid], "hide_members": False}), eid

    # Hauflo's group / scene removed: the foreign lamp as it was; its registry never Hauflo's to change.
    assert allowed(hass, house, "DELETE", f"/api/config/config_entries/entry/{gid}")
    assert allowed(hass, house, "DELETE", SCENE)
    assert await hass.config_entries.async_remove(gid)
    lamp = reg.async_get("light.matter_lamp")
    assert lamp is not None and lamp.hidden_by is None and lamp.disabled_by is None
    for msg in (
        {"type": "config/entity_registry/update", "entity_id": "light.matter_lamp", "hidden_by": "user"},
        {"type": "config/entity_registry/update", "entity_id": "light.matter_lamp", "new_entity_id": "light.x"},
        {"type": "config/entity_registry/remove", "entity_id": "light.matter_lamp"},
        {"type": "config/entity_registry/remove", "entity_id": "cover.matter_blind"},
    ):
        assert not m._ws_allowed(msg, hass, house["owned"], house["entries"]), msg


async def test_family_scenes_take_the_listed_ones_too(hass, hass_client, house):
    fam = m._scenes(hass)
    assert not fam.owns("light.matter_lamp")
    await _members(hass_client, ["light.matter_lamp", "light.lamp_over_lock"])
    assert fam.owns("light.matter_lamp")
    assert not fam.owns("light.lamp_over_lock") and not fam.owns("lock.front")
