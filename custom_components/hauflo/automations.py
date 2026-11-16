from __future__ import annotations
import asyncio
from collections.abc import Callable
import datetime as dt
import hashlib
import json
import logging
import math
import re
import secrets
import time
from typing import Any
from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, translation
from homeassistant.util import dt as dt_util
from .house import refuse
from .ownership import entity_room
from .natural import natural_key
from .const import DOMAIN, wants_ru
from .storage import HaufloStore
from . import recipes
from .tuning import TUNING
from . import yamlfile
from .notify import label
from . import rules as rl
_LOGGER = logging.getLogger(__name__)
STORE_KEY = 'hauflo.automations'
PREFIX = 'hauflo_auto_'
ALARM_EVENT = 'hauflo_alarm_'
RUN_VAR = 'hauflo_check'
AUTOMATIONS = 'automations.yaml'
EVENTS = ('sunrise', 'sunset', 'time', 'alarm_android', 'alarm_iphone', 'value', 'presence', 'state')
VALUE_KINDS = ('temperature', 'humidity', 'carbon_dioxide', 'illuminance')
STATS = ('mean', 'min', 'max')
OPS = ('above', 'below')
_T = TUNING['automations']
MAX_SENSORS = _T['max_sensors']
MAX_FOR_MIN = _T['max_for_min']
STATE_CLASSES: dict[str, str] = {c: kind for kind, classes in _T['state_classes'].items() for c in classes}
MEDIA = 'media'
STATE_KINDS = (*_T['state_classes'], MEDIA)
TOS = ('on', 'off')
STATE_ALL_MARGIN_S = _T['state_all_margin_s']
IFS = ('dark', 'light', 'between', 'home', 'away', 'value')
MAX_ONLY_IF = _T['max_only_if']
MAX_SET = _T['max_set']
HELPER = 'min_max'
DAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
MAX_OFFSET = 120
MAX_RULES = 50
MAX_ACTIONS = 20
MAX_RAMP_MIN = _T['max_ramp_min']
RAMP_STEPS = 12
_FROM = re.compile('[a-z0-9_]{1,40}')
_ID = re.compile('hauflo_auto_[0-9a-f]{12}')
_ALARM_CODE = re.compile('hauflo_alarm_[0-9a-f]{6}')
_TIME = re.compile('([01]\\d|2[0-3]):([0-5]\\d)')
_UNSET = ((0.0, 0.0), (32.87336, -117.22743))
NO_LOCATION_RU = 'Место объекта не задано — рассвет и закат посчитаются неправильно. Укажите, где он.'
NO_LOCATION_EN = "The site's location isn't set — sunrise and sunset will be wrong. Set where it is."
CLIENT_DOMAINS = tuple(rl.C('actions', 'client_domains'))
CLIENT_ACTS = frozenset(rl.C('actions', 'client_acts'))
GROUP_DOMAINS = tuple(rl.C('actions', 'group_domains'))
REPEAT = rl.C('actions', 'repeat')
RAMP = 'ramp'
MAX_RAMPS = _T['max_ramps']
SCRIPT_NEVER = set(_T['script_never_domains'])
LEGACY_SET = ('light', 'cover', 'scene')
LEGACY_MODES = ('heat', 'cool', 'off')
LEGACY_DO = {'scene': {'on': 'scene'}, 'climate': {'temp': 'setpoint'}}
COARSE = {'brightness': 'on', RAMP: 'on', 'pulse': 'on', 'scene': 'on', 'setpoint': 'temp'}
MOVED = '_moved'

def _acts(domain: str) -> dict[str, dict[str, Any]]:
    if domain not in CLIENT_DOMAINS:
        return {}
    return {do: spec for do, spec in rl.C('actions_by_domain').get(domain, {}).items() if do in CLIENT_ACTS}
_H = TUNING['habits']
COVER_CLASSES = set(_H['cover_classes'])
COVER_NEVER = set(_H['cover_never'])
SCENE_PLATFORMS = set(_H['scene_platforms'])
CLIMATE_STEP = _H['climate_step']
BACKUP = AUTOMATIONS + '.hauflo-bak-'
KEEP_BACKUPS = 3
_FILE_LOCK = asyncio.Lock()

class Invalid(rl.Invalid):

    def __init__(self, code: str, key: str | None=None, **ph: Any) -> None:
        super().__init__(code, key or f'automations_{code}', **ph)

def _bad(what: str) -> Invalid:
    return Invalid('bad_request', what=what)

class NotPlain(RuntimeError):
    RU = 'Hauflo не меняет automations.yaml: в нём комментарии, !include / !secret или ручное оформление, которые потерялись бы при перезаписи. Правила Hauflo на этом объекте добавляет интегратор, или он приводит файл к виду, в каком его сохраняет редактор автоматизаций Home Assistant.'
    EN = "Hauflo doesn't change automations.yaml: it has comments, !include / !secret or hand formatting a rewrite would lose. At this site the integrator adds Hauflo's rules, or saves the file the way Home Assistant's automation editor writes it."

class Changed(Exception):
    pass

def _digest(cfg: Any) -> str:
    if not isinstance(cfg, dict):
        return ''
    body = {k: v for k, v in cfg.items() if k not in ('description', 'initial_state')}
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

def _int(value: Any, lo: int, hi: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or (not lo <= value <= hi):
        raise Invalid('bad_range', field=what, lo=lo, hi=hi)
    return value

def _float(value: Any, lo: float, hi: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (not math.isfinite(value)) or (not lo <= value <= hi):
        raise Invalid('bad_range', field=what, lo=lo, hi=hi)
    return float(value)

def _clean_name(value: Any) -> str:
    if value is None:
        return ''
    if not isinstance(value, str):
        raise _bad('name')
    return ' '.join(''.join((c for c in value if c.isprintable())).split())[:80]

def _offset_text(minutes: int) -> str:
    sign = '-' if minutes < 0 else ''
    m = abs(minutes)
    return f'{sign}{m // 60:02d}:{m % 60:02d}:00'

async def async_add_new(hass: HomeAssistant, configs: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    from homeassistant.components.automation.config import async_validate_config_item
    for cfg in configs:
        await async_validate_config_item(hass, cfg['id'], cfg)
    helper = Automations(hass, lambda _eid: False)
    async with _FILE_LOCK:
        data, before = await helper._read_file()
        have = {a.get('id') for a in data if isinstance(a, dict)}
        new = [c for c in configs if c['id'] not in have]
        if new:

            def redo(fresh: list[Any]) -> list[Any]:
                there = {a.get('id') for a in fresh if isinstance(a, dict)}
                return [*fresh, *(c for c in new if c['id'] not in there)]
            await helper._write_file([*data, *new], before, redo)
    if new:
        await hass.services.async_call('automation', 'reload', {}, blocking=True)
    return ([c['id'] for c in new], [c['id'] for c in configs if c['id'] in have])

class Automations:

    def __init__(self, hass: HomeAssistant, owns: Callable[[str], bool], protected: Callable[[str], bool] | None=None, habits: Callable[[], Any] | None=None) -> None:
        self.hass = hass
        self.owns = owns
        self.protected = protected or (lambda eid: False)
        self.habits = habits or (lambda: None)
        self.store = HaufloStore(hass, STORE_KEY)
        self.lock = asyncio.Lock()
        self._env = rl.Env(hass, owns=self._pickable, own_raw=lambda _eid: False, client=True)
        self.tx = rl.Texts(hass)

    async def texts(self) -> rl.Texts:
        if not self.tx.texts[rl.LANGS[1]]:
            await self.tx.async_texts()
        return self.tx

    def message(self, err: rl.Invalid, ru: bool) -> str:
        tx, ph = (self.tx, dict(err.ph))
        if isinstance((f := ph.get('field')), str):
            ph['field'] = tx.say(f'automations_f_{f}', ru)
        if isinstance((k := ph.get('kind')), str):
            ph['kind'] = self._d(ru, f'dom_{k}') if k in GROUP_DOMAINS else k
        if isinstance((ks := ph.get('kinds')), list):
            ph['kinds'] = ', '.join((self._d(ru, f'dom_{k}') for k in ks))
        if isinstance((acts := ph.get('acts')), list):
            ph['acts'] = ', '.join((self._lower(tx.say(f'rules_do_{a}', ru)) for a in acts)) or '—'
        if isinstance((room := ph.get('room')), str):
            ph['room'] = self._room_name(room)
        return tx.say(err.key, ru, **tx.words(ru, ph))

    def _area(self, eid: str) -> str | None:
        return entity_room(self.hass, eid)

    def _pickable(self, eid: Any, depth: int=0) -> bool:
        if not isinstance(eid, str) or not (acts := _acts((domain := eid.split('.')[0]))) or (st := self.hass.states.get(eid)) is None:
            return False
        ent = er.async_get(self.hass).async_get(eid)
        if ent is None or ent.disabled_by or ent.hidden_by or (ent.entity_category is not None) or self.protected(eid):
            return False
        classes = rl.classes_of(self.hass, eid)
        if domain == 'cover':
            if classes & COVER_NEVER or not (classes & COVER_CLASSES or self.owns(eid)):
                return False
        elif not rl.client_class_ok(self.hass, eid):
            return False
        if domain == 'scene':
            if self.owns(eid) or ent.platform in SCENE_PLATFORMS:
                return True
            members = st.attributes.get('entity_id')
            return ent.platform == 'homeassistant' and depth < 2 and isinstance(members, list) and bool(members) and all((self._pickable(m, depth + 1) for m in members))
        if domain == 'script' and (not self._safe_script(eid, depth)):
            return False
        return any((rl.supports(st, spec) for spec in acts.values()))

    def _safe_script(self, eid: str, depth: int) -> bool:
        from homeassistant.components import script
        try:
            if depth >= 2 or any((f(self.hass, eid) for f in (script.devices_in_script, script.areas_in_script, script.floors_in_script, script.labels_in_script))):
                return False
            members = script.entities_in_script(self.hass, eid)
        except Exception:
            return False
        for m in members:
            d = m.split('.')[0]
            if d in SCRIPT_NEVER or self.protected(m) or (d == 'cover' and rl.classes_of(self.hass, m) & COVER_NEVER) or (d == 'script' and (not self._pickable(m, depth + 1))):
                return False
        return True

    def _dims(self, eid: str) -> bool:
        st = self.hass.states.get(eid)
        return st is not None and any((m != 'onoff' for m in st.attributes.get('supported_color_modes') or []))

    def _fits(self, eid: str, do: str, value: Any=None) -> bool:
        if do == RAMP:
            return self._dims(eid)
        domain = eid.split('.')[0]
        if (spec := _acts(domain).get(do)) is None:
            return False
        if domain == 'light':
            return True
        st = self.hass.states.get(eid)
        if not rl.supports(st, spec):
            return False
        if spec.get('value') == 'option' and value is not None and (st is not None):
            opts = st.attributes.get(spec['options'])
            return isinstance(opts, (list, tuple)) and str(value) in map(str, opts)
        return True

    def _modes(self, eid: str) -> list[str]:
        st = self.hass.states.get(eid)
        have = st.attributes.get('hvac_modes') or [] if st else []
        return [m for m in ('heat', 'cool', 'off') if m in have]

    def _alarm_sensor(self, eid: Any) -> bool:
        if not isinstance(eid, str) or not eid.startswith('sensor.'):
            return False
        ent = er.async_get(self.hass).async_get(eid)
        if ent is None or ent.platform != 'mobile_app' or ent.disabled_by:
            return False
        return str(ent.unique_id).endswith('next_alarm') or eid.endswith('_next_alarm')

    def _members(self, domain: str, area_id: Any, do: str | None=None, value: Any=None) -> list[str]:
        if not isinstance(area_id, str):
            return []
        reg = er.async_get(self.hass)
        found = [e for e in self.hass.states.async_entity_ids(domain) if self._pickable(e) and self._area(e) == area_id and (do is None or self._fits(e, do, value))]
        single = [e for e in found if (x := reg.async_get(e)) is None or x.platform != 'group']
        return sorted(single or found) if domain == 'light' else sorted(single)

    def _room_lights(self, area_id: str) -> list[str]:
        return self._members('light', area_id)

    def _room_covers(self, area_id: str, position: bool=False) -> list[str]:
        return self._members('cover', area_id, 'position' if position else None)

    def _area_target(self, domain: str, area_id: str, explicit: list[str], do: Any=None) -> dict[str, Any]:
        if domain != 'light':
            return {'entity_id': explicit}
        everything = [eid for eid in self.hass.states.async_entity_ids(domain) if self._area(eid) == area_id]
        if everything and all((self._pickable(eid) for eid in everything)) and (not (do == 'toggle' and self._grouped(everything))):
            return {'area_id': area_id}
        return {'entity_id': explicit}

    def _grouped(self, eids: list[str]) -> bool:
        reg = er.async_get(self.hass)
        return any(((e := reg.async_get(eid)) is not None and e.platform == 'group' for eid in eids))

    def _house_target(self, domain: str, do: Any) -> bool:
        everything = self.hass.states.async_entity_ids(domain)
        return domain == 'light' and bool(everything) and all((self._pickable(e) for e in everything)) and (not (do == 'toggle' and self._grouped(everything)))

    def _value_kind(self, eid: Any) -> str | None:
        if not isinstance(eid, str) or not eid.startswith('sensor.') or (s := self.hass.states.get(eid)) is None:
            return None
        kind = s.attributes.get('device_class')
        if kind not in VALUE_KINDS:
            return None
        ent = er.async_get(self.hass).async_get(eid)
        if ent is not None and (ent.disabled_by or ent.hidden_by or (ent.platform == HELPER and str(ent.original_name or '').startswith('hauflo_'))):
            return None
        return str(kind)

    def _state_kind(self, eid: Any) -> str | None:
        if not isinstance(eid, str) or (domain := eid.split('.')[0]) not in ('binary_sensor', 'media_player') or (s := self.hass.states.get(eid)) is None:
            return None
        ent = er.async_get(self.hass).async_get(eid)
        if ent is not None and (ent.disabled_by or ent.hidden_by or ent.entity_category is not None):
            return None
        if domain == 'media_player':
            return MEDIA
        classes = [s.attributes.get('device_class'), *((ent.device_class, ent.original_device_class) if ent else ())]
        return next((STATE_CLASSES[c] for c in classes if c in STATE_CLASSES), None)

    def _state_spec(self, raw: dict[str, Any]) -> dict[str, Any]:
        sensors = raw.get('sensors')
        if not isinstance(sensors, list) or not 1 <= len(sensors) <= MAX_SENSORS or len(set(map(str, sensors))) != len(sensors):
            raise Invalid('bad_sensors', n=MAX_SENSORS)
        kinds = {self._state_kind(e) for e in sensors}
        if None in kinds:
            raise Invalid('bad_state_sensor', entity=next((e for e in sensors if self._state_kind(e) is None)))
        if len(kinds) != 1:
            raise Invalid('bad_sensor_mix')
        kind = kinds.pop()
        if raw.get('kind') not in (None, kind):
            raise _bad('when.kind')
        if raw.get('to') not in TOS:
            raise _bad('when.to')
        if kind == MEDIA and raw['to'] == 'off' and (len(sensors) > 1):
            raise Invalid('bad_media_off')
        return {'kind': kind, 'sensors': sorted(sensors), 'to': raw['to'], 'for_min': _int(raw.get('for_min', 0), 0, MAX_FOR_MIN, 'for_min')}

    def _value_spec(self, raw: Any, what: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise _bad(what)
        sensors = raw.get('sensors')
        if not isinstance(sensors, list) or not 1 <= len(sensors) <= MAX_SENSORS or len(set(map(str, sensors))) != len(sensors):
            raise Invalid('bad_sensors', n=MAX_SENSORS)
        kinds = {self._value_kind(e) for e in sensors}
        if None in kinds:
            raise Invalid('bad_value_sensor', entity=next((e for e in sensors if self._value_kind(e) is None)))
        if len(kinds) != 1 or len({self.hass.states.get(e).attributes.get('unit_of_measurement') for e in sensors}) != 1:
            raise Invalid('bad_value_mix')
        if raw.get('kind') not in (None, *kinds):
            raise _bad(f'{what}.kind')
        op = raw.get('op')
        if op not in OPS:
            raise _bad(f'{what}.op')
        x = _float(raw.get('x'), -100, 200000, 'x')
        out: dict[str, Any] = {'sensors': sorted(sensors), 'kind': kinds.pop(), 'op': op, 'x': int(x) if x.is_integer() else round(x, 2)}
        if len(sensors) > 1:
            if raw.get('stat', 'mean') not in STATS:
                raise _bad(f'{what}.stat')
            out['stat'] = raw.get('stat', 'mean')
        return out

    @staticmethod
    def _never(when: dict[str, Any], only_if: list[dict[str, Any]]) -> None:
        ifs = {c['if'] for c in only_if}
        if when['event'] == 'presence' and ('home' if when['to'] == 'away' else 'away') in ifs:
            raise Invalid('never_presence')
        if when['event'] == 'time':
            for c in only_if:
                if c['if'] == 'between':
                    a, b = (c['after'], c['before'])
                    for at in when.get('times') or [when['at']]:
                        if not (a <= at < b if a < b else at >= a or at < b):
                            raise Invalid('never_between', a=a, b=b, at=at)
        spans: dict[tuple[Any, ...], list[float]] = {}
        for spec in (x for x in [when, *only_if] if x.get('event') == 'value' or x.get('if') == 'value'):
            span = spans.setdefault((tuple(spec['sensors']), spec.get('stat')), [-math.inf, math.inf])
            if spec['op'] == 'above':
                span[0] = max(span[0], spec['x'])
            else:
                span[1] = min(span[1], spec['x'])
            if span[0] >= span[1]:
                raise Invalid('never_value')

    def _only_if(self, raw: Any) -> list[dict[str, Any]]:
        if raw is None:
            return []
        if not isinstance(raw, list) or len(raw) > MAX_ONLY_IF:
            raise Invalid('bad_only_if_count', n=MAX_ONLY_IF)
        out: list[dict[str, Any]] = []
        for c in raw:
            kind = c.get('if') if isinstance(c, dict) else None
            if kind not in IFS:
                raise _bad('only_if.if')
            if kind == 'between':
                after, before = (c.get('after'), c.get('before'))
                if not all((isinstance(t, str) and _TIME.fullmatch(t) for t in (after, before))) or after == before:
                    raise Invalid('bad_between')
                out.append({'if': 'between', 'after': after, 'before': before})
            elif kind == 'value':
                out.append({'if': 'value', **self._value_spec(c, 'only_if.value')})
            else:
                out.append({'if': kind})
        if {'dark', 'light'} <= {c['if'] for c in out} or {'home', 'away'} <= {c['if'] for c in out}:
            raise Invalid('never_only_if')
        if len([c for c in out if c['if'] == 'between']) > 1:
            raise Invalid('bad_between_one')
        return out

    def _set_members(self, domain: str, area_id: str, position: bool=False) -> list[str]:
        return self._members(domain, area_id, 'position' if position else None)

    def _all_target(self, domain: str, do: Any=None, value: Any=None) -> tuple[list[str], list[str]]:
        if isinstance(do, bool):
            do = 'position' if do else None
        reg = er.async_get(self.hass)
        rooms, loose = (set(), [])
        for eid in self.hass.states.async_entity_ids(domain):
            if not self._pickable(eid):
                continue
            if (area := self._area(eid)):
                rooms.add(area)
            elif (e := reg.async_get(eid)) is not None and e.platform != 'group' and (do is None or self._fits(eid, do, value)):
                loose.append(eid)
        rooms = {r for r in rooms if self._members(domain, r, do, value)}
        return (sorted(rooms), sorted(loose))

    @staticmethod
    def _domain(t: dict[str, Any]) -> str:
        if t.get('domain'):
            return t['domain']
        e = t['entity_id']
        return (e if isinstance(e, str) else e[0]).split('.')[0]

    def _target(self, a: dict[str, Any]) -> dict[str, Any] | None:
        t, do, value = (a['target'], a['do'], a.get('value'))
        domain = self._domain(t)
        if isinstance(t.get('entity_id'), str):
            return {'entity_id': t['entity_id']}
        if isinstance(t.get('area_id'), str):
            members = self._members(domain, t['area_id'], do, value)
            return self._area_target(domain, t['area_id'], members, do) if members else None
        if t.get('all') and self._house_target(domain, do):
            return {'entity_id': 'all'}
        rooms, loose = self._all_target(domain, do, value) if t.get('all') else ([], [])
        rooms = sorted({*rooms, *t.get('area_id', [])})
        area_ids: list[str] = []
        eids: list[str] = []
        for room in rooms:
            if not (members := self._members(domain, room, do, value)):
                continue
            got = self._area_target(domain, room, members, do)
            if 'area_id' in got:
                area_ids.append(room)
            else:
                eids += got['entity_id']
        for eid in [*loose, *t.get('entity_id', [])]:
            if self._pickable(eid) and self._fits(eid, do, value) and (eid not in eids) and (self._area(eid) not in area_ids):
                eids.append(eid)
        target: dict[str, Any] = {}
        if area_ids:
            target['area_id'] = area_ids
        if eids:
            target['entity_id'] = eids
        return target or None

    def covers(self, a: dict[str, Any]) -> set[tuple[str, str]]:
        try:
            a = a if 'target' in a else self._legacy(a)
            t = a['target']
            domain = self._domain(t)
        except (Invalid, KeyError, IndexError, TypeError, AttributeError):
            return set()
        if isinstance(t.get('entity_id'), str):
            eids = {t['entity_id']}
        elif isinstance(t.get('area_id'), str):
            eids = set(self._members(domain, t['area_id']))
        else:
            rooms, loose = self._all_target(domain) if t.get('all') else ([], [])
            eids = {*loose, *(t.get('entity_id') or [])}
            for room in {*rooms, *(t.get('area_id') or [])}:
                eids |= set(self._members(domain, room))
        act = a.get('value') if a['do'] == 'mode' else COARSE.get(a['do'], a['do'])
        return {(e, act) for e in eids}

    def _acts_of(self, eid: str, st: Any) -> dict[str, dict[str, Any]]:
        lim = rl.C('limits')
        attrs = st.attributes
        out: dict[str, dict[str, Any]] = {}
        for do, spec in _acts(eid.split('.')[0]).items():
            if not rl.supports(st, spec):
                continue
            kind = spec.get('value')
            item: dict[str, Any] = {'kind': kind} if kind else {}
            if kind == 'option':
                item['options'] = [str(o) for o in attrs.get(spec['options']) or []]
            elif kind in rl.NUMERIC:
                fallback = {'percent': lim['percent'], 'setpoint': lim['setpoint'], 'seconds': rl.C('actions', 'pulse_s')}.get(kind, (-lim['number_abs'], lim['number_abs']))
                item['min'], item['max'] = rl._bounds(spec, attrs, fallback)
                step = rl.num(attrs.get(spec['step'])) if spec.get('step') else None
                item['step'] = step if step and step > 0 else lim['setpoint_step'] if kind == 'setpoint' else 1
                unit = {'percent': '%', 'seconds': 's', 'setpoint': self.hass.config.units.temperature_unit}.get(kind, attrs.get('unit_of_measurement'))
                if unit:
                    item['unit'] = unit
            out[do] = item
        if eid.startswith('light.') and self._dims(eid):
            out[RAMP] = {'kind': RAMP, 'min': 1, 'max': 100, 'step': 1, 'unit': '%', 'minutes': [1, MAX_RAMP_MIN]}
        return out

    def targets(self) -> dict[str, Any]:
        areas = ar.async_get(self.hass)
        out: dict[str, list[dict[str, Any]]] = {'cover': [], 'light': [], 'scene': [], 'fan': [], 'switch': [], 'climate': []}
        rooms: dict[str, int] = {}
        for domain in out:
            for eid in sorted(self.hass.states.async_entity_ids(domain)):
                if not self._pickable(eid):
                    continue
                s = self.hass.states.get(eid)
                area = self._area(eid)
                item: dict[str, Any] = {'entity_id': eid, 'name': label(self.hass, eid, s), 'area': area}
                if domain == 'cover':
                    item['position'] = bool(int(s.attributes.get('supported_features') or 0) & 4)
                    item['group'] = (e := er.async_get(self.hass).async_get(eid)) is not None and e.platform == 'group'
                if domain == 'light':
                    modes = s.attributes.get('supported_color_modes') or []
                    item['dim'] = any((m != 'onoff' for m in modes))
                if domain == 'climate':
                    item.update(self._climate_spec(eid))
                out[domain].append(item)
        for area_id in {i['area'] for i in out['light'] if i['area']}:
            if (n := len(self._room_lights(area_id))):
                rooms[area_id] = n
        names = {a: area.name if (area := areas.async_get_area(a)) else a for a in {i['area'] for v in out.values() for i in v if i['area']}}
        cover_rooms = []
        for area_id in {i['area'] for i in out['cover'] if i['area']}:
            if (n := len(self._room_covers(area_id))):
                cover_rooms.append({'area_id': area_id, 'name': names.get(area_id, area_id), 'covers': n, 'position': bool(self._room_covers(area_id, True))})
        alarms = []
        for ent in er.async_get(self.hass).entities.values():
            if ent.domain == 'sensor' and self._alarm_sensor(ent.entity_id):
                dev = dr.async_get(self.hass).async_get(ent.device_id) if ent.device_id else None
                s = self.hass.states.get(ent.entity_id)
                alarms.append({'entity_id': ent.entity_id, 'phone': dev.name_by_user or dev.name if dev else ent.entity_id, 'next': s.state if s else None})
        sensors = []
        for eid in sorted(self.hass.states.async_entity_ids('sensor')):
            if (kind := self._value_kind(eid)) is None:
                continue
            s = self.hass.states.get(eid)
            sensors.append({'entity_id': eid, 'name': label(self.hass, eid, s), 'area': self._area(eid), 'kind': kind, 'unit': s.attributes.get('unit_of_measurement'), 'value': s.state})
        binaries = []
        for domain in ('binary_sensor', 'media_player'):
            for eid in sorted(self.hass.states.async_entity_ids(domain)):
                if (kind := self._state_kind(eid)) is None:
                    continue
                s = self.hass.states.get(eid)
                binaries.append({'entity_id': eid, 'name': label(self.hass, eid, s), 'area': self._area(eid), 'kind': kind, 'state': s.state})
        for i in (*sensors, *binaries):
            if i['area'] and i['area'] not in names:
                names[i['area']] = area.name if (area := areas.async_get_area(i['area'])) else i['area']
        reg = er.async_get(self.hass)
        entities = []
        for domain in CLIENT_DOMAINS:
            for eid in sorted(self.hass.states.async_entity_ids(domain)):
                if not self._pickable(eid) or not (acts := self._acts_of(eid, (s := self.hass.states.get(eid)))):
                    continue
                item = {'entity_id': eid, 'name': label(self.hass, eid, s), 'area': self._area(eid), 'domain': domain, 'acts': acts, 'state': s.state}
                if (e := reg.async_get(eid)) is not None and e.platform == 'group':
                    item['group'] = True
                entities.append(item)
                if item['area'] and item['area'] not in names:
                    names[item['area']] = area.name if (area := areas.async_get_area(item['area'])) else item['area']
        people = sum((1 for eid in self.hass.states.async_entity_ids('person') if self.hass.states.get(eid).attributes.get('device_trackers') or []))
        return {'sensors': sensors, 'binaries': binaries, 'people': people, 'alarms': sorted(alarms, key=lambda a: str(a['phone'])), 'covers': out['cover'], 'lights': out['light'], 'scenes': out['scene'], 'fans': out['fan'], 'switches': out['switch'], 'climates': out['climate'], 'rooms': sorted(({'area_id': a, 'name': names.get(a, a), 'lights': n} for a, n in rooms.items()), key=lambda r: natural_key(r['name'])), 'cover_rooms': sorted(cover_rooms, key=lambda r: natural_key(r['name'])), 'areas': names, 'entities': entities, 'group_domains': list(GROUP_DOMAINS)}

    def validate(self, raw: Any, previous: dict[str, Any] | None=None) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise _bad('rule')
        when = raw.get('when')
        if not isinstance(when, dict) or when.get('event') not in EVENTS:
            raise _bad('when.event')
        rule_when: dict[str, Any] = {'event': when['event']}
        if when['event'] == 'value':
            rule_when.update(self._value_spec(when, 'when'))
            rule_when['for_min'] = _int(when.get('for_min', 0), 0, MAX_FOR_MIN, 'for_min')
        elif when['event'] == 'state':
            rule_when.update(self._state_spec(when))
        elif when['event'] == 'presence':
            if when.get('to') not in ('away', 'home'):
                raise _bad('when.to')
            rule_when['to'] = when['to']
        elif when['event'] == 'time':
            times = when.get('times', [when.get('at')] if 'at' in when else None)
            n = rl.C('schedule', 'moments')
            if not isinstance(times, list) or not 1 <= len(times) <= n or (not all((isinstance(t, str) and _TIME.fullmatch(t) for t in times))) or (len(set(times)) != len(times)):
                raise Invalid('bad_times', n=n)
            rule_when['times'] = sorted(times)
        elif when['event'] == 'alarm_iphone':
            code = (previous or {}).get('when', {}).get('code')
            rule_when['code'] = code if isinstance(code, str) and _ALARM_CODE.fullmatch(code) else ALARM_EVENT + secrets.token_hex(3)
        else:
            rule_when['offset'] = _int(when.get('offset', 0), -MAX_OFFSET, MAX_OFFSET, 'offset')
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
        if sum((1 for a in clean if a['do'] == RAMP)) > MAX_RAMPS:
            raise Invalid('bad_ramps', n=MAX_RAMPS)
        enabled = raw.get('enabled', True)
        if not isinstance(enabled, bool):
            raise _bad('enabled')
        origin = raw.get('from')
        if origin is not None and (not isinstance(origin, str) or not _FROM.fullmatch(origin)):
            raise _bad('from')
        out = {'when': rule_when, 'days': [d for d in DAYS if d in days], 'actions': clean, 'enabled': enabled, 'name': _clean_name(raw.get('name'))}
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
                raise _bad('set.domain')
            rooms, entities = (a.get('rooms') or [], a.get('entities') or [])
            if not isinstance(rooms, list) or not isinstance(entities, list):
                raise _bad('set.rooms')
            if domain == 'scene' and (rooms or a.get('all')):
                raise _bad('set.scene')
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
                raise _bad('room_covers.cover')
            return {'do': cmd, 'target': {'domain': 'cover', 'area_id': a.get('area_id')}, **(value if cmd == 'position' else {})}
        eid = a.get('entity_id')
        if not isinstance(eid, str):
            raise _bad('entity_id')
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
            _int(value['value'], 1, 100, 'brightness')
            return 'brightness'
        return LEGACY_DO.get(domain, {}).get(do, do)

    def _clean_act(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise _bad('action')
        if 'target' not in raw:
            raw = self._legacy(raw)
        elif set(raw) - {'do', 'target', 'value', 'minutes', 'repeat'}:
            raise _bad('action.keys')
        t, do = (raw.get('target'), raw.get('do'))
        if not isinstance(t, dict) or not t or set(t) - {'entity_id', 'area_id', 'domain', 'all'} or (t.get('all', True) is not True):
            raise _bad('target')
        ents, areas, every = (t.get('entity_id'), t.get('area_id'), t.get('all') is True)
        el = [ents] if isinstance(ents, str) else ents if ents is not None else []
        al = [areas] if isinstance(areas, str) else areas if areas is not None else []
        if not isinstance(el, list) or not isinstance(al, list) or (not all((isinstance(x, str) for x in (*el, *al)))) or (len(set(el)) != len(el)) or (len(set(al)) != len(al)):
            raise _bad('target.lists')
        if not (el or al or every):
            raise Invalid('bad_target_empty')
        if len(el) + len(al) > MAX_SET:
            raise Invalid('bad_target_many', n=MAX_SET)
        if (isinstance(ents, str) or isinstance(areas, str)) and sum(map(bool, (el, al, every))) > 1:
            raise _bad('target.one')
        kinds = {e.split('.')[0] for e in el}
        domain = t.get('domain') or (next(iter(kinds)) if len(kinds) == 1 else None)
        if not isinstance(domain, str) or kinds - {domain}:
            raise Invalid('bad_target_kinds')
        if (al or every) and domain not in GROUP_DOMAINS:
            raise Invalid('bad_target_group', kinds=list(GROUP_DOMAINS))
        for e in el:
            if not self._pickable(e):
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
            return {'do': RAMP, 'target': out_t, 'value': _int(raw.get('value'), 1, 100, 'brightness'), 'minutes': _int(raw.get('minutes'), 1, MAX_RAMP_MIN, 'ramp_minutes')}
        acts = _acts(domain)
        if (spec := acts.get(do)) is None:
            raise Invalid('bad_do', kind=domain, acts=list(acts))
        kind = spec.get('value')
        value = raw.get('value')
        try:
            if kind == 'option' and (not el):
                if not isinstance(value, str) or not value:
                    raise Invalid('bad_option')
            elif kind:
                value = rl.action_value(kind, spec, do, value, self.hass.states.get(el[0]) if el else None)
            else:
                value = None
            check = 'on' if domain == 'light' and do == 'brightness' else do
            for e in el:
                rl.clean_action({'do': check, 'entity_id': e, **({'value': value} if kind and check == do else {})}, self._env)
            repeat = rl.clean_repeat(raw.get('repeat'), do, value if kind == 'seconds' else None)
        except Invalid:
            raise
        except rl.Invalid as err:
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
        return {'temp': temp, 'min': lo, 'max': hi, 'step': float(step) if ok(step) and step > 0 else CLIMATE_STEP, 'modes': self._modes(eid), 'value': a.get('temperature')}

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
        spec = rl.C('actions_by_domain')[domain][do]
        call: dict[str, Any] = {'action': f"{domain}.{spec['service']}", 'target': target}
        if spec.get('field') and a.get('value') is not None:
            call['data'] = {spec['field']: a['value']}
        steps = [call]
        pulse = a['value'] if spec.get('value') == 'seconds' else 0
        if pulse:
            steps += [{'delay': {'seconds': pulse}}, {'action': f"{domain}.{rl.C('actions_by_domain')[domain]['off']['service']}", 'target': target}]
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
        when = rule['when']
        also: list[dict[str, Any]] = []
        if when['event'] == 'time':
            at = [t + ':00' for t in when.get('times') or [when['at']]]
            trigger: dict[str, Any] = {'trigger': 'time', 'at': at[0] if len(at) == 1 else at}
        elif when['event'] == 'alarm_iphone':
            trigger = {'trigger': 'event', 'event_type': when['code']}
        elif when['event'] == 'alarm_android':
            trigger = {'trigger': 'time', 'at': {'entity_id': when['sensor'], 'offset': _offset_text(when['offset'])}}
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
                trigger['offset'] = _offset_text(when['offset'])
        ru = self._ru(rule)
        conditions = also + ([{'condition': 'time', 'weekday': list(rule['days'])}] if len(rule['days']) < 7 else []) + [self._condition(c) for c in rule.get('only_if', [])]
        actions = self._service_calls(rule)
        if when['event'] == 'alarm_iphone':
            if conditions:
                skip = {'condition': 'template', 'value_template': '{{ %s | default(false) }}' % RUN_VAR}
                actions = [{'condition': 'or', 'conditions': [skip, {'condition': 'and', 'conditions': conditions}]}, *actions]
            conditions = []
        cfg: dict[str, Any] = {'id': rule_id, 'alias': rule.get('alias') or self._alias(rule), 'description': self.describe(rule, ru), 'triggers': [trigger], 'conditions': conditions, 'actions': actions, 'mode': 'single'}
        return cfg

    def _ru(self, rule: dict[str, Any]) -> bool:
        lang = rule.get('lang')
        return lang == rl.LANGS[0] if lang in rl.LANGS else str(self.hass.config.language or '').startswith(rl.LANGS[0])

    def _alias(self, rule: dict[str, Any]) -> str:
        return f"Hauflo · {rule.get('name') or self.tx.say('automations_name_default', self._ru(rule))}"

    async def _unique_alias(self, rule_id: str | None, rule: dict[str, Any], data: dict[str, Any]) -> str:
        base = self._alias(rule)
        taken = {r.get('alias') or self._alias(r) for rid, r in data['rules'].items() if rid != rule_id and isinstance(r, dict)}
        try:
            file, _ = await self._read_file()
        except Exception:
            file = []
        taken |= {a.get('alias') for a in file if isinstance(a, dict) and a.get('id') != rule_id}
        alias, n = (base, 1)
        while alias in taken:
            n += 1
            alias = f'{base} ({n})'
        return alias

    def _count(self, key: str, ru: bool, n: int, **ph: Any) -> str:
        if ru:
            form = 'one' if n % 10 == 1 and n % 100 != 11 else 'few' if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else 'many'
        else:
            form = 'one' if n == 1 else 'many'
        return self.tx.say(f'{key}_{form}', ru, n=n, **ph)

    def _d(self, ru: bool, key: str, **ph: Any) -> str:
        return self.tx.say(f'automations_d_{key}', ru, **ph)

    @staticmethod
    def _lower(text: str) -> str:
        return text[:1].lower() + text[1:]

    def _room_name(self, area_id: str) -> str:
        area = ar.async_get(self.hass).async_get_area(area_id)
        return area.name if area else area_id

    def _and(self, ru: bool, items: list[str]) -> str:
        if len(items) < 2:
            return ''.join(items)
        return self._d(ru, 'and', a=', '.join(items[:-1]), b=items[-1])

    def _num(self, v: Any, ru: bool) -> str:
        text = f'{v:g}' if isinstance(v, float) else str(v)
        return text.replace('.', ',') if ru else text

    def _minutes(self, n: int, ru: bool) -> str:
        h, m = divmod(abs(int(n)), 60)
        if not h:
            return self._d(ru, 'min', n=m)
        return self._d(ru, 'hmin', h=h, m=m) if m else self._d(ru, 'h', n=h)

    def _seconds(self, n: Any, ru: bool) -> str:
        n = int(n)
        return self._d(ru, 'sec', n=n) if n < 60 or n % 60 else self._minutes(n // 60, ru)

    def _names(self, eids: Any) -> str:
        return ', '.join((label(self.hass, e) for e in ([eids] if isinstance(eids, str) else eids)))

    def _phone(self, eid: str) -> str:
        ent = er.async_get(self.hass).async_get(eid)
        dev = dr.async_get(self.hass).async_get(ent.device_id) if ent and ent.device_id else None
        return dev.name_by_user or dev.name if dev and (dev.name_by_user or dev.name) else label(self.hass, eid)

    def _days(self, days: list[str], ru: bool) -> str | None:
        if len(days) == len(DAYS):
            return None
        if days == list(DAYS[:5]):
            return self._d(ru, 'days_workdays')
        if days == list(DAYS[5:]):
            return self._d(ru, 'days_weekend')
        return self._d(ru, 'days_some', days=', '.join((self._d(ru, f'wd_{d}') for d in days)))

    def _reading(self, spec: dict[str, Any], ru: bool) -> tuple[str, str]:
        kind = self._d(ru, f"kind_{spec['kind']}")
        st = self.hass.states.get(spec['sensors'][0])
        unit = (st.attributes.get('unit_of_measurement') if st else None) or ''
        if len(spec['sensors']) == 1:
            what = self._d(ru, 'reading_one', kind=kind, sensor=self._names(spec['sensors']))
        else:
            what = self._d(ru, 'reading_many', kind=kind, stat=self.tx.say(f"automations_stat_{spec.get('stat', 'mean')}", ru), sensors=self._names(spec['sensors']))
        return (what, self._d(ru, f"op_{spec['op']}", x=f"{self._num(spec['x'], ru)} {unit}".strip()))

    def _when_text(self, when: dict[str, Any], ru: bool) -> str:
        ev = when['event']
        if ev == 'time':
            return self._d(ru, 'at', times=self._and(ru, list(when.get('times') or [when['at']])))
        if ev == 'alarm_iphone':
            return self._d(ru, 'alarm_iphone')
        if ev == 'value':
            what, how = self._reading(when, ru)
            text = self._d(ru, 'when_value', what=what, op=how)
            return self._d(ru, 'hold', text=text, d=self._minutes(when['for_min'], ru)) if when.get('for_min') else text
        if ev == 'state':
            names = self._names(when['sensors'])
            if len(when['sensors']) > 1:
                names = self._d(ru, 'all_of' if when['to'] == 'off' else 'any_of', names=names)
            text = self._d(ru, f"state_{when['kind']}_{when['to']}", sensors=names)
            return self._d(ru, 'hold', text=text, d=self._minutes(when['for_min'], ru)) if when.get('for_min') else text
        if ev == 'presence':
            return self._d(ru, f"presence_{when['to']}")
        if ev == 'alarm_android':
            phone = self._phone(when['sensor'])
            at, of = (self._d(ru, 'alarm', phone=phone), self._d(ru, 'of_alarm', phone=phone))
        else:
            at, of = (self._d(ru, ev), self._d(ru, f'of_{ev}'))
        if not (off := (when.get('offset') or 0)):
            return at
        return self._d(ru, 'before' if off < 0 else 'after', d=self._minutes(off, ru), of=of)

    def _if_text(self, c: dict[str, Any], ru: bool) -> str:
        if c['if'] == 'between':
            return self._d(ru, 'if_between', a=c['after'], b=c['before'])
        if c['if'] == 'value':
            what, how = self._reading(c, ru)
            return self._d(ru, 'if_value', what=what, op=how)
        return self._d(ru, f"if_{c['if']}")

    def _value_text(self, a: dict[str, Any], spec: dict[str, Any], domain: str, ru: bool) -> str:
        v, kind = (a.get('value'), spec.get('value'))
        if v is None or not kind:
            return ''
        if kind == 'percent':
            return f'{self._num(v, ru)} %'
        if kind == 'seconds':
            return self._seconds(v, ru)
        if kind == 'setpoint':
            return f'{self._num(v, ru)} {self.hass.config.units.temperature_unit}'
        if kind == 'option':
            return self._d(ru, 'option', option=self.tx.option_label(ru, domain, spec.get('field'), str(v)))
        eid = a['target'].get('entity_id')
        st = self.hass.states.get(eid if isinstance(eid, str) else eid[0]) if eid else None
        return f"{self._num(v, ru)} {(st.attributes.get('unit_of_measurement') if st else None) or ''}".strip()

    def _where(self, a: dict[str, Any], ru: bool) -> str:
        t = a['target']
        picked = [t['entity_id']] if isinstance(t.get('entity_id'), str) else list(t.get('entity_id') or [])
        parts: list[str] = []
        if t.get('area_id') or t.get('all'):
            rooms = [t['area_id']] if isinstance(t.get('area_id'), str) else list(t.get('area_id') or [])
            names = sorted({self._room_name(r) for r in rooms}, key=natural_key)
            places = self._d(ru, 'everywhere') if t.get('all') else self._and(ru, names)
            got = self._target(a) or {}
            eids, by_room = (got.get('entity_id'), got.get('area_id') or [])
            by_room = [by_room] if isinstance(by_room, str) else list(by_room)
            if eids != 'all' and (t.get('all') or set(rooms) - set(by_room)):
                took = {e for e in ([eids] if isinstance(eids, str) else eids or []) if e not in picked}
                took |= {e for r in by_room for e in self._members(self._domain(t), r, a['do'], a.get('value'))}
                places = self._d(ru, 'snapshot', places=places, count=self._count('automations_d_devices', ru, len(took)))
            parts.append(self._d(ru, 'where_kind', kind=self._d(ru, f'dom_{self._domain(t)}'), places=places))
        if picked:
            parts.append(self._names(picked))
        return ', '.join(parts)

    def _act_text(self, a: dict[str, Any], ru: bool) -> str:
        a = a if 'target' in a else self._legacy(a)
        where = self._where(a, ru)
        if a['do'] == RAMP:
            text = self._d(ru, 'ramp', value=a['value'], d=self._minutes(a['minutes'], ru), where=where)
        else:
            domain = self._domain(a['target'])
            spec = rl.C('actions_by_domain')[domain][a['do']]
            value = self._value_text(a, spec, domain, ru)
            do = self._lower(self.tx.say(f"rules_do_{a['do']}", ru))
            text = self._d(ru, 'act_value' if value else 'act', do=do, value=value, where=where)
        if (rep := a.get('repeat')):
            text = self._d(ru, 'repeat', act=text, times=self._count('automations_d_times', ru, rep['count']), every=self._seconds(rep['every_s'], ru))
        return text

    def describe(self, rule: dict[str, Any], ru: bool) -> str:
        made = self._d(ru, 'made')
        try:
            when = rule['when']
            head = self._when_text(when, ru)
            if (days := self._days(list(rule['days']), ru)):
                timed = when['event'] in ('time', 'sunrise', 'sunset', 'alarm_android', 'alarm_iphone')
                head = self._d(ru, 'days_at' if timed else 'days_when', days=days, when=head)
            out = [self._d(ru, 'sentence', head=head[:1].upper() + head[1:], acts='; '.join((self._act_text(a, ru) for a in rule['actions'])))]
            if (only := rule.get('only_if')):
                out.append(self._d(ru, 'only_if', conds=self._and(ru, [self._if_text(c, ru) for c in only])))
        except Exception:
            _LOGGER.debug('Hauflo automations: no words for a rule', exc_info=True)
            return made
        return ' '.join([*out, made])

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
                cond['for'] = {'seconds': n * 60 - STATE_ALL_MARGIN_S}
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

    @staticmethod
    def _readings(rule: dict[str, Any]) -> list[dict[str, Any]]:
        return [x for x in [rule['when'], *rule.get('only_if', [])] if x.get('event') == 'value' or x.get('if') == 'value']

    async def _resolve(self, rule: dict[str, Any], data: dict[str, Any]) -> None:
        for spec in self._readings(rule):
            spec['entity'] = spec['sensors'][0] if len(spec['sensors']) == 1 else await self._helper(data, spec)

    async def _helper(self, data: dict[str, Any], spec: dict[str, Any]) -> str:
        helpers = data.setdefault('helpers', {})
        sig = f"{spec['stat']}:{','.join(spec['sensors'])}"
        reg = er.async_get(self.hass)
        if (h := helpers.get(sig)) and self.hass.config_entries.async_get_entry(h['entry_id']) and (eid := reg.async_get_entity_id('sensor', HELPER, h['entry_id'])):
            return eid
        ru = str(self.hass.config.language or '').startswith('ru')
        flow = await self.hass.config_entries.flow.async_init(HELPER, context={'source': 'user'})
        res = await self.hass.config_entries.flow.async_configure(flow['flow_id'], {'name': f"hauflo_{spec['stat']}_{secrets.token_hex(4)}", 'entity_ids': list(spec['sensors']), 'type': spec['stat'], 'round_digits': 1 if spec['kind'] == 'temperature' else 0})
        entry = res.get('result')
        if entry is None:
            _LOGGER.warning('Hauflo automations: no %s helper (%s)', spec['stat'], res.get('errors') or res.get('reason'))
            raise Invalid('helper_failed')
        eid = None
        for _ in range(50):
            if (eid := reg.async_get_entity_id('sensor', HELPER, entry.entry_id)):
                break
            await asyncio.sleep(0.1)
        if eid is None:
            await self.hass.config_entries.async_remove(entry.entry_id)
            raise Invalid('helper_failed')
        title = await self._helper_title(spec, ru)
        self.hass.config_entries.async_update_entry(entry, title=title)
        reg.async_update_entity(eid, name=title)
        helpers[sig] = {'entry_id': entry.entry_id, 'entity_id': eid}
        return eid

    async def _helper_title(self, spec: dict[str, Any], ru: bool) -> str:
        pre = f'component.{DOMAIN}.exceptions.'
        tr: dict[str, str] = {}
        for lang in ('ru', 'en') if ru else ('en',):
            got = await translation.async_get_translations(self.hass, lang, 'exceptions', [DOMAIN])
            tr = {**{k: v for k, v in got.items() if k.startswith(pre)}, **tr}
        say = lambda key: tr.get(f'{pre}{key}.message', key)
        try:
            return say('automations_helper').format(stat=say(f"automations_stat_{spec['stat']}"), n=len(spec['sensors']))
        except (KeyError, IndexError, ValueError):
            return f"Hauflo · {spec['stat']}"

    async def _drop_unused_helpers(self, data: dict[str, Any]) -> None:
        used = {spec.get('entity') for rule in data['rules'].values() for spec in self._readings(rule)}
        for sig, h in list(data.get('helpers', {}).items()):
            if h.get('entity_id') in used:
                continue
            if self.hass.config_entries.async_get_entry(h['entry_id']):
                await self.hass.config_entries.async_remove(h['entry_id'])
            del data['helpers'][sig]

    @staticmethod
    def _plain(text: str) -> list[Any]:
        return yamlfile.parse(text, NotPlain)

    async def _read_file(self) -> tuple[list[dict[str, Any]], str | None]:
        return await yamlfile.async_read(self.hass, self.hass.config.path(AUTOMATIONS), NotPlain)

    async def _write_file(self, data: list[dict[str, Any]], before: str | None, redo: Callable[[list[Any]], list[Any]] | None=None) -> list[Any]:
        return await yamlfile.async_write(self.hass, self.hass.config.path(AUTOMATIONS), self.hass.config.path(BACKUP), KEEP_BACKUPS, data, before, NotPlain, redo)

    def entity_of(self, rule_id: str) -> str | None:
        return er.async_get(self.hass).async_get_entity_id('automation', 'automation', rule_id)

    async def _put(self, rule_id: str, cfg: dict[str, Any] | None) -> None:
        if not _ID.fullmatch(rule_id):
            raise _bad('id')
        if cfg is not None:
            from homeassistant.components.automation.config import async_validate_config_item
            await async_validate_config_item(self.hass, rule_id, cfg)
        async with _FILE_LOCK:
            data, before = await self._read_file()

            def put(rows: list[Any]) -> list[Any]:
                out: list[Any] = []
                done = cfg is None
                for a in rows:
                    if not (isinstance(a, dict) and a.get('id') == rule_id):
                        out.append(a)
                    elif not done:
                        out.append(cfg)
                        done = True
                return out if done else [*out, cfg]
            await self._write_file(put(data), before, put)
        if cfg is not None:
            await self.hass.services.async_call('automation', 'reload', {'id': rule_id}, blocking=True)
        elif (eid := self.entity_of(rule_id)):
            er.async_get(self.hass).async_remove(eid)

    async def _load(self) -> dict[str, Any]:
        await self.texts()
        data = await self.store.async_load()
        data = data if isinstance(data, dict) and isinstance(data.get('rules'), dict) else {'rules': {}}
        if (moved := [rid for rid, rule in data['rules'].items() if isinstance(rule, dict) and self._upgrade(rid, rule)]):
            data[MOVED] = moved
        return data

    async def _save(self, data: dict[str, Any]) -> None:
        data.pop(MOVED, None)
        await self.store.async_save(data)

    def _upgrade(self, rule_id: str, rule: dict[str, Any]) -> bool:
        w = rule.get('when') if isinstance(rule.get('when'), dict) else {}
        acts = rule.get('actions') if isinstance(rule.get('actions'), list) else []
        if not ('at' in w or any((isinstance(a, dict) and 'target' not in a for a in acts))):
            return False
        try:
            if not rule.get('hash'):
                rule['hash'] = _digest(self.automation(rule_id, rule))
            new = [a if 'target' in a else self._legacy(a) for a in acts]
        except Exception:
            return False
        if 'at' in w:
            w['times'] = [w.pop('at')]
        rule['actions'] = new
        return True

    async def _migrate(self, data: dict[str, Any]) -> None:
        moved = data.get(MOVED) or []
        try:
            file, _ = await self._read_file()
        except Exception:
            file = []
        there = {a.get('id') for a in file if isinstance(a, dict)}
        edited = await self.changed(data)
        for rule_id in moved:
            rule = data['rules'][rule_id]
            if rule_id not in there or rule_id in edited:
                continue
            try:
                cfg = self.automation(rule_id, rule)
                if _digest(cfg) != rule.get('hash'):
                    await self._put(rule_id, cfg)
                    rule['hash'] = _digest(cfg)
            except Exception as err:
                _LOGGER.warning('Hauflo automations: %s kept as it was (%s)', rule_id, err)
        await self._save(data)

    async def changed(self, data: dict[str, Any]) -> set[str]:
        try:
            file, _ = await self._read_file()
        except Exception:
            return set()
        now = {a.get('id'): a for a in file if isinstance(a, dict)}
        out = set()
        for rule_id, rule in data['rules'].items():
            if (cur := now.get(rule_id)) is None:
                continue
            want = rule.get('hash') or _digest(self.automation(rule_id, rule))
            if _digest(cur) != want:
                out.add(rule_id)
        return out

    async def save(self, raw: Any, user: str | None, force: bool=False, lang: str | None=None) -> dict[str, Any]:
        async with self.lock:
            data = await self._load()
            rule_id = raw.get('id') if isinstance(raw, dict) else None
            if rule_id is not None:
                if not isinstance(rule_id, str) or not _ID.fullmatch(rule_id):
                    raise _bad('id')
                if rule_id not in data['rules']:
                    raise KeyError(rule_id)
            elif len(data['rules']) >= MAX_RULES:
                raise Invalid('bad_rules_many', n=MAX_RULES)
            if rule_id is not None and (not force) and (rule_id in await self.changed(data)):
                raise Changed(rule_id)
            rule = self.validate(raw, data['rules'].get(rule_id) if rule_id else None)
            rule_id = rule_id or PREFIX + secrets.token_hex(6)
            rule['by'], rule['at'] = (user, time.time())
            rule['lang'] = lang if lang in rl.LANGS else rl.LANGS[0] if self._ru({}) else rl.LANGS[1]
            rule['alias'] = await self._unique_alias(rule_id, rule, data)
            await self._resolve(rule, data)
            cfg = self.automation(rule_id, rule)
            try:
                await self._put(rule_id, cfg)
            except Exception:
                await self._drop_unused_helpers(data)
                await self._save(data)
                raise
            rule['hash'] = _digest(cfg)
            data['rules'][rule_id] = rule
            await self._drop_unused_helpers(data)
            await self._save(data)
        await self._apply_enabled(rule_id, rule['enabled'])
        out = {'id': rule_id, **{k: v for k, v in rule.items() if k != 'hash'}}
        if rule['when'].get('event') in ('sunrise', 'sunset') and (not self.location()['set']):
            out['warning'] = 'no_location'
        return out

    async def _apply_enabled(self, rule_id: str, enabled: bool) -> None:
        for _ in range(20):
            if (eid := self.entity_of(rule_id)):
                await self.hass.services.async_call('automation', 'turn_on' if enabled else 'turn_off', {'entity_id': eid}, blocking=True)
                return
            await asyncio.sleep(0.1)

    async def set_enabled(self, rule_id: Any, enabled: Any) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise _bad('enabled')
        async with self.lock:
            data = await self._load()
            rule = self._own(data, rule_id)
            rule['enabled'] = enabled
            await self._save(data)
        await self._apply_enabled(rule_id, enabled)
        return {'id': rule_id, **{k: v for k, v in rule.items() if k != 'hash'}}

    @staticmethod
    def _own(data: dict[str, Any], rule_id: Any) -> dict[str, Any]:
        if not isinstance(rule_id, str) or not _ID.fullmatch(rule_id):
            raise _bad('id')
        if rule_id not in data['rules']:
            raise KeyError(rule_id)
        return data['rules'][rule_id]

    async def delete(self, rule_id: Any) -> None:
        async with self.lock:
            data = await self._load()
            self._own(data, rule_id)
            await self._put(rule_id, None)
            del data['rules'][rule_id]
            await self._drop_unused_helpers(data)
            await self._save(data)

    async def run(self, rule_id: Any) -> dict[str, Any]:
        data = await self._load()
        self._own(data, rule_id)
        eid = self.entity_of(rule_id)
        if eid is None or self.hass.states.get(eid) is None:
            raise LookupError('automations_not_in_ha')
        await self.hass.services.async_call('automation', 'trigger', {'entity_id': eid, 'skip_condition': True, 'variables': {RUN_VAR: True}}, blocking=False)
        return {'id': rule_id, 'entity_id': eid}

    def location(self) -> dict[str, Any]:
        c = self.hass.config
        lat, lon = (round(float(c.latitude), 5), round(float(c.longitude), 5))
        from homeassistant.core_config import ConfigSource
        return {'latitude': c.latitude, 'longitude': c.longitude, 'elevation': c.elevation, 'set': (lat, lon) not in _UNSET, 'yaml': c.config_source == ConfigSource.YAML, 'time_zone': c.time_zone}

    async def set_location(self, body: dict[str, Any]) -> dict[str, Any]:
        from homeassistant.core_config import ConfigSource
        if self.hass.config.config_source == ConfigSource.YAML:
            raise PermissionError('automations_yaml_location')
        if set(body) - {'op', 'lang', 'latitude', 'longitude', 'elevation'}:
            raise _bad('location')
        lat = _float(body.get('latitude'), -90, 90, 'latitude')
        lon = _float(body.get('longitude'), -180, 180, 'longitude')
        if (round(lat, 5), round(lon, 5)) in _UNSET:
            raise Invalid('bad_place')
        kwargs: dict[str, Any] = {'latitude': round(lat, 6), 'longitude': round(lon, 6)}
        if body.get('elevation') is not None:
            kwargs['elevation'] = int(round(_float(body['elevation'], -500, 9000, 'elevation')))
        await self.hass.config.async_update(**kwargs)
        return self.location()

    async def set_time_zone(self, body: dict[str, Any]) -> dict[str, Any]:
        from homeassistant.core_config import ConfigSource
        if self.hass.config.config_source == ConfigSource.YAML:
            raise PermissionError('automations_yaml_time_zone')
        tz = body.get('time_zone')
        if set(body) - {'op', 'lang', 'time_zone'} or not isinstance(tz, str) or (not re.fullmatch('[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){0,2}', tz)) or (dt_util.get_time_zone(tz) is None):
            raise Invalid('bad_time_zone')
        await self.hass.config.async_update(time_zone=tz)
        return self.location()

    async def detect(self) -> dict[str, Any]:
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        from homeassistant.util.location import async_detect_location_info
        info = await async_detect_location_info(async_get_clientsession(self.hass))
        if info is None or info.latitude is None or info.longitude is None:
            raise LookupError('automations_no_detect')
        return {'latitude': info.latitude, 'longitude': info.longitude, 'city': info.city, 'region': getattr(info, 'region_name', None), 'country': info.country_code, 'time_zone': info.time_zone}

    def days(self, sun: bool=True) -> list[dict[str, Any]]:
        import astral
        import astral.sun
        tz = dt_util.get_time_zone(self.hass.config.time_zone) or dt_util.DEFAULT_TIME_ZONE
        obs = astral.Observer(self.hass.config.latitude, self.hass.config.longitude, self.hass.config.elevation)
        today = dt_util.now(tz).date()
        out = []
        for n in range(8):
            day = today + dt.timedelta(days=n)
            row: dict[str, Any] = {'date': day.isoformat(), 'wd': DAYS[day.weekday()], 'midnight': dt.datetime.combine(day, dt.time(), tz).isoformat()}
            for event in ('sunrise', 'sunset'):
                if not sun:
                    row[event] = None
                    continue
                try:
                    row[event] = getattr(astral.sun, event)(obs, date=day, tzinfo=tz).isoformat()
                except ValueError:
                    row[event] = None
            out.append(row)
        return out

    async def answer(self) -> dict[str, Any]:
        async with self.lock:
            data = await self._load()
            if data.get(MOVED):
                await self._migrate(data)
        edited = await self.changed(data)
        rules = []
        for rule_id, rule in data['rules'].items():
            eid = self.entity_of(rule_id)
            s = self.hass.states.get(eid) if eid else None
            rules.append({'id': rule_id, **{k: v for k, v in rule.items() if k not in ('by', 'hash')}, 'entity_id': eid, 'state': s.state if s else 'missing', 'enabled': s.state == 'on' if s else rule['enabled'], 'changed': rule_id in edited})
        rules.sort(key=lambda r: r.get('at') or 0)
        targets = self.targets()
        out = {'location': self.location(), 'days': self.days(self.location()['set']), 'rules': rules, 'targets': targets, 'max_offset': MAX_OFFSET}
        out['features'] = {'rules_v2': True}
        out['limits'] = {'times': rl.C('schedule', 'moments'), 'repeat': {k: REPEAT[k] for k in ('count', 'every_s', 'acts')}, 'targets': MAX_SET, 'actions': MAX_ACTIONS, 'ramps': MAX_RAMPS, 'pulse_s': rl.C('actions', 'pulse_s')}
        out['recipes'] = recipes.resolve(recipes.DATA, targets, self.validate)
        out['recipe_groups'] = recipes.DATA['groups']
        if (h := self.habits()) is not None:
            out['habits_on'] = h.enabled
            out['habits'] = h.suggestions(self, data) if h.enabled else []
        return out

class AutomationsView(HomeAssistantView):
    url = '/api/hauflo/automations'
    name = 'api:hauflo:automations'
    requires_auth = True

    def __init__(self, auto_of: Callable[[], Automations | None]) -> None:
        self.auto_of = auto_of

    async def get(self, request):
        engine = self.auto_of()
        if engine is None:
            return self.json_message('Hauflo agent not set up', 503)
        return self.json(await engine.answer())

    async def post(self, request):
        engine = self.auto_of()
        if engine is None:
            return self.json_message('Hauflo agent not set up', 503)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict):
            return self.json_message('invalid JSON', 400)
        user = request['hass_user']
        op = body.get('op')
        ru = wants_ru(body, engine.hass.config.language)
        if op in ('location', 'detect') and (not (user is not None and user.is_admin)):
            msg = 'Место объекта задаёт администратор Home Assistant.' if ru else "The site's location is set by the Home Assistant administrator."
            return self.json({'message': msg, 'code': 'admin_only'}, 403)
        if op in ('save', 'enable', 'delete') and (no := (await refuse(request))) is not None:
            return no
        try:
            if op == 'save':
                saved = await engine.save(body.get('rule'), user.name if user else None, body.get('force') is True, rl.LANGS[0] if ru else rl.LANGS[1])
                if saved.get('warning') == 'no_location':
                    saved['warning_text'] = NO_LOCATION_RU if ru else NO_LOCATION_EN
                return self.json(saved)
            if op == 'enable':
                return self.json(await engine.set_enabled(body.get('id'), body.get('enabled')))
            if op == 'delete':
                await engine.delete(body.get('id'))
                return self.json({'deleted': body.get('id')})
            if op == 'run':
                return self.json(await engine.run(body.get('id')))
            if op == 'location':
                return self.json(await engine.set_location(body))
            if op == 'detect':
                return self.json(await engine.detect())
            if op in ('dismiss_habit', 'habits'):
                if (no := (await refuse(request))) is not None:
                    return no
                if (h := engine.habits()) is None:
                    return self.json_message('habits not running', 503)
                if op == 'habits':
                    if not isinstance(body.get('enabled'), bool):
                        raise _bad('enabled')
                    await h.set_enabled(body['enabled'])
                else:
                    await h.dismiss(body.get('key'))
                return self.json(await engine.answer())
            if op == 'timezone':
                if not (user and user.is_admin):
                    return self.json_message('admin only', 403)
                return self.json(await engine.set_time_zone(body))
        except rl.Invalid as err:
            await engine.texts()
            out = {'message': engine.message(err, ru), 'code': err.code, 'key': err.key}
            if err.code == 'bad_request':
                out['field'] = err.ph.get('what')
            return self.json(out, 400)
        except NotPlain as err:
            _LOGGER.warning('Hauflo automations: automations.yaml left as it is (%s)', err)
            return self.json({'message': NotPlain.RU if ru else NotPlain.EN, 'code': 'not_plain'}, 409)
        except KeyError:
            return self.json(await self._said(engine, 'no_rule', ru), 404)
        except Changed:
            return self.json(await self._said(engine, 'changed', ru), 409)
        except (LookupError, PermissionError) as err:
            return self.json(await self._said(engine, str(err).removeprefix('automations_'), ru), 409)
        except Exception as err:
            _LOGGER.warning('Hauflo sun: %s failed: %s', op, err)
            return self.json_message(f'Home Assistant: {err}', 500)
        return self.json_message('op: save, enable, delete, run, detect, location, timezone, dismiss_habit or habits', 400)

    @staticmethod
    async def _said(engine: Automations, code: str, ru: bool) -> dict[str, Any]:
        tx = await engine.texts()
        return {'message': tx.say(f'automations_{code}', ru), 'code': code}
