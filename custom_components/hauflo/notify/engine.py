"""The alarm engine: rules, places and status."""
from __future__ import annotations
from collections.abc import Callable
import logging
from typing import Any
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util
from ..const import NATIVE_STORE
from ..foreign import flags as foreign_flags
from ..natural import natural_key
from ..ownership import entity_room
from ..storage import HaufloStore
from .common import ACTION_EVENT, ACTIVE_KEY, INTERCOM_EVENT, STORE_KEY, LockedChange, alarm_classes, fault_name, heartbeat, label, levels, list_max, outputs_cfg
from .delivery import NotifyDeliveryMixin
from .firing import NotifyFiringMixin
from .levels import labelled, level_text, output_ok
from .native import NotifyNativeMixin
from .push import automation_event, event_entity
from .repeat import NotifyRepeatMixin
from .ring import NotifyRingMixin
from .rules import clean_rules, devices, narrowing
_LOGGER = logging.getLogger(__name__)

class NotifyEngine(NotifyRingMixin, NotifyFiringMixin, NotifyRepeatMixin, NotifyNativeMixin, NotifyDeliveryMixin):

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: HaufloStore = HaufloStore(hass, STORE_KEY)
        self.active_store: HaufloStore = HaufloStore(hass, ACTIVE_KEY)
        self.config: dict[str, Any] = {'rules': [], 'groups': []}
        self.ready = False
        self._unsub_state = None
        self._unsub_action = None
        self._unsub_events = None
        self._unsub_intercom: Callable[[], None] | None = None
        self._rang: dict[str, float] = {}
        self._visits: dict[str, str] = {}
        self._said: dict[str, tuple[str, str]] = {}
        self.watches: Callable[[str], bool] | None = None
        self.quiet_now: Callable[[], bool] | None = None
        self.lock_owns: Callable[[str], bool] | None = None
        self.active: dict[str, dict[str, Any]] = {}
        self.groups: dict[str, dict[str, Any]] = {}
        self._restored: dict[str, dict[str, Any]] = {}
        self._pending_start: dict[str, list[str]] = {}
        self._pending_clear: dict[str, list[tuple[str, dict[str, Any], State]]] = {}
        self._pending_nodata: dict[str, list[str]] = {}
        self._pending_back: dict[str, list[str]] = {}
        self._nodata_timers: dict[str, Callable[[], None]] = {}
        self._flaps: dict[str, list[float]] = {}
        self.unacked_rtn: dict[str, dict[str, Any]] = {}
        self.house_acks: dict[str, str] = {}
        self._chatter: dict[str, Callable[[], None]] = {}
        self._holding: dict[str, Callable[[], None]] = {}
        self._flush_unsub = None
        self._late_kicks: dict[str, Callable[[], None]] = {}
        self.sent_log: list[dict[str, Any]] = []
        self.on_change = None
        self.owns: Callable[[str], bool] | None = None
        self.locked: Callable[[], bool] | None = None
        self._seen_ok: set[str] = set()
        self._pushes: dict[str, list[float]] = {}
        self.limited: dict[str, str] = {}
        self.throttled: dict[str, str] = {}
        self._tested: dict[str, float] = {}
        self._tests: list[float] = []
        self.native: dict[str, dict[str, Any]] = {}
        self.native_store: HaufloStore = HaufloStore(hass, NATIVE_STORE)
        self._unsub_helper = None
        self._native_who: dict[str, str] = {}
        self._native_first: set[str] = set()
        self._missed: dict[str, Any] = {}
        self._missed_src: dict[str, list[str]] = {}
        self._unsub_automations = None
        self._sounding: dict[str, set[str]] = {}

    @callback
    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    async def async_start(self) -> None:
        stored = await self.store.async_load()
        self.config = clean_rules(stored, self.hass, self.owns)
        self.ready = stored is not None
        saved = await self.active_store.async_load() or {}
        self._seen_ok = {str(x) for x in saved.get('seen_ok') or []}
        self._pushes = {str(k): [float(t) for t in v if isinstance(t, (int, float))] for k, v in (saved.get('pushes') or {}).items() if isinstance(v, list)}
        self.unacked_rtn = {str(k): v for k, v in (saved.get('rtn') or {}).items() if isinstance(v, dict)}
        self.house_acks = {str(k): str(v)[:80] for k, v in (saved.get('house_acks') or {}).items() if isinstance(v, str) and v}
        self._restored = {k: v for k, v in (saved.get('active') or {}).items() if isinstance(v, dict)}
        for rule_id, g in (saved.get('groups') or {}).items():
            if isinstance(g, dict):
                self.groups[rule_id] = {'since': g.get('since'), 'acked_by': g.get('acked_by'), 'count': int(g.get('count') or 1), 'repeat': None, 'last': g.get('last')}
        saved_native = await self.native_store.async_load()
        self.native = {str(k): v for k, v in (saved_native or {}).items() if isinstance(v, dict) and v.get('automation') and v.get('helper')}
        self._unsub_action = self.hass.bus.async_listen(ACTION_EVENT, self._on_action)
        self._watch_helpers()
        self._unsub_automations = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_automation, event_filter=automation_event)
        self._unsub_events = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_event_entity, event_filter=event_entity)
        self._unsub_intercom = self.hass.bus.async_listen(INTERCOM_EVENT, self._on_intercom)
        self._apply()
        self._missed_src = {}
        for key, inc in self._restored.items():
            if not inc.get('nodata'):
                self._missed_src.setdefault(key.partition('|')[0], []).append(key.partition('|')[2])

        async def _started(_hass: HomeAssistant) -> None:
            for rule_id in list(self.native):
                self._watch_missed(rule_id)
        async_at_started(self.hass, _started)

    @callback
    def async_stop(self) -> None:
        if self._unsub_events:
            self._unsub_events()
            self._unsub_events = None
        if self._unsub_intercom:
            self._unsub_intercom()
            self._unsub_intercom = None
        if self._unsub_automations:
            self._unsub_automations()
            self._unsub_automations = None
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_action:
            self._unsub_action()
            self._unsub_action = None
        if self._unsub_helper:
            self._unsub_helper()
            self._unsub_helper = None
        if self._flush_unsub:
            self._flush_unsub()
            self._flush_unsub = None
        for cancel in [*self._nodata_timers.values(), *self._chatter.values(), *self._holding.values(), *self._missed.values(), *self._late_kicks.values()]:
            cancel()
        self._late_kicks.clear()
        self._nodata_timers.clear()
        self._chatter.clear()
        self._holding.clear()
        self._missed.clear()
        snapshot = self._snapshot()
        self.active_store.async_delay_save(lambda: snapshot, 0)
        for inc in [*self.active.values(), *self.groups.values()]:
            if inc.get('repeat'):
                inc['repeat']()
        self.active.clear()
        self.groups.clear()

    def check(self, raw: Any) -> dict[str, Any]:
        config = clean_rules(raw, self.hass, self.owns)
        if self.locked is not None and self.locked() and (why := narrowing(config, self.config)):
            raise LockedChange(why)
        return config

    @callback
    def policy_changed(self) -> bool:
        """The site's policy changed (signals.py): each rule's overlay and hold taken again; True — a rule HA runs as its own automation now needs the engine (its automation is written again by the caller)."""
        before = {r['id']: (r.get('policy'), r['hold'], r.get('holds')) for r in self.config['rules']}
        self.config = clean_rules(self.config, self.hass, self.owns)
        after = {r['id']: (r.get('policy'), r['hold'], r.get('holds')) for r in self.config['rules']}
        if before == after:
            return False
        self._apply()
        self._changed()
        return any((rid in self.native and before.get(rid) != after[rid] for rid in after))

    async def async_set(self, raw: Any) -> dict[str, Any]:
        config = self.check(raw)
        self.config = config
        self.ready = True
        await self.store.async_save(self.config)
        self._apply()
        self._changed()
        return self.config

    async def async_remove(self) -> None:
        await self.store.async_remove()

    def _snapshot(self) -> dict[str, Any]:
        keep = ('since', 'acked_by', 'count', 'group', 'own', 'last', 'pushed')
        return {'active': {**self._restored, **{k: {f: v.get(f) for f in keep} for k, v in self.active.items()}}, 'groups': {r: {f: g.get(f) for f in ('since', 'acked_by', 'count', 'last')} for r, g in self.groups.items()}, 'seen_ok': sorted(self._seen_ok), 'rtn': self.unacked_rtn, 'pushes': self._pushes, 'house_acks': self.house_acks}

    @callback
    def _save(self) -> None:
        self.active_store.async_delay_save(self._snapshot, 2)

    def where(self, state: State) -> str:
        """The room's name of the entity (the one counter lists it)."""
        return self._where(state)

    def _where(self, state: State) -> str:
        area_id = entity_room(self.hass, state.entity_id)
        area = ar.async_get(self.hass).async_get_area(area_id) if area_id else None
        return area.name if area else ''

    def name_of(self, entity_id: str) -> str:
        return label(self.hass, entity_id)

    def _where_list(self, keys: list[str], rule: dict[str, Any] | None=None) -> str:
        by: dict[str, list[str]] = {}
        for k in keys:
            entity_id = k.partition('|')[2]
            state = self.hass.states.get(entity_id)
            by.setdefault(self._where(state) if state else '', []).append(self.name_of(entity_id) + (level_text(rule, state) if rule else ''))
        parts, shown = ([], 0)
        for area in sorted(by, key=lambda a: (a == '', natural_key(a))):
            if shown >= list_max():
                break
            names = by[area][:list_max() - shown]
            shown += len(names)
            parts.append((f'{area}: ' if area else '') + ', '.join(names))
        rest = len(keys) - shown
        return '; '.join(parts) + (f' и ещё {rest}' if rest > 0 else '')

    @callback
    def _adopt(self, rule: dict[str, Any], key: str) -> None:
        saved = self._restored.pop(key)
        self.active[key] = {'since': saved.get('since') or dt_util.utcnow().isoformat(), 'acked_by': saved.get('acked_by'), 'repeat': None, 'count': int(saved.get('count') or 1), 'group': bool(saved.get('group')), 'own': bool(saved.get('own')), 'last': saved.get('last'), 'pushed': bool(saved.get('pushed'))}
        group = self.groups.get(rule['id'])
        if self.active[key]['group'] and group:
            if not group['acked_by'] and (not group.get('repeat')):
                self._schedule_group(rule)
        elif not self.active[key]['acked_by']:
            self._schedule(rule, key)
        if not self.active[key]['acked_by']:
            if self.active[key]['group']:
                members = [k for k, inc in self.active.items() if k.startswith(rule['id'] + '|') and inc.get('group')]
                self._note(f'Открыто: {self._where_list(members)}.', title=f"🚨 {rule['title']} — {len(members)} {devices(len(members))}", notification_id=self._gtag(rule['id']), rule=rule)
            elif (state := self.hass.states.get(key.partition('|')[2])) is not None:
                where = self._where(state)
                self._note(f'{self.name_of(state.entity_id)}: авария.', title=f"🚨 {rule['title']}" + (f' — {where}' if where else ''), notification_id=self.tag(key), rule=rule)
        self._save()

    def _link_unknown(self, rule: dict[str, Any], state: State) -> bool:
        if rule['kind'] not in ('connection', 'link', 'connectivity'):
            return False
        return state.entity_id not in self._seen_ok

    def active_list(self) -> list[dict[str, Any]]:
        out = []
        known = {r['id'] for r in self.config['rules']}
        pending = {k: v for k, v in self._restored.items() if k.partition('|')[0] in known}
        for key, inc in [*self.active.items(), *pending.items()]:
            rule_id, _, entity_id = key.partition('|')
            rule = next((r for r in self.config['rules'] if r['id'] == rule_id), None)
            state = self.hass.states.get(entity_id)
            out.append({'key': key, 'entityId': entity_id, 'title': rule['title'] if rule else 'Авария', 'name': label(self.hass, entity_id, state), 'since': inc['since'], 'ackedBy': inc.get('acked_by'), 'area': self._where(state) if state else '', 'kind': rule['kind'] if rule else 'custom', 'critical': bool(rule and rule['priority'] == 'critical') and (not inc.get('nodata')), **({'noData': True} if inc.get('nodata') else {})})
        return out

    def alarm_items(self) -> list[dict[str, Any]]:
        """The alarms the one counter (alarms_now.py) reads: the open ones (active_list) and those back to normal that nobody accepted yet (`returned` — the alert card asks for a look), with their names and rooms."""
        out = self.active_list()
        for key, v in self.unacked_rtn.items():
            if key in self.active:
                continue
            rule_id, _, entity_id = key.partition('|')
            state = self.hass.states.get(entity_id)
            rule = next((r for r in self.config['rules'] if r['id'] == rule_id), None)
            out.append({'key': key, 'entityId': entity_id, 'title': v.get('title') or (rule['title'] if rule else ''), 'name': label(self.hass, entity_id, state), 'since': v.get('since'), 'ackedBy': None, 'area': self._where(state) if state else '', 'critical': bool(rule and rule['priority'] == 'critical'), 'returned': True})
        return out

    def status(self) -> dict[str, Any]:
        ents = self.hass.states.async_all(['binary_sensor', 'sensor'])
        candidates = []
        own = {e.entity_id for e in er.async_get(self.hass).entities.values() if e.platform in ('hauflo', 'mobile_app')}
        for s in ents:
            if s.entity_id in own:
                continue
            dc = s.attributes.get('device_class')
            if dc is None and s.domain == 'binary_sensor' and heartbeat().search(s.attributes.get('friendly_name') or s.entity_id):
                dc = 'connectivity'
            elif dc is None and s.domain == 'binary_sensor' and (fault_name().search(s.attributes.get('friendly_name') or '') or fault_name().search(s.entity_id) or labelled(self.hass, s.entity_id)):
                dc = 'problem'
            if dc in alarm_classes() or (s.domain == 'sensor' and dc in levels()):
                if foreign_flags.notify_flag(self.hass, s.entity_id) is False and (not (self.owns and self.owns(s.entity_id))):
                    continue
                entry = er.async_get(self.hass).async_get(s.entity_id)
                candidates.append({'entity_id': s.entity_id, 'name': label(self.hass, s.entity_id, s), 'device_class': dc, 'area': self._where(s), 'state': s.state, 'own': bool(self.owns and self.owns(s.entity_id)), 'monitored': labelled(self.hass, s.entity_id), 'platform': entry.platform if entry else '', **({'original': entry.original_name} if entry and entry.original_name and (entry.original_name != s.attributes.get('friendly_name')) else {}), **({'unit': str(s.attributes.get('unit_of_measurement') or '')} if s.domain == 'sensor' else {})})
        valves = [{'entity_id': s.entity_id, 'name': s.attributes.get('friendly_name') or s.entity_id, 'area': self._where(s)} for s in self.hass.states.async_all(['valve', 'switch']) if s.domain == 'valve' or (self.owns is not None and self.owns(s.entity_id))]
        reg = er.async_get(self.hass)
        outputs = [{'entity_id': s.entity_id, 'name': s.attributes.get('friendly_name') or s.entity_id, 'area': self._where(s), 'platform': e.platform if (e := reg.async_get(s.entity_id)) else '', 'own': bool(self.owns and self.owns(s.entity_id))} for s in self.hass.states.async_all(list(outputs_cfg())) if output_ok(self.hass, s.entity_id)]
        return {'config': self.config, 'people': self.people(), 'outputs': sorted(outputs, key=lambda c: (natural_key(c['area']), natural_key(c['name']))), 'levels': levels(), 'phones': self.phones(), 'candidates': sorted(candidates, key=lambda c: (natural_key(c['area']), natural_key(c['name']))), 'actuators': sorted(valves, key=lambda c: (natural_key(c['area']), natural_key(c['name']))), 'active': [{'key': k, 'since': v['since'], 'acked_by': v['acked_by'], 'count': v['count']} for k, v in self.active.items()], 'log': self.sent_log[:10], 'native': self.native_status(), 'limits': {'rate_limited': self._recent(self.limited, 86400), 'held': self._recent(self.throttled, 3600)}}
