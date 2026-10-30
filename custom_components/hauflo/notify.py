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
import logging
import re
import time
from datetime import timedelta
from typing import Any, Callable

from homeassistant.components import persistent_notification
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, label_registry as lr
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util, slugify

from .natural import natural_key
from .storage import HaufloStore
from .const import LOCKED_CODE, MONITOR_LABELS
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
SCOPES = ("all", "critical", "off")
PHONE_ID = re.compile(r"^mobile_app_[a-z0-9_]+$")
# A test rings a phone at full volume: at most once per phone in this many seconds, and a few tests a minute in all.
TEST_GAP = _T["test_gap_s"]
TEST_PER_MIN = _T["test_per_min"]
GROUP_MIN = _T["group_min"]  # from this many devices at once — one message instead of one per device
LIST_MAX = _T["list_max"]
HEARTBEAT = re.compile(_T["heartbeat_words"], re.I)


SOURCE_DOMAINS = ("binary_sensor.", "sensor.")


def labelled(hass: HomeAssistant | None, eid: str) -> bool:
    """The integrator marked it «Hauflo мониторинг» in HA (on the entity or its device) — HA's admin's say, not the
    cloud's."""
    if hass is None:
        return False
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
    return bool(owns is not None and owns(eid)) or labelled(hass, eid) or eid in picked


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
    return None


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


def _en(text: str) -> str:
    if text in _EN_WORDS:
        return _EN_WORDS[text]
    for ru, en in _EN_FRAGMENTS:
        text = text.replace(ru, en)
    text = re.sub(r"^С (\d)", r"Since \1", text)
    text = re.sub(r" и ещё (\d+)", r" and \1 more", text)
    return re.sub(r"(\d+) (устройство|устройства|устройств)\b", lambda m: f"{m.group(1)} device{'' if m.group(2) == 'устройство' else 's'}", text)


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
    for g in raw.get("groups") or []:
        if isinstance(g, dict) and g.get("id") and isinstance(g.get("people"), list):
            out["groups"].append({"id": str(g["id"]), "name": str(g.get("name") or ""), "people": [str(p) for p in g["people"] if str(p).startswith("person.")]})
    for r in raw.get("rules") or []:
        if not isinstance(r, dict) or not r.get("id") or not isinstance(r.get("sources"), list):
            continue
        rec = r.get("recipients") or {}
        action = r.get("action") if isinstance(r.get("action"), dict) else None
        picked = frozenset(p for p in r["picked"] if isinstance(p, str)) if isinstance(r.get("picked"), list) else frozenset()
        sources = [str(s) for s in r["sources"] if _source_ok(hass, str(s), owns, picked)]
        # Loud only for safety sources or classless ones (security review 28.10: a motion sensor rang through silent
        # mode every minute); repeats no more often than every REPEAT_MIN minutes.
        # Owner 28.10: a rule marked critical never goes quiet unseen — the reason is kept in «downgrade» and shown.
        wanted = r.get("priority") == "critical"
        verdict = loud_verdict(hass, sources) if wanted else None
        critical = wanted and verdict is None
        downgrade = verdict if wanted else (str(r["downgrade"]) if DOWNGRADE.match(str(r.get("downgrade") or "")) else None)
        repeat = max(0, min(_int(r.get("repeat_min")), 240))
        out["rules"].append(
            {
                "id": str(r["id"]),
                "kind": str(r.get("kind") or "custom"),
                "title": str(r.get("title") or "Авария")[:80],
                "sources": sources,
                **({"picked": [s for s in sources if s in picked]} if any(s in picked for s in sources) else {}),
                "alarm_states": [str(s) for s in (r.get("alarm_states") or ["on"])],
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
            }
        )
    lang = out.get("lang") or (str(getattr(getattr(hass, "config", None), "language", "") or "ru") if hass is not None else "ru")
    if not str(lang).lower().startswith("ru"):
        for r in out["rules"]:
            r["title"] = _EN_TITLES.get(r["title"], r["title"])
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
        self._unsub_state = None
        self._unsub_action = None
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
        self._chatter: dict[str, Callable[[], None]] = {}
        self._flush_unsub = None
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

    @callback
    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    async def async_start(self) -> None:
        self.config = _clean_rules(await self.store.async_load(), self.hass, self.owns)
        saved = await self.active_store.async_load() or {}
        self._seen_ok = {str(x) for x in saved.get("seen_ok") or []}
        self._pushes = {str(k): [float(t) for t in v if isinstance(t, (int, float))] for k, v in (saved.get("pushes") or {}).items() if isinstance(v, list)}
        self.unacked_rtn = {str(k): v for k, v in (saved.get("rtn") or {}).items() if isinstance(v, dict)}
        self._restored = {k: v for k, v in (saved.get("active") or {}).items() if isinstance(v, dict)}
        for rule_id, g in (saved.get("groups") or {}).items():
            if isinstance(g, dict):
                self.groups[rule_id] = {"since": g.get("since"), "acked_by": g.get("acked_by"), "count": int(g.get("count") or 1), "repeat": None, "last": g.get("last")}
        self._unsub_action = self.hass.bus.async_listen(ACTION_EVENT, self._on_action)
        self._apply()

    @callback
    def async_stop(self) -> None:
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_action:
            self._unsub_action()
            self._unsub_action = None
        if self._flush_unsub:
            self._flush_unsub()
            self._flush_unsub = None
        for cancel in [*self._nodata_timers.values(), *self._chatter.values()]:
            cancel()
        self._nodata_timers.clear()
        self._chatter.clear()
        snapshot = self._snapshot()
        self.active_store.async_delay_save(lambda: snapshot, 0)
        for inc in [*self.active.values(), *self.groups.values()]:
            if inc.get("repeat"):
                inc["repeat"]()
        self.active.clear()
        self.groups.clear()

    async def async_set(self, raw: Any) -> dict[str, Any]:
        config = _clean_rules(raw, self.hass, self.owns)
        if self.locked is not None and self.locked() and (why := narrowing(config, self.config)):
            raise LockedChange(why)
        self.config = config
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
        sources = sorted({s for r in self.config["rules"] if r["enabled"] for s in r["sources"]})
        if sources:
            self._unsub_state = async_track_state_change_event(self.hass, sources, self._on_state)
        # Already in alarm when the rules arrive: said once now (many at once — one message).
        # Told before an HA restart: taken back quietly; ended while HA was down — «снова в норме».
        for rule in self.config["rules"]:
            if not rule["enabled"]:
                continue
            for entity_id in rule["sources"]:
                key = f"{rule['id']}|{entity_id}"
                state = self.hass.states.get(entity_id)
                if not state or state.state in NOT_A_STATE:
                    self._watch_nodata(rule, entity_id)
                if key in self.active or not state or state.state in NOT_A_STATE:
                    continue
                if state.state not in rule["alarm_states"]:
                    self._seen_ok.add(entity_id)
                if key in self._restored:
                    self._adopt(rule, key)
                    if state.state not in rule["alarm_states"]:
                        self._clear(rule, state)
                elif state.state in rule["alarm_states"]:
                    self._start(rule, state)
        # Incidents of rules or sources no longer there end quietly.
        keep = {f"{r['id']}|{s}" for r in self.config["rules"] if r["enabled"] for s in r["sources"]}
        for key in [k for k in self.active if k not in keep]:
            self._end_quietly(key)
        # Rules not here yet (a re-added agent starts empty until Hauflo sends them — integrator run 25.10:
        # accepted alarms rang again): their open alarms wait for them.
        known = {r["id"] for r in self.config["rules"]}
        self._restored = {k: v for k, v in self._restored.items() if k in keep or k.partition("|")[0] not in known}
        for rule_id in [r for r in self.groups if not any(k.startswith(r + "|") for k in [*self.active, *self._restored])]:
            self._end_group(rule_id)
        self._save()

    @callback
    def _on_state(self, event: Event) -> None:
        new: State | None = event.data.get("new_state")
        old: State | None = event.data.get("old_state")
        if not new:
            return
        for rule in self.config["rules"]:
            if not rule["enabled"] or new.entity_id not in rule["sources"]:
                continue
            key = f"{rule['id']}|{new.entity_id}"
            alarm = new.state in rule["alarm_states"]
            if not alarm and new.state not in NOT_A_STATE and new.entity_id not in self._seen_ok:
                self._seen_ok.add(new.entity_id)
                self._save()
            was = bool(old and old.state in rule["alarm_states"])
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
                    self._start(rule, new)
                continue
            if key in self._restored and new.state not in NOT_A_STATE:
                self._adopt(rule, key)
                if not alarm:
                    self._clear(rule, new)
            elif alarm and not was and key not in self.active:
                self._start(rule, new)
            elif not alarm and key in self.active and new.state not in NOT_A_STATE:
                self._clear(rule, new)

    # --- incidents ------------------------------------------------------------------------------

    def _where(self, state: State) -> str:
        entry = er.async_get(self.hass).async_get(state.entity_id)
        area_id = entry.area_id if entry else None
        if not area_id and entry and entry.device_id:
            device = dr.async_get(self.hass).async_get(entry.device_id)
            area_id = device.area_id if device else None
        area = ar.async_get(self.hass).async_get_area(area_id) if area_id else None
        return area.name if area else ""

    def _name(self, entity_id: str) -> str:
        state = self.hass.states.get(entity_id)
        return (state.attributes.get("friendly_name") if state else None) or entity_id

    def _where_list(self, keys: list[str]) -> str:
        """«Гостиная: Ошибка тёплого пола 3; Холл: Ошибка тёплого пола 1» — every device with its room,
        same names in different rooms kept apart (integrator run 25.10: four sensors read as three)."""
        by: dict[str, list[str]] = {}
        for k in keys:
            entity_id = k.partition("|")[2]
            state = self.hass.states.get(entity_id)
            by.setdefault(self._where(state) if state else "", []).append(self._name(entity_id))
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
                self._note(f"Открыто: {self._where_list(members)}.", title=f"🚨 {rule['title']} — {len(members)} {_devices(len(members))}", notification_id=self._gtag(rule["id"]))
            elif (state := self.hass.states.get(key.partition("|")[2])) is not None:
                where = self._where(state)
                self._note(f"{self._name(state.entity_id)}: авария.", title=f"🚨 {rule['title']}" + (f" — {where}" if where else ""), notification_id=self._tag(key))
        self._save()

    def _link_unknown(self, rule: dict[str, Any], state: State) -> bool:
        """A link rule's sensor that never reported «on»: its «off» says nothing yet."""
        if rule["kind"] not in ("connection", "link", "connectivity"):
            return False
        return state.entity_id not in self._seen_ok

    @callback
    def _watch_nodata(self, rule: dict[str, Any], entity_id: str) -> None:
        key = f"{rule['id']}|{entity_id}"
        if rule["kind"] in LINK_KINDS or key in self._nodata_timers or key in self.active or entity_id not in self._seen_ok:
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
        for rule_id, keys in lost.items():
            rule = rules.get(rule_id)
            keys = [k for k in keys if k in self.active]
            if not rule or not keys:
                continue
            names = self._where_list(keys)
            title = f"📵 {rule['title']}: нет связи" + (f" — {len(keys)} {_devices(len(keys))}" if len(keys) > 1 else "")
            message = f"Нет данных {NODATA_AFTER // 60} мин: {names}. Пока связь не вернётся, авария с них не придёт."
            await self._send(rule, title, message, tag=self._gtag(rule_id) + "_nd", critical=False)
            self._note(message, title=title, notification_id=self._gtag(rule_id) + "_nd")
        for rule_id, keys in back.items():
            rule = rules.get(rule_id)
            if not rule:
                continue
            if not any(k.startswith(rule_id + "|") and inc.get("nodata") for k, inc in self.active.items()):
                persistent_notification.async_dismiss(self.hass, self._gtag(rule_id) + "_nd")
            await self._send(rule, f"✅ {rule['title']}: связь есть", f"Снова на связи: {self._where_list(keys)}.", tag=self._gtag(rule_id) + "_nd", critical=False)

    @callback
    def _start(self, rule: dict[str, Any], state: State) -> None:
        if self._link_unknown(rule, state):
            return
        key = f"{rule['id']}|{state.entity_id}"
        self.unacked_rtn.pop(key, None)  # in alarm again: the open incident is what's shown
        now = time.monotonic()
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

    async def _say_chatter(self, rule: dict[str, Any], state: State) -> None:
        name = self._name(state.entity_id)
        where = self._where(state)
        title = f"📳 {rule['title']}" + (f" — {where}" if where else "")
        message = f"{name}: срабатывает и сбрасывается ({CHATTER_COUNT} раза за {CHATTER_WINDOW // 60} мин) — похоже на неисправный датчик. Уведомления по нему приглушены, пока он не успокоится; на экране он виден."
        await self._send(rule, title, message, tag=self._tag(f"{rule['id']}|{state.entity_id}") + "_chatter", critical=False)
        self._note(message, title=title, notification_id=self._tag(f"{rule['id']}|{state.entity_id}") + "_chatter")
        self._calm_later(f"{rule['id']}|{state.entity_id}")

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
        for rule_id, items in clears.items():
            rule = rules.get(rule_id)
            if not rule:
                continue
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
        name = state.attributes.get("friendly_name") or state.entity_id
        title = f"🚨 {rule['title']}" + (f" — {where}" if where else "")
        at = self._hhmm(inc["since"])
        message = f"{name}: авария в {at}." if inc["count"] == 1 else f"{name}: авария с {at}, никто не подтвердил. Повтор {inc['count']}."
        actions = [{"action": f"{ACK}|{key}", "title": "Принято"}]
        if rule.get("action"):
            actions.insert(0, {"action": f"{RUN}|{key}", "title": _button(rule["action"], self._name(rule["action"]["entity_id"])), "destructive": True, "authenticationRequired": True})
        if await self._send(rule, title, message, tag=self._tag(key), critical=rule["priority"] == "critical", actions=actions):
            inc["pushed"] = True  # a phone has it: the alarm card may say so (integrator run 23: said with no phone)
        self._note(f"{message}", title=title, notification_id=self._tag(key))
        self._schedule(rule, key)
        self._save()

    def _left(self, rule: dict[str, Any], last: str | None, count: int = 0) -> timedelta:
        """Time to the next repeat on the original schedule — a restart or a re-pair doesn't push it back
        (integrator run 25.10: the 11:41 repeat came at 11:46). After REPEAT_FAST repeats nobody accepted — hourly:
        every 5 minutes all night is ~290 pushes a phone, past HA's daily limit (owner 27.10)."""
        full = timedelta(minutes=rule["repeat_min"] if count <= REPEAT_FAST else max(rule["repeat_min"], 60))
        sent = dt_util.parse_datetime(last) if last else None
        return max(timedelta(seconds=5), full - (dt_util.utcnow() - sent)) if sent else full

    @callback
    def _schedule(self, rule: dict[str, Any], key: str) -> None:
        inc = self.active.get(key)
        if not rule["repeat_min"] or not inc:
            return
        rule_id, entity_id = rule["id"], key.partition("|")[2]

        @callback
        def _again(_now) -> None:
            self.hass.async_create_task(self._repeat(rule_id, entity_id))

        inc["repeat"] = async_call_later(self.hass, self._left(rule, inc.get("last"), int(inc.get("count") or 0)), _again)

    async def _repeat(self, rule_id: str, entity_id: str) -> None:
        rule = next((r for r in self.config["rules"] if r["id"] == rule_id), None)
        state = self.hass.states.get(entity_id)
        key = f"{rule_id}|{entity_id}"
        if rule and state and key in self.active and state.state in rule["alarm_states"]:
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
        lines = [f"С {at}: {self._where_list(members)}."]
        if back:
            lines.append("Снова в норме: " + self._where_list(back) + ".")
        if repeat:
            lines.append(f"Никто не подтвердил. Повтор {group['count']}.")
        gkey = f"{rule_id}|*"
        actions = [{"action": f"{ACK}|{gkey}", "title": "Принято"}]
        if rule.get("action"):
            actions.insert(0, {"action": f"{RUN}|{gkey}", "title": _button(rule["action"], self._name(rule["action"]["entity_id"])), "destructive": True, "authenticationRequired": True})
        # A return to normal of some devices updates the message quietly; new alarms ring.
        if await self._send(rule, title, " ".join(lines), tag=self._gtag(rule_id), critical=rule["priority"] == "critical" and not back, actions=actions, quiet=bool(back)):
            for k in members:
                self.active[k]["pushed"] = True
        self._note(" ".join(lines), title=title, notification_id=self._gtag(rule_id))
        if not back:
            if group.get("repeat"):
                group["repeat"]()
                group["repeat"] = None
            self._schedule_group(rule)

    @callback
    def _schedule_group(self, rule: dict[str, Any]) -> None:
        group = self.groups.get(rule["id"])
        if not rule["repeat_min"] or not group:
            return
        rule_id = rule["id"]

        @callback
        def _again(_now) -> None:
            group["repeat"] = None
            if (r := next((x for x in self.config["rules"] if x["id"] == rule_id), None)) and self.groups.get(rule_id) is group:
                self.hass.async_create_task(self._send_group(r, repeat=True))

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

    @staticmethod
    def _tag(key: str) -> str:
        return "hauflo_" + slugify(key)

    @staticmethod
    def _gtag(rule_id: str) -> str:
        return "hauflo_" + slugify(rule_id) + "_all"

    async def _on_action(self, event: Event) -> None:
        action = str(event.data.get("action") or "")
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
        self._save()
        self._changed()  # monitoring says who accepted it now, not at the next status (integrator run 20)
        if rule:
            await self._send(rule, f"{rule['title']}", f"{name}: {done}", tag=tag, critical=False)

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

    def scope(self, phone: dict[str, Any]) -> str:
        """What a phone gets of the rules sent «to everyone»: its own setting, else its person's, else «*»."""
        aud = self.config.get("audience") or {}
        return aud.get(phone["service"]) or (aud.get(phone["person"]) if phone.get("person") else None) or aud.get("*") or "all"

    def _targets(self, rule: dict[str, Any]) -> list[dict[str, Any]]:
        phones = self.phones()
        rec = rule["recipients"]
        if rec["all"]:
            critical = rule.get("priority") == "critical"
            return [p for p in phones if (s := self.scope(p)) == "all" or (s == "critical" and critical)]
        people = set(rec["people"])
        for g in self.config["groups"]:
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

    def _english(self) -> bool:
        """The project's language when Hauflo said it, else Home Assistant's."""
        lang = self.config.get("lang") or str(getattr(self.hass.config, "language", "") or "ru")
        return not str(lang).lower().startswith("ru")

    def _say(self, text: str) -> str:
        return _en(text) if text and self._english() else text

    def _note(self, message: str, *, title: str, notification_id: str) -> None:
        persistent_notification.async_create(self.hass, self._say(message), title=self._say(title), notification_id=notification_id)

    async def _send(self, rule: dict[str, Any], title: str, message: str, *, tag: str, critical: bool, actions: list | None = None, targets: list | None = None, quiet: bool = False, results: dict[str, str] | None = None) -> list[str]:
        if message != "clear_notification":
            title, message = self._say(title), self._say(message)
        if actions:
            actions = [{**a, "title": self._say(a["title"])} for a in actions]
        sent: list[str] = []
        failed: list[str] = []
        limited: list[str] = []
        held: list[str] = []
        testing = results is not None

        async def one(phone: dict[str, Any]) -> None:
            # Over the agent's cap: not sent (a test is capped on its own — TEST_GAP).
            if not testing and not self._room(phone["service"], critical):
                held.append(phone["service"])
                self.throttled[phone["service"]] = dt_util.utcnow().isoformat()
                return
            self._pushes.setdefault(phone["service"], []).append(time.time())
            self._save()
            data: dict[str, Any] = {"tag": tag}
            if actions:
                data["actions"] = actions
            if quiet:  # an update of a message already there: no sound, no banner
                data.update({"push": {"interruption-level": "passive"}} if phone["os"] == "ios" else {"alert_once": True})
            if critical:
                if phone["os"] == "ios":
                    data["push"] = {"sound": {"name": "default", "critical": 1, "volume": 1.0}, "interruption-level": "critical"}
                else:
                    data.update({"priority": "high", "ttl": 0, "channel": "alarm_stream", "importance": "high"})
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
                    "name": (state.attributes.get("friendly_name") if state else None) or entity_id,
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
            if dc in ALARM_CLASSES:
                entry = er.async_get(self.hass).async_get(s.entity_id)
                # Whose it is: Hauflo's own (suggested by itself) or another integration's — taken only when the
                # integrator picks it (owner 27.10: a suggested rule rang about another integration's gas boiler).
                candidates.append({"entity_id": s.entity_id, "name": s.attributes.get("friendly_name") or s.entity_id, "device_class": dc, "area": self._where(s), "state": s.state, "own": bool(self.owns and self.owns(s.entity_id)), "monitored": labelled(self.hass, s.entity_id), "platform": entry.platform if entry else ""})
        valves = [
            {"entity_id": s.entity_id, "name": s.attributes.get("friendly_name") or s.entity_id, "area": self._where(s)}
            for s in self.hass.states.async_all(["valve", "switch"])
            # A switch only of Hauflo's own: the integrator's gate relay is never a button's (integrator run 22).
            if s.domain == "valve" or (self.owns is not None and self.owns(s.entity_id))
        ]
        return {
            "config": self.config,
            "people": self.people(),
            "phones": self.phones(),
            "candidates": sorted(candidates, key=lambda c: (natural_key(c["area"]), natural_key(c["name"]))),
            "actuators": sorted(valves, key=lambda c: (natural_key(c["area"]), natural_key(c["name"]))),
            "active": [{"key": k, "since": v["since"], "acked_by": v["acked_by"], "count": v["count"]} for k, v in self.active.items()],
            "log": self.sent_log[:10],
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
        try:
            config = await engine.async_set(body)
        except LockedChange as err:
            # The rules kept as they are; Hauflo says where the owner opens the house (security review 28.10).
            return self.json({"code": LOCKED_CODE, "reason": str(err)}, 423)
        return self.json({"config": config})


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

    def __init__(self, engine_of) -> None:
        self.engine_of = engine_of

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
        if engine is not None and isinstance(body, dict) and body.get("ack_all") is True:
            keys = [k for k, inc in engine.active.items() if not inc.get("acked_by")] + [k for k in engine.unacked_rtn if k not in engine.active]
            done = [k for k in keys if await engine.async_ack(k, who)]
            return self.json({"ok": True, "acked": len(done)})
        # «Принять все неисправности» (family review round 3): exactly the alarms the card listed — the equipment faults; a
        # leak, smoke or gas shown beside them keeps its own «Принято».
        if engine is not None and isinstance(body, dict) and "ack_keys" in body:
            keys = body["ack_keys"]
            if not isinstance(keys, list) or len(keys) > 200 or not all(isinstance(k, str) and "|" in k and len(k) <= 300 for k in keys):
                return self.json_message("ack_keys: a list of alarm keys", 400)
            done = [k for k in dict.fromkeys(keys) if await engine.async_ack(k, who)]
            return self.json({"ok": True, "acked": len(done)})
        key = body.get("ack") if isinstance(body, dict) else None
        if engine is None or not isinstance(key, str) or "|" not in key or len(key) > 300:
            return self.json_message("ack: a key expected", 400)
        ok = await engine.async_ack(key, who)
        return self.json({"ok": ok}, 200 if ok else 404)
