"""The client's sidebar without HA's own extras (owner 2026-10-25): Map, Energy, Logbook, History, Calendar, Media,
To-do hidden for the family — every person in this HA except its administrators (HA reviewer r2: an admin's menu was
stripped to «Home» — the integrator and the owner who runs the house keep HA's full menu). Per user, in HA's own
frontend storage (each can show a panel again in their profile). The choice is kept by the agent: a person added
later gets it too (integrator run 16: a client created after the deploy saw Map/Energy/…).

API for Hauflo (through the agent's tunnel, admin): POST /api/hauflo/sidebar {"hide": [...], "show": [...]}.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import Event, HomeAssistant, callback

from .storage import HaufloStore
from .const import DATA_SIDEBAR_LISTENER

STORE_KEY = "hauflo.sidebar"

# What a client doesn't need in the menu — HA's own panels (their url paths).
EXTRAS = ["map", "energy", "logbook", "history", "calendar", "media-browser", "todo"]


async def async_apply(hass: HomeAssistant, hide: list[str], show: list[str]) -> dict[str, Any]:
    from homeassistant.components.frontend.storage import async_user_store  # noqa: PLC0415

    hide = [p for p in hide if isinstance(p, str)]
    show = [p for p in show if isinstance(p, str)]
    users = [u for u in await hass.auth.async_get_users() if not u.system_generated and u.is_active and not u.is_admin]
    for user in users:
        store = await async_user_store(hass, user.id)
        sidebar = dict(store.data.get("sidebar") or {})
        hidden = [p for p in (sidebar.get("hiddenPanels") or []) if p not in show]
        hidden += [p for p in hide if p not in hidden]
        sidebar["hiddenPanels"] = hidden
        await store.async_set_item("sidebar", sidebar)
    # Remember what's hidden for people added later.
    saved = HaufloStore(hass, STORE_KEY)
    kept = [p for p in ((await saved.async_load()) or {}).get("hide", []) if p not in show]
    kept += [p for p in hide if p not in kept]
    await saved.async_save({**((await saved.async_load()) or {}), "hide": kept})
    return {"users": len(users), "hidden": hide, "shown": show}


async def async_admins_back(hass: HomeAssistant) -> int:
    """Once, after an agent that hid the extras for administrators too: theirs come back (what Hauflo recorded as
    hidden by it). Returns how many administrators got them back."""
    from homeassistant.components.frontend.storage import async_user_store  # noqa: PLC0415

    saved = HaufloStore(hass, STORE_KEY)
    data = (await saved.async_load()) or {}
    if not data or data.get("admins_kept"):
        return 0
    ours = {p for p in data.get("hide", []) if p in MENU_EXTRAS}
    back = 0
    for user in await hass.auth.async_get_users():
        if user.system_generated or not user.is_admin or not ours:
            continue
        store = await async_user_store(hass, user.id)
        sidebar = dict(store.data.get("sidebar") or {})
        was = list(sidebar.get("hiddenPanels") or [])
        now = [p for p in was if p not in ours]
        if now != was:
            sidebar["hiddenPanels"] = now
            await store.async_set_item("sidebar", sidebar)
            back += 1
    await saved.async_save({**data, "admins_kept": True})
    return back


async def async_setup(hass: HomeAssistant) -> None:
    """A person added to this HA gets the same short menu."""
    from homeassistant.components.frontend.storage import async_user_store  # noqa: PLC0415

    async def _added(event: Event) -> None:
        hide = ((await HaufloStore(hass, STORE_KEY).async_load()) or {}).get("hide", [])
        user = await hass.auth.async_get_user(event.data.get("user_id", ""))
        if not hide or user is None or user.system_generated or user.is_admin:
            return
        store = await async_user_store(hass, user.id)
        sidebar = dict(store.data.get("sidebar") or {})
        sidebar["hiddenPanels"] = list(dict.fromkeys([*(sidebar.get("hiddenPanels") or []), *hide]))
        await store.async_set_item("sidebar", sidebar)

    if not hass.data.get(DATA_SIDEBAR_LISTENER):
        hass.data[DATA_SIDEBAR_LISTENER] = hass.bus.async_listen("user_added", _added)
    await async_admins_back(hass)


def async_unload(hass: HomeAssistant) -> None:
    """The integration unloaded or removed: people added later keep HA's full menu (HA review round 3)."""
    if unsub := hass.data.pop(DATA_SIDEBAR_LISTENER, None):
        unsub()


MENU_EXTRAS = {"map", "energy", "logbook", "history", "calendar", "media-browser", "todo"}


async def async_remove(hass: HomeAssistant) -> int:
    """The integration removed: the panels Hauflo hid (as it recorded them) come back for every person, and the choice
    is forgotten — a person added later gets HA's full menu (HA expert 28.10). Returns how many people got them back."""
    from homeassistant.components.frontend.storage import async_user_store  # noqa: PLC0415

    saved = HaufloStore(hass, STORE_KEY)
    hidden_by_us = {p for p in ((await saved.async_load()) or {}).get("hide", []) if p in MENU_EXTRAS}
    back = 0
    if hidden_by_us:
        for user in await hass.auth.async_get_users():
            if user.system_generated:
                continue
            store = await async_user_store(hass, user.id)
            sidebar = dict(store.data.get("sidebar") or {})
            was = list(sidebar.get("hiddenPanels") or [])
            now = [p for p in was if p not in hidden_by_us]
            if now != was:
                sidebar["hiddenPanels"] = now
                await store.async_set_item("sidebar", sidebar)
                back += 1
    await saved.async_remove()
    return back


class SidebarView(HomeAssistantView):
    url = "/api/hauflo/sidebar"
    name = "api:hauflo:sidebar"
    requires_auth = True

    async def post(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict) or not all(isinstance(body.get(k) or [], list) for k in ("hide", "show")):
            return self.json_message("hide / show: lists expected", 400)
        hass = request.app[KEY_HASS]
        # Only HA's own extras leave the menu — never Settings, a dashboard or the integrator's panels (security
        # expert 25.10).
        if not set(body.get("hide") or []) <= MENU_EXTRAS:
            return self.json_message("only HA's extra panels can be hidden", 400)
        result = await async_apply(hass, body.get("hide") or [], body.get("show") or [])
        if isinstance(home := body.get("home"), str) and home.startswith("hauflo-"):
            result["home"] = await async_home(hass, home)
        return self.json(result)


async def async_home(hass: HomeAssistant, url_path: str) -> bool:
    """Hauflo's dashboard as HA's default — only while it's still HA's own («Обзор»): a choice made in HA stays."""
    from homeassistant.components.frontend.storage import async_system_store  # noqa: PLC0415

    store = await async_system_store(hass)
    core = dict(store.data.get("core") or {})
    current = core.get("default_panel")
    # A Hauflo dashboard deleted since (a project taken over) is nobody's choice any more (integrator run 25: HA
    # opened its «Overview», the hand-over promised «Дом» first).
    dashboards = getattr(hass.data.get("lovelace"), "dashboards", None) or {}
    # Any start page that no longer exists (a dashboard deleted since, an older install's one) is nobody's choice
    # either — HA itself falls back to «Обзор» (round 6, B13).
    panels = hass.data.get("frontend_panels") or {}
    gone = isinstance(current, str) and current not in ("", "home", "lovelace") and current not in dashboards and current not in panels
    if current not in (None, "", "home", "lovelace", url_path) and not gone:
        return False
    core["default_panel"] = url_path
    await store.async_set_item("core", core)
    return True
