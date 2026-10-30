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
        vol.Required("recipes"): [RECIPE],
        vol.Required("links"): [LINK],
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
    for p in data["client_purposes"]:
        if p not in data["purposes"]:
            raise vol.Invalid(f"client purpose {p} unknown")
    return data


CANON: dict[str, Any] = load()
