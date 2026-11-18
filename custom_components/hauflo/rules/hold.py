"""Holds, quiet times and deferred actions of the rules."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.core import Event, callback
from homeassistant.util import dt as dt_util
from .. import derived as dv, issues, quiet
from ..guard import contained
from .const import OFF_STATES, TODO
from .spec import C, act_spec, powering, scheduled
_LOGGER = logging.getLogger(__name__)

class RulesHoldMixin:
    """Holds and defers rule actions."""

    async def _rate_hold(self, rule: dict[str, Any], eid: str, until: float) -> None:
        rid = rule['id']
        st = self.st(rid)
        st['hold_until'], st['hold_why'] = (until, 'rate')
        self._log(rid, 'hold', entity_id=eid, result='rate', until=until)
        issues.raise_rule_issue(self.hass, 'rule_rate', rid, name=rule['name'], entity=self.who(eid))
        await self._warn(rule, 'rate', self.say('rules_warn_rate', self.ru, name=rule['name'], entity=self.who(eid), n=int(C('helpers', 'rate_max')), time=self._hhmm(until)))
        self._refresh(rid)

    def _defer(self, rule: dict[str, Any], a: dict[str, Any], due: float, back: bool, why: str) -> bool:
        self.todo = [x for x in self.todo if not (x['rule'] == rule['id'] and x['action'].get('entity_id') == a.get('entity_id'))]
        self.todo.append({'rule': rule['id'], 'action': a, 'due': due, 'back': back, 'why': why})
        self._log(rule['id'], 'wait', entity_id=a.get('entity_id'), result=why, until=due)
        self._wake(TODO, due - dv.now())
        self._save_soon()
        return False

    def _cancel_todo(self, rid: str) -> None:
        self.todo = [x for x in self.todo if x['rule'] != rid]

    async def _run_todo(self) -> None:
        t = dv.now()
        due = [x for x in self.todo if x['due'] <= t]
        self.todo = [x for x in self.todo if x['due'] > t]
        for x in due:
            rule = self.rules.get(x['rule'])
            if rule is None:
                continue
            st = self.st(rule['id'])
            if st['active'] == x['back'] and (not scheduled(rule)) or st['hold_until'] or st['paused'] or (not rule['enabled']):
                continue
            if not x['back'] and self._interlock_off(rule) and powering(x['action']):
                await self._interlocked(rule, x['action'])
                continue
            await self._write(rule, x['action'], why=x['why'], back=x['back'])
        if self.todo:
            self._wake(TODO, min((x['due'] for x in self.todo)) - t)

    def _bypass(self, rule: dict[str, Any]) -> bool:
        v = rule.get('quiet_ok')
        return v if isinstance(v, bool) else rule['priority'] in C('quiet', 'bypass_priorities')

    def _quiet(self, rule: dict[str, Any]) -> bool:
        return not self._bypass(rule) and quiet.inside(self.quiet, dt_util.utc_from_timestamp(dv.now()))

    def _quiet_hold(self, rule: dict[str, Any], item: dict[str, Any]) -> None:
        st = self.st(rule['id'])
        st['held'] = [h for h in st['held'] if h['key'] != item['key']] + [{**item, 'at': dv.now()}]
        self._log(rule['id'], 'quiet_hold', result=item['k'])
        self._save_soon()

    def _still(self, rule: dict[str, Any], h: dict[str, Any]) -> bool:
        st = self.st(rule['id'])
        if not rule['enabled']:
            return False
        if h['k'] == 'notify':
            return bool(st['active']) or scheduled(rule)
        return st['fault'] is not None if h['kind'] == 'fault' else self._interlock_off(rule) if h['kind'] == 'interlock' else True

    async def _release_held(self) -> None:
        for rid, rule in list(self.rules.items()):
            st = self.st(rid)
            if not st['held'] or self._quiet(rule):
                continue
            held, st['held'] = (st['held'], [])
            for h in held:
                if not self._still(rule, h):
                    self._log(rid, 'quiet_drop', result=h['k'])
                    continue
                self._log(rid, 'quiet_release', result=h['k'])
                if h['k'] == 'notify':
                    await self._notify(rule, h['a'], late=True)
                else:
                    await self._warn(rule, h['kind'], h['text'], late=True)
            self._save_soon()

    async def set_quiet(self, raw: Any) -> dict[str, Any]:
        self.quiet = quiet.clean(raw, self.quiet)
        await self._save()
        await self._release_held()
        return self.quiet_out()

    def quiet_out(self) -> dict[str, Any]:
        return {**self.quiet, 'default': quiet.default(), 'bypass_priorities': C('quiet', 'bypass_priorities')}

    async def _notify(self, rule: dict[str, Any], a: dict[str, Any], *, late: bool=False) -> None:
        if not late and self._quiet(rule):
            self._quiet_hold(rule, {'key': f"notify:{a['text']}", 'k': 'notify', 'a': a})
            return
        self._log(rule['id'], 'notify', result=a['to'])
        if self.tell is not None:
            with contained(_LOGGER, 'Hauflo rule %s: notify failed: %s', rule['id'], err=True):
                await self.tell(rule['name'], a['text'], rule.get('user_id') if a['to'] == 'me' else None)

    async def _warn(self, rule: dict[str, Any], kind: str, text: str, *, late: bool=False) -> None:
        if not late and self._quiet(rule):
            self._quiet_hold(rule, {'key': f'warn:{kind}', 'k': 'warn', 'kind': kind, 'text': text})
            return
        key = f"{rule['id']}:{kind}"
        t = dv.now()
        if key in self._warned and t - self._warned[key] < C('engine', 'warn_gap_s'):
            return
        self._warned[key] = t
        self._log(rule['id'], 'warn', result=kind)
        self._logbook(rule, text)
        if self.tell is not None:
            with contained(_LOGGER, 'Hauflo rule %s: warning not sent: %s', rule['id'], err=True):
                await self.tell(rule['name'], text, None)

    @callback
    def _target_changed(self, event: Event) -> None:
        new, old = (event.data.get('new_state'), event.data.get('old_state'))
        if new is None or old is None or new.state in dv.NOT_A_STATE or (old.state in dv.NOT_A_STATE):
            return
        eid, ctx = (new.entity_id, new.context)
        if (new.state in OFF_STATES) != (old.state in OFF_STATES):
            self.switched[eid] = new.last_changed.timestamp()
            self._save_soon()
        if ctx.id in self._ctx or (ctx.parent_id and ctx.parent_id in self._ctx):
            return
        for rid, rule in list(self.rules.items()):
            if rule.get('interlock') and new.state not in OFF_STATES and any((a.get('entity_id') == eid for a in rule['actions'])):
                self.hass.async_create_task(self._interlock(rule), 'hauflo interlock')
            st = self.st(rid)
            if not rule['enabled'] or st['paused'] or (not st['active']) or (st['hold_why'] == 'manual'):
                continue
            for a in rule['actions']:
                if a.get('entity_id') != eid or (act_spec(a) or {}).get('value') == 'seconds':
                    continue
                cur = self.current(eid, a['do'])
                if cur is None or cur == self._wanted(a):
                    continue
                sent = self._sent.get(eid)
                if sent is not None and dv.now() - sent[0] <= C('engine', 'echo_s') and (cur == sent[1]):
                    continue
                self._hold(rule, eid, 'user' if ctx.user_id else 'bus')
                break

    @callback
    def _hold(self, rule: dict[str, Any], eid: str, who: str) -> None:
        rid, h = (rule['id'], C('helpers'))
        st = self.st(rid)
        t = dv.now()
        st['hold_until'], st['hold_why'] = (t + h['manual_hold_s'], 'manual')
        st['holds'] = [x for x in st['holds'] if t - x < h['pause_window_s']] + [t]
        self._cancel_todo(rid)
        self._log(rid, 'manual', entity_id=eid, result=who, until=st['hold_until'])
        if len(st['holds']) >= h['pause_holds']:
            st['paused'] = True
            self._log(rid, 'paused')
            issues.raise_rule_issue(self.hass, 'rule_paused', rid, name=rule['name'], entity=self.who(eid))
            self.hass.async_create_task(self._warn(rule, 'paused', self.say('rules_warn_paused', self.ru, name=rule['name'], entity=self.who(eid))), 'hauflo rule warn')
        self._logbook(rule, self.explain(rule, self.ru))
        self._refresh(rid)
        self._save_soon()

    @staticmethod
    def _hhmm(ts: float | None) -> str:
        return dt_util.as_local(dt_util.utc_from_timestamp(ts)).strftime('%H:%M') if ts else '—'
