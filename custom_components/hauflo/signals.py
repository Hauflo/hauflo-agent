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
from homeassistant.util import dt as dt_util

from .storage import HaufloStore
from .tuning import TUNING

_LOGGER = logging.getLogger(__name__)
CANON = TUNING["signals"]
_A = TUNING["signals_agent"]
FEATURE = "signals"
STORE_KEY = "hauflo.signals"
DATA = "hauflo_signals"
SEVERITIES = list(_A["severities"])
HASH = re.compile(_A["hash_format"])
STAMP_LEN = _A["stamp_len"]
# Not watched by class: the agent's own entities (its link to Hauflo) and the family's phones (as notify.status).
WATCH_SKIP = frozenset(_A["watch_skip_platforms"])
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
# Who reads the diagnosis in HA, as the integrator chose it (the cloud's model.diag.visible).
DIAG_VISIBLE = tuple(CANON["diag"]["visible"])


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


def base_of(eid: str, device_class: Any, name: str | None) -> tuple[str, str, dict[str, Any]]:
    """(kind, the canon's key, the base) of a signal before any choice."""
    dom = eid.split(".", 1)[0]
    dc = class_of(device_class)
    if dc:
        key = f"{dom}.{dc}"
        return ("class", key, CANON["classes"][key]) if key in CANON["classes"] else ("unlisted", "unlisted", CANON["unlisted"])
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


def resolve(eid: str, *, platform: str | None = None, device_class: Any = None, name: str | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """One signal's policy: entity → scopes → the canon's class → classless → unlisted (signals.ts resolvePolicy).
    `source` — the last layer that chose anything for it (entity / scope), else the canon's base kind."""
    kind, key, base = base_of(eid, device_class, name)
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
    """The state is an alarm by this condition (HA's «unavailable» is always one — the lost-link class)."""
    state = state_of(raw)
    if state == "unavailable":
        return True
    if not alarm or "none" in alarm:
        return False
    if "states" in alarm:
        return state in alarm["states"]
    n = js_number(state)
    if n is None:
        return False
    return ("below" in alarm and n < alarm["below"]) or ("above" in alarm and n > alarm["above"])


def affects_house(problem: bool, house: str, undetermined: bool) -> bool:
    """On «Всё хорошо»: a problem whose policy affects the house (not «не решено», not house «none»)."""
    return problem and not undetermined and house != "none"


def watch_by_class(eid: str, device_class: Any, platform: str | None = None) -> bool:
    """Watched by default by the canon (its watch list of «domain.device_class»), not the agent's own nor a phone's."""
    dc = class_of(device_class)
    return bool(dc) and platform not in WATCH_SKIP and f"{eid.split('.', 1)[0]}.{dc}" in CANON["watch"]


def evaluate(state: State | str | None, *, eid: str | None = None, platform: str | None = None, device_class: Any = None, name: str | None = None, area: str | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
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
    r = resolve(eid, platform=platform, device_class=device_class, name=name, policy=policy)
    problem = alarm_of(raw, r["alarm"])
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
        "from": r["from"],
        "affects": affects_house(problem, r["house"], r["undetermined"]),
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


def signals_in(v: Any) -> tuple[dict[str, Any], int] | None:
    """({scopes, entities, findings[, diag]}, rows dropped) — None: not the shape at all (the whole body refused)."""
    if not isinstance(v, dict) or set(v) - {"scopes", "entities", "findings", "diag", "v", "hash"}:
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
        p = policy_in({k: w for k, w in x.items() if k not in ("by", "at", "lifeSafety")}) if ok else None
        if not p:
            dropped += 1
            continue
        entities[eid] = p
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
    return out, dropped


class Signals:
    """The site's policy as Hauflo last sent it, with the hash Hauflo computed (the agent never recomputes it: the two
    languages serialize numbers differently — the cloud compares its own hash with the one kept here)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = HaufloStore(hass, STORE_KEY)
        self.policy: dict[str, Any] = {"scopes": [], "entities": {}, "findings": {}}
        self.hash: str | None = None
        self.at: str | None = None
        self.listeners: list[Callable[[], None]] = []

    async def async_load(self) -> None:
        data = await self.store.async_load()
        got = signals_in({k: v for k, v in data.items() if k in ("scopes", "entities", "findings", "diag")}) if isinstance(data, dict) else None
        if got is not None:
            self.policy = got[0]
            h = data.get("hash")
            self.hash = h if isinstance(h, str) and HASH.match(h) else None
            self.at = data.get("at") if isinstance(data.get("at"), str) else None

    def _data(self) -> dict[str, Any]:
        return {**self.policy, "hash": self.hash, "at": self.at}

    @callback
    def apply(self, policy: dict[str, Any], hash_: str) -> None:
        # Who reads the diagnosis stays as last chosen when a push doesn't say it (an older cloud) or says it wrong (dropped):
        # «всем» never goes silently (acceptance 10.11 L2); never pushed — the canon's default (diag_visible).
        if "diag" not in policy and "diag" in self.policy:
            policy = {**policy, "diag": self.policy["diag"]}
        self.policy, self.hash, self.at = policy, hash_, dt_util.utcnow().isoformat()
        self.store.async_delay_save(self._data, 0)
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:  # noqa: BLE001 — a listener's bug never refuses the policy
                _LOGGER.exception("Hauflo signals: a listener failed")

    @callback
    def listen(self, fn: Callable[[], None]) -> Callable[[], None]:
        self.listeners.append(fn)

        def off() -> None:
            if fn in self.listeners:
                self.listeners.remove(fn)

        return off

    def counts(self) -> dict[str, int]:
        return {"scopes": len(self.policy["scopes"]), "entities": len(self.policy["entities"]), "findings": len(self.policy["findings"])}

    def finding(self, key: str) -> dict[str, Any] | None:
        return self.policy["findings"].get(key)

    def watch_on(self) -> set[str]:
        return {e for e, p in self.policy["entities"].items() if p.get("watch") is True}

    def watch_off(self) -> set[str]:
        return {e for e, p in self.policy["entities"].items() if p.get("watch") is False}


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
    from homeassistant.helpers import entity_registry as er

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
