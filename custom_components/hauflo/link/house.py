"""The house as the agent reports it: watched signals, alarms and the KNX link."""
from __future__ import annotations
import logging
import time
from typing import Any
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, label_registry as lr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util
from .. import alarms_now, issues, ownership
from ..const import DOMAIN, MONITOR_LABEL, MONITOR_LABEL_EN, MONITOR_LABELS
from ..foreign import flags as foreign_flags
from ..guard import contained
from ..issues import degrading
from ..notify import common as notify_common, levels as notify_levels
from ..policy.areas import ha_url
from ..policy.knx import knx_conn, knx_data
from ..policy.own import recent_event
from ..signals import canon as signals_canon, store as signals_store
from .common import event_recent, knx_round_min_reads
_LOGGER = logging.getLogger(__name__)

class AgentHouseMixin:
    """Reads the house for the status."""

    async def _ensure_label(self) -> None:
        reg = lr.async_get(self.hass)
        if not any((label.name.casefold() in MONITOR_LABELS for label in reg.async_list_labels())):
            ru = str(self.hass.config.language or '').startswith('ru')
            reg.async_create(MONITOR_LABEL if ru else MONITOR_LABEL_EN, icon='mdi:bell-alert', color='red', description=ownership.MONITOR_TEXT['ru' if ru else 'en'])

    def _watch_ctx(self) -> dict[str, Any]:
        label = next((lab for lab in lr.async_get(self.hass).async_list_labels() if lab.name.casefold() in MONITOR_LABELS), None)
        reg = er.async_get(self.hass)
        sig = signals_store.store(self.hass)
        return {'reg': reg, 'label': label.label_id if label is not None else None, 'chosen': foreign_flags.notify_on(self.hass) | (sig.watch_on() if sig else set()) | (self.intercom.watched() if self.intercom is not None else set()), 'off': foreign_flags.notify_off(self.hass) | (sig.watch_off() if sig else set()), 'pol': signals_store.policy(self.hass)}

    def _kept(self, i: str, ctx: dict[str, Any]) -> bool:
        if i in ctx['off']:
            return False
        pol = ctx['pol']
        if pol and pol['scopes'] and ((ent := ctx['reg'].async_get(i)) is not None):
            st = self.hass.states.get(i)
            return bool(signals_canon.resolve(i, platform=ent.platform, device_class=st.attributes.get('device_class') if st else ent.device_class or ent.original_device_class, name=st.name if st else None, policy=pol, device_kind=signals_canon.device_kind_of(self.hass, i))['watch'])
        return True

    def watches(self, eid: str) -> bool:
        ctx = self._watch_ctx()
        e = ctx['reg'].async_get(eid)
        if e is None:
            return False
        labelled_ = ctx['label'] is not None and ctx['label'] in e.labels
        by_class = not e.disabled_by and (signals_canon.watch_by_class(eid, e.device_class or e.original_device_class, e.platform) or notify_common.watched_press(self.hass.states.get(eid), e.platform))
        return (labelled_ or by_class or eid in ctx['chosen']) and self._kept(eid, ctx)

    def _monitored_ids(self) -> list[str]:
        ctx = self._watch_ctx()
        reg = ctx['reg']
        ids = [e.entity_id for e in er.async_entries_for_label(reg, ctx['label'])] if ctx['label'] is not None else []
        by_class = sorted((e.entity_id for e in reg.entities.values() if not e.disabled_by and (signals_canon.watch_by_class(e.entity_id, e.device_class or e.original_device_class, e.platform) or (e.entity_id.startswith('event.') and notify_common.watched_press(self.hass.states.get(e.entity_id), e.platform)))))
        chosen = sorted((i for i in ctx['chosen'] if reg.async_get(i) is not None))
        return [i for i in dict.fromkeys([*ids, *by_class, *chosen]) if self._kept(i, ctx)]

    def house(self, summary: bool=False) -> dict[str, Any]:
        """«Всё хорошо» by the one calculator (signals.py, 0.32) over the watched signals: `_house` alarm (a problem whose policy says «авария») > fault («неисправность») > offline (only lost links among them) > ok; `_faults` — those problems; `_shelved` — the «не решено» problems (listed, never moving it)."""
        v = self.house_now()
        out = {**v, '_signals': self.signal_verdicts()}
        if summary:
            out['_summary'] = self.alarms_now(v['_faults'])
        return out

    @callback
    def house_now(self, mon: list[dict[str, Any]] | None=None) -> dict[str, Any]:
        faults, shelved, held = self._faults(self.monitored() if mon is None else mon)
        live = [f for f in faults if not f['offline']]
        level = 'alarm' if any((f['house'] == 'alarm' for f in live)) else 'fault' if live else 'offline' if faults else 'ok'
        v = {'_house': level, '_faults': faults, '_shelved': shelved, '_held': held}
        prev = self.house_state
        since = prev['since'] if prev and prev['level'] == level else dt_util.utcnow().isoformat()
        now = {'level': level, 'since': since, 'faults': faults, 'undetermined': len(shelved), 'held': held}
        if now != prev:
            self.house_state = now
            async_dispatcher_send(self.hass, f'{DOMAIN}_house')
        self._unshelve_soon()
        return v

    @callback
    def _unshelve_soon(self) -> None:
        at = signals_canon.next_unshelve(signals_store.policy(self.hass))
        if at == self._unshelve_at:
            return
        if self._unshelve_timer:
            self._unshelve_timer()
        self._unshelve_at = at
        self._unshelve_timer = async_track_point_in_utc_time(self.hass, self._unshelved, at) if at is not None else None

    @callback
    def _unshelved(self, _now: Any) -> None:
        self._unshelve_timer = None
        self._unshelve_at = None
        self.status_now()

    def alarms_now(self, faults: list[dict[str, Any]], cap: int | None=None) -> dict[str, Any]:
        engine = self.engine
        where = getattr(engine, 'where', None)
        items = getattr(engine, 'alarm_items', None)
        rich = []
        for f in faults:
            s = self.hass.states.get(f['entity'])
            rich.append({**f, 'area': where(s) if where and s else '', 'since': s.last_changed.isoformat() if s else None})
        acks = getattr(engine, 'house_acks', None)
        if acks and (keep := getattr(engine, 'house_acks_keep', None)):
            keep({f['entity'] for f in faults})
        return alarms_now.summary(items() if items else [], rich, cap, acks)

    def _faults(self, mon: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
        faults: list[dict[str, Any]] = []
        shelved: list[str] = []
        held: list[dict[str, Any]] = []
        for m in mon:
            if not m['problem']:
                continue
            if m['undetermined']:
                shelved.append(m['entityId'])
            elif m['house'] == 'none':
                continue
            elif m.get('shelvedUntil'):
                held.append({'entity': m['entityId'], 'until': m['shelvedUntil']})
            else:
                faults.append({'entity': m['entityId'], 'name': m['name'], 'house': m['house'], 'severity': m['severity'], 'offline': m['state'] == 'unavailable'})
        return (faults, shelved, held)

    def signal_verdicts(self) -> dict[str, dict[str, Any]]:
        reg = er.async_get(self.hass)
        pol = signals_store.policy(self.hass)
        own = (pol or {}).get('entities') or {}
        monitored = set(self._monitored_ids())
        sig = signals_store.store(self.hass)
        off = foreign_flags.notify_off(self.hass) | (sig.watch_off() if sig else set())
        ruled = {e for r in (self.engine.config['rules'] if self.engine is not None else []) if r.get('enabled', True) for e in notify_levels.live_sources(r)}
        out: dict[str, dict[str, Any]] = {}
        kinds = signals_canon.device_kinds(self.hass)
        for s in sorted(self.hass.states.async_all(signals_canon.signal_domains()), key=lambda x: (x.entity_id not in own, x.entity_id)):
            if len(out) >= signals_canon.cards_max():
                break
            eid = s.entity_id
            ent = reg.async_get(eid)
            platform = str(ent.platform) if ent is not None and ent.platform else None
            if platform in signals_canon.watch_skip():
                continue
            ev = signals_canon.evaluate(s, platform=platform, policy=pol, device_kind=kinds.get(ent.device_id) if ent is not None and ent.device_id else None)
            by_canon = signals_canon.watch_by_class(eid, s.attributes.get('device_class'), platform) or ev['base'] in signals_canon.CLASSLESS_WATCHED
            if not (eid in monitored or eid in off or by_canon or (eid in own)) or (ev['undetermined'] and eid not in off and (eid not in own)):
                continue
            alarm = ev['alarm']
            cond = alarm if alarm and 'none' not in alarm else None
            watched = eid not in off and ev['watch'] and (eid in monitored or by_canon) and (ev['house'] != 'none') and (not ev['shelved'])
            v: dict[str, Any] = {'house': ev['house'], 'alarm': alarm} if watched and cond else {'house': 'none', **({'alarm': cond} if cond else {})}
            who = sig.own_of(eid) if sig else {'meaning': None, 'ha': False}
            v['src'] = 'ha' if who['ha'] else ev['source']
            if who['meaning']:
                v['meaning'] = who['meaning']
            if eid in ruled:
                v['rule'] = True
            if ev['shelved']:
                v['until'] = ev['shelvedUntil']
            out[eid] = v
        return out

    def _knx(self) -> dict[str, Any]:
        entries = self.hass.config_entries.async_entries('knx')
        loaded = any((e.state is ConfigEntryState.LOADED for e in entries))
        connected = None
        if loaded:
            with degrading(self.hass, 'knx connection state', issues.HA):
                connected = knx_data(self.hass).xknx.connection_manager.connected.is_set()
                issues.recover(self.hass, 'knx connection state')
        now = time.monotonic()
        silent = int((now - (self._knx_heard or self._knx_since)) // 60)
        unanswered = int((now - self._knx_asked) // 60) if self._knx_asked is not None and self._knx_asked_n >= knx_round_min_reads() else None
        return {'loaded': loaded, 'connected': connected, 'state': entries[0].state.value if entries else 'missing', 'silentMin': silent, 'unansweredMin': unanswered, 'unansweredReads': self._knx_asked_n, 'heard': self._knx_heard is not None}

    def _knx_conn(self) -> dict[str, Any] | None:
        entries = self.hass.config_entries.async_entries('knx')
        if not entries:
            return None
        return knx_conn(entries[0].data)

    def _ha_url(self) -> str | None:
        return ha_url(self.hass.config.external_url or self.hass.config.internal_url)

    async def _refresh_admins(self) -> None:
        with contained(_LOGGER, 'Hauflo: list of HA admins not read'):
            users = await self.hass.auth.async_get_users()
            self._admins = sorted({str(u.name)[:80] for u in users if u.is_admin and u.is_active and (not u.system_generated) and u.name})[:20]

    def monitored(self) -> list[dict[str, Any]]:
        """The watched signals now, each by the one calculator (signals.py): problem, its «Всё хорошо» (house), importance, «не решено», where its policy came from — the same as the cloud's applySignals (and its own 0.31 status)."""
        reg = er.async_get(self.hass)
        pol = signals_store.policy(self.hass)
        kinds = signals_canon.device_kinds(self.hass)
        out = []
        for eid in self._monitored_ids():
            s = self.hass.states.get(eid)
            if s is None:
                continue
            dc = s.attributes.get('device_class')
            ent = reg.async_get(eid)
            platform = str(ent.platform) if ent is not None and ent.platform else None
            kind = kinds.get(ent.device_id) if ent is not None and ent.device_id else None
            ev = signals_canon.evaluate(s, platform=platform, policy=pol, device_kind=kind)
            item: dict[str, Any] = {'entityId': eid, 'name': s.name, 'state': s.state, 'problem': ev['problem']}
            if kind:
                item['deviceKind'] = kind
            if dc:
                item['deviceClass'] = str(dc)
            item['platform'] = platform
            item.update({'severity': ev['defaultSeverity'], 'house': ev['house'], 'undetermined': ev['undetermined'], 'source': ev['source'], 'lifeSafety': ev['lifeSafety']})
            item['policy'] = {'alarm': ev['alarm'], 'holdS': ev['holdS'], 'from': ev['from']}
            if ev['shelved']:
                item['shelvedUntil'] = ev['shelvedUntil']
            if s.domain == 'event' and (ring := recent_event(self.engine.ring_said(s) if self.engine is not None else s.state, event_recent())):
                item['ring'] = ring
                item['eventClass'] = kind if (kind := notify_common.event_kind(s)) != 'event' else str(dc) if dc else ''
            out.append(item)
        return out
