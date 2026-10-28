"""Setup and unload on a real HA: the «Связь» entity follows the link, diagnostics never carry the secret, and an
unloaded agent leaves nothing listening (HA core review round 3)."""

import asyncio
import json
import time
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry

from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):  # the link loop: no Hauflo server in tests
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "top-secret"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


def _link_id(hass: HomeAssistant, entry) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_link")
    assert entity_id
    return entity_id


async def test_link_entity_follows_the_link(hass: HomeAssistant, entry) -> None:
    assert entry.state is ConfigEntryState.LOADED
    link = _link_id(hass, entry)
    assert hass.states.get(link).state == STATE_OFF
    assert hass.states.get(link).attributes["server"] == "app.hauflo.ru"
    entry.runtime_data._set_linked(True)
    await hass.async_block_till_done()
    assert hass.states.get(link).state == STATE_ON
    entry.runtime_data._set_linked(False)
    await hass.async_block_till_done()
    assert hass.states.get(link).state == STATE_OFF


async def test_diagnostics_redact_the_secret(hass: HomeAssistant, hass_client, entry) -> None:
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert "top-secret" not in str(diag)
    assert diag["server_host"] == "app.hauflo.ru"
    assert diag["linked"] is False


async def test_unload_leaves_nothing_behind(hass: HomeAssistant, entry) -> None:
    assert hass.data.get("hauflo_sidebar_listener")
    agent = entry.runtime_data
    assert agent.engine is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not hass.data.get("hauflo_sidebar_listener")
    assert not hass.data.get("hauflo_theme_listener")
    assert agent.engine is None
    assert hass.states.get(_link_id(hass, entry)).state == "unavailable"


class _Local:
    def __init__(self):
        self.sent: list[str] = []

    async def send_str(self, data: str) -> None:
        self.sent.append(data)


async def test_only_hauflos_knx_entities_can_be_deleted(hass: HomeAssistant, entry) -> None:
    import json

    reg = er.async_get(hass)
    ours = reg.async_get_or_create("light", "knx", "knx-ui-ours", suggested_object_id="ours")
    by_hand = reg.async_get_or_create("switch", "knx", "knx-ui-by-hand", suggested_object_id="by_hand")
    agent = entry.runtime_data
    local, out = _Local(), []

    async def send(frame):
        out.append(frame)

    agent.channels[1] = local
    from custom_components.hauflo import backup

    with patch.object(agent, "send", send):
        # No backup yet: the change is refused before it reaches HA (security expert 25.10).
        backup.LAST.clear()
        await agent._channel_send(1, json.dumps({"id": 5, "type": "knx/create_entity", "platform": "light", "data": {}}))
        assert len(local.sent) == 0
        assert json.loads(out[-1]["data"])["error"]["code"] == "backup_required"
        backup.LAST.update(state="done", name="Hauflo x", at=time.time())
        # Hauflo creates «ours»; HA answers with its entity id → owned from now on (kept across restarts).
        await agent._channel_send(1, json.dumps({"id": 7, "type": "knx/create_entity", "platform": "light", "data": {}}))
        agent._track(1, json.dumps({"id": 7, "type": "result", "success": True, "result": {"success": True, "entity_id": ours.entity_id}}))
        assert agent.owned == {"knx-ui-ours"}
        # Updating the integrator's switch is refused too, and would never make it Hauflo's (round 4: update, then delete).
        await agent._channel_send(1, json.dumps({"id": 6, "type": "knx/update_entity", "entity_id": by_hand.entity_id, "platform": "switch", "data": {}}))
        assert len(local.sent) == 1
        assert json.loads(out[-1]["data"])["error"]["code"] == "not_allowed"
        assert "knx-ui-by-hand" not in agent.owned
        # The integrator's own switch: refused before it reaches HA.
        await agent._channel_send(1, json.dumps({"id": 8, "type": "knx/delete_entity", "entity_id": by_hand.entity_id}))
        assert len(local.sent) == 1
        assert json.loads(out[-1]["data"])["error"]["code"] == "not_allowed"
        # Its own: passes, and is forgotten once HA deleted it.
        await agent._channel_send(1, json.dumps({"id": 9, "type": "knx/delete_entity", "entity_id": ours.entity_id}))
        assert len(local.sent) == 2
        agent._track(1, json.dumps([{"id": 9, "type": "result", "success": True, "result": None}]))
        assert agent.owned == set()
    agent.channels.pop(1)


async def test_no_backup_integration_is_said(hass: HomeAssistant, hass_client, entry) -> None:
    from custom_components.hauflo import backup

    backup.LAST.clear()
    res = await (await hass_client()).post("/api/hauflo/backup")
    assert res.status == 501  # Hauflo then doesn't deploy


async def test_a_backup_before_a_deploy(hass: HomeAssistant, hass_client, entry) -> None:
    from homeassistant.components.backup.const import DATA_MANAGER
    from homeassistant.setup import async_setup_component

    from custom_components.hauflo import backup

    backup.LAST.clear()
    assert await async_setup_component(hass, "backup", {})
    await hass.async_block_till_done()
    client = await hass_client()
    res = await client.post("/api/hauflo/backup")
    body = await res.json()
    assert res.status == 200, body
    assert body["state"] == "done" and body["name"].startswith("Hauflo ")
    backups, _ = await hass.data[DATA_MANAGER].async_get_backups()
    assert any(b.name == body["name"] and not b.database_included for b in backups.values())
    assert (await (await client.get("/api/hauflo/backup")).json())["state"] == "done"


async def test_hauflo_backups_kept_by_age_and_asked_again_is_the_same(hass: HomeAssistant, hass_client, entry) -> None:
    """Security expert 25.10: asking for backups must not clean away the ones from before an attack."""
    from homeassistant.components.backup.const import DATA_MANAGER
    from homeassistant.setup import async_setup_component

    from custom_components.hauflo import backup

    backup.LAST.clear()
    assert await async_setup_component(hass, "backup", {})
    await hass.async_block_till_done()
    manager = hass.data[DATA_MANAGER]
    agent_id = next(iter(manager.local_backup_agents))
    await manager.async_create_backup(agent_ids=[agent_id], include_addons=None, include_all_addons=False, include_database=False, include_folders=None, include_homeassistant=True, name="Mine before the holidays", password=None)
    client = await hass_client()

    async def ours() -> int:
        backups, _ = await manager.async_get_backups()
        return sum((b.name or "").startswith("Hauflo ") for b in backups.values())

    # Asked again within 10 minutes: the same backup, not a new one — and said so.
    assert (await client.post("/api/hauflo/backup")).status == 200
    first = await ours()
    again = await client.post("/api/hauflo/backup")
    assert again.status == 200
    named = await again.json()
    assert named["reused"] is True
    assert await ours() == first
    # …unless a deploy already went through after it (Hauflo names it in `used`): a new one (review round 2).
    await asyncio.sleep(1.1)  # names are to the second
    fresh = await client.post("/api/hauflo/backup", json={"used": named["name"]})
    body = await fresh.json()
    assert fresh.status == 200 and body["name"] != named["name"] and "reused" not in body
    assert await ours() == first + 1
    first = await ours()
    # Asked many times: a day's are all kept.
    with patch.object(backup, "_REUSE", 0):
        for _ in range(4):
            backup.LAST.clear()
            assert (await client.post("/api/hauflo/backup")).status == 200
        day = await ours()
        assert day >= first + 1
        # Once they're older than a day: the newest three stay.
        with patch.object(backup, "_KEEP_DAY", -1), patch.object(backup, "_KEEP_WEEK", 10**9):
            backup.LAST.clear()
            assert (await client.post("/api/hauflo/backup")).status == 200
            assert await ours() == 3
    backups, _ = await manager.async_get_backups()
    assert "Mine before the holidays" in [b.name for b in backups.values()]  # never one the person made


async def test_without_a_backup_is_said_in_ha(hass: HomeAssistant, hass_client, entry) -> None:
    from custom_components.hauflo import backup

    backup.LAST.clear()
    res = await (await hass_client()).post("/api/hauflo/backup", json={"skip": True})
    assert res.status == 200
    assert backup.fresh() is True
    from homeassistant.components import persistent_notification

    assert backup.NO_BACKUP_NOTE in persistent_notification._async_get_or_create_notifications(hass)


async def test_hauflos_dashboard_becomes_the_default_only_over_has_own(hass: HomeAssistant, entry) -> None:
    from homeassistant.components.frontend.storage import async_system_store

    from custom_components.hauflo.sidebar import async_home

    store = await async_system_store(hass)
    assert await async_home(hass, "hauflo-dom") is True
    assert store.data["core"]["default_panel"] == "hauflo-dom"
    # Chosen in HA by a person (a panel that exists): kept.
    hass.data.setdefault("frontend_panels", {})["my-dash"] = object()
    await store.async_set_item("core", {"default_panel": "my-dash"})
    assert await async_home(hass, "hauflo-dom") is False
    assert store.data["core"]["default_panel"] == "my-dash"
    # A start page that no longer exists (an earlier install's dashboard) is nobody's choice (round 6, B13).
    await store.async_set_item("core", {"default_panel": "gone-dash"})
    assert await async_home(hass, "hauflo-dom") is True
    assert store.data["core"]["default_panel"] == "hauflo-dom"


async def test_a_label_in_use_is_never_deleted_through_the_tunnel(hass: HomeAssistant, entry) -> None:
    from homeassistant.helpers import label_registry as lr

    from custom_components.hauflo import _ws_allowed

    labels = lr.async_get(hass)
    used, empty = labels.async_create("Свет"), labels.async_create("Lighting")
    reg = er.async_get(hass)
    ent = reg.async_get_or_create("light", "knx", "knx-ui-l1", suggested_object_id="l1")
    reg.async_update_entity(ent.entity_id, labels={used.label_id})
    mine = {f"label:{empty.label_id}", f"label:{used.label_id}"}
    assert _ws_allowed({"type": "config/label_registry/delete", "label_id": empty.label_id}, hass, set(), mine) is True
    assert _ws_allowed({"type": "config/label_registry/delete", "label_id": used.label_id}, hass, set(), mine) is False
    # An empty label someone else made (security expert 25.10): not Hauflo's to delete.
    assert _ws_allowed({"type": "config/label_registry/delete", "label_id": empty.label_id}, hass, set(), set()) is False


async def test_get_states_leaves_without_private_attributes(hass: HomeAssistant, entry) -> None:
    import json

    agent = entry.runtime_data
    local = _Local()
    agent.channels[2] = local
    await agent._channel_send(2, json.dumps({"id": 3, "type": "get_states"}))
    states = [
        {"entity_id": "camera.door", "state": "idle", "attributes": {"friendly_name": "Дверь", "access_token": "abc", "entity_picture": "/api/camera_proxy/camera.door?token=abc"}},
        {"entity_id": "person.anna", "state": "home", "attributes": {"friendly_name": "Анна", "latitude": 55.7, "longitude": 37.6, "gps_accuracy": 5, "user_id": "u"}},
        {"entity_id": "climate.kitchen", "state": "heat", "attributes": {"friendly_name": "Кухня", "hvac_modes": ["heat", "off"], "temperature": 22}},
        {"entity_id": "sensor.phone_location", "state": "x", "attributes": {"friendly_name": "f", "latitude": 1, "longitude": 2}},
    ]
    out = json.loads(agent._track(2, json.dumps({"id": 3, "type": "result", "success": True, "result": states})))["result"]
    text = json.dumps(out)
    for secret in ("abc", "55.7", "37.6", "camera_proxy", '"home"'):
        assert secret not in text
    assert out[2]["attributes"] == {"friendly_name": "Кухня", "hvac_modes": ["heat", "off"], "temperature": 22}
    assert out[1]["attributes"] == {"friendly_name": "Анна"}
    # get_config: where the house is never leaves (integrator run 19).
    await agent._channel_send(2, json.dumps({"id": 4, "type": "get_config"}))
    cfg = json.loads(agent._track(2, json.dumps({"id": 4, "type": "result", "success": True, "result": {"version": "2026.10.0", "components": ["knx"], "time_zone": "Europe/Moscow", "latitude": 55.75, "longitude": 37.62, "location_name": "Дом клиента", "config_dir": "/config", "internal_url": "http://192.168.1.2:8123", "external_url": "https://home.example.com"}})))["result"]
    assert cfg == {"version": "2026.10.0", "components": ["knx"], "time_zone": "Europe/Moscow", "external_url": "https://home.example.com"}
    # A login and password in the public address never leave the house (narrow round 3).
    from custom_components.hauflo import _scrub_config
    assert _scrub_config({"external_url": "https://user:s3cr3t@home.example.com:8443/x?token=1"}) == {"external_url": "https://home.example.com:8443/x"}
    assert _scrub_config({"external_url": "ftp://home.example.com"}) == {}
    assert _scrub_config({"external_url": None}) == {"external_url": None}
    # Not asked for → passed as is (an event, another id).
    assert agent._track(2, '{"id": 99, "type": "result", "success": true, "result": []}') == '{"id": 99, "type": "result", "success": true, "result": []}'
    agent.channels.pop(2)


async def test_homekit_never_gets_a_garage_door(hass: HomeAssistant) -> None:
    from custom_components.hauflo import _flow_input_ok

    reg = er.async_get(hass)
    reg.async_get_or_create("cover", "knx", "u-garage", suggested_object_id="garage")
    reg.async_get_or_create("cover", "knx", "u-blind", suggested_object_id="living_blind")
    hass.states.async_set("cover.garage", "closed", {"device_class": "garage"})
    hass.states.async_set("cover.living_blind", "open", {"device_class": "blind"})
    owned = {"u-garage", "u-blind"}
    memo = {"f": ["cover"], "g": ["cover"]}
    assert _flow_input_ok(hass, "homekit", {"entities": ["cover.living_blind"]}, memo, "f", "include", owned, set()) is True
    assert _flow_input_ok(hass, "homekit", {"entities": ["cover.living_blind", "cover.garage"]}, memo, "g", "include", owned, set()) is False
    assert _flow_input_ok(hass, "homekit", {"include_domains": ["cover"]}, memo, "h", "user") is False
    assert _flow_input_ok(hass, "homekit", {"include_domains": ["switch"]}, memo, "h", "user") is False  # a gate's relay
    assert _flow_input_ok(hass, "homekit", {"include_domains": ["person"]}, memo, "h", "user") is True
    assert _flow_input_ok(hass, "homekit", {}, memo, "h", "pairing") is True
    assert _flow_input_ok(hass, "homekit", {"include_domains": ["person"]}, memo, "h", None) is False  # no step known


async def test_the_tunnel_reaches_ha_bound_to_one_address(hass: HomeAssistant, entry) -> None:
    from homeassistant.components.http import ApiConfig

    agent = entry.runtime_data
    hass.config.api = ApiConfig("192.168.1.10", "0.0.0.0", 8123, False)
    assert agent.local_base == "http://127.0.0.1:8123"  # no server_host: HA binds 0.0.0.0, listens everywhere
    hass.config.api = ApiConfig("192.168.1.10", "192.168.1.10", 8123, False)
    assert agent.local_base == "http://192.168.1.10:8123"  # server_host = its own LAN address (HA review round 5)
    hass.config.api = ApiConfig("192.168.1.10", "10.0.0.5", 8443, True)
    assert agent.local_base == "https://10.0.0.5:8443"  # http: server_host: 10.0.0.5
    hass.config.api = ApiConfig("192.168.1.10", "fd00::5", 8123, False)
    assert agent.local_base == "http://[fd00::5]:8123"



async def test_privacy_round_5(hass: HomeAssistant, entry) -> None:
    """A phone's geocoded address, who's home, the sun's times and Nabu Casa's secrets never leave (round 5)."""
    import json

    reg = er.async_get(hass)
    reg.async_get_or_create("sensor", "mobile_app", "iphone_geo", suggested_object_id="iphone_geocoded_location")
    agent = entry.runtime_data
    local = _Local()
    agent.channels[3] = local
    await agent._channel_send(3, json.dumps({"id": 1, "type": "get_states"}))
    states = [
        {"entity_id": "sensor.iphone_geocoded_location", "state": "ул. Садовая 1, Москва", "attributes": {"Location": [55.75, 37.61], "Postal Code": "101000", "friendly_name": "Телефон"}},
        {"entity_id": "zone.home", "state": "2", "attributes": {"latitude": 55.7, "friendly_name": "Дом"}},
        {"entity_id": "sun.sun", "state": "above_horizon", "attributes": {"next_rising": "2026-10-26T04:51:00+00:00"}},
        {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Кухня", "brightness": 200, "Postal Code": "x"}},
    ]
    out = json.dumps(json.loads(agent._track(3, json.dumps({"id": 1, "type": "result", "success": True, "result": states})))["result"])
    for secret in ("Садовая", "55.7", "101000", "04:51", '"2"', "Postal"):
        assert secret not in out, secret
    assert '"brightness": 200' in out
    await agent._channel_send(3, json.dumps({"id": 2, "type": "cloud/status"}))
    status = {"logged_in": True, "email": "owner@mail.ru", "remote_domain": "abc.ui.nabu.casa", "prefs": {"alexa_enabled": True, "google_enabled": False, "google_secure_devices_pin": "1234", "cloudhooks": {"x": {"cloudhook_url": "https://hooks.nabu.casa/x"}}}}
    cloud = json.loads(agent._track(3, json.dumps({"id": 2, "type": "result", "success": True, "result": status})))["result"]
    assert cloud == {"logged_in": True, "cloud": None, "prefs": {"alexa_enabled": True, "google_enabled": False}}
    agent.channels.pop(3)


async def test_a_channel_number_in_use_is_refused(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    sent = []

    async def send(frame):
        sent.append(frame)

    agent.channels[5] = _Local()
    with patch.object(agent, "send", send):
        await agent._open_channel(5)
    assert sent == [{"t": "ws_closed", "ch": 5}]
    assert isinstance(agent.channels[5], _Local)  # the open one stays, no second session behind it
    agent.channels.pop(5)


async def test_homekit_options_need_an_entity_of_every_domain(hass: HomeAssistant) -> None:
    from custom_components.hauflo import _flow_input_ok

    reg = er.async_get(hass)
    reg.async_get_or_create("light", "knx", "u-kitchen", suggested_object_id="kitchen")
    reg.async_get_or_create("cover", "knx", "u-blind", suggested_object_id="living_blind")
    reg.async_get_or_create("light", "knx", "u-by-hand", suggested_object_id="by_hand")
    reg.async_get_or_create("switch", "template", "u-gate", suggested_object_id="gate_relay")
    owned = {"u-kitchen", "u-blind"}
    ok = lambda data, fid, step: _flow_input_ok(hass, "homekit", data, memo, fid, step, owned, set())  # noqa: E731
    memo: dict[str, list[str]] = {}
    init = {"mode": "bridge", "include_exclude_mode": "include", "domains": ["light", "cover"]}
    assert ok(init, "f1", "init")
    # «include» with no cover picked would take every cover (the garage door with them).
    assert ok({"entities": ["light.kitchen"]}, "f1", "include") is False
    ok(init, "f2", "init")
    # Integrator run 20: no «entities» at all (or a string HA makes a list of) took both domains whole.
    assert ok({}, "f2", "include") is False
    assert ok({"entities": "cover.living_blind"}, "f2", "include") is False
    assert ok({"entities": ["light.kitchen", "cover.living_blind"]}, "f2", "include") is True
    assert ok({"entities": ["light.kitchen", "cover.living_blind"]}, "f3", "include") is False  # no init seen
    # Integrator run 21: only Hauflo's own — not the integrator's gate relay, not a KNX light made by hand.
    ok({"mode": "bridge", "include_exclude_mode": "include", "domains": ["switch"]}, "f5", "init")
    assert ok({"entities": ["switch.gate_relay"]}, "f5", "include") is False
    ok({"mode": "bridge", "include_exclude_mode": "include", "domains": ["light"]}, "f6", "init")
    assert ok({"entities": ["light.by_hand"]}, "f6", "include") is False
    assert _flow_input_ok(hass, "homekit", {"devices": []}, memo, "f2", "bridged_device_triggers") is True
    assert _flow_input_ok(hass, "homekit", {"devices": [], "include_domains": ["cover"]}, memo, "f2", "bridged_device_triggers") is False
    assert _flow_input_ok(hass, "homekit", {"mode": "bridge", "domains": ["light"]}, memo, "f4", "init") is False  # include mode said


async def test_alarm_rules_watch_only_house_sensors(hass: HomeAssistant) -> None:
    """Panel round 6: a rule on a person or a phone told the server when the family left."""
    from custom_components.hauflo.notify import _clean_rules

    er.async_get(hass).async_get_or_create("sensor", "mobile_app", "phone-battery", suggested_object_id="phone_battery")
    raw = {"rules": [{"id": "r", "sources": ["binary_sensor.leak", "sensor.co2", "person.anna", "device_tracker.phone", "sensor.phone_battery", "zone.home"]}], "groups": []}
    assert _clean_rules(raw, hass, lambda eid: True)["rules"][0]["sources"] == ["binary_sensor.leak", "sensor.co2"]


async def test_a_push_button_switches_off_only_hauflos_own(hass: HomeAssistant) -> None:
    """Integrator run 22: a rule from the server put «Принял» on a button that turned the gate relay off."""
    from custom_components.hauflo.notify import _button, _clean_rules

    def rule(service: str, eid: str) -> dict:
        return {"rules": [{"id": "r", "sources": ["binary_sensor.leak"], "action": {"title": "Принял", "service": service, "entity_id": eid}}], "groups": []}

    owns = lambda eid: eid in ("switch.hauflo_pump", "valve.water")  # noqa: E731
    assert "action" not in _clean_rules(rule("switch.turn_off", "switch.rele_vorot"), hass, owns)["rules"][0]
    assert "action" not in _clean_rules(rule("switch.turn_off", "switch.hauflo_pump"), hass)["rules"][0]  # owner unknown: none
    assert _clean_rules(rule("switch.turn_off", "switch.hauflo_pump"), hass, owns)["rules"][0]["action"]["entity_id"] == "switch.hauflo_pump"
    assert _clean_rules(rule("valve.close_valve", "valve.water"), hass, owns)["rules"][0]["action"]["entity_id"] == "valve.water"
    assert "action" not in _clean_rules(rule("valve.open_valve", "valve.water"), hass, owns)["rules"][0]
    # Security review 28.10: a valve that isn't Hauflo's (the garden's) is never a button's.
    assert "action" not in _clean_rules(rule("valve.close_valve", "valve.front_garden"), hass, owns)["rules"][0]
    assert _button({"service": "switch.turn_off"}, "Насос") == "Выключить: Насос"
    assert _button({"service": "valve.close_valve"}, "Кран") == "Закрыть: Кран"


async def test_a_group_with_a_garage_door_inside_is_protected(hass: HomeAssistant) -> None:
    from custom_components.hauflo import _protected

    hass.states.async_set("cover.garage", "closed", {"device_class": "garage"})
    hass.states.async_set("cover.blind", "open", {"device_class": "curtain"})
    hass.states.async_set("cover.all", "open", {"entity_id": ["cover.blind", "cover.garage"]})
    hass.states.async_set("cover.blinds", "open", {"entity_id": ["cover.blind"]})
    assert _protected(hass, "cover.all") is True
    assert _protected(hass, "cover.blinds") is False


async def test_notifications_to_hauflo_only_pairing_codes(hass: HomeAssistant) -> None:
    """Panel round 6: failed logins, Alice's link and the rest of HA's notifications stayed visible to the server."""
    from custom_components.hauflo import _scrub_notes

    hk = MockConfigEntry(domain="homekit", title="HASS Bridge")
    hk.add_to_hass(hass)
    event = {"type": "current", "notifications": {hk.entry_id: {"message": "PIN 123-45-678"}, "http-login": {"message": "Login attempt from 1.2.3.4"}, "hauflo_alisa": {"message": "ID x"}}}
    assert list(_scrub_notes(event, hass, {hk.entry_id})["notifications"]) == [hk.entry_id]
    # The integrator's own bridge: its PIN stays in the house (security expert 25.10).
    assert list(_scrub_notes(event, hass, set())["notifications"]) == []


async def test_alice_login_stays_in_the_house(hass: HomeAssistant) -> None:
    from custom_components.hauflo import _alice_creds_to_ha

    res = json.loads(_alice_creds_to_ha(hass, json.dumps({"type": "create_entry", "handler": "yandex_smart_home", "description_placeholders": {"id": "abc", "password": "s3cret", "otp": "-"}})))
    assert res["description_placeholders"] == {"hauflo_note": "hauflo_alisa"}
    assert "s3cret" not in json.dumps(res)
    await hass.async_block_till_done()
    notes = hass.data["persistent_notification"] if "persistent_notification" in hass.data else None
    assert notes is None or "hauflo_alisa" in notes
    # Anything else passes as is.
    other = json.dumps({"type": "form", "handler": "yandex_smart_home", "description_placeholders": {"id": "abc"}})
    assert _alice_creds_to_ha(hass, other) == other


async def test_rooms_hauflo_made_are_its_own_and_others_only_get_a_floor(hass: HomeAssistant, entry) -> None:
    """Security expert 25.10: an integrator's room was renamed «Кухня-PWNED» with the alias «открой ворота»."""
    from homeassistant.helpers import area_registry as ar, floor_registry as fr

    from custom_components.hauflo import _ws_allowed

    agent = entry.runtime_data
    agent._pending[(1, 3)] = ("config/area_registry/create", None)
    agent._track(1, json.dumps({"id": 3, "type": "result", "success": True, "result": {"area_id": "kukhnia", "name": "Кухня"}}))
    assert "area:kukhnia" in agent.owned_entries
    assert _ws_allowed({"type": "config/area_registry/update", "area_id": "kukhnia", "name": "Кухня-столовая"}, hass, set(), agent.owned_entries) is True
    theirs = ar.async_get(hass).async_create("Спальня")
    floor = fr.async_get(hass).async_create("1 этаж")
    upd = {"type": "config/area_registry/update", "area_id": theirs.id, "floor_id": floor.floor_id}
    assert _ws_allowed(upd, hass, set(), agent.owned_entries) is True  # its empty floor filled in
    assert _ws_allowed({**upd, "name": "x"}, hass, set(), agent.owned_entries) is False
    assert _ws_allowed({"type": "config/area_registry/update", "area_id": theirs.id, "aliases": ["открой ворота"]}, hass, set(), agent.owned_entries) is False
    ar.async_get(hass).async_update(theirs.id, floor_id=floor.floor_id)
    other = fr.async_get(hass).async_create("2 этаж")
    assert _ws_allowed({**upd, "floor_id": other.floor_id}, hass, set(), agent.owned_entries) is False  # its floor stays


async def test_only_has_extras_leave_the_menu(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    assert (await client.post("/api/hauflo/sidebar", json={"hide": ["config"]})).status == 400
    assert (await client.post("/api/hauflo/sidebar", json={"hide": ["map", "logbook"]})).status == 200


async def test_an_alarm_sensor_without_data_is_said_and_its_return(hass: HomeAssistant) -> None:
    """SCADA expert 25.10: a leak sensor that lost the bus stayed silent — and the house read «Всё спокойно»."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.hauflo import notify

    eng = notify.NotifyEngine(hass)
    eng.owns = lambda eid: True  # the leak sensor is Hauflo's own
    sent: list[tuple[str, str]] = []

    async def fake_send(rule, title, message, **kw):
        sent.append((title, message))
        return []

    async def later(seconds: int) -> None:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
        await hass.async_block_till_done()

    with patch.object(eng, "_send", fake_send):
        hass.states.async_set("binary_sensor.leak", "off", {"friendly_name": "Протечка в котельной", "device_class": "moisture"})
        await eng.async_start()
        await eng.async_set({"rules": [{"id": "r", "kind": "leak", "title": "Протечка", "sources": ["binary_sensor.leak"], "priority": "critical"}], "groups": []})
        await hass.async_block_till_done()
        hass.states.async_set("binary_sensor.leak", "unavailable", {"friendly_name": "Протечка в котельной", "device_class": "moisture"})
        await hass.async_block_till_done()
        await later(60)
        assert not sent  # a minute without data is not yet said
        await later(notify.NODATA_AFTER + 5)
        await later(notify.NODATA_AFTER + 10)
        assert any("нет связи" in title for title, _ in sent)
        assert any("Протечка в котельной" in message for _, message in sent)
        assert [a.get("noData") for a in eng.active_list()] == [True]  # grey «нет связи», not the alarm itself
        hass.states.async_set("binary_sensor.leak", "off", {"friendly_name": "Протечка в котельной", "device_class": "moisture"})
        await hass.async_block_till_done()
        await later(notify.NODATA_AFTER + 20)
        assert any("связь есть" in title for title, _ in sent)
        assert not eng.active
    eng.async_stop()


async def test_an_alarm_is_accepted_from_the_dashboard(hass: HomeAssistant, hass_client, entry) -> None:
    """SCADA expert 25.10: only a phone's push button could accept an alarm; the dashboard now can, as the person."""
    eng = entry.runtime_data.engine
    eng.owns = lambda eid: True  # the leak sensor is Hauflo's own
    sent: list[str] = []

    async def fake_send(rule, title, message, **kw):
        sent.append(message)
        return []

    with patch.object(eng, "_send", fake_send):
        hass.states.async_set("binary_sensor.leak", "off", {"friendly_name": "Протечка", "device_class": "moisture"})
        await eng.async_set({"rules": [{"id": "r", "kind": "leak", "title": "Протечка", "sources": ["binary_sensor.leak"], "priority": "critical"}], "groups": []})
        hass.states.async_set("binary_sensor.leak", "on", {"friendly_name": "Протечка", "device_class": "moisture"})
        await hass.async_block_till_done()
        client = await hass_client()
        listed = await (await client.get("/api/hauflo/alarms")).json()
        item = next(a for a in listed["_list"] if a["entity"] == "binary_sensor.leak")
        assert item["critical"] is True and item["ackedBy"] is None
        res = await client.post("/api/hauflo/alarms", json={"ack": item["key"]})
        assert res.status == 200
        listed = await (await client.get("/api/hauflo/alarms")).json()
        assert next(a for a in listed["_list"] if a["entity"] == "binary_sensor.leak")["ackedBy"]
        assert (await client.post("/api/hauflo/alarms", json={"ack": "r|binary_sensor.none"})).status == 404


async def test_a_chattering_sensor_is_said_once_and_its_settled_alarm_after(hass: HomeAssistant) -> None:
    """SCADA expert 25.10 (ISA-18.2): 20 changes in 10 minutes sent 20 pushes."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.hauflo import notify

    eng = notify.NotifyEngine(hass)
    eng.owns = lambda eid: True  # the leak sensor is Hauflo's own
    sent: list[str] = []

    async def fake_send(rule, title, message, **kw):
        sent.append(title)
        return []

    async def later(seconds: int) -> None:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
        await hass.async_block_till_done()

    with patch.object(eng, "_send", fake_send):
        hass.states.async_set("binary_sensor.leak", "off", {"friendly_name": "Протечка", "device_class": "moisture"})
        await eng.async_start()
        await eng.async_set({"rules": [{"id": "r", "kind": "leak", "title": "Протечка", "sources": ["binary_sensor.leak"], "priority": "critical"}], "groups": []})
        for _ in range(20):
            hass.states.async_set("binary_sensor.leak", "on", {"friendly_name": "Протечка", "device_class": "moisture"})
            await hass.async_block_till_done()
            await later(notify.GROUP_WINDOW + 1)
            hass.states.async_set("binary_sensor.leak", "off", {"friendly_name": "Протечка", "device_class": "moisture"})
            await hass.async_block_till_done()
            await later(notify.GROUP_WINDOW + 1)
        assert len(sent) <= 6  # the first alarms and «снова в норме», then one «дребезжит» — not 40
        assert sum("📳" in s for s in sent) == 1
        # Settles in alarm: said as an alarm once it stays put.
        hass.states.async_set("binary_sensor.leak", "on", {"friendly_name": "Протечка", "device_class": "moisture"})
        await hass.async_block_till_done()
        before = len(sent)
        await later(notify.CHATTER_WINDOW + 5)
        await later(notify.CHATTER_WINDOW + notify.GROUP_WINDOW + 10)
        assert len(sent) > before and "🚨" in sent[-1]
    eng.async_stop()


async def test_a_critical_alarm_back_to_normal_stays_until_accepted(hass: HomeAssistant, hass_client, entry) -> None:
    """SCADA expert 25.10 (ISA-18.2 returned-unacknowledged): a leak that dried up before anyone looked vanished."""
    eng = entry.runtime_data.engine
    eng.owns = lambda eid: True  # the leak sensor is Hauflo's own

    async def fake_send(rule, title, message, **kw):
        return []

    with patch.object(eng, "_send", fake_send):
        hass.states.async_set("binary_sensor.leak", "off", {"friendly_name": "Протечка", "device_class": "moisture"})
        await eng.async_set({"rules": [{"id": "r", "kind": "leak", "title": "Протечка", "sources": ["binary_sensor.leak"], "priority": "critical"}], "groups": []})
        hass.states.async_set("binary_sensor.leak", "on", {"friendly_name": "Протечка", "device_class": "moisture"})
        await hass.async_block_till_done()
        hass.states.async_set("binary_sensor.leak", "off", {"friendly_name": "Протечка", "device_class": "moisture"})
        await hass.async_block_till_done()
        client = await hass_client()
        listed = (await (await client.get("/api/hauflo/alarms")).json())["_list"]
        item = next(a for a in listed if a["entity"] == "binary_sensor.leak")
        assert item["returned"] and item["critical"] is True
        assert (await client.post("/api/hauflo/alarms", json={"ack": item["key"]})).status == 200
        listed = (await (await client.get("/api/hauflo/alarms")).json())["_list"]
        assert not [a for a in listed if a["entity"] == "binary_sensor.leak"]


async def test_a_knx_write_only_into_ranges_the_integrator_allowed(hass: HomeAssistant, entry) -> None:
    """Narrow round 25.10 (B9): Hauflo writes only into the group address ranges allowed in HA."""
    agent = entry.runtime_data
    hass.config_entries.async_update_entry(entry, options={"ranges": ["1/1"]})
    msg = lambda ga: {"type": "knx/create_entity", "platform": "switch", "data": {"knx": {"ga_switch": {"write": ga, "state": None, "passive": []}}}}  # noqa: E731
    assert agent._outside_ranges(msg("1/1/5")) == set()
    # Asked for address by address, never the whole group around it (KNX review 27.10).
    assert agent._outside_ranges(msg("7/1/1")) == {"7/1/1"}
    agent._ask_ranges({"7/1/1"})
    from custom_components.hauflo.const import DATA_PENDING_RANGES

    assert "7/1/1" in hass.data[DATA_PENDING_RANGES]


async def test_single_addresses_and_spans_allow_only_themselves(hass: HomeAssistant, entry) -> None:
    """KNX review 27.10: «0/0» allowed the ventilation commands 0/0/3, 0/0/5 Hauflo never writes — the integrator
    can allow just 0/0/1 and a span 1/1/1-21."""
    agent = entry.runtime_data
    hass.config_entries.async_update_entry(entry, options={"ranges": ["0/0/1", "1/1/1-21", "2/2"]})
    msg = lambda ga: {"type": "knx/update_entity", "platform": "switch", "data": {"knx": {"ga_switch": {"write": ga, "state": None, "passive": []}}}}  # noqa: E731
    for ga in ("0/0/1", "1/1/1", "1/1/21", "2/2/200", "2/2/0"):
        assert agent._outside_ranges(msg(ga)) == set(), ga
    for ga in ("0/0/3", "0/0/5", "1/1/22", "1/2/1"):
        assert agent._outside_ranges(msg(ga)) == {ga}, ga
    # Spelled otherwise, the same address (numeric 0/0/3 = 3): still refused.
    assert agent._outside_ranges(msg(3)) == {"0/0/3"}


async def test_a_push_the_ha_app_failed_quietly_is_not_sent(hass: HomeAssistant) -> None:
    """Narrow round 25.10 (D4): mobile_app logs a failed push and returns — Hauflo said «sent»."""
    import logging

    from custom_components.hauflo import notify

    async def broken(call):
        logging.getLogger("homeassistant.components.mobile_app.notify").error("Error sending notification to TestPhone")

    async def fine(call):
        return None

    hass.services.async_register("notify", "mobile_app_broken", broken)
    hass.services.async_register("notify", "mobile_app_fine", fine)
    eng = notify.NotifyEngine(hass)
    eng.owns = lambda eid: True  # the leak sensor is Hauflo's own
    sent = await eng._send({"recipients": {"all": True}}, "Протечка", "Кухня", tag="t", critical=True, targets=[{"service": "mobile_app_broken", "os": "android"}, {"service": "mobile_app_fine", "os": "ios"}])
    assert sent == ["mobile_app_fine"]
    assert eng.sent_log[0]["failed"] == ["mobile_app_broken"]


def test_voice_strays_of_an_earlier_agent_go(tmp_path) -> None:
    """Round 6: an earlier agent's sentences under another name stayed — «Unknown intent». Ours by the header go; a
    file a human wrote stays."""
    from custom_components.hauflo.voice import HEADER, SENTENCES, _write

    folder = tmp_path / "custom_sentences" / "ru"
    folder.mkdir(parents=True)
    (folder / "old.yaml").write_text(HEADER + "language: ru\nintents: {}\n", encoding="utf-8")
    (folder / "mine.yaml").write_text("language: ru\nintents: {}\n", encoding="utf-8")
    path = str(folder / "hauflo.yaml")
    assert _write(path) is True
    assert sorted(p.name for p in folder.iterdir()) == ["hauflo.yaml", "mine.yaml"]
    assert (folder / "hauflo.yaml").read_text(encoding="utf-8") == SENTENCES
    assert _write(path) is False  # nothing changed the second time
