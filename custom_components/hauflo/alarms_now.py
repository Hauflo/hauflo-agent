"""The house's open alarms, counted ONCE (0.34 п.2): the cards' header / alert / status, Hauflo's «Мониторинг», the object
page and the bot all said their own sums (a card: the rules' alarms; the bot: the rules' + the marked signals; the
header: the faults apart) — now each shows this one answer.

Pure: no HA here. In — the push engine's alarms (`items`: notify.NotifyEngine.alarm_items — open, taken back after a
restart, back to normal and not yet looked at) and the house's problems by the site's policy (`faults`: __init__.house
`_faults` — a «Не авария» / «не влияет» / «не следить» signal is never one). Out — one entry per entity:

  state  open      — an alarm now (a rule's or the house's problem), counted;
         returned  — back to normal, nobody accepted it yet (the alert card asks for a look), not counted as open;
         lost      — no data from it (a rule's «нет связи», a signal unavailable), told apart, never an alarm.

Counts over the open ones: `critical` — not accepted, life / critical priority; `normal` — not accepted, the rest;
`acked` — every rule alarm of it accepted, or — a house problem with no rule's alarm — accepted by its own key
`_house|<entity>` (0.34 fix п.2: «Принять» on it said «принял …» and the counter never moved); the problem stays open
until it clears, and its «Принято» is forgotten then (notify.NotifyEngine.house_acks_keep)."""

from __future__ import annotations

from typing import Any

from .tuning import TUNING

# Per entity, at most this many travel to the cloud (status): the counts stay whole.
ITEMS_MAX = TUNING["notify"]["now_items_max"]

_RANK = {"open": 0, "returned": 1, "lost": 2}

# The accept key of a house problem: «_house|<entity>» (no rule id starts with «_»: rules.py ids are slugs).
HOUSE_KEY = "_house"


def house_key(entity_id: str) -> str:
    return f"{HOUSE_KEY}|{entity_id}"


def summary(items: list[dict[str, Any]], faults: list[dict[str, Any]], cap: int | None = None, house_acks: dict[str, str] | None = None) -> dict[str, Any]:
    by: dict[str, dict[str, Any]] = {}
    for a in items:
        eid = a.get("entityId") or a.get("entity")
        if not isinstance(eid, str) or not eid:
            continue
        state = "lost" if a.get("noData") else "returned" if a.get("returned") else "open"
        e = by.get(eid)
        if e is None:
            e = by[eid] = {"entity": eid, "name": a.get("name") or "", "area": a.get("area") or "", "title": a.get("title") or "", "state": state, "critical": False, "since": a.get("since"), "keys": [], "ackedBy": None, "source": "rule", "_acks": []}
        elif _RANK[state] < _RANK[e["state"]]:
            # The worst of its rules says what it is (an alarm beats «нет связи» of another rule on the same sensor).
            e.update(state=state, title=a.get("title") or e["title"])
        if state == "open":
            e["critical"] = e["critical"] or bool(a.get("critical"))
            e["_acks"].append(a.get("ackedBy"))
        if a.get("since") and (not e["since"] or str(a["since"]) < str(e["since"])):
            e["since"] = a["since"]
        if a.get("key") and state != "lost":
            e["keys"].append(a["key"])
    for f in faults:
        eid = f.get("entity")
        if not isinstance(eid, str) or not eid:
            continue
        state = "lost" if f.get("offline") else "open"
        e = by.get(eid)
        if e is not None:
            # A rule's alarm of it says it already; its problem by the policy only raises it to open / critical.
            if state == "open" and e["state"] != "open":
                e["state"], e["source"] = "open", "house"
            if state == "open":
                e["critical"] = e["critical"] or f.get("house") == "alarm"
                e["house"] = f.get("house")
            continue
        by[eid] = {"entity": eid, "name": f.get("name") or "", "area": f.get("area") or "", "title": "", "state": state, "critical": state == "open" and f.get("house") == "alarm", "since": f.get("since"), "keys": [], "ackedBy": None, "source": "house", "house": f.get("house"), "_acks": []}
    out = []
    for e in by.values():
        acks = e.pop("_acks")
        # Accepted when every open rule alarm of it is; a house problem with no open rule alarm — by its own key.
        if e["state"] == "open" and acks and all(acks) and e["source"] == "rule":
            e["ackedBy"] = acks[0]
        elif e["state"] == "open" and e["source"] == "house" and not acks:
            e["keys"] = [house_key(e["entity"])]
            e["ackedBy"] = (house_acks or {}).get(e["entity"]) or None
        e["acked"] = bool(e["ackedBy"])
        out.append(e)
    # Critical first, then not accepted, then the oldest — as every list shows them.
    out.sort(key=lambda e: (_RANK[e["state"]], e["acked"], not e["critical"], str(e["since"] or "")))
    open_ = [e for e in out if e["state"] == "open"]
    res = {
        "critical": sum(1 for e in open_ if e["critical"] and not e["acked"]),
        "normal": sum(1 for e in open_ if not e["critical"] and not e["acked"]),
        "acked": sum(1 for e in open_ if e["acked"]),
        "total": len(open_),
        "returned": sum(1 for e in out if e["state"] == "returned"),
        "lost": sum(1 for e in out if e["state"] == "lost"),
        "items": out if cap is None else out[:cap],
    }
    if cap is not None and len(out) > cap:
        res["more"] = len(out) - cap
    return res
