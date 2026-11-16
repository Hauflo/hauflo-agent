from __future__ import annotations
from collections import deque
from collections.abc import Callable
import datetime as dt
import hashlib
import logging
import re
import statistics
from typing import Any
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, Event, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util
from .automations import MAX_OFFSET, Invalid
from .storage import HaufloStore
from .tuning import TUNING
_LOGGER = logging.getLogger(__name__)
STORE_KEY = 'hauflo.habits'
T = TUNING['habits']
DOMAINS = ('light', 'cover', 'scene', 'fan', 'switch', 'climate')
NOT_A_STATE = ('unavailable', 'unknown')
DAY = int(dt.timedelta(days=1).total_seconds()) // 60
CLASSES = {'all': range(7), 'workdays': range(5), 'weekend': range(5, 7)}
DAY_NAMES = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
KEY = re.compile('habit_[0-9a-f]{10}')
DEFAULT = {'enabled': True, 'dismissed': [], 'days': [], 'events': {}, 'auto': {}}

def _now() -> dt.datetime:
    return dt_util.now()

def _wrap(delta: float) -> float:
    return (delta + DAY / 2) % DAY - DAY / 2

def _fit(values: list[float], circular: bool) -> tuple[float, float, int]:
    if circular:
        c = values[0]
        rel = [_wrap(v - c) for v in values]
        med = statistics.median(rel)
        dev = [abs(r - med) for r in rel]
        center = (c + med) % DAY
    else:
        center = statistics.median(values)
        dev = [abs(v - center) for v in values]
    return (center, statistics.median(dev), sum((1 for d in dev if d <= T['window_min'])))

class Habits:

    def __init__(self, hass: HomeAssistant, pickable: Callable[[str], bool], engines: Callable[[], list[Any]], area: Callable[[str], str | None]) -> None:
        self.hass = hass
        self.pickable = pickable
        self.engines = engines
        self.area = area
        self.store = HaufloStore(hass, STORE_KEY)
        self.data: dict[str, Any] = {k: v.copy() if isinstance(v, (list, dict)) else v for k, v in DEFAULT.items()}
        self._since: float | None = None
        self._auto_ctx: deque[str] = deque(maxlen=int(T['auto_ctx']))
        self._scene_ctx: deque[str] = deque(maxlen=int(T['auto_ctx']))
        self._last: dict[str, tuple[float, str]] = {}
        self._travel: dict[str, tuple[float, Any]] = {}
        self._rest: dict[str, str] = {}
        self._sun: dict[tuple[int, str], dt.datetime | None] = {}
        self._mined: list[dict[str, Any]] | None = None
        self._unsubs: list[Callable[[], None]] = []

    @property
    def enabled(self) -> bool:
        return bool(self.data['enabled'])

    async def async_start(self) -> None:
        stored = await self.store.async_load()
        if isinstance(stored, dict):
            for k, v in DEFAULT.items():
                if isinstance(stored.get(k), type(v)):
                    self.data[k] = stored[k]
        bus = self.hass.bus
        self._unsubs += [bus.async_listen('state_changed', self._changed), bus.async_listen('automation_triggered', self._automated), bus.async_listen('script_started', self._automated), async_track_time_interval(self.hass, self._tick, dt.timedelta(seconds=T['mine_every_s']))]
        if self.hass.state is CoreState.running:
            self._since = dt_util.utcnow().timestamp() + T['start_delay_s']
        else:

            @callback
            def _up(_event: Event) -> None:
                if once in self._unsubs:
                    self._unsubs.remove(once)
                self._since = dt_util.utcnow().timestamp() + T['start_delay_s']
            once = bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _up)
            self._unsubs.append(once)
        self.mine()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self.store.async_save(self.data)

    @callback
    def _tick(self, _now: dt.datetime) -> None:
        self.mine()

    def _save_soon(self) -> None:
        self.store.async_delay_save(lambda: self.data, T['save_delay_s'])

    async def set_enabled(self, on: bool) -> None:
        self.data['enabled'] = on
        if not on:
            self.data.update(days=[], events={}, auto={})
        self._mined = None
        await self.store.async_save(self.data)

    async def dismiss(self, key: Any) -> None:
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise Invalid('bad_request', what='key')
        if key not in self.data['dismissed']:
            self.data['dismissed'] = [*self.data['dismissed'], key][-int(T['max_keys']):]
        await self.store.async_save(self.data)

    @callback
    def _automated(self, event: Event) -> None:
        self._auto_ctx.append(event.context.id)

    def _ours(self, st: State) -> bool:
        ctx, t = (st.context, st.last_updated.timestamp())
        for engine in self.engines():
            if engine is None:
                continue
            if isinstance(getattr(engine, 'data', None), dict) and engine.data.get('enabled') is True:
                return True
            seen = getattr(engine, '_ctx', ())
            if ctx.id in seen or (ctx.parent_id and ctx.parent_id in seen):
                return True
            sent = getattr(engine, '_sent', {}).get(st.entity_id)
            at = sent[0] if isinstance(sent, tuple) else sent
            if isinstance(at, (int, float)) and at - 1 <= t <= at + T['echo_s']:
                return True
        return False

    def _actions(self, domain: str, old: State, new: State) -> list[tuple[str, float | None]]:
        a, b = (old.state, new.state)
        if domain == 'scene':
            return [('on', None)] if b != a and b not in NOT_A_STATE else []
        if a in NOT_A_STATE or b in NOT_A_STATE:
            return []
        if domain in ('light', 'fan', 'switch'):
            if a == b or b not in ('on', 'off'):
                return []
            level = new.attributes.get('brightness') if domain == 'light' and b == 'on' else None
            return [(b, round(level * 100 / 255) if isinstance(level, (int, float)) and level > 0 else None)]
        if domain == 'cover':
            if b in ('opening', 'closing'):
                return []
            act, was = (self._cover_action(new), self._cover_action(old))
            if act is None or act[0] == self._rest.get(new.entity_id, was[0] if was else None):
                return []
            return [act]
        out: list[tuple[str, float | None]] = []
        if b != a and b in ('heat', 'cool', 'off'):
            out.append((b, None))
        temp, before = (new.attributes.get('temperature'), old.attributes.get('temperature'))
        if b != 'off' and isinstance(temp, (int, float)) and (temp != before):
            out.append(('temp', float(temp)))
        return out

    @staticmethod
    def _cover_action(st: State) -> tuple[str, float | None] | None:
        pos, step = (st.attributes.get('current_position'), T['position_step'])
        if isinstance(pos, (int, float)):
            if pos <= 0:
                return ('close', None)
            if pos >= 100:
                return ('open', None)
            bucket = int(min(100 - step, max(step, round(pos / step) * step)))
            return (f'pos{bucket}', float(pos))
        return {'open': ('open', None), 'closed': ('close', None)}.get(st.state)

    @callback
    def _changed(self, event: Event) -> None:
        new, old = (event.data.get('new_state'), event.data.get('old_state'))
        if not self.data['enabled'] or new is None or old is None or ((domain := new.entity_id.split('.')[0]) not in DOMAINS):
            return
        eid, ctx, when = (new.entity_id, new.context, new.last_updated)
        if domain == 'cover' and new.state in ('opening', 'closing'):
            t0 = self._travel.get(eid)
            if t0 is None or when.timestamp() - t0[0] > T['travel_s']:
                self._travel[eid] = (when.timestamp(), ctx)
            return
        acts = self._actions(domain, old, new)
        if domain == 'cover':
            if (start := self._travel.pop(eid, None)) is not None and when.timestamp() - start[0] <= T['travel_s']:
                when, ctx = (dt_util.utc_from_timestamp(start[0]), start[1])
            if acts:
                self._rest[eid] = acts[0][0]
        if not acts or self._since is None or when.timestamp() < self._since:
            return
        if domain == 'scene':
            self._scene_ctx.append(ctx.id)
        elif ctx.id in self._scene_ctx:
            return
        if (ent := er.async_get(self.hass).async_get(eid)) is not None and ent.platform == 'group':
            return
        if ctx.parent_id is not None or ctx.id in self._auto_ctx:
            for act, _ in acts:
                self._add('auto', f'{eid}|{act}', when, None)
            return
        if self._ours(new) or not self.pickable(eid):
            return
        for act, level in acts:
            self.record(eid, act, when, level)

    def record(self, eid: str, act: str, when: dt.datetime, level: float | None=None) -> None:
        key, t = (f'{eid}|{act}', when.timestamp())
        last = self._last.get(eid)
        if last is not None and 0 <= t - last[0] < T['settle_s']:
            if last[1] == key and (rows := self.data['events'].get(key)):
                rows[-1][4] = level
                self._save_soon()
            return
        self._last[eid] = (t, key)
        self._add('events', key, when, level)

    def _add(self, where: str, key: str, when: dt.datetime, level: float | None) -> None:
        local = dt_util.as_local(when)
        day = local.date().toordinal()
        rows = self.data[where].setdefault(key, [])
        rows.append([day, local.hour * 60 + local.minute, self._from_sun('sunset', local), self._from_sun('sunrise', local), level])
        del rows[:max(0, len(rows) - int(T['max_per_key']))]
        if len(self.data[where]) > T['max_keys']:
            stale = min(self.data[where], key=lambda k: self.data[where][k][-1][0] if self.data[where][k] else 0)
            self.data[where].pop(stale, None)
        if where == 'events':
            self.observed(day)
        self._save_soon()

    def observed(self, day: int | None=None) -> None:
        day = day if day is not None else _now().date().toordinal()
        if day not in self.data['days']:
            self.data['days'] = sorted([*self.data['days'], day])[-int(T['keep_days']):]
            self._save_soon()

    def _from_sun(self, event: str, local: dt.datetime) -> int | None:
        key = (local.date().toordinal(), event)
        if key not in self._sun:
            try:
                at = get_astral_event_date(self.hass, event, local.date())
            except Exception:
                at = None
            self._sun[key] = at
            if len(self._sun) > 4 * T['keep_days']:
                self._sun.pop(next(iter(self._sun)))
        at = self._sun[key]
        return None if at is None else round((local - at).total_seconds() / 60)

    def mine(self) -> list[dict[str, Any]]:
        today = _now().date().toordinal()
        if self.data['enabled'] and self._since is not None:
            self.observed(today)
        old = today - T['keep_days']
        for where in ('events', 'auto'):
            for key in list(self.data[where]):
                rows = [r for r in self.data[where][key] if r[0] > old]
                if rows:
                    self.data[where][key] = rows
                else:
                    self.data[where].pop(key)
        self.data['days'] = [d for d in self.data['days'] if d > old]
        found: list[dict[str, Any]] = []
        for key, rows in self.data['events'].items():
            eid, act = key.rsplit('|', 1)
            kept: list[dict[str, Any]] = []
            for f in self._fits(rows, 'all', today):
                if all((self._share_in(f, cls, today) >= T['min_share'] for cls in ('workdays', 'weekend'))):
                    kept.append(f)
            for f in sorted((f for cls in ('workdays', 'weekend') for f in self._fits(rows, cls, today)), key=lambda f: -f['share']):
                if not any((set(f['days']) & set(k['days']) and self._same(f, k) for k in kept)):
                    kept.append(f)
            for f in kept:
                f.pop('_rows')
                if not self._automated_then(eid, f, today):
                    found.append({**f, 'entity_id': eid, 'act': act})
        self._mined = found
        return found

    @staticmethod
    def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
        if set(map(id, a['_rows'])) & set(map(id, b['_rows'])):
            return True
        if a['kind'] != b['kind']:
            return False
        if a['kind'] == 'time':
            return abs(_wrap(a['minute'] - b['minute'])) <= T['window_min']
        return abs(a['offset'] - b['offset']) <= T['window_min']

    def _window(self, cls: str, today: int, any_day: bool=False) -> list[int]:
        span = T['weekend_window_days'] if cls == 'weekend' else T['window_days']
        return [d for d in self.data['days'] if today - span < d <= today and (any_day or dt.date.fromordinal(d).weekday() in CLASSES[cls])]

    def _fits(self, rows: list[list[Any]], cls: str, today: int) -> list[dict[str, Any]]:
        days = self._window(cls, today)
        need = T['weekend_min_days'] if cls == 'weekend' else T['min_days']
        if len(days) < need:
            return []
        pool = [r for r in rows if r[0] in set(days)]
        axes = {kind: self._clusters([r for r in pool if r[col] is not None], col, kind == 'time', need, len(days)) for kind, col in (('time', 1), ('sunset', 2), ('sunrise', 3))}
        used: set[tuple[str, int]] = set()
        out = []
        for c in axes['time']:
            ids = {id(r) for r in c['rows']}
            best = None
            for kind in ('sunset', 'sunrise'):
                for i, sc in enumerate(axes[kind]):
                    if len(ids & {id(r) for r in sc['rows']}) * 2 >= len(ids) and (best is None or sc['spread'] < best[1]['spread']):
                        best = ((kind, i), sc, kind)
            if best is not None:
                used.add(best[0])
                if best[1]['drift'] + T['sun_better_min'] <= c['drift'] and abs(best[1]['center']) <= MAX_OFFSET:
                    out.append(self._fit_of(cls, best[2], best[1], len(days)))
                    continue
            out.append(self._fit_of(cls, 'time', c, len(days)))
        for kind in ('sunset', 'sunrise'):
            for i, sc in enumerate(axes[kind]):
                if (kind, i) not in used and abs(sc['center']) <= MAX_OFFSET and (not any((set(map(id, sc['rows'])) & set(map(id, f['_rows'])) for f in out))):
                    out.append(self._fit_of(cls, kind, sc, len(days)))
        return out

    def _share_in(self, fit: dict[str, Any], cls: str, today: int) -> float:
        days = {d for d in self._window('all', today) if dt.date.fromordinal(d).weekday() in CLASSES[cls]}
        return len(days & {r[0] for r in fit['_rows']}) / len(days) if days else 0.0

    @staticmethod
    def _clusters(pool: list[list[Any]], col: int, circular: bool, need: int, of: int) -> list[dict[str, Any]]:
        gap = (lambda a, b: abs(_wrap(a - b))) if circular else lambda a, b: abs(a - b)
        out = []
        while pool:
            best, best_days = (None, 0)
            for r in pool:
                n = len({x[0] for x in pool if gap(x[col], r[col]) <= T['window_min']})
                if n > best_days:
                    best, best_days = (r, n)
            if best is None or best_days < need or best_days / of < T['min_share']:
                break
            near = [x for x in pool if gap(x[col], best[col]) <= T['window_min']]
            per_day: dict[int, list[Any]] = {}
            for x in sorted(near, key=lambda x: gap(x[col], best[col])):
                per_day.setdefault(x[0], x)
            pool = [x for x in pool if x not in near]
            picked = list(per_day.values())
            center, spread, hits = _fit([x[col] for x in picked], circular)
            rel = [_wrap(x[col] - center) if circular else x[col] - center for x in picked]
            if hits >= need and hits / of >= T['min_share']:
                out.append({'center': center, 'spread': spread, 'drift': max(rel) - min(rel), 'hits': hits, 'rows': picked})
        return out

    @staticmethod
    def _fit_of(cls: str, kind: str, c: dict[str, Any], of: int) -> dict[str, Any]:
        rows = c['rows']
        levels = [x[4] for x in rows if isinstance(x[4], (int, float))]
        clock = _fit([x[1] for x in rows], True)[0]
        fit = {'class': cls, 'kind': kind, 'minute': clock, 'spread': c['spread'], 'hits': c['hits'], 'of': of, 'share': c['hits'] / of, 'days': [*CLASSES[cls]], 'level': statistics.median(levels) if levels else None, '_rows': rows}
        if kind != 'time':
            fit['offset'] = c['center']
        return fit

    def _automated_then(self, eid: str, fit: dict[str, Any], today: int) -> bool:
        days = set(self._window(fit['class'], today, any_day=True))
        hit = {r[0] for key, rows in self.data['auto'].items() if key.split('|')[0] == eid for r in rows if r[0] in days and abs(_wrap(r[1] - fit['minute'])) <= T['window_min']}
        return len(hit) >= T['auto_min_days']

    def suggestions(self, auto: Any, data: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.data['enabled']:
            return []
        found = self._mined if self._mined is not None else self.mine()
        found = [f for f in found if auto._pickable(f['entity_id'])]
        rules = [r for r in data.get('rules', {}).values() if isinstance(r, dict)]
        done = {r.get('from') for r in rules}
        out: list[dict[str, Any]] = []
        for group in self._groups(found):
            rule = self._rule(auto, group)
            if rule is None:
                continue
            key = 'habit_' + hashlib.sha1('|'.join([*sorted((f['entity_id'] for f in group)), group[0]['act'], group[0]['class'], group[0]['kind']]).encode()).hexdigest()[:10]
            if key in self.data['dismissed'] or key in done or self._ruled(auto, rules, group):
                continue
            try:
                clean = auto.validate({**rule, 'from': key})
            except Invalid as err:
                _LOGGER.debug('Hauflo habits: %s dropped (%s)', key, err)
                continue
            share = min((f['share'] for f in group))
            out.append({'key': key, 'rule': clean, 'class': group[0]['class'], 'hits': min((f['hits'] for f in group)), 'of': group[0]['of'], 'share': round(share, 2), 'entities': sorted((f['entity_id'] for f in group)), 'area': self.area(group[0]['entity_id'])})
        out.sort(key=lambda s: (-s['share'], -s['hits']))
        return out[:int(T['max_suggestions'])]

    def _groups(self, found: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        groups: list[list[dict[str, Any]]] = []
        for f in sorted(found, key=lambda f: (-f['share'], f['entity_id'])):
            area = self.area(f['entity_id'])
            for g in groups:
                h = g[0]
                if area and self.area(h['entity_id']) == area and (h['entity_id'].split('.')[0] == f['entity_id'].split('.')[0]) and (h['act'] == f['act']) and (h['class'] == f['class']) and (h['kind'] == f['kind']) and (abs(_wrap(h['minute'] - f['minute'])) <= T['window_min']) and (len(g) < 20):
                    g.append(f)
                    break
            else:
                groups.append([f])
        return groups

    @staticmethod
    def _round(value: float) -> int:
        return int(round(value / T['round_min']) * T['round_min'])

    def _rule(self, auto: Any, group: list[dict[str, Any]]) -> dict[str, Any] | None:
        head, eids = (group[0], sorted((f['entity_id'] for f in group)))
        domain, act = (head['entity_id'].split('.')[0], head['act'])
        if head['kind'] == 'time':
            minute = self._round(statistics.median([f['minute'] for f in group])) % DAY
            when: dict[str, Any] = {'event': 'time', 'at': f'{minute // 60:02d}:{minute % 60:02d}'}
        else:
            when = {'event': head['kind'], 'offset': self._round(statistics.median([f['offset'] for f in group]))}
        do = act if not act.startswith('pos') else 'position'
        levels = [f['level'] for f in group if f['level'] is not None]
        level = statistics.median(levels) if levels else None
        area = self.area(head['entity_id'])
        actions: list[dict[str, Any]] = []
        if area and len(eids) > 1 and (domain == 'light') and (act == 'off') and (set(auto._room_lights(area)) <= set(eids)):
            actions = [{'do': 'room_off', 'area_id': area}]
        elif area and len(eids) > 1 and (domain == 'cover') and (set(auto._room_covers(area, do == 'position')) <= set(eids)):
            actions = [{'do': 'room_covers', 'area_id': area, 'cover': do, **({'value': int(round(level))} if do == 'position' and level is not None else {})}]
        else:
            for f in sorted(group, key=lambda f: f['entity_id']):
                a: dict[str, Any] = {'entity_id': f['entity_id'], 'do': do}
                v = f['level']
                if do == 'position' and v is not None:
                    a['value'] = int(round(v))
                elif domain == 'light' and do == 'on' and (v is not None) and self._dims(f['entity_id']):
                    a['value'] = max(1, min(100, int(round(v / T['brightness_step']) * T['brightness_step']) or 1))
                elif do == 'temp':
                    if v is None:
                        return None
                    a['value'] = v
                actions.append(a)
        return {'when': when, 'days': [DAY_NAMES[d] for d in head['days']], 'actions': actions, 'enabled': True, 'name': ''}

    def _dims(self, eid: str) -> bool:
        st = self.hass.states.get(eid)
        return st is not None and any((m != 'onoff' for m in st.attributes.get('supported_color_modes') or []))

    def _ruled(self, auto: Any, rules: list[dict[str, Any]], group: list[dict[str, Any]]) -> bool:
        head = group[0]
        want = 'position' if head['act'].startswith('pos') else head['act']
        eids = {f['entity_id'] for f in group}
        for r in rules:
            w = r.get('when') or {}
            if head['kind'] == 'time':
                times = w.get('times') or [w.get('at')]
                if w.get('event') != 'time' or not any((isinstance(at, str) and ':' in at and (abs(_wrap(int(at[:2]) * 60 + int(at[3:5]) - head['minute'])) <= T['rule_near_min']) for at in times)):
                    continue
            elif w.get('event') != head['kind']:
                continue
            for a in r.get('actions') or []:
                if self._does(auto, a) & {(e, want) for e in eids}:
                    return True
        return False

    @staticmethod
    def _does(auto: Any, a: dict[str, Any]) -> set[tuple[str, str]]:
        return auto.covers(a) if isinstance(a, dict) else set()
