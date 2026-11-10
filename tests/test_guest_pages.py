"""«Гость со входом: страницы» (guest.py «pages», dashmerge.expand_access, owner 10.11): an administrator gives a guest
chosen pages of Hauflo's dashboards; the agent makes a non-admin HA user, every page gets a list of who sees it, revoke /
expiry delete the user. Mostly negative: the cloud must never see the guests' lists, the family never lose a page."""

import asyncio
import copy
import datetime as dt
import json
import re

import pytest
from homeassistant.auth.providers import homeassistant as auth_ha
from homeassistant.components.frontend.storage import async_user_store
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, flush_store

from custom_components.hauflo import dashmerge
from custom_components.hauflo import guest as G
from custom_components.hauflo.house import is_restricted

from .test_guest import _client, entry, person  # noqa: F401 — the same agent and family member

URL = "/api/hauflo/guest"
HOME = "hauflo-home"


def _cloud() -> dict:
    """What the cloud deploys: pages for everyone, one said `visible: true`, the service floor for admins, one hidden."""
    return {
        "title": "Дом",
        "views": [
            {"title": "Главная", "path": "building", "cards": [{"type": "markdown", "content": "a"}]},
            {"title": "Котельная", "path": "boiler", "cards": [{"type": "markdown", "content": "b"}]},
            {"title": "Комнаты", "path": "rooms", "visible": True, "cards": []},
            {"title": "Комната 1", "path": "room_1", "subview": True, "cards": []},
            {"title": "Сервис", "path": "service", "visible": [{"user": "hauflo:admins"}], "cards": []},
            {"title": "Черновик", "path": "draft", "visible": False, "cards": []},
            {"title": "Без адреса", "cards": []},
        ],
    }


def _sees(cfg: dict, uid: str) -> list[str]:
    """HA's frontend (hui-root _isVisible): no `visible`, `true`, or a list naming the user."""
    out = []
    for v in cfg["views"]:
        vis = v.get("visible")
        if "visible" not in v or vis is True or (isinstance(vis, list) and any(isinstance(x, dict) and x.get("user") == uid for x in vis)):
            out.append(v.get("path", "?"))
    return out


async def _local_ws(cmds):
    return [[] if c["type"] == "lovelace/dashboards/list" else None for c in cmds]


@pytest.fixture
async def home(hass: HomeAssistant, entry, person, hass_owner_user, hass_admin_user):  # noqa: F811 — every admin made before the deploy
    """A Hauflo dashboard in HA's own lovelace (storage), saved by the cloud through the agent."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from homeassistant.components.lovelace.dashboard import LovelaceStorage

    assert await async_setup_component(hass, "lovelace", {})
    dash = LovelaceStorage(hass, {"id": "d1", "url_path": HOME, "title": "Дом", "mode": "storage", "require_admin": False, "show_in_sidebar": True})
    hass.data[LOVELACE_DATA].dashboards[HOME] = dash
    agent = entry.runtime_data
    r = await agent.dashboards.save(HOME, _cloud(), None, None, False, "Дом", _local_ws)
    assert r["saved"]
    await hass.async_block_till_done()
    return dash


async def _cfg(dash) -> dict:
    return await dash.async_load(False)


async def _give(hass, hass_client, admin, pages, label="Мастер котельной", hours=24) -> dict:
    c = await _client(hass, hass_client, admin)
    r = await c.post(URL, json={"op": "issue_pages", "label": label, "pages": [{"dashboard": HOME, "view": p} for p in pages], "hours": hours})
    assert r.status == 200, await r.text()
    return await r.json()


async def _pause(hass) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + dt.timedelta(seconds=G.PAGES_DEBOUNCE + 1))
    await hass.async_block_till_done()


async def test_issue_makes_a_non_admin_login_and_shows_only_his_pages(hass: HomeAssistant, entry, home, person, hass_owner_user, hass_client) -> None:  # noqa: F811
    agent = entry.runtime_data
    before = await _cfg(home)
    c = await _client(hass, hass_client, hass_owner_user)
    got = await (await c.get(f"{URL}?pages=1")).json()
    assert got["can_issue_pages"] and got["pages_hours"] == G.PAGES_HOURS and got["pages"] == []
    offered = {v["path"]: v for d in got["pages_available"] for v in d["views"]}
    # Every page with an address, the admins' one marked; the hidden one and the one without an address not offered.
    assert set(offered) == {"building", "boiler", "rooms", "room_1", "service"}
    assert offered["service"]["admin"] and offered["room_1"]["sub"] and not offered["boiler"]["admin"]
    hass.config.external_url, hass.config.internal_url = "https://house.example", "http://192.168.1.5:8123"

    res = await _give(hass, hass_client, hass_owner_user, ["boiler"])
    assert res["username"].startswith("guest-") and len(res["password"]) >= 12
    assert res["urls"] == [f"https://house.example/{HOME}/boiler", f"http://192.168.1.5:8123/{HOME}/boiler"]
    assert res["urls_source"] == "configured"
    guest = await hass.auth.async_get_user(next(g["user_id"] for g in agent.guest.grants if g.get("kind") == "pages"))
    assert guest is not None and not guest.is_admin and not guest.system_generated and guest.is_active
    assert [g.id for g in guest.groups] == ["system-users"]
    # The login works — HA's own provider; the password is kept nowhere by Hauflo.
    provider = auth_ha.async_get_provider(hass)
    await provider.async_validate_login(res["username"], res["password"])
    await flush_store(agent.guest.store)
    assert res["password"] not in json.dumps(agent.guest._data())
    listed = await (await c.get(URL)).text()
    assert res["password"] not in listed and res["username"] in listed

    cfg = await _cfg(home)
    assert _sees(cfg, guest.id) == ["boiler"]
    assert set(_sees(cfg, person.id)) == set(_sees(before, person.id))
    assert set(_sees(cfg, hass_owner_user.id)) == set(_sees(before, hass_owner_user.id))
    assert "service" in _sees(cfg, hass_owner_user.id) and "service" not in _sees(cfg, person.id)
    # His menu: only this dashboard, his start page.
    store = await async_user_store(hass, guest.id)
    assert HOME not in store.data["sidebar"]["hiddenPanels"] and store.data["sidebar"]["panelOrder"] == [HOME]
    assert store.data["core"]["default_panel"] == HOME
    # A guest is no person of the house: restricted, no guest settings, not in the people list.
    assert await is_restricted(hass, guest)
    gc = await _client(hass, hass_client, guest)
    assert (await gc.get(URL)).status == 403
    assert (await gc.post(URL, json={"op": "revoke_all"})).status == 403
    assert (await gc.post(URL, json={"op": "issue_pages", "label": "x", "pages": [{"dashboard": HOME, "view": "boiler"}], "hours": 3})).status == 403
    house = await (await c.get("/api/hauflo/house")).json()
    assert guest.id not in {u["id"] for u in house["users"]}


async def test_cloud_sees_its_own_config(hass: HomeAssistant, entry, home, person, hass_owner_user, hass_client) -> None:  # noqa: F811
    agent = entry.runtime_data
    read0 = await agent.dashboards.read(HOME)
    plan0 = await agent.dashboards.plan(HOME, _cloud())
    await _give(hass, hass_client, hass_owner_user, ["boiler", "service"])
    expanded = await _cfg(home)
    assert await agent.dashboards.read(HOME) == read0
    assert await agent.dashboards.plan(HOME, _cloud()) == plan0
    # The same deploy again: nothing to write, the guest's pages as they were.
    r = await agent.dashboards.save(HOME, _cloud(), None, None, False, "Дом", _local_ws)
    assert not r["saved"] and await _cfg(home) == expanded
    # A changed deploy: written, verified, the guest's lists put back on it.
    new = _cloud()
    new["views"][1]["title"] = "Котельная и бойлер"
    r = await agent.dashboards.save(HOME, new, None, None, False, "Дом", _local_ws)
    assert r["saved"] and r.get("verify") == "ok"
    guest = next(g["user_id"] for g in agent.guest.grants if g.get("kind") == "pages")
    cfg = await _cfg(home)
    assert _sees(cfg, guest) == ["boiler", "service"] and cfg["views"][1]["title"] == "Котельная и бойлер"
    read1 = await agent.dashboards.read(HOME)
    assert "hauflo:guests" not in json.dumps(read1) and guest not in json.dumps(read1) and "hauflo:all" not in json.dumps(read1)


async def test_two_guests_revoke_expiry_and_back_to_byte_identical(hass: HomeAssistant, entry, home, person, hass_owner_user, hass_client) -> None:  # noqa: F811
    agent = entry.runtime_data
    before = await _cfg(home)
    first = await _give(hass, hass_client, hass_owner_user, ["boiler"], label="Мастер")
    # No external / internal address in HA: no links, the card says the guest opens it only at home.
    assert first["urls"] == [] and first["urls_source"] == "fallback"
    await _give(hass, hass_client, hass_owner_user, ["rooms", "room_1", "service"], label="Мастер")  # same name: a second login
    a, b = [g for g in agent.guest.grants if g.get("kind") == "pages"][::-1]
    assert a["username"] != b["username"] and b["username"].startswith(a["username"])
    cfg = await _cfg(home)
    assert _sees(cfg, a["user_id"]) == ["boiler"]
    assert _sees(cfg, b["user_id"]) == ["rooms", "room_1", "service"]  # an admins' page given to a guest
    assert "service" not in _sees(cfg, person.id)
    # Any person of the house may close it (the safe direction) — the login goes at once.
    pc = await _client(hass, hass_client, person)
    r = await pc.post(URL, json={"op": "revoke", "id": a["id"]})
    assert r.status == 200
    assert await hass.auth.async_get_user(a["user_id"]) is None and a["removed"]
    cfg = await _cfg(home)
    assert a["user_id"] not in json.dumps(cfg) and _sees(cfg, b["user_id"]) == ["rooms", "room_1", "service"]
    assert agent.guest.log[0]["sys"] == "pages_revoked"
    # Expiry: the minute's check deletes the user, notes it, and the dashboard is again exactly the cloud's.
    b["until"] = G._iso(dt_util.utcnow() - dt.timedelta(minutes=1))
    async_fire_time_changed(hass, dt_util.utcnow() + G.PAGES_CHECK + dt.timedelta(seconds=1))
    await hass.async_block_till_done()
    assert await hass.auth.async_get_user(b["user_id"]) is None and b["removed"]
    assert any(x["sys"] == "pages_expired" for x in agent.guest.log)
    assert await _cfg(home) == before
    got = await (await pc.get(URL)).json()
    assert [p["status"] for p in got["pages"]] == ["expired", "revoked"]


async def test_who_may_and_limits(hass: HomeAssistant, entry, home, person, hass_owner_user, hass_client) -> None:  # noqa: F811
    agent = entry.runtime_data
    page = [{"dashboard": HOME, "view": "boiler"}]
    pc = await _client(hass, hass_client, person)
    # A family member who isn't HA's administrator: no logins (HA's users are the admins').
    assert (await pc.post(URL, json={"op": "issue_pages", "label": "x", "pages": page, "hours": 3})).status == 403
    assert not (await (await pc.get(f"{URL}?pages=1")).json())["can_issue_pages"]
    c = await _client(hass, hass_client, hass_owner_user)
    for body in (
        {"label": "", "pages": page, "hours": 3},
        {"label": "x", "pages": [], "hours": 3},
        {"label": "x", "pages": "boiler", "hours": 3},
        {"label": "x", "pages": [{"dashboard": HOME, "view": "draft"}], "hours": 3},  # hidden from everyone
        {"label": "x", "pages": [{"dashboard": HOME, "view": "nope"}], "hours": 3},
        {"label": "x", "pages": [{"dashboard": "lovelace", "view": "boiler"}], "hours": 3},
        {"label": "x", "pages": [{"dashboard": HOME, "view": "boiler", "admin": True}], "hours": 3},
        {"label": "x", "pages": page, "hours": 0},
        {"label": "x", "pages": page, "hours": G.PAGES_MAX_DAYS * 24 + 1},
        {"label": "x", "pages": page, "hours": True},
        {"label": "x", "pages": page, "hours": 3, "admin": True},
    ):
        r = await c.post(URL, json={"op": "issue_pages", **body})
        assert r.status == 400, body
    assert not [g for g in agent.guest.grants if g.get("kind") == "pages"]
    assert len(await hass.auth.async_get_users()) == len({u.id for u in await hass.auth.async_get_users()})
    users_before = len(await hass.auth.async_get_users())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(G, "PAGES_MAX_ACTIVE", 2)
        await _give(hass, hass_client, hass_owner_user, ["boiler"])
        await _give(hass, hass_client, hass_owner_user, ["boiler"])
        r = await c.post(URL, json={"op": "issue_pages", "label": "x", "pages": page, "hours": 3})
        assert r.status == 400
    assert len(await hass.auth.async_get_users()) == users_before + 2  # the refused one made no user
    # «Закрыть всё»: links and logins alike.
    r = await c.post(URL, json={"op": "revoke_all"})
    assert (await r.json())["revoked"] == 2 and len(await hass.auth.async_get_users()) == users_before


async def test_family_added_later_and_pages_added_in_ha(hass: HomeAssistant, entry, home, person, hass_owner_user, hass_client) -> None:  # noqa: F811
    await _give(hass, hass_client, hass_owner_user, ["boiler"])
    late = await hass.auth.async_create_user("Пётр", group_ids=["system-users"])
    await _pause(hass)
    cfg = await _cfg(home)
    assert set(_sees(cfg, late.id)) == set(_sees(cfg, person.id)) and "building" in _sees(cfg, late.id)
    # A page the integrator adds in HA's own editor: a list at once (else the guest would see it).
    guest = next(g["user_id"] for g in entry.runtime_data.guest.grants if g.get("kind") == "pages")
    cfg["views"].append({"title": "Новая", "path": "new", "cards": []})
    await home.async_save(cfg)
    await _pause(hass)
    cfg = await _cfg(home)
    assert "new" in _sees(cfg, person.id) and "new" not in _sees(cfg, guest)
    # HA's editor saved a list without our tags: the guest's id is still taken out for the cloud.
    raw = await _cfg(home)
    for v in raw["views"]:
        if isinstance(v.get("visible"), list):
            v["visible"] = [x for x in v["visible"] if not str(x.get("user", "")).startswith("hauflo:")]
    await home.async_save(raw)
    await hass.async_block_till_done()
    assert guest not in json.dumps(await entry.runtime_data.dashboards.read(HOME))


async def test_restart_and_race_with_a_deploy(hass: HomeAssistant, entry, home, person, hass_owner_user, hass_client) -> None:  # noqa: F811
    agent = entry.runtime_data
    await _give(hass, hass_client, hass_owner_user, ["boiler"])
    g = next(g for g in agent.guest.grants if g.get("kind") == "pages")
    await flush_store(agent.guest.store)
    # HA restarted: a fresh engine knows the guest from its store (restricted at once, before any request).
    hass.data.pop(G.GUEST_USERS, None)
    again = G.GuestAccess(hass, lambda _e: False, lambda: "")
    await again.async_load()
    assert g["user_id"] in hass.data[G.GUEST_USERS]
    assert (await again.access()).pages == {HOME: {"boiler": [g["user_id"]]}}
    # A revoke while the cloud saves: whichever runs first, the guest is in no list after both.
    new = _cloud()
    new["views"][0]["title"] = "Дом целиком"
    await asyncio.gather(agent.dashboards.save(HOME, new, None, None, False, "Дом", _local_ws), agent.guest.revoke(person, g["id"]))
    await hass.async_block_till_done()
    cfg = await _cfg(home)
    assert g["user_id"] not in json.dumps(cfg) and cfg["views"][0]["title"] == "Дом целиком"


async def test_no_guest_nothing_touched(hass: HomeAssistant, entry, home) -> None:  # noqa: F811
    agent = entry.runtime_data
    before = await _cfg(home)
    await agent.guest.check()
    await agent.guest.sync(force=True)
    assert await agent.dashboards.apply_access() == 0
    assert (await agent.guest.access()).active is False
    r = await agent.dashboards.save(HOME, _cloud(), None, None, False, "Дом", _local_ws)
    assert not r["saved"] and await _cfg(home) == before


async def test_integration_removed_deletes_the_guests(hass: HomeAssistant, entry, home, person, hass_owner_user, hass_client) -> None:  # noqa: F811
    before = await _cfg(home)
    await _give(hass, hass_client, hass_owner_user, ["boiler"])
    uid = next(g["user_id"] for g in entry.runtime_data.guest.grants if g.get("kind") == "pages")
    await flush_store(entry.runtime_data.guest.store)
    await flush_store(entry.runtime_data.dashboards.store)
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.auth.async_get_user(uid) is None
    assert await _cfg(home) == before


def test_expand_collapse_round_trip() -> None:
    cfg = _cloud()
    cfg["views"][4]["visible"] = [{"user": "admin1"}]
    plain = copy.deepcopy(cfg)
    acc = dashmerge.Access(["admin1", "kid"], {"g1", "g2"}, {HOME: {"boiler": ["g1"], "service": ["g2"]}}, True)
    out = dashmerge.expand_access(copy.deepcopy(cfg), acc, HOME)
    assert _sees(out, "g1") == ["boiler"] and _sees(out, "g2") == ["service"]
    assert set(_sees(out, "kid")) == set(_sees(plain, "kid")) and set(_sees(out, "admin1")) == set(_sees(plain, "admin1"))
    assert out["views"][5]["visible"] is False  # nobody stays nobody
    assert dashmerge.collapse_access(copy.deepcopy(out), acc.guests) == plain
    again = dashmerge.expand_access(dashmerge.collapse_access(copy.deepcopy(out), acc.guests), acc, HOME)
    assert again == out  # idempotent: a re-check writes nothing
    off = dashmerge.Access(["admin1", "kid"], {"g1"}, {}, False)
    assert dashmerge.expand_access(copy.deepcopy(plain), off, HOME) == plain
    assert dashmerge.expand_access(copy.deepcopy(plain), None, HOME) == plain
    # Another dashboard: the family's lists, no guest.
    other = dashmerge.expand_access(copy.deepcopy(plain), acc, "hauflo-other")
    assert _sees(other, "g1") == [] and set(_sees(other, "kid")) == set(_sees(plain, "kid"))


def test_username_without_double_prefix_and_never_unknown() -> None:
    taken: set[str] = set()
    assert G.GuestAccess._username("Guest EN", taken) == "guest-en"
    assert G.GuestAccess._username("Гость Иван", taken) == "guest-ivan"
    assert G.GuestAccess._username("Иван", {"guest-ivan"}) == "guest-ivan-2"
    for label in ("🙂🙂", "Guest", "гость"):
        name = G.GuestAccess._username(label, taken)
        assert re.fullmatch(r"guest-[0-9a-f]{4}", name), (label, name)
