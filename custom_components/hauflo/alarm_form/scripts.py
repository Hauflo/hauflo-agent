"""The push script of an alarm rule and the scripts file."""
from __future__ import annotations
from typing import Any
from homeassistant.core import HomeAssistant
from .. import yamlfile
from ..notify.push import push_data
from .automations import digest
from .common import KEEP_BACKUPS, SCRIPTS, SCRIPTS_BACKUP, NotPlainScripts, jinja, names_cfg

def push_script(t: dict[str, str]) -> dict[str, Any]:
    """``script.hauflo_push`` — one push of an alarm to the rule's phones, every phone on its own (a failing one never
    holds the others). What it says by ``say``: alarm (repeat ≥ 2 — «никто не подтвердил») / repeat / back / accepted /
    done; HA's notice with the alarm's tag made on the alarm and gone on «Принято» / back."""
    crit = {**push_data('android', tag='', critical=True), **push_data('ios', tag='', critical=True)}
    quiet = {**push_data('android', tag='', quiet=True), **push_data('ios', tag='', quiet=True)}
    crit.pop('tag')
    quiet.pop('tag')
    fields = {'rule': {'name': t['f_rule'], 'required': True, 'selector': {'text': {}}}, 'say': {'name': t['f_say'], 'required': True, 'default': 'alarm', 'selector': {'select': {'options': ['alarm', 'repeat', 'back', 'accepted', 'done']}}}, 'round': {'name': t['f_round'], 'default': 1, 'selector': {'number': {'min': 1, 'max': 1000}}}, 'critical': {'name': t['f_critical'], 'default': False, 'selector': {'boolean': {}}}, 'quiet': {'name': t['f_quiet'], 'default': False, 'selector': {'boolean': {}}}, 'tag': {'name': t['f_tag'], 'required': True, 'selector': {'text': {}}}, 'targets': {'name': t['f_targets'], 'required': True, 'selector': {'object': {}}}, 'group': {'name': t['f_group'], 'selector': {'entity': {'domain': 'binary_sensor'}}}, 'buttons': {'name': t['f_buttons'], 'selector': {'object': {}}}, 'button': {'name': t['f_button'], 'selector': {'text': {}}}, 'note': {'name': t['f_note'], 'selector': {'select': {'options': ['create', 'dismiss']}}}, 'sources': {'name': t['f_sources'], 'selector': {'entity': {'multiple': True}}}, 'alarm_states': {'name': t['f_states'], 'selector': {'object': {}}}, 'levels': {'name': t['f_levels'], 'selector': {'object': {}}}}
    title = "{%- if say == 'back' -%}" + jinja(t['back_title'], title='rule') + '{%- else -%}' + jinja(t['title'], title='rule') + '{%- endif -%}'
    names = '(names or rule)'
    message = "{%- if say == 'back' -%}" + t['back_all'] + "{%- elif say == 'accepted' -%}" + t['accepted'] + "{%- elif say == 'done' -%}" + jinja(t['done'], button="button | default('')") + "{%- elif say == 'repeat' or (round | int(1)) > 1 -%}" + jinja(t['repeat'], names=names) + '{%- else -%}' + jinja(t['alarm'], names=names) + '{%- endif -%}'
    data = "{%- set d = {'tag': tag} -%}{%- if buttons | default([]) -%}{%- set d = dict(d, actions=buttons) -%}{%- endif -%}{%- if critical -%}{%- set d = dict(d, **critical_data) -%}{%- elif quiet or say in ['accepted', 'done'] -%}{%- set d = dict(d, **quiet_data) -%}{%- endif -%}{{ d }}"
    return {'alias': t['script_alias'], 'description': t['script_description'], 'icon': 'mdi:bell-ring', 'mode': 'parallel', 'max': 100, 'fields': fields, 'sequence': [{'variables': {'round': '{{ round | default(1) | int(1) }}', 'critical': '{{ critical | default(false) }}', 'quiet': '{{ quiet | default(false) }}', 'buttons': '{{ buttons | default([]) }}', 'button': "{{ button | default('') }}", 'note': "{{ note | default('') }}", 'critical_data': crit, 'quiet_data': quiet}}, {'variables': {'names': names_cfg()}}, {'variables': {'title': title, 'message': message, 'data': data}}, {'if': [{'condition': 'template', 'value_template': "{{ note | default('') == 'create' }}"}], 'then': [{'action': 'persistent_notification.create', 'data': {'notification_id': '{{ tag }}', 'title': '{{ title }}', 'message': '{{ message }}'}}]}, {'if': [{'condition': 'template', 'value_template': "{{ note | default('') == 'dismiss' }}"}], 'then': [{'action': 'persistent_notification.dismiss', 'data': {'notification_id': '{{ tag }}'}}]}, {'repeat': {'for_each': '{{ targets }}', 'sequence': [{'action': '{{ repeat.item }}', 'continue_on_error': True, 'data': {'title': '{{ title }}', 'message': '{{ message }}', 'data': '{{ data }}'}}]}}]}

async def read_scripts(hass: HomeAssistant) -> tuple[dict[str, Any], str | None]:
    data, before = await yamlfile.async_read(hass, hass.config.path(SCRIPTS), NotPlainScripts, dict)
    return (dict(data), before)

def rebase_scripts(writes: dict[str, Any], base: dict[str, Any], kept: list[str]) -> Any:
    """scripts.yaml changed in HA's editor while this pass ran: the writes again on what is there now — a script
    someone changed or removed meanwhile is left as they made it."""

    def redo(fresh: dict[str, Any]) -> dict[str, Any]:
        out = dict(fresh)
        for key, item in writes.items():
            if key in base and (key not in fresh or digest(fresh[key]) != digest(base[key])):
                kept.append(key)
                continue
            if key not in base and key in fresh:
                kept.append(key)
                continue
            if item is None:
                out.pop(key, None)
            else:
                out[key] = item
        return out
    return redo

async def write_scripts(hass: HomeAssistant, data: dict[str, Any], before: str | None, redo: Any) -> None:
    await yamlfile.async_write(hass, hass.config.path(SCRIPTS), hass.config.path(SCRIPTS_BACKUP), KEEP_BACKUPS, data, before, NotPlainScripts, redo, dict)
