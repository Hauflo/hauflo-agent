"""Builds the HA automation of one alarm rule."""
from __future__ import annotations
from typing import Any
from ..notify.common import ACTION_EVENT, LINK_KINDS, RUN_SERVICES, repeat_fast, repeat_slow
from .common import ALARMED, LEVELS_ALARMED, LEVELS_ON, NO_DATA, ONE, alarm_groups, alarm_hold, back_after, gap, helper_action, hold_cfg, ids, level_names, lit, names_cfg, outputs, push, say, state_is, who_template

def automation_for(rule: dict[str, Any], phones: list[dict[str, Any]], t: dict[str, str], ack: str, opn: str, *, names: dict[str, str] | None=None) -> dict[str, Any]:
    """One rule as a plain HA automation (HA's own editor format). ``t`` — the texts (``texts``), ``ack`` — the rule's
    «Принято» helper, ``opn`` — its «авария открыта» helper, ``names`` — entity → its name for the button («Закрыть:"""
    if rule.get('thresholds'):
        return level_automation_for(rule, phones, t, ack, opn, names=names)
    sources, states = (list(rule['sources']), list(rule['alarm_states']))
    link = rule.get('kind') in LINK_KINDS
    back_s = back_after(rule)
    alarm = [{'trigger': 'state', 'entity_id': eids, 'to': states, **({'not_from': NO_DATA} if link else {}), 'for': {'seconds': s}, 'id': 'alarm'} for eids, s in alarm_groups(rule, sources)]
    back = {'trigger': 'state', 'entity_id': sources, 'from': states + NO_DATA, 'not_to': states + NO_DATA, 'for': {'seconds': back_s}, 'id': 'back'}
    fresh = '{{ trigger.from_state is not none and ' + gap(back_s) + ' }}' if link else "{{ (trigger.from_state is none and is_state(ack, 'off')) or (trigger.from_state is not none and " + gap(back_s) + ') }}'
    told = [*([{'condition': 'template', 'value_template': '{{ trigger.from_state is not none }}'}] if link else []), {'condition': 'or', 'conditions': [state_is(opn, 'off'), {'condition': 'template', 'value_template': fresh}]}]
    still = {'condition': 'state', 'entity_id': sources, 'state': states, 'match': 'any'}
    return _automation(rule, phones, t, ack, opn, names, triggers=[*alarm, back], told=told, still=still, alarmed=ALARMED, names_tpl=names_cfg(), variables={'sources': sources, 'alarm_states': states, 'ack': ack})

def level_automation_for(rule: dict[str, Any], phones: list[dict[str, Any]], t: dict[str, str], ack: str, opn: str, *, names: dict[str, str] | None=None) -> dict[str, Any]:
    """    threshold for the rule's hold, back past it by the step for HOLD (a level has its own hysteresis: battery < 20 →
    alarm, > 25 → back). One threshold for all its sources (no signals): ``below`` / ``above`` as an integrator writes
    them; different ones (or signals beside them): each source's margin to its own threshold (``value_template``, a
    signal −1 in alarm / 1 in normal). «Принято», the repeat, the button and the siren as in ``automation_for``."""
    sources, states = (list(rule['sources']), list(rule['alarm_states']))
    levels: dict[str, dict[str, float]] = rule['thresholds']
    signals = [e for e in sources if e not in levels]
    shapes = {('below' if 'below' in th else 'above', th.get('below', th.get('above')), th['step']) for th in levels.values()}
    held, hold = ({'seconds': alarm_hold(rule)}, {'seconds': hold_cfg()})
    if len(shapes) == 1 and (not signals):
        way, value, step = next(iter(shapes))
        other = 'above' if way == 'below' else 'below'
        alarm = [{'trigger': 'numeric_state', 'entity_id': sources, way: value, 'for': held, 'id': 'alarm'}]
        back = {'trigger': 'numeric_state', 'entity_id': sources, other: value + step if way == 'below' else value - step, 'for': hold, 'id': 'back'}
    else:

        def margin(step: str) -> str:
            return '{%- set th = levels.get(state.entity_id) -%}{%- set v = state.state | float(none) -%}{%- if th -%}{%- if v is none -%}unknown{%- elif th.below is defined -%}{{ v - th.below' + step + ' }}{%- else -%}{{ th.above - v' + step + " }}{%- endif -%}{%- elif state.state in ['unavailable', 'unknown'] -%}unknown{%- else -%}{{ -1 if state.state in alarm_states else 1 }}{%- endif -%}"
        alarm = [{'trigger': 'numeric_state', 'entity_id': eids, 'value_template': margin(''), 'below': 0, 'for': {'seconds': s}, 'id': 'alarm'} for eids, s in alarm_groups(rule, sources)]
        back = {'trigger': 'numeric_state', 'entity_id': sources, 'value_template': margin(' - th.step'), 'above': 0, 'for': hold, 'id': 'back'}
    back_s = back_after(rule)
    fresh = "{%- set f = trigger.from_state -%}{%- set th = levels.get(trigger.entity_id) -%}{%- if f is none -%}{{ is_state(ack, 'off') }}{%- elif th and f.state | is_number -%}{{ (f.state | float > th.below + th.step) if th.below is defined else (f.state | float < th.above - th.step) }}{%- elif th or f.state in " + repr(NO_DATA) + ' or f.state not in alarm_states -%}{{ ' + gap(back_s) + ' }}{%- else -%}{{ false }}{%- endif -%}'
    told = [{'condition': 'or', 'conditions': [state_is(opn, 'off'), {'condition': 'template', 'value_template': fresh}]}]
    still = {'condition': 'template', 'value_template': LEVELS_ON + '{{ ns.o | count > 0 }}'}
    return _automation(rule, phones, t, ack, opn, names, triggers=[*alarm, back], told=told, still=still, alarmed=LEVELS_ALARMED, names_tpl=level_names(), variables={'sources': sources, 'alarm_states': states, 'ack': ack, 'levels': levels})

def _automation(rule: dict[str, Any], phones: list[dict[str, Any]], t: dict[str, str], ack: str, opn: str, names: dict[str, str] | None, *, triggers: list[dict[str, Any]], told: list[dict[str, Any]], still: dict[str, Any], alarmed: str, names_tpl: str, variables: dict[str, Any]) -> dict[str, Any]:
    the = ids(rule['id'])
    aid = the['automation']
    title = lit(rule['title'])
    act = rule.get('action') if (rule.get('action') or {}).get('service') in RUN_SERVICES else None
    button = None
    if act:
        button = lit(say(t, 'close' if act['service'] == 'valve.close_valve' else 'off', name=(names or {}).get(act['entity_id']) or act['entity_id'])[:60])
    buttons = ([{'action': the['run'], 'title': button, 'destructive': True, 'authenticationRequired': True}] if act else []) + [{'action': the['ack'], 'title': t['accept']}]
    alarm_title = say(t, 'title', title=title)
    who_said = '{% if who %}' + say(t, 'accepted_by', who='{{ who }}') + '{% else %}' + t['accepted'] + '{% endif %}'
    phone_names = ', '.join((str(p.get('name') or p['service']) for p in phones)) or t['nobody']
    repeat = int(rule.get('repeat_min') or 0)
    alarm_push = {'tag': aid, 'critical': rule.get('priority') == 'critical', 'actions': buttons}
    siren_on, siren_off = (outputs(rule, 'on_start'), outputs(rule, 'on_end'))
    named = [{'variables': {'alarmed': alarmed}}, {'variables': {'names': names_tpl}}]
    says = say(t, 'alarm', names='{{ names }}')
    choose = [{'conditions': [{'condition': 'trigger', 'id': 'alarm'}, *told], 'sequence': [*named, helper_action(opn, True), helper_action(ack, False), {'action': 'persistent_notification.create', 'data': {'notification_id': aid, 'title': alarm_title, 'message': says}}, *push(phones, alarm_title, says, **alarm_push), *siren_on]}, {'conditions': [{'condition': 'trigger', 'id': 'back'}, state_is(opn, 'on'), {'condition': 'not', 'conditions': [still]}], 'sequence': [helper_action(opn, False), helper_action(ack, False), {'action': 'persistent_notification.dismiss', 'data': {'notification_id': aid}}, *siren_off, *push(phones, say(t, 'back_title', title=title), say(t, 'back', name=ONE), tag=aid)]}, {'conditions': [{'condition': 'trigger', 'id': 'ack'}, state_is(opn, 'on'), state_is(ack, 'off')], 'sequence': [helper_action(ack, True), *siren_off, {'variables': {'who': who_template('trigger.event.context.user_id')}}, *push(phones, alarm_title, who_said, tag=aid, quiet=True)]}]
    waits = ([{'trigger': 'event', 'event_type': ACTION_EVENT, 'event_data': {'action': the['run']}, 'id': 'run'}] if act else []) + [{'trigger': 'state', 'entity_id': ack, 'from': 'off', 'to': 'on', 'id': 'ack'}]
    loop: list[dict[str, Any]] = []
    if repeat:
        loop.append({'conditions': [{'condition': 'template', 'value_template': '{{ wait.trigger is none }}'}, state_is(ack, 'off'), still], 'sequence': [*named, *push(phones, alarm_title, say(t, 'repeat', names='{{ names }}'), **alarm_push)]})
    if act:
        loop.append({'conditions': [{'condition': 'template', 'value_template': "{{ wait.trigger is not none and wait.trigger.id == 'run' }}"}], 'sequence': [{'action': act['service'], 'target': {'entity_id': act['entity_id']}}, helper_action(ack, True), *siren_off, *push(phones, alarm_title, say(t, 'done', button=button or ''), tag=aid, quiet=True)]})
    loop.append({'conditions': [{'condition': 'template', 'value_template': "{{ wait.trigger is not none and wait.trigger.id == 'ack' }}"}], 'sequence': [*siren_off, {'if': [{'condition': 'template', 'value_template': '{{ wait.trigger.to_state.context.parent_id is none }}'}], 'then': [{'variables': {'who': who_template('wait.trigger.to_state.context.user_id')}}, *push(phones, alarm_title, who_said, tag=aid, quiet=True)]}]})
    wait: dict[str, Any] = {'wait_for_trigger': waits}
    if repeat:
        step, slow = (repeat * 60, max(repeat, repeat_slow()) * 60)
        fast = step * repeat_fast() if repeat < repeat_slow() else 0
        pace = f'{{% set e = (now() - states.{opn}.last_changed).total_seconds() %}}{{% set a = e + 1 %}}{{{{ ((a // {step} + 1) * {step} if a < {fast} else {fast} + ((a - {fast}) // {slow} + 1) * {slow}) - e }}}}'
        wait |= {'timeout': {'seconds': pace}, 'continue_on_timeout': True}
    return {'id': aid, 'alias': say(t, 'alias', title=rule['title']), 'description': say(t, 'description', title=rule['title'], phones=phone_names, helper=ack, open=opn), 'mode': 'restart', 'trigger_variables': variables, 'triggers': [*triggers, {'trigger': 'event', 'event_type': ACTION_EVENT, 'event_data': {'action': the['ack']}, 'id': 'ack'}], 'actions': [{'choose': choose}, {'repeat': {'while': [state_is(opn, 'on'), *([] if act else [state_is(ack, 'off')])], 'sequence': [wait, {'choose': loop}]}}]}
