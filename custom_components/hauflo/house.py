from __future__ import annotations
import re
import time
import unicodedata
from typing import Any
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from . import service as service_info
from .natural import natural_key
from .ownership import ZONES_KEY
from .storage import HaufloStore
STORE_KEY = 'hauflo.house'
DATA = 'hauflo_house'
GUEST_USERS = 'hauflo_guest_users'

def is_guest(hass: HomeAssistant, user: Any) -> bool:
    return user is not None and getattr(user, 'id', None) in hass.data.get(GUEST_USERS, ())
MAX_FAV = 24
MAX_RESTRICTED = 50
OLD_ISSUE = 'house_editors'
_FAV = re.compile('^(light|switch|cover|scene|script|fan|climate|media_player|valve)\\.[a-z0-9_]{1,200}$')
_PATH = re.compile('^/hauflo-[a-z0-9_-]{1,100}/[A-Za-z0-9_%.-]{1,150}$')
_USER = re.compile('^[0-9a-f]{32}$')

def _text(v: Any, n: int) -> str:
    if not isinstance(v, str):
        return ''
    v = ''.join((' ' if unicodedata.category(ch)[0] == 'C' else ch for ch in v))
    return ' '.join(v.split())[:n].strip()

def clean_favorites(body: Any) -> dict[str, Any] | None:
    if not isinstance(body, dict):
        return None
    items = body.get('items', [])
    if not isinstance(items, list):
        return None
    seen: list[str] = []
    for x in items:
        if isinstance(x, str) and _FAV.match(x) and (x not in seen):
            seen.append(x)
    out: dict[str, Any] = {'items': seen[:MAX_FAV]}
    room = body.get('room')
    if isinstance(room, dict) and isinstance(room.get('path'), str) and _PATH.match(room['path']):
        out['room'] = {'path': room['path'], 'name': _text(room.get('name'), 60) or room['path'].rsplit('/', 1)[-1]}
    return out

class House:

    def __init__(self, hass: HomeAssistant) -> None:
        self.store = HaufloStore(hass, STORE_KEY)
        self.data: dict[str, Any] | None = None

    async def load(self) -> dict[str, Any]:
        if self.data is None:
            raw = await self.store.async_load() or {}
            self.data = raw if isinstance(raw, dict) else {}
        return self.data

    async def save(self) -> None:
        assert self.data is not None
        self.data['at'] = time.time()
        await self.store.async_save(self.data)

async def service_of(hass: HomeAssistant) -> dict[str, str] | None:
    info = await service_info.async_load(hass)
    contact = (info or {}).get('contact') or {}
    if not info or not (contact.get('phone') or contact.get('email')):
        return None
    name = contact.get('name')
    return {k: v for k, v in (('company', info.get('company')), ('name', name if name != info.get('company') else None), ('phone', contact.get('phone')), ('email', contact.get('email'))) if v}

def _migrate(data: dict[str, Any], people: list[Any], issue_cleared: bool) -> None:
    if 'restricted' in data:
        return
    editors = data.pop('editors', None)
    restricted: list[str] = []
    if isinstance(editors, list) and data.get('grandfathered') and issue_cleared:
        restricted = sorted((u.id for u in people if not u.is_admin and u.id not in editors))
    data['restricted'] = restricted[:MAX_RESTRICTED]
    data.pop('grandfathered', None)

async def async_init(hass: HomeAssistant) -> None:
    from homeassistant.helpers import issue_registry as ir
    from .const import DOMAIN
    h = house(hass)
    data = await h.load()
    reg = ir.async_get(hass)
    note = reg.async_get_issue(DOMAIN, OLD_ISSUE)
    if 'restricted' in data and 'init' in data:
        if note is not None:
            ir.async_delete_issue(hass, DOMAIN, OLD_ISSUE)
        return
    people = [u for u in await hass.auth.async_get_users() if not u.system_generated and u.is_active]
    _migrate(data, people, issue_cleared=note is None)
    if note is not None:
        ir.async_delete_issue(hass, DOMAIN, OLD_ISSUE)
    data.setdefault('init', time.time())
    await h.save()

def house(hass: HomeAssistant) -> House:
    if DATA not in hass.data:
        hass.data[DATA] = House(hass)
    return hass.data[DATA]

async def is_restricted(hass: HomeAssistant, user: Any) -> bool:
    if user is None or not getattr(user, 'is_active', False) or is_guest(hass, user):
        return True
    if user.is_admin:
        return False
    data = await house(hass).load()
    if 'restricted' not in data:
        _migrate(data, [], issue_cleared=False)
    return user.id in (data.get('restricted') or [])

async def may_edit(hass: HomeAssistant, user: Any) -> bool:
    return not await is_restricted(hass, user)

async def refuse(request: Any) -> Any:
    if await may_edit(request.app[KEY_HASS], request.get('hass_user')):
        return None
    return HomeAssistantView.json({'message': "a restricted (child) profile can't change the house's settings", 'code': 'not_allowed'}, 403)

def _protected_ids(hass: HomeAssistant) -> list[str]:
    from . import protected_ids
    return protected_ids(hass)

def _protected_why(hass: HomeAssistant, ids: list[str]) -> dict[str, str]:
    from . import unknown_source
    return {eid: 'unknown_source' for eid in ids if unknown_source(hass, eid)}

def _rule_states(hass: HomeAssistant, admin: bool) -> dict[str, str]:
    from . import rule_states
    return rule_states(hass, admin)

def _locks_view(hass: HomeAssistant, user_id: str | None) -> dict[str, Any]:
    from .const import DOMAIN
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        locks = getattr(entry.runtime_data, 'locks', None)
        return locks.view(user_id) if locks is not None else {}
    return {}

class HouseView(HomeAssistantView):
    url = '/api/hauflo/house'
    name = 'api:hauflo:house'
    requires_auth = True

    async def get(self, request):
        hass = request.app[KEY_HASS]
        user = request['hass_user']
        data = await house(hass).load()
        mine = (data.get('favorites') or {}).get(user.id) if user else None
        protected = _protected_ids(hass)
        out: dict[str, Any] = {'service': await service_of(hass), 'can_edit': await may_edit(hass, user), 'admin': bool(user and user.is_admin), 'favorites': mine or {'items': []}, 'protected': protected, 'protected_why': _protected_why(hass, protected), 'rules': _rule_states(hass, bool(user and user.is_admin)), 'zones': dict(hass.data.get(ZONES_KEY) or {}), 'locks': _locks_view(hass, user.id if user else None)}
        if user and user.is_admin:
            people = [u for u in await hass.auth.async_get_users() if not u.system_generated and u.is_active and (not is_guest(hass, u))]
            out['restricted'] = list(data.get('restricted') or [])
            out['users'] = [{'id': u.id, 'name': u.name or '', 'admin': u.is_admin} for u in sorted(people, key=lambda u: (not u.is_admin, natural_key(u.name)))]
        return self.json(out)

    async def post(self, request):
        hass = request.app[KEY_HASS]
        user = request['hass_user']
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict) or not {'restricted', 'favorites'} & set(body) or set(body) - {'restricted', 'favorites'}:
            return self.json_message('restricted or favorites expected', 400)
        h = house(hass)
        data = await h.load()
        if 'restricted' in body:
            if not (user and user.is_admin):
                return self.json_message('only an administrator marks restricted (child) profiles', 403)
            ids = body['restricted']
            if not isinstance(ids, list) or not all((isinstance(x, str) and _USER.match(x) for x in ids)):
                return self.json_message('restricted: a list of user ids', 400)
            known = {u.id for u in await hass.auth.async_get_users() if not u.system_generated and (not u.is_admin) and (not is_guest(hass, u))}
            data['restricted'] = sorted({x for x in ids if x in known})[:MAX_RESTRICTED]
        if 'favorites' in body:
            fav = clean_favorites(body['favorites'])
            if fav is None or user is None:
                return self.json_message('favorites: {items: [entity ids], room?: {path, name}}', 400)
            data.setdefault('favorites', {})[user.id] = fav
        await h.save()
        out = {'favorites': (data.get('favorites') or {}).get(user.id if user else '', {'items': []}), 'can_edit': await may_edit(hass, user)}
        if user and user.is_admin:
            out['restricted'] = list(data.get('restricted') or [])
        return self.json(out)
