"""Reads the inputs of a rule and evaluates its conditions."""
from __future__ import annotations
from typing import Any
from homeassistant.const import STATE_HOME, STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import Event, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_state_report_event
from homeassistant.util import dt as dt_util
from .. import derived as dv, issues
from ..derived import SETPOINT_GAP, num
from .actions import inputs_of, targets_of
from .conditions import is_match
from .const import DAYS, IS, OFF_STATES
from .schedule import past_off, past_on
from .spec import C, scheduled

class RulesEvaluateMixin:
    """Evaluates rules as their inputs change."""

    @callback
    def _listen(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        srcs = sorted({dv.split_source(s)[0] for d in self.derived_cfg.values() for s in d['sources']})
        if srcs:
            self._unsubs.append(async_track_state_change_event(self.hass, srcs, self._source_changed))
            self._unsubs.append(async_track_state_report_event(self.hass, srcs, self._source_reported))
        ins = sorted({i for r in self.rules.values() for i in inputs_of(r)})
        outs = sorted({e for r in self.rules.values() for e, _ in targets_of(r)})
        if ins:
            self._unsubs.append(async_track_state_change_event(self.hass, ins, self._input_changed))
        if outs:
            self._unsubs.append(async_track_state_change_event(self.hass, outs, self._target_changed))

    async def _tick(self, _now: Any=None) -> None:
        for did, d in self.derived.items():
            d.update()
            if (s := self.sensors.get(did)) is not None:
                s.refresh()
        await self.async_evaluate_all('tick')
        await self._run_todo()
        await self._pulses_due()
        for rid in list(self.rules):
            await self._suggest_due(rid)
        await self._release_held()

    @callback
    def _source_changed(self, event: Event) -> None:
        eid = event.data.get('entity_id')
        for did, d in self.derived.items():
            if any((dv.split_source(s)[0] == eid for s in d.cfg['sources'])):
                d.update()
                if (s := self.sensors.get(did)) is not None:
                    s.refresh()

    @callback
    def _source_reported(self, event: Event) -> None:
        eid = event.data.get('entity_id')
        for did, d in self.derived.items():
            if (d.why or '').startswith('stale') and any((dv.split_source(s)[0] == eid for s in d.cfg['sources'])):
                d.update()
                if (s := self.sensors.get(did)) is not None:
                    s.refresh()

    @callback
    def _input_changed(self, event: Event) -> None:
        eid = event.data.get('entity_id')
        for rid, r in list(self.rules.items()):
            if eid in inputs_of(r) and (not (r.get('window') and eid == r['input']['entity_id'])):
                self.hass.async_create_task(self.evaluate(rid, 'input'), 'hauflo rule')

    async def async_evaluate_all(self, why: str='tick') -> None:
        for rid in list(self.rules):
            await self.evaluate(rid, why)

    def read(self, rule: dict[str, Any]) -> tuple[float | None, str | None]:
        """(value in the rule's unit, why not)."""
        inp = rule['input']
        eid, attr = (inp['entity_id'], inp.get('attribute'))
        st = self.hass.states.get(eid)
        if st is None:
            return (None, 'no_entity')
        if st.state == STATE_UNKNOWN:
            return (None, 'unknown')
        if st.state in dv.NOT_A_STATE:
            return (None, 'unavailable')
        if rule['when'] == IS:
            return (self.is_value(inp), None)
        if rule['when'] in ('on', 'off'):
            if st.state not in (STATE_ON, STATE_OFF):
                return (None, 'not_a_number')
            return (float(st.state == STATE_ON), None)
        if attr == SETPOINT_GAP:
            sp, cur = (num(st.attributes.get('temperature')), num(st.attributes.get('current_temperature')))
            v = None if sp is None or cur is None else sp - cur
        else:
            v = num(st.attributes.get(attr) if attr else st.state)
        if v is None:
            return (None, 'not_a_number')
        conv = dv.convert(v, dv.unit_of(st, attr), inp.get('unit'))
        if conv is None:
            return (None, 'unit')
        return (conv, None)

    def is_value(self, src: dict[str, Any]) -> Any:
        """The value «равно» compares: as the device's canon read says it (a fan off: speed 0), else the state / attribute."""
        st = self.hass.states.get(src['entity_id'])
        if st is None or st.state in dv.NOT_A_STATE:
            return None
        if src.get('do'):
            return self.current(src['entity_id'], src['do'])
        return st.attributes.get(src['attribute']) if src.get('attribute') else st.state

    def conditions(self, rule: dict[str, Any]) -> tuple[bool, str | None]:
        now = dt_util.now()
        for c in rule['only_if']:
            t = c['type']
            if t == 'time':
                hm, a, b = (now.strftime('%H:%M'), c['after'], c['before'])
                inside = a <= hm < b if a <= b else hm >= a or hm < b
                start_day = DAYS[now.weekday()] if a <= b or hm >= a else DAYS[now.weekday() - 1]
                ok = inside and start_day in c['days']
            elif t == 'season':
                ok = self.season() == c['mode']
            elif t == 'presence':
                ok = any((s.state == STATE_HOME for s in self.hass.states.async_all('person'))) == c['home']
            elif t == 'heating':
                st = self.hass.states.get(c['entity_id'])
                ok = st is not None and st.attributes.get('hvac_action') == 'heating'
            elif t == 'value':
                st = self.hass.states.get(c['entity_id'])
                v = num(self.is_value(c) if c.get('do') else (st.attributes.get(c['attribute']) if c['attribute'] else st.state) if st else None)
                ok = v is not None and (v < c['x'] if c['when'] == 'below' else v > c['x'])
            elif IS in c:
                v = self.is_value(c)
                ok = v is not None and is_match(v, c[IS], c.get('tol', C('limits', 'is_tolerance'))) != c.get('not', False)
            else:
                st = self.hass.states.get(c['entity_id'])
                ok = (st is not None and st.state not in (*OFF_STATES, *dv.NOT_A_STATE)) == c['on']
            if not ok:
                return (False, t)
        return (True, None)

    async def evaluate(self, rid: str, why: str='tick') -> None:
        rule = self.rules.get(rid)
        if rule is None:
            return
        st = self.st(rid)
        t = dv.now()
        if not rule['enabled'] or st['paused']:
            self._refresh(rid)
            return
        if st['hold_until'] and t >= st['hold_until']:
            await self._hold_end(rule)
        if scheduled(rule):
            await self._sched_eval(rule, why)
            return
        if rule.get('window'):
            await self._evaluate_window(rule, why)
            return
        v, bad = self.read(rule)
        if bad == 'unknown':
            st_in = self.hass.states.get(rule['input']['entity_id'])
            until = (st_in.last_changed.timestamp() if st_in else t) + C('engine', 'unknown_grace_s')
            if t < until:
                self._wake(rid, until - t)
                self._refresh(rid)
                return
            bad = 'unavailable'
        if bad:
            await self._fault(rule, bad)
            self._refresh(rid)
            return
        if st['fault']:
            st['fault'] = None
            issues.clear_issue(self.hass, f'rule_fault_{rid}')
            self._log(rid, 'input_back', value=v)
            self._logbook(rule, self.say('rules_logbook_back', self.ru))
        st['value'] = v
        ok, cond = self.conditions(rule)
        st['cond'] = cond
        if not ok:
            st['pend'] = None
            if st['active']:
                await self._go(rule, False, v, 'only_if')
        elif not st['active']:
            if past_on(rule, v):
                await self._pending(rule, True, v, t)
            else:
                st['pend'] = None
        elif past_off(rule, v):
            await self._pending(rule, False, v, t)
        else:
            st['pend'] = None
        await self._interlock(rule)
        if st.get('interlocked') and (not self._interlock_off(rule)):
            st['interlocked'] = False
            if st['active'] and (not st['hold_until']):
                self._log(rid, 'interlock_back')
                await self._apply(rule, back=False, why='interlock_back')
        self._refresh(rid)
        self._save_soon()
