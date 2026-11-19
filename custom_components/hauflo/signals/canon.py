"""Signal canon and the resolution of one entity's class, alarm and watch."""
from __future__ import annotations
import functools
import logging
import math
import re
from typing import Any
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from ..tuning import tuning
_LOGGER = logging.getLogger(__name__)
canon = functools.cache(lambda: tuning()['signals'])
_a = functools.cache(lambda: tuning()['signals_agent'])
FEATURE = 'signals'
MUTE_FEATURE = 'signals_mute'
SHELVE_FEATURE = 'signals_shelve'
COMBOS_FEATURE = 'signals_combos'
STORE_KEY = 'hauflo.signals'
DATA = 'hauflo_signals'
severities = functools.cache(lambda: list(_a()['severities']))
hash = functools.cache(lambda: re.compile(_a()['hash_format']))
stamp_len = functools.cache(lambda: _a()['stamp_len'])
watch_skip = functools.cache(lambda: frozenset(_a()['watch_skip_platforms']))
cards_max = functools.cache(lambda: _a()['cards_max'])
signal_domains = functools.cache(lambda: tuple(sorted({k.split('.', 1)[0] for k in canon()['classes']} | set(canon()['classless']['domains']))))
CLASSLESS_WATCHED = ('classless.loud', 'classless.fault')
hold_max = functools.cache(lambda: tuning()['notify']['hold']['max_s'])
level_abs_max = functools.cache(lambda: tuning()['notify']['level_abs_max'])
loud = functools.cache(lambda: re.compile(canon()['classless']['loudWords'], re.I))
fault = functools.cache(lambda: re.compile(canon()['classless']['faultWords'], re.I))
entity_id_cfg = functools.cache(lambda: re.compile(_a()['entity_id']))
platform_cfg = functools.cache(lambda: re.compile(_a()['platform']))
domain_id = functools.cache(lambda: re.compile(_a()['domain']))
device_class_cfg = functools.cache(lambda: re.compile(_a()['device_class']))
finding_key = functools.cache(lambda: re.compile(_a()['finding_key']))
iso_cfg = functools.cache(lambda: re.compile(_a()['iso']))
_JS_DEC = re.compile('^[+-]?(\\d+\\.?\\d*|\\.\\d+)([eE][+-]?\\d+)?$')
_JS_RADIX = re.compile('^0([xX][0-9a-fA-F]+|[oO][0-7]+|[bB][01]+)$')
POLICY_KEYS = ('alarm', 'holdS', 'severity', 'house', 'watch', 'shelvedUntil')
combo_cfg = functools.cache(lambda: _a()['combos'])
combo_id = functools.cache(lambda: re.compile(combo_cfg()['id']))
combo_preset = functools.cache(lambda: re.compile(combo_cfg()['preset']))
COMBO_KEYS = ('name', 'all', 'holdS', 'preset')
combo_idle = functools.cache(lambda: frozenset(combo_cfg()['idle_states']))
diag_visible = functools.cache(lambda: tuple(canon()['diag']['visible']))
meanings = functools.cache(lambda: canon()['meanings'])
MEANING_DEFAULT = 'default'
local_max = functools.cache(lambda: _a()['local_max'])
watchdog_off_max = functools.cache(lambda: tuning()['watchdog']['off_max'])
MEANING_KEYS = ('alarm', 'watch')

def class_of(v: Any) -> str:
    """HA's device class as compared: trimmed, lower case; anything else — none."""
    return v.strip().lower() if isinstance(v, str) else ''

def state_of(v: Any) -> str:
    """A state as compared: a string as is, a finite number as its JS text, anything else ""."""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)) and (not isinstance(v, bool)) and math.isfinite(v):
        return str(int(v)) if float(v).is_integer() else repr(float(v))
    return ''

def js_number(text: str) -> float | None:
    """`Number(text)` as the cloud reads a state (blank → NaN): None when it is no finite number."""
    t = text.strip()
    if not t:
        return None
    if _JS_DEC.match(t):
        v = float(t)
    elif _JS_RADIX.match(t):
        v = float(int(t, 0))
    else:
        return None
    return v if math.isfinite(v) else None

def base_of(eid: str, device_class: Any, name: str | None, device_kind: str | None=None) -> tuple[str, str, dict[str, Any]]:
    """(kind, the canon's key, the base) of a signal before any choice."""
    dom = eid.split('.', 1)[0]
    dc = class_of(device_class)
    own = f'{dom}.{dc}' if dc else dom
    on_dev = (canon().get('onDevice') or {}).get(device_kind or '')
    if on_dev and own in on_dev:
        return ('class', f'{device_kind}:{own}', on_dev[own])
    if dc and own in canon()['classes']:
        return ('class', own, canon()['classes'][own])
    if dom in canon()['classes']:
        return ('class', dom, canon()['classes'][dom])
    if dc:
        return ('unlisted', 'unlisted', canon()['unlisted'])
    cl = canon()['classless']
    if dom not in cl['domains']:
        return ('unlisted', 'unlisted', canon()['unlisted'])
    text = name or ''
    if loud().search(text):
        return ('classless.loud', 'classless.loud', {'alarm': cl['alarm'], **cl['withLoudWords']})
    if fault().search(text):
        return ('classless.fault', 'classless.fault', {'alarm': cl['alarm'], **cl['withWords']})
    return ('undetermined', 'undetermined', {'alarm': cl['alarm'], **canon()['undetermined']})

def _specificity(m: dict[str, Any]) -> int:
    return (4 if m.get('platform') else 0) + (2 if m.get('domain') else 0) + (1 if 'deviceClass' in m else 0)

def scope_matches(m: dict[str, Any], eid: str, platform: str | None, device_class: Any) -> bool:
    if 'platform' in m and m['platform'] != platform:
        return False
    if 'domain' in m and m['domain'] != eid.split('.', 1)[0]:
        return False
    return not ('deviceClass' in m and m['deviceClass'] != class_of(device_class))

def resolve(eid: str, *, platform: str | None=None, device_class: Any=None, name: str | None=None, policy: dict[str, Any] | None=None, device_kind: str | None=None) -> dict[str, Any]:
    """One signal's policy: entity → scopes → the canon's class → classless → unlisted (signals.ts resolvePolicy)."""
    kind, key, base = base_of(eid, device_class, name, device_kind)
    r: dict[str, Any] = {'base': kind, 'baseKey': key, 'alarm': base.get('alarm'), 'house': base['house'], 'severity': base['severity'], 'defaultSeverity': base['severity'], 'holdS': None, 'watch': True, 'shelvedUntil': None, 'lifeSafety': key in canon()['lifeSafety'], 'undetermined': kind == 'undetermined', 'from': {'alarm': 'canon', 'severity': 'canon', 'house': 'canon'}, 'source': kind}

    def layer(p: dict[str, Any], frm: str) -> None:
        for k in ('alarm', 'severity', 'house'):
            if p.get(k):
                r[k] = p[k]
                r['from'][k] = frm
        if p.get('holdS') is not None:
            r['holdS'] = p['holdS']
        if p.get('watch') is not None:
            r['watch'] = p['watch']
        if p.get('shelvedUntil'):
            r['shelvedUntil'] = p['shelvedUntil']
        if p.get('alarm') or p.get('severity') or p.get('house'):
            r['undetermined'] = False
        if any((p.get(k) is not None for k in POLICY_KEYS)):
            r['source'] = frm
    s = policy or {}
    scopes = [(i, sc) for i, sc in enumerate(s.get('scopes') or []) if scope_matches(sc['match'], eid, platform, device_class)]
    for _i, sc in sorted(scopes, key=lambda x: (_specificity(x[1]['match']), x[0])):
        layer(sc['set'], 'scope')
    r['defaultSeverity'] = r['severity']
    if (own := (s.get('entities') or {}).get(eid)) is not None:
        layer(own, 'entity')
    return r

def alarm_of(raw: Any, alarm: dict[str, Any] | None) -> bool:
    """The state is an alarm by this condition (HA's «unavailable» is one — the lost-link class — unless the site said «не авария» for it: 0.34, one choice excludes the entity everywhere; the canon itself never says {none})."""
    if alarm and 'none' in alarm:
        return False
    state = state_of(raw)
    if state == 'unavailable':
        return True
    if not alarm:
        return False
    if 'states' in alarm:
        return state in alarm['states']
    n = js_number(state)
    if n is None:
        return False
    return 'below' in alarm and n < alarm['below'] or ('above' in alarm and n > alarm['above'])

def cond_holds(raw: Any, alarm: dict[str, Any]) -> bool:
    raw = raw.state if isinstance(raw, State) else raw
    return state_of(raw) not in combo_idle() and alarm_of(raw, alarm)

def combo_holds(states: list[Any], combo: dict[str, Any]) -> bool:
    """Every condition of the combination holds now (`states` — the sources' states, in the order of combo["all"])."""
    return len(states) == len(combo['all']) and all((cond_holds(st, c['alarm']) for st, c in zip(states, combo['all'], strict=True)))

def affects_house(problem: bool, house: str, undetermined: bool) -> bool:
    """On «Всё хорошо»: a problem whose policy affects the house (not «не решено», not house «none»)."""
    return problem and (not undetermined) and (house != 'none')

def shelved_now(until: Any, now: Any=None) -> bool:
    t = when(until)
    return t is not None and t > (now or dt_util.utcnow())

def next_unshelve(pol: dict[str, Any] | None, now: Any=None) -> Any:
    """The nearest future «Отложить до» of the site's policy (entities and rules) — None: nothing is shelved."""
    now = now or dt_util.utcnow()
    times = [when(p.get('shelvedUntil')) for p in [*((pol or {}).get('entities') or {}).values(), *(x.get('set') or {} for x in (pol or {}).get('scopes') or [])]]
    ahead = [t for t in times if t is not None and t > now]
    return min(ahead) if ahead else None

def watch_by_class(eid: str, device_class: Any, platform: str | None=None) -> bool:
    """Watched by default by the canon (its watch list of «domain.device_class»), not the agent's own nor a phone's."""
    dc = class_of(device_class)
    dom = eid.split('.', 1)[0]
    return platform not in watch_skip() and (bool(dc) and f'{dom}.{dc}' in canon()['watch'] or dom in canon()['watch'])

def device_kinds(hass: HomeAssistant) -> dict[str, str]:
    """{device id: the canon's onDevice key} — a device holding an entity of that key's domain (a lock's device), the first key of the canon's order."""
    kinds = list(canon().get('onDevice') or {})
    if not kinds:
        return {}
    out: dict[str, str] = {}
    for e in er.async_get(hass).entities.values():
        dom = e.entity_id.split('.', 1)[0]
        if e.device_id and dom in kinds and (e.device_id not in out or kinds.index(dom) < kinds.index(out[e.device_id])):
            out[e.device_id] = dom
    return out

def device_kind_of(hass: HomeAssistant, eid: str, kinds: dict[str, str] | None=None) -> str | None:
    """The onDevice key of an entity's device (None: none / no device)."""
    e = er.async_get(hass).async_get(eid)
    if e is None or not e.device_id:
        return None
    return (kinds if kinds is not None else device_kinds(hass)).get(e.device_id)

def evaluate(state: State | str | None, *, eid: str | None=None, platform: str | None=None, device_class: Any=None, name: str | None=None, area: str | None=None, policy: dict[str, Any] | None=None, device_kind: str | None=None) -> dict[str, Any]:
    """{problem, house, severity, undetermined, watch, source, …} of one signal now."""
    if isinstance(state, State):
        eid = eid or state.entity_id
        device_class = device_class if device_class is not None else state.attributes.get('device_class')
        name = name if name is not None else state.name
        raw: Any = state.state
    else:
        raw = state
    if not eid:
        raise ValueError('evaluate: an entity id')
    r = resolve(eid, platform=platform, device_class=device_class, name=name, policy=policy, device_kind=device_kind)
    problem = alarm_of(raw, r['alarm'])
    shelved = shelved_now(r['shelvedUntil'])
    return {'problem': problem, 'house': r['house'], 'severity': r['severity'], 'defaultSeverity': r['defaultSeverity'], 'undetermined': r['undetermined'], 'watch': r['watch'], 'source': r['source'], 'base': r['base'], 'baseKey': r['baseKey'], 'lifeSafety': r['lifeSafety'], 'alarm': r['alarm'], 'holdS': r['holdS'], 'shelvedUntil': r['shelvedUntil'], 'shelved': shelved, 'from': r['from'], 'affects': affects_house(problem, r['house'], r['undetermined']) and (not shelved)}

def when(v: Any) -> Any:
    """An ISO time as compared (a cloud's «…Z» and an agent's «+00:00» alike); None — not one."""
    return dt_util.parse_datetime(v) if isinstance(v, str) else None
