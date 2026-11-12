"""«Правила» / rules (design 29.10, docs/automations.md «Правила»): by a value and by a schedule.

«Когда [значение] [ниже|выше|вне|между] X с запасом Y дольше N мин · только если … → [действия] · когда вернётся в
норму: [обратное | ничего | своё]». Run here, in HA (works without Hauflo or the internet), level-based: every rule is
NORMAL or ACTIVE, worked out on every input change, every tick and at HA start — nothing has to «cross» first.
ACTIVE once the value is past X for N minutes; NORMAL once it is back past X ∓ Y for M minutes, or at once when «только
если» ends. Its state survives a restart (store `hauflo.rules`).

Hauflo HELPS, it does not protect (owner 29.10): the helpers make a rule gentle on the equipment and the bus; they never
replace the installation's own hard-wired protection (a calorifer's overheat thermostat, its fan run-on). The hints say
so (translations rules_hint_…).

Helpers (numbers: rules_canon.json, canon.py): per-target minimum on / off time by purpose, a climate written at most
once per climate_gap, at most rate_max switches of one target per rate_window (else the rule holds and says so), a
house-wide bus cap; an input unavailable / unknown / not a number / in another unit → the purpose's safe state (a heater
off; frost / leak rules say it) + a Repairs issue, never an action on an old value; a heater is not switched on (and is
switched off, with a warning) while its block's supply fan is off; a person / the bus / a central command changing a
target the rule holds → the rule leaves it for manual_hold or until NORMAL («вручную, вернётся в 15:40»); pause_holds
such in pause_window → the rule pauses with a warning. At save: no loops (a target is never an input of the chain),
one rule per target attribute of one priority (safety > comfort wins at run time).

Targets: Hauflo's own entities only (`owns`); a valve only closes; never a lock, the alarm, a gate / garage door, a
valve opening. Limits: canon «limits».

Who: the integrator (an HA admin — the tunnel's user is one) everything; the client (not an admin) sees the rules,
switches one the integrator let him, moves X inside the integrator's range, makes rules only with client actions
(light, blinds, scene, «notify me») on Hauflo's sensors; a restricted (child) profile only looks.
GET/POST /api/hauflo/rules (op save / delete / enable / tune / preview / derived_save / derived_delete).

By a schedule (wave 5, п.13, `when: "at"`): «в 07:00 / на закате ± N мин · по дням · только если … → [действия]». No
input, no «back»: a moment fires once (HA time / sun trigger, KNX switching clock, Loxone Schaltuhr); a pair is two
moments or two rules. A moment missed while HA was down is logged, not caught up (canon «schedule»). Actions: any entity
action of the canon table «actions_by_domain» (one table for the agent, the backend and the app) on Hauflo's own.
User texts: translations, section «exceptions», keys rules_… (RU + EN).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from functools import partial
import datetime as dt
from http import HTTPStatus
import logging
import re
import secrets
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.climate import HVACMode
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory, STATE_CLOSED, STATE_HOME, STATE_OFF, STATE_ON, STATE_OPEN, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Context, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, translation
from homeassistant.helpers.event import async_call_later, async_track_point_in_utc_time, async_track_state_change_event, async_track_state_report_event, async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util, slugify

from . import derived as dv, issues, quiet, window as win
from .canon import CANON
from .const import DOMAIN, async_entity_names, site_name, site_ru
from .derived import ROUND_DIGITS, SECONDS_PER_MINUTE, SETPOINT_GAP, Invalid, num
from .device import device_info
from .house import may_edit, refuse
from .notify import label
from .ownership import entity_room, room_name, service_entity
from .storage import HaufloStore

_LOGGER = logging.getLogger(__name__)
STORE_KEY = f"{DOMAIN}.rules"
BRIGHTNESS_MAX = 255  # HA's light brightness scale
ID_BYTES = 5  # a generated rule id: «r» + 10 hex
TODO = "_todo"  # the timer of the deferred writes (rule ids never start with «_»)
LANGS = ("ru", "en")
AT = "at"  # a schedule rule: moments → actions, no input
FEATURES = ("schedule", "actions_by_domain", "repeat")  # the answer's «features»: what this agent's API takes (0.34)
WHENS = ("below", "above", "outside", "between", "on", "off", AT)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
RANGES = ("below", "above", "outside", "between")
VALUE_DOMAINS = {"sensor", "input_number", "number", "climate", "weather"}
BINARY_DOMAINS = {"binary_sensor", "input_boolean", "switch", "fan", "light"}
OFF_STATES = (STATE_OFF, STATE_CLOSED)
_ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_ATTR_RE = re.compile(r"^[a-z_]+$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$")
_DATE = re.compile(r"^\d{4}-\d\d-\d\d$")
_USER_RE = re.compile(r"^[0-9a-f]{32}$")
# Every entity action is the canon's (rules_canon.json «actions_by_domain»: domain → do → service, value, its bounds'
# attributes, slot); the house's own two — «Зима / Лето» and a push — have no entity. do → the slot it holds.
HOUSE_ACTS = {"season": "season", "notify": ""}
NUMERIC = ("percent", "setpoint", "number", "seconds")
SEASON_MODES = ("heat", "cool")  # the house's «Зима / Лето» (season.py): a rule's «season» action sets it
SEASON_TARGET = f"{DOMAIN}.season"  # what a «season» action holds (no entity): one rule per house may drive it
SUGGEST_ACTION = "HAUFLO_RULE"  # a phone's button: «HAUFLO_RULE|<rule>|on|off|later»
LATER = "later"
# A rule ending without its input saying so (edited, deleted, switched off): its own actions are undone (no orphan heater
# left on) — but never a «season» one: the house's «Зима / Лето» is a mode the house is in, not something the rule holds
# on; undoing it would switch a heating house to cooling because the integrator edited a threshold.
UNDO_WHYS = ("changed", "deleted", "switched_off")
PRIORITY = {"comfort": 0, "safety": 1}
_GA_RE = re.compile(r"^\d{1,5}(/\d{1,4}){0,2}$")
KNX_WRITE = "GroupValueWrite"  # HA KNX telegram dict «telegramtype» of a write (a read / response never triggers)


def C(*path: str) -> Any:
    """A canon value (rules_canon.json)."""
    v: Any = CANON
    for p in path:
        v = v[p]
    return v


# Domains whose actions pick an option (climate mode / preset, fan preset, select…): HA's labels of their values are loaded.
OPTION_DOMAINS = {d for d, acts in C("actions_by_domain").items() if any(a.get("value") == "option" for a in acts.values())}


def purpose(name: str) -> dict[str, Any]:
    return C("purposes", name)


def spec_of(eid: Any, do: Any) -> dict[str, Any] | None:
    """The canon's row of an entity action (None: out of the table)."""
    return C("actions_by_domain").get(eid.split(".")[0], {}).get(do) if isinstance(eid, str) else None


def act_spec(a: dict[str, Any]) -> dict[str, Any] | None:
    return spec_of(a.get("entity_id"), a.get("do"))


def slot_of(a: dict[str, Any]) -> str:
    """What two rules of one priority may not both hold on a target (power, position, setpoint…)."""
    if a["do"] in HOUSE_ACTS:
        return HOUSE_ACTS[a["do"]]
    spec = act_spec(a)
    return spec["slot"] if spec else a["do"]


def restores(a: dict[str, Any]) -> bool:
    """Its reverse is the value before (a speed, a setpoint, an option); a press / a pulse restores nothing."""
    spec = act_spec(a)
    return bool(spec and spec.get("restore"))


def restore_key(a: dict[str, Any]) -> str:
    """One remembered value per target and action (a fan's speed and its preset are two)."""
    return f"{a['entity_id']}:{a['do']}"


def powering(a: dict[str, Any]) -> bool:
    """May switch its target on (the interlock's and the safe state's)."""
    spec = act_spec(a)
    return bool(spec and spec.get("power") in ("on", "value", "toggle"))


def all_dos() -> set[str]:
    return {do for dos in C("actions_by_domain").values() for do in dos} | set(HOUSE_ACTS)


def scheduled(rule: dict[str, Any]) -> bool:
    return rule.get("when") == AT


def client_locked(rule: dict[str, Any]) -> bool:
    """A protective rule (safety priority: frost, leak) or one that works a valve is never the client's to switch or tune,
    whatever the integrator ticked (docs/acceptance.md C6: lock / alarm / valve / gate never; the engine itself never
    targets a lock, the alarm or a gate)."""
    return rule.get("priority") == "safety" or any(str(a.get("entity_id", "")).startswith("valve.") for a in rule.get("actions") or [])


def client_can(rule: dict[str, Any]) -> dict[str, bool]:
    """What a client's card may offer on a rule the integrator left him: the switch, the threshold."""
    c, locked = rule["client"], client_locked(rule)
    return {"toggle": c.get("toggle", True) is not False and not locked, "tune": "lo" in c and not locked}


def _fmt(v: float | None, unit: str | None) -> str:
    if v is None:
        return "—"
    s = f"{v:.1f}".rstrip("0").rstrip(".").replace("-", "−")
    return f"{s} {unit}" if unit else s


# --- settings -----------------------------------------------------------------------------------------------------


class Env:
    """What a save is checked against."""

    def __init__(
        self,
        hass: HomeAssistant,
        owns: Callable[[str], bool],
        own_raw: Callable[[str], bool],
        client: bool = False,
    ) -> None:
        self.hass, self.owns, self.own_raw, self.client = hass, owns, own_raw, client

    def may_change(self, eid: str) -> bool:
        return may_change(self.hass, eid, self.owns, self.own_raw)


def may_change(hass: HomeAssistant, eid: str, owns: Callable[[str], bool], own_raw: Callable[[str], bool]) -> bool:
    """A rule's target — one test for the picker (targets), a save (clean_action) and the moment it runs (_write): a
    valve only Hauflo's own raw one (it only closes), anything else Hauflo's own and never a lock / the alarm / a gate,
    and never «Служебное» (a rule's own on / off switch, «Зима / Лето автоматически» — a rule could switch itself off)."""
    domain = eid.split(".")[0]
    return (own_raw(eid) if domain == "valve" else owns(eid)) and not service_entity(hass, eid)


def _entity(v: Any, what: str) -> str:
    if not isinstance(v, str) or not _ENTITY_RE.match(v):
        raise Invalid("bad_entity", "rules_bad_entity", what=what)
    return v


def _attribute(v: Any) -> str | None:
    if v is not None and not (isinstance(v, str) and _ATTR_RE.match(v)):
        raise Invalid("bad_input", "rules_bad_attribute")
    return v


def _minutes(v: Any) -> float:
    if v is None:
        return 0.0
    f = num(v)
    if f is None or not 0 <= f <= C("limits", "for_min"):
        raise Invalid("bad_time", "rules_bad_time", n=C("limits", "for_min"))
    return f


def _number(v: Any, what: str) -> float:
    f = num(v)
    if f is None or abs(f) > C("limits", "number_abs"):
        raise Invalid("bad_number", "rules_bad_number", what=what, n=C("limits", "number_abs"))
    return f


def _name(v: Any) -> str:
    if not isinstance(v, str) or not v.strip() or len(v) > C("limits", "name_len") or not v.isprintable():
        raise Invalid("bad_name", "rules_bad_name", n=C("limits", "name_len"))
    return v.strip()


def clean_input(raw: Any, env: Env) -> dict[str, Any]:
    """The value a rule watches: an entity (+ attribute), or «outdoor» resolved here to the street's sensor."""
    if raw == "outdoor" or (isinstance(raw, dict) and raw.get("outdoor") is True):
        found = dv.outdoor(env.hass, env.owns)
        if found is None:
            raise Invalid("no_outdoor", "rules_no_outdoor")
        raw = {"entity_id": found["entity_id"], "attribute": found["attribute"], "forecast": found["forecast"], "outdoor": True}
    if not isinstance(raw, dict):
        raise Invalid("bad_input", "rules_bad_input", entity="—")
    eid = _entity(raw.get("entity_id"), "input")
    attr = _attribute(raw.get("attribute"))
    domain = eid.split(".")[0]
    if domain not in VALUE_DOMAINS | BINARY_DOMAINS:
        raise Invalid("bad_input", "rules_bad_input", entity=eid)
    st = env.hass.states.get(eid)
    if st is None:
        raise Invalid("no_entity", "rules_no_entity", entity=eid)
    if env.client and not (env.owns(eid) or eid.startswith(f"sensor.{DOMAIN}_")):
        raise Invalid("not_allowed", "rules_client_input")
    attr = attr or dv.default_attribute(eid)
    if attr == SETPOINT_GAP and domain != "climate":
        raise Invalid("bad_input", "rules_bad_input", entity=eid)
    out = {"entity_id": eid, "attribute": attr, "forecast": bool(raw.get("forecast")) or domain == "weather", "unit": dv.unit_of(st, attr)}
    if raw.get("outdoor") is True:
        out["outdoor"] = True  # chosen as «улица»: a window rule follows the street's sensor if it changes
    return out


def clean_window(raw: Any, inp: dict[str, Any], env: Env) -> dict[str, Any]:
    """«среднее / мин / макс / сумма за час · день · неделю, N подряд»: checked against the canon and what the input's
    recorder data can say (window.py)."""
    w = C("window")
    if not isinstance(raw, dict) or raw.get("stat") not in w["stats"] or raw.get("period") not in w["periods"]:
        raise Invalid("bad_window", "rules_bad_window")
    lo, hi = w["n"][raw["period"]]
    ns = []
    for key in ("n", "n_back"):
        n = raw.get(key, raw.get("n"))
        if not isinstance(n, int) or isinstance(n, bool) or not lo <= n <= hi:
            raise Invalid("bad_window", "rules_bad_window_n", lo=int(lo), hi=int(hi))
        ns.append(n)
    src = win.source_of(env.hass, inp["entity_id"], inp.get("attribute"), raw["stat"])
    if src != win.STATISTICS and not win.history_span_ok(raw["period"], max(ns)):
        raise Invalid("bad_window", "rules_window_history", n=int(w["history_keep_days"]))
    return {"stat": raw["stat"], "period": raw["period"], "n": ns[0], "n_back": ns[1], "source": src}


def outputs(rule: dict[str, Any]) -> dict[str, bool]:
    """What a rule does when it changes: its HA entity always; a suggestion to the family; its actions by itself. A
    window rule suggests by default and acts only if allowed; a rule from before the outputs acts, as it always did."""
    base = {"suggest": True, "suggest_back": False, "auto": False} if rule.get("window") else {"suggest": False, "suggest_back": False, "auto": True}
    return {**base, **(rule.get("outputs") or {})}


def clean_outputs(raw: Any, window: bool) -> dict[str, bool]:
    if raw is not None and not isinstance(raw, dict):
        raise Invalid("bad_outputs", "rules_bad_outputs")
    out = outputs({"window": window, "outputs": {}})
    for k in out:
        if k in (raw or {}):
            if not isinstance(raw[k], bool):
                raise Invalid("bad_outputs", "rules_bad_outputs")
            out[k] = raw[k]
    return out


def clean_condition(raw: Any) -> dict[str, Any]:
    kind = raw.get("type") if isinstance(raw, dict) else None
    bad = Invalid("bad_condition", "rules_bad_condition", kind=str(kind)[: C("limits", "id_len")])
    if kind == "time":
        after, before = raw.get("after"), raw.get("before")
        days = raw.get("days") or list(DAYS)
        if not (isinstance(after, str) and _HHMM.match(after) and isinstance(before, str) and _HHMM.match(before)):
            raise bad
        if not isinstance(days, list) or not days or any(d not in DAYS for d in days):
            raise bad
        return {"type": "time", "after": after, "before": before, "days": [d for d in DAYS if d in days]}
    if kind == "season":
        if raw.get("mode") not in ("winter", "summer"):
            raise bad
        return {"type": "season", "mode": raw["mode"]}
    if kind == "presence":
        return {"type": "presence", "home": raw.get("home") is not False}
    if kind == "value":
        if raw.get("when") not in ("below", "above"):
            raise bad
        return {"type": "value", "entity_id": _entity(raw.get("entity_id"), "condition"), "attribute": _attribute(raw.get("attribute")), "when": raw["when"], "x": _number(raw.get("x"), "X")}
    if kind == "state":
        return {"type": "state", "entity_id": _entity(raw.get("entity_id"), "condition"), "on": raw.get("on") is not False}
    if kind == "heating":
        eid = _entity(raw.get("entity_id"), "condition")
        if not eid.startswith("climate."):
            raise bad
        return {"type": "heating", "entity_id": eid}
    raise bad


def _has_now(a: dict[str, Any]) -> bool:
    """An action with a state to compare: the season, or a canon action that reads one (not a press / scene / run / toggle)."""
    if "entity_id" not in a:
        return a["do"] == "season"
    spec = spec_of(a["entity_id"], a["do"])
    return bool(spec and spec.get("read"))


def supports(st: Any, spec: dict[str, Any]) -> bool:
    """The entity takes this canon action — as HA itself decides (the service's required_features: any one, each fully),
    an option only with its options listed, a helper's value only of the kind it holds (needs), a light's brightness only beyond «onoff». Not knowable (unavailable): yes —
    an unknown state still has its capabilities."""
    if st is None or st.state == STATE_UNAVAILABLE:
        return True
    a = st.attributes
    if (feats := spec.get("features")) is not None:
        have = a.get("supported_features")
        have = have if isinstance(have, int) and not isinstance(have, bool) else 0
        if not any(have & f == f for f in feats):
            return False
    if (opts := spec.get("options")) is not None and not (isinstance(a.get(opts), (list, tuple)) and a.get(opts)):
        return False
    if any(a.get(flag) is not True for flag in spec.get("needs") or ()):  # an input_datetime's has_date / has_time
        return False
    if (b := spec.get("beyond")) is not None:
        modes = a.get(b["attr"])
        if not isinstance(modes, (list, tuple, set)) or not set(map(str, modes)) - set(b["not"]):
            return False
    return True


def classes_of(hass: HomeAssistant, eid: str) -> set[str]:
    """An entity's device class as HA has it: the state's, the registry's (a person's choice), the integration's."""
    st, ent = hass.states.get(eid), er.async_get(hass).async_get(eid)
    return {c for c in ((st.attributes.get("device_class") if st else None), *((ent.device_class, ent.original_device_class) if ent else ())) if c}


def client_class_ok(hass: HomeAssistant, eid: str) -> bool:
    """A client's target of a domain the canon narrows by class (actions.client_classes: a switch only a socket)."""
    want = C("actions", "client_classes").get(eid.split(".")[0])
    return want is None or bool(classes_of(hass, eid) & set(want))


def clean_repeat(raw: Any, do: str, pulse_s: Any = None) -> dict[str, int] | None:
    """«Повторить N раз через M с» (canon actions.repeat): only an action that does something each time (a press, a
    pulse, a toggle, a scene, a script); a pulse's pause longer than the pulse itself."""
    if raw is None:
        return None
    r = C("actions", "repeat")
    (lo, hi), (s_lo, s_hi) = r["count"], r["every_s"]
    bad = Invalid("bad_action", "rules_bad_repeat", lo=lo, hi=hi, s_lo=s_lo, s_hi=s_hi)
    if do not in r["acts"] or not isinstance(raw, dict) or set(raw) - {"count", "every_s"}:
        raise bad
    n, every = raw.get("count"), raw.get("every_s")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (n, every)) or not lo <= n <= hi or not s_lo <= every <= s_hi:
        raise bad
    if isinstance(pulse_s, (int, float)) and every <= pulse_s:
        raise bad
    return {"count": n, "every_s": every}


def clean_action(raw: Any, env: Env) -> dict[str, Any]:
    lim = C("limits")
    do = raw.get("do") if isinstance(raw, dict) else None
    if do not in all_dos():
        raise Invalid("bad_action", "rules_bad_action", do=str(do)[: lim["id_len"]], entity="—")
    if env.client and do not in C("actions", "client_acts"):
        raise Invalid("not_allowed", "rules_client_action")
    if do == "season":
        if raw.get("value") not in SEASON_MODES:
            raise Invalid("bad_action", "rules_bad_season")
        return {"do": "season", "value": raw["value"]}
    if do == "notify":
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > lim["notify_len"] or not text.isprintable():
            raise Invalid("bad_action", "rules_bad_text", n=lim["notify_len"])
        return {"do": "notify", "text": text.strip(), "to": "me" if env.client or raw.get("to") == "me" else "all"}
    eid = _entity(raw.get("entity_id"), "action")
    domain = eid.split(".")[0]
    spec = spec_of(eid, do)
    if spec is None:  # not in the canon table: a lock / the alarm (never there), a valve opening, a do of another domain
        raise Invalid("bad_action", "rules_bad_action", do=do, entity=eid)
    if env.client and (domain not in C("actions", "client_domains") or not client_class_ok(env.hass, eid)):
        raise Invalid("not_allowed", "rules_client_action")
    # A valve only closes (and only Hauflo's own); everything else: Hauflo's own and never a lock / the alarm / a gate.
    if not env.may_change(eid):
        raise Invalid("target_not_allowed", "rules_target_not_allowed", entity=eid)
    if not supports(env.hass.states.get(eid), spec):  # HA would refuse it at run time («does not support this service»)
        raise Invalid("bad_action", "rules_bad_action", do=do, entity=eid)
    out: dict[str, Any] = {"do": do, "entity_id": eid}
    if (kind := spec.get("value")) is not None:
        out["value"] = action_value(kind, spec, do, raw.get("value"), env.hass.states.get(eid))
    if (again := clean_repeat(raw.get("repeat"), do, out["value"] if kind == "seconds" else None)) is not None:
        out["repeat"] = again
    return out


def _bounds(spec: dict[str, Any], attrs: Any, fallback: Any) -> tuple[float, float]:
    """The entity's own min / max (its attributes named by the canon), else the canon's."""
    names = spec.get("bounds")
    lo, hi = (num(attrs.get(names[0])), num(attrs.get(names[1]))) if names else (None, None)
    return (lo, hi) if lo is not None and hi is not None and lo <= hi else (fallback[0], fallback[1])


def action_value(kind: str, spec: dict[str, Any], do: str, v: Any, st: Any) -> Any:
    """An action's value against the canon kind and the target's own attributes (its options, min / max / step)."""
    lim = C("limits")
    attrs = st.attributes if st is not None else {}
    if kind == "option":
        opts = attrs.get(spec["options"])
        allowed = [str(o) for o in opts] if isinstance(opts, (list, tuple)) else []
        if not isinstance(v, str) or v not in allowed:
            raise Invalid("bad_action", "rules_bad_option", do=do, options=allowed, field=spec.get("field"), domain=getattr(st, "domain", None))
        return v
    if kind in ("text", "time", "date", "datetime"):
        if not isinstance(v, str) or not v.isprintable():
            raise Invalid("bad_action", "rules_bad_action_format", do=do)
        if kind == "time" and _TIME.match(v):
            return dt.time.fromisoformat(v).isoformat()
        if kind == "date" and _DATE.match(v):
            try:
                return dt.date.fromisoformat(v).isoformat()
            except ValueError:
                pass
        if kind == "datetime" and dt_util.parse_datetime(v) is not None:
            return v
        if kind == "text":
            lo, hi = _bounds(spec, attrs, (1, C("actions", "text_len")))
            if lo <= len(v) <= min(hi, C("actions", "text_len")):
                return v
            raise Invalid("bad_action", "rules_bad_action_value", do=do, lo=_fmt(lo, None), hi=_fmt(min(hi, C("actions", "text_len")), None))
        raise Invalid("bad_action", "rules_bad_action_format", do=do)
    n = _number(v if v is not None or kind != "seconds" else C("actions", "pulse_default_s"), do)
    fallback = {"percent": lim["percent"], "setpoint": lim["setpoint"], "seconds": C("actions", "pulse_s")}.get(kind, (-lim["number_abs"], lim["number_abs"]))
    lo, hi = _bounds(spec, attrs, fallback)
    if not lo <= n <= hi:
        raise Invalid("bad_action", "rules_bad_action_value", do=do, lo=_fmt(lo, None), hi=_fmt(hi, None))
    if kind == "percent":
        return int(round(n))
    step = num(attrs.get(spec["step"])) if spec.get("step") else None
    step = step if step and step > 0 else lim["setpoint_step"] if kind == "setpoint" else None
    return round(round(n / step) * step, ROUND_DIGITS) if step else n


def clean_at(raw: Any) -> dict[str, Any]:
    """«в 07:00, 22:00 · на закате + 15 мин · по будням»: moments (canon «schedule»), weekdays."""
    s = C("schedule")
    bad = Invalid("bad_schedule", "rules_bad_schedule", n=int(s["moments"]))
    if not isinstance(raw, dict):
        raise bad
    times, sun = raw.get("times") or [], raw.get("sun") or []
    days = raw["days"] if raw.get("days") is not None else list(DAYS)  # none said: every day; an empty list: refused
    if not isinstance(times, list) or not isinstance(sun, list) or not isinstance(days, list):
        raise bad
    if not 1 <= len(times) + len(sun) <= s["moments"] or any(not (isinstance(t, str) and _HHMM.match(t)) for t in times):
        raise bad
    if not days or any(d not in DAYS for d in days):
        raise bad
    lo, hi = s["sun_offset_min"]
    out_sun: list[dict[str, Any]] = []
    for e in sun:
        if not isinstance(e, dict) or e.get("event") not in s["events"]:
            raise bad
        off = num(e.get("offset_min", 0))
        if off is None or not lo <= off <= hi:
            raise Invalid("bad_schedule", "rules_bad_sun_offset", lo=int(lo), hi=int(hi))
        item = {"event": e["event"], "offset_min": int(round(off))}
        if item not in out_sun:
            out_sun.append(item)
    return {"times": sorted(set(times)), "sun": out_sun, "days": [d for d in DAYS if d in days]}


def next_fire(at: dict[str, Any], after: dt.datetime, sun: Callable[[str, dt.date], dt.datetime | None]) -> dt.datetime | None:
    """The first moment of `at` strictly after `after` (aware, in the house's time zone) within the canon's horizon;
    None: none there (no sunset in a polar summer). A sun moment belongs to the day of its event."""
    found: list[dt.datetime] = []
    for k in range(int(C("schedule", "horizon_days")) + 1):
        day = after.date() + dt.timedelta(days=k)
        if DAYS[day.weekday()] in at["days"]:
            found += _day_moments(at, day, after.tzinfo, sun)
    later = [m for m in found if dt_util.as_utc(m) > dt_util.as_utc(after)]
    return min(later, key=dt_util.as_utc) if later else None


def _day_moments(at: dict[str, Any], day: dt.date, tz: Any, sun: Callable[[str, dt.date], dt.datetime | None]) -> list[dt.datetime]:
    out = [dt.datetime.combine(day, dt.time.fromisoformat(t), tzinfo=tz) for t in at["times"]]
    for e in at.get("sun") or []:
        if (when := sun(e["event"], day)) is not None:
            out.append(when.astimezone(tz) + dt.timedelta(minutes=e["offset_min"]))
    return out


def next_off_day(at: dict[str, Any], after: dt.datetime, sun: Callable[[str, dt.date], dt.datetime | None]) -> dt.datetime | None:
    """The first moment of the next day the schedule does not run on (a weekday not among its days), strictly after
    `after`: there the log says once «пропущен: не тот день недели» (as HA's own automation's trace shows a weekday
    condition not met). None: it runs every day, or no such day within the horizon."""
    for k in range(int(C("schedule", "horizon_days")) + 1):
        day = after.date() + dt.timedelta(days=k)
        if DAYS[day.weekday()] in at["days"] or not (ms := _day_moments(at, day, after.tzinfo, sun)):
            continue
        first = min(ms, key=dt_util.as_utc)
        if dt_util.as_utc(first) > dt_util.as_utc(after):
            return first
    return None


def _actions(raw: Any, env: Env) -> list[dict[str, Any]]:
    n = C("limits", "actions")
    if not isinstance(raw, list) or not raw or len(raw) > n:
        raise Invalid("limit", "rules_limit_actions", n=n)
    return [clean_action(a, env) for a in raw]


def clean_name_gen(raw: Any) -> dict[str, Any] | None:
    """A generated name kept as recipe + room (+ number), shown in the reader's language; a typed name has none."""
    if not isinstance(raw, dict) or not any(r["id"] == raw.get("recipe") for r in C("recipes")):
        return None
    room = raw.get("room")
    n = raw.get("n")
    return {"recipe": raw["recipe"], "room": room.strip()[: C("limits", "name_len")] if isinstance(room, str) else "", "n": n if isinstance(n, int) and 1 <= n <= C("limits", "rules") else 1}


def clean_rule(raw: Any, env: Env) -> dict[str, Any]:
    """One rule, checked (the rest of the house is checked by check_house)."""
    lim = C("limits")
    if not isinstance(raw, dict):
        raise Invalid("bad", "rules_bad_object")
    rid = raw.get("id")
    if rid is None:
        rid = f"r{secrets.token_hex(ID_BYTES)}"
    if not isinstance(rid, str) or not dv.id_re().match(rid):
        raise Invalid("bad_id", "rules_bad_id", n=lim["id_len"])
    pname = raw.get("purpose") or "other"
    if pname not in C("purposes") or (env.client and pname not in C("client_purposes")):
        raise Invalid("bad_purpose", "rules_bad_purpose")
    meta = purpose(pname)
    priority = raw.get("priority") or meta["priority"]
    if priority not in PRIORITY or (env.client and priority != "comfort"):
        raise Invalid("bad_priority", "rules_bad_priority")
    rule: dict[str, Any] = {"id": rid, "name": _name(raw.get("name")), "enabled": raw.get("enabled", True) is not False, "purpose": pname, "priority": priority}
    if (gen := clean_name_gen(raw.get("name_gen"))) is not None:
        rule["name_gen"] = gen
    when = raw.get("when")
    if when not in WHENS:
        raise Invalid("bad_when", "rules_bad_when")
    if when == AT:
        rule["when"], rule["at"] = AT, clean_at(raw.get("at"))
        return _clean_tail(raw, rule, env, meta)
    rule["input"] = inp = clean_input(raw.get("input"), env)
    if raw.get("window") is not None:
        if when not in RANGES:
            raise Invalid("bad_when", "rules_window_when")
        rule["window"] = clean_window(raw["window"], inp, env)
        if rule["window"]["source"] == win.ON_TIME:
            inp["unit"] = C("window", "on_time_unit")
    else:
        binary = inp["entity_id"].split(".")[0] in BINARY_DOMAINS and inp["attribute"] is None
        if binary != (when in ("on", "off")):
            raise Invalid("bad_when", "rules_when_kind")
    rule["when"] = when
    y = num(raw.get("y")) if raw.get("y") is not None else 0.0
    if y is None or y < 0:
        raise Invalid("bad_hysteresis", "rules_bad_hysteresis")
    if when in ("below", "above"):
        rule["x"] = _number(raw.get("x"), "X")
        if not y < max(abs(rule["x"]), lim["hysteresis_floor"]):
            raise Invalid("bad_hysteresis", "rules_bad_hysteresis")
    elif when in ("outside", "between"):
        lo, hi = _number(raw.get("lo"), "lo"), _number(raw.get("hi"), "hi")
        if lo >= hi:
            raise Invalid("bad_range", "rules_bad_range")
        # «вне»: the normal band lo + Y … hi − Y must be left; «между»: Y below the span.
        if not (y + y < hi - lo if when == "outside" else y < hi - lo):
            raise Invalid("bad_hysteresis", "rules_bad_hysteresis")
        rule["lo"], rule["hi"] = lo, hi
    elif y:
        raise Invalid("bad_hysteresis", "rules_bad_hysteresis")
    rule["y"] = y
    if "window" in rule:
        _in_bounds(rule)
        rule["for_min"] = rule["back_min"] = 0.0  # held by N periods in a row, not minutes
    else:
        rule["for_min"] = _minutes(raw.get("for_min"))
        rule["back_min"] = _minutes(raw.get("back_min", raw.get("for_min")))
    return _clean_tail(raw, rule, env, meta)


def _clean_tail(raw: dict[str, Any], rule: dict[str, Any], env: Env, meta: dict[str, Any]) -> dict[str, Any]:
    """What a value rule and a schedule rule share: «только если», actions, back, interlock, who."""
    lim = C("limits")
    at = scheduled(rule)
    conds = raw.get("only_if") or []
    if not isinstance(conds, list) or len(conds) > lim["conditions"]:
        raise Invalid("limit", "rules_limit_conditions", n=lim["conditions"])
    rule["only_if"] = [clean_condition(c) for c in conds]
    rule["actions"] = _actions(raw.get("actions"), env)
    for a in rule["actions"]:
        # A pulse never shorter than the purpose's minimum on time (a heater's, a pump's): the helper still holds.
        if (act_spec(a) or {}).get("value") == "seconds" and a["value"] < (meta.get("min_on_s") or 0):
            raise Invalid("bad_action", "rules_pulse_min_on", n=int(meta["min_on_s"]))
    back = raw.get("back", meta.get("back", "reverse"))
    if meta.get("back") == "nothing" or at:
        back = "nothing"  # a leak's valve stays shut: never opened by a rule; a moment has no «back»
    if isinstance(back, list):
        back = _actions(back, env)
    elif back not in ("reverse", "nothing"):
        raise Invalid("bad_back", "rules_bad_back")
    rule["back"] = back
    if (inter := raw.get("interlock")) is not None:
        if meta.get("safe") != "off":
            raise Invalid("bad_interlock", "rules_bad_interlock")
        rule["interlock"] = _entity(inter, "interlock")
    client = raw.get("client") if isinstance(raw.get("client"), dict) else {}
    c: dict[str, Any] = {"toggle": client.get("toggle", True) is not False, "visible": client.get("visible", True) is not False}
    if not at and (client.get("lo") is not None or client.get("hi") is not None):
        c["lo"], c["hi"] = _number(client.get("lo"), "lo"), _number(client.get("hi"), "hi")
        if c["lo"] > c["hi"]:
            raise Invalid("bad_range", "rules_bad_range")
    rule["client"] = c
    rule["owner"] = "client" if env.client or raw.get("owner") == "client" else "integrator"
    if not at and ("window" in rule or raw.get("outputs") is not None):
        rule["outputs"] = clean_outputs(raw.get("outputs"), "window" in rule)
    if raw.get("snooze_days") is not None or outputs(rule)["suggest"]:
        lo, hi = C("suggest", "snooze_bounds")
        d = num(raw.get("snooze_days")) if raw.get("snooze_days") is not None else C("suggest", "snooze_days")
        if d is None or not lo <= d <= hi:
            raise Invalid("bad_snooze", "rules_bad_snooze", lo=int(lo), hi=int(hi))
        rule["snooze_days"] = int(d)
    if isinstance(raw.get("quiet_ok"), bool):
        rule["quiet_ok"] = raw["quiet_ok"]
    if isinstance(raw.get("user_id"), str) and _USER_RE.match(raw["user_id"]):
        rule["user_id"] = raw["user_id"]
    return rule


def _in_bounds(rule: dict[str, Any]) -> None:
    """A window rule's thresholds inside what its unit can be (canon «thresholds»; the backend checks the same)."""
    unit = rule["input"].get("unit")
    b = C("thresholds").get(unit) if unit else None
    if b and any(k in rule and not b[0] <= rule[k] <= b[1] for k in ("x", "lo", "hi")):
        raise Invalid("bad_number", "rules_window_threshold", lo=_fmt(b[0], None), hi=_fmt(b[1], None), unit=unit)


def targets_of(rule: dict[str, Any]) -> list[tuple[str, str]]:
    """(entity, attribute) the rule writes."""
    acts = list(rule["actions"]) + (rule["back"] if isinstance(rule["back"], list) else [])
    return sorted({(a.get("entity_id") or SEASON_TARGET, slot_of(a)) for a in acts if a["do"] != "notify"})


def inputs_of(rule: dict[str, Any], derived_sources: Callable[[str], list[str]] | None = None) -> set[str]:
    """Every entity the rule's decision reads — derived values unfolded to their sources."""
    out = {*([rule["input"]["entity_id"]] if "input" in rule else []), *(c["entity_id"] for c in rule["only_if"] if "entity_id" in c)}
    if rule.get("interlock"):
        out.add(rule["interlock"])
    if derived_sources is not None:
        for eid in list(out):
            out.update(dv.split_source(s)[0] for s in derived_sources(eid))
    return out


def check_house(rule: dict[str, Any], others: list[dict[str, Any]], derived_sources: Callable[[str], list[str]]) -> None:
    """Loops and conflicts with the house's other rules; raises Invalid."""
    if len(others) >= C("limits", "rules"):
        raise Invalid("limit", "rules_limit_rules", n=C("limits", "rules"))
    # A schedule rule fires once and holds nothing: no loop through it, no conflict (an ACTIVE rule of the same or a higher
    # priority on that target wins at run time, said in its log).
    if scheduled(rule):
        return
    others = [o for o in others if not scheduled(o)]
    mine = {e for e, _ in targets_of(rule)}
    ins = inputs_of(rule, derived_sources)
    if mine & ins:
        raise Invalid("loop", "rules_loop", entity=sorted(mine & ins)[0])
    # Across rules: an edge from what a rule reads to what it writes; a path back to this rule's inputs is a loop.
    edges: dict[str, set[str]] = {}
    for r in [*others, rule]:
        tg = {e for e, _ in targets_of(r)}
        for i in inputs_of(r, derived_sources):
            edges.setdefault(i, set()).update(tg)
    for start in sorted(mine):
        seen, todo = {start}, [start]
        while todo:
            for nxt in edges.get(todo.pop(), ()):
                if nxt in ins:
                    raise Invalid("loop", "rules_loop_chain", entity=start, other=nxt)
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
    for o in others:
        both = set(targets_of(o)) & set(targets_of(rule)) if o["priority"] == rule["priority"] else set()
        if both:
            e, a = sorted(both)[0]
            raise Invalid("conflict", "rules_conflict", entity=e, attribute=a, rule=o["name"])


# --- the decision -----------------------------------------------------------------------------------------------------


def past_on(rule: dict[str, Any], v: float) -> bool:
    w = rule["when"]
    if w == "below":
        return v < rule["x"]
    if w == "above":
        return v > rule["x"]
    if w == "outside":
        return v < rule["lo"] or v > rule["hi"]
    if w == "between":
        return rule["lo"] <= v <= rule["hi"]
    return bool(v) == (w == "on")


def past_off(rule: dict[str, Any], v: float) -> bool:
    w, y = rule["when"], rule["y"]
    if w == "below":
        return v >= rule["x"] + y
    if w == "above":
        return v <= rule["x"] - y
    if w == "outside":
        return rule["lo"] + y <= v <= rule["hi"] - y
    if w == "between":
        return v < rule["lo"] - y or v > rule["hi"] + y
    return not past_on(rule, v)


def window_off(rule: dict[str, Any], v: float) -> bool:
    """Back to normal for one period of a window rule: strictly past X ∓ Y (a period at the threshold itself breaks
    the run — as the season's suggestion always did)."""
    w, y = rule["when"], rule["y"]
    if w == "below":
        return v > rule["x"] + y
    if w == "above":
        return v < rule["x"] - y
    if w == "outside":
        return rule["lo"] + y < v < rule["hi"] - y
    return v < rule["lo"] - y or v > rule["hi"] + y


def in_row(rule: dict[str, Any], vals: list[float | None], test: Callable[[dict[str, Any], float], bool]) -> int:
    """How many of the latest periods in a row pass `test` (a period without data ends the run)."""
    n = 0
    for v in reversed(vals):
        if v is None or not test(rule, v):
            break
        n += 1
    return n


def ga_raw(v: Any) -> int | None:
    """A KNX group address as its 16-bit number in any notation the canon's `ga_bits` knows (3-level «1/2/3», 2-level
    «1/234», free «2058»), None — not one. The bit layout is the KNX standard's, kept with the canon (no numbers here)."""
    if isinstance(v, bool) or not isinstance(v, (str, int)):
        return None
    t = str(v).strip()
    if not _GA_RE.match(t):
        return None
    parts = [int(x) for x in t.split("/")]
    bits = C("bus_reads", "ga_bits").get(str(len(parts)))
    if bits is None or any(p >= (1 << b) for p, b in zip(parts, bits, strict=True)):
        return None
    out = 0
    for p, b in zip(parts, bits, strict=True):
        out = (out << b) | p
    return out


def _gas(v: Any, cap: int) -> list[str]:
    if not isinstance(v, list) or not v or len(v) > cap:
        raise Invalid("bad_bus_read", "rules_bus_read_gas", n=cap)
    out: list[str] = []
    for g in v:
        if ga_raw(g) is None:
            raise Invalid("bad_bus_read", "rules_bus_read_ga", ga=str(g)[: C("limits", "id_len")])
        if (t := str(g).strip()) not in out:
            out.append(t)
    return out


def clean_bus_read(raw: Any) -> dict[str, Any]:
    """One «читать после команды» rule (Р65): a write to any of `triggers` → knx.read of `reads` after `delay_s`."""
    br = C("bus_reads")
    if not isinstance(raw, dict):
        raise Invalid("bad", "rules_bad_object")
    rid = raw.get("id")
    if not isinstance(rid, str) or not dv.id_re().match(rid):
        raise Invalid("bad_id", "rules_bad_id", n=C("limits", "id_len"))
    lo, hi = br["delay_bounds_s"]
    delay = raw.get("delay_s", br["delay_s"])
    if isinstance(delay, bool) or num(delay) is None or not lo <= float(delay) <= hi:
        raise Invalid("bad_bus_read", "rules_bus_read_delay", lo=_fmt(lo, None), hi=_fmt(hi, None))
    out = {"id": rid, "triggers": _gas(raw.get("triggers"), int(br["triggers"])), "reads": _gas(raw.get("reads"), int(br["reads"])), "delay_s": float(delay), "enabled": raw.get("enabled", True) is not False}
    if (name := raw.get("name")) not in (None, ""):
        if not isinstance(name, str) or len(name) > br["name_len"] or not name.isprintable():
            raise Invalid("bad_name", "rules_bad_name", n=int(br["name_len"]))
        out["name"] = name.strip()
    return out


def clean_bus_reads(raw: Any) -> list[dict[str, Any]]:
    """The house's whole list (the deploy sends it whole; ids unique)."""
    cap = int(C("bus_reads", "rules"))
    if not isinstance(raw, list):
        raise Invalid("bad", "rules_bad_object")
    if len(raw) > cap:
        raise Invalid("limit", "rules_limit_bus_reads", n=cap)
    out = [clean_bus_read(r) for r in raw]
    if len({r["id"] for r in out}) != len(out):
        raise Invalid("bad_id", "rules_bad_id", n=C("limits", "id_len"))
    return out


def _blank() -> dict[str, Any]:
    return {"active": False, "pend": None, "hold_until": None, "hold_why": None, "paused": False, "fault": None, "restore": {}, "holds": [], "since": None, "log": [], "sug": None, "confirmed": None, "auto_at": {}, "no_data": False, "held": [], "next_at": None, "last_at": None, "last_result": None, "off_at": None}


# What a rule's answer says about what it read: hidden from the tunnel when the rule reads what get_states hides.
_LIVE = ("state", "active", "value", "why", "why_en", "since", "hold_until", "values", "starts", "periods", "suggestion")


def _redact(out: dict[str, Any]) -> dict[str, Any]:
    out = {**out, **{k: None for k in _LIVE if k in out}, "hidden": True}
    if "state" in out:
        out["state"] = "hidden"
    if "log" in out:
        out["log"] = [{"at": i.get("at"), "event": "hidden"} for i in out["log"]]
    return out


class Texts:
    """The agent's texts in both languages: translations («exceptions»), HA's option labels, devices by name.

    One place for every refusal / description a person reads (rules, automations): `say` a key, `words` its placeholders.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.texts: dict[str, dict[str, str]] = {lang: {} for lang in LANGS}
        # HA's own labels of option values (climate «Не дома», water heater «Эко»…) — a refusal lists them in words.
        self.labels: dict[str, dict[str, str]] = {lang: {} for lang in LANGS}

    async def async_texts(self) -> None:
        prefix, suffix = f"component.{DOMAIN}.exceptions.", ".message"
        for lang in LANGS:
            tr = await translation.async_get_translations(self.hass, lang, "exceptions", [DOMAIN])
            self.texts[lang] = {k[len(prefix) : -len(suffix)]: v for k, v in tr.items() if k.startswith(prefix) and k.endswith(suffix)}
            self.labels[lang] = await translation.async_get_translations(self.hass, lang, "entity_component", OPTION_DOMAINS)

    def say(self, key: str, ru: bool, **ph: Any) -> str:
        text = self.texts[LANGS[0] if ru else LANGS[1]].get(key) or self.texts[LANGS[1]].get(key) or key
        try:
            return text.format(**ph)
        except (KeyError, IndexError, ValueError):
            return text

    def who(self, eid: Any) -> Any:
        """A device in a text: what a person calls it (notify.label), never its entity id."""
        return label(self.hass, eid) if isinstance(eid, str) and _ENTITY_RE.match(eid) else eid

    def option_label(self, ru: bool, domain: Any, field: Any, option: str) -> str:
        """An option value as HA names it in the language (its state attribute's labels, else the domain's states), else as is."""
        labels = self.labels[LANGS[0] if ru else LANGS[1]]
        root = f"component.{domain}.entity_component._."
        return labels.get(f"{root}state_attributes.{field}.state.{option}") or labels.get(f"{root}state.{option}") or option

    def words(self, ru: bool, ph: dict[str, Any]) -> dict[str, Any]:
        """A refusal's placeholders in words: an action by its name (rules_do_…), a device by its name, options by HA's labels."""
        out = {k: self.who(v) if k in ("entity", "other") else v for k, v in ph.items()}
        for k in ("do", "what"):
            if isinstance(v := out.get(k), str) and f"rules_do_{v}" in self.texts[LANGS[1]]:
                out[k] = self.say(f"rules_do_{v}", ru)
        if isinstance(opts := out.get("options"), (list, tuple)):
            out["options"] = ", ".join(self.option_label(ru, out.get("domain"), out.get("field"), str(o)) for o in opts) or "—"
        return out

    def refusal(self, err: Invalid) -> dict[str, Any]:
        return {"code": err.code, "key": err.key, "message": self.say(err.key, False, **self.words(False, err.ph)), "message_ru": self.say(err.key, True, **self.words(True, err.ph))}


async def said(hass: HomeAssistant, err: Invalid, ru: bool) -> dict[str, Any]:
    """A keyed refusal as a page shows it: its text in the request's language (never a key, an id), its code."""
    tx = Texts(hass)
    await tx.async_texts()
    return {"message": tx.say(err.key, ru, **tx.words(ru, err.ph)), "code": err.code}


class Rules(Texts):
    """Every value rule and derived value of the house (one per loaded entry)."""

    def __init__(
        self,
        hass: HomeAssistant,
        owns: Callable[[str], bool],
        own_raw: Callable[[str], bool],
        season: Callable[[], str] | None = None,
        tell: Callable[..., Any] | None = None,
        *,
        season_now: Callable[[], str | None] | None = None,
        season_set: Callable[[str, str | None], Any] | None = None,
        ask: Callable[..., Any] | None = None,
        notify_of: Callable[[], Any] | None = None,
    ) -> None:
        self.hass = hass
        # The alarm engine: its config holds the project's language — the house's one language for every push (С3).
        self.notify_of = notify_of or (lambda: None)
        self.owns, self.own_raw = owns, own_raw
        self.season = season or (lambda: "winter")
        self.tell = tell
        # «Зима / Лето» as a target (season.py): what the house is in now, set it; a suggestion's push with buttons.
        self.season_now = season_now or (lambda: None)
        self.season_set = season_set
        self.ask = ask
        # A recipe's own push wording (the season's «Пора на зиму?»): recipe → (rule, on, ru) → (title, message, tag).
        self.ask_text: dict[str, Callable[[dict[str, Any], bool, bool], tuple[str, str, str]]] = {}
        # What the tunnel (the cloud's system user) may read through a rule — set by the agent; fail closed.
        self.cloud_reads: Callable[[str, str | None], bool] = lambda eid, attr: False
        self.win: dict[str, dict[str, Any]] = {}  # rule → its last periods' values (window.py), read when one closes
        self._win_timers: dict[str, CALLBACK_TYPE] = {}
        self._sug_sent: dict[str, list[float]] = {}  # rule → its suggestions' push times (the daily cap)
        self.store = HaufloStore(hass, STORE_KEY)
        self.rules: dict[str, dict[str, Any]] = {}
        self.quiet = quiet.default()  # quiet hours of the house (quiet.py); a rule may override (`quiet_ok`)
        self.derived_cfg: dict[str, dict[str, Any]] = {}
        self.derived: dict[str, dv.Derived] = {}
        self.state: dict[str, dict[str, Any]] = {}
        self.writes: dict[str, list[float]] = {}  # target → our write times (last rate window)
        self.climate_at: dict[str, float] = {}
        self.switched: dict[str, float] = {}  # target → when it last really switched on/off (kept over a restart)
        self.todo: list[dict[str, Any]] = []
        self.pulses: dict[str, dict[str, Any]] = {}  # target → {rule, until}: a pulse's «off» due (kept over a restart)
        self._pulse_timers: dict[str, CALLBACK_TYPE] = {}
        self._repeat_timers: dict[str, list[CALLBACK_TYPE]] = {}  # rule → its «повторить» timers (not kept over a restart)
        Texts.__init__(self, hass)
        self.names: dict[str, dict[str, str]] = {}  # its sensors' names in the house's language (const.site_name)
        self.entry_id: str | None = None
        self.add_binary: Callable[[list[Any]], None] | None = None
        self.add_switches: Callable[[list[Any]], None] | None = None
        self.add_sensors: Callable[[list[Any]], None] | None = None
        self.binaries: dict[str, RuleActive] = {}
        self.switches: dict[str, RuleSwitch] = {}
        self.sensors: dict[str, DerivedSensor] = {}
        self._ctx: deque[str] = deque(maxlen=int(C("engine", "contexts")))
        self._sent: dict[str, tuple[float, Any]] = {}
        self._bus: deque[float] = deque()
        self._warned: dict[str, float] = {}
        self._timers: dict[str, CALLBACK_TYPE] = {}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._unsub_tick: CALLBACK_TYPE | None = None
        # Р65 «читать после команды»: the rules, trigger GA (16-bit) → rule ids, pending reads, the last reads, the cap.
        self.bus_reads: list[dict[str, Any]] = []
        self._br_index: dict[int, list[str]] = {}
        self._br_timers: dict[str, tuple[CALLBACK_TYPE, float]] = {}
        self._br_log: deque[dict[str, Any]] = deque(maxlen=int(C("bus_reads", "log_size")))
        self._br_sent: deque[tuple[float, int]] = deque()
        self._unsub_br: CALLBACK_TYPE | None = None

    @property
    def ru(self) -> bool:
        """The house's one language (const.site_ru): the project's, else HA's — as the alarms' pushes say it."""
        return site_ru(self.hass.config.language, getattr(self.notify_of(), "config", None))

    # --- texts (translations, «exceptions» → rules_…): Texts; the rules also load their sensors' names -------------------

    async def async_texts(self) -> None:
        await super().async_texts()
        self.names = await async_entity_names(self.hass)

    # --- storage ------------------------------------------------------------------------------------------------------

    async def async_load(self) -> None:
        data = await self.store.async_load() or {}
        for raw in data.get("derived") or []:
            try:
                d = dv.clean(raw)
            except Invalid:
                continue
            self.derived_cfg[d["id"]] = d
        for raw in data.get("rules") or []:
            # Kept as it was checked; a target that stopped being Hauflo's is refused at run time, not dropped here.
            if isinstance(raw, dict) and isinstance(raw.get("id"), str) and (isinstance(raw.get("input"), dict) or (raw.get("when") == AT and isinstance(raw.get("at"), dict))):
                self.rules[raw["id"]] = raw
        for rid, st in (data.get("state") or {}).items():
            if rid in self.rules and isinstance(st, dict):
                self.state[rid] = {**_blank(), **st}
        if isinstance(data.get("quiet"), dict):
            try:
                self.quiet = quiet.clean(data["quiet"], quiet.default())
            except Invalid:
                pass
        self.writes = {k: [float(t) for t in v] for k, v in (data.get("writes") or {}).items() if isinstance(v, list)}
        self.climate_at = {k: float(v) for k, v in (data.get("climate_at") or {}).items()}
        self.switched = {k: float(v) for k, v in (data.get("switched") or {}).items() if isinstance(v, (int, float))}
        self.todo = [t for t in data.get("todo") or [] if isinstance(t, dict) and t.get("rule") in self.rules]
        self.pulses = {k: v for k, v in (data.get("pulses") or {}).items() if isinstance(v, dict) and isinstance(v.get("until"), (int, float))}
        try:
            self._br_set(clean_bus_reads(data.get("bus_reads") or []))
        except Invalid:
            _LOGGER.warning("hauflo: stored bus reads dropped (out of the canon)")
        self._make_derived()

    def data(self) -> dict[str, Any]:
        return {"rules": list(self.rules.values()), "derived": list(self.derived_cfg.values()), "state": self.state, "quiet": self.quiet, "writes": self.writes, "climate_at": self.climate_at, "switched": self.switched, "todo": self.todo, "bus_reads": self.bus_reads, "pulses": self.pulses}

    def _save_soon(self) -> None:
        self.store.async_delay_save(self.data, C("engine", "save_delay_s"))

    async def _save(self) -> None:
        await self.store.async_save(self.data())

    def _env(self, client: bool) -> Env:
        return Env(self.hass, self.owns, self.own_raw, client)

    def reads(self, rule: dict[str, Any]) -> set[tuple[str, str | None]]:
        """(entity, attribute) a rule's decision reads, derived values unfolded to their sources."""
        out = {(rule["input"]["entity_id"], rule["input"].get("attribute"))} if "input" in rule else set()
        for c in rule["only_if"]:
            if "entity_id" in c:
                out.add((c["entity_id"], c.get("attribute") or ("hvac_action" if c["type"] == "heating" else dv.default_attribute(c["entity_id"]))))
        if rule.get("interlock"):
            out.add((rule["interlock"], None))
        for eid, _ in list(out):
            for src in self.derived_sources(eid):
                e, a = dv.split_source(src)
                out.add((e, a or dv.default_attribute(e)))
        return out

    def cloud_hidden(self, rule: dict[str, Any]) -> str | None:
        """The first thing a rule reads that the tunnel may not (or None). «Дома / никого» reads where the family is — a
        person's state, never the tunnel's (red-team 2, A3): such a rule is saved in HA only, and the tunnel sees it
        redacted like any rule over a private input."""
        if any(c["type"] == "presence" for c in rule["only_if"]):
            return next(iter(sorted(self.hass.states.async_entity_ids("person"))), "person")
        return next((eid for eid, attr in sorted(self.reads(rule), key=str) if not self.cloud_reads(eid, attr)), None)

    def cloud_hidden_entities(self) -> set[str]:
        """The rule / derived entities whose state would tell the tunnel what it may not read (scrubbed like private)."""
        out: set[str] = set()
        for rid, rule in self.rules.items():
            if self.cloud_hidden(rule) is not None and (b := self.binaries.get(rid)) is not None and b.entity_id:
                out.add(b.entity_id)
        for did, cfg in self.derived_cfg.items():
            if any(not self.cloud_reads(e, a or dv.default_attribute(e)) for e, a in map(dv.split_source, cfg["sources"])):
                ent = self.sensors.get(did)
                out.add(ent.entity_id if ent is not None and ent.entity_id else derived_entity_id(cfg))
        return out

    def _cloud_check(self, rule: dict[str, Any]) -> None:
        if (eid := self.cloud_hidden(rule)) is not None:
            raise Invalid("not_allowed", "rules_private_input", entity=eid)

    def derived_sources(self, eid: str) -> list[str]:
        for did, d in self.derived_cfg.items():
            ent = self.sensors.get(did)
            if eid == (ent.entity_id if ent is not None and ent.entity_id else derived_entity_id(d)):
                return d["sources"]
        return []

    def st(self, rid: str) -> dict[str, Any]:
        return self.state.setdefault(rid, _blank())

    # --- running ------------------------------------------------------------------------------------------------------

    async def async_start(self) -> None:
        await self.async_texts()
        self.sync_entities()
        self._listen()
        self._bus_listen()
        self._unsub_tick = async_track_time_interval(self.hass, self._tick, dt.timedelta(seconds=C("engine", "tick_s")), name="hauflo rules")

        self._unsub_action = self.hass.bus.async_listen("mobile_app_notification_action", self._on_action)

        @callback
        def _up(_hass: HomeAssistant) -> None:
            self.hass.async_create_task(self._start_all(), "hauflo rules start")

        async_at_started(self.hass, _up)

    async def _start_all(self) -> None:
        await self._pulses_due(start=True)
        for rid in [r for r, x in self.rules.items() if x.get("window")]:
            await self.window_refresh(rid, "start")
        await self.async_evaluate_all("start")

    @callback
    def async_stop(self) -> None:
        for u in [*self._unsubs, *self._timers.values(), *self._win_timers.values(), *self._pulse_timers.values(), *(u for us in self._repeat_timers.values() for u in us), *([self._unsub_tick] if self._unsub_tick else []), *([self._unsub_action] if getattr(self, "_unsub_action", None) else [])]:
            u()
        self._unsubs.clear()
        self._timers.clear()
        self._win_timers.clear()
        self._pulse_timers.clear()
        self._repeat_timers.clear()
        self._unsub_tick = None
        self._unsub_action = None
        for u, _ in self._br_timers.values():
            u()
        self._br_timers.clear()
        if self._unsub_br is not None:
            self._unsub_br()
            self._unsub_br = None

    @callback
    def _listen(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        srcs = sorted({dv.split_source(s)[0] for d in self.derived_cfg.values() for s in d["sources"]})
        if srcs:
            self._unsubs.append(async_track_state_change_event(self.hass, srcs, self._source_changed))
            # The same value sent again (a KNX sensor's cyclic send) is only «reported»: a stale value is fresh again.
            self._unsubs.append(async_track_state_report_event(self.hass, srcs, self._source_reported))
        ins = sorted({i for r in self.rules.values() for i in inputs_of(r)})
        outs = sorted({e for r in self.rules.values() for e, _ in targets_of(r)})
        if ins:
            self._unsubs.append(async_track_state_change_event(self.hass, ins, self._input_changed))
        if outs:
            self._unsubs.append(async_track_state_change_event(self.hass, outs, self._target_changed))

    # --- Р65: read after a command -------------------------------------------------------------------------------------

    def _br_set(self, rules: list[dict[str, Any]]) -> None:
        self.bus_reads = rules
        self._br_index = {}
        for r in rules:
            if r["enabled"]:
                for g in r["triggers"]:
                    self._br_index.setdefault(ga_raw(g) or 0, []).append(r["id"])
        for rid in [k for k in self._br_timers if k not in {r["id"] for r in rules}]:
            self._br_timers.pop(rid)[0]()

    @callback
    def _bus_listen(self) -> None:
        """HA KNX tells every telegram (incoming and its own outgoing) through one dispatcher signal."""
        try:
            from homeassistant.components.knx.const import SIGNAL_KNX_TELEGRAM  # noqa: PLC0415
            from homeassistant.helpers.dispatcher import async_dispatcher_connect  # noqa: PLC0415
        except Exception as err:  # noqa: BLE001 — no KNX integration: nothing to hear, the rules wait
            _LOGGER.debug("hauflo: no KNX telegram signal (%s)", err)
            return
        self._unsub_br = async_dispatcher_connect(self.hass, SIGNAL_KNX_TELEGRAM, self._bus_heard)

    @callback
    def _bus_heard(self, _telegram: Any, telegram_dict: Any = None) -> None:
        t = telegram_dict or {}
        if str(t.get("telegramtype", "")) != KNX_WRITE or (raw := ga_raw(t.get("destination"))) is None:
            return
        rids = self._br_index.get(raw)
        if not rids:
            return
        now = dv.now()
        hi = C("bus_reads", "delay_bounds_s")[1]
        for rid in rids:
            rule = next((r for r in self.bus_reads if r["id"] == rid), None)
            if rule is None:
                continue
            # A burst of writes (a button pressed twice) → one read after the last one, but never later than the bound.
            first = now
            if (old := self._br_timers.pop(rid, None)) is not None:
                old[0]()
                first = old[1]
            delay = max(0.0, min(rule["delay_s"], first + hi - now))
            trigger = str(t.get("destination"))
            self._br_timers[rid] = (async_call_later(self.hass, delay, partial(self._bus_fire, rid, trigger)), first)

    async def _bus_fire(self, rid: str, trigger: str, _now: Any = None) -> None:
        self._br_timers.pop(rid, None)
        rule = next((r for r in self.bus_reads if r["id"] == rid), None)
        if rule is None or not rule["enabled"]:
            return
        now = dv.now()
        while self._br_sent and now - self._br_sent[0][0] >= SECONDS_PER_MINUTE:
            self._br_sent.popleft()
        n = len(rule["reads"])
        item: dict[str, Any] = {"at": now, "rule": rid, "trigger": trigger, "reads": rule["reads"]}
        if sum(k for _, k in self._br_sent) + n > C("bus_reads", "reads_per_min"):
            self._br_log.append({**item, "result": "capped"})
            _LOGGER.warning("hauflo: bus read %s after %s skipped — more than %s reads a minute", rid, trigger, C("bus_reads", "reads_per_min"))
            return
        self._br_sent.append((now, n))
        try:
            await self.hass.services.async_call("knx", "read", {"address": rule["reads"]}, blocking=True)
        except Exception as err:  # noqa: BLE001 — KNX gone / not set up: said in the log, the next write tries again
            self._br_log.append({**item, "result": "error", "error": str(err)[: int(C("engine", "error_len"))]})
            _LOGGER.warning("hauflo: bus read %s after %s failed: %s", rid, trigger, err)
            return
        self._br_log.append({**item, "result": "read"})
        _LOGGER.info("hauflo: bus read %s after a write to %s: knx.read %s", rid, trigger, ", ".join(rule["reads"]))

    async def set_bus_reads(self, raw: Any) -> dict[str, Any]:
        self._br_set(clean_bus_reads(raw))
        await self._save()
        return {"bus_reads": self.bus_reads_out()}

    def bus_reads_out(self) -> dict[str, Any]:
        return {"rules": self.bus_reads, "log": list(self._br_log)[-int(C("bus_reads", "log_out")) :], "listening": self._unsub_br is not None}

    async def _tick(self, _now: Any = None) -> None:
        for did, d in self.derived.items():
            d.update()
            if (s := self.sensors.get(did)) is not None:
                s.refresh()
        await self.async_evaluate_all("tick")
        await self._run_todo()
        await self._pulses_due()
        for rid in list(self.rules):
            await self._suggest_due(rid)
        await self._release_held()

    @callback
    def _source_changed(self, event: Event) -> None:
        eid = event.data.get("entity_id")
        for did, d in self.derived.items():
            if any(dv.split_source(s)[0] == eid for s in d.cfg["sources"]):
                d.update()
                if (s := self.sensors.get(did)) is not None:
                    s.refresh()

    @callback
    def _source_reported(self, event: Event) -> None:
        eid = event.data.get("entity_id")
        for did, d in self.derived.items():
            if (d.why or "").startswith("stale") and any(dv.split_source(s)[0] == eid for s in d.cfg["sources"]):
                d.update()
                if (s := self.sensors.get(did)) is not None:
                    s.refresh()

    @callback
    def _input_changed(self, event: Event) -> None:
        eid = event.data.get("entity_id")
        for rid, r in list(self.rules.items()):
            if eid in inputs_of(r) and not (r.get("window") and eid == r["input"]["entity_id"]):
                self.hass.async_create_task(self.evaluate(rid, "input"), "hauflo rule")

    async def async_evaluate_all(self, why: str = "tick") -> None:
        for rid in list(self.rules):
            await self.evaluate(rid, why)

    # --- reading -------------------------------------------------------------------------------------------------------

    def read(self, rule: dict[str, Any]) -> tuple[float | None, str | None]:
        """(value in the rule's unit, why not)."""
        inp = rule["input"]
        eid, attr = inp["entity_id"], inp.get("attribute")
        st = self.hass.states.get(eid)
        if st is None:
            return None, "no_entity"
        if st.state == STATE_UNKNOWN:
            return None, "unknown"
        if st.state in dv.NOT_A_STATE:
            return None, "unavailable"
        if rule["when"] in ("on", "off"):
            if st.state not in (STATE_ON, STATE_OFF):
                return None, "not_a_number"
            return float(st.state == STATE_ON), None
        if attr == SETPOINT_GAP:
            sp, cur = num(st.attributes.get("temperature")), num(st.attributes.get("current_temperature"))
            v = None if sp is None or cur is None else sp - cur
        else:
            v = num(st.attributes.get(attr) if attr else st.state)
        if v is None:
            return None, "not_a_number"
        conv = dv.convert(v, dv.unit_of(st, attr), inp.get("unit"))
        if conv is None:
            return None, "unit"
        return conv, None

    def conditions(self, rule: dict[str, Any]) -> tuple[bool, str | None]:
        now = dt_util.now()
        for c in rule["only_if"]:
            t = c["type"]
            if t == "time":
                hm, a, b = now.strftime("%H:%M"), c["after"], c["before"]
                inside = a <= hm < b if a <= b else (hm >= a or hm < b)
                # Past midnight the window belongs to the day it started on.
                start_day = DAYS[now.weekday()] if (a <= b or hm >= a) else DAYS[now.weekday() - 1]
                ok = inside and start_day in c["days"]
            elif t == "season":
                ok = self.season() == c["mode"]
            elif t == "presence":
                ok = any(s.state == STATE_HOME for s in self.hass.states.async_all("person")) == c["home"]
            elif t == "heating":
                st = self.hass.states.get(c["entity_id"])
                ok = st is not None and st.attributes.get("hvac_action") == "heating"
            elif t == "value":
                st = self.hass.states.get(c["entity_id"])
                v = num((st.attributes.get(c["attribute"]) if c["attribute"] else st.state) if st else None)
                ok = v is not None and (v < c["x"] if c["when"] == "below" else v > c["x"])
            else:
                st = self.hass.states.get(c["entity_id"])
                ok = (st is not None and st.state not in (*OFF_STATES, *dv.NOT_A_STATE)) == c["on"]
            if not ok:
                return False, t
        return True, None

    # --- the state machine ------------------------------------------------------------------------------------------------

    async def evaluate(self, rid: str, why: str = "tick") -> None:
        rule = self.rules.get(rid)
        if rule is None:
            return
        st = self.st(rid)
        t = dv.now()
        if not rule["enabled"] or st["paused"]:
            self._refresh(rid)
            return
        if st["hold_until"] and t >= st["hold_until"]:
            await self._hold_end(rule)
        if scheduled(rule):
            await self._sched_eval(rule, why)
            return
        if rule.get("window"):
            await self._evaluate_window(rule, why)
            return
        v, bad = self.read(rule)
        if bad == "unknown":
            # Not heard yet (after a restart the bus answers within minutes): wait, the safe state only after the grace.
            st_in = self.hass.states.get(rule["input"]["entity_id"])
            until = (st_in.last_changed.timestamp() if st_in else t) + C("engine", "unknown_grace_s")
            if t < until:
                self._wake(rid, until - t)
                self._refresh(rid)
                return
            bad = "unavailable"
        if bad:
            await self._fault(rule, bad)
            self._refresh(rid)
            return
        if st["fault"]:
            st["fault"] = None
            issues.clear_issue(self.hass, f"rule_fault_{rid}")
            self._log(rid, "input_back", value=v)
            self._logbook(rule, self.say("rules_logbook_back", self.ru))
        st["value"] = v
        ok, cond = self.conditions(rule)
        st["cond"] = cond
        if not ok:
            st["pend"] = None
            if st["active"]:
                await self._go(rule, False, v, "only_if")
        elif not st["active"]:
            if past_on(rule, v):  # type: ignore[arg-type]
                await self._pending(rule, True, v, t)  # type: ignore[arg-type]
            else:
                st["pend"] = None
        elif past_off(rule, v):  # type: ignore[arg-type]
            await self._pending(rule, False, v, t)  # type: ignore[arg-type]
        else:
            st["pend"] = None
        await self._interlock(rule)
        if st.get("interlocked") and not self._interlock_off(rule):
            st["interlocked"] = False
            if st["active"] and not st["hold_until"]:
                self._log(rid, "interlock_back")
                await self._apply(rule, back=False, why="interlock_back")
        self._refresh(rid)
        self._save_soon()

    # --- window rules (window.py): N periods in a row ----------------------------------------------------------------

    def _window_input(self, rule: dict[str, Any]) -> tuple[str, str | None]:
        """The input now: a rule made on «улица» follows the street's sensor (found again, as the season always did)."""
        inp = rule["input"]
        if inp.get("outdoor") and (found := dv.outdoor(self.hass, self.owns)) is not None and found["entity_id"] != inp["entity_id"]:
            try:
                win.source_of(self.hass, found["entity_id"], found["attribute"], rule["window"]["stat"])
                return found["entity_id"], found["attribute"]
            except Invalid:
                pass
        return inp["entity_id"], inp.get("attribute")

    async def window_refresh(self, rid: str, why: str) -> None:
        """The last periods' values from the recorder (in its executor), then the decision; again when the next closes."""
        rule = self.rules.get(rid)
        if rule is None or not rule.get("window"):
            return
        eid, attr = self._window_input(rule)
        try:
            vals, starts, bad = await win.values(self.hass, eid, attr, {**rule["window"], "source": None if eid != rule["input"]["entity_id"] else rule["window"]["source"]})
        except Exception as err:  # noqa: BLE001 — the recorder failing is «no data», never a guess
            _LOGGER.debug("Hauflo rule %s: no statistics: %s", rid, err)
            vals, starts, bad = [], [], "no_statistics"
        self.win[rid] = {"values": vals, "starts": starts, "why": bad, "entity_id": eid, "at": dv.now()}
        await self.evaluate(rid, why)
        self._window_schedule(rid)

    @callback
    def _window_schedule(self, rid: str) -> None:
        if (u := self._win_timers.pop(rid, None)) is not None:
            u()
        rule = self.rules.get(rid)
        if rule is None or not rule.get("window"):
            return
        due = dt_util.as_utc(win.next_start(dt_util.now(), rule["window"]["period"])) + dt.timedelta(seconds=C("window", "compile_delay_s"))

        async def _go(_now: Any) -> None:
            self._win_timers.pop(rid, None)
            await self.window_refresh(rid, "period")

        self._win_timers[rid] = async_track_point_in_utc_time(self.hass, _go, due)

    async def _evaluate_window(self, rule: dict[str, Any], why: str) -> None:
        """ACTIVE after n periods in a row past X, NORMAL after n_back strictly back past X ∓ Y; a period without data
        holds where it is and never triggers; «только если» ending → NORMAL at once (as a live rule)."""
        rid, w = rule["id"], rule["window"]
        st = self.st(rid)
        vals = (self.win.get(rid) or {}).get("values") or []
        st["value"] = vals[-1] if vals else None
        st["periods"] = in_row(rule, vals, past_on)  # periods in a row past X (for HA's automations: how long it holds)
        ok, cond = self.conditions(rule)
        st["cond"] = cond
        need = w["n_back"] if st["active"] else w["n"]
        last = vals[-need:]
        st["no_data"] = len(last) < need or any(v is None for v in last)
        if not ok:
            if st["active"]:
                await self._go(rule, False, st["value"], "only_if")
        elif st["no_data"]:
            pass
        elif not st["active"] and all(past_on(rule, v) for v in last):  # type: ignore[arg-type]
            await self._go(rule, True, st["value"], "window")
        elif st["active"] and all(window_off(rule, v) for v in last):  # type: ignore[arg-type]
            await self._go(rule, False, st["value"], "window")
        elif not st["active"] and st["confirmed"] is None and len(vals) >= w["n_back"] and all(v is not None and window_off(rule, v) for v in vals[-w["n_back"]:]):
            # Normal for n_back periods from the first look (the season's «warm while heating»): said both ways.
            st["confirmed"] = "off"
            self._log(rid, "normal", value=st["value"], why="window")
            if outputs(rule)["suggest_back"]:
                await self._suggest(rule, False)
        self._refresh(rid)
        self._save_soon()

    def _auto_ok(self, rule: dict[str, Any], on: bool) -> bool:
        """A window rule's own actions at most once a period each way (a restart or a re-read never runs them twice)."""
        starts = (self.win.get(rule["id"]) or {}).get("starts") or []
        if not rule.get("window") or not starts:
            return True
        st, key = self.st(rule["id"]), "on" if on else "off"
        if st["auto_at"].get(key) == starts[-1]:
            return False
        st["auto_at"] = {**st["auto_at"], key: starts[-1]}
        return True

    # --- suggestions (output b): a push with «Сделать / Не сейчас» ---------------------------------------------------

    def _sug_acts(self, rule: dict[str, Any], on: bool) -> list[dict[str, Any]]:
        if on:
            return list(rule["actions"])
        return self._reverse(rule) if rule["back"] == "reverse" else [] if rule["back"] == "nothing" else list(rule["back"])

    def _needed(self, acts: list[dict[str, Any]]) -> bool:
        """Something to do: an action not done already (a season not known yet is never suggested — no guess)."""
        for a in acts:
            if a["do"] == "notify":
                continue
            cur = self.season_now() if a["do"] == "season" else self.current(a["entity_id"], a["do"]) if "entity_id" in a else None
            if (a["do"] != "season" or cur is not None) and cur != self._wanted(a):
                return True
        return False

    async def _suggest(self, rule: dict[str, Any], on: bool) -> None:
        st = self.st(rule["id"])
        if not self._needed(self._sug_acts(rule, on)):
            if st["sug"] is not None:
                st["sug"] = None
                self._dismiss(rule)
            return
        st["sug"] = {"on": on, "at": dv.now(), "sent": None, "snooze_until": 0.0}
        await self._suggest_send(rule)

    async def _suggest_due(self, rid: str) -> None:
        """A suggestion waiting for the daily cap, or back after its snooze — while it is still wanted."""
        rule = self.rules.get(rid)
        st = self.st(rid) if rule else None
        sug = st["sug"] if st else None
        if rule is None or sug is None or not rule["enabled"]:
            return
        if sug["on"] != st["active"] or not self._needed(self._sug_acts(rule, sug["on"])):
            st["sug"] = None
            self._dismiss(rule)
            self._save_soon()
            return
        t = dv.now()
        if sug["snooze_until"] and t >= sug["snooze_until"]:
            sug["snooze_until"], sug["sent"] = 0.0, None
        if sug["sent"] is None and not sug["snooze_until"]:
            await self._suggest_send(rule)

    def suggestion(self, rid: str) -> dict[str, Any] | None:
        """The suggestion the family sees now (not snoozed, still wanted), or None."""
        rule = self.rules.get(rid)
        sug = self.st(rid)["sug"] if rule else None
        if rule is None or sug is None or not rule["enabled"] or sug["snooze_until"] > dv.now():
            return None
        acts = self._sug_acts(rule, sug["on"])
        return {"on": sug["on"], "actions": acts, "sent": sug["sent"]} if self._needed(acts) else None

    async def _suggest_send(self, rule: dict[str, Any]) -> None:
        rid = rule["id"]
        st, t = self.st(rid), dv.now()
        day = C("suggest", "day_s")
        if self._quiet(rule):  # kept: the next tick after the window sends it (if still wanted)
            if not st["sug"].get("quiet"):
                st["sug"]["quiet"] = True
                self._log(rid, "quiet_hold", result="suggest")
                self._save_soon()
            return
        sent = [x for x in self._sug_sent.get(rid, []) if t - x < day]
        self._sug_sent[rid] = sent
        if len(sent) >= C("suggest", "per_day"):
            return  # the daily cap: sent with the next tick after it
        on = st["sug"]["on"]
        ru = self.ru
        if (hook := self.ask_text.get((rule.get("name_gen") or {}).get("recipe", ""))) is not None:
            title, message, tag = hook(rule, on, ru)
        else:
            title = self.display_name(rule, ru)
            message = self.say("rules_suggest_on" if on else "rules_suggest_off", ru, why=self.explain(rule, ru).rstrip("."))
            tag = f"{DOMAIN}_rule_suggest_{rid}"
        buttons = [
            {"action": f"{SUGGEST_ACTION}|{rid}|{'on' if on else 'off'}", "title": self.say("rules_act_do", ru)},
            {"action": f"{SUGGEST_ACTION}|{rid}|{LATER}", "title": self.say("rules_act_later", ru)},
        ]
        st["sug"]["sent"] = t
        self._sug_sent[rid] = [*sent, t]
        self._log(rid, "suggest", result="on" if on else "off")
        self._logbook(rule, message)
        self._save_soon()
        if self.ask is not None:
            try:
                await self.ask(title, message, buttons, tag)
            except Exception as err:  # noqa: BLE001 — a phone failing never stops the rules
                _LOGGER.warning("Hauflo rule %s: suggestion not sent: %s", rid, err)

    def _tag(self, rule: dict[str, Any]) -> str:
        if (hook := self.ask_text.get((rule.get("name_gen") or {}).get("recipe", ""))) is not None:
            *_, tag = hook(rule, True, self.ru)
            return tag
        return f"{DOMAIN}_rule_suggest_{rule['id']}"

    @callback
    def _dismiss(self, rule: dict[str, Any]) -> None:
        from homeassistant.components import persistent_notification  # noqa: PLC0415

        persistent_notification.async_dismiss(self.hass, self._tag(rule))

    async def answer_suggestion(self, rid: Any, what: str, who: str | None = None) -> bool:
        """«Сделать» runs the actions of the suggestion there is now (an old push runs nothing); «Не сейчас» snoozes it
        for the rule's snooze_days. Neither changes the rule's entity."""
        rule = self.rules.get(rid) if isinstance(rid, str) else None
        sug = self.st(rid)["sug"] if rule else None
        if rule is None or sug is None:
            return False
        st = self.st(rid)
        if what == LATER:
            sug["snooze_until"] = dv.now() + rule.get("snooze_days", C("suggest", "snooze_days")) * C("suggest", "day_s")
            self._log(rid, "suggest_later", result=who)
            self._dismiss(rule)
            await self._save()
            self._refresh(rid)
            return True
        on = what == STATE_ON
        if what not in (STATE_ON, STATE_OFF) or sug["on"] != on or st["active"] != on or self.suggestion(rid) is None:
            return False
        st["sug"] = None
        self._log(rid, "suggest_done", result=who)
        self._dismiss(rule)
        await self._apply(rule, back=not on, why="suggest")
        if not on:
            st["restore"] = {}
        await self._save()
        self._refresh(rid)
        return True

    async def _on_action(self, event: Event) -> None:
        action = str(event.data.get("action") or "")
        if not action.startswith(SUGGEST_ACTION + "|"):
            return
        rid, _, what = action[len(SUGGEST_ACTION) + 1 :].partition("|")
        user = await self.hass.auth.async_get_user(event.context.user_id) if event.context.user_id else None
        if not await may_edit(self.hass, user):
            return  # a restricted (child) profile's phone, or nobody known: refused, as on the dashboard
        await self.answer_suggestion(rid, what, user.name if user else None)

    async def _hold_end(self, rule: dict[str, Any]) -> None:
        st = self.st(rule["id"])
        rate = st["hold_why"] == "rate"
        st["hold_until"] = st["hold_why"] = None
        issues.clear_issue(self.hass, f"rule_rate_{rule['id']}")
        self._log(rule["id"], "hold_end")
        if st["active"]:
            await self._apply(rule, back=False, why="hold_end")
        elif rate:
            # Held by too many switches through the episode's end: its end is done now (no heater left on).
            await self._apply(rule, back=True, why="hold_end")
            st["restore"] = {}

    async def _pending(self, rule: dict[str, Any], on: bool, v: float, t: float) -> None:
        st = self.st(rule["id"])
        need = (rule["for_min"] if on else rule["back_min"]) * SECONDS_PER_MINUTE
        if st["pend"] is None:
            st["pend"] = t
            self._log(rule["id"], "pending_on" if on else "pending_off", value=v)
        if t - st["pend"] >= need:
            await self._go(rule, on, v, "level")
            return
        self._wake(rule["id"], st["pend"] + need - t)

    # --- by a schedule: a moment fires once --------------------------------------------------------------------------

    def _sun(self, event: str, day: dt.date) -> dt.datetime | None:
        """Sunrise / sunset of a day at the house (HA's location); None in a polar day / night."""
        return get_astral_event_date(self.hass, event, day)

    def next_at(self, rule: dict[str, Any], after: float, *, off: bool = False) -> float | None:
        """The next moment it runs (off: the first moment of the next day it does not run on — said in the log)."""
        nxt = (next_off_day if off else next_fire)(rule["at"], dt_util.as_local(dt_util.utc_from_timestamp(after)), self._sun)
        return nxt.timestamp() if nxt is not None else None

    async def _sched_eval(self, rule: dict[str, Any], why: str) -> None:
        """Due → fired (or said why not); a moment found later than the canon's catch-up (HA was off) → «пропущено»,
        not done late (HA's own time trigger, a KNX clock); then the next moment armed."""
        rid, st, t = rule["id"], self.st(rule["id"]), dv.now()
        due, changed = st.get("next_at"), why != "tick"
        if due is not None and t >= due:
            changed = True
            st["next_at"] = None
            if t - due > C("schedule", "catch_up_s"):
                st["last_at"], st["last_result"] = due, "missed"
                self._log(rid, "missed", planned=due, why=why)
            elif st["hold_until"]:
                st["last_at"], st["last_result"] = due, "skipped"
                self._log(rid, "sched_skip", planned=due, result=st["hold_why"] or "hold")
            else:
                await self._sched_fire(rule, due)
            self._save_soon()
        if st.get("next_at") is None:
            st["next_at"] = self.next_at(rule, max(t, due or t))
            changed = True
            self._save_soon()
        # A day it does not run on: said once, at that day's first moment (not «missed» while HA was off then).
        off = st.get("off_at")
        if off is not None and t >= off:
            changed, st["off_at"] = True, None
            if t - off <= C("schedule", "catch_up_s"):
                self._log(rid, "sched_skip", planned=off, result="weekday")
        if st.get("off_at") is None and (nxt_off := self.next_at(rule, max(t, off or t), off=True)) is not None:
            st["off_at"], changed = nxt_off, True
            self._save_soon()
        if not changed:  # the tick: nothing new (the timer stands)
            return
        if (wake := min((x for x in (st["next_at"], st.get("off_at")) if x is not None), default=None)) is not None:
            self._wake(rid, wake - t)
        self._refresh(rid)

    async def _sched_fire(self, rule: dict[str, Any], planned: float) -> None:
        rid, st = rule["id"], self.st(rule["id"])
        ok, cond = self.conditions(rule)
        st["last_at"] = planned
        if not ok:
            st["last_result"] = "skipped"
            self._log(rid, "sched_skip", planned=planned, result=cond)
            return
        st["last_result"] = "fired"
        self._log(rid, "fired", planned=planned)
        self._logbook(rule, self.say("rules_logbook_fired", self.ru, time=self._hhmm(planned)))
        await self._apply(rule, back=False, why="schedule")

    def _when_text(self, ts: float | None) -> str:
        """«07:00» today, «13.11 07:00» another day."""
        if not ts:
            return "—"
        at = dt_util.as_local(dt_util.utc_from_timestamp(ts))
        today = dt_util.as_local(dt_util.utc_from_timestamp(dv.now())).date()
        return at.strftime("%H:%M") if at.date() == today else at.strftime("%d.%m %H:%M")

    async def _pulses_due(self, *, start: bool = False) -> None:
        """Pulses whose time is over (a timer lost to a restart, a slept loop): ended now; the rest re-armed at start."""
        t = dv.now()
        for eid, p in list(self.pulses.items()):
            if p["until"] <= t:
                await self._pulse_end(eid)
            elif start:
                self._pulse_arm(eid)

    @callback
    def _wake(self, rid: str, delay: float) -> None:
        if (u := self._timers.pop(rid, None)) is not None:
            u()

        async def _go(_now: Any) -> None:
            self._timers.pop(rid, None)
            if rid == TODO:
                await self._run_todo()
            else:
                await self.evaluate(rid, "timer")

        self._timers[rid] = async_call_later(self.hass, max(C("engine", "min_wake_s"), delay), _go)

    async def _go(self, rule: dict[str, Any], on: bool, v: float | None, why: str) -> None:
        rid = rule["id"]
        st = self.st(rid)
        st["active"], st["pend"], st["since"] = on, None, dv.now()
        st["confirmed"] = STATE_ON if on else STATE_OFF
        held = st["hold_until"] is not None
        if not on and st["hold_why"] == "manual":
            # «вручную, до нормы»: the person had this episode — its end is not undone on them.
            st["hold_until"] = st["hold_why"] = None
            self._log(rid, "normal_after_manual", value=v)
        else:
            self._log(rid, "active" if on else "normal", value=v, why=why)
        self._logbook(rule, self.explain(rule, self.ru))
        out = outputs(rule)
        if out["suggest"] and (on or (out["suggest_back"] and why == "window")):
            await self._suggest(rule, on)
        elif st["sug"] is not None:
            st["sug"] = None  # the suggestion of the other way is over
            self._dismiss(rule)
        if held or not out["auto"]:
            return
        if not self._auto_ok(rule, on):
            self._log(rid, "skip", result="period")
            return
        if rule.get("window"):
            self._log(rid, "auto", result=STATE_ON if on else STATE_OFF)
        await self._apply(rule, back=not on, why=why)
        if not on:
            st["restore"] = {}

    async def _fault(self, rule: dict[str, Any], bad: str) -> None:
        """An input that can't be trusted: the purpose's safe state, said once (Repairs + logbook + a frost / leak push)."""
        rid = rule["id"]
        st = self.st(rid)
        st["pend"] = None
        if st["fault"] == bad:
            return
        first = st["fault"] is None
        st["fault"] = bad
        self._cancel_todo(rid)
        self._log(rid, "fault", why=bad)
        issues.raise_rule_issue(self.hass, "rule_fault", rid, name=rule["name"], entity=self.who(rule["input"]["entity_id"]), reason=self.say(f"rules_reason_{bad}", self.ru))
        safe = purpose(rule["purpose"]).get("safe")
        text = self.explain(rule, self.ru)
        self._logbook(rule, text)
        if safe == "off":
            for a in rule["actions"]:
                if powering(a) and self._power(a) is not False and spec_of(a["entity_id"], STATE_OFF):
                    await self._write(rule, {"do": STATE_OFF, "entity_id": a["entity_id"]}, why="safe", force=True)
            st["active"] = False
            st["restore"] = {}
        elif safe == "notify" and first:
            await self._warn(rule, "fault", text)

    async def _interlock(self, rule: dict[str, Any]) -> None:
        """A heater with its block's supply fan off: switched off, said (a Hauflo helper, not a protection)."""
        if not self._interlock_off(rule) or not rule["enabled"]:
            return
        for a in rule["actions"]:
            ts = self.hass.states.get(a.get("entity_id") or "")
            if powering(a) and spec_of(a["entity_id"], STATE_OFF) and ts is not None and ts.state not in (*OFF_STATES, STATE_UNAVAILABLE):
                self.st(rule["id"])["interlocked"] = self.st(rule["id"])["active"]  # back on with the fan, if still wanted
                await self._write(rule, {"do": STATE_OFF, "entity_id": a["entity_id"]}, why="interlock", force=True)
                await self._warn(rule, "interlock", self.say("rules_warn_interlock_off", self.ru, name=rule["name"]))

    def _interlock_off(self, rule: dict[str, Any]) -> bool:
        if not (eid := rule.get("interlock")):
            return False
        s = self.hass.states.get(eid)
        return s is None or s.state != STATE_ON

    # --- writing ----------------------------------------------------------------------------------------------------------

    async def _apply(self, rule: dict[str, Any], *, back: bool, why: str) -> None:
        st = self.st(rule["id"])
        if back:
            acts = self._reverse(rule) if rule["back"] == "reverse" else ([] if rule["back"] == "nothing" else rule["back"])
        else:
            acts = rule["actions"]
            for a in acts if not scheduled(rule) else []:  # a moment has no «back»: nothing to remember
                if restores(a) and restore_key(a) not in st["restore"]:
                    st["restore"][restore_key(a)] = {"do": a["do"], "value": self.current(a["entity_id"], a["do"])}
        if back and why in UNDO_WHYS:
            acts = [a for a in acts if a["do"] != "season"]
        for a in acts:
            if not back and self._interlock_off(rule) and powering(a):
                await self._interlocked(rule, a)
                continue
            if await self._write(rule, a, why=why, back=back) and a.get("repeat"):
                self._repeat_arm(rule["id"], a, back, why, a["repeat"]["count"] - 1)

    @callback
    def _repeat_arm(self, rid: str, a: dict[str, Any], back: bool, why: str, left: int) -> None:
        """«Повторить N раз через M с»: the same action again after its pause — while the rule is there, on and
        unchanged (an edit / delete / switch-off stops what's left, _repeat_stop); each time through _write (its checks)."""
        if left <= 0:
            return
        timers = self._repeat_timers.setdefault(rid, [])
        me: list[CALLBACK_TYPE] = []

        async def _again(_now: Any) -> None:
            if me and me[0] in timers:
                timers.remove(me[0])
            rule = self.rules.get(rid)
            if rule is None or not rule["enabled"] or a not in [*rule["actions"], *(rule["back"] if isinstance(rule["back"], list) else [])]:
                return
            if await self._write(rule, a, why=why, back=back):
                self._repeat_arm(rid, a, back, why, left - 1)

        me.append(async_call_later(self.hass, a["repeat"]["every_s"], _again))
        timers.append(me[0])

    @callback
    def _repeat_stop(self, rid: str) -> None:
        for u in self._repeat_timers.pop(rid, []):
            u()

    async def _interlocked(self, rule: dict[str, Any], a: dict[str, Any]) -> None:
        """A heater's «on» held back while its supply fan is off: said, and done once the fan is back (level-based)."""
        self.st(rule["id"])["interlocked"] = True
        self._log(rule["id"], "skip", entity_id=a.get("entity_id"), result="interlock")
        await self._warn(rule, "interlock", self.say("rules_warn_interlock_skip", self.ru, name=rule["name"]))

    def _reverse(self, rule: dict[str, Any]) -> list[dict[str, Any]]:
        st = self.st(rule["id"])
        out: list[dict[str, Any]] = []
        for a in rule["actions"]:
            if a["do"] == "season":
                out.append({"do": "season", "value": SEASON_MODES[1 - SEASON_MODES.index(a["value"])]})
                continue
            spec = act_spec(a) or {}
            # Kept before 0.34 by the target alone: still read back, for the same action.
            old = st["restore"].get(a.get("entity_id"))
            r = st["restore"].get(restore_key(a)) if a["do"] not in HOUSE_ACTS else None
            r = r or (old if isinstance(old, dict) and old.get("do") == a["do"] else None)
            if spec.get("opposite"):  # on ↔ off, open ↔ close, play ↔ pause
                out.append({"do": spec["opposite"], "entity_id": a["entity_id"]})
            elif spec.get("restore") and r and r.get("value") is not None:
                out.append({"do": a["do"], "entity_id": a["entity_id"], "value": r["value"]})
        return out

    def current(self, eid: str, do: str) -> Any:
        s = self.hass.states.get(eid)
        if s is None or s.state in dv.NOT_A_STATE:
            return None
        spec = spec_of(eid, do)
        read = spec.get("read") if spec else None  # a press, a scene, a toggle: nothing to compare with
        a, off = s.attributes, s.state in OFF_STATES
        full = C("limits", "percent")[1]
        if read is None:
            return None
        if read == "power":
            return STATE_OFF if off else STATE_ON
        if read == "closed":
            return STATE_CLOSED if off else STATE_OPEN
        if read == "percentage":
            p = num(a.get("percentage"))
            return 0 if off else int(p) if p is not None else None
        if read == "position":
            p = num(a.get("current_position"))
            return int(p) if p is not None else full if s.state == STATE_OPEN else 0 if off else None
        if read == "brightness":
            b = num(a.get("brightness"))
            return 0 if off else round(b / BRIGHTNESS_MAX * full) if b is not None else full
        if read == "volume":
            v = num(a.get("volume_level"))
            return round(v / spec["scale"]) if v is not None else None
        v = s.state if read == "state" else a.get(read)
        return num(v) if spec.get("value") in NUMERIC else v

    @staticmethod
    def _wanted(a: dict[str, Any]) -> Any:
        spec = act_spec(a) or {}
        return spec["want"] if "want" in spec else a.get("value")

    @staticmethod
    def _power(a: dict[str, Any], state: str | None = None) -> bool | None:
        """Does the action leave the target on (True), off (False) — for the minimum on / off times."""
        p = (act_spec(a) or {}).get("power")
        if p in (STATE_ON, STATE_OFF):
            return p == STATE_ON
        if p == "value":
            v = a.get("value")
            return v != HVACMode.OFF if isinstance(v, str) else bool(v)
        if p == "toggle" and state is not None and state != STATE_UNKNOWN:
            return state in OFF_STATES
        return None

    def _controlled_by_higher(self, rule: dict[str, Any], eid: str, attr: str) -> dict[str, Any] | None:
        """An ACTIVE rule of a higher priority holding the target — of the same one too, for a schedule's moment."""
        mine = PRIORITY[rule["priority"]]
        for o in self.rules.values():
            p = PRIORITY[o["priority"]]
            if o["id"] != rule["id"] and o["enabled"] and (p > mine or (scheduled(rule) and p == mine)) and self.st(o["id"])["active"] and (eid, attr) in targets_of(o):
                return o
        return None

    async def _write(self, rule: dict[str, Any], a: dict[str, Any], *, why: str, back: bool = False, force: bool = False) -> bool:
        """One action, through the helpers. `force`: a safe-state / interlock «off» — not held by the minimum on time."""
        rid, h = rule["id"], C("helpers")
        if a["do"] == "notify":
            if not back:
                await self._notify(rule, a)
            return True
        if a["do"] == "season":
            return await self._season(rule, a, why)
        eid = a["entity_id"]
        domain = eid.split(".")[0]
        spec = spec_of(eid, a["do"])
        if spec is None or not may_change(self.hass, eid, self.owns, self.own_raw):
            self._log(rid, "skip", entity_id=eid, result="not_allowed")
            return False
        s = self.hass.states.get(eid)
        if s is None or s.state == STATE_UNAVAILABLE:  # «unknown» (a write-only actuator not heard yet) is still written
            self._log(rid, "skip", entity_id=eid, result="unavailable")
            return False
        if (o := self._controlled_by_higher(rule, eid, slot_of(a))) is not None:
            self._log(rid, "skip", entity_id=eid, result=f"priority:{o['id']}")
            return False
        want = self._wanted(a)
        cur = self.current(eid, a["do"])
        if cur is not None and cur == want:
            self._log(rid, "already", entity_id=eid, do=a["do"], value=want)
            return True
        t = dv.now()
        power = self._power(a, s.state)
        meta = purpose(rule["purpose"])
        if power is not None and not force and s.state != STATE_UNKNOWN:
            on_now = s.state not in OFF_STATES
            gap = meta["min_on_s"] if (on_now and not power) else meta["min_off_s"] if (not on_now and power) else 0
            # The last real switch, kept over a restart (HA's last_changed restarts with HA); unknown — HA's.
            since = self.switched.get(eid, s.last_changed.timestamp())
            if gap and t - since < gap:
                return self._defer(rule, a, since + gap, back, "min_time")
        if domain == "climate" and not force and eid in self.climate_at and t - self.climate_at[eid] < h["climate_gap_s"]:
            return self._defer(rule, a, self.climate_at[eid] + h["climate_gap_s"], back, "climate_gap")
        recent = [x for x in self.writes.get(eid, []) if t - x < h["rate_window_s"]]
        self.writes[eid] = recent
        if len(recent) >= h["rate_max"] and a["do"] != "close":
            await self._rate_hold(rule, eid, recent[0] + h["rate_window_s"])
            return False
        while self._bus and t - self._bus[0] >= h["bus_window_s"]:
            self._bus.popleft()
        if len(self._bus) >= h["bus_cap"] and a["do"] != "close":
            return self._defer(rule, a, self._bus[0] + h["bus_window_s"], back, "bus")
        service, data = _service(a)
        ctx = Context()
        self._ctx.append(ctx.id)
        self._sent[eid] = (t, want)
        self._bus.append(t)
        self.writes[eid] = [*recent, t]
        if domain == "climate":
            self.climate_at[eid] = t
        entry = {"entity_id": eid, "do": a["do"], "value": a.get("value"), "why": why}
        try:
            await self.hass.services.async_call(domain, service, {"entity_id": eid, **data}, blocking=True, context=ctx)
        except Exception as err:  # noqa: BLE001 — one device failing never stops the others
            self._log(rid, "error", **entry, result=str(err)[: int(C("engine", "error_len"))])
            _LOGGER.warning("Hauflo rule %s: %s %s failed: %s", rid, service, eid, err)
            return False
        self._log(rid, "write", **entry, result="ok")
        if spec.get("value") == "seconds":  # a pulse: off again after its seconds, said in the log
            self.pulses[eid] = {"rule": rid, "until": t + a["value"]}
            self._pulse_arm(eid)
        self._save_soon()
        return True

    # --- a pulse: on for N seconds (a button wired to a switch actuator, a valve drive's step) ----------------------------

    @callback
    def _pulse_arm(self, eid: str) -> None:
        if (u := self._pulse_timers.pop(eid, None)) is not None:
            u()
        p = self.pulses[eid]

        async def _end(_now: Any) -> None:
            self._pulse_timers.pop(eid, None)
            await self._pulse_end(eid)

        self._pulse_timers[eid] = async_call_later(self.hass, max(0, p["until"] - dv.now()), _end)

    async def _pulse_end(self, eid: str) -> None:
        """The pulse's «off», once its time is over (also after a restart): not held by the minimum times (the pulse
        itself was checked against them when saved) — a target someone switched off already is left as is."""
        p = self.pulses.pop(eid, None)
        if (u := self._pulse_timers.pop(eid, None)) is not None:
            u()
        if p is None:
            return
        rid = p["rule"] if p["rule"] in self.rules else None
        s = self.hass.states.get(eid)
        if s is None or s.state in (*OFF_STATES, *dv.NOT_A_STATE) or not self.owns(eid):
            self._save_soon()
            return
        ctx = Context()
        self._ctx.append(ctx.id)
        self._sent[eid] = (dv.now(), STATE_OFF)
        entry = {"entity_id": eid, "do": STATE_OFF, "why": "pulse"}
        try:
            await self.hass.services.async_call(eid.split(".")[0], "turn_off", {"entity_id": eid}, blocking=True, context=ctx)
        except Exception as err:  # noqa: BLE001
            if rid:
                self._log(rid, "error", **entry, result=str(err)[: int(C("engine", "error_len"))])
            _LOGGER.warning("Hauflo pulse %s: off failed: %s", eid, err)
            return
        if rid:
            self._log(rid, "write", **entry, result="ok")
        self._save_soon()

    async def _season(self, rule: dict[str, Any], a: dict[str, Any], why: str) -> bool:
        """The house's «Зима / Лето» (season.py does the switching, never switches a climate on)."""
        rid = rule["id"]
        if self.season_set is None:
            self._log(rid, "skip", entity_id=SEASON_TARGET, result="not_allowed")
            return False
        if self.season_now() == a["value"]:
            self._log(rid, "already", entity_id=SEASON_TARGET, do="season", value=a["value"])
            return True
        try:
            # season.py may take it later (its automatic switch: once a day, in the morning) — then it says so.
            done = await self.season_set(a["value"], why)
        except Exception as err:  # noqa: BLE001
            self._log(rid, "error", entity_id=SEASON_TARGET, do="season", value=a["value"], why=why, result=str(err)[: int(C("engine", "error_len"))])
            return False
        self._log(rid, "write", entity_id=SEASON_TARGET, do="season", value=a["value"], why=why, result=done if isinstance(done, str) else "ok")
        return True

    async def _rate_hold(self, rule: dict[str, Any], eid: str, until: float) -> None:
        rid = rule["id"]
        st = self.st(rid)
        st["hold_until"], st["hold_why"] = until, "rate"
        self._log(rid, "hold", entity_id=eid, result="rate", until=until)
        issues.raise_rule_issue(self.hass, "rule_rate", rid, name=rule["name"], entity=self.who(eid))
        await self._warn(rule, "rate", self.say("rules_warn_rate", self.ru, name=rule["name"], entity=self.who(eid), n=int(C("helpers", "rate_max")), time=self._hhmm(until)))
        self._refresh(rid)

    def _defer(self, rule: dict[str, Any], a: dict[str, Any], due: float, back: bool, why: str) -> bool:
        self.todo = [x for x in self.todo if not (x["rule"] == rule["id"] and x["action"].get("entity_id") == a.get("entity_id"))]
        self.todo.append({"rule": rule["id"], "action": a, "due": due, "back": back, "why": why})
        self._log(rule["id"], "wait", entity_id=a.get("entity_id"), result=why, until=due)
        self._wake(TODO, due - dv.now())
        self._save_soon()
        return False

    def _cancel_todo(self, rid: str) -> None:
        self.todo = [x for x in self.todo if x["rule"] != rid]

    async def _run_todo(self) -> None:
        t = dv.now()
        due = [x for x in self.todo if x["due"] <= t]
        self.todo = [x for x in self.todo if x["due"] > t]
        for x in due:
            rule = self.rules.get(x["rule"])
            if rule is None:
                continue
            st = self.st(rule["id"])
            # Still wanted: the rule is where it was when the write waited, and nobody holds it.
            if (st["active"] == x["back"] and not scheduled(rule)) or st["hold_until"] or st["paused"] or not rule["enabled"]:
                continue
            if not x["back"] and self._interlock_off(rule) and powering(x["action"]):
                await self._interlocked(rule, x["action"])
                continue
            await self._write(rule, x["action"], why=x["why"], back=x["back"])
        if self.todo:
            self._wake(TODO, min(x["due"] for x in self.todo) - t)

    # --- quiet hours: a push of a rule is held, not lost ---------------------------------------------------------------

    def _bypass(self, rule: dict[str, Any]) -> bool:
        """Never silenced: the rule's own «присылать и ночью», else its class (safety: leak, frost…) — as the phones'
        critical alerts pass «Do not disturb»."""
        v = rule.get("quiet_ok")
        return v if isinstance(v, bool) else rule["priority"] in C("quiet", "bypass_priorities")

    def _quiet(self, rule: dict[str, Any]) -> bool:
        return not self._bypass(rule) and quiet.inside(self.quiet, dt_util.utc_from_timestamp(dv.now()))

    def _quiet_hold(self, rule: dict[str, Any], item: dict[str, Any]) -> None:
        """One held push per kind (a repeat replaces it); it is told once when the window ends, if the cause is still there."""
        st = self.st(rule["id"])
        st["held"] = [h for h in st["held"] if h["key"] != item["key"]] + [{**item, "at": dv.now()}]
        self._log(rule["id"], "quiet_hold", result=item["k"])
        self._save_soon()

    def _still(self, rule: dict[str, Any], h: dict[str, Any]) -> bool:
        st = self.st(rule["id"])
        if not rule["enabled"]:
            return False
        if h["k"] == "notify":
            return bool(st["active"]) or scheduled(rule)  # a moment's push: it happened
        return st["fault"] is not None if h["kind"] == "fault" else self._interlock_off(rule) if h["kind"] == "interlock" else True

    async def _release_held(self) -> None:
        for rid, rule in list(self.rules.items()):
            st = self.st(rid)
            if not st["held"] or self._quiet(rule):
                continue
            held, st["held"] = st["held"], []
            for h in held:
                if not self._still(rule, h):
                    self._log(rid, "quiet_drop", result=h["k"])
                    continue
                self._log(rid, "quiet_release", result=h["k"])
                if h["k"] == "notify":
                    await self._notify(rule, h["a"], late=True)
                else:
                    await self._warn(rule, h["kind"], h["text"], late=True)
            self._save_soon()

    async def set_quiet(self, raw: Any) -> dict[str, Any]:
        self.quiet = quiet.clean(raw, self.quiet)
        await self._save()
        await self._release_held()
        return self.quiet_out()

    def quiet_out(self) -> dict[str, Any]:
        return {**self.quiet, "default": quiet.default(), "bypass_priorities": C("quiet", "bypass_priorities")}

    async def _notify(self, rule: dict[str, Any], a: dict[str, Any], *, late: bool = False) -> None:
        if not late and self._quiet(rule):
            self._quiet_hold(rule, {"key": f"notify:{a['text']}", "k": "notify", "a": a})
            return
        self._log(rule["id"], "notify", result=a["to"])
        if self.tell is not None:
            try:
                await self.tell(rule["name"], a["text"], rule.get("user_id") if a["to"] == "me" else None)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Hauflo rule %s: notify failed: %s", rule["id"], err)

    async def _warn(self, rule: dict[str, Any], kind: str, text: str, *, late: bool = False) -> None:
        if not late and self._quiet(rule):
            self._quiet_hold(rule, {"key": f"warn:{kind}", "k": "warn", "kind": kind, "text": text})
            return
        key = f"{rule['id']}:{kind}"
        t = dv.now()
        if key in self._warned and t - self._warned[key] < C("engine", "warn_gap_s"):
            return
        self._warned[key] = t
        self._log(rule["id"], "warn", result=kind)
        self._logbook(rule, text)
        if self.tell is not None:
            try:
                await self.tell(rule["name"], text, None)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Hauflo rule %s: warning not sent: %s", rule["id"], err)

    # --- a person changed a target ----------------------------------------------------------------------------------------

    @callback
    def _target_changed(self, event: Event) -> None:
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or old is None or new.state in dv.NOT_A_STATE or old.state in dv.NOT_A_STATE:
            return
        eid, ctx = new.entity_id, new.context
        if (new.state in OFF_STATES) != (old.state in OFF_STATES):  # a real switch (anyone's): the min times count from it
            self.switched[eid] = new.last_changed.timestamp()
            self._save_soon()
        if ctx.id in self._ctx or (ctx.parent_id and ctx.parent_id in self._ctx):
            return
        for rid, rule in list(self.rules.items()):
            # A heater switched on (by anyone) while its supply fan is off: off again, said.
            if rule.get("interlock") and new.state not in OFF_STATES and any(a.get("entity_id") == eid for a in rule["actions"]):
                self.hass.async_create_task(self._interlock(rule), "hauflo interlock")
            st = self.st(rid)
            if not rule["enabled"] or st["paused"] or not st["active"] or st["hold_why"] == "manual":
                continue
            for a in rule["actions"]:
                if a.get("entity_id") != eid or (act_spec(a) or {}).get("value") == "seconds":  # a pulse ends by itself
                    continue
                cur = self.current(eid, a["do"])
                if cur is None or cur == self._wanted(a):
                    continue
                sent = self._sent.get(eid)
                if sent is not None and dv.now() - sent[0] <= C("engine", "echo_s") and cur == sent[1]:
                    continue  # our own command's echo from the bus
                self._hold(rule, eid, "user" if ctx.user_id else "bus")
                break

    @callback
    def _hold(self, rule: dict[str, Any], eid: str, who: str) -> None:
        rid, h = rule["id"], C("helpers")
        st = self.st(rid)
        t = dv.now()
        st["hold_until"], st["hold_why"] = t + h["manual_hold_s"], "manual"
        st["holds"] = [x for x in st["holds"] if t - x < h["pause_window_s"]] + [t]
        self._cancel_todo(rid)
        self._log(rid, "manual", entity_id=eid, result=who, until=st["hold_until"])
        if len(st["holds"]) >= h["pause_holds"]:
            st["paused"] = True
            self._log(rid, "paused")
            issues.raise_rule_issue(self.hass, "rule_paused", rid, name=rule["name"], entity=self.who(eid))
            self.hass.async_create_task(self._warn(rule, "paused", self.say("rules_warn_paused", self.ru, name=rule["name"], entity=self.who(eid))), "hauflo rule warn")
        self._logbook(rule, self.explain(rule, self.ru))
        self._refresh(rid)
        self._save_soon()

    # --- what people see ---------------------------------------------------------------------------------------------------

    @staticmethod
    def _hhmm(ts: float | None) -> str:
        return dt_util.as_local(dt_util.utc_from_timestamp(ts)).strftime("%H:%M") if ts else "—"

    def status(self, rid: str) -> str:
        rule, st = self.rules[rid], self.st(rid)
        if not rule["enabled"]:
            return "off"
        for key in ("paused", "fault"):
            if st[key]:
                return key
        if st["hold_until"]:
            return "hold"
        if scheduled(rule):
            return "scheduled" if st.get("next_at") else "no_next"
        if st["active"]:
            return "active"
        if st.get("cond"):
            return "waiting"
        if rule.get("window") and st.get("no_data"):
            return "no_data"
        return "pending" if st["pend"] else "normal"

    def threshold(self, rule: dict[str, Any], ru: bool) -> str:
        u, w = rule["input"].get("unit"), rule["when"]
        if w in ("below", "above"):
            return self.say(f"rules_thr_{w}", ru, x=_fmt(rule["x"], u))
        if w in ("outside", "between"):
            return self.say(f"rules_thr_{w}", ru, lo=_fmt(rule["lo"], None), hi=_fmt(rule["hi"], u))
        return self.say(f"rules_word_{w}", ru)

    def back_threshold(self, rule: dict[str, Any], ru: bool) -> str:
        """When an ACTIVE rule goes back — the threshold with the hysteresis (past_off), said."""
        u, w, y = rule["input"].get("unit"), rule["when"], rule.get("y", 0)
        if w == "below":
            return self.say("rules_thr_min", ru, x=_fmt(rule["x"] + y, u))
        if w == "above":
            return self.say("rules_thr_max", ru, x=_fmt(rule["x"] - y, u))
        if w == "outside":
            return self.say("rules_thr_between", ru, lo=_fmt(rule["lo"] + y, None), hi=_fmt(rule["hi"] - y, u))
        if w == "between":
            return self.say("rules_thr_outside", ru, lo=_fmt(rule["lo"] - y, None), hi=_fmt(rule["hi"] + y, u))
        return self.say(f"rules_word_{STATE_OFF if w == STATE_ON else STATE_ON}", ru)

    def explain(self, rule: dict[str, Any], ru: bool) -> str:
        """The rule now, one sentence (RU / EN, translations rules_explain_…)."""
        st = self.st(rule["id"])
        state = self.status(rule["id"])
        if scheduled(rule):
            return self._explain_at(rule, state, ru)
        inp = rule["input"]
        s = self.hass.states.get(inp["entity_id"])
        label = (s.attributes.get("friendly_name") if s else None) or inp["entity_id"]
        if inp.get("forecast"):
            label = self.say("rules_forecast", ru, label=label)
        if rule["when"] in ("on", "off"):
            value = self.say(f"rules_word_{STATE_ON if st.get('value') else STATE_OFF}", ru)
        else:
            value = _fmt(st.get("value"), inp.get("unit"))
        ph = {"label": label, "value": value, "threshold": self.threshold(rule, ru), "back": self.back_threshold(rule, ru), "time": self._hhmm(st["hold_until"] or st["since"])}
        if rule.get("window") and state in ("active", "normal", "no_data"):
            return self._explain_window(rule, state, ru, ph)
        if state == "fault":
            key = "rules_explain_fault_off" if purpose(rule["purpose"]).get("safe") == "off" else "rules_explain_fault_wait"
            return self.say(key, ru, **ph, reason=self.say(f"rules_reason_{st['fault']}", ru))
        if state == "hold":
            return self.say(f"rules_explain_hold_{st['hold_why']}", ru, **ph)
        if state == "active" and self._interlock_off(rule):  # ACTIVE with the fan off: the heater is held back / off
            return self.say("rules_explain_interlocked", ru, **ph)
        if state == "active" and (due := [x["due"] for x in self.todo if x["rule"] == rule["id"] and not x["back"]]):
            return self.say("rules_explain_deferred", ru, **{**ph, "time": self._hhmm(min(due))})
        if st["pend"] and state in ("pending", "active"):
            need = (rule["back_min"] if st["active"] else rule["for_min"]) * SECONDS_PER_MINUTE
            left = max(0, round((st["pend"] + need - dv.now()) / SECONDS_PER_MINUTE))
            return self.say("rules_explain_pending_off" if st["active"] else "rules_explain_pending_on", ru, **ph, minutes=left)
        return self.say(f"rules_explain_{state}", ru, **ph)

    def _explain_at(self, rule: dict[str, Any], state: str, ru: bool) -> str:
        """«Следующий запуск: 07:00. Последний: сработало в 07:00.»"""
        st = self.st(rule["id"])
        ph = {"time": self._hhmm(st["hold_until"]), "next": self._when_text(st.get("next_at"))}
        if state in ("off", "paused"):
            head = self.say(f"rules_explain_{state}", ru, **ph)
        elif state == "hold":
            head = self.say(f"rules_explain_hold_{st['hold_why']}", ru, **ph)
        else:
            head = self.say(f"rules_explain_{state}", ru, **ph)
        if st.get("last_result") in ("fired", "skipped", "missed"):
            head += " " + self.say(f"rules_explain_last_{st['last_result']}", ru, time=self._when_text(st["last_at"]))
        return head

    def window_words(self, rule: dict[str, Any], ru: bool) -> str:
        """«среднее за день» / «daily mean»."""
        w = rule["window"]
        return self.say(f"rules_window_{w['stat']}_{w['period']}", ru)

    def _explain_window(self, rule: dict[str, Any], state: str, ru: bool, ph: dict[str, Any]) -> str:
        st, w = self.st(rule["id"]), rule["window"]
        vals = (self.win.get(rule["id"]) or {}).get("values") or []
        n = w["n_back"] if st["active"] else w["n"]
        u, wh, y = rule["input"].get("unit"), rule["when"], rule["y"]
        # Back strictly past X ∓ Y (window_off) — said so, not «or more».
        if wh in ("below", "above"):
            back = self.say(f"rules_thr_{'above' if wh == 'below' else 'below'}", ru, x=_fmt(rule["x"] + (y if wh == "below" else -y), u))
        else:
            inner = wh == "outside"
            back = self.say(f"rules_thr_{'between' if inner else 'outside'}", ru, lo=_fmt(rule["lo"] + (y if inner else -y), None), hi=_fmt(rule["hi"] - (y if inner else -y), u))
        ph = {**ph, "back": back, "stat": self.window_words(rule, ru), "held": st.get("periods", 0), "n": n, "missing": sum(v is None for v in vals[-n:]) + max(0, n - len(vals))}
        return self.say(f"rules_explain_window_{state}", ru, **ph)

    def _log(self, rid: str, event: str, **kw: Any) -> None:
        st = self.st(rid)
        item = {"at": dv.now(), "event": event, **{k: v for k, v in kw.items() if v is not None}}
        st["log"] = [*st["log"], item][-int(C("engine", "log_size")) :]

    def _logbook(self, rule: dict[str, Any], message: str) -> None:
        ent = self.binaries.get(rule["id"])
        data: dict[str, Any] = {"name": rule["name"], "message": message, "domain": DOMAIN}
        if ent is not None and ent.entity_id:
            data["entity_id"] = ent.entity_id
        self.hass.bus.async_fire("logbook_entry", data)

    @callback
    def _refresh(self, rid: str) -> None:
        for e in (self.binaries.get(rid), self.switches.get(rid)):
            if e is not None:
                e.refresh()

    # --- entities ------------------------------------------------------------------------------------------------------

    def _make_derived(self) -> None:
        for did in [d for d in self.derived if d not in self.derived_cfg]:
            self.derived.pop(did)
        for did, cfg in self.derived_cfg.items():
            if did in self.derived:
                self.derived[did].cfg = cfg
            else:
                self.derived[did] = dv.Derived(self.hass, cfg)
            self.derived[did].update()

    @callback
    def sync_entities(self) -> None:
        reg = er.async_get(self.hass)
        for ents, ids in ((self.binaries, self.rules), (self.switches, self.rules), (self.sensors, self.derived_cfg)):
            for key in [k for k in ents if k not in ids]:
                ent = ents.pop(key)
                if ent.entity_id and reg.async_get(ent.entity_id):
                    reg.async_remove(ent.entity_id)
        for rid, rule in self.rules.items():
            for ents, add, cls in ((self.binaries, self.add_binary, RuleActive), (self.switches, self.add_switches, RuleSwitch)):
                if rid in ents:
                    ents[rid].refresh()
                elif add is not None:
                    ents[rid] = e = cls(self, rule)
                    add([e])
        for did in self.derived_cfg:
            if did in self.sensors:
                self.sensors[did].refresh()
            elif self.add_sensors is not None:
                self.sensors[did] = d = DerivedSensor(self, did)
                self.add_sensors([d])

    # --- the API --------------------------------------------------------------------------------------------------------

    def hint(self, pname: str) -> dict[str, str]:
        if not purpose(pname).get("hint"):
            return {}
        return {"hint": self.say(f"rules_hint_{pname}", True), "hint_en": self.say(f"rules_hint_{pname}", False)}

    def display_name(self, rule: dict[str, Any], ru: bool) -> str:
        """The name in a language: a generated one from its recipe title + room (+ number), a typed one as typed."""
        gen = rule.get("name_gen")
        if not gen:
            return rule["name"]
        parts = [self.say(f"rules_recipe_{gen['recipe']}", ru)]
        if gen.get("room"):
            parts.append(gen["room"])
        text = " · ".join(parts)
        return f"{text} {gen['n']}"[: C("limits", "name_len")] if gen.get("n", 1) > 1 else text[: C("limits", "name_len")]

    def unique_name(self, rule: dict[str, Any]) -> None:
        """Two rules never share a name: a generated one gets the next number, a typed one «(2)»."""
        taken = {self.display_name(r, True).casefold() for k, r in self.rules.items() if k != rule["id"]}
        gen = rule.get("name_gen")
        if gen:
            while self.display_name(rule, True).casefold() in taken and gen["n"] < C("limits", "rules"):
                gen["n"] += 1
            rule["name"] = self.display_name(rule, self.ru)
            return
        base, k = rule["name"], 1
        while rule["name"].casefold() in taken:
            k += 1
            suffix = f" ({k})"
            rule["name"] = base[: C("limits", "name_len") - len(suffix)] + suffix

    def rule_out(self, rid: str, full: bool, tunnel: bool = False) -> dict[str, Any]:
        rule, st = self.rules[rid], self.st(rid)
        b, s = self.binaries.get(rid), self.switches.get(rid)
        out: dict[str, Any] = {
            "id": rid,
            "name": rule["name"],
            "name_ru": self.display_name(rule, True),
            "name_en": self.display_name(rule, False),
            "enabled": rule["enabled"],
            "state": self.status(rid),
            "active": st["active"],
            "value": st.get("value"),
            "unit": rule["input"].get("unit") if "input" in rule else None,
            "why": self.explain(rule, True),
            "why_en": self.explain(rule, False),
            "hold_until": st["hold_until"],
            "since": st["since"],
            "client": rule["client"],
            "client_can": client_can(rule),
            "purpose": rule["purpose"],
            "priority": rule.get("priority"),
            "owner": rule.get("owner", "integrator"),
            "entities": {"active": b.entity_id if b else None, "switch": s.entity_id if s else None},
            **{k: rule[k] for k in ("when", "x", "lo", "hi", "y", "at") if k in rule},
            **({k: st.get(k) for k in ("next_at", "last_at", "last_result")} if scheduled(rule) else {}),
            **self.hint(rule["purpose"]),
            "outputs": outputs(rule),
            "quiet_bypass": self._bypass(rule),
        }
        if rule.get("window"):
            data = self.win.get(rid) or {}
            k = int(C("window", "values_out"))
            out.update(window=rule["window"], values=(data.get("values") or [])[-k:], starts=(data.get("starts") or [])[-k:], periods=st.get("periods", 0), no_data=bool(st.get("no_data")))
        if (sug := self.suggestion(rid)) is not None:
            out["suggestion"] = {"on": sug["on"], "sent": sug["sent"]}
        elif st["sug"] is not None and st["sug"]["snooze_until"]:
            out["suggestion"] = {"on": st["sug"]["on"], "snoozed_until": st["sug"]["snooze_until"]}
        if full:
            out["rule"] = rule
            out["log"] = st["log"][-int(C("engine", "log_out")) :]
        if tunnel and self.cloud_hidden(rule) is not None:
            out = _redact(out)
        return out

    def recipes(self) -> list[dict[str, Any]]:
        return [{**r, "title": self.say(f"rules_recipe_{r['id']}", True), "title_en": self.say(f"rules_recipe_{r['id']}", False), **self.hint(r["purpose"])} for r in C("recipes")]

    def links(self) -> list[dict[str, Any]]:
        """Recipes another part of Hauflo already makes (the silent sensor: the rule hints) — shown, not duplicated."""
        return [{**r, "title": self.say(f"rules_recipe_{r['id']}", True), "title_en": self.say(f"rules_recipe_{r['id']}", False), "text": self.say(f"rules_link_{r['id']}", True), "text_en": self.say(f"rules_link_{r['id']}", False)} for r in C("links")]

    def schedule_recipes(self) -> list[dict[str, Any]]:
        return [{**r, "title": self.say(f"rules_recipe_{r['id']}", True), "title_en": self.say(f"rules_recipe_{r['id']}", False)} for r in C("schedule_recipes")]

    def targets(self, tunnel: bool = False, client: bool = False) -> list[dict[str, Any]]:
        """What a rule may change (the app's picker: room → device → action → value): Hauflo's own of the canon's
        domains (may_change), with the attributes its actions take their options / bounds from. «dos» — exactly what a
        save takes (clean_action): the canon's actions the entity supports (supports), a client's only the client's; a
        device with nothing to do is not offered. «room» — the room's name (HA's area registry), «area» its id."""
        table = C("actions_by_domain")
        acts = set(C("actions", "client_acts")) if client else None
        domains = [d for d in table if not client or d in C("actions", "client_domains")]
        out: list[dict[str, Any]] = []
        for s in self.hass.states.async_all(domains):
            eid, domain = s.entity_id, s.domain
            if not may_change(self.hass, eid, self.owns, self.own_raw) or (client and not client_class_ok(self.hass, eid)):
                continue
            dos = [do for do, spec in table[domain].items() if supports(s, spec) and (acts is None or do in acts)]
            if not dos:
                continue
            names = {n for spec in table[domain].values() for n in (spec.get("options"), spec.get("step"), *(spec.get("bounds") or ()))} - {None}
            seen = not tunnel or self.cloud_reads(eid, None)
            out.append({
                "entity_id": eid,
                "domain": domain,
                "name": label(self.hass, eid, s),  # what a person calls it, never the entity id
                "area": entity_room(self.hass, eid),
                "room": room_name(self.hass, eid),
                "state": s.state if seen else None,
                "attrs": {n: s.attributes[n] for n in sorted(names) if n in s.attributes},
                "dos": dos,
                # «сейчас: …» by action (a fan's speed, a select's option); none for a press / a scene / a toggle.
                "now": {do: self.current(eid, do) for do in dos if seen and table[domain][do].get("read")},
            })
        return sorted(out, key=lambda x: (x["room"] is None, x["room"] or "", x["name"]))  # «без комнаты» last

    def answer(self, admin: bool, tunnel: bool = False) -> dict[str, Any]:
        visible = [rid for rid, r in self.rules.items() if admin or r["client"].get("visible", True)]
        derived = []
        if admin:
            hidden = self.cloud_hidden_entities() if tunnel else set()
            for did, cfg in self.derived_cfg.items():
                d, s = self.derived.get(did), self.sensors.get(did)
                item = {**cfg, "entity_id": s.entity_id if s else derived_entity_id(cfg), "value": d.value if d else None, "unit": d.unit if d else None, "why": d.why if d else None}
                derived.append({**item, "value": None, "why": None} if item["entity_id"] in hidden else item)
        return {
            "rules": [self.rule_out(rid, admin or self.rules[rid].get("owner") == "client", tunnel) for rid in visible],
            "derived": derived,
            "quiet": self.quiet_out(),
            "outdoor": dv.outdoor(self.hass, self.owns),
            "recipes": self.recipes() if admin else [],
            "schedule_recipes": self.schedule_recipes() if admin else [],
            "targets": self.targets(tunnel, client=not admin),
            # What this agent can do (the app asks an older one to be updated before a schedule / a new action is saved).
            "features": list(FEATURES),
            "links": self.links() if admin else [],
            **({"bus_reads": self.bus_reads_out()} if admin else {}),
            "limits": {
                **{k: C("limits", k) for k in ("rules", "actions", "conditions", "for_min")},
                "window": {k: C("window", k) for k in ("stats", "periods", "n")},
                "thresholds": C("thresholds"),
                "snooze_days": C("suggest", "snooze_bounds"),
                "schedule": C("schedule"),
                "pulse_s": C("actions", "pulse_s"),
                "text_len": C("actions", "text_len"),
                "repeat": {k: C("actions", "repeat", k) for k in ("count", "every_s", "acts")},
            },
            "actions_by_domain": C("actions_by_domain"),
            "client_actions": {"acts": C("actions", "client_acts"), "domains": C("actions", "client_domains")},
        }

    def log_of(self, rid: str, tunnel: bool = False) -> list[dict[str, Any]]:
        if rid not in self.rules:
            return []
        log = list(self.st(rid)["log"])
        return _redact({"log": log})["log"] if tunnel and self.cloud_hidden(self.rules[rid]) is not None else log

    async def save(self, raw: Any, *, client: bool, user_id: str | None, tunnel: bool = False) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise Invalid("bad", "rules_bad_object")
        if client and raw.get("id") in self.rules and self.rules[raw["id"]].get("owner") != "client":
            raise Invalid("not_allowed", "rules_client_integrator_rule")
        if user_id and "user_id" not in raw:
            raw = {**raw, "user_id": user_id}
        rule = clean_rule(raw, self._env(client))
        if tunnel:
            self._cloud_check(rule)
        check_house(rule, [r for k, r in self.rules.items() if k != rule["id"]], self.derived_sources)
        self.unique_name(rule)
        old = self.rules.get(rule["id"])
        if old is not None and {k: v for k, v in old.items() if k != "outputs"} == {k: v for k, v in rule.items() if k != "outputs"}:
            # Only what it does on a change (suggest / auto): the rule goes on where it is — no restart, nothing undone.
            return await self.set_outputs(rule["id"], rule.get("outputs"), tunnel)
        if old is not None and self.st(rule["id"])["active"]:
            # A changed rule starts over from NORMAL, its own actions undone first (no orphan heater left on).
            await self._go(old, False, None, "changed")
        log = self.st(rule["id"])["log"]
        self.rules[rule["id"]] = rule
        self.state[rule["id"]] = {**_blank(), "log": log}
        self._cancel_todo(rule["id"])
        for kind in issues.RULE_KINDS:
            issues.clear_issue(self.hass, f"{kind}_{rule['id']}")
        self._log(rule["id"], "saved")
        await self._save()
        self.sync_entities()
        self._listen()
        self.win.pop(rule["id"], None)
        if rule.get("window"):
            await self.window_refresh(rule["id"], "saved")
        else:
            self._window_schedule(rule["id"])
            await self.evaluate(rule["id"], "saved")
        return self.rule_out(rule["id"], True, tunnel)

    def _rule(self, rid: Any) -> dict[str, Any]:
        rule = self.rules.get(rid) if isinstance(rid, str) else None
        if rule is None:
            raise Invalid("no_rule", "rules_no_rule")
        return rule

    async def delete(self, rid: Any) -> None:
        rule = self._rule(rid)
        if self.st(rid)["active"]:
            await self._go(rule, False, None, "deleted")
        if self.st(rid)["sug"] is not None:
            self._dismiss(rule)
        self.rules.pop(rid)
        self._repeat_stop(rid)
        self.state.pop(rid, None)
        self.win.pop(rid, None)
        self._cancel_todo(rid)
        for timers in (self._timers, self._win_timers):
            if (u := timers.pop(rid, None)) is not None:
                u()
        for kind in issues.RULE_KINDS:
            issues.clear_issue(self.hass, f"{kind}_{rid}")
        await self._save()
        self.sync_entities()
        self._listen()

    async def set_outputs(self, rid: Any, raw: Any, tunnel: bool = False) -> dict[str, Any]:
        """What the rule does when it changes (its HA entity always; a suggestion; its actions by itself) — the rule keeps
        its state: switching «auto» on or off neither restarts it nor undoes / redoes anything now."""
        rule = self._rule(rid)
        rule["outputs"] = clean_outputs(raw, bool(rule.get("window")))
        st = self.st(rid)
        if not rule["outputs"]["suggest"] and st["sug"] is not None:
            st["sug"] = None
            self._dismiss(rule)
        self._log(rid, "outputs", result=",".join(k for k, v in rule["outputs"].items() if v) or "-")
        await self._save()
        self._refresh(rid)
        return self.rule_out(rid, True, tunnel)

    async def set_enabled(self, rid: Any, on: Any, tunnel: bool = False) -> dict[str, Any]:
        rule = self._rule(rid)
        if not isinstance(on, bool):
            raise Invalid("bad", "rules_bad_object")
        st = self.st(rid)
        if not on and st["active"]:
            await self._go(rule, False, None, "switched_off")
        rule["enabled"] = on
        st["next_at"] = st["off_at"] = None  # a schedule's next moment counted again from now (never «пропущено» while it was off)
        if on:
            # Switched on again: a pause / hold is over, it starts from NORMAL.
            st.update(paused=False, hold_until=None, hold_why=None, holds=[], pend=None)
            for kind in ("rule_paused", "rule_rate"):
                issues.clear_issue(self.hass, f"{kind}_{rid}")
        self._log(rid, "enabled" if on else "disabled")
        await self._save()
        await self.evaluate(rid, "enabled")
        self._refresh(rid)
        return self.rule_out(rid, True, tunnel)

    async def tune(self, rid: Any, body: dict[str, Any], *, client: bool, tunnel: bool = False) -> dict[str, Any]:
        rule = self._rule(rid)
        keys = ("x",) if rule["when"] in ("below", "above") else ("lo", "hi") if rule["when"] in ("outside", "between") else ()
        if not keys:
            raise Invalid("bad", "rules_no_threshold")
        new = {**rule, **{k: _number(body[k], k) for k in keys if k in body}}
        c = rule["client"]
        if client and rule.get("owner") != "client":
            if "lo" not in c or client_locked(rule):
                raise Invalid("not_allowed", "rules_tune_not_allowed")
            if any(not c["lo"] <= new[k] <= c["hi"] for k in keys):
                raise Invalid("out_of_range", "rules_out_of_range", lo=_fmt(c["lo"], None), hi=_fmt(c["hi"], None))
        if "lo" in keys and new["lo"] >= new["hi"]:
            raise Invalid("bad_range", "rules_bad_range")
        if new.get("window"):
            _in_bounds(new)
        self.rules[rid] = new
        self._log(rid, "tuned", **{k: new[k] for k in keys})
        await self._save()
        await self.evaluate(rid, "tuned")
        return self.rule_out(rid, not client or rule.get("owner") == "client", tunnel)

    async def preview(self, raw: Any, *, client: bool, tunnel: bool = False) -> dict[str, Any]:
        """A dry run: the rule checked as a save would, and what it would do now — nothing written. Through the tunnel
        only what get_states would show is read, and «дома / никого» is not asked."""
        try:
            rule = clean_rule(raw, self._env(client))
            if tunnel:
                self._cloud_check(rule)
            check_house(rule, [r for k, r in self.rules.items() if k != rule["id"]], self.derived_sources)
        except Invalid as err:
            return {"ok": False, **self.refusal(err)}
        live = {**rule, "only_if": [c for c in rule["only_if"] if c["type"] != "presence"]} if tunnel else rule
        if rule.get("window"):
            return await self._preview_window(rule, live)
        if scheduled(rule):
            return self._preview_at(rule, live)
        v, bad = self.read(rule)
        ok, _cond = self.conditions(live)
        if bad:
            now = "fault"
        elif not ok:
            now = "waiting"
        elif past_on(rule, v):  # type: ignore[arg-type]
            now = "wait" if rule["for_min"] else "now"
        else:
            now = "normal_for" if rule["for_min"] else "normal"
        ph = {"value": _fmt(v, rule["input"].get("unit")), "minutes": _fmt(rule["for_min"], None)}
        acts = self._preview_actions(rule)
        return {
            "ok": True,
            "rule": rule,
            "value": v,
            "unit": rule["input"].get("unit"),
            "fault": bad,
            "only_if": ok,
            "now": now,
            "actions": acts,
            "why": self.say(f"rules_preview_{now}", True, **ph, threshold=self.threshold(rule, True)),
            "why_en": self.say(f"rules_preview_{now}", False, **ph, threshold=self.threshold(rule, False)),
            **self.hint(rule["purpose"]),
        }

    def _preview_actions(self, rule: dict[str, Any]) -> list[dict[str, Any]]:
        acts = []
        for a in rule["actions"]:
            if not _has_now(a):  # a press, a scene, a run, a toggle: nothing «now» to say (not «unknown»)
                acts.append(dict(a))
                continue
            cur = self.current(a["entity_id"], a["do"]) if "entity_id" in a else self.season_now() if a["do"] == "season" else None
            acts.append({**a, "current": cur, "already": "entity_id" in a and cur is not None and cur == self._wanted(a)})
        return acts

    def _preview_at(self, rule: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
        """The next moment and whether «только если» holds now — nothing written."""
        nxt = self.next_at(rule, dv.now())
        ok, _cond = self.conditions(live)
        now = "schedule" if nxt is not None else "no_next"
        return {
            "ok": True,
            "rule": rule,
            "value": None,
            "unit": None,
            "fault": None,
            "only_if": ok,
            "now": now,
            "next_at": nxt,
            "actions": self._preview_actions(rule),
            "why": self.say(f"rules_preview_{now}", True, next=self._when_text(nxt)),
            "why_en": self.say(f"rules_preview_{now}", False, next=self._when_text(nxt)),
            **self.hint(rule["purpose"]),
        }

    async def _preview_window(self, rule: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
        """The last periods from the recorder, and whether n in a row are past X now — nothing written."""
        w = rule["window"]
        try:
            vals, starts, bad = await win.values(self.hass, rule["input"]["entity_id"], rule["input"].get("attribute"), w)
        except Exception:  # noqa: BLE001
            vals, starts, bad = [], [], "no_statistics"
        last = vals[-w["n"]:]
        ok, _cond = self.conditions(live)
        held = in_row(rule, vals, past_on)
        if len(last) < w["n"] or any(v is None for v in last):
            now = "window_no_data"
        elif not ok:
            now = "waiting"
        else:
            now = "window_now" if held >= w["n"] else "window_normal"
        unit = rule["input"].get("unit")
        ph = {"stat": self.window_words(rule, True), "n": w["n"], "held": held, "threshold": self.threshold(rule, True), "value": _fmt(vals[-1] if vals else None, unit)}
        ph_en = {**ph, "stat": self.window_words(rule, False), "threshold": self.threshold(rule, False)}
        return {
            "ok": True,
            "rule": rule,
            "value": vals[-1] if vals else None,
            "unit": unit,
            "fault": bad,
            "only_if": ok,
            "now": now,
            "values": vals,
            "starts": starts,
            "periods": held,
            "outputs": outputs(rule),
            "actions": [{**a, "current": self.season_now() if a["do"] == "season" else self.current(a["entity_id"], a["do"]) if "entity_id" in a else None} if _has_now(a) else dict(a) for a in rule["actions"]],
            "why": self.say(f"rules_preview_{now}", True, **ph),
            "why_en": self.say(f"rules_preview_{now}", False, **ph_en),
            **self.hint(rule["purpose"]),
        }

    async def save_derived(self, raw: Any, *, tunnel: bool = False) -> dict[str, Any]:
        d = dv.clean(raw, self.hass)
        for src in d["sources"] if tunnel else ():
            e, a = dv.split_source(src)
            if not self.cloud_reads(e, a or dv.default_attribute(e)):
                raise Invalid("not_allowed", "rules_private_input", entity=e)
        if d["id"] not in self.derived_cfg and len(self.derived_cfg) >= C("derived", "max"):
            raise Invalid("limit", "rules_limit_derived", n=int(C("derived", "max")))
        self.derived_cfg[d["id"]] = d
        self._make_derived()
        await self._save()
        self.sync_entities()
        self._listen()
        s = self.sensors.get(d["id"])
        return {**d, "entity_id": s.entity_id if s else derived_entity_id(d), "value": self.derived[d["id"]].value}

    async def delete_derived(self, did: Any) -> None:
        if not isinstance(did, str) or did not in self.derived_cfg:
            raise Invalid("no_value", "rules_no_value")
        ent = self.sensors.get(did)
        eid = ent.entity_id if ent is not None and ent.entity_id else derived_entity_id(self.derived_cfg[did])
        users = [r["name"] for r in self.rules.values() if eid in inputs_of(r)]
        if users:
            raise Invalid("in_use", "rules_in_use", rules=", ".join(users))
        self.derived_cfg.pop(did)
        self._make_derived()
        await self._save()
        self.sync_entities()
        self._listen()


def derived_entity_id(cfg: dict[str, Any]) -> str:
    return f"sensor.{DOMAIN}_{C('engine', 'derived_prefix')}_{slugify(cfg['name']) or cfg['id']}"


def _service(a: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The HA service of an action, by the canon's row: 0 % → its «zero» service (off / close), 100 % → its «full»."""
    spec, v = act_spec(a) or {}, a.get("value")
    numeric = isinstance(v, (int, float)) and not isinstance(v, bool)
    if numeric and spec.get("zero") and v == 0:
        return spec["zero"], {}
    if numeric and spec.get("full") and v == C("limits", "percent")[1]:
        return spec["full"], {}
    if not spec.get("field"):
        return spec["service"], {}
    if spec.get("scale") and numeric:
        v = round(v * spec["scale"], ROUND_DIGITS)
    return spec["service"], {spec["field"]: v}


# --- entities ---------------------------------------------------------------------------------------------------------


class _RuleEntity:
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "rule"

    def _setup(self, engine: Rules, rule: dict[str, Any], domain: str, suffix: str) -> None:
        self.engine = engine
        self.rid = rule["id"]
        self._attr_unique_id = f"{engine.entry_id}_rule_{rule['id']}{suffix}"
        self.entity_id = f"{domain}.{DOMAIN}_{C('engine', 'rule_prefix')}_{slugify(rule['name']) or rule['id']}"
        if engine.entry_id:
            self._attr_device_info = device_info(engine.entry_id)
        self._name_key = f"{domain}.{self._attr_translation_key}"
        self._attr_translation_placeholders = {"name": rule["name"]}
        self._name_in_site_lang(rule)

    def _name_in_site_lang(self, rule: dict[str, Any]) -> bool:
        """«Сработало · <name>» in the house's language over HA's (const.site_name, w5 acceptance: «Rule active ·
        Полив» on an English HA of a Russian project); True — it changed."""
        names = getattr(self.engine, "names", None)
        title = site_name(names, self.engine.ru, self._name_key, name=rule["name"]) if names else None
        if title is None or getattr(self, "_attr_name", None) == title:
            return False
        self._attr_name = title
        return True

    @callback
    def refresh(self) -> None:
        rule = self.engine.rules.get(self.rid)
        if rule is not None and (self._name_in_site_lang(rule) or self._attr_translation_placeholders != {"name": rule["name"]}):
            self._attr_translation_placeholders = {"name": rule["name"]}
            self.__dict__.pop("name", None)
        if self.hass is not None and self.entity_id and self.platform is not None:  # type: ignore[attr-defined]
            self.async_write_ha_state()  # type: ignore[attr-defined]


class RuleActive(_RuleEntity, BinarySensorEntity):
    """On while the rule is ACTIVE; why in its attributes (and the logbook)."""

    _unrecorded_attributes = frozenset({"why", "x", "lo", "hi", "y", "stat", "period", "periods_needed"})

    def __init__(self, engine: Rules, rule: dict[str, Any]) -> None:
        self._setup(engine, rule, "binary_sensor", "")

    @property
    def is_on(self) -> bool:
        return bool(self.engine.st(self.rid)["active"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rule = self.engine.rules.get(self.rid)
        if rule is None:
            return {}
        st = self.engine.st(self.rid)
        out = {"state": self.engine.status(self.rid), "value": st.get("value"), "unit": rule["input"].get("unit") if "input" in rule else None, "why": self.engine.explain(rule, self.engine.ru)}
        if scheduled(rule):
            out.update(next_at=st.get("next_at"), last_at=st.get("last_at"), last_result=st.get("last_result"))
        out.update({k: rule[k] for k in ("x", "lo", "hi", "y") if k in rule})
        if (w := rule.get("window")) is not None:
            # For HA's automations: the statistic, how many periods in a row it holds, since when the rule is so.
            out.update(statistic=st.get("value"), stat=w["stat"], period=w["period"], periods_in_row=st.get("periods", 0), periods_needed=w["n"], no_data=bool(st.get("no_data")))
            if st["since"]:
                out["since"] = dt_util.utc_from_timestamp(st["since"]).isoformat()
        if (sug := self.engine.suggestion(self.rid)) is not None:
            out["suggestion"] = STATE_ON if sug["on"] else STATE_OFF
        if st["hold_until"]:
            out["hold_until"] = dt_util.utc_from_timestamp(st["hold_until"]).isoformat()
        return out


class RuleSwitch(_RuleEntity, SwitchEntity):
    """The rule on / off (as the app's switch)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, engine: Rules, rule: dict[str, Any]) -> None:
        self._setup(engine, rule, "switch", "_enabled")

    @property
    def is_on(self) -> bool:
        rule = self.engine.rules.get(self.rid)
        return bool(rule and rule["enabled"])

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.engine.set_enabled(self.rid, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.engine.set_enabled(self.rid, False)


class DerivedSensor(SensorEntity):
    """«Точка росы · Ванная» and the like: a derived value as a real sensor."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "derived"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, engine: Rules, did: str) -> None:
        self.engine = engine
        self.did = did
        cfg = engine.derived_cfg[did]
        self._attr_unique_id = f"{engine.entry_id}_derived_{did}"
        self.entity_id = derived_entity_id(cfg)
        if engine.entry_id:
            self._attr_device_info = device_info(engine.entry_id)
        self._attr_translation_placeholders = {"name": cfg["name"]}

    @property
    def _d(self) -> dv.Derived | None:
        return self.engine.derived.get(self.did)

    @property
    def available(self) -> bool:
        """«unknown» while the sources aren't heard yet (the rules on it wait), «unavailable» once a source is lost."""
        d = self._d
        return d is not None and (d.value is not None or (d.why or "").split(":")[0] in dv.WAITING)

    @property
    def native_value(self) -> float | None:
        return self._d.value if self._d else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._d.unit if self._d else None

    @property
    def device_class(self) -> Any:
        return self._d.device_class if self._d else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self._d
        return {"kind": d.cfg["kind"], "sources": d.cfg["sources"], **({"why": d.why} if d.why else {})} if d else {}

    @callback
    def refresh(self) -> None:
        if self.hass is not None and self.entity_id and self.platform is not None:
            self.async_write_ha_state()


# --- the view -----------------------------------------------------------------------------------------------------------

OPS = ("save", "delete", "enable", "tune", "preview", "derived_save", "derived_delete", "quiet", "bus_reads")
ADMIN_OPS = ("quiet", "bus_reads")


class RulesView(HomeAssistantView):
    """GET: the rules (admin — everything; a client — what he may see); POST op …. Through the tunnel (integrator)."""

    url = "/api/hauflo/rules"
    name = "api:hauflo:rules"
    requires_auth = True

    def __init__(self, engine_of: Callable[[], Rules | None]) -> None:
        self.engine_of = engine_of

    async def get(self, request):
        engine = self.engine_of()
        if engine is None:
            return self.json_message("Hauflo agent not set up", HTTPStatus.SERVICE_UNAVAILABLE)
        user = request["hass_user"]
        admin = bool(user and user.is_admin)
        tunnel = bool(user and user.system_generated)  # the cloud: reads only what get_states shows
        if (rid := request.query.get("log")) is not None:
            rule = engine.rules.get(rid)
            if rule is None or not (admin or rule["client"].get("visible", True)):
                return self.json_message("no such rule", HTTPStatus.NOT_FOUND)
            return self.json({"id": rid, "log": engine.log_of(rid, tunnel)})
        return self.json(engine.answer(admin, tunnel))

    async def post(self, request):
        engine = self.engine_of()
        if engine is None:
            return self.json_message("Hauflo agent not set up", HTTPStatus.SERVICE_UNAVAILABLE)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", HTTPStatus.BAD_REQUEST)
        if not isinstance(body, dict) or body.get("op") not in OPS:
            return self.json_message(f"op: {' / '.join(OPS)}", HTTPStatus.BAD_REQUEST)
        user = request["hass_user"]
        client = not (user and user.is_admin)
        op = body["op"]
        if (op.startswith("derived") or op in ADMIN_OPS) and client:
            return self.json_message("admin only", HTTPStatus.FORBIDDEN)
        # A restricted (child) profile only looks (house.py); a dry run changes nothing.
        if op != "preview" and client and (no := await refuse(request)) is not None:
            return no
        try:
            return self.json(await self._op(engine, op, body, client, user.id if user else None, bool(user and user.system_generated)))
        except Invalid as err:
            return self.json(engine.refusal(err), HTTPStatus.FORBIDDEN if err.code == "not_allowed" else HTTPStatus.BAD_REQUEST)

    @staticmethod
    async def _op(engine: Rules, op: str, body: dict[str, Any], client: bool, user_id: str | None, tunnel: bool = False) -> dict[str, Any]:
        if op == "preview":
            return await engine.preview(body.get("rule"), client=client, tunnel=tunnel)
        if op == "save":
            return await engine.save(body.get("rule"), client=client, user_id=user_id, tunnel=tunnel)
        if op == "quiet":
            return {"quiet": await engine.set_quiet(body.get("quiet"))}
        if op == "bus_reads":
            return await engine.set_bus_reads(body.get("rules"))
        if op == "derived_save":
            return await engine.save_derived(body.get("derived"), tunnel=tunnel)
        if op == "derived_delete":
            await engine.delete_derived(body.get("id"))
            return {"deleted": body.get("id")}
        rule = engine._rule(body.get("id"))
        if op == "delete":
            if client and rule.get("owner") != "client":
                raise Invalid("not_allowed", "rules_delete_not_allowed")
            await engine.delete(rule["id"])
            return {"deleted": rule["id"]}
        if op == "enable":
            if client and rule.get("owner") != "client" and not client_can(rule)["toggle"]:
                raise Invalid("not_allowed", "rules_toggle_not_allowed")
            return await engine.set_enabled(rule["id"], body.get("enabled"), tunnel)
        return await engine.tune(rule["id"], body, client=client, tunnel=tunnel)
