from __future__ import annotations
from http import HTTPStatus
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
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.helpers.event import async_track_point_in_time, async_track_state_change_event, async_track_time_change
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util
from . import derived
from .const import wants_ru
from .house import refuse
from .ownership import entity_room
from .rules import said
from .storage import HaufloStore
from .tuning import TUNING
from .notify import label
_LOGGER = logging.getLogger(__name__)
STORE_KEY = 'hauflo.presence'
_T = TUNING['presence']
LEARN_DAYS = _T['learn_days']
JITTER = _T['jitter_min']
SLOT = _T['slot_min']
SLOTS = _T['slots']
EVENING = tuple(_T['evening'])
MORNING = tuple(_T['morning'])
AFTERNOON = tuple(_T['afternoon'])
CAP_END = _T['cap_end']
DEFAULT_END = _T['default_end']
DARK_BEFORE = _T['dark_before_min']
P_MIN = _T['p_min']
MIN_EVENINGS = _T['min_evenings']
MAX_ROOMS = _T['max_rooms']
LIGHTS_PER_ROOM = _T['lights_per_room']
SUN_BRIGHTNESS = _T['sun_brightness']
COALESCE = _T['coalesce_s']
MANUAL = _T['manual_s']
WATCH_OFF = _T['watch_off_s']
OURS_AFTER = _T['ours_after_s']
MAX_EVENTS = _T['max_events']
LOG_SIZE = _T['log_size']
MAX_UNTIL_DAYS = _T['max_until_days']
REPLAN = tuple(_T['replan'])
MODES = ('learned', 'sun')
_DATE = re.compile('\\d{4}-\\d{2}-\\d{2}')
_LIVING = re.compile(_T['living_words'], re.I)
MAX_SKIP = _T['max_skip']
MAX_OURS = _T['max_ours']
DEFAULT: dict[str, Any] = {'enabled': False, 'until': None, 'lights': True, 'covers': True, 'mode': 'learned', 'since': None, 'by': None, 'log': [], 'lit': [], 'lit_at': {}, 'ours': [], 'skip_rooms': []}

class Invalid(ValueError):
    pass

class Refused(Invalid, derived.Refused):

    def __init__(self, code: str, status: int=HTTPStatus.BAD_REQUEST, **ph: Any) -> None:
        derived.Refused.__init__(self, code, f'presence_{code}', status, **ph)

def _wall(t: dt.datetime) -> dt.datetime:
    return dt_util.as_local(t).replace(tzinfo=None)

def day_min(t: dt.datetime) -> tuple[dt.date, float]:
    w = _wall(t) - dt.timedelta(hours=4)
    return (w.date(), w.hour * 60 + w.minute + w.second / 60)

def rel_min(day: dt.date, t: dt.datetime) -> float:
    return (_wall(t) - dt.datetime.combine(day, dt.time(4))).total_seconds() / 60

def at(day: dt.date, minutes: float) -> dt.datetime:
    return (dt.datetime.combine(day, dt.time(4)) + dt.timedelta(minutes=minutes)).replace(tzinfo=dt_util.get_default_time_zone())

def action_of(domain: str, st: State, dim: bool, pos: bool) -> tuple[str, int | None] | None:
    s = st.state
    if domain == 'light':
        if s == 'off':
            return ('off', None)
        if s != 'on':
            return None
        b = st.attributes.get('brightness')
        if dim and isinstance(b, (int, float)) and (not isinstance(b, bool)) and (b > 0):
            return ('on', max(1, min(100, round(b * 100 / 255))))
        return ('on', None)
    if domain == 'cover':
        if s not in ('open', 'closed'):
            return None
        p = st.attributes.get('current_position')
        if pos and isinstance(p, (int, float)) and (not isinstance(p, bool)):
            p = round(p)
            return ('close', None) if p <= 0 else ('open', None) if p >= 100 else ('position', int(p))
        return ('open', None) if s == 'open' else ('close', None)
    return None

def changes(states: list[State], domain: str, dim: bool, pos: bool, start: dt.datetime) -> list[tuple[dt.datetime, str, int | None]]:
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
            if i < 0 or ts > times[i] + OURS_AFTER:
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
    return (t - off.last_updated).total_seconds() < WATCH_OFF

def build_model(history: dict[str, list[State]], info: dict[str, dict[str, Any]], rooms: dict[str, tuple[str, str]], start: dt.datetime, end: dt.datetime) -> dict[str, Any]:
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
        ka = math.ceil((a - grid0).total_seconds() / (SLOT * 60) - 0.5)
        kb = math.floor((b - grid0).total_seconds() / (SLOT * 60) - 0.5)
        return range(max(ka, 0), min(kb, n * SLOTS - 1) + 1)
    ev = range(EVENING[0] // SLOT, EVENING[1] // SLOT)
    lights: dict[str, dict[str, Any]] = {}
    room_slots: dict[str, set[int]] = {}
    for eid, meta in info.items():
        if meta['domain'] != 'light':
            continue
        marks: set[int] = set()
        bright: list[int] = []
        for a, b, pct in on_intervals(history.get(eid, []), end):
            marks.update(slots_of(a, b))
            if pct is not None and meta['dim'] and (EVENING[0] - 60 <= day_min(a)[1] < EVENING[1]):
                bright.append(pct)
        wd, we = ([0] * SLOTS, [0] * SLOTS)
        for k in marks:
            (we if weekend[k // SLOTS] else wd)[k % SLOTS] += 1
        key = rooms.get(eid, (eid, eid))[0]
        room_slots.setdefault(key, set()).update(marks)
        lights[eid] = {'room': key, 'wd': [c / n_wd if n_wd else 0.0 for c in wd], 'we': [c / n_we if n_we else 0.0 for c in we], 'all': [(a + b) / n if n else 0.0 for a, b in zip(wd, we, strict=True)], 'n': {'wd': n_wd, 'we': n_we}, 'evening': sum((1 for k in marks if k % SLOTS in ev)), 'dim': meta['dim'], 'bright': int(statistics.median(bright)) if bright else None}
    evenings: set[int] = set()
    last_light: dict[int, int] = {}
    room_out: dict[str, dict[str, Any]] = {}
    for key, marks in room_slots.items():
        for k in marks:
            if k % SLOTS in ev:
                evenings.add(k // SLOTS)
            if k % SLOTS >= ev.start:
                last_light[k // SLOTS] = max(last_light.get(k // SLOTS, 0), min(CAP_END, (k % SLOTS + 1) * SLOT))
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
        mine = sorted((e for e, x in lights.items() if x['room'] == key), key=lambda e: -lights[e]['evening'])
        name = next((rooms[e][1] for e in mine if e in rooms), mine[0] if mine else key)
        room_out[key] = {'name': name, 'lights': mine, 'durations': durations, 'weight': sum((1 for k in marks if k % SLOTS in ev)) / n if n else 0.0, 'total': len(marks) * SLOT}
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
            if opening and MORNING[0] <= m < MORNING[1]:
                opens.append(m)
            elif not opening and AFTERNOON[0] <= m < AFTERNOON[1]:
                closes.append(m)
                if do == 'position':
                    positions.append(int(value or 0))
        covers[eid] = {'room': rooms.get(eid, (eid, eid))[0], 'open': opens, 'close': closes, 'positions': positions}
    return {'days': n, 'evenings': len(evenings), 'last_light': sorted(last_light.values()), 'lights': lights, 'rooms': room_out, 'covers': covers}

def _profile(light: dict[str, Any], weekend: bool) -> list[float]:
    kind = 'we' if weekend else 'wd'
    return light[kind] if light['n'][kind] >= 2 else light['all']

def learned_sessions(model: dict[str, Any], day: dt.date, dark: float, end: float, rng: random.Random) -> list[dict[str, Any]]:
    weekend = day.weekday() >= 5
    out: list[dict[str, Any]] = []
    s0, s1 = (math.ceil(dark / SLOT), math.ceil(min(end, CAP_END) / SLOT))
    for key, room in model['rooms'].items():
        lights = [e for e in room['lights'] if model['lights'][e]['evening'] > 0][:LIGHTS_PER_ROOM]
        if not lights:
            continue
        prof = [max((_profile(model['lights'][e], weekend)[s] for e in lights)) for s in range(SLOTS)]
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
                continue
            frm = max(dark, a * SLOT + rng.uniform(-JITTER, JITTER))
            durs = room['durations'] or [(b - a) * SLOT]
            length = max(SLOT, rng.choice(durs) + rng.uniform(-JITTER, JITTER))
            to = min(frm + length, b * SLOT + 2 * JITTER, CAP_END)
            if to - frm >= SLOT:
                out.append({'room': key, 'name': room['name'], 'lights': lights, 'from': frm, 'to': to, 'weight': room['weight']})
    return out

def sun_rooms(model: dict[str, Any]) -> list[str]:
    rooms = model['rooms']
    return sorted((k for k in rooms if rooms[k]['lights']), key=lambda k: (-rooms[k]['total'], not _LIVING.search(rooms[k]['name'] or ''), -len(rooms[k]['lights']), rooms[k]['name']))

def sun_sessions(model: dict[str, Any], dark: float, rng: random.Random) -> list[dict[str, Any]]:
    out = []
    for i, key in enumerate(sun_rooms(model)[:2]):
        room = model['rooms'][key]
        if i == 0:
            frm = dark + rng.uniform(0, 30)
            to = max(frm + 45, 1110 + rng.uniform(-30, 30))
        else:
            frm = max(dark + rng.uniform(20, 50), 930 + rng.uniform(-30, 30))
            to = frm + rng.uniform(60, 120)
        to = min(to, CAP_END)
        if to - frm >= 20:
            out.append({'room': key, 'name': room['name'], 'lights': room['lights'][:1], 'from': frm, 'to': to, 'weight': 2 - i})
    return out

def cap_rooms(cands: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for c in sorted(cands, key=lambda x: (-x['weight'], x['from'])):
        points = [c['from'], *(x['from'] for x in kept if c['from'] <= x['from'] < c['to'])]
        if all((sum((1 for x in kept if x['from'] <= p < x['to'])) < cap for p in points)):
            kept.append(c)
    return sorted(kept, key=lambda x: x['from'])

def plan_day(model: dict[str, Any], day: dt.date, sun: tuple[dt.datetime, dt.datetime] | None, now: dt.datetime, rng: random.Random, mode: str='learned', cap: int=MAX_ROOMS, lights_on: bool=True, covers_on: bool=True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    lights_by = 'off'
    if lights_on:
        if sun is None:
            lights_by = 'no_location'
        elif mode == 'learned' and model['evenings'] < MIN_EVENINGS:
            lights_by = 'few'
        else:
            lights_by = mode
            dark = rel_min(day, sun[1]) - DARK_BEFORE
            ends = model['last_light']
            end = min(CAP_END, statistics.median(ends)) if mode == 'learned' and ends else DEFAULT_END
            if end > dark:
                found = learned_sessions(model, day, dark, end, rng) if mode == 'learned' else sun_sessions(model, dark, rng)
                sessions = cap_rooms(found, cap)
    for s in sessions:
        frm, to = (at(day, s['from']), at(day, s['to']))
        if frm < now < to - dt.timedelta(minutes=20):
            frm = now + dt.timedelta(minutes=rng.uniform(5, 15))
        for eid in s['lights']:
            light = model['lights'].get(eid, {})
            value = (light.get('bright') if mode == 'learned' else SUN_BRIGHTNESS) if light.get('dim') else None
            events.append({'at': frm + dt.timedelta(seconds=rng.uniform(0, 90)), 'entity_id': eid, 'do': 'on', 'value': value})
            events.append({'at': to + dt.timedelta(seconds=rng.uniform(0, 90)), 'entity_id': eid, 'do': 'off', 'value': None})
    covers_by: set[str] = set()
    moves: dict[str, list[dt.datetime]] = {'open': [], 'close': []}
    if covers_on:
        jit: dict[str, tuple[float, float]] = {}
        for eid, c in model['covers'].items():
            jo, jc = jit.setdefault(c['room'], (rng.uniform(-JITTER, JITTER), rng.uniform(-JITTER, JITTER)))
            for kind, learned, by_sun, j in (('open', c['open'], rel_min(day, sun[0]) + 30 if sun else None, jo), ('close', c['close'], rel_min(day, sun[1]) if sun else None, jc)):
                if mode == 'learned' and len(learned) >= MIN_EVENINGS:
                    m, by = (statistics.median(learned) + j, 'history')
                elif by_sun is not None:
                    m, by = (by_sun + j * 10 / JITTER, 'sun')
                else:
                    continue
                covers_by.add(by)
                do, value = (kind, None)
                if kind == 'close' and by == 'history' and (len(c['positions']) * 2 > len(learned)):
                    do, value = ('position', int(statistics.median(c['positions'])))
                t = at(day, m) + dt.timedelta(seconds=rng.uniform(0, 60))
                moves[kind].append(t)
                events.append({'at': t, 'entity_id': eid, 'do': do, 'value': value})
    events = sorted((e for e in events if e['at'] > now), key=lambda e: e['at'])[:MAX_EVENTS]
    summary = {'lights': lights_by, 'evenings': model['evenings'], 'rooms': [{'name': s['name'], 'from': at(day, s['from']).isoformat(timespec='minutes'), 'to': at(day, s['to']).isoformat(timespec='minutes')} for s in sessions], 'covers': {k: min(v).isoformat(timespec='minutes') if v else None for k, v in moves.items()} | {'by': 'mixed' if len(covers_by) > 1 else next(iter(covers_by), None)}}
    return (events, summary)

class Presence:

    def __init__(self, hass: HomeAssistant, owns: Callable[[str], bool]) -> None:
        self.hass = hass
        self.owns = owns
        self.store = HaufloStore(hass, STORE_KEY)
        self.data: dict[str, Any] = {**DEFAULT, 'log': [], 'lit': [], 'lit_at': {}, 'ours': []}
        self.rng = random.Random()
        self.status = 'off'
        self.tonight: dict[str, Any] | None = None
        self.pending: dict[int, tuple[CALLBACK_TYPE, dict[str, Any]]] = {}
        self._n = 0
        self._daily: CALLBACK_TYPE | None = None
        self._off: CALLBACK_TYPE | None = None
        self._ctx: deque[str] = deque(maxlen=400)
        self._sent: dict[str, float] = {}
        self._waits: dict[str, CALLBACK_TYPE] = {}
        self._claims: dict[str, CALLBACK_TYPE] = {}
        self._outs: dict[str, CALLBACK_TYPE] = {}
        self._started = False
        self._t0: float | None = None
        self._boot: float | None = None

    def _pickable(self, eid: str) -> bool:
        if eid.split('.')[0] not in ('light', 'cover') or self.hass.states.get(eid) is None:
            return False
        ent = er.async_get(self.hass).async_get(eid)
        if ent is None or ent.disabled_by or ent.hidden_by or (ent.platform == 'group'):
            return False
        return bool(self.owns(eid))

    def candidates(self, every: bool=False) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for domain, on in (('light', self.data['lights']), ('cover', self.data['covers'])):
            if not on and (not every):
                continue
            for eid in self.hass.states.async_entity_ids(domain):
                if not self._pickable(eid):
                    continue
                a = self.hass.states.get(eid).attributes
                modes = a.get('supported_color_modes') or []
                out[eid] = {'domain': domain, 'dim': domain == 'light' and any((m != 'onoff' for m in modes)), 'pos': domain == 'cover' and bool(int(a.get('supported_features') or 0) & 4)}
        return out

    def rooms(self, eids: list[str]) -> dict[str, tuple[str, str]]:
        areas = ar.async_get(self.hass)
        out = {}
        for eid in eids:
            area_id = entity_room(self.hass, eid)
            area = areas.async_get_area(area_id) if area_id else None
            st = self.hass.states.get(eid)
            out[eid] = (area.id, area.name) if area else (eid, (st.attributes.get('friendly_name') if st else None) or eid)
        return out

    def _sun(self, day: dt.date) -> tuple[dt.datetime, dt.datetime] | None:
        from .automations import _UNSET
        import astral
        import astral.sun
        c = self.hass.config
        if (round(float(c.latitude), 5), round(float(c.longitude), 5)) in _UNSET:
            return None
        obs = astral.Observer(c.latitude, c.longitude, c.elevation)
        tz = dt_util.get_time_zone(c.time_zone) or dt_util.get_default_time_zone()
        try:
            return (astral.sun.sunrise(obs, date=day, tzinfo=tz), astral.sun.sunset(obs, date=day, tzinfo=tz))
        except ValueError:
            return None

    async def async_start(self) -> None:
        stored = await self.store.async_load()
        if isinstance(stored, dict):
            self.data = {**DEFAULT, **{k: v for k, v in stored.items() if k in DEFAULT}}
            for k in ('log', 'lit', 'ours', 'skip_rooms'):
                if not isinstance(self.data.get(k), list):
                    self.data[k] = []
            if not isinstance(self.data.get('lit_at'), dict):
                self.data['lit_at'] = {}
            self.data['skip_rooms'] = [x for x in self.data['skip_rooms'] if isinstance(x, str)][:MAX_SKIP]
            if self.data.get('mode') not in MODES:
                self.data['mode'] = 'learned'
        self._started = True
        if self.hass.state is not CoreState.running:
            self._t0 = time.time()

            @callback
            def _up(_hass: HomeAssistant) -> None:
                self._boot = time.time()
            async_at_started(self.hass, _up)
        if not self.data['enabled']:
            if self.data['lit']:

                @callback
                def _out(_hass: HomeAssistant) -> None:
                    if self._started and (not self.data['enabled']):
                        self.hass.async_create_task(self._lights_out_saved(), eager_start=True)
                async_at_started(self.hass, _out)
            return
        if self._expired():
            await self._switch_off('auto_off')
            return

        @callback
        def _go(_hass: HomeAssistant) -> None:
            if self._started and self.data['enabled']:
                self.hass.async_create_task(self._arm(), eager_start=True)
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
        for name in ('_daily', '_off'):
            if (unsub := getattr(self, name)) is not None:
                unsub()
                setattr(self, name, None)

    def _off_at(self) -> dt.datetime | None:
        until = self.data.get('until')
        if not isinstance(until, str) or not _DATE.fullmatch(until):
            return None
        return dt_util.start_of_local_day(dt.date.fromisoformat(until) + dt.timedelta(days=1))

    def _expired(self) -> bool:
        off = self._off_at()
        return off is not None and dt_util.now() >= off

    async def _arm(self) -> None:
        self._cancel()
        if not self._started or not self.data['enabled']:
            return
        if self._expired():
            await self._switch_off('auto_off')
            return
        self._daily = async_track_time_change(self.hass, self._replan, hour=REPLAN[0], minute=REPLAN[1], second=0)
        if (off := self._off_at()) is not None:
            self._off = async_track_point_in_time(self.hass, self._auto_off, off)
        self._stop_outs()
        await self.plan()
        soon = dt_util.now() + dt.timedelta(minutes=20)
        for eid in list(self.data['lit']):
            self._remember(eid)
            st = self.hass.states.get(eid)
            if st is None or st.state != 'on':
                self._claim(eid)
            nxt = min((ev['at'] for _, ev in self.pending.values() if ev['entity_id'] == eid), default=None)
            if nxt is not None and nxt <= soon:
                continue
            if st is not None and st.state == 'on':
                self._schedule({'at': dt_util.now() + dt.timedelta(minutes=self.rng.uniform(2, 10)), 'entity_id': eid, 'do': 'off', 'value': None})
            else:
                self._wait(eid, dt_util.now() + dt.timedelta(seconds=WATCH_OFF))
        if self.data['lit']:
            self.store.async_delay_save(lambda: self.data, 5)

    async def _replan(self, _now: dt.datetime) -> None:
        if self._expired():
            await self._switch_off('auto_off')
            return
        await self.plan()

    async def _auto_off(self, _now: dt.datetime) -> None:
        self._off = None
        await self._switch_off('auto_off')

    async def _switch_off(self, why: str) -> None:
        self._cancel()
        was = self.data['enabled']
        self.data['enabled'] = False
        self.status = 'off'
        self.tonight = None
        await self._lights_out()
        if was:
            self._log({'sys': why})
        await self.store.async_save(self.data)

    async def _lights_out(self, final: bool=False) -> None:
        for eid in list(self.data['lit']):
            st = self.hass.states.get(eid)
            if st is not None and st.state == 'on':
                person = self._person_since(st)
                self._unlit(eid)
                if self._pickable(eid) and (not person):
                    await self._off_now(eid, st)
                continue
            if st is not None and st.state == 'off' and (not self._restarting()):
                self._unlit(eid)
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
        ev = {'entity_id': eid, 'do': 'off', 'value': None}
        name = label(self.hass, eid, st)
        if await self._send(ev, name):
            key, room = self.rooms([eid])[eid]
            self._log({**_brief(ev), 'name': name, 'room': room if key != eid else None})

    def _restarting(self) -> bool:
        return self._t0 is not None and time.time() < self._t0 + WATCH_OFF

    def _watch_out(self, eid: str) -> None:
        if eid in self._outs:
            return
        unsubs: list[CALLBACK_TYPE] = []

        def done() -> None:
            self._outs.pop(eid, None)
            for unsub in unsubs:
                unsub()

        async def settle(st: State | None) -> None:
            if eid not in self.data['lit'] or self.data['enabled']:
                return
            on = st is not None and st.state == 'on'
            person = on and self._person_since(st)
            self._unlit(eid)
            if on and (not person) and self._pickable(eid):
                await self._off_now(eid, st)
            await self.store.async_save(self.data)

        @callback
        def changed(event: Any) -> None:
            new = event.data.get('new_state')
            if new is None or new.state != 'on' or eid not in self._outs:
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
        self.async_stop()
        await self._lights_out(final=True)
        await self.store.async_save(self.data)

    async def _history(self, entity_ids: list[str], start: dt.datetime, end: dt.datetime) -> dict[str, list[State]] | None:
        if 'recorder' not in self.hass.config.components:
            return None
        from homeassistant.components.recorder import get_instance, history
        got = await get_instance(self.hass).async_add_executor_job(history.get_significant_states, self.hass, start, end, entity_ids, None, True, False, False, False)
        return {k: [s for s in v if isinstance(s, State)] for k, v in got.items()}

    async def plan(self) -> None:
        now = dt_util.now()
        day = dt_util.as_local(now).date()
        info = self.candidates()
        skip = set(self.data.get('skip_rooms') or [])
        if skip and info:
            where = self.rooms(sorted(info))
            info = {e: m for e, m in info.items() if where[e][0] not in skip}
        if not info:
            self.status, self.tonight = ('no_devices', None)
            return
        try:
            history = await self._history(sorted(info), now - dt.timedelta(days=LEARN_DAYS), now)
        except Exception as err:
            _LOGGER.warning('Hauflo presence: history unreadable: %s', err)
            history = None
        if history is None:
            self.status, self.tonight = ('no_recorder', None)
            return
        history = drop_ours(history, self.data.get('ours') or [])
        rooms = self.rooms(sorted(info))
        model = await self.hass.async_add_executor_job(build_model, history, info, rooms, now - dt.timedelta(days=LEARN_DAYS), now)
        n_rooms = len({rooms[e][0] for e, m in info.items() if m['domain'] == 'light'})
        cap = min(MAX_ROOMS, max(1, n_rooms - 1))
        events, self.tonight = plan_day(model, day, self._sun(day), now, self.rng, self.data['mode'], cap, self.data['lights'], self.data['covers'])
        self.status = self.tonight['lights'] if self.tonight['lights'] in ('few', 'no_location') else 'on'
        for ev in events:
            self._schedule(ev)

    def _schedule(self, ev: dict[str, Any]) -> None:
        self._n += 1
        key = self._n
        ev['key'] = key

        async def run(_now: dt.datetime) -> None:
            if (item := self.pending.pop(key, None)) is not None:
                await self.fire(item[1])
        self.pending[key] = (async_track_point_in_time(self.hass, run, ev['at']), ev)

    def _touched(self, st: State) -> bool:
        if (dt_util.utcnow() - st.last_updated).total_seconds() > MANUAL:
            return False
        if self._boot is not None and st.last_updated.timestamp() <= self._boot:
            return False
        if st.context.id in self._ctx or (st.context.parent_id and st.context.parent_id in self._ctx):
            return False
        sent = self._sent.get(st.entity_id)
        return not (sent is not None and sent - 1 <= st.last_updated.timestamp() <= sent + OURS_AFTER)

    def _person_since(self, st: State) -> bool:
        c = st.context
        if not c.user_id or c.id in self._ctx or (c.parent_id and c.parent_id in self._ctx):
            return False
        return st.last_updated.timestamp() > float(self.data['lit_at'].get(st.entity_id) or 0) + 1

    def _unlit(self, eid: str) -> None:
        self.data['lit'] = [e for e in self.data['lit'] if e != eid]
        self.data['lit_at'] = {k: v for k, v in self.data['lit_at'].items() if k != eid}

    def _claim(self, eid: str) -> None:
        if eid in self._claims:
            return
        unsubs: list[CALLBACK_TYPE] = []

        def done() -> None:
            self._claims.pop(eid, None)
            for unsub in unsubs:
                unsub()

        @callback
        def changed(event: Any) -> None:
            new = event.data.get('new_state')
            if new is None or new.state != 'on' or eid not in self._claims:
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

    def _wait(self, eid: str, until: dt.datetime | None=None) -> None:
        if eid in self._waits:
            return
        unsubs: list[CALLBACK_TYPE] = []

        def done() -> None:
            self._waits.pop(eid, None)
            for unsub in unsubs:
                unsub()

        @callback
        def changed(event: Any) -> None:
            new = event.data.get('new_state')
            if new is None or new.state in ('unavailable', 'unknown') or eid not in self._waits:
                return
            if new.state == 'on':
                done()
                self._schedule({'at': dt_util.now() + dt.timedelta(seconds=self.rng.uniform(30, 120)), 'entity_id': eid, 'do': 'off', 'value': None})
            elif until is None:
                done()
                self._unlit(eid)

        @callback
        def expired(_now: dt.datetime) -> None:
            if eid in self._waits:
                done()
                if (st := self.hass.states.get(eid)) is None or st.state != 'on':
                    self._unlit(eid)
        unsubs.append(async_track_state_change_event(self.hass, [eid], changed))
        if until is not None:
            unsubs.append(async_track_point_in_time(self.hass, expired, until))
        self._waits[eid] = done

    async def _send(self, ev: dict[str, Any], name: str) -> bool:
        eid = ev['entity_id']
        domain = eid.split('.')[0]
        service, data = _service(domain, ev['do'], ev['value'])
        ctx = Context()
        self._ctx.append(ctx.id)
        self._sent[eid] = time.time()
        try:
            await self.hass.services.async_call(domain, service, {'entity_id': eid, **data}, blocking=True, context=ctx)
        except Exception as err:
            _LOGGER.warning('Hauflo presence: %s %s failed: %s', eid, service, err)
            self._log({**_brief(ev), 'name': name, 'skip': 'error'})
            return False
        self._remember(eid)
        return True

    def _remember(self, eid: str) -> None:
        cut = time.time() - (LEARN_DAYS + 1) * 86400
        kept = [x for x in self.data.get('ours') or [] if isinstance(x, list) and len(x) == 2 and isinstance(x[1], (int, float)) and (x[1] >= cut)]
        self.data['ours'] = [*kept[-(MAX_OURS - 1):], [eid, round(time.time(), 1)]]

    async def fire(self, ev: dict[str, Any]) -> None:
        if not self.data['enabled']:
            return
        if self._expired():
            await self._switch_off('auto_off')
            return
        eid = ev['entity_id']
        domain = eid.split('.')[0]
        mine = domain == 'light' and ev['do'] == 'off' and (eid in self.data['lit'])
        if not mine and (domain == 'light' and (not self.data['lights']) or (domain == 'cover' and (not self.data['covers']))):
            return
        if ev['do'] != 'off' and self.rooms([eid])[eid][0] in set(self.data.get('skip_rooms') or []):
            return
        st = self.hass.states.get(eid)
        if mine and (st is None or st.state in ('unavailable', 'unknown')):
            if eid not in self._waits:
                self._log({**_brief(ev), 'name': (st.attributes.get('friendly_name') if st else None) or eid, 'skip': 'offline'})
            self._wait(eid)
            return
        if not self._pickable(eid) or st is None:
            self._log({**_brief(ev), 'name': eid, 'skip': 'gone'})
            if mine:
                self._unlit(eid)
            return
        name = label(self.hass, eid, st)
        if st.state in ('unavailable', 'unknown'):
            self._log({**_brief(ev), 'name': name, 'skip': 'offline'})
            return
        if self._person_since(st) if mine else self._touched(st):
            self._log({**_brief(ev), 'name': name, 'skip': 'manual'})
            if mine:
                self._unlit(eid)
            return
        info = self.candidates(every=True).get(eid) or {'dim': False, 'pos': False}
        now_is = action_of(domain, st, info['dim'], info['pos'])
        if now_is == (ev['do'], ev['value']) or (ev['do'] == 'on' and ev['value'] is None and now_is and (now_is[0] == 'on')):
            if mine:
                self._unlit(eid)
            return
        if not await self._send(ev, name):
            return
        if domain == 'light':
            self._unlit(eid)
            if ev['do'] == 'on':
                self.data['lit'] = [*self.data['lit'], eid]
                self.data['lit_at'] = {**self.data['lit_at'], eid: round(time.time(), 1)}
        key, room = self.rooms([eid])[eid]
        self._log({**_brief(ev), 'name': name, 'room': room if key != eid else None})

    def _log(self, item: dict[str, Any]) -> None:
        self.data['log'] = [{'t': dt_util.now().isoformat(timespec='seconds'), **item}, *self.data['log']][:LOG_SIZE]
        self.store.async_delay_save(lambda: self.data, 5)

    async def set(self, body: dict[str, Any], user: str | None) -> dict[str, Any]:
        new = dict(self.data)
        for key in ('enabled', 'lights', 'covers'):
            if key in body:
                if not isinstance(body[key], bool):
                    raise Invalid(f'{key}: true or false')
                new[key] = body[key]
        if 'mode' in body:
            if body['mode'] not in MODES:
                raise Invalid('mode: learned or sun')
            new['mode'] = body['mode']
        today = dt_util.now().date()
        if 'until' in body:
            v = body['until']
            if v in (None, ''):
                new['until'] = None
            else:
                if not isinstance(v, str) or not _DATE.fullmatch(v):
                    raise Invalid('until: YYYY-MM-DD')
                try:
                    day = dt.date.fromisoformat(v)
                except ValueError as err:
                    raise Invalid('until: YYYY-MM-DD') from err
                if not today <= day <= today + dt.timedelta(days=MAX_UNTIL_DAYS):
                    raise Refused('bad_until', n=MAX_UNTIL_DAYS)
                new['until'] = v
        elif new['enabled'] and (not self.data['enabled']) and new.get('until') and (new['until'] < today.isoformat()):
            new['until'] = None
        if 'skip_rooms' in body:
            v = body['skip_rooms']
            if not isinstance(v, list) or len(v) > MAX_SKIP or (not all((isinstance(x, str) and 0 < len(x) <= 255 for x in v))):
                raise Invalid('skip_rooms: a list of room ids')
            new['skip_rooms'] = list(dict.fromkeys(v))
        if not new['lights'] and (not new['covers']):
            raise Refused('lights_or_covers')
        if new['enabled'] and (not self.data['enabled']):
            new['since'], new['by'] = (dt_util.now().isoformat(timespec='seconds'), user)
        turned = new['enabled'] != self.data['enabled']
        self.data = new
        if turned and (not new['enabled']):
            self._cancel()
            await self._lights_out()
        if turned:
            self._log({'sys': 'on' if new['enabled'] else 'off', **({'by': user} if user else {})})
        await self.store.async_save(self.data)
        if new['enabled']:
            await self._arm()
        else:
            self._cancel()
            self.status, self.tonight = ('off', None)
        return self.answer()

    def answer(self) -> dict[str, Any]:
        info = self.candidates(every=True)
        nxt = sorted((ev for _, ev in self.pending.values()), key=lambda e: e['at'])
        name = lambda eid: (s.attributes.get('friendly_name') if (s := self.hass.states.get(eid)) else None) or eid
        off = self._off_at()
        skip = set(self.data.get('skip_rooms') or [])
        where = self.rooms(sorted(info))
        rooms: dict[str, dict[str, Any]] = {}
        for eid, meta in info.items():
            key, room_name = where[eid]
            if key == eid:
                continue
            r = rooms.setdefault(key, {'id': key, 'name': room_name, 'lights': 0, 'covers': 0, 'on': key not in skip})
            r['lights' if meta['domain'] == 'light' else 'covers'] += 1
        return {**{k: self.data[k] for k in ('enabled', 'until', 'lights', 'covers', 'mode', 'since')}, 'skip_rooms': list(self.data.get('skip_rooms') or []), 'rooms': sorted(rooms.values(), key=lambda r: str(r['name']).lower()), 'status': self.status if self.data['enabled'] else 'off', 'tonight': self.tonight if self.data['enabled'] else None, 'left': len(nxt), 'next': [{**_brief(e), 'at': e['at'].isoformat(timespec='seconds'), 'name': name(e['entity_id'])} for e in nxt[:5]], 'log': self.data['log'], 'off_at': off.isoformat() if off else None, 'devices': {'lights': sum((1 for v in info.values() if v['domain'] == 'light')), 'covers': sum((1 for v in info.values() if v['domain'] == 'cover'))}, 'min_evenings': MIN_EVENINGS, 'max_rooms': MAX_ROOMS, 'jitter_min': JITTER}

def _brief(ev: dict[str, Any]) -> dict[str, Any]:
    return {'entity_id': ev['entity_id'], 'do': ev['do'], 'value': ev['value']}

def _service(domain: str, do: str, value: int | None) -> tuple[str, dict[str, Any]]:
    if domain == 'light':
        return ('turn_on', {'brightness_pct': value} if value else {}) if do == 'on' else ('turn_off', {})
    if do == 'position':
        return ('set_cover_position', {'position': value})
    return ('open_cover' if do == 'open' else 'close_cover', {})

class PresenceView(HomeAssistantView):
    url = '/api/hauflo/presence'
    name = 'api:hauflo:presence'
    requires_auth = True

    def __init__(self, engine_of: Callable[[], Presence | None]) -> None:
        self.engine_of = engine_of

    async def get(self, request):
        engine = self.engine_of()
        if engine is None:
            return self.json_message('Hauflo agent not set up', 503)
        return self.json(engine.answer())

    async def post(self, request):
        engine = self.engine_of()
        if engine is None:
            return self.json_message('Hauflo agent not set up', 503)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict) or body.get('op') != 'set':
            return self.json_message('op: set', 400)
        if set(body) - {'op', 'enabled', 'until', 'lights', 'covers', 'mode', 'skip_rooms', 'lang'}:
            return self.json_message('only enabled, until, lights, covers, mode, skip_rooms', 400)
        if (no := (await refuse(request))) is not None:
            return no
        user = request['hass_user']
        try:
            return self.json(await engine.set(body, user.name if user else None))
        except Refused as err:
            return self.json(await said(engine.hass, err, wants_ru(body, engine.hass.config.language)), err.status)
        except Invalid as err:
            return self.json_message(str(err), 400)
