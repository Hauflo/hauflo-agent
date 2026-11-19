"""Learns the evening light sessions of each room from the recorder history."""
from __future__ import annotations
from bisect import bisect_right
import datetime as dt
import math
import random
import statistics
from typing import Any
from homeassistant.core import State
from .common import action_of, afternoon, at, cap_end, coalesce, day_min, evening, jitter, lights_per_room, morning, ours_after, p_min, slot, slots, watch_off

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
        if merged and (c[0] - merged[-1][0]).total_seconds() < coalesce():
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
        if st.state == 'on':
            b = st.attributes.get('brightness')
            pct = max(1, min(100, round(b * 100 / 255))) if isinstance(b, (int, float)) and (not isinstance(b, bool)) and (b > 0) else None
            if since is None:
                since, bright = (st.last_updated, pct)
            elif bright is None:
                bright = pct
        elif since is not None:
            out.append((since, st.last_updated, bright))
            since = bright = None
    if since is not None and since < end:
        out.append((since, end, bright))
    return out

def drop_ours(history: dict[str, list[State]], ours: list[Any]) -> dict[str, list[State]]:
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
        light = eid.startswith('light.')
        mine: list[State] = []
        for st in sorted(states, key=lambda s: s.last_updated):
            ts = st.last_updated.timestamp()
            i = bisect_right(times, ts + 1) - 1
            if i < 0 or ts > times[i] + ours_after():
                keep.append(st)
                continue
            if light and st.state == 'off':
                j = len(keep)
                while j and keep[j - 1].state != 'off':
                    j -= 1
                if j < len(keep):
                    if _bus_again(keep[j], keep[j - 1] if j else None, mine):
                        del keep[j:]
                    else:
                        keep.append(st)
            mine.append(st)
        out[eid] = keep
    return out

def _bus_again(first_on: State, off: State | None, mine: list[State]) -> bool:
    t = first_on.last_updated
    last = next((m for m in reversed(mine) if m.last_updated <= t), None)
    if last is None or last.state == 'off':
        return False
    if off is None or off.last_updated < last.last_updated:
        return True
    return (t - off.last_updated).total_seconds() < watch_off()

def build_model(history: dict[str, list[State]], info: dict[str, dict[str, Any]], rooms: dict[str, tuple[str, str]], start: dt.datetime, end: dt.datetime) -> dict[str, Any]:
    """History of [start, end) → what the family's days look like."""
    firsts = [min((s.last_updated for s in v)) for k, v in history.items() if v and k in info]
    data_start = max(start, min(firsts)) if firsts else end
    d0, m0 = day_min(data_start)
    first = d0 if m0 == 0 else d0 + dt.timedelta(days=1)
    last = day_min(end)[0] - dt.timedelta(days=1)
    days = [first + dt.timedelta(days=i) for i in range((last - first).days + 1)] if last >= first else []
    n = len(days)
    weekend = [d.weekday() >= 5 for d in days]
    n_we = sum(weekend)
    n_wd = n - n_we
    grid0 = at(first, 0)

    def slots_of(a: dt.datetime, b: dt.datetime) -> range:
        ka = math.ceil((a - grid0).total_seconds() / (slot() * 60) - 0.5)
        kb = math.floor((b - grid0).total_seconds() / (slot() * 60) - 0.5)
        return range(max(ka, 0), min(kb, n * slots() - 1) + 1)
    ev = range(evening()[0] // slot(), evening()[1] // slot())
    lights: dict[str, dict[str, Any]] = {}
    room_slots: dict[str, set[int]] = {}
    for eid, meta in info.items():
        if meta['domain'] != 'light':
            continue
        marks: set[int] = set()
        bright: list[int] = []
        for a, b, pct in on_intervals(history.get(eid, []), end):
            marks.update(slots_of(a, b))
            if pct is not None and meta['dim'] and (evening()[0] - 60 <= day_min(a)[1] < evening()[1]):
                bright.append(pct)
        wd, we = ([0] * slots(), [0] * slots())
        for k in marks:
            (we if weekend[k // slots()] else wd)[k % slots()] += 1
        key = rooms.get(eid, (eid, eid))[0]
        room_slots.setdefault(key, set()).update(marks)
        lights[eid] = {'room': key, 'wd': [c / n_wd if n_wd else 0.0 for c in wd], 'we': [c / n_we if n_we else 0.0 for c in we], 'all': [(a + b) / n if n else 0.0 for a, b in zip(wd, we, strict=True)], 'n': {'wd': n_wd, 'we': n_we}, 'evening': sum((1 for k in marks if k % slots() in ev)), 'dim': meta['dim'], 'bright': int(statistics.median(bright)) if bright else None}
    evenings: set[int] = set()
    last_light: dict[int, int] = {}
    room_out: dict[str, dict[str, Any]] = {}
    for key, marks in room_slots.items():
        for k in marks:
            if k % slots() in ev:
                evenings.add(k // slots())
            if k % slots() >= ev.start:
                last_light[k // slots()] = max(last_light.get(k // slots(), 0), min(cap_end(), (k % slots() + 1) * slot()))
        durations: list[int] = []
        run_ev = 0
        prev: int | None = None
        for k in sorted(marks):
            if prev is not None and k - prev == 1:
                run_ev += k % slots() in ev
            else:
                if run_ev:
                    durations.append(run_ev * slot())
                run_ev = int(k % slots() in ev)
            prev = k
        if run_ev:
            durations.append(run_ev * slot())
        mine = sorted((e for e, x in lights.items() if x['room'] == key), key=lambda e: -lights[e]['evening'])
        name = next((rooms[e][1] for e in mine if e in rooms), mine[0] if mine else key)
        room_out[key] = {'name': name, 'lights': mine, 'durations': durations, 'weight': sum((1 for k in marks if k % slots() in ev)) / n if n else 0.0, 'total': len(marks) * slot()}
    covers: dict[str, dict[str, Any]] = {}
    for eid, meta in info.items():
        if meta['domain'] != 'cover':
            continue
        opens: list[float] = []
        closes: list[float] = []
        positions: list[int] = []
        for t, do, value in changes(history.get(eid, []), 'cover', False, meta['pos'], start):
            m = day_min(t)[1]
            opening = do == 'open' or (do == 'position' and (value or 0) > 50)
            if opening and morning()[0] <= m < morning()[1]:
                opens.append(m)
            elif not opening and afternoon()[0] <= m < afternoon()[1]:
                closes.append(m)
                if do == 'position':
                    positions.append(int(value or 0))
        covers[eid] = {'room': rooms.get(eid, (eid, eid))[0], 'open': opens, 'close': closes, 'positions': positions}
    return {'days': n, 'evenings': len(evenings), 'last_light': sorted(last_light.values()), 'lights': lights, 'rooms': room_out, 'covers': covers}

def _profile(light: dict[str, Any], weekend: bool) -> list[float]:
    kind = 'we' if weekend else 'wd'
    return light[kind] if light['n'][kind] >= 2 else light['all']

def learned_sessions(model: dict[str, Any], day: dt.date, dark: float, end: float, rng: random.Random) -> list[dict[str, Any]]:
    """Where and when the rooms are lit tonight: drawn from each room's profile, never a copy of one day."""
    weekend = day.weekday() >= 5
    out: list[dict[str, Any]] = []
    s0, s1 = (math.ceil(dark / slot()), math.ceil(min(end, cap_end()) / slot()))
    for key, room in model['rooms'].items():
        lights = [e for e in room['lights'] if model['lights'][e]['evening'] > 0][:lights_per_room()]
        if not lights:
            continue
        prof = [max((_profile(model['lights'][e], weekend)[s] for e in lights)) for s in range(slots())]
        runs: list[tuple[int, int]] = []
        a = None
        for s in range(s0, s1 + 1):
            if s < s1 and prof[s] >= p_min():
                a = s if a is None else a
            elif a is not None:
                runs.append((a, s))
                a = None
        for a, b in runs:
            if rng.random() >= max(prof[a:b]):
                continue
            frm = max(dark, a * slot() + rng.uniform(-jitter(), jitter()))
            durs = room['durations'] or [(b - a) * slot()]
            length = max(slot(), rng.choice(durs) + rng.uniform(-jitter(), jitter()))
            to = min(frm + length, b * slot() + 2 * jitter(), cap_end())
            if to - frm >= slot():
                out.append({'room': key, 'name': room['name'], 'lights': lights, 'from': frm, 'to': to, 'weight': room['weight']})
    return out
