"""Starts, holds and clears alarms as their sources change."""
from __future__ import annotations
import logging
import time
from typing import Any
from homeassistant.components import persistent_notification
from homeassistant.core import Event, State, callback
from homeassistant.helpers import translation
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util
from ..const import DOMAIN
from .common import LINK_KINDS, NOT_A_STATE, chatter_count, chatter_window_cfg, group_min, group_window, native_hold, nodata_after, source_hold
from .levels import in_alarm, live_sources, muted
from .rules import devices
_LOGGER = logging.getLogger(__name__)

class NotifyFiringMixin:
    """Fires and clears alarms."""

    @callback
    def _apply(self) -> None:
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        sources = sorted({s for r in self.config['rules'] if r['enabled'] for s in live_sources(r)})
        if sources:
            self._unsub_state = async_track_state_change_event(self.hass, sources, self._on_state)
        for cancel in self._holding.values():
            cancel()
        self._holding.clear()
        for rule in self.config['rules']:
            if not rule['enabled']:
                continue
            for entity_id in live_sources(rule):
                key = f"{rule['id']}|{entity_id}"
                state = self.hass.states.get(entity_id)
                if not state or state.state in NOT_A_STATE:
                    self._watch_nodata(rule, entity_id)
                if key in self.active or not state or state.state in NOT_A_STATE:
                    continue
                if not in_alarm(rule, state):
                    self._seen_ok.add(entity_id)
                if key in self._restored:
                    self._adopt(rule, key)
                    if not in_alarm(rule, state, open_now=True):
                        self._clear(rule, state)
                elif in_alarm(rule, state):
                    self._start_held(rule, state)
        keep = {f"{r['id']}|{s}" for r in self.config['rules'] if r['enabled'] for s in live_sources(r)}
        for key in [k for k in self.active if k not in keep]:
            self._end_quietly(key)
        known = {r['id'] for r in self.config['rules']}
        self._restored = {k: v for k, v in self._restored.items() if k in keep or k.partition('|')[0] not in known}
        for rule_id in [r for r in self.groups if not any((k.startswith(r + '|') for k in [*self.active, *self._restored]))]:
            self._end_group(rule_id)
        outs = {r['id']: {o['entity_id'] for o in r.get('outputs') or []} for r in self.config['rules'] if r['enabled']}
        for rule_id, eids in list(self._sounding.items()):
            gone = eids - outs.get(rule_id, set())
            if gone:
                self.hass.async_create_task(self._switch(rule_id, sorted(gone), 'on_end', 'rule changed'))
        self._save()

    @callback
    def _on_state(self, event: Event) -> None:
        new: State | None = event.data.get('new_state')
        old: State | None = event.data.get('old_state')
        if not new:
            return
        for rule in self.config['rules']:
            if not rule['enabled'] or new.entity_id not in rule['sources'] or muted(rule, new.entity_id):
                continue
            key = f"{rule['id']}|{new.entity_id}"
            alarm = in_alarm(rule, new, open_now=key in self.active or key in self._restored)
            if not alarm and new.state not in NOT_A_STATE and (new.entity_id not in self._seen_ok):
                self._seen_ok.add(new.entity_id)
                self._save()
            was = in_alarm(rule, old, open_now=key in self.active)
            if not alarm:
                self._unhold(key)
            if new.state in NOT_A_STATE:
                self._watch_nodata(rule, new.entity_id)
            elif (cancel := self._nodata_timers.pop(key, None)) is not None:
                cancel()
            if key in self.active and self.active[key].get('nodata') and (new.state not in NOT_A_STATE):
                self.active.pop(key)
                persistent_notification.async_dismiss(self.hass, self.tag(key))
                self._pending_back.setdefault(rule['id'], []).append(key)
                self._later()
                if alarm:
                    self._start_held(rule, new)
                continue
            if key in self._restored and new.state not in NOT_A_STATE:
                self._adopt(rule, key)
                if not alarm:
                    self._clear(rule, new)
            elif alarm and (not was) and (key not in self.active):
                if old is not None and old.state in NOT_A_STATE and self._ha_misses(rule, old, new):
                    self._native_first.add(rule['id'])
                self._start_held(rule, new)
            elif not alarm and key in self.active and (new.state not in NOT_A_STATE):
                self._clear(rule, new)

    @callback
    def _watch_nodata(self, rule: dict[str, Any], entity_id: str) -> None:
        key = f"{rule['id']}|{entity_id}"
        if rule['kind'] in LINK_KINDS or muted(rule, entity_id) or key in self._nodata_timers or (key in self.active) or (entity_id not in self._seen_ok):
            return

        @callback
        def _go(_now) -> None:
            self._nodata_timers.pop(key, None)
            s = self.hass.states.get(entity_id)
            if (s is None or s.state in NOT_A_STATE) and key not in self.active and any((r['id'] == rule['id'] and r['enabled'] for r in self.config['rules'])):
                self.active[key] = {'since': dt_util.utcnow().isoformat(), 'acked_by': None, 'repeat': None, 'count': 0, 'group': False, 'nodata': True}
                self._pending_nodata.setdefault(rule['id'], []).append(key)
                self._later()
        self._nodata_timers[key] = async_call_later(self.hass, nodata_after(), _go)

    async def _flush_nodata(self, rules: dict[str, dict[str, Any]]) -> None:
        lost, back = (self._pending_nodata, self._pending_back)
        self._pending_nodata, self._pending_back = ({}, {})
        t = await self._words() if lost or back else {}
        for rule_id, keys in lost.items():
            rule = rules.get(rule_id)
            keys = [k for k in keys if k in self.active]
            if not rule or not keys:
                continue
            names = self._where_list(keys)
            title = t.get('nodata_title', '📵 {title}').format(title=rule['title']) + (f' — {len(keys)} {devices(len(keys))}' if len(keys) > 1 else '')
            message = t.get('nodata', '{names}').format(minutes=nodata_after() // 60, names=names)
            own = self._engine_says(rule_id)
            await self.send(rule, title, message, tag=self._gtag(rule_id) + '_nd', critical=False, force=own)
            self._note(message, title=title, notification_id=self._gtag(rule_id) + '_nd', rule=None if own else rule)
        for rule_id, keys in back.items():
            rule = rules.get(rule_id)
            if not rule:
                continue
            if not any((k.startswith(rule_id + '|') and inc.get('nodata') for k, inc in self.active.items())):
                persistent_notification.async_dismiss(self.hass, self._gtag(rule_id) + '_nd')
            await self.send(rule, t.get('nodata_back_title', '✅ {title}').format(title=rule['title']), t.get('nodata_back', '{names}').format(names=self._where_list(keys)), tag=self._gtag(rule_id) + '_nd', critical=False, force=self._engine_says(rule_id))

    @callback
    def _start_held(self, rule: dict[str, Any], state: State) -> None:
        key = f"{rule['id']}|{state.entity_id}"
        hold = source_hold(rule, state.entity_id)
        hold = max(native_hold(), hold) if self._engine_says(rule['id']) else hold
        left = hold - (dt_util.utcnow() - state.last_changed).total_seconds()
        if left <= 0:
            if (cancel := self._holding.pop(key, None)) is not None:
                cancel()
            self._start(rule, state)
            return
        if key in self._holding:
            return
        rule_id, eid = (rule['id'], state.entity_id)

        @callback
        def _held(_now) -> None:
            self._holding.pop(key, None)
            now_rule = next((r for r in self.config['rules'] if r['id'] == rule_id and r['enabled'] and (eid in r['sources'])), None)
            st = self.hass.states.get(eid)
            if now_rule is not None and st is not None and (key not in self.active) and in_alarm(now_rule, st):
                self._start(now_rule, st)
        self._holding[key] = async_call_later(self.hass, left, _held)

    @callback
    def _unhold(self, key: str) -> None:
        if (cancel := self._holding.pop(key, None)) is not None:
            cancel()
            rule_id = key.partition('|')[0]
            if rule_id not in self._pending_start and (not any((k.startswith(rule_id + '|') for k in self._holding))):
                self._native_first.discard(rule_id)

    @callback
    def _start(self, rule: dict[str, Any], state: State) -> None:
        if self._link_unknown(rule, state):
            return
        key = f"{rule['id']}|{state.entity_id}"
        self.unacked_rtn.pop(key, None)
        now = time.monotonic()
        flaps = [x for x in self._flaps.get(key, []) if now - x < chatter_window_cfg()] + [now]
        self._flaps[key] = flaps
        chattering = key in self._chatter
        self.active[key] = {'since': dt_util.utcnow().isoformat(), 'acked_by': None, 'repeat': None, 'count': 0, 'group': False, **({'quiet': True} if chattering or len(flaps) >= chatter_count() else {})}
        self._calm_later(key)
        if chattering:
            return
        if len(flaps) >= chatter_count():
            self._chatter[key] = lambda: None
            self.hass.async_create_task(self._say_chatter(rule, state))
            return
        self._pending_start.setdefault(rule['id'], []).append(key)
        self._later()

    @callback
    def _calm_later(self, key: str) -> None:
        if key not in self._chatter:
            return
        self._chatter[key]()

        @callback
        def _calm(_now) -> None:
            self._chatter.pop(key, None)
            self._flaps.pop(key, None)
            inc = self.active.get(key)
            if inc and inc.pop('quiet', None):
                self._pending_start.setdefault(key.partition('|')[0], []).append(key)
                self._later()
        self._chatter[key] = async_call_later(self.hass, chatter_window_cfg(), _calm)

    async def _words(self) -> dict[str, str]:
        try:
            got = await translation.async_get_translations(self.hass, 'en' if self.english() else 'ru', 'exceptions', [DOMAIN])
        except Exception:
            _LOGGER.warning('Hauflo: words missing never stop an alarm', exc_info=True)
            return {}
        pre = f'component.{DOMAIN}.exceptions.alarm_native_'
        return {k.removeprefix(pre).removesuffix('.message'): v for k, v in got.items() if k.startswith(pre)}

    async def _say_chatter(self, rule: dict[str, Any], state: State) -> None:
        name = self.name_of(state.entity_id)
        where = self._where(state)
        try:
            got = await self._words()
            t_title = got.get('chatter_title') or '{title}'
            own = self._engine_says(rule['id'])
            t_msg = got.get('chatter_ha' if own else 'chatter') or '{name}: {count}/{minutes}'
            title = t_title.format(title=rule['title']) + (f' — {where}' if where else '')
            message = t_msg.format(name=name, count=chatter_count(), minutes=max(1, chatter_window_cfg() // 60))
            await self.send(rule, title, message, tag=self.tag(f"{rule['id']}|{state.entity_id}") + '_chatter', critical=False, force=own)
            self._note(message, title=title, notification_id=self.tag(f"{rule['id']}|{state.entity_id}") + '_chatter', rule=None if own else rule)
        finally:
            self._calm_later(f"{rule['id']}|{state.entity_id}")

    @callback
    def _clear(self, rule: dict[str, Any], state: State) -> None:
        key = f"{rule['id']}|{state.entity_id}"
        inc = self.active.pop(key, None)
        if not inc:
            return
        if inc.get('repeat'):
            inc['repeat']()
        if rule['priority'] == 'critical' and (not inc.get('acked_by')) and (not inc.get('nodata')):
            self.unacked_rtn[key] = {'since': inc['since'], 'cleared': dt_util.utcnow().isoformat(), 'title': rule['title']}
            self._changed()
        starting = self._pending_start.get(rule['id']) or []
        if key in self._chatter:
            self._calm_later(key)
        if inc.get('quiet'):
            pass
        elif key in starting:
            starting.remove(key)
        else:
            self._pending_clear.setdefault(rule['id'], []).append((key, inc, state))
            self._later()
        self._save()

    @callback
    def _later(self) -> None:
        if self._flush_unsub:
            return

        @callback
        def _go(_now) -> None:
            self._flush_unsub = None
            self.hass.async_create_task(self._flush())
        self._flush_unsub = async_call_later(self.hass, group_window(), _go)

    async def _flush(self) -> None:
        starts, clears = (self._pending_start, self._pending_clear)
        self._pending_start, self._pending_clear = ({}, {})
        rules = {r['id']: r for r in self.config['rules']}
        await self._flush_nodata(rules)
        for rule_id, keys in starts.items():
            rule = rules.get(rule_id)
            keys = [k for k in keys if k in self.active]
            if not rule or not keys:
                continue
            if rule.get('outputs') and (not self._in_ha(rule_id) or rule_id in self._native_first):
                self.hass.async_create_task(self._switch(rule_id, [o['entity_id'] for o in rule['outputs'] if o['on_start']], 'on_start', 'alarm'))
            if len(keys) >= group_min() or (rule_id in self.groups and (not self.groups[rule_id]['acked_by'])):
                for k, inc in self.active.items():
                    if k.startswith(rule_id + '|') and (not inc.get('acked_by')) and (not inc.get('group')) and (k not in keys):
                        keys.append(k)
                        if inc.get('repeat'):
                            inc['repeat']()
                            inc['repeat'] = None
                        if inc.get('own'):
                            await self.send(rule, '', 'clear_notification', tag=self.tag(k), critical=False)
                            persistent_notification.async_dismiss(self.hass, self.tag(k))
                            inc['own'] = False
                for k in keys:
                    self.active[k]['group'] = True
                group = self.groups.get(rule_id)
                if not group or group['acked_by']:
                    self._end_group(rule_id)
                    self.groups[rule_id] = {'since': dt_util.utcnow().isoformat(), 'acked_by': None, 'count': 0, 'repeat': None}
                await self._send_group(rule)
            else:
                for k in keys:
                    if (state := self.hass.states.get(k.partition('|')[2])):
                        await self._send_alarm(rule, state, k)
        self._native_first.difference_update(starts)
        for rule_id, items in clears.items():
            rule = rules.get(rule_id)
            if not rule:
                continue
            if not self._in_ha(rule_id) and (not any((k.startswith(rule_id + '|') and (not inc.get('nodata')) for k, inc in self.active.items()))):
                if rule.get('outputs'):
                    await self._switch(rule_id, [o['entity_id'] for o in rule['outputs'] if o['on_end']], 'on_end', 'back to normal')
                await self._set_helper(rule_id, False, 'open')
            grouped = [(k, s) for k, inc, s in items if inc.get('group')]
            single = [(k, s) for k, inc, s in items if not inc.get('group')]
            if grouped:
                left = [k for k, inc in self.active.items() if k.startswith(rule_id + '|') and inc.get('group')]
                if not left:
                    self._end_group(rule_id)
                    title = f"✅ {rule['title']}" + (f' — {len(grouped)} {devices(len(grouped))}' if len(grouped) > 1 else '')
                    await self.send(rule, title, f'Снова в норме: {self._where_list([k for k, _ in grouped])}.', tag=self._gtag(rule_id), critical=False)
                    persistent_notification.async_dismiss(self.hass, self._gtag(rule_id))
                else:
                    await self._send_group(rule, back=[k for k, _ in grouped])
            if len(single) >= group_min():
                await self.send(rule, f"✅ {rule['title']} — {len(single)} {devices(len(single))}", f'Снова в норме: {self._where_list([k for k, _ in single])}.', tag=self._gtag(rule_id) + '_ok', critical=False)
                for k, _ in single:
                    persistent_notification.async_dismiss(self.hass, self.tag(k))
            else:
                for k, state in single:
                    where = self._where(state)
                    await self.send(rule, f"✅ {rule['title']}" + (f' — {where}' if where else ''), f'{self.name_of(state.entity_id)}: снова в норме.', tag=self.tag(k), critical=False)
                    persistent_notification.async_dismiss(self.hass, self.tag(k))
        self._save()
        self._changed()
