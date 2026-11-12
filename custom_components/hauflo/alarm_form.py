"""Р83: an alarm rule as an integrator writes it by hand in HA's editor (the Г2 form, docs/reference-ha-gorkogo2.md).

Per rule (independent.async_sync writes them, the same plain-file check, lock and backup as before):
- ``input_boolean.hauflo_ack_<rule>`` — «Принято» (as before);
- ``binary_sensor.hauflo_alarm_<rule>`` «<Правило> · все датчики» — HA's own group helper of the rule's binary sensors
  (on = a sensor in alarm); a rule of levels / other states / a link sensor — HA's own template helper saying the same;
- ``automation hauflo_alarm_<rule>`` «Авария: <правило>» — triggers: its sensors in alarm for the rule's hold; actions in
  a line: «Принято» off → the push (and sirens) → repeats while nobody accepted it and the group is on (a few at the
  rule's pace, then hourly) → «Принято» said / the notice gone;
- ``automation hauflo_alarm_<rule>_back`` «Авария: <правило> — снова в норме» — a critical rule only: the group off for
  the back window, no sensor without data → «снова в норме»;
- ``script hauflo_run_<rule>`` — a rule with a push button («Закрыть: Кран»): the service, then «Принято».
Shared by all rules: ``script.hauflo_push`` (the push to the rule's phones — words, names of the sensors in alarm,
critical per OS, buttons, HA's notice), the automations «Hauflo: «Принято» с телефона» and «Hauflo: кнопка из пуша».
A chattering sensor, «нет связи», the summary and quiet hours stay with the agent's engine (notify.py) — no memory in YAML.
All words — the agent's translations (exceptions.alarm_native_*), in the house's language.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import category_registry as cr, entity_registry as er

from . import yamlfile
from .scenes import KEEP_BACKUPS as SCENE_BACKUPS
from .const import ACK_PREFIX, ALARM_PREFIX, DO_ACTION, NATIVE_STORE, OK_ACTION, PUSH_SCRIPT, RUN_PREFIX
from .notify import ACTION_EVENT, LINK_KINDS, LIST_MAX, REPEAT_SLOW, _T, push_data

_LOGGER = logging.getLogger(__name__)
FORM = int(_T["native_form"])  # the flat form (Р83); a record without it — the form before
SCRIPT = PUSH_SCRIPT
SHARED_STORE = NATIVE_STORE + "_shared"  # the shared objects' digests as last written (one edited by hand stays)
ACCEPT_ID = ALARM_PREFIX + "accept"  # the shared automations (a rule's slug always ends in _<6 hex>: never these)
BUTTON_ID = ALARM_PREFIX + "button"
SHARED_IDS = (ACCEPT_ID, BUTTON_ID)
BACK = "_back"
SCRIPTS = "scripts.yaml"
SCRIPTS_BACKUP = SCRIPTS + ".hauflo-bak-"
KEEP_BACKUPS = SCENE_BACKUPS  # as many as automations.yaml / scenes.yaml keep
FAST_ROUNDS = int(_T["native_fast_rounds"])  # the push and its first repeats at the rule's pace, then REPEAT_SLOW
NO_DATA = ["unavailable", "unknown"]
GROUP_ICON = "mdi:alarm-light-outline"


class NotPlainScripts(Exception):
    """scripts.yaml isn't as HA's script editor writes it (hand formatting, !include, comments): never rewritten."""


def run_key(slug: str) -> str:
    return RUN_PREFIX + slug


def back_id(aid: str) -> str:
    return aid + BACK


def grouped(rule: dict[str, Any]) -> bool:
    """HA's group helper fits: every source a binary sensor, alarm = «on» (a group is on when any member is)."""
    return (not rule.get("thresholds") and rule.get("kind") not in LINK_KINDS and list(rule.get("alarm_states") or []) == ["on"]
            and all(str(e).startswith("binary_sensor.") for e in rule["sources"]))


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def helper_state(rule: dict[str, Any]) -> str:
    """The template helper's state for a rule a group doesn't fit: on while a source is in alarm — a level until it is
    back past its threshold by the step (battery < 20 → alarm, > 25 → back: the open alarm's way, notify.in_alarm)."""
    levels: dict[str, dict[str, float]] = rule.get("thresholds") or {}
    states = list(rule.get("alarm_states") or [])
    parts: list[str] = []
    for e in rule["sources"]:
        th = levels.get(e)
        if th and "below" in th:
            edge = th["below"] + th["step"]
            parts.append(f"states('{e}') | float({_num(edge + 1)}) <= {_num(edge)}")
        elif th:
            edge = th["above"] - th["step"]
            parts.append(f"states('{e}') | float({_num(edge - 1)}) >= {_num(edge)}")
        elif len(states) == 1:
            parts.append(f"is_state('{e}', '{states[0]}')")
        else:
            parts.append(f"states('{e}') in {json.dumps(states, ensure_ascii=False)}")
    return "{{ " + " or ".join(parts or ["false"]) + " }}"


# --- the shared script ------------------------------------------------------------------------------------------------

def _name(e: str) -> str:
    """An entity's name in the push: its friendly name, else its object id in words (never the raw id)."""
    return f"(state_attr({e}, 'friendly_name') or ({e}.split('.')[1] | replace('_', ' ') | capitalize))"


# The rule's sensors in alarm now, named: «Протечка кухня · Кухня, …» — up to LIST_MAX, then «+N». A group's members
# (its own attribute), else the rule's `sources`; a level says its value («Батарея · Кухня (18 %)»).
_NAMES = (
    "{%- set ns = namespace(o=[]) -%}"
    "{%- for e in (sources | default([]) or state_attr(group, 'entity_id') or []) -%}"
    "{%- set th = (levels | default({})).get(e) -%}{%- set s = states(e) -%}"
    "{%- if (th and s | is_number and ((s | float < th.below) if th.below is defined else (s | float > th.above)))"
    " or (not th and s in (alarm_states | default(['on']))) -%}"
    "{%- set a = area_name(e) -%}{%- set u = state_attr(e, 'unit_of_measurement') -%}"
    "{%- set ns.o = ns.o + [" + _name("e") + " ~ (' · ' ~ a if a else '') ~ ((' (' ~ s ~ ((' ' ~ u) if u else '') ~ ')') if th else '')] -%}"
    "{%- endif -%}{%- endfor -%}"
    "{{ ns.o[:" + str(LIST_MAX) + "] | join(', ') }}{% if ns.o | count > " + str(LIST_MAX) + " %} +{{ ns.o | count - " + str(LIST_MAX) + " }}{% endif %}"
)


def _jinja(text: str, **parts: str) -> str:
    """A translation with its {placeholders} as Jinja expressions."""
    for k, v in parts.items():
        text = text.replace("{" + k + "}", "{{ " + v + " }}")
    return text


def push_script(t: dict[str, str]) -> dict[str, Any]:
    """``script.hauflo_push`` — one push of an alarm to the rule's phones, every phone on its own (a failing one never
    holds the others). What it says by ``say``: alarm (repeat ≥ 2 — «никто не подтвердил») / repeat / back / accepted /
    done; HA's notice with the alarm's tag made on the alarm and gone on «Принято» / back."""
    crit = {**push_data("android", tag="", critical=True), **push_data("ios", tag="", critical=True)}
    quiet = {**push_data("android", tag="", quiet=True), **push_data("ios", tag="", quiet=True)}
    crit.pop("tag")
    quiet.pop("tag")
    fields = {
        "rule": {"name": t["f_rule"], "required": True, "selector": {"text": {}}},
        "say": {"name": t["f_say"], "required": True, "default": "alarm",
                "selector": {"select": {"options": ["alarm", "repeat", "back", "accepted", "done"]}}},
        "round": {"name": t["f_round"], "default": 1, "selector": {"number": {"min": 1, "max": 1000}}},
        "critical": {"name": t["f_critical"], "default": False, "selector": {"boolean": {}}},
        "quiet": {"name": t["f_quiet"], "default": False, "selector": {"boolean": {}}},
        "tag": {"name": t["f_tag"], "required": True, "selector": {"text": {}}},
        "targets": {"name": t["f_targets"], "required": True, "selector": {"object": {}}},
        "group": {"name": t["f_group"], "selector": {"entity": {"domain": "binary_sensor"}}},
        "buttons": {"name": t["f_buttons"], "selector": {"object": {}}},
        "button": {"name": t["f_button"], "selector": {"text": {}}},
        "note": {"name": t["f_note"], "selector": {"select": {"options": ["create", "dismiss"]}}},
        "sources": {"name": t["f_sources"], "selector": {"entity": {"multiple": True}}},
        "alarm_states": {"name": t["f_states"], "selector": {"object": {}}},
        "levels": {"name": t["f_levels"], "selector": {"object": {}}},
    }
    title = ("{%- if say == 'back' -%}" + _jinja(t["back_title"], title="rule") + "{%- else -%}" + _jinja(t["title"], title="rule") + "{%- endif -%}")
    names = "(names or rule)"
    message = (
        "{%- if say == 'back' -%}" + t["back_all"]
        + "{%- elif say == 'accepted' -%}" + t["accepted"]
        + "{%- elif say == 'done' -%}" + _jinja(t["done"], button="button | default('')")
        + "{%- elif say == 'repeat' or (round | int(1)) > 1 -%}" + _jinja(t["repeat"], names=names)
        + "{%- else -%}" + _jinja(t["alarm"], names=names) + "{%- endif -%}"
    )
    data = ("{%- set d = {'tag': tag} -%}{%- if buttons | default([]) -%}{%- set d = dict(d, actions=buttons) -%}{%- endif -%}"
            "{%- if critical -%}{%- set d = dict(d, **critical_data) -%}{%- elif quiet or say in ['accepted', 'done'] -%}{%- set d = dict(d, **quiet_data) -%}{%- endif -%}"
            "{{ d }}")
    return {
        "alias": t["script_alias"],
        "description": t["script_description"],
        "icon": "mdi:bell-ring",
        "mode": "parallel",
        "max": 100,
        "fields": fields,
        "sequence": [
            # A field left out of the call is undefined in HA (its `default` is the editor's only): set once here.
            {"variables": {"round": "{{ round | default(1) | int(1) }}", "critical": "{{ critical | default(false) }}",
                           "quiet": "{{ quiet | default(false) }}", "buttons": "{{ buttons | default([]) }}",
                           "button": "{{ button | default('') }}", "note": "{{ note | default('') }}",
                           "critical_data": crit, "quiet_data": quiet}},
            {"variables": {"names": _NAMES}},
            {"variables": {"title": title, "message": message, "data": data}},
            {"if": [{"condition": "template", "value_template": "{{ note | default('') == 'create' }}"}],
             "then": [{"action": "persistent_notification.create", "data": {"notification_id": "{{ tag }}", "title": "{{ title }}", "message": "{{ message }}"}}]},
            {"if": [{"condition": "template", "value_template": "{{ note | default('') == 'dismiss' }}"}],
             "then": [{"action": "persistent_notification.dismiss", "data": {"notification_id": "{{ tag }}"}}]},
            {"repeat": {"for_each": "{{ targets }}",
                        "sequence": [{"action": "{{ repeat.item }}", "continue_on_error": True,
                                      "data": {"title": "{{ title }}", "message": "{{ message }}", "data": "{{ data }}"}}]}},
        ],
    }


def accept_automation(t: dict[str, str]) -> dict[str, Any]:
    """«Принято» from any rule's push: that rule's helper on (its automation's wait ends: the repeats stop)."""
    n = len(OK_ACTION)
    return {
        "id": ACCEPT_ID,
        "alias": t["accept_alias"],
        "description": t["accept_description"],
        "mode": "queued",
        "triggers": [{"trigger": "event", "event_type": ACTION_EVENT}],
        "conditions": [{"condition": "template", "value_template": "{{ (trigger.event.data.action | string).startswith('" + OK_ACTION + "') }}"}],
        "actions": [{"action": "input_boolean.turn_on",
                     "target": {"entity_id": "input_boolean." + ACK_PREFIX + "{{ trigger.event.data.action[" + str(n) + ":] | lower }}"}}],
    }


def button_automation(t: dict[str, str]) -> dict[str, Any]:
    """A rule's button from its push («Закрыть: Кран»): that rule's script."""
    n = len(DO_ACTION)
    return {
        "id": BUTTON_ID,
        "alias": t["button_alias"],
        "description": t["button_description"],
        "mode": "queued",
        "triggers": [{"trigger": "event", "event_type": ACTION_EVENT}],
        "conditions": [{"condition": "template", "value_template": "{{ (trigger.event.data.action | string).startswith('" + DO_ACTION + "') }}"}],
        "actions": [{"action": "script.turn_on",
                     "target": {"entity_id": "script." + RUN_PREFIX + "{{ trigger.event.data.action[" + str(n) + ":] | lower }}"}}],
    }


# --- one rule -----------------------------------------------------------------------------------------------------------

def _helper(entity_id: str, on: bool) -> dict[str, Any]:
    return {"action": f"input_boolean.turn_{'on' if on else 'off'}", "target": {"entity_id": entity_id}}


def _is(entity_id: str, state: str) -> dict[str, Any]:
    return {"condition": "state", "entity_id": entity_id, "state": state}


def _when(template: str) -> dict[str, Any]:
    return {"condition": "template", "value_template": template}


def _push(base: dict[str, Any], **data: Any) -> dict[str, Any]:
    return {"action": "script." + SCRIPT, "data": {**base, **data}}


def triggers(rule: dict[str, Any], hold_groups: list[tuple[list[str], int]], hold: int) -> list[dict[str, Any]]:
    """The rule's sensors in alarm for its hold: HA's own `state` (`to:` without `from:` — a sensor waking up from no
    data in alarm is an alarm too) / `numeric_state` (a level below / above its threshold, one trigger per threshold);
    a link sensor's «off» only after it was seen «on» (never from no data)."""
    states = list(rule["alarm_states"])
    levels: dict[str, dict[str, float]] = rule.get("thresholds") or {}
    to: Any = states[0] if len(states) == 1 else states
    link = {"not_from": NO_DATA} if rule.get("kind") in LINK_KINDS else {}
    out: list[dict[str, Any]] = []
    for eids, s in hold_groups:
        signals = [e for e in eids if e not in levels]
        if signals:
            out.append({"trigger": "state", "entity_id": signals, "to": to, **link, "for": {"seconds": s}})
        by: dict[tuple[str, float], list[str]] = {}
        for e in eids:
            if th := levels.get(e):
                way = "below" if "below" in th else "above"
                by.setdefault((way, th[way]), []).append(e)
        for (way, value), es in by.items():
            out.append({"trigger": "numeric_state", "entity_id": es, way: value, "for": {"seconds": s}})
    return out


def rule_configs(rule: dict[str, Any], phones: list[dict[str, Any]], t: dict[str, str], ack: str, group: str, *, slug: str, aid: str,
                 hold_groups: list[tuple[list[str], int]], hold: int, back_s: int, outputs_on: list[dict[str, Any]], outputs_off: list[dict[str, Any]],
                 button: str | None, button_text: str | None, act: dict[str, Any] | None, title: str, names: dict[str, str], who: str) -> dict[str, Any]:
    """The rule's objects: ``automation``, ``back`` (a critical rule's «снова в норме», else None) and ``run`` (its push
    button's script, else None). ``title`` — the rule's title as a literal (independent._lit), ``button`` — the button's
    words («Закрыть: Кран») as a literal too, ``button_text`` — as they are (the script's name)."""
    critical = rule.get("priority") == "critical"
    targets = [f"notify.{p['service']}" for p in phones]
    base: dict[str, Any] = {"rule": title, "tag": aid, "targets": targets, "group": group}
    if not grouped(rule):
        base["sources"] = list(rule["sources"])
        if list(rule["alarm_states"]) != ["on"]:
            base["alarm_states"] = list(rule["alarm_states"])
        if rule.get("thresholds"):
            base["levels"] = rule["thresholds"]
    alarm = {**base, **({"critical": True} if critical else {})}
    if act:
        alarm["buttons"] = [{"action": DO_ACTION + slug.upper(), "title": button, "destructive": True, "authenticationRequired": True},
                            {"action": OK_ACTION + slug.upper(), "title": t["accept"]}]
    else:
        alarm["buttons"] = [{"action": OK_ACTION + slug.upper(), "title": t["accept"]}]
    wait = {"wait_template": "{{ is_state('" + ack + "', 'on') or is_state('" + group + "', 'off') }}"}
    repeat = int(rule.get("repeat_min") or 0)
    if repeat:
        slow = max(repeat, REPEAT_SLOW)
        escalation: list[dict[str, Any]] = [
            # The push, then again every `repeat` minutes nobody accepted it — FAST_ROUNDS pushes in all.
            {"repeat": {"sequence": [_push(alarm, say="alarm", round="{{ repeat.index }}", note="create"), {**wait, "timeout": {"minutes": repeat}}],
                        "until": [_when("{{ wait.completed or repeat.index >= " + str(FAST_ROUNDS) + " }}")]}},
            # Still nobody, the alarm still on: hourly until «Принято» or back.
            {"if": [_is(ack, "off"), _is(group, "on")],
             "then": [{"repeat": {"sequence": [_push(alarm, say="repeat", note="create"), {**wait, "timeout": {"minutes": slow}}],
                                  "until": [_when("{{ wait.completed }}")]}}]},
        ]
    else:
        escalation = [_push(alarm, say="alarm", note="create"), wait]
    actions = [
        _helper(ack, False),
        *outputs_on,
        *escalation,
        # «Принято» (a push, the card, the helper): said quietly; else back — the notice goes (a critical rule's «снова в
        # норме» is its own automation's).
        {"if": [_is(ack, "on")], "then": [*outputs_off, _push(base, say="accepted", note="dismiss")],
         "else": [*outputs_off, {"action": "persistent_notification.dismiss", "data": {"notification_id": aid}}]},
    ]
    who = who or t["nobody"]
    shown = [names.get(e) or e for e in rule["sources"]]
    watched = ", ".join(shown[:LIST_MAX]) + (f" +{len(shown) - LIST_MAX}" if len(shown) > LIST_MAX else "")
    plan = (t["repeat_plan"].format(every=repeat, more=FAST_ROUNDS - 1, slow=max(repeat, REPEAT_SLOW)) if repeat else t["no_repeat_plan"])
    automation = {
        "id": aid,
        "alias": t["alias"].format(title=rule["title"]),
        "description": t["rule_description"].format(sources=watched, hold=hold, phones=who, repeat=plan, helper=t["helper"].format(title=rule["title"]),
                                                    group=t["group"].format(title=rule["title"])),
        "mode": "restart",
        "triggers": triggers(rule, hold_groups, hold),
        "conditions": [],
        "actions": actions,
    }
    back = None
    if critical:
        back = {
            "id": back_id(aid),
            "alias": t["back_alias"].format(title=rule["title"]),
            "description": t["back_description"].format(title=rule["title"], back=back_s, phones=who),
            "mode": "single",
            "triggers": [
                {"trigger": "state", "entity_id": group, "from": "on", "to": "off", "for": {"seconds": back_s}},
                # A sensor back from «без связи» straight to normal (Р83 acceptance С2): the group was off already — no
                # on → off; the alarm told stays open without this.
                {"trigger": "state", "entity_id": list(rule["sources"]), "from": NO_DATA, "to": "off", "for": {"seconds": back_s}},
            ],
            "conditions": [
                {"condition": "state", "entity_id": group, "state": "off"},
                # A sensor without data is no «в норме».
                {"condition": "not", "conditions": [{"condition": "state", "entity_id": list(rule["sources"]), "state": NO_DATA, "match": "any"}]},
                # The alarm was told since the last «снова в норме» (a flicker shorter than the hold said nothing, says
                # nothing back; a sensor back from «без связи» without an alarm says nothing either).
                _when("{% set told = states.automation | selectattr('attributes.id', 'defined') | selectattr('attributes.id', 'eq', '" + aid + "')"
                      " | map(attribute='attributes.last_triggered') | first %}"
                      "{{ told is not none and (this.attributes.last_triggered is none or told > this.attributes.last_triggered) }}"),
            ],
            "actions": [*outputs_off, _push(base, say="back", note="dismiss")],
        }
    run = None
    if act:
        run = {
            "alias": t["run_alias"].format(title=rule["title"], button=button_text),
            "description": t["run_description"].format(title=rule["title"], button=button_text),
            "icon": "mdi:gesture-tap-button",
            "mode": "single",
            "sequence": [
                # The service first, no «continue»: when it fails the alarm stays unaccepted and keeps repeating.
                {"action": act["service"], "target": {"entity_id": act["entity_id"]}},
                _push(base, say="done", button=button, tag=aid + "_done"),
                _helper(ack, True),
            ],
        }
    return {"automation": automation, "back": back, "run": run}


def digest(*cfgs: Any) -> str:
    """What a rule's objects do together (each without its description — automations._digest's rule)."""
    def one(cfg: Any) -> str:
        if not isinstance(cfg, dict):
            return ""
        body = {k: v for k, v in cfg.items() if k not in ("description", "initial_state")}
        return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

    return hashlib.sha1("|".join(one(c) for c in cfgs).encode()).hexdigest()


# --- scripts.yaml -------------------------------------------------------------------------------------------------------

async def read_scripts(hass: HomeAssistant) -> tuple[dict[str, Any], str | None]:
    data, before = await yamlfile.async_read(hass, hass.config.path(SCRIPTS), NotPlainScripts, dict)
    return dict(data), before


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


# --- the rule's «все датчики» helper ------------------------------------------------------------------------------------

def our_entries(hass: HomeAssistant) -> dict[str, Any]:
    """The rules' group / template helpers by their object id (made with it as the name: options «name»)."""
    out: dict[str, Any] = {}
    for domain in ("group", "template"):
        for entry in hass.config_entries.async_entries(domain):
            name = str(entry.options.get("name") or "")
            if name.startswith(ALARM_PREFIX):
                out[name] = entry
    return out


def group_entity(hass: HomeAssistant, object_id: str) -> str:
    """The helper's entity (renamed in HA — still found by its entry), else the id it will get."""
    if (entry := our_entries(hass).get(object_id)) is not None:
        reg = er.async_get(hass)
        if eid := reg.async_get_entity_id("binary_sensor", entry.domain, entry.entry_id):
            return eid
    return f"binary_sensor.{object_id}"


def _wanted(rule: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if grouped(rule):
        return "group", {"entities": list(rule["sources"]), "all": False, "hide_members": False}
    return "template", {"state": helper_state(rule)}


async def ensure_group(hass: HomeAssistant, object_id: str, rule: dict[str, Any], name: str) -> str:
    """The rule's «все датчики» helper, as HA's helper editor makes it (group, else template); its members / state
    kept as the rule's; named «<Правило> · все датчики». Returns its entity id."""
    domain, opts = _wanted(rule)
    reg = er.async_get(hass)
    entry = our_entries(hass).get(object_id)
    if entry is not None and entry.domain != domain:
        await hass.config_entries.async_remove(entry.entry_id)
        entry = None
    if entry is None:
        flow = await hass.config_entries.flow.async_init(domain, context={"source": "user"})
        flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": "binary_sensor"})
        res = await hass.config_entries.flow.async_configure(flow["flow_id"], {"name": object_id, **opts})
        entry = res.get("result")
        if entry is None:
            raise RuntimeError(f"helper {object_id} not made: {res.get('errors') or res.get('reason')}")
    elif any(entry.options.get(k) != v for k, v in opts.items()):
        hass.config_entries.async_update_entry(entry, options={**entry.options, **opts})
        await hass.config_entries.async_reload(entry.entry_id)
    eid = None
    for _ in range(50):
        if eid := reg.async_get_entity_id("binary_sensor", domain, entry.entry_id):
            break
        await asyncio.sleep(0.1)
    if eid is None:
        raise RuntimeError(f"helper {object_id} not set up")
    if entry.title != name:
        hass.config_entries.async_update_entry(entry, title=name)
    ent = reg.async_get(eid)
    if ent is not None and (ent.name != name or ent.icon != GROUP_ICON):
        reg.async_update_entity(eid, name=name, icon=GROUP_ICON)
    return eid


async def drop_group(hass: HomeAssistant, object_id: str) -> None:
    if (entry := our_entries(hass).get(object_id)) is not None:
        await hass.config_entries.async_remove(entry.entry_id)


async def drop_all_groups(hass: HomeAssistant) -> None:
    for entry in list(our_entries(hass).values()):
        await hass.config_entries.async_remove(entry.entry_id)


# --- «Hauflo · Аварии»: one category in the automations', scripts' and helpers' lists ---------------------------------

def categorize(hass: HomeAssistant, name: str, entity_ids: dict[str, list[str]]) -> None:
    """The alarm objects under one category per list (HA's own categories: the automations / scripts / helpers page
    groups them). A category the integrator set by hand on one stays."""
    cats = cr.async_get(hass)
    reg = er.async_get(hass)
    for scope, eids in entity_ids.items():
        if not eids:
            continue
        cat = next((c for c in cats.async_list_categories(scope=scope) if c.name == name), None)
        if cat is None:
            cat = cats.async_create(scope=scope, name=name, icon="mdi:alarm-light")
        for eid in eids:
            ent = reg.async_get(eid)
            if ent is not None and not ent.categories.get(scope):
                reg.async_update_entity(eid, categories={**ent.categories, scope: cat.category_id})


def category_members(hass: HomeAssistant, recs: list[dict[str, Any]]) -> dict[str, list[str]]:
    """The alarm objects of the flat form's records (and the shared ones) by the list they show in."""
    reg = er.async_get(hass)
    autos = [*SHARED_IDS, *(x for rec in recs for x in (rec.get("automation"), rec.get("back")) if x)]
    scripts = [SCRIPT, *(rec["run_script"] for rec in recs if rec.get("run_script"))]
    return {
        "automation": [e for a in autos if (e := reg.async_get_entity_id("automation", "automation", a))],
        "script": [e for k in scripts if (e := reg.async_get_entity_id("script", "script", k))],
        "helpers": [x for rec in recs for x in (rec.get("helper"), rec.get("group")) if x],
    }

