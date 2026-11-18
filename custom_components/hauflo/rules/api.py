"""Saves, lists, tunes and deletes rules."""
from __future__ import annotations
from typing import Any
from .. import derived as dv, issues
from ..derived import Invalid
from ..notify.common import label
from ..ownership import entity_room, room_name
from .actions import check_house, clean_rule, in_bounds
from .answer import blank, fmt, redact
from .checks import may_change, number
from .conditions import clean_outputs, client_class_ok, outputs, supports
from .const import FEATURES, IS
from .entities import derived_entity_id
from .spec import C, client_can, client_locked, scheduled

class RulesApiMixin:
    """Rule commands of the HTTP view."""

    def rule_out(self, rid: str, full: bool, tunnel: bool=False) -> dict[str, Any]:
        rule, st = (self.rules[rid], self.st(rid))
        b, s = (self.binaries.get(rid), self.switches.get(rid))
        out: dict[str, Any] = {'id': rid, 'name': rule['name'], 'name_ru': self.display_name(rule, True), 'name_en': self.display_name(rule, False), 'enabled': rule['enabled'], 'state': self.status(rid), 'active': st['active'], 'value': st.get('value'), 'unit': rule['input'].get('unit') if 'input' in rule else None, 'why': self.explain(rule, True), 'why_en': self.explain(rule, False), 'hold_until': st['hold_until'], 'since': st['since'], 'client': rule['client'], 'client_can': client_can(rule), 'purpose': rule['purpose'], 'priority': rule.get('priority'), 'owner': rule.get('owner', 'integrator'), 'entities': {'active': b.entity_id if b else None, 'switch': s.entity_id if s else None}, **{k: rule[k] for k in ('when', 'x', 'lo', 'hi', 'y', 'at', IS, 'not') if k in rule}, **({k: st.get(k) for k in ('next_at', 'last_at', 'last_result')} if scheduled(rule) else {}), **self.hint(rule['purpose']), 'outputs': outputs(rule), 'quiet_bypass': self._bypass(rule)}
        if rule.get('window'):
            data = self.win.get(rid) or {}
            k = int(C('window', 'values_out'))
            out.update(window=rule['window'], values=(data.get('values') or [])[-k:], starts=(data.get('starts') or [])[-k:], periods=st.get('periods', 0), no_data=bool(st.get('no_data')))
        if (sug := self.suggestion(rid)) is not None:
            out['suggestion'] = {'on': sug['on'], 'sent': sug['sent']}
        elif st['sug'] is not None and st['sug']['snooze_until']:
            out['suggestion'] = {'on': st['sug']['on'], 'snoozed_until': st['sug']['snooze_until']}
        if full:
            out['rule'] = rule
            out['log'] = st['log'][-int(C('engine', 'log_out')):]
        if tunnel and self.cloud_hidden(rule) is not None:
            out = redact(out)
        return out

    def recipes(self) -> list[dict[str, Any]]:
        return [{**r, 'title': self.say(f"rules_recipe_{r['id']}", True), 'title_en': self.say(f"rules_recipe_{r['id']}", False), **self.hint(r['purpose'])} for r in C('recipes')]

    def links(self) -> list[dict[str, Any]]:
        """Recipes another part of Hauflo already makes (the silent sensor: the rule hints) — shown, not duplicated."""
        return [{**r, 'title': self.say(f"rules_recipe_{r['id']}", True), 'title_en': self.say(f"rules_recipe_{r['id']}", False), 'text': self.say(f"rules_link_{r['id']}", True), 'text_en': self.say(f"rules_link_{r['id']}", False)} for r in C('links')]

    def schedule_recipes(self) -> list[dict[str, Any]]:
        return [{**r, 'title': self.say(f"rules_recipe_{r['id']}", True), 'title_en': self.say(f"rules_recipe_{r['id']}", False)} for r in C('schedule_recipes')]

    def targets(self, tunnel: bool=False, client: bool=False) -> list[dict[str, Any]]:
        """What a rule may change (the app's picker: room → device → action → value): Hauflo's own of the canon's
        domains (may_change), with the attributes its actions take their options / bounds from. «dos» — exactly what a
        save takes (clean_action): the canon's actions the entity supports (supports), a client's only the client's; a
        device with nothing to do is not offered. «room» — the room's name (HA's area registry), «area» its id."""
        table = C('actions_by_domain')
        acts = set(C('actions', 'client_acts')) if client else None
        domains = [d for d in table if not client or d in C('actions', 'client_domains')]
        out: list[dict[str, Any]] = []
        for s in self.hass.states.async_all(domains):
            eid, domain = (s.entity_id, s.domain)
            if not may_change(self.hass, eid, self.owns, self.own_raw) or (client and (not client_class_ok(self.hass, eid))):
                continue
            dos = [do for do, spec in table[domain].items() if supports(s, spec) and (acts is None or do in acts)]
            if not dos:
                continue
            names = {n for spec in table[domain].values() for n in (spec.get('options'), spec.get('step'), *(spec.get('bounds') or ()))} - {None}
            seen = not tunnel or self.cloud_reads(eid, None)
            out.append({'entity_id': eid, 'domain': domain, 'name': label(self.hass, eid, s), 'area': entity_room(self.hass, eid), 'room': room_name(self.hass, eid), 'state': s.state if seen else None, 'attrs': {n: s.attributes[n] for n in sorted(names) if n in s.attributes}, 'dos': dos, 'now': {do: self.current(eid, do) for do in dos if seen and table[domain][do].get('read')}, 'reads': self.reads_out(s)})
        return sorted(out, key=lambda x: (x['room'] is None, x['room'] or '', x['name']))

    def answer(self, admin: bool, tunnel: bool=False) -> dict[str, Any]:
        visible = [rid for rid, r in self.rules.items() if admin or r['client'].get('visible', True)]
        derived = []
        if admin:
            hidden = self.cloud_hidden_entities() if tunnel else set()
            for did, cfg in self.derived_cfg.items():
                d, s = (self.derived.get(did), self.sensors.get(did))
                item = {**cfg, 'entity_id': s.entity_id if s else derived_entity_id(cfg), 'value': d.value if d else None, 'unit': d.unit if d else None, 'why': d.why if d else None}
                derived.append({**item, 'value': None, 'why': None} if item['entity_id'] in hidden else item)
        return {'rules': [self.rule_out(rid, admin or self.rules[rid].get('owner') == 'client', tunnel) for rid in visible], 'derived': derived, 'quiet': self.quiet_out(), 'outdoor': dv.outdoor(self.hass, self.owns), 'recipes': self.recipes() if admin else [], 'schedule_recipes': self.schedule_recipes() if admin else [], 'targets': self.targets(tunnel, client=not admin), 'features': list(FEATURES), 'links': self.links() if admin else [], **({'bus_reads': self.bus_reads_out()} if admin else {}), 'limits': {**{k: C('limits', k) for k in ('rules', 'actions', 'conditions', 'for_min')}, 'window': {k: C('window', k) for k in ('stats', 'periods', 'n')}, 'thresholds': C('thresholds'), 'snooze_days': C('suggest', 'snooze_bounds'), 'schedule': C('schedule'), 'pulse_s': C('actions', 'pulse_s'), 'text_len': C('actions', 'text_len'), 'repeat': {k: C('actions', 'repeat', k) for k in ('count', 'every_s', 'acts')}, 'is_max': C('limits', 'is_max')}, 'actions_by_domain': C('actions_by_domain'), 'client_actions': {'acts': C('actions', 'client_acts'), 'domains': C('actions', 'client_domains')}}

    def log_of(self, rid: str, tunnel: bool=False) -> list[dict[str, Any]]:
        if rid not in self.rules:
            return []
        log = list(self.st(rid)['log'])
        return redact({'log': log})['log'] if tunnel and self.cloud_hidden(self.rules[rid]) is not None else log

    async def save(self, raw: Any, *, client: bool, user_id: str | None, tunnel: bool=False) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise Invalid('bad', 'rules_bad_object')
        if client and raw.get('id') in self.rules and (self.rules[raw['id']].get('owner') != 'client'):
            raise Invalid('not_allowed', 'rules_client_integrator_rule')
        if user_id and 'user_id' not in raw:
            raw = {**raw, 'user_id': user_id}
        rule = clean_rule(raw, self._env(client))
        if tunnel:
            self._cloud_check(rule)
        check_house(rule, [r for k, r in self.rules.items() if k != rule['id']], self.derived_sources)
        self.unique_name(rule)
        old = self.rules.get(rule['id'])
        if old is not None and {k: v for k, v in old.items() if k != 'outputs'} == {k: v for k, v in rule.items() if k != 'outputs'}:
            return await self.set_outputs(rule['id'], rule.get('outputs'), tunnel)
        if old is not None and self.st(rule['id'])['active']:
            await self._go(old, False, None, 'changed')
        log = self.st(rule['id'])['log']
        self.rules[rule['id']] = rule
        self.state[rule['id']] = {**blank(), 'log': log}
        self._cancel_todo(rule['id'])
        for kind in issues.RULE_KINDS:
            issues.clear_issue(self.hass, f"{kind}_{rule['id']}")
        self._log(rule['id'], 'saved')
        await self._save()
        self.sync_entities()
        self._listen()
        self.win.pop(rule['id'], None)
        if rule.get('window'):
            await self.window_refresh(rule['id'], 'saved')
        else:
            self._window_schedule(rule['id'])
            await self.evaluate(rule['id'], 'saved')
        return self.rule_out(rule['id'], True, tunnel)

    def existing_rule(self, rid: Any) -> dict[str, Any]:
        rule = self.rules.get(rid) if isinstance(rid, str) else None
        if rule is None:
            raise Invalid('no_rule', 'rules_no_rule')
        return rule

    async def delete(self, rid: Any) -> None:
        rule = self.existing_rule(rid)
        if self.st(rid)['active']:
            await self._go(rule, False, None, 'deleted')
        if self.st(rid)['sug'] is not None:
            self._dismiss(rule)
        self.rules.pop(rid)
        self._repeat_stop(rid)
        self.state.pop(rid, None)
        self.win.pop(rid, None)
        self._cancel_todo(rid)
        for timers in (self._timers, self._win_timers):
            if (u := timers.pop(rid, None)) is not None:
                u()
        for kind in issues.RULE_KINDS:
            issues.clear_issue(self.hass, f'{kind}_{rid}')
        await self._save()
        self.sync_entities()
        self._listen()

    async def set_outputs(self, rid: Any, raw: Any, tunnel: bool=False) -> dict[str, Any]:
        """What the rule does when it changes (its HA entity always; a suggestion; its actions by itself) — the rule
        keeps
        its state: switching «auto» on or off neither restarts it nor undoes / redoes anything now."""
        rule = self.existing_rule(rid)
        rule['outputs'] = clean_outputs(raw, bool(rule.get('window')))
        st = self.st(rid)
        if not rule['outputs']['suggest'] and st['sug'] is not None:
            st['sug'] = None
            self._dismiss(rule)
        self._log(rid, 'outputs', result=','.join((k for k, v in rule['outputs'].items() if v)) or '-')
        await self._save()
        self._refresh(rid)
        return self.rule_out(rid, True, tunnel)

    async def set_enabled(self, rid: Any, on: Any, tunnel: bool=False) -> dict[str, Any]:
        rule = self.existing_rule(rid)
        if not isinstance(on, bool):
            raise Invalid('bad', 'rules_bad_object')
        st = self.st(rid)
        if not on and st['active']:
            await self._go(rule, False, None, 'switched_off')
        rule['enabled'] = on
        st['next_at'] = st['off_at'] = None
        if on:
            st.update(paused=False, hold_until=None, hold_why=None, holds=[], pend=None)
            for kind in ('rule_paused', 'rule_rate'):
                issues.clear_issue(self.hass, f'{kind}_{rid}')
        self._log(rid, 'enabled' if on else 'disabled')
        await self._save()
        await self.evaluate(rid, 'enabled')
        self._refresh(rid)
        return self.rule_out(rid, True, tunnel)

    async def tune(self, rid: Any, body: dict[str, Any], *, client: bool, tunnel: bool=False) -> dict[str, Any]:
        rule = self.existing_rule(rid)
        keys = ('x',) if rule['when'] in ('below', 'above') else ('lo', 'hi') if rule['when'] in ('outside', 'between') else ()
        if not keys:
            raise Invalid('bad', 'rules_no_threshold')
        new = {**rule, **{k: number(body[k], k) for k in keys if k in body}}
        c = rule['client']
        if client and rule.get('owner') != 'client':
            if 'lo' not in c or client_locked(rule):
                raise Invalid('not_allowed', 'rules_tune_not_allowed')
            if any((not c['lo'] <= new[k] <= c['hi'] for k in keys)):
                raise Invalid('out_of_range', 'rules_out_of_range', lo=fmt(c['lo'], None), hi=fmt(c['hi'], None))
        if 'lo' in keys and new['lo'] >= new['hi']:
            raise Invalid('bad_range', 'rules_bad_range')
        in_bounds(new)
        self.rules[rid] = new
        self._log(rid, 'tuned', **{k: new[k] for k in keys})
        await self._save()
        await self.evaluate(rid, 'tuned')
        return self.rule_out(rid, not client or rule.get('owner') == 'client', tunnel)
