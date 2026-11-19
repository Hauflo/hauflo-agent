"""Automation limits, errors and value checks."""
from __future__ import annotations
import asyncio
import functools
import hashlib
import json
import math
import re
from typing import Any
from ..rules import actions as rules_actions, spec as rules_spec
from ..tuning import tuning
STORE_KEY = 'hauflo.automations'
PREFIX = 'hauflo_auto_'
ALARM_EVENT = 'hauflo_alarm_'
RUN_VAR = 'hauflo_check'
AUTOMATIONS = 'automations.yaml'
EVENTS = ('sunrise', 'sunset', 'time', 'alarm_android', 'alarm_iphone', 'value', 'presence', 'state')
VALUE_KINDS = ('temperature', 'humidity', 'carbon_dioxide', 'illuminance')
STATS = ('mean', 'min', 'max')
OPS = ('above', 'below')
_t = functools.cache(lambda: tuning()['automations'])
max_sensors = functools.cache(lambda: _t()['max_sensors'])
max_for_min = functools.cache(lambda: _t()['max_for_min'])
state_classes = functools.cache(lambda: {c: kind for kind, classes in _t()['state_classes'].items() for c in classes})
MEDIA = 'media'
state_kinds = functools.cache(lambda: (*_t()['state_classes'], MEDIA))
TOS = ('on', 'off')
state_all_margin_s = functools.cache(lambda: _t()['state_all_margin_s'])
IFS = ('dark', 'light', 'between', 'home', 'away', 'value')
max_only_if = functools.cache(lambda: _t()['max_only_if'])
max_set = functools.cache(lambda: _t()['max_set'])
HELPER = 'min_max'
DAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
MAX_OFFSET = 120
MAX_RULES = 50
MAX_ACTIONS = 20
max_ramp_min = functools.cache(lambda: _t()['max_ramp_min'])
RAMP_STEPS = 12
FROM = re.compile('[a-z0-9_]{1,40}')
ID = re.compile('hauflo_auto_[0-9a-f]{12}')
ALARM_CODE = re.compile('hauflo_alarm_[0-9a-f]{6}')
TIME = re.compile('([01]\\d|2[0-3]):([0-5]\\d)')
UNSET = ((0.0, 0.0), (32.87336, -117.22743))
NO_LOCATION_RU = 'Место объекта не задано — рассвет и закат посчитаются неправильно. Укажите, где он.'
NO_LOCATION_EN = "The site's location isn't set — sunrise and sunset will be wrong. Set where it is."
client_domains = functools.cache(lambda: tuple(rules_spec.C('actions', 'client_domains')))
client_acts_cfg = functools.cache(lambda: frozenset(rules_spec.C('actions', 'client_acts')))
group_domains = functools.cache(lambda: tuple(rules_spec.C('actions', 'group_domains')))
repeat_cfg = functools.cache(lambda: rules_spec.C('actions', 'repeat'))
RAMP = 'ramp'
max_ramps = functools.cache(lambda: _t()['max_ramps'])
script_never = functools.cache(lambda: set(_t()['script_never_domains']))
LEGACY_SET = ('light', 'cover', 'scene')
LEGACY_MODES = ('heat', 'cool', 'off')
LEGACY_DO = {'scene': {'on': 'scene'}, 'climate': {'temp': 'setpoint'}}
COARSE = {'brightness': 'on', RAMP: 'on', 'pulse': 'on', 'scene': 'on', 'setpoint': 'temp'}
MOVED = '_moved'

def client_acts(domain: str) -> dict[str, dict[str, Any]]:
    """The canon's client actions of a domain (none for one a client's rule never touches)."""
    if domain not in client_domains():
        return {}
    return {do: spec for do, spec in rules_spec.C('actions_by_domain').get(domain, {}).items() if do in client_acts_cfg()}
_h = functools.cache(lambda: tuning()['habits'])
cover_classes = functools.cache(lambda: set(_h()['cover_classes']))
cover_never = functools.cache(lambda: set(_h()['cover_never']))
scene_platforms = functools.cache(lambda: set(_h()['scene_platforms']))
climate_step = functools.cache(lambda: _h()['climate_step'])
BACKUP = AUTOMATIONS + '.hauflo-bak-'
KEEP_BACKUPS = 3
FILE_LOCK = asyncio.Lock()

class Invalid(rules_actions.Invalid):
    """A request the agent refuses (400): `code` for the app, its text in translations (automations_<code>, or the canon's own rules_… key of an action it refused), `ph` the placeholders — said in the request's language (Automations.message)."""

    def __init__(self, code: str, key: str | None=None, **ph: Any) -> None:
        super().__init__(code, key or f'automations_{code}', **ph)

def bad(what: str) -> Invalid:
    return Invalid('bad_request', what=what)

class NotPlain(RuntimeError):
    RU = 'Hauflo не меняет automations.yaml: в нём комментарии, !include / !secret или ручное оформление, которые потерялись бы при перезаписи. Правила Hauflo на этом объекте добавляет интегратор, или он приводит файл к виду, в каком его сохраняет редактор автоматизаций Home Assistant.'
    EN = "Hauflo doesn't change automations.yaml: it has comments, !include / !secret or hand formatting a rewrite would lose. At this site the integrator adds Hauflo's rules, or saves the file the way Home Assistant's automation editor writes it."

class Changed(Exception):
    """The automation was edited in HA's own editor: Hauflo never writes over it unasked (409)."""

def digest(cfg: Any) -> str:
    """What an automation does — not its description or initial_state (written by older agents)."""
    if not isinstance(cfg, dict):
        return ''
    body = {k: v for k, v in cfg.items() if k not in ('description', 'initial_state')}
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

def bounded_int(value: Any, lo: int, hi: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or (not lo <= value <= hi):
        raise Invalid('bad_range', field=what, lo=lo, hi=hi)
    return value

def bounded_float(value: Any, lo: float, hi: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (not math.isfinite(value)) or (not lo <= value <= hi):
        raise Invalid('bad_range', field=what, lo=lo, hi=hi)
    return float(value)

def clean_name(value: Any) -> str:
    if value is None:
        return ''
    if not isinstance(value, str):
        raise bad('name')
    return ' '.join(''.join((c for c in value if c.isprintable())).split())[:80]

def offset_text(minutes: int) -> str:
    sign = '-' if minutes < 0 else ''
    m = abs(minutes)
    return f'{sign}{m // 60:02d}:{m % 60:02d}:00'
