"""What Hauflo made is identifiable in HA (service-company review 29.10): one «Hauflo» label on exactly Hauflo's
entities, kept in sync by the agent; «Создано в этом доме» says how many, which were changed by hand and what the next deploy
would do; empty rooms Hauflo made may be removed through the tunnel, nobody else's."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er, label_registry as lr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from custom_components.hauflo.diagnostics import async_get_config_entry_diagnostics

from custom_components.hauflo import _http_allowed, _http_locked, _ws_allowed
from custom_components.hauflo import ownership as own_mod
from custom_components.hauflo.const import CONF_ALLOW_CHANGES, CONF_SECRET, CONF_SERVER, DOMAIN, MONITOR_LABEL_EN


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):  # the link loop: no Hauflo server in tests
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


def _make(hass: HomeAssistant, entry) -> dict[str, str]:
    reg = er.async_get(hass)
    agent = entry.runtime_data
    group_entry = MockConfigEntry(domain="group", title="Свет в комнате")
    group_entry.add_to_hass(hass)
    agent.owned_entries.add(group_entry.entry_id)
    agent.owned.add("u-ours")
    ids = {
        "ours": reg.async_get_or_create("light", "knx", "u-ours", suggested_object_id="ours").entity_id,
        "hand": reg.async_get_or_create("light", "knx", "u-hand", suggested_object_id="hand").entity_id,
        "group": reg.async_get_or_create("light", "group", "g1", config_entry=group_entry, suggested_object_id="room").entity_id,
        "scene": reg.async_get_or_create("scene", "homeassistant", "hauflo_r1_evening", suggested_object_id="evening").entity_id,
        "their_scene": reg.async_get_or_create("scene", "homeassistant", "1700000", suggested_object_id="theirs").entity_id,
        "auto": reg.async_get_or_create("automation", "automation", "hauflo_auto_0123456789ab", suggested_object_id="wake").entity_id,
        "zigbee": reg.async_get_or_create("sensor", "zha", "z1", suggested_object_id="boiler").entity_id,
    }
    return ids


def _label(hass: HomeAssistant) -> str:
    found = [label for label in lr.async_get(hass).async_list_labels() if label.name == own_mod.LABEL]
    assert len(found) == 1
    return found[0].label_id


async def test_one_label_on_exactly_haufls_entities(hass: HomeAssistant, entry) -> None:
    ids = _make(hass, entry)
    reg = er.async_get(hass)
    # Someone put the label on a foreign entity by hand: it comes off (the label means «made by Hauflo»).
    own = entry.runtime_data.ownership
    own.async_sync()
    label = _label(hass)
    reg.async_update_entity(ids["zigbee"], labels={label, "boiler_room"})
    added, removed = own.async_sync()
    assert removed == 1 and added == 0
    labelled = {e.entity_id for e in er.async_entries_for_label(reg, label)}
    assert {ids["ours"], ids["group"], ids["scene"], ids["auto"]} <= labelled
    assert not labelled & {ids["hand"], ids["their_scene"], ids["zigbee"]}
    assert reg.async_get(ids["zigbee"]).labels == {"boiler_room"}  # the person's own label stays
    # The agent's own entities («Связь», «Создано в этом доме») carry it too; entity ids never change.
    assert reg.async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_link") in labelled
    assert all(reg.async_get(e) is not None for e in ids.values())
    # Run again: nothing to do — and still one label, never a second «Hauflo».
    assert own.async_sync() == (0, 0)
    _label(hass)


async def test_label_follows_ownership_and_is_reused(hass: HomeAssistant, entry) -> None:
    ids = _make(hass, entry)
    agent = entry.runtime_data
    agent.ownership.async_sync()
    label = _label(hass)
    # Deleted through the tunnel (knx/delete_entity) — no longer Hauflo's: the label comes off on the next sync.
    agent.owned.discard("u-ours")
    agent.ownership.async_sync()
    assert label not in er.async_get(hass).async_get(ids["ours"]).labels
    # A label someone already called «hauflo» is reused, not duplicated.
    lr.async_get(hass).async_delete(label)
    pre = lr.async_get(hass).async_create("hauflo")
    agent.ownership.label_id = None
    agent.ownership.async_sync()
    assert pre.label_id in er.async_get(hass).async_get(ids["group"]).labels
    assert [lb.name for lb in lr.async_get(hass).async_list_labels() if lb.name.casefold() == "hauflo"] == ["hauflo"]


async def test_registry_changes_trigger_a_sync(hass: HomeAssistant, entry) -> None:
    ids = _make(hass, entry)
    own = entry.runtime_data.ownership
    with patch.object(own, "async_sync") as sync:
        er.async_get(hass).async_update_entity(ids["ours"], icon="mdi:lamp")
        er.async_get(hass).async_update_entity(ids["group"], icon="mdi:lamp")
        await hass.async_block_till_done()
        sync.assert_not_called()  # not at once: after the burst
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=own_mod.SYNC_DELAY + 1))
        await hass.async_block_till_done()
        sync.assert_called_once()
    assert own._timer is None


async def test_locked_house_gets_no_label_changes(hass: HomeAssistant, entry) -> None:
    ids = _make(hass, entry)
    hass.config_entries.async_update_entry(entry, options={CONF_ALLOW_CHANGES: False})
    await hass.async_block_till_done()
    assert entry.runtime_data.ownership.async_sync() == (0, 0)
    assert not er.async_get(hass).async_get(ids["ours"]).labels


async def test_monitoring_label_says_what_it_is_for(hass: HomeAssistant, entry) -> None:
    reg = lr.async_get(hass)
    old = next((lb for lb in reg.async_list_labels() if lb.name == MONITOR_LABEL_EN), None) or reg.async_create(MONITOR_LABEL_EN)
    reg.async_update(old.label_id, description="Hauflo: watch it and send alarms to Telegram")
    mine = reg.async_create("Hauflo мониторинг", description="наш щит")
    entry.runtime_data.ownership.async_sync()
    assert reg.async_get_label(old.label_id).description == own_mod.MONITOR_TEXT["en"]
    assert reg.async_get_label(mine.label_id).description == "наш щит"  # a hand-written one stays


async def test_sensor_says_owned_hand_edited_and_next_deploy(hass: HomeAssistant, entry, hass_client) -> None:
    ids = _make(hass, entry)
    reg = er.async_get(hass)
    reg.async_update_entity(ids["ours"], name="Люстра над столом")  # renamed in HA by hand
    own = entry.runtime_data.ownership
    sensor = reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_ownership")
    assert sensor
    own.async_sync()
    await hass.async_block_till_done()
    st = hass.states.get(sensor)
    assert int(st.state) == own.facts()["owned"] >= 4
    assert ids["ours"] in st.attributes["hand_edited"] and ids["ours"] in st.attributes["renamed"]
    assert st.attributes["next_deploy"] is None
    # Hauflo tells what the next deploy would do (after a plan): numbers, the kept ones; anything else dropped.
    client = await hass_client()
    res = await client.post("/api/hauflo/ownership", json={"create": 3, "update": 2, "leftovers": 1, "kept_count": 1, "kept": [ids["hand"], "<script>"], "dashboard": True, "evil": "x", "project": "Вилла"})
    assert res.status == 200
    await hass.async_block_till_done()
    st = hass.states.get(sensor)
    nd = st.attributes["next_deploy"]
    assert nd["create"] == 3 and nd["update"] == 2 and nd["leftovers"] == 1 and nd["dashboard"] is True and nd["project"] == "Вилла" and "evil" not in nd and nd["at"]
    assert st.attributes["kept_by_plan"] == [ids["hand"]]
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["ownership"]["next_deploy"]["create"] == 3 and ids["ours"] in diag["ownership"]["renamed"]
    assert (await client.post("/api/hauflo/ownership", data="nope")).status == 400
    # Kept over a restart of the agent.
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(sensor).attributes["next_deploy"]["create"] == 3


async def test_names_hauflo_set_are_not_hand_renames(hass: HomeAssistant, entry, monkeypatch) -> None:
    """HA reviewer r2: right after a fresh deploy «23 renamed» — the names Hauflo gives its light / cover groups in the
    registry (through the tunnel) counted as a person's; the agent's own entities counted as «made»."""
    import json

    ids = _make(hass, entry)
    agent = entry.runtime_data
    own = agent.ownership
    reg = er.async_get(hass)

    class _Local:
        async def send_str(self, data):
            return None

    agent.channels[5] = _Local()
    monkeypatch.setattr("custom_components.hauflo.backup.fresh", lambda: True)  # a deploy: the backup is made

    async def tunnel_update(mid: int, **kw) -> None:
        msg = {"id": mid, "type": "config/entity_registry/update", **kw}
        await agent._channel_send(5, json.dumps(msg))
        old = kw["entity_id"]
        changes = {k: v for k, v in kw.items() if k in ("name", "new_entity_id", "icon")}
        updated = reg.async_update_entity(old, **changes)  # what HA does with it
        agent._track(5, json.dumps({"id": mid, "type": "result", "success": True, "result": {"entity_entry": {"entity_id": updated.entity_id, "name": updated.name}}}))

    await tunnel_update(1, entity_id=ids["group"], name="Свет в комнате", icon="mdi:lightbulb-group-outline")
    facts = own.facts()
    assert ids["group"] not in facts["renamed"] and not facts["renamed"]
    assert "agent" not in facts["by_kind"] and facts["owned"] == sum(facts["by_kind"].values())
    # Moved to another entity_id by Hauflo: still Hauflo's name.
    await tunnel_update(2, entity_id=ids["group"], new_entity_id="light.room_lights_kitchen")
    assert not own.facts()["renamed"]
    # The person renames it in HA: now it's theirs.
    reg.async_update_entity("light.room_lights_kitchen", name="Кухня — весь свет")
    assert own.facts()["renamed"] == ["light.room_lights_kitchen"]
    # A refused / failed update remembers nothing.
    await agent._channel_send(5, json.dumps({"id": 3, "type": "config/entity_registry/update", "entity_id": ids["ours"], "name": "X"}))
    agent._track(5, json.dumps({"id": 3, "type": "result", "success": False, "error": {"code": "x"}}))
    reg.async_update_entity(ids["ours"], name="X")
    assert ids["ours"] in own.facts()["renamed"]
    # Kept over a restart of the agent.
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.ownership.names.get("light.room_lights_kitchen") == "Свет в комнате"
    agent.channels.pop(5, None)


async def test_an_earlier_agents_group_names_are_taken_as_hauflos_once(hass: HomeAssistant, entry) -> None:
    """Updated from an agent that kept no names: the groups' names now are Hauflo's; KNX names stay the person's."""
    ids = _make(hass, entry)
    reg = er.async_get(hass)
    reg.async_update_entity(ids["group"], name="Room lights")
    reg.async_update_entity(ids["ours"], name="Люстра")
    own = entry.runtime_data.ownership
    own.names = {}
    await own.store.async_save({"plan": None})  # what 0.16.17 kept
    await own.async_start()
    assert own.names == {ids["group"]: "Room lights"}
    assert own.facts()["renamed"] == [ids["ours"]]


def test_the_tunnel_may_say_the_plan_even_in_a_locked_house(hass: HomeAssistant) -> None:
    assert _http_allowed(hass, "POST", "/api/hauflo/ownership", "{}")
    assert not _http_allowed(hass, "DELETE", "/api/hauflo/ownership", "{}")
    assert not _http_locked("POST", "/api/hauflo/ownership")


def test_clean_plan_refuses_junk() -> None:
    with pytest.raises(ValueError):
        own_mod.clean_plan([1])
    got = own_mod.clean_plan({"create": -1, "update": True, "kept": "light.x"})
    assert got.pop("at") and got == {"kept": []}


async def test_only_empty_rooms_hauflo_made_may_be_removed(hass: HomeAssistant, entry, hass_client) -> None:
    ids = _make(hass, entry)
    agent = entry.runtime_data
    areas = ar.async_get(hass)
    ours_empty = areas.async_create("Room 201")
    ours_used = areas.async_create("Kitchen")
    theirs = areas.async_create("Гараж")
    agent.owned_entries |= {f"area:{ours_empty.id}", f"area:{ours_used.id}"}
    er.async_get(hass).async_update_entity(ids["ours"], area_id=ours_used.id)

    def ok(area_id: str, **extra) -> bool:
        return _ws_allowed({"id": 1, "type": "config/area_registry/delete", "area_id": area_id, **extra}, hass, agent.owned, agent.owned_entries)

    assert ok(ours_empty.id)
    assert not ok(ours_used.id)  # something still in it
    assert not ok(theirs.id)  # not Hauflo's
    assert not ok(ours_empty.id, name="x")
    assert not ok("missing")
    # The plan learns which rooms Hauflo made (read only, admin).
    client = await hass_client()
    body = await (await client.get("/api/hauflo/owned")).json()
    assert set(body["areas"]) == {ours_empty.id, ours_used.id}
