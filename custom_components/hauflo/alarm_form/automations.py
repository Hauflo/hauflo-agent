"""The automations of an alarm rule: triggers, accept and button."""
from __future__ import annotations
import hashlib
import json
from typing import Any
from homeassistant.core import HomeAssistant
from ..const import ACK_PREFIX, ALARM_PREFIX, DO_ACTION, OK_ACTION, RUN_PREFIX
from ..notify.common import ACTION_EVENT, LINK_KINDS, list_max, repeat_slow
from .common import ACCEPT_ID, BUTTON_ID, NO_DATA, SCRIPT, back_id, fast_rounds, grouped

def accept_automation(t: dict[str, str]) -> dict[str, Any]:
    """«Принято» from any rule's push: that rule's helper on (its automation's wait ends: the repeats stop)."""
    n = len(OK_ACTION)
    return {'id': ACCEPT_ID, 'alias': t['accept_alias'], 'description': t['accept_description'], 'mode': 'queued', 'triggers': [{'trigger': 'event', 'event_type': ACTION_EVENT}], 'conditions': [{'condition': 'template', 'value_template': "{{ (trigger.event.data.action | string).startswith('" + OK_ACTION + "') }}"}], 'actions': [{'action': 'input_boolean.turn_on', 'target': {'entity_id': 'input_boolean.' + ACK_PREFIX + '{{ trigger.event.data.action[' + str(n) + ':] | lower }}'}}]}

def button_automation(t: dict[str, str]) -> dict[str, Any]:
    """A rule's button from its push («Закрыть: Кран»): that rule's script."""
    n = len(DO_ACTION)
    return {'id': BUTTON_ID, 'alias': t['button_alias'], 'description': t['button_description'], 'mode': 'queued', 'triggers': [{'trigger': 'event', 'event_type': ACTION_EVENT}], 'conditions': [{'condition': 'template', 'value_template': "{{ (trigger.event.data.action | string).startswith('" + DO_ACTION + "') }}"}], 'actions': [{'action': 'script.turn_on', 'target': {'entity_id': 'script.' + RUN_PREFIX + '{{ trigger.event.data.action[' + str(n) + ':] | lower }}'}}]}

def _helper(entity_id: str, on: bool) -> dict[str, Any]:
    return {'action': f"input_boolean.turn_{('on' if on else 'off')}", 'target': {'entity_id': entity_id}}

def _is(entity_id: str, state: str) -> dict[str, Any]:
    return {'condition': 'state', 'entity_id': entity_id, 'state': state}

def _when(template: str) -> dict[str, Any]:
    return {'condition': 'template', 'value_template': template}

def _push(base: dict[str, Any], **data: Any) -> dict[str, Any]:
    return {'action': 'script.' + SCRIPT, 'data': {**base, **data}}

def triggers(rule: dict[str, Any], hold_groups: list[tuple[list[str], int]], hold: int) -> list[dict[str, Any]]:
    """The rule's sensors in alarm for its hold: HA's own `state` (`to:` without `from:` — a sensor waking up from no
    data in alarm is an alarm too) / `numeric_state` (a level below / above its threshold, one trigger per threshold);
    a link sensor's «off» only after it was seen «on» (never from no data)."""
    states = list(rule['alarm_states'])
    levels: dict[str, dict[str, float]] = rule.get('thresholds') or {}
    to: Any = states[0] if len(states) == 1 else states
    link = {'not_from': NO_DATA} if rule.get('kind') in LINK_KINDS else {}
    out: list[dict[str, Any]] = []
    for eids, s in hold_groups:
        signals = [e for e in eids if e not in levels]
        if signals:
            out.append({'trigger': 'state', 'entity_id': signals, 'to': to, **link, 'for': {'seconds': s}})
        by: dict[tuple[str, float], list[str]] = {}
        for e in eids:
            if (th := levels.get(e)):
                way = 'below' if 'below' in th else 'above'
                by.setdefault((way, th[way]), []).append(e)
        for (way, value), es in by.items():
            out.append({'trigger': 'numeric_state', 'entity_id': es, way: value, 'for': {'seconds': s}})
    return out

def rule_configs(rule: dict[str, Any], phones: list[dict[str, Any]], t: dict[str, str], ack: str, group: str, *, slug: str, aid: str, hold_groups: list[tuple[list[str], int]], hold: int, back_s: int, outputs_on: list[dict[str, Any]], outputs_off: list[dict[str, Any]], button: str | None, button_text: str | None, act: dict[str, Any] | None, title: str, names: dict[str, str], who: str) -> dict[str, Any]:
    """The rule's objects: ``automation``, ``back`` (a critical rule's «снова в норме», else None) and ``run`` (its push
    button's script, else None). ``title`` — the rule's title as a literal (independent.common.lit), ``button`` — the
    button's
    words («Закрыть: Кран») as a literal too, ``button_text`` — as they are (the script's name)."""
    critical = rule.get('priority') == 'critical'
    targets = [f"notify.{p['service']}" for p in phones]
    base: dict[str, Any] = {'rule': title, 'tag': aid, 'targets': targets, 'group': group}
    if not grouped(rule):
        base['sources'] = list(rule['sources'])
        if list(rule['alarm_states']) != ['on']:
            base['alarm_states'] = list(rule['alarm_states'])
        if rule.get('thresholds'):
            base['levels'] = rule['thresholds']
    alarm = {**base, **({'critical': True} if critical else {})}
    if act:
        alarm['buttons'] = [{'action': DO_ACTION + slug.upper(), 'title': button, 'destructive': True, 'authenticationRequired': True}, {'action': OK_ACTION + slug.upper(), 'title': t['accept']}]
    else:
        alarm['buttons'] = [{'action': OK_ACTION + slug.upper(), 'title': t['accept']}]
    wait = {'wait_template': "{{ is_state('" + ack + "', 'on') or is_state('" + group + "', 'off') }}"}
    repeat = int(rule.get('repeat_min') or 0)
    if repeat:
        slow = max(repeat, repeat_slow())
        escalation: list[dict[str, Any]] = [{'repeat': {'sequence': [_push(alarm, say='alarm', round='{{ repeat.index }}', note='create'), {**wait, 'timeout': {'minutes': repeat}}], 'until': [_when('{{ wait.completed or repeat.index >= ' + str(fast_rounds()) + ' }}')]}}, {'if': [_is(ack, 'off'), _is(group, 'on')], 'then': [{'repeat': {'sequence': [_push(alarm, say='repeat', note='create'), {**wait, 'timeout': {'minutes': slow}}], 'until': [_when('{{ wait.completed }}')]}}]}]
    else:
        escalation = [_push(alarm, say='alarm', note='create'), wait]
    actions = [_helper(ack, False), *outputs_on, *escalation, {'if': [_is(ack, 'on')], 'then': [*outputs_off, _push(base, say='accepted', note='dismiss')], 'else': [*outputs_off, {'action': 'persistent_notification.dismiss', 'data': {'notification_id': aid}}]}]
    who = who or t['nobody']
    shown = [names.get(e) or e for e in rule['sources']]
    watched = ', '.join(shown[:list_max()]) + (f' +{len(shown) - list_max()}' if len(shown) > list_max() else '')
    plan = t['repeat_plan'].format(every=repeat, more=fast_rounds() - 1, slow=max(repeat, repeat_slow())) if repeat else t['no_repeat_plan']
    automation = {'id': aid, 'alias': t['alias'].format(title=rule['title']), 'description': t['rule_description'].format(sources=watched, hold=hold, phones=who, repeat=plan, helper=t['helper'].format(title=rule['title']), group=t['group'].format(title=rule['title'])), 'mode': 'restart', 'triggers': triggers(rule, hold_groups, hold), 'conditions': [], 'actions': actions}
    back = None
    if critical:
        back = {'id': back_id(aid), 'alias': t['back_alias'].format(title=rule['title']), 'description': t['back_description'].format(title=rule['title'], back=back_s, phones=who), 'mode': 'single', 'triggers': [{'trigger': 'state', 'entity_id': group, 'from': 'on', 'to': 'off', 'for': {'seconds': back_s}}, {'trigger': 'state', 'entity_id': list(rule['sources']), 'from': NO_DATA, 'to': 'off', 'for': {'seconds': back_s}}], 'conditions': [{'condition': 'state', 'entity_id': group, 'state': 'off'}, {'condition': 'not', 'conditions': [{'condition': 'state', 'entity_id': list(rule['sources']), 'state': NO_DATA, 'match': 'any'}]}, _when("{% set told = states.automation | selectattr('attributes.id', 'defined') | selectattr('attributes.id', 'eq', '" + aid + "') | map(attribute='attributes.last_triggered') | first %}{{ told is not none and (this.attributes.last_triggered is none or told > this.attributes.last_triggered) }}")], 'actions': [*outputs_off, _push(base, say='back', note='dismiss')]}
    run = None
    if act:
        run = {'alias': t['run_alias'].format(title=rule['title'], button=button_text), 'description': t['run_description'].format(title=rule['title'], button=button_text), 'icon': 'mdi:gesture-tap-button', 'mode': 'single', 'sequence': [{'action': act['service'], 'target': {'entity_id': act['entity_id']}}, _push(base, say='done', button=button, tag=aid + '_done'), _helper(ack, True)]}
    return {'automation': automation, 'back': back, 'run': run}

def digest(*cfgs: Any) -> str:
    """What a rule's objects do together (each without its description — automations.common.digest's rule)."""

    def one(cfg: Any) -> str:
        if not isinstance(cfg, dict):
            return ''
        body = {k: v for k, v in cfg.items() if k not in ('description', 'initial_state')}
        return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return hashlib.sha1('|'.join((one(c) for c in cfgs)).encode()).hexdigest()

def our_entries(hass: HomeAssistant) -> dict[str, Any]:
    """The rules' group / template helpers by their object id (made with it as the name: options «name»)."""
    out: dict[str, Any] = {}
    for domain in ('group', 'template'):
        for entry in hass.config_entries.async_entries(domain):
            name = str(entry.options.get('name') or '')
            if name.startswith(ALARM_PREFIX):
                out[name] = entry
    return out
