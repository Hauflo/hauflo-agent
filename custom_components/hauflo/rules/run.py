"""Runs the actions of a rule on its targets."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_CLOSED, STATE_OFF, STATE_ON, STATE_OPEN, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Context, callback
from homeassistant.helpers.event import async_call_later
from .. import derived as dv, issues
from ..derived import num
from .actions import targets_of
from .checks import may_change
from .conditions import outputs
from .const import BRIGHTNESS_MAX, HOUSE_ACTS, NUMERIC, OFF_STATES, PRIORITY, SEASON_MODES, SEASON_TARGET, UNDO_WHYS
from .entities import service_for
from .spec import C, act_spec, powering, purpose, restore_key, restores, scheduled, slot_of, spec_of
_LOGGER = logging.getLogger(__name__)

class RulesRunMixin:
    """Writes rule actions to their targets."""

    async def _go(self, rule: dict[str, Any], on: bool, v: float | None, why: str) -> None:
        rid = rule['id']
        st = self.st(rid)
        st['active'], st['pend'], st['since'] = (on, None, dv.now())
        st['confirmed'] = STATE_ON if on else STATE_OFF
        held = st['hold_until'] is not None
        if not on and st['hold_why'] == 'manual':
            st['hold_until'] = st['hold_why'] = None
            self._log(rid, 'normal_after_manual', value=v)
        else:
            self._log(rid, 'active' if on else 'normal', value=v, why=why)
        self._logbook(rule, self.explain(rule, self.ru))
        out = outputs(rule)
        if out['suggest'] and (on or (out['suggest_back'] and why == 'window')):
            await self._suggest(rule, on)
        elif st['sug'] is not None:
            st['sug'] = None
            self._dismiss(rule)
        if held or not out['auto']:
            return
        if not self._auto_ok(rule, on):
            self._log(rid, 'skip', result='period')
            return
        if rule.get('window'):
            self._log(rid, 'auto', result=STATE_ON if on else STATE_OFF)
        await self._apply(rule, back=not on, why=why)
        if not on:
            st['restore'] = {}

    async def _fault(self, rule: dict[str, Any], bad: str) -> None:
        rid = rule['id']
        st = self.st(rid)
        st['pend'] = None
        if st['fault'] == bad:
            return
        first = st['fault'] is None
        st['fault'] = bad
        self._cancel_todo(rid)
        self._log(rid, 'fault', why=bad)
        issues.raise_rule_issue(self.hass, 'rule_fault', rid, name=rule['name'], entity=self.who(rule['input']['entity_id']), reason=self.say(f'rules_reason_{bad}', self.ru))
        safe = purpose(rule['purpose']).get('safe')
        text = self.explain(rule, self.ru)
        self._logbook(rule, text)
        if safe == 'off':
            for a in rule['actions']:
                if powering(a) and self._power(a) is not False and spec_of(a['entity_id'], STATE_OFF):
                    await self._write(rule, {'do': STATE_OFF, 'entity_id': a['entity_id']}, why='safe', force=True)
            st['active'] = False
            st['restore'] = {}
        elif safe == 'notify' and first:
            await self._warn(rule, 'fault', text)

    async def _interlock(self, rule: dict[str, Any]) -> None:
        if not self._interlock_off(rule) or not rule['enabled']:
            return
        for a in rule['actions']:
            ts = self.hass.states.get(a.get('entity_id') or '')
            if powering(a) and spec_of(a['entity_id'], STATE_OFF) and (ts is not None) and (ts.state not in (*OFF_STATES, STATE_UNAVAILABLE)):
                self.st(rule['id'])['interlocked'] = self.st(rule['id'])['active']
                await self._write(rule, {'do': STATE_OFF, 'entity_id': a['entity_id']}, why='interlock', force=True)
                await self._warn(rule, 'interlock', self.say('rules_warn_interlock_off', self.ru, name=rule['name']))

    def _interlock_off(self, rule: dict[str, Any]) -> bool:
        if not (eid := rule.get('interlock')):
            return False
        s = self.hass.states.get(eid)
        return s is None or s.state != STATE_ON

    async def _apply(self, rule: dict[str, Any], *, back: bool, why: str) -> None:
        st = self.st(rule['id'])
        if back:
            acts = self._reverse(rule) if rule['back'] == 'reverse' else [] if rule['back'] == 'nothing' else rule['back']
        else:
            acts = rule['actions']
            for a in acts if not scheduled(rule) else []:
                if restores(a) and restore_key(a) not in st['restore']:
                    st['restore'][restore_key(a)] = {'do': a['do'], 'value': self.current(a['entity_id'], a['do'])}
        if back and why in UNDO_WHYS:
            acts = [a for a in acts if a['do'] != 'season']
        for a in acts:
            if not back and self._interlock_off(rule) and powering(a):
                await self._interlocked(rule, a)
                continue
            if await self._write(rule, a, why=why, back=back) and a.get('repeat'):
                self._repeat_arm(rule['id'], a, back, why, a['repeat']['count'] - 1)

    @callback
    def _repeat_arm(self, rid: str, a: dict[str, Any], back: bool, why: str, left: int) -> None:
        if left <= 0:
            return
        timers = self._repeat_timers.setdefault(rid, [])
        me: list[CALLBACK_TYPE] = []

        async def _again(_now: Any) -> None:
            if me and me[0] in timers:
                timers.remove(me[0])
            rule = self.rules.get(rid)
            if rule is None or not rule['enabled'] or a not in [*rule['actions'], *(rule['back'] if isinstance(rule['back'], list) else [])]:
                return
            if await self._write(rule, a, why=why, back=back):
                self._repeat_arm(rid, a, back, why, left - 1)
        me.append(async_call_later(self.hass, a['repeat']['every_s'], _again))
        timers.append(me[0])

    @callback
    def _repeat_stop(self, rid: str) -> None:
        for u in self._repeat_timers.pop(rid, []):
            u()

    async def _interlocked(self, rule: dict[str, Any], a: dict[str, Any]) -> None:
        self.st(rule['id'])['interlocked'] = True
        self._log(rule['id'], 'skip', entity_id=a.get('entity_id'), result='interlock')
        await self._warn(rule, 'interlock', self.say('rules_warn_interlock_skip', self.ru, name=rule['name']))

    def _reverse(self, rule: dict[str, Any]) -> list[dict[str, Any]]:
        st = self.st(rule['id'])
        out: list[dict[str, Any]] = []
        for a in rule['actions']:
            if a['do'] == 'season':
                out.append({'do': 'season', 'value': SEASON_MODES[1 - SEASON_MODES.index(a['value'])]})
                continue
            spec = act_spec(a) or {}
            old = st['restore'].get(a.get('entity_id'))
            r = st['restore'].get(restore_key(a)) if a['do'] not in HOUSE_ACTS else None
            r = r or (old if isinstance(old, dict) and old.get('do') == a['do'] else None)
            if spec.get('opposite'):
                out.append({'do': spec['opposite'], 'entity_id': a['entity_id']})
            elif spec.get('restore') and r and (r.get('value') is not None):
                out.append({'do': a['do'], 'entity_id': a['entity_id'], 'value': r['value']})
        return out

    def current(self, eid: str, do: str) -> Any:
        s = self.hass.states.get(eid)
        if s is None or s.state in dv.NOT_A_STATE:
            return None
        spec = spec_of(eid, do)
        read = spec.get('read') if spec else None
        a, off = (s.attributes, s.state in OFF_STATES)
        full = C('limits', 'percent')[1]
        if read is None:
            return None
        if read == 'power':
            return STATE_OFF if off else STATE_ON
        if read == 'closed':
            return STATE_CLOSED if off else STATE_OPEN
        if read == 'percentage':
            p = num(a.get('percentage'))
            return 0 if off else int(p) if p is not None else None
        if read == 'position':
            p = num(a.get('current_position'))
            return int(p) if p is not None else full if s.state == STATE_OPEN else 0 if off else None
        if read == 'brightness':
            b = num(a.get('brightness'))
            return 0 if off else round(b / BRIGHTNESS_MAX * full) if b is not None else full
        if read == 'volume':
            v = num(a.get('volume_level'))
            return round(v / spec['scale']) if v is not None else None
        v = s.state if read == 'state' else a.get(read)
        return num(v) if spec.get('value') in NUMERIC else v

    @staticmethod
    def _wanted(a: dict[str, Any]) -> Any:
        spec = act_spec(a) or {}
        return spec['want'] if 'want' in spec else a.get('value')

    @staticmethod
    def _power(a: dict[str, Any], state: str | None=None) -> bool | None:
        p = (act_spec(a) or {}).get('power')
        if p in (STATE_ON, STATE_OFF):
            return p == STATE_ON
        if p == 'value':
            v = a.get('value')
            return v != HVACMode.OFF if isinstance(v, str) else bool(v)
        if p == 'toggle' and state is not None and (state != STATE_UNKNOWN):
            return state in OFF_STATES
        return None

    def _controlled_by_higher(self, rule: dict[str, Any], eid: str, attr: str) -> dict[str, Any] | None:
        mine = PRIORITY[rule['priority']]
        for o in self.rules.values():
            p = PRIORITY[o['priority']]
            if o['id'] != rule['id'] and o['enabled'] and (p > mine or (scheduled(rule) and p == mine)) and self.st(o['id'])['active'] and ((eid, attr) in targets_of(o)):
                return o
        return None

    async def _write(self, rule: dict[str, Any], a: dict[str, Any], *, why: str, back: bool=False, force: bool=False) -> bool:
        rid, h = (rule['id'], C('helpers'))
        if a['do'] == 'notify':
            if not back:
                await self._notify(rule, a)
            return True
        if a['do'] == 'season':
            return await self._season(rule, a, why)
        eid = a['entity_id']
        domain = eid.split('.')[0]
        spec = spec_of(eid, a['do'])
        if spec is None or not may_change(self.hass, eid, self.owns, self.own_raw):
            self._log(rid, 'skip', entity_id=eid, result='not_allowed')
            return False
        s = self.hass.states.get(eid)
        if s is None or s.state == STATE_UNAVAILABLE:
            self._log(rid, 'skip', entity_id=eid, result='unavailable')
            return False
        if (o := self._controlled_by_higher(rule, eid, slot_of(a))) is not None:
            self._log(rid, 'skip', entity_id=eid, result=f"priority:{o['id']}")
            return False
        want = self._wanted(a)
        cur = self.current(eid, a['do'])
        if cur is not None and cur == want:
            self._log(rid, 'already', entity_id=eid, do=a['do'], value=want)
            return True
        t = dv.now()
        power = self._power(a, s.state)
        meta = purpose(rule['purpose'])
        if power is not None and (not force) and (s.state != STATE_UNKNOWN):
            on_now = s.state not in OFF_STATES
            gap = meta['min_on_s'] if on_now and (not power) else meta['min_off_s'] if not on_now and power else 0
            since = self.switched.get(eid, s.last_changed.timestamp())
            if gap and t - since < gap:
                return self._defer(rule, a, since + gap, back, 'min_time')
        if domain == 'climate' and (not force) and (eid in self.climate_at) and (t - self.climate_at[eid] < h['climate_gap_s']):
            return self._defer(rule, a, self.climate_at[eid] + h['climate_gap_s'], back, 'climate_gap')
        recent = [x for x in self.writes.get(eid, []) if t - x < h['rate_window_s']]
        self.writes[eid] = recent
        if len(recent) >= h['rate_max'] and a['do'] != 'close':
            await self._rate_hold(rule, eid, recent[0] + h['rate_window_s'])
            return False
        while self._bus and t - self._bus[0] >= h['bus_window_s']:
            self._bus.popleft()
        if len(self._bus) >= h['bus_cap'] and a['do'] != 'close':
            return self._defer(rule, a, self._bus[0] + h['bus_window_s'], back, 'bus')
        service, data = service_for(a)
        ctx = Context()
        self._ctx.append(ctx.id)
        self._sent[eid] = (t, want)
        self._bus.append(t)
        self.writes[eid] = [*recent, t]
        if domain == 'climate':
            self.climate_at[eid] = t
        entry = {'entity_id': eid, 'do': a['do'], 'value': a.get('value'), 'why': why}
        try:
            await self.hass.services.async_call(domain, service, {'entity_id': eid, **data}, blocking=True, context=ctx)
        except Exception as err:
            self._log(rid, 'error', **entry, result=str(err)[:int(C('engine', 'error_len'))])
            _LOGGER.warning('Hauflo rule %s: %s %s failed: %s', rid, service, eid, err)
            return False
        self._log(rid, 'write', **entry, result='ok')
        if spec.get('value') == 'seconds':
            self.pulses[eid] = {'rule': rid, 'until': t + a['value']}
            self._pulse_arm(eid)
        self._save_soon()
        return True

    @callback
    def _pulse_arm(self, eid: str) -> None:
        if (u := self._pulse_timers.pop(eid, None)) is not None:
            u()
        p = self.pulses[eid]

        async def _end(_now: Any) -> None:
            self._pulse_timers.pop(eid, None)
            await self._pulse_end(eid)
        self._pulse_timers[eid] = async_call_later(self.hass, max(0, p['until'] - dv.now()), _end)

    async def _pulse_end(self, eid: str) -> None:
        p = self.pulses.pop(eid, None)
        if (u := self._pulse_timers.pop(eid, None)) is not None:
            u()
        if p is None:
            return
        rid = p['rule'] if p['rule'] in self.rules else None
        s = self.hass.states.get(eid)
        if s is None or s.state in (*OFF_STATES, *dv.NOT_A_STATE) or (not self.owns(eid)):
            self._save_soon()
            return
        ctx = Context()
        self._ctx.append(ctx.id)
        self._sent[eid] = (dv.now(), STATE_OFF)
        entry = {'entity_id': eid, 'do': STATE_OFF, 'why': 'pulse'}
        try:
            await self.hass.services.async_call(eid.split('.')[0], 'turn_off', {'entity_id': eid}, blocking=True, context=ctx)
        except Exception as err:
            if rid:
                self._log(rid, 'error', **entry, result=str(err)[:int(C('engine', 'error_len'))])
            _LOGGER.warning('Hauflo pulse %s: off failed: %s', eid, err)
            return
        if rid:
            self._log(rid, 'write', **entry, result='ok')
        self._save_soon()

    async def _season(self, rule: dict[str, Any], a: dict[str, Any], why: str) -> bool:
        rid = rule['id']
        if self.season_set is None:
            self._log(rid, 'skip', entity_id=SEASON_TARGET, result='not_allowed')
            return False
        if self.season_now() == a['value']:
            self._log(rid, 'already', entity_id=SEASON_TARGET, do='season', value=a['value'])
            return True
        try:
            done = await self.season_set(a['value'], why)
        except Exception as err:
            self._log(rid, 'error', entity_id=SEASON_TARGET, do='season', value=a['value'], why=why, result=str(err)[:int(C('engine', 'error_len'))])
            return False
        self._log(rid, 'write', entity_id=SEASON_TARGET, do='season', value=a['value'], why=why, result=done if isinstance(done, str) else 'ok')
        return True
