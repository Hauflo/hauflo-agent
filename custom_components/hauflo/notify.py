from __future__ import annotations
import asyncio
import contextvars
import json
import logging
import re
import time
from datetime import timedelta
from typing import Any, Callable
from homeassistant.components import persistent_notification
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, Event, HomeAssistant, State, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, label_registry as lr, translation
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util, slugify
from .natural import natural_key
from .ownership import entity_room
from .storage import HaufloStore
from .const import ACK_ACTION, DO_ACTION, DOMAIN, LOCKED_CODE, MONITOR_LABELS, NATIVE_STORE, OK_ACTION, QUIET_PARENT, RUN_ACTION, site_ru
from . import alarms_now, foreign, signals
from .tuning import TUNING
_LOGGER = logging.getLogger(__name__)
STORE_KEY = 'hauflo.notify'
ACTIVE_KEY = 'hauflo.notify_active'
ACTION_EVENT = 'mobile_app_notification_action'
ACK = 'HAUFLO_ACK'
RUN_SERVICES = {'valve.close_valve', 'switch.turn_off'}
_T = TUNING['notify']
CRITICAL_CLASSES = set(_T['critical_classes'])
REPEAT_MIN = _T['repeat_min']
RUN = 'HAUFLO_RUN'
ALARM_CLASSES = set(_T['alarm_classes'])
NOT_A_STATE = {STATE_UNAVAILABLE, STATE_UNKNOWN, None}
GROUP_WINDOW = _T['group_window_s']
NODATA_AFTER = _T['nodata_after_s']
CHATTER_COUNT = _T['chatter_count']
CHATTER_WINDOW = _T['chatter_window_s']
CHATTER_WINDOW_LIFE = _T['chatter_window_life_s']
LIFE_SAFETY_KINDS = frozenset(_T['life_safety_kinds'])

def chatter_window(rule: dict[str, Any] | None) -> int:
    return CHATTER_WINDOW_LIFE if rule and rule.get('kind') in LIFE_SAFETY_KINDS else CHATTER_WINDOW
HOLD_CANON = _T['hold']
HOLD_MAX = HOLD_CANON['max_s']
NATIVE_HOLD = _T['native_hold_s']

def native_back(rule: dict[str, Any]) -> int:
    return max(NATIVE_HOLD, chatter_window(rule))

def hold_value(v: Any) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v) or (not 0 <= v <= HOLD_MAX):
        return None
    return int(v)

def default_hold(hass: HomeAssistant | None, kind: str, sources: list[str], thresholds: dict[str, Any]) -> int:
    found = [HOLD_CANON['kinds'][kind]] if kind in HOLD_CANON['kinds'] else []
    for eid in sources:
        found.append(HOLD_CANON['level_s'] if eid in thresholds else HOLD_CANON['classes'].get(str(_device_class(hass, eid) or ''), HOLD_CANON['default_s']))
    return int(min(found)) if found else int(HOLD_CANON['default_s'])

def source_hold(rule: dict[str, Any], eid: str) -> int:
    return int((rule.get('holds') or {}).get(eid, rule.get('hold') or 0))

def own_kind(hass: HomeAssistant | None, eid: str) -> str | None:
    if hass is None:
        return None
    if eid in signals.combo_entities(hass):
        return 'combo'
    entry = er.async_get(hass).async_get(eid)
    key = entry.translation_key if entry is not None and entry.platform == DOMAIN and (entry.domain == 'binary_sensor') else None
    return key if key in HOLD_CANON['own'] else None

def own_holds(hass: HomeAssistant | None, sources: list[str], holds: dict[str, int]) -> dict[str, int]:
    pol = signals.policy(hass) or {}
    out: dict[str, int] = {}
    for eid in sources:
        if (kind := own_kind(hass, eid)) is not None:
            mine = ((pol.get('entities') or {}).get(eid) or {}).get('holdS')
            out[eid] = int(mine) if isinstance(mine, int) and eid in holds else int(HOLD_CANON['own'][kind])
    return out
LINK_KINDS = ('connection', 'link', 'connectivity')
_PHONE: contextvars.ContextVar[str | None] = contextvars.ContextVar('hauflo_phone', default=None)

class _PushErrors(logging.Handler):

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.failed: set[str] = set()
        self.limited: set[str] = set()

    def emit(self, record: logging.LogRecord) -> None:
        if (phone := _PHONE.get()):
            self.failed.add(phone)
            if record.levelno == logging.WARNING or 'rate limit' in record.getMessage().lower():
                self.limited.add(phone)
PUSH_HOUR_MAX = _T['push_hour_max']
PUSH_DAY_MAX = _T['push_day_max']
PUSH_DAY_CRITICAL = _T['push_day_critical']
PUSH_EVENT_HOUR_MAX = _T['push_event_hour_max']
PUSH_EVENT_DAY_MAX = _T['push_event_day_max']
LANES = ('alarm', 'event')
REPEAT_FAST = _T['repeat_fast']
REPEAT_SLOW = _T['repeat_slow_min']
SCOPES = ('all', 'critical', 'off')
PHONE_ID = re.compile('^mobile_app_[a-z0-9_]+$')
WORD = re.compile('^[a-z0-9_]{1,32}$')
STATES_MAX = _T['rule_states_max']
TEST_GAP = _T['test_gap_s']
TEST_PER_MIN = _T['test_per_min']
GROUP_MIN = _T['group_min']
LIST_MAX = _T['list_max']
HEARTBEAT = re.compile(_T['heartbeat_words'], re.I)
FAULT_NAME = re.compile(_T['fault_name_words'], re.I)
OUTPUTS: dict[str, dict[str, str]] = _T['outputs']
OUTPUTS_MAX = _T['outputs_max']
ENTITY_ID = re.compile('^[a-z_]+\\.[a-z0-9_]+$')
LEVELS: dict[str, dict[str, float]] = _T['levels']
LEVEL_STEP_SHARE = _T['level_step_share']
LEVEL_ABS_MAX = _T['level_abs_max']
RING_WINDOW = _T['ring_window_s']
RING_FRESH = TUNING['link']['event_recent_s']
QUIET_CHANNEL = _T['quiet_channel']
_KINDS = {k: v for k, v in _T['event_kinds'].items() if not k.startswith('_')}
EVENT_KINDS = {k: (frozenset(v.get('classes', ())), re.compile(v['words'], re.I)) for k, v in _KINDS.items()}
C_SOS = frozenset((k for k, v in _KINDS.items() if 'critical' in v.get('does', ())))
WATCH_KINDS = frozenset((k for k, v in _KINDS.items() if 'watch' in v.get('does', ())))
RING_KINDS = frozenset((k for k, v in _KINDS.items() if 'ring' in v.get('does', ())))
RING = _T['ring_push']
INTERCOM_EVENT = f'{DOMAIN}_intercom'

def event_kind(state: State) -> str:
    dc = str(state.attributes.get('device_class') or '')
    said = f"{state.attributes.get('event_type') or ''} {state.name or ''} {state.object_id}"
    return next((kind for kind, (classes, words) in EVENT_KINDS.items() if dc and dc in classes or words.search(said)), 'event')

def watched_press(state: State | None, platform: str | None=None) -> bool:
    return state is not None and state.domain == 'event' and (platform not in signals.WATCH_SKIP) and (event_kind(state) in WATCH_KINDS)
SOURCE_DOMAINS = ('binary_sensor.', 'sensor.')

def labelled(hass: HomeAssistant | None, eid: str) -> bool:
    if hass is None:
        return False
    if (said := foreign.notify_flag(hass, eid)) is not None:
        return said
    try:
        entry = er.async_get(hass).async_get(eid)
        if entry is None:
            return False
        ids = set(entry.labels)
        if entry.device_id and (device := dr.async_get(hass).async_get(entry.device_id)):
            ids |= set(device.labels)
        reg = lr.async_get(hass)
        return any(((label := reg.async_get_label(i)) is not None and label.name.casefold() in MONITOR_LABELS for i in ids))
    except Exception:
        return False

def _source_ok(hass: HomeAssistant | None, eid: str, owns: Callable[[str], bool] | None=None, picked: frozenset[str]=frozenset()) -> bool:
    if not eid.startswith(SOURCE_DOMAINS):
        return False
    entry = er.async_get(hass).async_get(eid) if hass is not None else None
    if entry is not None and entry.platform == 'mobile_app':
        return False
    if owns is not None and owns(eid):
        return True
    if foreign.notify_flag(hass, eid) is False:
        return False
    return labelled(hass, eid) or eid in picked

def _device_class(hass: HomeAssistant | None, eid: str) -> str | None:
    if hass is None:
        return None
    entry = er.async_get(hass).async_get(eid)
    dc = entry.device_class or entry.original_device_class if entry is not None else None
    if dc is None and (st := hass.states.get(eid)) is not None:
        dc = st.attributes.get('device_class')
    return str(dc) if dc is not None else None
QUIET_NAME = re.compile(_T['quiet_name_words'], re.I)
LOUD_NAME = re.compile(_T['loud_name_words'], re.I)
DOWNGRADE = re.compile('^(no_sources|quiet_class:[a-z_]{1,32}|quiet_name)$')

def loud_verdict(hass: HomeAssistant | None, sources: list[str]) -> str | None:
    if not sources:
        return 'no_sources'
    for s in sources:
        dc = _device_class(hass, s)
        if dc is not None and dc not in CRITICAL_CLASSES:
            return f'quiet_class:{dc}'[:45]
        if dc is None:
            st = hass.states.get(s) if hass is not None else None
            name = f"{s} {(st.attributes.get('friendly_name') or '' if st else '')}"
            if QUIET_NAME.search(name) and (not LOUD_NAME.search(name)):
                return 'quiet_name'
    return None

def _output_ok(hass: HomeAssistant | None, eid: str) -> bool:
    if not ENTITY_ID.match(eid) or eid.partition('.')[0] not in OUTPUTS:
        return False
    if hass is None:
        return True
    entry = er.async_get(hass).async_get(eid)
    if entry is not None and entry.platform == 'mobile_app':
        return False
    return entry is not None or hass.states.get(eid) is not None

def _clean_outputs(hass: HomeAssistant | None, raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for o in raw if isinstance(raw, list) else []:
        if not isinstance(o, dict) or not isinstance(o.get('entity_id'), str) or any((x['entity_id'] == o['entity_id'] for x in out)):
            continue
        on_start, on_end = (o.get('on_start', True) is not False, o.get('on_end', True) is not False)
        if (on_start or on_end) and _output_ok(hass, o['entity_id']):
            out.append({'entity_id': o['entity_id'], 'on_start': on_start, 'on_end': on_end})
        else:
            _LOGGER.warning('Hauflo: alarm output %r not accepted (allowed: %s, known in HA)', str(o.get('entity_id'))[:80], ', '.join(sorted(OUTPUTS)))
    return out[:OUTPUTS_MAX]

def _number(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    try:
        x = float(v)
    except ValueError:
        return None
    return x if x == x and abs(x) <= LEVEL_ABS_MAX else None

def level_default(device_class: str | None) -> dict[str, float] | None:
    canon = LEVELS.get(str(device_class or ''))
    if not canon:
        return None
    way = 'below' if 'below' in canon else 'above'
    return {way: canon[way], 'step': canon['step']}

def _clean_thresholds(hass: HomeAssistant | None, raw: Any, sources: list[str]) -> dict[str, dict[str, float]]:
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
        canon = LEVELS.get(str(_device_class(hass, eid) or ''), {})
        step = _number(th.get('step'))
        if step is None or step < 0:
            step = float(canon['step']) if 'step' in canon else round(abs(value) * LEVEL_STEP_SHARE, 3)
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
    return bool(((rule.get('policy') or {}).get(eid) or {}).get('none'))

def live_sources(rule: dict[str, Any]) -> list[str]:
    return [s for s in rule['sources'] if not muted(rule, s)]

def in_alarm(rule: dict[str, Any], state: State | None, open_now: bool=False) -> bool:
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
    th = _threshold(rule, state.entity_id) if state is not None else None
    v = _value(state)
    if th is None or v is None or state is None:
        return ''
    unit = str(state.attributes.get('unit_of_measurement') or '')
    way = 'below' if 'below' in th else 'above'
    return f" ({v:g} {unit} {('<' if way == 'below' else '>')} {th[way]:g} {unit})".replace('  ', ' ').replace(' )', ')')

def _action_ok(action: dict[str, Any] | None, owns: Callable[[str], bool] | None) -> bool:
    if not action:
        return False
    service, eid = (str(action.get('service')), str(action.get('entity_id')))
    if service not in RUN_SERVICES or eid.split('.')[0] != service.split('.')[0]:
        return False
    return owns is not None and bool(owns(eid))
_SCOPE_RANK = {'off': 0, 'critical': 1, 'all': 2}

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
        if _SCOPE_RANK[v] > _SCOPE_RANK[oa[k]] if k in oa else v != 'off':
            return f'audience {k}: more'
    nr, or_ = (new.get('rings') or {}, old.get('rings') or {})
    for k, v in or_.items():
        if v is False and nr.get(k) is not False:
            return f'rings {k}: more'
    for k, v in nr.items():
        if v is True and or_.get(k) is not True:
            return f'rings {k}: more'
    return None

def push_data(os: str | None, *, tag: str, critical: bool=False, quiet: bool=False, actions: list | None=None, silent: bool=False) -> dict[str, Any]:
    data: dict[str, Any] = {'tag': tag}
    if actions:
        data['actions'] = actions
    if quiet:
        data.update({'push': {'interruption-level': 'passive'}} if os == 'ios' else {'alert_once': True})
    if silent and (not critical):
        data.update({'push': {'interruption-level': 'passive'}} if os == 'ios' else {'channel': QUIET_CHANNEL, 'importance': 'low'})
    if critical:
        if os == 'ios':
            data['push'] = {'sound': {'name': 'default', 'critical': 1, 'volume': 1.0}, 'interruption-level': 'critical'}
        else:
            data.update({'priority': 'high', 'ttl': 0, 'channel': 'alarm_stream', 'importance': 'high'})
    return data

def _button(action: dict[str, Any], name: str) -> str:
    return f"{('Закрыть' if action['service'] == 'valve.close_valve' else 'Выключить')}: {name}"[:60]
_EN_FRAGMENTS = [('Hauflo: проверка уведомлений', 'Hauflo: notification test'), ('Так будут приходить аварии.', 'This is how alarms will arrive.'), (' Критичные — со звуком даже в беззвучном режиме.', ' Critical ones ring even on silent.'), (', никто не подтвердил. Повтор ', ', nobody accepted it. Repeat '), ('Никто не подтвердил. Повтор ', 'Nobody accepted it. Repeat '), (': снова в норме.', ': back to normal.'), ('Снова в норме: ', 'Back to normal: '), (': авария в ', ': alarm at '), (': авария с ', ': alarm since '), (': авария.', ': alarm.'), (' (последняя в ', ' (latest at '), (': выполнено (', ': done ('), (': не получилось — ', ': failed — '), ('Открыто: ', 'Open: '), ('Принято: ', 'Accepted: '), ('Выключить: ', 'Turn off: '), ('Закрыть: ', 'Close: '), ('кто-то', 'someone')]
_EN_WORDS = {'Принято': 'Accept', 'Авария': 'Alarm'}
_EN_TITLES = {'Протечка': 'Water leak', 'Дым': 'Smoke', 'Газ': 'Gas', 'Авария котла': 'Boiler alarm', 'Авария оборудования': 'Equipment alarm', 'Температура': 'Temperature', 'Батарейка': 'Battery', 'Двери и окна': 'Doors and windows', 'Своё': 'Custom', 'Связь со щитом': 'Panel link lost', 'Авария': 'Alarm'}
_RU_TITLES = {en: ru for ru, en in _EN_TITLES.items()}

def label(hass: HomeAssistant, entity_id: str, state: State | None=None) -> str:
    state = state or hass.states.get(entity_id)
    if (name := (str(state.attributes.get('friendly_name') or '').strip() if state else '')):
        return plain(once(name))
    entry = er.async_get(hass).async_get(entity_id)
    if entry:
        if (name := (entry.name or entry.original_name or '')):
            return plain(once(str(name)))
        if entry.device_id and (device := dr.async_get(hass).async_get(entry.device_id)):
            if (name := (device.name_by_user or device.name or '')):
                return plain(once(str(name)))
    return plain(entity_id.partition('.')[2] or entity_id)
_SNAKE = re.compile('\\b[a-z0-9]+(?:_[a-z0-9]+)+\\b')

def plain(name: str) -> str:
    out = _SNAKE.sub(lambda m: m.group(0).replace('_', ' '), name.strip())
    return out[:1].upper() + out[1:] if out != name.strip() else out

def once(name: str) -> str:
    half, rest = (len(name) // 2, len(name) % 2)
    if rest and name[half] == ' ' and (name[:half] == name[half + 1:]):
        return name[:half]
    return name

@callback
def _event_entity(data: Any) -> bool:
    return str(data.get('entity_id') or '').startswith('event.')

@callback
def _automation_event(data: Any) -> bool:
    return str(data.get('entity_id') or '').startswith('automation.')

def _en(text: str) -> str:
    if text in _EN_WORDS:
        return _EN_WORDS[text]
    for ru, en in _EN_FRAGMENTS:
        text = text.replace(ru, en)
    text = re.sub('^С (\\d)', 'Since \\1', text)
    text = re.sub(' и ещё (\\d+)', ' and \\1 more', text)
    return re.sub('(\\d+) (устройство|устройства|устройств)\\b', lambda m: f"{m.group(1)} device{('' if m.group(2) == 'устройство' else 's')}", text)

def policy_overlay(hass: HomeAssistant | None, sources: list[str], *, own_states: bool, thresholds: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    pol = signals.policy(hass)
    if hass is None or not pol or (not (pol['scopes'] or pol['entities'])):
        return ({}, {})
    overlay: dict[str, dict[str, Any]] = {}
    holds: dict[str, int] = {}
    for eid in sources:
        st = hass.states.get(eid)
        r = signals.resolve(eid, platform=signals.platform_of(hass, eid), device_class=st.attributes.get('device_class') if st else None, name=st.name if st else None, policy=pol)
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
    if not holds:
        return default_hold(hass, kind, sources, thresholds)
    rest = [e for e in sources if e not in holds]
    found = [*holds.values()]
    if rest or kind in HOLD_CANON['kinds']:
        found.append(default_hold(hass, kind, rest, thresholds))
    return int(min(found))

def _clean_rules(raw: Any, hass: HomeAssistant | None=None, owns: Callable[[str], bool] | None=None) -> dict[str, Any]:
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
        if states_raw not in (None, []) and (not (isinstance(states_raw, list) and len(states_raw) <= STATES_MAX and all((isinstance(s, str) and WORD.match(s) for s in states_raw)))):
            out.setdefault('rejected', []).append({'id': str(r['id']), 'reason': 'bad_alarm_states', 'value': [str(s)[:40] for s in states_raw][:STATES_MAX + 1] if isinstance(states_raw, list) else str(states_raw)[:40]})
            _LOGGER.warning('Hauflo: alarm rule %s not accepted: alarm_states %r are not state names (a-z, 0-9, _), up to %d', str(r['id'])[:64], states_raw, STATES_MAX)
            continue
        sources = [str(s) for s in r['sources'] if _source_ok(hass, str(s), owns, picked)]
        wanted = r.get('priority') == 'critical'
        verdict = loud_verdict(hass, sources) if wanted else None
        critical = wanted and verdict is None
        downgrade = verdict if wanted else str(r['downgrade']) if DOWNGRADE.match(str(r.get('downgrade') or '')) else None
        repeat = max(0, min(_int(r.get('repeat_min')), 240))
        outputs = _clean_outputs(hass, r.get('outputs'))
        thresholds = _clean_thresholds(hass, r.get('thresholds'), sources)
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
        out['rules'].append({'id': str(r['id']), 'kind': kind, 'title': str(r.get('title') or 'Авария')[:80], 'sources': sources, **({'picked': [s for s in sources if s in picked]} if any((s in picked for s in sources)) else {}), 'alarm_states': list(states_raw) if states_raw else ['on'], 'recipients': {'all': bool(rec.get('all')), 'people': [str(p) for p in rec.get('people') or [] if str(p).startswith('person.')], 'groups': [str(g) for g in rec.get('groups') or []], 'phones': [str(p) for p in rec.get('phones') or [] if PHONE_ID.match(str(p))]}, 'priority': 'critical' if critical else 'normal', **({'downgrade': downgrade} if downgrade and (not critical) else {}), 'repeat_min': max(REPEAT_MIN, repeat) if repeat else 0, 'enabled': r.get('enabled', True) is not False, **({'action': {'title': str(action.get('title') or '')[:40], 'service': str(action.get('service') or ''), 'entity_id': str(action.get('entity_id') or '')}} if action and _action_ok(action, owns) else {}), **({'outputs': outputs} if outputs else {}), **({'thresholds': thresholds} if thresholds else {}), **({'policy': overlay} if overlay else {}), 'hold_s': own_hold, 'hold': hold, **({'holds': selfheld} if selfheld else {})})
    titles = _RU_TITLES if site_ru(getattr(getattr(hass, 'config', None), 'language', None) if hass is not None else None, out) else _EN_TITLES
    for r in out['rules']:
        r['title'] = titles.get(r['title'], r['title'])
    return out

def _devices(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return 'устройство'
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return 'устройства'
    return 'устройств'

def _int(v: Any) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0

class LockedChange(PermissionError):
    pass

class NotifyEngine:

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: HaufloStore = HaufloStore(hass, STORE_KEY)
        self.active_store: HaufloStore = HaufloStore(hass, ACTIVE_KEY)
        self.config: dict[str, Any] = {'rules': [], 'groups': []}
        self.ready = False
        self._unsub_state = None
        self._unsub_action = None
        self._unsub_events = None
        self._unsub_intercom: Callable[[], None] | None = None
        self._rang: dict[str, float] = {}
        self._visits: dict[str, str] = {}
        self._said: dict[str, tuple[str, str]] = {}
        self.watches: Callable[[str], bool] | None = None
        self.quiet_now: Callable[[], bool] | None = None
        self.lock_owns: Callable[[str], bool] | None = None
        self.active: dict[str, dict[str, Any]] = {}
        self.groups: dict[str, dict[str, Any]] = {}
        self._restored: dict[str, dict[str, Any]] = {}
        self._pending_start: dict[str, list[str]] = {}
        self._pending_clear: dict[str, list[tuple[str, dict[str, Any], State]]] = {}
        self._pending_nodata: dict[str, list[str]] = {}
        self._pending_back: dict[str, list[str]] = {}
        self._nodata_timers: dict[str, Callable[[], None]] = {}
        self._flaps: dict[str, list[float]] = {}
        self.unacked_rtn: dict[str, dict[str, Any]] = {}
        self.house_acks: dict[str, str] = {}
        self._chatter: dict[str, Callable[[], None]] = {}
        self._holding: dict[str, Callable[[], None]] = {}
        self._flush_unsub = None
        self._late_kicks: dict[str, Callable[[], None]] = {}
        self.sent_log: list[dict[str, Any]] = []
        self.on_change = None
        self.owns: Callable[[str], bool] | None = None
        self.locked: Callable[[], bool] | None = None
        self._seen_ok: set[str] = set()
        self._pushes: dict[str, list[float]] = {}
        self.limited: dict[str, str] = {}
        self.throttled: dict[str, str] = {}
        self._tested: dict[str, float] = {}
        self._tests: list[float] = []
        self.native: dict[str, dict[str, Any]] = {}
        self.native_store: HaufloStore = HaufloStore(hass, NATIVE_STORE)
        self._unsub_helper = None
        self._native_who: dict[str, str] = {}
        self._native_first: set[str] = set()
        self._missed: dict[str, Any] = {}
        self._missed_src: dict[str, list[str]] = {}
        self._unsub_automations = None
        self._sounding: dict[str, set[str]] = {}

    @callback
    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    async def async_start(self) -> None:
        stored = await self.store.async_load()
        self.config = _clean_rules(stored, self.hass, self.owns)
        self.ready = stored is not None
        saved = await self.active_store.async_load() or {}
        self._seen_ok = {str(x) for x in saved.get('seen_ok') or []}
        self._pushes = {str(k): [float(t) for t in v if isinstance(t, (int, float))] for k, v in (saved.get('pushes') or {}).items() if isinstance(v, list)}
        self.unacked_rtn = {str(k): v for k, v in (saved.get('rtn') or {}).items() if isinstance(v, dict)}
        self.house_acks = {str(k): str(v)[:80] for k, v in (saved.get('house_acks') or {}).items() if isinstance(v, str) and v}
        self._restored = {k: v for k, v in (saved.get('active') or {}).items() if isinstance(v, dict)}
        for rule_id, g in (saved.get('groups') or {}).items():
            if isinstance(g, dict):
                self.groups[rule_id] = {'since': g.get('since'), 'acked_by': g.get('acked_by'), 'count': int(g.get('count') or 1), 'repeat': None, 'last': g.get('last')}
        saved_native = await self.native_store.async_load()
        self.native = {str(k): v for k, v in (saved_native or {}).items() if isinstance(v, dict) and v.get('automation') and v.get('helper')}
        self._unsub_action = self.hass.bus.async_listen(ACTION_EVENT, self._on_action)
        self._watch_helpers()
        self._unsub_automations = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_automation, event_filter=_automation_event)
        self._unsub_events = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_event_entity, event_filter=_event_entity)
        self._unsub_intercom = self.hass.bus.async_listen(INTERCOM_EVENT, self._on_intercom)
        self._apply()
        self._missed_src = {}
        for key, inc in self._restored.items():
            if not inc.get('nodata'):
                self._missed_src.setdefault(key.partition('|')[0], []).append(key.partition('|')[2])

        async def _started(_hass: HomeAssistant) -> None:
            for rule_id in list(self.native):
                self._watch_missed(rule_id)
        async_at_started(self.hass, _started)

    @callback
    def async_stop(self) -> None:
        if self._unsub_events:
            self._unsub_events()
            self._unsub_events = None
        if self._unsub_intercom:
            self._unsub_intercom()
            self._unsub_intercom = None
        if self._unsub_automations:
            self._unsub_automations()
            self._unsub_automations = None
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_action:
            self._unsub_action()
            self._unsub_action = None
        if self._unsub_helper:
            self._unsub_helper()
            self._unsub_helper = None
        if self._flush_unsub:
            self._flush_unsub()
            self._flush_unsub = None
        for cancel in [*self._nodata_timers.values(), *self._chatter.values(), *self._holding.values(), *self._missed.values(), *self._late_kicks.values()]:
            cancel()
        self._late_kicks.clear()
        self._nodata_timers.clear()
        self._chatter.clear()
        self._holding.clear()
        self._missed.clear()
        snapshot = self._snapshot()
        self.active_store.async_delay_save(lambda: snapshot, 0)
        for inc in [*self.active.values(), *self.groups.values()]:
            if inc.get('repeat'):
                inc['repeat']()
        self.active.clear()
        self.groups.clear()

    def check(self, raw: Any) -> dict[str, Any]:
        config = _clean_rules(raw, self.hass, self.owns)
        if self.locked is not None and self.locked() and (why := narrowing(config, self.config)):
            raise LockedChange(why)
        return config

    @callback
    def policy_changed(self) -> bool:
        before = {r['id']: (r.get('policy'), r['hold'], r.get('holds')) for r in self.config['rules']}
        self.config = _clean_rules(self.config, self.hass, self.owns)
        after = {r['id']: (r.get('policy'), r['hold'], r.get('holds')) for r in self.config['rules']}
        if before == after:
            return False
        self._apply()
        self._changed()
        return any((rid in self.native and before.get(rid) != after[rid] for rid in after))

    async def async_set(self, raw: Any) -> dict[str, Any]:
        config = self.check(raw)
        self.config = config
        self.ready = True
        await self.store.async_save(self.config)
        self._apply()
        self._changed()
        return self.config

    async def async_remove(self) -> None:
        await self.store.async_remove()

    def _snapshot(self) -> dict[str, Any]:
        keep = ('since', 'acked_by', 'count', 'group', 'own', 'last', 'pushed')
        return {'active': {**self._restored, **{k: {f: v.get(f) for f in keep} for k, v in self.active.items()}}, 'groups': {r: {f: g.get(f) for f in ('since', 'acked_by', 'count', 'last')} for r, g in self.groups.items()}, 'seen_ok': sorted(self._seen_ok), 'rtn': self.unacked_rtn, 'pushes': self._pushes, 'house_acks': self.house_acks}

    @callback
    def _save(self) -> None:
        self.active_store.async_delay_save(self._snapshot, 2)

    @callback
    def _apply(self) -> None:
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        sources = sorted({s for r in self.config['rules'] if r['enabled'] for s in live_sources(r)})
        if sources:
            self._unsub_state = async_track_state_change_event(self.hass, sources, self._on_state)
        for cancel in self._holding.values():
            cancel()
        self._holding.clear()
        for rule in self.config['rules']:
            if not rule['enabled']:
                continue
            for entity_id in live_sources(rule):
                key = f"{rule['id']}|{entity_id}"
                state = self.hass.states.get(entity_id)
                if not state or state.state in NOT_A_STATE:
                    self._watch_nodata(rule, entity_id)
                if key in self.active or not state or state.state in NOT_A_STATE:
                    continue
                if not in_alarm(rule, state):
                    self._seen_ok.add(entity_id)
                if key in self._restored:
                    self._adopt(rule, key)
                    if not in_alarm(rule, state, open_now=True):
                        self._clear(rule, state)
                elif in_alarm(rule, state):
                    self._start_held(rule, state)
        keep = {f"{r['id']}|{s}" for r in self.config['rules'] if r['enabled'] for s in live_sources(r)}
        for key in [k for k in self.active if k not in keep]:
            self._end_quietly(key)
        known = {r['id'] for r in self.config['rules']}
        self._restored = {k: v for k, v in self._restored.items() if k in keep or k.partition('|')[0] not in known}
        for rule_id in [r for r in self.groups if not any((k.startswith(r + '|') for k in [*self.active, *self._restored]))]:
            self._end_group(rule_id)
        outs = {r['id']: {o['entity_id'] for o in r.get('outputs') or []} for r in self.config['rules'] if r['enabled']}
        for rule_id, eids in list(self._sounding.items()):
            gone = eids - outs.get(rule_id, set())
            if gone:
                self.hass.async_create_task(self._switch(rule_id, sorted(gone), 'on_end', 'rule changed'))
        self._save()

    @callback
    def _on_state(self, event: Event) -> None:
        new: State | None = event.data.get('new_state')
        old: State | None = event.data.get('old_state')
        if not new:
            return
        for rule in self.config['rules']:
            if not rule['enabled'] or new.entity_id not in rule['sources'] or muted(rule, new.entity_id):
                continue
            key = f"{rule['id']}|{new.entity_id}"
            alarm = in_alarm(rule, new, open_now=key in self.active or key in self._restored)
            if not alarm and new.state not in NOT_A_STATE and (new.entity_id not in self._seen_ok):
                self._seen_ok.add(new.entity_id)
                self._save()
            was = in_alarm(rule, old, open_now=key in self.active)
            if not alarm:
                self._unhold(key)
            if new.state in NOT_A_STATE:
                self._watch_nodata(rule, new.entity_id)
            elif (cancel := self._nodata_timers.pop(key, None)) is not None:
                cancel()
            if key in self.active and self.active[key].get('nodata') and (new.state not in NOT_A_STATE):
                self.active.pop(key)
                persistent_notification.async_dismiss(self.hass, self._tag(key))
                self._pending_back.setdefault(rule['id'], []).append(key)
                self._later()
                if alarm:
                    self._start_held(rule, new)
                continue
            if key in self._restored and new.state not in NOT_A_STATE:
                self._adopt(rule, key)
                if not alarm:
                    self._clear(rule, new)
            elif alarm and (not was) and (key not in self.active):
                if old is not None and old.state in NOT_A_STATE and self._ha_misses(rule, old, new):
                    self._native_first.add(rule['id'])
                self._start_held(rule, new)
            elif not alarm and key in self.active and (new.state not in NOT_A_STATE):
                self._clear(rule, new)

    @callback
    def _on_event_entity(self, event: Event) -> None:
        new: State | None = event.data.get('new_state')
        old: State | None = event.data.get('old_state')
        if new is None or (old is not None and old.state == new.state):
            return
        at = dt_util.parse_datetime(new.state)
        if at is None or at.tzinfo is None or abs((dt_util.utcnow() - at).total_seconds()) > RING_FRESH:
            return
        eid = new.entity_id
        kind = event_kind(new)
        if self.lock_owns is not None and self.lock_owns(eid) and (kind not in WATCH_KINDS):
            return
        said = str(new.attributes.get('event_type') or '').lower()
        if said in RING['clear_event_types'] or said in RING['skip_event_types']:
            if said in RING['clear_event_types']:
                for pushed in self._pushed_with(eid):
                    self.hass.async_create_task(self._clear_ring(pushed))
            return
        if not (self.watches(eid) if self.watches is not None else labelled(self.hass, eid)):
            return
        now, visit = (time.monotonic(), self._visit(eid, kind))
        first = next((e for e, t in self._rang.items() if now - t < RING_WINDOW and self._visits.get(e) == visit), None)
        if first is not None:
            self._said[eid] = (new.state, self._said[first][1] if first == eid and first in self._said else '')
            return
        self._rang[eid], self._visits[eid], self._said[eid] = (now, visit, (new.state, new.state))
        self.hass.async_create_task(self._send_ring(new))

    def _device(self, eid: str) -> str | None:
        entry = er.async_get(self.hass).async_get(eid)
        return entry.device_id if entry is not None and entry.device_id else None

    def _visit(self, eid: str, kind: str) -> str:
        return f'{dev}|{kind}' if kind in RING_KINDS and (dev := self._device(eid)) else eid

    def _pushed_with(self, eid: str) -> list[str]:
        dev = self._device(eid)
        return [e for e in self._rang if e == eid or (dev and self._visits.get(e, '').startswith(f'{dev}|'))]

    def ring_said(self, state: State) -> str | None:
        if str(state.attributes.get('event_type') or '').lower() in (*RING['clear_event_types'], *RING['skip_event_types']):
            return None
        last, visit = self._said.get(state.entity_id, ('', ''))
        return visit or None if last == state.state else state.state

    async def _send_ring(self, state: State) -> None:
        name, where = (self._name(state.entity_id), self._where(state))
        kind = event_kind(state)
        try:
            got = await translation.async_get_translations(self.hass, 'en' if self._english() else 'ru', 'exceptions', [DOMAIN])
        except Exception:
            got = {}

        def word(key: str, fallback: str) -> str:
            return got.get(f'component.{DOMAIN}.exceptions.{key}.message') or fallback
        what = word(f'event_push_{kind}', '{name}').format(name=name)
        head = word('event_push_title' if where else 'event_push_title_bare', '{what}').format(what=what, where=where)
        message = word(f'event_push_{kind}_text', '{name}').format(name=name)
        critical = kind in C_SOS
        rule = {'id': 'event', 'title': name, 'priority': 'critical' if critical else 'normal', 'recipients': {'all': True, 'people': [], 'groups': [], 'phones': []}}
        silent = not critical and bool(self.quiet_now and self.quiet_now())
        extra = await self._ring_extra(state, urgent=not silent) if kind in RING_KINDS and (not critical) else None
        await self._send(rule, head, message, tag=self._tag(f'event|{state.entity_id}'), critical=critical, silent=silent, targets=self.ring_targets(), extra=extra, lane='event')

    async def _ring_extra(self, state: State, *, urgent: bool) -> Callable[[str | None], dict[str, Any]]:
        camera = self._door_camera(state.entity_id)
        link = await self._door_link({state.entity_id, *([camera] if camera else [])})

        def extra(os: str | None) -> dict[str, Any]:
            out: dict[str, Any] = {}
            if camera:
                out['image'] = f'/api/camera_proxy/{camera}'
                if os == 'ios':
                    out['entity_id'] = camera
            if link:
                out['url' if os == 'ios' else 'clickAction'] = link
            if urgent:
                out.update({'push': dict(RING['ios'])} if os == 'ios' else RING['android'])
            return out
        return extra

    def _door_camera(self, entity_id: str) -> str | None:
        reg = er.async_get(self.hass)
        entry = reg.async_get(entity_id)
        if entry is None or not entry.device_id:
            return None
        cams = sorted((e.entity_id for e in er.async_entries_for_device(reg, entry.device_id) if e.domain == 'camera' and e.translation_key not in RING['skip_camera_keys']))
        return cams[0] if cams else None

    async def _door_link(self, ids: set[str]) -> str | None:
        try:
            from homeassistant.components.lovelace.const import LOVELACE_DATA
            dashboards = list(self.hass.data[LOVELACE_DATA].dashboards.items())
        except (ImportError, KeyError):
            return None
        for url_path, dash in dashboards:
            if not str(url_path or '').startswith('hauflo-'):
                continue
            try:
                config = await dash.async_load(False)
            except Exception:
                continue
            views = config.get('views') if isinstance(config, dict) else None
            hits = [(i, v) for i, v in enumerate(views or []) if isinstance(v, dict) and any((x in json.dumps(v) for x in ids))]
            if hits:
                i, view = hits[1] if len(hits) > 1 else hits[0]
                return f"/{url_path}/{view.get('path') or i}"
        return None

    async def _clear_ring(self, entity_id: str) -> None:
        self._rang.pop(entity_id, None)
        self._visits.pop(entity_id, None)
        rule = {'id': 'event', 'title': '', 'priority': 'normal', 'recipients': {'all': True, 'people': [], 'groups': [], 'phones': []}}
        await self._send(rule, '', 'clear_notification', tag=self._tag(f'event|{entity_id}'), critical=False, targets=self.ring_targets(), force=True, lane='event')

    @callback
    def _on_intercom(self, event: Event) -> None:
        call = event.data.get('call')
        if event.data.get('state') in RING['clear_states'] and isinstance(call, str) and (call in self._rang):
            self.hass.async_create_task(self._clear_ring(call))

    def rings_on(self, phone: dict[str, Any], config: dict[str, Any] | None=None) -> bool:
        rings = (config or self.config).get('rings') or {}
        for key in (phone['service'], phone.get('person'), '*'):
            if key and isinstance(rings.get(key), bool):
                return rings[key]
        return self.scope(phone, config) != 'off'

    def ring_targets(self, config: dict[str, Any] | None=None) -> list[dict[str, Any]]:
        return [p for p in self.phones() if self.rings_on(p, config)]

    def _where(self, state: State) -> str:
        area_id = entity_room(self.hass, state.entity_id)
        area = ar.async_get(self.hass).async_get_area(area_id) if area_id else None
        return area.name if area else ''

    def _name(self, entity_id: str) -> str:
        return label(self.hass, entity_id)

    def _where_list(self, keys: list[str], rule: dict[str, Any] | None=None) -> str:
        by: dict[str, list[str]] = {}
        for k in keys:
            entity_id = k.partition('|')[2]
            state = self.hass.states.get(entity_id)
            by.setdefault(self._where(state) if state else '', []).append(self._name(entity_id) + (level_text(rule, state) if rule else ''))
        parts, shown = ([], 0)
        for area in sorted(by, key=lambda a: (a == '', natural_key(a))):
            if shown >= LIST_MAX:
                break
            names = by[area][:LIST_MAX - shown]
            shown += len(names)
            parts.append((f'{area}: ' if area else '') + ', '.join(names))
        rest = len(keys) - shown
        return '; '.join(parts) + (f' и ещё {rest}' if rest > 0 else '')

    @callback
    def _adopt(self, rule: dict[str, Any], key: str) -> None:
        saved = self._restored.pop(key)
        self.active[key] = {'since': saved.get('since') or dt_util.utcnow().isoformat(), 'acked_by': saved.get('acked_by'), 'repeat': None, 'count': int(saved.get('count') or 1), 'group': bool(saved.get('group')), 'own': bool(saved.get('own')), 'last': saved.get('last'), 'pushed': bool(saved.get('pushed'))}
        group = self.groups.get(rule['id'])
        if self.active[key]['group'] and group:
            if not group['acked_by'] and (not group.get('repeat')):
                self._schedule_group(rule)
        elif not self.active[key]['acked_by']:
            self._schedule(rule, key)
        if not self.active[key]['acked_by']:
            if self.active[key]['group']:
                members = [k for k, inc in self.active.items() if k.startswith(rule['id'] + '|') and inc.get('group')]
                self._note(f'Открыто: {self._where_list(members)}.', title=f"🚨 {rule['title']} — {len(members)} {_devices(len(members))}", notification_id=self._gtag(rule['id']), rule=rule)
            elif (state := self.hass.states.get(key.partition('|')[2])) is not None:
                where = self._where(state)
                self._note(f'{self._name(state.entity_id)}: авария.', title=f"🚨 {rule['title']}" + (f' — {where}' if where else ''), notification_id=self._tag(key), rule=rule)
        self._save()

    def _link_unknown(self, rule: dict[str, Any], state: State) -> bool:
        if rule['kind'] not in ('connection', 'link', 'connectivity'):
            return False
        return state.entity_id not in self._seen_ok

    @callback
    def _watch_nodata(self, rule: dict[str, Any], entity_id: str) -> None:
        key = f"{rule['id']}|{entity_id}"
        if rule['kind'] in LINK_KINDS or muted(rule, entity_id) or key in self._nodata_timers or (key in self.active) or (entity_id not in self._seen_ok):
            return

        @callback
        def _go(_now) -> None:
            self._nodata_timers.pop(key, None)
            s = self.hass.states.get(entity_id)
            if (s is None or s.state in NOT_A_STATE) and key not in self.active and any((r['id'] == rule['id'] and r['enabled'] for r in self.config['rules'])):
                self.active[key] = {'since': dt_util.utcnow().isoformat(), 'acked_by': None, 'repeat': None, 'count': 0, 'group': False, 'nodata': True}
                self._pending_nodata.setdefault(rule['id'], []).append(key)
                self._later()
        self._nodata_timers[key] = async_call_later(self.hass, NODATA_AFTER, _go)

    async def _flush_nodata(self, rules: dict[str, dict[str, Any]]) -> None:
        lost, back = (self._pending_nodata, self._pending_back)
        self._pending_nodata, self._pending_back = ({}, {})
        t = await self._words() if lost or back else {}
        for rule_id, keys in lost.items():
            rule = rules.get(rule_id)
            keys = [k for k in keys if k in self.active]
            if not rule or not keys:
                continue
            names = self._where_list(keys)
            title = t.get('nodata_title', '📵 {title}').format(title=rule['title']) + (f' — {len(keys)} {_devices(len(keys))}' if len(keys) > 1 else '')
            message = t.get('nodata', '{names}').format(minutes=NODATA_AFTER // 60, names=names)
            own = self._engine_says(rule_id)
            await self._send(rule, title, message, tag=self._gtag(rule_id) + '_nd', critical=False, force=own)
            self._note(message, title=title, notification_id=self._gtag(rule_id) + '_nd', rule=None if own else rule)
        for rule_id, keys in back.items():
            rule = rules.get(rule_id)
            if not rule:
                continue
            if not any((k.startswith(rule_id + '|') and inc.get('nodata') for k, inc in self.active.items())):
                persistent_notification.async_dismiss(self.hass, self._gtag(rule_id) + '_nd')
            await self._send(rule, t.get('nodata_back_title', '✅ {title}').format(title=rule['title']), t.get('nodata_back', '{names}').format(names=self._where_list(keys)), tag=self._gtag(rule_id) + '_nd', critical=False, force=self._engine_says(rule_id))

    @callback
    def _start_held(self, rule: dict[str, Any], state: State) -> None:
        key = f"{rule['id']}|{state.entity_id}"
        hold = source_hold(rule, state.entity_id)
        hold = max(NATIVE_HOLD, hold) if self._engine_says(rule['id']) else hold
        left = hold - (dt_util.utcnow() - state.last_changed).total_seconds()
        if left <= 0:
            if (cancel := self._holding.pop(key, None)) is not None:
                cancel()
            self._start(rule, state)
            return
        if key in self._holding:
            return
        rule_id, eid = (rule['id'], state.entity_id)

        @callback
        def _held(_now) -> None:
            self._holding.pop(key, None)
            now_rule = next((r for r in self.config['rules'] if r['id'] == rule_id and r['enabled'] and (eid in r['sources'])), None)
            st = self.hass.states.get(eid)
            if now_rule is not None and st is not None and (key not in self.active) and in_alarm(now_rule, st):
                self._start(now_rule, st)
        self._holding[key] = async_call_later(self.hass, left, _held)

    @callback
    def _unhold(self, key: str) -> None:
        if (cancel := self._holding.pop(key, None)) is not None:
            cancel()
            rule_id = key.partition('|')[0]
            if rule_id not in self._pending_start and (not any((k.startswith(rule_id + '|') for k in self._holding))):
                self._native_first.discard(rule_id)

    @callback
    def _start(self, rule: dict[str, Any], state: State) -> None:
        if self._link_unknown(rule, state):
            return
        key = f"{rule['id']}|{state.entity_id}"
        self.unacked_rtn.pop(key, None)
        now = time.monotonic()
        flaps = [x for x in self._flaps.get(key, []) if now - x < CHATTER_WINDOW] + [now]
        self._flaps[key] = flaps
        chattering = key in self._chatter
        self.active[key] = {'since': dt_util.utcnow().isoformat(), 'acked_by': None, 'repeat': None, 'count': 0, 'group': False, **({'quiet': True} if chattering or len(flaps) >= CHATTER_COUNT else {})}
        self._calm_later(key)
        if chattering:
            return
        if len(flaps) >= CHATTER_COUNT:
            self._chatter[key] = lambda: None
            self.hass.async_create_task(self._say_chatter(rule, state))
            return
        self._pending_start.setdefault(rule['id'], []).append(key)
        self._later()

    @callback
    def _calm_later(self, key: str) -> None:
        if key not in self._chatter:
            return
        self._chatter[key]()

        @callback
        def _calm(_now) -> None:
            self._chatter.pop(key, None)
            self._flaps.pop(key, None)
            inc = self.active.get(key)
            if inc and inc.pop('quiet', None):
                self._pending_start.setdefault(key.partition('|')[0], []).append(key)
                self._later()
        self._chatter[key] = async_call_later(self.hass, CHATTER_WINDOW, _calm)

    async def _words(self) -> dict[str, str]:
        try:
            got = await translation.async_get_translations(self.hass, 'en' if self._english() else 'ru', 'exceptions', [DOMAIN])
        except Exception:
            return {}
        pre = f'component.{DOMAIN}.exceptions.alarm_native_'
        return {k.removeprefix(pre).removesuffix('.message'): v for k, v in got.items() if k.startswith(pre)}

    async def _say_chatter(self, rule: dict[str, Any], state: State) -> None:
        name = self._name(state.entity_id)
        where = self._where(state)
        try:
            got = await self._words()
            t_title = got.get('chatter_title') or '{title}'
            own = self._engine_says(rule['id'])
            t_msg = got.get('chatter_ha' if own else 'chatter') or '{name}: {count}/{minutes}'
            title = t_title.format(title=rule['title']) + (f' — {where}' if where else '')
            message = t_msg.format(name=name, count=CHATTER_COUNT, minutes=max(1, CHATTER_WINDOW // 60))
            await self._send(rule, title, message, tag=self._tag(f"{rule['id']}|{state.entity_id}") + '_chatter', critical=False, force=own)
            self._note(message, title=title, notification_id=self._tag(f"{rule['id']}|{state.entity_id}") + '_chatter', rule=None if own else rule)
        finally:
            self._calm_later(f"{rule['id']}|{state.entity_id}")

    @callback
    def _clear(self, rule: dict[str, Any], state: State) -> None:
        key = f"{rule['id']}|{state.entity_id}"
        inc = self.active.pop(key, None)
        if not inc:
            return
        if inc.get('repeat'):
            inc['repeat']()
        if rule['priority'] == 'critical' and (not inc.get('acked_by')) and (not inc.get('nodata')):
            self.unacked_rtn[key] = {'since': inc['since'], 'cleared': dt_util.utcnow().isoformat(), 'title': rule['title']}
            self._changed()
        starting = self._pending_start.get(rule['id']) or []
        if key in self._chatter:
            self._calm_later(key)
        if inc.get('quiet'):
            pass
        elif key in starting:
            starting.remove(key)
        else:
            self._pending_clear.setdefault(rule['id'], []).append((key, inc, state))
            self._later()
        self._save()

    @callback
    def _later(self) -> None:
        if self._flush_unsub:
            return

        @callback
        def _go(_now) -> None:
            self._flush_unsub = None
            self.hass.async_create_task(self._flush())
        self._flush_unsub = async_call_later(self.hass, GROUP_WINDOW, _go)

    async def _flush(self) -> None:
        starts, clears = (self._pending_start, self._pending_clear)
        self._pending_start, self._pending_clear = ({}, {})
        rules = {r['id']: r for r in self.config['rules']}
        await self._flush_nodata(rules)
        for rule_id, keys in starts.items():
            rule = rules.get(rule_id)
            keys = [k for k in keys if k in self.active]
            if not rule or not keys:
                continue
            if rule.get('outputs') and (not self._in_ha(rule_id) or rule_id in self._native_first):
                self.hass.async_create_task(self._switch(rule_id, [o['entity_id'] for o in rule['outputs'] if o['on_start']], 'on_start', 'alarm'))
            if len(keys) >= GROUP_MIN or (rule_id in self.groups and (not self.groups[rule_id]['acked_by'])):
                for k, inc in self.active.items():
                    if k.startswith(rule_id + '|') and (not inc.get('acked_by')) and (not inc.get('group')) and (k not in keys):
                        keys.append(k)
                        if inc.get('repeat'):
                            inc['repeat']()
                            inc['repeat'] = None
                        if inc.get('own'):
                            await self._send(rule, '', 'clear_notification', tag=self._tag(k), critical=False)
                            persistent_notification.async_dismiss(self.hass, self._tag(k))
                            inc['own'] = False
                for k in keys:
                    self.active[k]['group'] = True
                group = self.groups.get(rule_id)
                if not group or group['acked_by']:
                    self._end_group(rule_id)
                    self.groups[rule_id] = {'since': dt_util.utcnow().isoformat(), 'acked_by': None, 'count': 0, 'repeat': None}
                await self._send_group(rule)
            else:
                for k in keys:
                    if (state := self.hass.states.get(k.partition('|')[2])):
                        await self._send_alarm(rule, state, k)
        self._native_first.difference_update(starts)
        for rule_id, items in clears.items():
            rule = rules.get(rule_id)
            if not rule:
                continue
            if not self._in_ha(rule_id) and (not any((k.startswith(rule_id + '|') and (not inc.get('nodata')) for k, inc in self.active.items()))):
                if rule.get('outputs'):
                    await self._switch(rule_id, [o['entity_id'] for o in rule['outputs'] if o['on_end']], 'on_end', 'back to normal')
                await self._set_helper(rule_id, False, 'open')
            grouped = [(k, s) for k, inc, s in items if inc.get('group')]
            single = [(k, s) for k, inc, s in items if not inc.get('group')]
            if grouped:
                left = [k for k, inc in self.active.items() if k.startswith(rule_id + '|') and inc.get('group')]
                if not left:
                    self._end_group(rule_id)
                    title = f"✅ {rule['title']}" + (f' — {len(grouped)} {_devices(len(grouped))}' if len(grouped) > 1 else '')
                    await self._send(rule, title, f'Снова в норме: {self._where_list([k for k, _ in grouped])}.', tag=self._gtag(rule_id), critical=False)
                    persistent_notification.async_dismiss(self.hass, self._gtag(rule_id))
                else:
                    await self._send_group(rule, back=[k for k, _ in grouped])
            if len(single) >= GROUP_MIN:
                await self._send(rule, f"✅ {rule['title']} — {len(single)} {_devices(len(single))}", f'Снова в норме: {self._where_list([k for k, _ in single])}.', tag=self._gtag(rule_id) + '_ok', critical=False)
                for k, _ in single:
                    persistent_notification.async_dismiss(self.hass, self._tag(k))
            else:
                for k, state in single:
                    where = self._where(state)
                    await self._send(rule, f"✅ {rule['title']}" + (f' — {where}' if where else ''), f'{self._name(state.entity_id)}: снова в норме.', tag=self._tag(k), critical=False)
                    persistent_notification.async_dismiss(self.hass, self._tag(k))
        self._save()
        self._changed()

    def _hhmm(self, iso: str | None) -> str:
        at = dt_util.parse_datetime(iso or '') or dt_util.utcnow()
        if at.tzinfo is None:
            at = at.replace(tzinfo=dt_util.UTC)
        tz = dt_util.get_time_zone(str(self.hass.config.time_zone or '')) or dt_util.get_default_time_zone()
        return at.astimezone(tz).strftime('%H:%M')

    async def _send_alarm(self, rule: dict[str, Any], state: State, key: str) -> None:
        inc = self.active.get(key)
        if not inc or inc.get('acked_by'):
            return
        inc['count'] += 1
        inc['last'] = dt_util.utcnow().isoformat()
        inc['own'] = True
        where = self._where(state)
        name = label(self.hass, state.entity_id, state) + level_text(rule, state)
        title = f"🚨 {rule['title']}" + (f' — {where}' if where else '')
        at = self._hhmm(inc['since'])
        message = f'{name}: авария в {at}.' if inc['count'] == 1 else f"{name}: авария с {at}, никто не подтвердил. Повтор {inc['count']}."
        actions = [{'action': f'{ACK}|{key}', 'title': 'Принято'}]
        if rule.get('action'):
            actions.insert(0, {'action': f'{RUN}|{key}', 'title': _button(rule['action'], self._name(rule['action']['entity_id'])), 'destructive': True, 'authenticationRequired': True})
        first = rule['id'] in self._native_first
        if await self._send(rule, title, message, tag=self.native[rule['id']]['automation'] if first else self._tag(key), critical=rule['priority'] == 'critical', actions=actions, force=first):
            inc['pushed'] = True
        self._note(f'{message}', title=title, notification_id=self._tag(key), rule=None if first else rule)
        self._schedule(rule, key)
        self._save()

    def _left(self, rule: dict[str, Any], last: str | None, count: int=0) -> timedelta:
        full = timedelta(minutes=rule['repeat_min'] if count <= REPEAT_FAST else max(rule['repeat_min'], REPEAT_SLOW))
        sent = dt_util.parse_datetime(last) if last else None
        return max(timedelta(seconds=5), full - (dt_util.utcnow() - sent)) if sent else full

    @callback
    def _schedule(self, rule: dict[str, Any], key: str) -> None:
        inc = self.active.get(key)
        if not rule['repeat_min'] or not inc or self._in_ha(rule['id']):
            return
        rule_id, entity_id = (rule['id'], key.partition('|')[2])

        @callback
        def _again(_now) -> None:
            self.hass.async_create_task(self._repeat(rule_id, entity_id))
        if inc.get('repeat'):
            inc['repeat']()
        inc['repeat'] = async_call_later(self.hass, self._left(rule, inc.get('last'), int(inc.get('count') or 0)), _again)

    async def _repeat(self, rule_id: str, entity_id: str) -> None:
        rule = next((r for r in self.config['rules'] if r['id'] == rule_id), None)
        state = self.hass.states.get(entity_id)
        key = f'{rule_id}|{entity_id}'
        if rule and state and (key in self.active) and in_alarm(rule, state, open_now=True):
            await self._send_alarm(rule, state, key)

    async def _send_group(self, rule: dict[str, Any], *, repeat: bool=False, back: list[str] | None=None) -> None:
        rule_id = rule['id']
        group = self.groups.get(rule_id)
        members = [k for k, inc in self.active.items() if k.startswith(rule_id + '|') and inc.get('group') and (not inc.get('acked_by'))]
        if not group or group['acked_by'] or (not members):
            return
        if not back:
            group['count'] += 1
            group['last'] = dt_util.utcnow().isoformat()
        starts = sorted((self.active[k]['since'] for k in members))
        first = min(starts[0], group['since'])
        at = self._hhmm(first)
        latest = self._hhmm(starts[-1])
        if latest != at:
            at = f'{at} (последняя в {latest})'
        n = len(members)
        title = f"🚨 {rule['title']} — {n} {_devices(n)}"
        lines = [f'С {at}: {self._where_list(members, rule)}.']
        if back:
            lines.append('Снова в норме: ' + self._where_list(back) + '.')
        if repeat:
            lines.append(f"Никто не подтвердил. Повтор {group['count']}.")
        gkey = f'{rule_id}|*'
        actions = [{'action': f'{ACK}|{gkey}', 'title': 'Принято'}]
        if rule.get('action'):
            actions.insert(0, {'action': f'{RUN}|{gkey}', 'title': _button(rule['action'], self._name(rule['action']['entity_id'])), 'destructive': True, 'authenticationRequired': True})
        first = rule_id in self._native_first and (not back) and (not repeat)
        if await self._send(rule, title, ' '.join(lines), tag=self.native[rule_id]['automation'] if first else self._gtag(rule_id), critical=rule['priority'] == 'critical' and (not back), actions=actions, quiet=bool(back), force=first):
            for k in members:
                self.active[k]['pushed'] = True
        self._note(' '.join(lines), title=title, notification_id=self._gtag(rule_id), rule=None if first else rule)
        if not back:
            if group.get('repeat'):
                group['repeat']()
                group['repeat'] = None
            self._schedule_group(rule)

    @callback
    def _schedule_group(self, rule: dict[str, Any]) -> None:
        group = self.groups.get(rule['id'])
        if not rule['repeat_min'] or not group or self._in_ha(rule['id']):
            return
        rule_id = rule['id']

        @callback
        def _again(_now) -> None:
            group['repeat'] = None
            if (r := next((x for x in self.config['rules'] if x['id'] == rule_id), None)) and self.groups.get(rule_id) is group:
                self.hass.async_create_task(self._send_group(r, repeat=True))
        if group.get('repeat'):
            group['repeat']()
        group['repeat'] = async_call_later(self.hass, self._left(rule, group.get('last'), int(group.get('count') or 0)), _again)

    @callback
    def _end_group(self, rule_id: str) -> None:
        group = self.groups.pop(rule_id, None)
        if group and group.get('repeat'):
            group['repeat']()
        if group:
            persistent_notification.async_dismiss(self.hass, self._gtag(rule_id))

    @callback
    def _end_quietly(self, key: str) -> None:
        inc = self.active.pop(key, None)
        if inc and inc.get('repeat'):
            inc['repeat']()
        if inc:
            persistent_notification.async_dismiss(self.hass, self._tag(key))

    def watched(self) -> set[str]:
        return {e for r in self.config['rules'] if r['enabled'] for e in (*r['sources'], *(r.get('picked') or [])) if not muted(r, e)}

    async def _switch(self, rule_id: str, eids: list[str], when: str, why: str) -> None:
        rule = next((r for r in self.config['rules'] if r['id'] == rule_id), None)
        saved = {o['entity_id'] for o in (rule or {}).get('outputs') or []}
        sounding = self._sounding.setdefault(rule_id, set())
        for eid in eids:
            domain = eid.partition('.')[0]
            service = OUTPUTS.get(domain, {}).get(when)
            if service is None or not (eid in saved or (when == 'on_end' and eid in sounding)):
                _LOGGER.warning('Hauflo: alarm rule %s: output %s refused (%s)', rule_id, eid, when)
                continue
            try:
                await self.hass.services.async_call(domain, service, {'entity_id': eid}, blocking=True)
                _LOGGER.info('Hauflo: alarm rule %s: %s.%s %s (%s)', rule_id, domain, service, eid, why)
                if when == 'on_start':
                    sounding.add(eid)
                else:
                    sounding.discard(eid)
            except Exception as err:
                _LOGGER.warning('Hauflo: alarm rule %s: %s.%s %s failed: %s', rule_id, domain, service, eid, err)
        if not sounding:
            self._sounding.pop(rule_id, None)

    @staticmethod
    def _tag(key: str) -> str:
        return 'hauflo_' + slugify(key)

    @staticmethod
    def _gtag(rule_id: str) -> str:
        return 'hauflo_' + slugify(rule_id) + '_all'

    @callback
    def _watch_helpers(self) -> None:
        if self._unsub_helper:
            self._unsub_helper()
            self._unsub_helper = None
        if (helpers := sorted({str(rec['helper']) for rec in self.native.values()})):
            self._unsub_helper = async_track_state_change_event(self.hass, helpers, self._on_helper)

    @callback
    def _on_helper(self, event: Event) -> None:
        new: State | None = event.data.get('new_state')
        old: State | None = event.data.get('old_state')
        if new is None or old is None or new.state != 'on' or (old.state != 'off') or (new.context.parent_id == QUIET_PARENT):
            return
        if (rule_id := next((r for r, rec in self.native.items() if rec['helper'] == new.entity_id), None)):
            self.hass.async_create_task(self._native_accepted(rule_id, new.context))

    async def _native_accepted(self, rule_id: str, context: Context) -> None:
        who = self._native_who.pop(rule_id, None)
        if who is None:
            who = await self._who(Event('state_changed', context=context))
        changed = False
        for key, inc in self.active.items():
            if key.startswith(rule_id + '|') and (not inc.get('acked_by')) and (not inc.get('nodata')):
                inc['acked_by'] = who
                changed = True
        if (group := self.groups.get(rule_id)) and (not group['acked_by']):
            group['acked_by'] = who
            changed = True
        if changed:
            self._save()
            self._changed()

    async def _set_helper(self, rule_id: str, on: bool, which: str='helper') -> None:
        rec = self.native.get(rule_id)
        eid = (rec or {}).get(which)
        if not eid or (state := self.hass.states.get(eid)) is None or state.state == ('on' if on else 'off'):
            return
        try:
            await self.hass.services.async_call('input_boolean', f"turn_{('on' if on else 'off')}", {'entity_id': eid}, blocking=True, context=Context(parent_id=QUIET_PARENT))
        except Exception as err:
            _LOGGER.warning('Hauflo: helper %s not switched: %s', eid, err)

    async def async_set_native(self, native: dict[str, dict[str, Any]]) -> None:
        before, self.native = (self.native, {str(k): dict(v) for k, v in native.items()})
        await self.native_store.async_save(self.native)
        self._watch_helpers()
        rules = {r['id']: r for r in self.config['rules']}
        for rule_id in self.native:
            if (rule_id not in before or before[rule_id].get('digest') != self.native[rule_id].get('digest')) and any((k.startswith(rule_id + '|') for k in self._holding)):
                self._native_first.add(rule_id)
            if rule_id in before:
                if 'open' in self.native[rule_id] and 'open' not in before[rule_id]:
                    await self._set_helper(rule_id, any((k.startswith(rule_id + '|') and (not inc.get('nodata')) for k, inc in self.active.items())), 'open')
                continue
            if rule_id in self._pending_start:
                self._native_first.add(rule_id)
            await self._handed(rule_id)
        for rule_id in before:
            if rule_id in self.native or (rule := rules.get(rule_id)) is None:
                continue
            self._taken_back(rule)
        self._save()
        self._changed()

    def _automation(self, rec: dict[str, Any]) -> State | None:
        eid = er.async_get(self.hass).async_get_entity_id('automation', 'automation', rec['automation'])
        return self.hass.states.get(eid) if eid else None

    def _ha_state(self, rec: dict[str, Any]) -> str:
        st = self._automation(rec)
        if st is None:
            return 'missing'
        return 'ha' if st.state == 'on' else 'unavailable' if st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN) else 'off'

    def _engine_says(self, rule_id: str | None) -> bool:
        rec = self.native.get(str(rule_id)) if rule_id is not None else None
        return rec is not None and 'chatter' not in rec and self._in_ha(rule_id)

    def _ha_misses(self, rule: dict[str, Any], old: State, new: State) -> bool:
        rec = self.native.get(rule['id']) or {}
        opn = self.hass.states.get(rec.get('open') or '')
        return self._engine_says(rule['id']) and rule.get('kind') not in LINK_KINDS and (opn is not None) and (opn.state == 'on') and ((new.last_changed - old.last_changed).total_seconds() < native_back(rule))

    def _in_ha(self, rule_id: str | None) -> bool:
        rec = self.native.get(str(rule_id)) if rule_id is not None else None
        if rec is None:
            return False
        return not self.hass.is_running or self._ha_state(rec) == 'ha'

    @callback
    def _on_automation(self, event: Event) -> None:
        new: State | None = event.data.get('new_state')
        old: State | None = event.data.get('old_state')
        st = new or old
        aid = st.attributes.get('id') if st is not None else None
        rule_id = next((r for r, rec in self.native.items() if rec.get('automation') == aid), None) if aid else None
        was, now = (old is not None and old.state == 'on', new is not None and new.state == 'on')
        if rule_id is None or was == now:
            return
        if now:
            self.hass.async_create_task(self._handed(rule_id))
        elif (rule := next((r for r in self.config['rules'] if r['id'] == rule_id), None)):
            _LOGGER.warning("Hauflo: alarm automation %s doesn't run in HA (%s) — the agent tells its alarms", aid, self._ha_state(self.native[rule_id]))
            self._taken_back(rule)
        self._changed()

    async def _handed(self, rule_id: str) -> None:
        keys = [k for k, inc in self.active.items() if k.startswith(rule_id + '|') and (not inc.get('nodata'))]
        for k in keys:
            if self.active[k].get('repeat'):
                self.active[k]['repeat']()
                self.active[k]['repeat'] = None
        if (group := self.groups.get(rule_id)) and group.get('repeat'):
            group['repeat']()
            group['repeat'] = None
        await self._set_helper(rule_id, bool(keys) and all((self.active[k].get('acked_by') for k in keys)))
        if keys:
            await self._set_helper(rule_id, True, 'open')
        else:
            self._watch_missed(rule_id)
        if keys:

            async def _go(_hass: HomeAssistant) -> None:
                if (self.native.get(rule_id) or {}).get('form') == 2:
                    from .independent import alarm_hold
                    rule = next((r for r in self.config['rules'] if r['id'] == rule_id), None)
                    later = (alarm_hold(rule) if rule else NATIVE_HOLD) + NATIVE_HOLD

                    @callback
                    def _late(_now: Any) -> None:
                        self._late_kicks.pop(rule_id, None)
                        self.hass.async_create_task(self._kick(rule_id))
                    if (cancel := self._late_kicks.pop(rule_id, None)):
                        cancel()
                    self._late_kicks[rule_id] = async_call_later(self.hass, later, _late)
                    return
                await self._kick(rule_id)
            async_at_started(self.hass, _go)

    async def _kick(self, rule_id: str) -> None:
        rec = self.native.get(rule_id) or {}
        st = self._automation(rec) if rec.get('automation') else None
        if rec.get('form') == 2:
            grp, ack = (self.hass.states.get(rec.get('group') or ''), self.hass.states.get(rec.get('helper') or ''))
            if st is None or st.state != 'on' or st.attributes.get('current') or (grp is None) or (grp.state != 'on') or (ack is not None and ack.state == 'on'):
                return
            try:
                await self.hass.services.async_call('automation', 'trigger', {'entity_id': st.entity_id, 'skip_condition': True}, blocking=False, context=Context(parent_id=QUIET_PARENT))
            except Exception as err:
                _LOGGER.warning('Hauflo: alarm automation %s not started: %s', st.entity_id, err)
            return
        opn = self.hass.states.get(rec.get('open') or '')
        if st is None or st.state != 'on' or st.attributes.get('current') or (opn is None) or (opn.state != 'on'):
            return
        ent = getattr(self.hass.data.get('automation'), 'get_entity', lambda _e: None)(st.entity_id)
        variables = dict((getattr(ent, 'raw_config', None) or {}).get('trigger_variables') or {})
        try:
            await self.hass.services.async_call('automation', 'trigger', {'entity_id': st.entity_id, 'skip_condition': True, 'variables': variables}, blocking=False, context=Context(parent_id=QUIET_PARENT))
        except Exception as err:
            _LOGGER.warning('Hauflo: alarm automation %s not started: %s', st.entity_id, err)

    @callback
    def _watch_missed(self, rule_id: str, delay: float | None=None) -> None:
        if (cancel := self._missed.pop(rule_id, None)) is not None:
            cancel()
        if (rule := next((r for r in self.config['rules'] if r['id'] == rule_id), None)) is None:
            return

        @callback
        def _go(_now: Any) -> None:
            self._missed.pop(rule_id, None)
            self.hass.async_create_task(self._missed_back(rule_id))
        self._missed[rule_id] = async_call_later(self.hass, native_back(rule) + GROUP_WINDOW if delay is None else delay, _go)

    async def _missed_back(self, rule_id: str) -> None:
        rule = next((r for r in self.config['rules'] if r['id'] == rule_id and r['enabled']), None)
        rec = self.native.get(rule_id) or {}
        flat = rec.get('form') == 2
        if rule is None or not self._in_ha(rule_id):
            return
        if flat:
            if not self._missed_src.get(rule_id):
                return
        else:
            opn = self.hass.states.get(rec.get('open') or '')
            if opn is None or opn.state != 'on':
                return
        states = [st for e in rule['sources'] if (st := self.hass.states.get(e)) is not None]
        if any((in_alarm(rule, st, open_now=True) for st in states)):
            if flat:
                self._missed_src.pop(rule_id, None)
            return
        told = [e for e in self._missed_src.get(rule_id) or [] if e in rule['sources']] or rule['sources']
        normal = [st for st in states if st.entity_id in told and st.state not in NOT_A_STATE]
        if len(normal) < len(told):
            self._watch_missed(rule_id)
            return
        wait = native_back(rule) + GROUP_WINDOW - min(((dt_util.utcnow() - st.last_changed).total_seconds() for st in normal))
        if wait > 0:
            self._watch_missed(rule_id, wait)
            return
        self._missed_src.pop(rule_id, None)
        if flat:
            back = self._automation({'automation': rec['back']}) if rec.get('back') else None
            if back is not None and back.state == 'on':
                try:
                    await self.hass.services.async_call('automation', 'trigger', {'entity_id': back.entity_id, 'skip_condition': False}, blocking=True, context=Context(parent_id=QUIET_PARENT))
                except Exception as err:
                    _LOGGER.warning('Hauflo: automation %s not started: %s', back.entity_id, err)
                else:
                    return
            persistent_notification.async_dismiss(self.hass, rec['automation'])
            if rule.get('outputs'):
                await self._switch(rule_id, [o['entity_id'] for o in rule['outputs'] if o['on_end']], 'on_end', 'back to normal')
            if rec.get('back'):
                t = await self._words()
                await self._send(rule, t.get('back_title', '✅ {title}').format(title=rule['title']), t.get('back_all', 'OK'), tag=rec['automation'], critical=False, force=True)
            return
        await self._set_helper(rule_id, False, 'open')
        await self._set_helper(rule_id, False)
        persistent_notification.async_dismiss(self.hass, rec['automation'])
        if rule.get('outputs'):
            await self._switch(rule_id, [o['entity_id'] for o in rule['outputs'] if o['on_end']], 'on_end', 'back to normal')
        t = await self._words()
        names = ', '.join((self._name(st.entity_id) + (f' · {where}' if (where := self._where(st)) else '') for st in normal[:LIST_MAX]))
        await self._send(rule, t.get('back_title', '✅ {title}').format(title=rule['title']), t.get('back', '{name}').format(name=names), tag=rec['automation'], critical=False, force=True)

    @callback
    def _taken_back(self, rule: dict[str, Any]) -> None:
        for k, inc in self.active.items():
            if k.startswith(rule['id'] + '|') and (not inc.get('acked_by')) and (not inc.get('nodata')) and (not inc.get('group')) and (not inc.get('repeat')):
                self._schedule(rule, k)
        if (group := self.groups.get(rule['id'])) and (not group['acked_by']) and (not group.get('repeat')):
            self._schedule_group(rule)

    def native_status(self) -> dict[str, Any]:
        reg = er.async_get(self.hass)
        out: dict[str, Any] = {}
        for rule_id, rec in self.native.items():
            eid = reg.async_get_entity_id('automation', 'automation', rec['automation'])
            st = self.hass.states.get(eid) if eid else None
            hs = self.hass.states.get(rec['helper'])
            last = st.attributes.get('last_triggered') if st else None
            out[rule_id] = {'automation': eid, 'id': rec['automation'], 'helper': rec['helper'], 'state': self._ha_state(rec), 'on': st.state == 'on' if st else None, 'acked': hs.state == 'on' if hs else None, 'last_triggered': last.isoformat() if hasattr(last, 'isoformat') else last, 'form': rec.get('form', 1)}
            if rec.get('form', 1) == 1:
                ent = getattr(self.hass.data.get('automation'), 'get_entity', lambda _e: None)(eid) if eid else None
                raw = getattr(ent, 'raw_config', None)
                if isinstance(raw, dict):
                    from .automations import _digest
                    out[rule_id]['edited'] = _digest(raw) != rec.get('digest')
        return out

    async def _on_action(self, event: Event) -> None:
        action = str(event.data.get('action') or '')
        if action.startswith((ACK_ACTION, RUN_ACTION, OK_ACTION, DO_ACTION)):
            if (rule_id := next((r for r, rec in self.native.items() if action in (rec.get('ack'), rec.get('run'))), None)):
                self._native_who[rule_id] = await self._who(event)
            return
        if not action.startswith((ACK + '|', RUN + '|')):
            return
        kind, key = action.split('|', 1)
        await self._accept(kind, key, await self._who(event), event.context)

    async def async_ack(self, key: str, who: str) -> bool:
        if key in self.unacked_rtn and key not in self.active:
            self.unacked_rtn.pop(key, None)
            self._save()
            self._changed()
            return True
        if key not in self.active and (not (key.endswith('|*') and key.partition('|')[0] in self.groups)):
            return False
        await self._accept(ACK, key, who, None)
        return True

    @callback
    def ack_house(self, entity_id: str, who: str) -> bool:
        if self.house_acks.get(entity_id):
            return False
        self.house_acks[entity_id] = who[:80]
        self._save()
        self._changed()
        return True

    @callback
    def house_acks_keep(self, problems: set[str]) -> None:
        gone = [e for e in self.house_acks if e not in problems]
        for e in gone:
            del self.house_acks[e]
        if gone:
            self._save()

    async def _accept(self, kind: str, key: str, who: str, context: Any) -> None:
        rule_id, _, entity_id = key.partition('|')
        rule = next((r for r in self.config['rules'] if r['id'] == rule_id), None)
        open_now = key in self.active or (entity_id == '*' and rule_id in self.groups)
        if not open_now:
            return
        if kind == RUN and rule and rule.get('action') and open_now and (rule['action']['service'] in RUN_SERVICES):
            domain, _, service = rule['action']['service'].partition('.')
            try:
                await self.hass.services.async_call(domain, service, {'entity_id': rule['action']['entity_id']}, blocking=True, context=context)
                done = f"{_button(rule['action'], self._name(rule['action']['entity_id']))}: выполнено ({who})."
            except Exception as err:
                done = f"{_button(rule['action'], self._name(rule['action']['entity_id']))}: не получилось — {err}."
        else:
            done = f'Принято: {who}.'
        if entity_id == '*':
            incs = [inc for k, inc in self.active.items() if k.startswith(rule_id + '|') and inc.get('group')]
            if (group := self.groups.get(rule_id)):
                group['acked_by'] = who
                if group.get('repeat'):
                    group['repeat']()
                    group['repeat'] = None
            name, tag = (f'{len(incs)} {_devices(len(incs))}' if incs else rule['title'] if rule else '', self._gtag(rule_id))
        else:
            incs = [inc] if (inc := self.active.get(key)) else []
            state = self.hass.states.get(entity_id)
            where = self._where(state) if state else ''
            name, tag = (self._name(entity_id) + (f' ({where})' if where else ''), self._tag(key))
        for inc in incs:
            inc['acked_by'] = who
            if inc.get('repeat'):
                inc['repeat']()
                inc['repeat'] = None
        if rule and rule.get('outputs'):
            await self._switch(rule_id, [o['entity_id'] for o in rule['outputs'] if o['on_end']], 'on_end', f'accepted: {who}')
        self._save()
        self._changed()
        native = self.native.get(rule_id)
        if native:
            if not any((not i.get('acked_by') and (not i.get('nodata')) for k, i in self.active.items() if k.startswith(rule_id + '|'))):
                await self._set_helper(rule_id, True)
            if self._in_ha(rule_id):
                tag = native['automation']
        if rule:
            await self._send(rule, f"{rule['title']}", f'{name}: {done}', tag=tag, critical=False, force=bool(native))

    async def _who(self, event: Event) -> str:
        user_id = event.context.user_id if event.context else None
        if user_id and (user := (await self.hass.auth.async_get_user(user_id))):
            return user.name or 'кто-то'
        return 'кто-то'

    def phones(self) -> list[dict[str, Any]]:
        ents = er.async_get(self.hass)
        devs = dr.async_get(self.hass)
        owners: dict[str, str] = {}
        for state in self.hass.states.async_all('person'):
            for tracker in state.attributes.get('device_trackers') or []:
                owners[tracker] = state.entity_id
        out = []
        for entry in ents.entities.values():
            if entry.platform != 'mobile_app' or entry.domain != 'device_tracker' or (not entry.device_id):
                continue
            device = devs.async_get(entry.device_id)
            if not device:
                continue
            service = slugify(f"mobile_app_{device.name or ''}")
            if not self.hass.services.has_service('notify', service):
                continue
            os = 'ios' if (device.manufacturer or '').lower() == 'apple' else 'android'
            out.append({'service': service, 'name': device.name_by_user or device.name, 'os': os, 'person': owners.get(entry.entity_id)})
        return out

    def people(self) -> list[dict[str, Any]]:
        phones = self.phones()
        return [{'id': s.entity_id, 'name': s.attributes.get('friendly_name') or s.entity_id, 'phones': [p for p in phones if p['person'] == s.entity_id]} for s in self.hass.states.async_all('person')]

    def scope(self, phone: dict[str, Any], config: dict[str, Any] | None=None) -> str:
        aud = (config or self.config).get('audience') or {}
        return aud.get(phone['service']) or (aud.get(phone['person']) if phone.get('person') else None) or aud.get('*') or 'all'

    def _targets(self, rule: dict[str, Any], config: dict[str, Any] | None=None) -> list[dict[str, Any]]:
        phones = self.phones()
        rec = rule['recipients']
        if rec['all']:
            critical = rule.get('priority') == 'critical'
            return [p for p in phones if (s := self.scope(p, config)) == 'all' or (s == 'critical' and critical)]
        people = set(rec['people'])
        for g in (config or self.config)['groups']:
            if g['id'] in (rec.get('groups') or []):
                people.update(g['people'])
        chosen = set(rec.get('phones') or [])
        return [p for p in phones if p['person'] in people or p['service'] in chosen]

    @staticmethod
    def _recent(when: dict[str, str], seconds: int) -> dict[str, str]:
        now = dt_util.utcnow()
        return {k: v for k, v in when.items() if (t := dt_util.parse_datetime(v)) is not None and (now - t).total_seconds() < seconds}

    @staticmethod
    def _lane_key(service: str, lane: str) -> str:
        return service if lane == 'alarm' else f'{service}|{lane}'

    def _room(self, service: str, critical: bool, lane: str='alarm') -> bool:
        now = time.time()
        key = self._lane_key(service, lane)
        log = [t for t in self._pushes.get(key, []) if 0 <= now - t < 86400]
        self._pushes[key] = log
        if critical:
            return len(log) < PUSH_DAY_CRITICAL
        day, hour = (PUSH_EVENT_DAY_MAX, PUSH_EVENT_HOUR_MAX) if lane == 'event' else (PUSH_DAY_MAX, PUSH_HOUR_MAX)
        return len(log) < day and sum((1 for t in log if now - t < 3600)) < hour

    def _english(self, config: dict[str, Any] | None=None) -> bool:
        return not site_ru(getattr(self.hass.config, 'language', None), config or self.config)

    def _say(self, text: str) -> str:
        return _en(text) if text and self._english() else text

    def _note(self, message: str, *, title: str, notification_id: str, rule: dict[str, Any] | None=None) -> None:
        if rule is not None and self._in_ha(rule.get('id')):
            return
        persistent_notification.async_create(self.hass, self._say(message), title=self._say(title), notification_id=notification_id)

    async def _send(self, rule: dict[str, Any], title: str, message: str, *, tag: str, critical: bool, actions: list | None=None, targets: list | None=None, quiet: bool=False, results: dict[str, str] | None=None, force: bool=False, silent: bool=False, extra: Callable[[str | None], dict[str, Any]] | None=None, lane: str='alarm') -> list[str]:
        testing = results is not None
        if not testing and (not force) and self._in_ha(rule.get('id')):
            return [p['service'] for p in (targets if targets is not None else self._targets(rule))]
        if message != 'clear_notification':
            title, message = (self._say(title), self._say(message))
        if actions:
            actions = [{**a, 'title': self._say(a['title'])} for a in actions]
        sent: list[str] = []
        failed: list[str] = []
        limited: list[str] = []
        held: list[str] = []

        async def one(phone: dict[str, Any]) -> None:
            if not testing and (not self._room(phone['service'], critical, lane)):
                held.append(phone['service'])
                self.throttled[phone['service']] = dt_util.utcnow().isoformat()
                return
            self._pushes.setdefault(self._lane_key(phone['service'], lane), []).append(time.time())
            self._save()
            data = push_data(phone['os'], tag=tag, critical=critical, quiet=quiet, actions=actions, silent=silent)
            for k, v in (extra(phone['os']) if extra is not None else {}).items():
                data[k] = {**data[k], **v} if isinstance(v, dict) and isinstance(data.get(k), dict) else v
            _PHONE.set(phone['service'])
            try:
                payload = {'message': message, 'data': data, **({'title': title} if title else {})}
                await self.hass.services.async_call('notify', phone['service'], payload, blocking=True)
                if phone['service'] in errors.limited:
                    limited.append(phone['service'])
                    self.limited[phone['service']] = dt_util.utcnow().isoformat()
                    _LOGGER.warning("Hauflo alarm to %s: the HA app's push limit for this phone is used up for today", phone['service'])
                    return
                if phone['service'] in errors.failed:
                    raise RuntimeError("the HA app's push service said it failed (see HA log)")
                sent.append(phone['service'])
                self.limited.pop(phone['service'], None)
            except Exception as err:
                failed.append(phone['service'])
                _LOGGER.warning('Hauflo alarm to %s failed: %s', phone['service'], err)
        errors = _PushErrors()
        app_log = logging.getLogger('homeassistant.components.mobile_app')
        app_log.addHandler(errors)
        try:
            await asyncio.gather(*(asyncio.ensure_future(one(p)) for p in (targets if targets is not None else self._targets(rule))))
        finally:
            app_log.removeHandler(errors)
        self.sent_log = ([{'at': dt_util.utcnow().isoformat(), 'title': title, 'sent': sent, 'failed': failed, **({'limited': limited} if limited else {}), **({'held': held} if held else {})}] + self.sent_log)[:30]
        if results is not None:
            results.update({s: 'sent' for s in sent} | {s: 'failed' for s in failed} | {s: 'rate_limited' for s in limited})
        return sent

    async def async_tell(self, title: str, message: str, user_id: str | None=None, *, actions: list | None=None, tag: str | None=None) -> list[str]:
        phones = self.phones()
        if user_id:
            people = {s.entity_id for s in self.hass.states.async_all('person') if s.attributes.get('user_id') == user_id}
            targets = [p for p in phones if p['person'] in people]
        else:
            targets = [p for p in phones if self.scope(p) == 'all']
            self._note(message, title=title, notification_id=tag or f'hauflo_rule_{slugify(title)[:40]}')
        return await self._send({}, title, message, tag=tag or f'hauflo-rule-{slugify(title)[:40]}', critical=False, targets=targets, actions=actions)

    def test_wait(self, body: dict[str, Any]) -> int:
        now = time.monotonic()
        self._tests = [t for t in self._tests if now - t < 60]
        if len(self._tests) >= TEST_PER_MIN:
            return int(60 - (now - self._tests[0])) + 1
        waits = [TEST_GAP - (now - self._tested[p['service']]) for p in self._test_targets(body) if p['service'] in self._tested]
        return max([0, *[int(w) + 1 for w in waits if w > 0]])

    def _test_targets(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        rec = {'all': False, 'people': [str(p) for p in body.get('people') or []], 'groups': [str(g) for g in body.get('groups') or []], 'phones': [str(p) for p in body.get('phones') or []]}
        return self.phones() if body.get('all') else self._targets({'recipients': rec})

    async def async_test(self, body: dict[str, Any]) -> dict[str, Any]:
        rule = {'recipients': {'all': False, 'people': [], 'groups': []}}
        critical = body.get('priority') == 'critical'
        targets = self._test_targets(body)
        now = time.monotonic()
        self._tests.append(now)
        for p in targets:
            self._tested[p['service']] = now
        results: dict[str, str] = {}
        sent = await self._send(rule, 'Hauflo: проверка уведомлений', 'Так будут приходить аварии.' + (' Критичные — со звуком даже в беззвучном режиме.' if critical else ''), tag='hauflo_test', critical=critical, targets=targets, results=results)
        return {'targets': len(targets), 'sent': sent, 'results': [{'service': p['service'], 'name': p['name'], 'os': p.get('os'), 'status': results.get(p['service'], 'failed')} for p in targets]}

    def active_list(self) -> list[dict[str, Any]]:
        out = []
        known = {r['id'] for r in self.config['rules']}
        pending = {k: v for k, v in self._restored.items() if k.partition('|')[0] in known}
        for key, inc in [*self.active.items(), *pending.items()]:
            rule_id, _, entity_id = key.partition('|')
            rule = next((r for r in self.config['rules'] if r['id'] == rule_id), None)
            state = self.hass.states.get(entity_id)
            out.append({'key': key, 'entityId': entity_id, 'title': rule['title'] if rule else 'Авария', 'name': label(self.hass, entity_id, state), 'since': inc['since'], 'ackedBy': inc.get('acked_by'), 'area': self._where(state) if state else '', 'kind': rule['kind'] if rule else 'custom', 'critical': bool(rule and rule['priority'] == 'critical') and (not inc.get('nodata')), **({'noData': True} if inc.get('nodata') else {})})
        return out

    def alarm_items(self) -> list[dict[str, Any]]:
        out = self.active_list()
        for key, v in self.unacked_rtn.items():
            if key in self.active:
                continue
            rule_id, _, entity_id = key.partition('|')
            state = self.hass.states.get(entity_id)
            rule = next((r for r in self.config['rules'] if r['id'] == rule_id), None)
            out.append({'key': key, 'entityId': entity_id, 'title': v.get('title') or (rule['title'] if rule else ''), 'name': label(self.hass, entity_id, state), 'since': v.get('since'), 'ackedBy': None, 'area': self._where(state) if state else '', 'critical': bool(rule and rule['priority'] == 'critical'), 'returned': True})
        return out

    def status(self) -> dict[str, Any]:
        ents = self.hass.states.async_all(['binary_sensor', 'sensor'])
        candidates = []
        own = {e.entity_id for e in er.async_get(self.hass).entities.values() if e.platform in ('hauflo', 'mobile_app')}
        for s in ents:
            if s.entity_id in own:
                continue
            dc = s.attributes.get('device_class')
            if dc is None and s.domain == 'binary_sensor' and HEARTBEAT.search(s.attributes.get('friendly_name') or s.entity_id):
                dc = 'connectivity'
            elif dc is None and s.domain == 'binary_sensor' and (FAULT_NAME.search(s.attributes.get('friendly_name') or '') or FAULT_NAME.search(s.entity_id) or labelled(self.hass, s.entity_id)):
                dc = 'problem'
            if dc in ALARM_CLASSES or (s.domain == 'sensor' and dc in LEVELS):
                if foreign.notify_flag(self.hass, s.entity_id) is False and (not (self.owns and self.owns(s.entity_id))):
                    continue
                entry = er.async_get(self.hass).async_get(s.entity_id)
                candidates.append({'entity_id': s.entity_id, 'name': label(self.hass, s.entity_id, s), 'device_class': dc, 'area': self._where(s), 'state': s.state, 'own': bool(self.owns and self.owns(s.entity_id)), 'monitored': labelled(self.hass, s.entity_id), 'platform': entry.platform if entry else '', **({'original': entry.original_name} if entry and entry.original_name and (entry.original_name != s.attributes.get('friendly_name')) else {}), **({'unit': str(s.attributes.get('unit_of_measurement') or '')} if s.domain == 'sensor' else {})})
        valves = [{'entity_id': s.entity_id, 'name': s.attributes.get('friendly_name') or s.entity_id, 'area': self._where(s)} for s in self.hass.states.async_all(['valve', 'switch']) if s.domain == 'valve' or (self.owns is not None and self.owns(s.entity_id))]
        reg = er.async_get(self.hass)
        outputs = [{'entity_id': s.entity_id, 'name': s.attributes.get('friendly_name') or s.entity_id, 'area': self._where(s), 'platform': e.platform if (e := reg.async_get(s.entity_id)) else '', 'own': bool(self.owns and self.owns(s.entity_id))} for s in self.hass.states.async_all(list(OUTPUTS)) if _output_ok(self.hass, s.entity_id)]
        return {'config': self.config, 'people': self.people(), 'outputs': sorted(outputs, key=lambda c: (natural_key(c['area']), natural_key(c['name']))), 'levels': LEVELS, 'phones': self.phones(), 'candidates': sorted(candidates, key=lambda c: (natural_key(c['area']), natural_key(c['name']))), 'actuators': sorted(valves, key=lambda c: (natural_key(c['area']), natural_key(c['name']))), 'active': [{'key': k, 'since': v['since'], 'acked_by': v['acked_by'], 'count': v['count']} for k, v in self.active.items()], 'log': self.sent_log[:10], 'native': self.native_status(), 'limits': {'rate_limited': self._recent(self.limited, 86400), 'held': self._recent(self.throttled, 3600)}}

class NotifyView(HomeAssistantView):
    url = '/api/hauflo/notify'
    name = 'api:hauflo:notify'
    requires_auth = True

    def __init__(self, engine_of) -> None:
        self.engine_of = engine_of

    async def get(self, request):
        engine = self.engine_of()
        if not engine:
            return self.json_message('Hauflo agent not set up', 503)
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        return self.json(engine.status())

    async def post(self, request):
        engine = self.engine_of()
        if not engine:
            return self.json_message('Hauflo agent not set up', 503)
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict):
            return self.json_message('an object expected', 400)
        from . import independent
        migrate = body.pop('migrate_alarms', None) is True
        try:
            if body.get('dry_run') is True:
                config = engine.check(body)
                return self.json({'config': config, 'native': await independent.async_sync(engine.hass, engine, dry_run=True, config=config, migrate=migrate)})
            config = await engine.async_set(body)
        except LockedChange as err:
            return self.json({'code': LOCKED_CODE, 'reason': str(err)}, 423)
        try:
            native = await independent.async_sync(engine.hass, engine, migrate=migrate)
        except Exception as err:
            _LOGGER.exception('Hauflo: alarm automations not synced')
            native = {'error': str(err)}
        return self.json({'config': config, 'native': native})

class NotifyTestView(HomeAssistantView):
    url = '/api/hauflo/notify/test'
    name = 'api:hauflo:notify:test'
    requires_auth = True

    def __init__(self, engine_of) -> None:
        self.engine_of = engine_of

    async def post(self, request):
        engine = self.engine_of()
        if not engine:
            return self.json_message('Hauflo agent not set up', 503)
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict):
            return self.json_message('an object expected', 400)
        if (wait := engine.test_wait(body)):
            return self.json({'message': 'a test to this phone was just sent', 'code': 'test_wait', 'retry_after': wait}, 429)
        return self.json(await engine.async_test(body))

class AlarmsView(HomeAssistantView):
    url = '/api/hauflo/alarms'
    name = 'api:hauflo:alarms'
    requires_auth = True

    def __init__(self, engine_of, house_of=None) -> None:
        self.engine_of = engine_of
        self.house_of = house_of

    async def get(self, request):
        engine = self.engine_of()
        out: dict[str, Any] = {}
        pushed: set[str] = set()
        for key, inc in engine.active.items() if engine else []:
            entity_id = key.partition('|')[2]
            if entity_id not in out or inc['since'] < out[entity_id]:
                out[entity_id] = inc['since']
            if inc.get('pushed'):
                pushed.add(entity_id)
        out['_pushed'] = sorted(pushed)
        out['_list'] = [{'key': key, 'entity': key.partition('|')[2], 'since': inc['since'], 'ackedBy': inc.get('acked_by'), 'critical': bool(r and r['priority'] == 'critical'), 'noData': bool(inc.get('nodata')), 'title': r['title'] if r else ''} for key, inc in (engine.active.items() if engine else []) for r in [next((x for x in engine.config['rules'] if x['id'] == key.partition('|')[0]), None)]] + [{'key': key, 'entity': key.partition('|')[2], 'since': v.get('since'), 'returned': v.get('cleared'), 'ackedBy': None, 'critical': True, 'noData': False, 'title': v.get('title', '')} for key, v in (engine.unacked_rtn.items() if engine else []) if key not in engine.active]
        if self.house_of is not None and (house := self.house_of()) is not None:
            out.update(house)
        return self.json(out)

    async def post(self, request):
        engine = self.engine_of()
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        user = request['hass_user']
        who = (user.name if user else None) or 'кто-то'
        if user is not None and user.system_generated and (user.name == 'Hauflo agent') and isinstance(body, dict) and isinstance(body.get('by'), str):
            by = ''.join((ch for ch in body['by'] if ch.isprintable())).strip()[:80]
            if by:
                who = f'{by} (Hauflo)'
        if engine is not None and isinstance(body, dict) and (body.get('ack_all') is True):
            keys = [k for k, inc in engine.active.items() if not inc.get('acked_by')] + [k for k in engine.unacked_rtn if k not in engine.active]
            keys += [k for e in self._house_open() if not e['acked'] for k in e['keys']]
            done = [k for k in keys if await self._ack(engine, k, who)]
            return self.json({'ok': True, 'acked': len(done)})
        if engine is not None and isinstance(body, dict) and ('ack_keys' in body):
            keys = body['ack_keys']
            if not isinstance(keys, list) or len(keys) > 200 or (not all((isinstance(k, str) and '|' in k and (len(k) <= 300) for k in keys))):
                return self.json_message('ack_keys: a list of alarm keys', 400)
            done = [k for k in dict.fromkeys(keys) if await self._ack(engine, k, who)]
            return self.json({'ok': True, 'acked': len(done)})
        key = body.get('ack') if isinstance(body, dict) else None
        if engine is None or not isinstance(key, str) or '|' not in key or (len(key) > 300):
            return self.json_message('ack: a key expected', 400)
        ok = await self._ack(engine, key, who)
        return self.json({'ok': ok}, 200 if ok else 404)

    def _house_open(self) -> list[dict[str, Any]]:
        house = self.house_of() if self.house_of is not None else None
        items = ((house or {}).get('_summary') or {}).get('items') or []
        return [e for e in items if e.get('state') == 'open' and e.get('source') == 'house' and e.get('keys')]

    async def _ack(self, engine: Any, key: str, who: str) -> bool:
        if key.startswith(alarms_now.HOUSE_KEY + '|'):
            entity_id = key.partition('|')[2]
            return any((e['entity'] == entity_id for e in self._house_open())) and engine.ack_house(entity_id, who)
        return await engine.async_ack(key, who)
