"""The rules' template sensors through the tunnel, against HA's real «template» helper flows (tester 30.10: «POST
/api/config/config_entries/flow: 403 Hauflo agent: not allowed»): made, tuned and removed as the backend does it
(blockRulesDeploy.ts), each step checked by HA's own current step — and nothing else a template could do."""

import json

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hauflo as m
from tests.test_agent_tunnel import entry  # noqa: F401 — the agent set up (fixture)
from tests.test_allowlist_knx import knx

FLOW = "/api/config/config_entries/flow"
OPTIONS = "/api/config/config_entries/options/flow"


@pytest.fixture
def house(hass):
    """Hauflo's KNX ventilation (P8 / B8), a hand-made KNX switch, a phone's tracker, the integrator's template helper."""
    k = MockConfigEntry(domain="knx")
    k.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create("switch", "knx", "u-p8", config_entry=k, suggested_object_id="p8")
    reg.async_get_or_create("switch", "knx", "u-b8", config_entry=k, suggested_object_id="b8")
    reg.async_get_or_create("switch", "knx", "u-hand", config_entry=k, suggested_object_id="hand")
    reg.async_get_or_create("device_tracker", "mobile_app", "ph", suggested_object_id="anna_phone")
    hass.data["knx"] = knx({})
    return {"owned": {"u-p8", "u-b8"}, "entries": set()}


def allowed(hass, h, method, path, body=None):
    return m._http_allowed(hass, method, path, json.dumps(body) if body is not None else None, h["entries"], {}, h["owned"])


RULE = "{{ states('switch.p8') not in ['off', 'unavailable', 'unknown'] and not (states('switch.b8') not in ['off', 'unavailable', 'unknown']) }}"
TIMED = "{{ (states.switch.p8 is not none and states('switch.p8') not in ['off', 'unavailable', 'unknown'] and (now() - states.switch.p8.last_changed).total_seconds() > 600) }}"


async def test_a_rule_sensor_made_tuned_and_removed_as_the_backend_does_it(hass, house):
    # 1. The helper's menu: HA's real first step is «user».
    assert allowed(hass, house, "POST", FLOW, {"handler": "template", "show_advanced_options": False})
    menu = await hass.config_entries.flow.async_init("template", context={"source": "user"})
    assert menu["type"] == "menu" and menu["step_id"] == "user"
    fid = menu["flow_id"]
    assert allowed(hass, house, "POST", f"{FLOW}/{fid}", {"next_step_id": "binary_sensor"})
    for other in ("sensor", "switch", "button", "image", "select"):
        assert not allowed(hass, house, "POST", f"{FLOW}/{fid}", {"next_step_id": other}), other  # a switch / button runs actions
    form = await hass.config_entries.flow.async_configure(fid, {"next_step_id": "binary_sensor"})
    assert form["type"] == "form" and form["step_id"] == "binary_sensor"
    # 2. The form: only a rule's name, template and class.
    body = {"name": "Включите вытяжку", "state": RULE, "device_class": "problem"}
    assert allowed(hass, house, "POST", f"{FLOW}/{fid}", body)
    assert allowed(hass, house, "POST", f"{FLOW}/{fid}", {**body, "state": TIMED})
    for bad in (
        {**body, "device_class": "smoke"},  # a fake smoke alarm
        {**body, "device_id": "d1"},
        {**body, "availability": "{{ true }}"},
        {"name": "x"},  # no template
        {**body, "state": "{{ states('switch.hand') == 'on' }}"},  # the integrator's own switch
        {**body, "state": "{{ states('device_tracker.anna_phone') == 'home' }}"},  # where someone is
        {**body, "state": "{{ states('person.anna') == 'home' }}"},
        {**body, "state": "{{ states.switch | list | count > 0 }}"},  # the rest of the house
        {**body, "state": "{{ expand('switch.p8') }}"},
        {**body, "state": "{% for s in states %}{{ s.state }}{% endfor %}"},
        {**body, "state": "{{ state_attr('switch.p8', 'friendly_name') }}"},
        {**body, "state": "{{ range(100000000) | list }}"},
        {**body, "state": RULE + " " * 4000},  # too long
        {**body, "name": ""},
    ):
        assert not allowed(hass, house, "POST", f"{FLOW}/{fid}", bad), bad
    done = await hass.config_entries.flow.async_configure(fid, body)
    assert done["type"] == "create_entry"
    entry_id = done["result"].entry_id
    sensor = next(e for e in er.async_entries_for_config_entry(er.async_get(hass), entry_id))
    assert sensor.platform == "template" and sensor.domain == "binary_sensor"

    # 3. Not yet the agent's (it hasn't seen it made): no options, no delete, no registry edit, not listed.
    assert not allowed(hass, house, "POST", OPTIONS, {"handler": entry_id})
    assert not allowed(hass, house, "DELETE", f"/api/config/config_entries/entry/{entry_id}")
    assert not m._ws_allowed({"type": "config/entity_registry/update", "entity_id": sensor.entity_id, "name": "x"}, hass, house["owned"], house["entries"])
    # The agent saw it made through the tunnel (the answer of the flow's last step): Hauflo's now.
    tracked = set()
    agent = type("A", (), {"hass": hass, "owned_entries": tracked, "_owned_store": type("S", (), {"async_delay_save": lambda *a: None})(), "_owned_data": lambda s: {}, "_owned_changed": lambda s: None})()
    m.Agent._track_entry(agent, json.dumps({"type": "create_entry", "result": {"entry_id": entry_id}}))
    assert tracked == {entry_id}
    house["entries"] = tracked

    # 4. Its entity: renamed to the rule's id, named, placed, kept off Assist — what the backend does next.
    for msg in (
        {"type": "config/entity_registry/update", "entity_id": sensor.entity_id, "new_entity_id": "binary_sensor.hauflo_rule_0123456789"},
        {"type": "config/entity_registry/update", "entity_id": sensor.entity_id, "name": "Включите вытяжку", "icon": "mdi:alert-circle-outline", "area_id": "kitchen"},
        {"type": "homeassistant/expose_entity", "assistants": ["conversation"], "entity_ids": [sensor.entity_id], "should_expose": False},
        {"type": "config_entries/get", "domain": "template"},
    ):
        assert m._ws_allowed(msg, hass, house["owned"], house["entries"]), msg

    # 5. The rule changed: the options flow — HA's real step is the helper's type.
    assert allowed(hass, house, "POST", OPTIONS, {"handler": entry_id, "show_advanced_options": False})
    opt = await hass.config_entries.options.async_init(entry_id)
    assert opt["type"] == "form" and opt["step_id"] == "binary_sensor"
    oid = opt["flow_id"]
    assert allowed(hass, house, "POST", f"{OPTIONS}/{oid}", {"state": TIMED})
    assert not allowed(hass, house, "POST", f"{OPTIONS}/{oid}", {"state": "{{ states('device_tracker.anna_phone') == 'home' }}"})
    assert not allowed(hass, house, "POST", f"{OPTIONS}/{oid}", {"state": TIMED, "device_id": "d1"})
    # HA's options form has no class (set once, at setup): sent anyway, HA itself says «extra keys not allowed».
    assert not allowed(hass, house, "POST", f"{OPTIONS}/{oid}", {"state": TIMED, "device_class": "problem"})
    res = await hass.config_entries.options.async_configure(oid, {"state": TIMED})
    assert res["type"] == "create_entry"

    # 6. The rule deleted: its entry goes.
    assert allowed(hass, house, "DELETE", f"/api/config/config_entries/entry/{entry_id}")


async def test_the_integrators_own_template_helper_stays_his(hass, house):
    menu = await hass.config_entries.flow.async_init("template", context={"source": "user"})
    await hass.config_entries.flow.async_configure(menu["flow_id"], {"next_step_id": "binary_sensor"})
    done = await hass.config_entries.flow.async_configure(menu["flow_id"], {"name": "Свой", "state": "{{ true }}"})
    theirs = done["result"].entry_id
    sensor = next(e for e in er.async_entries_for_config_entry(er.async_get(hass), theirs))
    assert not allowed(hass, house, "POST", OPTIONS, {"handler": theirs})
    assert not allowed(hass, house, "DELETE", f"/api/config/config_entries/entry/{theirs}")
    opt = await hass.config_entries.options.async_init(theirs)
    assert not allowed(hass, house, "POST", f"{OPTIONS}/{opt['flow_id']}", {"state": RULE})
    assert not m._ws_allowed({"type": "config/entity_registry/update", "entity_id": sensor.entity_id, "name": "x"}, hass, house["owned"], {"other"})
    # A rule may not read it either (not Hauflo's).
    assert not m._template_step_ok(hass, {"name": "x", "state": f"{{{{ states('{sensor.entity_id}') == 'on' }}}}"}, "binary_sensor", house["owned"], set(), False)


async def test_config_entries_get_of_template_answers_with_hauflos_own_only(hass, entry):
    agent = entry.runtime_data
    agent.owned_entries.add("mine")
    agent._pending[(1, 5)] = ("config_entries/get", None)
    res = [{"entry_id": "mine", "title": "Включите вытяжку"}, {"entry_id": "his", "title": "Анин iPhone батарея"}]
    out = json.loads(agent._track(1, json.dumps({"id": 5, "type": "result", "success": True, "result": res})))
    assert [e["entry_id"] for e in out["result"]] == ["mine"]
