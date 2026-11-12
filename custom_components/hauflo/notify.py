"""Push alarms to the residents' phones (owner 2026-10-24), run by the agent itself.

Hauflo sends the rules: an alarm type (leak, boiler, smoke…), its source entities, who gets it (people,
groups of people, or everyone with the Home Assistant app), critical or normal, an optional button
(«Перекрыть воду» → a service on an entity) and how often to repeat until someone presses «Принято».

Why the agent and not HA automations or notify groups: critical alerts (iOS Critical Alerts, Android
alarm stream) need the `data` of the legacy `notify.mobile_app_<phone>` services — HA's notify
entities and UI notify groups drop it. Phones are found every time from people (person → their
device trackers → the phone device → its service), so a reinstalled app is picked up by itself.
The rules are kept in HA's storage: alarms work without Hauflo online.

API for Hauflo (through the agent's tunnel, HA auth): GET/POST /api/hauflo/notify,
POST /api/hauflo/notify/test.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import time
from datetime import timedelta
from typing import Any, Callable

from homeassistant.components import persistent_notification
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, Event, HomeAssistant, State, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, label_registry as lr, translation
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util, slugify

from .natural import natural_key
from .ownership import entity_room
from .storage import HaufloStore
from .const import ACK_ACTION, DO_ACTION, DOMAIN, LOCKED_CODE, MONITOR_LABELS, NATIVE_STORE, OK_ACTION, QUIET_PARENT, RUN_ACTION, site_ru
from . import alarms_now, foreign, signals
from .tuning import TUNING

_LOGGER = logging.getLogger(__name__)
STORE_KEY = "hauflo.notify"
ACTIVE_KEY = "hauflo.notify_active"
ACTION_EVENT = "mobile_app_notification_action"
ACK = "HAUFLO_ACK"
# What a push button may do: shut a valve / switch something off — never unlock, open, arm (security review 25.10).
RUN_SERVICES = {"valve.close_valve", "switch.turn_off"}
_T = TUNING["notify"]
# Loud (critical: rings through silent mode) only for what endangers the house; a repeat no more often than this.
CRITICAL_CLASSES = set(_T["critical_classes"])
REPEAT_MIN = _T["repeat_min"]
RUN = "HAUFLO_RUN"
# Device classes worth an alarm, for Hauflo's suggestions.
ALARM_CLASSES = set(_T["alarm_classes"])
NOT_A_STATE = {STATE_UNAVAILABLE, STATE_UNKNOWN, None}
GROUP_WINDOW = _T["group_window_s"]  # seconds: alarms of one rule starting together are told together
# An alarm sensor with no data this long (after it once reported) is said: it would stay silent through the very
# alarm it is for (SCADA expert 25.10, ISA-18.2 — a bad-quality input is a diagnostic alarm, not «all quiet»).
NODATA_AFTER = _T["nodata_after_s"]
# A sensor flapping alarm / normal (ISA-18.2 chattering, SCADA expert 25.10): this many alarms within the window are
# said once as «дребезжит», then quiet until it stays put for the window — on the screen it's still shown.
CHATTER_COUNT = _T["chatter_count"]
CHATTER_WINDOW = _T["chatter_window_s"]
# Life safety (leak, smoke, gas, CO — NFPA 72 / EN 54 / EN 50291: a fire or gas alarm is never latched away by a nuisance
# filter; the alarm verification window there is ~60 s): a real second alarm within the long window is told — the window
# (the re-arm after «back») is short. Chattering is still counted over the common one (R3-1, ISA-18.2): CHATTER_COUNT
# alarms within CHATTER_WINDOW. Which kinds — tuning (life_safety_kinds), not code.
CHATTER_WINDOW_LIFE = _T["chatter_window_life_s"]
LIFE_SAFETY_KINDS = frozenset(_T["life_safety_kinds"])


def chatter_window(rule: dict[str, Any] | None) -> int:
    """The rule's chatter window (seconds): short for life safety, the common one for the rest."""
    return CHATTER_WINDOW_LIFE if rule and rule.get("kind") in LIFE_SAFETY_KINDS else CHATTER_WINDOW


# How long a rule's alarm state must hold before it counts (ISA-18.2 on-delay, HA's own `for:` — owner 09.11 «дребезг:
# устойчивое on > 60 с»): the rule's own `hold_s` (seconds, 0 = at once), else the canon by its kind and its sources'
# classes — life safety at once, doors / windows / motion / links and classless signals a minute, levels two (tuning
# notify.hold; the app and Hauflo read the same canon). Several sources — the shortest: a leak never waits for a door.
HOLD_CANON = _T["hold"]
HOLD_MAX = HOLD_CANON["max_s"]
# The shortest hold of a rule HA runs (its `for:`): a flicker of a few seconds is nothing there, even for a leak.
NATIVE_HOLD = _T["native_hold_s"]


def native_back(rule: dict[str, Any]) -> int:
    """Seconds a source of a rule HA runs stays in normal before its «back» (its chatter window, at least NATIVE_HOLD —
    ISA-18.2 / Nagios flap hysteresis on the return; HA's own `for:`); in alarm again sooner — the same open alarm."""
    return max(NATIVE_HOLD, chatter_window(rule))


def hold_value(v: Any) -> int | None:
    """A rule's own hold as sent: a whole number of seconds 0..HOLD_MAX; anything else (absent, an older Hauflo) — None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v) or not 0 <= v <= HOLD_MAX:
        return None
    return int(v)


def default_hold(hass: HomeAssistant | None, kind: str, sources: list[str], thresholds: dict[str, Any]) -> int:
    """The canon hold of a rule with no own: its kind's, each source's (a level — level_s, a signal — by its class,
    classless — default_s); the shortest."""
    found = [HOLD_CANON["kinds"][kind]] if kind in HOLD_CANON["kinds"] else []
    for eid in sources:
        found.append(HOLD_CANON["level_s"] if eid in thresholds else HOLD_CANON["classes"].get(str(_device_class(hass, eid) or ""), HOLD_CANON["default_s"]))
    return int(min(found)) if found else int(HOLD_CANON["default_s"])


def source_hold(rule: dict[str, Any], eid: str) -> int:
    """How long this source's alarm state must hold in this rule: its own (a combination's — `holds`), else the rule's."""
    return int((rule.get("holds") or {}).get(eid, rule.get("hold") or 0))


def own_kind(hass: HomeAssistant | None, eid: str) -> str | None:
    """A Hauflo signal that holds its time itself — its key in notify.hold.own, else None: a combination of the site's
    policy (on only once every condition held its «дольше, чем» — that T is its hold, a second one after it would make
    it T + a minute), or an own sensor of that kind in HA's registry (platform hauflo, its translation key: a rule's
    «Сработало» is on only after its own «дольше» / periods in a row). By what it is, never by its name."""
    if hass is None:
        return None
    if eid in signals.combo_entities(hass):
        return "combo"
    entry = er.async_get(hass).async_get(eid)
    key = entry.translation_key if entry is not None and entry.platform == DOMAIN and entry.domain == "binary_sensor" else None
    return key if key in HOLD_CANON["own"] else None


def own_holds(hass: HomeAssistant | None, sources: list[str], holds: dict[str, int]) -> dict[str, int]:
    """{source: its hold} for the rule's sources that hold their time themselves (own_kind): the site's own hold of
    that very signal if it set one, else the canon's for its kind (notify.hold.own) — never another source's class,
    nor a scope's (a class's choice, the canon's level): the rule's other sources keep theirs."""
    pol = signals.policy(hass) or {}
    out: dict[str, int] = {}
    for eid in sources:
        if (kind := own_kind(hass, eid)) is not None:
            mine = ((pol.get("entities") or {}).get(eid) or {}).get("holdS")
            out[eid] = int(mine) if isinstance(mine, int) and eid in holds else int(HOLD_CANON["own"][kind])
    return out
LINK_KINDS = ("connection", "link", "connectivity")
# The HA app's push fails quietly — mobile_app logs it and the service call returns (narrow round 25.10, D4: «sent»
# for a phone that never got it). Which phone a log line is about: the task sending to it.
_PHONE: contextvars.ContextVar[str | None] = contextvars.ContextVar("hauflo_phone", default=None)


class _PushErrors(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.failed: set[str] = set()
        # The HA app's push relay said «Rate limit exceeded» (HTTP 429): mobile_app logs it as a WARNING, any other
        # failure as an ERROR (owner 27.10: a raw «HA 429» on the screen).
        self.limited: set[str] = set()

    def emit(self, record: logging.LogRecord) -> None:
        if phone := _PHONE.get():
            self.failed.add(phone)
            if record.levelno == logging.WARNING or "rate limit" in record.getMessage().lower():
                self.limited.add(phone)


# Pushes per phone the agent allows itself (owner 27.10: «лимит пушей HA»): HA's relay has a daily limit per phone —
# a flapping house must not use it up before the leak. Normal ones are capped hourly and daily; critical ones go
# until the higher daily cap. What's over is still on the screen and in HA's bell, and Hauflo's Telegram/email
# don't depend on it.
PUSH_HOUR_MAX = _T["push_hour_max"]
PUSH_DAY_MAX = _T["push_day_max"]
PUSH_DAY_CRITICAL = _T["push_day_critical"]
# Repeats of an unaccepted alarm at the rule's pace this many times; then at most hourly until someone accepts.
REPEAT_FAST = _T["repeat_fast"]
REPEAT_SLOW = _T["repeat_slow_min"]
SCOPES = ("all", "critical", "off")
PHONE_ID = re.compile(r"^mobile_app_[a-z0-9_]+$")
# A rule's kind and alarm states are plain HA words (security N-6 07.11, same rule as backend bodyEnums.badRuleField):
# never a template or markup on their way into an automation.
WORD = re.compile(r"^[a-z0-9_]{1,32}$")
STATES_MAX = _T["rule_states_max"]
# A test rings a phone at full volume: at most once per phone in this many seconds, and a few tests a minute in all.
TEST_GAP = _T["test_gap_s"]
TEST_PER_MIN = _T["test_per_min"]
GROUP_MIN = _T["group_min"]  # from this many devices at once — one message instead of one per device
LIST_MAX = _T["list_max"]
HEARTBEAT = re.compile(_T["heartbeat_words"], re.I)
# A classless binary sensor named a fault («ГК ошибка», «Вентмашина Авария»): a problem (09.11 п.18 — the boiler picker
# found nothing). The cards' FAULT_NAME, the entity_id's transliteration too.
FAULT_NAME = re.compile(_T["fault_name_words"], re.I)
# Alarm outputs (399-15, «исполнитель тревоги»): what a rule switches itself — a siren on when its alarm is told, off on
# «Принято» or once the rule is back to normal. Only a domain of this table, only its services, only an entity the
# integrator chose for this very rule (saved in it — the same trust as a picked source: the cloud checked it against
# what this agent reported). A light's flash or a relay is one more row of the table, never code.
OUTPUTS: dict[str, dict[str, str]] = _T["outputs"]
OUTPUTS_MAX = _T["outputs_max"]
ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
# Level rules (399-20): a number below / above a threshold — a battery under 20 %, CO2 over 1500 ppm — with hysteresis
# (ISA-18.2 deadband): open, it ends only once the value is back past the threshold by `step` (battery: 20 → ≥ 25), so a
# value wobbling at the line is one alarm, not a storm. The defaults by device class are one table (tuning.json
# notify.levels); a class not in it gets `step` as a share of the threshold.
LEVELS: dict[str, dict[str, float]] = _T["levels"]
LEVEL_STEP_SHARE = _T["level_step_share"]
LEVEL_ABS_MAX = _T["level_abs_max"]
# An event entity (a doorbell press) is one push per press: a second press within this many seconds is the same visit,
# and an event older than `event_recent_s` is a restored state after a restart, not a press.
RING_WINDOW = _T["ring_window_s"]
RING_FRESH = TUNING["link"]["event_recent_s"]
# What a press is (0.33 п.9): kind → (HA device classes, words of its event type / name); the first match wins.
QUIET_CHANNEL = _T["quiet_channel"]  # Android: a channel made low (no sound) for the quiet hours' pushes
C_SOS = frozenset(_T["event_critical_kinds"])  # kinds pushed as critical
EVENT_KINDS = {k: (frozenset(v.get("classes", ())), re.compile(v["words"], re.I)) for k, v in _T["event_kinds"].items() if not k.startswith("_")}
RING = _T["ring_push"]  # Р55: a doorbell's push — its picture, its tap, «answered» taking it off
INTERCOM_EVENT = f"{DOMAIN}_intercom"  # the agent's own intercom (intercom/manager.py): its call's moments


def event_kind(state: State) -> str:
    """«doorbell» (a doorbell, an intercom's SIP call), «sos» (a panic button) or «event» (any other press) — one rule for
    the push here and the bell / Telegram line in the cloud (status `eventClass`)."""
    dc = str(state.attributes.get("device_class") or "")
    for kind, (classes, _) in EVENT_KINDS.items():
        if dc and dc in classes:
            return kind
    said = f"{state.attributes.get('event_type') or ''} {state.name or ''} {state.object_id}"
    return next((kind for kind, (_, words) in EVENT_KINDS.items() if words.search(said)), "event")


def watched_press(state: State | None, platform: str | None = None) -> bool:
    """A press the canon watches by default though HA gives it no doorbell class (acceptance 0.33 M4): an event entity
    whose kind is one of `event_kinds` — an SOS / panic button, an intercom's SIP call. Any other press stays the
    integrator's choice (the label / «Уведомлять»)."""
    return state is not None and state.domain == "event" and platform not in signals.WATCH_SKIP and event_kind(state) in EVENT_KINDS


SOURCE_DOMAINS = ("binary_sensor.", "sensor.")


def labelled(hass: HomeAssistant | None, eid: str) -> bool:
    """The integrator marked it «Hauflo мониторинг» in HA (on the entity or its device) — HA's admin's say, not the
    cloud's. The integrator's «Уведомлять» switch in Hauflo's list (foreign.py, п.63.6) says it first."""
    if hass is None:
        return False
    if (said := foreign.notify_flag(hass, eid)) is not None:
        return said
    try:
        entry = er.async_get(hass).async_get(eid)
        if entry is None:
            return False
        ids = set(entry.labels)
        if entry.device_id and (device := dr.async_get(hass).async_get(entry.device_id)):
            ids |= set(device.labels)
        reg = lr.async_get(hass)
        return any((label := reg.async_get_label(i)) is not None and label.name.casefold() in MONITOR_LABELS for i in ids)
    except Exception:  # noqa: BLE001 — not knowable: not marked
        return False


def _source_ok(hass: HomeAssistant | None, eid: str, owns: Callable[[str], bool] | None = None, picked: frozenset[str] = frozenset()) -> bool:
    """An alarm watches a sensor of the house — never a person, a tracker or a phone's own sensors (panel round 6: a
    rule on «person.…» told the server when the family left) — and only one of Hauflo's own or one the integrator
    marked «Hauflo мониторинг» in HA (security review 28.10: the cloud put the family's motion sensor on a critical
    rule repeating every minute)."""
    if not eid.startswith(SOURCE_DOMAINS):
        return False
    entry = er.async_get(hass).async_get(eid) if hass is not None else None
    if entry is not None and entry.platform == "mobile_app":
        return False
    # «Picked»: the integrator chose it in Hauflo from the entities this agent reported (any integration — Zigbee, MQTT,
    # Z-Wave…); the cloud checked it against that list. Never a phone's, never a person's (filtered above).
    if owns is not None and owns(eid):
        return True
    # «Уведомлять» off in Hauflo's list (п.63.6): no alarm from it, a rule picking it included.
    if foreign.notify_flag(hass, eid) is False:
        return False
    return labelled(hass, eid) or eid in picked


def _device_class(hass: HomeAssistant | None, eid: str) -> str | None:
    if hass is None:
        return None
    entry = er.async_get(hass).async_get(eid)
    dc = (entry.device_class or entry.original_device_class) if entry is not None else None
    if dc is None and (st := hass.states.get(eid)) is not None:
        dc = st.attributes.get("device_class")
    return str(dc) if dc is not None else None


# A source without a device class (KNX binary sensors often have none) may ring loud — but not one that says it is a
# motion/presence sensor (security review 28.10: the family's motion sensor on a critical rule), nor a door / window /
# gate contact (Д8, 29.10) — by its name or its entity id (transliterated: okno, dver…). Word starts only: «outdoor»,
# «поворот» are no door or gate.
QUIET_NAME = re.compile(_T["quiet_name_words"], re.I)
# …unless it says it is a leak / smoke / gas sensor: «Протечка у окна» stays loud.
LOUD_NAME = re.compile(_T["loud_name_words"], re.I)
DOWNGRADE = re.compile(r"^(no_sources|quiet_class:[a-z_]{1,32}|quiet_name)$")


def loud_verdict(hass: HomeAssistant | None, sources: list[str]) -> str | None:
    """Why a rule the integrator marked critical rings only normally; None = rings loud, even in silent mode.
    Loud: every source is moisture/smoke/gas/CO/heat/problem/safety or has no device class at all (a KNX sensor).
    Normal: no source left, any source of another class (motion, door, window, occupancy, presence, battery…), or
    a classless one that is named like a motion/presence sensor."""
    if not sources:
        return "no_sources"
    for s in sources:
        dc = _device_class(hass, s)
        if dc is not None and dc not in CRITICAL_CLASSES:
            return f"quiet_class:{dc}"[:45]
        if dc is None:
            st = hass.states.get(s) if hass is not None else None
            name = f"{s} {(st.attributes.get('friendly_name') or '') if st else ''}"
            if QUIET_NAME.search(name) and not LOUD_NAME.search(name):
                return "quiet_name"
    return None


def _output_ok(hass: HomeAssistant | None, eid: str) -> bool:
    """An alarm output: a domain of OUTPUTS, a plain entity id, never a phone's, and one HA knows (registry or state)."""
    if not ENTITY_ID.match(eid) or eid.partition(".")[0] not in OUTPUTS:
        return False
    if hass is None:
        return True
    entry = er.async_get(hass).async_get(eid)
    if entry is not None and entry.platform == "mobile_app":
        return False
    return entry is not None or hass.states.get(eid) is not None


def _clean_outputs(hass: HomeAssistant | None, raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for o in raw if isinstance(raw, list) else []:
        if not isinstance(o, dict) or not isinstance(o.get("entity_id"), str) or any(x["entity_id"] == o["entity_id"] for x in out):
            continue
        on_start, on_end = o.get("on_start", True) is not False, o.get("on_end", True) is not False
        if (on_start or on_end) and _output_ok(hass, o["entity_id"]):
            out.append({"entity_id": o["entity_id"], "on_start": on_start, "on_end": on_end})
        else:
            _LOGGER.warning("Hauflo: alarm output %r not accepted (allowed: %s, known in HA)", str(o.get("entity_id"))[:80], ", ".join(sorted(OUTPUTS)))
    return out[:OUTPUTS_MAX]


def _number(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    try:
        x = float(v)
    except ValueError:
        return None
    return x if x == x and abs(x) <= LEVEL_ABS_MAX else None  # never NaN / inf / a typo of a million


def level_default(device_class: str | None) -> dict[str, float] | None:
    """The canon threshold of a device class (tuning.json notify.levels): its first direction and its step."""
    canon = LEVELS.get(str(device_class or ""))
    if not canon:
        return None
    way = "below" if "below" in canon else "above"
    return {way: canon[way], "step": canon["step"]}


def _clean_thresholds(hass: HomeAssistant | None, raw: Any, sources: list[str]) -> dict[str, dict[str, float]]:
    """Per source: exactly one of below / above (a number) and the hysteresis step — only for a level (sensor.*) source."""
    out: dict[str, dict[str, float]] = {}
    if not isinstance(raw, dict):
        return out
    for eid, th in raw.items():
        if eid not in sources or not str(eid).startswith("sensor.") or not isinstance(th, dict):
            continue
        ways = [w for w in ("below", "above") if w in th]
        value = _number(th.get(ways[0])) if len(ways) == 1 else None
        if value is None:
            _LOGGER.warning("Hauflo: threshold of %s not accepted: one number below / above expected, got %r", eid, th)
            continue
        canon = LEVELS.get(str(_device_class(hass, eid) or ""), {})
        step = _number(th.get("step"))
        if step is None or step < 0:
            step = float(canon["step"]) if "step" in canon else round(abs(value) * LEVEL_STEP_SHARE, 3)
        out[eid] = {ways[0]: value, "step": step}
    return out


def _value(state: State | None) -> float | None:
    if state is None or state.state in NOT_A_STATE:
        return None
    return _number(state.state)


def _threshold(rule: dict[str, Any], eid: str) -> dict[str, float] | None:
    """The source's level condition: the rule's own, else the site's policy one (signals.py — set only where the rule has
    no own condition for it)."""
    th = (rule.get("thresholds") or {}).get(eid)
    if th is not None:
        return th
    pol = (rule.get("policy") or {}).get(eid)
    return pol if pol is not None and ("below" in pol or "above" in pol) else None


def muted(rule: dict[str, Any], eid: str) -> bool:
    """The site marked this source «не авария» itself (signals.py: a scope or the signal's own `alarm: {none}`): the rule
    keeps it as sent (the cloud's list stays whole), but never rings, never waits for its data and never counts it."""
    return bool(((rule.get("policy") or {}).get(eid) or {}).get("none"))


def live_sources(rule: dict[str, Any]) -> list[str]:
    """The rule's sources the engine weighs: all but the muted ones."""
    return [s for s in rule["sources"] if not muted(rule, s)]


def in_alarm(rule: dict[str, Any], state: State | None, open_now: bool = False) -> bool:
    """Is this source in alarm for the rule: a signal — one of its alarm states; a level — past its threshold, and while
    the alarm is open, until it is back past the threshold by its step (the hysteresis). Not a number: not an alarm.
    A rule with no own states / threshold takes the source's from the site's policy (`policy`, signals.py) — its states
    compared by value (any language: an enum «Авария» too)."""
    if state is None or muted(rule, state.entity_id):
        return False
    th = _threshold(rule, state.entity_id)
    if th is None:
        pol = (rule.get("policy") or {}).get(state.entity_id)
        return state.state in (pol["states"] if pol is not None and "states" in pol else rule["alarm_states"])
    if "below" in th and "above" in th:
        v = _value(state)
        if v is None:
            return False
        step = th.get("step", 0)
        return v < th["below"] or v > th["above"] or (open_now and (v <= th["below"] + step or v >= th["above"] - step))
    v = _value(state)
    if v is None:
        return False
    # Back strictly past the step, as HA's own numeric_state in the rule's automation (independent.py): battery < 20 →
    # alarm, 25 still in it, > 25 → back.
    if "below" in th:
        return v < th["below"] or (open_now and v <= th["below"] + th["step"])
    return v > th["above"] or (open_now and v >= th["above"] - th["step"])


def level_text(rule: dict[str, Any], state: State | None) -> str:
    """« (18 % < 20 %)» — the value and its threshold, said in numbers (no words: any language)."""
    th = _threshold(rule, state.entity_id) if state is not None else None
    v = _value(state)
    if th is None or v is None or state is None:
        return ""
    unit = str(state.attributes.get("unit_of_measurement") or "")
    way = "below" if "below" in th else "above"
    return f" ({v:g} {unit} {'<' if way == 'below' else '>'} {th[way]:g} {unit})".replace("  ", " ").replace(" )", ")")


def _action_ok(action: dict[str, Any] | None, owns: Callable[[str], bool] | None) -> bool:
    """A push button closes one of Hauflo's own valves or switches off one of its own switches — never the
    integrator's gate relay (integrator run 22) nor a valve that isn't Hauflo's (security review 28.10)."""
    if not action:
        return False
    service, eid = str(action.get("service")), str(action.get("entity_id"))
    if service not in RUN_SERVICES or eid.split(".")[0] != service.split(".")[0]:
        return False
    return owns is not None and bool(owns(eid))


_SCOPE_RANK = {"off": 0, "critical": 1, "all": 2}


def narrowing(new: dict[str, Any], old: dict[str, Any]) -> str | None:
    """While the owner keeps the house closed, Hauflo may only narrow the alarm rules: fewer sources, recipients,
    repeats, a rule off, a button gone — never a new rule, source, recipient, button, louder or more often (security
    review 28.10). Both cleaned. None = narrowing; else what widens."""
    before = {r["id"]: r for r in old.get("rules") or []}
    for r in new.get("rules") or []:
        p = before.get(r["id"])
        if p is None:
            return f"new rule {r['id']}"
        if (r["kind"], r["title"], r["alarm_states"]) != (p["kind"], p["title"], p["alarm_states"]):
            return f"{r['id']}: changed text or alarm states"
        if not set(r["sources"]) <= set(p["sources"]):
            return f"{r['id']}: new source"
        rc, pc = r["recipients"], p["recipients"]
        if (rc["all"] and not pc["all"]) or any(not set(rc[k]) <= set(pc[k]) for k in ("people", "groups", "phones")):
            return f"{r['id']}: new recipient"
        if r.get("action") and r.get("action") != p.get("action"):
            return f"{r['id']}: new button"
        if any(o not in (p.get("outputs") or []) for o in r.get("outputs") or []):
            return f"{r['id']}: new output"
        if any((p.get("thresholds") or {}).get(e) != th for e, th in (r.get("thresholds") or {}).items()):
            return f"{r['id']}: new threshold"
        if r["priority"] == "critical" and p["priority"] != "critical":
            return f"{r['id']}: critical"
        if r["repeat_min"] and (not p["repeat_min"] or r["repeat_min"] < p["repeat_min"]):
            return f"{r['id']}: repeats more often"
        if r["enabled"] and not p["enabled"]:
            return f"{r['id']}: switched on"
    groups = {g["id"]: g for g in old.get("groups") or []}
    for g in new.get("groups") or []:
        if g["id"] not in groups or not set(g["people"]) <= set(groups[g["id"]]["people"]):
            return f"group {g['id']}: new people"
    na, oa = new.get("audience") or {}, old.get("audience") or {}
    if set(oa) - set(na):
        return "audience: a setting removed"
    for k, v in na.items():
        if _SCOPE_RANK[v] > _SCOPE_RANK[oa[k]] if k in oa else v != "off":
            return f"audience {k}: more"
    # «Пуш о звонке»: switched on (or its «off» taken away) is more.
    nr, or_ = new.get("rings") or {}, old.get("rings") or {}
    for k, v in or_.items():
        if v is False and nr.get(k) is not False:
            return f"rings {k}: more"
    for k, v in nr.items():
        if v is True and or_.get(k) is not True:
            return f"rings {k}: more"
    return None


def push_data(os: str | None, *, tag: str, critical: bool = False, quiet: bool = False, actions: list | None = None, silent: bool = False) -> dict[str, Any]:
    """The `data` of a push to the HA app — the engine's and the alarm automations' (independent.py) one shape.
    `silent`: a new message without a sound (the house's quiet hours) — iOS passive, Android its own low channel."""
    data: dict[str, Any] = {"tag": tag}
    if actions:
        data["actions"] = actions
    if quiet:  # an update of a message already there: no sound, no banner
        data.update({"push": {"interruption-level": "passive"}} if os == "ios" else {"alert_once": True})
    if silent and not critical:
        data.update({"push": {"interruption-level": "passive"}} if os == "ios" else {"channel": QUIET_CHANNEL, "importance": "low"})
    if critical:
        if os == "ios":
            data["push"] = {"sound": {"name": "default", "critical": 1, "volume": 1.0}, "interruption-level": "critical"}
        else:
            data.update({"priority": "high", "ttl": 0, "channel": "alarm_stream", "importance": "high"})
    return data


def _button(action: dict[str, Any], name: str) -> str:
    """Said by the house, not by the server: what the button does and to what (integrator run 22: a server's
    «Принял» on a button that turned the gate relay off)."""
    return f"{'Закрыть' if action['service'] == 'valve.close_valve' else 'Выключить'}: {name}"[:60]


# The pushes in English for an English project (integrator run 23: «Floor heating fault: авария в 07:15», «Принято»).
# The engine words its messages in Russian; these turn them into English, whole fragments first.
_EN_FRAGMENTS = [
    ("Hauflo: проверка уведомлений", "Hauflo: notification test"),
    ("Так будут приходить аварии.", "This is how alarms will arrive."),
    (" Критичные — со звуком даже в беззвучном режиме.", " Critical ones ring even on silent."),
    (", никто не подтвердил. Повтор ", ", nobody accepted it. Repeat "),
    ("Никто не подтвердил. Повтор ", "Nobody accepted it. Repeat "),
    (": снова в норме.", ": back to normal."),
    ("Снова в норме: ", "Back to normal: "),
    (": авария в ", ": alarm at "),
    (": авария с ", ": alarm since "),
    (": авария.", ": alarm."),
    (" (последняя в ", " (latest at "),
    (": выполнено (", ": done ("),
    (": не получилось — ", ": failed — "),
    ("Открыто: ", "Open: "),
    ("Принято: ", "Accepted: "),
    ("Выключить: ", "Turn off: "),
    ("Закрыть: ", "Close: "),
    ("кто-то", "someone"),
]
_EN_WORDS = {"Принято": "Accept", "Авария": "Alarm"}
# A rule's title as Hauflo suggests it, in the language of the page it was made on (integrator run 25: «🚨 Авария
# оборудования» over an English push) — only a title that is exactly one of these.
_EN_TITLES = {"Протечка": "Water leak", "Дым": "Smoke", "Газ": "Gas", "Авария котла": "Boiler alarm", "Авария оборудования": "Equipment alarm", "Температура": "Temperature", "Батарейка": "Battery", "Двери и окна": "Doors and windows", "Своё": "Custom", "Связь со щитом": "Panel link lost", "Авария": "Alarm"}
_RU_TITLES = {en: ru for ru, en in _EN_TITLES.items()}


def label(hass: HomeAssistant, entity_id: str, state: State | None = None) -> str:
    """What a person calls the device: HA's friendly name, else the registry's name, else its device's name — the raw
    object id («leak_bathroom») only when nothing else exists (stage 10 round 3)."""
    state = state or hass.states.get(entity_id)
    if name := (str(state.attributes.get("friendly_name") or "").strip() if state else ""):
        return plain(once(name))
    entry = er.async_get(hass).async_get(entity_id)
    if entry:
        if name := (entry.name or entry.original_name or ""):
            return plain(once(str(name)))
        if entry.device_id and (device := dr.async_get(hass).async_get(entry.device_id)):
            if name := (device.name_by_user or device.name or ""):
                return plain(once(str(name)))
    return plain(entity_id.partition(".")[2] or entity_id)


_SNAKE = re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b")


def plain(name: str) -> str:
    """A machine id said as words (acceptance 0.33: «climate_living Temperature» — an MQTT device named by its id): the
    snake_case tokens spaced, as HA itself says an entity without a name («leak_bathroom» → «Leak bathroom»)."""
    out = _SNAKE.sub(lambda m: m.group(0).replace("_", " "), name.strip())
    return out[:1].upper() + out[1:] if out != name.strip() else out


def once(name: str) -> str:
    """«Звонок в дверь Звонок в дверь» → «Звонок в дверь»: HA's friendly name is «device + entity», and a device whose
    one entity is named as the device itself (an MQTT doorbell / SOS button) says it twice (acceptance 0.33)."""
    half, rest = len(name) // 2, len(name) % 2
    if rest and name[half] == " " and name[:half] == name[half + 1 :]:
        return name[:half]
    return name


@callback
def _event_entity(data: Any) -> bool:
    """State changes of event entities only (a doorbell press)."""
    return str(data.get("entity_id") or "").startswith("event.")


@callback
def _automation_event(data: Any) -> bool:
    """State changes of automations only (the engine's watch of the alarm rules' own, H-1)."""
    return str(data.get("entity_id") or "").startswith("automation.")


def _en(text: str) -> str:
    if text in _EN_WORDS:
        return _EN_WORDS[text]
    for ru, en in _EN_FRAGMENTS:
        text = text.replace(ru, en)
    text = re.sub(r"^С (\d)", r"Since \1", text)
    text = re.sub(r" и ещё (\d+)", r" and \1 more", text)
    return re.sub(r"(\d+) (устройство|устройства|устройств)\b", lambda m: f"{m.group(1)} device{'' if m.group(2) == 'устройство' else 's'}", text)


def policy_overlay(hass: HomeAssistant | None, sources: list[str], *, own_states: bool, thresholds: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """({source: its alarm condition}, {source: its hold}) from the site's policy (signals.py) — only what the site chose
    itself (a scope or the signal's own), only where the rule has no own condition: the rule's own states / threshold /
    hold always win (audit 10.11 §3.7). «Не авария» ({none}) chosen by the site silences the source in every rule too
    (0.34: one choice excludes the entity everywhere — signal, rule, counter, «Всё хорошо»): {none: True}, see muted()."""
    pol = signals.policy(hass)
    if hass is None or not pol or not (pol["scopes"] or pol["entities"]):
        return {}, {}
    overlay: dict[str, dict[str, Any]] = {}
    holds: dict[str, int] = {}
    for eid in sources:
        st = hass.states.get(eid)
        r = signals.resolve(eid, platform=signals.platform_of(hass, eid), device_class=st.attributes.get("device_class") if st else None, name=st.name if st else None, policy=pol)
        if r["holdS"] is not None:
            holds[eid] = r["holdS"]
        a = r["alarm"]
        if a and "none" in a and r["from"]["alarm"] != "canon":
            overlay[eid] = {"none": True}
            continue
        if r["from"]["alarm"] == "canon" or not a or "none" in a or eid in thresholds:
            continue
        if "states" in a:
            if not own_states:
                overlay[eid] = {"states": list(a["states"])}
        elif not own_states:
            overlay[eid] = {k: float(a[k]) for k in ("below", "above") if k in a} | {"step": float(a.get("step", 0))}
    return overlay, holds


def policy_hold(hass: HomeAssistant | None, kind: str, sources: list[str], thresholds: dict[str, Any], holds: dict[str, int]) -> int:
    """The canon hold (default_hold) with the site's own hold of a source in place of its class's; the shortest."""
    if not holds:
        return default_hold(hass, kind, sources, thresholds)
    rest = [e for e in sources if e not in holds]
    found = [*holds.values()]
    if rest or kind in HOLD_CANON["kinds"]:
        found.append(default_hold(hass, kind, rest, thresholds))
    return int(min(found))


def _clean_rules(raw: Any, hass: HomeAssistant | None = None, owns: Callable[[str], bool] | None = None) -> dict[str, Any]:
    """Only what the engine understands; anything else from the wire is dropped."""
    out: dict[str, Any] = {"rules": [], "groups": []}
    if not isinstance(raw, dict):
        return out
    if raw.get("lang") in ("ru", "en"):
        out["lang"] = raw["lang"]
    # Who gets the rules sent «to everyone»: a person or a phone → «all» / «critical» / «off»; «*» — anyone not
    # listed (owner 27.10: «только себе» wasn't possible without groups).
    aud = raw.get("audience")
    if isinstance(aud, dict):
        out["audience"] = {str(k): str(v) for k, v in list(aud.items())[:200] if v in SCOPES and (k == "*" or str(k).startswith("person.") or PHONE_ID.match(str(k)))}
    # «Пуш о звонке» (0.33 п.9): a person / phone / «*» → on or off; not said — on unless its alarms are «off».
    # Always said (empty too): the cloud knows by it that this agent keeps them (its resync compares them then).
    rings = raw.get("rings")
    out["rings"] = {str(k): v for k, v in list(rings.items())[:200] if isinstance(v, bool) and (k == "*" or str(k).startswith("person.") or PHONE_ID.match(str(k)))} if isinstance(rings, dict) else {}
    for g in raw.get("groups") or []:
        if isinstance(g, dict) and g.get("id") and isinstance(g.get("people"), list):
            out["groups"].append({"id": str(g["id"]), "name": str(g.get("name") or ""), "people": [str(p) for p in g["people"] if str(p).startswith("person.")]})
    for r in raw.get("rules") or []:
        if not isinstance(r, dict) or not r.get("id") or not isinstance(r.get("sources"), list):
            continue
        rec = r.get("recipients") or {}
        action = r.get("action") if isinstance(r.get("action"), dict) else None
        picked = frozenset(p for p in r["picked"] if isinstance(p, str)) if isinstance(r.get("picked"), list) else frozenset()
        # A rule whose alarm_states are not valid state names («Alarm», «water leak») is refused with its reason, never
        # read as «on»: it would silently never fire (review r3 R3-9). Absent / empty = the default «on».
        states_raw = r.get("alarm_states")
        if states_raw not in (None, []) and not (isinstance(states_raw, list) and len(states_raw) <= STATES_MAX and all(isinstance(s, str) and WORD.match(s) for s in states_raw)):
            out.setdefault("rejected", []).append({"id": str(r["id"]), "reason": "bad_alarm_states", "value": [str(s)[:40] for s in states_raw][:STATES_MAX + 1] if isinstance(states_raw, list) else str(states_raw)[:40]})
            _LOGGER.warning("Hauflo: alarm rule %s not accepted: alarm_states %r are not state names (a-z, 0-9, _), up to %d", str(r["id"])[:64], states_raw, STATES_MAX)
            continue
        sources = [str(s) for s in r["sources"] if _source_ok(hass, str(s), owns, picked)]
        # Loud only for safety sources or classless ones (security review 28.10: a motion sensor rang through silent
        # mode every minute); repeats no more often than every REPEAT_MIN minutes.
        # Owner 28.10: a rule marked critical never goes quiet unseen — the reason is kept in «downgrade» and shown.
        wanted = r.get("priority") == "critical"
        verdict = loud_verdict(hass, sources) if wanted else None
        critical = wanted and verdict is None
        downgrade = verdict if wanted else (str(r["downgrade"]) if DOWNGRADE.match(str(r.get("downgrade") or "")) else None)
        repeat = max(0, min(_int(r.get("repeat_min")), 240))
        outputs = _clean_outputs(hass, r.get("outputs"))
        thresholds = _clean_thresholds(hass, r.get("thresholds"), sources)
        kind = str(r.get("kind")) if WORD.match(str(r.get("kind") or "")) else "custom"
        own_hold = hold_value(r.get("hold_s"))
        overlay, holds = policy_overlay(hass, sources, own_states=bool(states_raw), thresholds=thresholds)
        # A source that holds its time itself (a combination's T): its own hold, the rule's others by their classes —
        # the rule's `hold` is theirs (all of them own — the shortest own). The rule's own hold_s is for every source.
        selfheld = own_holds(hass, sources, holds) if own_hold is None else {}
        rest = [e for e in sources if e not in selfheld]
        if own_hold is not None:
            hold = own_hold
        elif rest or not selfheld:
            hold = policy_hold(hass, kind, rest, thresholds, {e: h for e, h in holds.items() if e not in selfheld})
        else:
            hold = min(selfheld.values())
        out["rules"].append(
            {
                "id": str(r["id"]),
                "kind": kind,
                "title": str(r.get("title") or "Авария")[:80],
                "sources": sources,
                **({"picked": [s for s in sources if s in picked]} if any(s in picked for s in sources) else {}),
                "alarm_states": list(states_raw) if states_raw else ["on"],
                "recipients": {
                    "all": bool(rec.get("all")),
                    "people": [str(p) for p in rec.get("people") or [] if str(p).startswith("person.")],
                    "groups": [str(g) for g in rec.get("groups") or []],
                    "phones": [str(p) for p in rec.get("phones") or [] if PHONE_ID.match(str(p))],
                },
                "priority": "critical" if critical else "normal",
                **({"downgrade": downgrade} if downgrade and not critical else {}),
                "repeat_min": max(REPEAT_MIN, repeat) if repeat else 0,
                "enabled": r.get("enabled", True) is not False,
                **(
                    {"action": {"title": str(action.get("title") or "")[:40], "service": str(action.get("service") or ""), "entity_id": str(action.get("entity_id") or "")}}
                    if action and _action_ok(action, owns)
                    else {}
                ),
                **({"outputs": outputs} if outputs else {}),
                **({"thresholds": thresholds} if thresholds else {}),
                # The site's policy for its sources where the rule set none (signals.py; derived, never stored as own).
                **({"policy": overlay} if overlay else {}),
                # As sent (None — the canon's; Hauflo sees by the key that this agent knows holds) and the one used.
                "hold_s": own_hold,
                "hold": hold,
                # A source's own hold where it isn't the rule's (own_holds): HA's `for:` and the engine take it.
                **({"holds": selfheld} if selfheld else {}),
            }
        )
    # A canon title in the house's one language both ways (0.33 С3: a rule made on an English page kept «Equipment alarm»
    # over a Russian push «…: авария в 01:00» — only Russian → English was turned before). A typed title stays as is.
    titles = _RU_TITLES if site_ru(getattr(getattr(hass, "config", None), "language", None) if hass is not None else None, out) else _EN_TITLES
    for r in out["rules"]:
        r["title"] = titles.get(r["title"], r["title"])
    return out


def _devices(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "устройство"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "устройства"
    return "устройств"



def _int(v: Any) -> int:
    """A number from Hauflo's rules, never a 500 on «abc» (HA review round 4)."""
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0

class LockedChange(PermissionError):
    """The owner closed the house and the new rules would widen what is told (423 locked_by_owner)."""


class NotifyEngine:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: HaufloStore = HaufloStore(hass, STORE_KEY)
        # Open alarms survive an HA restart: an alarm already told isn't told again on start.
        self.active_store: HaufloStore = HaufloStore(hass, ACTIVE_KEY)
        self.config: dict[str, Any] = {"rules": [], "groups": []}
        # Whether the rules are known at all: a stored config (even with no rules) or one Hauflo sent. A re-added agent
        # before its first rules reports «not ready» and the cloud keeps its open alarms; a site that simply has no rules
        # is ready — before 0.31 it never was, and the cloud never closed its «нет данных» (prod ГВГ, 10.11: 33 open).
        self.ready = False
        self._unsub_state = None
        self._unsub_action = None
        self._unsub_events = None
        self._unsub_intercom: Callable[[], None] | None = None
        self._rang: dict[str, float] = {}  # event entity → monotonic time of its last push
        # Set by the link to Hauflo: is an entity watched (the status' own rule, so the push, the bell and Telegram
        # agree — 0.33 п.9); unset — the «Hauflo мониторинг» label alone, as before.
        self.watches: Callable[[str], bool] | None = None
        # Set by the link to Hauflo: is it the house's quiet hours now (rules.py) — a press then comes without a sound.
        self.quiet_now: Callable[[], bool] | None = None
        # Set by the link to Hauflo: an event entity of a lock's device (locks.py, Р56) — its press is the lock's push
        # («открыта отпечатком · Мария»), not a «кнопка» ring; a doorbell / SOS of such a device still rings.
        self.lock_owns: Callable[[str], bool] | None = None
        # incident key (rule id|entity) → {"since", "acked_by", "repeat": cancel, "count", "group"}
        self.active: dict[str, dict[str, Any]] = {}
        # rule id → {"since", "acked_by", "count", "repeat"}: many devices of one rule told as one message
        self.groups: dict[str, dict[str, Any]] = {}
        # Told before the restart, their state not known yet (KNX reads the bus after the agent starts).
        self._restored: dict[str, dict[str, Any]] = {}
        self._pending_start: dict[str, list[str]] = {}
        self._pending_clear: dict[str, list[tuple[str, dict[str, Any], State]]] = {}
        self._pending_nodata: dict[str, list[str]] = {}
        self._pending_back: dict[str, list[str]] = {}
        self._nodata_timers: dict[str, Callable[[], None]] = {}
        self._flaps: dict[str, list[float]] = {}
        # A critical alarm back to normal that nobody accepted stays on the screen until someone does (ISA-18.2
        # «returned, unacknowledged» — SCADA expert 25.10: it vanished, nobody knew the boiler room had flooded).
        self.unacked_rtn: dict[str, dict[str, Any]] = {}
        # A house problem (a signal in trouble by the site's policy, no rule's alarm) accepted: entity → who (0.34 fix п.2:
        # «Принять» on it said «принял …» and the counter never moved). Kept while the problem lasts (house_acks_keep).
        self.house_acks: dict[str, str] = {}
        self._chatter: dict[str, Callable[[], None]] = {}
        # incident key → cancel: in alarm, not yet for the rule's hold (its `for:`) — started only if it stays.
        self._holding: dict[str, Callable[[], None]] = {}
        self._flush_unsub = None
        # rule id → cancel: a flat-form rule's look after its hold (Р83, _handed) — cancelled on stop.
        self._late_kicks: dict[str, Callable[[], None]] = {}
        self.sent_log: list[dict[str, Any]] = []
        # Set by the link to Hauflo: called when open alarms or the rules change.
        self.on_change = None
        # Set by the link to Hauflo: is an entity Hauflo's own (a push button may switch off only those).
        self.owns: Callable[[str], bool] | None = None
        # Set by the link to Hauflo: the owner closed the house (options) — rules may then only narrow.
        self.locked: Callable[[], bool] | None = None
        # Link/heartbeat sensors seen healthy at least once: before that an «off» is commissioning, not a lost link
        # (integrator run 25.10: «Связь со щитом — 39 устройств» right after saving the rules on a site without a bus).
        self._seen_ok: set[str] = set()
        # phone service → wall-clock times of its pushes in the last day (the agent's own cap, PUSH_*) — kept over a
        # restart (active_store), so a restart doesn't give a flapping house a fresh day.
        self._pushes: dict[str, list[float]] = {}
        # phone service → when HA's relay last said «rate limit» / the agent's cap last held a push back.
        self.limited: dict[str, str] = {}
        self.throttled: dict[str, str] = {}
        self._tested: dict[str, float] = {}
        self._tests: list[float] = []
        # Rules HA's own automations run (independent.py, 0.26): rule id → {"automation", "helper", "digest"}. The engine
        # keeps their open alarms for the cards and monitoring and pushes, notes or repeats them only while that
        # automation doesn't run: deleted, off or unavailable in HA (HA review H-1 — the record alone silenced the alarm,
        # _in_ha). Kept when the integration is removed — the automations stay in HA too.
        self.native: dict[str, dict[str, Any]] = {}
        self.native_store: HaufloStore = HaufloStore(hass, NATIVE_STORE)
        self._unsub_helper = None
        self._native_who: dict[str, str] = {}
        # Rules just handed to HA with an alarm the engine hadn't told yet (open when the rules came): told once by the
        # engine — HA's automation only sees alarms that start after it exists.
        self._native_first: set[str] = set()
        # A rule HA runs, its alarm open over a restart of HA: whether its sources came back meanwhile (see _missed_back).
        self._missed: dict[str, Any] = {}
        self._missed_src: dict[str, list[str]] = {}  # … and its sources open then (the saved alarms)
        self._unsub_automations = None
        # rule id → the alarm outputs (sirens) this engine switched on for it: off again on «Принято», back to normal, or
        # when the rule loses them.
        self._sounding: dict[str, set[str]] = {}

    @callback
    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    async def async_start(self) -> None:
        stored = await self.store.async_load()
        self.config = _clean_rules(stored, self.hass, self.owns)
        self.ready = stored is not None
        saved = await self.active_store.async_load() or {}
        self._seen_ok = {str(x) for x in saved.get("seen_ok") or []}
        self._pushes = {str(k): [float(t) for t in v if isinstance(t, (int, float))] for k, v in (saved.get("pushes") or {}).items() if isinstance(v, list)}
        self.unacked_rtn = {str(k): v for k, v in (saved.get("rtn") or {}).items() if isinstance(v, dict)}
        self.house_acks = {str(k): str(v)[:80] for k, v in (saved.get("house_acks") or {}).items() if isinstance(v, str) and v}
        self._restored = {k: v for k, v in (saved.get("active") or {}).items() if isinstance(v, dict)}
        for rule_id, g in (saved.get("groups") or {}).items():
            if isinstance(g, dict):
                self.groups[rule_id] = {"since": g.get("since"), "acked_by": g.get("acked_by"), "count": int(g.get("count") or 1), "repeat": None, "last": g.get("last")}
        saved_native = await self.native_store.async_load()
        self.native = {str(k): v for k, v in (saved_native or {}).items() if isinstance(v, dict) and v.get("automation") and v.get("helper")}
        self._unsub_action = self.hass.bus.async_listen(ACTION_EVENT, self._on_action)
        self._watch_helpers()
        self._unsub_automations = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_automation, event_filter=_automation_event)
        self._unsub_events = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_event_entity, event_filter=_event_entity)
        self._unsub_intercom = self.hass.bus.async_listen(INTERCOM_EVENT, self._on_intercom)
        self._apply()

        self._missed_src = {}
        for key, inc in self._restored.items():
            if not inc.get("nodata"):
                self._missed_src.setdefault(key.partition("|")[0], []).append(key.partition("|")[2])

        async def _started(_hass: HomeAssistant) -> None:
            for rule_id in list(self.native):
                self._watch_missed(rule_id)

        async_at_started(self.hass, _started)

    @callback
    def async_stop(self) -> None:
        if self._unsub_events:
            self._unsub_events()
            self._unsub_events = None
        if self._unsub_intercom:
            self._unsub_intercom()
            self._unsub_intercom = None
        if self._unsub_automations:
            self._unsub_automations()
            self._unsub_automations = None
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_action:
            self._unsub_action()
            self._unsub_action = None
        if self._unsub_helper:
            self._unsub_helper()
            self._unsub_helper = None
        if self._flush_unsub:
            self._flush_unsub()
            self._flush_unsub = None
        for cancel in [*self._nodata_timers.values(), *self._chatter.values(), *self._holding.values(), *self._missed.values(), *self._late_kicks.values()]:
            cancel()
        self._late_kicks.clear()
        self._nodata_timers.clear()
        self._chatter.clear()
        self._holding.clear()
        self._missed.clear()
        snapshot = self._snapshot()
        self.active_store.async_delay_save(lambda: snapshot, 0)
        for inc in [*self.active.values(), *self.groups.values()]:
            if inc.get("repeat"):
                inc["repeat"]()
        self.active.clear()
        self.groups.clear()

    def check(self, raw: Any) -> dict[str, Any]:
        """The rules as the engine would keep them — LockedChange when the owner's closed house refuses them."""
        config = _clean_rules(raw, self.hass, self.owns)
        if self.locked is not None and self.locked() and (why := narrowing(config, self.config)):
            raise LockedChange(why)
        return config

    @callback
    def policy_changed(self) -> bool:
        """The site's policy changed (signals.py): each rule's overlay and hold taken again; True — a rule HA runs as its own
        automation now needs the engine (its automation is written again by the caller)."""
        before = {r["id"]: (r.get("policy"), r["hold"], r.get("holds")) for r in self.config["rules"]}
        self.config = _clean_rules(self.config, self.hass, self.owns)
        after = {r["id"]: (r.get("policy"), r["hold"], r.get("holds")) for r in self.config["rules"]}
        if before == after:
            return False
        self._apply()
        self._changed()
        return any(rid in self.native and before.get(rid) != after[rid] for rid in after)

    async def async_set(self, raw: Any) -> dict[str, Any]:
        config = self.check(raw)
        self.config = config
        self.ready = True
        await self.store.async_save(self.config)
        self._apply()
        self._changed()
        return self.config

    async def async_remove(self) -> None:
        # The open alarms stay: re-added, the agent doesn't tell (and ring) the accepted ones again.
        await self.store.async_remove()

    def _snapshot(self) -> dict[str, Any]:
        keep = ("since", "acked_by", "count", "group", "own", "last", "pushed")
        return {
            "active": {**self._restored, **{k: {f: v.get(f) for f in keep} for k, v in self.active.items()}},
            "groups": {r: {f: g.get(f) for f in ("since", "acked_by", "count", "last")} for r, g in self.groups.items()},
            "seen_ok": sorted(self._seen_ok),
            "rtn": self.unacked_rtn,
            "pushes": self._pushes,
            "house_acks": self.house_acks,
        }

    @callback
    def _save(self) -> None:
        self.active_store.async_delay_save(self._snapshot, 2)

    # --- watching -------------------------------------------------------------------------------

    @callback
    def _apply(self) -> None:
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        sources = sorted({s for r in self.config["rules"] if r["enabled"] for s in live_sources(r)})
        if sources:
            self._unsub_state = async_track_state_change_event(self.hass, sources, self._on_state)
        # The rules (and their holds) may have changed: a held alarm is weighed again below, from its state's own time.
        for cancel in self._holding.values():
            cancel()
        self._holding.clear()
        # Already in alarm when the rules arrive: said once now (many at once — one message).
        # Told before an HA restart: taken back quietly; ended while HA was down — «снова в норме».
        for rule in self.config["rules"]:
            if not rule["enabled"]:
                continue
            for entity_id in live_sources(rule):
                key = f"{rule['id']}|{entity_id}"
                state = self.hass.states.get(entity_id)
                if not state or state.state in NOT_A_STATE:
                    self._watch_nodata(rule, entity_id)
                if key in self.active or not state or state.state in NOT_A_STATE:
                    continue
                if not in_alarm(rule, state):
                    self._seen_ok.add(entity_id)
                if key in self._restored:
                    self._adopt(rule, key)
                    if not in_alarm(rule, state, open_now=True):
                        self._clear(rule, state)
                elif in_alarm(rule, state):
                    self._start_held(rule, state)
        # Incidents of rules or sources no longer there end quietly.
        keep = {f"{r['id']}|{s}" for r in self.config["rules"] if r["enabled"] for s in live_sources(r)}
        for key in [k for k in self.active if k not in keep]:
            self._end_quietly(key)
        # Rules not here yet (a re-added agent starts empty until Hauflo sends them — integrator run 25.10:
        # accepted alarms rang again): their open alarms wait for them.
        known = {r["id"] for r in self.config["rules"]}
        self._restored = {k: v for k, v in self._restored.items() if k in keep or k.partition("|")[0] not in known}
        for rule_id in [r for r in self.groups if not any(k.startswith(r + "|") for k in [*self.active, *self._restored])]:
            self._end_group(rule_id)
        # A siren the engine switched on for a rule that no longer has it (rule removed, off, output taken away): off.
        outs = {r["id"]: {o["entity_id"] for o in r.get("outputs") or []} for r in self.config["rules"] if r["enabled"]}
        for rule_id, eids in list(self._sounding.items()):
            gone = eids - outs.get(rule_id, set())
            if gone:
                self.hass.async_create_task(self._switch(rule_id, sorted(gone), "on_end", "rule changed"))
        self._save()

    @callback
    def _on_state(self, event: Event) -> None:
        new: State | None = event.data.get("new_state")
        old: State | None = event.data.get("old_state")
        if not new:
            return
        for rule in self.config["rules"]:
            if not rule["enabled"] or new.entity_id not in rule["sources"] or muted(rule, new.entity_id):
                continue
            key = f"{rule['id']}|{new.entity_id}"
            alarm = in_alarm(rule, new, open_now=key in self.active or key in self._restored)
            if not alarm and new.state not in NOT_A_STATE and new.entity_id not in self._seen_ok:
                self._seen_ok.add(new.entity_id)
                self._save()
            was = in_alarm(rule, old, open_now=key in self.active)
            if not alarm:
                self._unhold(key)
            if new.state in NOT_A_STATE:
                self._watch_nodata(rule, new.entity_id)
            elif (cancel := self._nodata_timers.pop(key, None)) is not None:
                cancel()
            if key in self.active and self.active[key].get("nodata") and new.state not in NOT_A_STATE:
                # Data again: the diagnostic alarm ends; an alarm value now is a real alarm.
                self.active.pop(key)
                persistent_notification.async_dismiss(self.hass, self._tag(key))
                self._pending_back.setdefault(rule["id"], []).append(key)
                self._later()
                if alarm:
                    self._start_held(rule, new)
                continue
            if key in self._restored and new.state not in NOT_A_STATE:
                self._adopt(rule, key)
                if not alarm:
                    self._clear(rule, new)
            elif alarm and not was and key not in self.active:
                if old is not None and old.state in NOT_A_STATE and self._ha_misses(rule, old, new):
                    self._native_first.add(rule["id"])
                self._start_held(rule, new)
            elif not alarm and key in self.active and new.state not in NOT_A_STATE:
                self._clear(rule, new)

    @callback
    def _on_event_entity(self, event: Event) -> None:
        """A press of an event entity the house watches (a doorbell; a button / motion only once the integrator turned it
        on — `labelled`): one push to the HA app by the alarms' own path, never an alarm state or «снова в норме»."""
        new: State | None = event.data.get("new_state")
        old: State | None = event.data.get("old_state")
        if new is None or (old is not None and old.state == new.state):
            return
        at = dt_util.parse_datetime(new.state)
        if at is None or at.tzinfo is None or abs((dt_util.utcnow() - at).total_seconds()) > RING_FRESH:
            return  # unknown / unavailable / a restored old press
        eid = new.entity_id
        if self.lock_owns is not None and self.lock_owns(eid) and event_kind(new) == "event":
            return  # Р56: a lock's own event (unlocked by a fingerprint…) is the locks' push, never a ring
        said = str(new.attributes.get("event_type") or "").lower()
        if said in RING["clear_event_types"] or said in RING["skip_event_types"]:
            # A SIP integration's call answered (taken off every phone) / ended: no push of its own (Р55).
            if said in RING["clear_event_types"] and eid in self._rang:
                self.hass.async_create_task(self._clear_ring(eid))
            return
        now = time.monotonic()
        if now - self._rang.get(eid, -RING_WINDOW) < RING_WINDOW or not (self.watches(eid) if self.watches is not None else labelled(self.hass, eid)):
            return
        self._rang[eid] = now
        self.hass.async_create_task(self._send_ring(new))

    async def _send_ring(self, state: State) -> None:
        """«🔔 Звонок в дверь · Прихожая» / «🆘 SOS · Спальня» / «🔔 Кнопка · Холл» — the device's name below; to every phone
        whose «пуш о звонке» is on (`ring_targets`), no repeats, nothing to accept. An SOS press is critical (through silent
        mode and quiet hours); any other in quiet hours comes without a sound."""
        name, where = self._name(state.entity_id), self._where(state)
        kind = event_kind(state)
        try:
            got = await translation.async_get_translations(self.hass, "en" if self._english() else "ru", "exceptions", [DOMAIN])
        except Exception:  # noqa: BLE001 — words not loaded: the name alone still tells which
            got = {}

        def word(key: str, fallback: str) -> str:
            return got.get(f"component.{DOMAIN}.exceptions.{key}.message") or fallback

        what = word(f"event_push_{kind}", "{name}").format(name=name)
        head = word("event_push_title" if where else "event_push_title_bare", "{what}").format(what=what, where=where)
        message = word(f"event_push_{kind}_text", "{name}").format(name=name)
        critical = kind in C_SOS
        rule = {"id": "event", "title": name, "priority": "critical" if critical else "normal", "recipients": {"all": True, "people": [], "groups": [], "phones": []}}
        silent = not critical and bool(self.quiet_now and self.quiet_now())
        extra = await self._ring_extra(state, urgent=not silent) if kind in RING["kinds"] and not critical else None
        await self._send(rule, head, message, tag=self._tag(f"event|{state.entity_id}"), critical=critical, silent=silent, targets=self.ring_targets(), extra=extra)

    async def _ring_extra(self, state: State, *, urgent: bool) -> Callable[[str | None], dict[str, Any]]:
        """A doorbell's push (Р55): the door's picture (iOS shows the live camera when expanded), a tap opening the
        dashboard view with the door, time-sensitive on iOS / high on Android (not in quiet hours) — the canon in
        tuning.json notify.ring_push."""
        camera = self._door_camera(state.entity_id)
        link = await self._door_link({state.entity_id, *([camera] if camera else [])})

        def extra(os: str | None) -> dict[str, Any]:
            out: dict[str, Any] = {}
            if camera:
                out["image"] = f"/api/camera_proxy/{camera}"
                if os == "ios":
                    out["entity_id"] = camera
            if link:
                out["url" if os == "ios" else "clickAction"] = link
            if urgent:
                out.update({"push": dict(RING["ios"])} if os == "ios" else RING["android"])
            return out

        return extra

    def _door_camera(self, entity_id: str) -> str | None:
        """A camera of the same device (the panel's video; its talk camera skipped)."""
        reg = er.async_get(self.hass)
        entry = reg.async_get(entity_id)
        if entry is None or not entry.device_id:
            return None
        cams = sorted(e.entity_id for e in er.async_entries_for_device(reg, entry.device_id) if e.domain == "camera" and e.translation_key not in RING["skip_camera_keys"])
        return cams[0] if cams else None

    async def _door_link(self, ids: set[str]) -> str | None:
        """«/hauflo-…/<view>» — Hauflo's dashboard view showing the door: its room's, else «Главная»."""
        try:
            from homeassistant.components.lovelace.const import LOVELACE_DATA  # noqa: PLC0415

            dashboards = list(self.hass.data[LOVELACE_DATA].dashboards.items())
        except (ImportError, KeyError):
            return None
        for url_path, dash in dashboards:
            if not str(url_path or "").startswith("hauflo-"):
                continue
            try:
                config = await dash.async_load(False)
            except Exception:  # noqa: BLE001 — not set up yet / a YAML one gone: the next dashboard
                continue
            views = config.get("views") if isinstance(config, dict) else None
            hits = [(i, v) for i, v in enumerate(views or []) if isinstance(v, dict) and any(x in json.dumps(v) for x in ids)]
            if hits:
                i, view = hits[1] if len(hits) > 1 else hits[0]  # the room's view before «Главная» (the first)
                return f"/{url_path}/{view.get('path') or i}"
        return None

    async def _clear_ring(self, entity_id: str) -> None:
        """«Ответили» — the ring's push taken off every phone it went to."""
        self._rang.pop(entity_id, None)
        rule = {"id": "event", "title": "", "priority": "normal", "recipients": {"all": True, "people": [], "groups": [], "phones": []}}
        await self._send(rule, "", "clear_notification", tag=self._tag(f"event|{entity_id}"), critical=False, targets=self.ring_targets(), force=True)

    @callback
    def _on_intercom(self, event: Event) -> None:
        """The agent's own door answered here or on another phone: its ring's push off every phone."""
        call = event.data.get("call")
        if event.data.get("state") in RING["clear_states"] and isinstance(call, str) and call in self._rang:
            self.hass.async_create_task(self._clear_ring(call))

    def rings_on(self, phone: dict[str, Any], config: dict[str, Any] | None = None) -> bool:
        """«Пуш о звонке» of a phone: its own, else its person's, else «*»; not said — on unless its alarms are «off»."""
        rings = (config or self.config).get("rings") or {}
        for key in (phone["service"], phone.get("person"), "*"):
            if key and isinstance(rings.get(key), bool):
                return rings[key]
        return self.scope(phone, config) != "off"

    def ring_targets(self, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return [p for p in self.phones() if self.rings_on(p, config)]

    # --- incidents ------------------------------------------------------------------------------

    def _where(self, state: State) -> str:
        area_id = entity_room(self.hass, state.entity_id)  # Р57: the room's name, never the zone's
        area = ar.async_get(self.hass).async_get_area(area_id) if area_id else None
        return area.name if area else ""

    def _name(self, entity_id: str) -> str:
        return label(self.hass, entity_id)

    def _where_list(self, keys: list[str], rule: dict[str, Any] | None = None) -> str:
        """«Гостиная: Ошибка тёплого пола 3; Холл: Ошибка тёплого пола 1» — every device with its room,
        same names in different rooms kept apart (integrator run 25.10: four sensors read as three)."""
        by: dict[str, list[str]] = {}
        for k in keys:
            entity_id = k.partition("|")[2]
            state = self.hass.states.get(entity_id)
            by.setdefault(self._where(state) if state else "", []).append(self._name(entity_id) + (level_text(rule, state) if rule else ""))
        parts, shown = [], 0
        for area in sorted(by, key=lambda a: (a == "", natural_key(a))):
            if shown >= LIST_MAX:
                break
            names = by[area][: LIST_MAX - shown]
            shown += len(names)
            parts.append((f"{area}: " if area else "") + ", ".join(names))
        rest = len(keys) - shown
        return "; ".join(parts) + (f" и ещё {rest}" if rest > 0 else "")

    @callback
    def _adopt(self, rule: dict[str, Any], key: str) -> None:
        """An alarm told before the restart: open again without telling anyone."""
        saved = self._restored.pop(key)
        self.active[key] = {"since": saved.get("since") or dt_util.utcnow().isoformat(), "acked_by": saved.get("acked_by"), "repeat": None, "count": int(saved.get("count") or 1), "group": bool(saved.get("group")), "own": bool(saved.get("own")), "last": saved.get("last"), "pushed": bool(saved.get("pushed"))}
        group = self.groups.get(rule["id"])
        if self.active[key]["group"] and group:
            if not group["acked_by"] and not group.get("repeat"):
                self._schedule_group(rule)
        elif not self.active[key]["acked_by"]:
            self._schedule(rule, key)
        # Its notice in HA's bell too — gone with the old entry after a re-pair (integrator run 25.10).
        if not self.active[key]["acked_by"]:
            if self.active[key]["group"]:
                members = [k for k, inc in self.active.items() if k.startswith(rule["id"] + "|") and inc.get("group")]
                self._note(f"Открыто: {self._where_list(members)}.", title=f"🚨 {rule['title']} — {len(members)} {_devices(len(members))}", notification_id=self._gtag(rule["id"]), rule=rule)
            elif (state := self.hass.states.get(key.partition("|")[2])) is not None:
                where = self._where(state)
                self._note(f"{self._name(state.entity_id)}: авария.", title=f"🚨 {rule['title']}" + (f" — {where}" if where else ""), notification_id=self._tag(key), rule=rule)
        self._save()

    def _link_unknown(self, rule: dict[str, Any], state: State) -> bool:
        """A link rule's sensor that never reported «on»: its «off» says nothing yet."""
        if rule["kind"] not in ("connection", "link", "connectivity"):
            return False
        return state.entity_id not in self._seen_ok

    @callback
    def _watch_nodata(self, rule: dict[str, Any], entity_id: str) -> None:
        key = f"{rule['id']}|{entity_id}"
        if rule["kind"] in LINK_KINDS or muted(rule, entity_id) or key in self._nodata_timers or key in self.active or entity_id not in self._seen_ok:
            return

        @callback
        def _go(_now) -> None:
            self._nodata_timers.pop(key, None)
            s = self.hass.states.get(entity_id)
            if (s is None or s.state in NOT_A_STATE) and key not in self.active and any(r["id"] == rule["id"] and r["enabled"] for r in self.config["rules"]):
                self.active[key] = {"since": dt_util.utcnow().isoformat(), "acked_by": None, "repeat": None, "count": 0, "group": False, "nodata": True}
                self._pending_nodata.setdefault(rule["id"], []).append(key)
                self._later()

        self._nodata_timers[key] = async_call_later(self.hass, NODATA_AFTER, _go)

    async def _flush_nodata(self, rules: dict[str, dict[str, Any]]) -> None:
        lost, back = self._pending_nodata, self._pending_back
        self._pending_nodata, self._pending_back = {}, {}
        t = await self._words() if lost or back else {}
        for rule_id, keys in lost.items():
            rule = rules.get(rule_id)
            keys = [k for k in keys if k in self.active]
            if not rule or not keys:
                continue
            names = self._where_list(keys)
            title = t.get("nodata_title", "📵 {title}").format(title=rule["title"]) + (f" — {len(keys)} {_devices(len(keys))}" if len(keys) > 1 else "")
            message = t.get("nodata", "{names}").format(minutes=NODATA_AFTER // 60, names=names)
            own = self._engine_says(rule_id)
            await self._send(rule, title, message, tag=self._gtag(rule_id) + "_nd", critical=False, force=own)
            self._note(message, title=title, notification_id=self._gtag(rule_id) + "_nd", rule=None if own else rule)
        for rule_id, keys in back.items():
            rule = rules.get(rule_id)
            if not rule:
                continue
            if not any(k.startswith(rule_id + "|") and inc.get("nodata") for k, inc in self.active.items()):
                persistent_notification.async_dismiss(self.hass, self._gtag(rule_id) + "_nd")
            await self._send(rule, t.get("nodata_back_title", "✅ {title}").format(title=rule["title"]), t.get("nodata_back", "{names}").format(names=self._where_list(keys)),
                             tag=self._gtag(rule_id) + "_nd", critical=False, force=self._engine_says(rule_id))

    @callback
    def _start_held(self, rule: dict[str, Any], state: State) -> None:
        """An alarm state that counts only once it has held for the rule's hold (HA's `for:` in its automation): at once
        when it has (0, or in it since long enough), else a timer — any change out of alarm cancels it (_unhold). The
        flaps (ISA-18.2 chattering, Hauflo's «дребезжит») count only alarms that held."""
        key = f"{rule['id']}|{state.entity_id}"
        # A rule HA runs: held as its automation holds (never under NATIVE_HOLD), so the engine's «дребезжит» counts the
        # alarms HA told, not flickers it ignored.
        hold = source_hold(rule, state.entity_id)
        hold = max(NATIVE_HOLD, hold) if self._engine_says(rule["id"]) else hold
        left = hold - (dt_util.utcnow() - state.last_changed).total_seconds()
        if left <= 0:
            if (cancel := self._holding.pop(key, None)) is not None:
                cancel()
            self._start(rule, state)
            return
        if key in self._holding:
            return
        rule_id, eid = rule["id"], state.entity_id

        @callback
        def _held(_now) -> None:
            self._holding.pop(key, None)
            now_rule = next((r for r in self.config["rules"] if r["id"] == rule_id and r["enabled"] and eid in r["sources"]), None)
            st = self.hass.states.get(eid)
            if now_rule is not None and st is not None and key not in self.active and in_alarm(now_rule, st):
                self._start(now_rule, st)

        self._holding[key] = async_call_later(self.hass, left, _held)

    @callback
    def _unhold(self, key: str) -> None:
        if (cancel := self._holding.pop(key, None)) is not None:
            cancel()
            rule_id = key.partition("|")[0]
            # Nothing of the rule held any more: a rule just handed to HA tells its alarms itself again (async_set_native).
            if rule_id not in self._pending_start and not any(k.startswith(rule_id + "|") for k in self._holding):
                self._native_first.discard(rule_id)

    @callback
    def _start(self, rule: dict[str, Any], state: State) -> None:
        if self._link_unknown(rule, state):
            return
        key = f"{rule['id']}|{state.entity_id}"
        self.unacked_rtn.pop(key, None)  # in alarm again: the open incident is what's shown
        now = time.monotonic()
        # Counted over the common window for every kind (R3-1): the short life-safety one would let a slow flap through.
        flaps = [x for x in self._flaps.get(key, []) if now - x < CHATTER_WINDOW] + [now]
        self._flaps[key] = flaps
        chattering = key in self._chatter
        self.active[key] = {"since": dt_util.utcnow().isoformat(), "acked_by": None, "repeat": None, "count": 0, "group": False, **({"quiet": True} if chattering or len(flaps) >= CHATTER_COUNT else {})}
        self._calm_later(key)
        if chattering:
            return
        if len(flaps) >= CHATTER_COUNT:
            self._chatter[key] = lambda: None
            self.hass.async_create_task(self._say_chatter(rule, state))
            return
        self._pending_start.setdefault(rule["id"], []).append(key)
        self._later()

    @callback
    def _calm_later(self, key: str) -> None:
        """Quiet until the sensor stays put for the window: every change starts the wait again."""
        if key not in self._chatter:
            return
        self._chatter[key]()

        @callback
        def _calm(_now) -> None:
            self._chatter.pop(key, None)
            self._flaps.pop(key, None)
            # Settled in alarm: now it's an alarm like any — told as one (never only «дребезжит»).
            inc = self.active.get(key)
            if inc and inc.pop("quiet", None):
                self._pending_start.setdefault(key.partition("|")[0], []).append(key)
                self._later()

        self._chatter[key] = async_call_later(self.hass, CHATTER_WINDOW, _calm)

    async def _words(self) -> dict[str, str]:
        """The engine's own words in the project's language (translations, R3-7 — not Russian in the code): the
        ``alarm_native_*`` messages by their short key. Not loaded yet — empty; the callers say the names at least."""
        try:
            got = await translation.async_get_translations(self.hass, "en" if self._english() else "ru", "exceptions", [DOMAIN])
        except Exception:  # noqa: BLE001 — words missing never stop an alarm
            return {}
        pre = f"component.{DOMAIN}.exceptions.alarm_native_"
        return {k.removeprefix(pre).removesuffix(".message"): v for k, v in got.items() if k.startswith(pre)}

    async def _say_chatter(self, rule: dict[str, Any], state: State) -> None:
        name = self._name(state.entity_id)
        where = self._where(state)
        # The words of the rule's automation (translations, R3-7: the project's language, not only Russian).
        try:
            got = await self._words()
            # A missing key (translations not loaded yet) says the rule and the sensor rather than nothing (review 07.11).
            t_title = got.get("chatter_title") or "{title}"
            # A rule HA runs (0.31) keeps telling its alarms there: never «приглушены», only the sensor's fault.
            own = self._engine_says(rule["id"])
            t_msg = got.get("chatter_ha" if own else "chatter") or "{name}: {count}/{minutes}"
            title = t_title.format(title=rule["title"]) + (f" — {where}" if where else "")
            message = t_msg.format(name=name, count=CHATTER_COUNT, minutes=max(1, CHATTER_WINDOW // 60))
            await self._send(rule, title, message, tag=self._tag(f"{rule['id']}|{state.entity_id}") + "_chatter", critical=False, force=own)
            self._note(message, title=title, notification_id=self._tag(f"{rule['id']}|{state.entity_id}") + "_chatter", rule=None if own else rule)
        finally:
            self._calm_later(f"{rule['id']}|{state.entity_id}")  # the calm timer runs whatever the words did

    @callback
    def _clear(self, rule: dict[str, Any], state: State) -> None:
        key = f"{rule['id']}|{state.entity_id}"
        inc = self.active.pop(key, None)
        if not inc:
            return
        if inc.get("repeat"):
            inc["repeat"]()
        if rule["priority"] == "critical" and not inc.get("acked_by") and not inc.get("nodata"):
            self.unacked_rtn[key] = {"since": inc["since"], "cleared": dt_util.utcnow().isoformat(), "title": rule["title"]}
            self._changed()
        starting = self._pending_start.get(rule["id"]) or []
        if key in self._chatter:
            self._calm_later(key)
        if inc.get("quiet"):  # never told one by one (a chattering sensor): nothing to take back
            pass
        elif key in starting:  # flickered within the window: never told, nothing to take back
            starting.remove(key)
        else:
            self._pending_clear.setdefault(rule["id"], []).append((key, inc, state))
            self._later()
        self._save()

    @callback
    def _later(self) -> None:
        """Alarms (and returns to normal) of one rule within a few seconds go out together."""
        if self._flush_unsub:
            return

        @callback
        def _go(_now) -> None:
            self._flush_unsub = None
            self.hass.async_create_task(self._flush())

        self._flush_unsub = async_call_later(self.hass, GROUP_WINDOW, _go)

    async def _flush(self) -> None:
        starts, clears = self._pending_start, self._pending_clear
        self._pending_start, self._pending_clear = {}, {}
        rules = {r["id"]: r for r in self.config["rules"]}
        await self._flush_nodata(rules)
        for rule_id, keys in starts.items():
            rule = rules.get(rule_id)
            keys = [k for k in keys if k in self.active]
            if not rule or not keys:
                continue
            if rule.get("outputs") and (not self._in_ha(rule_id) or rule_id in self._native_first):
                # First the siren (not waiting for it: a slow one never holds the pushes), then the phones.
                self.hass.async_create_task(self._switch(rule_id, [o["entity_id"] for o in rule["outputs"] if o["on_start"]], "on_start", "alarm"))
            if len(keys) >= GROUP_MIN or (rule_id in self.groups and not self.groups[rule_id]["acked_by"]):
                # Open single alarms of the rule not accepted yet go into the summary too.
                for k, inc in self.active.items():
                    if k.startswith(rule_id + "|") and not inc.get("acked_by") and not inc.get("group") and k not in keys:
                        keys.append(k)
                        if inc.get("repeat"):
                            inc["repeat"]()
                            inc["repeat"] = None
                        if inc.get("own"):  # its own message leaves the phones: the summary names it
                            await self._send(rule, "", "clear_notification", tag=self._tag(k), critical=False)
                            persistent_notification.async_dismiss(self.hass, self._tag(k))
                            inc["own"] = False
                for k in keys:
                    self.active[k]["group"] = True
                group = self.groups.get(rule_id)
                if not group or group["acked_by"]:
                    self._end_group(rule_id)
                    self.groups[rule_id] = {"since": dt_util.utcnow().isoformat(), "acked_by": None, "count": 0, "repeat": None}
                await self._send_group(rule)
            else:
                for k in keys:
                    if state := self.hass.states.get(k.partition("|")[2]):
                        await self._send_alarm(rule, state, k)
        self._native_first.difference_update(starts)
        for rule_id, items in clears.items():
            rule = rules.get(rule_id)
            if not rule:
                continue
            if not self._in_ha(rule_id) and not any(k.startswith(rule_id + "|") and not inc.get("nodata") for k, inc in self.active.items()):
                if rule.get("outputs"):
                    await self._switch(rule_id, [o["entity_id"] for o in rule["outputs"] if o["on_end"]], "on_end", "back to normal")
                # Its automation off: the engine says this «back» — «авария открыта» ends with it (not said again).
                await self._set_helper(rule_id, False, "open")
            grouped = [(k, s) for k, inc, s in items if inc.get("group")]
            single = [(k, s) for k, inc, s in items if not inc.get("group")]
            if grouped:
                left = [k for k, inc in self.active.items() if k.startswith(rule_id + "|") and inc.get("group")]
                if not left:  # the whole group is fine again: replaces the summary on the phones
                    self._end_group(rule_id)
                    title = f"✅ {rule['title']}" + (f" — {len(grouped)} {_devices(len(grouped))}" if len(grouped) > 1 else "")
                    await self._send(rule, title, f"Снова в норме: {self._where_list([k for k, _ in grouped])}.", tag=self._gtag(rule_id), critical=False)
                    persistent_notification.async_dismiss(self.hass, self._gtag(rule_id))
                else:
                    await self._send_group(rule, back=[k for k, _ in grouped])
            if len(single) >= GROUP_MIN:
                await self._send(rule, f"✅ {rule['title']} — {len(single)} {_devices(len(single))}", f"Снова в норме: {self._where_list([k for k, _ in single])}.", tag=self._gtag(rule_id) + "_ok", critical=False)
                for k, _ in single:
                    persistent_notification.async_dismiss(self.hass, self._tag(k))
            else:
                for k, state in single:
                    where = self._where(state)
                    # Same tag: replaces the alarm on the phones instead of a second message.
                    await self._send(rule, f"✅ {rule['title']}" + (f" — {where}" if where else ""), f"{self._name(state.entity_id)}: снова в норме.", tag=self._tag(k), critical=False)
                    persistent_notification.async_dismiss(self.hass, self._tag(k))
        self._save()
        self._changed()

    def _hhmm(self, iso: str | None) -> str:
        """«01:46» UTC said as the house's clock: HA's configured time zone (integrator review 29.10, defect 9: the
        alarm card said «с 03:46», the push «01:46»). Stored times stay ISO with their offset."""
        at = dt_util.parse_datetime(iso or "") or dt_util.utcnow()
        if at.tzinfo is None:
            at = at.replace(tzinfo=dt_util.UTC)
        tz = dt_util.get_time_zone(str(self.hass.config.time_zone or "")) or dt_util.get_default_time_zone()
        return at.astimezone(tz).strftime("%H:%M")

    async def _send_alarm(self, rule: dict[str, Any], state: State, key: str) -> None:
        inc = self.active.get(key)
        if not inc or inc.get("acked_by"):
            return
        inc["count"] += 1
        inc["last"] = dt_util.utcnow().isoformat()
        inc["own"] = True  # its own message is on the phones: taken back by its own tag too
        where = self._where(state)
        name = label(self.hass, state.entity_id, state) + level_text(rule, state)
        title = f"🚨 {rule['title']}" + (f" — {where}" if where else "")
        at = self._hhmm(inc["since"])
        message = f"{name}: авария в {at}." if inc["count"] == 1 else f"{name}: авария с {at}, никто не подтвердил. Повтор {inc['count']}."
        actions = [{"action": f"{ACK}|{key}", "title": "Принято"}]
        if rule.get("action"):
            actions.insert(0, {"action": f"{RUN}|{key}", "title": _button(rule["action"], self._name(rule["action"]["entity_id"])), "destructive": True, "authenticationRequired": True})
        first = rule["id"] in self._native_first
        if await self._send(rule, title, message, tag=self.native[rule["id"]]["automation"] if first else self._tag(key), critical=rule["priority"] == "critical", actions=actions, force=first):
            inc["pushed"] = True  # a phone has it: the alarm card may say so (integrator run 23: said with no phone)
        self._note(f"{message}", title=title, notification_id=self._tag(key), rule=None if first else rule)
        self._schedule(rule, key)
        self._save()

    def _left(self, rule: dict[str, Any], last: str | None, count: int = 0) -> timedelta:
        """Time to the next repeat on the original schedule — a restart or a re-pair doesn't push it back
        (integrator run 25.10: the 11:41 repeat came at 11:46). After REPEAT_FAST repeats nobody accepted — hourly:
        every 5 minutes all night is ~290 pushes a phone, past HA's daily limit (owner 27.10)."""
        full = timedelta(minutes=rule["repeat_min"] if count <= REPEAT_FAST else max(rule["repeat_min"], REPEAT_SLOW))
        sent = dt_util.parse_datetime(last) if last else None
        return max(timedelta(seconds=5), full - (dt_util.utcnow() - sent)) if sent else full

    @callback
    def _schedule(self, rule: dict[str, Any], key: str) -> None:
        inc = self.active.get(key)
        if not rule["repeat_min"] or not inc or self._in_ha(rule["id"]):
            return
        rule_id, entity_id = rule["id"], key.partition("|")[2]

        @callback
        def _again(_now) -> None:
            self.hass.async_create_task(self._repeat(rule_id, entity_id))

        if inc.get("repeat"):  # scheduled twice (taken back while its alarm was being told): one repeat, not two
            inc["repeat"]()
        inc["repeat"] = async_call_later(self.hass, self._left(rule, inc.get("last"), int(inc.get("count") or 0)), _again)

    async def _repeat(self, rule_id: str, entity_id: str) -> None:
        rule = next((r for r in self.config["rules"] if r["id"] == rule_id), None)
        state = self.hass.states.get(entity_id)
        key = f"{rule_id}|{entity_id}"
        if rule and state and key in self.active and in_alarm(rule, state, open_now=True):
            await self._send_alarm(rule, state, key)

    async def _send_group(self, rule: dict[str, Any], *, repeat: bool = False, back: list[str] | None = None) -> None:
        """One message for many devices of a rule: how many, which ones, where."""
        rule_id = rule["id"]
        group = self.groups.get(rule_id)
        # The ones accepted before aren't news: a summary after «Принято» lists only the new ones (integrator run 25.10).
        members = [k for k, inc in self.active.items() if k.startswith(rule_id + "|") and inc.get("group") and not inc.get("acked_by")]
        if not group or group["acked_by"] or not members:
            return
        if not back:
            group["count"] += 1
            group["last"] = dt_util.utcnow().isoformat()
        # Since the earliest of them (a single one folded in started before the summary).
        starts = sorted(self.active[k]["since"] for k in members)
        first = min(starts[0], group["since"])
        at = self._hhmm(first)
        latest = self._hhmm(starts[-1])
        # Started at different times: both said («С 12:20 (последняя в 12:40)»), not only the first.
        if latest != at:
            at = f"{at} (последняя в {latest})"
        n = len(members)
        title = f"🚨 {rule['title']} — {n} {_devices(n)}"
        lines = [f"С {at}: {self._where_list(members, rule)}."]
        if back:
            lines.append("Снова в норме: " + self._where_list(back) + ".")
        if repeat:
            lines.append(f"Никто не подтвердил. Повтор {group['count']}.")
        gkey = f"{rule_id}|*"
        actions = [{"action": f"{ACK}|{gkey}", "title": "Принято"}]
        if rule.get("action"):
            actions.insert(0, {"action": f"{RUN}|{gkey}", "title": _button(rule["action"], self._name(rule["action"]["entity_id"])), "destructive": True, "authenticationRequired": True})
        # A return to normal of some devices updates the message quietly; new alarms ring.
        first = rule_id in self._native_first and not back and not repeat
        if await self._send(rule, title, " ".join(lines), tag=self.native[rule_id]["automation"] if first else self._gtag(rule_id), critical=rule["priority"] == "critical" and not back, actions=actions, quiet=bool(back), force=first):
            for k in members:
                self.active[k]["pushed"] = True
        self._note(" ".join(lines), title=title, notification_id=self._gtag(rule_id), rule=None if first else rule)
        if not back:
            if group.get("repeat"):
                group["repeat"]()
                group["repeat"] = None
            self._schedule_group(rule)

    @callback
    def _schedule_group(self, rule: dict[str, Any]) -> None:
        group = self.groups.get(rule["id"])
        if not rule["repeat_min"] or not group or self._in_ha(rule["id"]):
            return
        rule_id = rule["id"]

        @callback
        def _again(_now) -> None:
            group["repeat"] = None
            if (r := next((x for x in self.config["rules"] if x["id"] == rule_id), None)) and self.groups.get(rule_id) is group:
                self.hass.async_create_task(self._send_group(r, repeat=True))

        if group.get("repeat"):
            group["repeat"]()
        group["repeat"] = async_call_later(self.hass, self._left(rule, group.get("last"), int(group.get("count") or 0)), _again)

    @callback
    def _end_group(self, rule_id: str) -> None:
        group = self.groups.pop(rule_id, None)
        if group and group.get("repeat"):
            group["repeat"]()
        if group:
            persistent_notification.async_dismiss(self.hass, self._gtag(rule_id))

    @callback
    def _end_quietly(self, key: str) -> None:
        inc = self.active.pop(key, None)
        if inc and inc.get("repeat"):
            inc["repeat"]()
        # Its notice in HA's bell goes too (integrator run 23: a deleted rule's «🚨 …» stayed there for good).
        if inc:
            persistent_notification.async_dismiss(self.hass, self._tag(key))

    def watched(self) -> set[str]:
        """Every entity an enabled rule watches (its sources and the integrator's picks): monitored (399-18)."""
        return {e for r in self.config["rules"] if r["enabled"] for e in (*r["sources"], *(r.get("picked") or [])) if not muted(r, e)}

    async def _switch(self, rule_id: str, eids: list[str], when: str, why: str) -> None:
        """An alarm output of a rule (399-15): its service from OUTPUTS for `when` («on_start» / «on_end») on each entity —
        only one of this rule's saved outputs (or, off, one this engine switched on for it). Every call logged."""
        rule = next((r for r in self.config["rules"] if r["id"] == rule_id), None)
        saved = {o["entity_id"] for o in (rule or {}).get("outputs") or []}
        sounding = self._sounding.setdefault(rule_id, set())
        for eid in eids:
            domain = eid.partition(".")[0]
            service = OUTPUTS.get(domain, {}).get(when)
            if service is None or not (eid in saved or (when == "on_end" and eid in sounding)):
                _LOGGER.warning("Hauflo: alarm rule %s: output %s refused (%s)", rule_id, eid, when)
                continue
            try:
                await self.hass.services.async_call(domain, service, {"entity_id": eid}, blocking=True)
                _LOGGER.info("Hauflo: alarm rule %s: %s.%s %s (%s)", rule_id, domain, service, eid, why)
                if when == "on_start":
                    sounding.add(eid)
                else:
                    sounding.discard(eid)
            except Exception as err:  # noqa: BLE001 — a siren that fails never stops the alarm's pushes
                _LOGGER.warning("Hauflo: alarm rule %s: %s.%s %s failed: %s", rule_id, domain, service, eid, err)
        if not sounding:
            self._sounding.pop(rule_id, None)

    @staticmethod
    def _tag(key: str) -> str:
        return "hauflo_" + slugify(key)

    @staticmethod
    def _gtag(rule_id: str) -> str:
        return "hauflo_" + slugify(rule_id) + "_all"

    # --- rules HA's own automations run (independent.py) ------------------------------------------------------------

    @callback
    def _watch_helpers(self) -> None:
        if self._unsub_helper:
            self._unsub_helper()
            self._unsub_helper = None
        if helpers := sorted({str(rec["helper"]) for rec in self.native.values()}):
            self._unsub_helper = async_track_state_change_event(self.hass, helpers, self._on_helper)

    @callback
    def _on_helper(self, event: Event) -> None:
        """«Принято» in HA (the push button through the automation, the helper's toggle, a card): the rule's open alarms
        are accepted in the list too. The engine's own switching (QUIET_PARENT) is already in it."""
        new: State | None = event.data.get("new_state")
        old: State | None = event.data.get("old_state")
        if new is None or old is None or new.state != "on" or old.state != "off" or new.context.parent_id == QUIET_PARENT:
            return
        if rule_id := next((r for r, rec in self.native.items() if rec["helper"] == new.entity_id), None):
            self.hass.async_create_task(self._native_accepted(rule_id, new.context))

    async def _native_accepted(self, rule_id: str, context: Context) -> None:
        who = self._native_who.pop(rule_id, None)
        if who is None:
            who = await self._who(Event("state_changed", context=context))
        changed = False
        for key, inc in self.active.items():
            if key.startswith(rule_id + "|") and not inc.get("acked_by") and not inc.get("nodata"):
                inc["acked_by"] = who
                changed = True
        if (group := self.groups.get(rule_id)) and not group["acked_by"]:
            group["acked_by"] = who
            changed = True
        if changed:
            self._save()
            self._changed()

    async def _set_helper(self, rule_id: str, on: bool, which: str = "helper") -> None:
        """The rule's «Принято» (``helper``) or «авария открыта» (``open``, 0.31) helper switched quietly."""
        rec = self.native.get(rule_id)
        eid = (rec or {}).get(which)
        if not eid or (state := self.hass.states.get(eid)) is None or state.state == ("on" if on else "off"):
            return
        try:
            await self.hass.services.async_call("input_boolean", f"turn_{'on' if on else 'off'}", {"entity_id": eid}, blocking=True, context=Context(parent_id=QUIET_PARENT))
        except Exception as err:  # noqa: BLE001 — the list is right either way; the automation may repeat once more
            _LOGGER.warning("Hauflo: helper %s not switched: %s", eid, err)

    async def async_set_native(self, native: dict[str, dict[str, Any]]) -> None:
        """Which rules HA's automations run now (independent.async_sync). A rule handed over brings its open alarms: the
        engine's repeats stop, its helper says whether they were accepted. A rule taken back is repeated by the engine."""
        before, self.native = self.native, {str(k): dict(v) for k, v in native.items()}
        await self.native_store.async_save(self.native)
        self._watch_helpers()
        rules = {r["id"]: r for r in self.config["rules"]}
        for rule_id in self.native:
            # An alarm held (not yet for the rule's hold) when its automation came or changed: HA's `for:` waits only
            # for a change after that, so the engine tells it once when it holds.
            if (rule_id not in before or before[rule_id].get("digest") != self.native[rule_id].get("digest")) and any(k.startswith(rule_id + "|") for k in self._holding):
                self._native_first.add(rule_id)
            if rule_id in before:
                if "open" in self.native[rule_id] and "open" not in before[rule_id]:  # 0.30 → 0.31: its open alarms are HA's to end
                    await self._set_helper(rule_id, any(k.startswith(rule_id + "|") and not inc.get("nodata") for k, inc in self.active.items()), "open")
                continue
            if rule_id in self._pending_start:
                self._native_first.add(rule_id)
            await self._handed(rule_id)
        for rule_id in before:
            if rule_id in self.native or (rule := rules.get(rule_id)) is None:
                continue
            self._taken_back(rule)
        self._save()
        self._changed()

    def _automation(self, rec: dict[str, Any]) -> State | None:
        eid = er.async_get(self.hass).async_get_entity_id("automation", "automation", rec["automation"])
        return self.hass.states.get(eid) if eid else None

    def _ha_state(self, rec: dict[str, Any]) -> str:
        """«ha» — the rule's automation runs; else why HA doesn't: «missing» (deleted), «off», «unavailable»."""
        st = self._automation(rec)
        if st is None:
            return "missing"
        return "ha" if st.state == "on" else "unavailable" if st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN) else "off"

    def _engine_says(self, rule_id: str | None) -> bool:
        """A rule HA runs in the simple form (0.31: alarm, back, «Принято», repeat — its record has no chatter memory):
        «нет связи» and a chattering sensor are still the engine's to tell."""
        rec = self.native.get(str(rule_id)) if rule_id is not None else None
        return rec is not None and "chatter" not in rec and self._in_ha(rule_id)

    def _ha_misses(self, rule: dict[str, Any], old: State, new: State) -> bool:
        """A new source of a rule HA runs in alarm straight out of a short no-data blip (a second sensor waking up wet)
        while the rule's alarm is open: HA's automation keeps no memory of which sources it told, so it takes it for the
        open one (gap < the back window) and says nothing — the engine tells it once, with the automation's tag."""
        rec = self.native.get(rule["id"]) or {}
        opn = self.hass.states.get(rec.get("open") or "")
        return (self._engine_says(rule["id"]) and rule.get("kind") not in LINK_KINDS and opn is not None and opn.state == "on"
                and (new.last_changed - old.last_changed).total_seconds() < native_back(rule))

    def _in_ha(self, rule_id: str | None) -> bool:
        """HA's own automation tells this rule's alarms now — its record, and the automation itself on in HA (H-1).
        Not on (deleted / off / unavailable): the engine pushes, notes and repeats, as for any rule. HA still starting:
        the record (the automations load later)."""
        rec = self.native.get(str(rule_id)) if rule_id is not None else None
        if rec is None:
            return False
        return not self.hass.is_running or self._ha_state(rec) == "ha"

    @callback
    def _on_automation(self, event: Event) -> None:
        """One of the rules' automations off / deleted / unavailable in HA: its open alarms go on with the engine's
        repeats (new ones with its pushes, _in_ha). On again: its repeat trigger sees an alarm already on — the
        engine's repeats stop, its helper says whether they were accepted. The cards and monitoring hear it at once."""
        new: State | None = event.data.get("new_state")
        old: State | None = event.data.get("old_state")
        st = new or old
        aid = st.attributes.get("id") if st is not None else None
        rule_id = next((r for r, rec in self.native.items() if rec.get("automation") == aid), None) if aid else None
        was, now = old is not None and old.state == "on", new is not None and new.state == "on"
        if rule_id is None or was == now:
            return
        if now:
            self.hass.async_create_task(self._handed(rule_id))
        elif rule := next((r for r in self.config["rules"] if r["id"] == rule_id), None):
            _LOGGER.warning("Hauflo: alarm automation %s doesn't run in HA (%s) — the agent tells its alarms", aid, self._ha_state(self.native[rule_id]))
            self._taken_back(rule)
        self._changed()

    async def _handed(self, rule_id: str) -> None:
        """HA's automation runs the rule (again): the engine's repeats of its open alarms stop, the helper says whether
        they were accepted."""
        keys = [k for k, inc in self.active.items() if k.startswith(rule_id + "|") and not inc.get("nodata")]
        for k in keys:
            if self.active[k].get("repeat"):
                self.active[k]["repeat"]()
                self.active[k]["repeat"] = None
        if (group := self.groups.get(rule_id)) and group.get("repeat"):
            group["repeat"]()
            group["repeat"] = None
        await self._set_helper(rule_id, bool(keys) and all(self.active[k].get("acked_by") for k in keys))
        # Open alarms handed over: HA's automation says their «back» (and its «Принято» works). None in the engine while
        # «авария открыта» is on (HA restarted, the sources not read yet or normal again meanwhile — acceptance 10.11):
        # not switched off quietly — it ends with «снова в норме» once its sources hold normal (_missed_back).
        if keys:
            await self._set_helper(rule_id, True, "open")
        else:
            self._watch_missed(rule_id)
        if keys:

            async def _go(_hass: HomeAssistant) -> None:
                if (self.native.get(rule_id) or {}).get("form") == 2:
                    # HA's own `to: on` trigger tells it after the rule's hold when its sensors come up in alarm: looked
                    # at only after that, never a second push.
                    from .independent import alarm_hold  # noqa: PLC0415 — it builds on this module

                    rule = next((r for r in self.config["rules"] if r["id"] == rule_id), None)
                    later = (alarm_hold(rule) if rule else NATIVE_HOLD) + NATIVE_HOLD

                    @callback
                    def _late(_now: Any) -> None:
                        self._late_kicks.pop(rule_id, None)
                        self.hass.async_create_task(self._kick(rule_id))

                    if cancel := self._late_kicks.pop(rule_id, None):
                        cancel()
                    self._late_kicks[rule_id] = async_call_later(self.hass, later, _late)
                    return
                await self._kick(rule_id)

            async_at_started(self.hass, _go)

    async def _kick(self, rule_id: str) -> None:
        """An open alarm HA's automation didn't start (it was off, HA restarted, its config reloaded): its repeat and
        button live in a run of it, and HA's `for:` triggers wait for a change — the run is started without a trigger
        (HA's own «Выполнить»): no branch of its ``choose`` matches, its loop waits for the repeat / button / «Принято»."""
        rec = self.native.get(rule_id) or {}
        st = self._automation(rec) if rec.get("automation") else None
        if rec.get("form") == 2:
            # The flat form (Р83): its run is the alarm itself — started again while its group is on and nobody accepted
            # it (one more push: said in its description). Not when HA's own trigger already started it (_handed waits
            # the rule's hold for that).
            grp, ack = self.hass.states.get(rec.get("group") or ""), self.hass.states.get(rec.get("helper") or "")
            if st is None or st.state != "on" or st.attributes.get("current") or grp is None or grp.state != "on" or (ack is not None and ack.state == "on"):
                return
            try:
                await self.hass.services.async_call("automation", "trigger", {"entity_id": st.entity_id, "skip_condition": True},
                                                    blocking=False, context=Context(parent_id=QUIET_PARENT))
            except Exception as err:  # noqa: BLE001 — the alarm is on the phones and in the list; only its repeat is late
                _LOGGER.warning("Hauflo: alarm automation %s not started: %s", st.entity_id, err)
            return
        opn = self.hass.states.get(rec.get("open") or "")
        if st is None or st.state != "on" or st.attributes.get("current") or opn is None or opn.state != "on":
            return
        ent = getattr(self.hass.data.get("automation"), "get_entity", lambda _e: None)(st.entity_id)
        variables = dict((getattr(ent, "raw_config", None) or {}).get("trigger_variables") or {})
        try:
            await self.hass.services.async_call("automation", "trigger", {"entity_id": st.entity_id, "skip_condition": True, "variables": variables},
                                                blocking=False, context=Context(parent_id=QUIET_PARENT))
        except Exception as err:  # noqa: BLE001 — the alarm is on the phones and in the list; only its repeat is late
            _LOGGER.warning("Hauflo: alarm automation %s not started: %s", st.entity_id, err)

    @callback
    def _watch_missed(self, rule_id: str, delay: float | None = None) -> None:
        """After HA's start: an open alarm of a rule HA runs is looked at once its sources could have held normal for
        the automation's back window (and the engine's group window on top)."""
        if (cancel := self._missed.pop(rule_id, None)) is not None:
            cancel()
        if (rule := next((r for r in self.config["rules"] if r["id"] == rule_id), None)) is None:
            return

        @callback
        def _go(_now: Any) -> None:
            self._missed.pop(rule_id, None)
            self.hass.async_create_task(self._missed_back(rule_id))

        self._missed[rule_id] = async_call_later(self.hass, native_back(rule) + GROUP_WINDOW if delay is None else delay, _go)

    async def _missed_back(self, rule_id: str) -> None:
        """HA restarted with the rule's alarm open and its sources came back to normal meanwhile (acceptance 10.11): the
        automation's «back» waits for a change from alarm it never saw, so «авария открыта» stayed on for good, nobody
        was told. The engine ends it as the automation would: «снова в норме» with its tag, both helpers off, its notice
        gone, the outputs' «on_end». A source still in alarm — the automation's own (its run, _kick); one back too
        recently — looked at again when it has held the back window (HA may still say it itself)."""
        rule = next((r for r in self.config["rules"] if r["id"] == rule_id and r["enabled"]), None)
        rec = self.native.get(rule_id) or {}
        flat = rec.get("form") == 2
        if rule is None or not self._in_ha(rule_id):
            return
        if flat:
            # The flat form (Р83): no «авария открыта» helper — the run was the alarm and died with HA; the engine's
            # saved alarms say it was open. Its group starts off when the sensors are back: no on → off for HA's own
            # «снова в норме», so the engine says it (a critical rule) or ends it quietly (the note), as the run would.
            if not self._missed_src.get(rule_id):
                return
        else:
            opn = self.hass.states.get(rec.get("open") or "")
            if opn is None or opn.state != "on":
                return
        states = [st for e in rule["sources"] if (st := self.hass.states.get(e)) is not None]
        if any(in_alarm(rule, st, open_now=True) for st in states):
            if flat:
                self._missed_src.pop(rule_id, None)  # the alarm goes on in HA's own run (_kick): its «back» is HA's
            return
        told = [e for e in self._missed_src.get(rule_id) or [] if e in rule["sources"]] or rule["sources"]
        normal = [st for st in states if st.entity_id in told and st.state not in NOT_A_STATE]
        if len(normal) < len(told):  # one of them not read yet (KNX reads the bus later): looked at again
            self._watch_missed(rule_id)
            return
        wait = native_back(rule) + GROUP_WINDOW - min((dt_util.utcnow() - st.last_changed).total_seconds() for st in normal)
        if wait > 0:
            self._watch_missed(rule_id, wait)
            return
        self._missed_src.pop(rule_id, None)
        if flat:
            back = self._automation({"automation": rec["back"]}) if rec.get("back") else None
            if back is not None and back.state == "on":
                # HA's own «снова в норме» run, with its conditions: said once (the alarm told since its last run — never
                # after HA said it itself, before the restart or since), the outputs' «on_end» and the notice its own.
                try:
                    await self.hass.services.async_call("automation", "trigger", {"entity_id": back.entity_id, "skip_condition": False},
                                                        blocking=True, context=Context(parent_id=QUIET_PARENT))
                except Exception as err:  # noqa: BLE001 — said by the engine below then
                    _LOGGER.warning("Hauflo: automation %s not started: %s", back.entity_id, err)
                else:
                    return
            persistent_notification.async_dismiss(self.hass, rec["automation"])
            if rule.get("outputs"):
                await self._switch(rule_id, [o["entity_id"] for o in rule["outputs"] if o["on_end"]], "on_end", "back to normal")
            if rec.get("back"):
                t = await self._words()
                await self._send(rule, t.get("back_title", "✅ {title}").format(title=rule["title"]), t.get("back_all", "OK"), tag=rec["automation"], critical=False, force=True)
            return
        await self._set_helper(rule_id, False, "open")
        await self._set_helper(rule_id, False)
        persistent_notification.async_dismiss(self.hass, rec["automation"])
        if rule.get("outputs"):
            await self._switch(rule_id, [o["entity_id"] for o in rule["outputs"] if o["on_end"]], "on_end", "back to normal")
        t = await self._words()
        names = ", ".join(self._name(st.entity_id) + (f" · {where}" if (where := self._where(st)) else "") for st in normal[:LIST_MAX])
        await self._send(rule, t.get("back_title", "✅ {title}").format(title=rule["title"]), t.get("back", "{name}").format(name=names), tag=rec["automation"], critical=False, force=True)

    @callback
    def _taken_back(self, rule: dict[str, Any]) -> None:
        """The engine tells the rule again: its open alarms nobody accepted are repeated by it."""
        for k, inc in self.active.items():
            if k.startswith(rule["id"] + "|") and not inc.get("acked_by") and not inc.get("nodata") and not inc.get("group") and not inc.get("repeat"):
                self._schedule(rule, k)
        if (group := self.groups.get(rule["id"])) and not group["acked_by"] and not group.get("repeat"):
            self._schedule_group(rule)

    def native_status(self) -> dict[str, Any]:
        """Each rule HA runs: its automation and helper, on/accepted, when it last ran (Hauflo's monitoring); ``state``
        — «ha» (its automation runs) or why the agent tells its alarms instead: «off», «missing», «unavailable»."""
        reg = er.async_get(self.hass)
        out: dict[str, Any] = {}
        for rule_id, rec in self.native.items():
            eid = reg.async_get_entity_id("automation", "automation", rec["automation"])
            st = self.hass.states.get(eid) if eid else None
            hs = self.hass.states.get(rec["helper"])
            last = st.attributes.get("last_triggered") if st else None
            out[rule_id] = {
                "automation": eid,
                "helper": rec["helper"],
                "state": self._ha_state(rec),
                "on": (st.state == "on") if st else None,
                "acked": (hs.state == "on") if hs else None,
                "last_triggered": last.isoformat() if hasattr(last, "isoformat") else last,
                "form": rec.get("form", 1),  # Р83: 2 — the flat form (alarm_form.py); 1 — rewritten by the owner's «переписать»
            }
            if rec.get("form", 1) == 1:
                # Changed in HA's own editor since Hauflo wrote it (independent.py's kept_edited): never written over,
                # so «Переписать» doesn't count it (acceptance Н1 — the banner stayed for good).
                ent = getattr(self.hass.data.get("automation"), "get_entity", lambda _e: None)(eid) if eid else None
                raw = getattr(ent, "raw_config", None)
                if isinstance(raw, dict):
                    from .automations import _digest  # noqa: PLC0415 — automations imports notify
                    out[rule_id]["edited"] = _digest(raw) != rec.get("digest")
        return out

    async def _on_action(self, event: Event) -> None:
        action = str(event.data.get("action") or "")
        if action.startswith((ACK_ACTION, RUN_ACTION, OK_ACTION, DO_ACTION)):
            # A push of HA's own alarm automation: it accepts / runs the button itself; who pressed it is kept for the
            # moment its helper goes on (_on_helper) — the monitoring names that person.
            if rule_id := next((r for r, rec in self.native.items() if action in (rec.get("ack"), rec.get("run"))), None):
                self._native_who[rule_id] = await self._who(event)
            return
        if not action.startswith((ACK + "|", RUN + "|")):
            return
        kind, key = action.split("|", 1)
        await self._accept(kind, key, await self._who(event), event.context)

    async def async_ack(self, key: str, who: str) -> bool:
        """«Принято» from the dashboard (SCADA expert 25.10: only a phone's push button could accept an alarm)."""
        if key in self.unacked_rtn and key not in self.active:
            # Back to normal, now accepted: off the screen.
            self.unacked_rtn.pop(key, None)
            self._save()
            self._changed()
            return True
        if key not in self.active and not (key.endswith("|*") and key.partition("|")[0] in self.groups):
            return False
        await self._accept(ACK, key, who, None)
        return True

    @callback
    def ack_house(self, entity_id: str, who: str) -> bool:
        """«Принято» for a house problem (alarms_now.HOUSE_KEY): no push or siren is its — only who accepted it, until it
        clears (house_acks_keep). False — accepted already."""
        if self.house_acks.get(entity_id):
            return False
        self.house_acks[entity_id] = who[:80]
        self._save()
        self._changed()
        return True

    @callback
    def house_acks_keep(self, problems: set[str]) -> None:
        """A house problem that cleared forgets its «Принято»: the next one is a new alarm (ISA-18.2)."""
        gone = [e for e in self.house_acks if e not in problems]
        for e in gone:
            del self.house_acks[e]
        if gone:
            self._save()

    async def _accept(self, kind: str, key: str, who: str, context: Any) -> None:
        rule_id, _, entity_id = key.partition("|")
        rule = next((r for r in self.config["rules"] if r["id"] == rule_id), None)
        # Only for an alarm that is open now: a fired event alone runs nothing.
        open_now = key in self.active or (entity_id == "*" and rule_id in self.groups)
        # A button on an old push (the alarm is over, or never was): nothing to accept or run — and no «Принято»
        # to every phone, it would replace «снова в норме» under the same tag (integrator run 14).
        if not open_now:
            return
        if kind == RUN and rule and rule.get("action") and open_now and rule["action"]["service"] in RUN_SERVICES:
            domain, _, service = rule["action"]["service"].partition(".")
            try:
                await self.hass.services.async_call(domain, service, {"entity_id": rule["action"]["entity_id"]}, blocking=True, context=context)
                done = f"{_button(rule['action'], self._name(rule['action']['entity_id']))}: выполнено ({who})."
            except Exception as err:  # noqa: BLE001 — said to the people, not swallowed
                done = f"{_button(rule['action'], self._name(rule['action']['entity_id']))}: не получилось — {err}."
        else:
            done = f"Принято: {who}."
        if entity_id == "*":  # the summary of many devices: accepted for all of them
            incs = [inc for k, inc in self.active.items() if k.startswith(rule_id + "|") and inc.get("group")]
            if group := self.groups.get(rule_id):
                group["acked_by"] = who
                if group.get("repeat"):
                    group["repeat"]()
                    group["repeat"] = None
            name, tag = (f"{len(incs)} {_devices(len(incs))}" if incs else rule["title"] if rule else ""), self._gtag(rule_id)
        else:
            incs = [inc] if (inc := self.active.get(key)) else []
            state = self.hass.states.get(entity_id)
            where = self._where(state) if state else ""
            name, tag = self._name(entity_id) + (f" ({where})" if where else ""), self._tag(key)
        for inc in incs:
            inc["acked_by"] = who
            if inc.get("repeat"):
                inc["repeat"]()
                inc["repeat"] = None
        if rule and rule.get("outputs"):
            await self._switch(rule_id, [o["entity_id"] for o in rule["outputs"] if o["on_end"]], "on_end", f"accepted: {who}")
        self._save()
        self._changed()  # monitoring says who accepted it now, not at the next status (integrator run 20)
        native = self.native.get(rule_id)
        if native:
            # HA's automation runs this rule: its «Принято» helper goes on (quietly — the automation says nothing) once
            # no open alarm of the rule waits, and the phones hear it under the automation's own tag.
            if not any(not i.get("acked_by") and not i.get("nodata") for k, i in self.active.items() if k.startswith(rule_id + "|")):
                await self._set_helper(rule_id, True)
            if self._in_ha(rule_id):
                tag = native["automation"]
        if rule:
            await self._send(rule, f"{rule['title']}", f"{name}: {done}", tag=tag, critical=False, force=bool(native))

    async def _who(self, event: Event) -> str:
        user_id = event.context.user_id if event.context else None
        if user_id and (user := await self.hass.auth.async_get_user(user_id)):
            return user.name or "кто-то"
        return "кто-то"

    # --- phones ---------------------------------------------------------------------------------

    def phones(self) -> list[dict[str, Any]]:
        """Every phone with the Home Assistant app: its notify service, OS, owner (person)."""
        ents = er.async_get(self.hass)
        devs = dr.async_get(self.hass)
        owners: dict[str, str] = {}
        for state in self.hass.states.async_all("person"):
            for tracker in state.attributes.get("device_trackers") or []:
                owners[tracker] = state.entity_id
        out = []
        for entry in ents.entities.values():
            if entry.platform != "mobile_app" or entry.domain != "device_tracker" or not entry.device_id:
                continue
            device = devs.async_get(entry.device_id)
            if not device:
                continue
            # As HA's legacy notify names it: slugify("mobile_app_<registration name>").
            service = slugify(f"mobile_app_{device.name or ''}")
            if not self.hass.services.has_service("notify", service):
                continue
            os = "ios" if (device.manufacturer or "").lower() == "apple" else "android"
            out.append({"service": service, "name": device.name_by_user or device.name, "os": os, "person": owners.get(entry.entity_id)})
        return out

    def people(self) -> list[dict[str, Any]]:
        phones = self.phones()
        return [
            {"id": s.entity_id, "name": s.attributes.get("friendly_name") or s.entity_id, "phones": [p for p in phones if p["person"] == s.entity_id]}
            for s in self.hass.states.async_all("person")
        ]

    def scope(self, phone: dict[str, Any], config: dict[str, Any] | None = None) -> str:
        """What a phone gets of the rules sent «to everyone»: its own setting, else its person's, else «*»."""
        aud = (config or self.config).get("audience") or {}
        return aud.get(phone["service"]) or (aud.get(phone["person"]) if phone.get("person") else None) or aud.get("*") or "all"

    def _targets(self, rule: dict[str, Any], config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """The phones of a rule — by the rules in force, or by `config` (rules not saved yet: Hauflo's plan)."""
        phones = self.phones()
        rec = rule["recipients"]
        if rec["all"]:
            critical = rule.get("priority") == "critical"
            return [p for p in phones if (s := self.scope(p, config)) == "all" or (s == "critical" and critical)]
        people = set(rec["people"])
        for g in (config or self.config)["groups"]:
            if g["id"] in (rec.get("groups") or []):
                people.update(g["people"])
        chosen = set(rec.get("phones") or [])
        return [p for p in phones if p["person"] in people or p["service"] in chosen]

    @staticmethod
    def _recent(when: dict[str, str], seconds: int) -> dict[str, str]:
        """HA's relay limit resets within a day, the agent's own hourly: an older mark isn't said any more."""
        now = dt_util.utcnow()
        return {k: v for k, v in when.items() if (t := dt_util.parse_datetime(v)) is not None and (now - t).total_seconds() < seconds}

    def _room(self, service: str, critical: bool) -> bool:
        """Under the agent's own cap for this phone (PUSH_*)."""
        now = time.time()
        log = [t for t in self._pushes.get(service, []) if 0 <= now - t < 86400]
        self._pushes[service] = log
        if critical:
            return len(log) < PUSH_DAY_CRITICAL
        return len(log) < PUSH_DAY_MAX and sum(1 for t in log if now - t < 3600) < PUSH_HOUR_MAX

    def _english(self, config: dict[str, Any] | None = None) -> bool:
        """The project's language when Hauflo said it (in ``config`` — rules not saved yet), else Home Assistant's."""
        return not site_ru(getattr(self.hass.config, "language", None), config or self.config)

    def _say(self, text: str) -> str:
        return _en(text) if text and self._english() else text

    def _note(self, message: str, *, title: str, notification_id: str, rule: dict[str, Any] | None = None) -> None:
        if rule is not None and self._in_ha(rule.get("id")):  # its automation keeps HA's note itself
            return
        persistent_notification.async_create(self.hass, self._say(message), title=self._say(title), notification_id=notification_id)

    async def _send(self, rule: dict[str, Any], title: str, message: str, *, tag: str, critical: bool, actions: list | None = None, targets: list | None = None, quiet: bool = False, results: dict[str, str] | None = None, force: bool = False, silent: bool = False, extra: Callable[[str | None], dict[str, Any]] | None = None) -> list[str]:
        testing = results is not None
        if not testing and not force and self._in_ha(rule.get("id")):
            # HA's own automation pushes this rule (independent.py): the phones it goes to, nothing sent twice.
            return [p["service"] for p in (targets if targets is not None else self._targets(rule))]
        if message != "clear_notification":
            title, message = self._say(title), self._say(message)
        if actions:
            actions = [{**a, "title": self._say(a["title"])} for a in actions]
        sent: list[str] = []
        failed: list[str] = []
        limited: list[str] = []
        held: list[str] = []

        async def one(phone: dict[str, Any]) -> None:
            # Over the agent's cap: not sent (a test is capped on its own — TEST_GAP).
            if not testing and not self._room(phone["service"], critical):
                held.append(phone["service"])
                self.throttled[phone["service"]] = dt_util.utcnow().isoformat()
                return
            self._pushes.setdefault(phone["service"], []).append(time.time())
            self._save()
            data = push_data(phone["os"], tag=tag, critical=critical, quiet=quiet, actions=actions, silent=silent)
            for k, v in (extra(phone["os"]) if extra is not None else {}).items():
                data[k] = {**data[k], **v} if isinstance(v, dict) and isinstance(data.get(k), dict) else v
            _PHONE.set(phone["service"])
            try:
                payload = {"message": message, "data": data, **({"title": title} if title else {})}
                await self.hass.services.async_call("notify", phone["service"], payload, blocking=True)
                if phone["service"] in errors.limited:
                    limited.append(phone["service"])
                    self.limited[phone["service"]] = dt_util.utcnow().isoformat()
                    _LOGGER.warning("Hauflo alarm to %s: the HA app's push limit for this phone is used up for today", phone["service"])
                    return
                if phone["service"] in errors.failed:
                    raise RuntimeError("the HA app's push service said it failed (see HA log)")
                sent.append(phone["service"])
                self.limited.pop(phone["service"], None)
            except Exception as err:  # noqa: BLE001 — a phone that fails mustn't stop the others
                failed.append(phone["service"])
                _LOGGER.warning("Hauflo alarm to %s failed: %s", phone["service"], err)

        # All phones at once: a critical alarm doesn't wait for a slow one (HA core review 25.10).
        errors = _PushErrors()
        app_log = logging.getLogger("homeassistant.components.mobile_app")
        app_log.addHandler(errors)
        try:
            await asyncio.gather(*(asyncio.ensure_future(one(p)) for p in (targets if targets is not None else self._targets(rule))))
        finally:
            app_log.removeHandler(errors)
        self.sent_log = ([{"at": dt_util.utcnow().isoformat(), "title": title, "sent": sent, "failed": failed, **({"limited": limited} if limited else {}), **({"held": held} if held else {})}] + self.sent_log)[:30]
        if results is not None:
            results.update({s: "sent" for s in sent} | {s: "failed" for s in failed} | {s: "rate_limited" for s in limited})
        return sent

    async def async_tell(self, title: str, message: str, user_id: str | None = None, *, actions: list | None = None, tag: str | None = None) -> list[str]:
        """A value rule's message (rules.py), the season's suggestion (season.py): to one HA user's phones («уведомить
        меня»), else to every phone that gets the house's messages (its «audience»), and — to everyone — a note in HA too.
        `tag`: one message replacing the previous one of its kind (push tag and HA note id)."""
        phones = self.phones()
        if user_id:
            people = {s.entity_id for s in self.hass.states.async_all("person") if s.attributes.get("user_id") == user_id}
            targets = [p for p in phones if p["person"] in people]
        else:
            targets = [p for p in phones if self.scope(p) == "all"]
            self._note(message, title=title, notification_id=tag or f"hauflo_rule_{slugify(title)[:40]}")
        return await self._send({}, title, message, tag=tag or f"hauflo-rule-{slugify(title)[:40]}", critical=False, targets=targets, actions=actions)

    def test_wait(self, body: dict[str, Any]) -> int:
        """Seconds until this test may ring its phones (0 = now): each phone at most once per TEST_GAP, TEST_PER_MIN
        tests a minute in all — a test rings at full volume (security re-review round 5), but «Проверить» then
        «Проверить критичное» isn't an error (owner 27.10: a red «HA 429» — this agent's own one-test-a-minute)."""
        now = time.monotonic()
        self._tests = [t for t in self._tests if now - t < 60]
        if len(self._tests) >= TEST_PER_MIN:
            return int(60 - (now - self._tests[0])) + 1
        waits = [TEST_GAP - (now - self._tested[p["service"]]) for p in self._test_targets(body) if p["service"] in self._tested]
        return max([0, *[int(w) + 1 for w in waits if w > 0]])

    def _test_targets(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        rec = {"all": False, "people": [str(p) for p in body.get("people") or []], "groups": [str(g) for g in body.get("groups") or []], "phones": [str(p) for p in body.get("phones") or []]}
        # A test to «everyone» reaches every phone, whatever it gets of the alarms.
        return self.phones() if body.get("all") else self._targets({"recipients": rec})

    async def async_test(self, body: dict[str, Any]) -> dict[str, Any]:
        rule = {"recipients": {"all": False, "people": [], "groups": []}}
        critical = body.get("priority") == "critical"
        targets = self._test_targets(body)
        now = time.monotonic()
        self._tests.append(now)
        for p in targets:
            self._tested[p["service"]] = now
        results: dict[str, str] = {}
        sent = await self._send(
            rule,
            "Hauflo: проверка уведомлений",
            "Так будут приходить аварии." + (" Критичные — со звуком даже в беззвучном режиме." if critical else ""),
            tag="hauflo_test",
            critical=critical,
            targets=targets,
            results=results,
        )
        return {"targets": len(targets), "sent": sent, "results": [{"service": p["service"], "name": p["name"], "os": p.get("os"), "status": results.get(p["service"], "failed")} for p in targets]}

    def active_list(self) -> list[dict[str, Any]]:
        """Open alarms for Hauflo's monitoring: which rule, what, since when, accepted by whom — the ones taken
        back after a restart but not yet confirmed by their sensor too (integrator run 25.10: a green row with
        two open critical alarms)."""
        out = []
        known = {r["id"] for r in self.config["rules"]}
        pending = {k: v for k, v in self._restored.items() if k.partition("|")[0] in known}
        for key, inc in [*self.active.items(), *pending.items()]:
            rule_id, _, entity_id = key.partition("|")
            rule = next((r for r in self.config["rules"] if r["id"] == rule_id), None)
            state = self.hass.states.get(entity_id)
            out.append(
                {
                    # The accept key («rule|entity»): Hauflo's monitoring accepts one alarm with it (D8).
                    "key": key,
                    "entityId": entity_id,
                    "title": rule["title"] if rule else "Авария",
                    "name": label(self.hass, entity_id, state),
                    "since": inc["since"],
                    "ackedBy": inc.get("acked_by"),
                    "area": self._where(state) if state else "",
                    "kind": rule["kind"] if rule else "custom",
                    # A sensor without data, not an alarm value: shown grey «нет связи», never as the alarm itself.
                    "critical": bool(rule and rule["priority"] == "critical") and not inc.get("nodata"),
                    **({"noData": True} if inc.get("nodata") else {}),
                }
            )
        return out

    def alarm_items(self) -> list[dict[str, Any]]:
        """The alarms the one counter (alarms_now.py) reads: the open ones (active_list) and those back to normal that nobody
        accepted yet (`returned` — the alert card asks for a look), with their names and rooms."""
        out = self.active_list()
        for key, v in self.unacked_rtn.items():
            if key in self.active:
                continue
            rule_id, _, entity_id = key.partition("|")
            state = self.hass.states.get(entity_id)
            rule = next((r for r in self.config["rules"] if r["id"] == rule_id), None)
            out.append({"key": key, "entityId": entity_id, "title": v.get("title") or (rule["title"] if rule else ""), "name": label(self.hass, entity_id, state), "since": v.get("since"), "ackedBy": None, "area": self._where(state) if state else "", "critical": bool(rule and rule["priority"] == "critical"), "returned": True})
        return out

    def status(self) -> dict[str, Any]:
        ents = self.hass.states.async_all(["binary_sensor", "sensor"])
        candidates = []
        # Not the agent's own «Связь» entity: Hauflo's cloud link down is no alarm for the residents (integrator run 16:
        # a server restart woke every client with «Связь со щитом — авария»).
        # Nor a phone's own sensors: they are the family's, not the house's (panel round 6).
        own = {e.entity_id for e in er.async_get(self.hass).entities.values() if e.platform in ("hauflo", "mobile_app")}
        for s in ents:
            if s.entity_id in own:
                continue
            dc = s.attributes.get("device_class")
            # A panel's heartbeat / link signal without a class reads as connectivity (off = lost).
            if dc is None and s.domain == "binary_sensor" and HEARTBEAT.search(s.attributes.get("friendly_name") or s.entity_id):
                dc = "connectivity"
            # Named a fault, or watched as an alarm anyway («Hauflo мониторинг» label / «Авария» on in Hauflo's list —
            # its «on» is a problem in the status, 09.11 п.22): offered as a problem source.
            elif dc is None and s.domain == "binary_sensor" and (FAULT_NAME.search(s.attributes.get("friendly_name") or "") or FAULT_NAME.search(s.entity_id) or labelled(self.hass, s.entity_id)):
                dc = "problem"
            # A level (sensor.*) of a class with a canon threshold (399-20): a battery %, CO2, humidity, temperature.
            if dc in ALARM_CLASSES or (s.domain == "sensor" and dc in LEVELS):
                # «Авария» off in Hauflo's list (п.63.6): a rule never takes it (_source_ok) — not offered either.
                if foreign.notify_flag(self.hass, s.entity_id) is False and not (self.owns and self.owns(s.entity_id)):
                    continue
                entry = er.async_get(self.hass).async_get(s.entity_id)
                # Whose it is: Hauflo's own (suggested by itself) or another integration's — taken only when the
                # integrator picks it (owner 27.10: a suggested rule rang about another integration's gas boiler).
                candidates.append({"entity_id": s.entity_id, "name": label(self.hass, s.entity_id, s), "device_class": dc, "area": self._where(s), "state": s.state, "own": bool(self.owns and self.owns(s.entity_id)), "monitored": labelled(self.hass, s.entity_id), "platform": entry.platform if entry else "", **({"original": entry.original_name} if entry and entry.original_name and entry.original_name != s.attributes.get("friendly_name") else {}), **({"unit": str(s.attributes.get("unit_of_measurement") or "")} if s.domain == "sensor" else {})})
        valves = [
            {"entity_id": s.entity_id, "name": s.attributes.get("friendly_name") or s.entity_id, "area": self._where(s)}
            for s in self.hass.states.async_all(["valve", "switch"])
            # A switch only of Hauflo's own: the integrator's gate relay is never a button's (integrator run 22).
            if s.domain == "valve" or (self.owns is not None and self.owns(s.entity_id))
        ]
        reg = er.async_get(self.hass)
        outputs = [
            {"entity_id": s.entity_id, "name": s.attributes.get("friendly_name") or s.entity_id, "area": self._where(s), "platform": (e.platform if (e := reg.async_get(s.entity_id)) else ""), "own": bool(self.owns and self.owns(s.entity_id))}
            for s in self.hass.states.async_all(list(OUTPUTS))
            if _output_ok(self.hass, s.entity_id)
        ]
        return {
            "config": self.config,
            "people": self.people(),
            # What a rule may switch itself (399-15): the house's sirens, any integration — taken only when the integrator
            # picks one for a rule.
            "outputs": sorted(outputs, key=lambda c: (natural_key(c["area"]), natural_key(c["name"]))),
            # The canon thresholds by device class (399-20): the app's defaults for a level source.
            "levels": LEVELS,
            "phones": self.phones(),
            "candidates": sorted(candidates, key=lambda c: (natural_key(c["area"]), natural_key(c["name"]))),
            "actuators": sorted(valves, key=lambda c: (natural_key(c["area"]), natural_key(c["name"]))),
            "active": [{"key": k, "since": v["since"], "acked_by": v["acked_by"], "count": v["count"]} for k, v in self.active.items()],
            "log": self.sent_log[:10],
            # Rules HA's own automations run (0.26): present (maybe empty) — Hauflo then plans them on a deploy.
            "native": self.native_status(),
            # Phones HA's push relay refused today («rate limit») / the agent's own cap held back — said on the page.
            "limits": {"rate_limited": self._recent(self.limited, 86400), "held": self._recent(self.throttled, 3600)},
        }


class NotifyView(HomeAssistantView):
    url = "/api/hauflo/notify"
    name = "api:hauflo:notify"
    requires_auth = True

    def __init__(self, engine_of) -> None:
        self.engine_of = engine_of

    async def get(self, request):
        engine = self.engine_of()
        if not engine:
            return self.json_message("Hauflo agent not set up", 503)
        # People and their phones: admins only (HA core review 25.10).
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        return self.json(engine.status())

    async def post(self, request):
        engine = self.engine_of()
        if not engine:
            return self.json_message("Hauflo agent not set up", 503)
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict):
            return self.json_message("an object expected", 400)
        from . import independent  # noqa: PLC0415 — it builds on this module

        # Р83: the owner's «переписать аварии» (the «Аварии» page, after its dry run): the rules' automations of the form
        # before rewritten in the flat one — never without it (an agent update changes nothing in HA).
        migrate = body.pop("migrate_alarms", None) is True
        try:
            if body.get("dry_run") is True:
                # Hauflo's deploy plan: what the alarm automations in HA would become — nothing saved or written.
                config = engine.check(body)
                return self.json({"config": config, "native": await independent.async_sync(engine.hass, engine, dry_run=True, config=config, migrate=migrate)})
            config = await engine.async_set(body)
        except LockedChange as err:
            # The rules kept as they are; Hauflo says where the owner opens the house (security review 28.10).
            return self.json({"code": LOCKED_CODE, "reason": str(err)}, 423)
        # The rules saved: HA's own automations follow them (independent.py). A failure leaves the engine pushing them.
        try:
            native = await independent.async_sync(engine.hass, engine, migrate=migrate)
        except Exception as err:  # noqa: BLE001 — said to Hauflo, the rules are saved all the same
            _LOGGER.exception("Hauflo: alarm automations not synced")
            native = {"error": str(err)}
        return self.json({"config": config, "native": native})


class NotifyTestView(HomeAssistantView):
    url = "/api/hauflo/notify/test"
    name = "api:hauflo:notify:test"
    requires_auth = True

    def __init__(self, engine_of) -> None:
        self.engine_of = engine_of

    async def post(self, request):
        engine = self.engine_of()
        if not engine:
            return self.json_message("Hauflo agent not set up", 503)
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict):
            return self.json_message("an object expected", 400)
        if wait := engine.test_wait(body):
            return self.json({"message": "a test to this phone was just sent", "code": "test_wait", "retry_after": wait}, 429)
        return self.json(await engine.async_test(body))


class AlarmsView(HomeAssistantView):
    """Since when each open alarm is on — for the dashboard's alarm card (HA's last_changed restarts with HA;
    integrator run 25.10: «с 08:32» after a restart for an alarm since 08:16). Any signed-in user."""

    url = "/api/hauflo/alarms"
    name = "api:hauflo:alarms"
    requires_auth = True

    def __init__(self, engine_of, house_of=None) -> None:
        self.engine_of = engine_of
        self.house_of = house_of

    async def get(self, request):
        engine = self.engine_of()
        out: dict[str, Any] = {}
        pushed: set[str] = set()
        for key, inc in (engine.active.items() if engine else []):
            entity_id = key.partition("|")[2]
            if entity_id not in out or inc["since"] < out[entity_id]:
                out[entity_id] = inc["since"]
            if inc.get("pushed"):
                pushed.add(entity_id)
        # Which of them a phone really got (no entity_id starts with «_»).
        out["_pushed"] = sorted(pushed)
        # The one list every screen shows (SCADA expert 25.10, ISA-18.2): what the rules say is an alarm — not a
        # card's own «state is on» (a link rule's «off» is its alarm) — with priority, since, who accepted it.
        out["_list"] = [
            {"key": key, "entity": key.partition("|")[2], "since": inc["since"], "ackedBy": inc.get("acked_by"), "critical": bool(r and r["priority"] == "critical"), "noData": bool(inc.get("nodata")), "title": r["title"] if r else ""}
            for key, inc in (engine.active.items() if engine else [])
            for r in [next((x for x in engine.config["rules"] if x["id"] == key.partition("|")[0]), None)]
        ] + [
            {"key": key, "entity": key.partition("|")[2], "since": v.get("since"), "returned": v.get("cleared"), "ackedBy": None, "critical": True, "noData": False, "title": v.get("title", "")}
            for key, v in ((engine.unacked_rtn.items()) if engine else [])
            if key not in engine.active
        ]
        # «Всё хорошо» by the site's policy (signals.py, 0.32): `_house` ok / fault / alarm / offline, `_faults` — the
        # problems that move it, `_shelved` — «не решено» ones (listed, never moving it); `_summary` — the one counter of
        # open alarms (alarms_now.py, 0.34 п.2) every card counts and lists, as Hauflo's monitoring and the bot do.
        if self.house_of is not None and (house := self.house_of()) is not None:
            out.update(house)
        return self.json(out)

    async def post(self, request):
        """{"ack": "<rule>|<entity>"} — accepted by the signed-in person, as the push button does."""
        engine = self.engine_of()
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        user = request["hass_user"]
        who = (user.name if user else None) or "кто-то"
        # Through Hauflo's tunnel the caller is the agent's own system user: the person who pressed «Принять» in Hauflo's
        # monitoring is named by Hauflo (service review r2 D8) — never trusted from any other user.
        if user is not None and user.system_generated and user.name == "Hauflo agent" and isinstance(body, dict) and isinstance(body.get("by"), str):
            by = "".join(ch for ch in body["by"] if ch.isprintable()).strip()[:80]
            if by:
                who = f"{by} (Hauflo)"
        # «Принять все» (family review round 2: 19 faults took 19 taps): every open alarm nobody accepted yet, and those
        # back to normal waiting for a look. Open to every person of the house, as one «Принято» is (safety).
        # A house problem (no rule's alarm) is one of them too (0.34 fix п.2: «Принять все» skipped it): its key
        # «_house|<entity>» from the one counter (alarms_now).
        if engine is not None and isinstance(body, dict) and body.get("ack_all") is True:
            keys = [k for k, inc in engine.active.items() if not inc.get("acked_by")] + [k for k in engine.unacked_rtn if k not in engine.active]
            keys += [k for e in self._house_open() if not e["acked"] for k in e["keys"]]
            done = [k for k in keys if await self._ack(engine, k, who)]
            return self.json({"ok": True, "acked": len(done)})
        # «Принять все неисправности» (family review round 3): exactly the alarms the card listed — the equipment faults; a
        # leak, smoke or gas shown beside them keeps its own «Принято».
        if engine is not None and isinstance(body, dict) and "ack_keys" in body:
            keys = body["ack_keys"]
            if not isinstance(keys, list) or len(keys) > 200 or not all(isinstance(k, str) and "|" in k and len(k) <= 300 for k in keys):
                return self.json_message("ack_keys: a list of alarm keys", 400)
            done = [k for k in dict.fromkeys(keys) if await self._ack(engine, k, who)]
            return self.json({"ok": True, "acked": len(done)})
        key = body.get("ack") if isinstance(body, dict) else None
        if engine is None or not isinstance(key, str) or "|" not in key or len(key) > 300:
            return self.json_message("ack: a key expected", 400)
        ok = await self._ack(engine, key, who)
        return self.json({"ok": ok}, 200 if ok else 404)

    def _house_open(self) -> list[dict[str, Any]]:
        """The one counter's house problems open now (those a «_house|<entity>» key accepts)."""
        house = self.house_of() if self.house_of is not None else None
        items = ((house or {}).get("_summary") or {}).get("items") or []
        return [e for e in items if e.get("state") == "open" and e.get("source") == "house" and e.get("keys")]

    async def _ack(self, engine: Any, key: str, who: str) -> bool:
        """A rule's alarm — the engine's accept; a house problem — only while the one counter lists it open."""
        if key.startswith(alarms_now.HOUSE_KEY + "|"):
            entity_id = key.partition("|")[2]
            return any(e["entity"] == entity_id for e in self._house_open()) and engine.ack_house(entity_id, who)
        return await engine.async_ack(key, who)
