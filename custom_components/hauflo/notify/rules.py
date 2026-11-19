"""Validates the alarm rules and overlays the house policy on them."""
from __future__ import annotations
from collections.abc import Callable
import logging
from typing import Any
from homeassistant.core import HomeAssistant
from ..const import site_ru
from ..signals import canon as signals_canon, store as signals_store
from .common import DOWNGRADE, EN_TITLES, PHONE_ID, RU_TITLES, RUN_SERVICES, SCOPE_RANK, SCOPES, WORD, default_hold, hold_canon, hold_value, own_holds, repeat_min, safe_int, states_max
from .levels import clean_outputs, clean_thresholds, loud_verdict, source_ok
_LOGGER = logging.getLogger(__name__)

def _action_ok(action: dict[str, Any] | None, owns: Callable[[str], bool] | None) -> bool:
    if not action:
        return False
    service, eid = (str(action.get('service')), str(action.get('entity_id')))
    if service not in RUN_SERVICES or eid.split('.')[0] != service.split('.')[0]:
        return False
    return owns is not None and bool(owns(eid))

def narrowing(new: dict[str, Any], old: dict[str, Any]) -> str | None:
    before = {r['id']: r for r in old.get('rules') or []}
    for r in new.get('rules') or []:
        p = before.get(r['id'])
        if p is None:
            return f"new rule {r['id']}"
        if (r['kind'], r['title'], r['alarm_states']) != (p['kind'], p['title'], p['alarm_states']):
            return f"{r['id']}: changed text or alarm states"
        if not set(r['sources']) <= set(p['sources']):
            return f"{r['id']}: new source"
        rc, pc = (r['recipients'], p['recipients'])
        if rc['all'] and (not pc['all']) or any((not set(rc[k]) <= set(pc[k]) for k in ('people', 'groups', 'phones'))):
            return f"{r['id']}: new recipient"
        if r.get('action') and r.get('action') != p.get('action'):
            return f"{r['id']}: new button"
        if any((o not in (p.get('outputs') or []) for o in r.get('outputs') or [])):
            return f"{r['id']}: new output"
        if any(((p.get('thresholds') or {}).get(e) != th for e, th in (r.get('thresholds') or {}).items())):
            return f"{r['id']}: new threshold"
        if r['priority'] == 'critical' and p['priority'] != 'critical':
            return f"{r['id']}: critical"
        if r['repeat_min'] and (not p['repeat_min'] or r['repeat_min'] < p['repeat_min']):
            return f"{r['id']}: repeats more often"
        if r['enabled'] and (not p['enabled']):
            return f"{r['id']}: switched on"
    groups = {g['id']: g for g in old.get('groups') or []}
    for g in new.get('groups') or []:
        if g['id'] not in groups or not set(g['people']) <= set(groups[g['id']]['people']):
            return f"group {g['id']}: new people"
    na, oa = (new.get('audience') or {}, old.get('audience') or {})
    if set(oa) - set(na):
        return 'audience: a setting removed'
    for k, v in na.items():
        if SCOPE_RANK[v] > SCOPE_RANK[oa[k]] if k in oa else v != 'off':
            return f'audience {k}: more'
    nr, or_ = (new.get('rings') or {}, old.get('rings') or {})
    for k, v in or_.items():
        if v is False and nr.get(k) is not False:
            return f'rings {k}: more'
    for k, v in nr.items():
        if v is True and or_.get(k) is not True:
            return f'rings {k}: more'
    return None

def policy_overlay(hass: HomeAssistant | None, sources: list[str], *, own_states: bool, thresholds: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    pol = signals_store.policy(hass)
    if hass is None or not pol or (not (pol['scopes'] or pol['entities'])):
        return ({}, {})
    overlay: dict[str, dict[str, Any]] = {}
    holds: dict[str, int] = {}
    for eid in sources:
        st = hass.states.get(eid)
        r = signals_canon.resolve(eid, platform=signals_store.platform_of(hass, eid), device_class=st.attributes.get('device_class') if st else None, name=st.name if st else None, policy=pol)
        if r['holdS'] is not None:
            holds[eid] = r['holdS']
        a = r['alarm']
        if a and 'none' in a and (r['from']['alarm'] != 'canon'):
            overlay[eid] = {'none': True}
            continue
        if r['from']['alarm'] == 'canon' or not a or 'none' in a or (eid in thresholds):
            continue
        if 'states' in a:
            if not own_states:
                overlay[eid] = {'states': list(a['states'])}
        elif not own_states:
            overlay[eid] = {k: float(a[k]) for k in ('below', 'above') if k in a} | {'step': float(a.get('step', 0))}
    return (overlay, holds)

def policy_hold(hass: HomeAssistant | None, kind: str, sources: list[str], thresholds: dict[str, Any], holds: dict[str, int]) -> int:
    """The canon hold (default_hold) with the site's own hold of a source in place of its class's; the shortest."""
    if not holds:
        return default_hold(hass, kind, sources, thresholds)
    rest = [e for e in sources if e not in holds]
    found = [*holds.values()]
    if rest or kind in hold_canon()['kinds']:
        found.append(default_hold(hass, kind, rest, thresholds))
    return int(min(found))

def clean_rules(raw: Any, hass: HomeAssistant | None=None, owns: Callable[[str], bool] | None=None) -> dict[str, Any]:
    """Only what the engine understands; anything else from the wire is dropped."""
    out: dict[str, Any] = {'rules': [], 'groups': []}
    if not isinstance(raw, dict):
        return out
    if raw.get('lang') in ('ru', 'en'):
        out['lang'] = raw['lang']
    aud = raw.get('audience')
    if isinstance(aud, dict):
        out['audience'] = {str(k): str(v) for k, v in list(aud.items())[:200] if v in SCOPES and (k == '*' or str(k).startswith('person.') or PHONE_ID.match(str(k)))}
    rings = raw.get('rings')
    out['rings'] = {str(k): v for k, v in list(rings.items())[:200] if isinstance(v, bool) and (k == '*' or str(k).startswith('person.') or PHONE_ID.match(str(k)))} if isinstance(rings, dict) else {}
    for g in raw.get('groups') or []:
        if isinstance(g, dict) and g.get('id') and isinstance(g.get('people'), list):
            out['groups'].append({'id': str(g['id']), 'name': str(g.get('name') or ''), 'people': [str(p) for p in g['people'] if str(p).startswith('person.')]})
    for r in raw.get('rules') or []:
        if not isinstance(r, dict) or not r.get('id') or (not isinstance(r.get('sources'), list)):
            continue
        rec = r.get('recipients') or {}
        action = r.get('action') if isinstance(r.get('action'), dict) else None
        picked = frozenset((p for p in r['picked'] if isinstance(p, str))) if isinstance(r.get('picked'), list) else frozenset()
        states_raw = r.get('alarm_states')
        if states_raw not in (None, []) and (not (isinstance(states_raw, list) and len(states_raw) <= states_max() and all((isinstance(s, str) and WORD.match(s) for s in states_raw)))):
            out.setdefault('rejected', []).append({'id': str(r['id']), 'reason': 'bad_alarm_states', 'value': [str(s)[:40] for s in states_raw][:states_max() + 1] if isinstance(states_raw, list) else str(states_raw)[:40]})
            _LOGGER.warning('Hauflo: alarm rule %s not accepted: alarm_states %r are not state names (a-z, 0-9, _), up to %d', str(r['id'])[:64], states_raw, states_max())
            continue
        sources = [str(s) for s in r['sources'] if source_ok(hass, str(s), owns, picked)]
        wanted = r.get('priority') == 'critical'
        verdict = loud_verdict(hass, sources) if wanted else None
        critical = wanted and verdict is None
        downgrade = verdict if wanted else str(r['downgrade']) if DOWNGRADE.match(str(r.get('downgrade') or '')) else None
        repeat = max(0, min(safe_int(r.get('repeat_min')), 240))
        outputs = clean_outputs(hass, r.get('outputs'))
        thresholds = clean_thresholds(hass, r.get('thresholds'), sources)
        kind = str(r.get('kind')) if WORD.match(str(r.get('kind') or '')) else 'custom'
        own_hold = hold_value(r.get('hold_s'))
        overlay, holds = policy_overlay(hass, sources, own_states=bool(states_raw), thresholds=thresholds)
        selfheld = own_holds(hass, sources, holds) if own_hold is None else {}
        rest = [e for e in sources if e not in selfheld]
        if own_hold is not None:
            hold = own_hold
        elif rest or not selfheld:
            hold = policy_hold(hass, kind, rest, thresholds, {e: h for e, h in holds.items() if e not in selfheld})
        else:
            hold = min(selfheld.values())
        out['rules'].append({'id': str(r['id']), 'kind': kind, 'title': str(r.get('title') or 'Авария')[:80], 'sources': sources, **({'picked': [s for s in sources if s in picked]} if any((s in picked for s in sources)) else {}), 'alarm_states': list(states_raw) if states_raw else ['on'], 'recipients': {'all': bool(rec.get('all')), 'people': [str(p) for p in rec.get('people') or [] if str(p).startswith('person.')], 'groups': [str(g) for g in rec.get('groups') or []], 'phones': [str(p) for p in rec.get('phones') or [] if PHONE_ID.match(str(p))]}, 'priority': 'critical' if critical else 'normal', **({'downgrade': downgrade} if downgrade and (not critical) else {}), 'repeat_min': max(repeat_min(), repeat) if repeat else 0, 'enabled': r.get('enabled', True) is not False, **({'action': {'title': str(action.get('title') or '')[:40], 'service': str(action.get('service') or ''), 'entity_id': str(action.get('entity_id') or '')}} if action and _action_ok(action, owns) else {}), **({'outputs': outputs} if outputs else {}), **({'thresholds': thresholds} if thresholds else {}), **({'policy': overlay} if overlay else {}), 'hold_s': own_hold, 'hold': hold, **({'holds': selfheld} if selfheld else {})})
    titles = RU_TITLES if site_ru(getattr(getattr(hass, 'config', None), 'language', None) if hass is not None else None, out) else EN_TITLES
    for r in out['rules']:
        r['title'] = titles.get(r['title'], r['title'])
    return out

def devices(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return 'устройство'
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return 'устройства'
    return 'устройств'
