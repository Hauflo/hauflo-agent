"""Ids, names and helpers shared by the alarm rule's parts."""
from __future__ import annotations
import functools
import json
import logging
from typing import Any
from ..const import ALARM_PREFIX, NATIVE_STORE, PUSH_SCRIPT, RUN_PREFIX
from ..notify.common import LINK_KINDS, list_max, t
from ..scenes.common import KEEP_BACKUPS as SCENE_BACKUPS
_LOGGER = logging.getLogger(__name__)
form = functools.cache(lambda: int(t()['native_form']))
SCRIPT = PUSH_SCRIPT
SHARED_STORE = NATIVE_STORE + '_shared'
ACCEPT_ID = ALARM_PREFIX + 'accept'
BUTTON_ID = ALARM_PREFIX + 'button'
SHARED_IDS = (ACCEPT_ID, BUTTON_ID)
BACK = '_back'
SCRIPTS = 'scripts.yaml'
SCRIPTS_BACKUP = SCRIPTS + '.hauflo-bak-'
KEEP_BACKUPS = SCENE_BACKUPS
fast_rounds = functools.cache(lambda: int(t()['native_fast_rounds']))
NO_DATA = ['unavailable', 'unknown']
GROUP_ICON = 'mdi:alarm-light-outline'

class NotPlainScripts(Exception):
    """scripts.yaml isn't as HA's script editor writes it (hand formatting, !include, comments): never rewritten."""

def run_key(slug: str) -> str:
    return RUN_PREFIX + slug

def back_id(aid: str) -> str:
    return aid + BACK

def grouped(rule: dict[str, Any]) -> bool:
    """HA's group helper fits: every source a binary sensor, alarm = «on» (a group is on when any member is)."""
    return not rule.get('thresholds') and rule.get('kind') not in LINK_KINDS and (list(rule.get('alarm_states') or []) == ['on']) and all((str(e).startswith('binary_sensor.') for e in rule['sources']))

def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)

def helper_state(rule: dict[str, Any]) -> str:
    """The template helper's state for a rule a group doesn't fit: on while a source is in alarm — a level until it is
    back past its threshold by the step (battery < 20 → alarm, > 25 → back: the open alarm's way, notify.in_alarm)."""
    levels: dict[str, dict[str, float]] = rule.get('thresholds') or {}
    states = list(rule.get('alarm_states') or [])
    parts: list[str] = []
    for e in rule['sources']:
        th = levels.get(e)
        if th and 'below' in th:
            edge = th['below'] + th['step']
            parts.append(f"states('{e}') | float({_num(edge + 1)}) <= {_num(edge)}")
        elif th:
            edge = th['above'] - th['step']
            parts.append(f"states('{e}') | float({_num(edge - 1)}) >= {_num(edge)}")
        elif len(states) == 1:
            parts.append(f"is_state('{e}', '{states[0]}')")
        else:
            parts.append(f"states('{e}') in {json.dumps(states, ensure_ascii=False)}")
    return '{{ ' + ' or '.join(parts or ['false']) + ' }}'

def _name(e: str) -> str:
    return f"(state_attr({e}, 'friendly_name') or ({e}.split('.')[1] | replace('_', ' ') | capitalize))"
names_cfg = functools.cache(lambda: "{%- set ns = namespace(o=[]) -%}{%- for e in (sources | default([]) or state_attr(group, 'entity_id') or []) -%}{%- set th = (levels | default({})).get(e) -%}{%- set s = states(e) -%}{%- if (th and s | is_number and ((s | float < th.below) if th.below is defined else (s | float > th.above))) or (not th and s in (alarm_states | default(['on']))) -%}{%- set a = area_name(e) -%}{%- set u = state_attr(e, 'unit_of_measurement') -%}{%- set ns.o = ns.o + [" + _name('e') + " ~ (' · ' ~ a if a else '') ~ ((' (' ~ s ~ ((' ' ~ u) if u else '') ~ ')') if th else '')] -%}{%- endif -%}{%- endfor -%}{{ ns.o[:" + str(list_max()) + "] | join(', ') }}{% if ns.o | count > " + str(list_max()) + ' %} +{{ ns.o | count - ' + str(list_max()) + ' }}{% endif %}')

def jinja(text: str, **parts: str) -> str:
    """A translation with its {placeholders} as Jinja expressions."""
    for k, v in parts.items():
        text = text.replace('{' + k + '}', '{{ ' + v + ' }}')
    return text
