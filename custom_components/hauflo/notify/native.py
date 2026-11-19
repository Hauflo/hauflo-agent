"""Alarms handed over to HA's own automations and taken back when they miss."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.components import persistent_notification
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, Event, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util, slugify
from ..automations.common import digest
from ..const import QUIET_PARENT
from ..guard import contained
from ..independent.common import alarm_hold
from .common import LINK_KINDS, NOT_A_STATE, group_window, list_max, native_back, native_hold, outputs_cfg
from .levels import in_alarm, muted
_LOGGER = logging.getLogger(__name__)

class NotifyNativeMixin:
    """Hands alarms over to HA automations."""

    def watched(self) -> set[str]:
        return {e for r in self.config['rules'] if r['enabled'] for e in (*r['sources'], *(r.get('picked') or [])) if not muted(r, e)}

    async def _switch(self, rule_id: str, eids: list[str], when: str, why: str) -> None:
        rule = next((r for r in self.config['rules'] if r['id'] == rule_id), None)
        saved = {o['entity_id'] for o in (rule or {}).get('outputs') or []}
        sounding = self._sounding.setdefault(rule_id, set())
        for eid in eids:
            domain = eid.partition('.')[0]
            service = outputs_cfg().get(domain, {}).get(when)
            if service is None or not (eid in saved or (when == 'on_end' and eid in sounding)):
                _LOGGER.warning('Hauflo: alarm rule %s: output %s refused (%s)', rule_id, eid, when)
                continue
            with contained(_LOGGER, 'Hauflo: alarm rule %s: %s.%s %s failed: %s', rule_id, domain, service, eid, err=True):
                await self.hass.services.async_call(domain, service, {'entity_id': eid}, blocking=True)
                _LOGGER.info('Hauflo: alarm rule %s: %s.%s %s (%s)', rule_id, domain, service, eid, why)
                if when == 'on_start':
                    sounding.add(eid)
                else:
                    sounding.discard(eid)
        if not sounding:
            self._sounding.pop(rule_id, None)

    @staticmethod
    def tag(key: str) -> str:
        return 'hauflo_' + slugify(key)

    @staticmethod
    def _gtag(rule_id: str) -> str:
        return 'hauflo_' + slugify(rule_id) + '_all'

    @callback
    def _watch_helpers(self) -> None:
        if self._unsub_helper:
            self._unsub_helper()
            self._unsub_helper = None
        if (helpers := sorted({str(rec['helper']) for rec in self.native.values()})):
            self._unsub_helper = async_track_state_change_event(self.hass, helpers, self._on_helper)

    @callback
    def _on_helper(self, event: Event) -> None:
        new: State | None = event.data.get('new_state')
        old: State | None = event.data.get('old_state')
        if new is None or old is None or new.state != 'on' or (old.state != 'off') or (new.context.parent_id == QUIET_PARENT):
            return
        if (rule_id := next((r for r, rec in self.native.items() if rec['helper'] == new.entity_id), None)):
            self.hass.async_create_task(self._native_accepted(rule_id, new.context))

    async def _native_accepted(self, rule_id: str, context: Context) -> None:
        who = self._native_who.pop(rule_id, None)
        if who is None:
            who = await self._who(Event('state_changed', context=context))
        changed = False
        for key, inc in self.active.items():
            if key.startswith(rule_id + '|') and (not inc.get('acked_by')) and (not inc.get('nodata')):
                inc['acked_by'] = who
                changed = True
        if (group := self.groups.get(rule_id)) and (not group['acked_by']):
            group['acked_by'] = who
            changed = True
        if changed:
            self._save()
            self._changed()

    async def _set_helper(self, rule_id: str, on: bool, which: str='helper') -> None:
        rec = self.native.get(rule_id)
        eid = (rec or {}).get(which)
        if not eid or (state := self.hass.states.get(eid)) is None or state.state == ('on' if on else 'off'):
            return
        with contained(_LOGGER, 'Hauflo: helper %s not switched: %s', eid, err=True):
            await self.hass.services.async_call('input_boolean', f"turn_{('on' if on else 'off')}", {'entity_id': eid}, blocking=True, context=Context(parent_id=QUIET_PARENT))

    async def async_set_native(self, native: dict[str, dict[str, Any]]) -> None:
        """Which rules HA's automations run now (independent.async_sync)."""
        before, self.native = (self.native, {str(k): dict(v) for k, v in native.items()})
        await self.native_store.async_save(self.native)
        self._watch_helpers()
        rules = {r['id']: r for r in self.config['rules']}
        for rule_id in self.native:
            if (rule_id not in before or before[rule_id].get('digest') != self.native[rule_id].get('digest')) and any((k.startswith(rule_id + '|') for k in self._holding)):
                self._native_first.add(rule_id)
            if rule_id in before:
                if 'open' in self.native[rule_id] and 'open' not in before[rule_id]:
                    await self._set_helper(rule_id, any((k.startswith(rule_id + '|') and (not inc.get('nodata')) for k, inc in self.active.items())), 'open')
                continue
            if rule_id in self._pending_start:
                self._native_first.add(rule_id)
            await self._handed(rule_id)
        for rule_id in before:
            if rule_id in self.native or (rule := rules.get(rule_id)) is None:
                continue
            self._taken_back(rule)
        self._save()
        self._changed()

    def _automation(self, rec: dict[str, Any]) -> State | None:
        eid = er.async_get(self.hass).async_get_entity_id('automation', 'automation', rec['automation'])
        return self.hass.states.get(eid) if eid else None

    def _ha_state(self, rec: dict[str, Any]) -> str:
        st = self._automation(rec)
        if st is None:
            return 'missing'
        return 'ha' if st.state == 'on' else 'unavailable' if st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN) else 'off'

    def _engine_says(self, rule_id: str | None) -> bool:
        rec = self.native.get(str(rule_id)) if rule_id is not None else None
        return rec is not None and 'chatter' not in rec and self._in_ha(rule_id)

    def _ha_misses(self, rule: dict[str, Any], old: State, new: State) -> bool:
        rec = self.native.get(rule['id']) or {}
        opn = self.hass.states.get(rec.get('open') or '')
        return self._engine_says(rule['id']) and rule.get('kind') not in LINK_KINDS and (opn is not None) and (opn.state == 'on') and ((new.last_changed - old.last_changed).total_seconds() < native_back(rule))

    def _in_ha(self, rule_id: str | None) -> bool:
        rec = self.native.get(str(rule_id)) if rule_id is not None else None
        if rec is None:
            return False
        return not self.hass.is_running or self._ha_state(rec) == 'ha'

    @callback
    def _on_automation(self, event: Event) -> None:
        new: State | None = event.data.get('new_state')
        old: State | None = event.data.get('old_state')
        st = new or old
        aid = st.attributes.get('id') if st is not None else None
        rule_id = next((r for r, rec in self.native.items() if rec.get('automation') == aid), None) if aid else None
        was, now = (old is not None and old.state == 'on', new is not None and new.state == 'on')
        if rule_id is None or was == now:
            return
        if now:
            self.hass.async_create_task(self._handed(rule_id))
        elif (rule := next((r for r in self.config['rules'] if r['id'] == rule_id), None)):
            _LOGGER.warning("Hauflo: alarm automation %s doesn't run in HA (%s) — the agent tells its alarms", aid, self._ha_state(self.native[rule_id]))
            self._taken_back(rule)
        self._changed()

    async def _handed(self, rule_id: str) -> None:
        keys = [k for k, inc in self.active.items() if k.startswith(rule_id + '|') and (not inc.get('nodata'))]
        for k in keys:
            if self.active[k].get('repeat'):
                self.active[k]['repeat']()
                self.active[k]['repeat'] = None
        if (group := self.groups.get(rule_id)) and group.get('repeat'):
            group['repeat']()
            group['repeat'] = None
        await self._set_helper(rule_id, bool(keys) and all((self.active[k].get('acked_by') for k in keys)))
        if keys:
            await self._set_helper(rule_id, True, 'open')
        else:
            self._watch_missed(rule_id)
        if keys:

            async def _go(_hass: HomeAssistant) -> None:
                if (self.native.get(rule_id) or {}).get('form') == 2:
                    rule = next((r for r in self.config['rules'] if r['id'] == rule_id), None)
                    later = (alarm_hold(rule) if rule else native_hold()) + native_hold()

                    @callback
                    def _late(_now: Any) -> None:
                        self._late_kicks.pop(rule_id, None)
                        self.hass.async_create_task(self._kick(rule_id))
                    if (cancel := self._late_kicks.pop(rule_id, None)):
                        cancel()
                    self._late_kicks[rule_id] = async_call_later(self.hass, later, _late)
                    return
                await self._kick(rule_id)
            async_at_started(self.hass, _go)

    async def _kick(self, rule_id: str) -> None:
        rec = self.native.get(rule_id) or {}
        st = self._automation(rec) if rec.get('automation') else None
        if rec.get('form') == 2:
            grp, ack = (self.hass.states.get(rec.get('group') or ''), self.hass.states.get(rec.get('helper') or ''))
            if st is None or st.state != 'on' or st.attributes.get('current') or (grp is None) or (grp.state != 'on') or (ack is not None and ack.state == 'on'):
                return
            with contained(_LOGGER, 'Hauflo: alarm automation %s not started: %s', st.entity_id, err=True):
                await self.hass.services.async_call('automation', 'trigger', {'entity_id': st.entity_id, 'skip_condition': True}, blocking=False, context=Context(parent_id=QUIET_PARENT))
            return
        opn = self.hass.states.get(rec.get('open') or '')
        if st is None or st.state != 'on' or st.attributes.get('current') or (opn is None) or (opn.state != 'on'):
            return
        ent = getattr(self.hass.data.get('automation'), 'get_entity', lambda _e: None)(st.entity_id)
        variables = dict((getattr(ent, 'raw_config', None) or {}).get('trigger_variables') or {})
        with contained(_LOGGER, 'Hauflo: alarm automation %s not started: %s', st.entity_id, err=True):
            await self.hass.services.async_call('automation', 'trigger', {'entity_id': st.entity_id, 'skip_condition': True, 'variables': variables}, blocking=False, context=Context(parent_id=QUIET_PARENT))

    @callback
    def _watch_missed(self, rule_id: str, delay: float | None=None) -> None:
        if (cancel := self._missed.pop(rule_id, None)) is not None:
            cancel()
        if (rule := next((r for r in self.config['rules'] if r['id'] == rule_id), None)) is None:
            return

        @callback
        def _go(_now: Any) -> None:
            self._missed.pop(rule_id, None)
            self.hass.async_create_task(self._missed_back(rule_id))
        self._missed[rule_id] = async_call_later(self.hass, native_back(rule) + group_window() if delay is None else delay, _go)

    async def _missed_back(self, rule_id: str) -> None:
        rule = next((r for r in self.config['rules'] if r['id'] == rule_id and r['enabled']), None)
        rec = self.native.get(rule_id) or {}
        flat = rec.get('form') == 2
        if rule is None or not self._in_ha(rule_id):
            return
        if flat:
            if not self._missed_src.get(rule_id):
                return
        else:
            opn = self.hass.states.get(rec.get('open') or '')
            if opn is None or opn.state != 'on':
                return
        states = [st for e in rule['sources'] if (st := self.hass.states.get(e)) is not None]
        if any((in_alarm(rule, st, open_now=True) for st in states)):
            if flat:
                self._missed_src.pop(rule_id, None)
            return
        told = [e for e in self._missed_src.get(rule_id) or [] if e in rule['sources']] or rule['sources']
        normal = [st for st in states if st.entity_id in told and st.state not in NOT_A_STATE]
        if len(normal) < len(told):
            self._watch_missed(rule_id)
            return
        wait = native_back(rule) + group_window() - min(((dt_util.utcnow() - st.last_changed).total_seconds() for st in normal))
        if wait > 0:
            self._watch_missed(rule_id, wait)
            return
        self._missed_src.pop(rule_id, None)
        if flat:
            back = self._automation({'automation': rec['back']}) if rec.get('back') else None
            if back is not None and back.state == 'on':
                try:
                    await self.hass.services.async_call('automation', 'trigger', {'entity_id': back.entity_id, 'skip_condition': False}, blocking=True, context=Context(parent_id=QUIET_PARENT))
                except Exception as err:
                    _LOGGER.warning('Hauflo: automation %s not started: %s', back.entity_id, err)
                else:
                    return
            persistent_notification.async_dismiss(self.hass, rec['automation'])
            if rule.get('outputs'):
                await self._switch(rule_id, [o['entity_id'] for o in rule['outputs'] if o['on_end']], 'on_end', 'back to normal')
            if rec.get('back'):
                t = await self._words()
                await self.send(rule, t.get('back_title', '✅ {title}').format(title=rule['title']), t.get('back_all', 'OK'), tag=rec['automation'], critical=False, force=True)
            return
        await self._set_helper(rule_id, False, 'open')
        await self._set_helper(rule_id, False)
        persistent_notification.async_dismiss(self.hass, rec['automation'])
        if rule.get('outputs'):
            await self._switch(rule_id, [o['entity_id'] for o in rule['outputs'] if o['on_end']], 'on_end', 'back to normal')
        t = await self._words()
        names = ', '.join((self.name_of(st.entity_id) + (f' · {where}' if (where := self._where(st)) else '') for st in normal[:list_max()]))
        await self.send(rule, t.get('back_title', '✅ {title}').format(title=rule['title']), t.get('back', '{name}').format(name=names), tag=rec['automation'], critical=False, force=True)

    @callback
    def _taken_back(self, rule: dict[str, Any]) -> None:
        for k, inc in self.active.items():
            if k.startswith(rule['id'] + '|') and (not inc.get('acked_by')) and (not inc.get('nodata')) and (not inc.get('group')) and (not inc.get('repeat')):
                self._schedule(rule, k)
        if (group := self.groups.get(rule['id'])) and (not group['acked_by']) and (not group.get('repeat')):
            self._schedule_group(rule)

    def native_status(self) -> dict[str, Any]:
        """Each rule HA runs: its automation and helper, on/accepted, when it last ran (Hauflo's monitoring); ``state`` — «ha» (its automation runs) or why the agent tells its alarms instead: «off», «missing», «unavailable»."""
        reg = er.async_get(self.hass)
        out: dict[str, Any] = {}
        for rule_id, rec in self.native.items():
            eid = reg.async_get_entity_id('automation', 'automation', rec['automation'])
            st = self.hass.states.get(eid) if eid else None
            hs = self.hass.states.get(rec['helper'])
            last = st.attributes.get('last_triggered') if st else None
            out[rule_id] = {'automation': eid, 'id': rec['automation'], 'helper': rec['helper'], 'state': self._ha_state(rec), 'on': st.state == 'on' if st else None, 'acked': hs.state == 'on' if hs else None, 'last_triggered': last.isoformat() if hasattr(last, 'isoformat') else last, 'form': rec.get('form', 1)}
            if rec.get('form', 1) == 1:
                ent = getattr(self.hass.data.get('automation'), 'get_entity', lambda _e: None)(eid) if eid else None
                raw = getattr(ent, 'raw_config', None)
                if isinstance(raw, dict):
                    out[rule_id]['edited'] = digest(raw) != rec.get('digest')
        return out
