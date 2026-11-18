"""Validation of the site's signal policy as the cloud sends it."""
from __future__ import annotations
import math
from typing import Any
from homeassistant.util import dt as dt_util
from .canon import COMBO_KEYS, MEANING_DEFAULT, POLICY_KEYS, canon, class_of, combo_cfg, combo_id, combo_preset, device_class_cfg, diag_visible, domain_id, entity_id_cfg, finding_key, hold_max, iso_cfg, level_abs_max, meanings, platform_cfg, severities, stamp_len, watchdog_off_max, when

def combo_in(v: Any) -> dict[str, Any] | None:
    """One «Сочетание» as the wire carries it: {name, all: [{entityId, alarm}], holdS[, preset]} (unknown keys refused)."""
    if not isinstance(v, dict) or set(v) - set(COMBO_KEYS) or (not {'name', 'all', 'holdS'} <= set(v)):
        return None
    name, conds, hold = (v['name'], v['all'], v['holdS'])
    if not isinstance(name, str) or not name.strip() or len(name) > combo_cfg()['name_max']:
        return None
    if isinstance(hold, bool) or not isinstance(hold, int) or (not 0 <= hold <= combo_cfg()['hold_max_s']):
        return None
    if not isinstance(conds, list) or not combo_cfg()['conds_min'] <= len(conds) <= combo_cfg()['conds_max']:
        return None
    out = []
    for c in conds:
        if not isinstance(c, dict) or set(c) != {'entityId', 'alarm'} or (not isinstance(c['entityId'], str)) or (not entity_id_cfg().match(c['entityId'])):
            return None
        p = policy_in({'alarm': c['alarm']})
        if not p or 'none' in p['alarm']:
            return None
        out.append({'entityId': c['entityId'], 'alarm': p['alarm']})
    if len({c['entityId'] for c in out}) != len(out):
        return None
    combo: dict[str, Any] = {'name': name.strip(), 'all': out, 'holdS': hold}
    if 'preset' in v:
        if not isinstance(v['preset'], str) or not combo_preset().match(v['preset']):
            return None
        combo['preset'] = v['preset']
    return combo

def combos_in(v: Any) -> tuple[dict[str, dict[str, Any]], int] | None:
    """{id: combo} — None: not a map or over the limit (the whole push refused); a bad combo is dropped alone."""
    if not isinstance(v, dict) or len(v) > combo_cfg()['max']:
        return None
    out, dropped = ({}, 0)
    for cid, x in v.items():
        c = combo_in(x) if isinstance(cid, str) and combo_id().match(cid) else None
        if c is None:
            dropped += 1
            continue
        out[cid] = c
    return (out, dropped)

def _level(v: Any) -> bool:
    return isinstance(v, (int, float)) and (not isinstance(v, bool)) and math.isfinite(v) and (abs(v) <= level_abs_max())

def iso(v: Any) -> bool:
    return isinstance(v, str) and iso_cfg().match(v) is not None and (dt_util.parse_datetime(v) is not None)

def policy_in(v: Any) -> dict[str, Any] | None:
    """A policy as the wire carries it (unknown keys refused — a typo never passes as «nothing set»)."""
    if not isinstance(v, dict) or set(v) - set(POLICY_KEYS):
        return None
    p: dict[str, Any] = {}
    if 'alarm' in v:
        a = v['alarm']
        if not isinstance(a, dict) or not a:
            return None
        if set(a) == {'none'} and a['none'] is True:
            p['alarm'] = {'none': True}
        elif set(a) == {'states'} and isinstance(a['states'], list):
            st = a['states']
            if not st or len(st) > canon()['statesMax'] or (not all((isinstance(x, str) and 0 < len(x) <= canon()['stateLen'] for x in st))) or (len(set(st)) != len(st)):
                return None
            p['alarm'] = {'states': list(st)}
        elif set(a) <= {'below', 'above', 'step'} and ('below' in a or 'above' in a):
            if any((k in a and (not _level(a[k])) for k in ('below', 'above'))) or ('step' in a and (not (_level(a['step']) and a['step'] >= 0))):
                return None
            if 'below' in a and 'above' in a and (a['below'] >= a['above']):
                return None
            p['alarm'] = {k: a[k] for k in ('below', 'above', 'step') if k in a}
        else:
            return None
    if 'holdS' in v:
        h = v['holdS']
        if isinstance(h, bool) or not isinstance(h, int) or (not 0 <= h <= hold_max()):
            return None
        p['holdS'] = h
    if 'severity' in v:
        if v['severity'] not in severities():
            return None
        p['severity'] = v['severity']
    if 'house' in v:
        if v['house'] not in canon()['houses']:
            return None
        p['house'] = v['house']
    if 'watch' in v:
        if not isinstance(v['watch'], bool):
            return None
        p['watch'] = v['watch']
    if 'shelvedUntil' in v:
        if not iso(v['shelvedUntil']):
            return None
        p['shelvedUntil'] = v['shelvedUntil']
    return p

def meaning_policy(meaning: str) -> dict[str, Any]:
    """The policy one of the canon's meanings stands for: {alarm[, watch: False]}; «default» — {} (nothing of its
    own)."""
    if meaning == MEANING_DEFAULT:
        return {}
    m = meanings()[meaning]
    p: dict[str, Any] = {'alarm': {'states': list(m['states'])} if 'states' in m else {'none': True}}
    if m.get('watch') == 'off':
        p['watch'] = False
    return p

def meaning_of(p: dict[str, Any] | None) -> str | None:
    """Which canon meaning a signal's own policy says (its alarm condition and watch exactly), None — none of them."""
    if not p or not p.get('alarm'):
        return None
    for k in meanings():
        want = meaning_policy(k)
        if p['alarm'] == want['alarm'] and (p.get('watch') is False) == ('watch' in want):
            return k
    return None

def newer(a: Any, b: Any) -> bool:
    """`a` is a later time than `b` (no `b` — any `a` is)."""
    ta, tb = (when(a), when(b))
    return ta is not None and (tb is None or ta > tb)

def match_in(v: Any) -> dict[str, str] | None:
    if not isinstance(v, dict) or not v or set(v) - {'platform', 'domain', 'deviceClass'}:
        return None
    m: dict[str, str] = {}
    for k, rx in (('platform', platform_cfg()), ('domain', domain_id())):
        if k in v:
            if not isinstance(v[k], str) or not rx.match(v[k]):
                return None
            m[k] = v[k]
    if 'deviceClass' in v:
        if not isinstance(v['deviceClass'], str) or not device_class_cfg().match(class_of(v['deviceClass'])):
            return None
        m['deviceClass'] = class_of(v['deviceClass'])
    return m

def finding_in(v: Any) -> dict[str, Any] | None:
    if not isinstance(v, dict):
        return None
    f: dict[str, Any] = {k: v[k] for k in ('by', 'at') if isinstance(v.get(k), str) and len(v[k]) <= stamp_len()}
    if v.get('normal') is True:
        f['normal'] = True
    if _level(v.get('factor')) and v['factor'] > 0:
        f['factor'] = v['factor']
    if iso(v.get('resetAt')):
        f['resetAt'] = v['resetAt']
    return f if f.get('normal') or 'factor' in f or 'resetAt' in f else None

def diag_in(v: Any) -> dict[str, str] | None:
    """The integrator's «Диагностика»: {visible: all | admins} — who in HA reads it (the view, GET
    /api/hauflo/insights)."""
    return {'visible': v['visible']} if isinstance(v, dict) and set(v) == {'visible'} and (v['visible'] in diag_visible()) else None

def watchdog_in(v: Any) -> dict[str, list[str]] | None:
    """The integrator's watchdog switches (Р58): {off: [integration domains]} — the watchdog leaves these alone."""
    if not isinstance(v, dict) or set(v) != {'off'} or (not isinstance(v['off'], list)) or (len(v['off']) > watchdog_off_max()):
        return None
    if not all((isinstance(d, str) and platform_cfg().match(d) for d in v['off'])):
        return None
    return {'off': sorted(set(v['off']))}

def observe_in(v: Any) -> dict[str, bool] | None:
    """    still."""
    return {'on': v['on']} if isinstance(v, dict) and set(v) == {'on'} and isinstance(v['on'], bool) else None

def signals_in(v: Any) -> tuple[dict[str, Any], int] | None:
    """({scopes, entities, findings[, diag][, watchdog][, observe]}, rows dropped) — None: not the shape at all (the
    whole body refused)."""
    if not isinstance(v, dict) or set(v) - {'scopes', 'entities', 'findings', 'diag', 'watchdog', 'observe', 'combos', 'v', 'hash'}:
        return None
    scopes_raw, ents_raw, finds_raw = (v.get('scopes') or [], v.get('entities') or {}, v.get('findings') or {})
    if not isinstance(scopes_raw, list) or not isinstance(ents_raw, dict) or (not isinstance(finds_raw, dict)):
        return None
    if len(scopes_raw) > canon()['scopesMax'] or len(ents_raw) > canon()['entitiesMax'] or len(finds_raw) > canon()['findingsMax']:
        return None
    dropped = 0
    scopes = []
    for x in scopes_raw:
        m = match_in(x.get('match')) if isinstance(x, dict) else None
        p = policy_in(x.get('set')) if isinstance(x, dict) else None
        if m is None or not p:
            dropped += 1
            continue
        scopes.append({'match': m, 'set': p})
    entities = {}
    for eid, x in ents_raw.items():
        ok = isinstance(x, dict) and isinstance(eid, str) and entity_id_cfg().match(eid)
        p = policy_in({k: w for k, w in x.items() if k not in ('by', 'at', 'lifeSafety', 'ha')}) if ok else None
        if not p:
            dropped += 1
            continue
        entities[eid] = {**p, **({'at': x['at']} if iso(x.get('at')) else {})}
    findings = {}
    for k, x in finds_raw.items():
        f = finding_in(x) if isinstance(k, str) and finding_key().match(k) else None
        if f is None:
            dropped += 1
            continue
        findings[k] = f
    out: dict[str, Any] = {'scopes': scopes, 'entities': entities, 'findings': findings}
    if v.get('diag') is not None:
        if (d := diag_in(v['diag'])) is None:
            dropped += 1
        else:
            out['diag'] = d
    if v.get('watchdog') is not None:
        if (w := watchdog_in(v['watchdog'])) is None:
            dropped += 1
        else:
            out['watchdog'] = w
    if v.get('observe') is not None:
        if (o := observe_in(v['observe'])) is None:
            dropped += 1
        else:
            out['observe'] = o
    if v.get('combos') is not None:
        if (cb := combos_in(v['combos'])) is None:
            dropped += 1
        else:
            out['combos'] = cb[0]
            dropped += cb[1]
    return (out, dropped)
