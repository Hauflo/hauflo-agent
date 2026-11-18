"""The integrator's switches: alarms, members, shown entities and locks of other integrations."""
from __future__ import annotations
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from ..storage import HaufloStore
from .common import DATA, DOOR_ID, LOCK_KEYS, MEMBER_DOMAINS, STORE_KEY, UNSAFE_NAME, USER_ID, USER_KEY, lt, max_ids

class Flags:

    def __init__(self, hass: HomeAssistant) -> None:
        self.notify: dict[str, bool] = {}
        self.plans: dict[str, dict[str, Any]] = {}
        self.journal: dict[str, dict[str, dict[str, Any]]] = {}
        self.members: set[str] = set()
        self.locks: dict[str, dict[str, Any]] = {}
        self.on_locks: Any = None
        self.lock_info: Any = None
        self._store = HaufloStore(hass, STORE_KEY)

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        raw = data.get('notify') if isinstance(data, dict) else None
        self.notify = {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, bool)} if isinstance(raw, dict) else {}
        j = data.get('journal') if isinstance(data, dict) else None
        self.journal = {k: v for k, v in j.items() if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get('was'), dict) and isinstance(v.get('set'), dict)} if isinstance(j, dict) else {}
        self.members = members_ok(data.get('members') if isinstance(data, dict) else None) or set()
        self.locks = locks_ok(data.get('locks') if isinstance(data, dict) else None) or {}

    def _data(self) -> dict[str, Any]:
        return {'notify': self.notify, 'journal': self.journal, 'members': sorted(self.members), 'locks': self.locks}

    def save(self) -> None:
        self._store.async_delay_save(self._data, 0)

    def set_notify(self, m: dict[str, bool]) -> None:
        self.notify = dict(m)
        self.save()

    def set_members(self, ids: set[str]) -> None:
        self.members = set(ids)
        self.save()

    def set_locks(self, m: dict[str, dict[str, Any]]) -> None:
        was = self.locks
        self.locks = m
        self.save()
        if callable(self.on_locks):
            self.on_locks(was, m)

    async def async_remove(self) -> None:
        await self._store.async_remove()

def flags(hass: HomeAssistant | None) -> Flags | None:
    data = getattr(hass, 'data', None)
    return data.get(DATA) if isinstance(data, dict) else None

def notify_flag(hass: HomeAssistant | None, eid: str) -> bool | None:
    """The integrator's say for this entity: True / False, None — not said (the label decides)."""
    f = flags(hass)
    return f.notify.get(eid) if f is not None else None

def notify_on(hass: HomeAssistant | None) -> set[str]:
    f = flags(hass)
    return {k for k, v in f.notify.items() if v} if f is not None else set()

def notify_off(hass: HomeAssistant | None) -> set[str]:
    f = flags(hass)
    return {k for k, v in f.notify.items() if not v} if f is not None else set()

async def async_setup(hass: HomeAssistant) -> Flags:
    if (f := flags(hass)) is not None:
        return f
    f = Flags(hass)
    await f.async_load()
    hass.data[DATA] = f
    return f

def notify_ok(m: Any) -> bool:
    return isinstance(m, dict) and len(m) <= max_ids() and all((isinstance(k, str) and cv.valid_entity_id(k) and isinstance(v, bool) for k, v in m.items()))

def members_ok(v: Any) -> set[str] | None:
    """{"members": [...]}: entity ids of lights / covers only, at most MAX_IDS (anything else — the whole body
    refused)."""
    if not isinstance(v, list) or len(v) > max_ids():
        return None
    if not all((isinstance(x, str) and len(x) <= 255 and cv.valid_entity_id(x) and (x.split('.', 1)[0] in MEMBER_DOMAINS) for x in v)):
        return None
    return set(v)

def locks_ok(v: Any) -> dict[str, dict[str, Any]] | None:
    """{"locks": {lock entity: {push?, alarms?: bool, users?: [HA user ids], names?: {user no.: name}, door?: intercom
    door id | null, poll?: minutes}}} — anything else refuses the whole body (a flood or a garbage name never kept)."""
    if not isinstance(v, dict) or len(v) > lt()['locks_max']:
        return None
    out: dict[str, dict[str, Any]] = {}
    for eid, s in v.items():
        if not (isinstance(eid, str) and cv.valid_entity_id(eid) and eid.startswith('lock.') and isinstance(s, dict) and (not set(s) - LOCK_KEYS)):
            return None
        c: dict[str, Any] = {}
        for k in ('push', 'alarms'):
            if k in s:
                if not isinstance(s[k], bool):
                    return None
                c[k] = s[k]
        users = s.get('users', [])
        if not isinstance(users, list) or len(users) > lt()['users_max'] or (not all((isinstance(u, str) and USER_ID.match(u) for u in users))):
            return None
        if users:
            c['users'] = sorted(set(users))
        names = s.get('names', {})
        if not isinstance(names, dict) or len(names) > lt()['names_max']:
            return None
        for k, n in names.items():
            if not (isinstance(k, str) and USER_KEY.match(k) and isinstance(n, str) and (0 < len(n.strip()) <= lt()['name_max']) and (not UNSAFE_NAME.search(n))):
                return None
        if names:
            c['names'] = {k: n.strip() for k, n in names.items()}
        door = s.get('door')
        if door is not None and (not (isinstance(door, str) and DOOR_ID.match(door))):
            return None
        if door:
            c['door'] = door
        poll = s.get('poll', 0)
        if isinstance(poll, bool) or not isinstance(poll, int) or (not 0 <= poll <= lt()['poll_max_min']):
            return None
        if poll:
            c['poll'] = poll
        out[eid] = c
    return out

def locks_of(hass: HomeAssistant | None) -> dict[str, dict[str, Any]]:
    f = flags(hass)
    return f.locks if f is not None else {}
