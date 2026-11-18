"""To-do hidden for the family — every person in this HA who isn't an administrator. HA's administrators only with the
option «Также у администраторов» (HA reviewer r2: an admin's menu was stripped to «Home» — so off for new houses; the
frontend storage (each can show a panel again in their profile). The choice is kept by the agent: a person added
later gets it too (integrator run 16: a client created after the deploy saw Map/Energy/…).

API for Hauflo (through the agent's tunnel, admin): POST /api/hauflo/sidebar {"hide": [...], "show": [...],
"admins": bool}. Without "admins" the house's choice stays as it is."""
from __future__ import annotations
from typing import Any
from homeassistant.components.frontend.storage import async_system_store, async_user_store
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import Event, HomeAssistant
from .const import DATA_SIDEBAR_LISTENER
from .house import is_guest
from .storage import HaufloStore
STORE_KEY = 'hauflo.sidebar'
EXTRAS = ['map', 'energy', 'logbook', 'history', 'calendar', 'media-browser', 'todo']

def _admins_of(data: dict[str, Any]) -> bool:
    if 'admins' in data:
        return data['admins'] is True
    return bool(data.get('hide'))

async def _set_hidden(hass: HomeAssistant, user_id: str, add: list[str], drop: set[str]) -> bool:
    store = await async_user_store(hass, user_id)
    sidebar = dict(store.data.get('sidebar') or {})
    was = list(sidebar.get('hiddenPanels') or [])
    now = [p for p in was if p not in drop]
    now += [p for p in add if p not in now]
    if now == was:
        return False
    sidebar['hiddenPanels'] = now
    await store.async_set_item('sidebar', sidebar)
    return True

async def async_apply(hass: HomeAssistant, hide: list[str], show: list[str], admins: bool | None=None) -> dict[str, Any]:
    hide = [p for p in hide if isinstance(p, str)]
    show = [p for p in show if isinstance(p, str)]
    saved = HaufloStore(hass, STORE_KEY)
    data = await saved.async_load() or {}
    before = _admins_of(data)
    admins = before if admins is None else admins
    kept = [p for p in data.get('hide', []) if p not in show]
    kept += [p for p in hide if p not in kept]
    ours = {p for p in kept if p in MENU_EXTRAS}
    users = 0
    for user in await hass.auth.async_get_users():
        if user.system_generated or not user.is_active or is_guest(hass, user):
            continue
        if user.is_admin and (not admins):
            if before:
                await _set_hidden(hass, user.id, [], ours | set(show))
            continue
        await _set_hidden(hass, user.id, hide, set(show))
        users += 1
    await saved.async_save({'hide': kept, 'admins': admins})
    return {'users': users, 'hidden': hide, 'shown': show, 'admins': admins}

async def async_upgrade(hass: HomeAssistant) -> int:
    """    its
    goes out of their menu again. Returns how many administrators got it hidden again."""
    saved = HaufloStore(hass, STORE_KEY)
    data = await saved.async_load() or {}
    if 'admins' in data or not data.get('hide'):
        return 0
    ours = [p for p in data['hide'] if p in MENU_EXTRAS]
    again = 0
    if data.get('admins_kept'):
        for user in await hass.auth.async_get_users():
            if user.system_generated or not user.is_active or (not user.is_admin):
                continue
            if await _set_hidden(hass, user.id, ours, set()):
                again += 1
    await saved.async_save({'hide': data['hide'], 'admins': True})
    return again

async def async_setup(hass: HomeAssistant) -> None:
    """A person added to this HA gets the same short menu (an administrator — only with the option on)."""

    async def _added(event: Event) -> None:
        data = await HaufloStore(hass, STORE_KEY).async_load() or {}
        hide = data.get('hide', [])
        user = await hass.auth.async_get_user(event.data.get('user_id', ''))
        if not hide or user is None or user.system_generated or is_guest(hass, user) or (user.is_admin and (not _admins_of(data))):
            return
        await _set_hidden(hass, user.id, hide, set())
    if not hass.data.get(DATA_SIDEBAR_LISTENER):
        hass.data[DATA_SIDEBAR_LISTENER] = hass.bus.async_listen('user_added', _added)
    await async_upgrade(hass)

def async_unload(hass: HomeAssistant) -> None:
    """The integration unloaded or removed: people added later keep HA's full menu (HA review round 3)."""
    if (unsub := hass.data.pop(DATA_SIDEBAR_LISTENER, None)):
        unsub()
MENU_EXTRAS = {'map', 'energy', 'logbook', 'history', 'calendar', 'media-browser', 'todo'}

async def async_remove(hass: HomeAssistant) -> int:
    """The integration removed: the panels Hauflo hid (as it recorded them) come back for every person, and the choice"""
    saved = HaufloStore(hass, STORE_KEY)
    hidden_by_us = {p for p in (await saved.async_load() or {}).get('hide', []) if p in MENU_EXTRAS}
    back = 0
    if hidden_by_us:
        for user in await hass.auth.async_get_users():
            if user.system_generated:
                continue
            store = await async_user_store(hass, user.id)
            sidebar = dict(store.data.get('sidebar') or {})
            was = list(sidebar.get('hiddenPanels') or [])
            now = [p for p in was if p not in hidden_by_us]
            if now != was:
                sidebar['hiddenPanels'] = now
                await store.async_set_item('sidebar', sidebar)
                back += 1
    await saved.async_remove()
    return back

class SidebarView(HomeAssistantView):
    url = '/api/hauflo/sidebar'
    name = 'api:hauflo:sidebar'
    requires_auth = True

    async def post(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict) or not all((isinstance(body.get(k) or [], list) for k in ('hide', 'show'))):
            return self.json_message('hide / show: lists expected', 400)
        hass = request.app[KEY_HASS]
        if not set(body.get('hide') or []) <= MENU_EXTRAS:
            return self.json_message("only HA's extra panels can be hidden", 400)
        admins = body.get('admins')
        if admins is not None and (not isinstance(admins, bool)):
            return self.json_message('admins: true / false expected', 400)
        result = await async_apply(hass, body.get('hide') or [], body.get('show') or [], admins)
        if isinstance((home := body.get('home')), str) and home.startswith('hauflo-'):
            result['home'] = await async_home(hass, home)
        return self.json(result)

async def async_home(hass: HomeAssistant, url_path: str) -> bool:
    """Hauflo's dashboard as HA's default — only while it's still HA's own («Обзор»): a choice made in HA stays."""
    store = await async_system_store(hass)
    core = dict(store.data.get('core') or {})
    current = core.get('default_panel')
    dashboards = getattr(hass.data.get('lovelace'), 'dashboards', None) or {}
    panels = hass.data.get('frontend_panels') or {}
    gone = isinstance(current, str) and current not in ('', 'home', 'lovelace') and (current not in dashboards) and (current not in panels)
    if current not in (None, '', 'home', 'lovelace', url_path) and (not gone):
        return False
    core['default_panel'] = url_path
    await store.async_set_item('core', core)
    return True
