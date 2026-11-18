"""Guest access store: issue, revoke and list accesses."""
from __future__ import annotations
import asyncio
from collections.abc import Callable
import datetime as dt
import hashlib
from http import HTTPStatus
import logging
import secrets
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.util import dt as dt_util
from .. import qr
from ..dashmerge.access import Access
from ..guard import contained
from ..house import GUEST_USERS
from ..natural import natural_key
from ..ownership import entity_room
from ..storage import HaufloStore
from .common import DOMAINS, ENTITY, KEEP_ENDED, PAGES, STORE_KEY, Invalid, Refused, Window, clean, default_devices, iso, issuer_ok, log_days, log_max, max_active, max_devices, max_hours, max_rooms, may_control, pages_hours, rate_bad, roles, secret_hash
from .control import GuestControlMixin
from .lifecycle import GuestLifecycleMixin
from .pages import GuestPagesMixin
_LOGGER = logging.getLogger(__name__)

def route_of(secret: str) -> str:
    """The guest pages' address part of this house: the cloud derives the same from the hash of the agent's secret
    (agentHub.ts guestRouteOf) — no project id in the link, and no other agent can take this house's route."""
    inner = hashlib.sha256(secret.encode()).hexdigest()
    return hashlib.sha256(f'hauflo-guest-route:{inner}'.encode()).hexdigest()[:24]

def _parse(value: Any) -> dt.datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None

class GuestAccess(GuestPagesMixin, GuestControlMixin, GuestLifecycleMixin):

    def __init__(self, hass: HomeAssistant, owns: Callable[[str], bool], link_base: Callable[[], str]) -> None:
        self.hass = hass
        self.owns = owns
        self.link_base = link_base
        self.store: HaufloStore = HaufloStore(hass, STORE_KEY)
        self.grants: list[dict[str, Any]] = []
        self.log: list[dict[str, Any]] = []
        self.loaded = False
        self.lock = asyncio.Lock()
        self._load_lock = asyncio.Lock()
        self.bad = Window(rate_bad())
        self.rates: dict[tuple[str, ...], Window] = {}
        self.refused: set[tuple[str, str]] = set()
        self.dashboards: Any = None
        self._pending: set[str] = set()
        self._applied: Any = None
        self._unsubs: list[Callable[[], None]] = []
        self._later: Callable[[], None] | None = None
        self._sync_lock = asyncio.Lock()

    async def async_load(self) -> None:
        if self.loaded:
            return
        async with self._load_lock:
            if self.loaded:
                return
            data = await self.store.async_load()
            if isinstance(data, dict):
                self.grants = [g for g in data.get('grants', []) if isinstance(g, dict) and isinstance(g.get('id'), str)]
                self.log = [x for x in data.get('log', []) if isinstance(x, dict)]
            self._mark()
            self.loaded = True

    async def async_stop(self) -> None:
        """The entry unloaded: the log noted in the last second is written now, not by a timer of a gone agent."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._later is not None:
            self._later()
            self._later = None
        if self.loaded:
            async with self.lock:
                await self.store.async_save(self._data())
        self.rates.clear()
        self.refused.clear()

    def _data(self) -> dict[str, Any]:
        return {'grants': self.grants, 'log': self.log}

    def _prune(self) -> None:
        now = dt_util.utcnow()
        self.grants = [g for g in self.grants if g.get('kind') == PAGES and (not g.get('removed')) or (end := self._ended_at(g)) is None or now - end < KEEP_ENDED]
        cut = now - dt.timedelta(days=log_days())
        self.log = [x for x in self.log if (t := _parse(x.get('t'))) is not None and t >= cut][:log_max()]

    def _note(self, entry: dict[str, Any]) -> None:
        self.log.insert(0, {'t': iso(dt_util.utcnow()), **entry})
        del self.log[log_max():]
        self.store.async_delay_save(self._data, 1)

    def _area(self, eid: str) -> str | None:
        return entity_room(self.hass, eid)

    def _safe(self, eid: Any) -> bool:
        if not isinstance(eid, str) or not ENTITY.fullmatch(eid) or self.hass.states.get(eid) is None:
            return False
        ent = er.async_get(self.hass).async_get(eid)
        if ent is None or ent.disabled_by or ent.hidden_by or (ent.platform in ('group', 'hauflo')):
            return False
        return bool(self.owns(eid))

    def candidates(self, area_id: str) -> list[str]:
        return sorted((eid for d in DOMAINS for eid in self.hass.states.async_entity_ids(d) if self._safe(eid) and self._area(eid) == area_id))

    def rooms(self, user: Any=None) -> list[dict[str, Any]]:
        out = []
        for area in ar.async_get(self.hass).async_list_areas():
            ids = [e for e in self.candidates(area.id) if user is None or may_control(user, e)]
            if ids:
                out.append({'id': area.id, 'name': area.name, 'count': len(ids), 'kinds': sorted({e.split('.')[0] for e in ids})})
        return sorted(out, key=lambda r: natural_key(r['name']))

    @staticmethod
    def _end(g: dict[str, Any]) -> dt.datetime | None:
        return _parse(g.get('until') if g.get('kind') == PAGES else g.get('expires'))

    def _ended_at(self, g: dict[str, Any]) -> dt.datetime | None:
        if g.get('revoked'):
            return _parse(g['revoked']) or dt_util.utcnow()
        exp = self._end(g)
        return exp if exp is not None and exp <= dt_util.utcnow() else None

    def _status(self, g: dict[str, Any]) -> str:
        if g.get('revoked'):
            return 'revoked'
        exp = self._end(g)
        return 'expired' if exp is None or exp <= dt_util.utcnow() else 'active'

    def answer(self, user: Any=None) -> dict[str, Any]:
        self._prune()
        names = {a.id: a.name for a in ar.async_get(self.hass).async_list_areas()}
        return {'grants': [{'id': g['id'], 'label': g.get('label') or '', 'rooms': [{'id': r, 'name': names.get(r, r)} for r in g.get('rooms', [])], 'count': len(g.get('entities', [])), 'by': g.get('by') or '', 'created': g.get('created'), 'expires': g.get('expires'), 'revoked': g.get('revoked'), 'status': self._status(g), 'devices': len(g.get('devices', [])), 'max_devices': g.get('max_devices', default_devices())} for g in self.grants if g.get('kind') != PAGES], 'pages': [{'id': g['id'], 'label': g.get('label') or '', 'username': g.get('username') or '', 'pages': [dict(x) for x in g.get('pages', [])], 'by': g.get('issued_by') or '', 'created': g.get('created'), 'until': g.get('until'), 'revoked': g.get('revoked'), 'status': self._status(g), 'role': g.get('role')} for g in self.grants if g.get('kind') == PAGES], 'pages_hours': pages_hours(), 'pages_roles': [{'id': k, **v} for k, v in roles().items()], 'log': self.log[:100], 'can_issue': user is not None and issuer_ok(user)}

    async def issue(self, user: Any, body: dict[str, Any]) -> dict[str, Any]:
        if not issuer_ok(user):
            raise Refused('not_issuer', HTTPStatus.FORBIDDEN)
        label = clean(body.get('label'), 40)
        rooms = body.get('rooms')
        if not isinstance(rooms, list) or not 1 <= len(rooms) <= max_rooms() or (not all((isinstance(r, str) for r in rooms))):
            raise Invalid('rooms: a list of rooms')
        rooms = list(dict.fromkeys(rooms))
        areas = ar.async_get(self.hass)
        if any((areas.async_get_area(r) is None for r in rooms)):
            raise Refused('no_room')
        hours = body.get('hours')
        if isinstance(hours, bool) or not isinstance(hours, int) or (not 1 <= hours <= max_hours()):
            raise Invalid(f'hours: 1…{max_hours()}')
        devices = body.get('max_devices', default_devices())
        if isinstance(devices, bool) or not isinstance(devices, int) or (not 1 <= devices <= max_devices()):
            raise Invalid(f'max_devices: 1…{max_devices()}')
        entities = [e for r in rooms for e in self.candidates(r) if may_control(user, e)]
        if not entities:
            raise Refused('nothing')
        async with self.lock:
            self._prune()
            if sum((1 for g in self.grants if g.get('kind') != PAGES and self._status(g) == 'active')) >= max_active():
                raise Refused('too_many_links', n=max_active())
            gid, secret = (secrets.token_urlsafe(9), secrets.token_urlsafe(24))
            now = dt_util.utcnow()
            grant = {'id': gid, 'hash': secret_hash(secret), 'label': label, 'rooms': rooms, 'entities': sorted(set(entities)), 'by': user.name or '', 'by_id': user.id, 'created': iso(now), 'expires': iso(now + dt.timedelta(hours=hours)), 'revoked': None, 'devices': [], 'max_devices': devices}
            self.grants.insert(0, grant)
            self.log.insert(0, {'t': iso(now), 'sys': 'issued', 'grant': gid, 'label': label, 'by': user.name or '', 'hours': hours})
            await self.store.async_save(self._data())
        link = f'{self.link_base()}#{gid}.{secret}'
        return {'id': gid, 'link': link, 'qr': [''.join(('1' if v else '0' for v in row)) for row in qr.encode(link)], 'expires': grant['expires'], 'count': len(grant['entities'])}

    async def revoke(self, user: Any, grant_id: Any=None) -> int:
        """One link (`grant_id`) or every one still open (None). Any person of the house may — the safe direction."""
        n = 0
        async with self.lock:
            now = iso(dt_util.utcnow())
            one = next((g for g in self.grants if g['id'] == grant_id), None)
            if grant_id is not None and one is None:
                raise Refused('no_link')
            for g in self.grants:
                if (grant_id is None or g['id'] == grant_id) and self._status(g) == 'active':
                    g['revoked'], g['revoked_by'] = (now, getattr(user, 'name', '') or '')
                    n += 1
            label = (one or {}).get('label') or ''
            what = ('pages_revoked' if one.get('kind') == PAGES else 'revoked') if one else 'revoked_all'
            self.log.insert(0, {'t': now, 'sys': what, 'grant': grant_id, 'label': label, 'by': getattr(user, 'name', '') or '', 'n': n})
            del self.log[log_max():]
            for g in self.grants:
                if g.get('kind') == PAGES and g.get('revoked') and (not g.get('removed')):
                    await self._remove_user(g)
            await self.store.async_save(self._data())
        await self.sync()
        return n

    def _mark(self) -> None:
        self.hass.data[GUEST_USERS] = {g['user_id'] for g in self.grants if g.get('kind') == PAGES and isinstance(g.get('user_id'), str) and (not g.get('removed'))} | self._pending

    async def access(self) -> Access:
        """Who sees which page now (dashmerge.Access): every active person of HA but the guests; the guests given a
        page."""
        users = await self.hass.auth.async_get_users()
        present = {u.id for u in users}
        self._mark()
        known = {g['user_id'] for g in self.grants if g.get('kind') == PAGES and isinstance(g.get('user_id'), str)} | self._pending
        live = [g for g in self.grants if g.get('kind') == PAGES and self._status(g) == 'active' and (not g.get('removed')) and (g.get('user_id') in present)]
        family = sorted((u.id for u in users if u.is_active and (not u.system_generated) and (u.id not in known)))
        pages: dict[str, dict[str, list[str]]] = {}
        for g in live:
            for x in g.get('pages', []):
                pages.setdefault(x['dashboard'], {}).setdefault(x['view'], []).append(g['user_id'])
        return Access(family, known, pages, bool(live))

    async def available(self) -> list[dict[str, Any]]:
        if self.dashboards is None:
            return []
        with contained(_LOGGER, 'Hauflo guest: pages not readable'):
            return await self.dashboards.page_list()
        return []
