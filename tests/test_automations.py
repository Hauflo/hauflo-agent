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
HAND = "light.integrator_hand"  # a KNX light the integrator made by hand and hid in HA: no rule may touch it
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
    # Owner 04.11: a light of any integration may be in a rule — one the person sees in HA (this one is hidden).
    reg.async_update_entity(HAND, hidden_by=er.RegistryEntryHider.INTEGRATION)
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
    assert {x["entity_id"] for x in t["lights"]} == {LIGHT, LAMP}  # not the hidden one
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
    # No initial_state: HA keeps a switch a person made in HA over restarts (HA expert 28.10).
    assert "initial_state" not in mine and mine["alias"] == "Hauflo · Закат −15 мин" and mine["description"] == "15 min before sunset: close — living blind; brightness 30 % — living ceiling. Made in Hauflo."
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
    assert [a for a in _file(hass) if a.get("id") == rid][0] == mine  # switching never rewrites the file
    assert (await (await client.get(URL)).json())["rules"][0]["enabled"] is False
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
        {**SUNSET, "actions": [{"entity_id": HAND, "do": "on"}]},  # hidden in HA
        {**SUNSET, "actions": [{"entity_id": GARAGE, "do": "open"}]},  # opens the house
        {**SUNSET, "actions": [{"entity_id": LOCK, "do": "on"}]},
        {**SUNSET, "actions": [{"entity_id": "light.nowhere", "do": "on"}]},
        {**SUNSET, "actions": [{"entity_id": BLIND, "do": "press"}]},  # not a service a blind has
        {**SUNSET, "actions": [{"entity_id": BLIND, "do": "speed", "value": 50}]},
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
    # Every light of the room is Hauflo's: the room itself (a lamp added later follows).
    assert mine["actions"][0] == {"action": "light.turn_off", "target": {"area_id": entry.area}}
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

    # 09.11 п.23: the iPhone runs it by the HA app's «Trigger automation» (automation.trigger, conditions skipped) — the
    # days are the actions' first step, so a day not chosen does nothing either way; «Проверить сейчас» still runs.
    from homeassistant.util import dt as dt_util
    today = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[dt_util.now().weekday()]
    other = [d for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun") if d != today]
    r3 = await (await client.post(URL, json={"op": "save", "rule": {**iphone, "id": r["id"], "days": other}})).json()
    mine = [a for a in _file(hass) if a.get("id") == r["id"]][0]
    assert mine["conditions"] == [] and mine["actions"][0]["condition"] == "or"
    await hass.async_block_till_done()
    eid = er.async_get(hass).async_get_entity_id("automation", "automation", r["id"])
    for how in ({"entity_id": eid}, None):
        bus.clear()
        if how:
            await hass.services.async_call("automation", "trigger", how, blocking=True)
        else:
            hass.bus.async_fire(r3["when"]["code"], {})
        await hass.async_block_till_done()
        assert bus == []
    res = await client.post(URL, json={"op": "run", "id": r["id"]})
    assert res.status == 200
    await hass.async_block_till_done()
    assert [m[0] for m in bus] == ["cover.open_cover"]
    r4 = await (await client.post(URL, json={"op": "save", "rule": {**iphone, "id": r["id"]}})).json()
    await hass.async_block_till_done()
    bus.clear()
    await hass.services.async_call("automation", "trigger", {"entity_id": eid}, blocking=True)
    await hass.async_block_till_done()
    assert [m[0] for m in bus] == ["cover.open_cover"] and r4["when"]["code"] == code


async def test_room_with_the_integrators_light_is_listed(hass: HomeAssistant, hass_client, entry, bus) -> None:
    er.async_get(hass).async_update_entity(HAND, area_id=entry.area)
    client = await hass_client()
    rule = {"when": {"event": "time", "at": "23:30"}, "actions": [{"do": "room_off", "area_id": entry.area}]}
    r = await (await client.post(URL, json={"op": "save", "rule": rule})).json()
    mine = [a for a in _file(hass) if a.get("id") == r["id"]][0]
    assert mine["actions"] == [{"action": "light.turn_off", "target": {"entity_id": [LIGHT, LAMP]}}]  # never HAND


async def test_edited_in_ha_is_never_overwritten(hass: HomeAssistant, hass_client, entry, bus) -> None:
    """Edited in HA's own editor (HA expert 28.10): Hauflo says so, switching keeps the edit, a save is refused unless
    «Вернуть как в Hauflo» (force) was asked."""
    client = await hass_client()
    r = await (await client.post(URL, json={"op": "save", "rule": SUNSET})).json()
    rid = r["id"]
    await hass.async_block_till_done()
    data = _file(hass)
    for a in data:
        if a.get("id") == rid:
            a["actions"] = [{"action": "light.turn_on", "target": {"entity_id": LIGHT}, "data": {"brightness_pct": 70}}]
    Path(hass.config.path("automations.yaml")).write_text(dump(data))
    listed = (await (await client.get(URL)).json())["rules"]
    assert listed[0]["changed"] is True
    assert (await client.post(URL, json={"op": "enable", "id": rid, "enabled": False})).status == 200
    assert [a for a in _file(hass) if a.get("id") == rid][0]["actions"][0]["data"] == {"brightness_pct": 70}
    res = await client.post(URL, json={"op": "save", "rule": {**SUNSET, "id": rid}})
    assert res.status == 409
    assert [a for a in _file(hass) if a.get("id") == rid][0]["actions"][0]["data"] == {"brightness_pct": 70}
    res = await client.post(URL, json={"op": "save", "rule": {**SUNSET, "id": rid}, "force": True})
    assert res.status == 200
    assert (await (await client.get(URL)).json())["rules"][0]["changed"] is False


async def test_room_blinds_as_one(hass: HomeAssistant, hass_client, entry, bus) -> None:
    """«Все шторы спальни» as one action (family review 28.10): the room's own Hauflo blinds, never its garage door."""
    er.async_get(hass).async_update_entity(GARAGE, area_id=entry.area)
    client = await hass_client()
    got = await (await client.get(URL)).json()
    assert got["targets"]["cover_rooms"] == [{"area_id": entry.area, "name": "Гостиная", "covers": 1, "position": True}]
    rule = {"when": {"event": "time", "at": "21:00"}, "actions": [{"do": "room_covers", "area_id": entry.area, "cover": "position", "value": 30}]}
    r = await (await client.post(URL, json={"op": "save", "rule": rule})).json()
    mine = [a for a in _file(hass) if a.get("id") == r["id"]][0]
    assert mine["actions"] == [{"action": "cover.set_cover_position", "target": {"entity_id": [BLIND]}, "data": {"position": 30}}]
    for bad in ({"do": "room_covers", "area_id": "nowhere", "cover": "close"}, {"do": "room_covers", "area_id": entry.area, "cover": "unlock"}, {"do": "room_covers", "area_id": entry.area, "cover": "position", "value": 101}):
        res = await client.post(URL, json={"op": "save", "rule": {**rule, "actions": [bad]}})
        assert res.status == 400, bad


async def test_time_zone_admin_only(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry) -> None:
    """HA in UTC showed a Moscow family sunset at 13:58 (family review 28.10): the owner may fix the zone, nobody else."""
    reader = await hass_client(hass_read_only_access_token)
    assert (await reader.post(URL, json={"op": "timezone", "time_zone": "Europe/Moscow"})).status == 403
    admin = await hass_client()
    for bad in ("Mars/Olympus", "", 5):
        assert (await admin.post(URL, json={"op": "timezone", "time_zone": bad})).status == 400
    res = await admin.post(URL, json={"op": "timezone", "time_zone": "Europe/Moscow"})
    assert res.status == 200 and (await res.json())["time_zone"] == "Europe/Moscow"
    assert hass.config.time_zone == "Europe/Moscow"


async def test_sun_rule_without_the_home_location_is_said(hass: HomeAssistant, hass_client, entry, bus) -> None:
    """HA expert 28.10: a sunset rule saved while HA's location is 0 / 0 — saved, with a warning."""
    hass.config.latitude, hass.config.longitude = 0, 0
    client = await hass_client()
    res = await client.post(URL, json={"op": "save", "rule": SUNSET})
    assert res.status == 200, await res.text()
    got = await res.json()
    assert got["warning"] == "no_location" and got["warning_text"]
    hass.config.latitude, hass.config.longitude = 59.94, 30.31
    got = await (await client.post(URL, json={"op": "save", "rule": {**SUNSET, "id": got["id"]}})).json()
    assert "warning" not in got
    # A time rule needs no place.
    hass.config.latitude, hass.config.longitude = 0, 0
    timed = {**SUNSET, "when": {"event": "time", "at": "07:00"}}
    got = await (await client.post(URL, json={"op": "save", "rule": timed})).json()
    assert "warning" not in got, got


async def test_location(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry) -> None:
    hass.config.latitude, hass.config.longitude = 0, 0
    family = await hass_client(hass_read_only_access_token)
    got = await (await family.get(URL)).json()
    assert got["location"]["set"] is False and len(got["days"]) == 8 and got["days"][0]["sunset"] is None  # times only
    # Security review 28.10: a household member (not an admin) moved HA's home (zone.home) — now an admin's only.
    for body in ({"op": "location", "latitude": 59.938, "longitude": 30.314}, {"op": "detect"}):
        res = await family.post(URL, json=body)
        assert res.status == 403, body
        assert (await res.json())["code"] == "admin_only"
    assert (hass.config.latitude, hass.config.longitude) == (0, 0)
    assert hass.states.get("zone.home") is None or (hass.states.get("zone.home").attributes.get("latitude"), hass.states.get("zone.home").attributes.get("longitude")) == (0, 0)
    client = await hass_client()  # the owner (an admin)
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


NIGHT = {"when": {"event": "time", "at": "23:30"}, "actions": [{"entity_id": SCENE, "do": "on"}]}


@pytest.mark.parametrize(
    "text",
    [
        "# Integrator: keep the gate rule first\n" + dump([THEIRS]),
        dump([THEIRS]).replace("alias:", "alias:  ", 1),  # hand formatting
        dump([THEIRS]) + "- !include more_automations.yaml\n",
        dump([{**THEIRS, "alias": "x"}]).replace("alias: x", "alias: !secret gate_alias"),
        "!include_dir_list automations/\n",
    ],
    ids=["comment", "hand-formatting", "include", "secret", "include-dir"],
)
async def test_not_plain_file_is_never_rewritten(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry, bus, text) -> None:
    # Security review 28.10: the agent re-dumped automations.yaml — comments and !include of the integrator's gone.
    path = Path(hass.config.path("automations.yaml"))
    path.write_text(text)
    client = await hass_client(hass_read_only_access_token)
    res = await client.post(URL, json={"op": "save", "rule": NIGHT})
    assert res.status == 409, await res.text()
    got = await res.json()
    assert got["code"] == "not_plain" and "automations.yaml" in got["message"]
    assert path.read_text() == text  # byte for byte
    assert not list(path.parent.glob("automations.yaml.hauflo-bak-*"))
    assert (await (await client.get(URL)).json())["rules"] == []  # nothing half-saved


async def test_backups_lock_and_migration(hass: HomeAssistant, hass_client, entry, bus) -> None:
    path = Path(hass.config.path("automations.yaml"))
    # Migration: a rule agent 0.14.4 wrote tonight sits in the file next to the integrator's (both as HA dumps them).
    client = await hass_client()
    old = await (await client.post(URL, json={"op": "save", "rule": NIGHT})).json()
    assert path.read_text() == dump([THEIRS, [a for a in _file(hass) if a["id"] == old["id"]][0]])
    # Two saves at once (two phones): both land — one read-modify-write at a time.
    a, b = await asyncio.gather(*(client.post(URL, json={"op": "save", "rule": {**NIGHT, "name": n}}) for n in ("A", "B")))
    assert a.status == b.status == 200
    ids = {r["id"] for r in (await a.json(), await b.json())}
    assert ids | {old["id"], THEIRS["id"]} == {x["id"] for x in _file(hass)}
    # The old rule still edits, enables and deletes.
    assert (await client.post(URL, json={"op": "enable", "id": old["id"], "enabled": False})).status == 200
    assert (await client.post(URL, json={"op": "delete", "id": old["id"]})).status == 200
    assert old["id"] not in {x["id"] for x in _file(hass)}
    # A copy before every write, the last 3 kept; the newest is the file as it was before the delete.
    baks = sorted(path.parent.glob("automations.yaml.hauflo-bak-*"))
    assert len(baks) == 3
    assert old["id"] in baks[-1].read_text() and load_yaml(str(baks[-1]))[0] == THEIRS
    assert [x for x in _file(hass) if x["id"] == THEIRS["id"]] == [THEIRS]


async def test_empty_or_missing_file(hass: HomeAssistant, hass_client, entry, bus) -> None:
    path = Path(hass.config.path("automations.yaml"))
    client = await hass_client()
    for text in ("[]", "", None):
        if text is None:
            path.unlink()
        else:
            path.write_text(text)
        res = await client.post(URL, json={"op": "save", "rule": NIGHT})
        assert res.status == 200, (text, await res.text())
        assert len(_file(hass)) == 1


async def test_tunnel_cannot_reach(hass: HomeAssistant) -> None:
    for method, body in (("GET", ""), ("POST", {"op": "save", "rule": SUNSET}), ("POST", {"op": "location", "latitude": 1, "longitude": 1})):
        assert not _http_allowed(hass, method, URL, body)
    # Nor HA's own automation editor (a generic automation-create).
    assert not _http_allowed(hass, "POST", "/api/config/automation/config/hauflo_auto_0123456789ab", SUNSET)


def test_saved_without_location_is_a_caution_not_an_error() -> None:
    """Re-verifier 28.10: «saved, but the home's location isn't set» showed in red (.msg.bad) — it is saved: amber."""
    js = (Path(__file__).parents[1] / "custom_components/hauflo/www/hauflo-cards.js").read_text(encoding="utf-8")
    line = next(x for x in js.splitlines() if "w.locWarn" in x and "toList" in x)
    assert "warn: true" in line and "bad: true" not in line
    assert ".msg.warn{" in js and "hf-amber" in js.split(".msg.warn{", 1)[1].split("}", 1)[0]
    assert js.count('this.bad ? "bad" : this.warn ? "warn" : ""') == 2


# --- owner 03.11: «что сделать» over a set, a rule by a reading (one sensor or the average of several), «только если» ---
T1, T2, T3 = "sensor.living_temp", "sensor.bedroom_temp", "sensor.kitchen_temp"
HUM = "sensor.living_humidity"


def _sensors(hass: HomeAssistant) -> None:
    for eid, v in ((T1, "21.0"), (T2, "23.0"), (T3, "25.0")):
        hass.states.async_set(eid, v, {"device_class": "temperature", "unit_of_measurement": "°C", "friendly_name": eid})
    hass.states.async_set(HUM, "40", {"device_class": "humidity", "unit_of_measurement": "%"})


async def test_set_action_rooms_all_and_devices(hass: HomeAssistant, hass_client, entry, bus) -> None:
    """Several devices, a whole room, everything of a kind — one action, HA's own target (owner 03.11: «по одному»)."""
    client = await hass_client()
    every = {"when": {"event": "time", "at": "07:00"}, "actions": [{"do": "set", "domain": "light", "cmd": "on", "all": True, "value": 60}, {"do": "set", "domain": "cover", "cmd": "position", "rooms": [entry.area], "value": 40}, {"do": "set", "domain": "scene", "cmd": "on", "entities": [SCENE]}]}
    r = await client.post(URL, json={"op": "save", "rule": every})
    assert r.status == 200, await r.text()
    rid = (await r.json())["id"]
    mine = [a for a in _file(hass) if a.get("id") == rid][0]
    assert mine["actions"] == [
        {"action": "light.turn_on", "target": {"area_id": [entry.area]}, "data": {"brightness_pct": 60}},
        # Blinds by name, never the room: a garage door put in the room later would follow it.
        {"action": "cover.set_cover_position", "target": {"entity_id": [BLIND]}, "data": {"position": 40}},
        {"action": "scene.turn_on", "target": {"entity_id": [SCENE]}},
    ]
    # A hidden light in the room: the room becomes its other lights by name, never HAND.
    er.async_get(hass).async_update_entity(HAND, area_id=entry.area)
    picked = {"when": {"event": "time", "at": "07:00"}, "actions": [{"do": "set", "domain": "light", "cmd": "off", "rooms": [entry.area], "entities": [LIGHT]}]}
    r = await (await client.post(URL, json={"op": "save", "rule": picked})).json()
    mine = [a for a in _file(hass) if a.get("id") == r["id"]][0]
    assert mine["actions"] == [{"action": "light.turn_off", "target": {"entity_id": [LIGHT, LAMP]}}]
    bus.clear()
    assert (await client.post(URL, json={"op": "run", "id": r["id"]})).status == 200
    await hass.async_block_till_done()
    assert [m[0] for m in bus] == ["light.turn_off"] and sorted(bus[0][1]) == [LIGHT, LAMP]
    for bad in (
        {"do": "set", "domain": "light", "cmd": "on"},  # nothing picked
        {"do": "set", "domain": "light", "cmd": "on", "entities": [HAND]},
        {"do": "set", "domain": "light", "cmd": "on", "entities": ["light.nowhere"]},
        {"do": "set", "domain": "cover", "cmd": "open", "entities": [GARAGE]},
        {"do": "set", "domain": "light", "cmd": "on", "entities": [BLIND]},  # another kind
        {"do": "set", "domain": "light", "cmd": "ramp", "entities": [LIGHT]},
        {"do": "set", "domain": "lock", "cmd": "unlock", "all": True},
        {"do": "set", "domain": "scene", "cmd": "on", "all": True},
        {"do": "set", "domain": "light", "cmd": "on", "rooms": ["nowhere"]},
        {"do": "set", "domain": "light", "cmd": "on", "entities": [LIGHT, LIGHT]},
        {"do": "set", "domain": "cover", "cmd": "position", "rooms": [entry.area]},  # no value
        {"do": "set", "domain": "light", "cmd": "on", "all": True, "value": 0},
    ):
        res = await client.post(URL, json={"op": "save", "rule": {**picked, "actions": [bad]}})
        assert res.status == 400, bad


async def test_value_rule_one_sensor_and_the_average(hass: HomeAssistant, hass_client, entry, bus) -> None:
    """«Средняя температура выше 24° — закрыть шторы»: one sensor → its own numeric_state; several → HA's min_max helper
    (an unavailable one left out by HA), shared and removed with the last rule."""
    _sensors(hass)
    client = await hass_client()
    got = await (await client.get(URL)).json()
    assert {s["entity_id"] for s in got["targets"]["sensors"]} >= {T1, T2, T3, HUM}
    one = {"when": {"event": "value", "sensors": [T1], "op": "below", "x": 18.5, "for_min": 10}, "actions": [{"do": "set", "domain": "light", "cmd": "on", "rooms": [entry.area]}]}
    r = await client.post(URL, json={"op": "save", "rule": one})
    assert r.status == 200, await r.text()
    mine = [a for a in _file(hass) if a.get("id") == (await r.json())["id"]][0]
    assert mine["triggers"] == [{"trigger": "numeric_state", "entity_id": T1, "below": 18.5, "for": {"minutes": 10}}]

    avg = {"when": {"event": "value", "sensors": [T3, T1, T2], "stat": "mean", "op": "above", "x": 24}, "actions": [{"do": "set", "domain": "cover", "cmd": "close", "all": True}], "only_if": [{"if": "light"}]}
    r = await client.post(URL, json={"op": "save", "rule": avg})
    assert r.status == 200, await r.text()
    rule = await r.json()
    helper = rule["when"]["entity"]
    assert helper.startswith("sensor.") and rule["when"]["sensors"] == sorted([T1, T2, T3])
    await hass.async_block_till_done()
    assert float(hass.states.get(helper).state) == 23.0
    ent = er.async_get(hass).async_get(helper)
    assert ent.platform == "min_max" and ent.name.startswith("Hauflo · ")
    assert helper not in {s["entity_id"] for s in (await (await client.get(URL)).json())["targets"]["sensors"]}  # not offered as a sensor
    mine = [a for a in _file(hass) if a.get("id") == rule["id"]][0]
    assert mine["triggers"] == [{"trigger": "numeric_state", "entity_id": helper, "above": 24}]
    assert mine["conditions"] == [{"condition": "sun", "after": "sunrise", "before": "sunset"}]
    # An unavailable sensor: HA's average of the others.
    hass.states.async_set(T1, "unavailable", {"device_class": "temperature", "unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert float(hass.states.get(helper).state) == 24.0
    # The same sensors in another rule: the same helper; deleted one by one — the helper goes with the last.
    r2 = await (await client.post(URL, json={"op": "save", "rule": {**avg, "only_if": [{"if": "between", "after": "22:00", "before": "06:00"}]}})).json()
    assert r2["when"]["entity"] == helper
    assert [a for a in _file(hass) if a.get("id") == r2["id"]][0]["conditions"] == [{"condition": "time", "after": "22:00:00", "before": "06:00:00"}]
    assert (await client.post(URL, json={"op": "delete", "id": rule["id"]})).status == 200
    assert hass.states.get(helper) is not None and len(hass.config_entries.async_entries("min_max")) == 1
    # Edited to one sensor: the helper is no longer needed.
    r3 = await (await client.post(URL, json={"op": "save", "rule": {**avg, "id": r2["id"], "when": {**avg["when"], "sensors": [T2]}}})).json()
    assert r3["when"]["entity"] == T2
    await hass.async_block_till_done()
    assert hass.config_entries.async_entries("min_max") == []
    for bad in (
        {**avg["when"], "sensors": []},
        {**avg["when"], "sensors": [T1, HUM]},  # two kinds
        {**avg["when"], "sensors": ["sensor.nowhere"]},
        {**avg["when"], "sensors": [PHONE_ALARM]},
        {**avg["when"], "sensors": [helper]},
        {**avg["when"], "stat": "sum"},
        {**avg["when"], "op": "equal"},
        {**avg["when"], "x": "{{ 1 }}"},
        {**avg["when"], "for_min": 241},
    ):
        res = await client.post(URL, json={"op": "save", "rule": {**avg, "when": bad}})
        assert res.status == 400, bad
    assert hass.config_entries.async_entries("min_max") == []  # a refused rule leaves no helper


async def test_only_if_and_presence(hass: HomeAssistant, hass_client, entry, bus) -> None:
    _sensors(hass)
    client = await hass_client()
    away = {"when": {"event": "presence", "to": "away"}, "actions": [{"do": "set", "domain": "light", "cmd": "off", "all": True}], "only_if": [{"if": "dark"}, {"if": "value", "sensors": [HUM], "op": "above", "x": 30}]}
    r = await client.post(URL, json={"op": "save", "rule": away})
    assert r.status == 200, await r.text()
    mine = [a for a in _file(hass) if a.get("id") == (await r.json())["id"]][0]
    assert mine["triggers"] == [{"trigger": "numeric_state", "entity_id": "zone.home", "below": 1}]
    assert mine["conditions"] == [
        {"condition": "or", "conditions": [{"condition": "sun", "after": "sunset"}, {"condition": "sun", "before": "sunrise"}]},
        {"condition": "numeric_state", "entity_id": HUM, "above": 30},
    ]
    for bad in (
        {**away, "only_if": [{"if": "dark"}, {"if": "light"}]},
        {**away, "only_if": [{"if": "home"}, {"if": "away"}]},
        {**away, "only_if": [{"if": "between", "after": "22:00", "before": "22:00"}]},
        {**away, "only_if": [{"if": "between", "after": "25:00", "before": "06:00"}]},
        {**away, "only_if": [{"if": "template"}]},
        {**away, "only_if": [{"if": "dark"}] * 6},
        {**away, "when": {"event": "presence", "to": "work"}},
        # never true at its own trigger (live check 03.11: «пришёл первый» + «только если никого» was saved)
        {**away, "only_if": [{"if": "home"}]},
        {**away, "when": {"event": "presence", "to": "home"}, "only_if": [{"if": "away"}]},
        {**away, "when": {"event": "time", "at": "07:00"}, "only_if": [{"if": "between", "after": "22:00", "before": "06:00"}]},
        {**away, "when": {"event": "time", "at": "07:00"}, "only_if": [{"if": "between", "after": "08:00", "before": "09:00"}, {"if": "between", "after": "06:00", "before": "08:00"}]},
        {**away, "when": {"event": "value", "sensors": [T1, T2], "stat": "mean", "op": "above", "x": 25}, "only_if": [{"if": "value", "sensors": [T2, T1], "stat": "mean", "op": "below", "x": 25}]},
        {**away, "only_if": [{"if": "value", "sensors": [HUM], "op": "above", "x": 60}, {"if": "value", "sensors": [HUM], "op": "below", "x": 40}]},
        # the sensors decide the kind: a humidity sensor isn't taken for a temperature rule
        {**away, "when": {"event": "value", "kind": "temperature", "sensors": [HUM], "op": "above", "x": 25}, "only_if": []},
    ):
        res = await client.post(URL, json={"op": "save", "rule": bad})
        assert res.status == 400, bad
    for good in (
        {**away, "when": {"event": "time", "at": "23:00"}, "only_if": [{"if": "between", "after": "22:00", "before": "06:00"}]},
        {**away, "when": {"event": "time", "at": "05:59"}, "only_if": [{"if": "between", "after": "22:00", "before": "06:00"}]},
        {**away, "when": {"event": "value", "sensors": [T1, T2], "stat": "mean", "op": "above", "x": 25}, "only_if": [{"if": "value", "sensors": [T1, T2], "stat": "max", "op": "below", "x": 25}]},
        {**away, "only_if": [{"if": "value", "sensors": [HUM], "op": "above", "x": 40}, {"if": "value", "sensors": [HUM], "op": "below", "x": 60}]},
    ):
        res = await client.post(URL, json={"op": "save", "rule": good})
        assert res.status == 200, (good, await res.text())


async def test_old_rules_keep_their_file_and_digest(hass: HomeAssistant, hass_client, entry, bus) -> None:
    """A rule saved before 03.11 (no only_if, per-device actions) makes the very same automation — no «changed in HA»."""
    client = await hass_client()
    r = await (await client.post(URL, json={"op": "save", "rule": SUNSET})).json()
    before = [a for a in _file(hass) if a.get("id") == r["id"]][0]
    assert before["conditions"] == [] and "only_if" not in r
    listed = (await (await client.get(URL)).json())["rules"]
    assert listed[0]["changed"] is False


M1, M2 = "binary_sensor.living_motion", "binary_sensor.living_presence"
DOOR = "binary_sensor.front_door"
TV, TV2 = "media_player.living_tv", "media_player.kitchen_tv"


def _binaries(hass: HomeAssistant, entry) -> None:
    """Motion (two classes of one kind), a door known only by its registry class, a TV; and what never qualifies."""
    reg = er.async_get(hass)
    for eid, uid, attrs in (
        (M1, "u-m1", {"device_class": "motion"}),
        (M2, "u-m2", {"device_class": "occupancy"}),
        (DOOR, "u-door", {}),
        ("binary_sensor.hidden_motion", "u-hm", {"device_class": "motion"}),
        ("binary_sensor.diag_door", "u-dd", {"device_class": "door"}),
        ("binary_sensor.link", "u-link", {"device_class": "connectivity"}),
        (TV, "u-tv", {}),
        (TV2, "u-tv2", {}),
    ):
        domain, obj = eid.split(".")
        reg.async_get_or_create(domain, "zha", uid, suggested_object_id=obj)
        hass.states.async_set(eid, "off" if domain == "binary_sensor" else "idle", {"friendly_name": obj, **attrs})
    reg.async_update_entity(DOOR, device_class="door")
    reg.async_update_entity("binary_sensor.hidden_motion", hidden_by=er.RegistryEntryHider.USER)
    reg.async_update_entity("binary_sensor.diag_door", entity_category=er.EntityCategory.DIAGNOSTIC)
    for eid in (M1, M2, TV):
        reg.async_update_entity(eid, area_id=entry.area)


async def test_recipes_are_offered_accepted_and_the_alarm_clock_yaml(hass: HomeAssistant, hass_client, entry, bus) -> None:
    """p6d: every recipe the house can run is accepted by the engine as offered; the wake-up chain end to end."""
    reg = er.async_get(hass)
    area_reg = __import__("homeassistant.helpers.area_registry", fromlist=["x"]).async_get(hass)
    bed = area_reg.async_create("Спальня")
    for eid, uid, extra in (
        ("light.bed_lamp", "u-bedlamp", {"supported_color_modes": ["brightness"]}),
        ("cover.bed_blind", "u-bedblind", {"supported_features": 15}),
        ("climate.bed", "u-bedclimate", {"supported_features": 1, "min_temp": 7, "max_temp": 30, "target_temp_step": 0.5, "hvac_modes": ["off", "heat"]}),
        ("fan.bed", "u-bedfan", {"supported_features": 48}),
    ):
        domain, obj = eid.split(".")
        reg.async_get_or_create(domain, "knx", uid, suggested_object_id=obj)
        hass.states.async_set(eid, "off" if domain != "cover" else "open", {"friendly_name": obj, **extra})
        reg.async_update_entity(eid, area_id=bed.id)
    entry.runtime_data.owned |= {"u-bedlamp", "u-bedblind"}
    reg.async_get_or_create("sensor", "knx", "bed-co2", suggested_object_id="bed_co2")
    hass.states.async_set("sensor.bed_co2", "700", {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm", "friendly_name": "co2"})
    reg.async_update_entity("sensor.bed_co2", area_id=bed.id)
    _binaries(hass, entry)
    client = await hass_client()
    got = await (await client.get(URL)).json()
    offered = {r["key"]: r for r in got["recipes"]}
    assert {"sunset_covers", "wake_covers", "wake_light", "wake_climate", "night_off", "night_climate", "co2_fan_on", "co2_fan_off", "motion_light_on", "motion_light_off", "media_dim"} <= set(offered)
    assert "away_off" not in offered or got["targets"]["people"]  # «nobody home» is never known without a person
    # Whatever is offered, the engine saves as offered (and writes a real HA automation).
    for key, r in offered.items():
        res = await client.post(URL, json={"op": "save", "rule": r["rule"]})
        assert res.status == 200, (key, await res.text())
    await hass.async_block_till_done()
    by_from = {x.get("description"): x for x in _file(hass)}
    assert len([a for a in _file(hass) if str(a.get("id", "")).startswith("hauflo_auto_")]) == len(offered)

    # The alarm clock, Android (the phone's next_alarm sensor): the chain at -20 min.
    light = next(a for a in _file(hass) if a["alias"].startswith("Hauflo ·") and any(s.get("data", {}).get("brightness_pct") == 25 for s in a["actions"] if "action" in s))
    assert light["triggers"] == [{"trigger": "time", "at": {"entity_id": PHONE_ALARM, "offset": "-00:20:00"}}]
    levels = [s["data"]["brightness_pct"] for s in light["actions"] if "action" in s]
    assert len(levels) == 12 and levels[-1] == 25 and levels == sorted(levels)
    warm = next(a for a in _file(hass) if any(s.get("action") == "climate.set_temperature" and s["data"] == {"temperature": 21.0} for s in a["actions"]))
    assert warm["triggers"] == [{"trigger": "time", "at": {"entity_id": PHONE_ALARM, "offset": "-00:30:00"}}]
    # Failure cases: the sensor goes «unavailable» (alarm cleared / phone off) — the rule stays, HA simply does not fire
    # (a time trigger on a non-timestamp state is skipped); the page's «Проверить» says which step is missing.
    hass.states.async_set(PHONE_ALARM, "unavailable", {"device_class": "timestamp"})
    got = await (await client.get(URL)).json()
    assert [a["entity_id"] for a in got["targets"]["alarms"]] == [PHONE_ALARM] and got["targets"]["alarms"][0]["next"] == "unavailable"
    # No Android sensor at all: the same recipes fall back to the iPhone event, a new code per rule.
    reg.async_remove(PHONE_ALARM)
    got = await (await client.get(URL)).json()
    iphone = next(r for r in got["recipes"] if r["key"] == "wake_light")
    assert iphone["rule"]["when"] == {"event": "alarm_iphone"}


async def test_state_rule_motion_door_media(hass: HomeAssistant, hass_client, entry, bus, freezer) -> None:
    """p6d «Движение / дверь»: HA's state trigger held N minutes; «нет движения» over several sensors = none of them
    for N minutes (the condition), checked live with HA's own clock."""
    from datetime import timedelta  # noqa: PLC0415

    from homeassistant.util import dt as dt_util  # noqa: PLC0415
    from pytest_homeassistant_custom_component.common import async_fire_time_changed  # noqa: PLC0415

    _binaries(hass, entry)
    client = await hass_client()
    got = (await (await client.get(URL)).json())["targets"]
    assert {(b["entity_id"], b["kind"]) for b in got["binaries"]} == {(M1, "motion"), (M2, "motion"), (DOOR, "door"), (TV, "media"), (TV2, "media")}
    assert next(b for b in got["binaries"] if b["entity_id"] == M1)["area"] == entry.area

    lights_on = {"do": "set", "domain": "light", "cmd": "on", "rooms": [entry.area]}
    on = {"when": {"event": "state", "kind": "motion", "sensors": [M1], "to": "on", "for_min": 0}, "actions": [lights_on]}
    r = await client.post(URL, json={"op": "save", "rule": on})
    assert r.status == 200, await r.text()
    mine = [a for a in _file(hass) if a.get("id") == (await r.json())["id"]][0]
    assert mine["triggers"] == [{"trigger": "state", "entity_id": [M1], "from": "off", "to": "on"}] and mine["conditions"] == []
    # Live: motion → the room's lights; a sensor back from «unavailable» already on is not motion.
    hass.states.async_set(M1, "unavailable", {"device_class": "motion"})
    await hass.async_block_till_done()
    hass.states.async_set(M1, "on", {"device_class": "motion"})
    await hass.async_block_till_done()
    assert not [m for m in bus if m[0] == "light.turn_on"]
    hass.states.async_set(M1, "off", {"device_class": "motion"})
    await hass.async_block_till_done()
    hass.states.async_set(M1, "on", {"device_class": "motion"})
    await hass.async_block_till_done()
    assert len([m for m in bus if m[0] == "light.turn_on"]) == 1

    off = {"when": {"event": "state", "sensors": [M2, M1], "to": "off", "for_min": 5}, "actions": [{"do": "room_off", "area_id": entry.area}]}
    r = await client.post(URL, json={"op": "save", "rule": off})
    assert r.status == 200, await r.text()
    rule = await r.json()
    assert rule["when"] == {"event": "state", "kind": "motion", "sensors": [M1, M2], "to": "off", "for_min": 5}
    mine = [a for a in _file(hass) if a.get("id") == rule["id"]][0]
    assert mine["triggers"] == [{"trigger": "state", "entity_id": [M1, M2], "from": "on", "to": "off", "for": {"minutes": 5}}]
    assert mine["conditions"] == [{"condition": "state", "entity_id": [M1, M2], "state": "off", "for": {"seconds": 299}}]
    # Live: M1 quiet since 0, M2 since 4 min — nothing at 5 min (M2 quiet only 1 min), lights off at 9 min.
    hass.states.async_set(M2, "on", {"device_class": "occupancy"})
    await hass.async_block_till_done()
    hass.states.async_set(M1, "off", {"device_class": "motion"})
    await hass.async_block_till_done()
    async def tick(minutes: int) -> None:
        freezer.tick(timedelta(minutes=minutes))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

    await tick(4)
    hass.states.async_set(M2, "off", {"device_class": "occupancy"})
    await hass.async_block_till_done()
    await tick(1)
    assert [m for m in bus if m[0] == "light.turn_off"] == []  # 5 min: M2 has been quiet for 1
    await tick(4)
    assert len([m for m in bus if m[0] == "light.turn_off"]) == 1  # 9 min: nowhere for 5

    # A door known only by its registry class; a TV playing / not playing.
    r = await client.post(URL, json={"op": "save", "rule": {"when": {"event": "state", "sensors": [DOOR], "to": "on"}, "actions": [lights_on], "only_if": [{"if": "dark"}]}})
    assert r.status == 200, await r.text()
    assert (await r.json())["when"]["kind"] == "door"
    r = await client.post(URL, json={"op": "save", "rule": {"when": {"event": "state", "sensors": [TV, TV2], "to": "on", "for_min": 1}, "actions": [lights_on]}})
    mine = [a for a in _file(hass) if a.get("id") == (await r.json())["id"]][0]
    assert mine["triggers"] == [{"trigger": "state", "entity_id": [TV2, TV], "to": "playing", "for": {"minutes": 1}}] and mine["conditions"] == []
    r = await client.post(URL, json={"op": "save", "rule": {"when": {"event": "state", "sensors": [TV], "to": "off", "for_min": 2}, "actions": [lights_on]}})
    mine = [a for a in _file(hass) if a.get("id") == (await r.json())["id"]][0]
    assert mine["triggers"] == [{"trigger": "state", "entity_id": [TV], "from": "playing", "for": {"minutes": 2}}] and mine["conditions"] == []

    good = {"when": {"event": "state", "sensors": [M1], "to": "on"}, "actions": [lights_on]}
    for bad in (
        {"event": "state", "sensors": [M1, DOOR], "to": "on"},  # a mix of kinds
        {"event": "state", "sensors": [TV, TV2], "to": "off"},  # «not playing» — one player
        {"event": "state", "sensors": [M1], "to": "open"},
        {"event": "state", "sensors": [M1]},
        {"event": "state", "sensors": [], "to": "on"},
        {"event": "state", "sensors": [M1, M1], "to": "on"},
        {"event": "state", "sensors": [M1], "to": "on", "for_min": 241},
        {"event": "state", "sensors": [M1], "to": "on", "for_min": -1},
        {"event": "state", "sensors": [M1], "to": "on", "for_min": 1.5},
        {"event": "state", "kind": "door", "sensors": [M1], "to": "on"},  # the sensors decide the kind
        {"event": "state", "sensors": ["binary_sensor.hidden_motion"], "to": "on"},
        {"event": "state", "sensors": ["binary_sensor.diag_door"], "to": "on"},
        {"event": "state", "sensors": ["binary_sensor.link"], "to": "on"},
        {"event": "state", "sensors": ["binary_sensor.nowhere"], "to": "on"},
        {"event": "state", "sensors": [T1], "to": "on"},
        {"event": "state", "sensors": [LIGHT], "to": "on"},
        {"event": "state", "sensors": [f"binary_sensor.m{i}" for i in range(9)], "to": "on"},
    ):
        res = await client.post(URL, json={"op": "save", "rule": {**good, "when": bad}})
        assert res.status == 400, bad
