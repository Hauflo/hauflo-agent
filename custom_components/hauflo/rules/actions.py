"""Validates the actions of a rule and the whole rule."""
from __future__ import annotations
from collections.abc import Callable
import datetime as dt
import secrets
from typing import TYPE_CHECKING, Any
from homeassistant.util import dt as dt_util
from .. import derived as dv, window as win
from ..derived import ROUND_DIGITS, Invalid, num
from .answer import fmt
from .checks import minutes, number, valid_entity, valid_name
from .conditions import clean_condition, clean_input, clean_is, clean_is_input, clean_outputs, clean_window, client_class_ok, outputs, supports
from .const import AT, BINARY_DOMAINS, DATE, ID_BYTES, IS, PRIORITY, RANGES, SEASON_MODES, SEASON_TARGET, TIME, USER_RE, WHENS
from .schedule import clean_at
from .spec import C, act_spec, all_dos, bounds, purpose, scheduled, slot_of, spec_of
if TYPE_CHECKING:
    from .checks import Env

def clean_repeat(raw: Any, do: str, pulse_s: Any=None) -> dict[str, int] | None:
    """«Повторить N раз через M с» (canon actions.repeat): only an action that does something each time (a press, a pulse, a toggle, a scene, a script); a pulse's pause longer than the pulse itself."""
    if raw is None:
        return None
    r = C('actions', 'repeat')
    (lo, hi), (s_lo, s_hi) = (r['count'], r['every_s'])
    bad = Invalid('bad_action', 'rules_bad_repeat', lo=lo, hi=hi, s_lo=s_lo, s_hi=s_hi)
    if do not in r['acts'] or not isinstance(raw, dict) or set(raw) - {'count', 'every_s'}:
        raise bad
    n, every = (raw.get('count'), raw.get('every_s'))
    if any((isinstance(v, bool) or not isinstance(v, int) for v in (n, every))) or not lo <= n <= hi or (not s_lo <= every <= s_hi):
        raise bad
    if isinstance(pulse_s, (int, float)) and every <= pulse_s:
        raise bad
    return {'count': n, 'every_s': every}

def clean_action(raw: Any, env: Env) -> dict[str, Any]:
    lim = C('limits')
    do = raw.get('do') if isinstance(raw, dict) else None
    if do not in all_dos():
        raise Invalid('bad_action', 'rules_bad_action', do=str(do)[:lim['id_len']], entity='—')
    if env.client and do not in C('actions', 'client_acts'):
        raise Invalid('not_allowed', 'rules_client_action')
    if do == 'season':
        if raw.get('value') not in SEASON_MODES:
            raise Invalid('bad_action', 'rules_bad_season')
        return {'do': 'season', 'value': raw['value']}
    if do == 'notify':
        text = raw.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > lim['notify_len'] or (not text.isprintable()):
            raise Invalid('bad_action', 'rules_bad_text', n=lim['notify_len'])
        return {'do': 'notify', 'text': text.strip(), 'to': 'me' if env.client or raw.get('to') == 'me' else 'all'}
    eid = valid_entity(raw.get('entity_id'), 'action')
    domain = eid.split('.')[0]
    spec = spec_of(eid, do)
    if spec is None:
        raise Invalid('bad_action', 'rules_bad_action', do=do, entity=eid)
    if env.client and (domain not in C('actions', 'client_domains') or not client_class_ok(env.hass, eid)):
        raise Invalid('not_allowed', 'rules_client_action')
    if not env.may_change(eid):
        raise Invalid('target_not_allowed', 'rules_target_not_allowed', entity=eid)
    if not supports(env.hass.states.get(eid), spec):
        raise Invalid('bad_action', 'rules_bad_action', do=do, entity=eid)
    out: dict[str, Any] = {'do': do, 'entity_id': eid}
    if (kind := spec.get('value')) is not None:
        out['value'] = action_value(kind, spec, do, raw.get('value'), env.hass.states.get(eid))
    if (again := clean_repeat(raw.get('repeat'), do, out['value'] if kind == 'seconds' else None)) is not None:
        out['repeat'] = again
    return out

def action_value(kind: str, spec: dict[str, Any], do: str, v: Any, st: Any) -> Any:
    """An action's value against the canon kind and the target's own attributes (its options, min / max / step)."""
    lim = C('limits')
    attrs = st.attributes if st is not None else {}
    if kind == 'option':
        opts = attrs.get(spec['options'])
        allowed = [str(o) for o in opts] if isinstance(opts, (list, tuple)) else []
        if not isinstance(v, str) or v not in allowed:
            raise Invalid('bad_action', 'rules_bad_option', do=do, options=allowed, field=spec.get('field'), domain=getattr(st, 'domain', None))
        return v
    if kind in ('text', 'time', 'date', 'datetime'):
        if not isinstance(v, str) or not v.isprintable():
            raise Invalid('bad_action', 'rules_bad_action_format', do=do)
        if kind == 'time' and TIME.match(v):
            return dt.time.fromisoformat(v).isoformat()
        if kind == 'date' and DATE.match(v):
            try:
                return dt.date.fromisoformat(v).isoformat()
            except ValueError:
                pass
        if kind == 'datetime' and dt_util.parse_datetime(v) is not None:
            return v
        if kind == 'text':
            lo, hi = bounds(spec, attrs, (1, C('actions', 'text_len')))
            if lo <= len(v) <= min(hi, C('actions', 'text_len')):
                return v
            raise Invalid('bad_action', 'rules_bad_action_value', do=do, lo=fmt(lo, None), hi=fmt(min(hi, C('actions', 'text_len')), None))
        raise Invalid('bad_action', 'rules_bad_action_format', do=do)
    n = number(v if v is not None or kind != 'seconds' else C('actions', 'pulse_default_s'), do)
    fallback = {'percent': lim['percent'], 'setpoint': lim['setpoint'], 'seconds': C('actions', 'pulse_s')}.get(kind, (-lim['number_abs'], lim['number_abs']))
    lo, hi = bounds(spec, attrs, fallback)
    if not lo <= n <= hi:
        raise Invalid('bad_action', 'rules_bad_action_value', do=do, lo=fmt(lo, None), hi=fmt(hi, None))
    if kind == 'percent':
        return round(n)
    step = num(attrs.get(spec['step'])) if spec.get('step') else None
    step = step if step and step > 0 else lim['setpoint_step'] if kind == 'setpoint' else None
    return round(round(n / step) * step, ROUND_DIGITS) if step else n

def _actions(raw: Any, env: Env) -> list[dict[str, Any]]:
    n = C('limits', 'actions')
    if not isinstance(raw, list) or not raw or len(raw) > n:
        raise Invalid('limit', 'rules_limit_actions', n=n)
    return [clean_action(a, env) for a in raw]

def clean_name_gen(raw: Any) -> dict[str, Any] | None:
    """A generated name kept as recipe + room (+ number), shown in the reader's language; a typed name has none."""
    if not isinstance(raw, dict) or not any((r['id'] == raw.get('recipe') for r in C('recipes'))):
        return None
    room = raw.get('room')
    n = raw.get('n')
    return {'recipe': raw['recipe'], 'room': room.strip()[:C('limits', 'name_len')] if isinstance(room, str) else '', 'n': n if isinstance(n, int) and 1 <= n <= C('limits', 'rules') else 1}

def clean_rule(raw: Any, env: Env) -> dict[str, Any]:
    """One rule, checked (the rest of the house is checked by check_house)."""
    lim = C('limits')
    if not isinstance(raw, dict):
        raise Invalid('bad', 'rules_bad_object')
    rid = raw.get('id')
    if rid is None:
        rid = f'r{secrets.token_hex(ID_BYTES)}'
    if not isinstance(rid, str) or not dv.id_re().match(rid):
        raise Invalid('bad_id', 'rules_bad_id', n=lim['id_len'])
    pname = raw.get('purpose') or 'other'
    if pname not in C('purposes') or (env.client and pname not in C('client_purposes')):
        raise Invalid('bad_purpose', 'rules_bad_purpose')
    meta = purpose(pname)
    priority = raw.get('priority') or meta['priority']
    if priority not in PRIORITY or (env.client and priority != 'comfort'):
        raise Invalid('bad_priority', 'rules_bad_priority')
    rule: dict[str, Any] = {'id': rid, 'name': valid_name(raw.get('name')), 'enabled': raw.get('enabled', True) is not False, 'purpose': pname, 'priority': priority}
    if (gen := clean_name_gen(raw.get('name_gen'))) is not None:
        rule['name_gen'] = gen
    when = raw.get('when')
    if when not in WHENS:
        raise Invalid('bad_when', 'rules_bad_when')
    if when == AT:
        rule['when'], rule['at'] = (AT, clean_at(raw.get('at')))
        return _clean_tail(raw, rule, env, meta)
    if when == IS:
        rule['input'] = inp = clean_is_input(raw.get('input'), env)
        rule[IS] = clean_is(raw.get(IS), inp, env.hass.states.get(inp['entity_id']))
    else:
        rule['input'] = inp = clean_input(raw.get('input'), env)
    neg = raw.get('not', False)
    if not isinstance(neg, bool) or (neg and when != IS):
        raise Invalid('bad_not', 'rules_bad_not')
    if when == IS:
        rule['not'] = neg
    if raw.get('window') is not None:
        if when not in RANGES:
            raise Invalid('bad_when', 'rules_window_when')
        rule['window'] = clean_window(raw['window'], inp, env)
        if rule['window']['source'] == win.ON_TIME:
            inp['unit'] = C('window', 'on_time_unit')
    else:
        binary = inp['entity_id'].split('.')[0] in BINARY_DOMAINS and inp['attribute'] is None
        if when != IS and binary != (when in ('on', 'off')):
            raise Invalid('bad_when', 'rules_when_kind')
    rule['when'] = when
    y = num(raw.get('y')) if raw.get('y') is not None else 0.0
    if y is None or y < 0:
        raise Invalid('bad_hysteresis', 'rules_bad_hysteresis')
    if when in ('below', 'above'):
        rule['x'] = number(raw.get('x'), 'X')
        if not y < max(abs(rule['x']), lim['hysteresis_floor']):
            raise Invalid('bad_hysteresis', 'rules_bad_hysteresis')
    elif when in ('outside', 'between'):
        lo, hi = (number(raw.get('lo'), 'lo'), number(raw.get('hi'), 'hi'))
        if lo >= hi:
            raise Invalid('bad_range', 'rules_bad_range')
        if not (y + y < hi - lo if when == 'outside' else y < hi - lo):
            raise Invalid('bad_hysteresis', 'rules_bad_hysteresis')
        rule['lo'], rule['hi'] = (lo, hi)
    elif y:
        raise Invalid('bad_hysteresis', 'rules_bad_hysteresis')
    rule['y'] = y
    in_bounds(rule)
    if 'window' in rule:
        rule['for_min'] = rule['back_min'] = 0.0
    else:
        rule['for_min'] = minutes(raw.get('for_min'))
        rule['back_min'] = minutes(raw.get('back_min', raw.get('for_min')))
    return _clean_tail(raw, rule, env, meta)

def _clean_tail(raw: dict[str, Any], rule: dict[str, Any], env: Env, meta: dict[str, Any]) -> dict[str, Any]:
    lim = C('limits')
    at = scheduled(rule)
    conds = raw.get('only_if') or []
    if not isinstance(conds, list) or len(conds) > lim['conditions']:
        raise Invalid('limit', 'rules_limit_conditions', n=lim['conditions'])
    rule['only_if'] = [clean_condition(c, env) for c in conds]
    rule['actions'] = _actions(raw.get('actions'), env)
    for a in rule['actions']:
        if (act_spec(a) or {}).get('value') == 'seconds' and a['value'] < (meta.get('min_on_s') or 0):
            raise Invalid('bad_action', 'rules_pulse_min_on', n=int(meta['min_on_s']))
    back = raw.get('back', meta.get('back', 'reverse'))
    if meta.get('back') == 'nothing' or at:
        back = 'nothing'
    if isinstance(back, list):
        back = _actions(back, env)
    elif back not in ('reverse', 'nothing'):
        raise Invalid('bad_back', 'rules_bad_back')
    rule['back'] = back
    if (inter := raw.get('interlock')) is not None:
        if meta.get('safe') != 'off':
            raise Invalid('bad_interlock', 'rules_bad_interlock')
        rule['interlock'] = valid_entity(inter, 'interlock')
    client = raw.get('client') if isinstance(raw.get('client'), dict) else {}
    c: dict[str, Any] = {'toggle': client.get('toggle', True) is not False, 'visible': client.get('visible', True) is not False}
    if not at and (client.get('lo') is not None or client.get('hi') is not None):
        c['lo'], c['hi'] = (number(client.get('lo'), 'lo'), number(client.get('hi'), 'hi'))
        if c['lo'] > c['hi']:
            raise Invalid('bad_range', 'rules_bad_range')
    rule['client'] = c
    rule['owner'] = 'client' if env.client or raw.get('owner') == 'client' else 'integrator'
    if not at and ('window' in rule or raw.get('outputs') is not None):
        rule['outputs'] = clean_outputs(raw.get('outputs'), 'window' in rule)
    if raw.get('snooze_days') is not None or outputs(rule)['suggest']:
        lo, hi = C('suggest', 'snooze_bounds')
        d = num(raw.get('snooze_days')) if raw.get('snooze_days') is not None else C('suggest', 'snooze_days')
        if d is None or not lo <= d <= hi:
            raise Invalid('bad_snooze', 'rules_bad_snooze', lo=int(lo), hi=int(hi))
        rule['snooze_days'] = int(d)
    if isinstance(raw.get('quiet_ok'), bool):
        rule['quiet_ok'] = raw['quiet_ok']
    if isinstance(raw.get('user_id'), str) and USER_RE.match(raw['user_id']):
        rule['user_id'] = raw['user_id']
    return rule

def in_bounds(rule: dict[str, Any]) -> None:
    """A rule's thresholds inside what its unit can be (canon «thresholds»; the backend checks the same for the project's own sensors)."""
    unit = rule['input'].get('unit')
    b = C('thresholds').get(unit) if unit else None
    if b and any((k in rule and (not b[0] <= rule[k] <= b[1]) for k in ('x', 'lo', 'hi'))):
        raise Invalid('bad_number', 'rules_window_threshold', lo=fmt(b[0], None), hi=fmt(b[1], None), unit=unit)

def targets_of(rule: dict[str, Any]) -> list[tuple[str, str]]:
    """(entity, attribute) the rule writes."""
    acts = list(rule['actions']) + (rule['back'] if isinstance(rule['back'], list) else [])
    return sorted({(a.get('entity_id') or SEASON_TARGET, slot_of(a)) for a in acts if a['do'] != 'notify'})

def inputs_of(rule: dict[str, Any], derived_sources: Callable[[str], list[str]] | None=None) -> set[str]:
    """Every entity the rule's decision reads — derived values unfolded to their sources."""
    out = {*([rule['input']['entity_id']] if 'input' in rule else []), *(c['entity_id'] for c in rule['only_if'] if 'entity_id' in c)}
    if rule.get('interlock'):
        out.add(rule['interlock'])
    if derived_sources is not None:
        for eid in list(out):
            out.update((dv.split_source(s)[0] for s in derived_sources(eid)))
    return out

def check_house(rule: dict[str, Any], others: list[dict[str, Any]], derived_sources: Callable[[str], list[str]]) -> None:
    """Loops and conflicts with the house's other rules; raises Invalid."""
    if len(others) >= C('limits', 'rules'):
        raise Invalid('limit', 'rules_limit_rules', n=C('limits', 'rules'))
    if scheduled(rule):
        return
    others = [o for o in others if not scheduled(o)]
    mine = {e for e, _ in targets_of(rule)}
    ins = inputs_of(rule, derived_sources)
    if mine & ins:
        raise Invalid('loop', 'rules_loop', entity=sorted(mine & ins)[0])
    edges: dict[str, set[str]] = {}
    for r in [*others, rule]:
        tg = {e for e, _ in targets_of(r)}
        for i in inputs_of(r, derived_sources):
            edges.setdefault(i, set()).update(tg)
    for start in sorted(mine):
        seen, todo = ({start}, [start])
        while todo:
            for nxt in edges.get(todo.pop(), ()):
                if nxt in ins:
                    raise Invalid('loop', 'rules_loop_chain', entity=start, other=nxt)
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
    for o in others:
        both = set(targets_of(o)) & set(targets_of(rule)) if o['priority'] == rule['priority'] else set()
        if both:
            e, a = sorted(both)[0]
            raise Invalid('conflict', 'rules_conflict', entity=e, attribute=a, rule=o['name'])
