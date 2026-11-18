"""The presence simulation engine: rooms, arming and settings."""
from __future__ import annotations
from collections import deque
from collections.abc import Callable
import datetime as dt
import logging
import random
import time
from typing import Any
import astral
import astral.sun
from homeassistant.core import CALLBACK_TYPE, CoreState, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.helpers.event import async_track_point_in_time, async_track_time_change
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util
from ..automations.common import UNSET
from ..ownership import entity_room
from ..storage import HaufloStore
from .common import DATE, DEFAULT, MODES, STORE_KEY, Invalid, Refused, brief, jitter, max_rooms, max_skip, max_until_days, min_evenings, replan, watch_off
from .firing import PresenceFiringMixin
from .lights import PresenceLightsMixin
_LOGGER = logging.getLogger(__name__)

class Presence(PresenceLightsMixin, PresenceFiringMixin):

    def __init__(self, hass: HomeAssistant, owns: Callable[[str], bool]) -> None:
        self.hass = hass
        self.owns = owns
        self.store = HaufloStore(hass, STORE_KEY)
        self.data: dict[str, Any] = {**DEFAULT, 'log': [], 'lit': [], 'lit_at': {}, 'ours': []}
        self.rng = random.Random()
        self.status = 'off'
        self.tonight: dict[str, Any] | None = None
        self.pending: dict[int, tuple[CALLBACK_TYPE, dict[str, Any]]] = {}
        self._n = 0
        self._daily: CALLBACK_TYPE | None = None
        self._off: CALLBACK_TYPE | None = None
        self._ctx: deque[str] = deque(maxlen=400)
        self._sent: dict[str, float] = {}
        self._waits: dict[str, CALLBACK_TYPE] = {}
        self._claims: dict[str, CALLBACK_TYPE] = {}
        self._outs: dict[str, CALLBACK_TYPE] = {}
        self._started = False
        self._t0: float | None = None
        self._boot: float | None = None

    def _pickable(self, eid: str) -> bool:
        if eid.split('.')[0] not in ('light', 'cover') or self.hass.states.get(eid) is None:
            return False
        ent = er.async_get(self.hass).async_get(eid)
        if ent is None or ent.disabled_by or ent.hidden_by or (ent.platform == 'group'):
            return False
        return bool(self.owns(eid))

    def candidates(self, every: bool=False) -> dict[str, dict[str, Any]]:
        """The lights / blinds that take part (`every`: whatever is switched off on the page too)."""
        out: dict[str, dict[str, Any]] = {}
        for domain, on in (('light', self.data['lights']), ('cover', self.data['covers'])):
            if not on and (not every):
                continue
            for eid in self.hass.states.async_entity_ids(domain):
                if not self._pickable(eid):
                    continue
                a = self.hass.states.get(eid).attributes
                modes = a.get('supported_color_modes') or []
                out[eid] = {'domain': domain, 'dim': domain == 'light' and any((m != 'onoff' for m in modes)), 'pos': domain == 'cover' and bool(int(a.get('supported_features') or 0) & 4)}
        return out

    def rooms(self, eids: list[str]) -> dict[str, tuple[str, str]]:
        areas = ar.async_get(self.hass)
        out = {}
        for eid in eids:
            area_id = entity_room(self.hass, eid)
            area = areas.async_get_area(area_id) if area_id else None
            st = self.hass.states.get(eid)
            out[eid] = (area.id, area.name) if area else (eid, (st.attributes.get('friendly_name') if st else None) or eid)
        return out

    def _sun(self, day: dt.date) -> tuple[dt.datetime, dt.datetime] | None:
        c = self.hass.config
        if (round(float(c.latitude), 5), round(float(c.longitude), 5)) in UNSET:
            return None
        obs = astral.Observer(c.latitude, c.longitude, c.elevation)
        tz = dt_util.get_time_zone(c.time_zone) or dt_util.get_default_time_zone()
        try:
            return (astral.sun.sunrise(obs, date=day, tzinfo=tz), astral.sun.sunset(obs, date=day, tzinfo=tz))
        except ValueError:
            return None

    async def async_start(self) -> None:
        stored = await self.store.async_load()
        if isinstance(stored, dict):
            self.data = {**DEFAULT, **{k: v for k, v in stored.items() if k in DEFAULT}}
            for k in ('log', 'lit', 'ours', 'skip_rooms'):
                if not isinstance(self.data.get(k), list):
                    self.data[k] = []
            if not isinstance(self.data.get('lit_at'), dict):
                self.data['lit_at'] = {}
            self.data['skip_rooms'] = [x for x in self.data['skip_rooms'] if isinstance(x, str)][:max_skip()]
            if self.data.get('mode') not in MODES:
                self.data['mode'] = 'learned'
        self._started = True
        if self.hass.state is not CoreState.running:
            self._t0 = time.time()

            @callback
            def _up(_hass: HomeAssistant) -> None:
                self._boot = time.time()
            async_at_started(self.hass, _up)
        if not self.data['enabled']:
            if self.data['lit']:

                @callback
                def _out(_hass: HomeAssistant) -> None:
                    if self._started and (not self.data['enabled']):
                        self.hass.async_create_task(self._lights_out_saved(), eager_start=True)
                async_at_started(self.hass, _out)
            return
        if self._expired():
            await self._switch_off('auto_off')
            return

        @callback
        def _go(_hass: HomeAssistant) -> None:
            if self._started and self.data['enabled']:
                self.hass.async_create_task(self._arm(), eager_start=True)
        async_at_started(self.hass, _go)

    @callback
    def async_stop(self) -> None:
        self._started = False
        self._cancel()
        self._stop_outs()

    def _cancel(self) -> None:
        for unsub, _ in self.pending.values():
            unsub()
        self.pending.clear()
        for unsub in list(self._waits.values()):
            unsub()
        self._waits.clear()
        for unsub in list(self._claims.values()):
            unsub()
        self._claims.clear()
        for name in ('_daily', '_off'):
            if (unsub := getattr(self, name)) is not None:
                unsub()
                setattr(self, name, None)

    def _off_at(self) -> dt.datetime | None:
        until = self.data.get('until')
        if not isinstance(until, str) or not DATE.fullmatch(until):
            return None
        return dt_util.start_of_local_day(dt.date.fromisoformat(until) + dt.timedelta(days=1))

    def _expired(self) -> bool:
        off = self._off_at()
        return off is not None and dt_util.now() >= off

    async def _arm(self) -> None:
        self._cancel()
        if not self._started or not self.data['enabled']:
            return
        if self._expired():
            await self._switch_off('auto_off')
            return
        self._daily = async_track_time_change(self.hass, self._replan, hour=replan()[0], minute=replan()[1], second=0)
        if (off := self._off_at()) is not None:
            self._off = async_track_point_in_time(self.hass, self._auto_off, off)
        self._stop_outs()
        await self.plan()
        soon = dt_util.now() + dt.timedelta(minutes=20)
        for eid in list(self.data['lit']):
            self._remember(eid)
            st = self.hass.states.get(eid)
            if st is None or st.state != 'on':
                self._claim(eid)
            nxt = min((ev['at'] for _, ev in self.pending.values() if ev['entity_id'] == eid), default=None)
            if nxt is not None and nxt <= soon:
                continue
            if st is not None and st.state == 'on':
                self._schedule({'at': dt_util.now() + dt.timedelta(minutes=self.rng.uniform(2, 10)), 'entity_id': eid, 'do': 'off', 'value': None})
            else:
                self._wait(eid, dt_util.now() + dt.timedelta(seconds=watch_off()))
        if self.data['lit']:
            self.store.async_delay_save(lambda: self.data, 5)

    async def _replan(self, _now: dt.datetime) -> None:
        if self._expired():
            await self._switch_off('auto_off')
            return
        await self.plan()

    async def _auto_off(self, _now: dt.datetime) -> None:
        self._off = None
        await self._switch_off('auto_off')

    async def set(self, body: dict[str, Any], user: str | None) -> dict[str, Any]:
        new = dict(self.data)
        for key in ('enabled', 'lights', 'covers'):
            if key in body:
                if not isinstance(body[key], bool):
                    raise Invalid(f'{key}: true or false')
                new[key] = body[key]
        if 'mode' in body:
            if body['mode'] not in MODES:
                raise Invalid('mode: learned or sun')
            new['mode'] = body['mode']
        today = dt_util.now().date()
        if 'until' in body:
            v = body['until']
            if v in (None, ''):
                new['until'] = None
            else:
                if not isinstance(v, str) or not DATE.fullmatch(v):
                    raise Invalid('until: YYYY-MM-DD')
                try:
                    day = dt.date.fromisoformat(v)
                except ValueError as err:
                    raise Invalid('until: YYYY-MM-DD') from err
                if not today <= day <= today + dt.timedelta(days=max_until_days()):
                    raise Refused('bad_until', n=max_until_days())
                new['until'] = v
        elif new['enabled'] and (not self.data['enabled']) and new.get('until') and (new['until'] < today.isoformat()):
            new['until'] = None
        if 'skip_rooms' in body:
            v = body['skip_rooms']
            if not isinstance(v, list) or len(v) > max_skip() or (not all((isinstance(x, str) and 0 < len(x) <= 255 for x in v))):
                raise Invalid('skip_rooms: a list of room ids')
            new['skip_rooms'] = list(dict.fromkeys(v))
        if not new['lights'] and (not new['covers']):
            raise Refused('lights_or_covers')
        if new['enabled'] and (not self.data['enabled']):
            new['since'], new['by'] = (dt_util.now().isoformat(timespec='seconds'), user)
        turned = new['enabled'] != self.data['enabled']
        self.data = new
        if turned and (not new['enabled']):
            self._cancel()
            await self._lights_out()
        if turned:
            self._log({'sys': 'on' if new['enabled'] else 'off', **({'by': user} if user else {})})
        await self.store.async_save(self.data)
        if new['enabled']:
            await self._arm()
        else:
            self._cancel()
            self.status, self.tonight = ('off', None)
        return self.answer()

    def answer(self) -> dict[str, Any]:
        info = self.candidates(every=True)
        nxt = sorted((ev for _, ev in self.pending.values()), key=lambda e: e['at'])
        name = lambda eid: (s.attributes.get('friendly_name') if (s := self.hass.states.get(eid)) else None) or eid
        off = self._off_at()
        skip = set(self.data.get('skip_rooms') or [])
        where = self.rooms(sorted(info))
        rooms: dict[str, dict[str, Any]] = {}
        for eid, meta in info.items():
            key, room_name = where[eid]
            if key == eid:
                continue
            r = rooms.setdefault(key, {'id': key, 'name': room_name, 'lights': 0, 'covers': 0, 'on': key not in skip})
            r['lights' if meta['domain'] == 'light' else 'covers'] += 1
        return {**{k: self.data[k] for k in ('enabled', 'until', 'lights', 'covers', 'mode', 'since')}, 'skip_rooms': list(self.data.get('skip_rooms') or []), 'rooms': sorted(rooms.values(), key=lambda r: str(r['name']).lower()), 'status': self.status if self.data['enabled'] else 'off', 'tonight': self.tonight if self.data['enabled'] else None, 'left': len(nxt), 'next': [{**brief(e), 'at': e['at'].isoformat(timespec='seconds'), 'name': name(e['entity_id'])} for e in nxt[:5]], 'log': self.data['log'], 'off_at': off.isoformat() if off else None, 'devices': {'lights': sum((1 for v in info.values() if v['domain'] == 'light')), 'covers': sum((1 for v in info.values() if v['domain'] == 'cover'))}, 'min_evenings': min_evenings(), 'max_rooms': max_rooms(), 'jitter_min': jitter()}

    @property
    def own_contexts(self) -> deque[str]:
        """The contexts of this engine's own commands (the habits recorder tells them from a person's)."""
        return self._ctx

    def sent_at(self, entity_id: str) -> float | None:
        """When this engine last commanded the entity (the echo window of the habits recorder)."""
        sent = self._sent.get(entity_id)
        at = sent[0] if isinstance(sent, tuple) else sent
        return at if isinstance(at, (int, float)) else None
