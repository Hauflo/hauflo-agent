"""The wizard of other integrations (п.57, agent 0.23.0, «foreign_wizard»): with the deploy's plan the agent lets a
device's room / name / role labels and an entity's room / name / hidden / role labels through — also an entity with no
device — the family's labels kept, a missing role label made once, the values as they were kept for «Вернуть как было».
Every refusal says why. Never KNX / Hauflo / a phone / a service device / outside the plan / another field / another
hidden_by / a label not hauflo_role_*."""

import inspect
import json
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hauflo as hauflo
from custom_components.hauflo import foreign
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN
from tests.test_agent_tunnel import Local, Out


async def _idle(self):
    return None


@pytest.fixture
async def house(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    areas = ar.async_get(hass)
    ids = {n: areas.async_create(n).id for n in ("Kitchen", "Hall", "Family room")}
    cfg = {d: MockConfigEntry(domain=d, title=t) for d, t in (("zha", "Zigbee"), ("knx", "KNX"), ("mobile_app", "Анин iPhone"), ("template", "Helpers"))}
    for c in cfg.values():
        c.add_to_hass(hass)
    devs, ents = dr.async_get(hass), er.async_get(hass)
    lr.async_get(hass).async_create("family_tag")

    def dev(key, entry, **kw):
        return devs.async_get_or_create(config_entry_id=cfg[entry].entry_id, identifiers={(entry, key)}, name=key, **kw).id

    d = {
        "leak": dev("leak", "zha"),
        "hub": dev("hub", "zha"),
        "svc": dev("svc", "zha", entry_type=dr.DeviceEntryType.SERVICE),
        "knx": dev("knx", "knx"),
        "phone": dev("phone", "mobile_app"),
        "mix": dev("mix", "zha"),
        "outside": dev("outside", "zha"),
    }
    devs.async_update_device(d["leak"], labels={"family_tag"}, area_id=ids["Family room"])

    def ent(eid, platform, device=None, entry="zha"):
        domain, obj = eid.split(".")
        return ents.async_get_or_create(domain, platform, f"u-{obj}", suggested_object_id=obj, device_id=d.get(device), config_entry=cfg[entry]).entity_id

    ent("binary_sensor.leak", "zha", "leak")
    ent("sensor.leak_battery", "zha", "leak")
    ent("sensor.hub_link", "zha", "hub")
    ent("sensor.svc", "zha", "svc")
    ent("light.knx", "knx", "knx", "knx")
    ent("sensor.phone", "mobile_app", "phone", "mobile_app")
    ent("switch.mix_zha", "zha", "mix")
    ent("switch.mix_knx", "knx", "mix", "knx")
    ent("sensor.outside", "zha", "outside")
    ent("sensor.helper", "template", None, "template")  # no device, a unique id: S5
    ent("sensor.not_planned", "template", None, "template")
    ent("sensor.own", DOMAIN, None, "zha")
    ent("lock.door", "zha", "leak")
    ents.async_update_entity("sensor.leak_battery", labels={"family_tag"})
    agent = entry.runtime_data
    agent.channels[1] = Local()
    plan = {"areas": [ids["Kitchen"], ids["Hall"]], "devices": [d[k] for k in ("leak", "hub", "svc", "knx", "phone", "mix")], "entities": ["binary_sensor.leak", "sensor.leak_battery", "sensor.hub_link", "sensor.svc", "light.knx", "sensor.phone", "switch.mix_zha", "sensor.helper", "sensor.own", "lock.door"]}
    yield {"agent": agent, "areas": ids, "devices": d, "plan": plan, "local": agent.channels[1]}


async def _plan(hass, hass_client, plan):
    res = await (await hass_client()).post("/api/hauflo/foreign", json={"plan": plan})
    assert res.status == 200, await res.text()
    return await res.json()


class Send:
    def __init__(self, agent):
        self.agent, self.n = agent, 100

    async def __call__(self, **msg):
        self.n += 1
        await self.agent._channel_send(1, json.dumps({"id": self.n, **msg}))


def _apply(hass, sent):
    """HA applying what the agent forwarded (the test's local socket only records it)."""
    m = {k: v for k, v in sent.items() if k not in ("id", "type")}
    if sent["type"] == foreign.DEVICE_UPDATE:
        dev = m.pop("device_id")
        if "labels" in m:
            m["labels"] = set(m["labels"])
        dr.async_get(hass).async_update_device(dev, **m)
    else:
        eid = m.pop("entity_id")
        if "labels" in m:
            m["labels"] = set(m["labels"])
        if "hidden_by" in m:
            m["hidden_by"] = er.RegistryEntryHider(m["hidden_by"]) if m["hidden_by"] else None
        er.async_get(hass).async_update_entity(eid, **m)


def _errors(out):
    return [json.loads(f["data"])["error"] for f in out.frames if f.get("t") == "ws_msg"]


async def test_allowed_writes_merge_labels_and_keep_the_old(hass: HomeAssistant, hass_client, house) -> None:
    agent, d, a, local = house["agent"], house["devices"], house["areas"], house["local"]
    got = await _plan(hass, hass_client, house["plan"])
    assert got["plan"] is True
    send = Send(agent)
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        # A device: into a room of the plan (out of the family's own — the wizard may), named, given a role.
        await send(type=foreign.DEVICE_UPDATE, device_id=d["leak"], area_id=a["Kitchen"], name_by_user="Протечка кухня", labels=["hauflo_role_leaks"])
        # An entity on a device: room (a channel of its own), name, hidden, role.
        await send(type=foreign.ENTITY_UPDATE, entity_id="sensor.leak_battery", area_id=a["Hall"], name="Батарея", hidden_by="user", labels=["hauflo_role_diag"])
        # An entity with no device (a template helper, S5): unique id only.
        await send(type=foreign.ENTITY_UPDATE, entity_id="sensor.helper", area_id=a["Hall"], labels=["hauflo_role_readings"])
        # Room back to «follows its device», shown again, name back to the integration's.
        await send(type=foreign.ENTITY_UPDATE, entity_id="sensor.hub_link", area_id=None, hidden_by=None, name=None)
    assert _errors(out) == []
    sent = local.sent
    assert len(sent) == 4
    # The family's label is kept: HA replaces the whole list, the agent merges.
    assert sent[0]["labels"] == ["family_tag", "hauflo_role_leaks"] and sent[0]["area_id"] == a["Kitchen"] and sent[0]["name_by_user"] == "Протечка кухня"
    assert sent[1]["labels"] == ["family_tag", "hauflo_role_diag"] and sent[1]["hidden_by"] == "user"
    assert sent[2]["labels"] == ["hauflo_role_readings"]
    # The role labels made once, Hauflo's (it may delete them when unused), with their ids as asked.
    labels = lr.async_get(hass)
    for lid in ("hauflo_role_leaks", "hauflo_role_diag", "hauflo_role_readings"):
        assert labels.async_get_label(lid) is not None and f"label:{lid}" in agent.owned_entries
    # The values as they were, before Hauflo's first write.
    j = foreign.flags(hass).journal
    assert j[f"d:{d['leak']}"]["was"] == {"area_id": a["Family room"], "name_by_user": None, "labels": ["family_tag"]}
    eb = er.async_get(hass).async_get("sensor.leak_battery")
    assert j[f"e:{eb.id}"]["was"] == {"area_id": None, "name": None, "hidden_by": None, "labels": ["family_tag"]}
    # Idempotent: the same again makes no second label and keeps the first «was».
    for s in sent:
        _apply(hass, s)
    local.sent.clear()
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await _plan(hass, hass_client, house["plan"])  # a plan is one-shot: each deploy sends its own
        await send(type=foreign.DEVICE_UPDATE, device_id=d["leak"], labels=["hauflo_role_leaks", "hauflo_role_climate"])
        await _plan(hass, hass_client, house["plan"])
        await send(type=foreign.DEVICE_UPDATE, device_id=d["leak"], labels=[])  # role labels off, the family's stay
    assert _errors(out) == []
    assert local.sent[0]["labels"] == ["family_tag", "hauflo_role_climate", "hauflo_role_leaks"]
    assert local.sent[1]["labels"] == ["family_tag"]
    assert j[f"d:{d['leak']}"]["was"]["labels"] == ["family_tag"]
    assert len([x for x in labels.async_list_labels() if x.label_id.startswith("hauflo_role_leaks")]) == 1
    # The journal is kept across a restart.
    await foreign.flags(hass)._store.async_save(foreign.flags(hass)._data())
    again = foreign.Flags(hass)
    await again.async_load()
    assert again.journal[f"d:{d['leak']}"]["was"]["area_id"] == a["Family room"]


async def test_every_refusal_says_why(hass: HomeAssistant, hass_client, house) -> None:
    agent, d, a, local = house["agent"], house["devices"], house["areas"], house["local"]
    await _plan(hass, hass_client, house["plan"])
    yandex = MockConfigEntry(domain="yandex_smart_home", options={"label": "hauflo_role_voice"})
    yandex.add_to_hass(hass)
    D, E = foreign.DEVICE_UPDATE, foreign.ENTITY_UPDATE
    cases = [
        # never these devices
        ({"type": D, "device_id": d["knx"], "name_by_user": "X"}, None),  # KNX: Hauflo's own path refuses it
        ({"type": D, "device_id": d["phone"], "name_by_user": "X"}, "platform_not_allowed"),
        ({"type": D, "device_id": d["mix"], "name_by_user": "X"}, "platform_not_allowed"),  # a KNX entity on it
        ({"type": D, "device_id": d["svc"], "name_by_user": "X"}, "service_device"),
        ({"type": D, "device_id": d["outside"], "name_by_user": "X"}, "not_in_plan"),
        ({"type": D, "device_id": "nope", "name_by_user": "X"}, "unknown_device"),
        # never these entities
        ({"type": E, "entity_id": "light.knx", "name": "X"}, None),  # KNX: Hauflo's own path refuses it
        ({"type": E, "entity_id": "sensor.phone", "name": "X"}, "platform_not_allowed"),
        ({"type": E, "entity_id": "switch.mix_zha", "name": "X"}, "platform_not_allowed"),
        ({"type": E, "entity_id": "sensor.svc", "name": "X"}, "service_device"),
        ({"type": E, "entity_id": "sensor.outside", "area_id": a["Hall"]}, "not_in_plan"),
        ({"type": E, "entity_id": "sensor.not_planned", "area_id": a["Hall"]}, "not_in_plan"),
        ({"type": E, "entity_id": "sensor.ghost", "name": "X"}, "unknown_entity"),
        ({"type": E, "entity_id": "lock.door", "name": "X"}, "protected_domain"),
        # fields outside the allowlist
        ({"type": D, "device_id": d["leak"], "disabled_by": "user"}, "field_not_allowed:disabled_by"),
        ({"type": D, "device_id": d["leak"], "area_id": a["Hall"], "name": "X"}, "field_not_allowed:name"),
        ({"type": E, "entity_id": "binary_sensor.leak", "device_class": "door"}, "field_not_allowed:device_class"),
        ({"type": E, "entity_id": "binary_sensor.leak", "new_entity_id": "binary_sensor.x"}, "field_not_allowed:new_entity_id"),
        ({"type": E, "entity_id": "binary_sensor.leak", "icon": "mdi:x"}, "field_not_allowed:icon"),
        ({"type": E, "entity_id": "binary_sensor.leak", "aliases": ["открой"]}, "field_not_allowed:aliases"),
        ({"type": E, "entity_id": "binary_sensor.leak", "options_domain": "conversation", "options": {"should_expose": True}}, "field_not_allowed:options"),
        ({"type": E, "entity_id": "binary_sensor.leak", "disabled_by": "user"}, "field_not_allowed:disabled_by"),
        ({"type": D, "device_id": d["leak"]}, "nothing_to_change"),
        # hidden_by: only «user» / None (HA's own API takes no other)
        ({"type": E, "entity_id": "binary_sensor.leak", "hidden_by": "integration"}, "bad_hidden_by"),
        ({"type": E, "entity_id": "binary_sensor.leak", "hidden_by": True}, "bad_hidden_by"),
        # labels: only hauflo_role_*, never one a voice assistant exposes by
        ({"type": D, "device_id": d["leak"], "labels": ["alisa"]}, "label_not_hauflo_role"),
        ({"type": D, "device_id": d["leak"], "labels": ["hauflo_monitoring"]}, "label_not_hauflo_role"),
        ({"type": E, "entity_id": "binary_sensor.leak", "labels": ["hauflo_role_Climate"]}, "label_not_hauflo_role"),
        ({"type": E, "entity_id": "binary_sensor.leak", "labels": ["hauflo_role_voice"]}, "label_not_hauflo_role"),
        ({"type": E, "entity_id": "binary_sensor.leak", "labels": "hauflo_role_x"}, "bad_labels"),
        ({"type": E, "entity_id": "binary_sensor.leak", "labels": [f"hauflo_role_{i}" for i in range(11)]}, "bad_labels"),
        # rooms: only the plan's, existing; a device never cleared out of its room
        ({"type": D, "device_id": d["hub"], "area_id": a["Family room"]}, "area_not_in_plan"),
        ({"type": D, "device_id": d["hub"], "area_id": "ghost"}, "area_not_in_plan"),
        ({"type": E, "entity_id": "binary_sensor.leak", "area_id": a["Family room"]}, "area_not_in_plan"),
        ({"type": D, "device_id": d["leak"], "area_id": None}, "area_not_in_plan"),
        ({"type": D, "device_id": d["leak"], "area_id": 5}, "bad_area"),
        # names
        ({"type": D, "device_id": d["leak"], "name_by_user": "  "}, "bad_name"),
        ({"type": D, "device_id": d["leak"], "name_by_user": "x" * 101}, "bad_name"),
        ({"type": E, "entity_id": "binary_sensor.leak", "name": 5}, "bad_name"),
    ]
    send = Send(agent)
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        for msg, _ in cases:
            await send(**msg)
    errs = _errors(out)
    assert local.sent == [], local.sent  # nothing reached HA
    assert len(errs) == len(cases)
    for (msg, why), err in zip(cases, errs):
        assert err["code"] == "not_allowed", msg
        assert err["message"] == (f"Hauflo agent: not allowed ({why})" if why else "Hauflo agent: not allowed"), (msg, err["message"])
    assert foreign.flags(hass).journal == {}
    # Hauflo's own entity / KNX one go by the old rules (no wizard reason): a plain «not allowed».
    assert agent._foreign_reason({"type": E, "entity_id": "sensor.own", "labels": ["x"]}) is None


async def test_no_plan_old_plan_and_backup_first(hass: HomeAssistant, hass_client, house) -> None:
    agent, d, a, local = house["agent"], house["devices"], house["areas"], house["local"]
    send = Send(agent)
    msg = {"type": foreign.DEVICE_UPDATE, "device_id": d["hub"], "name_by_user": "Хаб"}
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(**msg)  # no plan
    assert _errors(out)[0]["message"] == "Hauflo agent: not allowed (no_plan)"
    await _plan(hass, hass_client, house["plan"])
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=False):
        await send(**msg)  # a backup first: nothing kept, nothing sent
    assert _errors(out)[0]["code"] == "backup_required" and local.sent == [] and foreign.flags(hass).journal == {}
    foreign.flags(hass).plans[foreign.LEGACY_PLAN]["at"] -= foreign.PLAN_TTL + 1  # idle too long
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(**msg)
    assert _errors(out)[0]["message"] == "Hauflo agent: not allowed (no_plan)"
    await _plan(hass, hass_client, None)  # cleared
    assert not foreign.active_plans(hass)
    # The 0.22 forms still pass with no plan: an entity hidden / named, a device in no room put in a room.
    devs = dr.async_get(hass)
    devs.async_update_device(d["hub"], area_id=None)
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(type=foreign.ENTITY_UPDATE, entity_id="sensor.hub_link", hidden_by="user")
        await send(type=foreign.DEVICE_UPDATE, device_id=d["hub"], area_id=a["Kitchen"])
    assert _errors(out) == [] and len(local.sent) == 2


async def test_plan_bodies(hass: HomeAssistant, hass_client, hass_read_only_access_token, house) -> None:
    client = await hass_client()
    p = house["plan"]
    for body in (
        {"plan": {"areas": [], "devices": []}},
        {"plan": {**p, "x": []}},
        {"plan": {**p, "entities": ["not an id"]}},
        {"plan": {**p, "devices": [5]}},
        {"plan": {**p, "areas": "kitchen"}},
        {"plan": []},
        {"revert": {"devices": "x"}},
        {"revert": {"devices": [], "x": []}},
        {"revert": []},
        {},
    ):
        assert (await client.post("/api/hauflo/foreign", json=body)).status == 400, body
    user = await hass_client(hass_read_only_access_token)
    assert (await user.post("/api/hauflo/foreign", json={"plan": p})).status == 403
    assert not foreign.active_plans(hass)
    got = await (await client.get("/api/hauflo/foreign")).json()
    assert got == {"notify": {}, "features": ["foreign_wizard", "foreign_plan_id"], "plan": False, "journal": {"devices": [], "entities": []}}


async def test_revert_puts_back_what_hauflo_wrote(hass: HomeAssistant, hass_client, house) -> None:
    agent, d, a, local = house["agent"], house["devices"], house["areas"], house["local"]
    await _plan(hass, hass_client, house["plan"])
    send = Send(agent)
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(type=foreign.DEVICE_UPDATE, device_id=d["leak"], area_id=a["Kitchen"], name_by_user="Протечка", labels=["hauflo_role_leaks"])
        await send(type=foreign.DEVICE_UPDATE, device_id=d["hub"], name_by_user="Хаб", labels=["hauflo_role_devices"])
        await send(type=foreign.ENTITY_UPDATE, entity_id="sensor.leak_battery", area_id=a["Hall"], name="Батарея", hidden_by="user", labels=["hauflo_role_diag"])
        await send(type=foreign.ENTITY_UPDATE, entity_id="sensor.helper", name="Счётчик", labels=["hauflo_role_readings"])
    assert _errors(out) == []
    for s in local.sent:
        _apply(hass, s)
    devs, ents = dr.async_get(hass), er.async_get(hass)
    # Since then, by hand in HA: the hub renamed, a label of the family's own on the helper, its id renamed.
    devs.async_update_device(d["hub"], name_by_user="Мой хаб")
    ents.async_update_entity("sensor.helper", labels={"hauflo_role_readings", "family_tag"}, new_entity_id="sensor.water")
    client = await hass_client()
    with patch.object(foreign.backup, "fresh", return_value=False):
        res = await client.post("/api/hauflo/foreign", json={"revert": {"devices": [d["leak"]]}})
    assert res.status == 428 and (await res.json())["code"] == "backup_required"
    assert devs.async_get(d["leak"]).area_id == a["Kitchen"]  # nothing put back without a backup
    got = await (await client.get("/api/hauflo/foreign")).json()
    assert got["journal"] == {"devices": sorted([d["leak"], d["hub"]]), "entities": sorted(["sensor.leak_battery", "sensor.water"])}
    with patch.object(foreign.backup, "fresh", return_value=True):
        res = await client.post("/api/hauflo/foreign", json={"revert": {"devices": [d["leak"], d["hub"], d["outside"]], "entities": ["sensor.leak_battery", "sensor.water"]}})
    assert res.status == 200, await res.text()
    rv = (await res.json())["reverted"]
    assert rv["devices"] == sorted([d["leak"], d["hub"]]) and rv["entities"] == ["sensor.leak_battery", "sensor.water"]
    assert rv["unknown"] == [d["outside"]]
    assert rv["kept"] == {d["hub"]: ["name_by_user"], "sensor.water": ["labels"]}
    leak = devs.async_get(d["leak"])
    assert (leak.area_id, leak.name_by_user, leak.labels) == (a["Family room"], None, {"family_tag"})  # back to the family's room
    hub = devs.async_get(d["hub"])
    assert (hub.name_by_user, hub.labels) == ("Мой хаб", set())  # his rename stays, Hauflo's role goes
    bat = ents.async_get("sensor.leak_battery")
    assert (bat.area_id, bat.name, bat.hidden_by, bat.labels) == (None, None, None, {"family_tag"})
    water = ents.async_get("sensor.water")
    assert (water.name, water.labels) == (None, {"family_tag"})
    assert foreign.flags(hass).journal == {}


async def test_revert_of_a_device_puts_its_entities_back_too(hass: HomeAssistant, hass_client, house) -> None:
    """The wizard's name template renamed the device's entities; the page's «Откатить» sends the device alone
    (acceptance B 06.11, D3): its entities Hauflo wrote go back with it, an entity of another device stays."""
    agent, d, a, local = house["agent"], house["devices"], house["areas"], house["local"]
    await _plan(hass, hass_client, house["plan"])
    send = Send(agent)
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        await send(type=foreign.DEVICE_UPDATE, device_id=d["leak"], area_id=a["Kitchen"], name_by_user="Протечка", labels=["hauflo_role_leaks"])
        await send(type=foreign.ENTITY_UPDATE, entity_id="sensor.leak_battery", name="Протечка Батарея")
        await send(type=foreign.ENTITY_UPDATE, entity_id="binary_sensor.leak", name="Протечка Датчик")
        await send(type=foreign.ENTITY_UPDATE, entity_id="sensor.helper", name="Счётчик")
    assert _errors(out) == []
    for s in local.sent:
        _apply(hass, s)
    ents = er.async_get(hass)
    assert ents.async_get("sensor.leak_battery").name == "Протечка Батарея"
    client = await hass_client()
    with patch.object(foreign.backup, "fresh", return_value=True):
        res = await client.post("/api/hauflo/foreign", json={"revert": {"devices": [d["leak"]]}})
    assert res.status == 200, await res.text()
    rv = (await res.json())["reverted"]
    assert rv["devices"] == [d["leak"]] and rv["entities"] == ["binary_sensor.leak", "sensor.leak_battery"]
    assert ents.async_get("sensor.leak_battery").name is None and ents.async_get("binary_sensor.leak").name is None
    assert ents.async_get("sensor.helper").name == "Счётчик"  # an entity of no device: not touched
    assert dr.async_get(hass).async_get(d["leak"]).name_by_user is None
    assert set(foreign.flags(hass).journal) == {f"e:{ents.async_get('sensor.helper').id}"}


async def test_reads_the_wizard_needs(hass: HomeAssistant, house) -> None:
    agent = house["agent"]
    devs, ents = dr.async_get(hass), er.async_get(hass)
    hub = house["devices"]["hub"]
    child = devs.async_get_or_create(config_entry_id=devs.async_get(hub).config_entry_id, identifiers={("zha", "child")}, name="Child", via_device_id=hub, model="M1", manufacturer="Acme").id
    # config_entries/get without a domain: id, title, domain only; a phone's never, a title with an e-mail without it.
    MockConfigEntry(domain="xiaomi_miot", title="ivan@example.com").add_to_hass(hass)
    with Out(agent), patch.object(hauflo.backup, "fresh", return_value=True):
        await agent._channel_send(1, json.dumps({"id": 1, "type": "config_entries/get"}))
    assert agent._pending[(1, 1)] == ("config_entries/get:all", None)
    raw = [{"entry_id": e.entry_id, "domain": e.domain, "title": e.title, "source": "user", "state": "loaded", "options": {"k": 1}} for e in hass.config_entries.async_entries()]
    got = json.loads(agent._track(1, json.dumps({"id": 1, "type": "result", "success": True, "result": raw})))["result"]
    by = {e["domain"]: e for e in got}
    assert all(set(e) == {"entry_id", "title", "domain"} for e in got)
    assert "mobile_app" not in by and by["zha"]["title"] == "Zigbee" and by["xiaomi_miot"]["title"] is None
    assert hauflo._scrub_entries({"a": 1}) == []
    # The device and entity lists (HA's own shapes) keep every field the wizard reads.
    agent._pending[(1, 2)] = ("config/device_registry/list", None)
    agent._pending[(1, 3)] = ("config/entity_registry/list", None)
    dl = json.loads(agent._track(1, json.dumps({"id": 2, "type": "result", "success": True, "result": [x.dict_repr for x in devs.devices]})))["result"]
    el = json.loads(agent._track(1, json.dumps({"id": 3, "type": "result", "success": True, "result": [x.as_partial_dict for x in ents.entities.values()]})))["result"]
    c = next(x for x in dl if x["id"] == child)
    for k in ("id", "via_device_id", "identifiers", "entry_type", "name", "name_by_user", "model", "manufacturer", "labels", "area_id"):
        assert k in c, k
    assert c["via_device_id"] == hub and c["model"] == "M1"
    assert next(x for x in dl if x["id"] == house["devices"]["svc"])["entry_type"] == "service"
    h = next(x for x in el if x["entity_id"] == "sensor.helper")
    for k in ("unique_id", "platform", "config_entry_id", "device_id", "has_entity_name", "entity_category", "hidden_by", "labels", "area_id", "original_name"):
        assert k in h, k
    assert h["device_id"] is None and h["unique_id"] == "u-helper"
    assert not any(x["platform"] == "mobile_app" for x in el)  # the phones' never leave


async def test_the_agent_says_it_can(hass: HomeAssistant, house) -> None:
    assert house["agent"].status()["features"] == ["foreign_wizard", "foreign_plan_id"]
    assert '"features": FEATURES' in inspect.getsource(hauflo.Agent.run)  # in the hello too
