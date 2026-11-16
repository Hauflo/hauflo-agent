from __future__ import annotations
from typing import Any, Callable
import voluptuous as vol
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
from .const import DOMAIN
from .storage import HaufloStore
from .tuning import TUNING
STORE_KEY = f'{DOMAIN}.protected'
DATA = f'{DOMAIN}_protected'
SERVICE = 'unprotect'
MAX_MARK = TUNING['ownership']['protect_max']
SCHEMA = vol.Schema({vol.Optional('entity_id'): cv.entity_ids, vol.Optional('group_address'): vol.All(cv.ensure_list, [cv.string])})

class Marks:

    def __init__(self, hass: HomeAssistant) -> None:
        self._uids: set[str] = set()
        self._gas: set[str] = set()
        self.deploy: dict[str, set[str]] = {}
        self.uids: set[str] = set()
        self.gas: set[str] = set()
        self._store = HaufloStore(hass, STORE_KEY)

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        own = data.get('own') if isinstance(data.get('own'), dict) else data
        self._uids = {str(u) for u in own.get('uids') or []}
        self._gas = {str(g) for g in own.get('gas') or []}
        deploy = data.get('deploy') if 'own' in data and isinstance(data.get('deploy'), dict) else {}
        self.deploy = {str(u): {str(g) for g in gas or []} for u, gas in deploy.items()}
        self._union()

    def _union(self) -> None:
        self.uids = self._uids | set(self.deploy)
        self.gas = self._gas.union(*self.deploy.values())

    def _data(self) -> dict[str, Any]:
        return {'uids': sorted(self.uids), 'gas': sorted(self.gas), 'own': {'uids': sorted(self._uids), 'gas': sorted(self._gas)}, 'deploy': {u: sorted(g) for u, g in sorted(self.deploy.items())}}

    def _save(self) -> None:
        self._union()
        self._store.async_delay_save(self._data, 0)

    @callback
    def add(self, uid: str, gas: set[str]) -> None:
        if uid in self._uids and gas <= self._gas:
            return
        self._uids.add(uid)
        self._gas |= gas
        self._save()

    @callback
    def add_deploy(self, uid: str, gas: set[str]) -> None:
        if uid in self.deploy and gas <= self.deploy[uid]:
            return
        self.deploy[uid] = self.deploy.get(uid, set()) | gas
        self._save()

    @callback
    def unmark_deploy(self, uids: set[str]) -> set[str]:
        gone = uids & set(self.deploy)
        for uid in gone:
            del self.deploy[uid]
        if gone:
            self._save()
        return gone

    @callback
    def clear(self, uids: set[str], gas: set[str]) -> None:
        self._uids -= uids
        self._gas -= gas
        self.deploy = {u: g - gas for u, g in self.deploy.items() if u not in uids}
        self._save()

    async def async_remove(self) -> None:
        await self._store.async_remove()

def marks(hass: HomeAssistant | None) -> Marks | None:
    data = getattr(hass, 'data', None)
    return data.get(DATA) if data is not None else None

async def async_setup(hass: HomeAssistant, marks_of: Callable[[str], tuple[str | None, set[str]]], ga_key: Callable[[Any], str | None], refresh: Callable[[], None]) -> Marks:
    if (m := marks(hass)) is not None:
        return m
    m = Marks(hass)
    await m.async_load()
    hass.data[DATA] = m

    async def handle(call: ServiceCall) -> None:
        user = await hass.auth.async_get_user(call.context.user_id) if call.context.user_id else None
        if user is None or user.system_generated or (not user.is_admin):
            raise Unauthorized(context=call.context)
        uids, gas = (set(), set())
        for eid in call.data.get('entity_id') or []:
            uid, opening = marks_of(eid)
            if uid is not None:
                uids.add(uid)
            gas |= opening
        for ga in call.data.get('group_address') or []:
            if (key := ga_key(ga)) is not None:
                gas.add(key)
        m.clear(uids, gas)
        refresh()
    async_register_admin_service(hass, DOMAIN, SERVICE, handle, schema=SCHEMA)
    return m

class ProtectView(HomeAssistantView):
    url = '/api/hauflo/protect'
    name = 'api:hauflo:protect'
    requires_auth = True

    def __init__(self, mark: Callable[[HomeAssistant, list[str]], list[str]], unmark: Callable[[HomeAssistant, list[str]], list[str]]) -> None:
        self._mark = mark
        self._unmark = unmark

    async def post(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        ids = body.get('entity_ids') if isinstance(body, dict) else None
        if not _ids_ok(ids):
            return self.json_message('entity_ids: a list of entity ids expected', 400)
        off = body.get('unmark', [])
        if not _ids_ok(off) or set(off) & set(ids):
            return self.json_message('unmark: a list of entity ids, none of entity_ids, expected', 400)
        hass = request.app[KEY_HASS]
        if marks(hass) is None:
            return self.json_message('not ready', 409)
        unmarked = self._unmark(hass, off) if off else []
        return self.json({'marked': self._mark(hass, ids), 'unmarked': unmarked})

def _ids_ok(ids: Any) -> bool:
    return isinstance(ids, list) and len(ids) <= MAX_MARK and all((isinstance(x, str) and cv.valid_entity_id(x) for x in ids))
