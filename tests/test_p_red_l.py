"""Red team L (06.11): the wizard's plans (п.57).

L1 — one plan per agent: a second integrator's deploy replaced the first's, whose writes then failed (not_in_plan).
Now plans by id, several at once; more than PLANS_MAX — refused (plans_busy), never one dropped.
L2 — a plan lived an hour after its deploy. Now each item is written once by its plan, the plan gone when empty or idle
PLAN_TTL; «Вернуть как было» takes its items out of every plan; an unloaded / re-paired agent keeps none."""

import time
from unittest.mock import patch

from homeassistant.core import HomeAssistant

import custom_components.hauflo as hauflo
from custom_components.hauflo import foreign
from tests.test_agent_tunnel import Out
from tests.test_foreign_wizard import Send, _errors, house  # noqa: F401 — the fixture

D, E = foreign.DEVICE_UPDATE, foreign.ENTITY_UPDATE


async def _post(hass_client, body):
    res = await (await hass_client()).post("/api/hauflo/foreign", json=body)
    return res.status, await res.json()


def _plan(a, devices=(), entities=(), areas=None):
    return {"areas": list(areas if areas is not None else a.values()), "devices": list(devices), "entities": list(entities)}


async def test_two_deploys_at_once_each_writes_its_own(hass: HomeAssistant, hass_client, house) -> None:  # noqa: F811
    """The race: deploy A sends its plan, deploy B (another integrator) sends its — A's writes still go (were refused)."""
    agent, d, a, local = house["agent"], house["devices"], house["areas"], house["local"]
    assert (await _post(hass_client, {"plan": _plan(a, [d["leak"]], ["sensor.leak_battery"]), "plan_id": "deploy-A"}))[0] == 200
    assert (await _post(hass_client, {"plan": _plan(a, [d["hub"]], ["sensor.hub_link"]), "plan_id": "deploy-B"}))[0] == 200
    assert set(foreign.active_plans(hass)) == {"deploy-A", "deploy-B"}
    send = Send(agent)
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        # Neither plan holds this one: refused, whoever asks.
        await send(type=D, device_id=d["outside"], name_by_user="Снаружи")
        await send(type=D, device_id=d["leak"], name_by_user="Протечка")  # A's
        await send(type=D, device_id=d["hub"], name_by_user="Хаб")  # B's
        await send(type=E, entity_id="sensor.leak_battery", labels=["hauflo_role_diag"])  # A's
        await send(type=E, entity_id="sensor.hub_link", labels=["hauflo_role_diag"])  # B's
    assert [e["message"] for e in _errors(out)] == ["Hauflo agent: not allowed (not_in_plan)"]
    assert len(local.sent) == 4
    assert not foreign.active_plans(hass)  # both written through: gone


async def test_a_room_is_never_taken_from_another_plan(hass: HomeAssistant, hass_client, house) -> None:  # noqa: F811
    agent, d, a, local = house["agent"], house["devices"], house["areas"], house["local"]
    await _post(hass_client, {"plan": _plan(a, [d["leak"]], areas=[a["Kitchen"]]), "plan_id": "A"})
    await _post(hass_client, {"plan": _plan(a, [d["hub"]], areas=[a["Hall"]]), "plan_id": "B"})
    send = Send(agent)
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(type=D, device_id=d["leak"], area_id=a["Hall"])  # B's room, A's device
    assert [e["message"] for e in _errors(out)] == ["Hauflo agent: not allowed (area_not_in_plan)"] and local.sent == []
    # The same device in both plans: the plan whose room it is writes it (the older first), the other keeps its own.
    await _post(hass_client, {"plan": _plan(a, [d["leak"], d["hub"]], areas=[a["Hall"]]), "plan_id": "B"})
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(type=D, device_id=d["leak"], area_id=a["Hall"])
    assert _errors(out) == [] and d["leak"] not in foreign.active_plans(hass)["B"]["devices"]
    assert d["leak"] in foreign.active_plans(hass)["A"]["devices"]


async def test_a_plan_is_one_shot(hass: HomeAssistant, hass_client, house) -> None:  # noqa: F811
    """The same write again — refused: the plan is not a pass for the hour after (was: any number of writes)."""
    agent, d, a, local = house["agent"], house["devices"], house["areas"], house["local"]
    await _post(hass_client, {"plan": _plan(a, [d["leak"], d["hub"]], ["sensor.helper"]), "plan_id": "A"})
    send = Send(agent)
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(type=D, device_id=d["leak"], labels=["hauflo_role_leaks"])
        await send(type=D, device_id=d["leak"], labels=["hauflo_role_climate"])  # replayed
        await send(type=E, entity_id="sensor.helper", labels=["hauflo_role_readings"])
        await send(type=E, entity_id="sensor.helper", area_id=a["Kitchen"])  # replayed
    assert [e["message"] for e in _errors(out)] == ["Hauflo agent: not allowed (not_in_plan)"] * 2
    assert len(local.sent) == 2
    plan = foreign.active_plans(hass)["A"]
    assert plan["devices"] == {d["hub"]} and plan["entities"] == set()


async def test_idle_plan_goes_and_a_write_keeps_it(hass: HomeAssistant, hass_client, house) -> None:  # noqa: F811
    agent, d, a = house["agent"], house["devices"], house["areas"]
    assert foreign.PLAN_TTL <= 600  # minutes, not the hour (L2)
    await _post(hass_client, {"plan": _plan(a, [d["leak"], d["hub"], d["outside"]]), "plan_id": "A"})
    plan = foreign.active_plans(hass)["A"]
    plan["at"] -= foreign.PLAN_TTL - 5
    send = Send(agent)
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(type=D, device_id=d["leak"], name_by_user="Протечка")
    assert _errors(out) == [] and time.time() - plan["at"] < 5  # a write keeps it alive
    plan["at"] -= foreign.PLAN_TTL + 1
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(type=D, device_id=d["hub"], name_by_user="Хаб")
    assert [e["message"] for e in _errors(out)] == ["Hauflo agent: not allowed (no_plan)"]
    assert not foreign.active_plans(hass)


async def test_plans_busy_and_the_ids(hass: HomeAssistant, hass_client, house) -> None:  # noqa: F811
    d, a = house["devices"], house["areas"]
    for i in range(foreign.PLANS_MAX):
        assert (await _post(hass_client, {"plan": _plan(a, [d["leak"]]), "plan_id": f"p{i}"}))[0] == 200
    status, got = await _post(hass_client, {"plan": _plan(a, [d["hub"]]), "plan_id": "late"})
    assert status == 409 and got["code"] == "plans_busy" and "late" not in foreign.active_plans(hass)
    # Nothing else of the body taken with it (the alarm map neither).
    status, _ = await _post(hass_client, {"plan": _plan(a, [d["hub"]]), "plan_id": "late", "notify": {"sensor.helper": True}})
    assert status == 409 and foreign.flags(hass).notify == {}
    # The same id again: its own plan replaced, not refused; one dropped frees a place.
    assert (await _post(hass_client, {"plan": _plan(a, [d["hub"]]), "plan_id": "p0"}))[0] == 200
    assert foreign.active_plans(hass)["p0"]["devices"] == {d["hub"]}
    assert (await _post(hass_client, {"plan": None, "plan_id": "p1"}))[0] == 200
    assert (await _post(hass_client, {"plan": _plan(a, [d["hub"]]), "plan_id": "late"}))[0] == 200
    assert "p1" not in foreign.active_plans(hass) and len(foreign.active_plans(hass)) == foreign.PLANS_MAX
    # An old backend's plan (no id) replaces only the one legacy plan, never a deploy's by id.
    foreign.active_plans(hass).pop("late")
    assert (await _post(hass_client, {"plan": _plan(a, [d["outside"]])}))[0] == 200
    assert (await _post(hass_client, {"plan": None}))[0] == 200
    assert foreign.LEGACY_PLAN not in foreign.active_plans(hass) and "p0" in foreign.active_plans(hass)
    for bad in ({"plan_id": "p0"}, {"plan": None, "plan_id": "a b"}, {"plan": None, "plan_id": "x" * 65}, {"plan": None, "plan_id": 5}, {"plan": None, "plan_id": ""}):
        assert (await _post(hass_client, bad))[0] == 400, bad


async def test_revert_and_unload_end_the_plans(hass: HomeAssistant, hass_client, house) -> None:  # noqa: F811
    agent, d, a, local = house["agent"], house["devices"], house["areas"], house["local"]
    await _post(hass_client, {"plan": _plan(a, [d["leak"], d["hub"]], ["sensor.helper"]), "plan_id": "A"})
    send = Send(agent)
    with Out(agent), patch.object(hauflo.backup, "fresh", return_value=True):
        await send(type=D, device_id=d["leak"], name_by_user="Протечка")
        await send(type=E, entity_id="sensor.helper", name="Счётчик")
    # «Вернуть как было» while another deploy's plan still holds the hub: the hub, put back, leaves it too.
    foreign.flags(hass).journal[f"d:{d['hub']}"] = {"was": {"name_by_user": None}, "set": {"name_by_user": None}}
    with patch.object(foreign.backup, "fresh", return_value=True):
        status, got = await _post(hass_client, {"revert": {"devices": [d["leak"], d["hub"]]}})
    assert status == 200 and sorted(got["reverted"]["devices"]) == sorted([d["leak"], d["hub"]])
    assert not foreign.active_plans(hass)
    # Revert after revert: nothing of Hauflo's left to put back — said, nothing written; only the entity not asked for stays in the journal.
    with patch.object(foreign.backup, "fresh", return_value=True):
        status, again = await _post(hass_client, {"revert": {"devices": [d["leak"], d["hub"]]}})
    assert status == 200 and again["reverted"]["devices"] == [] and sorted(again["reverted"]["unknown"]) == sorted([d["leak"], d["hub"]])
    assert not any(k.startswith("d:") for k in foreign.flags(hass).journal) and len(foreign.flags(hass).journal) == 1
    # Unloaded (re-paired, a new token): no plan of the old link left to write by.
    await _post(hass_client, {"plan": _plan(a, [d["hub"]]), "plan_id": "B"})
    entry = hass.config_entries.async_entries("hauflo")[0]
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not foreign.flags(hass).plans


async def test_l3_one_protected_rule_for_helpers_and_the_card(hass: HomeAssistant, hass_client, house) -> None:  # noqa: F811
    """Red team L3: a gate's relay under a «Switch as X» gate cover (or a template gate cover) was a plain switch to the
    room card — one tap opened the gate — and the wizard put it in a room / gave it a role. One rule (_protected) for
    every analog, the card reading the same list."""
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    agent, a, local = house["agent"], house["areas"], house["local"]
    reg = er.async_get(hass)

    def helper(domain, options, wrapper, state, attrs=None):
        e = MockConfigEntry(domain=domain, options=options)
        e.add_to_hass(hass)
        d, obj = wrapper.split(".")
        reg.async_get_or_create(d, domain, f"h-{obj}", config_entry=e, suggested_object_id=obj)
        hass.states.async_set(wrapper, state, attrs or {})
        return e

    for eid in ("switch.gate_relay", "switch.tpl_relay", "switch.plain", "switch.lamp"):
        reg.async_get_or_create("switch", "zha", f"u-{eid}", suggested_object_id=eid.split(".")[1])
        hass.states.async_set(eid, "off")
    # A «Switch as X» gate over a relay: the relay too (by the registry id, as HA may keep it).
    helper("switch_as_x", {"entity_id": reg.async_get("switch.gate_relay").id, "target_domain": "cover"}, "cover.gate", "closed", {"device_class": "gate"})
    # A template gate cover whose actions switch a relay.
    helper("template", {"template_type": "cover", "device_class": "garage", "open_cover": [{"action": "switch.turn_on", "target": {"entity_id": "switch.tpl_relay"}}]}, "cover.tpl_gate", "closed", {"device_class": "garage"})
    # A template switch whose action unlocks the door: protected as what it operates.
    hass.states.async_set("lock.door", "locked")
    helper("template", {"template_type": "switch", "turn_on": [{"action": "lock.unlock", "target": {"entity_id": ["lock.door"]}}]}, "switch.unlock_tpl", "off")
    # A template sensor only reading a switch, a «Switch as X» light over a lamp: neither protects anything.
    helper("template", {"template_type": "sensor", "state": "{{ states('switch.plain') }}"}, "sensor.reads_plain", "off")
    helper("switch_as_x", {"entity_id": "switch.lamp", "target_domain": "light"}, "light.lamp", "off")

    for eid in ("switch.gate_relay", "cover.gate", "switch.tpl_relay", "cover.tpl_gate", "switch.unlock_tpl"):
        assert hauflo._protected(hass, eid), eid
    for eid in ("switch.plain", "sensor.reads_plain", "switch.lamp", "light.lamp"):
        assert not hauflo._protected(hass, eid), eid
    # The card's list: the same rule (family members read it too, not only admins).
    got = (await (await (await hass_client()).get("/api/hauflo/house")).json())["protected"]
    assert {"switch.gate_relay", "switch.tpl_relay", "switch.unlock_tpl", "cover.gate", "lock.door"} <= set(got)
    assert not {"switch.plain", "switch.lamp", "light.lamp"} & set(got)
    # The wizard never puts the relay in a room nor gives it a role; its name / hidden stay the integrator's.
    await _post(hass_client, {"plan": _plan(a, [], ["switch.gate_relay", "switch.lamp"]), "plan_id": "A"})
    send = Send(agent)
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(type=E, entity_id="switch.gate_relay", area_id=a["Kitchen"])
        await send(type=E, entity_id="switch.gate_relay", labels=["hauflo_role_switches"])
        await send(type=E, entity_id="switch.lamp", area_id=a["Kitchen"])
    assert [e["message"] for e in _errors(out)] == ["Hauflo agent: not allowed (protected_domain)"] * 2
    assert [m["entity_id"] for m in local.sent] == ["switch.lamp"]
    # Unreadable helpers never break the rule for the rest.
    with patch.object(hauflo.er, "async_entries_for_config_entry", side_effect=RuntimeError):
        assert hauflo._helper_links(hass) == [] and hauflo._protected(hass, "lock.door")


async def test_n7_a_write_goes_only_by_the_plan_it_names(hass: HomeAssistant, hass_client, house) -> None:  # noqa: F811
    """N7: deploy B's live plan holding the same item never lets deploy A's write through once A's plan is gone; the
    write names its plan (`hauflo_plan`), taken off before HA sees it."""
    agent, d, a, local = house["agent"], house["devices"], house["areas"], house["local"]
    await _post(hass_client, {"plan": _plan(a, [d["leak"], d["hub"]], ["sensor.helper"]), "plan_id": "A"})
    await _post(hass_client, {"plan": _plan(a, [d["leak"]]), "plan_id": "B"})
    send = Send(agent)
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(type=D, device_id=d["leak"], name_by_user="Протечка", hauflo_plan="B")
    assert _errors(out) == [] and local.sent[-1] == {"id": local.sent[-1]["id"], "type": D, "device_id": d["leak"], "name_by_user": "Протечка"}
    plans = foreign.active_plans(hass)
    assert "B" not in plans and d["leak"] in plans["A"]["devices"]  # consumed from the plan it named, not the oldest
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(type=D, device_id=d["hub"], name_by_user="Хаб", hauflo_plan="B")  # B is gone (written through)
        await send(type=E, entity_id="sensor.helper", name="Счётчик", hauflo_plan="nobody")  # never sent
        await send(type=E, entity_id="sensor.helper", name="Счётчик", hauflo_plan="a b")  # not an id
        await send(type=E, entity_id="sensor.helper", name="Счётчик", hauflo_plan=5)
    assert [e["message"] for e in _errors(out)] == ["Hauflo agent: not allowed (plan_gone)"] * 4
    # A live plan named, the item not in it.
    await _post(hass_client, {"plan": _plan(a, [d["outside"]]), "plan_id": "C"})
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(type=D, device_id=d["hub"], name_by_user="Хаб", hauflo_plan="C")
    assert [e["message"] for e in _errors(out)] == ["Hauflo agent: not allowed (not_in_plan)"]
    # A expired: its write is refused although C is live (was: any live plan let it through).
    foreign.active_plans(hass)["A"]["at"] -= foreign.PLAN_TTL + 1
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(type=D, device_id=d["hub"], name_by_user="Хаб", hauflo_plan="A")
        await send(type=D, device_id=d["hub"], area_id=a["Hall"], hauflo_plan="A")  # never the click's placing either
    assert [e["message"] for e in _errors(out)] == ["Hauflo agent: not allowed (plan_gone)"] * 2
    # A backend from before (no id on the write): the old way, any live plan holding it.
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(type=D, device_id=d["outside"], name_by_user="Снаружи")
    assert _errors(out) == [] and "C" not in foreign.active_plans(hass)


async def test_n7_the_feature_is_said(hass: HomeAssistant, hass_client, house) -> None:  # noqa: F811
    res = await (await hass_client()).get("/api/hauflo/foreign")
    assert foreign.WRITE_PLAN_FEATURE in (await res.json())["features"]
    assert foreign.WRITE_PLAN_FEATURE in hauflo.FEATURES


async def test_n6_a_yaml_helper_over_a_gate_is_protected_source_unknown(hass: HomeAssistant, hass_client, house) -> None:  # noqa: F811
    """Red team N6: a YAML template switch / script over a gate or lock has no config entry — what it operates can't be
    read. By its class or its name (signalWords gateOpener, class words, no makers) it counts as one: protected, with
    the reason «unknown_source» for the card; a UI one and a plain YAML one are judged as before."""
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    reg = er.async_get(hass)
    # YAML (no config entry): with a unique_id (in the registry) and without (state only).
    reg.async_get_or_create("switch", "template", "y-gate", suggested_object_id="vorota_yaml", original_name="Откатные ворота")
    hass.states.async_set("switch.vorota_yaml", "off", {"friendly_name": "Откатные ворота"})
    hass.states.async_set("switch.front_gate", "off")
    hass.states.async_set("script.open_wicket", "off", {"friendly_name": "Открыть калитку"})
    hass.states.async_set("button.strike", "unknown", {"friendly_name": "Door opener"})
    hass.states.async_set("cover.shed", "closed", {"friendly_name": "Сарай", "device_class": "garage"})
    hass.states.async_set("switch.electro", "off", {"friendly_name": "Электрозамок входной"})
    # Not a gate: the words inside others, a YAML lamp, a light.
    for eid, name in (("switch.povorot", "Поворотный кран"), ("switch.yard_light", "Свет у гаража"), ("script.navigate", "navigate"), ("switch.zamknut", "Замкнуть контур"), ("light.vorota", "Подсветка ворот")):
        hass.states.async_set(eid, "off", {"friendly_name": name})
    # A UI helper / an integration's switch named so: it has a config entry — judged by what it operates, not by name.
    e = MockConfigEntry(domain="zha")
    e.add_to_hass(hass)
    reg.async_get_or_create("switch", "zha", "z-gate", config_entry=e, suggested_object_id="gate_zha")
    hass.states.async_set("switch.gate_zha", "off", {"friendly_name": "Ворота"})

    yes = ("switch.vorota_yaml", "switch.front_gate", "script.open_wicket", "button.strike", "cover.shed", "switch.electro")
    no = ("switch.povorot", "switch.yard_light", "script.navigate", "switch.zamknut", "light.vorota", "switch.gate_zha")
    for eid in yes:
        assert hauflo.unknown_source(hass, eid) and hauflo._protected(hass, eid), eid
    for eid in no:
        assert not hauflo._protected(hass, eid), eid
    assert not hauflo.unknown_source(None, "switch.front_gate") and not hauflo.unknown_source(hass, 5)
    body = await (await (await hass_client()).get("/api/hauflo/house")).json()
    assert set(yes) <= set(body["protected"]) and not set(no) & set(body["protected"])
    assert body["protected_why"] == dict.fromkeys(yes, "unknown_source")
    # No registry (HA starting): the state alone still decides.
    with patch.object(hauflo.er, "async_get", side_effect=RuntimeError):
        assert hauflo.unknown_source(hass, "switch.front_gate") and not hauflo.unknown_source(hass, "switch.povorot")


async def test_n6_a_ui_helper_naming_no_entity_is_protected_source_unknown(hass: HomeAssistant, hass_client, house) -> None:  # noqa: F811
    """Acceptance 3 (06.11): a UI template switch «Ворота гаража» with empty actions, or one whose actions call a
    service naming no entity (rest_command, shell_command, mqtt.publish), has a config entry but nothing readable
    about what it operates — the same unknown source as a YAML one: protected, why «unknown_source». A UI helper
    whose actions name an entity is still judged by that entity; a plain-named one with empty actions is not a gate."""
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    reg = er.async_get(hass)

    def helper(domain: str, options: dict, object_id: str, name: str, platform: str = "template", kind: str = "switch") -> str:
        e = MockConfigEntry(domain=domain, options=options)
        e.add_to_hass(hass)
        reg.async_get_or_create(kind, platform, f"u-{object_id}", config_entry=e, suggested_object_id=object_id, original_name=name)
        eid = f"{kind}.{object_id}"
        hass.states.async_set(eid, "off", {"friendly_name": name})
        return eid

    hass.states.async_set("light.yard", "off", {"friendly_name": "Двор"})
    empty = helper("template", {"name": "Ворота гаража шаблон", "template_type": "switch", "turn_on": [], "turn_off": [], "value_template": "{{ is_state('input_boolean.x', 'on') }}"}, "vorota_shablon", "Ворота гаража шаблон")
    service = helper("template", {"name": "Калитка", "template_type": "switch", "turn_on": [{"action": "rest_command.open", "data": {"code": "1"}}], "turn_off": []}, "kalitka_rest", "Калитка")
    button = helper("template", {"name": "Gate opener", "template_type": "button", "press": [{"action": "mqtt.publish", "data": {"topic": "gate/cmd", "payload": "open"}}]}, "gate_mqtt", "Gate opener", kind="button")
    over_light = helper("template", {"name": "Ворота подсветка", "template_type": "switch", "turn_on": [{"action": "light.turn_on", "target": {"entity_id": "light.yard"}}], "turn_off": []}, "vorota_light", "Ворота подсветка")
    plain = helper("template", {"name": "Полив", "template_type": "switch", "turn_on": [], "turn_off": []}, "poliv", "Полив")
    other = helper("zha", {}, "gate_zha2", "Ворота", platform="zha")

    yes = (empty, service, button)
    no = (over_light, plain, other)
    for eid in yes:
        assert hauflo.unknown_source(hass, eid) and hauflo._protected(hass, eid), eid
    for eid in no:
        assert not hauflo.unknown_source(hass, eid) and not hauflo._protected(hass, eid), eid
    body = await (await (await hass_client()).get("/api/hauflo/house")).json()
    assert set(yes) <= set(body["protected"]) and not set(no) & set(body["protected"])
    assert {k: v for k, v in body["protected_why"].items() if k in yes + no} == dict.fromkeys(yes, "unknown_source")
    # The entry can't be read at all: as a helper naming nothing (fail closed).
    with patch.object(hass.config_entries, "async_get_entry", side_effect=RuntimeError):
        assert hauflo.unknown_source(hass, over_light) and not hauflo.unknown_source(hass, plain)
