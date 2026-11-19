"""Plans one day of light sessions from the learned model or the sun."""
from __future__ import annotations
import datetime as dt
import random
import statistics
from typing import Any
from .common import at, cap_end, dark_before, default_end, jitter, living, max_events, max_rooms, min_evenings, rel_min, sun_brightness
from .model import learned_sessions

def sun_rooms(model: dict[str, Any]) -> list[str]:
    """The rooms «по солнцу» lights: the ones used most; with no history — a living room / kitchen by name, then the one with most lights."""
    rooms = model['rooms']
    return sorted((k for k in rooms if rooms[k]['lights']), key=lambda k: (-rooms[k]['total'], not living().search(rooms[k]['name'] or ''), -len(rooms[k]['lights']), rooms[k]['name']))

def sun_sessions(model: dict[str, Any], dark: float, rng: random.Random) -> list[dict[str, Any]]:
    """A calm evening: the main room from dusk to about 22:30, another for an hour or two around 19:30."""
    out = []
    for i, key in enumerate(sun_rooms(model)[:2]):
        room = model['rooms'][key]
        if i == 0:
            frm = dark + rng.uniform(0, 30)
            to = max(frm + 45, 1110 + rng.uniform(-30, 30))
        else:
            frm = max(dark + rng.uniform(20, 50), 930 + rng.uniform(-30, 30))
            to = frm + rng.uniform(60, 120)
        to = min(to, cap_end())
        if to - frm >= 20:
            out.append({'room': key, 'name': room['name'], 'lights': room['lights'][:1], 'from': frm, 'to': to, 'weight': 2 - i})
    return out

def cap_rooms(cands: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """At most `cap` rooms lit at once — the rooms used most go first."""
    kept: list[dict[str, Any]] = []
    for c in sorted(cands, key=lambda x: (-x['weight'], x['from'])):
        points = [c['from'], *(x['from'] for x in kept if c['from'] <= x['from'] < c['to'])]
        if all((sum((1 for x in kept if x['from'] <= p < x['to'])) < cap for p in points)):
            kept.append(c)
    return sorted(kept, key=lambda x: x['from'])

def plan_day(model: dict[str, Any], day: dt.date, sun: tuple[dt.datetime, dt.datetime] | None, now: dt.datetime, rng: random.Random, mode: str='learned', cap: int | None=None, lights_on: bool=True, covers_on: bool=True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The day's events (from now on) and what it is in words: the lit rooms tonight, when the blinds move (`cap` rooms at most, max_rooms by default)."""
    cap = max_rooms() if cap is None else cap
    events: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    lights_by = 'off'
    if lights_on:
        if sun is None:
            lights_by = 'no_location'
        elif mode == 'learned' and model['evenings'] < min_evenings():
            lights_by = 'few'
        else:
            lights_by = mode
            dark = rel_min(day, sun[1]) - dark_before()
            ends = model['last_light']
            end = min(cap_end(), statistics.median(ends)) if mode == 'learned' and ends else default_end()
            if end > dark:
                found = learned_sessions(model, day, dark, end, rng) if mode == 'learned' else sun_sessions(model, dark, rng)
                sessions = cap_rooms(found, cap)
    for s in sessions:
        frm, to = (at(day, s['from']), at(day, s['to']))
        if frm < now < to - dt.timedelta(minutes=20):
            frm = now + dt.timedelta(minutes=rng.uniform(5, 15))
        for eid in s['lights']:
            light = model['lights'].get(eid, {})
            value = (light.get('bright') if mode == 'learned' else sun_brightness()) if light.get('dim') else None
            events.append({'at': frm + dt.timedelta(seconds=rng.uniform(0, 90)), 'entity_id': eid, 'do': 'on', 'value': value})
            events.append({'at': to + dt.timedelta(seconds=rng.uniform(0, 90)), 'entity_id': eid, 'do': 'off', 'value': None})
    covers_by: set[str] = set()
    moves: dict[str, list[dt.datetime]] = {'open': [], 'close': []}
    if covers_on:
        jit: dict[str, tuple[float, float]] = {}
        for eid, c in model['covers'].items():
            jo, jc = jit.setdefault(c['room'], (rng.uniform(-jitter(), jitter()), rng.uniform(-jitter(), jitter())))
            for kind, learned, by_sun, j in (('open', c['open'], rel_min(day, sun[0]) + 30 if sun else None, jo), ('close', c['close'], rel_min(day, sun[1]) if sun else None, jc)):
                if mode == 'learned' and len(learned) >= min_evenings():
                    m, by = (statistics.median(learned) + j, 'history')
                elif by_sun is not None:
                    m, by = (by_sun + j * 10 / jitter(), 'sun')
                else:
                    continue
                covers_by.add(by)
                do, value = (kind, None)
                if kind == 'close' and by == 'history' and (len(c['positions']) * 2 > len(learned)):
                    do, value = ('position', int(statistics.median(c['positions'])))
                t = at(day, m) + dt.timedelta(seconds=rng.uniform(0, 60))
                moves[kind].append(t)
                events.append({'at': t, 'entity_id': eid, 'do': do, 'value': value})
    events = sorted((e for e in events if e['at'] > now), key=lambda e: e['at'])[:max_events()]
    summary = {'lights': lights_by, 'evenings': model['evenings'], 'rooms': [{'name': s['name'], 'from': at(day, s['from']).isoformat(timespec='minutes'), 'to': at(day, s['to']).isoformat(timespec='minutes')} for s in sessions], 'covers': {k: min(v).isoformat(timespec='minutes') if v else None for k, v in moves.items()} | {'by': 'mixed' if len(covers_by) > 1 else next(iter(covers_by), None)}}
    return (events, summary)
