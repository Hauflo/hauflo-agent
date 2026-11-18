"""Forecasts of device health: battery, link quality and the HA host."""
from __future__ import annotations
from typing import Any, ClassVar
from homeassistant.const import PERCENTAGE, STATE_UNKNOWN
from homeassistant.core import State
from homeassistant.util import dt as dt_util
from ..derived import NOT_A_STATE, num
from ..insight.common import round2
from ..insight.detector import CanonAttr
from .stats import DAY_S, WEEK, Series, date_in, day_of, day_x, forecast, p_cfg

class BatteryForecast(Series):
    name = 'battery'
    domains = ('sensor',)
    agg = 'max'
    kinds: ClassVar[dict[str, str]] = {'battery': 'battery_forecast'}
    units: ClassVar[dict[str, str]] = {'battery': 'pct'}
    KINDS = ('battery_forecast',)
    LOW = CanonAttr(lambda: p_cfg()['battery_low_pct'])
    WARN = CanonAttr(lambda: p_cfg()['battery_warn_days'])

    def need(self, rec: dict[str, Any]) -> int:
        return p_cfg()['battery_min_days']

    def excluded(self, kind: str, eid: str) -> str | None:
        rec = self.recs.get(eid)
        if rec and len(rec['pts']) >= p_cfg()['battery_min_days'] and (len({v for _, v in rec['pts']}) < p_cfg()['battery_min_distinct']):
            return 'coarse_battery'
        return super().excluded(kind, eid)

    def kind_of(self, st: State) -> str | None:
        a = st.attributes
        return 'battery' if a.get('device_class') == 'battery' and a.get('unit_of_measurement') == PERCENTAGE else None

    def on_reading(self, rec: dict[str, Any], v: float) -> None:
        ref = [x for x in (rec['today'], rec['pts'][-1][1] if rec['pts'] else None) if x is not None]
        if ref and v - max(ref) >= p_cfg()['battery_jump_pct']:
            rec.update(pts=[], today=None, open=None, found=None, since=self.ctx.clock())

    def assess(self, eid: str, rec: dict[str, Any], now: float) -> dict[str, Any] | None:
        last = rec['last']
        pts = rec['pts']
        if last is None:
            return None
        fc = None
        if len(pts) >= p_cfg()['battery_min_days'] and pts[-1][0] - pts[0][0] >= p_cfg()['battery_min_span_days'] and (len({v for _, v in pts}) >= p_cfg()['battery_min_distinct']):
            fc = forecast(pts, day_x(now), self.LOW, -1, p_cfg()['battery_min_drop_pct'])
            if fc is not None and -fc['slope'] < p_cfg()['battery_min_slope_pct']:
                fc = None
        if rec['open'] is None:
            if fc is None or last <= self.LOW or fc['days'] >= self.WARN:
                return None
        elif last > self.LOW and (fc is None or fc['days'] >= self.WARN * p_cfg()['battery_end_factor']):
            return None
        days = 0.0 if last <= self.LOW or fc is None else min(fc['days'], (last - self.LOW) / -fc['slope'])
        return {'daysLeft': round(days), 'date': date_in(now, days), 'value': round2(last), 'limit': self.LOW, **({'perWeek': round2(-fc['slope'] * WEEK)} if fc else {})}

class LinkQuality(Series):
    name = 'link'
    domains = ('sensor',)
    agg = 'median'
    kinds: ClassVar[dict[str, str]] = {'lqi': 'link_quality', 'dbm': 'link_quality'}
    units: ClassVar[dict[str, str]] = {'lqi': 'lqi', 'dbm': 'dbm'}
    KINDS = ('link_quality',)

    def need(self, rec: dict[str, Any]) -> int:
        return p_cfg()['link_min_days']

    def kind_of(self, st: State) -> str | None:
        a, eid = (st.attributes, st.entity_id)
        unit = a.get('unit_of_measurement')
        if unit in p_cfg()['link_lqi_units'] or any((eid.endswith(s) for s in p_cfg()['link_lqi_suffixes'])):
            return 'lqi'
        if a.get('device_class') in p_cfg()['link_dbm_classes'] or unit in p_cfg()['link_dbm_units']:
            return 'dbm'
        return None

    def assess(self, eid: str, rec: dict[str, Any], now: float) -> dict[str, Any] | None:
        pts = rec['pts'][-p_cfg()['link_days']:]
        lqi = rec['kind'] == 'lqi'
        warn = p_cfg()['lqi_warn'] if lqi else -p_cfg()['rssi_warn_dbm']
        fc = forecast(pts, day_x(now), warn, -1, p_cfg()['lqi_min_drop'] if lqi else p_cfg()['rssi_min_drop_db']) if len(pts) >= p_cfg()['link_min_days'] else None
        bound = p_cfg()['link_warn_days'] * (p_cfg()['link_end_factor'] if rec['open'] is not None else 1)
        if fc is None or fc['days'] >= bound:
            return None
        return {'daysLeft': round(fc['days']), 'date': date_in(now, fc['days']), 'value': round2(fc['level']), 'limit': warn, 'perWeek': round2(fc['slope'] * WEEK)}

class HaHost(Series):
    """HA's own disk (System Monitor / Supervisor sensors) and the age of its last automatic backup."""
    name = 'host'
    domains = ('sensor',)
    agg = 'max'
    kinds: ClassVar[dict[str, str]] = {'use': 'disk_full', 'free': 'disk_full', 'backup': 'backup_old'}
    units: ClassVar[dict[str, str]] = {'use': 'pct', 'free': 'gb', 'backup': 'days'}
    KINDS = ('disk_full', 'backup_old')

    def need(self, rec: dict[str, Any]) -> int:
        return 0 if rec['kind'] == 'backup' else p_cfg()['disk_min_days']

    def _key(self, st: State) -> tuple[str | None, str | None]:
        entry = self.ctx.entry(st.entity_id)
        return (entry.platform, entry.translation_key) if entry else (None, None)

    def kind_of(self, st: State) -> str | None:
        platform, key = self._key(st)
        if platform in p_cfg()['backup_platforms'] and key in p_cfg()['backup_keys']:
            return 'backup'
        if platform not in p_cfg()['host_platforms']:
            return None
        if key in p_cfg()['disk_use_keys']:
            return 'use'
        if key in p_cfg()['disk_free_keys'] and st.attributes.get('unit_of_measurement') in p_cfg()['disk_units']:
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
        return v * p_cfg()['disk_unit_gb'][p_cfg()['disk_units'].index(st.attributes.get('unit_of_measurement'))]

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
            if st.state == STATE_UNKNOWN and now - (rec['since'] or now) >= p_cfg()['backup_never_days'] * DAY_S:
                return {'limit': p_cfg()['backup_max_days']}
            return None
        if kind == 'backup':
            age = (now - last) / DAY_S
            if age <= p_cfg()['backup_max_days']:
                return None
            return {'days': int(age), 'date': dt_util.as_local(dt_util.utc_from_timestamp(last)).date().isoformat(), 'limit': p_cfg()['backup_max_days']}
        if kind == 'use':
            limit, sign, change, past = (p_cfg()['disk_full_pct'], 1, p_cfg()['disk_min_rise_pct'], last >= p_cfg()['disk_full_pct'])
        else:
            limit, sign, change, past = (p_cfg()['disk_min_free_gb'], -1, p_cfg()['disk_min_fall_gb'], last <= p_cfg()['disk_min_free_gb'])
        pts = rec['pts']
        fc = forecast(pts, day_x(now), limit, sign, change) if len(pts) >= p_cfg()['disk_min_days'] else None
        bound = p_cfg()['disk_warn_days'] * (p_cfg()['disk_end_factor'] if rec['open'] is not None else 1)
        if not past and (fc is None or fc['days'] >= bound):
            return None
        days = 0.0 if past or fc is None else fc['days']
        return {'daysLeft': round(days), 'date': date_in(now, days), 'value': round2(last), 'limit': limit, **({'perWeek': round2(fc['slope'] * WEEK)} if fc else {})}
