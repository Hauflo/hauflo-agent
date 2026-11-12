"""The value rules' canon (owner 29.10: no hardcode): every default a house, an integrator or the product may change —
helper times and limits, purposes, derived-value defaults, recipes — lives in rules_canon.json, checked against this
schema when the agent loads. A bad canon is a bug of the release: the import fails loudly, never a silent default.
User texts are in strings.json / translations (section «exceptions», keys «rules_…»), never here or in the code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import voluptuous as vol

FILE = Path(__file__).with_name("rules_canon.json")

_pos = vol.All(vol.Coerce(float), vol.Range(min=0))
_int = vol.All(int, vol.Range(min=0))
_pair = vol.All([vol.Coerce(float)], vol.Length(min=2, max=2))
_ipair = vol.All([vol.All(int, vol.Range(min=1))], vol.Length(min=2, max=2))
_SAFE = vol.In(["off", "notify"])
_PRIORITY = vol.In(["safety", "comfort"])

PURPOSE = vol.Schema(
    {
        vol.Optional("min_on_s", default=0): _pos,
        vol.Optional("min_off_s", default=0): _pos,
        vol.Optional("safe"): _SAFE,
        vol.Optional("priority", default="comfort"): _PRIORITY,
        vol.Optional("back"): vol.In(["reverse", "nothing"]),
        vol.Optional("hint", default=False): bool,
    }
)
RECIPE = vol.Schema(
    {
        vol.Required("id"): str,
        vol.Required("purpose"): str,
        vol.Required("input"): str,
        vol.Required("when"): vol.In(["below", "above", "outside", "between", "on", "off"]),
        vol.Optional("x"): vol.Coerce(float),
        vol.Optional("lo"): vol.Coerce(float),
        vol.Optional("hi"): vol.Coerce(float),
        vol.Optional("y", default=0): _pos,
        vol.Required("for_min"): _pos,
        vol.Required("back_min"): _pos,
        vol.Required("actions"): [vol.Schema({vol.Required("do"): str, vol.Optional("role"): str, vol.Optional("value"): vol.Any(int, float, str)})],
        vol.Required("back"): vol.In(["reverse", "nothing"]),
        vol.Optional("interlock"): str,
        vol.Optional("window"): {vol.Required("stat"): str, vol.Required("period"): str, vol.Required("n"): vol.All(int, vol.Range(min=1))},
        vol.Optional("outputs"): {vol.Required(k): bool for k in ("suggest", "suggest_back", "auto")},
        vol.Optional("auto_allowed", default=False): bool,
        vol.Optional("only_if"): [dict],
        vol.Optional("room"): str,
        vol.Optional("unit"): str,
        # Where the numbers come from (a standard / best practice): data for the people who keep the canon.
        vol.Optional("source"): str,
    }
)
# One entity action (HA entity service of its domain): what it calls, what value it takes, where the value's bounds come
# from (the entity's own attributes), the slot two rules may not both hold, how its current state is read.
KINDS = ("percent", "setpoint", "number", "option", "seconds", "text", "time", "date", "datetime")
ACT = vol.Schema(
    {
        vol.Required("service"): vol.Match(r"^[a-z_]+$"),
        vol.Required("slot"): vol.Match(r"^[a-z_]+$"),
        vol.Optional("field"): vol.Match(r"^[a-z_]+$"),
        vol.Optional("value"): vol.In(KINDS),
        vol.Optional("options"): str,
        vol.Optional("bounds"): vol.All([str], vol.Length(min=2, max=2)),
        vol.Optional("step"): str,
        vol.Optional("read"): str,
        vol.Optional("want"): vol.Any(str, int, float),
        vol.Optional("zero"): vol.Match(r"^[a-z_]+$"),
        vol.Optional("full"): vol.Match(r"^[a-z_]+$"),
        vol.Optional("opposite"): str,
        vol.Optional("restore", default=False): bool,
        vol.Optional("power"): vol.In(["on", "off", "value", "toggle"]),
        vol.Optional("scale"): vol.Coerce(float),
        # HA's own required_features of the service (any one of them, each fully): an entity without it never gets the action.
        vol.Optional("features"): [vol.All(int, vol.Range(min=1))],
        # A light's brightness: only when its colour modes go beyond «onoff».
        vol.Optional("beyond"): {vol.Required("attr"): str, vol.Required("not"): [str]},
        vol.Optional("needs"): [str],
    }
)
_DAY = vol.In(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
SCHEDULE_RECIPE = vol.Schema(
    {
        vol.Required("id"): str,
        vol.Required("purpose"): str,
        vol.Required("at"): {
            vol.Optional("times"): [vol.Match(r"^([01]\d|2[0-3]):[0-5]\d$")],
            vol.Optional("sun"): [{vol.Required("event"): str, vol.Required("offset_min"): vol.Coerce(float)}],
            vol.Required("days"): vol.All([_DAY], vol.Length(min=1)),
        },
        vol.Required("actions"): [vol.Schema({vol.Required("do"): str, vol.Required("domain"): str, vol.Optional("value"): vol.Any(int, float, str)})],
        vol.Optional("only_if"): [dict],
        vol.Required("source"): str,
    }
)
LINK = vol.Schema({vol.Required("id"): str, vol.Required("to"): str, vol.Required("hints"): [str], vol.Required("source"): str})
_HHMM = vol.Match(r"^([01]\d|2[0-3]):[0-5]\d$")
_STATS = ("mean", "min", "max", "sum")
SCHEMA = vol.Schema(
    {
        vol.Required("version"): 1,
        vol.Required("engine"): {
            vol.Required(k): _pos for k in ("tick_s", "log_size", "log_out", "warn_gap_s", "echo_s", "error_len", "contexts", "save_delay_s", "min_wake_s", "unknown_grace_s")
        }
        | {vol.Required(k): vol.Match(r"^[a-z][a-z0-9_]*[a-z0-9]$") for k in ("rule_prefix", "derived_prefix")},
        vol.Required("helpers"): {
            vol.Required(k): _pos for k in ("manual_hold_s", "pause_holds", "pause_window_s", "rate_max", "rate_window_s", "climate_gap_s", "bus_cap", "bus_window_s")
        },
        vol.Required("limits"): {
            **{vol.Required(k): _int for k in ("rules", "actions", "conditions", "for_min", "name_len", "notify_len", "id_len", "number_abs", "hysteresis_floor")},
            vol.Required("setpoint"): _pair,
            vol.Required("setpoint_step"): _pos,
            vol.Required("percent"): _pair,
        },
        vol.Required("purposes"): {str: PURPOSE},
        vol.Required("client_purposes"): [str],
        vol.Required("derived"): {
            **{vol.Required(k): _pos for k in ("max", "max_sources", "max_age_min", "max_age_hi_min", "rise_window_default_min", "rise_min_span_s", "rise_min_share", "samples")},
            vol.Required("rise_window_min"): _pair,
            vol.Required("outdoor_words"): [str],
        },
        vol.Required("window"): {
            vol.Required("stats"): [vol.In(_STATS)],
            vol.Required("periods"): {str: vol.All(int, vol.Range(min=1))},
            vol.Required("n"): {str: _pair},
            vol.Required("state_classes"): {vol.In(_STATS): [str]},
            vol.Required("history_domains"): [str],
            vol.Required("history_stats"): [vol.In(_STATS)],
            vol.Required("on_time_domains"): [str],
            vol.Required("on_time_unit"): str,
            **{vol.Required(k): _pos for k in ("compile_delay_s", "history_keep_days", "history_min_samples", "history_max_gap_share", "values_out")},
        },
        vol.Required("thresholds"): {str: _pair},
        vol.Required("suggest"): {vol.Required(k): _pos for k in ("per_day", "snooze_days", "day_s")} | {vol.Required("snooze_bounds"): _pair},
        vol.Required("quiet"): {vol.Required("default"): {vol.Required("on"): bool, vol.Required("from"): _HHMM, vol.Required("to"): _HHMM}, vol.Required("bypass_priorities"): [str]},
        vol.Required("actions_by_domain"): {vol.Match(r"^[a-z_]+$"): {vol.Match(r"^[a-z_]+$"): ACT}},
        vol.Required("actions"): {
            vol.Required("pulse_s"): _pair,
            vol.Required("pulse_default_s"): _pos,
            vol.Required("text_len"): _int,
            vol.Required("client_acts"): [str],
            vol.Required("client_domains"): [str],
            vol.Required("client_classes"): {str: [str]},
            vol.Required("group_domains"): [str],
            vol.Required("repeat"): {vol.Required("count"): _ipair, vol.Required("every_s"): _ipair, vol.Required("acts"): [str], vol.Required("source"): str},
            vol.Required("source"): str,
        },
        vol.Required("schedule"): {
            **{vol.Required(k): _int for k in ("moments", "catch_up_s", "horizon_days")},
            vol.Required("sun_offset_min"): _pair,
            vol.Required("events"): [str],
            vol.Required("source"): str,
        },
        vol.Required("schedule_recipes"): [SCHEDULE_RECIPE],
        vol.Required("recipes"): [RECIPE],
        vol.Required("links"): [LINK],
        # Р65: «читать после команды» — a write to a trigger GA → knx.read of the status GAs after delay_s.
        vol.Required("bus_reads"): {
            **{vol.Required(k): _pos for k in ("delay_s", "rules", "triggers", "reads", "name_len", "reads_per_min", "log_size", "log_out")},
            vol.Required("delay_bounds_s"): _pair,
            # The KNX group address notations: parts → bits each (3-level 5/3/8, 2-level 5/11, free 16).
            vol.Required("ga_bits"): {vol.In(["1", "2", "3"]): vol.All([vol.All(int, vol.Range(min=1))], vol.Length(min=1))},
            vol.Required("source"): str,
        },
    }
)


def load(path: Path = FILE) -> dict[str, Any]:
    data = SCHEMA(json.loads(path.read_text(encoding="utf-8")))
    win = data["window"]
    for r in data["recipes"]:
        if r["purpose"] not in data["purposes"]:
            raise vol.Invalid(f"recipe {r['id']}: unknown purpose {r['purpose']}")
        if (w := r.get("window")) is not None:
            lo, hi = win["n"].get(w["period"], (0, -1))
            if w["stat"] not in win["stats"] or w["period"] not in win["periods"] or not lo <= w["n"] <= hi:
                raise vol.Invalid(f"recipe {r['id']}: window out of the canon")
    if set(win["n"]) != set(win["periods"]):
        raise vol.Invalid("window: n bounds for every period")
    if any(len(b) != int(k) or sum(b) != 16 for k, b in data["bus_reads"]["ga_bits"].items()):  # noqa: PLR2004 — a KNX address is 16 bits
        raise vol.Invalid("bus_reads: ga_bits — one width per part, 16 bits in all")
    lo, hi = data["bus_reads"]["delay_bounds_s"]
    if not lo <= data["bus_reads"]["delay_s"] <= hi:
        raise vol.Invalid("bus_reads: delay_s out of its bounds")
    acts = data["actions_by_domain"]
    for domain, dos in acts.items():
        for do, a in dos.items():
            if a.get("value") == "option" and "options" not in a:
                raise vol.Invalid(f"actions_by_domain {domain}.{do}: an option needs its options attribute")
            if a.get("opposite") is not None and a["opposite"] not in dos:
                raise vol.Invalid(f"actions_by_domain {domain}.{do}: opposite {a['opposite']} unknown")
            if a.get("restore") and "read" not in a:
                raise vol.Invalid(f"actions_by_domain {domain}.{do}: restore needs read")
    every = {do for dos in acts.values() for do in dos} | {"season", "notify"}
    if any(a not in every for a in data["actions"]["client_acts"]) or any(d not in acts for d in data["actions"]["client_domains"]):
        raise vol.Invalid("actions: a client act / domain out of actions_by_domain")
    lo, hi = data["actions"]["pulse_s"]
    if not 0 < lo <= data["actions"]["pulse_default_s"] <= hi:
        raise vol.Invalid("actions: pulse_default_s out of pulse_s")
    client = data["actions"]
    if any(d not in client["client_domains"] for d in (*client["client_classes"], *client["group_domains"])) or any(a not in client["client_acts"] for a in client["repeat"]["acts"]):
        raise vol.Invalid("actions: client_classes / group_domains / repeat out of the client's domains and acts")
    if not 2 <= client["repeat"]["count"][0] <= client["repeat"]["count"][1] or not 0 < client["repeat"]["every_s"][0] <= client["repeat"]["every_s"][1]:  # noqa: PLR2004 — once is no repeat
        raise vol.Invalid("actions: repeat count from 2, every_s above 0")
    for r in data["schedule_recipes"]:
        if r["purpose"] not in data["purposes"]:
            raise vol.Invalid(f"schedule recipe {r['id']}: unknown purpose {r['purpose']}")
        if any(a["do"] not in acts.get(a["domain"], {}) for a in r["actions"]):
            raise vol.Invalid(f"schedule recipe {r['id']}: an action out of actions_by_domain")
        if any(e["event"] not in data["schedule"]["events"] for e in r["at"].get("sun", [])):
            raise vol.Invalid(f"schedule recipe {r['id']}: unknown sun event")
        if len(r["at"].get("times", [])) + len(r["at"].get("sun", [])) not in range(1, data["schedule"]["moments"] + 1):
            raise vol.Invalid(f"schedule recipe {r['id']}: moments out of the canon")
    for p in data["client_purposes"]:
        if p not in data["purposes"]:
            raise vol.Invalid(f"client purpose {p} unknown")
    return data


CANON: dict[str, Any] = load()
