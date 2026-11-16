from __future__ import annotations
import statistics
from datetime import date, datetime, time as dtime, timedelta
from typing import TYPE_CHECKING, Any
from homeassistant.const import PERCENTAGE, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import State
from homeassistant.util import dt as dt_util
from .derived import NOT_A_STATE, SECONDS_PER_HOUR, num
from .insight import GAP_BAND, OBS_EPS, Detector, _iso, _round
from .tuning import TUNING
if TYPE_CHECKING:
    from .insight import Insights
_P = TUNING['predict']
_I = TUNING['insight']
DAY_S = timedelta(days=1).total_seconds()
WEEK = timedelta(weeks=1).days
TREND_DAYS = _P['trend_days']
RING_MAX = _P['ring_max']
NOISE_K = _P['noise_k']
GONE_AFTER = _I['gone_after_s']
ON_ACTIONS = set(_I['heat_actions']) | set(_I['cool_actions'])

def theil_sen(pts: list[tuple[float, float]]) -> tuple[float, float] | None:
    slopes = [(y2 - y1) / (x2 - x1) for i, (x1, y1) in enumerate(pts) for x2, y2 in pts[i + 1:] if x2 != x1]
    if not slopes:
        return None
    m = statistics.median(slopes)
    return (m, statistics.median((y - m * x for x, y in pts)))

def spread(pts: list[tuple[float, float]], m: float, b: float) -> float:
    return statistics.median((abs(y - (m * x + b)) for x, y in pts))

def day_of(ts: float) -> int:
    return dt_util.as_local(dt_util.utc_from_timestamp(ts)).date().toordinal()

def day_x(ts: float) -> float:
    local = dt_util.as_local(dt_util.utc_from_timestamp(ts))
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local.date().toordinal() + (local - midnight).total_seconds() / DAY_S

def date_in(now: float, days: float) -> str:
    return dt_util.as_local(dt_util.utc_from_timestamp(now + days * DAY_S)).date().isoformat()

def forecast(pts: list[tuple[float, float]], now_x: float, limit: float, sign: int, min_change: float) -> dict[str, float] | None:
    fit = theil_sen(pts)
    if fit is None:
        return None
    m, b = fit
    span = pts[-1][0] - pts[0][0]
    change = m * span * sign
    if m * sign <= 0 or change < min_change or spread(pts, m, b) * NOISE_K > change:
        return None
    level = m * now_x + b
    return {'level': level, 'slope': m, 'days': max(0.0, (limit - level) / m)}

class Series(Detector):
    agg = 'max'
    diagnostic = True

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        self.recs: dict[str, dict[str, Any]] = {}

    def kind_of(self, st: State) -> str | None:
        raise NotImplementedError

    def reading(self, st: State, kind: str) -> float | None:
        return num(st.state)

    def agg_of(self, kind: str) -> str:
        return self.agg

    def assess(self, eid: str, rec: dict[str, Any], now: float) -> dict[str, Any] | None:
        raise NotImplementedError

    def on_reading(self, rec: dict[str, Any], v: float) -> None:
        pass

    def load(self, data: dict[str, Any]) -> None:
        for eid, r in (data.get('series') or {}).items():
            if not isinstance(r, dict) or not isinstance(r.get('kind'), str):
                continue
            pts = [(int(p[0]), float(p[1])) for p in r.get('pts') or [] if isinstance(p, list) and len(p) == 2 and (num(p[0]) is not None) and (num(p[1]) is not None)]
            ring = [float(x) for x in r.get('ring') or [] if num(x) is not None][-RING_MAX:]
            self.recs[str(eid)] = {'kind': r['kind'], 'pts': pts[-TREND_DAYS:], 'day': int(num(r.get('day')) or 0), 'today': num(r.get('today')), 'ring': ring, 'last': num(r.get('last')), 'open': num(r.get('open')), 'since': num(r.get('since'))}

    def dump(self) -> dict[str, Any]:
        return {'series': {eid: {**r, 'pts': [list(p) for p in r['pts']]} for eid, r in self.recs.items()}}

    def _watch(self, st: State) -> str | None:
        if st.state in NOT_A_STATE or not self.ctx.visible(st.entity_id, self.diagnostic):
            return None
        return self.kind_of(st)

    def on_state(self, new: State, now: float) -> None:
        kind = self._watch(new)
        v = self.reading(new, kind) if kind else None
        if kind is None or v is None:
            return
        rec = self.recs.get(new.entity_id)
        if rec is None or rec['kind'] != kind:
            rec = self.recs[new.entity_id] = {'kind': kind, 'pts': [], 'day': day_of(now), 'today': None, 'ring': [], 'last': None, 'open': None, 'since': now}
        self._roll(rec, now)
        self.on_reading(rec, v)
        rec['last'] = v
        agg = self.agg_of(kind)
        if agg == 'median':
            if len(rec['ring']) >= RING_MAX:
                rec['ring'] = rec['ring'][::2]
            rec['ring'].append(v)
            rec['today'] = statistics.median(rec['ring'])
        else:
            pick = max if agg == 'max' else min
            rec['today'] = v if rec['today'] is None else pick(rec['today'], v)
        self.ctx.dirty()

    def _roll(self, rec: dict[str, Any], now: float) -> None:
        today = day_of(now)
        if rec['day'] == today:
            return
        held = rec['today'] if rec['today'] is not None else rec['last']
        if held is not None:
            rec['pts'] = [*rec['pts'], (rec['day'], round(held, 3))][-TREND_DAYS:]
        rec.update(day=today, today=None, ring=[])
        self.ctx.dirty()

    def tick(self, now: float) -> None:
        for st in self.ctx.hass.states.async_all(self.domains):
            if st.entity_id not in self.recs and self._watch(st):
                self.on_state(st, now)
        for eid, rec in list(self.recs.items()):
            self._roll(rec, now)
            if now - self.ctx.started > GONE_AFTER and self.ctx.hass.states.get(eid) is None:
                del self.recs[eid]
                self.ctx.dirty()
                continue
            found = self.assess(eid, rec, now)
            was = rec['open']
            rec['open'] = was or now if found is not None else None
            rec['found'] = found
            if (was is None) != (rec['open'] is None):
                self.ctx.dirty()

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for eid, rec in self.recs.items():
            found = rec.get('found')
            if rec['open'] is None or not found:
                continue
            kind = self.kinds[rec['kind']]
            out.append({'key': f'{kind}|{eid}', 'kind': kind, 'entityId': eid, **self.ctx.who(eid), 'since': _iso(rec['open']), **found})
        return out
    kinds: dict[str, str] = {}
    units: dict[str, str] = {}
    DEV = ('limit', 'value')

    def need(self, rec: dict[str, Any]) -> int:
        raise NotImplementedError

    def keys(self) -> list[tuple[str, str]]:
        return [(self.kinds[r['kind']], eid) for eid, r in self.recs.items() if r['kind'] in self.kinds]

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        rec = self.recs.get(eid)
        if rec is None:
            return self.learning(eid, 0, 0, now)
        need = self.need(rec)
        if len(rec['pts']) < need:
            return self.learning(eid, len(rec['pts']), need, now)
        return {'ready': {'norm': _round(rec['last']) if rec['last'] is not None else None, 'unit': self.units.get(rec['kind'], ''), 'n': len(rec['pts'])}}

    def reset(self, eid: str, kind: str) -> None:
        self.recs.pop(eid, None)

class BatteryForecast(Series):
    name = 'battery'
    domains = ('sensor',)
    agg = 'max'
    kinds = {'battery': 'battery_forecast'}
    units = {'battery': 'pct'}
    KINDS = ('battery_forecast',)
    LOW = _P['battery_low_pct']
    WARN = _P['battery_warn_days']

    def need(self, rec: dict[str, Any]) -> int:
        return _P['battery_min_days']

    def excluded(self, kind: str, eid: str) -> str | None:
        rec = self.recs.get(eid)
        if rec and len(rec['pts']) >= _P['battery_min_days'] and (len({v for _, v in rec['pts']}) < _P['battery_min_distinct']):
            return 'coarse_battery'
        return super().excluded(kind, eid)

    def kind_of(self, st: State) -> str | None:
        a = st.attributes
        return 'battery' if a.get('device_class') == 'battery' and a.get('unit_of_measurement') == PERCENTAGE else None

    def on_reading(self, rec: dict[str, Any], v: float) -> None:
        ref = [x for x in (rec['today'], rec['pts'][-1][1] if rec['pts'] else None) if x is not None]
        if ref and v - max(ref) >= _P['battery_jump_pct']:
            rec.update(pts=[], today=None, open=None, found=None, since=self.ctx.clock())

    def assess(self, eid: str, rec: dict[str, Any], now: float) -> dict[str, Any] | None:
        last = rec['last']
        pts = rec['pts']
        if last is None:
            return None
        fc = None
        if len(pts) >= _P['battery_min_days'] and pts[-1][0] - pts[0][0] >= _P['battery_min_span_days'] and (len({v for _, v in pts}) >= _P['battery_min_distinct']):
            fc = forecast(pts, day_x(now), self.LOW, -1, _P['battery_min_drop_pct'])
            if fc is not None and -fc['slope'] < _P['battery_min_slope_pct']:
                fc = None
        if rec['open'] is None:
            if fc is None or last <= self.LOW or fc['days'] >= self.WARN:
                return None
        elif last > self.LOW and (fc is None or fc['days'] >= self.WARN * _P['battery_end_factor']):
            return None
        days = 0.0 if last <= self.LOW or fc is None else min(fc['days'], (last - self.LOW) / -fc['slope'])
        return {'daysLeft': int(round(days)), 'date': date_in(now, days), 'value': _round(last), 'limit': self.LOW, **({'perWeek': _round(-fc['slope'] * WEEK)} if fc else {})}

class LinkQuality(Series):
    name = 'link'
    domains = ('sensor',)
    agg = 'median'
    kinds = {'lqi': 'link_quality', 'dbm': 'link_quality'}
    units = {'lqi': 'lqi', 'dbm': 'dbm'}
    KINDS = ('link_quality',)

    def need(self, rec: dict[str, Any]) -> int:
        return _P['link_min_days']

    def kind_of(self, st: State) -> str | None:
        a, eid = (st.attributes, st.entity_id)
        unit = a.get('unit_of_measurement')
        if unit in _P['link_lqi_units'] or any((eid.endswith(s) for s in _P['link_lqi_suffixes'])):
            return 'lqi'
        if a.get('device_class') in _P['link_dbm_classes'] or unit in _P['link_dbm_units']:
            return 'dbm'
        return None

    def assess(self, eid: str, rec: dict[str, Any], now: float) -> dict[str, Any] | None:
        pts = rec['pts'][-_P['link_days']:]
        lqi = rec['kind'] == 'lqi'
        warn = _P['lqi_warn'] if lqi else -_P['rssi_warn_dbm']
        fc = forecast(pts, day_x(now), warn, -1, _P['lqi_min_drop'] if lqi else _P['rssi_min_drop_db']) if len(pts) >= _P['link_min_days'] else None
        bound = _P['link_warn_days'] * (_P['link_end_factor'] if rec['open'] is not None else 1)
        if fc is None or fc['days'] >= bound:
            return None
        return {'daysLeft': int(round(fc['days'])), 'date': date_in(now, fc['days']), 'value': _round(fc['level']), 'limit': warn, 'perWeek': _round(fc['slope'] * WEEK)}

class HaHost(Series):
    name = 'host'
    domains = ('sensor',)
    agg = 'max'
    kinds = {'use': 'disk_full', 'free': 'disk_full', 'backup': 'backup_old'}
    units = {'use': 'pct', 'free': 'gb', 'backup': 'days'}
    KINDS = ('disk_full', 'backup_old')

    def need(self, rec: dict[str, Any]) -> int:
        return 0 if rec['kind'] == 'backup' else _P['disk_min_days']

    def _key(self, st: State) -> tuple[str | None, str | None]:
        entry = self.ctx.entry(st.entity_id)
        return (entry.platform, entry.translation_key) if entry else (None, None)

    def kind_of(self, st: State) -> str | None:
        platform, key = self._key(st)
        if platform in _P['backup_platforms'] and key in _P['backup_keys']:
            return 'backup'
        if platform not in _P['host_platforms']:
            return None
        if key in _P['disk_use_keys']:
            return 'use'
        if key in _P['disk_free_keys'] and st.attributes.get('unit_of_measurement') in _P['disk_units']:
            return 'free'
        return None

    def _watch(self, st: State) -> str | None:
        if st.state == STATE_UNKNOWN and self.ctx.visible(st.entity_id, self.diagnostic):
            return 'backup' if self.kind_of(st) == 'backup' else None
        return super()._watch(st)

    def on_state(self, new: State, now: float) -> None:
        if new.state == STATE_UNKNOWN and self._watch(new) == 'backup':
            rec = self.recs.get(new.entity_id)
            if rec is None or rec['kind'] != 'backup':
                self.recs[new.entity_id] = {'kind': 'backup', 'pts': [], 'day': day_of(now), 'today': None, 'ring': [], 'last': None, 'open': None, 'since': now}
                self.ctx.dirty()
            return
        super().on_state(new, now)

    def reading(self, st: State, kind: str) -> float | None:
        if kind == 'backup':
            at = dt_util.parse_datetime(st.state)
            return at.timestamp() if at else None
        v = num(st.state)
        if v is None or kind != 'free':
            return v
        return v * _P['disk_unit_gb'][_P['disk_units'].index(st.attributes.get('unit_of_measurement'))]

    def agg_of(self, kind: str) -> str:
        return 'min' if kind == 'free' else 'max'

    def assess(self, eid: str, rec: dict[str, Any], now: float) -> dict[str, Any] | None:
        kind, last = (rec['kind'], rec['last'])
        if last is None:
            st = self.ctx.hass.states.get(eid)
            if kind != 'backup':
                return None
            if st is None or (st.state != STATE_UNKNOWN and st.state in NOT_A_STATE):
                return rec.get('found') if rec['open'] is not None else None
            if st.state == STATE_UNKNOWN and now - (rec['since'] or now) >= _P['backup_never_days'] * DAY_S:
                return {'limit': _P['backup_max_days']}
            return None
        if kind == 'backup':
            age = (now - last) / DAY_S
            if age <= _P['backup_max_days']:
                return None
            return {'days': int(age), 'date': dt_util.as_local(dt_util.utc_from_timestamp(last)).date().isoformat(), 'limit': _P['backup_max_days']}
        if kind == 'use':
            limit, sign, change, past = (_P['disk_full_pct'], 1, _P['disk_min_rise_pct'], last >= _P['disk_full_pct'])
        else:
            limit, sign, change, past = (_P['disk_min_free_gb'], -1, _P['disk_min_fall_gb'], last <= _P['disk_min_free_gb'])
        pts = rec['pts']
        fc = forecast(pts, day_x(now), limit, sign, change) if len(pts) >= _P['disk_min_days'] else None
        bound = _P['disk_warn_days'] * (_P['disk_end_factor'] if rec['open'] is not None else 1)
        if not past and (fc is None or fc['days'] >= bound):
            return None
        days = 0.0 if past or fc is None else fc['days']
        return {'daysLeft': int(round(days)), 'date': date_in(now, days), 'value': _round(last), 'limit': limit, **({'perWeek': _round(fc['slope'] * WEEK)} if fc else {})}

class HeatSlowing(Detector):
    name = 'slowing'
    domains = ()
    KIND = {'heat': 'heat_slowing', 'cool': 'cool_slowing'}
    KINDS = tuple(KIND.values())
    UNIT = 'c_per_h'
    DEV = ('wasRate', 'nowRate')

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        self.recs: dict[str, dict[str, dict[str, Any]]] = {}

    def load(self, data: dict[str, Any]) -> None:
        for eid, by in (data.get('climates') or {}).items():
            if not isinstance(by, dict):
                continue
            for d, r in by.items():
                if d not in self.KIND or not isinstance(r, dict):
                    continue
                hist = [[float(h[0]), float(h[1]), num(h[2]), num(h[3]) if len(h) == 4 else None] for h in r.get('hist') or [] if isinstance(h, list) and len(h) in (3, 4) and (num(h[0]) is not None) and ((num(h[1]) or 0) > 0)]
                self.recs.setdefault(str(eid), {})[d] = {'hist': hist[-_P['slow_keep']:], 'open': num(r.get('open')), 'cond': num(r.get('cond'))}

    def dump(self) -> dict[str, Any]:
        return {'climates': {eid: {d: {k: r.get(k) for k in ('hist', 'open', 'cond')} for d, r in by.items()} for eid, by in self.recs.items()}}

    def on_state(self, new: State, now: float) -> None:
        return

    def on_learned(self, eid: str, direction: str, rate: float, now: float, gap: float | None=None) -> None:
        if direction not in self.KIND or rate <= 0 or self.ctx.air_handler(eid):
            return
        self.ctx.mark(self.name, eid, now)
        r = self.recs.setdefault(eid, {}).setdefault(direction, {'hist': [], 'open': None, 'cond': None})
        r['hist'] = [*r['hist'], [now, rate, self.ctx.outdoor_c(), gap]][-_P['slow_keep']:]
        self._assess(r, now, eid, direction)
        self.ctx.dirty()

    @staticmethod
    def need() -> int:
        return max(_P['trend_recent'] + _P['slow_min_old'], OBS_EPS)

    def _ready(self, eid: str, hist: list[list[Any]]) -> bool:
        return len(hist) >= self.need() and self.ctx.observed(self.name, eid)

    @staticmethod
    def _like_gap(old: list[list[Any]], gap: Any) -> list[list[Any]]:
        if gap is None:
            return old
        return [o for o in old if o[3] is None or abs(o[3] - gap) <= GAP_BAND]

    def compare(self, hist: list[list[Any]]) -> tuple[float, float, bool] | None:
        n = _P['trend_recent']
        recent, old = (hist[-n:], hist[:-n])
        if len(recent) < n or len(old) < _P['slow_min_old']:
            return None
        if all((h[2] is not None for h in recent)):
            pairs = []
            for h in recent:
                like = [o[1] for o in self._like_gap(old, h[3]) if o[2] is not None and abs(o[2] - h[2]) <= _P['slow_band_c']]
                if len(like) >= _P['slow_min_like']:
                    pairs.append((statistics.median(like), h[1]))
            if len(pairs) == n:
                return (statistics.median((p[0] for p in pairs)), statistics.median((p[1] for p in pairs)), True)
        gaps = [h[3] for h in recent if h[3] is not None]
        like_old = self._like_gap(old, statistics.median(gaps)) if gaps else old
        base = like_old if len(like_old) >= _P['slow_min_old'] else old
        return (statistics.median((h[1] for h in base)), statistics.median((h[1] for h in recent)), False)

    def _assess(self, r: dict[str, Any], now: float, eid: str, d: str) -> None:
        got = self.compare(r['hist']) if self._ready(eid, r['hist']) else None
        if got is None:
            r['open'] = r['cond'] = None
            return
        was, cur, like = got
        drop = was / cur if cur > 0 else 0.0
        if r['open'] is not None:
            if drop < _P['slow_end_factor']:
                r['open'] = r['cond'] = None
            return
        if drop < self.ctx.factor(f'{self.KIND[d]}|{eid}', _P['slow_factor']):
            r['cond'] = None
            return
        if like:
            r['open'] = now
            return
        r['cond'] = r['cond'] or now
        if now - r['cond'] >= _P['slow_persist_days'] * DAY_S:
            r['open'] = now

    def tick(self, now: float) -> None:
        for eid in list(self.recs):
            if now - self.ctx.started > GONE_AFTER and self.ctx.hass.states.get(eid) is None:
                del self.recs[eid]
                self.ctx.dirty()
                continue
            for d, r in self.recs[eid].items():
                was = r['open']
                self._assess(r, now, eid, d)
                if was != r['open']:
                    self.ctx.dirty()

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for eid, by in self.recs.items():
            if self.ctx.air_handler(eid):
                continue
            for d, r in by.items():
                got = self.compare(r['hist']) if r['open'] is not None else None
                if got is None:
                    continue
                kind = self.KIND[d]
                out.append({'key': f'{kind}|{eid}', 'kind': kind, 'entityId': eid, **self.ctx.who(eid), 'since': _iso(r['open']), 'wasRate': _round(got[0]), 'nowRate': _round(got[1])})
        return out

    def inputs(self, kind: str, eid: str) -> list[dict[str, Any]]:
        return self.ctx.climate_inputs(eid)

    def keys(self) -> list[tuple[str, str]]:
        return [(self.KIND[d], eid) for eid, by in self.recs.items() for d in by]

    def excluded(self, kind: str, eid: str) -> str | None:
        return 'air_handler' if self.ctx.air_handler(eid) else super().excluded(kind, eid)

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        d = next((d for d, k in self.KIND.items() if k == kind))
        hist = (self.recs.get(eid) or {}).get(d, {}).get('hist') or []
        if not self._ready(eid, hist):
            return self.learning(eid, len(hist), self.need(), now)
        return {'ready': {'norm': _round(statistics.median((h[1] for h in hist[:-_P['trend_recent']]))), 'unit': self.UNIT, 'n': len(hist)}}

    def episodes(self, eid: str) -> list[dict[str, Any]]:
        return []

    def reset(self, eid: str, kind: str) -> None:
        d = next((d for d, k in self.KIND.items() if k == kind))
        (self.recs.get(eid) or {}).pop(d, None)
        if not self.recs.get(eid):
            self.recs.pop(eid, None)

class ShortCycling(Detector):
    name = 'cycling'
    domains = ('climate', *_P['cycle_switch_domains'])
    KIND = 'short_cycling'
    KINDS = (KIND,)
    UNIT = 'per_hour'
    DEV = ('normPerHour', 'perHour')

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        self.recs: dict[str, dict[str, Any]] = {}
        self._climate_devices: set[str] = set()

    def load(self, data: dict[str, Any]) -> None:
        for eid, r in (data.get('entities') or {}).items():
            if not isinstance(r, dict):
                continue
            self.recs[str(eid)] = {'ring': [float(x) for x in r.get('ring') or [] if num(x) is not None][-_P['cycle_ring']:], 'day': int(num(r.get('day')) or 0), 'n': int(num(r.get('n')) or 0), 'hours': int(num(r.get('hours')) or 0), 'hour': num(r.get('hour')), 'pts': [float(x) for x in r.get('pts') or [] if num(x) is not None][-_P['cycle_days']:], 'open': num(r.get('open'))}

    def dump(self) -> dict[str, Any]:
        return {'entities': self.recs}

    @staticmethod
    def _on(st: State | None) -> bool | None:
        if st is None or st.state in NOT_A_STATE:
            return None
        if st.domain == 'climate':
            action = st.attributes.get('hvac_action')
            return None if action is None else action in ON_ACTIONS
        return st.state == 'on'

    def _watched(self, st: State) -> bool:
        if not self.ctx.visible(st.entity_id):
            return False
        if st.domain == 'climate':
            return 'hvac_action' in st.attributes and (not self.ctx.air_handler(st.entity_id))
        entry = self.ctx.entry(st.entity_id)
        return bool(entry and entry.device_id and (entry.device_id in self._climate_devices))

    def on_state(self, new: State, now: float) -> None:
        return

    def on_event(self, old: State | None, new: State, now: float) -> None:
        was, on = (self._on(old), self._on(new))
        if was is not False or on is not True or (not self._watched(new)):
            return
        rec = self.recs.get(new.entity_id) or self.recs.setdefault(new.entity_id, {'ring': [], 'day': day_of(now), 'n': 0, 'hours': 0, 'hour': None, 'pts': [], 'open': None})
        self._roll(rec, now)
        rec['ring'] = [*rec['ring'], now][-_P['cycle_ring']:]
        rec['n'] += 1
        hour = now // SECONDS_PER_HOUR
        if rec['hour'] != hour:
            rec['hours'] += 1
            rec['hour'] = hour
        self._assess(rec, now, new.entity_id)
        self.ctx.dirty()

    def _roll(self, rec: dict[str, Any], now: float) -> None:
        today = day_of(now)
        if rec['day'] == today:
            return
        if rec['n']:
            rec['pts'] = [*rec['pts'], round(rec['n'] / max(rec['hours'], 1), 3)][-_P['cycle_days']:]
        rec.update(day=today, n=0, hours=0)

    def norm(self, rec: dict[str, Any], eid: str | None=None) -> float | None:
        if len(rec['pts']) < _P['cycle_learn_days'] or (eid is not None and (not self.ctx.observed(self.name, eid))):
            return None
        return statistics.median(rec['pts'])

    def per_hour(self, rec: dict[str, Any], now: float) -> float:
        window = _P['cycle_window_h']
        return sum((1 for t in rec['ring'] if now - t <= window * SECONDS_PER_HOUR)) / window

    def _assess(self, rec: dict[str, Any], now: float, eid: str) -> None:
        norm = self.norm(rec, eid)
        rate = self.per_hour(rec, now)
        if norm is None:
            rec['open'] = None
        elif rec['open'] is None:
            if rate >= max(self.ctx.factor(f'{self.KIND}|{eid}', _P['cycle_factor']) * norm, _P['cycle_min_per_h']):
                rec['open'] = now
        elif rate < max(_P['cycle_end_factor'] * norm, _P['cycle_end_min_per_h']):
            rec['open'] = None

    def tick(self, now: float) -> None:
        devices = set()
        for st in self.ctx.hass.states.async_all('climate'):
            entry = self.ctx.entry(st.entity_id)
            if entry and entry.device_id and (not self.ctx.air_handler(st.entity_id)):
                devices.add(entry.device_id)
        self._climate_devices = devices
        for eid in list(self.recs):
            rec = self.recs[eid]
            if now - self.ctx.started > GONE_AFTER and self.ctx.hass.states.get(eid) is None:
                del self.recs[eid]
                self.ctx.dirty()
                continue
            was = (rec['day'], rec['open'])
            self._roll(rec, now)
            self._assess(rec, now, eid)
            if was != (rec['day'], rec['open']):
                self.ctx.dirty()

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for eid, rec in self.recs.items():
            norm = self.norm(rec, eid) if rec['open'] is not None else None
            if norm is None or self.ctx.air_handler(eid):
                continue
            out.append({'key': f'{self.KIND}|{eid}', 'kind': self.KIND, 'entityId': eid, **self.ctx.who(eid), 'since': _iso(rec['open']), 'perHour': _round(self.per_hour(rec, now)), 'normPerHour': _round(norm)})
        return out

    def keys(self) -> list[tuple[str, str]]:
        return [(self.KIND, eid) for eid in self.recs]

    def excluded(self, kind: str, eid: str) -> str | None:
        return 'air_handler' if self.ctx.air_handler(eid) else super().excluded(kind, eid)

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        rec = self.recs.get(eid)
        norm = self.norm(rec, eid) if rec else None
        if norm is None:
            return self.learning(eid, len(rec['pts']) if rec else 0, _P['cycle_learn_days'], now)
        return {'ready': {'norm': _round(norm), 'unit': self.UNIT, 'n': len(rec['pts'])}}

    def reset(self, eid: str, kind: str) -> None:
        self.recs.pop(eid, None)

class Flapping(Detector):
    name = 'flapping'
    domains = None
    KIND = 'flapping'
    KINDS = (KIND,)
    UNIT = 'per_day'
    DEV = ('normPerDay', 'perDay')

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        self.recs: dict[str, dict[str, Any]] = {}
        self.pending: list[tuple[float, str, str]] = []
        self.first_day: int | None = None

    def load(self, data: dict[str, Any]) -> None:
        self.first_day = int(num(data.get('first')) or 0) or None
        for unit, r in (data.get('units') or {}).items():
            if isinstance(r, dict) and isinstance(r.get('eid'), str):
                self.recs[str(unit)] = {'eid': r['eid'], 'day': int(num(r.get('day')) or 0), 'n': int(num(r.get('n')) or 0), 'days': [int(x) for x in r.get('days') or [] if num(x) is not None][-_P['flap_days']:], 'open': num(r.get('open'))}

    def dump(self) -> dict[str, Any]:
        return {'first': self.first_day, 'units': self.recs}

    def on_state(self, new: State, now: float) -> None:
        return

    def on_event(self, old: State | None, new: State, now: float) -> None:
        if new.state != STATE_UNAVAILABLE or old is None or old.state == STATE_UNAVAILABLE:
            return
        if now - self.ctx.started < _P['flap_grace_s'] or not self.ctx.visible(new.entity_id):
            return
        entry = self.ctx.entry(new.entity_id)
        self.pending.append((now, entry.device_id if entry and entry.device_id else new.entity_id, new.entity_id))

    def _count(self, now: float) -> None:
        window = _P['flap_mass_s']
        ready = [p for p in self.pending if now - p[0] > window]
        if not ready:
            return
        for t, unit, eid in ready:
            around = {u for tt, u, _e in self.pending if abs(tt - t) <= window}
            if len(around) >= _P['flap_mass']:
                continue
            rec = self.recs.get(unit)
            if rec is None:
                if len(self.recs) >= _P['flap_max']:
                    continue
                today = day_of(now)
                zeros = max(0, min(_P['flap_days'], today - (self.first_day or today)))
                rec = self.recs[unit] = {'eid': eid, 'day': today, 'n': 0, 'days': [0] * zeros, 'open': None, 'at': None}
            if rec.get('at') is not None and abs(t - rec['at']) <= window:
                continue
            rec['at'] = t
            rec['n'] += 1
        self.pending = [p for p in self.pending if now - p[0] <= window * 2]
        self.ctx.dirty()

    def rates(self, rec: dict[str, Any]) -> tuple[float, float] | None:
        n = _P['flap_recent_days']
        recent = [*rec['days'][-(n - 1):], rec['n']] if n > 1 else [rec['n']]
        old = rec['days'][:-(n - 1)] if n > 1 else rec['days']
        if len(old) < _P['flap_learn_days']:
            return None
        return (sum(recent) / n, statistics.median(old))

    def tick(self, now: float) -> None:
        today = day_of(now)
        if self.first_day is None:
            self.first_day = today
            self.ctx.dirty()
        self._count(now)
        for unit in list(self.recs):
            rec = self.recs[unit]
            if rec['day'] != today:
                gap = today - rec['day']
                rec['days'] = [*rec['days'], rec['n'], *[0] * max(0, min(gap - 1, _P['flap_days']))][-_P['flap_days']:]
                rec.update(day=today, n=0)
                if not any(rec['days']) and rec['open'] is None:
                    del self.recs[unit]
                    self.ctx.dirty()
                    continue
                self.ctx.dirty()
            got = self.rates(rec)
            was = rec['open']
            if got is None:
                rec['open'] = None
            else:
                recent, norm = got
                if was is None and recent >= max(self.ctx.factor(f"{self.KIND}|{rec['eid']}", _P['flap_factor']) * norm, _P['flap_min_per_day']):
                    rec['open'] = now
                elif was is not None and recent < max(_P['flap_end_factor'] * norm, _P['flap_end_per_day']):
                    rec['open'] = None
            if was != rec['open']:
                self.ctx.dirty()

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for rec in self.recs.values():
            got = self.rates(rec) if rec['open'] is not None else None
            if got is None:
                continue
            eid = rec['eid']
            out.append({'key': f'{self.KIND}|{eid}', 'kind': self.KIND, 'entityId': eid, **self.ctx.who(eid), 'since': _iso(rec['open']), 'perDay': _round(got[0]), 'normPerDay': _round(got[1])})
        return out

    def keys(self) -> list[tuple[str, str]]:
        return [(self.KIND, eid) for eid in dict.fromkeys((r['eid'] for r in self.recs.values()))]

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        rec = next((r for r in self.recs.values() if r['eid'] == eid), None)
        got = self.rates(rec) if rec else None
        if got is None:
            old = max(0, len(rec['days']) - (_P['flap_recent_days'] - 1)) if rec else 0
            return self.learning(eid, old, _P['flap_learn_days'], now)
        return {'ready': {'norm': _round(got[1]), 'unit': self.UNIT, 'n': len(rec['days'])}}

    def reset(self, eid: str, kind: str) -> None:
        for unit in [u for u, r in self.recs.items() if r['eid'] == eid]:
            del self.recs[unit]

class NightWater(Detector):
    name = 'water'
    domains = ('sensor',)
    KIND = 'night_water'
    KINDS = (KIND,)
    UNIT = 'l'
    DEV = ('normL', 'nightL')

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        self.recs: dict[str, dict[str, Any]] = {}

    def load(self, data: dict[str, Any]) -> None:
        for eid, r in (data.get('meters') or {}).items():
            if not isinstance(r, dict):
                continue
            nights = [[int(n[0]), float(n[1])] for n in r.get('nights') or [] if isinstance(n, list) and len(n) == 2 and (num(n[0]) is not None) and (num(n[1]) is not None)]
            self.recs[str(eid)] = {'value': num(r.get('value')), 'night': num(r.get('night')), 'base': num(r.get('base')), 'nights': nights[-_P['night_keep']:], 'open': num(r.get('open'))}

    def dump(self) -> dict[str, Any]:
        return {'meters': self.recs}

    @staticmethod
    def litres(st: State) -> float | None:
        a = st.attributes
        unit = a.get('unit_of_measurement')
        if a.get('device_class') != 'water' or a.get('state_class') not in _P['water_state_classes'] or unit not in _P['water_units']:
            return None
        v = num(st.state)
        return None if v is None else v * _P['water_unit_l'][_P['water_units'].index(unit)]

    @staticmethod
    def night_of(now: float) -> int | None:
        local = dt_util.as_local(dt_util.utc_from_timestamp(now))
        h, a, b = (local.hour, _P['night_from_h'], _P['night_to_h'])
        inside = a <= h < b if a < b else h >= a or h < b
        if not inside:
            return None
        day = local.date().toordinal()
        return day - 1 if a > b and h < b else day

    def _end_of(self, night: int) -> float:
        a, b = (_P['night_from_h'], _P['night_to_h'])
        day = night + (1 if a > b else 0)
        local = datetime.combine(date.fromordinal(day), dtime(hour=int(b)), tzinfo=dt_util.get_default_time_zone())
        return local.timestamp()

    def _roll(self, rec: dict[str, Any], now: float) -> None:
        night = self.night_of(now)
        if night is not None and rec['night'] != night:
            rec['night'], rec['base'] = (night, rec['value'])
            self.ctx.dirty()
        elif night is None and rec['night'] is not None:
            done = int(rec['night'])
            if rec['base'] is not None and rec['value'] is not None and (now - self._end_of(done) <= _P['night_close_grace_h'] * SECONDS_PER_HOUR):
                used = rec['value'] - rec['base']
                if used >= 0:
                    rec['nights'] = [*rec['nights'], [done, round(used, 3)]][-_P['night_keep']:]
            rec['night'] = rec['base'] = None
            self._assess(rec, now)
            self.ctx.dirty()

    def on_state(self, new: State, now: float) -> None:
        if new.state in NOT_A_STATE or not self.ctx.visible(new.entity_id):
            return
        v = self.litres(new)
        if v is None:
            return
        rec = self.recs.setdefault(new.entity_id, {'value': None, 'night': None, 'base': None, 'nights': [], 'open': None})
        self._roll(rec, now)
        if rec['night'] is not None and rec['base'] is None:
            rec['base'] = v
        rec['value'] = v
        self.ctx.dirty()

    def check(self, rec: dict[str, Any]) -> tuple[float, float] | None:
        n = _P['night_days']
        nights = rec['nights']
        recent, old = (nights[-n:], nights[:-n])
        if len(recent) < n or len(old) < _P['night_learn']:
            return None
        norm = statistics.median((v for _d, v in old))
        return (statistics.median((v for _d, v in recent)), max(norm * self.ctx.factor(f'{self.KIND}|{self._eid_of(rec)}', _P['night_factor']), norm + _P['night_min_l']))

    def _eid_of(self, rec: dict[str, Any]) -> str:
        return next((e for e, r in self.recs.items() if r is rec), '')

    def _assess(self, rec: dict[str, Any], now: float) -> None:
        got = self.check(rec)
        n = _P['night_days']
        if got is None:
            rec['open'] = None
            return
        _med, thr = got
        recent = rec['nights'][-n:]
        if rec['open'] is None:
            in_a_row = all((recent[i + 1][0] == recent[i][0] + 1 for i in range(n - 1)))
            if in_a_row and all((v > thr for _d, v in recent)):
                rec['open'] = now
        elif recent[-1][1] <= thr:
            rec['open'] = None

    def tick(self, now: float) -> None:
        for st in self.ctx.hass.states.async_all('sensor'):
            if st.entity_id not in self.recs and self.litres(st) is not None:
                self.on_state(st, now)
        for eid in list(self.recs):
            if now - self.ctx.started > GONE_AFTER and self.ctx.hass.states.get(eid) is None:
                del self.recs[eid]
                self.ctx.dirty()
                continue
            self._roll(self.recs[eid], now)

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for eid, rec in self.recs.items():
            got = self.check(rec) if rec['open'] is not None else None
            if got is None:
                continue
            old = rec['nights'][:-_P['night_days']]
            out.append({'key': f'{self.KIND}|{eid}', 'kind': self.KIND, 'entityId': eid, **self.ctx.who(eid), 'since': _iso(rec['open']), 'nightL': _round(got[0]), 'normL': _round(statistics.median((v for _d, v in old))), 'nights': _P['night_days']})
        return out

    def keys(self) -> list[tuple[str, str]]:
        return [(self.KIND, eid) for eid in self.recs]

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        nights = (self.recs.get(eid) or {}).get('nights') or []
        old = nights[:-_P['night_days']]
        if len(nights) < _P['night_days'] or len(old) < _P['night_learn']:
            return self.learning(eid, max(0, len(nights) - _P['night_days']), _P['night_learn'], now)
        return {'ready': {'norm': _round(statistics.median((v for _d, v in old))), 'unit': self.UNIT, 'n': len(nights)}}

    def reset(self, eid: str, kind: str) -> None:
        self.recs.pop(eid, None)
TRENDS: tuple[type[Detector], ...] = (BatteryForecast, HeatSlowing, ShortCycling, LinkQuality, Flapping, HaHost, NightWater)
