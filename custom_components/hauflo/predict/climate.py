"""Forecasts of heating and cooling: slower effect and short cycling."""
from __future__ import annotations
import statistics
from typing import TYPE_CHECKING, Any, ClassVar
from homeassistant.core import State
from ..derived import NOT_A_STATE, SECONDS_PER_HOUR, num
from ..insight.common import gap_band, iso, obs_eps, round2
from ..insight.detector import CanonAttr, Detector
if TYPE_CHECKING:
    from ..insight.engine import Insights
from .stats import DAY_S, day_of, gone_after, on_actions, p_cfg

class HeatSlowing(Detector):
    """ClimateEffect's learned rates (°C/h) over time: the recent ones clearly slower than the zone's older ones."""
    name = 'slowing'
    domains = ()
    KIND: ClassVar[dict[str, str]] = {'heat': 'heat_slowing', 'cool': 'cool_slowing'}
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
                self.recs.setdefault(str(eid), {})[d] = {'hist': hist[-p_cfg()['slow_keep']:], 'open': num(r.get('open')), 'cond': num(r.get('cond'))}

    def dump(self) -> dict[str, Any]:
        return {'climates': {eid: {d: {k: r.get(k) for k in ('hist', 'open', 'cond')} for d, r in by.items()} for eid, by in self.recs.items()}}

    def on_state(self, new: State, now: float) -> None:
        return

    def on_learned(self, eid: str, direction: str, rate: float, now: float, gap: float | None=None) -> None:
        if direction not in self.KIND or rate <= 0 or self.ctx.air_handler(eid):
            return
        self.ctx.mark(self.name, eid, now)
        r = self.recs.setdefault(eid, {}).setdefault(direction, {'hist': [], 'open': None, 'cond': None})
        r['hist'] = [*r['hist'], [now, rate, self.ctx.outdoor_c(), gap]][-p_cfg()['slow_keep']:]
        self._assess(r, now, eid, direction)
        self.ctx.dirty()

    @staticmethod
    def need() -> int:
        return max(p_cfg()['trend_recent'] + p_cfg()['slow_min_old'], obs_eps())

    def _ready(self, eid: str, hist: list[list[Any]]) -> bool:
        return len(hist) >= self.need() and self.ctx.observed(self.name, eid)

    @staticmethod
    def _like_gap(old: list[list[Any]], gap: Any) -> list[list[Any]]:
        if gap is None:
            return old
        return [o for o in old if o[3] is None or abs(o[3] - gap) <= gap_band()]

    def compare(self, hist: list[list[Any]]) -> tuple[float, float, bool] | None:
        """(was, now — °C/h, like-for-like by the outdoor temperature?) or None — too little history. Each recent
        episode
        against the older ones that started as far from the target (a 3° gap is crossed faster than a 1° one)."""
        n = p_cfg()['trend_recent']
        recent, old = (hist[-n:], hist[:-n])
        if len(recent) < n or len(old) < p_cfg()['slow_min_old']:
            return None
        if all((h[2] is not None for h in recent)):
            pairs = []
            for h in recent:
                like = [o[1] for o in self._like_gap(old, h[3]) if o[2] is not None and abs(o[2] - h[2]) <= p_cfg()['slow_band_c']]
                if len(like) >= p_cfg()['slow_min_like']:
                    pairs.append((statistics.median(like), h[1]))
            if len(pairs) == n:
                return (statistics.median((p[0] for p in pairs)), statistics.median((p[1] for p in pairs)), True)
        gaps = [h[3] for h in recent if h[3] is not None]
        like_old = self._like_gap(old, statistics.median(gaps)) if gaps else old
        base = like_old if len(like_old) >= p_cfg()['slow_min_old'] else old
        return (statistics.median((h[1] for h in base)), statistics.median((h[1] for h in recent)), False)

    def _assess(self, r: dict[str, Any], now: float, eid: str, d: str) -> None:
        got = self.compare(r['hist']) if self._ready(eid, r['hist']) else None
        if got is None:
            r['open'] = r['cond'] = None
            return
        was, cur, like = got
        drop = was / cur if cur > 0 else 0.0
        if r['open'] is not None:
            if drop < p_cfg()['slow_end_factor']:
                r['open'] = r['cond'] = None
            return
        if drop < self.ctx.factor(f'{self.KIND[d]}|{eid}', p_cfg()['slow_factor']):
            r['cond'] = None
            return
        if like:
            r['open'] = now
            return
        r['cond'] = r['cond'] or now
        if now - r['cond'] >= p_cfg()['slow_persist_days'] * DAY_S:
            r['open'] = now

    def tick(self, now: float) -> None:
        for eid in list(self.recs):
            if now - self.ctx.started > gone_after() and self.ctx.hass.states.get(eid) is None:
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
                out.append({'key': f'{kind}|{eid}', 'kind': kind, 'entityId': eid, **self.ctx.who(eid), 'since': iso(r['open']), 'wasRate': round2(got[0]), 'nowRate': round2(got[1])})
        return out

    def inputs(self, kind: str, eid: str) -> list[dict[str, Any]]:
        """The climate's heating / cooling episodes (ClimateEffect's), its room's windows, the street, the season."""
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
        return {'ready': {'norm': round2(statistics.median((h[1] for h in hist[:-p_cfg()['trend_recent']]))), 'unit': self.UNIT, 'n': len(hist)}}

    def episodes(self, eid: str) -> list[dict[str, Any]]:
        return []

    def reset(self, eid: str, kind: str) -> None:
        d = next((d for d, k in self.KIND.items() if k == kind))
        (self.recs.get(eid) or {}).pop(d, None)
        if not self.recs.get(eid):
            self.recs.pop(eid, None)

class ShortCycling(Detector):
    """Starts per hour (a thermostat beginning to heat / cool, a relay / valve on the climate's own device switching on)
    over the last `cycle_window_h` against the entity's own daily norm (starts per active hour)."""
    name = 'cycling'
    domains = CanonAttr(lambda: ('climate', *p_cfg()['cycle_switch_domains']))
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
            self.recs[str(eid)] = {'ring': [float(x) for x in r.get('ring') or [] if num(x) is not None][-p_cfg()['cycle_ring']:], 'day': int(num(r.get('day')) or 0), 'n': int(num(r.get('n')) or 0), 'hours': int(num(r.get('hours')) or 0), 'hour': num(r.get('hour')), 'pts': [float(x) for x in r.get('pts') or [] if num(x) is not None][-p_cfg()['cycle_days']:], 'open': num(r.get('open'))}

    def dump(self) -> dict[str, Any]:
        return {'entities': self.recs}

    @staticmethod
    def _on(st: State | None) -> bool | None:
        if st is None or st.state in NOT_A_STATE:
            return None
        if st.domain == 'climate':
            action = st.attributes.get('hvac_action')
            return None if action is None else action in on_actions()
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
        rec['ring'] = [*rec['ring'], now][-p_cfg()['cycle_ring']:]
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
            rec['pts'] = [*rec['pts'], round(rec['n'] / max(rec['hours'], 1), 3)][-p_cfg()['cycle_days']:]
        rec.update(day=today, n=0, hours=0)

    def norm(self, rec: dict[str, Any], eid: str | None=None) -> float | None:
        if len(rec['pts']) < p_cfg()['cycle_learn_days'] or (eid is not None and (not self.ctx.observed(self.name, eid))):
            return None
        return statistics.median(rec['pts'])

    def per_hour(self, rec: dict[str, Any], now: float) -> float:
        window = p_cfg()['cycle_window_h']
        return sum((1 for t in rec['ring'] if now - t <= window * SECONDS_PER_HOUR)) / window

    def _assess(self, rec: dict[str, Any], now: float, eid: str) -> None:
        norm = self.norm(rec, eid)
        rate = self.per_hour(rec, now)
        if norm is None:
            rec['open'] = None
        elif rec['open'] is None:
            if rate >= max(self.ctx.factor(f'{self.KIND}|{eid}', p_cfg()['cycle_factor']) * norm, p_cfg()['cycle_min_per_h']):
                rec['open'] = now
        elif rate < max(p_cfg()['cycle_end_factor'] * norm, p_cfg()['cycle_end_min_per_h']):
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
            if now - self.ctx.started > gone_after() and self.ctx.hass.states.get(eid) is None:
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
            out.append({'key': f'{self.KIND}|{eid}', 'kind': self.KIND, 'entityId': eid, **self.ctx.who(eid), 'since': iso(rec['open']), 'perHour': round2(self.per_hour(rec, now)), 'normPerHour': round2(norm)})
        return out

    def keys(self) -> list[tuple[str, str]]:
        return [(self.KIND, eid) for eid in self.recs]

    def excluded(self, kind: str, eid: str) -> str | None:
        return 'air_handler' if self.ctx.air_handler(eid) else super().excluded(kind, eid)

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        rec = self.recs.get(eid)
        norm = self.norm(rec, eid) if rec else None
        if norm is None:
            return self.learning(eid, len(rec['pts']) if rec else 0, p_cfg()['cycle_learn_days'], now)
        return {'ready': {'norm': round2(norm), 'unit': self.UNIT, 'n': len(rec['pts'])}}

    def reset(self, eid: str, kind: str) -> None:
        self.recs.pop(eid, None)
