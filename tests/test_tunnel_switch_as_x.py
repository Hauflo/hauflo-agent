"""A lamp outlet's «Switch as X» light through the tunnel, against HA's real «switch_as_x» helper flows (backend
lampWrappers.ts): a light, over a switch Hauflo made — never over the integrator's relay, a lock or as another kind of
thing; options and the delete only of a helper the agent saw made."""

import json

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hauflo as m
from tests.test_allowlist_knx import knx

FLOW = "/api/config/config_entries/flow"
OPTIONS = "/api/config/config_entries/options/flow"


@pytest.fixture
def house(hass):
    """Hauflo's lamp outlet (KNX switch), the integrator's own KNX relay, a gate relay by hand, a lock."""
    k = MockConfigEntry(domain="knx")
    k.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create("switch", "knx", "u-lamp", config_entry=k, suggested_object_id="torsher")
    reg.async_get_or_create("switch", "knx", "u-hand", config_entry=k, suggested_object_id="hand")
    reg.async_get_or_create("switch", "template", "gate", suggested_object_id="gate_relay")
    reg.async_get_or_create("lock", "template", "lk", suggested_object_id="front")
    for eid in ("switch.torsher", "switch.hand", "switch.gate_relay"):
        hass.states.async_set(eid, "off")
    hass.data["knx"] = knx({})
    return {"owned": {"u-lamp"}, "entries": set()}


def allowed(hass, h, method, path, body=None):
    return m._http_allowed(hass, method, path, json.dumps(body) if body is not None else None, h["entries"], {}, h["owned"])


GOOD = {"entity_id": "switch.torsher", "target_domain": "light", "invert": False}


async def test_a_lamp_light_made_tuned_and_removed_as_the_backend_does_it(hass, house):
    assert allowed(hass, house, "POST", FLOW, {"handler": "switch_as_x", "show_advanced_options": False})
    form = await hass.config_entries.flow.async_init("switch_as_x", context={"source": "user"})
    assert form["type"] == "form" and form["step_id"] == "user"  # HA's one form, no menu
    fid = form["flow_id"]
    assert allowed(hass, house, "POST", f"{FLOW}/{fid}", GOOD)
    assert allowed(hass, house, "POST", f"{FLOW}/{fid}", {"entity_id": "switch.torsher", "target_domain": "light"})
    for bad in (
        {**GOOD, "target_domain": "lock"},  # a «lock» to open by voice
        {**GOOD, "target_domain": "cover"},
        {**GOOD, "target_domain": "valve"},
        {**GOOD, "entity_id": "switch.hand"},  # the integrator's own KNX relay
        {**GOOD, "entity_id": "switch.gate_relay"},  # his gate relay
        {**GOOD, "entity_id": "lock.front"},
        {**GOOD, "entity_id": "switch.nothing"},
        {**GOOD, "invert": "yes"},
        {**GOOD, "extra": 1},
        {"target_domain": "light"},
    ):
        assert not allowed(hass, house, "POST", f"{FLOW}/{fid}", bad), bad
    done = await hass.config_entries.flow.async_configure(fid, GOOD)
    assert done["type"] == "create_entry"
    entry_id = done["result"].entry_id
    light = next(e for e in er.async_entries_for_config_entry(er.async_get(hass), entry_id))
    assert light.platform == "switch_as_x" and light.domain == "light"

    # Not the agent's until it saw it made: no options, no delete, no registry edit of the light.
    assert not allowed(hass, house, "POST", OPTIONS, {"handler": entry_id})
    assert not allowed(hass, house, "DELETE", f"/api/config/config_entries/entry/{entry_id}")
    assert not m._ws_allowed({"type": "config/entity_registry/update", "entity_id": light.entity_id, "area_id": "kitchen"}, hass, house["owned"], house["entries"])
    tracked = set()
    agent = type("A", (), {"hass": hass, "owned_entries": tracked, "_owned_store": type("S", (), {"async_delay_save": lambda *a: None})(), "_owned_data": lambda s: {}, "_owned_changed": lambda s: None})()
    m.Agent._track_entry(agent, json.dumps({"type": "create_entry", "result": {"entry_id": entry_id}}))
    assert tracked == {entry_id}
    house["entries"] = tracked

    # What the backend does next: the light in the room, the switch off Assist.
    assert m._ws_allowed({"type": "config/entity_registry/update", "entity_id": light.entity_id, "area_id": "kitchen"}, hass, house["owned"], house["entries"])
    assert m._ws_allowed({"type": "homeassistant/expose_entity", "assistants": ["conversation"], "entity_ids": ["switch.torsher"], "should_expose": False}, hass, house["owned"], house["entries"])

    # Options: HA's real step and keys (invert only — never another switch).
    assert allowed(hass, house, "POST", OPTIONS, {"handler": entry_id})
    opt = await hass.config_entries.options.async_init(entry_id)
    assert opt["type"] == "form" and opt["step_id"] == "init"
    oid = opt["flow_id"]
    assert allowed(hass, house, "POST", f"{OPTIONS}/{oid}", {"invert": False})
    assert not allowed(hass, house, "POST", f"{OPTIONS}/{oid}", {"invert": False, "entity_id": "switch.gate_relay"})
    assert not allowed(hass, house, "POST", f"{OPTIONS}/{oid}", {"invert": "no"})
    assert (await hass.config_entries.options.async_configure(oid, {"invert": False}))["type"] == "create_entry"

    assert allowed(hass, house, "DELETE", f"/api/config/config_entries/entry/{entry_id}")


async def test_the_integrators_own_switch_as_x_stays_his(hass, house):
    form = await hass.config_entries.flow.async_init("switch_as_x", context={"source": "user"})
    done = await hass.config_entries.flow.async_configure(form["flow_id"], {"entity_id": "switch.gate_relay", "target_domain": "light"})
    theirs = done["result"].entry_id
    light = next(e for e in er.async_entries_for_config_entry(er.async_get(hass), theirs))
    assert not allowed(hass, house, "POST", OPTIONS, {"handler": theirs})
    assert not allowed(hass, house, "DELETE", f"/api/config/config_entries/entry/{theirs}")
    assert not m._ws_allowed({"type": "config/entity_registry/update", "entity_id": light.entity_id, "name": "x"}, hass, house["owned"], {"other"})
    # Nor is his light Hauflo's to put in a group, a scene or a voice assistant.
    assert not m._hauflo_entity(hass, light.entity_id, house["owned"], {"other"})
