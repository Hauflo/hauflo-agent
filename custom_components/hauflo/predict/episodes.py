"""Forecasts from counted episodes: flapping devices and night water use."""
from __future__ import annotations
from datetime import date, datetime, time as dtime
import statistics
from typing import TYPE_CHECKING, Any
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import State
from homeassistant.util import dt as dt_util
from ..derived import NOT_A_STATE, SECONDS_PER_HOUR, num
from ..insight.common import iso, round2
from ..insight.detector import Detector
if TYPE_CHECKING:
    from ..insight.engine import Insights
from .stats import day_of, gone_after, p_cfg

class Flapping(Detector):
    """Episodes «went unavailable» a day per device (else per entity): the last `flap_recent_days` against its own norm."""
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
                self.recs[str(unit)] = {'eid': r['eid'], 'day': int(num(r.get('day')) or 0), 'n': int(num(r.get('n')) or 0), 'days': [int(x) for x in r.get('days') or [] if num(x) is not None][-p_cfg()['flap_days']:], 'open': num(r.get('open'))}

    def dump(self) -> dict[str, Any]:
        return {'first': self.first_day, 'units': self.recs}

    def on_state(self, new: State, now: float) -> None:
        return

    def on_event(self, old: State | None, new: State, now: float) -> None:
        if new.state != STATE_UNAVAILABLE or old is None or old.state == STATE_UNAVAILABLE:
            return
        if now - self.ctx.started < p_cfg()['flap_grace_s'] or not self.ctx.visible(new.entity_id):
            return
        entry = self.ctx.entry(new.entity_id)
        self.pending.append((now, entry.device_id if entry and entry.device_id else new.entity_id, new.entity_id))

    def _count(self, now: float) -> None:
        window = p_cfg()['flap_mass_s']
        ready = [p for p in self.pending if now - p[0] > window]
        if not ready:
            return
        for t, unit, eid in ready:
            around = {u for tt, u, _e in self.pending if abs(tt - t) <= window}
            if len(around) >= p_cfg()['flap_mass']:
                continue
            rec = self.recs.get(unit)
            if rec is None:
                if len(self.recs) >= p_cfg()['flap_max']:
                    continue
                today = day_of(now)
                zeros = max(0, min(p_cfg()['flap_days'], today - (self.first_day or today)))
                rec = self.recs[unit] = {'eid': eid, 'day': today, 'n': 0, 'days': [0] * zeros, 'open': None, 'at': None}
            if rec.get('at') is not None and abs(t - rec['at']) <= window:
                continue
            rec['at'] = t
            rec['n'] += 1
        self.pending = [p for p in self.pending if now - p[0] <= window * 2]
        self.ctx.dirty()

    def rates(self, rec: dict[str, Any]) -> tuple[float, float] | None:
        """(episodes a day lately — today counts as one of the recent days, the norm before them) or None — learning."""
        n = p_cfg()['flap_recent_days']
        recent = [*rec['days'][-(n - 1):], rec['n']] if n > 1 else [rec['n']]
        old = rec['days'][:-(n - 1)] if n > 1 else rec['days']
        if len(old) < p_cfg()['flap_learn_days']:
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
                rec['days'] = [*rec['days'], rec['n'], *[0] * max(0, min(gap - 1, p_cfg()['flap_days']))][-p_cfg()['flap_days']:]
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
                if was is None and recent >= max(self.ctx.factor(f"{self.KIND}|{rec['eid']}", p_cfg()['flap_factor']) * norm, p_cfg()['flap_min_per_day']):
                    rec['open'] = now
                elif was is not None and recent < max(p_cfg()['flap_end_factor'] * norm, p_cfg()['flap_end_per_day']):
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
            out.append({'key': f'{self.KIND}|{eid}', 'kind': self.KIND, 'entityId': eid, **self.ctx.who(eid), 'since': iso(rec['open']), 'perDay': round2(got[0]), 'normPerDay': round2(got[1])})
        return out

    def keys(self) -> list[tuple[str, str]]:
        return [(self.KIND, eid) for eid in dict.fromkeys((r['eid'] for r in self.recs.values()))]

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        rec = next((r for r in self.recs.values() if r['eid'] == eid), None)
        got = self.rates(rec) if rec else None
        if got is None:
            old = max(0, len(rec['days']) - (p_cfg()['flap_recent_days'] - 1)) if rec else 0
            return self.learning(eid, old, p_cfg()['flap_learn_days'], now)
        return {'ready': {'norm': round2(got[1]), 'unit': self.UNIT, 'n': len(rec['days'])}}

    def reset(self, eid: str, kind: str) -> None:
        for unit in [u for u, r in self.recs.items() if r['eid'] == eid]:
            del self.recs[unit]

class NightWater(Detector):
    """A water meter's use between `night_from_h` and `night_to_h` (local) against its own night norm."""
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
            self.recs[str(eid)] = {'value': num(r.get('value')), 'night': num(r.get('night')), 'base': num(r.get('base')), 'nights': nights[-p_cfg()['night_keep']:], 'open': num(r.get('open'))}

    def dump(self) -> dict[str, Any]:
        return {'meters': self.recs}

    @staticmethod
    def litres(st: State) -> float | None:
        a = st.attributes
        unit = a.get('unit_of_measurement')
        if a.get('device_class') != 'water' or a.get('state_class') not in p_cfg()['water_state_classes'] or unit not in p_cfg()['water_units']:
            return None
        v = num(st.state)
        return None if v is None else v * p_cfg()['water_unit_l'][p_cfg()['water_units'].index(unit)]

    @staticmethod
    def night_of(now: float) -> int | None:
        """The night's number (the day it began) if `now` is inside the night window, else None."""
        local = dt_util.as_local(dt_util.utc_from_timestamp(now))
        h, a, b = (local.hour, p_cfg()['night_from_h'], p_cfg()['night_to_h'])
        inside = a <= h < b if a < b else h >= a or h < b
        if not inside:
            return None
        day = local.date().toordinal()
        return day - 1 if a > b and h < b else day

    def _end_of(self, night: int) -> float:
        a, b = (p_cfg()['night_from_h'], p_cfg()['night_to_h'])
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
            if rec['base'] is not None and rec['value'] is not None and (now - self._end_of(done) <= p_cfg()['night_close_grace_h'] * SECONDS_PER_HOUR):
                used = rec['value'] - rec['base']
                if used >= 0:
                    rec['nights'] = [*rec['nights'], [done, round(used, 3)]][-p_cfg()['night_keep']:]
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
        """(the recent nights' median use, the threshold) — None while learning."""
        n = p_cfg()['night_days']
        nights = rec['nights']
        recent, old = (nights[-n:], nights[:-n])
        if len(recent) < n or len(old) < p_cfg()['night_learn']:
            return None
        norm = statistics.median((v for _d, v in old))
        return (statistics.median((v for _d, v in recent)), max(norm * self.ctx.factor(f'{self.KIND}|{self._eid_of(rec)}', p_cfg()['night_factor']), norm + p_cfg()['night_min_l']))

    def _eid_of(self, rec: dict[str, Any]) -> str:
        return next((e for e, r in self.recs.items() if r is rec), '')

    def _assess(self, rec: dict[str, Any], now: float) -> None:
        got = self.check(rec)
        n = p_cfg()['night_days']
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
            if now - self.ctx.started > gone_after() and self.ctx.hass.states.get(eid) is None:
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
            old = rec['nights'][:-p_cfg()['night_days']]
            out.append({'key': f'{self.KIND}|{eid}', 'kind': self.KIND, 'entityId': eid, **self.ctx.who(eid), 'since': iso(rec['open']), 'nightL': round2(got[0]), 'normL': round2(statistics.median((v for _d, v in old))), 'nights': p_cfg()['night_days']})
        return out

    def keys(self) -> list[tuple[str, str]]:
        return [(self.KIND, eid) for eid in self.recs]

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        nights = (self.recs.get(eid) or {}).get('nights') or []
        old = nights[:-p_cfg()['night_days']]
        if len(nights) < p_cfg()['night_days'] or len(old) < p_cfg()['night_learn']:
            return self.learning(eid, max(0, len(nights) - p_cfg()['night_days']), p_cfg()['night_learn'], now)
        return {'ready': {'norm': round2(statistics.median((v for _d, v in old))), 'unit': self.UNIT, 'n': len(nights)}}

    def reset(self, eid: str, kind: str) -> None:
        self.recs.pop(eid, None)
