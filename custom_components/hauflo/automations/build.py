"""Validates a simple automation and builds its HA config."""
from __future__ import annotations
import logging
import math
import secrets
from typing import Any
from homeassistant.components.climate import ClimateEntityFeature
from ..guard import contained
from ..rules import actions as rules_actions, spec as rules_spec
from .common import ALARM_CODE, ALARM_EVENT, DAYS, EVENTS, FROM, LEGACY_DO, LEGACY_MODES, LEGACY_SET, MAX_ACTIONS, MAX_OFFSET, MEDIA, RAMP, RAMP_STEPS, RUN_VAR, TIME, Invalid, NotPlain, bad, bounded_int, clean_name, client_acts, climate_step, group_domains, max_for_min, max_ramp_min, max_ramps, max_set, offset_text, state_all_margin_s
_LOGGER = logging.getLogger(__name__)

class AutomationsBuildMixin:
    """Builds HA automation configs."""

    def validate(self, raw: Any, previous: dict[str, Any] | None=None) -> dict[str, Any]:
        """A rule as the dashboard sends it → the stored rule; anything else is refused."""
        if not isinstance(raw, dict):
            raise bad('rule')
        when = raw.get('when')
        if not isinstance(when, dict) or when.get('event') not in EVENTS:
            raise bad('when.event')
        rule_when: dict[str, Any] = {'event': when['event']}
        if when['event'] == 'value':
            rule_when.update(self._value_spec(when, 'when'))
            rule_when['for_min'] = bounded_int(when.get('for_min', 0), 0, max_for_min(), 'for_min')
        elif when['event'] == 'state':
            rule_when.update(self._state_spec(when))
        elif when['event'] == 'presence':
            if when.get('to') not in ('away', 'home'):
                raise bad('when.to')
            rule_when['to'] = when['to']
        elif when['event'] == 'time':
            times = when.get('times', [when.get('at')] if 'at' in when else None)
            n = rules_spec.C('schedule', 'moments')
            if not isinstance(times, list) or not 1 <= len(times) <= n or (not all((isinstance(t, str) and TIME.fullmatch(t) for t in times))) or (len(set(times)) != len(times)):
                raise Invalid('bad_times', n=n)
            rule_when['times'] = sorted(times)
        elif when['event'] == 'alarm_iphone':
            code = (previous or {}).get('when', {}).get('code')
            rule_when['code'] = code if isinstance(code, str) and ALARM_CODE.fullmatch(code) else ALARM_EVENT + secrets.token_hex(3)
        else:
            rule_when['offset'] = bounded_int(when.get('offset', 0), -MAX_OFFSET, MAX_OFFSET, 'offset')
            if when['event'] == 'alarm_android':
                sensor = when.get('sensor')
                if not self._alarm_sensor(sensor):
                    raise Invalid('bad_alarm_sensor')
                rule_when['sensor'] = sensor
        days = raw.get('days', list(DAYS))
        if not isinstance(days, list) or not days or any((d not in DAYS for d in days)) or (len(set(days)) != len(days)):
            raise Invalid('bad_days')
        actions = raw.get('actions')
        if not isinstance(actions, list) or not actions or len(actions) > MAX_ACTIONS:
            raise Invalid('bad_actions', n=MAX_ACTIONS)
        clean = [self._clean_act(a) for a in actions]
        if sum((1 for a in clean if a['do'] == RAMP)) > max_ramps():
            raise Invalid('bad_ramps', n=max_ramps())
        enabled = raw.get('enabled', True)
        if not isinstance(enabled, bool):
            raise bad('enabled')
        origin = raw.get('from')
        if origin is not None and (not isinstance(origin, str) or not FROM.fullmatch(origin)):
            raise bad('from')
        out = {'when': rule_when, 'days': [d for d in DAYS if d in days], 'actions': clean, 'enabled': enabled, 'name': clean_name(raw.get('name'))}
        if (only_if := self._only_if(raw.get('only_if'))):
            self._never(rule_when, only_if)
            out['only_if'] = only_if
        if origin:
            out['from'] = origin
        return out

    def _legacy(self, a: dict[str, Any]) -> dict[str, Any]:
        do = a.get('do')
        value = {'value': a['value']} if a.get('value') is not None else {}
        if do == 'set':
            domain, cmd = (a.get('domain'), a.get('cmd'))
            if domain not in LEGACY_SET:
                raise bad('set.domain')
            rooms, entities = (a.get('rooms') or [], a.get('entities') or [])
            if not isinstance(rooms, list) or not isinstance(entities, list):
                raise bad('set.rooms')
            if domain == 'scene' and (rooms or a.get('all')):
                raise bad('set.scene')
            t: dict[str, Any] = {'domain': domain}
            if a.get('all') is True:
                t['all'] = True
            if rooms:
                t['area_id'] = rooms
            if entities:
                t['entity_id'] = entities
            return {'do': self._legacy_do(domain, cmd, value), 'target': t, **(value if cmd != RAMP else {})}
        if do == 'room_off':
            return {'do': 'off', 'target': {'domain': 'light', 'area_id': a.get('area_id')}}
        if do == 'room_covers':
            cmd = a.get('cover')
            if cmd not in ('open', 'close', 'position'):
                raise bad('room_covers.cover')
            return {'do': cmd, 'target': {'domain': 'cover', 'area_id': a.get('area_id')}, **(value if cmd == 'position' else {})}
        eid = a.get('entity_id')
        if not isinstance(eid, str):
            raise bad('entity_id')
        domain = eid.split('.')[0]
        if domain == 'climate' and do in LEGACY_MODES:
            return {'do': 'mode', 'target': {'entity_id': eid}, 'value': do}
        out: dict[str, Any] = {'do': self._legacy_do(domain, do, value), 'target': {'entity_id': eid}, **value}
        if do == RAMP:
            out['minutes'] = a.get('minutes')
        if a.get('repeat') is not None:
            out['repeat'] = a['repeat']
        return out

    @staticmethod
    def _legacy_do(domain: Any, do: Any, value: dict[str, Any]) -> Any:
        if domain == 'light' and do == 'on' and value:
            bounded_int(value['value'], 1, 100, 'brightness')
            return 'brightness'
        return LEGACY_DO.get(domain, {}).get(do, do)

    def _clean_act(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise bad('action')
        if 'target' not in raw:
            raw = self._legacy(raw)
        elif set(raw) - {'do', 'target', 'value', 'minutes', 'repeat'}:
            raise bad('action.keys')
        t, do = (raw.get('target'), raw.get('do'))
        if not isinstance(t, dict) or not t or set(t) - {'entity_id', 'area_id', 'domain', 'all'} or (t.get('all', True) is not True):
            raise bad('target')
        ents, areas, every = (t.get('entity_id'), t.get('area_id'), t.get('all') is True)
        el = [ents] if isinstance(ents, str) else ents if ents is not None else []
        al = [areas] if isinstance(areas, str) else areas if areas is not None else []
        if not isinstance(el, list) or not isinstance(al, list) or (not all((isinstance(x, str) for x in (*el, *al)))) or (len(set(el)) != len(el)) or (len(set(al)) != len(al)):
            raise bad('target.lists')
        if not (el or al or every):
            raise Invalid('bad_target_empty')
        if len(el) + len(al) > max_set():
            raise Invalid('bad_target_many', n=max_set())
        if (isinstance(ents, str) or isinstance(areas, str)) and sum(map(bool, (el, al, every))) > 1:
            raise bad('target.one')
        kinds = {e.split('.')[0] for e in el}
        domain = t.get('domain') or (next(iter(kinds)) if len(kinds) == 1 else None)
        if not isinstance(domain, str) or kinds - {domain}:
            raise Invalid('bad_target_kinds')
        if (al or every) and domain not in group_domains():
            raise Invalid('bad_target_group', kinds=list(group_domains()))
        for e in el:
            if not self.pickable(e):
                raise Invalid('no_device', entity=e)
        out_t: dict[str, Any] = {'domain': domain} if al or every else {}
        if every:
            out_t['all'] = True
        if al:
            out_t['area_id'] = areas if isinstance(areas, str) else sorted(al)
        if el:
            out_t['entity_id'] = ents if isinstance(ents, str) else sorted(el)
        if do == RAMP:
            if domain != 'light' or al or every or (not all((self._dims(e) for e in el))):
                raise Invalid('bad_ramp')
            if raw.get('repeat') is not None:
                raise Invalid('bad_ramp_repeat')
            return {'do': RAMP, 'target': out_t, 'value': bounded_int(raw.get('value'), 1, 100, 'brightness'), 'minutes': bounded_int(raw.get('minutes'), 1, max_ramp_min(), 'ramp_minutes')}
        acts = client_acts(domain)
        if (spec := acts.get(do)) is None:
            raise Invalid('bad_do', kind=domain, acts=list(acts))
        kind = spec.get('value')
        value = raw.get('value')
        try:
            if kind == 'option' and (not el):
                if not isinstance(value, str) or not value:
                    raise Invalid('bad_option')
            elif kind:
                value = rules_actions.action_value(kind, spec, do, value, self.hass.states.get(el[0]) if el else None)
            else:
                value = None
            check = 'on' if domain == 'light' and do == 'brightness' else do
            for e in el:
                rules_actions.clean_action({'do': check, 'entity_id': e, **({'value': value} if kind and check == do else {})}, self._env)
            repeat = rules_actions.clean_repeat(raw.get('repeat'), do, value if kind == 'seconds' else None)
        except Invalid:
            raise
        except rules_actions.Invalid as err:
            raise Invalid(err.code, err.key, **err.ph) from err
        for room in al:
            if not self._members(domain, room, do, value):
                raise Invalid('bad_room_empty', room=room, kind=domain, do=do)
        if every and (not any(self._all_target(domain, do, value))):
            raise Invalid('bad_all_empty', kind=domain, do=do)
        out: dict[str, Any] = {'do': do, 'target': out_t}
        if value is not None:
            out['value'] = value
        if repeat:
            out['repeat'] = repeat
        return out

    def _climate_spec(self, eid: str) -> dict[str, Any]:
        a = self.hass.states.get(eid).attributes
        lo, hi, step = (a.get('min_temp'), a.get('max_temp'), a.get('target_temp_step'))
        ok = lambda v: isinstance(v, (int, float)) and (not isinstance(v, bool)) and math.isfinite(v)
        lo, hi = (float(lo), float(hi)) if ok(lo) and ok(hi) and (lo < hi) else (None, None)
        temp = lo is not None and bool(int(a.get('supported_features') or 0) & ClimateEntityFeature.TARGET_TEMPERATURE)
        return {'temp': temp, 'min': lo, 'max': hi, 'step': float(step) if ok(step) and step > 0 else climate_step(), 'modes': self._modes(eid), 'value': a.get('temperature')}

    @staticmethod
    def _ramp(a: dict[str, Any]) -> list[dict[str, Any]]:
        steps = min(RAMP_STEPS, a['minutes'] * 2, a['value'])
        pause = round(a['minutes'] * 60 / steps)
        out: list[dict[str, Any]] = []
        for i in range(1, steps + 1):
            if i > 1:
                out.append({'delay': {'seconds': pause}})
            out.append({'action': 'light.turn_on', 'target': {'entity_id': a['target']['entity_id']}, 'data': {'brightness_pct': max(1, round(a['value'] * i / steps))}})
        return out

    def _steps(self, a: dict[str, Any]) -> list[dict[str, Any]]:
        if (target := self._target(a)) is None:
            return []
        domain, do = (self._domain(a['target']), a['do'])
        spec = rules_spec.C('actions_by_domain')[domain][do]
        call: dict[str, Any] = {'action': f"{domain}.{spec['service']}", 'target': target}
        if spec.get('field') and a.get('value') is not None:
            call['data'] = {spec['field']: a['value']}
        steps = [call]
        pulse = a['value'] if spec.get('value') == 'seconds' else 0
        if pulse:
            steps += [{'delay': {'seconds': pulse}}, {'action': f"{domain}.{rules_spec.C('actions_by_domain')[domain]['off']['service']}", 'target': target}]
        if (rep := a.get('repeat')):
            steps = [{'repeat': {'count': rep['count'], 'sequence': [*steps, {'delay': {'seconds': rep['every_s'] - pulse}}]}}]
        return steps

    def _service_calls(self, rule: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        ramps = []
        for a in rule['actions']:
            a = a if 'target' in a else self._legacy(a)
            if a['do'] == RAMP:
                ramps.append(self._ramp(a))
            else:
                out += self._steps(a)
        if len(ramps) == 1:
            out += ramps[0]
        elif ramps:
            out.append({'parallel': [{'sequence': r} for r in ramps]})
        return out

    def automation(self, rule_id: str, rule: dict[str, Any]) -> dict[str, Any]:
        """The HA automation of a rule (the new syntax: triggers / conditions / actions)."""
        when = rule['when']
        also: list[dict[str, Any]] = []
        if when['event'] == 'time':
            at = [t + ':00' for t in when.get('times') or [when['at']]]
            trigger: dict[str, Any] = {'trigger': 'time', 'at': at[0] if len(at) == 1 else at}
        elif when['event'] == 'alarm_iphone':
            trigger = {'trigger': 'event', 'event_type': when['code']}
        elif when['event'] == 'alarm_android':
            trigger = {'trigger': 'time', 'at': {'entity_id': when['sensor'], 'offset': offset_text(when['offset'])}}
        elif when['event'] == 'value':
            trigger = {'trigger': 'numeric_state', 'entity_id': when['entity'], when['op']: when['x']}
            if when.get('for_min'):
                trigger['for'] = {'minutes': when['for_min']}
        elif when['event'] == 'state':
            trigger, also = self._state_trigger(when)
        elif when['event'] == 'presence':
            trigger = {'trigger': 'numeric_state', 'entity_id': 'zone.home', **({'below': 1} if when['to'] == 'away' else {'above': 0})}
        else:
            trigger = {'trigger': 'sun', 'event': when['event']}
            if when['offset']:
                trigger['offset'] = offset_text(when['offset'])
        ru = self._ru(rule)
        conditions = also + ([{'condition': 'time', 'weekday': list(rule['days'])}] if len(rule['days']) < 7 else []) + [self._condition(c) for c in rule.get('only_if', [])]
        actions = self._service_calls(rule)
        if when['event'] == 'alarm_iphone':
            if conditions:
                skip = {'condition': 'template', 'value_template': f'{{{{ {RUN_VAR} | default(false) }}}}'}
                actions = [{'condition': 'or', 'conditions': [skip, {'condition': 'and', 'conditions': conditions}]}, *actions]
            conditions = []
        cfg: dict[str, Any] = {'id': rule_id, 'alias': rule.get('alias') or self._alias(rule), 'description': self.describe(rule, ru), 'triggers': [trigger], 'conditions': conditions, 'actions': actions, 'mode': 'single'}
        return cfg

    def _alias(self, rule: dict[str, Any]) -> str:
        return f"Hauflo · {rule.get('name') or self.tx.say('automations_name_default', self._ru(rule))}"

    async def _unique_alias(self, rule_id: str | None, rule: dict[str, Any], data: dict[str, Any]) -> str:
        base = self._alias(rule)
        taken = {r.get('alias') or self._alias(r) for rid, r in data['rules'].items() if rid != rule_id and isinstance(r, dict)}
        file = []
        with contained(_LOGGER, 'Hauflo automations: aliases in automations.yaml not read', expect=(NotPlain, OSError)):
            file, _ = await self.read_file()
        taken |= {a.get('alias') for a in file if isinstance(a, dict) and a.get('id') != rule_id}
        alias, n = (base, 1)
        while alias in taken:
            n += 1
            alias = f'{base} ({n})'
        return alias

    @staticmethod
    def _state_trigger(when: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        eids, n = (list(when['sensors']), when['for_min'])
        if when['kind'] == MEDIA:
            trigger: dict[str, Any] = {'trigger': 'state', 'entity_id': eids, **({'to': 'playing'} if when['to'] == 'on' else {'from': 'playing'})}
        else:
            trigger = {'trigger': 'state', 'entity_id': eids, 'from': 'off' if when['to'] == 'on' else 'on', 'to': when['to']}
        if n:
            trigger['for'] = {'minutes': n}
        also: list[dict[str, Any]] = []
        if when['to'] == 'off' and len(eids) > 1:
            cond: dict[str, Any] = {'condition': 'state', 'entity_id': eids, 'state': 'off'}
            if n:
                cond['for'] = {'seconds': n * 60 - state_all_margin_s()}
            also.append(cond)
        return (trigger, also)

    @staticmethod
    def _condition(c: dict[str, Any]) -> dict[str, Any]:
        if c['if'] == 'dark':
            return {'condition': 'or', 'conditions': [{'condition': 'sun', 'after': 'sunset'}, {'condition': 'sun', 'before': 'sunrise'}]}
        if c['if'] == 'light':
            return {'condition': 'sun', 'after': 'sunrise', 'before': 'sunset'}
        if c['if'] == 'between':
            return {'condition': 'time', 'after': c['after'] + ':00', 'before': c['before'] + ':00'}
        if c['if'] == 'home':
            return {'condition': 'numeric_state', 'entity_id': 'zone.home', 'above': 0}
        if c['if'] == 'away':
            return {'condition': 'numeric_state', 'entity_id': 'zone.home', 'below': 1}
        return {'condition': 'numeric_state', 'entity_id': c['entity'], c['op']: c['x']}
