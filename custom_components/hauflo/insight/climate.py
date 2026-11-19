"""How fast each climate heats or cools the room, against its own norm."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any
from homeassistant.core import State
from ..derived import NOT_A_STATE, SECONDS_PER_HOUR, num
from .common import COOL, DIR_OF, DROPPED, HEAT, KEEP, KIND, SIGN, USED, actions, episodes_keep, factor, flat_c, flat_h, gap_cfg, gone_after, iso, learn_gap, learn_min, learn_min_h, learned_rates, median_rate, min_h, modes, obs_eps, rate_min_h, reach, round2, samples_cfg, sane_line
from .detector import Detector
if TYPE_CHECKING:
    from .engine import Insights

class ClimateEffect(Detector):
    """Heating / cooling without effect, against the zone's own rate (°C per hour from wanting to reaching)."""
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
                got = {d: [x for x in learned_rates(by.get(d)) if x[1] is None or x[1] / x[0] >= learn_min_h()][-samples_cfg():] for d in (HEAT, COOL)}
                self.norms[str(eid)] = {d: v for d, v in got.items() if v}
        for eid, ring in (data.get('ring') or {}).items():
            if isinstance(ring, list):
                self.ring[str(eid)] = [sane_line(e) for e in ring if isinstance(e, dict)][-episodes_keep():]
        for eid, ep in (data.get('episodes') or {}).items():
            if not isinstance(ep, dict) or ep.get('dir') not in (HEAT, COOL) or num(ep.get('start')) is None or (num(ep.get('gap')) is None):
                continue
            last_on = num(ep.get('last_on'))
            alerted = num(ep.get('alerted'))
            self.eps[str(eid)] = {'dir': ep['dir'], 'start': float(ep['start']), 'gap': float(ep['gap']), 'target': num(ep.get('target')), 'mode': str(ep.get('mode') or ''), 'last_on': last_on if last_on is not None else float(ep['start']), 'alerted': alerted, 'c0': num(ep.get('c0')), 'resumed': True, 'samples': []}

    def dump(self) -> dict[str, Any]:
        return {'norms': self.norms, 'episodes': {eid: {k: ep.get(k) for k in KEEP} for eid, ep in self.eps.items()}, 'ring': self.ring}

    def _season_ok(self, d: str) -> bool:
        season = self.ctx.season_now()
        return season not in (HEAT, COOL) or season == d

    def _want(self, st: State, cur: float, tgt: float) -> str | None:
        action = st.attributes.get('hvac_action')
        for d in (HEAT, COOL):
            if st.state not in modes()[d] or not self._season_ok(d):
                continue
            if (tgt - cur) * SIGN[d] < gap_cfg():
                continue
            if action is not None and action not in actions()[d]:
                continue
            return d
        return None

    def on_state(self, new: State, now: float) -> None:
        self._eval(new, now)

    def tick(self, now: float) -> None:
        for st in self.ctx.hass.states.async_all('climate'):
            self._eval(st, now)
        if now - self.ctx.started > gone_after():
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
            elif (cur - tgt) * SIGN[d] >= -reach():
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
        if action is None or action in actions()[d]:
            ep['last_on'] = now
        samples: list[tuple[float, float]] = ep['samples']
        if not samples or samples[-1][1] != cur:
            samples.append((now, cur))
        edge = now - flat_h() * SECONDS_PER_HOUR
        while len(samples) > 1 and samples[1][0] <= edge:
            samples.pop(0)
        if ep['alerted'] is not None:
            return
        rate = self.norm(st.entity_id, d, ep['gap'])
        if rate is None:
            return
        expected = ep['gap'] / rate
        hours = (now - ep['start']) / SECONDS_PER_HOUR
        if hours <= max(min_h(), self.ctx.factor(f'{KIND[d]}|{st.entity_id}', factor()) * expected):
            return
        change = self._change(ep, cur, now)
        if change is None or change * SIGN[d] >= flat_c():
            return
        if now - ep['last_on'] > flat_h() * SECONDS_PER_HOUR:
            return
        if self.ctx.air_handler(st.entity_id):
            return
        ep['alerted'] = now
        self.ctx.dirty()

    def _change(self, ep: dict[str, Any], cur: float, now: float) -> float | None:
        samples = ep['samples']
        edge = now - flat_h() * SECONDS_PER_HOUR
        if not samples or samples[0][0] > edge:
            return None
        anchor = [v for t, v in samples if t <= edge][-1]
        return cur - anchor

    def _log(self, eid: str, ep: dict[str, Any], now: float, result: str, cur: float | None=None) -> None:
        hours = (now - ep['start']) / SECONDS_PER_HOUR
        outdoor_now = self.ctx.outdoor_c()
        line = {'start': iso(ep['start']), 'dir': ep['dir'], 'gap': round2(ep['gap']), 'hours': round2(hours), 'result': result}
        c0 = ep.get('c0')
        samples = ep.get('samples') or []
        last = cur if cur is not None else samples[-1][1] if samples else None
        if c0 is not None:
            line['from'] = round2(c0)
        if last is not None:
            line['to'] = round2(last)
        if c0 is not None and last is not None and (hours >= rate_min_h()) and (not ep.get('resumed')):
            line['rate'] = round2(abs(last - c0) / hours)
        if outdoor_now is not None:
            line['outdoor'] = round2(outdoor_now)
        self.ring[eid] = [*self.ring.get(eid, []), line][-episodes_keep():]

    def _finish(self, eid: str, now: float, cur: float | None=None) -> None:
        ep = self.eps.pop(eid)
        hours = (now - ep['start']) / SECONDS_PER_HOUR
        why = 'resumed' if ep['resumed'] else 'said' if ep['alerted'] is not None else 'too_short' if hours < learn_min_h() else 'small_gap' if ep['gap'] < learn_gap() else None
        self._log(eid, ep, now, USED if why is None else f'{DROPPED}{why}', cur)
        if why is None:
            rates = self.norms.setdefault(eid, {}).setdefault(ep['dir'], [])
            rates.append([round(ep['gap'] / hours, 4), round(ep['gap'], 3)])
            del rates[:-samples_cfg()]
            self.ctx.learned(eid, ep['dir'], rates[-1][0], now, ep['gap'])
        self.ctx.dirty()

    def _drop(self, eid: str, now: float | None=None, why: str='') -> None:
        ep = self.eps.pop(eid, None)
        if ep is not None:
            if now is not None:
                self._log(eid, ep, now, f'{DROPPED}{why}')
            self.ctx.dirty()

    def norm(self, eid: str, d: str, gap: float | None=None) -> float | None:
        """The zone's own rate, °C/h — only after the observation gate (OBS_EPS learned episodes AND OBSERVE_H hours since first seen); with `gap` — of the episodes that started as far away (±GAP_BAND), fewer than GAP_LIKE_MIN: None."""
        rates = self.norms.get(eid, {}).get(d) or []
        if len(rates) < self.need() or not self.ctx.observed(self.name, eid):
            return None
        return median_rate(rates, gap)

    def raw(self, eid: str, d: str) -> float | None:
        """Its rate before the gate (an air handler is known by a rate no room reaches — in its first day too)."""
        rates = self.norms.get(eid, {}).get(d) or []
        return median_rate(rates, None) if len(rates) >= learn_min() else None

    @staticmethod
    def need() -> int:
        return max(learn_min(), obs_eps())

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
            out.append({'key': f"{KIND[ep['dir']]}|{eid}", 'kind': KIND[ep['dir']], 'entityId': eid, **self.ctx.who(eid), 'since': iso(ep['start']), 'hours': round2((now - ep['start']) / SECONDS_PER_HOUR), **({'expectedH': round2(ep['gap'] / rate)} if rate else {}), **({'change': round2(change)} if change is not None else {}), 'flatH': flat_h(), **({'outdoor': round2(outdoor_now)} if outdoor_now is not None else {})})
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
        return {'ready': {'norm': round2(m), 'unit': self.UNIT, 'n': len(rates)}}

    def episodes(self, eid: str) -> list[dict[str, Any]]:
        return list(self.ring.get(eid, []))

    def inputs(self, kind: str, eid: str) -> list[dict[str, Any]]:
        """The climate (its temperature, setpoint and mode), the room's windows / doors (an open one pauses the check), the street's temperature (kept with each episode) and the season (heating / cooling)."""
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
