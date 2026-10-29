"""The house's own settings (house.py, family review rounds 2–3, 29.10): «Кто обслуживает дом» (service.py's) read by
everyone; «Кто может менять настройки дома» (Google Home: adults equal, an admin marks a restricted child profile)
enforced by the views themselves; each person's favourites."""

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import _http_allowed
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN
from custom_components.hauflo import service
from custom_components.hauflo.house import clean_favorites


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    yield entry


async def test_service_contact_read_by_the_family(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry) -> None:
    """«Кто обслуживает дом» is service.py's (the cloud's «service» frame) — read by every person for «Позвонить»."""
    family = await hass_client(hass_read_only_access_token)
    assert (await (await family.get("/api/hauflo/house")).json())["service"] is None
    await service.async_save(hass, {"company": "Умный дом Плюс", "contact": {"phone": "+7 912 345-67-89", "email": "help@smart.example", "name": "Андрей"}, "history": []})
    got = (await (await family.get("/api/hauflo/house")).json())["service"]
    assert got == {"company": "Умный дом Плюс", "name": "Андрей", "phone": "+7 912 345-67-89", "email": "help@smart.example"}
    # A company with no line to call: nothing to call (the card then says to tell the integrator, no dead button).
    await service.async_save(hass, {"company": "X", "contact": None, "history": []})
    assert (await (await family.get("/api/hauflo/house")).json())["service"] is None


async def test_tunnel_never_reaches_the_house_settings(hass: HomeAssistant) -> None:
    assert not _http_allowed(hass, "POST", "/api/hauflo/house", {"editors": []})
    assert not _http_allowed(hass, "GET", "/api/hauflo/house", "")
    assert not _http_allowed(hass, "POST", "/api/hauflo/service", {"service": {}})


# Every house-wide change a restricted (child) profile may not make — refused on the server, not only greyed out.
GUARDED = (
    ("/api/hauflo/season", {"mode": "heat"}),
    ("/api/hauflo/titles", {"titles": {"climate": "Мой климат"}}),
    ("/api/hauflo/order", {"clear": True}),
    ("/api/hauflo/presence", {"op": "set", "enabled": True}),
    ("/api/hauflo/guest", {"op": "issue", "label": "x", "rooms": ["a"], "hours": 2}),
    ("/api/hauflo/automations", {"op": "save", "rule": {"kind": "time", "at": "01:00", "do": "off", "targets": ["light.x"]}}),
    ("/api/hauflo/automations", {"op": "enable", "id": "x", "enabled": False}),
    ("/api/hauflo/automations", {"op": "delete", "id": "x"}),
    ("/api/hauflo/scenes", {"op": "add", "room": "kitchen", "name": "Тим", "entities": ["light.x"]}),
    ("/api/hauflo/scenes", {"op": "rename", "entity_id": "scene.evening", "name": "ТИМ ТУТ"}),
    ("/api/hauflo/scenes", {"op": "recapture", "entity_id": "scene.evening"}),
    ("/api/hauflo/scenes", {"op": "delete", "entity_id": "scene.evening"}),
    ("/api/hauflo/scenes", {"op": "unhide", "entity_id": "scene.evening"}),
    ("/api/hauflo/scenes", {"op": "knx_name", "entity_id": "scene.night", "name": "ТИМ"}),
    ("/api/hauflo/scenes", {"op": "knx_learn", "entity_id": "scene.night"}),
)


async def test_adults_are_equal_kids_are_limited(hass: HomeAssistant, hass_client, hass_admin_user, hass_read_only_user, hass_read_only_access_token, entry) -> None:
    """Google Home: every person may change the house's settings by default; an admin marks a child profile, whose
    every guarded action is a 403 from the view itself (family review round 3: Tim saved a rule, renamed a scene)."""
    admin = await hass_client()
    kid = await hass_client(hass_read_only_access_token)
    me = await (await kid.get("/api/hauflo/house")).json()
    assert me["can_edit"] is True and me["admin"] is False and "users" not in me and "restricted" not in me
    assert (await kid.post("/api/hauflo/titles", json={"titles": {"climate": "Мой климат"}})).status == 200
    # Only an admin marks a restricted profile.
    assert (await kid.post("/api/hauflo/house", json={"restricted": [hass_read_only_user.id]})).status == 403
    seen = await (await admin.get("/api/hauflo/house")).json()
    assert seen["can_edit"] is True and seen["admin"] is True and seen["restricted"] == []
    assert hass_read_only_user.id in {u["id"] for u in seen["users"]}
    assert (await admin.post("/api/hauflo/house", json={"restricted": "all"})).status == 400
    assert (await admin.post("/api/hauflo/house", json={"editors": []})).status == 400
    # An admin is never restricted, unknown ids are dropped.
    got = await (await admin.post("/api/hauflo/house", json={"restricted": [hass_read_only_user.id, hass_admin_user.id, "0" * 32]})).json()
    assert got["restricted"] == [hass_read_only_user.id]
    assert (await (await kid.get("/api/hauflo/house")).json())["can_edit"] is False
    hass.states.async_set("climate.ac", "cool", {"hvac_modes": ["heat", "cool", "off"]})
    for path, body in GUARDED:
        res = await kid.post(path, json=body)
        assert res.status == 403 and (await res.json())["code"] == "not_allowed", (path, body["op"] if "op" in body else body)
    assert hass.states.get("climate.ac").state == "cool"
    # What stays open to a child: running an automation now, the alarm's «Принято», asking for a guest link,
    # their own favourites, closing a guest link (the safe direction).
    assert (await kid.post("/api/hauflo/automations", json={"op": "run", "id": "x"})).status != 403
    assert (await kid.post("/api/hauflo/alarms", json={"ack": "rule|binary_sensor.leak"})).status in (200, 404)
    assert (await kid.post("/api/hauflo/house", json={"favorites": {"items": ["light.kitchen"]}})).status == 200
    assert (await kid.post("/api/hauflo/guest", json={"op": "revoke_all"})).status == 200
    guest = await (await kid.get("/api/hauflo/guest")).json()
    assert guest["can_issue"] is False and guest["can_ask"] is True
    # Taken back: the same person is an adult of the house again.
    await admin.post("/api/hauflo/house", json={"restricted": []})
    assert (await kid.post("/api/hauflo/titles", json={"titles": {}})).status == 200


async def test_a_child_asks_an_admin_for_a_guest_link(hass: HomeAssistant, hass_client, hass_admin_user, hass_read_only_user, hass_read_only_access_token, entry) -> None:
    """«Попросить администратора» (family review round 3): a notification in HA + a push to the admins' phones only."""
    from homeassistant.components import persistent_notification

    calls = []

    async def push(call):
        calls.append((call.service, call.data))

    hass.services.async_register("notify", "mobile_app_dad", push)
    hass.services.async_register("notify", "mobile_app_kid", push)
    hass.states.async_set("person.dad", "home", {"user_id": hass_admin_user.id})
    hass.states.async_set("person.kid", "home", {"user_id": hass_read_only_user.id})
    hass.config.language = "ru"
    phones = [{"service": "mobile_app_dad", "person": "person.dad", "os": "ios"}, {"service": "mobile_app_kid", "person": "person.kid", "os": "android"}]
    with patch("custom_components.hauflo.notify.NotifyEngine.phones", return_value=phones):
        await (await hass_client()).post("/api/hauflo/house", json={"restricted": [hass_read_only_user.id]})
        kid = await hass_client(hass_read_only_access_token)
        assert (await kid.post("/api/hauflo/guest", json={"op": "ask", "note": "на вечер", "x": 1})).status == 400
        assert (await kid.post("/api/hauflo/guest", json={"op": "ask", "note": "x" * 61})).status == 400
        res = await kid.post("/api/hauflo/guest", json={"op": "ask", "note": "на вечер"})
        assert res.status == 200, await res.text()
        await hass.async_block_till_done()
        # Again at once: not a flood of pushes.
        assert (await kid.post("/api/hauflo/guest", json={"op": "ask"})).status == 429
    got = await res.json()
    assert got["asked"] is True
    notes = persistent_notification._async_get_or_create_notifications(hass)
    assert any("просит гостевую ссылку на вечер" in n["message"] for n in notes.values()), notes
    assert got["phones"] == 1 and [c[0] for c in calls] == ["mobile_app_dad"] and "просит гостевую ссылку на вечер" in calls[0][1]["message"]


async def _setup(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _old(editors, grandfathered=None):
    data = {"init": 1.0, "editors": editors}
    if grandfathered is not None:
        data["grandfathered"] = grandfathered
    return {"version": 1, "minor_version": 1, "key": "hauflo.house", "data": data}


async def test_round2_allow_list_becomes_nobody_restricted(hass: HomeAssistant, hass_storage, hass_client, hass_read_only_access_token) -> None:
    """Grandfather rule: round 2's allow-list (a fresh install, only Мама ticked) → nobody restricted; its Repairs note
    goes."""
    mama = await hass.auth.async_create_user("Мама")
    hass_storage["hauflo.house"] = _old([mama.id])
    ir.async_create_issue(hass, DOMAIN, "house_editors", is_fixable=False, is_persistent=False, severity=ir.IssueSeverity.WARNING, translation_key="house_editors")
    await _setup(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "house_editors") is None
    kid = await hass_client(hass_read_only_access_token)
    assert (await (await kid.get("/api/hauflo/house")).json())["can_edit"] is True
    assert (await (await (await hass_client()).get("/api/hauflo/house")).json())["restricted"] == []


async def test_round2_explicit_untick_stays_restricted(hass: HomeAssistant, hass_storage, hass_client, hass_read_only_user, hass_read_only_access_token) -> None:
    """A lived-in house where everyone was allowed and the admin then saved the list without the child (the Repairs note
    was cleared by that save): the child stays restricted, Мама doesn't."""
    mama = await hass.auth.async_create_user("Мама")
    hass_storage["hauflo.house"] = _old([mama.id], grandfathered=2)
    await _setup(hass)
    kid = await hass_client(hass_read_only_access_token)
    assert (await (await kid.get("/api/hauflo/house")).json())["can_edit"] is False
    assert (await (await (await hass_client()).get("/api/hauflo/house")).json())["restricted"] == [hass_read_only_user.id]
    # Done once: a restart keeps the admin's choice as it is.
    from custom_components.hauflo.house import async_init

    await async_init(hass)
    assert (await (await (await hass_client()).get("/api/hauflo/house")).json())["restricted"] == [hass_read_only_user.id]


async def test_round2_grandfathered_not_yet_checked_is_nobody_restricted(hass: HomeAssistant, hass_storage, hass_client, hass_read_only_access_token) -> None:
    """The admin never looked (the Repairs note still open): nobody was unticked on purpose → nobody restricted."""
    hass_storage["hauflo.house"] = _old([], grandfathered=2)
    ir.async_create_issue(hass, DOMAIN, "house_editors", is_fixable=False, is_persistent=False, severity=ir.IssueSeverity.WARNING, translation_key="house_editors")
    await _setup(hass)
    kid = await hass_client(hass_read_only_access_token)
    assert (await (await kid.get("/api/hauflo/house")).json())["can_edit"] is True


async def test_a_fresh_install_restricts_nobody(hass: HomeAssistant, hass_client, hass_read_only_access_token) -> None:
    await hass.auth.async_create_user("Мама")
    await _setup(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "house_editors") is None
    kid = await hass_client(hass_read_only_access_token)
    assert (await (await kid.get("/api/hauflo/house")).json())["can_edit"] is True
    assert (await (await (await hass_client()).get("/api/hauflo/house")).json())["restricted"] == []


async def test_alarm_accept_stays_open_to_everyone(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry) -> None:
    # Safety: any person of the house may say «Принято» — never 403 for a person not allowed to change settings.
    kid = await hass_client(hass_read_only_access_token)
    res = await kid.post("/api/hauflo/alarms", json={"ack": "rule|binary_sensor.leak"})
    assert res.status in (200, 404)


async def test_favourites_are_per_person(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry) -> None:
    admin = await hass_client()
    kid = await hass_client(hass_read_only_access_token)
    assert (await (await kid.get("/api/hauflo/house")).json())["favorites"] == {"items": []}
    fav = {"items": ["light.kitchen", "scene.evening", "cover.bed", "lock.door", "light.kitchen", "<b>"], "room": {"path": "/hauflo-home/etazh_1_spalnia", "name": " Комната бабушки "}}
    got = await (await kid.post("/api/hauflo/house", json={"favorites": fav})).json()
    assert got["favorites"] == {"items": ["light.kitchen", "scene.evening", "cover.bed"], "room": {"path": "/hauflo-home/etazh_1_spalnia", "name": "Комната бабушки"}}
    # Not someone else's: the admin's own list is still empty; no write right needed for one's own favourites.
    assert (await (await admin.get("/api/hauflo/house")).json())["favorites"] == {"items": []}
    assert (await (await kid.get("/api/hauflo/house")).json())["favorites"]["items"] == ["light.kitchen", "scene.evening", "cover.bed"]
    assert (await kid.post("/api/hauflo/house", json={"favorites": "x"})).status == 400
    assert (await kid.post("/api/hauflo/house", json={"nope": 1})).status == 400


def test_favourites_cleaned() -> None:
    assert clean_favorites({"items": [f"light.l{i}" for i in range(40)]})["items"] == [f"light.l{i}" for i in range(24)]
    assert clean_favorites({"items": [], "room": {"path": "/evil/x"}}) == {"items": []}
    assert clean_favorites({"items": 5}) is None
