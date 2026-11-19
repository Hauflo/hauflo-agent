"""Names, ids and texts shared by the cloud-independent alarm automations."""
from __future__ import annotations
import functools
import hashlib
import json
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import translation
from homeassistant.util import slugify
from ..const import ACK_ACTION, ACK_PREFIX, ALARM_PREFIX, CHATTER_PREFIX, DOMAIN, OPEN_PREFIX, RUN_ACTION
from ..notify.common import default_hold, hold_value, list_max, native_back, native_hold, outputs_cfg
from ..notify.push import push_data
PREFIX = 'hauflo_independent_'
hold_cfg = functools.cache(lambda: native_hold())

def alarm_hold(rule: dict[str, Any]) -> int:
    """Seconds a source stays in alarm before the rule's «alarm» (its `for:`): the rule's hold (notify.hold_value / default_hold — 0 for life safety, a minute for doors / motion / classless signals, two for levels), never under HOLD (a flicker of a few seconds says nothing even for a leak)."""
    hold = rule.get('hold')
    if not isinstance(hold, int) or isinstance(hold, bool):
        own = hold_value(rule.get('hold_s'))
        hold = own if own is not None else default_hold(None, str(rule.get('kind') or ''), list(rule.get('sources') or []), rule.get('thresholds') or {})
    return max(hold_cfg(), hold)

def alarm_groups(rule: dict[str, Any], sources: list[str]) -> list[tuple[list[str], int]]:
    """The sources by their `for:` — the rule's (alarm_hold); one that holds its time itself (a combination's T — notify.own_holds, the rule's `holds`) its own, never under HOLD."""
    own = rule.get('holds') or {}
    by: dict[int, list[str]] = {}
    for eid in sources:
        by.setdefault(max(hold_cfg(), int(own[eid])) if eid in own else alarm_hold(rule), []).append(eid)
    return [(eids, s) for s, eids in by.items()]

def back_after(rule: dict[str, Any]) -> int:
    """Seconds a source stays in normal before the rule's «back» (its chatter window, at least HOLD — ISA-18.2 / Nagios flap hysteresis on the return; HA's own `for:`)."""
    return native_back(rule)
NO_DATA = ['unavailable', 'unknown']
ICON = 'mdi:bell-check'
OPEN_ICON = 'mdi:bell-ring'
_TEXT = 'alarm_native_'

def _name(e: str) -> str:
    return f"(state_attr({e}, 'friendly_name') or ({e}.split('.')[1] | replace('_', ' ') | capitalize))"
names_cfg = functools.cache(lambda: '{%- set ns = namespace(o=[]) -%}{%- for e in alarmed[:' + str(list_max()) + '] -%}{%- set a = area_name(e) -%}{%- set ns.o = ns.o + [' + _name('e') + " ~ (' · ' ~ a if a else '')] -%}{%- endfor -%}{{ ns.o | join(', ') }}{% if alarmed | count > " + str(list_max()) + ' %} +{{ alarmed | count - ' + str(list_max()) + ' }}{% endif %}')
level_names = functools.cache(lambda: '{%- set ns = namespace(o=[]) -%}{%- for e in alarmed[:' + str(list_max()) + "] -%}{%- set a = area_name(e) -%}{%- set u = state_attr(e, 'unit_of_measurement') -%}{%- set ns.o = ns.o + [" + _name('e') + " ~ (' · ' ~ a if a else '') ~ ((' (' ~ states(e) ~ ((' ' ~ u) if u else '') ~ ')') if e in levels else '')] -%}{%- endfor -%}{{ ns.o | join(', ') }}{% if alarmed | count > " + str(list_max()) + ' %} +{{ alarmed | count - ' + str(list_max()) + ' }}{% endif %}')
ONE = '{{ ' + _name('trigger.entity_id') + " ~ ((' · ' ~ area_name(trigger.entity_id)) if area_name(trigger.entity_id) else '') }}"
ALARMED = "{{ expand(sources) | selectattr('state', 'in', alarm_states) | map(attribute='entity_id') | list }}"
LEVELS_ON = '{%- set ns = namespace(o=[]) -%}{%- for e in sources -%}{%- set th = levels.get(e) -%}{%- set s = states(e) -%}{%- if th -%}{%- if s | is_number and ((s | float <= th.below + th.step) if th.below is defined else (s | float >= th.above - th.step)) -%}{%- set ns.o = ns.o + [e] -%}{%- endif -%}{%- elif s in alarm_states -%}{%- set ns.o = ns.o + [e] -%}{%- endif -%}{%- endfor -%}'
LEVELS_ALARMED = LEVELS_ON + '{{ ns.o }}'

def who_template(uid: str) -> str:
    """Who pressed «Принято»: the HA person of the user (a phone's push action carries its user, a toggle in HA too)."""
    return '{%- set uid = ' + uid + " -%}{{ (states.person | selectattr('attributes.user_id', 'eq', uid) | map(attribute='name') | list | first) if uid else '' }}"

def slug_of(rule_id: str) -> str:
    return f"{slugify(rule_id)[:40] or 'rule'}_{hashlib.sha1(rule_id.encode()).hexdigest()[:6]}"

def ids(rule_id: str) -> dict[str, str]:
    """The rule's automation id, its helpers' object ids and its push buttons (const.py prefixes)."""
    slug = slug_of(rule_id)
    return {'automation': ALARM_PREFIX + slug, 'helper': ACK_PREFIX + slug, 'open': OPEN_PREFIX + slug, 'chatter': CHATTER_PREFIX + slug, 'ack': ACK_ACTION + slug.upper(), 'run': RUN_ACTION + slug.upper()}

def of(automation: str, prefix: str) -> str:
    """A helper's object id of a rule's automation (records before 0.31 don't keep the «open» one)."""
    return prefix + automation.removeprefix(ALARM_PREFIX)

def automation_id(rule_id: str) -> str:
    return ids(rule_id)['automation']

def lit(text: str) -> str:
    """A person's text (a rule title, a device name) inside a field HA renders as a template: said as it is."""
    if not any((ch in text for ch in '{}%#')):
        return text
    body = json.dumps(text, ensure_ascii=False)[1:-1]
    for ch in '{}%#':
        body = body.replace(ch, f'\\u{ord(ch):04x}')
    return '{{ "' + body + '" }}'

async def texts(hass: HomeAssistant, english: bool) -> dict[str, str]:
    """The automations' words in the house's language (translations, exceptions.alarm_native_*)."""
    pre = f'component.{DOMAIN}.exceptions.{_TEXT}'
    got = await translation.async_get_translations(hass, 'en' if english else 'ru', 'exceptions', [DOMAIN])
    out = {k[len(pre):].removesuffix('.message'): v for k, v in got.items() if k.startswith(pre)}
    if 'alias' not in out:
        raise RuntimeError('Hauflo: alarm texts missing in translations')
    return out

def say(t: dict[str, str], key: str, **parts: str) -> str:
    return t[key].format(**parts)

def push(phones: list[dict[str, Any]], title: str | None, message: str, **data: Any) -> list[dict[str, Any]]:
    """Every phone at once, each on its own (``continue_on_error``): a slow or failing one never holds the others."""
    if not phones:
        return []
    return [{'parallel': [{'action': f"notify.{p['service']}", 'continue_on_error': True, 'data': {**({'title': title} if title else {}), 'message': message, 'data': push_data(p.get('os'), **data)}} for p in phones]}]

def helper_action(entity_id: str, on: bool) -> dict[str, Any]:
    return {'action': f"input_boolean.turn_{('on' if on else 'off')}", 'target': {'entity_id': entity_id}}

def state_is(entity_id: str, state: str) -> dict[str, Any]:
    return {'condition': 'state', 'entity_id': entity_id, 'state': state}

def outputs(rule: dict[str, Any], when: str) -> list[dict[str, Any]]:
    by: dict[str, list[str]] = {}
    for o in rule.get('outputs') or []:
        domain = o['entity_id'].partition('.')[0]
        if o.get(when) and outputs_cfg().get(domain, {}).get(when):
            by.setdefault(domain, []).append(o['entity_id'])
    return [{'action': f'{d}.{outputs_cfg()[d][when]}', 'continue_on_error': True, 'target': {'entity_id': eids}} for d, eids in by.items()]

def gap(seconds: int) -> str:
    """The source was out of alarm (in normal or without data) at least this long before this alarm."""
    return '(trigger.to_state.last_changed - trigger.from_state.last_changed).total_seconds() >= ' + str(seconds)
KIND = (('input_boolean', ACK_PREFIX), ('input_boolean', OPEN_PREFIX), ('input_text', CHATTER_PREFIX))
HELPERS_FEATURE = 'alarm helpers'
SERVICE = 'remove_alarms'
NOTE = f'{DOMAIN}_alarms_kept'
