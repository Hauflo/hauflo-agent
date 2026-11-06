"""Client recipes of «Настройки → Автоматизации» (p6d, docs/automations-recipes.md): ready rules from the house's own
devices. The recipes are DATA (automation_recipes.json: RU+EN texts, parameters with defaults and limits, the standard
behind each default); this module only resolves them against what the house has (targets()) into the rule form that
automations.validate() takes — the engine stays the single judge, a recipe it refuses is simply not offered.

Pure Python (no Home Assistant import): the data is checkable offline (check()) and the resolver is testable with a
fake `targets`."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import re
from typing import Any

PATH = Path(__file__).with_name("automation_recipes.json")
LANGS = ("ru", "en")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_TIME = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
_VAR = re.compile(r"-?\$([a-z]+)")
KINDS = ("int", "temp", "time")
DOS = ("room_covers", "room_off", "set", "on", "off", "ramp", "temp")
EVENTS = ("sunrise", "sunset", "time", "alarm", "value", "presence")


def load() -> dict[str, Any]:
    return json.loads(PATH.read_text(encoding="utf-8"))


DATA = load()  # read once at import (HA imports a platform off the event loop)


def _texts(v: Any, what: str, out: list[str]) -> None:
    if not isinstance(v, dict) or any(not isinstance(v.get(k), str) or not v[k].strip() for k in LANGS):
        out.append(f"{what}: text in {' and '.join(LANGS)}")


def check(data: dict[str, Any]) -> list[str]:
    """Everything wrong with the recipe data (empty = fine): ids, RU+EN texts, parameters with defaults inside limits,
    every $param / {param} known, only events and actions the engine has."""
    bad: list[str] = []
    for g, t in data.get("groups", {}).items():
        _texts(t, f"group {g}", bad)
    for n, m in data.get("matchers", {}).items():
        try:
            re.compile(m)
        except re.error:
            bad.append(f"matcher {n}: not a regex")
    for k, t in data.get("alarm_src", {}).items():
        _texts(t, f"alarm_src {k}", bad)
    ids: set[str] = set()
    recipes = data.get("recipes", [])
    if not 1 <= len(recipes) <= data.get("limits", {}).get("max_recipes", 40):
        bad.append("recipes: count out of limits")
    for r in recipes:
        rid = r.get("id")
        if not isinstance(rid, str) or not re.fullmatch(r"[a-z0-9_]{1,40}", rid) or rid in ids:
            bad.append(f"{rid!r}: id must be unique [a-z0-9_]")
            continue
        ids.add(rid)
        if r.get("group") not in data.get("groups", {}):
            bad.append(f"{rid}: group")
        for f in ("title", "sub"):
            _texts(r.get(f), f"{rid}.{f}", bad)
        if not isinstance(r.get("basis"), str) or len(r["basis"]) < 20:
            bad.append(f"{rid}: basis (the standard / platform behind the default)")
        params = r.get("params", {})
        for p, s in params.items():
            _texts(s.get("label"), f"{rid}.{p}.label", bad)
            kind, d = s.get("type"), s.get("default")
            if kind not in KINDS:
                bad.append(f"{rid}.{p}: type")
            elif kind == "time":
                if not isinstance(d, str) or not _TIME.fullmatch(d):
                    bad.append(f"{rid}.{p}: default HH:MM")
            else:
                lo, hi = s.get("min"), s.get("max")
                if not all(isinstance(x, (int, float)) for x in (lo, hi, d)) or not lo <= d <= hi:
                    bad.append(f"{rid}.{p}: default {d!r} outside {lo}…{hi}")
                if not isinstance(s.get("unit"), str):
                    bad.append(f"{rid}.{p}: unit")
        used = set(_VAR.findall(json.dumps([r.get("when"), r.get("actions"), r.get("only_if")])))
        used |= set(re.findall(r"\{([a-z]+)\}", json.dumps([r.get("title"), r.get("sub")]))) - {"rooms", "src"}
        if used - set(params):
            bad.append(f"{rid}: unknown parameters {sorted(used - set(params))}")
        when = r.get("when", {})
        if when.get("event") not in EVENTS:
            bad.append(f"{rid}: when.event")
        if bool(r.get("alarm")) != (when.get("event") == "alarm"):
            bad.append(f"{rid}: `alarm` flag must match the alarm event")
        days = r.get("days")
        if days is not None and (not isinstance(days, list) or any(d not in DAYS for d in days)):
            bad.append(f"{rid}: days")
        acts = r.get("actions", [])
        if not acts or any(a.get("do") not in DOS for a in acts):
            bad.append(f"{rid}: actions")
        for a in acts:
            for m in (a.get("match"), a.get("skip"), when.get("sensors", {}).get("match")):
                if m is not None and m not in data["matchers"]:
                    bad.append(f"{rid}: matcher {m}")
    return bad


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _value(spec: Any, vals: dict[str, Any]) -> Any:
    """'$p' → the parameter, '-$p' → its minus; anything else as is."""
    if isinstance(spec, str) and (m := re.fullmatch(r"(-?)\$([a-z]+)", spec)):
        v = vals[m.group(2)]
        return -v if m.group(1) else v
    return spec


def _fill(text: str, vals: dict[str, Any], extra: dict[str, str]) -> str:
    return text.format_map({**{k: _num(v) if isinstance(v, (int, float)) else v for k, v in vals.items()}, **extra})


def resolve(data: dict[str, Any], targets: dict[str, Any], validate: Callable[[Any], Any] | None = None) -> list[dict[str, Any]]:
    """The recipes this house can run: [{key, group, icon, title, sub, basis, params, rule}]. `validate` is the engine's
    automations.validate(): a recipe it refuses is left out (never an error for the client)."""
    out: list[dict[str, Any]] = []
    for r in data["recipes"]:
        item = _one(data, r, targets)
        if item is None:
            continue
        if validate is not None:
            try:
                validate(item["rule"])
            except Exception:  # noqa: BLE001 — Invalid and anything unexpected: this recipe is not offered
                continue
        out.append(item)
    return out


def _one(data: dict[str, Any], r: dict[str, Any], t: dict[str, Any]) -> dict[str, Any] | None:
    lim = data["limits"]
    areas: dict[str, str] = t.get("areas", {})
    rx = {k: re.compile(v, re.I) for k, v in data["matchers"].items()}
    if "people" in r.get("needs", []) and not t.get("people"):
        return None
    vals = {p: s["default"] for p, s in r.get("params", {}).items()}
    names: list[str] = []  # the rooms the rule is about (for the text)

    def area_ok(area: str | None, match: str | None, skip: str | None = None, only: str | None = None) -> bool:
        name = areas.get(area or "", "")
        if only is not None and area != only:
            return False
        return (match is None or bool(rx[match].search(name))) and (skip is None or not rx[skip].search(name))

    room_in: str | None = None
    when = r["when"]
    rule_when: dict[str, Any] = {"event": when["event"]}
    if when["event"] == "alarm":
        # A phone whose «next alarm» HA reads now; one not seen (unavailable, unknown) only when there is no other.
        alarms = list(t.get("alarms") or [])
        phone = ([a for a in alarms if a.get("next") not in (None, "unavailable", "unknown")] or alarms or [None])[0]
        lead = -_value(when["offset"], vals)
        if phone:
            rule_when = {"event": "alarm_android", "sensor": phone["entity_id"], "offset": -lead}
            src = {k: v.format(phone=phone["phone"], lead=_num(lead)) for k, v in data["alarm_src"]["android"].items()}
        else:
            rule_when = {"event": "alarm_iphone"}
            src = dict(data["alarm_src"]["iphone"])
    elif when["event"] == "value":
        s = when["sensors"]
        by_area: dict[str | None, list[dict[str, Any]]] = {}
        for x in t.get("sensors", []):
            if x["kind"] == s["kind"] and x.get("area") and area_ok(x["area"], s.get("match")):
                by_area.setdefault(x["area"], []).append(x)
        have = {i.get("area") for i in t.get({"fan": "fans", "light": "lights"}[s["with"]], [])} if s.get("with") else set(by_area)
        rooms = sorted((a for a in by_area if a in have), key=lambda a: areas.get(a, a))
        if not rooms:
            return None
        room_in = rooms[0]
        picked = [x for x in by_area[room_in] if x.get("unit") == by_area[room_in][0].get("unit")][: data["limits"]["per_rule_devices"]]
        rule_when.update({"sensors": [x["entity_id"] for x in picked], "kind": s["kind"], "op": when["op"], "x": _value(when["x"], vals), "for_min": _value(when["for_min"], vals)})
        if len(picked) > 1:
            rule_when["stat"] = "mean"
        names.append(areas.get(room_in, room_in))
        src = {"ru": "", "en": ""}
    else:
        for k in ("offset", "at", "to"):
            if k in when:
                rule_when[k] = _value(when[k], vals)
        src = {"ru": "", "en": ""}

    actions: list[dict[str, Any]] = []
    for a in r["actions"]:
        each, do = a.get("each"), a["do"]
        match, skip = a.get("match"), a.get("skip")
        only = room_in if a.get("in") == "@room" else None
        if do == "set":
            if not t.get("lights"):
                return None
            actions.append({k: a[k] for k in ("do", "domain", "cmd", "all")})
            names.append("")
        elif each == "cover_rooms":
            rooms = [x for x in t.get("cover_rooms", []) if area_ok(x["area_id"], match, skip)][: lim["per_rule_rooms"]]
            if not rooms:
                return None
            for x in rooms:
                actions.append({"do": "room_covers", "area_id": x["area_id"], "cover": a["cover"]})
                names.append(x["name"])
        elif each == "rooms":
            rooms = [x for x in t.get("rooms", []) if area_ok(x["area_id"], match, skip)][: lim["per_rule_rooms"]]
            if not rooms:
                return None
            for x in rooms:
                actions.append({"do": "room_off", "area_id": x["area_id"]})
                names.append(x["name"])
        elif each in ("light", "fan", "climate"):
            pool = t.get({"light": "lights", "fan": "fans", "climate": "climates"}[each], [])
            items = [x for x in pool if area_ok(x.get("area"), match, skip, only)]
            if each == "light" and a.get("dim"):
                items = [x for x in items if x.get("dim")]
            if each == "climate":
                items = [x for x in items if x.get("temp")]
                if not match and not items:
                    return None
            if a.get("first"):
                items = items[:1]
            items = items[: lim["per_rule_devices"]]
            if not items:
                return None
            for x in items:
                item: dict[str, Any] = {"entity_id": x["entity_id"], "do": do}
                v = _value(a.get("value"), vals)
                if do == "temp":
                    v = min(max(float(v), x["min"]), x["max"])
                    item["value"] = v
                elif each == "light" and v is not None and (x.get("dim") or do == "ramp"):
                    item["value"] = int(v)
                    if do == "ramp":
                        item["minutes"] = int(_value(a["minutes"], vals))
                actions.append(item)
                names.append(areas.get(x.get("area") or "", "") or x.get("name", ""))
    uniq = list(dict.fromkeys(n for n in names if n))
    rooms_txt = ", ".join(uniq[:3]) + ("…" if len(uniq) > 3 else "")
    rule: dict[str, Any] = {"when": rule_when, "days": list(r.get("days") or DAYS), "actions": actions, "from": r["id"]}
    if r.get("only_if"):
        rule["only_if"] = [dict(c) for c in r["only_if"]]
    texts = {k: {lang: _fill(r[k][lang], vals, {"rooms": rooms_txt, "src": src[lang]}) for lang in LANGS} for k in ("title", "sub")}
    return {
        "key": r["id"], "group": r["group"], "icon": r.get("icon"), "title": texts["title"], "sub": texts["sub"], "basis": r["basis"], "rule": rule,
        "params": [{"id": p, **{k: s[k] for k in ("type", "default", "min", "max", "unit", "label") if k in s}} for p, s in r.get("params", {}).items()],
    }
