"""Sources, outputs and levels of an alarm rule."""
from __future__ import annotations
from collections.abc import Callable
import logging
from typing import Any
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er, label_registry as lr
from ..const import MONITOR_LABELS
from ..foreign import flags as foreign_flags
from ..guard import contained
from .common import ENTITY_ID, NOT_A_STATE, SOURCE_DOMAINS, critical_classes, device_class_of, level_abs_max, level_step_share, levels, loud_name, outputs_cfg, outputs_max, quiet_name
_LOGGER = logging.getLogger(__name__)

def labelled(hass: HomeAssistant | None, eid: str) -> bool:
    """The integrator marked it «Hauflo мониторинг» in HA (on the entity or its device) — HA's admin's say, not the cloud's."""
    if hass is None:
        return False
    if (said := foreign_flags.notify_flag(hass, eid)) is not None:
        return said
    with contained(_LOGGER, 'Hauflo: labels of %s not readable — not marked for monitoring', eid):
        entry = er.async_get(hass).async_get(eid)
        if entry is None:
            return False
        ids = set(entry.labels)
        if entry.device_id and (device := dr.async_get(hass).async_get(entry.device_id)):
            ids |= set(device.labels)
        reg = lr.async_get(hass)
        return any(((label := reg.async_get_label(i)) is not None and label.name.casefold() in MONITOR_LABELS for i in ids))
    return False

def source_ok(hass: HomeAssistant | None, eid: str, owns: Callable[[str], bool] | None=None, picked: frozenset[str]=frozenset()) -> bool:
    if not eid.startswith(SOURCE_DOMAINS):
        return False
    entry = er.async_get(hass).async_get(eid) if hass is not None else None
    if entry is not None and entry.platform == 'mobile_app':
        return False
    if owns is not None and owns(eid):
        return True
    if foreign_flags.notify_flag(hass, eid) is False:
        return False
    return labelled(hass, eid) or eid in picked

def loud_verdict(hass: HomeAssistant | None, sources: list[str]) -> str | None:
    """Why a rule the integrator marked critical rings only normally; None = rings loud, even in silent mode."""
    if not sources:
        return 'no_sources'
    for s in sources:
        dc = device_class_of(hass, s)
        if dc is not None and dc not in critical_classes():
            return f'quiet_class:{dc}'[:45]
        if dc is None:
            st = hass.states.get(s) if hass is not None else None
            name = f"{s} {(st.attributes.get('friendly_name') or '' if st else '')}"
            if quiet_name().search(name) and (not loud_name().search(name)):
                return 'quiet_name'
    return None

def output_ok(hass: HomeAssistant | None, eid: str) -> bool:
    """An alarm output: a domain of OUTPUTS, a plain entity id, never a phone's, and one HA knows (registry or state)."""
    if not ENTITY_ID.match(eid) or eid.partition('.')[0] not in outputs_cfg():
        return False
    if hass is None:
        return True
    entry = er.async_get(hass).async_get(eid)
    if entry is not None and entry.platform == 'mobile_app':
        return False
    return entry is not None or hass.states.get(eid) is not None

def clean_outputs(hass: HomeAssistant | None, raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for o in raw if isinstance(raw, list) else []:
        if not isinstance(o, dict) or not isinstance(o.get('entity_id'), str) or any((x['entity_id'] == o['entity_id'] for x in out)):
            continue
        on_start, on_end = (o.get('on_start', True) is not False, o.get('on_end', True) is not False)
        if (on_start or on_end) and output_ok(hass, o['entity_id']):
            out.append({'entity_id': o['entity_id'], 'on_start': on_start, 'on_end': on_end})
        else:
            _LOGGER.warning('Hauflo: alarm output %r not accepted (allowed: %s, known in HA)', str(o.get('entity_id'))[:80], ', '.join(sorted(outputs_cfg())))
    return out[:outputs_max()]

def _number(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    try:
        x = float(v)
    except ValueError:
        return None
    return x if x == x and abs(x) <= level_abs_max() else None

def level_default(device_class: str | None) -> dict[str, float] | None:
    """The canon threshold of a device class (tuning.json notify.levels): its first direction and its step."""
    canon = levels().get(str(device_class or ''))
    if not canon:
        return None
    way = 'below' if 'below' in canon else 'above'
    return {way: canon[way], 'step': canon['step']}

def clean_thresholds(hass: HomeAssistant | None, raw: Any, sources: list[str]) -> dict[str, dict[str, float]]:
    """Per source: exactly one of below / above (a number) and the hysteresis step — only for a level (sensor.*) source."""
    out: dict[str, dict[str, float]] = {}
    if not isinstance(raw, dict):
        return out
    for eid, th in raw.items():
        if eid not in sources or not str(eid).startswith('sensor.') or (not isinstance(th, dict)):
            continue
        ways = [w for w in ('below', 'above') if w in th]
        value = _number(th.get(ways[0])) if len(ways) == 1 else None
        if value is None:
            _LOGGER.warning('Hauflo: threshold of %s not accepted: one number below / above expected, got %r', eid, th)
            continue
        canon = levels().get(str(device_class_of(hass, eid) or ''), {})
        step = _number(th.get('step'))
        if step is None or step < 0:
            step = float(canon['step']) if 'step' in canon else round(abs(value) * level_step_share(), 3)
        out[eid] = {ways[0]: value, 'step': step}
    return out

def _value(state: State | None) -> float | None:
    if state is None or state.state in NOT_A_STATE:
        return None
    return _number(state.state)

def _threshold(rule: dict[str, Any], eid: str) -> dict[str, float] | None:
    th = (rule.get('thresholds') or {}).get(eid)
    if th is not None:
        return th
    pol = (rule.get('policy') or {}).get(eid)
    return pol if pol is not None and ('below' in pol or 'above' in pol) else None

def muted(rule: dict[str, Any], eid: str) -> bool:
    """The site marked this source «не авария» itself (signals.py: a scope or the signal's own `alarm: {none}`): the rule keeps it as sent (the cloud's list stays whole), but never rings, never waits for its data and never counts it."""
    return bool(((rule.get('policy') or {}).get(eid) or {}).get('none'))

def live_sources(rule: dict[str, Any]) -> list[str]:
    """The rule's sources the engine weighs: all but the muted ones."""
    return [s for s in rule['sources'] if not muted(rule, s)]

def in_alarm(rule: dict[str, Any], state: State | None, open_now: bool=False) -> bool:
    """Is this source in alarm for the rule: a signal — one of its alarm states; a level — past its threshold, and while the alarm is open, until it is back past the threshold by its step (the hysteresis)."""
    if state is None or muted(rule, state.entity_id):
        return False
    th = _threshold(rule, state.entity_id)
    if th is None:
        pol = (rule.get('policy') or {}).get(state.entity_id)
        return state.state in (pol['states'] if pol is not None and 'states' in pol else rule['alarm_states'])
    if 'below' in th and 'above' in th:
        v = _value(state)
        if v is None:
            return False
        step = th.get('step', 0)
        return v < th['below'] or v > th['above'] or (open_now and (v <= th['below'] + step or v >= th['above'] - step))
    v = _value(state)
    if v is None:
        return False
    if 'below' in th:
        return v < th['below'] or (open_now and v <= th['below'] + th['step'])
    return v > th['above'] or (open_now and v >= th['above'] - th['step'])

def level_text(rule: dict[str, Any], state: State | None) -> str:
    """« (18 % < 20 %)» — the value and its threshold, said in numbers (no words: any language)."""
    th = _threshold(rule, state.entity_id) if state is not None else None
    v = _value(state)
    if th is None or v is None or state is None:
        return ''
    unit = str(state.attributes.get('unit_of_measurement') or '')
    way = 'below' if 'below' in th else 'above'
    return f" ({v:g} {unit} {('<' if way == 'below' else '>')} {th[way]:g} {unit})".replace('  ', ' ').replace(' )', ')')
