"""Alarm rules as Home Assistant's own objects (0.26, owner's decision 5B) and «Сделать дом независимым» (29.10).

Every enabled alarm rule Hauflo sends becomes, in the house's HA:
- a helper ``input_boolean.hauflo_ack_<rule>`` — «Принято»: on = someone accepted the open alarm;
- a helper ``input_text.hauflo_chatter_<rule>`` — its sensors' recent alarms (a chattering sensor, see CHAT_W);
- an automation ``hauflo_alarm_<rule>`` in automations.yaml (the same plain-file check, backup copy and lock as the
  dashboard automations): the alarm held for a few seconds → a push to the rule's phones (critical per OS, each phone on
  its own — one that fails doesn't stop the others) and a note in HA; a repeat while nobody accepted; «Принято» and the
  rule's button from the push; back to normal for the chatter window replaces the push (same tag) and resets «Принято»
  (in alarm again sooner: the alarm stays open, nothing said); no data for a while →
  a non-critical «нет связи».
It runs without Hauflo and without the agent; the agent's engine then only keeps the list for the cards and monitoring
(notify.py, ``engine.native``). A rule whose automation can't be written (automations.yaml not plain, no automation
integration, changes closed by the owner, a helper HA didn't make) stays with the engine, said in the result.
An automation edited by hand in HA is never written over or removed; removing the integration leaves them all
(``hauflo.remove_alarms`` takes them away). All texts are the agent's translations (exceptions.alarm_native_*).
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

from . import automations, keep, yamlfile
from .const import ACK_ACTION, ACK_PREFIX, ALARM_PREFIX, ASSET_TAG, CHATTER_PREFIX, DOMAIN, NATIVE_STORE, RUN_ACTION
from .storage import HaufloStore
from .notify import ACTION_EVENT, CHATTER_COUNT, CHATTER_WINDOW, LINK_KINDS, chatter_window, LIST_MAX, NODATA_AFTER, RUN_SERVICES, NotifyEngine, push_data
from .tuning import TUNING

_LOGGER = logging.getLogger(__name__)
# The export before 0.26 (one push, no repeats): while one is still there an alarm comes twice — said in the result.
PREFIX = "hauflo_independent_"
HOLD = TUNING["notify"]["native_hold_s"]  # an alarm (or its end) held this long — a flicker says nothing
RUNS = TUNING["notify"]["native_max_runs"]
# A chattering sensor (ISA-18.2; the engine's own window; F-3 07.11, hysteresis on the return as Nagios flap detection,
# Zabbix recovery, HA's own `for:`): the alarm is told at once (HOLD); «back» only once every source has stayed in normal
# this long. Back in alarm sooner: the alarm is still open — nothing said, the window starts again. No helper keeps that
# «open»: the automation's own `last_triggered` does — moved only by runs that act (the top conditions skip the rest).
# While an alarm is open each of the source's changes is a silent run (a chain: in alarm again within the window right
# after its own marked return, back to normal after something done in this alarm phase), so «the rule's last act is this
# source's own mark» means «still open»; after «back» (or with nothing told) the chain is broken and the next alarm is told.
# The window is the rule's (notify.chatter_window): short for life safety — leak, smoke, gas, CO (HA review N-3 07.11) —
# so a real second alarm a minute after the first is told; REARM / BACK_AFTER are the common window's.
# No data (unavailable / unknown) is not a change of the sensor (security N-1, HA M-5 07.11): it neither starts an alarm
# nor ends one, nor resets a window. An open alarm's source going to / coming from no data is marked like its other
# changes, so «on → no data → on» is the same alarm (a radio blip says nothing, however often) and «on → no data →
# normal» ends it with «back» after the window. Only a source whose no-data phase isn't part of an open alarm (the
# first alarm after it, a real restart — no state before) is told — and only while nobody accepted it.
REARM = CHATTER_WINDOW
BACK_AFTER = max(HOLD, REARM)
# A chattering sensor over the long window (R3-1, ISA-18.2): the short life-safety window lets a slow flap («on 6 s →
# off 70 s») through as a new alarm every time. The rule's input_text keeps, per source (its index in `sources`, «!» —
# chattering), the times of its alarms within CHAT_W: up to CHATTER_COUNT told as before, the CHATTER_COUNT-th also said
# once as «дребезжит»; then neither alarm nor «back» until no alarm of the rule for CHAT_W («calm»: «back», or the alarm
# told again when one is on). Times older than CHAT_W never count — a real alarm after a quiet window is told, a restart
# or a reload (the input_text restores, a `for:` doesn't) ages out on its own. No memory (helper gone): told as before.
CHAT_W = CHATTER_WINDOW
CHAT_MAX = TUNING["notify"]["chatter_memory_max"]  # input_text's longest value (HA's state limit, 255): the oldest sources go first
_CHAT_READ = (
    "{%- set T = now() | as_timestamp | int -%}{%- set ns = namespace(e=[]) -%}"
    "{%- for item in states(chatter).split(' ') if ':' in item -%}{%- set k = item.split(':')[0] -%}"
    "{%- set ts = item.split(':')[1].split(',') | select('match', '^[0-9]+$') | map('int') | select('gt', T - " + str(CHAT_W) + ") | list -%}"
    "{%- if ts -%}{%- set ns.e = ns.e + [[k | replace('!', ''), '!' in k, ts]] -%}{%- endif -%}{%- endfor -%}"
)
# This alarm's place: mode «tell» / «chatter» (told + «дребезжит») / «quiet», and the memory to write.
_CHAT_NEXT = (
    _CHAT_READ + "{%- set me = namespace(i='-', t=[], flag=false, rest=[]) -%}"
    "{%- for s in sources -%}{%- if s == trigger.entity_id -%}{%- set me.i = loop.index0 | string -%}{%- endif -%}{%- endfor -%}"
    "{%- for x in ns.e -%}{%- if x[0] == me.i -%}{%- set me.t = x[2] -%}{%- set me.flag = x[1] -%}{%- else -%}{%- set me.rest = me.rest + [x] -%}{%- endif -%}{%- endfor -%}"
    "{%- set t = (me.t + [T])[-" + str(CHATTER_COUNT) + ":] -%}"
    "{%- set mode = 'quiet' if me.flag else 'chatter' if t | count >= " + str(CHATTER_COUNT) + " else 'tell' -%}"
    "{%- set out = namespace(v='') -%}{%- for x in (me.rest + [[me.i, me.flag or mode == 'chatter', t]]) | reverse -%}"
    "{%- set v = x[0] ~ ('!' if x[1] else '') ~ ':' ~ (x[2] | join(',')) ~ ((' ' ~ out.v) if out.v else '') -%}"
    "{%- if v | length <= " + str(CHAT_MAX) + " -%}{%- set out.v = v -%}{%- endif -%}{%- endfor -%}"
    "{{ {'mode': mode, 'value': out.v} | tojson }}"
)
_CHATTERING = _CHAT_READ + "{%- set c = namespace(on=false) -%}{%- for x in ns.e if x[1] -%}{%- set c.on = true -%}{%- endfor -%}{{ c.on }}"


def back_after(rule: dict[str, Any]) -> int:
    """Seconds every source stays in normal before the rule's «back» (its chatter window, at least HOLD)."""
    return max(HOLD, chatter_window(rule))
NO_DATA = ["unavailable", "unknown"]
ICON = "mdi:bell-check"
_TEXT = "alarm_native_"
# One alarm's names in the push: «Протечка кухня · Кухня, …» — up to LIST_MAX, then «+N» (rendered by HA when it runs).
_NAMES = (
    "{%- set ns = namespace(o=[]) -%}{%- for e in alarmed[:" + str(LIST_MAX) + "] -%}{%- set a = area_name(e) -%}"
    "{%- set ns.o = ns.o + [(state_attr(e, 'friendly_name') or e) ~ (' · ' ~ a if a else '')] -%}{%- endfor -%}"
    "{{ ns.o | join(', ') }}{% if alarmed | count > " + str(LIST_MAX) + " %} +{{ alarmed | count - " + str(LIST_MAX) + " }}{% endif %}"
)
_ONE = "{{ (state_attr(trigger.entity_id, 'friendly_name') or trigger.entity_id) ~ ((' · ' ~ area_name(trigger.entity_id)) if area_name(trigger.entity_id) else '') }}"
_ALARMED = "{{ expand(sources) | selectattr('state', 'in', alarm_states) | map(attribute='entity_id') | list }}"
# Who pressed «Принято»: the HA person of the user (a phone's push action carries its user, a toggle in HA too).
_WHO = "{%- set uid = (trigger.event.context.user_id if trigger.event is defined else trigger.to_state.context.user_id) -%}{{ (states.person | selectattr('attributes.user_id', 'eq', uid) | map(attribute='name') | list | first) if uid else '' }}"
_ALARM_ON = "{{ expand(sources) | selectattr('state', 'in', alarm_states) | list | count > 0 }}"
_NONE_ON = "{{ expand(sources) | selectattr('state', 'in', alarm_states) | list | count == 0 }}"
# When the rule last acted (None — never): the automation's own state at the trigger.
_LAST = "{%- set last = this.attributes.last_triggered if this is defined and this and this.attributes is defined else none -%}"


def slug_of(rule_id: str) -> str:
    return f"{slugify(rule_id)[:40] or 'rule'}_{hashlib.sha1(rule_id.encode()).hexdigest()[:6]}"


def ids(rule_id: str) -> dict[str, str]:
    """The rule's automation id, its helper's object id and its push buttons (const.py prefixes)."""
    slug = slug_of(rule_id)
    return {"automation": ALARM_PREFIX + slug, "helper": ACK_PREFIX + slug, "chatter": CHATTER_PREFIX + slug, "ack": ACK_ACTION + slug.upper(), "run": RUN_ACTION + slug.upper()}


def chatter_of(automation: str) -> str:
    """The chatter memory's object id of a rule's automation (records before 0.27 don't keep it)."""
    return CHATTER_PREFIX + automation.removeprefix(ALARM_PREFIX)


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


def _helper(ack: str, on: bool) -> dict[str, Any]:
    return {"action": f"input_boolean.turn_{'on' if on else 'off'}", "target": {"entity_id": ack}}


def automation_for(rule: dict[str, Any], phones: list[dict[str, Any]], t: dict[str, str], ack: str, *, names: dict[str, str] | None = None, chatter: str | None = None) -> dict[str, Any]:
    """One rule as a plain HA automation (HA's own editor format). ``t`` — the texts (``texts``), ``ack`` — the
    rule's «Принято» helper, ``names`` — entity → its name for the button («Закрыть: Кран»), ``chatter`` — its chatter
    memory (input_text)."""
    the = ids(rule["id"])
    chatter = chatter or f"input_text.{the['chatter']}"
    aid = the["automation"]
    title = _lit(rule["title"])
    critical = rule.get("priority") == "critical"
    link = rule.get("kind") in LINK_KINDS
    act = rule.get("action") if (rule.get("action") or {}).get("service") in RUN_SERVICES else None
    button = None
    if act:
        # Cut as the person reads it, then made a literal (a cut literal would break the template).
        button = _lit(_say(t, "close" if act["service"] == "valve.close_valve" else "off", name=(names or {}).get(act["entity_id"]) or act["entity_id"])[:60])
    actions = ([{"action": the["run"], "title": button, "destructive": True, "authenticationRequired": True}] if act else []) + [{"action": the["ack"], "title": t["accept"]}]
    alarm_title = _say(t, "title", title=title)
    who_said = "{% if who %}" + _say(t, "accepted_by", who="{{ who }}") + "{% else %}" + t["accepted"] + "{% endif %}"
    phone_names = ", ".join(str(p.get("name") or p["service"]) for p in phones) or t["nobody"]
    repeat = int(rule.get("repeat_min") or 0)
    rearm = chatter_window(rule)
    back_after_s = back_after(rule)
    fresh = "trigger.from_state is not none and trigger.from_state.state not in ['unavailable', 'unknown']"
    # The chain of an open alarm (see REARM): «short» — this source was in normal less than the window; «own» — the rule's
    # last act is this source's marked return (its run comes at once; 2·HOLD is the queue's slack); «during» — the rule
    # did something since this alarm phase began (its marked start, the alarm told, a repeat, «Принято»).
    short = "(trigger.to_state.last_changed - trigger.from_state.last_changed).total_seconds() < " + str(rearm)
    own = "last is not none and 0 <= (last - trigger.from_state.last_changed).total_seconds() <= " + str(2 * HOLD)
    during = "last is not none and last >= trigger.to_state.last_changed"
    calm = "(not " + short + " or not (" + own + " or " + during + "))"
    # From no data: the alarm goes on when that no-data phase is a marked part of it, or the rule acted since.
    chain = "(trigger.from_state is not none and (" + own + " or " + during + "))"
    nodata_from = "trigger.from_state.state in " + repr(NO_DATA)
    triggers: list[dict[str, Any]] = [
        {"trigger": "state", "entity_id": list(rule["sources"]), "to": list(rule["alarm_states"]), "for": {"seconds": HOLD}, "id": "alarm"},
        # Normal from alarm or from no data (HA M-5: alarm → unavailable → normal ends it too), held the whole window.
        {"trigger": "state", "entity_id": list(rule["sources"]), "from": list(rule["alarm_states"]) + NO_DATA, "not_to": NO_DATA + list(rule["alarm_states"]),
         "for": {"seconds": back_after_s}, "id": "back"},
        {"trigger": "event", "event_type": ACTION_EVENT, "event_data": {"action": the["ack"]}, "id": "ack"},
        {"trigger": "state", "entity_id": ack, "from": "off", "to": "on", "id": "accepted"},
    ]
    chat_title = _say(t, "chatter_title", title=title)
    chat_say = _say(t, "chatter", name=_ONE, count=str(CHATTER_COUNT), minutes=str(max(1, CHAT_W // 60)))
    if rearm > 0:
        triggers += [
            {"trigger": "state", "entity_id": list(rule["sources"]), "to": list(rule["alarm_states"]), "id": "entered"},
            {"trigger": "state", "entity_id": list(rule["sources"]), "from": list(rule["alarm_states"]) + NO_DATA, "not_to": NO_DATA, "id": "returned"},
            {"trigger": "state", "entity_id": list(rule["sources"]), "to": NO_DATA, "id": "lost"},
        ]
    if repeat:
        # Every `repeat` minutes since the alarm (re)started, while nobody accepted it: true for one minute, then false —
        # a template re-rendered each minute, so a quiet house leaves no traces (a time pattern would run every time).
        triggers.append(
            {
                "trigger": "template",
                "value_template": "{%- set on = expand(sources) | selectattr('state', 'in', alarm_states) | list -%}"
                "{%- if on and is_state(ack, 'off') -%}{%- set m = ((now() - (on | map(attribute='last_changed') | max)).total_seconds() // 60) | int -%}"
                "{{ m >= " + str(repeat) + " and m % " + str(repeat) + " == 0 }}{%- else -%}{{ false }}{%- endif -%}",
                "id": "repeat",
            }
        )
    if act:
        triggers.append({"trigger": "event", "event_type": ACTION_EVENT, "event_data": {"action": the["run"]}, "id": "run"})
    if not link:
        triggers += [
            {"trigger": "state", "entity_id": list(rule["sources"]), "to": NO_DATA, "for": {"seconds": NODATA_AFTER}, "id": "nodata"},
            {"trigger": "state", "entity_id": list(rule["sources"]), "from": NO_DATA, "not_to": NO_DATA, "id": "data"},
        ]
    # Calm: no alarm of the rule for CHAT_W — at once by the memory's own `for:`, else (an automation reloaded meanwhile)
    # by the minute's template; the second of two finds the memory cleared.
    triggers += [
        {"trigger": "state", "entity_id": chatter, "not_to": ["", *NO_DATA], "for": {"seconds": CHAT_W}, "id": "calm"},
        {"trigger": "template", "value_template": "{%- set c = expand(chatter) | first | default(none) -%}{{ c is not none and '!' in c.state and (now() - c.last_changed).total_seconds() >= "
         + str(CHAT_W) + " }}", "id": "calm"},
    ]
    alarm_push = {"tag": aid, "critical": critical, "actions": actions}
    branches: list[dict[str, Any]] = [
        {
            # A new alarm (from a real state): «Принято» resets; after a restart / lost data only when nobody accepted it.
            # A link sensor's «off» means something only after it was seen «on».
            # A chattering source (in alarm again within the window, nothing done since its return) is quiet: the alarm
            # is still open — its notification stays, the repeat goes on until «Принято».
            "conditions": [{"condition": "trigger", "id": "alarm"},
                           {"condition": "template", "value_template": _LAST + "{{ (" + fresh + " and " + calm + ")" + (" }}" if link else " or (not (" + fresh + ") and is_state(ack, 'off') and not " + chain + ") }}")}],
            # A chattering source (CHAT_W): told up to CHATTER_COUNT times, the last one with «дребезжит»; then quiet. The
            # memory is written last and may fail: the alarm is told first.
            "sequence": [
                {"variables": {"chat": _CHAT_NEXT}},
                {
                    "if": [{"condition": "template", "value_template": "{{ chat.mode != 'quiet' }}"}],
                    "then": [
                        {"if": [{"condition": "template", "value_template": "{{ " + fresh + " }}"}], "then": [_helper(ack, False)]},
                        {"action": "persistent_notification.create", "data": {"notification_id": aid, "title": alarm_title, "message": _say(t, "alarm", names="{{ names }}")}},
                        *_push(phones, alarm_title, _say(t, "alarm", names="{{ names }}"), **alarm_push),
                    ],
                },
                {
                    "if": [{"condition": "template", "value_template": "{{ chat.mode == 'chatter' }}"}],
                    "then": [
                        {"action": "persistent_notification.create", "data": {"notification_id": f"{aid}_chatter", "title": chat_title, "message": chat_say}},
                        *_push(phones, chat_title, chat_say, tag=f"{aid}_chatter"),
                    ],
                },
                {"action": "input_text.set_value", "continue_on_error": True, "target": {"entity_id": chatter}, "data": {"value": "{{ chat.value }}"}},
            ],
        },
        {
            # Every source in normal for the whole window (BACK_AFTER): the alarm is over — «Принято» resets, HA's bell
            # clears. Run once, by the last source to change (one with no data has no «back» and doesn't count). «back»
            # said for an open alarm only: the rule did something in this source's last alarm phase or since, or its
            # last act is the marked change of a source still as it was (the chain above); a flicker after «back», or
            # one nobody was told about, ends quietly.
            "conditions": [{"condition": "trigger", "id": "back"}, {"condition": "template", "value_template": _NONE_ON},
                           {"condition": "template", "value_template": "{{ expand(sources) | rejectattr('state', 'in', " + repr(NO_DATA) + ") | selectattr('last_changed', 'gt', trigger.to_state.last_changed) | list | count == 0 }}"}],
            "sequence": [
                # From no data: only a short gap counts (a longer one had «нет связи» / data runs, not this alarm's).
                {"variables": {"told": _LAST + "{%- set ns = namespace(m=false) -%}{%- if last is not none -%}{%- for x in expand(sources) -%}"
                               "{%- if 0 <= (last - x.last_changed).total_seconds() <= " + str(2 * HOLD) + " -%}{%- set ns.m = true -%}{%- endif -%}{%- endfor -%}{%- endif -%}"
                               "{%- set f = trigger.from_state -%}"
                               "{{ is_state(ack, 'on') or last is not none and (f.state in alarm_states or (trigger.to_state.last_changed - f.last_changed).total_seconds() < "
                               + str(NODATA_AFTER) + ") and (last >= f.last_changed or ns.m) }}"}},
                # A chattering source: no «back» until calm (CHAT_W) — that says it.
                {"variables": {"chattering": _CHATTERING}},
                _helper(ack, False),
                {"action": "persistent_notification.dismiss", "data": {"notification_id": aid}},
                {
                    "if": [{"condition": "template", "value_template": "{{ told and not chattering }}"}],
                    "then": _push(phones, _say(t, "back_title", title=title), _say(t, "back", name=_ONE), tag=aid),
                },
            ],
        },
        {
            "conditions": [{"condition": "trigger", "id": "ack"}, {"condition": "state", "entity_id": ack, "state": "off"}, {"condition": "template", "value_template": _ALARM_ON}],
            "sequence": [_helper(ack, True), {"variables": {"who": _WHO}}, *_push(phones, alarm_title, who_said, tag=aid, quiet=True)],
        },
        {
            # Accepted in HA itself (the helper's toggle, a dashboard card): said to the phones; the agent's and this
            # automation's own switching carry a parent context and say nothing here.
            "conditions": [{"condition": "trigger", "id": "accepted"}, {"condition": "template", "value_template": "{{ trigger.to_state.context.parent_id is none }}"},
                           {"condition": "template", "value_template": _ALARM_ON}],
            "sequence": [{"variables": {"who": _WHO}}, *_push(phones, alarm_title, who_said, tag=aid, quiet=True)],
        },
    ]
    if rearm > 0:
        mark = [{"variables": {"marked": "{{ trigger.entity_id }}"}}]
        alarm_from = "trigger.from_state.state in alarm_states and last is not none and last >= trigger.from_state.last_changed"
        branches += [
            {
                # The chain (see REARM): silent runs marking an open alarm's source — in alarm again soon after its own
                # marked return (or from its marked no data, however long); back to normal / to no data after the rule
                # acted in this alarm phase, or from a marked phase. A closed alarm marks nothing.
                "conditions": [{"condition": "trigger", "id": "entered"},
                               {"condition": "template", "value_template": _LAST + "{{ trigger.from_state is not none and " + own + " and (" + nodata_from + " or " + short + ") }}"}],
                "sequence": mark,
            },
            {
                "conditions": [{"condition": "trigger", "id": "returned"},
                               {"condition": "template", "value_template": _LAST + "{{ trigger.from_state is not none and (" + alarm_from + " or " + nodata_from
                                + " and trigger.to_state.state not in alarm_states and " + own + ") }}"}],
                "sequence": mark,
            },
            {
                "conditions": [{"condition": "trigger", "id": "lost"},
                               {"condition": "template", "value_template": _LAST + "{{ trigger.from_state is not none and (" + alarm_from + " or trigger.from_state.state not in alarm_states and "
                                "(" + nodata_from + " or " + short + ") and " + own + ") }}"}],
                "sequence": mark,
            },
        ]
    if repeat:
        branches.append(
            {
                "conditions": [{"condition": "trigger", "id": "repeat"}, {"condition": "state", "entity_id": ack, "state": "off"}, {"condition": "template", "value_template": _ALARM_ON}],
                "sequence": _push(phones, alarm_title, _say(t, "repeat", names="{{ names }}"), **alarm_push),
            }
        )
    if act:
        branches.append(
            {
                # The service first, no «continue»: when it fails the alarm stays unaccepted and keeps repeating.
                "conditions": [{"condition": "trigger", "id": "run"}, {"condition": "template", "value_template": _ALARM_ON}],
                "sequence": [
                    {"action": act["service"], "target": {"entity_id": act["entity_id"]}},
                    _helper(ack, True),
                    *_push(phones, alarm_title, _say(t, "done", button=button or ""), tag=aid, quiet=True),
                ],
            }
        )
    if not link:
        nodata_title = _say(t, "nodata_title", title=title)
        branches += [
            {
                "conditions": [{"condition": "trigger", "id": "nodata"}, {"condition": "template", "value_template": "{{ trigger.from_state is not none and trigger.from_state.state not in ['unavailable', 'unknown'] }}"}],
                "sequence": [
                    {"action": "persistent_notification.create", "data": {"notification_id": f"{aid}_nd", "title": nodata_title, "message": _say(t, "nodata", name=_ONE, minutes=str(NODATA_AFTER // 60))}},
                    *_push(phones, nodata_title, _say(t, "nodata", name=_ONE, minutes=str(NODATA_AFTER // 60)), tag=f"{aid}_nd"),
                ],
            },
            {
                # Data again after a «нет связи»: that message leaves the phones and HA's bell.
                "conditions": [{"condition": "trigger", "id": "data"}, {"condition": "template", "value_template": "{{ (trigger.to_state.last_changed - trigger.from_state.last_changed).total_seconds() >= " + str(NODATA_AFTER) + " }}"}],
                "sequence": [{"action": "persistent_notification.dismiss", "data": {"notification_id": f"{aid}_nd"}}, *_push(phones, None, "clear_notification", tag=f"{aid}_nd")],
            },
        ]
    branches.append(
        {
            # A chattering source calm (CHAT_W): the memory cleared; an alarm on now (never told while quiet, or the last
            # one told) is told again — the repeat's way, while nobody accepted; all in normal — «back» with the calm.
            "conditions": [{"condition": "trigger", "id": "calm"}, {"condition": "template", "value_template": "{{ '!' in states(chatter) }}"}],
            "sequence": [
                {"action": "input_text.set_value", "continue_on_error": True, "target": {"entity_id": chatter}, "data": {"value": ""}},
                {"action": "persistent_notification.dismiss", "data": {"notification_id": f"{aid}_chatter"}},
                {
                    "if": [{"condition": "template", "value_template": _ALARM_ON}],
                    "then": [
                        {
                            "if": [{"condition": "state", "entity_id": ack, "state": "off"}],
                            "then": [
                                {"action": "persistent_notification.create", "data": {"notification_id": aid, "title": alarm_title, "message": _say(t, "alarm", names="{{ names }}")}},
                                *_push(phones, alarm_title, _say(t, "alarm", names="{{ names }}"), **alarm_push),
                            ],
                        }
                    ],
                    "else": [
                        {"action": "persistent_notification.dismiss", "data": {"notification_id": aid}},
                        *_push(phones, _say(t, "back_title", title=title), _say(t, "calm", minutes=str(max(1, CHAT_W // 60))), tag=aid),
                    ],
                },
            ],
        }
    )
    return {
        "id": aid,
        "alias": _say(t, "alias", title=rule["title"]),
        "description": _say(t, "description", title=rule["title"], phones=phone_names, helper=ack, chatter=chatter),
        "mode": "queued",
        "max": RUNS,
        "trigger_variables": {"sources": list(rule["sources"]), "alarm_states": list(rule["alarm_states"]), "ack": ack, "chatter": chatter},
        "triggers": triggers,
        # Only a run that acts runs: the quiet ones leave `last_triggered` as it was (the chatter window above).
        "conditions": [{"condition": "or", "conditions": [{"condition": "and", "conditions": b["conditions"]} for b in branches]}],
        "actions": [
            {"variables": {"alarmed": _ALARMED}},
            {"variables": {"names": _NAMES}},
            {"choose": branches},
        ],
    }


# The rule's helpers: domain → object id prefix, how HA's helper editor would make it.
CHAT_ICON = "mdi:vibrate"
_KIND = {"input_boolean": ACK_PREFIX, "input_text": CHATTER_PREFIX}


def _collection(hass: HomeAssistant, domain: str = "input_boolean") -> Any:
    """A helper domain's own storage collection — what HA's helper editor creates, updates and deletes through (its
    websocket commands are bound methods of the collection's websocket object)."""
    handler = (hass.data.get("websocket_api") or {}).get(f"{domain}/create")
    fn = handler[0] if handler else None
    while fn is not None and not hasattr(fn, "__self__"):
        fn = getattr(fn, "__wrapped__", None)
    return getattr(getattr(fn, "__self__", None), "storage_collection", None)


async def _helpers(hass: HomeAssistant, domain: str = "input_boolean") -> Any:
    if domain not in hass.config.components:
        await async_setup_component(hass, domain, {})
    if (coll := _collection(hass, domain)) is None:
        raise RuntimeError(f"{domain} helpers not reachable")
    return coll


def _ack_entity(hass: HomeAssistant, object_id: str, domain: str = "input_boolean") -> str:
    return er.async_get(hass).async_get_entity_id(domain, domain, object_id) or f"{domain}.{object_id}"


async def _ensure_helper(hass: HomeAssistant, coll: Any, object_id: str, name: str, domain: str = "input_boolean") -> str:
    """The rule's helper («Принято», its chatter memory) — made like HA's helper editor makes it (the id from its first
    name), then named."""
    want = {"icon": ICON} if domain == "input_boolean" else {"icon": CHAT_ICON, "max": CHAT_MAX, "mode": "text"}
    if object_id not in coll.data:
        await coll.async_create_item({"name": object_id, **want})
    if object_id not in coll.data:
        raise RuntimeError(f"helper {object_id} not made")
    item = coll.data[object_id]
    if item.get("name") != name or any(item.get(k) != v for k, v in want.items()):
        await coll.async_update_item(object_id, {"name": name, **want})
    return _ack_entity(hass, object_id, domain)


async def _drop_helper(hass: HomeAssistant, coll: Any, object_id: str) -> None:
    if coll is not None and object_id in coll.data:
        await coll.async_delete_item(object_id)


async def _drop_ours(hass: HomeAssistant, colls: dict[str, Any], object_id: str) -> None:
    """One of the rules' helpers, by its prefix (anything else is never touched)."""
    for domain, prefix in _KIND.items():
        if object_id.startswith(prefix):
            await _drop_helper(hass, colls.get(domain), object_id)


def _empty() -> dict[str, Any]:
    # restored — removed in HA by hand, written back by this apply: a change of HA said in the plan (HA review H-1),
    # never a silent «added».
    return {"rules": 0, "added": [], "updated": [], "restored": [], "unchanged": [], "removed": [], "kept_edited": [], "engine": [], "warnings": [], "error": None, "error_code": None}


async def async_sync(hass: HomeAssistant, engine: NotifyEngine, *, dry_run: bool = False, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """The engine's rules as automations + helpers in HA (one write of automations.yaml, then one reload); the engine is
    told which rules HA now runs (``async_set_native`` — open alarms move over). ``dry_run`` — only what would change
    (Hauflo's deploy plan, ``config`` — the rules it would send). Rules are said by their titles; ``engine`` — those left
    to the agent, with the reason."""
    from homeassistant.components.automation.config import async_validate_config_item  # noqa: PLC0415

    english = engine._english(config)
    lang = "en" if english else "ru"  # kept with each record: what the automations say after the agent is gone (D-4)
    out = _empty()
    rules = [r for r in (config or engine.config).get("rules") or [] if r.get("enabled") and r.get("sources")]
    out["rules"] = len(rules)
    stored: dict[str, dict[str, Any]] = dict(engine.native)

    def to_engine(rule: dict[str, Any], reason: str) -> None:
        out["engine"].append({"id": rule["id"], "title": rule["title"], "reason": reason})

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
    tcoll = None if dry_run else await _helpers(hass, "input_text")
    colls = {"input_boolean": coll, "input_text": tcoll}
    names = {r["action"]["entity_id"]: engine._name(r["action"]["entity_id"]) for r in rules if r.get("action")}
    file = automations.Automations(hass, lambda _eid: False)
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
        drop: list[str] = []
        made: dict[str, list[str]] = {}  # rule id → its helpers made in this pass (dropped when the rule isn't written, L-3)
        rule_of: dict[str, dict[str, Any]] = {}
        for r in rules:
            the = ids(r["id"])
            aid = the["automation"]
            cur, rec = current.get(aid), stored.get(r["id"])
            if cur is None and locked:
                to_engine(r, "locked")
                continue
            rule_of[aid] = r
            if coll is not None and tcoll is not None:
                made[r["id"]] = [o for o, c in ((the["helper"], coll), (the["chatter"], tcoll)) if o not in c.data]
            try:
                ack = await _ensure_helper(hass, coll, the["helper"], _say(t, "helper", title=r["title"])) if coll is not None else _ack_entity(hass, the["helper"])
                chat = (await _ensure_helper(hass, tcoll, the["chatter"], _say(t, "chatter_helper", title=r["title"]), "input_text") if tcoll is not None
                        else _ack_entity(hass, the["chatter"], "input_text"))
            except Exception:  # noqa: BLE001 — that rule stays with the agent, the others go on
                _LOGGER.exception("Hauflo: helpers for %s not made", r["id"])
                if cur is not None and rec:
                    native[r["id"]] = rec
                    out["warnings"].append({"code": "helper", "title": r["title"]})
                else:
                    to_engine(r, "helper")
                    drop.extend(made.pop(r["id"], []))
                continue
            cfg = automation_for(r, engine._targets(r, config), t, ack, names=names, chatter=chat)
            digest = automations._digest(cfg)  # noqa: SLF001
            if cur is not None and automations._digest(cur) == digest:  # noqa: SLF001
                state = "unchanged"
            elif cur is not None and not (rec and automations._digest(cur) == rec.get("digest")):  # noqa: SLF001
                # Changed in HA's own editor (or not Hauflo's record of it): never written over.
                out["kept_edited"].append(r["title"])
                native[r["id"]] = {"automation": aid, "helper": ack, "chatter": chat, "digest": rec.get("digest", "") if rec else "", "ack": the["ack"], "run": the["run"], "lang": (rec or {}).get("lang") or lang}
                continue
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
            native[r["id"]] = {"automation": aid, "helper": ack, "chatter": chat, "digest": digest, "ack": the["ack"], "run": the["run"], "lang": lang}
        wanted = {ids(r["id"])["automation"] for r in rules}
        by_aid = {rec.get("automation"): rec for rec in stored.values()}
        for aid, cur in current.items():
            if not isinstance(aid, str) or not aid.startswith(ALARM_PREFIX) or aid in wanted:
                continue
            rec = by_aid.get(aid)
            if rec is None or automations._digest(cur) != rec.get("digest"):  # noqa: SLF001
                out["warnings"].append({"code": "edited_left", "title": str(cur.get("alias") or aid)})
                continue
            writes[aid] = None
            out["removed"].append(str(cur.get("alias") or aid))
            drop += [str(rec.get("helper") or "").partition(".")[2], chatter_of(aid)]
        if writes and not dry_run:
            kept = [writes[a.get("id")] if isinstance(a, dict) and a.get("id") in writes else a for a in data]
            new = [a for a in kept if a is not None] + [c for a, c in writes.items() if c is not None and a not in current]
            theirs: list[str] = []  # changed in HA's editor while this pass ran (M-2): left as they are now
            same = lambda a, b: automations._digest(a) == automations._digest(b)  # noqa: E731, SLF001
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
                return out
            for aid in theirs:
                writes.pop(aid, None)
                if (r := rule_of.get(aid)) is None:  # a removal: the automation and its helper stay
                    gone = {str((by_aid.get(aid) or {}).get("helper") or "").partition(".")[2], chatter_of(aid)}
                    drop = [o for o in drop if o not in gone]
                    out["removed"] = [x for x in out["removed"] if x != str((current.get(aid) or {}).get("alias") or aid)]
                    out["warnings"].append({"code": "edited_left", "title": str((current.get(aid) or {}).get("alias") or aid)})
                    continue
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
    if not dry_run:
        if writes:
            await hass.services.async_call("automation", "reload", {}, blocking=True)
            reg = er.async_get(hass)
            for aid, cfg in writes.items():
                if cfg is None and (eid := reg.async_get_entity_id("automation", "automation", aid)):
                    reg.async_remove(eid)
        for object_id in drop:
            await _drop_ours(hass, colls, object_id)
        await engine.async_set_native(native)
    if old := [str(a.get("alias") or a.get("id")) for a in data if isinstance(a, dict) and str(a.get("id") or "").startswith(PREFIX)]:
        out["warnings"].append({"code": "double", "title": ", ".join(old)})
    return out


async def async_remove_all(hass: HomeAssistant, engine: NotifyEngine | None) -> list[str]:
    """«Убрать аварии Hauflo» (hauflo.remove_alarms, HA's admin): every hauflo_alarm_* automation, hauflo_ack_* and hauflo_chatter_* helper
    goes — edited ones too (the admin asked for exactly that); the agent's engine takes the rules back. Returns the
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
    for domain, prefix in _KIND.items():
        coll = _collection(hass, domain)
        for object_id in [k for k in (coll.data if coll is not None else {}) if str(k).startswith(prefix)]:
            await _drop_helper(hass, coll, object_id)
    if engine is not None:
        await engine.async_set_native({})
    return [str(a.get("alias") or a.get("id")) for a in ours]


async def written_english(hass: HomeAssistant, engine: NotifyEngine | None) -> bool:
    """One language for everything said about the alarm automations: the one they were written in (the project's, kept
    with their records), else the engine's (the project's or HA's) — never HA's beside a project's other (5B e2e D-4)."""
    stored = engine.native if engine is not None else await HaufloStore(hass, NATIVE_STORE).async_load()
    lang = next((r["lang"] for r in (stored or {}).values() if isinstance(r, dict) and r.get("lang") in ("ru", "en")), None)
    if lang:
        return lang == "en"
    if engine is not None:
        return engine._english()  # noqa: SLF001
    return not str(hass.config.language or "ru").lower().startswith("ru")


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
