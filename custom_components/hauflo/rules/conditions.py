"""Validates the inputs, windows and conditions of a rule."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any
from homeassistant.const import PERCENTAGE, STATE_CLOSED, STATE_OFF, STATE_ON, STATE_OPEN, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from .. import derived as dv, window as win
from ..const import DOMAIN
from ..derived import SETPOINT_GAP, Invalid, num
from .answer import fmt
from .checks import attribute, number, valid_entity
from .const import BINARY_DOMAINS, DAYS, HHMM, IS_KINDS, IS_NUMBERS, READ_ATTR, VALUE_DOMAINS
from .spec import C, bounds, spec_of
if TYPE_CHECKING:
    from .checks import Env

def clean_input(raw: Any, env: Env) -> dict[str, Any]:
    """The value a rule watches: an entity (+ attribute), or «outdoor» resolved here to the street's sensor."""
    if raw == 'outdoor' or (isinstance(raw, dict) and raw.get('outdoor') is True):
        found = dv.outdoor(env.hass, env.owns)
        if found is None:
            raise Invalid('no_outdoor', 'rules_no_outdoor')
        raw = {'entity_id': found['entity_id'], 'attribute': found['attribute'], 'forecast': found['forecast'], 'outdoor': True}
    if not isinstance(raw, dict):
        raise Invalid('bad_input', 'rules_bad_input', entity='—')
    eid = valid_entity(raw.get('entity_id'), 'input')
    attr = attribute(raw.get('attribute'))
    domain = eid.split('.')[0]
    if domain not in VALUE_DOMAINS | BINARY_DOMAINS:
        raise Invalid('bad_input', 'rules_bad_input', entity=eid)
    st = env.hass.states.get(eid)
    if st is None:
        raise Invalid('no_entity', 'rules_no_entity', entity=eid)
    if env.client and (not (env.owns(eid) or eid.startswith(f'sensor.{DOMAIN}_'))):
        raise Invalid('not_allowed', 'rules_client_input')
    attr = attr or dv.default_attribute(eid)
    if attr == SETPOINT_GAP and domain != 'climate':
        raise Invalid('bad_input', 'rules_bad_input', entity=eid)
    out = {'entity_id': eid, 'attribute': attr, 'forecast': bool(raw.get('forecast')) or domain == 'weather', 'unit': dv.unit_of(st, attr)}
    if raw.get('outdoor') is True:
        out['outdoor'] = True
    return out

def clean_window(raw: Any, inp: dict[str, Any], env: Env) -> dict[str, Any]:
    """«среднее / мин / макс / сумма за час · день · неделю, N подряд»: checked against the canon and what the input's
    recorder data can say (window.py)."""
    w = C('window')
    if not isinstance(raw, dict) or raw.get('stat') not in w['stats'] or raw.get('period') not in w['periods']:
        raise Invalid('bad_window', 'rules_bad_window')
    lo, hi = w['n'][raw['period']]
    ns = []
    for key in ('n', 'n_back'):
        n = raw.get(key, raw.get('n'))
        if not isinstance(n, int) or isinstance(n, bool) or (not lo <= n <= hi):
            raise Invalid('bad_window', 'rules_bad_window_n', lo=int(lo), hi=int(hi))
        ns.append(n)
    src = win.source_of(env.hass, inp['entity_id'], inp.get('attribute'), raw['stat'])
    if src != win.STATISTICS and (not win.history_span_ok(raw['period'], max(ns))):
        raise Invalid('bad_window', 'rules_window_history', n=int(w['history_keep_days']))
    return {'stat': raw['stat'], 'period': raw['period'], 'n': ns[0], 'n_back': ns[1], 'source': src}

def outputs(rule: dict[str, Any]) -> dict[str, bool]:
    """What a rule does when it changes: its HA entity always; a suggestion to the family; its actions by itself. A
    window rule suggests by default and acts only if allowed; a rule from before the outputs acts, as it always did."""
    base = {'suggest': True, 'suggest_back': False, 'auto': False} if rule.get('window') else {'suggest': False, 'suggest_back': False, 'auto': True}
    return {**base, **(rule.get('outputs') or {})}

def clean_outputs(raw: Any, window: bool) -> dict[str, bool]:
    if raw is not None and (not isinstance(raw, dict)):
        raise Invalid('bad_outputs', 'rules_bad_outputs')
    out = outputs({'window': window, 'outputs': {}})
    for k in out:
        if k in (raw or {}):
            if not isinstance(raw[k], bool):
                raise Invalid('bad_outputs', 'rules_bad_outputs')
            out[k] = raw[k]
    return out

def clean_condition(raw: Any, env: Env | None=None) -> dict[str, Any]:
    kind = raw.get('type') if isinstance(raw, dict) else None
    bad = Invalid('bad_condition', 'rules_bad_condition', kind=str(kind)[:C('limits', 'id_len')])
    if kind == 'time':
        after, before = (raw.get('after'), raw.get('before'))
        days = raw.get('days') or list(DAYS)
        if not (isinstance(after, str) and HHMM.match(after) and isinstance(before, str) and HHMM.match(before)):
            raise bad
        if not isinstance(days, list) or not days or any((d not in DAYS for d in days)):
            raise bad
        return {'type': 'time', 'after': after, 'before': before, 'days': [d for d in DAYS if d in days]}
    if kind == 'season':
        if raw.get('mode') not in ('winter', 'summer'):
            raise bad
        return {'type': 'season', 'mode': raw['mode']}
    if kind == 'presence':
        return {'type': 'presence', 'home': raw.get('home') is not False}
    if kind == 'value':
        if raw.get('when') not in ('below', 'above'):
            raise bad
        out = {'type': 'value', 'entity_id': valid_entity(raw.get('entity_id'), 'condition'), 'attribute': attribute(raw.get('attribute')), 'when': raw['when'], 'x': number(raw.get('x'), 'X')}
        if raw.get('do') is None:
            return out
        if env is not None:
            src = clean_is_input({'entity_id': out['entity_id'], 'do': raw['do']}, env, 'condition')
            if src['kind'] not in IS_NUMBERS:
                raise bad
            return {**out, 'attribute': src['attribute'], 'do': src['do']}
        if (spec_of(out['entity_id'], raw['do']) or {}).get('value') not in IS_NUMBERS:
            raise bad
        return {**out, 'do': raw['do']}
    if kind == 'state':
        eid = valid_entity(raw.get('entity_id'), 'condition')
        if 'is' not in raw:
            return {'type': 'state', 'entity_id': eid, 'on': raw.get('on') is not False}
        if not isinstance(raw.get('not', False), bool):
            raise bad
        if env is not None:
            src = clean_is_input({'entity_id': eid, 'attribute': raw.get('attribute'), 'do': raw.get('do')}, env, 'condition')
        else:
            src = {'entity_id': eid, 'attribute': attribute(raw.get('attribute')), 'kind': None, 'unit': None, 'tol': C('limits', 'is_tolerance')}
        src.pop('forecast', None)
        return {'type': 'state', **src, 'is': clean_is(raw.get('is'), src, env.hass.states.get(eid) if env else None), 'not': raw.get('not') is True}
    if kind == 'heating':
        eid = valid_entity(raw.get('entity_id'), 'condition')
        if not eid.startswith('climate.'):
            raise bad
        return {'type': 'heating', 'entity_id': eid}
    raise bad

def read_attr(read: str) -> str | None:
    return READ_ATTR.get(read, read)

def read_kind(domain: str, dos: list[str]) -> tuple[str, str] | None:
    """(the action whose value a canon read compares, its kind): on / off, an option, a number kind; None — nothing
    «равно» compares (a text, a time). A read of wants only (a player's playing / paused) is an option of them."""
    table = C('actions_by_domain')[domain]
    if table[dos[0]]['read'] in ('power', 'closed'):
        return (dos[0], 'onoff')
    for do in dos:
        if table[do].get('value') in IS_KINDS:
            return (do, table[do]['value'])
    if all((isinstance(table[do].get('want'), str) for do in dos)):
        return (dos[0], 'option')
    return None

def step_of(spec: dict[str, Any], attrs: Any) -> float | None:
    """The entity's own step of a value (a fan's percentage_step, a number's step, a thermostat's target_temp_step)."""
    name = spec.get('tol') or spec.get('step')
    step = num(attrs.get(name)) if name else None
    return step if step and step > 0 else None

def reads_of(st: Any) -> list[dict[str, Any]]:
    """What «равно» may compare on an entity (the app's «что проверить»): one per canon read the entity supports (on /
    off / pulse share «power») — the action it belongs to, the attribute, the kind and the options / bounds / step from
    the entity's own attributes."""
    table = C('actions_by_domain').get(st.domain, {})
    groups: dict[str, list[str]] = {}
    for do, spec in table.items():
        if spec.get('read'):
            groups.setdefault(spec['read'], []).append(do)
    lim, out = (C('limits'), [])
    for read, dos in groups.items():
        got = read_kind(st.domain, dos)
        if got is None or not any((supports(st, table[d]) for d in dos)):
            continue
        do, kind = got
        spec, item = (table[do], {'do': do, 'attribute': read_attr(read), 'kind': kind})
        if kind == 'onoff':
            item['options'] = [STATE_ON, STATE_OFF] if read == 'power' else [STATE_OPEN, STATE_CLOSED]
        elif kind == 'option':
            opts = st.attributes.get(spec['options']) if spec.get('options') else [table[d]['want'] for d in dos]
            item['options'] = [str(o) for o in opts] if isinstance(opts, (list, tuple)) else []
        else:
            fallback = {'percent': lim['percent'], 'setpoint': lim['setpoint']}.get(kind, (-lim['number_abs'], lim['number_abs']))
            item['min'], item['max'] = bounds(spec, st.attributes, fallback)
            if (step := step_of(spec, st.attributes)) is not None:
                item['step'] = step
        out.append(item)
    return out

def pick_read(st: Any, do: Any, attr: str | None) -> dict[str, Any] | None:
    """The read «равно» compares: by the action named, else by the attribute, else the entity's state (a mode, an
    option, a number), else its on / off."""
    reads, table = (reads_of(st), C('actions_by_domain').get(st.domain, {}))
    if do is not None:
        spec = table.get(do) if isinstance(do, str) else None
        return next((r for r in reads if spec and table[r['do']]['read'] == spec.get('read')), None)
    if attr is not None:
        return next((r for r in reads if r['attribute'] == attr), None)
    return next((r for r in reads if r['attribute'] is None and r['kind'] != 'onoff'), None) or next((r for r in reads if r['kind'] == 'onoff'), None)

def clean_is_input(raw: Any, env: Env, what: str='input') -> dict[str, Any]:
    """The value «равно» watches: a device of the canon's domains read as its action reads it (Hauflo's own only — the
    same boundary as a target), or a sensor / binary sensor / weather as the value rules always took it."""
    if not isinstance(raw, dict):
        raise Invalid('bad_input', 'rules_bad_input', entity='—')
    eid = valid_entity(raw.get('entity_id'), what)
    attr = attribute(raw.get('attribute'))
    domain = eid.split('.')[0]
    plain = domain in VALUE_DOMAINS | BINARY_DOMAINS
    bad = Invalid('bad_input', 'rules_bad_input', entity=eid)
    if not plain and domain not in C('actions_by_domain'):
        raise bad
    st = env.hass.states.get(eid)
    if st is None:
        raise Invalid('no_entity', 'rules_no_entity', entity=eid)
    if env.client and (not (env.owns(eid) or eid.startswith(f'sensor.{DOMAIN}_'))):
        raise Invalid('not_allowed', 'rules_client_input')
    if not plain and (not (env.owns(eid) or env.own_raw(eid))):
        raise Invalid('bad_input', 'rules_foreign_input', entity=eid)
    pick = pick_read(st, raw.get('do'), attr)
    if pick is None:
        if raw.get('do') is not None or not plain:
            raise bad
        return {'entity_id': eid, 'attribute': attr, 'kind': None, 'unit': dv.unit_of(st, attr), 'forecast': False, 'tol': C('limits', 'is_tolerance')}
    spec = C('actions_by_domain')[domain][pick['do']]
    step = step_of(spec, st.attributes)
    unit = PERCENTAGE if pick['kind'] == 'percent' else dv.unit_of(st, pick['attribute']) if pick['kind'] in IS_NUMBERS else None
    return {'entity_id': eid, 'attribute': pick['attribute'], 'do': pick['do'], 'kind': pick['kind'], 'unit': unit, 'forecast': False, 'tol': step * C('limits', 'is_step_share') if step else C('limits', 'is_tolerance')}

def _word(v: Any) -> str:
    return ''.join(str(v).split()).casefold()

def clean_is(raw: Any, src: dict[str, Any], st: Any) -> list[Any]:
    """«равно» values: 1…is_max of them; an option / on-off one of the device's own (as it names it), a number inside
    its bounds — a value the device never has would make a rule silently never true."""
    lim = C('limits')
    if not isinstance(raw, list) or not 1 <= len(raw) <= lim['is_max']:
        raise Invalid('bad_is', 'rules_is_count', n=lim['is_max'])
    kind = src.get('kind')
    item = next((r for r in reads_of(st) if r['do'] == src.get('do')), None) if st is not None and src.get('do') else None
    spec = spec_of(src['entity_id'], src.get('do')) or {}
    if item and 'options' in item:
        allowed: Any = item['options']
    elif item:
        allowed = f"{fmt(item['min'], None)}…{fmt(item['max'], src.get('unit'))}"
    else:
        allowed = fmt(None, None)
    out: list[Any] = []
    for v in raw:
        bad = Invalid('bad_is', 'rules_is_value', value=str(v)[:lim['id_len']], entity=src['entity_id'], options=allowed, field=spec.get('field'), domain=src['entity_id'].split('.')[0])
        if isinstance(v, bool) or not isinstance(v, (str, int, float)) or (isinstance(v, str) and (not (v.strip() and v.isprintable() and (len(v) <= C('actions', 'text_len'))))):
            raise bad
        if item and 'options' in item:
            v = next((o for o in item['options'] if _word(o) == _word(v)), None)
        elif kind in IS_NUMBERS:
            f = num(v)
            v = None if f is None or (item and (not item['min'] <= f <= item['max'])) else int(f) if f.is_integer() else f
        elif isinstance(v, float) and v.is_integer():
            v = int(v)
        if v is None:
            raise bad
        if v not in out:
            out.append(v)
    return out

def is_match(v: Any, wants: list[Any], tol: float) -> bool:
    """    spaces aside (HA's states are lower case, a person types «Boost»)."""
    if v is None or isinstance(v, bool):
        return False
    a = num(v)
    for w in wants:
        b = num(w)
        if a is not None and b is not None:
            if abs(a - b) < tol:
                return True
        elif _word(v) == _word(w):
            return True
    return False

def has_now(a: dict[str, Any]) -> bool:
    """An action with a state to compare: the season, or a canon action that reads one (not a press / scene / run /
    toggle)."""
    if 'entity_id' not in a:
        return a['do'] == 'season'
    spec = spec_of(a['entity_id'], a['do'])
    return bool(spec and spec.get('read'))

def supports(st: Any, spec: dict[str, Any]) -> bool:
    """The entity takes this canon action — as HA itself decides (the service's required_features: any one, each fully),
    an option only with its options listed, a helper's value only of the kind it holds (needs), a light's brightness
    only beyond «onoff». Not knowable (unavailable): yes —
    an unknown state still has its capabilities."""
    if st is None or st.state == STATE_UNAVAILABLE:
        return True
    a = st.attributes
    if (feats := spec.get('features')) is not None:
        have = a.get('supported_features')
        have = have if isinstance(have, int) and (not isinstance(have, bool)) else 0
        if not any((have & f == f for f in feats)):
            return False
    if (opts := spec.get('options')) is not None and (not (isinstance(a.get(opts), (list, tuple)) and a.get(opts))):
        return False
    if any((a.get(flag) is not True for flag in spec.get('needs') or ())):
        return False
    if (b := spec.get('beyond')) is not None:
        modes = a.get(b['attr'])
        if not isinstance(modes, (list, tuple, set)) or not set(map(str, modes)) - set(b['not']):
            return False
    return True

def classes_of(hass: HomeAssistant, eid: str) -> set[str]:
    """An entity's device class as HA has it: the state's, the registry's (a person's choice), the integration's."""
    st, ent = (hass.states.get(eid), er.async_get(hass).async_get(eid))
    return {c for c in (st.attributes.get('device_class') if st else None, *((ent.device_class, ent.original_device_class) if ent else ())) if c}

def client_class_ok(hass: HomeAssistant, eid: str) -> bool:
    """A client's target of a domain the canon narrows by class (actions.client_classes: a switch only a socket)."""
    want = C('actions', 'client_classes').get(eid.split('.')[0])
    return want is None or bool(classes_of(hass, eid) & set(want))
