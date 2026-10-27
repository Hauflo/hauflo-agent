"""«Автоматизации» of the dashboard (automations.py, owner 28.10): a client's sunrise / sunset / time / alarm rules become
real HA automations `hauflo_auto_…` acting only on Hauflo's own covers, lights and scenes with fixed services; any
signed-in person may make them, the tunnel may not; another automation is never touched."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import dump, load_yaml
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import _http_allowed
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

BLIND = "cover.living_blind"
LIGHT = "light.living_ceiling"
LAMP = "light.living_lamp"
SCENE = "scene.living_evening"
GARAGE = "cover.garage_door"  # Hauflo's, but it opens the house
HAND = "light.integrator_hand"  # a KNX light the integrator made by hand
LOCK = "lock.front"
PHONE_ALARM = "sensor.pixel_next_alarm"
URL = "/api/hauflo/automations"
# A sun trigger keeps HA's timer for the next sunset — HA's own, not a leak of the agent.
pytestmark = pytest.mark.parametrize("expected_lingering_timers", [True])
THEIRS = {"id": "1700000000000", "alias": "Integrator: gate at night", "triggers": [{"trigger": "time", "at": "23:00:00"}], "actions": [{"action": "lock.lock", "target": {"entity_id": LOCK}}]}


@pytest.fixture
async def entry(hass: HomeAssistant, tmp_path: Path):
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
    (tmp_path / "automations.yaml").write_text(dump([THEIRS]))
    assert await async_setup_component(hass, "automation", {"automation": [THEIRS]})
    await hass.async_block_till_done()

    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    reg = er.async_get(hass)
    for eid, uid, extra in (
        (BLIND, "u-blind", {"supported_features": 15}),
        (LIGHT, "u-light", {"supported_color_modes": ["brightness"]}),
        (LAMP, "u-lamp", {"supported_color_modes": ["onoff"]}),
        (SCENE, "u-scene", {}),
        (GARAGE, "u-garage", {"device_class": "garage", "supported_features": 3}),
        (HAND, "u-hand", {"supported_color_modes": ["onoff"]}),
    ):
        domain, obj = eid.split(".")
        reg.async_get_or_create(domain, "knx", uid, suggested_object_id=obj)
        hass.states.async_set(eid, "off" if domain != "cover" else "open", {"friendly_name": obj.replace("_", " "), **extra})
    hass.states.async_set(LOCK, "locked", {})
    area = __import__("homeassistant.helpers.area_registry", fromlist=["x"]).async_get(hass).async_create("Гостиная")
    for eid in (LIGHT, LAMP, BLIND):
        reg.async_update_entity(eid, area_id=area.id)
    reg.async_get_or_create("sensor", "mobile_app", "abc_next_alarm", suggested_object_id="pixel_next_alarm")
    hass.states.async_set(PHONE_ALARM, "2026-10-29T04:30:00+00:00", {"device_class": "timestamp"})
    entry.runtime_data.owned |= {"u-blind", "u-light", "u-lamp", "u-scene", "u-garage"}  # «u-hand»: the integrator's
    entry.area = area.id
    yield entry


@pytest.fixture
def bus(hass: HomeAssistant):
    made: list[tuple[str, object, dict]] = []

    def record(domain: str, service: str):
        async def handler(call: ServiceCall) -> None:
            made.append((f"{domain}.{service}", call.data.get("entity_id"), {k: v for k, v in call.data.items() if k != "entity_id"}))

        hass.services.async_register(domain, service, handler)

    for d, s in (("cover", "open_cover"), ("cover", "close_cover"), ("cover", "set_cover_position"), ("light", "turn_on"), ("light", "turn_off"), ("scene", "turn_on"), ("lock", "lock")):
        record(d, s)
    return made


def _file(hass: HomeAssistant) -> list[dict]:
    return load_yaml(hass.config.path("automations.yaml")) or []


SUNSET = {"when": {"event": "sunset", "offset": -15}, "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "actions": [{"entity_id": BLIND, "do": "close"}, {"entity_id": LIGHT, "do": "on", "value": 30}], "name": "Закат −15 мин"}


async def test_sunset_rule_end_to_end(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry, bus) -> None:
    hass.config.latitude, hass.config.longitude = 59.94, 30.31
    # The client isn't an admin: allowed.
    client = await hass_client(hass_read_only_access_token)
    got = await (await client.get(URL)).json()
    t = got["targets"]
    assert [c["entity_id"] for c in t["covers"]] == [BLIND]  # no garage door
    assert {x["entity_id"] for x in t["lights"]} == {LIGHT, LAMP}  # not the integrator's own
    assert [s["entity_id"] for s in t["scenes"]] == [SCENE]
    assert t["rooms"] == [{"area_id": entry.area, "name": "Гостиная", "lights": 2}]
    assert [a["entity_id"] for a in t["alarms"]] == [PHONE_ALARM]
    assert got["location"]["set"] and len(got["days"]) == 8 and got["days"][0]["sunset"]

    res = await client.post(URL, json={"op": "save", "rule": SUNSET})
    assert res.status == 200, await res.text()
    rule = await res.json()
    rid = rule["id"]
    assert rid.startswith("hauflo_auto_") and len(rid) == len("hauflo_auto_") + 12
    await hass.async_block_till_done()
    mine = [a for a in _file(hass) if a.get("id") == rid][0]
    assert mine["triggers"] == [{"trigger": "sun", "event": "sunset", "offset": "-00:15:00"}]
    assert mine["conditions"] == []
    assert mine["actions"] == [
        {"action": "cover.close_cover", "target": {"entity_id": BLIND}},
        {"action": "light.turn_on", "target": {"entity_id": LIGHT}, "data": {"brightness_pct": 30}},
    ]
    assert mine["initial_state"] is True and mine["alias"] == "Hauflo · Закат −15 мин"
    assert [a for a in _file(hass) if a.get("id") == THEIRS["id"]] == [THEIRS]  # the integrator's stays as it was
    eid = er.async_get(hass).async_get_entity_id("automation", "automation", rid)
    assert eid and hass.states.get(eid).state == "on"

    # «Проверить сейчас»: HA's own automation runs once.
    res = await client.post(URL, json={"op": "run", "id": rid})
    assert res.status == 200
    await hass.async_block_till_done()
    assert ("cover.close_cover", [BLIND], {}) in bus or ("cover.close_cover", BLIND, {}) in bus
    assert any(m[0] == "light.turn_on" and m[2] == {"brightness_pct": 30} for m in bus)

    # Off — and back on; weekdays only.
    res = await client.post(URL, json={"op": "enable", "id": rid, "enabled": False})
    assert res.status == 200
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "off"
    assert [a for a in _file(hass) if a.get("id") == rid][0]["initial_state"] is False
    res = await client.post(URL, json={"op": "save", "rule": {**SUNSET, "id": rid, "days": ["mon", "fri"], "enabled": True}})
    assert res.status == 200
    await hass.async_block_till_done()
    assert [a for a in _file(hass) if a.get("id") == rid][0]["conditions"] == [{"condition": "time", "weekday": ["mon", "fri"]}]
    assert hass.states.get(eid).state == "on"
    listed = (await (await client.get(URL)).json())["rules"]
    assert [(r["id"], r["state"], r["days"]) for r in listed] == [(rid, "on", ["mon", "fri"])]

    # Deleted: from the file, HA and the list; the integrator's stays.
    assert (await client.post(URL, json={"op": "delete", "id": rid})).status == 200
    await hass.async_block_till_done()
    assert [a.get("id") for a in _file(hass)] == [THEIRS["id"]]
    assert er.async_get(hass).async_get_entity_id("automation", "automation", rid) is None
    assert (await (await client.get(URL)).json())["rules"] == []


@pytest.mark.parametrize(
    "rule",
    [
        {**SUNSET, "actions": [{"entity_id": HAND, "do": "on"}]},  # the integrator's own light
        {**SUNSET, "actions": [{"entity_id": GARAGE, "do": "open"}]},  # opens the house
        {**SUNSET, "actions": [{"entity_id": LOCK, "do": "on"}]},
        {**SUNSET, "actions": [{"entity_id": "light.nowhere", "do": "on"}]},
        {**SUNSET, "actions": [{"entity_id": BLIND, "do": "toggle"}]},  # not a service a rule may do
        {**SUNSET, "actions": [{"entity_id": BLIND, "do": "stop"}]},
        {**SUNSET, "actions": [{"entity_id": SCENE, "do": "off"}]},
        {**SUNSET, "actions": [{"action": "shell_command.x"}]},
        {**SUNSET, "actions": [{"entity_id": BLIND, "do": "position", "value": 101}]},
        {**SUNSET, "actions": [{"entity_id": LIGHT, "do": "on", "value": 0}]},
        {**SUNSET, "actions": []},
        {**SUNSET, "actions": [{"entity_id": LAMP, "do": "ramp", "value": 25, "minutes": 10}]},  # not dimmable
        {**SUNSET, "actions": [{"entity_id": LIGHT, "do": "ramp", "value": 25, "minutes": 61}]},
        {**SUNSET, "actions": [{"entity_id": LIGHT, "do": "ramp", "value": 25}]},
        {**SUNSET, "from": "../x"},
        {**SUNSET, "when": {"event": "sunset", "offset": 121}},
        {**SUNSET, "when": {"event": "sunset", "offset": "-15"}},
        {**SUNSET, "when": {"event": "sunset", "offset": 1.5}},
        {**SUNSET, "when": {"event": "sunset", "offset": True}},
        {**SUNSET, "when": {"event": "noon"}},
        {**SUNSET, "when": {"event": "time", "at": "25:00"}},
        {**SUNSET, "when": {"event": "time", "at": "{{ now() }}"}},
        {**SUNSET, "when": {"event": "alarm_android", "sensor": "sensor.outside_temperature", "offset": 0}},
        {**SUNSET, "days": []},
        {**SUNSET, "days": ["mon", "mon"]},
        {**SUNSET, "days": ["monday"]},
        {**SUNSET, "id": "1700000000000"},  # someone else's automation
        {**SUNSET, "id": "hauflo_scene_x"},
        "not a rule",
    ],
)
async def test_refused(hass: HomeAssistant, hass_client, entry, bus, rule) -> None:
    client = await hass_client()
    res = await client.post(URL, json={"op": "save", "rule": rule})
    assert res.status == 400, await res.text()
    assert [a.get("id") for a in _file(hass)] == [THEIRS["id"]]


async def test_only_own_ids(hass: HomeAssistant, hass_client, entry, bus) -> None:
    client = await hass_client()
    for op in ("delete", "run", "enable"):
        res = await client.post(URL, json={"op": op, "id": THEIRS["id"], "enabled": False})
        assert res.status == 400
        # The prefix alone isn't enough: only one the agent made.
        res = await client.post(URL, json={"op": op, "id": "hauflo_auto_0123456789ab", "enabled": False})
        assert res.status == 404
    res = await client.post(URL, json={"op": "save", "rule": {**SUNSET, "id": "hauflo_auto_0123456789ab"}})
    assert res.status == 404
    assert _file(hass) == [THEIRS]
    assert hass.states.get("automation.integrator_gate_at_night").state == "on"


async def test_time_alarm_and_room(hass: HomeAssistant, hass_client, entry, bus) -> None:
    client = await hass_client()
    room = {"when": {"event": "time", "at": "23:30"}, "days": ["sat", "sun"], "actions": [{"do": "room_off", "area_id": entry.area}, {"entity_id": SCENE, "do": "on"}]}
    r = await (await client.post(URL, json={"op": "save", "rule": room})).json()
    mine = [a for a in _file(hass) if a.get("id") == r["id"]][0]
    assert mine["triggers"] == [{"trigger": "time", "at": "23:30:00"}]
    assert mine["actions"][0] == {"action": "light.turn_off", "target": {"entity_id": [LIGHT, LAMP]}}  # Hauflo's lights only
    assert mine["actions"][1] == {"action": "scene.turn_on", "target": {"entity_id": SCENE}}

    # Android: 20 min before the phone's alarm the light comes up 1 → 25 % in steps (KNX has no transition), the blind
    # goes to 40 % first.
    android = {"when": {"event": "alarm_android", "sensor": PHONE_ALARM, "offset": -20}, "from": "wake_light", "actions": [{"entity_id": LIGHT, "do": "ramp", "value": 25, "minutes": 20}, {"entity_id": BLIND, "do": "position", "value": 40}]}
    r = await (await client.post(URL, json={"op": "save", "rule": android})).json()
    assert r["from"] == "wake_light"
    mine = [a for a in _file(hass) if a.get("id") == r["id"]][0]
    assert mine["triggers"] == [{"trigger": "time", "at": {"entity_id": PHONE_ALARM, "offset": "-00:20:00"}}]
    assert mine["actions"][0] == {"action": "cover.set_cover_position", "target": {"entity_id": BLIND}, "data": {"position": 40}}
    ramp = mine["actions"][1:]
    levels = [a["data"]["brightness_pct"] for a in ramp if "action" in a]
    assert len(levels) == 12 and levels[-1] == 25 and levels == sorted(levels) and levels[0] >= 1
    assert all(a == {"delay": {"seconds": 100}} for a in ramp if "delay" in a)
    # «Проверить сейчас» never waits the 20 minutes.
    res = await client.post(URL, json={"op": "run", "id": r["id"]})
    assert res.status == 200
    for _ in range(20):
        await asyncio.sleep(0)
    assert any(m[0] == "cover.set_cover_position" for m in bus)
    assert any(m[0] == "light.turn_on" and m[2] == {"brightness_pct": 2} for m in bus)  # the first step only
    eid = er.async_get(hass).async_get_entity_id("automation", "automation", r["id"])
    await hass.services.async_call("automation", "turn_off", {"entity_id": eid, "stop_actions": True}, blocking=True)
    await hass.async_block_till_done()

    # iPhone: the event's name is the agent's, kept over edits, whatever the client sends.
    iphone = {"when": {"event": "alarm_iphone", "code": "call_service"}, "actions": [{"entity_id": BLIND, "do": "open"}]}
    r = await (await client.post(URL, json={"op": "save", "rule": iphone})).json()
    code = r["when"]["code"]
    assert code.startswith("hauflo_alarm_") and code != "call_service"
    r2 = await (await client.post(URL, json={"op": "save", "rule": {**iphone, "id": r["id"]}})).json()
    assert r2["when"]["code"] == code
    await hass.async_block_till_done()
    bus.clear()
    hass.bus.async_fire(code, {})
    await hass.async_block_till_done()
    assert [m[0] for m in bus] == ["cover.open_cover"]


async def test_location(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry) -> None:
    hass.config.latitude, hass.config.longitude = 0, 0
    client = await hass_client(hass_read_only_access_token)
    got = await (await client.get(URL)).json()
    assert got["location"]["set"] is False and len(got["days"]) == 8 and got["days"][0]["sunset"] is None  # times only
    for bad in ({"latitude": 91, "longitude": 0}, {"latitude": "59", "longitude": 30}, {"latitude": 59.9, "longitude": 30.3, "location_name": "x"}, {"latitude": 0, "longitude": 0}, {"latitude": float("nan"), "longitude": 1}):
        res = await client.post(URL, data=__import__("json").dumps({"op": "location", **bad}).replace("NaN", "NaN"), headers={"Content-Type": "application/json"})
        assert res.status == 400, bad
    name_before = hass.config.location_name
    res = await client.post(URL, json={"op": "location", "latitude": 59.938, "longitude": 30.314, "elevation": 12})
    assert res.status == 200
    assert (hass.config.latitude, hass.config.longitude, hass.config.elevation) == (59.938, 30.314, 12)
    assert hass.config.location_name == name_before
    got = await (await client.get(URL)).json()
    assert got["location"]["set"] and got["days"][0]["sunrise"]

    class Info:
        latitude, longitude, city, region_name, country_code, time_zone, ip, zip_code = 55.75, 37.62, "Moscow", "Moscow", "RU", "Europe/Moscow", "1.2.3.4", "101000"

    with patch("homeassistant.util.location.async_detect_location_info", return_value=Info()):
        d = await (await client.post(URL, json={"op": "detect"})).json()
    assert d["city"] == "Moscow" and d["latitude"] == 55.75 and d["region"] == "Moscow" and "ip" not in d and "zip_code" not in d
    assert hass.config.latitude == 59.938  # detecting never saves


async def test_tunnel_cannot_reach(hass: HomeAssistant) -> None:
    for method, body in (("GET", ""), ("POST", {"op": "save", "rule": SUNSET}), ("POST", {"op": "location", "latitude": 1, "longitude": 1})):
        assert not _http_allowed(hass, method, URL, body)
    # Nor HA's own automation editor (a generic automation-create).
    assert not _http_allowed(hass, "POST", "/api/config/automation/config/hauflo_auto_0123456789ab", SUNSET)
