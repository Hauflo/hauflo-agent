from __future__ import annotations
import logging
import re
import statistics
import time
from datetime import timedelta
from typing import Any, Awaitable, Callable
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.const import EVENT_STATE_CHANGED, STATE_ON
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from .derived import NOT_A_STATE, SECONDS_PER_HOUR, num, outdoor
from .notify import label, labelled
from .signals import ENTITY_ID, policy as policy_of
from .ownership import entity_room
from .storage import HaufloStore
from .tuning import TUNING
_LOGGER = logging.getLogger(__name__)
STORE_KEY = 'hauflo.insights'
META = '_meta'
_T = TUNING['insight']
TICK = _T['tick_s']
SAVE_DELAY = _T['save_delay_s']
LIST_MAX = _T['list_max']
GONE_AFTER = _T['gone_after_s']
GAP = _T['gap_c']
REACH = _T['reach_c']
LEARN_GAP = _T['learn_gap_c']
SAMPLES = _T['samples']
RATE_MIN_H = _T['rate_min_h']
LEARN_MIN_H = _T['learn_min_h']
LEARN_MIN = _T['learn_min']
MIN_H = _T['min_h']
FACTOR = _T['factor']
FLAT_C = _T['flat_c']
FLAT_H = _T['flat_h']
MODES = {'heat': set(_T['heat_modes']), 'cool': set(_T['cool_modes'])}
ACTIONS = {'heat': set(_T['heat_actions']), 'cool': set(_T['cool_actions'])}
OPENING = set(_T['opening_classes'])
STUCK_CLASSES = set(_T['stuck_classes'])
STUCK_H = _T['stuck_h']
STUCK_DAYS = _T['stuck_days']
LEARN_DAYS = _T['learn_days']
STUCK_NORM = _T['stuck_norm_changes']
AIR_HANDLER_WORDS = re.compile(_T['air_handler_words'], re.IGNORECASE)
AIR_HANDLER_RATE = _T['air_handler_rate_c_h']
OBSERVE_H = _T['observe_min_h']
OBS_EPS = _T['observe_min_eps']
GAP_BAND = _T['gap_band_c']
GAP_LIKE_MIN = _T['gap_like_min']
OFFSET_MAX = _T['states_offset_max']
OFFSET_RE = re.compile('[0-9]{1,%d}' % len(str(OFFSET_MAX)))
LIMIT_MAX = _T['states_limit_max']
CHOICES_MAX = _T['choices_max']
LOCAL_MAX = _T['local_max']
LIMIT_RE = re.compile('[0-9]{1,%d}' % len(str(LIMIT_MAX)))
EPISODES_KEEP = _T['episodes_keep']
MAX_PER_DAY = _T['insight_max_per_day']
DIAG_VISIBLE = TUNING['signals']['diag']['default']
DIAG_ALL = 'all'
INPUTS_MAX = _T['inputs_max']
INPUT_GAS_MAX = _T['input_addresses_max']
SECTIONS = _T['sections']
SECTION_ORDER = _T['section_order']
FACTOR_MIN, FACTOR_MAX = (_T['factor_min'], _T['factor_max'])
FEATURE = 'insights'
OBSERVE_FEATURE = 'insight_switch'
DAY_S = timedelta(days=1).total_seconds()
USED, DROPPED = ('used', 'dropped:')
ACTIONS_IN = ('normal', 'follow', 'reset')
HEAT, COOL = ('heat', 'cool')
KIND = {HEAT: 'no_heat', COOL: 'no_cool'}
DIR_OF = {v: k for k, v in KIND.items()}
SIGN = {HEAT: 1, COOL: -1}
_KEEP = ('dir', 'start', 'gap', 'target', 'mode', 'last_on', 'alerted', 'c0')

def _iso(ts: float) -> str:
    return dt_util.utc_from_timestamp(ts).isoformat()

def _round(v: float) -> float:
    return round(v, 2)

def _sane_line(line: dict[str, Any]) -> dict[str, Any]:
    hours = num(line.get('hours'))
    if 'rate' in line and (hours is None or hours < RATE_MIN_H):
        return {k: v for k, v in line.items() if k != 'rate'}
    return line

def _rates(v: Any) -> list[list[float | None]]:
    out: list[list[float | None]] = []
    for x in v if isinstance(v, list) else []:
        r, g = (x[0], x[1]) if isinstance(x, list) and len(x) == 2 else (x, None)
        if num(r) is not None and float(r) > 0:
            out.append([float(r), float(g) if num(g) is not None else None])
    return out

def _median_rate(rates: list[list[float | None]], gap: float | None) -> float | None:
    if gap is None:
        like = [r for r, _g in rates]
    else:
        like = [r for r, g in rates if g is not None and abs(g - gap) <= GAP_BAND]
        if len(like) < GAP_LIKE_MIN:
            return None
    if not like:
        return None
    m = statistics.median(like)
    return m if m > 0 else None

class Detector:
    name = ''
    domains: tuple[str, ...] | None = ()
    KINDS: tuple[str, ...] = ()
    UNIT = ''
    DEV: tuple[str, str] = ('', '')
    diagnostic = False

    def __init__(self, ctx: Insights) -> None:
        self.ctx = ctx

    def load(self, data: dict[str, Any]) -> None:
        raise NotImplementedError

    def dump(self) -> dict[str, Any]:
        raise NotImplementedError

    def on_state(self, new: State, now: float) -> None:
        raise NotImplementedError

    def on_event(self, old: State | None, new: State, now: float) -> None:
        self.on_state(new, now)

    def on_learned(self, eid: str, direction: str, rate: float, now: float, gap: float | None=None) -> None:
        pass

    def tick(self, now: float) -> None:
        raise NotImplementedError

    def warm(self, now: float) -> None:
        pass

    def items(self, now: float) -> list[dict[str, Any]]:
        raise NotImplementedError

    def keys(self) -> list[tuple[str, str]]:
        return []

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        return {'ready': {'norm': None, 'unit': self.UNIT, 'n': 0}}

    def excluded(self, kind: str, eid: str) -> str | None:
        return None if self.ctx.visible(eid, self.diagnostic) else 'hidden'

    def episodes(self, eid: str) -> list[dict[str, Any]]:
        return []

    def extra(self, kind: str, eid: str) -> dict[str, Any]:
        return {}

    def inputs(self, kind: str, eid: str) -> list[dict[str, Any]]:
        return [{'role': 'self', 'entityId': eid}]

    def reset(self, eid: str, kind: str) -> None:
        pass

    def learning(self, eid: str, have: int, need: int, now: float) -> dict[str, Any]:
        return {'learning': {'have': have, 'need': need, 'hours': _round(self.ctx.hours_seen(self.name, eid, now)), 'needH': OBSERVE_H}}

    def state(self, eid: str, now: float, kind: str | None=None, open_items: dict[str, dict[str, Any]] | None=None) -> dict[str, Any]:
        kind = kind or next((k for k, e in self.keys() if e == eid), self.KINDS[0] if self.KINDS else '')
        key = f'{kind}|{eid}'
        if (why := self.excluded(kind, eid)) is not None:
            return {'excluded': {'reason': why}}
        f = self.ctx.finding(key)
        if f and f.get('normal'):
            return {'normal': {'by': f.get('by'), 'at': f.get('at')}}
        opened = open_items if open_items is not None else {i['key']: i for i in self.items(now)}
        if (item := opened.get(key)) is not None:
            return {'deviation': {'since': item['since'], 'was': item.get(self.DEV[0]), 'now': item.get(self.DEV[1])}}
        return self.progress(kind, eid, now)

class ClimateEffect(Detector):
    name = 'climate'
    domains = ('climate',)
    KINDS = (KIND[HEAT], KIND[COOL])
    UNIT = 'c_per_h'
    DEV = ('expectedH', 'hours')

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        self.norms: dict[str, dict[str, list[list[float | None]]]] = {}
        self.eps: dict[str, dict[str, Any]] = {}
        self.ring: dict[str, list[dict[str, Any]]] = {}

    def load(self, data: dict[str, Any]) -> None:
        for eid, by in (data.get('norms') or {}).items():
            if isinstance(by, dict):
                got = {d: [x for x in _rates(by.get(d)) if x[1] is None or x[1] / x[0] >= LEARN_MIN_H][-SAMPLES:] for d in (HEAT, COOL)}
                self.norms[str(eid)] = {d: v for d, v in got.items() if v}
        for eid, ring in (data.get('ring') or {}).items():
            if isinstance(ring, list):
                self.ring[str(eid)] = [_sane_line(e) for e in ring if isinstance(e, dict)][-EPISODES_KEEP:]
        for eid, ep in (data.get('episodes') or {}).items():
            if not isinstance(ep, dict) or ep.get('dir') not in (HEAT, COOL) or num(ep.get('start')) is None or (num(ep.get('gap')) is None):
                continue
            last_on = num(ep.get('last_on'))
            alerted = num(ep.get('alerted'))
            self.eps[str(eid)] = {'dir': ep['dir'], 'start': float(ep['start']), 'gap': float(ep['gap']), 'target': num(ep.get('target')), 'mode': str(ep.get('mode') or ''), 'last_on': last_on if last_on is not None else float(ep['start']), 'alerted': alerted, 'c0': num(ep.get('c0')), 'resumed': True, 'samples': []}

    def dump(self) -> dict[str, Any]:
        return {'norms': self.norms, 'episodes': {eid: {k: ep.get(k) for k in _KEEP} for eid, ep in self.eps.items()}, 'ring': self.ring}

    def _season_ok(self, d: str) -> bool:
        season = self.ctx.season_now()
        return season not in (HEAT, COOL) or season == d

    def _want(self, st: State, cur: float, tgt: float) -> str | None:
        action = st.attributes.get('hvac_action')
        for d in (HEAT, COOL):
            if st.state not in MODES[d] or not self._season_ok(d):
                continue
            if (tgt - cur) * SIGN[d] < GAP:
                continue
            if action is not None and action not in ACTIONS[d]:
                continue
            return d
        return None

    def on_state(self, new: State, now: float) -> None:
        self._eval(new, now)

    def tick(self, now: float) -> None:
        for st in self.ctx.hass.states.async_all('climate'):
            self._eval(st, now)
        if now - self.ctx.started > GONE_AFTER:
            for eid in [e for e in self.eps if self.ctx.hass.states.get(e) is None]:
                self._drop(eid, now, 'gone')
            for eid in [e for e in self.ring if e not in self.eps and self.ctx.hass.states.get(e) is None]:
                del self.ring[eid]

    def _eval(self, st: State, now: float) -> None:
        eid = st.entity_id
        if st.state in NOT_A_STATE:
            return
        cur, tgt = (num(st.attributes.get('current_temperature')), num(st.attributes.get('temperature')))
        if cur is None or tgt is None:
            self._drop(eid, now, 'no_temp')
            return
        ep = self.eps.get(eid)
        if ep is not None:
            d = ep['dir']
            if ep['target'] != tgt:
                self._drop(eid, now, 'target')
            elif ep['mode'] != st.state:
                self._drop(eid, now, 'mode')
            elif not self._season_ok(d):
                self._drop(eid, now, 'season')
            elif (cur - tgt) * SIGN[d] >= -REACH:
                self._finish(eid, now, cur)
            elif self.ctx.opening_open(eid):
                self._drop(eid, now, 'window')
            else:
                self._observe(st, ep, cur, now)
                return
        d = self._want(st, cur, tgt)
        if d is not None and (not self.ctx.opening_open(eid)):
            self.eps[eid] = {'dir': d, 'start': now, 'gap': abs(tgt - cur), 'target': tgt, 'mode': st.state, 'last_on': now, 'alerted': None, 'c0': cur, 'resumed': False, 'samples': [(now, cur)]}
            self.ctx.dirty()

    def _observe(self, st: State, ep: dict[str, Any], cur: float, now: float) -> None:
        d = ep['dir']
        action = st.attributes.get('hvac_action')
        if action is None or action in ACTIONS[d]:
            ep['last_on'] = now
        samples: list[tuple[float, float]] = ep['samples']
        if not samples or samples[-1][1] != cur:
            samples.append((now, cur))
        edge = now - FLAT_H * SECONDS_PER_HOUR
        while len(samples) > 1 and samples[1][0] <= edge:
            samples.pop(0)
        if ep['alerted'] is not None:
            return
        rate = self.norm(st.entity_id, d, ep['gap'])
        if rate is None:
            return
        expected = ep['gap'] / rate
        hours = (now - ep['start']) / SECONDS_PER_HOUR
        if hours <= max(MIN_H, self.ctx.factor(f'{KIND[d]}|{st.entity_id}', FACTOR) * expected):
            return
        change = self._change(ep, cur, now)
        if change is None or change * SIGN[d] >= FLAT_C:
            return
        if now - ep['last_on'] > FLAT_H * SECONDS_PER_HOUR:
            return
        if self.ctx.air_handler(st.entity_id):
            return
        ep['alerted'] = now
        self.ctx.dirty()

    def _change(self, ep: dict[str, Any], cur: float, now: float) -> float | None:
        samples = ep['samples']
        edge = now - FLAT_H * SECONDS_PER_HOUR
        if not samples or samples[0][0] > edge:
            return None
        anchor = [v for t, v in samples if t <= edge][-1]
        return cur - anchor

    def _log(self, eid: str, ep: dict[str, Any], now: float, result: str, cur: float | None=None) -> None:
        hours = (now - ep['start']) / SECONDS_PER_HOUR
        outdoor_now = self.ctx.outdoor_c()
        line = {'start': _iso(ep['start']), 'dir': ep['dir'], 'gap': _round(ep['gap']), 'hours': _round(hours), 'result': result}
        c0 = ep.get('c0')
        samples = ep.get('samples') or []
        last = cur if cur is not None else samples[-1][1] if samples else None
        if c0 is not None:
            line['from'] = _round(c0)
        if last is not None:
            line['to'] = _round(last)
        if c0 is not None and last is not None and (hours >= RATE_MIN_H) and (not ep.get('resumed')):
            line['rate'] = _round(abs(last - c0) / hours)
        if outdoor_now is not None:
            line['outdoor'] = _round(outdoor_now)
        self.ring[eid] = [*self.ring.get(eid, []), line][-EPISODES_KEEP:]

    def _finish(self, eid: str, now: float, cur: float | None=None) -> None:
        ep = self.eps.pop(eid)
        hours = (now - ep['start']) / SECONDS_PER_HOUR
        why = 'resumed' if ep['resumed'] else 'said' if ep['alerted'] is not None else 'too_short' if hours < LEARN_MIN_H else 'small_gap' if ep['gap'] < LEARN_GAP else None
        self._log(eid, ep, now, USED if why is None else f'{DROPPED}{why}', cur)
        if why is None:
            rates = self.norms.setdefault(eid, {}).setdefault(ep['dir'], [])
            rates.append([round(ep['gap'] / hours, 4), round(ep['gap'], 3)])
            del rates[:-SAMPLES]
            self.ctx.learned(eid, ep['dir'], rates[-1][0], now, ep['gap'])
        self.ctx.dirty()

    def _drop(self, eid: str, now: float | None=None, why: str='') -> None:
        ep = self.eps.pop(eid, None)
        if ep is not None:
            if now is not None:
                self._log(eid, ep, now, f'{DROPPED}{why}')
            self.ctx.dirty()

    def norm(self, eid: str, d: str, gap: float | None=None) -> float | None:
        rates = self.norms.get(eid, {}).get(d) or []
        if len(rates) < self.need() or not self.ctx.observed(self.name, eid):
            return None
        return _median_rate(rates, gap)

    def raw(self, eid: str, d: str) -> float | None:
        rates = self.norms.get(eid, {}).get(d) or []
        return _median_rate(rates, None) if len(rates) >= LEARN_MIN else None

    @staticmethod
    def need() -> int:
        return max(LEARN_MIN, OBS_EPS)

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        outdoor_now = self.ctx.outdoor_c()
        for eid, ep in self.eps.items():
            if ep['alerted'] is None or self.ctx.air_handler(eid):
                continue
            st = self.ctx.hass.states.get(eid)
            rate = self.norm(eid, ep['dir'], ep['gap'])
            cur = num(st.attributes.get('current_temperature')) if st is not None else None
            change = self._change(ep, cur, now) if cur is not None else None
            out.append({'key': f"{KIND[ep['dir']]}|{eid}", 'kind': KIND[ep['dir']], 'entityId': eid, **self.ctx.who(eid), 'since': _iso(ep['start']), 'hours': _round((now - ep['start']) / SECONDS_PER_HOUR), **({'expectedH': _round(ep['gap'] / rate)} if rate else {}), **({'change': _round(change)} if change is not None else {}), 'flatH': FLAT_H, **({'outdoor': _round(outdoor_now)} if outdoor_now is not None else {})})
        return out

    def keys(self) -> list[tuple[str, str]]:
        out = []
        for eid in dict.fromkeys([*self.norms, *self.eps, *self.ring]):
            dirs = [d for d in (HEAT, COOL) if d in self.norms.get(eid, {})]
            if eid in self.eps and self.eps[eid]['dir'] not in dirs:
                dirs.append(self.eps[eid]['dir'])
            out += [(KIND[d], eid) for d in dirs or [HEAT]]
        return out

    def excluded(self, kind: str, eid: str) -> str | None:
        if self.ctx.air_handler(eid):
            return 'air_handler'
        if not self._season_ok(DIR_OF[kind]):
            return 'season'
        return super().excluded(kind, eid)

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        d = DIR_OF[kind]
        rates = self.norms.get(eid, {}).get(d) or []
        m = self.norm(eid, d)
        if m is None:
            return self.learning(eid, len(rates), self.need(), now)
        return {'ready': {'norm': _round(m), 'unit': self.UNIT, 'n': len(rates)}}

    def episodes(self, eid: str) -> list[dict[str, Any]]:
        return list(self.ring.get(eid, []))

    def inputs(self, kind: str, eid: str) -> list[dict[str, Any]]:
        return self.ctx.climate_inputs(eid)

    def reset(self, eid: str, kind: str) -> None:
        d = DIR_OF[kind]
        self.norms.get(eid, {}).pop(d, None)
        if not self.norms.get(eid):
            self.norms.pop(eid, None)
        if (self.eps.get(eid) or {}).get('dir') == d:
            self.eps.pop(eid)
        self.ring[eid] = [e for e in self.ring.get(eid, []) if e.get('dir') != d]
        if not self.ring[eid]:
            del self.ring[eid]

class StuckSensor(Detector):
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
            self.recs[str(eid)] = {'value': str(r.get('value')), 'last': float(r['last']), 'day': str(r.get('day') or ''), 'count': int(num(r.get('count')) or 0), 'partial': True, 'days': [int(x) for x in r.get('days') or [] if num(x) is not None][-STUCK_DAYS:]}

    def dump(self) -> dict[str, Any]:
        return {'sensors': self.recs}

    def _watched(self, st: State) -> bool:
        return st.attributes.get('device_class') in STUCK_CLASSES and (self.ctx.owns(st.entity_id) or labelled(self.ctx.hass, st.entity_id))

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
                    rec['days'] = [*rec['days'], rec['count']][-STUCK_DAYS:]
                rec.update(day=today, count=0, partial=False)
                self.ctx.dirty()
        if now - self.ctx.started > GONE_AFTER:
            for eid in [e for e in self.recs if self.ctx.hass.states.get(e) is None]:
                del self.recs[eid]
                self.ctx.dirty()

    def norm(self, eid: str) -> float | None:
        days = (self.recs.get(eid) or {}).get('days') or []
        return statistics.median(days) if len(days) >= LEARN_DAYS else None

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for eid, rec in self.recs.items():
            st = self.ctx.hass.states.get(eid)
            if st is None or st.state in NOT_A_STATE:
                continue
            per_day = self.norm(eid)
            hours = (now - rec['last']) / SECONDS_PER_HOUR
            if per_day is None or per_day < STUCK_NORM or hours < STUCK_H:
                continue
            out.append({'key': f'stuck|{eid}', 'kind': 'stuck', 'entityId': eid, **self.ctx.who(eid), 'since': _iso(rec['last']), 'hours': _round(hours), 'perDay': _round(per_day)})
        return out

    def keys(self) -> list[tuple[str, str]]:
        return [('stuck', eid) for eid in self.recs]

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        days = (self.recs.get(eid) or {}).get('days') or []
        m = self.norm(eid)
        if m is None:
            return self.learning(eid, len(days), LEARN_DAYS, now)
        return {'ready': {'norm': _round(m), 'unit': self.UNIT, 'n': len(days)}}

    def reset(self, eid: str, kind: str) -> None:
        self.recs.pop(eid, None)
from .analytics import ANALYTICS, is_note, section_of
from .noeffect import NOEFFECT
from .predict import TRENDS
DETECTORS: tuple[type[Detector], ...] = (ClimateEffect, StuckSensor, *TRENDS, *NOEFFECT, *ANALYTICS)

class Insights:

    def __init__(self, hass: HomeAssistant, owns: Callable[[str], bool], season_now: Callable[[], str | None]) -> None:
        self.hass = hass
        self.owns = owns
        self.watched: Callable[[], set[str]] = set
        self.season_now = season_now
        self.store: HaufloStore = HaufloStore(hass, STORE_KEY)
        self.clock: Callable[[], float] = time.time
        self.detectors: list[Detector] = [cls(self) for cls in DETECTORS]
        self.on_change: Callable[[], None] | None = None
        self.on: Callable[[], bool] = lambda: True
        self.off = False
        self.running = False
        self.started = 0.0
        self.not_windows: set[str] = set()
        self.finding_of: Callable[[str], dict[str, Any] | None] | None = None
        self.first: dict[str, float] = {}
        self.opens: dict[str, list[float]] = {}
        self.resets: dict[str, str] = {}
        self.local: dict[str, dict[str, Any]] = {}
        self.owner: dict[str, Detector] = {k: det for det in self.detectors for k in det.KINDS}
        self._listed: frozenset[str] = frozenset()
        self._unsubs: list[Callable[[], None]] = []
        self.addresses: Callable[[], dict[str, list[str]]] | None = None

    def _addresses(self) -> dict[str, list[str]]:
        try:
            return (self.addresses() if self.addresses else None) or {}
        except Exception:
            _LOGGER.debug('Hauflo insights: KNX addresses not read', exc_info=True)
            return {}

    async def async_start(self) -> None:
        data = await self.store.async_load() or {}
        self._load(data if isinstance(data, dict) else {})
        self.off = not self._wanted()
        if not self.off:
            self._subscribe()

    def _wanted(self) -> bool:
        try:
            return bool(self.on())
        except Exception:
            _LOGGER.debug('Hauflo insights: switch unreadable — on', exc_info=True)
            return True

    @callback
    def switch(self) -> None:
        want = self._wanted()
        if want != self.off:
            return
        self.off = not want
        if want:
            was = self.data()
            self.detectors = [cls(self) for cls in DETECTORS]
            self.owner = {k: det for det in self.detectors for k in det.KINDS}
            self.not_windows = set()
            self._load(was)
            now = self.clock()
            for det in self.detectors:
                self._run(det.warm, now)
            self._subscribe()
        else:
            self.async_stop()
            self.store.async_delay_save(self.data, 0)
            self._listed = frozenset()
        if self.on_change:
            self.on_change()

    def _subscribe(self) -> None:
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_event))
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=TICK)))
        self.running = True

    def _load(self, data: dict[str, Any]) -> None:
        for det in self.detectors:
            try:
                saved = data.get(det.name) if isinstance(data, dict) else None
                det.load(saved if isinstance(saved, dict) else {})
            except Exception:
                _LOGGER.warning('Hauflo insights: %s norms unreadable — learning anew', det.name, exc_info=True)
        self.started = self.clock()
        meta = data.get(META) if isinstance(data, dict) else None
        self._load_meta(meta if isinstance(meta, dict) else {})
        for det in self.detectors:
            for _kind, eid in self._keys(det):
                self.first.setdefault(f'{det.name}|{eid}', self.started - OBSERVE_H * SECONDS_PER_HOUR)
        self._listed = frozenset((i['key'] for i in self._raw(self.started)))

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self.running = False

    def _load_meta(self, meta: dict[str, Any]) -> None:
        self.first = {str(k): float(v) for k, v in (meta.get('first') or {}).items() if num(v) is not None}
        self.opens = {str(k): [float(t) for t in v if num(t) is not None][-MAX_PER_DAY:] for k, v in (meta.get('opens') or {}).items() if isinstance(v, list)}
        self.resets = {str(k): str(v) for k, v in (meta.get('resets') or {}).items() if isinstance(v, str)}
        self.local = {str(k): v for k, v in (meta.get('local') or {}).items() if isinstance(v, dict)}
        self._trim_local()

    def _trim_local(self) -> None:
        if len(self.local) > LOCAL_MAX:
            keep = sorted(self.local.items(), key=lambda kv: str(kv[1].get('at') or ''), reverse=True)[:LOCAL_MAX]
            self.local = dict(keep)

    def data(self) -> dict[str, Any]:
        return {**{det.name: det.dump() for det in self.detectors}, META: {'first': self.first, 'opens': self.opens, 'resets': self.resets, 'local': self.local}}

    @callback
    def dirty(self) -> None:
        self.store.async_delay_save(self.data, SAVE_DELAY)

    @callback
    def _on_event(self, event: Event) -> None:
        new: State | None = event.data.get('new_state')
        if new is None:
            return
        old: State | None = event.data.get('old_state')
        handled = False
        for det in self.detectors:
            if det.domains is None or new.domain in det.domains:
                handled = handled or det.domains is not None
                self._run(det.on_event, old, new, self.clock())
        if handled:
            self._after()

    @callback
    def learned(self, eid: str, direction: str, rate: float, now: float, gap: float | None=None) -> None:
        for det in self.detectors:
            self._run(det.on_learned, eid, direction, rate, now, gap)

    @callback
    def _tick(self, _now: Any=None) -> None:
        now = self.clock()
        for det in self.detectors:
            self._run(det.tick, now)
        self._seen(now)
        self._after()

    @staticmethod
    def _keys(det: Detector) -> list[tuple[str, str]]:
        try:
            return det.keys()
        except Exception:
            _LOGGER.exception('Hauflo insights: %s keys failed', det.name)
            return []

    def _seen(self, now: float) -> None:
        known = set()
        for det in self.detectors:
            for _kind, eid in self._keys(det):
                k = f'{det.name}|{eid}'
                known.add(k)
                if k not in self.first:
                    self.first[k] = now
                    self.dirty()
        if now - self.started > GONE_AFTER and set(self.first) - known:
            self.first = {k: v for k, v in self.first.items() if k in known}
            self.dirty()

    def hours_seen(self, name: str, eid: str, now: float) -> float:
        first = self.first.get(f'{name}|{eid}')
        return max(0.0, (now - first) / SECONDS_PER_HOUR) if first is not None else 0.0

    def mark(self, name: str, eid: str, now: float) -> None:
        if f'{name}|{eid}' not in self.first:
            self.first[f'{name}|{eid}'] = now
            self.dirty()

    def observed(self, name: str, eid: str) -> bool:
        return self.hours_seen(name, eid, self.clock()) >= OBSERVE_H

    def finding(self, key: str) -> dict[str, Any] | None:
        cloud = self.finding_of(key) if self.finding_of else None
        loc = self.local.get(key)
        if loc is not None and (cloud is None or str(loc.get('at') or '') >= str(cloud.get('at') or '')):
            return loc
        return cloud

    def is_normal(self, key: str) -> bool:
        return bool((self.finding(key) or {}).get('normal'))

    def factor(self, key: str, default: float) -> float:
        f = self.finding(key)
        return float(f['factor']) if f and num(f.get('factor')) is not None else default

    @staticmethod
    def _run(fn: Callable[..., None], *args: Any) -> None:
        try:
            fn(*args)
        except Exception:
            _LOGGER.exception('Hauflo insights: %s failed', getattr(fn, '__qualname__', fn))

    @callback
    def _after(self) -> None:
        if self.off:
            return
        now = self.clock()
        keys = frozenset((i['key'] for i in self._shown(now)))
        for k in keys - self._listed:
            self.opens[k] = [*(t for t in self.opens.get(k, []) if now - t < DAY_S), now][-MAX_PER_DAY:]
            self.dirty()
        for k in [k for k, ts in self.opens.items() if k not in keys and all((now - t >= DAY_S for t in ts))]:
            del self.opens[k]
            self.dirty()
        if keys != self._listed:
            self._listed = keys
            if self.on_change:
                self.on_change()

    def _raw(self, now: float) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for det in self.detectors:
            try:
                out.extend(det.items(now))
            except Exception:
                _LOGGER.exception('Hauflo insights: %s items failed', det.name)
        return [i for i in out if not (self.finding(i['key']) or {}).get('normal')]

    def _shown(self, now: float) -> list[dict[str, Any]]:
        out = []
        for i in self._raw(now):
            k = i['key']
            if k in self._listed or sum((1 for t in self.opens.get(k, []) if now - t < DAY_S)) < MAX_PER_DAY:
                out.append(i)
        return sorted(out, key=lambda i: (is_note(i['kind']), i['since'], i['key']))[:LIST_MAX]

    def items(self) -> list[dict[str, Any]]:
        return [] if self.off else self._shown(self.clock())

    def summary(self) -> dict[str, Any]:
        eids = {eid for det in self.detectors for _kind, eid in self._keys(det)}
        since = min(self.first.values(), default=None)
        return {'on': not self.off, 'devices': len(eids), 'since': dt_util.utc_from_timestamp(since).isoformat() if since is not None else None}

    def states(self, entity: str | None=None, offset: int=0, limit: int=LIST_MAX) -> dict[str, Any]:
        now = self.clock()
        rows: list[dict[str, Any]] = []
        for det in self.detectors:
            try:
                opened = {i['key']: i for i in det.items(now)}
                for kind, eid in det.keys():
                    if entity is None or eid == entity:
                        rows.append({'key': f'{kind}|{eid}', 'kind': kind, 'detector': det.name, 'entityId': eid, 'section': section_of(kind), **det.extra(kind, eid), **det.state(eid, now, kind, opened)})
            except Exception:
                _LOGGER.exception('Hauflo insights: %s states failed', det.name)
        rows.sort(key=lambda r: (r['entityId'], r['kind']))
        page = rows[offset:offset + limit]
        addresses = self._addresses()
        for r in page:
            r.update(self.who(r['entityId']))
            if (det := self.owner.get(r['kind'])) is not None:
                r['uses'] = self.uses(det, r['kind'], r['entityId'], addresses)
                r['how'] = det.name
        out: dict[str, Any] = {'total': len(rows), 'offset': offset, 'items': page}
        if entity is not None:
            out['episodes'] = [e for det in self.detectors for e in det.episodes(entity)]
        return out

    def reset(self, kind: str, eid: str, at: str) -> None:
        det = self.owner.get(kind)
        if det is None:
            return
        self._run(det.reset, eid, kind)
        key = f'{kind}|{eid}'
        self.first[f'{det.name}|{eid}'] = self.clock()
        self.resets[key] = at
        self.opens.pop(key, None)
        self.dirty()

    @callback
    def findings_changed(self) -> None:
        for det in self.detectors:
            for kind, eid in self._keys(det):
                key = f'{kind}|{eid}'
                at = (self.finding(key) or {}).get('resetAt')
                if at and at != self.resets.get(key):
                    self.reset(kind, eid, at)
        self._after()

    @callback
    def choose(self, kind: str, eid: str, action: str, by: str, factor: float | None=None) -> None:
        at = _iso(self.clock())
        key = f'{kind}|{eid}'
        if action == 'reset':
            self.reset(kind, eid, at)
        else:
            self.local.pop(key, None)
            self.local[key] = {'by': by, 'at': at, **({'normal': True} if action == 'normal' else {}), **({'factor': factor} if factor is not None else {})}
            self._trim_local()
            self.dirty()
        self._after()

    def choices(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for key, loc in sorted(self.local.items(), key=lambda kv: str(kv[1].get('at') or ''), reverse=True):
            cloud = (self.finding_of(key) if self.finding_of else None) or {}
            if str(loc.get('at') or '') > str(cloud.get('at') or '') and (cloud or 'normal' in loc or 'factor' in loc):
                out[key] = {k: loc[k] for k in ('normal', 'factor', 'by', 'at') if k in loc}
            if len(out) >= CHOICES_MAX:
                break
        return out

    def entry(self, eid: str) -> er.RegistryEntry | None:
        return er.async_get(self.hass).async_get(eid)

    def visible(self, eid: str, diagnostic: bool=False) -> bool:
        entry = self.entry(eid)
        if entry is None:
            return True
        if entry.disabled_by is not None or entry.hidden_by is not None:
            return False
        return diagnostic or entry.entity_category is None

    def outdoor_c(self) -> float | None:
        src = outdoor(self.hass, self.owns)
        st = self.hass.states.get(src['entity_id']) if src else None
        if st is None or src is None:
            return None
        return num(st.attributes.get(src['attribute'])) if src['attribute'] else num(st.state)

    def _area_id(self, eid: str) -> str | None:
        return entity_room(self.hass, eid)

    def air_handler(self, eid: str) -> bool:
        if not eid.startswith('climate.'):
            return False
        w = self.who(eid)
        if AIR_HANDLER_WORDS.search(f"{eid} {w['name']} {w['area']}"):
            return True
        ce = next((det for det in self.detectors if isinstance(det, ClimateEffect)), None)
        rates = [r for r in (ce.raw(eid, d) if ce else None for d in (HEAT, COOL)) if r is not None]
        return any((r >= AIR_HANDLER_RATE for r in rates))

    def who(self, eid: str) -> dict[str, str]:
        st = self.hass.states.get(eid)
        area_id = self._area_id(eid)
        area = ar.async_get(self.hass).async_get_area(area_id) if area_id else None
        return {'name': label(self.hass, eid, st), 'area': area.name if area else ''}

    def opening_open(self, eid: str, ignore: set[str] | frozenset[str]=frozenset()) -> bool:
        area_id = self._area_id(eid)
        if not area_id:
            return False
        return any((s.state == STATE_ON and s.entity_id not in ignore and (s.entity_id not in self.not_windows) and (s.attributes.get('device_class') in OPENING) and (self._area_id(s.entity_id) == area_id) for s in self.hass.states.async_all('binary_sensor')))

    def windows_of(self, eid: str) -> list[str]:
        area_id = self._area_id(eid)
        if not area_id:
            return []
        return sorted((s.entity_id for s in self.hass.states.async_all('binary_sensor') if s.entity_id not in self.not_windows and s.attributes.get('device_class') in OPENING and (self._area_id(s.entity_id) == area_id)))

    def climate_inputs(self, eid: str) -> list[dict[str, Any]]:
        src = outdoor(self.hass, self.owns)
        return [{'role': 'self', 'entityId': eid}, *({'role': 'window', 'entityId': w} for w in self.windows_of(eid)), *([{'role': 'outdoor', 'entityId': src['entity_id']}] if src else []), {'role': 'season'}]

    def uses(self, det: Detector, kind: str, eid: str, addresses: dict[str, list[str]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            got = det.inputs(kind, eid)
        except Exception:
            _LOGGER.exception('Hauflo insights: %s inputs failed', det.name)
            got = [{'role': 'self', 'entityId': eid}]
        for i in got[:INPUTS_MAX]:
            e = i.get('entityId')
            out.append({**i, **(self.who(e) if e else {}), **({'knx': addresses[e][:INPUT_GAS_MAX]} if e and addresses.get(e) else {})})
        return out

def diag_visible(hass: HomeAssistant | None) -> str:
    pushed = ((policy_of(hass) or {}).get('diag') or {}).get('visible')
    return pushed if isinstance(pushed, str) else DIAG_VISIBLE

class InsightsView(HomeAssistantView):
    url = '/api/hauflo/insights'
    name = 'api:hauflo:insights'
    requires_auth = True

    def __init__(self, ins_of: Callable[[], Insights | None], extra: Callable[[], Awaitable[dict[str, Any]]] | None=None, habits_set: Callable[[bool], Awaitable[bool]] | None=None) -> None:
        self.ins_of = ins_of
        self.extra = extra
        self.habits_set = habits_set

    async def _summary(self, ins: Insights) -> dict[str, Any]:
        return {**ins.summary(), **(await self.extra() if self.extra else {})}

    async def get(self, request):
        admin = request['hass_user'].is_admin
        if not admin and diag_visible(request.app[KEY_HASS]) != DIAG_ALL:
            return self.json_message('admin only', 403)
        ins = self.ins_of()
        if ins is None:
            return self.json_message('not ready', 409)
        entity = request.query.get('entity')
        offset = request.query.get('offset', '0')
        limit = request.query.get('limit', str(LIST_MAX))
        if entity is not None and (not ENTITY_ID.match(entity)) or not OFFSET_RE.fullmatch(offset) or int(offset) > OFFSET_MAX:
            return self.json_message('entity / offset', 400)
        if not LIMIT_RE.fullmatch(limit) or not 1 <= int(limit) <= LIMIT_MAX:
            return self.json_message(f'limit: 1..{LIMIT_MAX}', 400)
        out = ins.states(entity, int(offset), int(limit)) if not ins.off else {'total': 0, 'offset': int(offset), 'items': [], 'off': True}
        if int(offset) == 0 and entity is None:
            out['summary'] = await self._summary(ins)
        return self.json({**out, 'sections': SECTIONS, 'sectionOrder': SECTION_ORDER, 'tickS': TICK, 'canEdit': admin})

    async def post(self, request):
        user = request['hass_user']
        if not user.is_admin:
            return self.json_message('admin only', 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        ins = self.ins_of()
        if ins is None:
            return self.json_message('not ready', 409)
        if isinstance(body, dict) and set(body) == {'habits'}:
            if not isinstance(body['habits'], bool) or self.habits_set is None:
                return self.json_message('{habits: true | false} expected', 400)
            if not await self.habits_set(body['habits']):
                return self.json_message('habits not running', 503)
            return self.json({'summary': await self._summary(ins)})
        if not isinstance(body, dict) or not {'entity', 'kind', 'action'} <= set(body) <= {'entity', 'kind', 'action', 'factor'}:
            return self.json_message('{entity, kind, action, factor?} expected', 400)
        entity, kind, action, factor = (body['entity'], body['kind'], body['action'], body.get('factor'))
        if not isinstance(entity, str) or not ENTITY_ID.match(entity) or (not isinstance(kind, str)) or (kind not in ins.owner) or (not isinstance(action, str)) or (action not in ACTIONS_IN):
            return self.json_message('entity / kind / action', 400)
        if factor is not None and (action != 'follow' or isinstance(factor, bool) or (not isinstance(factor, (int, float))) or (not FACTOR_MIN <= float(factor) <= FACTOR_MAX)):
            return self.json_message(f'factor: {FACTOR_MIN}..{FACTOR_MAX} with follow', 400)
        ins.choose(kind, entity, action, user.name or user.id, float(factor) if factor is not None else None)
        return self.json(ins.states(entity))
