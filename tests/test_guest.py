"""«Гостевой доступ» (guest.py, Н2, docs/guest-access.md): a guest controls only the safe Hauflo lights / blinds /
climate of the chosen rooms, for a time, revoked at once; the token, the room, the action and the value are checked in
the agent on every request — never the browser, never the cloud. Mostly negative tests: they try to break it."""

import datetime as dt
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockConfigEntry, flush_store

from custom_components.hauflo import _http_allowed, _ws_allowed
from custom_components.hauflo import guest as G
from custom_components.hauflo import qr
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

URL = "/api/hauflo/guest"
LAMP, DIM, BLIND, AC = "light.living_lamp", "light.living_dim", "cover.living_blind", "climate.living"
GARAGE, LOCK, ALARM, HAND, GROUP, HIDDEN = "cover.garage_door", "lock.front_door", "alarm_control_panel.house", "light.integrator_hand", "light.living_group", "light.service_hidden"
BED = "light.bedroom"
DEVICE = "d" * 20


@pytest.fixture
async def entry(hass: HomeAssistant, tmp_path: Path):
    hass.config.config_dir = str(tmp_path)

    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    reg = er.async_get(hass)
    areas = ar.async_get(hass)
    living, bedroom = areas.async_create("Гостиная"), areas.async_create("Спальня")
    for eid, uid, platform, extra in (
        (LAMP, "u-lamp", "knx", {"supported_color_modes": ["onoff"]}),
        (DIM, "u-dim", "knx", {"supported_color_modes": ["brightness"]}),
        (BLIND, "u-blind", "knx", {"supported_features": 15, "current_position": 100}),
        (AC, "u-ac", "knx", {"supported_features": 1, "temperature": 22, "current_temperature": 23.5, "min_temp": 5, "max_temp": 35}),
        (GARAGE, "u-garage", "knx", {"device_class": "garage", "supported_features": 3}),
        (LOCK, "u-lock", "knx", {}),
        (ALARM, "u-alarm", "knx", {}),
        (HAND, "u-hand", "knx", {"supported_color_modes": ["onoff"]}),
        (GROUP, "u-group", "group", {"supported_color_modes": ["onoff"], "entity_id": [LAMP]}),
        (HIDDEN, "u-hidden", "knx", {"supported_color_modes": ["onoff"]}),
        (BED, "u-bed", "knx", {"supported_color_modes": ["onoff"]}),
    ):
        domain, obj = eid.split(".")
        reg.async_get_or_create(domain, platform, uid, suggested_object_id=obj)
        state = {"light": "off", "cover": "open", "climate": "heat", "lock": "locked"}.get(domain, "disarmed")
        hass.states.async_set(eid, state, {"friendly_name": obj.replace("_", " ").capitalize(), **extra})
        reg.async_update_entity(eid, area_id=bedroom.id if eid == BED else living.id)
    reg.async_update_entity(HIDDEN, hidden_by=er.RegistryEntryHider.INTEGRATION)
    entry.runtime_data.owned |= {"u-lamp", "u-dim", "u-blind", "u-ac", "u-garage", "u-lock", "u-alarm", "u-hidden", "u-bed"}  # «u-hand»: the integrator's
    entry.living, entry.bedroom = living.id, bedroom.id
    return entry


@pytest.fixture
def calls(hass: HomeAssistant):
    made: list[tuple[str, dict]] = []

    def record(domain: str, service: str):
        async def handler(call: ServiceCall) -> None:
            made.append((f"{domain}.{service}", dict(call.data)))

        hass.services.async_register(domain, service, handler)

    for d, s in (
        ("light", "turn_on"), ("light", "turn_off"), ("cover", "open_cover"), ("cover", "close_cover"), ("cover", "stop_cover"),
        ("cover", "set_cover_position"), ("climate", "set_temperature"), ("climate", "set_hvac_mode"), ("lock", "unlock"),
        ("alarm_control_panel", "alarm_disarm"), ("homeassistant", "restart"), ("script", "turn_on"),
    ):
        record(d, s)
    return made


@pytest.fixture
async def person(hass: HomeAssistant, hass_owner_user):
    """A family member: an ordinary HA user (not an admin — the client usually isn't). The owner exists first: HA makes
    the first person the owner (all rights)."""
    user = await hass.auth.async_create_user("Анна", group_ids=["system-users"])
    assert not user.is_admin
    return user


async def _client(hass, hass_client, user):
    refresh = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    return await hass_client(hass.auth.async_create_access_token(refresh))


async def _issue(engine: G.GuestAccess, user, rooms, hours=24, **kw) -> tuple[str, dict]:
    res = await engine.issue(user, {"label": "Для Ивана", "rooms": rooms, "hours": hours, **kw})
    return res["link"].split("#", 1)[1], res


def _req(token: str, op: str = "state", device: str = DEVICE, **kw) -> dict:
    return {"token": token, "device": device, "op": op, **kw}


async def test_route_and_link(hass: HomeAssistant, entry, person) -> None:
    # The cloud derives the same route from the hash of the agent's secret (backend guest.test.ts has this vector).
    assert G.route_of("s") == "eaff35fc4b6db16abca37d58"
    engine = entry.runtime_data.guest
    token, res = await _issue(engine, person, [entry.living])
    assert res["link"].startswith("https://app.hauflo.ru/g/eaff35fc4b6db16abca37d58#")
    assert G._TOKEN.fullmatch(token)
    # Only the hash is kept: a copy of HA's storage holds no working link.
    stored = json.dumps(engine._data())
    assert token.split(".")[1] not in stored and token not in stored
    # The QR is the link (version ≥ 1 square, finder patterns in three corners).
    rows = res["qr"]
    assert len(rows) == len(rows[0]) and (len(rows) - 17) % 4 == 0
    assert rows[0][:7] == "1111111" and rows[0][-7:] == "1111111" and rows[-1][:7] == "1111111"
    assert rows == ["".join("1" if v else "0" for v in r) for r in qr.encode(res["link"])]


async def test_snapshot_only_safe_hauflo_entities(hass: HomeAssistant, entry, person) -> None:
    engine = entry.runtime_data.guest
    assert engine.candidates(entry.living) == sorted([AC, BLIND, DIM, LAMP])  # no garage, lock, alarm, integrator's, group, hidden
    token, res = await _issue(engine, person, [entry.living])
    assert res["count"] == 4
    status, view = await engine.handle(_req(token))
    assert status == 200
    ids = {i["id"] for r in view["rooms"] for i in r["items"]}
    assert ids == {AC, BLIND, DIM, LAMP}
    assert set(view) == {"label", "expires", "rooms"}  # no house, no people, no log
    ac = next(i for r in view["rooms"] for i in r["items"] if i["id"] == AC)
    assert (ac["min"], ac["max"], ac["target"]) == (16.0, 28.0, 22)  # the entity's 5–35 narrowed to the guest's band


async def test_guest_actions_go_through(hass: HomeAssistant, entry, person, calls) -> None:
    engine = entry.runtime_data.guest
    token, _ = await _issue(engine, person, [entry.living])
    assert (await engine.handle(_req(token, "act", entity_id=DIM, action="on", value=40)))[0] == 200
    assert (await engine.handle(_req(token, "act", entity_id=LAMP, action="off")))[0] == 200
    assert (await engine.handle(_req(token, "act", entity_id=BLIND, action="position", value=30)))[0] == 200
    assert (await engine.handle(_req(token, "act", entity_id=BLIND, action="stop")))[0] == 200
    assert (await engine.handle(_req(token, "act", entity_id=AC, action="temperature", value=21.3)))[0] == 200
    assert calls == [
        ("light.turn_on", {"entity_id": DIM, "brightness_pct": 40}),
        ("light.turn_off", {"entity_id": LAMP}),
        ("cover.set_cover_position", {"entity_id": BLIND, "position": 30}),
        ("cover.stop_cover", {"entity_id": BLIND}),
        ("climate.set_temperature", {"entity_id": AC, "temperature": 21.5}),
    ]
    # The family sees it: who (the link), what, when.
    acts = [x for x in engine.log if x.get("do")]
    assert [x["do"] for x in acts] == ["temperature", "stop", "position", "off", "on"] and all(x["ok"] and x["label"] == "Для Ивана" for x in acts)
    assert any(x.get("sys") == "opened" for x in engine.log)


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"entity_id": LOCK, "action": "on"}, 403),  # a lock in the same room
        ({"entity_id": ALARM, "action": "off"}, 403),  # the alarm panel
        ({"entity_id": GARAGE, "action": "open"}, 403),  # a garage door cover in the same room
        ({"entity_id": HAND, "action": "on"}, 403),  # the integrator's own light in the room
        ({"entity_id": GROUP, "action": "on"}, 403),  # a group
        ({"entity_id": HIDDEN, "action": "on"}, 403),  # hidden
        ({"entity_id": BED, "action": "on"}, 403),  # another room
        ({"entity_id": "light.nope", "action": "on"}, 403),  # doesn't exist
        ({"entity_id": [LAMP, LOCK], "action": "on"}, 403),  # a list smuggled in
        ({"entity_id": "all", "action": "off"}, 403),
        ({"entity_id": LAMP, "action": "open"}, 403),  # an action of another domain
        ({"entity_id": BLIND, "action": "on"}, 403),
        ({"entity_id": LAMP, "action": "unlock"}, 400),  # not in the table
        ({"entity_id": LAMP, "action": "turn_on"}, 400),  # a service name instead of an action
        ({"entity_id": AC, "action": "set_hvac_mode"}, 400),
        ({"entity_id": LAMP, "action": "on", "value": 50}, 400),  # an on / off light can't dim
        ({"entity_id": DIM, "action": "on", "value": 0}, 400),
        ({"entity_id": DIM, "action": "on", "value": 101}, 400),
        ({"entity_id": DIM, "action": "on", "value": True}, 400),
        ({"entity_id": DIM, "action": "on", "value": "50"}, 400),
        ({"entity_id": DIM, "action": "on", "value": {"brightness": 255, "entity_id": LOCK}}, 400),
        ({"entity_id": LAMP, "action": "off", "value": 1}, 400),
        ({"entity_id": BLIND, "action": "position", "value": 150}, 400),
        ({"entity_id": BLIND, "action": "open", "value": 50}, 400),
        ({"entity_id": AC, "action": "temperature", "value": 35}, 400),  # outside the guest's band
        ({"entity_id": AC, "action": "temperature", "value": 10}, 400),
        ({"entity_id": AC, "action": "temperature", "value": float("nan")}, 400),
        ({"entity_id": AC, "action": "temperature"}, 400),
    ],
)
async def test_escalation_refused(hass: HomeAssistant, entry, person, calls, body, code) -> None:
    engine = entry.runtime_data.guest
    token, _ = await _issue(engine, person, [entry.living])
    status, _ = await engine.handle(_req(token, "act", **body))
    assert status == code
    assert calls == []


async def test_injection_of_fields_and_forms(hass: HomeAssistant, entry, person, calls) -> None:
    engine = entry.runtime_data.guest
    token, _ = await _issue(engine, person, [entry.living])
    for bad in (
        {**_req(token, "act", entity_id=LAMP, action="on"), "domain": "lock", "service": "unlock"},
        {**_req(token, "act", entity_id=LAMP, action="on"), "service_data": {"entity_id": LOCK}},
        _req(token, "call_service", entity_id=LAMP),
        _req(token, device="short"),
        _req(token, device=None),
        "not a dict",
        None,
        [_req(token)],
    ):
        status, _ = await engine.handle(bad)
        assert status == 400, bad
    assert calls == []


async def test_bad_expired_revoked_tokens(hass: HomeAssistant, entry, person, calls, freezer) -> None:
    engine = entry.runtime_data.guest
    token, res = await _issue(engine, person, [entry.living], hours=3)
    gid, secret = token.split(".")
    for bad in (f"{gid}.{'A' * 32}", f"{'B' * 12}.{secret}", token[:-1], token + "x", "", None, 42, f"{gid}.{secret}.{secret}"):
        assert (await engine.handle(_req(bad, "act", entity_id=LAMP, action="on")))[0] == 404
    # Expired: refused at once, by the clock of the agent.
    freezer.tick(dt.timedelta(hours=3, seconds=1))
    assert await engine.handle(_req(token, "act", entity_id=LAMP, action="on")) == (410, {"code": "expired"})
    # Revoked: the next request is refused (latency 0).
    freezer.tick(dt.timedelta(seconds=-2))
    t2, r2 = await _issue(engine, person, [entry.living])
    assert (await engine.handle(_req(t2)))[0] == 200
    assert await engine.revoke(person, r2["id"]) == 1
    assert await engine.handle(_req(t2, "act", entity_id=LAMP, action="on")) == (410, {"code": "revoked"})
    assert calls == []


async def test_brute_force_and_rate_limits(hass: HomeAssistant, entry, person, calls) -> None:
    engine = entry.runtime_data.guest
    token, _ = await _issue(engine, person, [entry.living])
    gid = token.split(".")[0]
    for i in range(G.RATE_BAD):
        assert (await engine.handle(_req(f"{gid}.{str(i).zfill(32)}")))[0] == 404
    # Too many wrong tokens: every wrong one waits now — the real link still works.
    assert await engine.handle(_req(f"{gid}.{'Z' * 32}")) == (429, {"code": "slow_down"})
    assert (await engine.handle(_req(token)))[0] == 200
    # Per link: at most RATE_ACT actions a minute.
    codes = [(await engine.handle(_req(token, "act", entity_id=LAMP, action="off")))[0] for _ in range(G.RATE_ACT + 3)]
    assert codes.count(200) == G.RATE_ACT and codes[-1] == 429
    assert len(calls) == G.RATE_ACT


async def test_device_limit(hass: HomeAssistant, entry, person) -> None:
    engine = entry.runtime_data.guest
    token, _ = await _issue(engine, person, [entry.living], max_devices=2)
    assert (await engine.handle(_req(token, device="a" * 20)))[0] == 200
    assert (await engine.handle(_req(token, device="b" * 20)))[0] == 200
    assert (await engine.handle(_req(token, device="a" * 20)))[0] == 200  # a known one again
    for _ in range(5):
        assert await engine.handle(_req(token, device="c" * 20)) == (403, {"code": "device_limit"})
    assert [x["sys"] for x in engine.log if x.get("sys") == "device_refused"] == ["device_refused"]  # once, not a flood


async def test_rechecked_on_every_request(hass: HomeAssistant, entry, person, calls) -> None:
    """The snapshot isn't trusted either: moved to another room, turned into a garage door, the issuer gone."""
    engine = entry.runtime_data.guest
    token, _ = await _issue(engine, person, [entry.living])
    reg = er.async_get(hass)
    reg.async_update_entity(LAMP, area_id=entry.bedroom)
    assert (await engine.handle(_req(token, "act", entity_id=LAMP, action="on")))[0] == 403
    hass.states.async_set(BLIND, "open", {"device_class": "gate", "supported_features": 15})
    assert (await engine.handle(_req(token, "act", entity_id=BLIND, action="open")))[0] == 403
    status, view = await engine.handle(_req(token))
    assert {i["id"] for r in view["rooms"] for i in r["items"]} == {AC, DIM}
    # The issuer can't control the house any more (made read-only) → the link is dead.
    await hass.auth.async_update_user(person, group_ids=["system-read-only"])
    assert await engine.handle(_req(token)) == (410, {"code": "revoked"})
    await hass.auth.async_update_user(person, group_ids=["system-users"])
    assert (await engine.handle(_req(token)))[0] == 200
    await hass.auth.async_remove_user(person)
    assert await engine.handle(_req(token)) == (410, {"code": "revoked"})
    assert calls == []


async def test_who_issues_and_revokes(hass: HomeAssistant, hass_client, entry, person, hass_read_only_user, hass_admin_user, calls) -> None:
    family = await _client(hass, hass_client, person)
    ro = await _client(hass, hass_client, hass_read_only_user)
    admin = await _client(hass, hass_client, hass_admin_user)
    # A read-only person can't hand control on; the list and the log are open to the family.
    r = await ro.post(URL, json={"op": "issue", "label": "x", "rooms": [entry.living], "hours": 2})
    assert r.status == 403
    r = await ro.get(URL, params={"rooms": "1"})
    assert r.status == 200 and (await r.json())["can_issue"] is False and (await r.json())["rooms"] == []
    # An ordinary person (the client) and an admin may.
    r = await family.get(URL, params={"rooms": "1"})
    body = await r.json()
    assert body["can_issue"] is True and [x["name"] for x in body["rooms"]] == ["Гостиная", "Спальня"]
    r = await family.post(URL, json={"op": "issue", "label": "Гости‮", "rooms": [entry.living], "hours": 48})
    assert r.status == 200
    one = await r.json()
    assert one["link"].startswith("https://app.hauflo.ru/g/") and one["count"] == 4
    r = await admin.post(URL, json={"op": "issue", "label": "Няня", "rooms": [entry.bedroom], "hours": 5})
    assert r.status == 200
    # Wrong forms.
    for bad in (
        {"op": "issue", "rooms": [entry.living], "hours": 0},
        {"op": "issue", "rooms": [entry.living], "hours": G.MAX_HOURS + 1},
        {"op": "issue", "rooms": [entry.living], "hours": True},
        {"op": "issue", "rooms": [], "hours": 2},
        {"op": "issue", "rooms": ["nope"], "hours": 2},
        {"op": "issue", "rooms": [entry.living], "hours": 2, "entities": [LOCK]},
        {"op": "issue", "rooms": [entry.living], "hours": 2, "max_devices": 99},
        {"op": "grant", "rooms": [entry.living], "hours": 2},
    ):
        assert (await family.post(URL, json=bad)).status == 400, bad
    # The list: no secrets, label cleaned of bidi controls.
    r = await ro.get(URL)
    listed = await r.json()
    assert [g["label"] for g in listed["grants"]] == ["Няня", "Гости"]
    assert all("hash" not in g and "link" not in g for g in listed["grants"])
    # Any person may close a link — the read-only one too (the safe direction); «Закрыть всё».
    r = await ro.post(URL, json={"op": "revoke", "id": one["id"]})
    assert r.status == 200 and (await r.json())["revoked"] == 1
    r = await family.post(URL, json={"op": "revoke_all"})
    assert (await r.json())["revoked"] == 1
    assert {g["status"] for g in (await (await ro.get(URL)).json())["grants"]} == {"revoked"}
    assert [x["sys"] for x in (await (await ro.get(URL)).json())["log"] if x.get("sys")][:2] == ["revoked_all", "revoked"]


async def test_nothing_to_give(hass: HomeAssistant, entry, person) -> None:
    engine = entry.runtime_data.guest
    room = ar.async_get(hass).async_create("Гараж")
    er.async_get(hass).async_update_entity(GARAGE, area_id=room.id)
    with pytest.raises(G.Invalid):
        await engine.issue(person, {"rooms": [room.id], "hours": 2})


async def test_cloud_can_not_issue_or_read(hass: HomeAssistant, entry) -> None:
    """The tunnel's allowlists don't reach the guest API (the cloud never mints a link or reads the log), nor a
    lock through it."""
    for method in ("GET", "POST", "DELETE"):
        assert not _http_allowed(hass, method, URL, json.dumps({"op": "issue", "rooms": [entry.living], "hours": 2}))
        assert not _http_allowed(hass, method, URL + "?rooms=1", None)
    assert not _ws_allowed({"type": "call_service", "domain": "light", "service": "turn_on", "service_data": {"entity_id": "light.living_lamp"}}, hass)


async def test_frame_round_trip_and_survives_restart(hass: HomeAssistant, entry, person, calls) -> None:
    agent = entry.runtime_data
    token, _ = await _issue(agent.guest, person, [entry.living])
    sent: list[dict] = []
    with patch.object(agent, "send", AsyncMock(side_effect=lambda f: sent.append(f))):
        await agent._on_frame({"t": "guest", "id": 7, "body": _req(token, "act", entity_id=LAMP, action="on")})
        await agent._on_frame({"t": "guest", "id": 8, "body": _req(token, "act", entity_id=LOCK, action="on")})
        await agent._on_frame({"t": "guest", "id": 9, "body": "junk"})
        await hass.async_block_till_done()
    by_id = {f["id"]: f for f in sent}
    assert by_id[7]["t"] == "guest_res" and by_id[7]["status"] == 200 and json.loads(by_id[7]["body"])["ok"] is True
    assert by_id[8]["status"] == 403 and by_id[9]["status"] == 400
    assert calls == [("light.turn_on", {"entity_id": LAMP})]
    # A fresh engine reads the same store (HA restarted): the link still works, the log is there.
    await flush_store(agent.guest.store)  # the log's delayed save (written on HA's stop too)
    fresh = G.GuestAccess(hass, agent.guest.owns, agent.guest.link_base)
    await fresh.async_load()
    assert (await fresh.handle(_req(token)))[0] == 200
    assert any(x.get("do") == "on" for x in fresh.log), json.dumps([fresh.log, agent.guest.log], ensure_ascii=False)


async def test_ended_links_and_log_are_pruned(hass: HomeAssistant, entry, person, freezer) -> None:
    engine = entry.runtime_data.guest
    await _issue(engine, person, [entry.living], hours=1)
    freezer.tick(G.KEEP_ENDED + dt.timedelta(hours=2))
    assert engine.answer(person)["grants"] == []
    freezer.tick(dt.timedelta(days=G.LOG_DAYS))
    assert engine.answer(person)["log"] == []
    assert dt_util.utcnow()  # the clock moved, nothing else did


async def test_active_cap(hass: HomeAssistant, entry, person) -> None:
    engine = entry.runtime_data.guest
    for _ in range(G.MAX_ACTIVE):
        await _issue(engine, person, [entry.living])
    with pytest.raises(G.Invalid):
        await _issue(engine, person, [entry.living])
    await engine.revoke(person, None)
    await _issue(engine, person, [entry.living])
