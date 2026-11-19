"""Rule times: schedules, windows and how long a value has held."""
from __future__ import annotations
from collections.abc import Callable
import datetime as dt
import logging
from typing import Any
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later, async_track_point_in_utc_time
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util
from .. import derived as dv, window as win
from ..derived import Invalid, num
from .conditions import is_match, outputs
from .const import DAYS, HHMM, IS, TODO
from .spec import C
_LOGGER = logging.getLogger(__name__)

def clean_at(raw: Any) -> dict[str, Any]:
    """«в 07:00, 22:00 · на закате + 15 мин · по будням»: moments (canon «schedule»), weekdays."""
    s = C('schedule')
    bad = Invalid('bad_schedule', 'rules_bad_schedule', n=int(s['moments']))
    if not isinstance(raw, dict):
        raise bad
    times, sun = (raw.get('times') or [], raw.get('sun') or [])
    days = raw['days'] if raw.get('days') is not None else list(DAYS)
    if not isinstance(times, list) or not isinstance(sun, list) or (not isinstance(days, list)):
        raise bad
    if not 1 <= len(times) + len(sun) <= s['moments'] or any((not (isinstance(t, str) and HHMM.match(t)) for t in times)):
        raise bad
    if not days or any((d not in DAYS for d in days)):
        raise bad
    lo, hi = s['sun_offset_min']
    out_sun: list[dict[str, Any]] = []
    for e in sun:
        if not isinstance(e, dict) or e.get('event') not in s['events']:
            raise bad
        off = num(e.get('offset_min', 0))
        if off is None or not lo <= off <= hi:
            raise Invalid('bad_schedule', 'rules_bad_sun_offset', lo=int(lo), hi=int(hi))
        item = {'event': e['event'], 'offset_min': round(off)}
        if item not in out_sun:
            out_sun.append(item)
    return {'times': sorted(set(times)), 'sun': out_sun, 'days': [d for d in DAYS if d in days]}

def next_fire(at: dict[str, Any], after: dt.datetime, sun: Callable[[str, dt.date], dt.datetime | None]) -> dt.datetime | None:
    """The first moment of `at` strictly after `after` (aware, in the house's time zone) within the canon's horizon; None: none there (no sunset in a polar summer)."""
    found: list[dt.datetime] = []
    for k in range(int(C('schedule', 'horizon_days')) + 1):
        day = after.date() + dt.timedelta(days=k)
        if DAYS[day.weekday()] in at['days']:
            found += _day_moments(at, day, after.tzinfo, sun)
    later = [m for m in found if dt_util.as_utc(m) > dt_util.as_utc(after)]
    return min(later, key=dt_util.as_utc) if later else None

def _day_moments(at: dict[str, Any], day: dt.date, tz: Any, sun: Callable[[str, dt.date], dt.datetime | None]) -> list[dt.datetime]:
    out = [dt.datetime.combine(day, dt.time.fromisoformat(t), tzinfo=tz) for t in at['times']]
    for e in at.get('sun') or []:
        if (when := sun(e['event'], day)) is not None:
            out.append(when.astimezone(tz) + dt.timedelta(minutes=e['offset_min']))
    return out

def next_off_day(at: dict[str, Any], after: dt.datetime, sun: Callable[[str, dt.date], dt.datetime | None]) -> dt.datetime | None:
    """The first moment of the next day the schedule does not run on (a weekday not among its days), strictly after `after`: there the log says once «пропущен: не тот день недели» (as HA's own automation's trace shows a weekday condition not met)."""
    for k in range(int(C('schedule', 'horizon_days')) + 1):
        day = after.date() + dt.timedelta(days=k)
        if DAYS[day.weekday()] in at['days'] or not (ms := _day_moments(at, day, after.tzinfo, sun)):
            continue
        first = min(ms, key=dt_util.as_utc)
        if dt_util.as_utc(first) > dt_util.as_utc(after):
            return first
    return None

def past_on(rule: dict[str, Any], v: float) -> bool:
    w = rule['when']
    if w == 'below':
        return v < rule['x']
    if w == 'above':
        return v > rule['x']
    if w == 'outside':
        return v < rule['lo'] or v > rule['hi']
    if w == 'between':
        return rule['lo'] <= v <= rule['hi']
    if w == IS:
        return v is not None and is_match(v, rule[IS], rule['input']['tol']) != rule.get('not', False)
    return bool(v) == (w == 'on')

def past_off(rule: dict[str, Any], v: float) -> bool:
    w, y = (rule['when'], rule['y'])
    if w == 'below':
        return v >= rule['x'] + y
    if w == 'above':
        return v <= rule['x'] - y
    if w == 'outside':
        return rule['lo'] + y <= v <= rule['hi'] - y
    if w == 'between':
        return v < rule['lo'] - y or v > rule['hi'] + y
    return not past_on(rule, v)

def window_off(rule: dict[str, Any], v: float) -> bool:
    """Back to normal for one period of a window rule: strictly past X ∓ Y (a period at the threshold itself breaks the run — as the season's suggestion always did)."""
    w, y = (rule['when'], rule['y'])
    if w == 'below':
        return v > rule['x'] + y
    if w == 'above':
        return v < rule['x'] - y
    if w == 'outside':
        return rule['lo'] + y < v < rule['hi'] - y
    return v < rule['lo'] - y or v > rule['hi'] + y

def in_row(rule: dict[str, Any], vals: list[float | None], test: Callable[[dict[str, Any], float], bool]) -> int:
    """How many of the latest periods in a row pass `test` (a period without data ends the run)."""
    n = 0
    for v in reversed(vals):
        if v is None or not test(rule, v):
            break
        n += 1
    return n

class RulesScheduleMixin:
    """Runs scheduled and windowed rules."""

    def _window_input(self, rule: dict[str, Any]) -> tuple[str, str | None]:
        inp = rule['input']
        if inp.get('outdoor') and (found := dv.outdoor(self.hass, self.owns)) is not None and (found['entity_id'] != inp['entity_id']):
            try:
                win.source_of(self.hass, found['entity_id'], found['attribute'], rule['window']['stat'])
                return (found['entity_id'], found['attribute'])
            except Invalid:
                pass
        return (inp['entity_id'], inp.get('attribute'))

    async def window_refresh(self, rid: str, why: str) -> None:
        """The last periods' values from the recorder (in its executor), then the decision; again when the next closes."""
        rule = self.rules.get(rid)
        if rule is None or not rule.get('window'):
            return
        eid, attr = self._window_input(rule)
        try:
            vals, starts, bad = await win.values(self.hass, eid, attr, {**rule['window'], 'source': None if eid != rule['input']['entity_id'] else rule['window']['source']})
        except Exception as err:
            _LOGGER.warning('Hauflo rule %s: no statistics: %s', rid, err, exc_info=True)
            vals, starts, bad = ([], [], 'no_statistics')
        self.win[rid] = {'values': vals, 'starts': starts, 'why': bad, 'entity_id': eid, 'at': dv.now()}
        await self.evaluate(rid, why)
        self._window_schedule(rid)

    @callback
    def _window_schedule(self, rid: str) -> None:
        if (u := self._win_timers.pop(rid, None)) is not None:
            u()
        rule = self.rules.get(rid)
        if rule is None or not rule.get('window'):
            return
        due = dt_util.as_utc(win.next_start(dt_util.now(), rule['window']['period'])) + dt.timedelta(seconds=C('window', 'compile_delay_s'))

        async def _go(_now: Any) -> None:
            self._win_timers.pop(rid, None)
            await self.window_refresh(rid, 'period')
        self._win_timers[rid] = async_track_point_in_utc_time(self.hass, _go, due)

    async def _evaluate_window(self, rule: dict[str, Any], why: str) -> None:
        rid, w = (rule['id'], rule['window'])
        st = self.st(rid)
        vals = (self.win.get(rid) or {}).get('values') or []
        st['value'] = vals[-1] if vals else None
        st['periods'] = in_row(rule, vals, past_on)
        ok, cond = self.conditions(rule)
        st['cond'] = cond
        need = w['n_back'] if st['active'] else w['n']
        last = vals[-need:]
        st['no_data'] = len(last) < need or any((v is None for v in last))
        if not ok:
            if st['active']:
                await self._go(rule, False, st['value'], 'only_if')
        elif st['no_data']:
            pass
        elif not st['active'] and all((past_on(rule, v) for v in last)):
            await self._go(rule, True, st['value'], 'window')
        elif st['active'] and all((window_off(rule, v) for v in last)):
            await self._go(rule, False, st['value'], 'window')
        elif not st['active'] and st['confirmed'] is None and (len(vals) >= w['n_back']) and all((v is not None and window_off(rule, v) for v in vals[-w['n_back']:])):
            st['confirmed'] = 'off'
            self._log(rid, 'normal', value=st['value'], why='window')
            if outputs(rule)['suggest_back']:
                await self._suggest(rule, False)
        self._refresh(rid)
        self._save_soon()

    def _sun(self, event: str, day: dt.date) -> dt.datetime | None:
        return get_astral_event_date(self.hass, event, day)

    def next_at(self, rule: dict[str, Any], after: float, *, off: bool=False) -> float | None:
        """The next moment it runs (off: the first moment of the next day it does not run on — said in the log)."""
        nxt = (next_off_day if off else next_fire)(rule['at'], dt_util.as_local(dt_util.utc_from_timestamp(after)), self._sun)
        return nxt.timestamp() if nxt is not None else None

    async def _sched_eval(self, rule: dict[str, Any], why: str) -> None:
        rid, st, t = (rule['id'], self.st(rule['id']), dv.now())
        due, changed = (st.get('next_at'), why != 'tick')
        if due is not None and t >= due:
            changed = True
            st['next_at'] = None
            if t - due > C('schedule', 'catch_up_s'):
                st['last_at'], st['last_result'] = (due, 'missed')
                self._log(rid, 'missed', planned=due, why=why)
            elif st['hold_until']:
                st['last_at'], st['last_result'] = (due, 'skipped')
                self._log(rid, 'sched_skip', planned=due, result=st['hold_why'] or 'hold')
            else:
                await self._sched_fire(rule, due)
            self._save_soon()
        if st.get('next_at') is None:
            st['next_at'] = self.next_at(rule, max(t, due or t))
            changed = True
            self._save_soon()
        off = st.get('off_at')
        if off is not None and t >= off:
            changed, st['off_at'] = (True, None)
            if t - off <= C('schedule', 'catch_up_s'):
                self._log(rid, 'sched_skip', planned=off, result='weekday')
        if st.get('off_at') is None and (nxt_off := self.next_at(rule, max(t, off or t), off=True)) is not None:
            st['off_at'], changed = (nxt_off, True)
            self._save_soon()
        if not changed:
            return
        if (wake := min((x for x in (st['next_at'], st.get('off_at')) if x is not None), default=None)) is not None:
            self._wake(rid, wake - t)
        self._refresh(rid)

    async def _sched_fire(self, rule: dict[str, Any], planned: float) -> None:
        rid, st = (rule['id'], self.st(rule['id']))
        ok, cond = self.conditions(rule)
        st['last_at'] = planned
        if not ok:
            st['last_result'] = 'skipped'
            self._log(rid, 'sched_skip', planned=planned, result=cond)
            return
        st['last_result'] = 'fired'
        self._log(rid, 'fired', planned=planned)
        self._logbook(rule, self.say('rules_logbook_fired', self.ru, time=self._hhmm(planned)))
        await self._apply(rule, back=False, why='schedule')

    def _when_text(self, ts: float | None) -> str:
        if not ts:
            return '—'
        at = dt_util.as_local(dt_util.utc_from_timestamp(ts))
        today = dt_util.as_local(dt_util.utc_from_timestamp(dv.now())).date()
        return at.strftime('%H:%M') if at.date() == today else at.strftime('%d.%m %H:%M')

    async def _pulses_due(self, *, start: bool=False) -> None:
        t = dv.now()
        for eid, p in list(self.pulses.items()):
            if p['until'] <= t:
                await self._pulse_end(eid)
            elif start:
                self._pulse_arm(eid)

    @callback
    def _wake(self, rid: str, delay: float) -> None:
        if (u := self._timers.pop(rid, None)) is not None:
            u()

        async def _go(_now: Any) -> None:
            self._timers.pop(rid, None)
            if rid == TODO:
                await self._run_todo()
            else:
                await self.evaluate(rid, 'timer')
        self._timers[rid] = async_call_later(self.hass, max(C('engine', 'min_wake_s'), delay), _go)
