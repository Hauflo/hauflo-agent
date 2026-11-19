"""Door rings: who is called, with which camera and link."""
from __future__ import annotations
from collections.abc import Callable
import json
import logging
import time
from typing import Any
from homeassistant.components.lovelace.const import LOVELACE_DATA, ConfigNotFound
from homeassistant.core import Event, State, callback
from homeassistant.helpers import entity_registry as er, translation
from homeassistant.util import dt as dt_util
from ..const import DOMAIN
from ..guard import contained
from .common import c_sos, event_kind, ring, ring_fresh, ring_kinds, ring_window, watch_kinds
from .levels import labelled
_LOGGER = logging.getLogger(__name__)

class NotifyRingMixin:
    """Sends and clears door rings."""

    @callback
    def _on_event_entity(self, event: Event) -> None:
        new: State | None = event.data.get('new_state')
        old: State | None = event.data.get('old_state')
        if new is None or (old is not None and old.state == new.state):
            return
        at = dt_util.parse_datetime(new.state)
        if at is None or at.tzinfo is None or abs((dt_util.utcnow() - at).total_seconds()) > ring_fresh():
            return
        eid = new.entity_id
        kind = event_kind(new)
        if self.lock_owns is not None and self.lock_owns(eid) and (kind not in watch_kinds()):
            return
        said = str(new.attributes.get('event_type') or '').lower()
        if said in ring()['clear_event_types'] or said in ring()['skip_event_types']:
            if said in ring()['clear_event_types']:
                for pushed in self._pushed_with(eid):
                    self.hass.async_create_task(self._clear_ring(pushed))
            return
        if not (self.watches(eid) if self.watches is not None else labelled(self.hass, eid)):
            return
        now, visit = (time.monotonic(), self._visit(eid, kind))
        first = next((e for e, t in self._rang.items() if now - t < ring_window() and self._visits.get(e) == visit), None)
        if first is not None:
            self._said[eid] = (new.state, self._said[first][1] if first == eid and first in self._said else '')
            return
        self._rang[eid], self._visits[eid], self._said[eid] = (now, visit, (new.state, new.state))
        self.hass.async_create_task(self._send_ring(new))

    def _device(self, eid: str) -> str | None:
        entry = er.async_get(self.hass).async_get(eid)
        return entry.device_id if entry is not None and entry.device_id else None

    def _visit(self, eid: str, kind: str) -> str:
        return f'{dev}|{kind}' if kind in ring_kinds() and (dev := self._device(eid)) else eid

    def _pushed_with(self, eid: str) -> list[str]:
        dev = self._device(eid)
        return [e for e in self._rang if e == eid or (dev and self._visits.get(e, '').startswith(f'{dev}|'))]

    def ring_said(self, state: State) -> str | None:
        if str(state.attributes.get('event_type') or '').lower() in (*ring()['clear_event_types'], *ring()['skip_event_types']):
            return None
        last, visit = self._said.get(state.entity_id, ('', ''))
        return visit or None if last == state.state else state.state

    async def _send_ring(self, state: State) -> None:
        name, where = (self.name_of(state.entity_id), self._where(state))
        kind = event_kind(state)
        got = {}
        with contained(_LOGGER, 'Hauflo: ring texts not loaded — the name stands in for them'):
            got = await translation.async_get_translations(self.hass, 'en' if self.english() else 'ru', 'exceptions', [DOMAIN])

        def word(key: str, fallback: str) -> str:
            return got.get(f'component.{DOMAIN}.exceptions.{key}.message') or fallback
        what = word(f'event_push_{kind}', '{name}').format(name=name)
        head = word('event_push_title' if where else 'event_push_title_bare', '{what}').format(what=what, where=where)
        message = word(f'event_push_{kind}_text', '{name}').format(name=name)
        critical = kind in c_sos()
        rule = {'id': 'event', 'title': name, 'priority': 'critical' if critical else 'normal', 'recipients': {'all': True, 'people': [], 'groups': [], 'phones': []}}
        silent = not critical and bool(self.quiet_now and self.quiet_now())
        extra = await self._ring_extra(state, urgent=not silent) if kind in ring_kinds() and (not critical) else None
        await self.send(rule, head, message, tag=self.tag(f'event|{state.entity_id}'), critical=critical, silent=silent, targets=self.ring_targets(), extra=extra, lane='event')

    async def _ring_extra(self, state: State, *, urgent: bool) -> Callable[[str | None], dict[str, Any]]:
        camera = self._door_camera(state.entity_id)
        link = await self._door_link({state.entity_id, *([camera] if camera else [])})

        def extra(os: str | None) -> dict[str, Any]:
            out: dict[str, Any] = {}
            if camera:
                out['image'] = f'/api/camera_proxy/{camera}'
                if os == 'ios':
                    out['entity_id'] = camera
            if link:
                out['url' if os == 'ios' else 'clickAction'] = link
            if urgent:
                out.update({'push': dict(ring()['ios'])} if os == 'ios' else ring()['android'])
            return out
        return extra

    def _door_camera(self, entity_id: str) -> str | None:
        reg = er.async_get(self.hass)
        entry = reg.async_get(entity_id)
        if entry is None or not entry.device_id:
            return None
        cams = sorted((e.entity_id for e in er.async_entries_for_device(reg, entry.device_id) if e.domain == 'camera' and e.translation_key not in ring()['skip_camera_keys']))
        return cams[0] if cams else None

    async def _door_link(self, ids: set[str]) -> str | None:
        try:
            dashboards = list(self.hass.data[LOVELACE_DATA].dashboards.items())
        except (ImportError, KeyError):
            return None
        for url_path, dash in dashboards:
            if not str(url_path or '').startswith('hauflo-') or getattr(dash, 'mode', 'storage') != 'storage':
                continue
            try:
                config = await dash.async_load(False)
            except ConfigNotFound:
                continue
            except Exception:
                _LOGGER.warning('Hauflo: dashboard %s not readable: skipped for the ring link', url_path, exc_info=True)
                continue
            views = config.get('views') if isinstance(config, dict) else None
            hits = [(i, v) for i, v in enumerate(views or []) if isinstance(v, dict) and any((x in json.dumps(v) for x in ids))]
            if hits:
                i, view = hits[1] if len(hits) > 1 else hits[0]
                return f"/{url_path}/{view.get('path') or i}"
        return None

    async def _clear_ring(self, entity_id: str) -> None:
        self._rang.pop(entity_id, None)
        self._visits.pop(entity_id, None)
        rule = {'id': 'event', 'title': '', 'priority': 'normal', 'recipients': {'all': True, 'people': [], 'groups': [], 'phones': []}}
        await self.send(rule, '', 'clear_notification', tag=self.tag(f'event|{entity_id}'), critical=False, targets=self.ring_targets(), force=True, lane='event')

    @callback
    def _on_intercom(self, event: Event) -> None:
        call = event.data.get('call')
        if event.data.get('state') in ring()['clear_states'] and isinstance(call, str) and (call in self._rang):
            self.hass.async_create_task(self._clear_ring(call))

    def rings_on(self, phone: dict[str, Any], config: dict[str, Any] | None=None) -> bool:
        """«Пуш о звонке» of a phone: its own, else its person's, else «*»; not said — on unless its alarms are «off»."""
        rings = (config or self.config).get('rings') or {}
        for key in (phone['service'], phone.get('person'), '*'):
            if key and isinstance(rings.get(key), bool):
                return rings[key]
        return self.scope(phone, config) != 'off'

    def ring_targets(self, config: dict[str, Any] | None=None) -> list[dict[str, Any]]:
        return [p for p in self.phones() if self.rings_on(p, config)]
