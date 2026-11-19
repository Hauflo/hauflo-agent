"""Season constants and the reads of climates, sensors and the KNX switch."""
from __future__ import annotations
from collections.abc import Callable
import functools
import logging
import re
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from .. import derived as dv
from ..const import DOMAIN
from ..guard import contained
from ..notify.common import label
from ..policy.knx import knx_data, knx_store_errors
from ..tuning import tuning
_LOGGER = logging.getLogger(__name__)
STORE_KEY = 'hauflo.season'
HEAT, COOL = ('heat', 'cool')
MODES = (HEAT, COOL)
RULE_SEASON = {HEAT: 'winter', COOL: 'summer'}
LABELS = ('season', 'mode')
LANGS = ('ru', 'en')
_GONE = ('unavailable', 'unknown')
_ENTITY_RE = re.compile('^[a-z_]+\\.[a-z0-9_]+$')
_t = functools.cache(lambda: tuning()['season'])
resend_s = functools.cache(lambda: _t()['resend_s'])
resend_lo = functools.cache(lambda: _t()['resend_bounds_s'][0])
resend_hi = functools.cache(lambda: _t()['resend_bounds_s'][1])
tick = functools.cache(lambda: _t()['tick_s'])
start_delay = functools.cache(lambda: _t()['start_delay_s'])
below = functools.cache(lambda: _t()['suggest_below'])
above = functools.cache(lambda: _t()['suggest_above'])
days = functools.cache(lambda: _t()['suggest_days'])
snooze_days = functools.cache(lambda: _t()['snooze_days'])
days_lo = functools.cache(lambda: _t()['days_bounds'][0])
days_hi = functools.cache(lambda: _t()['days_bounds'][1])
snooze_lo = functools.cache(lambda: _t()['snooze_bounds'][0])
snooze_hi = functools.cache(lambda: _t()['snooze_bounds'][1])
t_lo = functools.cache(lambda: _t()['threshold_bounds'][0])
t_hi = functools.cache(lambda: _t()['threshold_bounds'][1])
max_exclude = functools.cache(lambda: _t()['max_exclude'])
max_include = functools.cache(lambda: _t()['max_include'])
log_max = functools.cache(lambda: _t()['log_max'])
ACTION = 'HAUFLO_SEASON'
LATER = 'later'
NOTIFY_TAG = 'hauflo_season_suggest'
SEASON_RULE = 'season'
auto_hour = functools.cache(lambda: _t()['auto_hour'])
AUTO_BY = 'auto'
NONE, NO_DATA = ('none', 'no_data')
RECOMMENDED = (HEAT, COOL, NONE, NO_DATA)
SIGNAL = f'{DOMAIN}_season'

def mode_outputs(auto: bool) -> dict[str, bool]:
    """The season rule's outputs for the integrator's way: «вручную» asks the family both ways, «автоматически» acts."""
    return {'suggest': not auto, 'suggest_back': not auto, 'auto': auto}

def can_switch(state: Any) -> bool:
    modes = state.attributes.get('hvac_modes') or []
    return HEAT in modes and COOL in modes

def _shown(reg: er.EntityRegistry, eid: str) -> bool:
    ent = reg.async_get(eid)
    return not (ent and (ent.disabled_by or ent.hidden_by))

def is_chosen(state: Any, reg: er.EntityRegistry, ours: bool, exclude: Any, include: Any, legacy: bool=False) -> bool:
    """The one rule: capable and shown, and (Hauflo's own and not unticked) or (foreign and ticked)."""
    if not (can_switch(state) and _shown(reg, state.entity_id)):
        return False
    if ours:
        return state.entity_id not in exclude
    return state.entity_id in include or (legacy and state.entity_id not in exclude)

def acs(hass: HomeAssistant, exclude: frozenset[str] | set[str]=frozenset(), include: frozenset[str] | set[str]=frozenset(), owns: Callable[[str], bool] | None=None, legacy: bool=False) -> list[Any]:
    """The climates the season switches (is_chosen) — floors and heat-only thermostats never."""
    reg = er.async_get(hass)
    return [s for s in hass.states.async_all('climate') if s.state not in _GONE and is_chosen(s, reg, bool(owns and owns(s.entity_id)), exclude, include, legacy)]

def climates(hass: HomeAssistant, exclude: frozenset[str] | set[str]=frozenset(), include: frozenset[str] | set[str]=frozenset(), owns: Callable[[str], bool] | None=None, legacy: bool=False) -> list[dict[str, Any]]:
    """Every climate of the house for Hauflo's setting: what HA says it can, whether it is switched now."""
    reg = er.async_get(hass)
    out = []
    for s in sorted(hass.states.async_all('climate'), key=lambda x: x.entity_id):
        ent = reg.async_get(s.entity_id)
        ours = bool(owns and owns(s.entity_id))
        out.append({'entity_id': s.entity_id, 'name': label(hass, s.entity_id, s), 'platform': ent.platform if ent else None, 'modes': list(s.attributes.get('hvac_modes') or []), 'state': s.state, 'capable': can_switch(s), 'shown': _shown(reg, s.entity_id), 'heat_cool_address': heat_cool_address(hass, s.entity_id) is not None, 'ours': ours, 'chosen': is_chosen(s, reg, ours, exclude, include, legacy)})
    return out

def sensors(hass: HomeAssistant) -> list[dict[str, Any]]:
    """What the outdoor temperature may be read from: temperature sensors with statistics, weather entities."""
    out = []
    for s in sorted(hass.states.async_all('sensor'), key=lambda x: x.entity_id):
        if s.attributes.get('device_class') == 'temperature' and s.attributes.get('state_class'):
            out.append({'entity_id': s.entity_id, 'name': label(hass, s.entity_id, s), 'kind': 'sensor'})
    for s in sorted(hass.states.async_all('weather'), key=lambda x: x.entity_id):
        if dv.num(s.attributes.get('temperature')) is not None:
            out.append({'entity_id': s.entity_id, 'name': label(hass, s.entity_id, s), 'kind': 'weather'})
    return out

def heat_cool_address(hass: HomeAssistant, entity_id: str) -> str | None:
    """The heat/cool switchover address of a KNX UI climate (HA's KNX config store)."""
    knx = knx_data(hass)
    if knx is None:
        return None
    store = getattr(knx, 'config_store', None)
    if store is None:
        return None
    with contained(_LOGGER, 'Hauflo: no KNX config for %s', entity_id, expect=knx_store_errors()):
        cfg = store.get_entity_config(entity_id)
        ga = ((cfg.get('data') or {}).get('knx') or {}).get('ga_heat_cool') or {}
        w = ga.get('write')
        return w if isinstance(w, str) and w else None
    return None

def knx_connected(hass: HomeAssistant) -> bool | None:
    knx = knx_data(hass)
    if knx is None:
        return None
    with contained(_LOGGER, 'Hauflo: KNX connection state not readable'):
        return bool(knx.xknx.connection_manager.connected.is_set())
    return None

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def defaults() -> dict[str, Any]:
    return {'exclude': [], 'include': [], 'labels': LABELS[0], 'resend_s': resend_s(), 'auto': None, 'suggest': {'enabled': True, 'sensor': None, 'below': below(), 'above': above(), 'days': days(), 'snooze_days': snooze_days()}}

def clean_config(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """Only what the season understands, each value in its bounds; what is dropped is said."""
    out = defaults()
    dropped: list[str] = []
    if not isinstance(raw, dict):
        return (out, dropped)
    ex = raw.get('exclude')
    for eid in (ex if isinstance(ex, list) else [])[:max_exclude()]:
        if isinstance(eid, str) and eid.startswith('climate.') and _ENTITY_RE.match(eid):
            if eid not in out['exclude']:
                out['exclude'].append(eid)
        else:
            dropped.append(f'exclude {str(eid)[:80]!r} is not a climate')
    inc = raw.get('include')
    for eid in (inc if isinstance(inc, list) else [])[:max_include()]:
        if isinstance(eid, str) and eid.startswith('climate.') and _ENTITY_RE.match(eid):
            if eid not in out['include']:
                out['include'].append(eid)
        else:
            dropped.append(f'include {str(eid)[:80]!r} is not a climate')
    if raw.get('labels') in LABELS:
        out['labels'] = raw['labels']
    if isinstance(raw.get('auto'), bool):
        out['auto'] = raw['auto']
    elif raw.get('auto') is not None:
        dropped.append(f"auto {str(raw.get('auto'))[:20]!r} is not true / false")
    if (r := dv.num(raw.get('resend_s'))) is not None:
        out['resend_s'] = 0 if r <= 0 else int(_clamp(r, resend_lo(), resend_hi()))
    sg = raw.get('suggest')
    if isinstance(sg, dict):
        s = out['suggest']
        s['enabled'] = sg.get('enabled') is not False
        sensor = sg.get('sensor')
        if isinstance(sensor, str) and _ENTITY_RE.match(sensor) and (sensor.split('.')[0] in ('sensor', 'weather')):
            s['sensor'] = sensor
        elif sensor is not None:
            dropped.append(f'sensor {str(sensor)[:80]!r} is not a sensor or a weather entity')
        for key, lo, hi in (('below', t_lo(), t_hi()), ('above', t_lo(), t_hi()), ('days', days_lo(), days_hi()), ('snooze_days', snooze_lo(), snooze_hi())):
            if (v := dv.num(sg.get(key))) is not None:
                s[key] = int(_clamp(v, lo, hi)) if key in ('days', 'snooze_days') else _clamp(v, lo, hi)
    return (out, dropped)

def rule_body(s: dict[str, Any]) -> dict[str, Any]:
    """The suggestion's settings as a value rule (recipe «season», rules.py / window.py): the street's daily mean below `below` for `days` days in a row → «на зиму?»; strictly above `above` as long → «на лето?»."""
    return {'id': SEASON_RULE, 'name': SEASON_RULE, 'name_gen': {'recipe': SEASON_RULE}, 'purpose': SEASON_RULE, 'enabled': s['enabled'], 'input': {'entity_id': s['sensor']} if s['sensor'] else 'outdoor', 'when': 'below', 'x': s['below'], 'y': max(0.0, s['above'] - s['below']), 'window': {'stat': 'mean', 'period': 'day', 'n': int(s['days'])}, 'outputs': {'suggest': True, 'suggest_back': True, 'auto': False}, 'snooze_days': int(s['snooze_days']), 'actions': [{'do': 'season', 'value': HEAT}], 'back': 'reverse'}
