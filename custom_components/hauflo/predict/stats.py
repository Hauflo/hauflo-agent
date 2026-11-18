"""Robust trend statistics and the per-entity daily series the forecasts read."""
from __future__ import annotations
from datetime import timedelta
import functools
import statistics
from typing import TYPE_CHECKING, Any, ClassVar
from homeassistant.core import State
from homeassistant.util import dt as dt_util
from ..derived import NOT_A_STATE, num
from ..insight.common import iso, round2
from ..insight.detector import Detector
from ..tuning import tuning
if TYPE_CHECKING:
    from ..insight.engine import Insights
p_cfg = functools.cache(lambda: tuning()['predict'])
_i = functools.cache(lambda: tuning()['insight'])
DAY_S = timedelta(days=1).total_seconds()
WEEK = timedelta(weeks=1).days
trend_days = functools.cache(lambda: p_cfg()['trend_days'])
ring_max = functools.cache(lambda: p_cfg()['ring_max'])
noise_k = functools.cache(lambda: p_cfg()['noise_k'])
gone_after = functools.cache(lambda: _i()['gone_after_s'])
on_actions = functools.cache(lambda: set(_i()['heat_actions']) | set(_i()['cool_actions']))

def theil_sen(pts: list[tuple[float, float]]) -> tuple[float, float] | None:
    """The median of the pairwise slopes and its intercept (Sen 1968): one wild day doesn't tilt it. None — < 2 x."""
    slopes = [(y2 - y1) / (x2 - x1) for i, (x1, y1) in enumerate(pts) for x2, y2 in pts[i + 1:] if x2 != x1]
    if not slopes:
        return None
    m = statistics.median(slopes)
    return (m, statistics.median((y - m * x for x, y in pts)))

def spread(pts: list[tuple[float, float]], m: float, b: float) -> float:
    """The median absolute residual around the line — the series' own noise."""
    return statistics.median((abs(y - (m * x + b)) for x, y in pts))

def day_of(ts: float) -> int:
    """The local date as a day number."""
    return dt_util.as_local(dt_util.utc_from_timestamp(ts)).date().toordinal()

def day_x(ts: float) -> float:
    """The local moment as a fractional day number (the same axis as day_of)."""
    local = dt_util.as_local(dt_util.utc_from_timestamp(ts))
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local.date().toordinal() + (local - midnight).total_seconds() / DAY_S

def date_in(now: float, days: float) -> str:
    """The local date `days` from now, ISO (both languages read it)."""
    return dt_util.as_local(dt_util.utc_from_timestamp(now + days * DAY_S)).date().isoformat()

def forecast(pts: list[tuple[float, float]], now_x: float, limit: float, sign: int, min_change: float) -> dict[str, float] | None:
    """A steady trend toward `limit` (sign −1: falling to it, +1: rising to it): {level (the line's value now), slope
    (per
    day), days (to the limit, 0 if past it)}; None — no trend: flat, the other way, too small, or lost in its noise."""
    fit = theil_sen(pts)
    if fit is None:
        return None
    m, b = fit
    span = pts[-1][0] - pts[0][0]
    change = m * span * sign
    if m * sign <= 0 or change < min_change or spread(pts, m, b) * noise_k() > change:
        return None
    level = m * now_x + b
    return {'level': level, 'slope': m, 'days': max(0.0, (limit - level) / m)}

class Series(Detector):
    """Readings → one value a day (`agg`: max / min / median of the day), the last TREND_DAYS days kept; `assess` says
    whether a finding is open (its fields) or not, with its own end condition. Persisted: the days, today's running
    value, whether it is open."""
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
        """The day's value: max / min / median of its readings."""
        return self.agg

    def assess(self, eid: str, rec: dict[str, Any], now: float) -> dict[str, Any] | None:
        raise NotImplementedError

    def on_reading(self, rec: dict[str, Any], v: float) -> None:
        """Before a reading counts (a battery replaced resets)."""

    def load(self, data: dict[str, Any]) -> None:
        for eid, r in (data.get('series') or {}).items():
            if not isinstance(r, dict) or not isinstance(r.get('kind'), str):
                continue
            pts = [(int(p[0]), float(p[1])) for p in r.get('pts') or [] if isinstance(p, list) and len(p) == 2 and (num(p[0]) is not None) and (num(p[1]) is not None)]
            ring = [float(x) for x in r.get('ring') or [] if num(x) is not None][-ring_max():]
            self.recs[str(eid)] = {'kind': r['kind'], 'pts': pts[-trend_days():], 'day': int(num(r.get('day')) or 0), 'today': num(r.get('today')), 'ring': ring, 'last': num(r.get('last')), 'open': num(r.get('open')), 'since': num(r.get('since'))}

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
            if len(rec['ring']) >= ring_max():
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
            rec['pts'] = [*rec['pts'], (rec['day'], round(held, 3))][-trend_days():]
        rec.update(day=today, today=None, ring=[])
        self.ctx.dirty()

    def tick(self, now: float) -> None:
        for st in self.ctx.hass.states.async_all(self.domains):
            if st.entity_id not in self.recs and self._watch(st):
                self.on_state(st, now)
        for eid, rec in list(self.recs.items()):
            self._roll(rec, now)
            if now - self.ctx.started > gone_after() and self.ctx.hass.states.get(eid) is None:
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
            out.append({'key': f'{kind}|{eid}', 'kind': kind, 'entityId': eid, **self.ctx.who(eid), 'since': iso(rec['open']), **found})
        return out
    kinds: ClassVar[dict[str, str]] = {}
    units: ClassVar[dict[str, str]] = {}
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
        return {'ready': {'norm': round2(rec['last']) if rec['last'] is not None else None, 'unit': self.units.get(rec['kind'], ''), 'n': len(rec['pts'])}}

    def reset(self, eid: str, kind: str) -> None:
        self.recs.pop(eid, None)
