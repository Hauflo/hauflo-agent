"""«Нет дома — имитация присутствия» (owner 28.10). Not a recorder that plays last week back (ABB / Siemens modules do
that — the desk lamp on 9–18 every day, the KNX forums call it a poor simulation): the agent learns the family's evenings
and makes a new evening every day.

1. Learns from the last 14 days of HA's own history (recorder), Hauflo's own lights and covers only: per light, how
   likely it is on in each 15-minute slot (weekdays and weekends apart); per room, how long its evenings last; per blind,
   the usual open / close times (median).
2. Tonight's plan from that, not a copy: lights only when it's dark (from sunset − 20 min to the house's usual last light,
   01:00 at the latest), sessions drawn from the profile (they start where a room is usually lit, last as its evenings
   last, ± 15 min), only rooms really used in the evening, at most 3 rooms at once (never every room), at most 2 lights
   a room. Daytime light (a desk lamp 9–18) is never played — nobody sees it from the street.
3. Blinds at the family's usual times ± 15 min; no history of it — by the sun (open 30 min after sunrise, close at
   sunset): a safe default, not invented logic.
4. Fewer than 3 evenings of history: no lights, said plainly; the family may choose «по солнцу» (blinds by the sun, a
   calm evening light in the 1–2 rooms used most).

Safety as automations.py: never a lock, the alarm, a valve, a garage door / gate / door cover, the integrator's own
entity, a hidden / disabled one or a group (its lamps take part instead); only on / off (+ brightness) and open / close /
position. A device someone at home touched in the last 10 minutes is left alone — but the «off» of a light it lit itself
always goes unless a person changed that light in HA since (a restart's fresh states, the bus don't count; a light not
known yet is switched off once it is). Switched off — by a person, by its date, the agent unloaded — what it lit goes off,
a light that reads «off» only because the bus hasn't answered since a restart too (watched, or sent «off» on unload).
What it did itself is never learned as the family's evenings (its commands are kept two weeks, their echo dropped). Rooms the family left out (`skip_rooms`,
HA areas: a façade, a guest room) are never planned. Kept in the agent's store (on / until / mode / rooms left out / the
last 20 actions / what it left on): survives restarts. Planned at 00:05 and when switched on; off after the
«until» day. GET/POST /api/hauflo/presence — any signed-in person of this HA; not through the tunnel.
Plan and limits: docs/automations.md («Нет дома — имитация присутствия»).
"""

from __future__ import annotations

from bisect import bisect_right
from collections import deque
from collections.abc import Callable
import datetime as dt
import logging
import math
import random
import re
import statistics
import time
from typing import Any

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import CALLBACK_TYPE, Context, CoreState, HomeAssistant, State, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_track_point_in_time, async_track_state_change_event, async_track_time_change
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .house import refuse
from .storage import HaufloStore
from .tuning import TUNING
_LOGGER = logging.getLogger(__name__)
STORE_KEY = "hauflo.presence"
_T = TUNING["presence"]
LEARN_DAYS = _T["learn_days"]
JITTER = _T["jitter_min"]  # ± minutes
SLOT = _T["slot_min"]  # minutes
SLOTS = _T["slots"]
# A «day» here runs 04:00 → 04:00, so an evening that ends at 00:40 is still that day's evening. Minutes from 04:00:
EVENING = tuple(_T["evening"])  # 15:00 … 01:00
MORNING = tuple(_T["morning"])  # 04:00 … 12:00 — blinds opening
AFTERNOON = tuple(_T["afternoon"])  # 12:00 … 03:00 — blinds closing
CAP_END = _T["cap_end"]  # lights off by 01:00
DEFAULT_END = _T["default_end"]  # 23:00 when the family's own end isn't known
DARK_BEFORE = _T["dark_before_min"]  # lights from sunset − 20 min
P_MIN = _T["p_min"]  # a room counts as «usually lit» in a slot from this probability
MIN_EVENINGS = _T["min_evenings"]
MAX_ROOMS = _T["max_rooms"]
LIGHTS_PER_ROOM = _T["lights_per_room"]
SUN_BRIGHTNESS = _T["sun_brightness"]
COALESCE = _T["coalesce_s"]  # seconds: blind changes this close together are one
MANUAL = _T["manual_s"]  # seconds: touched by someone at home this recently — left alone
WATCH_OFF = _T["watch_off_s"]  # seconds a light it lit that reads «off» after a restart is watched (KNX answers late)
OURS_AFTER = _T["ours_after_s"]  # seconds a device's reports after our command are still ours (a blind travels, KNX answers)
MAX_EVENTS = _T["max_events"]  # a day
LOG_SIZE = _T["log_size"]
MAX_UNTIL_DAYS = _T["max_until_days"]
REPLAN = tuple(_T["replan"])
MODES = ("learned", "sun")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_LIVING = re.compile(_T["living_words"], re.I)
MAX_SKIP = _T["max_skip"]
MAX_OURS = _T["max_ours"]  # its own commands remembered (14 days of them) — kept out of what it learns
DEFAULT: dict[str, Any] = {"enabled": False, "until": None, "lights": True, "covers": True, "mode": "learned", "since": None, "by": None, "log": [], "lit": [], "lit_at": {}, "ours": [], "skip_rooms": []}


class Invalid(ValueError):
    """A request the agent refuses (400)."""


# --- time on the 04:00 → 04:00 day ------------------------------------------------------------------------------------
def _wall(t: dt.datetime) -> dt.datetime:
    return dt_util.as_local(t).replace(tzinfo=None)


def day_min(t: dt.datetime) -> tuple[dt.date, float]:
    """The day (04:00 → 04:00) a moment belongs to and its minutes from that day's 04:00."""
    w = _wall(t) - dt.timedelta(hours=4)
    return w.date(), w.hour * 60 + w.minute + w.second / 60


def rel_min(day: dt.date, t: dt.datetime) -> float:
    """Minutes of a moment from `day`'s 04:00 (negative before it)."""
    return (_wall(t) - dt.datetime.combine(day, dt.time(4))).total_seconds() / 60


def at(day: dt.date, minutes: float) -> dt.datetime:
    """`day`'s 04:00 + minutes, wall-clock in HA's time zone (a clock change keeps 19:40 at 19:40)."""
    return (dt.datetime.combine(day, dt.time(4)) + dt.timedelta(minutes=minutes)).replace(tzinfo=dt_util.get_default_time_zone())


# --- what a recorded state asks for ------------------------------------------------------------------------------------
def action_of(domain: str, st: State, dim: bool, pos: bool) -> tuple[str, int | None] | None:
    """A light on (brightness %) / off; a blind open / closed / at a position. Nothing for unavailable / unknown / a blind
    on its way."""
    s = st.state
    if domain == "light":
        if s == "off":
            return ("off", None)
        if s != "on":
            return None
        b = st.attributes.get("brightness")
        if dim and isinstance(b, (int, float)) and not isinstance(b, bool) and b > 0:
            return ("on", max(1, min(100, round(b * 100 / 255))))
        return ("on", None)
    if domain == "cover":
        if s not in ("open", "closed"):
            return None
        p = st.attributes.get("current_position")
        if pos and isinstance(p, (int, float)) and not isinstance(p, bool):
            p = round(p)
            return ("close", None) if p <= 0 else ("open", None) if p >= 100 else ("position", int(p))
        return ("open", None) if s == "open" else ("close", None)
    return None


def changes(states: list[State], domain: str, dim: bool, pos: bool, start: dt.datetime) -> list[tuple[dt.datetime, str, int | None]]:
    """The real changes after `start` (the state before it is where they start from), a burst of them one change."""
    base: tuple[str, int | None] | None = None
    raw: list[tuple[dt.datetime, str, int | None]] = []
    prev = None
    for st in sorted(states, key=lambda s: s.last_updated):
        act = action_of(domain, st, dim, pos)
        if act is None:
            continue
        if st.last_updated <= start:
            base = prev = act
            continue
        if act == prev:
            continue
        raw.append((st.last_updated, *act))
        prev = act
    merged: list[tuple[dt.datetime, str, int | None]] = []
    for c in raw:
        if merged and (c[0] - merged[-1][0]).total_seconds() < COALESCE:
            merged[-1] = c
        else:
            merged.append(c)
    out = []
    prev = base
    for c in merged:
        if (c[1], c[2]) != prev:
            out.append(c)
            prev = (c[1], c[2])
    return out


def on_intervals(states: list[State], end: dt.datetime) -> list[tuple[dt.datetime, dt.datetime, int | None]]:
    """When a light was on (from, to, brightness % when it came on)."""
    out: list[tuple[dt.datetime, dt.datetime, int | None]] = []
    since: dt.datetime | None = None
    bright: int | None = None
    for st in sorted(states, key=lambda s: s.last_updated):
        if st.state == "on":
            b = st.attributes.get("brightness")
            pct = max(1, min(100, round(b * 100 / 255))) if isinstance(b, (int, float)) and not isinstance(b, bool) and b > 0 else None
            if since is None:
                since, bright = st.last_updated, pct
            elif bright is None:
                bright = pct
        elif since is not None:
            out.append((since, st.last_updated, bright))
            since = bright = None
    if since is not None and since < end:
        out.append((since, end, bright))
    return out


# --- learning ---------------------------------------------------------------------------------------------------------
def drop_ours(history: dict[str, list[State]], ours: list[Any]) -> dict[str, list[State]]:
    """The simulation's own changes out of what it learns from (HA expert 28.10: two weeks away and the «family's
    evenings» were its own). HA's history keeps no context, so a state within OURS_AFTER of a command it sent to that
    device (`ours`: [entity, epoch seconds]) is its echo. A light's «on» right before its own «off» that was dropped goes
    too (verifier 28.10: after a restart the bus's late «on» of a light it lit belongs to no command; kept alone, the
    family's evening stayed «on» for days) — only when it isn't a person's (re-verifier 28.10: the family lit it at
    18:30, its planned «off» at 22:00 → the family's evening was lost): the light was last its own «on» and the «off» in
    between (a restart's bool(None)) lasted less than WATCH_OFF. A person's «on» keeps that «off» as its end."""
    sent: dict[str, list[float]] = {}
    for item in ours:
        if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[1], (int, float)):
            sent.setdefault(str(item[0]), []).append(float(item[1]))
    out: dict[str, list[State]] = {}
    for eid, states in history.items():
        times = sorted(sent.get(eid, []))
        if not times:
            out[eid] = states
            continue
        keep: list[State] = []
        light = eid.startswith("light.")
        mine: list[State] = []  # its own states (dropped), in order
        for st in sorted(states, key=lambda s: s.last_updated):
            ts = st.last_updated.timestamp()
            i = bisect_right(times, ts + 1) - 1
            if i < 0 or ts > times[i] + OURS_AFTER:
                keep.append(st)
                continue
            if light and st.state == "off":
                # Its own «off»: the «on» just before it is its own evening when it is the bus answering again for a
                # light it lit (its own «on» last, a short «off» in between) — not when a person lit it.
                j = len(keep)
                while j and keep[j - 1].state != "off":
                    j -= 1
                if j < len(keep):
                    if _bus_again(keep[j], keep[j - 1] if j else None, mine):
                        del keep[j:]
                    else:
                        keep.append(st)  # a person's evening: it did end then (dropped, it stayed «on» for days)
            mine.append(st)
        out[eid] = keep
    return out


def _bus_again(first_on: State, off: State | None, mine: list[State]) -> bool:
    """The «on» run from `first_on` is the bus re-reporting a light it lit itself: the last of its own states before it
    was «on», and the «off» right before (bool(None) while HA restarts) lasted less than WATCH_OFF."""
    t = first_on.last_updated
    last = next((m for m in reversed(mine) if m.last_updated <= t), None)
    if last is None or last.state == "off":
        return False
    if off is None or off.last_updated < last.last_updated:
        return True  # nothing in between: still its own evening
    return (t - off.last_updated).total_seconds() < WATCH_OFF


def build_model(
    history: dict[str, list[State]],
    info: dict[str, dict[str, Any]],
    rooms: dict[str, tuple[str, str]],
    start: dt.datetime,
    end: dt.datetime,
) -> dict[str, Any]:
    """History of [start, end) → what the family's days look like. `rooms`: entity → (room key, room name)."""
    firsts = [min(s.last_updated for s in v) for k, v in history.items() if v and k in info]
    data_start = max(start, min(firsts)) if firsts else end
    d0, m0 = day_min(data_start)
    first = d0 if m0 == 0 else d0 + dt.timedelta(days=1)  # only whole days are counted
    last = day_min(end)[0] - dt.timedelta(days=1)
    days = [first + dt.timedelta(days=i) for i in range((last - first).days + 1)] if last >= first else []
    n = len(days)
    weekend = [d.weekday() >= 5 for d in days]
    n_we = sum(weekend)
    n_wd = n - n_we
    grid0 = at(first, 0)

    def slots_of(a: dt.datetime, b: dt.datetime) -> range:
        # The slots whose middle the interval covers.
        ka = math.ceil((a - grid0).total_seconds() / (SLOT * 60) - 0.5)
        kb = math.floor((b - grid0).total_seconds() / (SLOT * 60) - 0.5)
        return range(max(ka, 0), min(kb, n * SLOTS - 1) + 1)

    ev = range(EVENING[0] // SLOT, EVENING[1] // SLOT)
    lights: dict[str, dict[str, Any]] = {}
    room_slots: dict[str, set[int]] = {}
    for eid, meta in info.items():
        if meta["domain"] != "light":
            continue
        marks: set[int] = set()
        bright: list[int] = []
        for a, b, pct in on_intervals(history.get(eid, []), end):
            marks.update(slots_of(a, b))
            if pct is not None and meta["dim"] and EVENING[0] - 60 <= day_min(a)[1] < EVENING[1]:
                bright.append(pct)
        wd, we = [0] * SLOTS, [0] * SLOTS
        for k in marks:
            (we if weekend[k // SLOTS] else wd)[k % SLOTS] += 1
        key = rooms.get(eid, (eid, eid))[0]
        room_slots.setdefault(key, set()).update(marks)
        lights[eid] = {
            "room": key,
            "wd": [c / n_wd if n_wd else 0.0 for c in wd],
            "we": [c / n_we if n_we else 0.0 for c in we],
            "all": [(a + b) / n if n else 0.0 for a, b in zip(wd, we, strict=True)],
            "n": {"wd": n_wd, "we": n_we},
            "evening": sum(1 for k in marks if k % SLOTS in ev),
            "dim": meta["dim"],
            "bright": int(statistics.median(bright)) if bright else None,
        }
    evenings: set[int] = set()
    last_light: dict[int, int] = {}
    room_out: dict[str, dict[str, Any]] = {}
    for key, marks in room_slots.items():
        for k in marks:
            if k % SLOTS in ev:
                evenings.add(k // SLOTS)
            if k % SLOTS >= ev.start:
                last_light[k // SLOTS] = max(last_light.get(k // SLOTS, 0), min(CAP_END, (k % SLOTS + 1) * SLOT))
        # A room's evenings: its lit runs, the part of them in the evening.
        durations: list[int] = []
        run_ev = 0
        prev: int | None = None
        for k in sorted(marks):
            if prev is not None and k - prev == 1:
                run_ev += k % SLOTS in ev
            else:
                if run_ev:
                    durations.append(run_ev * SLOT)
                run_ev = int(k % SLOTS in ev)
            prev = k
        if run_ev:
            durations.append(run_ev * SLOT)
        mine = sorted((e for e, x in lights.items() if x["room"] == key), key=lambda e: -lights[e]["evening"])
        name = next((rooms[e][1] for e in mine if e in rooms), mine[0] if mine else key)
        room_out[key] = {
            "name": name,
            "lights": mine,
            "durations": durations,
            "weight": sum(1 for k in marks if k % SLOTS in ev) / n if n else 0.0,
            "total": len(marks) * SLOT,
        }
    covers: dict[str, dict[str, Any]] = {}
    for eid, meta in info.items():
        if meta["domain"] != "cover":
            continue
        opens: list[float] = []
        closes: list[float] = []
        positions: list[int] = []
        for t, do, value in changes(history.get(eid, []), "cover", False, meta["pos"], start):
            m = day_min(t)[1]
            opening = do == "open" or (do == "position" and (value or 0) > 50)
            if opening and MORNING[0] <= m < MORNING[1]:
                opens.append(m)
            elif not opening and AFTERNOON[0] <= m < AFTERNOON[1]:
                closes.append(m)
                if do == "position":
                    positions.append(int(value or 0))
        covers[eid] = {"room": rooms.get(eid, (eid, eid))[0], "open": opens, "close": closes, "positions": positions}
    return {"days": n, "evenings": len(evenings), "last_light": sorted(last_light.values()), "lights": lights, "rooms": room_out, "covers": covers}


# --- tonight ------------------------------------------------------------------------------------------------------------
def _profile(light: dict[str, Any], weekend: bool) -> list[float]:
    kind = "we" if weekend else "wd"
    return light[kind] if light["n"][kind] >= 2 else light["all"]


def learned_sessions(model: dict[str, Any], day: dt.date, dark: float, end: float, rng: random.Random) -> list[dict[str, Any]]:
    """Where and when the rooms are lit tonight: drawn from each room's profile, never a copy of one day."""
    weekend = day.weekday() >= 5
    out: list[dict[str, Any]] = []
    s0, s1 = math.ceil(dark / SLOT), math.ceil(min(end, CAP_END) / SLOT)
    for key, room in model["rooms"].items():
        lights = [e for e in room["lights"] if model["lights"][e]["evening"] > 0][:LIGHTS_PER_ROOM]
        if not lights:
            continue  # daytime-only rooms (a desk lamp 9–18) never take part
        prof = [max(_profile(model["lights"][e], weekend)[s] for e in lights) for s in range(SLOTS)]
        runs: list[tuple[int, int]] = []
        a = None
        for s in range(s0, s1 + 1):
            if s < s1 and prof[s] >= P_MIN:
                a = s if a is None else a
            elif a is not None:
                runs.append((a, s))
                a = None
        for a, b in runs:
            if rng.random() >= max(prof[a:b]):
                continue  # a room lit 6 evenings of 10 is lit on about 6 of 10 nights
            frm = max(dark, a * SLOT + rng.uniform(-JITTER, JITTER))
            durs = room["durations"] or [(b - a) * SLOT]
            length = max(SLOT, rng.choice(durs) + rng.uniform(-JITTER, JITTER))
            to = min(frm + length, b * SLOT + 2 * JITTER, CAP_END)
            if to - frm >= SLOT:
                out.append({"room": key, "name": room["name"], "lights": lights, "from": frm, "to": to, "weight": room["weight"]})
    return out


def sun_rooms(model: dict[str, Any]) -> list[str]:
    """The rooms «по солнцу» lights: the ones used most; with no history — a living room / kitchen by name, then the one
    with most lights."""
    rooms = model["rooms"]
    return sorted((k for k in rooms if rooms[k]["lights"]), key=lambda k: (-rooms[k]["total"], not _LIVING.search(rooms[k]["name"] or ""), -len(rooms[k]["lights"]), rooms[k]["name"]))


def sun_sessions(model: dict[str, Any], dark: float, rng: random.Random) -> list[dict[str, Any]]:
    """A calm evening: the main room from dusk to about 22:30, another for an hour or two around 19:30."""
    out = []
    for i, key in enumerate(sun_rooms(model)[:2]):
        room = model["rooms"][key]
        if i == 0:
            frm = dark + rng.uniform(0, 30)
            to = max(frm + 45, 1110 + rng.uniform(-30, 30))
        else:
            frm = max(dark + rng.uniform(20, 50), 930 + rng.uniform(-30, 30))
            to = frm + rng.uniform(60, 120)
        to = min(to, CAP_END)
        if to - frm >= 20:
            out.append({"room": key, "name": room["name"], "lights": room["lights"][:1], "from": frm, "to": to, "weight": 2 - i})
    return out


def cap_rooms(cands: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """At most `cap` rooms lit at once — the rooms used most go first."""
    kept: list[dict[str, Any]] = []
    for c in sorted(cands, key=lambda x: (-x["weight"], x["from"])):
        points = [c["from"], *(x["from"] for x in kept if c["from"] <= x["from"] < c["to"])]
        if all(sum(1 for x in kept if x["from"] <= p < x["to"]) < cap for p in points):
            kept.append(c)
    return sorted(kept, key=lambda x: x["from"])


def plan_day(
    model: dict[str, Any],
    day: dt.date,
    sun: tuple[dt.datetime, dt.datetime] | None,
    now: dt.datetime,
    rng: random.Random,
    mode: str = "learned",
    cap: int = MAX_ROOMS,
    lights_on: bool = True,
    covers_on: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The day's events (from now on) and what it is in words: the lit rooms tonight, when the blinds move."""
    events: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    lights_by = "off"
    if lights_on:
        if sun is None:
            lights_by = "no_location"
        elif mode == "learned" and model["evenings"] < MIN_EVENINGS:
            lights_by = "few"
        else:
            lights_by = mode
            dark = rel_min(day, sun[1]) - DARK_BEFORE
            ends = model["last_light"]
            end = min(CAP_END, statistics.median(ends)) if mode == "learned" and ends else DEFAULT_END
            if end > dark:
                found = learned_sessions(model, day, dark, end, rng) if mode == "learned" else sun_sessions(model, dark, rng)
                sessions = cap_rooms(found, cap)
    for s in sessions:
        frm, to = at(day, s["from"]), at(day, s["to"])
        if frm < now < to - dt.timedelta(minutes=20):
            frm = now + dt.timedelta(minutes=rng.uniform(5, 15))  # already evening: the room lights up soon
        for eid in s["lights"]:
            light = model["lights"].get(eid, {})
            value = (light.get("bright") if mode == "learned" else SUN_BRIGHTNESS) if light.get("dim") else None
            events.append({"at": frm + dt.timedelta(seconds=rng.uniform(0, 90)), "entity_id": eid, "do": "on", "value": value})
            events.append({"at": to + dt.timedelta(seconds=rng.uniform(0, 90)), "entity_id": eid, "do": "off", "value": None})
    covers_by: set[str] = set()
    moves: dict[str, list[dt.datetime]] = {"open": [], "close": []}
    if covers_on:
        jit: dict[str, tuple[float, float]] = {}
        for eid, c in model["covers"].items():
            jo, jc = jit.setdefault(c["room"], (rng.uniform(-JITTER, JITTER), rng.uniform(-JITTER, JITTER)))  # a room's blinds together
            for kind, learned, by_sun, j in (
                ("open", c["open"], rel_min(day, sun[0]) + 30 if sun else None, jo),
                ("close", c["close"], rel_min(day, sun[1]) if sun else None, jc),
            ):
                if mode == "learned" and len(learned) >= MIN_EVENINGS:
                    m, by = statistics.median(learned) + j, "history"
                elif by_sun is not None:
                    m, by = by_sun + j * 10 / JITTER, "sun"
                else:
                    continue
                covers_by.add(by)
                do, value = (kind, None)
                if kind == "close" and by == "history" and len(c["positions"]) * 2 > len(learned):
                    do, value = "position", int(statistics.median(c["positions"]))
                t = at(day, m) + dt.timedelta(seconds=rng.uniform(0, 60))
                moves[kind].append(t)
                events.append({"at": t, "entity_id": eid, "do": do, "value": value})
    events = sorted((e for e in events if e["at"] > now), key=lambda e: e["at"])[:MAX_EVENTS]
    summary = {
        "lights": lights_by,
        "evenings": model["evenings"],
        "rooms": [{"name": s["name"], "from": at(day, s["from"]).isoformat(timespec="minutes"), "to": at(day, s["to"]).isoformat(timespec="minutes")} for s in sessions],
        "covers": {k: min(v).isoformat(timespec="minutes") if v else None for k, v in moves.items()} | {"by": "mixed" if len(covers_by) > 1 else next(iter(covers_by), None)},
    }
    return events, summary


# --- the engine ---------------------------------------------------------------------------------------------------------
class Presence:
    def __init__(self, hass: HomeAssistant, owns: Callable[[str], bool]) -> None:
        self.hass = hass
        self.owns = owns
        self.store = HaufloStore(hass, STORE_KEY)
        self.data: dict[str, Any] = {**DEFAULT, "log": [], "lit": [], "lit_at": {}, "ours": []}
        self.rng = random.Random()
        self.status = "off"
        self.tonight: dict[str, Any] | None = None
        self.pending: dict[int, tuple[CALLBACK_TYPE, dict[str, Any]]] = {}
        self._n = 0
        self._daily: CALLBACK_TYPE | None = None
        self._off: CALLBACK_TYPE | None = None
        self._ctx: deque[str] = deque(maxlen=400)
        self._sent: dict[str, float] = {}
        self._waits: dict[str, CALLBACK_TYPE] = {}
        self._claims: dict[str, CALLBACK_TYPE] = {}  # lit lights whose first «on» after `_arm` is its own (`_claim`)
        # Lights it lit watched after it was switched off (`_watch_out`) — not cleared by `_cancel`, only on unload.
        self._outs: dict[str, CALLBACK_TYPE] = {}
        self._started = False
        self._t0: float | None = None  # set up while HA was starting (a restart): when
        self._boot: float | None = None  # and when HA had started then

    # --- what takes part ----------------------------------------------------------------------------------------------
    def _pickable(self, eid: str) -> bool:
        if eid.split(".")[0] not in ("light", "cover") or self.hass.states.get(eid) is None:
            return False
        ent = er.async_get(self.hass).async_get(eid)
        # A group's lamps take part, not the group (it would switch them twice).
        if ent is None or ent.disabled_by or ent.hidden_by or ent.platform == "group":
            return False
        return bool(self.owns(eid))

    def candidates(self, every: bool = False) -> dict[str, dict[str, Any]]:
        """The lights / blinds that take part (`every`: whatever is switched off on the page too)."""
        out: dict[str, dict[str, Any]] = {}
        for domain, on in (("light", self.data["lights"]), ("cover", self.data["covers"])):
            if not on and not every:
                continue
            for eid in self.hass.states.async_entity_ids(domain):
                if not self._pickable(eid):
                    continue
                a = self.hass.states.get(eid).attributes
                modes = a.get("supported_color_modes") or []
                out[eid] = {"domain": domain, "dim": domain == "light" and any(m != "onoff" for m in modes), "pos": domain == "cover" and bool(int(a.get("supported_features") or 0) & 4)}
        return out

    def rooms(self, eids: list[str]) -> dict[str, tuple[str, str]]:
        ents, devs, areas = er.async_get(self.hass), dr.async_get(self.hass), ar.async_get(self.hass)
        out = {}
        for eid in eids:
            ent = ents.async_get(eid)
            area_id = ent.area_id if ent else None
            if ent and not area_id and ent.device_id and (dev := devs.async_get(ent.device_id)):
                area_id = dev.area_id
            area = areas.async_get_area(area_id) if area_id else None
            st = self.hass.states.get(eid)
            out[eid] = (area.id, area.name) if area else (eid, (st.attributes.get("friendly_name") if st else None) or eid)
        return out

    def _sun(self, day: dt.date) -> tuple[dt.datetime, dt.datetime] | None:
        from .automations import _UNSET  # noqa: PLC0415
        import astral  # noqa: PLC0415
        import astral.sun  # noqa: PLC0415

        c = self.hass.config
        if (round(float(c.latitude), 5), round(float(c.longitude), 5)) in _UNSET:
            return None
        obs = astral.Observer(c.latitude, c.longitude, c.elevation)
        tz = dt_util.get_time_zone(c.time_zone) or dt_util.get_default_time_zone()
        try:
            return astral.sun.sunrise(obs, date=day, tzinfo=tz), astral.sun.sunset(obs, date=day, tzinfo=tz)
        except ValueError:  # polar day / night
            return None

    # --- lifecycle ----------------------------------------------------------------------------------------------------
    async def async_start(self) -> None:
        stored = await self.store.async_load()
        if isinstance(stored, dict):
            self.data = {**DEFAULT, **{k: v for k, v in stored.items() if k in DEFAULT}}
            for k in ("log", "lit", "ours", "skip_rooms"):
                if not isinstance(self.data.get(k), list):
                    self.data[k] = []
            if not isinstance(self.data.get("lit_at"), dict):
                self.data["lit_at"] = {}
            self.data["skip_rooms"] = [x for x in self.data["skip_rooms"] if isinstance(x, str)][:MAX_SKIP]
            if self.data.get("mode") not in MODES:
                self.data["mode"] = "learned"
        self._started = True
        if self.hass.state is not CoreState.running:
            # A restart: KNX lights read «off» until the bus answers (`_restarting`), and the states HA writes while it
            # starts are no person's touch (`_touched`).
            self._t0 = time.time()

            @callback
            def _up(_hass: HomeAssistant) -> None:
                self._boot = time.time()

            async_at_started(self.hass, _up)
        if not self.data["enabled"]:
            if self.data["lit"]:
                # Switched off while lights it lit were still watched (a restart in between): watched again.
                @callback
                def _out(_hass: HomeAssistant) -> None:
                    if self._started and not self.data["enabled"]:
                        self.hass.async_create_task(self._lights_out_saved(), eager_start=True)

                async_at_started(self.hass, _out)
            return
        if self._expired():
            await self._switch_off("auto_off")
            return

        @callback
        def _go(_hass: HomeAssistant) -> None:
            if self._started and self.data["enabled"]:
                self.hass.async_create_task(self._arm(), eager_start=True)

        # The recorder and the KNX entities are there once HA has started.
        async_at_started(self.hass, _go)

    @callback
    def async_stop(self) -> None:
        self._started = False
        self._cancel()
        self._stop_outs()

    def _stop_outs(self) -> None:
        for done in list(self._outs.values()):
            done()
        self._outs.clear()

    def _cancel(self) -> None:
        for unsub, _ in self.pending.values():
            unsub()
        self.pending.clear()
        for unsub in list(self._waits.values()):
            unsub()
        self._waits.clear()
        for unsub in list(self._claims.values()):
            unsub()
        self._claims.clear()
        for name in ("_daily", "_off"):
            if (unsub := getattr(self, name)) is not None:
                unsub()
                setattr(self, name, None)

    def _off_at(self) -> dt.datetime | None:
        until = self.data.get("until")
        if not isinstance(until, str) or not _DATE.fullmatch(until):
            return None
        return dt_util.start_of_local_day(dt.date.fromisoformat(until) + dt.timedelta(days=1))  # «до 5 ноября» = the 5th included

    def _expired(self) -> bool:
        off = self._off_at()
        return off is not None and dt_util.now() >= off

    async def _arm(self) -> None:
        """On: today's plan from now, the 00:05 re-plan, the switch-off after the «until» day."""
        self._cancel()
        if not self._started or not self.data["enabled"]:
            return
        if self._expired():
            await self._switch_off("auto_off")
            return
        self._daily = async_track_time_change(self.hass, self._replan, hour=REPLAN[0], minute=REPLAN[1], second=0)
        if (off := self._off_at()) is not None:
            self._off = async_track_point_in_time(self.hass, self._auto_off, off)
        self._stop_outs()  # on again: what it lit is handled below
        await self.plan()
        # What it left on before a restart or a change of plan is switched off soon, not left burning all night — a
        # light whose state isn't known yet (KNX answers a while after a restart) too: it's switched off once it is.
        # Left alone only when tonight's plan moves it within 20 minutes anyway (a session going on) — not when its next
        # event is hours away (it would burn until then).
        soon = dt_util.now() + dt.timedelta(minutes=20)
        for eid in list(self.data["lit"]):
            # Its own: the states of a light it lit that come right after a restart (the bus's answer) are never learned
            # as the family's evening (verifier 28.10).
            self._remember(eid)
            st = self.hass.states.get(eid)
            if st is None or st.state != "on":
                self._claim(eid)
            nxt = min((ev["at"] for _, ev in self.pending.values() if ev["entity_id"] == eid), default=None)
            if nxt is not None and nxt <= soon:
                continue
            if st is not None and st.state == "on":
                self._schedule({"at": dt_util.now() + dt.timedelta(minutes=self.rng.uniform(2, 10)), "entity_id": eid, "do": "off", "value": None})
            else:
                # «Off», unknown or not there yet. Right after a restart HA's KNX light reads «off» until the bus answers
                # (bool(None) — live check 28.10): watched for WATCH_OFF and switched off if it turns out «on».
                self._wait(eid, dt_util.now() + dt.timedelta(seconds=WATCH_OFF))
        if self.data["lit"]:
            self.store.async_delay_save(lambda: self.data, 5)

    async def _replan(self, _now: dt.datetime) -> None:
        if self._expired():
            await self._switch_off("auto_off")
            return
        await self.plan()  # the new day's; yesterday's late «off»s stay planned

    async def _auto_off(self, _now: dt.datetime) -> None:
        self._off = None
        await self._switch_off("auto_off")

    async def _switch_off(self, why: str) -> None:
        self._cancel()
        was = self.data["enabled"]
        self.data["enabled"] = False
        self.status = "off"
        self.tonight = None
        await self._lights_out()
        if was:
            self._log({"sys": why})
        await self.store.async_save(self.data)

    async def _lights_out(self, final: bool = False) -> None:
        """Off (by a person, its date, the agent unloaded): the lights it lit and no person changed since go off (HA
        expert 28.10: switched off, the lamps it had lit burned on).

        A light it lit whose state isn't known — not there yet, unknown / unavailable, or «off» within WATCH_OFF of a
        restart (KNX reads bool(None) → «off» until the bus answers, live check 28.10) — is not let go (verifier 28.10:
        its date passed overnight, HA restarted, the bus answered «on» later and it burned on): it stays in `lit` and is
        watched (`_watch_out`, survives `_cancel`) for WATCH_OFF, switched off once it reads «on». `final` (the agent
        unloads — no watch can outlive it): such a light is sent «off» at once instead; an «off» to a light that is off
        changes nothing, one left burning with nobody to switch it off does harm."""
        for eid in list(self.data["lit"]):
            st = self.hass.states.get(eid)
            if st is not None and st.state == "on":
                person = self._person_since(st)
                self._unlit(eid)
                if self._pickable(eid) and not person:
                    await self._off_now(eid, st)
                continue
            if st is not None and st.state == "off" and not self._restarting():
                self._unlit(eid)  # known «off»
                continue
            if not final:
                self._watch_out(eid)
                continue
            self._unlit(eid)
            if st is not None and self._pickable(eid):
                await self._off_now(eid, st)

    async def _lights_out_saved(self) -> None:
        await self._lights_out()
        await self.store.async_save(self.data)

    async def _off_now(self, eid: str, st: State) -> None:
        ev = {"entity_id": eid, "do": "off", "value": None}
        name = st.attributes.get("friendly_name") or eid
        if await self._send(ev, name):
            key, room = self.rooms([eid])[eid]
            self._log({**_brief(ev), "name": name, "room": room if key != eid else None})

    def _restarting(self) -> bool:
        """Set up during HA's start less than WATCH_OFF ago: a KNX light's «off» may be only «the bus hasn't answered»."""
        return self._t0 is not None and time.time() < self._t0 + WATCH_OFF

    def _watch_out(self, eid: str) -> None:
        """Switched off, a light it lit isn't known yet: switched off if it reads «on» within WATCH_OFF (unless a person
        changed it since it lit it), let go after that. Kept in `lit` meanwhile (in the store: a restart watches again)."""
        if eid in self._outs:
            return
        unsubs: list[CALLBACK_TYPE] = []

        def done() -> None:
            self._outs.pop(eid, None)
            for unsub in unsubs:
                unsub()

        async def settle(st: State | None) -> None:
            if eid not in self.data["lit"] or self.data["enabled"]:
                return  # let go meanwhile, or on again (`_arm` handles it)
            on = st is not None and st.state == "on"
            person = on and self._person_since(st)
            self._unlit(eid)
            if on and not person and self._pickable(eid):
                await self._off_now(eid, st)
            await self.store.async_save(self.data)

        @callback
        def changed(event: Any) -> None:
            new = event.data.get("new_state")
            if new is None or new.state != "on" or eid not in self._outs:
                return
            done()
            self.hass.async_create_task(settle(new), eager_start=True)

        @callback
        def expired(_now: dt.datetime) -> None:
            if eid in self._outs:
                done()
                self.hass.async_create_task(settle(self.hass.states.get(eid)), eager_start=True)

        unsubs.append(async_track_state_change_event(self.hass, [eid], changed))
        unsubs.append(async_track_point_in_time(self.hass, expired, dt_util.now() + dt.timedelta(seconds=WATCH_OFF)))
        self._outs[eid] = done

    async def async_lights_out(self) -> None:
        """The agent unloaded or removed: nothing it lit keeps burning with nobody left to switch it off."""
        self.async_stop()
        await self._lights_out(final=True)
        await self.store.async_save(self.data)

    async def _history(self, entity_ids: list[str], start: dt.datetime, end: dt.datetime) -> dict[str, list[State]] | None:
        """HA's own history of these entities (None: HA keeps none). In the recorder's executor, never the loop."""
        if "recorder" not in self.hass.config.components:
            return None
        from homeassistant.components.recorder import get_instance, history  # noqa: PLC0415

        got = await get_instance(self.hass).async_add_executor_job(
            history.get_significant_states, self.hass, start, end, entity_ids, None, True, False, False, False
        )
        return {k: [s for s in v if isinstance(s, State)] for k, v in got.items()}

    async def plan(self) -> None:
        """Learns from the last 14 days, plans today (from now) and adds it."""
        now = dt_util.now()
        day = dt_util.as_local(now).date()
        info = self.candidates()
        # Rooms the family left out (a façade, a guest room — final review 28.10) are never planned.
        skip = set(self.data.get("skip_rooms") or [])
        if skip and info:
            where = self.rooms(sorted(info))
            info = {e: m for e, m in info.items() if where[e][0] not in skip}
        if not info:
            self.status, self.tonight = "no_devices", None
            return
        try:
            history = await self._history(sorted(info), now - dt.timedelta(days=LEARN_DAYS), now)
        except Exception as err:  # noqa: BLE001 — the recorder failing never stops the agent
            _LOGGER.warning("Hauflo presence: history unreadable: %s", err)
            history = None
        if history is None:
            self.status, self.tonight = "no_recorder", None
            return
        history = drop_ours(history, self.data.get("ours") or [])
        rooms = self.rooms(sorted(info))
        model = await self.hass.async_add_executor_job(build_model, history, info, rooms, now - dt.timedelta(days=LEARN_DAYS), now)
        n_rooms = len({rooms[e][0] for e, m in info.items() if m["domain"] == "light"})
        cap = min(MAX_ROOMS, max(1, n_rooms - 1))  # never every room at once
        events, self.tonight = plan_day(model, day, self._sun(day), now, self.rng, self.data["mode"], cap, self.data["lights"], self.data["covers"])
        self.status = self.tonight["lights"] if self.tonight["lights"] in ("few", "no_location") else "on"
        for ev in events:
            self._schedule(ev)

    def _schedule(self, ev: dict[str, Any]) -> None:
        self._n += 1
        key = self._n
        ev["key"] = key

        async def run(_now: dt.datetime) -> None:
            if (item := self.pending.pop(key, None)) is not None:
                await self.fire(item[1])

        self.pending[key] = (async_track_point_in_time(self.hass, run, ev["at"]), ev)

    # --- one event ----------------------------------------------------------------------------------------------------
    def _touched(self, st: State) -> bool:
        """Someone at home changed it in the last 10 minutes (not the simulation's own command and its echo)."""
        if (dt_util.utcnow() - st.last_updated).total_seconds() > MANUAL:
            return False
        if self._boot is not None and st.last_updated.timestamp() <= self._boot:
            return False  # written while HA was starting (verifier 28.10): a restart's state, no person's touch
        if st.context.id in self._ctx or (st.context.parent_id and st.context.parent_id in self._ctx):
            return False
        sent = self._sent.get(st.entity_id)
        return not (sent is not None and sent - 1 <= st.last_updated.timestamp() <= sent + OURS_AFTER)

    def _person_since(self, st: State) -> bool:
        """A person (an HA user: the app, HA's own buttons) changed a light after the simulation lit it — someone is
        home, it's theirs now. A restart's fresh states, the KNX bus and automations carry no user: they don't count
        (HA expert 28.10: after a restart every state looked «touched» and its lights burned all night)."""
        c = st.context
        if not c.user_id or c.id in self._ctx or (c.parent_id and c.parent_id in self._ctx):
            return False
        return st.last_updated.timestamp() > float(self.data["lit_at"].get(st.entity_id) or 0) + 1

    def _unlit(self, eid: str) -> None:
        self.data["lit"] = [e for e in self.data["lit"] if e != eid]
        self.data["lit_at"] = {k: v for k, v in self.data["lit_at"].items() if k != eid}

    def _claim(self, eid: str) -> None:
        """A light it lit not «on» yet at `_arm`: its first «on» report within WATCH_OFF is the bus answering for the
        simulation's own light (re-verifier 28.10: +201 s, past OURS_AFTER, a person's «off» after it — that «on» was
        learned as the family's evening), remembered as its own. An «on» by an HA user (the app) stays a person's."""
        if eid in self._claims:
            return
        unsubs: list[CALLBACK_TYPE] = []

        def done() -> None:
            self._claims.pop(eid, None)
            for unsub in unsubs:
                unsub()

        @callback
        def changed(event: Any) -> None:
            new = event.data.get("new_state")
            if new is None or new.state != "on" or eid not in self._claims:
                return
            done()
            if not new.context.user_id:
                self._remember(eid)
                self.store.async_delay_save(lambda: self.data, 5)

        @callback
        def expired(_now: dt.datetime) -> None:
            if eid in self._claims:
                done()

        unsubs.append(async_track_state_change_event(self.hass, [eid], changed))
        unsubs.append(async_track_point_in_time(self.hass, expired, dt_util.now() + dt.timedelta(seconds=WATCH_OFF)))
        self._claims[eid] = done

    def _wait(self, eid: str, until: dt.datetime | None = None) -> None:
        """A light it lit whose state isn't known yet: switched off once it reads «on», let go once it reads «off» — or,
        with `until` (it reads «off» already, maybe only until the bus answers), let go at that time."""
        if eid in self._waits:
            return
        unsubs: list[CALLBACK_TYPE] = []

        def done() -> None:
            self._waits.pop(eid, None)
            for unsub in unsubs:
                unsub()

        @callback
        def changed(event: Any) -> None:
            new = event.data.get("new_state")
            if new is None or new.state in ("unavailable", "unknown") or eid not in self._waits:
                return
            if new.state == "on":
                done()
                self._schedule({"at": dt_util.now() + dt.timedelta(seconds=self.rng.uniform(30, 120)), "entity_id": eid, "do": "off", "value": None})
            elif until is None:
                done()
                self._unlit(eid)

        @callback
        def expired(_now: dt.datetime) -> None:
            if eid in self._waits:
                done()
                if (st := self.hass.states.get(eid)) is None or st.state != "on":
                    self._unlit(eid)

        unsubs.append(async_track_state_change_event(self.hass, [eid], changed))
        if until is not None:
            unsubs.append(async_track_point_in_time(self.hass, expired, until))
        self._waits[eid] = done

    async def _send(self, ev: dict[str, Any], name: str) -> bool:
        eid = ev["entity_id"]
        domain = eid.split(".")[0]
        service, data = _service(domain, ev["do"], ev["value"])
        ctx = Context()
        self._ctx.append(ctx.id)
        self._sent[eid] = time.time()
        try:
            await self.hass.services.async_call(domain, service, {"entity_id": eid, **data}, blocking=True, context=ctx)
        except Exception as err:  # noqa: BLE001 — one device failing never stops the rest
            _LOGGER.warning("Hauflo presence: %s %s failed: %s", eid, service, err)
            self._log({**_brief(ev), "name": name, "skip": "error"})
            return False
        self._remember(eid)
        return True

    def _remember(self, eid: str) -> None:
        """Remembered for two weeks: what it did itself is never learned as the family's evenings."""
        cut = time.time() - (LEARN_DAYS + 1) * 86400
        kept = [x for x in self.data.get("ours") or [] if isinstance(x, list) and len(x) == 2 and isinstance(x[1], (int, float)) and x[1] >= cut]
        self.data["ours"] = [*kept[-(MAX_OURS - 1) :], [eid, round(time.time(), 1)]]

    async def fire(self, ev: dict[str, Any]) -> None:
        if not self.data["enabled"]:
            return
        if self._expired():
            await self._switch_off("auto_off")
            return
        eid = ev["entity_id"]
        domain = eid.split(".")[0]
        # The «off» of a light it lit itself always goes — only a person who changed it since keeps it on.
        mine = domain == "light" and ev["do"] == "off" and eid in self.data["lit"]
        if not mine and ((domain == "light" and not self.data["lights"]) or (domain == "cover" and not self.data["covers"])):
            return
        # A room left out since the plan: nothing new there (an «off» of what it lit still goes).
        if ev["do"] != "off" and self.rooms([eid])[eid][0] in set(self.data.get("skip_rooms") or []):
            return
        st = self.hass.states.get(eid)
        if mine and (st is None or st.state in ("unavailable", "unknown")):
            if eid not in self._waits:
                self._log({**_brief(ev), "name": (st.attributes.get("friendly_name") if st else None) or eid, "skip": "offline"})
            self._wait(eid)
            return
        if not self._pickable(eid) or st is None:
            self._log({**_brief(ev), "name": eid, "skip": "gone"})
            if mine:
                self._unlit(eid)
            return
        name = st.attributes.get("friendly_name") or eid
        if st.state in ("unavailable", "unknown"):
            self._log({**_brief(ev), "name": name, "skip": "offline"})
            return
        if self._person_since(st) if mine else self._touched(st):
            self._log({**_brief(ev), "name": name, "skip": "manual"})
            if mine:
                self._unlit(eid)
            return
        info = self.candidates(every=True).get(eid) or {"dim": False, "pos": False}
        now_is = action_of(domain, st, info["dim"], info["pos"])
        if now_is == (ev["do"], ev["value"]) or (ev["do"] == "on" and ev["value"] is None and now_is and now_is[0] == "on"):
            if mine:
                self._unlit(eid)  # off already
            return  # already so
        if not await self._send(ev, name):
            return
        if domain == "light":
            self._unlit(eid)
            if ev["do"] == "on":
                self.data["lit"] = [*self.data["lit"], eid]
                self.data["lit_at"] = {**self.data["lit_at"], eid: round(time.time(), 1)}
        key, room = self.rooms([eid])[eid]
        self._log({**_brief(ev), "name": name, "room": room if key != eid else None})

    def _log(self, item: dict[str, Any]) -> None:
        self.data["log"] = [{"t": dt_util.now().isoformat(timespec="seconds"), **item}, *self.data["log"]][:LOG_SIZE]
        self.store.async_delay_save(lambda: self.data, 5)

    # --- the page -----------------------------------------------------------------------------------------------------
    async def set(self, body: dict[str, Any], user: str | None) -> dict[str, Any]:
        new = dict(self.data)
        for key in ("enabled", "lights", "covers"):
            if key in body:
                if not isinstance(body[key], bool):
                    raise Invalid(f"{key}: true or false")
                new[key] = body[key]
        if "mode" in body:
            if body["mode"] not in MODES:
                raise Invalid("mode: learned or sun")
            new["mode"] = body["mode"]
        today = dt_util.now().date()
        if "until" in body:
            v = body["until"]
            if v in (None, ""):
                new["until"] = None
            else:
                if not isinstance(v, str) or not _DATE.fullmatch(v):
                    raise Invalid("until: YYYY-MM-DD")
                try:
                    day = dt.date.fromisoformat(v)
                except ValueError as err:
                    raise Invalid("until: YYYY-MM-DD") from err
                if not today <= day <= today + dt.timedelta(days=MAX_UNTIL_DAYS):
                    raise Invalid(f"until: today … {MAX_UNTIL_DAYS} days on")
                new["until"] = v
        elif new["enabled"] and not self.data["enabled"] and new.get("until") and new["until"] < today.isoformat():
            new["until"] = None  # an old «until» doesn't switch a new absence off at once
        if "skip_rooms" in body:
            v = body["skip_rooms"]
            if not isinstance(v, list) or len(v) > MAX_SKIP or not all(isinstance(x, str) and 0 < len(x) <= 255 for x in v):
                raise Invalid("skip_rooms: a list of room ids")
            new["skip_rooms"] = list(dict.fromkeys(v))
        if not new["lights"] and not new["covers"]:
            raise Invalid("lights or covers: at least one")
        if new["enabled"] and not self.data["enabled"]:
            new["since"], new["by"] = dt_util.now().isoformat(timespec="seconds"), user
        turned = new["enabled"] != self.data["enabled"]
        self.data = new
        if turned and not new["enabled"]:
            self._cancel()
            await self._lights_out()  # what it lit goes off with it
        if turned:
            self._log({"sys": "on" if new["enabled"] else "off", **({"by": user} if user else {})})
        await self.store.async_save(self.data)
        if new["enabled"]:
            await self._arm()
        else:
            self._cancel()
            self.status, self.tonight = "off", None
        return self.answer()

    def answer(self) -> dict[str, Any]:
        info = self.candidates(every=True)
        nxt = sorted((ev for _, ev in self.pending.values()), key=lambda e: e["at"])
        name = lambda eid: (s.attributes.get("friendly_name") if (s := self.hass.states.get(eid)) else None) or eid  # noqa: E731
        off = self._off_at()
        # The rooms (HA areas) the plan can use, each on / left out by the family.
        skip = set(self.data.get("skip_rooms") or [])
        where = self.rooms(sorted(info))
        rooms: dict[str, dict[str, Any]] = {}
        for eid, meta in info.items():
            key, room_name = where[eid]
            if key == eid:
                continue  # a device in no room: not a room to leave out
            r = rooms.setdefault(key, {"id": key, "name": room_name, "lights": 0, "covers": 0, "on": key not in skip})
            r["lights" if meta["domain"] == "light" else "covers"] += 1
        return {
            **{k: self.data[k] for k in ("enabled", "until", "lights", "covers", "mode", "since")},
            "skip_rooms": list(self.data.get("skip_rooms") or []),
            "rooms": sorted(rooms.values(), key=lambda r: str(r["name"]).lower()),
            "status": self.status if self.data["enabled"] else "off",
            "tonight": self.tonight if self.data["enabled"] else None,
            "left": len(nxt),
            "next": [{**_brief(e), "at": e["at"].isoformat(timespec="seconds"), "name": name(e["entity_id"])} for e in nxt[:5]],
            "log": self.data["log"],
            "off_at": off.isoformat() if off else None,
            "devices": {"lights": sum(1 for v in info.values() if v["domain"] == "light"), "covers": sum(1 for v in info.values() if v["domain"] == "cover")},
            "min_evenings": MIN_EVENINGS,
            "max_rooms": MAX_ROOMS,
            "jitter_min": JITTER,
        }


def _brief(ev: dict[str, Any]) -> dict[str, Any]:
    return {"entity_id": ev["entity_id"], "do": ev["do"], "value": ev["value"]}


def _service(domain: str, do: str, value: int | None) -> tuple[str, dict[str, Any]]:
    if domain == "light":
        return ("turn_on", {"brightness_pct": value} if value else {}) if do == "on" else ("turn_off", {})
    if do == "position":
        return "set_cover_position", {"position": value}
    return ("open_cover" if do == "open" else "close_cover"), {}


class PresenceView(HomeAssistantView):
    url = "/api/hauflo/presence"
    name = "api:hauflo:presence"
    requires_auth = True

    def __init__(self, engine_of: Callable[[], Presence | None]) -> None:
        self.engine_of = engine_of

    async def get(self, request):
        engine = self.engine_of()
        if engine is None:
            return self.json_message("Hauflo agent not set up", 503)
        return self.json(engine.answer())

    async def post(self, request):
        engine = self.engine_of()
        if engine is None:
            return self.json_message("Hauflo agent not set up", 503)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict) or body.get("op") != "set":
            return self.json_message("op: set", 400)
        if set(body) - {"op", "enabled", "until", "lights", "covers", "mode", "skip_rooms"}:
            return self.json_message("only enabled, until, lights, covers, mode, skip_rooms", 400)
        if (no := await refuse(request)) is not None:
            return no  # «Кто может менять настройки дома» (house.py): admins and the people an admin allowed
        # A person's own switch, as HA's own buttons are; the tunnel can't reach this view (not in its allowlist).
        user = request["hass_user"]
        try:
            return self.json(await engine.set(body, user.name if user else None))
        except Invalid as err:
            return self.json_message(str(err), 400)
