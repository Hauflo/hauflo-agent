"""Alarm rules as Home Assistant's own objects (0.26, owner's decision 5B; 0.31 — as an integrator writes them by hand).

Every enabled alarm rule Hauflo sends becomes, in the house's HA:
- a helper ``input_boolean.hauflo_ack_<rule>`` — «Принято»: on = someone accepted the open alarm;
- a helper ``input_boolean.hauflo_open_<rule>`` — «авария открыта»: on = the alarm was told and is not back yet;
- an automation ``hauflo_alarm_<rule>`` in automations.yaml (the same plain-file check, backup copy and lock as the
  dashboard automations), small enough to read in HA's visual editor — three triggers (in alarm for the rule's hold /
  every source back in normal / «Принято» from a push) and two actions: a ``choose`` (alarm → a push to the rule's
  phones, critical per OS, each phone on its own, and a note in HA; back → «снова в норме» in place of the push, same
  tag; «Принято» → said quietly) and a ``repeat`` while the alarm is open (the push again every `repeat_min` while
  nobody accepted it, the rule's button from the push, «Принято» toggled in HA itself).
It runs without Hauflo and without the agent. A chattering sensor, «нет связи» and the summary stay with the agent's
engine (notify.py, ``engine.native``) and the cloud: no memory in YAML. Losses, accepted by the owner (10.11): the
repeat and the button wait in a run that a restart of HA ends (the next alarm starts them again); chatter is damped by
the engine only — HA tells every alarm that held after the source was in normal for the rule's back window.
A rule whose automation can't be written (automations.yaml not plain, no automation integration, changes closed by the
owner, a helper HA didn't make) stays with the engine, said in the result. An automation edited by hand in HA is never
written over or removed — its helpers neither; removing the integration leaves them all (``hauflo.remove_alarms`` takes
them away). All texts are the agent's translations (exceptions.alarm_native_*).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import entity_registry as er, translation
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.setup import async_setup_component
from homeassistant.util import slugify

from . import alarm_form, automations, keep, yamlfile
from .const import ACK_ACTION, ACK_PREFIX, ALARM_PREFIX, ASSET_TAG, CHATTER_PREFIX, DO_ACTION, DOMAIN, NATIVE_STORE, OK_ACTION, OPEN_PREFIX, RUN_ACTION, RUN_PREFIX, site_ru
from .storage import HaufloStore
from .notify import ACTION_EVENT, LINK_KINDS, NATIVE_HOLD, OUTPUTS, default_hold, hold_value, LIST_MAX, REPEAT_FAST, REPEAT_SLOW, RUN_SERVICES, NotifyEngine, native_back, push_data

_LOGGER = logging.getLogger(__name__)
# The export before 0.26 (one push, no repeats): while one is still there an alarm comes twice — said in the result.
PREFIX = "hauflo_independent_"
HOLD = NATIVE_HOLD  # an alarm (or its end) held this long — a flicker says nothing


def alarm_hold(rule: dict[str, Any]) -> int:
    """Seconds a source stays in alarm before the rule's «alarm» (its `for:`): the rule's hold (notify.hold_value /
    default_hold — 0 for life safety, a minute for doors / motion / classless signals, two for levels), never under
    HOLD (a flicker of a few seconds says nothing even for a leak). A rule from before holds — the canon's."""
    hold = rule.get("hold")
    if not isinstance(hold, int) or isinstance(hold, bool):
        own = hold_value(rule.get("hold_s"))
        hold = own if own is not None else default_hold(None, str(rule.get("kind") or ""), list(rule.get("sources") or []), rule.get("thresholds") or {})
    return max(HOLD, hold)


def alarm_groups(rule: dict[str, Any], sources: list[str]) -> list[tuple[list[str], int]]:
    """The sources by their `for:` — the rule's (alarm_hold); one that holds its time itself (a combination's T —
    notify.own_holds, the rule's `holds`) its own, never under HOLD. In the rule's order, one trigger per hold."""
    own = rule.get("holds") or {}
    by: dict[int, list[str]] = {}
    for eid in sources:
        by.setdefault(max(HOLD, int(own[eid])) if eid in own else alarm_hold(rule), []).append(eid)
    return [(eids, s) for s, eids in by.items()]


def back_after(rule: dict[str, Any]) -> int:
    """Seconds a source stays in normal before the rule's «back» (its chatter window, at least HOLD — ISA-18.2 / Nagios
    flap hysteresis on the return; HA's own `for:`). In alarm again sooner (or after a radio blip of no data as short):
    the same open alarm — nothing said."""
    return native_back(rule)


NO_DATA = ["unavailable", "unknown"]
ICON = "mdi:bell-check"
OPEN_ICON = "mdi:bell-ring"
_TEXT = "alarm_native_"
def _name(e: str) -> str:
    """The Jinja of an entity's name in a native push: its friendly name, else its object id in words («leak_bath» →
    «Leak bath», as notify.label and HA say an unnamed entity) — never the raw id (acceptance 0.33 п.5)."""
    return f"(state_attr({e}, 'friendly_name') or ({e}.split('.')[1] | replace('_', ' ') | capitalize))"


# One alarm's names in the push: «Протечка кухня · Кухня, …» — up to LIST_MAX, then «+N» (rendered by HA when it runs).
_NAMES = (
    "{%- set ns = namespace(o=[]) -%}{%- for e in alarmed[:" + str(LIST_MAX) + "] -%}{%- set a = area_name(e) -%}"
    "{%- set ns.o = ns.o + [" + _name("e") + " ~ (' · ' ~ a if a else '')] -%}{%- endfor -%}"
    "{{ ns.o | join(', ') }}{% if alarmed | count > " + str(LIST_MAX) + " %} +{{ alarmed | count - " + str(LIST_MAX) + " }}{% endif %}"
)
# A level's names say its value too: «Батарея датчика · Кухня (18 %)».
_LEVEL_NAMES = (
    "{%- set ns = namespace(o=[]) -%}{%- for e in alarmed[:" + str(LIST_MAX) + "] -%}{%- set a = area_name(e) -%}{%- set u = state_attr(e, 'unit_of_measurement') -%}"
    "{%- set ns.o = ns.o + [" + _name("e") + " ~ (' · ' ~ a if a else '')"
    " ~ ((' (' ~ states(e) ~ ((' ' ~ u) if u else '') ~ ')') if e in levels else '')] -%}{%- endfor -%}"
    "{{ ns.o | join(', ') }}{% if alarmed | count > " + str(LIST_MAX) + " %} +{{ alarmed | count - " + str(LIST_MAX) + " }}{% endif %}"
)
_ONE = "{{ " + _name("trigger.entity_id") + " ~ ((' · ' ~ area_name(trigger.entity_id)) if area_name(trigger.entity_id) else '') }}"
_ALARMED = "{{ expand(sources) | selectattr('state', 'in', alarm_states) | map(attribute='entity_id') | list }}"
# A level rule's sources in alarm, the open alarm's way (notify.in_alarm, open_now): a level until it is back past its
# threshold by the step (battery < 20 → alarm, > 25 → back: 18 → 22 → 19 is one alarm), a signal in its states.
_LEVELS_ON = (
    "{%- set ns = namespace(o=[]) -%}{%- for e in sources -%}{%- set th = levels.get(e) -%}{%- set s = states(e) -%}"
    "{%- if th -%}{%- if s | is_number and ((s | float <= th.below + th.step) if th.below is defined else (s | float >= th.above - th.step)) -%}"
    "{%- set ns.o = ns.o + [e] -%}{%- endif -%}{%- elif s in alarm_states -%}{%- set ns.o = ns.o + [e] -%}{%- endif -%}{%- endfor -%}"
)
_LEVELS_ALARMED = _LEVELS_ON + "{{ ns.o }}"


def _who(uid: str) -> str:
    """Who pressed «Принято»: the HA person of the user (a phone's push action carries its user, a toggle in HA too)."""
    return "{%- set uid = " + uid + " -%}{{ (states.person | selectattr('attributes.user_id', 'eq', uid) | map(attribute='name') | list | first) if uid else '' }}"


def slug_of(rule_id: str) -> str:
    return f"{slugify(rule_id)[:40] or 'rule'}_{hashlib.sha1(rule_id.encode()).hexdigest()[:6]}"


def ids(rule_id: str) -> dict[str, str]:
    """The rule's automation id, its helpers' object ids and its push buttons (const.py prefixes). «chatter» — the
    helper of the rule before 0.31, only removed."""
    slug = slug_of(rule_id)
    return {"automation": ALARM_PREFIX + slug, "helper": ACK_PREFIX + slug, "open": OPEN_PREFIX + slug, "chatter": CHATTER_PREFIX + slug,
            "ack": ACK_ACTION + slug.upper(), "run": RUN_ACTION + slug.upper()}


def _of(automation: str, prefix: str) -> str:
    """A helper's object id of a rule's automation (records before 0.31 don't keep the «open» one)."""
    return prefix + automation.removeprefix(ALARM_PREFIX)


def automation_id(rule_id: str) -> str:
    return ids(rule_id)["automation"]


def _lit(text: str) -> str:
    """A person's text (a rule title, a device name) inside a field HA renders as a template: said as it is."""
    if not any(ch in text for ch in "{}%#"):
        return text
    body = json.dumps(text, ensure_ascii=False)[1:-1]
    for ch in "{}%#":
        body = body.replace(ch, f"\\u{ord(ch):04x}")
    return '{{ "' + body + '" }}'


async def texts(hass: HomeAssistant, english: bool) -> dict[str, str]:
    """The automations' words in the house's language (translations, exceptions.alarm_native_*)."""
    pre = f"component.{DOMAIN}.exceptions.{_TEXT}"
    got = await translation.async_get_translations(hass, "en" if english else "ru", "exceptions", [DOMAIN])
    out = {k[len(pre):].removesuffix(".message"): v for k, v in got.items() if k.startswith(pre)}
    if "alias" not in out:  # a release without its words is a bug, never an automation without texts
        raise RuntimeError("Hauflo: alarm texts missing in translations")
    return out


def _say(t: dict[str, str], key: str, **parts: str) -> str:
    return t[key].format(**parts)


def _push(phones: list[dict[str, Any]], title: str | None, message: str, **data: Any) -> list[dict[str, Any]]:
    """Every phone at once, each on its own (``continue_on_error``): a slow or failing one never holds the others."""
    if not phones:
        return []
    return [
        {
            "parallel": [
                {
                    "action": f"notify.{p['service']}",
                    "continue_on_error": True,
                    "data": {**({"title": title} if title else {}), "message": message, "data": push_data(p.get("os"), **data)},
                }
                for p in phones
            ]
        }
    ]


def _helper(entity_id: str, on: bool) -> dict[str, Any]:
    return {"action": f"input_boolean.turn_{'on' if on else 'off'}", "target": {"entity_id": entity_id}}


def _is(entity_id: str, state: str) -> dict[str, Any]:
    return {"condition": "state", "entity_id": entity_id, "state": state}


def _outputs(rule: dict[str, Any], when: str) -> list[dict[str, Any]]:
    """The rule's alarm outputs (399-15, a siren) switched `when` («on_start» / «on_end»): its service by domain from
    OUTPUTS, each on its own (``continue_on_error``: a siren that fails never stops the pushes). Empty for a rule with none
    — its automation stays exactly as before."""
    by: dict[str, list[str]] = {}
    for o in rule.get("outputs") or []:
        domain = o["entity_id"].partition(".")[0]
        if o.get(when) and OUTPUTS.get(domain, {}).get(when):
            by.setdefault(domain, []).append(o["entity_id"])
    return [{"action": f"{d}.{OUTPUTS[d][when]}", "continue_on_error": True, "target": {"entity_id": eids}} for d, eids in by.items()]


def _gap(seconds: int) -> str:
    """The source was out of alarm (in normal or without data) at least this long before this alarm."""
    return "(trigger.to_state.last_changed - trigger.from_state.last_changed).total_seconds() >= " + str(seconds)


def automation_for(rule: dict[str, Any], phones: list[dict[str, Any]], t: dict[str, str], ack: str, opn: str, *, names: dict[str, str] | None = None) -> dict[str, Any]:
    """One rule as a plain HA automation (HA's own editor format). ``t`` — the texts (``texts``), ``ack`` — the rule's
    «Принято» helper, ``opn`` — its «авария открыта» helper, ``names`` — entity → its name for the button («Закрыть:
    Кран»). A rule with levels (399-20) — ``level_automation_for``."""
    if rule.get("thresholds"):
        return level_automation_for(rule, phones, t, ack, opn, names=names)
    sources, states = list(rule["sources"]), list(rule["alarm_states"])
    link = rule.get("kind") in LINK_KINDS
    back_s = back_after(rule)
    # A link sensor's «off» means something only after it was seen «on» (never from no data, nor at HA's start).
    alarm = [{"trigger": "state", "entity_id": eids, "to": states, **({"not_from": NO_DATA} if link else {}), "for": {"seconds": s}, "id": "alarm"} for eids, s in alarm_groups(rule, sources)]
    # Normal from alarm or from no data (HA M-5: alarm → unavailable → normal ends it too), held the back window.
    back = {"trigger": "state", "entity_id": sources, "from": states + NO_DATA, "not_to": states + NO_DATA, "for": {"seconds": back_s}, "id": "back"}
    # Told: a new alarm (none open), or — while one is open (another source of the rule, or a «back» HA's restart lost) —
    # a source that was out of alarm for the whole back window; after HA's restart, while nobody accepted it. A shorter
    # normal / no-data phase is the same open alarm (a chattering sensor, a radio blip) — nothing said.
    fresh = ("{{ trigger.from_state is not none and " + _gap(back_s) + " }}" if link
             else "{{ (trigger.from_state is none and is_state(ack, 'off')) or (trigger.from_state is not none and " + _gap(back_s) + ") }}")
    told = [*([{"condition": "template", "value_template": "{{ trigger.from_state is not none }}"}] if link else []),
            {"condition": "or", "conditions": [_is(opn, "off"), {"condition": "template", "value_template": fresh}]}]
    still = {"condition": "state", "entity_id": sources, "state": states, "match": "any"}
    return _automation(rule, phones, t, ack, opn, names, triggers=[*alarm, back], told=told, still=still, alarmed=_ALARMED, names_tpl=_NAMES,
                       variables={"sources": sources, "alarm_states": states, "ack": ack})


def level_automation_for(rule: dict[str, Any], phones: list[dict[str, Any]], t: dict[str, str], ack: str, opn: str, *, names: dict[str, str] | None = None) -> dict[str, Any]:
    """A rule with levels (399-20) as a plain HA automation: HA's own ``numeric_state`` — in alarm below / above the
    threshold for the rule's hold, back past it by the step for HOLD (a level has its own hysteresis: battery < 20 →
    alarm, > 25 → back). One threshold for all its sources (no signals): ``below`` / ``above`` as an integrator writes
    them; different ones (or signals beside them): each source's margin to its own threshold (``value_template``, a
    signal −1 in alarm / 1 in normal). «Принято», the repeat, the button and the siren as in ``automation_for``."""
    sources, states = list(rule["sources"]), list(rule["alarm_states"])
    levels: dict[str, dict[str, float]] = rule["thresholds"]
    signals = [e for e in sources if e not in levels]
    shapes = {(("below" if "below" in th else "above"), th.get("below", th.get("above")), th["step"]) for th in levels.values()}
    held, hold = {"seconds": alarm_hold(rule)}, {"seconds": HOLD}
    if len(shapes) == 1 and not signals:
        way, value, step = next(iter(shapes))
        other = "above" if way == "below" else "below"
        alarm = [{"trigger": "numeric_state", "entity_id": sources, way: value, "for": held, "id": "alarm"}]
        back = {"trigger": "numeric_state", "entity_id": sources, other: value + step if way == "below" else value - step, "for": hold, "id": "back"}
    else:
        def margin(step: str) -> str:
            return ("{%- set th = levels.get(state.entity_id) -%}{%- set v = state.state | float(none) -%}"
                    "{%- if th -%}{%- if v is none -%}unknown{%- elif th.below is defined -%}{{ v - th.below" + step + " }}{%- else -%}{{ th.above - v" + step + " }}{%- endif -%}"
                    "{%- elif state.state in ['unavailable', 'unknown'] -%}unknown{%- else -%}{{ -1 if state.state in alarm_states else 1 }}{%- endif -%}")

        alarm = [{"trigger": "numeric_state", "entity_id": eids, "value_template": margin(""), "below": 0, "for": {"seconds": s}, "id": "alarm"} for eids, s in alarm_groups(rule, sources)]
        back = {"trigger": "numeric_state", "entity_id": sources, "value_template": margin(" - th.step"), "above": 0, "for": hold, "id": "back"}
    back_s = back_after(rule)
    # Told: a new alarm (none open), or — while one is open — a level that was back past its step (a value inside the
    # step is the same alarm), a source that was out of alarm / without data for the back window; after HA's restart,
    # while nobody accepted it.
    fresh = ("{%- set f = trigger.from_state -%}{%- set th = levels.get(trigger.entity_id) -%}"
             "{%- if f is none -%}{{ is_state(ack, 'off') }}"
             "{%- elif th and f.state | is_number -%}{{ (f.state | float > th.below + th.step) if th.below is defined else (f.state | float < th.above - th.step) }}"
             "{%- elif th or f.state in " + repr(NO_DATA) + " or f.state not in alarm_states -%}{{ " + _gap(back_s) + " }}{%- else -%}{{ false }}{%- endif -%}")
    told = [{"condition": "or", "conditions": [_is(opn, "off"), {"condition": "template", "value_template": fresh}]}]
    still = {"condition": "template", "value_template": _LEVELS_ON + "{{ ns.o | count > 0 }}"}
    return _automation(rule, phones, t, ack, opn, names, triggers=[*alarm, back], told=told, still=still, alarmed=_LEVELS_ALARMED, names_tpl=_LEVEL_NAMES,
                       variables={"sources": sources, "alarm_states": states, "ack": ack, "levels": levels})


def _automation(rule: dict[str, Any], phones: list[dict[str, Any]], t: dict[str, str], ack: str, opn: str, names: dict[str, str] | None, *,
                triggers: list[dict[str, Any]], told: list[dict[str, Any]], still: dict[str, Any], alarmed: str, names_tpl: str, variables: dict[str, Any]) -> dict[str, Any]:
    """The automation around a rule's own alarm / back triggers: «Принято» from a push the third trigger; actions — a
    ``choose`` (alarm / back / «Принято») and a ``repeat`` while the alarm is open (the push again, the rule's button,
    «Принято» in HA). ``told`` — when an alarm is told, ``still`` — a source in alarm now, ``alarmed`` / ``names_tpl`` —
    the sources in alarm and their names in the push."""
    the = ids(rule["id"])
    aid = the["automation"]
    title = _lit(rule["title"])
    act = rule.get("action") if (rule.get("action") or {}).get("service") in RUN_SERVICES else None
    button = None
    if act:
        # Cut as the person reads it, then made a literal (a cut literal would break the template).
        button = _lit(_say(t, "close" if act["service"] == "valve.close_valve" else "off", name=(names or {}).get(act["entity_id"]) or act["entity_id"])[:60])
    buttons = ([{"action": the["run"], "title": button, "destructive": True, "authenticationRequired": True}] if act else []) + [{"action": the["ack"], "title": t["accept"]}]
    alarm_title = _say(t, "title", title=title)
    who_said = "{% if who %}" + _say(t, "accepted_by", who="{{ who }}") + "{% else %}" + t["accepted"] + "{% endif %}"
    phone_names = ", ".join(str(p.get("name") or p["service"]) for p in phones) or t["nobody"]
    repeat = int(rule.get("repeat_min") or 0)
    alarm_push = {"tag": aid, "critical": rule.get("priority") == "critical", "actions": buttons}
    siren_on, siren_off = _outputs(rule, "on_start"), _outputs(rule, "on_end")
    named = [{"variables": {"alarmed": alarmed}}, {"variables": {"names": names_tpl}}]
    says = _say(t, "alarm", names="{{ names }}")
    choose = [
        {
            "conditions": [{"condition": "trigger", "id": "alarm"}, *told],
            "sequence": [*named, _helper(opn, True), _helper(ack, False),
                         {"action": "persistent_notification.create", "data": {"notification_id": aid, "title": alarm_title, "message": says}},
                         *_push(phones, alarm_title, says, **alarm_push), *siren_on],
        },
        {
            # Every source out of alarm (the last one back says it; one without data doesn't hold it): «Принято» resets.
            "conditions": [{"condition": "trigger", "id": "back"}, _is(opn, "on"), {"condition": "not", "conditions": [still]}],
            "sequence": [_helper(opn, False), _helper(ack, False), {"action": "persistent_notification.dismiss", "data": {"notification_id": aid}},
                         *siren_off, *_push(phones, _say(t, "back_title", title=title), _say(t, "back", name=_ONE), tag=aid)],
        },
        {
            "conditions": [{"condition": "trigger", "id": "ack"}, _is(opn, "on"), _is(ack, "off")],
            "sequence": [_helper(ack, True), *siren_off, {"variables": {"who": _who("trigger.event.context.user_id")}}, *_push(phones, alarm_title, who_said, tag=aid, quiet=True)],
        },
    ]
    # While the alarm is open: the push again every `repeat` minutes nobody accepted it, the rule's button (kept after
    # «Принято» — closing the valve is still worth it), «Принято» toggled in HA itself (a card, the helper). The run
    # waits for them; any trigger of the rule starts it again (mode: restart).
    waits = ([{"trigger": "event", "event_type": ACTION_EVENT, "event_data": {"action": the["run"]}, "id": "run"}] if act else []) + [
        {"trigger": "state", "entity_id": ack, "from": "off", "to": "on", "id": "ack"}]
    loop: list[dict[str, Any]] = []
    if repeat:
        loop.append({"conditions": [{"condition": "template", "value_template": "{{ wait.trigger is none }}"}, _is(ack, "off"), still],
                     "sequence": [*named, *_push(phones, alarm_title, _say(t, "repeat", names="{{ names }}"), **alarm_push)]})
    if act:
        # The service first, no «continue»: when it fails the alarm stays unaccepted and keeps repeating.
        loop.append({"conditions": [{"condition": "template", "value_template": "{{ wait.trigger is not none and wait.trigger.id == 'run' }}"}],
                     "sequence": [{"action": act["service"], "target": {"entity_id": act["entity_id"]}}, _helper(ack, True), *siren_off,
                                  *_push(phones, alarm_title, _say(t, "done", button=button or ""), tag=aid, quiet=True)]})
    # Accepted in HA (not by this automation: its own switching has a parent context; the agent's — the card's «Принято»
    # — too, the agent says it): the siren off, the phones told.
    loop.append({"conditions": [{"condition": "template", "value_template": "{{ wait.trigger is not none and wait.trigger.id == 'ack' }}"}],
                 "sequence": [*siren_off, {"if": [{"condition": "template", "value_template": "{{ wait.trigger.to_state.context.parent_id is none }}"}],
                                           "then": [{"variables": {"who": _who("wait.trigger.to_state.context.user_id")}}, *_push(phones, alarm_title, who_said, tag=aid, quiet=True)]}]})
    wait: dict[str, Any] = {"wait_for_trigger": waits}
    if repeat:
        # As the engine (owner 27.10): REPEAT_FAST repeats at the rule's pace, then hourly until «Принято» — every 5
        # minutes all night would use up HA's daily push limit before the next alarm. Counted from the alarm's opening
        # («авария открыта» on), not from the run: a second source flickering restarts the run (mode: restart) and
        # would put the repeat off for good (acceptance 10.11). A second of slack: a timer firing early never repeats twice.
        step, slow = repeat * 60, max(repeat, REPEAT_SLOW) * 60
        fast = step * REPEAT_FAST if repeat < REPEAT_SLOW else 0
        pace = (f"{{% set e = (now() - states.{opn}.last_changed).total_seconds() %}}{{% set a = e + 1 %}}"
                f"{{{{ ((a // {step} + 1) * {step} if a < {fast} else {fast} + ((a - {fast}) // {slow} + 1) * {slow}) - e }}}}")
        wait |= {"timeout": {"seconds": pace}, "continue_on_timeout": True}
    return {
        "id": aid,
        "alias": _say(t, "alias", title=rule["title"]),
        "description": _say(t, "description", title=rule["title"], phones=phone_names, helper=ack, open=opn),
        "mode": "restart",
        "trigger_variables": variables,
        "triggers": [*triggers, {"trigger": "event", "event_type": ACTION_EVENT, "event_data": {"action": the["ack"]}, "id": "ack"}],
        "actions": [
            {"choose": choose},
            {"repeat": {"while": [_is(opn, "on"), *([] if act else [_is(ack, "off")])], "sequence": [wait, {"choose": loop}]}},
        ],
    }


# The rule's helpers: (domain, object id prefix) — how HA's helper editor would make them; the input_text is the chatter
# memory of the rules before 0.31 (only removed).
_KIND = (("input_boolean", ACK_PREFIX), ("input_boolean", OPEN_PREFIX), ("input_text", CHATTER_PREFIX))


def _collection(hass: HomeAssistant, domain: str = "input_boolean") -> Any:
    """A helper domain's own storage collection — what HA's helper editor creates, updates and deletes through (its
    websocket commands are bound methods of the collection's websocket object)."""
    handler = (hass.data.get("websocket_api") or {}).get(f"{domain}/create")
    fn = handler[0] if handler else None
    while fn is not None and not hasattr(fn, "__self__"):
        fn = getattr(fn, "__wrapped__", None)
    return getattr(getattr(fn, "__self__", None), "storage_collection", None)


async def _helpers(hass: HomeAssistant) -> Any:
    if "input_boolean" not in hass.config.components:
        await async_setup_component(hass, "input_boolean", {})
    if (coll := _collection(hass)) is None:
        raise RuntimeError("input_boolean helpers not reachable")
    return coll


def _ack_entity(hass: HomeAssistant, object_id: str) -> str:
    return er.async_get(hass).async_get_entity_id("input_boolean", "input_boolean", object_id) or f"input_boolean.{object_id}"


async def _ensure_helper(hass: HomeAssistant, coll: Any, object_id: str, name: str, icon: str) -> str:
    """One of the rule's helpers («Принято», «авария открыта») — made like HA's helper editor makes it (the id from its
    first name), then named."""
    if object_id not in coll.data:
        await coll.async_create_item({"name": object_id, "icon": icon})
    if object_id not in coll.data:
        raise RuntimeError(f"helper {object_id} not made")
    item = coll.data[object_id]
    if item.get("name") != name or item.get("icon") != icon:
        await coll.async_update_item(object_id, {"name": name, "icon": icon})
    return _ack_entity(hass, object_id)


async def _drop_helper(hass: HomeAssistant, coll: Any, object_id: str) -> None:
    if coll is not None and object_id in coll.data:
        await coll.async_delete_item(object_id)


async def _drop_ours(hass: HomeAssistant, colls: dict[str, Any], object_id: str) -> None:
    """One of the rules' helpers, by its prefix (anything else is never touched)."""
    for domain, prefix in _KIND:
        if object_id.startswith(prefix):
            await _drop_helper(hass, colls.get(domain), object_id)


def _empty() -> dict[str, Any]:
    # restored — removed in HA by hand, written back by this apply: a change of HA said in the plan (HA review H-1),
    # never a silent «added».
    return {"rules": 0, "added": [], "updated": [], "restored": [], "unchanged": [], "removed": [], "kept_edited": [], "engine": [], "warnings": [], "migrate": [], "error": None, "error_code": None}


async def async_sync(hass: HomeAssistant, engine: NotifyEngine, *, dry_run: bool = False, config: dict[str, Any] | None = None, migrate: bool = False) -> dict[str, Any]:
    """The engine's rules as automations + helpers in HA (one write of automations.yaml, then one reload); the engine is
    told which rules HA now runs (``async_set_native`` — open alarms move over). ``dry_run`` — only what would change
    (Hauflo's deploy plan, ``config`` — the rules it would send). Rules are said by their titles; ``engine`` — those left
    to the agent, with the reason. A rule's automation from before 0.31 is written in the new form and its chatter
    memory (input_text) removed — unless it was edited by hand: then both stay as they are.
    Р83: a new rule is written in the flat form (alarm_form.py) where HA's scripts run; a rule already in HA in the
    form before stays as it is (``out["migrate"]`` — the ones the owner's «переписать» would rewrite) until ``migrate``
    (his explicit step on the «Аварии» page: never on an agent update) — one edited by hand never."""
    from homeassistant.components.automation.config import async_validate_config_item  # noqa: PLC0415

    english = engine._english(config)
    lang = "en" if english else "ru"  # kept with each record: what the automations say after the agent is gone (D-4)
    out = _empty()
    rules = [r for r in (config or engine.config).get("rules") or [] if r.get("enabled") and r.get("sources")]
    out["rules"] = len(rules)
    stored: dict[str, dict[str, Any]] = dict(engine.native)

    def to_engine(rule: dict[str, Any], reason: str) -> None:
        out["engine"].append({"id": rule["id"], "title": rule["title"], "reason": reason})

    # A source's alarm condition from the site's policy (signals.py: an enum in any language, a level by the policy) is
    # weighed per source by the engine — HA's automation has one list of states per rule. Its automation (if any) goes.
    for r in [r for r in rules if r.get("policy")]:
        to_engine(r, "policy")
    rules = [r for r in rules if not r.get("policy")]

    t = await texts(hass, english)
    if "automation" not in hass.config.components:
        for r in rules:
            to_engine(r, "no_automation")
        if rules:
            out["error"], out["error_code"] = t["no_automation"], "no_automation"
        if not dry_run and stored:
            await engine.async_set_native({})  # nothing in HA runs them: the agent does
        return out
    locked = bool(engine.locked is not None and engine.locked())
    coll = None if dry_run else await _helpers(hass)
    # The chatter memories of before 0.31: removed where input_text runs (no input_text — none of them either).
    colls = {"input_boolean": coll, "input_text": None if dry_run or "input_text" not in hass.config.components else _collection(hass, "input_text")}
    names = {r["action"]["entity_id"]: engine._name(r["action"]["entity_id"]) for r in rules if r.get("action")}
    file = automations.Automations(hass, lambda _eid: False)
    digest_of = automations._digest  # noqa: SLF001
    # The flat form needs HA's scripts (scripts.yaml, as HA's script editor writes it); else every rule the form before.
    flat = "script" in hass.config.components
    scripts: dict[str, Any] = {}
    sbefore: str | None = None
    if flat:
        try:
            scripts, sbefore = await alarm_form.read_scripts(hass)
        except alarm_form.NotPlainScripts:
            flat = False
            out["warnings"].append({"code": "scripts_not_plain", "title": alarm_form.SCRIPTS})
    shared_store = HaufloStore(hass, alarm_form.SHARED_STORE)
    shared_rec: dict[str, str] = dict(await shared_store.async_load() or {})
    async with automations._FILE_LOCK:  # noqa: SLF001 — the one lock every writer of automations.yaml takes
        try:
            data, before = await file._read_file()  # noqa: SLF001
        except automations.NotPlain:
            out["error"], out["error_code"] = (automations.NotPlain.EN if english else automations.NotPlain.RU), "not_plain"
            for r in rules:
                if r["id"] not in stored:  # those already in HA keep running as they are
                    to_engine(r, "not_plain")
            return out
        current = {a.get("id"): a for a in data if isinstance(a, dict)}
        native: dict[str, dict[str, Any]] = {}
        writes: dict[str, dict[str, Any] | None] = {}
        script_writes: dict[str, dict[str, Any] | None] = {}
        drop: list[str] = []
        made: dict[str, list[str]] = {}  # rule id → its helpers made in this pass (dropped when the rule isn't written, L-3)
        made_groups: dict[str, str] = {}  # rule id → its «все датчики» helper made in this pass
        rule_of: dict[str, dict[str, Any]] = {}
        flat_now: list[str] = []  # rule ids written in the flat form in this pass

        def flat_objects(rec: dict[str, Any] | None) -> tuple[Any, Any, Any]:
            rec = rec or {}
            return (current.get(rec.get("automation")), current.get(rec.get("back")) if rec.get("back") else None,
                    scripts.get(rec.get("run_script")) if rec.get("run_script") else None)

        for r in rules:
            the = ids(r["id"])
            aid = the["automation"]
            slug = aid.removeprefix(ALARM_PREFIX)
            cur, rec = current.get(aid), stored.get(r["id"])
            if cur is None and locked:
                to_engine(r, "locked")
                continue
            rule_of[aid] = r
            targets = engine._targets(r, config)
            was_flat = bool(rec and rec.get("form") == alarm_form.FORM)
            # An automation of the form before (with or without its record): as it is, unless the owner said «переписать».
            old_form = cur is not None and not was_flat
            if was_flat and not flat:  # its scripts out of reach now (scripts.yaml not plain): left as it is
                out["kept_edited"].append(r["title"])
                native[r["id"]] = dict(rec)
                continue
            if was_flat:
                if cur is not None and alarm_form.digest(*flat_objects(rec)) != rec.get("digest"):
                    # Changed in HA's own editor (the automation, its «снова в норме» or its button's script): never
                    # written over — its helpers stay as they are.
                    out["kept_edited"].append(r["title"])
                    native[r["id"]] = dict(rec)
                    continue
            elif cur is not None and not (rec and digest_of(cur) == rec.get("digest")):
                # Not Hauflo's last write: changed in HA's own editor (or no record of it) — never written over, its
                # helpers (a chatter memory of before 0.31 too) stay as they are. The same as Hauflo would write: its own.
                if digest_of(cur) != digest_of(automation_for(r, targets, t, _ack_entity(hass, the["helper"]), _ack_entity(hass, the["open"]), names=names)):
                    out["kept_edited"].append(r["title"])
                    native[r["id"]] = dict(rec) if rec else {"automation": aid, "helper": _ack_entity(hass, the["helper"]), "digest": "", "ack": the["ack"], "run": the["run"], "lang": lang}
                    continue
            if old_form and flat and not migrate:
                out["migrate"].append(r["title"])
            if flat and (not old_form or migrate):
                if await _flat_rule(hass, r, targets, t, the, slug, cur, rec, coll, names, engine, lang, out, native, writes, script_writes,
                                    made, made_groups, current, scripts, drop, to_engine, async_validate_config_item):
                    flat_now.append(r["id"])
                continue
            if coll is not None:
                made[r["id"]] = [o for o in (the["helper"], the["open"]) if o not in coll.data]
            try:
                if coll is not None:
                    ack = await _ensure_helper(hass, coll, the["helper"], _say(t, "helper", title=r["title"]), ICON)
                    opn = await _ensure_helper(hass, coll, the["open"], _say(t, "open_helper", title=r["title"]), OPEN_ICON)
                else:
                    ack, opn = _ack_entity(hass, the["helper"]), _ack_entity(hass, the["open"])
            except Exception:  # noqa: BLE001 — that rule stays with the agent, the others go on
                _LOGGER.exception("Hauflo: helpers for %s not made", r["id"])
                if cur is not None and rec:
                    native[r["id"]] = rec
                    out["warnings"].append({"code": "helper", "title": r["title"]})
                else:
                    to_engine(r, "helper")
                    drop.extend(made.pop(r["id"], []))
                continue
            cfg = automation_for(r, targets, t, ack, opn, names=names)
            digest = digest_of(cfg)
            if cur is not None and digest_of(cur) == digest:
                state = "unchanged"
            else:
                try:
                    await async_validate_config_item(hass, aid, cfg)
                except Exception as err:  # noqa: BLE001 — HA refuses it: never a broken automation in the file
                    _LOGGER.warning("Hauflo: alarm automation %s refused by HA: %s", aid, err)
                    if cur is not None and rec:
                        native[r["id"]] = rec
                        out["warnings"].append({"code": "invalid", "title": r["title"]})
                    else:
                        to_engine(r, "invalid")
                        drop.extend(made.pop(r["id"], []))
                    continue
                state = "updated" if cur is not None else "restored" if rec else "added"
                writes[aid] = cfg
            out[state].append(r["title"])
            native[r["id"]] = {"automation": aid, "helper": ack, "open": opn, "digest": digest, "ack": the["ack"], "run": the["run"], "lang": lang}
            drop.append(the["chatter"])  # its memory of before 0.31 (only once the new form is written)
        wanted = {ids(r["id"])["automation"] for r in rules}
        by_aid = {rec.get("automation"): rec for rec in stored.values()}
        # The flat form's own objects (a rule's «снова в норме», the shared ones) are never taken for a rule's automation.
        not_rules = {rec["back"] for rec in [*stored.values(), *native.values()] if rec.get("back")} | set(alarm_form.SHARED_IDS)

        def helpers_of(aid: str) -> list[str]:
            return [str((by_aid.get(aid) or {}).get("helper") or "").partition(".")[2] or _of(aid, ACK_PREFIX), _of(aid, OPEN_PREFIX), _of(aid, CHATTER_PREFIX)]

        gone_groups: list[str] = []  # the «все датчики» helpers of rules removed
        for aid, cur in current.items():
            if not isinstance(aid, str) or not aid.startswith(ALARM_PREFIX) or aid in wanted or aid in not_rules:
                continue
            rec = by_aid.get(aid)
            flat_rec = bool(rec and rec.get("form") == alarm_form.FORM)
            if rec is None or (alarm_form.digest(*flat_objects(rec)) if flat_rec else digest_of(cur)) != rec.get("digest"):
                out["warnings"].append({"code": "edited_left", "title": str(cur.get("alias") or aid)})
                continue
            writes[aid] = None
            out["removed"].append(str(cur.get("alias") or aid))
            drop += helpers_of(aid)
            if flat_rec:
                if rec.get("back") in current:
                    writes[rec["back"]] = None
                if rec.get("run_script") in scripts:
                    script_writes[rec["run_script"]] = None
                gone_groups.append(aid)
        # The shared objects: there while a rule of the flat form is; one edited by hand stays as it is.
        need = any(rec.get("form") == alarm_form.FORM for rec in native.values())
        need_button = any(rec.get("run_script") for rec in native.values() if rec.get("form") == alarm_form.FORM)
        shared_new: dict[str, str] = {}
        if flat:
            from homeassistant.components.script.config import async_validate_config_item as script_valid  # noqa: PLC0415

            wanted_shared: list[tuple[str, str, Any]] = [
                ("script", alarm_form.SCRIPT, alarm_form.push_script(t) if need else None),
                ("automation", alarm_form.ACCEPT_ID, alarm_form.accept_automation(t) if need else None),
                ("automation", alarm_form.BUTTON_ID, alarm_form.button_automation(t) if need_button else None),
            ]
            for kind, key, cfg in wanted_shared:
                cur = (scripts if kind == "script" else current).get(key)
                if cur is not None and alarm_form.digest(cur) not in (shared_rec.get(key), alarm_form.digest(cfg) if cfg else None):
                    out["warnings"].append({"code": "shared_edited", "title": str(cur.get("alias") or key)})
                    continue
                if cfg is None:
                    if cur is not None:
                        (script_writes if kind == "script" else writes)[key] = None
                    continue
                shared_new[key] = alarm_form.digest(cfg)
                if cur is not None and alarm_form.digest(cur) == shared_new[key]:
                    continue
                try:
                    await (script_valid if kind == "script" else async_validate_config_item)(hass, key, cfg)
                except Exception as err:  # noqa: BLE001 — never a broken object in the file
                    _LOGGER.warning("Hauflo: shared alarm object %s refused by HA: %s", key, err)
                    out["warnings"].append({"code": "invalid", "title": key})
                    shared_new.pop(key, None)
                    continue
                (script_writes if kind == "script" else writes)[key] = cfg
        # A chatter memory of before 0.31 whose automation is gone (deleted in HA by hand): nothing reads it any more.
        if (memories := colls["input_text"]) is not None:
            drop += [o for o in memories.data if str(o).startswith(CHATTER_PREFIX) and ALARM_PREFIX + str(o).removeprefix(CHATTER_PREFIX) not in current and o not in drop]
        script_failed = False
        if script_writes and not dry_run:
            # The scripts first: an automation never calls a script HA hasn't loaded.
            new_scripts = {k: v for k, v in {**scripts, **script_writes}.items() if v is not None}
            kept_scripts: list[str] = []
            try:
                await alarm_form.write_scripts(hass, new_scripts, sbefore, alarm_form.rebase_scripts(script_writes, scripts, kept_scripts))
                await hass.services.async_call("script", "reload", {}, blocking=True)
            except Exception as err:  # noqa: BLE001 — said in the result, the rules stay as they were
                _LOGGER.exception("Hauflo: alarm scripts not written: %s", err)
                script_failed = True
            if need and not hass.services.has_service("script", alarm_form.SCRIPT):
                _LOGGER.warning("Hauflo: script.%s not loaded — scripts.yaml isn't the scripts HA loads", alarm_form.SCRIPT)
                script_failed = True
            for key in kept_scripts:
                out["warnings"].append({"code": "edited_left", "title": key})
        if script_failed:
            out["error"], out["error_code"] = t["script_failed"], "script"
            for rule_id in flat_now:
                r = next(x for x in rules if x["id"] == rule_id)
                the = ids(rule_id)
                for key in ("added", "updated", "restored", "unchanged"):
                    if r["title"] in out[key]:
                        out[key].remove(r["title"])
                writes.pop(the["automation"], None)
                writes.pop(alarm_form.back_id(the["automation"]), None)
                drop = [o for o in drop if o not in (the["open"], the["chatter"])]
                if (rec := stored.get(rule_id)) is not None:
                    native[rule_id] = rec
                else:
                    native.pop(rule_id, None)
                    to_engine(r, "script")
                    drop.extend(made.get(rule_id, []))
                    if rule_id in made_groups:
                        gone_groups.append(made_groups[rule_id])
            for key in alarm_form.SHARED_IDS:
                writes.pop(key, None)
            shared_new = dict(shared_rec)
        if writes and not dry_run:
            kept = [writes[a.get("id")] if isinstance(a, dict) and a.get("id") in writes else a for a in data]
            new = [a for a in kept if a is not None] + [c for a, c in writes.items() if c is not None and a not in current]
            theirs: list[str] = []  # changed in HA's editor while this pass ran (M-2): left as they are now
            same = lambda a, b: digest_of(a) == digest_of(b)  # noqa: E731
            try:
                await file._write_file(new, before, yamlfile.rebase(writes, current, same, theirs))  # noqa: SLF001
            except Exception as err:  # noqa: BLE001 — nothing half-written (one atomic write): the agent keeps the new ones
                # The raw reason (paths, HA's internals) stays in HA's log; the cloud gets a code and words (security F-7).
                _LOGGER.exception("Hauflo: alarm automations not written: %s", err)
                out["error"], out["error_code"] = t["write_failed"], "write"
                for r in rules:
                    if r["id"] not in stored:
                        to_engine(r, "write")
                        native.pop(r["id"], None)
                        for object_id in made.get(r["id"], []):
                            await _drop_ours(hass, colls, object_id)
                        if r["id"] in made_groups:
                            await alarm_form.drop_group(hass, made_groups[r["id"]])
                return out
            for aid in theirs:
                writes.pop(aid, None)
                if aid in not_rules or aid.endswith(alarm_form.BACK):
                    continue
                if (r := rule_of.get(aid)) is None:  # a removal: the automation and its helpers stay
                    gone = set(helpers_of(aid))
                    drop = [o for o in drop if o not in gone]
                    gone_groups = [g for g in gone_groups if g != aid]
                    out["removed"] = [x for x in out["removed"] if x != str((current.get(aid) or {}).get("alias") or aid)]
                    out["warnings"].append({"code": "edited_left", "title": str((current.get(aid) or {}).get("alias") or aid)})
                    continue
                drop = [o for o in drop if o not in (ids(r["id"])["chatter"], ids(r["id"])["open"])]  # its automation as it is now: its memories too
                for key in ("added", "updated", "restored"):
                    if r["title"] in out[key]:
                        out[key].remove(r["title"])
                if (rec := stored.get(r["id"])) is not None:
                    out["kept_edited"].append(r["title"])
                    native[r["id"]] = rec
                else:  # never in HA before: the agent keeps it
                    native.pop(r["id"], None)
                    to_engine(r, "write")
                    drop.extend(made.get(r["id"], []))
                    if r["id"] in made_groups:
                        gone_groups.append(made_groups[r["id"]])
    if not dry_run:
        reg = er.async_get(hass)
        if writes:
            await hass.services.async_call("automation", "reload", {}, blocking=True)
            for aid, cfg in writes.items():
                if cfg is None and (eid := reg.async_get_entity_id("automation", "automation", aid)):
                    reg.async_remove(eid)
        if script_writes and not script_failed:
            for key, cfg in script_writes.items():
                if cfg is None and (eid := reg.async_get_entity_id("script", "script", key)):
                    reg.async_remove(eid)
        for object_id in drop:
            await _drop_ours(hass, colls, object_id)
        for object_id in gone_groups:
            await alarm_form.drop_group(hass, object_id)
        if flat and shared_new != shared_rec:
            await shared_store.async_save(shared_new)
        if flat_recs := [rec for rec in native.values() if rec.get("form") == alarm_form.FORM]:
            try:
                alarm_form.categorize(hass, t["category"], alarm_form.category_members(hass, flat_recs))
            except Exception:  # noqa: BLE001 — only the lists' grouping
                _LOGGER.exception("Hauflo: alarm category not set")
        await engine.async_set_native(native)
    if old := [str(a.get("alias") or a.get("id")) for a in data if isinstance(a, dict) and str(a.get("id") or "").startswith(PREFIX)]:
        out["warnings"].append({"code": "double", "title": ", ".join(old)})
    return out


async def _flat_rule(hass: HomeAssistant, r: dict[str, Any], targets: list[dict[str, Any]], t: dict[str, str], the: dict[str, str], slug: str, cur: Any,
                     rec: dict[str, Any] | None, coll: Any, names: dict[str, str], engine: NotifyEngine, lang: str, out: dict[str, Any],
                     native: dict[str, Any], writes: dict[str, Any], script_writes: dict[str, Any], made: dict[str, list[str]], made_groups: dict[str, str],
                     current: dict[str, Any], scripts: dict[str, Any], drop: list[str], to_engine: Any, validate: Any) -> bool:
    """One rule in the flat form (alarm_form.py): its helpers, automation(s) and button script. True — written (or the
    same already); False — left as it was / to the agent (said in ``out``)."""
    from homeassistant.components.script.config import async_validate_config_item as script_valid  # noqa: PLC0415

    aid = the["automation"]
    back_key = alarm_form.back_id(aid)
    run_key = alarm_form.run_key(slug)
    was_flat = bool(rec and rec.get("form") == alarm_form.FORM)
    if coll is not None:
        made[r["id"]] = [o for o in (the["helper"],) if o not in coll.data]
    try:
        if coll is not None:
            ack = await _ensure_helper(hass, coll, the["helper"], _say(t, "helper", title=r["title"]), ICON)
            if aid not in alarm_form.our_entries(hass):
                made_groups[r["id"]] = aid
            group = await alarm_form.ensure_group(hass, aid, r, _say(t, "group", title=r["title"]))
        else:
            ack, group = _ack_entity(hass, the["helper"]), alarm_form.group_entity(hass, aid)
    except Exception:  # noqa: BLE001 — that rule stays as it was / with the agent, the others go on
        _LOGGER.exception("Hauflo: helpers for %s not made", r["id"])
        if cur is not None and rec:
            native[r["id"]] = rec
            out["warnings"].append({"code": "helper", "title": r["title"]})
        else:
            to_engine(r, "helper")
            drop.extend(made.pop(r["id"], []))
            if r["id"] in made_groups:
                await alarm_form.drop_group(hass, made_groups.pop(r["id"]))
        return False
    act = r.get("action") if (r.get("action") or {}).get("service") in RUN_SERVICES else None
    button_text = button = None
    if act:
        # Cut as the person reads it, then made a literal (a cut literal would break the template).
        button_text = _say(t, "close" if act["service"] == "valve.close_valve" else "off", name=names.get(act["entity_id"]) or act["entity_id"])[:60]
        button = _lit(button_text)
    cfgs = alarm_form.rule_configs(
        r, targets, t, ack, group, slug=slug, aid=aid, hold_groups=alarm_groups(r, list(r["sources"])), hold=alarm_hold(r), back_s=back_after(r),
        outputs_on=_outputs(r, "on_start"), outputs_off=_outputs(r, "on_end"), button=button, button_text=button_text, act=act,
        title=_lit(r["title"]), names={e: engine._name(e) for e in r["sources"]},  # noqa: SLF001
        # «кому: Иван, Мария» — the people of the rule's phones (a phone without its person by its own name).
        who=", ".join(dict.fromkeys(engine._name(p["person"]) if p.get("person") and hass.states.get(p["person"]) else str(p.get("name") or p["service"]) for p in targets)))  # noqa: SLF001
    digest = alarm_form.digest(cfgs["automation"], cfgs["back"], cfgs["run"])
    if was_flat and cur is not None and alarm_form.digest(cur, current.get(back_key), scripts.get(run_key)) == digest:
        state = "unchanged"
    else:
        try:
            await validate(hass, aid, cfgs["automation"])
            if cfgs["back"]:
                await validate(hass, back_key, cfgs["back"])
            if cfgs["run"]:
                await script_valid(hass, run_key, cfgs["run"])
        except Exception as err:  # noqa: BLE001 — HA refuses it: never a broken automation in the file
            _LOGGER.warning("Hauflo: alarm automation %s refused by HA: %s", aid, err)
            if cur is not None and rec:
                native[r["id"]] = rec
                out["warnings"].append({"code": "invalid", "title": r["title"]})
            else:
                to_engine(r, "invalid")
                drop.extend(made.pop(r["id"], []))
                if r["id"] in made_groups:
                    await alarm_form.drop_group(hass, made_groups.pop(r["id"]))
            return False
        state = "updated" if cur is not None else "restored" if rec else "added"
        writes[aid] = cfgs["automation"]
        if cfgs["back"] is not None or back_key in current:
            writes[back_key] = cfgs["back"]
        if cfgs["run"] is not None or run_key in scripts:
            script_writes[run_key] = cfgs["run"]
    out[state].append(r["title"])
    native[r["id"]] = {"automation": aid, "helper": ack, "group": group, "back": back_key if cfgs["back"] else None, "run_script": run_key if cfgs["run"] else None,
                       "digest": digest, "ack": OK_ACTION + slug.upper(), "run": DO_ACTION + slug.upper(), "lang": lang, "form": alarm_form.FORM}
    # The form before's memories (its «авария открыта», a chatter memory of before 0.31): nothing reads them any more.
    drop += [the["open"], the["chatter"]]
    return True


async def async_remove_all(hass: HomeAssistant, engine: NotifyEngine | None) -> list[str]:
    """«Убрать аварии Hauflo» (hauflo.remove_alarms, HA's admin): every hauflo_alarm_* automation, hauflo_ack_* / hauflo_open_* helper (and a
    hauflo_chatter_* of before 0.31) goes — edited ones too (the admin asked for exactly that); the agent's engine takes the rules back. Returns the
    aliases removed. NotPlain when automations.yaml isn't plain (nothing removed)."""
    file = automations.Automations(hass, lambda _eid: False)
    async with automations._FILE_LOCK:  # noqa: SLF001
        data, before = await file._read_file()  # noqa: SLF001
        ours = [a for a in data if isinstance(a, dict) and str(a.get("id") or "").startswith(ALARM_PREFIX)]
        if ours:

            def others(rows: list[Any]) -> list[Any]:
                return [a for a in rows if not (isinstance(a, dict) and str(a.get("id") or "").startswith(ALARM_PREFIX))]

            await file._write_file(others(data), before, others)  # noqa: SLF001
    if ours:
        await hass.services.async_call("automation", "reload", {}, blocking=True)
        reg = er.async_get(hass)
        for a in ours:
            if eid := reg.async_get_entity_id("automation", "automation", str(a["id"])):
                reg.async_remove(eid)
    # The flat form's (Р83): the push / button scripts after the automations calling them, the «все датчики» helpers.
    gone_scripts: list[str] = []
    if "script" in hass.config.components:

        def mine(key: Any) -> bool:
            return key == alarm_form.SCRIPT or str(key).startswith(RUN_PREFIX)

        try:
            scripts, sbefore = await alarm_form.read_scripts(hass)
        except alarm_form.NotPlainScripts:
            _LOGGER.warning("Hauflo: scripts.yaml isn't plain — the alarm scripts stay")
            scripts, sbefore = {}, None
        if gone := {k: v for k, v in scripts.items() if mine(k)}:
            await alarm_form.write_scripts(hass, {k: v for k, v in scripts.items() if not mine(k)}, sbefore, lambda fresh: {k: v for k, v in fresh.items() if not mine(k)})
            await hass.services.async_call("script", "reload", {}, blocking=True)
            reg = er.async_get(hass)
            for key, cfg in gone.items():
                gone_scripts.append(str((cfg or {}).get("alias") or key))
                if eid := reg.async_get_entity_id("script", "script", key):
                    reg.async_remove(eid)
    await alarm_form.drop_all_groups(hass)
    await HaufloStore(hass, alarm_form.SHARED_STORE).async_save({})
    for domain, prefix in _KIND:
        coll = _collection(hass, domain)
        for object_id in [k for k in (coll.data if coll is not None else {}) if str(k).startswith(prefix)]:
            await _drop_helper(hass, coll, object_id)
    if engine is not None:
        await engine.async_set_native({})
    return [str(a.get("alias") or a.get("id")) for a in ours] + gone_scripts


async def written_english(hass: HomeAssistant, engine: NotifyEngine | None) -> bool:
    """One language for everything said about the alarm automations: the one they were written in (the project's, kept
    with their records), else the engine's (the project's or HA's) — never HA's beside a project's other (5B e2e D-4)."""
    stored = engine.native if engine is not None else await HaufloStore(hass, NATIVE_STORE).async_load()
    lang = next((r["lang"] for r in (stored or {}).values() if isinstance(r, dict) and r.get("lang") in ("ru", "en")), None)
    if lang:
        return lang == "en"
    if engine is not None:
        return engine._english()  # noqa: SLF001
    return not site_ru(hass.config.language)


SERVICE = "remove_alarms"
NOTE = f"{DOMAIN}_alarms_kept"


def async_setup_service(hass: HomeAssistant, engine_of: Any) -> None:
    """``hauflo.remove_alarms`` — HA's admin in the house only (never the agent's own tunnel user, an admin too)."""
    if hass.services.has_service(DOMAIN, SERVICE):
        return

    async def handle(call: ServiceCall) -> None:
        user = await hass.auth.async_get_user(call.context.user_id) if call.context.user_id else None
        if user is None or user.system_generated or not user.is_admin:
            raise Unauthorized(context=call.context)
        try:
            await async_remove_all(hass, engine_of())
        except automations.NotPlain as err:
            t = await texts(hass, await written_english(hass, engine_of()))
            raise HomeAssistantError(t["not_plain"]) from err
        persistent_notification.async_dismiss(hass, NOTE)

    async_register_admin_service(hass, DOMAIN, SERVICE, handle)


async def async_removed(hass: HomeAssistant) -> None:
    """The integration removed: the alarm automations keep working (deliberately) — HA's owner told so, and how to take
    them away. Their record stays: the agent added again knows them as its own."""
    if not await HaufloStore(hass, NATIVE_STORE).async_load():
        return
    t = await texts(hass, await written_english(hass, None))
    persistent_notification.async_create(hass, t["kept"], t["kept_title"], NOTE)


async def async_export(hass: HomeAssistant, engine: NotifyEngine | None) -> dict[str, Any]:
    """«Сделать дом независимым»: cards kept for good; the alarm rules as HA's own automations — the same sync as a
    deploy (they usually are there already). Returns what was done (shown to the owner)."""
    out: dict[str, Any] = {"cards": None, **_empty()}
    try:
        out["cards"] = await keep.async_make_independent(hass, ASSET_TAG)
    except Exception as err:  # noqa: BLE001 — the automations are still worth making
        _LOGGER.exception("Hauflo: cards not kept")
        out["cards"] = f"error: {err}"
    if engine is None:
        return out
    try:
        out.update(await async_sync(hass, engine))
    except Exception as err:  # noqa: BLE001 — said to the owner
        _LOGGER.exception("Hauflo: alarm rules not exported")
        out["error"] = str(err)
    out["skipped"] = [r["title"] for r in engine.config.get("rules") or [] if not (r.get("enabled") and r.get("sources"))]
    return out


def summary(result: dict[str, Any], ru: bool) -> str:
    """The result in the owner's words (the options flow's last step and a notification)."""
    lines: list[str] = []
    cards = result.get("cards")
    if cards == keep.ADDED:
        lines.append("✓ Карточки панели: /config/www/hauflo, подключены ресурсами панелей." if ru else "✓ Dashboard cards: /config/www/hauflo, added as dashboard resources.")
    elif cards == keep.YAML:
        lines.append("! Карточки скопированы в /config/www/hauflo, но ресурсы панелей заданы в configuration.yaml — добавьте их (см. уведомление)." if ru
                     else "! Cards copied to /config/www/hauflo, but dashboard resources live in configuration.yaml — add them (see the notification).")
    else:
        lines.append(f"! Карточки не сохранены: {cards}" if ru else f"! Cards not kept: {cards}")
    if result.get("error"):
        lines.append(f"! Автоматизации не созданы: {result['error']}" if ru else f"! Automations not made: {result['error']}")
    made = [*(result.get("added") or []), *(result.get("updated") or []), *(result.get("restored") or []), *(result.get("unchanged") or []), *(result.get("kept_edited") or [])]
    if made:
        lines.append(("✓ Аварии работают в Home Assistant сами (автоматизации «Авария: …», помощники «Hauflo · принято: …»): " if ru
                      else "✓ Alarms run in Home Assistant itself (automations “Alarm: …”, helpers “Hauflo · accepted: …”): ") + ", ".join(made))
    if kept := result.get("kept_edited"):
        lines.append(("Изменены вручную — оставлены как есть: " if ru else "Edited by hand — left as they are: ") + ", ".join(kept))
    if left := result.get("engine"):
        lines.append(("! Остались у агента Hauflo: " if ru else "! Left with the Hauflo agent: ") + ", ".join(str(e["title"]) for e in left))
    if skipped := result.get("skipped"):
        lines.append(("Выключенные / пустые правила пропущены: " if ru else "Disabled / empty rules skipped: ") + ", ".join(str(x) for x in skipped))
    for w in result.get("warnings") or []:
        if w.get("code") == "double":
            lines.append(f"! Старые автоматизации «независимого дома» ({w['title']}): авария придёт дважды — удалите их." if ru
                         else f"! Old “independent house” automations ({w['title']}): an alarm comes twice — delete them.")
    if not made and not result.get("error") and not result.get("engine"):
        lines.append("Правил аварий нет — автоматизации не нужны." if ru else "No alarm rules — no automations needed.")
    return "\n".join(lines)
