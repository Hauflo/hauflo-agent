"""One calculator of «is this signal a problem, and what does it mean for the house» (0.32 A4, ISA-18.2 rationalization).

The canon is tuning.json «signals» — the same object as the cloud's backend/src/agent/signalCanon.json (a test keeps
them equal); the site's policy is the cloud's `Project.model.signals` (scopes by integration / domain / class, a policy
per entity, the diagnostics findings' choices), pushed here by Hauflo (POST /api/hauflo/signals) and kept in
`.storage/hauflo.signals`. Resolution — exactly the cloud's signals.ts `resolvePolicy` / `alarmOf` / `affectsHouse`:

    entity → scopes (more specific later, a later equal one wins) → the canon's class → classless (by its name's words:
    loud / fault / «не решено») → unlisted.

A classless signal without marker words is «не решено» (undetermined): its problem is shown, it never moves «Всё хорошо».
An alarm condition's states are compared as strings by value (up to the canon's stateLen — a vendor's enum in any
language is a state too: notify.WORD never applies here). No policy from the cloud (an older Hauflo): the canon alone."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from typing import Any

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .storage import HaufloStore
from .tuning import TUNING

_LOGGER = logging.getLogger(__name__)
CANON = TUNING["signals"]
_A = TUNING["signals_agent"]
FEATURE = "signals"
# 0.34: the site's «не авария» silences the source in the alarm rules too (notify.muted) — the cloud says so in the editor.
MUTE_FEATURE = "signals_mute"
# п.12: the site's «Отложить до» (shelvedUntil) holds a signal off «Всё хорошо» here too, back at its end without a restart.
SHELVE_FEATURE = "signals_shelve"
# 0.34 п.10: the policy may carry «Сочетание состояний» (`combos`) — sent only to an agent saying this (an older one refuses
# the whole body for an unknown key).
COMBOS_FEATURE = "signals_combos"


def combo_entity(cid: str) -> str:
    """The sensor of a combination (combos.ComboActive; backend signalCombos.ts comboEntityId)."""
    return f"binary_sensor.{DOMAIN}_combo_{cid}"


def combo_entities(hass: HomeAssistant | None) -> set[str]:
    """The sensors of the site's combinations (its policy's `combos`)."""
    s = store(hass)
    return {combo_entity(cid) for cid in s.combos()} if s is not None else set()
STORE_KEY = "hauflo.signals"
DATA = "hauflo_signals"
SEVERITIES = list(_A["severities"])
HASH = re.compile(_A["hash_format"])
STAMP_LEN = _A["stamp_len"]
# Not watched by class: the agent's own entities (its link to Hauflo) and the family's phones (as notify.status).
WATCH_SKIP = frozenset(_A["watch_skip_platforms"])
# The cards' verdicts (`_signals` of GET /api/hauflo/alarms): at most this many entities, the site's own choices first.
CARDS_MAX = _A["cards_max"]
# The domains a signal can be of: the canon's classes' and the classless ones'.
SIGNAL_DOMAINS = tuple(sorted({k.split(".", 1)[0] for k in CANON["classes"]} | set(CANON["classless"]["domains"])))
# Watched by the canon without a class: a classless one whose name says a leak / smoke… or a fault.
CLASSLESS_WATCHED = ("classless.loud", "classless.fault")
HOLD_MAX = TUNING["notify"]["hold"]["max_s"]
LEVEL_ABS_MAX = TUNING["notify"]["level_abs_max"]
LOUD = re.compile(CANON["classless"]["loudWords"], re.I)
FAULT = re.compile(CANON["classless"]["faultWords"], re.I)
# The cloud's formats (signals.ts ENTITY_ID / PLATFORM / DOMAIN / DEVICE_CLASS, insightKey.ts).
ENTITY_ID = re.compile(_A["entity_id"])
PLATFORM = re.compile(_A["platform"])
DOMAIN_ID = re.compile(_A["domain"])
DEVICE_CLASS = re.compile(_A["device_class"])
FINDING_KEY = re.compile(_A["finding_key"])
ISO = re.compile(_A["iso"])
# A JS Number() of a trimmed state: decimal with an exponent, or 0x / 0o / 0b — what the cloud's alarmOf reads.
_JS_DEC = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_JS_RADIX = re.compile(r"^0([xX][0-9a-fA-F]+|[oO][0-7]+|[bB][01]+)$")
POLICY_KEYS = ("alarm", "holdS", "severity", "house", "watch", "shelvedUntil")
COMBO = _A["combos"]
COMBO_ID = re.compile(COMBO["id"])
COMBO_PRESET = re.compile(COMBO["preset"])
COMBO_KEYS = ("name", "all", "holdS", "preset")
COMBO_IDLE = frozenset(COMBO["idle_states"])
# Who reads the diagnosis in HA, as the integrator chose it (the cloud's model.diag.visible).
DIAG_VISIBLE = tuple(CANON["diag"]["visible"])
# «Авария когда» chosen on a card's row in HA (Р9/Р19): the canon's meanings (on / off / none / reading) and «default» —
# the site's own choice for the signal dropped. At most LOCAL_MAX such choices kept until Hauflo holds them.
MEANINGS = CANON["meanings"]
MEANING_DEFAULT = "default"
LOCAL_MAX = _A["local_max"]
# At most this many integration domains with the watchdog switched off (Р58) — the cloud caps the same.
WATCHDOG_OFF_MAX = TUNING["watchdog"]["off_max"]
# The keys of a policy a meaning decides (the rest of the site's choice for the signal stays).
MEANING_KEYS = ("alarm", "watch")


# --- the canon's resolution (signals.ts) -------------------------------------------------------------------------------


def class_of(v: Any) -> str:
    """HA's device class as compared: trimmed, lower case; anything else — none."""
    return v.strip().lower() if isinstance(v, str) else ""


def state_of(v: Any) -> str:
    """A state as compared: a string as is, a finite number as its JS text, anything else ""."""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return str(int(v)) if float(v).is_integer() else repr(float(v))
    return ""


def js_number(text: str) -> float | None:
    """`Number(text)` as the cloud reads a state (blank → NaN): None when it is no finite number."""
    t = text.strip()
    if not t:
        return None
    if _JS_DEC.match(t):
        v = float(t)
    elif _JS_RADIX.match(t):
        v = float(int(t, 0))
    else:
        return None
    return v if math.isfinite(v) else None


def base_of(eid: str, device_class: Any, name: str | None, device_kind: str | None = None) -> tuple[str, str, dict[str, Any]]:
    """(kind, the canon's key, the base) of a signal before any choice. `device_kind` — the canon's onDevice key of its
    device (a lock's battery is no «info», Р56 L2; device_kind_of); a domain's own key (a lock) needs no class."""
    dom = eid.split(".", 1)[0]
    dc = class_of(device_class)
    own = f"{dom}.{dc}" if dc else dom
    on_dev = (CANON.get("onDevice") or {}).get(device_kind or "")
    if on_dev and own in on_dev:
        return "class", f"{device_kind}:{own}", on_dev[own]
    if dc and own in CANON["classes"]:
        return "class", own, CANON["classes"][own]
    if dom in CANON["classes"]:
        return "class", dom, CANON["classes"][dom]
    if dc:
        return "unlisted", "unlisted", CANON["unlisted"]
    cl = CANON["classless"]
    if dom not in cl["domains"]:
        return "unlisted", "unlisted", CANON["unlisted"]
    text = name or ""
    if LOUD.search(text):
        return "classless.loud", "classless.loud", {"alarm": cl["alarm"], **cl["withLoudWords"]}
    if FAULT.search(text):
        return "classless.fault", "classless.fault", {"alarm": cl["alarm"], **cl["withWords"]}
    return "undetermined", "undetermined", {"alarm": cl["alarm"], **CANON["undetermined"]}


def _specificity(m: dict[str, Any]) -> int:
    return (4 if m.get("platform") else 0) + (2 if m.get("domain") else 0) + (1 if "deviceClass" in m else 0)


def scope_matches(m: dict[str, Any], eid: str, platform: str | None, device_class: Any) -> bool:
    if "platform" in m and m["platform"] != platform:
        return False
    if "domain" in m and m["domain"] != eid.split(".", 1)[0]:
        return False
    return not ("deviceClass" in m and m["deviceClass"] != class_of(device_class))


def resolve(eid: str, *, platform: str | None = None, device_class: Any = None, name: str | None = None, policy: dict[str, Any] | None = None, device_kind: str | None = None) -> dict[str, Any]:
    """One signal's policy: entity → scopes → the canon's class → classless → unlisted (signals.ts resolvePolicy).
    `source` — the last layer that chose anything for it (entity / scope), else the canon's base kind."""
    kind, key, base = base_of(eid, device_class, name, device_kind)
    r: dict[str, Any] = {
        "base": kind,
        "baseKey": key,
        "alarm": base.get("alarm"),
        "house": base["house"],
        "severity": base["severity"],
        "defaultSeverity": base["severity"],
        "holdS": None,
        "watch": True,
        "shelvedUntil": None,
        "lifeSafety": key in CANON["lifeSafety"],
        "undetermined": kind == "undetermined",
        "from": {"alarm": "canon", "severity": "canon", "house": "canon"},
        "source": kind,
    }

    def layer(p: dict[str, Any], frm: str) -> None:
        for k in ("alarm", "severity", "house"):
            if p.get(k):
                r[k] = p[k]
                r["from"][k] = frm
        if p.get("holdS") is not None:
            r["holdS"] = p["holdS"]
        if p.get("watch") is not None:
            r["watch"] = p["watch"]
        if p.get("shelvedUntil"):
            r["shelvedUntil"] = p["shelvedUntil"]
        if p.get("alarm") or p.get("severity") or p.get("house"):
            r["undetermined"] = False
        if any(p.get(k) is not None for k in POLICY_KEYS):
            r["source"] = frm

    s = policy or {}
    scopes = [(i, sc) for i, sc in enumerate(s.get("scopes") or []) if scope_matches(sc["match"], eid, platform, device_class)]
    for _i, sc in sorted(scopes, key=lambda x: (_specificity(x[1]["match"]), x[0])):
        layer(sc["set"], "scope")
    r["defaultSeverity"] = r["severity"]
    if (own := (s.get("entities") or {}).get(eid)) is not None:
        layer(own, "entity")
    return r


def alarm_of(raw: Any, alarm: dict[str, Any] | None) -> bool:
    """The state is an alarm by this condition (HA's «unavailable» is one — the lost-link class — unless the site said «не
    авария» for it: 0.34, one choice excludes the entity everywhere; the canon itself never says {none})."""
    if alarm and "none" in alarm:
        return False
    state = state_of(raw)
    if state == "unavailable":
        return True
    if not alarm:
        return False
    if "states" in alarm:
        return state in alarm["states"]
    n = js_number(state)
    if n is None:
        return False
    return ("below" in alarm and n < alarm["below"]) or ("above" in alarm and n > alarm["above"])


def cond_holds(raw: Any, alarm: dict[str, Any]) -> bool:
    """One condition of a «Сочетание» (0.34 п.10) by the same reading as a signal's alarm (`alarm_of`) — but a source that
    is unavailable / unknown holds nothing: its lost link is its own signal, never half of a combination."""
    raw = raw.state if isinstance(raw, State) else raw
    return state_of(raw) not in COMBO_IDLE and alarm_of(raw, alarm)


def combo_holds(states: list[Any], combo: dict[str, Any]) -> bool:
    """Every condition of the combination holds now (`states` — the sources' states, in the order of combo["all"])."""
    return len(states) == len(combo["all"]) and all(cond_holds(st, c["alarm"]) for st, c in zip(states, combo["all"], strict=True))


def combo_in(v: Any) -> dict[str, Any] | None:
    """One «Сочетание» as the wire carries it: {name, all: [{entityId, alarm}], holdS[, preset]} (unknown keys refused).
    Only a signal: it never switches anything — the service only warns (owner rule)."""
    if not isinstance(v, dict) or set(v) - set(COMBO_KEYS) or not {"name", "all", "holdS"} <= set(v):
        return None
    name, conds, hold = v["name"], v["all"], v["holdS"]
    if not isinstance(name, str) or not name.strip() or len(name) > COMBO["name_max"]:
        return None
    if isinstance(hold, bool) or not isinstance(hold, int) or not 0 <= hold <= COMBO["hold_max_s"]:
        return None
    if not isinstance(conds, list) or not COMBO["conds_min"] <= len(conds) <= COMBO["conds_max"]:
        return None
    out = []
    for c in conds:
        if not isinstance(c, dict) or set(c) != {"entityId", "alarm"} or not isinstance(c["entityId"], str) or not ENTITY_ID.match(c["entityId"]):
            return None
        p = policy_in({"alarm": c["alarm"]})
        if not p or "none" in p["alarm"]:
            return None
        out.append({"entityId": c["entityId"], "alarm": p["alarm"]})
    if len({c["entityId"] for c in out}) != len(out):
        return None
    combo: dict[str, Any] = {"name": name.strip(), "all": out, "holdS": hold}
    if "preset" in v:
        if not isinstance(v["preset"], str) or not COMBO_PRESET.match(v["preset"]):
            return None
        combo["preset"] = v["preset"]
    return combo


def combos_in(v: Any) -> tuple[dict[str, dict[str, Any]], int] | None:
    """{id: combo} — None: not a map or over the limit (the whole push refused); a bad combo is dropped alone."""
    if not isinstance(v, dict) or len(v) > COMBO["max"]:
        return None
    out, dropped = {}, 0
    for cid, x in v.items():
        c = combo_in(x) if isinstance(cid, str) and COMBO_ID.match(cid) else None
        if c is None:
            dropped += 1
            continue
        out[cid] = c
    return out, dropped


def affects_house(problem: bool, house: str, undetermined: bool) -> bool:
    """On «Всё хорошо»: a problem whose policy affects the house (not «не решено», not house «none»)."""
    return problem and not undetermined and house != "none"


def shelved_now(until: Any, now: Any = None) -> bool:
    """The site's «Отложить до» (policy shelvedUntil, п.12) still holds: a valid time in the future. Past or not a time —
    the signal counts again (no restart: Agent.house_now's timer fires at the nearest one)."""
    t = _when(until)
    return t is not None and t > (now or dt_util.utcnow())


def next_unshelve(pol: dict[str, Any] | None, now: Any = None) -> Any:
    """The nearest future «Отложить до» of the site's policy (entities and rules) — None: nothing is shelved."""
    now = now or dt_util.utcnow()
    times = [_when(p.get("shelvedUntil")) for p in [*((pol or {}).get("entities") or {}).values(), *(x.get("set") or {} for x in (pol or {}).get("scopes") or [])]]
    ahead = [t for t in times if t is not None and t > now]
    return min(ahead) if ahead else None


def watch_by_class(eid: str, device_class: Any, platform: str | None = None) -> bool:
    """Watched by default by the canon (its watch list of «domain.device_class»), not the agent's own nor a phone's."""
    dc = class_of(device_class)
    dom = eid.split(".", 1)[0]
    return platform not in WATCH_SKIP and ((bool(dc) and f"{dom}.{dc}" in CANON["watch"]) or dom in CANON["watch"])


def device_kinds(hass: HomeAssistant) -> dict[str, str]:
    """{device id: the canon's onDevice key} — a device holding an entity of that key's domain (a lock's device), the
    first key of the canon's order. One registry pass per call; the callers ask it once per round."""
    kinds = list(CANON.get("onDevice") or {})
    if not kinds:
        return {}
    out: dict[str, str] = {}
    for e in er.async_get(hass).entities.values():
        dom = e.entity_id.split(".", 1)[0]
        if e.device_id and dom in kinds and (e.device_id not in out or kinds.index(dom) < kinds.index(out[e.device_id])):
            out[e.device_id] = dom
    return out


def device_kind_of(hass: HomeAssistant, eid: str, kinds: dict[str, str] | None = None) -> str | None:
    """The onDevice key of an entity's device (None: none / no device)."""
    e = er.async_get(hass).async_get(eid)
    if e is None or not e.device_id:
        return None
    return (kinds if kinds is not None else device_kinds(hass)).get(e.device_id)


def evaluate(state: State | str | None, *, eid: str | None = None, platform: str | None = None, device_class: Any = None, name: str | None = None, area: str | None = None, policy: dict[str, Any] | None = None, device_kind: str | None = None) -> dict[str, Any]:
    """{problem, house, severity, undetermined, watch, source, …} of one signal now. `state` — HA's State (its entity id,
    class and name are read from it unless given) or the raw state with `eid`. `area` is accepted for the callers' one
    shape; the canon doesn't resolve by it (a room's choice is the cloud's bulk edit into `entities`)."""
    if isinstance(state, State):
        eid = eid or state.entity_id
        device_class = device_class if device_class is not None else state.attributes.get("device_class")
        name = name if name is not None else state.name
        raw: Any = state.state
    else:
        raw = state
    if not eid:
        raise ValueError("evaluate: an entity id")
    r = resolve(eid, platform=platform, device_class=device_class, name=name, policy=policy, device_kind=device_kind)
    problem = alarm_of(raw, r["alarm"])
    shelved = shelved_now(r["shelvedUntil"])
    return {
        "problem": problem,
        "house": r["house"],
        "severity": r["severity"],
        "defaultSeverity": r["defaultSeverity"],
        "undetermined": r["undetermined"],
        "watch": r["watch"],
        "source": r["source"],
        "base": r["base"],
        "baseKey": r["baseKey"],
        "lifeSafety": r["lifeSafety"],
        "alarm": r["alarm"],
        "holdS": r["holdS"],
        "shelvedUntil": r["shelvedUntil"],
        # «Отложено до» now: the problem stays said, it doesn't move «Всё хорошо» until then.
        "shelved": shelved,
        "from": r["from"],
        "affects": affects_house(problem, r["house"], r["undetermined"]) and not shelved,
    }


# --- the stored policy (signals.ts signalsOf, defensively: a bad row is dropped, never thrown) -------------------------


def _level(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) <= LEVEL_ABS_MAX


def _iso(v: Any) -> bool:
    return isinstance(v, str) and ISO.match(v) is not None and dt_util.parse_datetime(v) is not None


def policy_in(v: Any) -> dict[str, Any] | None:
    """A policy as the wire carries it (unknown keys refused — a typo never passes as «nothing set»)."""
    if not isinstance(v, dict) or set(v) - set(POLICY_KEYS):
        return None
    p: dict[str, Any] = {}
    if "alarm" in v:
        a = v["alarm"]
        if not isinstance(a, dict) or not a:
            return None
        if set(a) == {"none"} and a["none"] is True:
            p["alarm"] = {"none": True}
        elif set(a) == {"states"} and isinstance(a["states"], list):
            st = a["states"]
            if not st or len(st) > CANON["statesMax"] or not all(isinstance(x, str) and 0 < len(x) <= CANON["stateLen"] for x in st) or len(set(st)) != len(st):
                return None
            p["alarm"] = {"states": list(st)}
        elif set(a) <= {"below", "above", "step"} and ("below" in a or "above" in a):
            if any(k in a and not _level(a[k]) for k in ("below", "above")) or ("step" in a and not (_level(a["step"]) and a["step"] >= 0)):
                return None
            if "below" in a and "above" in a and a["below"] >= a["above"]:
                return None
            p["alarm"] = {k: a[k] for k in ("below", "above", "step") if k in a}
        else:
            return None
    if "holdS" in v:
        h = v["holdS"]
        if isinstance(h, bool) or not isinstance(h, int) or not 0 <= h <= HOLD_MAX:
            return None
        p["holdS"] = h
    if "severity" in v:
        if v["severity"] not in SEVERITIES:
            return None
        p["severity"] = v["severity"]
    if "house" in v:
        if v["house"] not in CANON["houses"]:
            return None
        p["house"] = v["house"]
    if "watch" in v:
        if not isinstance(v["watch"], bool):
            return None
        p["watch"] = v["watch"]
    if "shelvedUntil" in v:
        if not _iso(v["shelvedUntil"]):
            return None
        p["shelvedUntil"] = v["shelvedUntil"]
    return p


def meaning_policy(meaning: str) -> dict[str, Any]:
    """The policy one of the canon's meanings stands for: {alarm[, watch: False]}; «default» — {} (nothing of its own)."""
    if meaning == MEANING_DEFAULT:
        return {}
    m = MEANINGS[meaning]
    p: dict[str, Any] = {"alarm": {"states": list(m["states"])} if "states" in m else {"none": True}}
    if m.get("watch") == "off":
        p["watch"] = False
    return p


def meaning_of(p: dict[str, Any] | None) -> str | None:
    """Which canon meaning a signal's own policy says (its alarm condition and watch exactly), None — none of them."""
    if not p or not p.get("alarm"):
        return None
    for k in MEANINGS:
        want = meaning_policy(k)
        if p["alarm"] == want["alarm"] and (p.get("watch") is False) == ("watch" in want):
            return k
    return None


def _when(v: Any) -> Any:
    """An ISO time as compared (a cloud's «…Z» and an agent's «+00:00» alike); None — not one."""
    return dt_util.parse_datetime(v) if isinstance(v, str) else None


def newer(a: Any, b: Any) -> bool:
    """`a` is a later time than `b` (no `b` — any `a` is)."""
    ta, tb = _when(a), _when(b)
    return ta is not None and (tb is None or ta > tb)


def match_in(v: Any) -> dict[str, str] | None:
    if not isinstance(v, dict) or not v or set(v) - {"platform", "domain", "deviceClass"}:
        return None
    m: dict[str, str] = {}
    for k, rx in (("platform", PLATFORM), ("domain", DOMAIN_ID)):
        if k in v:
            if not isinstance(v[k], str) or not rx.match(v[k]):
                return None
            m[k] = v[k]
    if "deviceClass" in v:
        if not isinstance(v["deviceClass"], str) or not DEVICE_CLASS.match(class_of(v["deviceClass"])):
            return None
        m["deviceClass"] = class_of(v["deviceClass"])
    return m


def finding_in(v: Any) -> dict[str, Any] | None:
    if not isinstance(v, dict):
        return None
    f: dict[str, Any] = {k: v[k] for k in ("by", "at") if isinstance(v.get(k), str) and len(v[k]) <= STAMP_LEN}
    if v.get("normal") is True:
        f["normal"] = True
    if _level(v.get("factor")) and v["factor"] > 0:
        f["factor"] = v["factor"]
    if _iso(v.get("resetAt")):
        f["resetAt"] = v["resetAt"]
    return f if f.get("normal") or "factor" in f or "resetAt" in f else None


def diag_in(v: Any) -> dict[str, str] | None:
    """The integrator's «Диагностика»: {visible: all | admins} — who in HA reads it (the view, GET /api/hauflo/insights)."""
    return {"visible": v["visible"]} if isinstance(v, dict) and set(v) == {"visible"} and v["visible"] in DIAG_VISIBLE else None


def watchdog_in(v: Any) -> dict[str, list[str]] | None:
    """The integrator's watchdog switches (Р58): {off: [integration domains]} — the watchdog leaves these alone."""
    if not isinstance(v, dict) or set(v) != {"off"} or not isinstance(v["off"], list) or len(v["off"]) > WATCHDOG_OFF_MAX:
        return None
    if not all(isinstance(d, str) and PLATFORM.match(d) for d in v["off"]):
        return None
    return {"off": sorted(set(v["off"]))}


def observe_in(v: Any) -> dict[str, bool] | None:
    """The integrator's «Фоновые наблюдения» (0.34 п.9): {on: false} — the diagnosis engine (insight.py) stands still."""
    return {"on": v["on"]} if isinstance(v, dict) and set(v) == {"on"} and isinstance(v["on"], bool) else None


def signals_in(v: Any) -> tuple[dict[str, Any], int] | None:
    """({scopes, entities, findings[, diag][, watchdog][, observe]}, rows dropped) — None: not the shape at all (the
    whole body refused)."""
    if not isinstance(v, dict) or set(v) - {"scopes", "entities", "findings", "diag", "watchdog", "observe", "combos", "v", "hash"}:
        return None
    scopes_raw, ents_raw, finds_raw = v.get("scopes") or [], v.get("entities") or {}, v.get("findings") or {}
    if not isinstance(scopes_raw, list) or not isinstance(ents_raw, dict) or not isinstance(finds_raw, dict):
        return None
    if len(scopes_raw) > CANON["scopesMax"] or len(ents_raw) > CANON["entitiesMax"] or len(finds_raw) > CANON["findingsMax"]:
        return None
    dropped = 0
    scopes = []
    for x in scopes_raw:
        m = match_in(x.get("match")) if isinstance(x, dict) else None
        p = policy_in(x.get("set")) if isinstance(x, dict) else None
        if m is None or not p:
            dropped += 1
            continue
        scopes.append({"match": m, "set": p})
    entities = {}
    for eid, x in ents_raw.items():
        ok = isinstance(x, dict) and isinstance(eid, str) and ENTITY_ID.match(eid)
        p = policy_in({k: w for k, w in x.items() if k not in ("by", "at", "lifeSafety", "ha")}) if ok else None
        if not p:
            dropped += 1
            continue
        # Its time (the newer of the site's and HA's own choice wins, Р9/Р19) — never a part of the policy.
        entities[eid] = {**p, **({"at": x["at"]} if _iso(x.get("at")) else {})}
    findings = {}
    for k, x in finds_raw.items():
        f = finding_in(x) if isinstance(k, str) and FINDING_KEY.match(k) else None
        if f is None:
            dropped += 1
            continue
        findings[k] = f
    out: dict[str, Any] = {"scopes": scopes, "entities": entities, "findings": findings}
    if v.get("diag") is not None:
        if (d := diag_in(v["diag"])) is None:
            dropped += 1
        else:
            out["diag"] = d
    # Absent = every integration watched (the cloud sends it only with something switched off — an older cloud's hash
    # stays the same): never kept from an earlier push.
    if v.get("watchdog") is not None:
        if (w := watchdog_in(v["watchdog"])) is None:
            dropped += 1
        else:
            out["watchdog"] = w
    # The same for the background observations (insight.OBSERVE_FEATURE): absent = on, never kept from an earlier push.
    if v.get("observe") is not None:
        if (o := observe_in(v["observe"])) is None:
            dropped += 1
        else:
            out["observe"] = o
    # «Сочетание состояний» (0.34 п.10, COMBOS_FEATURE): absent = none — never kept from an earlier push.
    if v.get("combos") is not None:
        if (cb := combos_in(v["combos"])) is None:
            dropped += 1
        else:
            out["combos"] = cb[0]
            dropped += cb[1]
    return out, dropped


class Signals:
    """The site's policy as Hauflo last sent it, with the hash Hauflo computed (the agent never recomputes it: the two
    languages serialize numbers differently — the cloud compares its own hash with the one kept here)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = HaufloStore(hass, STORE_KEY)
        # The site's policy as Hauflo sent it; `policy` — with HA's own newer choices over it (what every reader resolves by).
        self.cloud: dict[str, Any] = {"scopes": [], "entities": {}, "findings": {}}
        self.policy: dict[str, Any] = self.cloud
        self.hash: str | None = None
        self.at: str | None = None
        self.listeners: list[Callable[[], None]] = []
        # «Авария когда» chosen in HA (Р9/Р19): entity → {meaning, by, at} — over the site's own until Hauflo holds a newer.
        self.local: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        data = await self.store.async_load()
        got = signals_in({k: v for k, v in data.items() if k in ("scopes", "entities", "findings", "diag", "watchdog", "observe", "combos")}) if isinstance(data, dict) else None
        if got is not None:
            self.cloud = got[0]
            h = data.get("hash")
            self.hash = h if isinstance(h, str) and HASH.match(h) else None
            self.at = data.get("at") if isinstance(data.get("at"), str) else None
        if isinstance(data, dict) and isinstance(data.get("local"), dict):
            self.local = {e: c for e, c in data["local"].items() if isinstance(e, str) and ENTITY_ID.match(e) and local_in(c) is not None}
            self._trim()
        self._merge()

    def _merge(self) -> None:
        own = dict(self.cloud.get("entities") or {})
        for eid, c in self.local.items():
            was = own.get(eid) or {}
            if not newer(c["at"], was.get("at")):
                continue
            rest = {k: v for k, v in was.items() if k not in MEANING_KEYS and k != "at"}
            p = {**rest, **meaning_policy(c["meaning"])}
            if any(k in p for k in POLICY_KEYS):
                own[eid] = {**p, "at": c["at"]}
            else:
                own.pop(eid, None)
        self.policy = {**self.cloud, "entities": own}

    def _trim(self) -> None:
        if len(self.local) > LOCAL_MAX:
            self.local = dict(sorted(self.local.items(), key=lambda kv: _when(kv[1]["at"]) or dt_util.utc_from_timestamp(0), reverse=True)[:LOCAL_MAX])

    def _data(self) -> dict[str, Any]:
        return {**self.cloud, "hash": self.hash, "at": self.at, "local": self.local}

    def _told(self) -> None:
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:  # noqa: BLE001 — a listener's bug never refuses the policy
                _LOGGER.exception("Hauflo signals: a listener failed")

    @callback
    def choose(self, eid: str, meaning: str, by: str) -> None:
        """«Авария когда» chosen on a card's row in HA by an admin: applied at once (the cards, the push engine, «Всё
        хорошо»), kept, and carried to Hauflo by the status (`choices`) — the newer of the two choices wins."""
        was = self.local.pop(eid, None)
        # «По умолчанию» for a signal the site holds nothing of its own for (and no card choice on its way there): nothing
        # to choose, nothing to carry (0.33 L4).
        if meaning == MEANING_DEFAULT and was is None and eid not in (self.cloud.get("entities") or {}):
            self._merge()
            self.store.async_delay_save(self._data, 0)
            self._told()
            return
        self.local[eid] = {"meaning": meaning, "by": str(by or "")[:STAMP_LEN], "at": dt_util.utcnow().isoformat()}
        self._trim()
        self._merge()
        self.store.async_delay_save(self._data, 0)
        self._told()

    def choices(self) -> dict[str, dict[str, Any]]:
        """HA's choices the site's policy doesn't hold yet (newer than its own for the signal), newest first — Hauflo folds
        them in with the same `at`; its push back then equals them and they stop going."""
        own = self.cloud.get("entities") or {}
        out: dict[str, dict[str, Any]] = {}
        for eid, c in sorted(self.local.items(), key=lambda kv: _when(kv[1]["at"]) or dt_util.utc_from_timestamp(0), reverse=True):
            if newer(c["at"], (own.get(eid) or {}).get("at")):
                out[eid] = dict(c)
        return out

    def own_of(self, eid: str) -> dict[str, Any]:
        """Who chose the signal's own policy now: {meaning, ha: chosen in HA (not held by the site yet), by}."""
        p = (self.policy.get("entities") or {}).get(eid)
        c = self.local.get(eid)
        # «По умолчанию» chosen on a card is no choice of its own (0.33 L4): the row reads its real default, not «на карточке».
        ha = c is not None and c["meaning"] != MEANING_DEFAULT and newer(c["at"], ((self.cloud.get("entities") or {}).get(eid) or {}).get("at"))
        return {"meaning": meaning_of(p), "ha": ha, **({"by": c["by"]} if ha and c else {})}

    @callback
    def apply(self, policy: dict[str, Any], hash_: str) -> None:
        # Who reads the diagnosis stays as last chosen when a push doesn't say it (an older cloud) or says it wrong (dropped):
        # «всем» never goes silently (acceptance 10.11 L2); never pushed — the canon's default (diag_visible).
        if "diag" not in policy and "diag" in self.cloud:
            policy = {**policy, "diag": self.cloud["diag"]}
        self.cloud, self.hash, self.at = policy, hash_, dt_util.utcnow().isoformat()
        # HA's own choices the site now holds (its time equal or newer) are done: forgotten.
        own = policy.get("entities") or {}
        # A «По умолчанию» the site holds no own policy for is done too (Hauflo dropped the signal's own: nothing to hold
        # it by — kept, it went in every status and the row said «на карточке» for good, 0.33 L4).
        self.local = {e: c for e, c in self.local.items() if newer(c["at"], (own.get(e) or {}).get("at")) and not (c["meaning"] == MEANING_DEFAULT and e not in own)}
        self._merge()
        self.store.async_delay_save(self._data, 0)
        self._told()

    @callback
    def listen(self, fn: Callable[[], None]) -> Callable[[], None]:
        self.listeners.append(fn)

        def off() -> None:
            if fn in self.listeners:
                self.listeners.remove(fn)

        return off

    def counts(self) -> dict[str, int]:
        return {"scopes": len(self.cloud["scopes"]), "entities": len(self.cloud["entities"]), "findings": len(self.cloud["findings"])}

    def finding(self, key: str) -> dict[str, Any] | None:
        return self.policy["findings"].get(key)

    def watch_on(self) -> set[str]:
        return {e for e, p in self.policy["entities"].items() if p.get("watch") is True}

    def combos(self) -> dict[str, dict[str, Any]]:
        return self.cloud.get("combos") or {}

    def watch_off(self) -> set[str]:
        return {e for e, p in self.policy["entities"].items() if p.get("watch") is False}


def local_in(v: Any) -> dict[str, Any] | None:
    """One HA choice as kept: {meaning: a canon one | default, by, at (ISO)}."""
    if not isinstance(v, dict) or (v.get("meaning") not in MEANINGS and v.get("meaning") != MEANING_DEFAULT) or not _iso(v.get("at")):
        return None
    return {"meaning": v["meaning"], "by": str(v.get("by") or "")[:STAMP_LEN], "at": v["at"]}


def watchdog_off(hass: HomeAssistant | None) -> set[str]:
    """The integration domains the integrator switched the watchdog off for (Р58)."""
    s = store(hass)
    return set(((s.cloud.get("watchdog") or {}).get("off") or ())) if s is not None else set()


def observing(hass: HomeAssistant | None) -> bool:
    """«Фоновые наблюдения» (0.34 п.9): on unless the integrator switched them off (absent / no policy yet — on)."""
    s = store(hass)
    return not (s is not None and (s.cloud.get("observe") or {}).get("on") is False)


def store(hass: HomeAssistant | None) -> Signals | None:
    return hass.data.get(DATA) if hass is not None else None


async def async_setup(hass: HomeAssistant) -> Signals:
    if (s := store(hass)) is not None:
        return s
    s = Signals(hass)
    await s.async_load()
    hass.data[DATA] = s
    return s


def policy(hass: HomeAssistant | None) -> dict[str, Any] | None:
    s = store(hass)
    return s.policy if s is not None else None


def current_hash(hass: HomeAssistant | None) -> str | None:
    s = store(hass)
    return s.hash if s is not None else None


def platform_of(hass: HomeAssistant, eid: str) -> str | None:
    ent = er.async_get(hass).async_get(eid)
    return ent.platform if ent is not None else None


def evaluate_in(hass: HomeAssistant, st: State, platform: str | None = None) -> dict[str, Any]:
    """`evaluate` with this house's policy and the entity's integration from HA's registry."""
    return evaluate(st, platform=platform if platform is not None else platform_of(hass, st.entity_id), policy=policy(hass))


class SignalsView(HomeAssistantView):
    """GET → {"hash": <kept> | null, "applied": counts, "at"}; POST (admin only, Hauflo through its tunnel)
    {"signals": {scopes, entities, findings}, "hash": "<16 hex>"} → {"hash", "applied": false when that hash is kept
    already (nothing written), "counts", "dropped"}."""

    url = "/api/hauflo/signals"
    name = "api:hauflo:signals"
    requires_auth = True

    async def get(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        s = store(request.app[KEY_HASS])
        if s is None:
            return self.json_message("not ready", 409)
        return self.json({"hash": s.hash, "applied": s.counts(), "at": s.at})

    async def post(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict) or set(body) != {"signals", "hash"}:
            return self.json_message("{signals, hash} expected", 400)
        if not isinstance(body["hash"], str) or not HASH.match(body["hash"]):
            return self.json_message("hash: 16 hex expected", 400)
        got = signals_in(body["signals"])
        if got is None:
            return self.json_message("signals: {scopes, entities, findings} within the canon's limits expected", 400)
        s = store(request.app[KEY_HASS])
        if s is None:
            return self.json_message("not ready", 409)
        # The same policy again (a status raced a write, a retry): nothing written, no listener told.
        if body["hash"] == s.hash:
            return self.json({"hash": s.hash, "applied": False, "counts": s.counts(), "dropped": got[1]})
        s.apply(got[0], body["hash"])
        return self.json({"hash": s.hash, "applied": True, "counts": s.counts(), "dropped": got[1]})


class SignalChooseView(HomeAssistantView):
    """«Авария когда» on a card's row (Р9/Р19), admins only: POST {entity, meaning: on | off | none | reading | default} →
    {entity, meaning, ha, problem, house, alarm} — applied at once, kept, carried to Hauflo by the status."""

    url = "/api/hauflo/signals/choose"
    name = "api:hauflo:signals:choose"
    requires_auth = True

    async def post(self, request):
        user = request["hass_user"]
        if not user.is_admin:
            return self.json_message("admin only", 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict) or set(body) != {"entity", "meaning"}:
            return self.json_message("{entity, meaning} expected", 400)
        eid, meaning = body["entity"], body["meaning"]
        if not isinstance(eid, str) or not ENTITY_ID.match(eid) or eid.split(".", 1)[0] not in SIGNAL_DOMAINS:
            return self.json_message("entity: a signal's entity id", 400)
        if not isinstance(meaning, str) or (meaning not in MEANINGS and meaning != MEANING_DEFAULT):
            return self.json_message(f"meaning: one of {', '.join([*MEANINGS, MEANING_DEFAULT])}", 400)
        hass = request.app[KEY_HASS]
        s = store(hass)
        if s is None:
            return self.json_message("not ready", 409)
        s.choose(eid, meaning, user.name or user.id)
        st = hass.states.get(eid)
        ev = evaluate_in(hass, st) if st is not None else None
        out: dict[str, Any] = {"entity": eid, **s.own_of(eid)}
        if ev is not None:
            out.update({"problem": ev["problem"], "house": ev["house"], "alarm": ev["alarm"]})
        return self.json(out)
