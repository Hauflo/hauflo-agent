"""Sensors that stopped changing while they should."""
from __future__ import annotations
import statistics
from typing import TYPE_CHECKING, Any
from homeassistant.core import State
from homeassistant.util import dt as dt_util
from ..derived import NOT_A_STATE, SECONDS_PER_HOUR, num
from ..notify.levels import labelled
from .common import gone_after, iso, learn_days, round2, stuck_classes, stuck_days, stuck_h, stuck_norm
from .detector import Detector
if TYPE_CHECKING:
    from .engine import Insights

class StuckSensor(Detector):
    """A temperature / humidity sensor that normally changes many times a day and hasn't for STUCK_H hours."""
    name = 'stuck'
    domains = ('sensor',)
    KINDS = ('stuck',)
    UNIT = 'per_day'
    DEV = ('perDay', 'hours')

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        self.recs: dict[str, dict[str, Any]] = {}

    def load(self, data: dict[str, Any]) -> None:
        for eid, r in (data.get('sensors') or {}).items():
            if not isinstance(r, dict) or num(r.get('last')) is None:
                continue
            self.recs[str(eid)] = {'value': str(r.get('value')), 'last': float(r['last']), 'day': str(r.get('day') or ''), 'count': int(num(r.get('count')) or 0), 'partial': True, 'days': [int(x) for x in r.get('days') or [] if num(x) is not None][-stuck_days():]}

    def dump(self) -> dict[str, Any]:
        return {'sensors': self.recs}

    def _watched(self, st: State) -> bool:
        return st.attributes.get('device_class') in stuck_classes() and (self.ctx.owns(st.entity_id) or labelled(self.ctx.hass, st.entity_id))

    def on_state(self, new: State, now: float) -> None:
        if new.state in NOT_A_STATE or num(new.state) is None or (not self._watched(new)):
            return
        rec = self.recs.get(new.entity_id)
        if rec is None:
            self._add(new, now)
        elif new.state != rec['value']:
            rec['value'], rec['last'] = (new.state, now)
            rec['count'] += 1
            self.ctx.dirty()

    @staticmethod
    def _day(now: float) -> str:
        return dt_util.as_local(dt_util.utc_from_timestamp(now)).date().isoformat()

    def _add(self, st: State, now: float) -> None:
        self.recs[st.entity_id] = {'value': st.state, 'last': min(st.last_changed.timestamp(), now), 'day': self._day(now), 'count': 0, 'partial': True, 'days': []}
        self.ctx.dirty()

    def tick(self, now: float) -> None:
        for st in self.ctx.hass.states.async_all('sensor'):
            if st.entity_id not in self.recs and st.state not in NOT_A_STATE and (num(st.state) is not None) and self._watched(st):
                self._add(st, now)
        today = self._day(now)
        for rec in self.recs.values():
            if rec['day'] != today:
                if not rec['partial']:
                    rec['days'] = [*rec['days'], rec['count']][-stuck_days():]
                rec.update(day=today, count=0, partial=False)
                self.ctx.dirty()
        if now - self.ctx.started > gone_after():
            for eid in [e for e in self.recs if self.ctx.hass.states.get(e) is None]:
                del self.recs[eid]
                self.ctx.dirty()

    def norm(self, eid: str) -> float | None:
        days = (self.recs.get(eid) or {}).get('days') or []
        return statistics.median(days) if len(days) >= learn_days() else None

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for eid, rec in self.recs.items():
            st = self.ctx.hass.states.get(eid)
            if st is None or st.state in NOT_A_STATE:
                continue
            per_day = self.norm(eid)
            hours = (now - rec['last']) / SECONDS_PER_HOUR
            if per_day is None or per_day < stuck_norm() or hours < stuck_h():
                continue
            out.append({'key': f'stuck|{eid}', 'kind': 'stuck', 'entityId': eid, **self.ctx.who(eid), 'since': iso(rec['last']), 'hours': round2(hours), 'perDay': round2(per_day)})
        return out

    def keys(self) -> list[tuple[str, str]]:
        return [('stuck', eid) for eid in self.recs]

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        days = (self.recs.get(eid) or {}).get('days') or []
        m = self.norm(eid)
        if m is None:
            return self.learning(eid, len(days), learn_days(), now)
        return {'ready': {'norm': round2(m), 'unit': self.UNIT, 'n': len(days)}}

    def reset(self, eid: str, kind: str) -> None:
        self.recs.pop(eid, None)
