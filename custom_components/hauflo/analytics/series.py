"""A series' own learned norm and the values outside it."""
from __future__ import annotations
import statistics
import time
from typing import TYPE_CHECKING, Any
from homeassistant.core import State
from ..derived import NOT_A_STATE, SECONDS_PER_HOUR, num
from ..insight.common import gone_after, iso, obs_eps, round2
from ..insight.detector import CanonAttr, Detector
if TYPE_CHECKING:
    from ..insight.engine import Insights
from .common import ANY, Facts, cover, days, discover, hold, hour_span, judge, keep, levels, local, match_cfg, matches, max_catchup, max_series, mid, norms, period, robust, spec_cfg, specs, to_cfg, value_of, z, z_end

class SeriesNorm(Detector):
    """A series per entity (analytics.series[]), an hourly value per season × hour-of-week bucket, judged against its bucket's median ± MAD (robust z ≥ Z, |Δ| ≥ the kind's min_delta, HOLD hours in a row) or the type's norm."""
    name = 'series'
    domains = None
    KINDS = CanonAttr(lambda: tuple((f"norm_{s['kind']}" for s in specs())))
    DEV = ('limit', 'value')

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        self.series: dict[str, dict[str, Any]] = {}
        self.acc: dict[str, dict[str, Any]] = {}
        self.facts = Facts(ctx)
        self._found_at: float | None = None
        self.took_ms = 0.0

    def load(self, data: dict[str, Any]) -> None:
        for eid, s in (data.get('series') or {}).items():
            if not isinstance(s, dict) or s.get('k') not in spec_cfg():
                continue
            b = {str(k): [float(x) for x in v if num(x) is not None][-keep():] for k, v in (s.get('b') or {}).items() if isinstance(v, list)}
            op = s.get('open')
            self.series[str(eid)] = {'k': s['k'], 'b': {k: v for k, v in b.items() if v}, 'bad': int(num(s.get('bad')) or 0), 'bb': int(num(s.get('bb')) or 0), 'open': op if isinstance(op, dict) and num(op.get('since')) is not None else None, 'norm': num(s.get('norm')), 'n': int(num(s.get('n')) or 0), 'tn': s.get('tn') if isinstance(s.get('tn'), int) and 0 <= s['tn'] < len(norms()) else None, 'last': num(s.get('last'))}

    def dump(self) -> dict[str, Any]:
        return {'series': self.series}

    def spec_of(self, st: State) -> tuple[str | None, int | None]:
        facts = self.facts.of(st)
        for spec in specs():
            if matches(match_cfg()[spec['kind']], facts) and ('units' not in spec or facts['unit'] in spec['units']):
                tn = next((i for i, n in enumerate(norms()) if matches(n['match'], {**facts, 'kind': spec['kind']})), None)
                return (spec['kind'], tn)
        return (None, None)

    def _discover(self, now: float) -> None:
        hass = self.ctx.hass
        if now - self.ctx.started > gone_after():
            for eid in [e for e in self.series if hass.states.get(e) is None]:
                del self.series[eid]
                self.acc.pop(eid, None)
                self.ctx.dirty()
        free = max_series() - len(self.series)
        if free <= 0:
            return
        found: list[tuple[int, str, str, int | None]] = []
        order = {s['kind']: i for i, s in enumerate(specs())}
        for st in hass.states.async_all():
            if st.entity_id in self.series or not self.ctx.visible(st.entity_id):
                continue
            kind, tn = self.spec_of(st)
            if kind is not None:
                found.append((order[kind], st.entity_id, kind, tn))
        for _o, eid, kind, tn in sorted(found)[:free]:
            self.series[eid] = {'k': kind, 'b': {}, 'bad': 0, 'bb': 0, 'open': None, 'norm': None, 'n': 0, 'tn': tn, 'last': None}
            self.ctx.mark(self.name, eid, now)
            self.ctx.dirty()

    def _new_acc(self, eid: str, spec: dict[str, Any], now: float) -> dict[str, Any]:
        st = self.ctx.hass.states.get(eid)
        v = value_of(spec, st) if spec['agg'] != 'count' else None
        a = {'p': int(now // period()), 'partial': True, 't': now, 'v': v, 'v0': v is not None, 'sw': 0.0, 'w': 0.0, 'inc': 0.0, 'n': 0}
        self.acc[eid] = a
        return a

    @staticmethod
    def _integrate(a: dict[str, Any], t: float) -> None:
        if a['v'] is not None and t > a['t']:
            a['sw'] += a['v'] * (t - a['t'])
            a['w'] += t - a['t']
        a['t'] = max(a['t'], t)

    def on_state(self, new: State, now: float) -> None:
        return

    def on_event(self, old: State | None, new: State, now: float) -> None:
        s = self.series.get(new.entity_id)
        if s is None:
            return
        spec = spec_cfg()[s['k']]
        if not spec.get('own', 1):
            return
        a = self.acc.get(new.entity_id) or self._new_acc(new.entity_id, spec, now)
        self._roll(new.entity_id, s, spec, a, now)
        agg = spec['agg']
        if agg == 'count':
            to = to_cfg()[s['k']]
            if to.search(new.state or '') and old is not None and (old.state not in NOT_A_STATE) and (not to.search(old.state or '')):
                a['n'] += 1
            return
        x = value_of(spec, new)
        if agg == 'mean':
            self._integrate(a, now)
            a['v'] = x
            return
        if x is None:
            return
        if a['v'] is not None:
            d = x - a['v']
            a['inc'] += d if d >= 0 else x
        a['v'] = x

    def _value(self, spec: dict[str, Any], a: dict[str, Any]) -> float | None:
        if a['partial']:
            return None
        if spec['agg'] == 'mean':
            return a['sw'] / a['w'] if a['w'] >= cover() * period() else None
        if spec['agg'] == 'count':
            return a['n'] * SECONDS_PER_HOUR / period()
        return a['inc'] * SECONDS_PER_HOUR / period() if a['v0'] else None

    def _roll(self, eid: str, s: dict[str, Any], spec: dict[str, Any], a: dict[str, Any], now: float) -> None:
        p_now = int(now // period())
        if p_now - a['p'] > max_catchup():
            self._new_acc(eid, spec, now)
            return
        while a['p'] < p_now:
            end = (a['p'] + 1) * period()
            if spec['agg'] == 'mean':
                self._integrate(a, end)
            x = self._value(spec, a)
            if x is not None:
                self._sample(eid, s, spec, x, a['p'] * period())
            a.update(p=a['p'] + 1, partial=False, t=end, sw=0.0, w=0.0, inc=0.0, n=0, v0=a['v'] is not None)

    def bucket(self, spec: dict[str, Any], ts: float) -> str:
        t = local(ts)
        season = self.ctx.season_now() or mid() if spec.get('season') else ANY
        return f'{season}|{days()[t.weekday()]}|{t.hour // hour_span()}'

    @staticmethod
    def norm_at(s: dict[str, Any], key: str) -> tuple[float, float, int, str] | None:
        """(median, spread, n, level) of the bucket, else of that hour on any day, else of the season — the first with OBS_EPS values (the shared gate's count)."""
        season, _dg, hb = key.split('|')
        b = s['b']
        for level in levels():
            if level == 'bucket':
                xs = b.get(key, [])
            elif level == 'hour':
                xs = [x for k, v in b.items() if k.startswith(f'{season}|') and k.endswith(f'|{hb}') for x in v]
            else:
                xs = [x for k, v in b.items() if k.startswith(f'{season}|') for x in v]
            if len(xs) >= obs_eps():
                m, spread = robust(xs)
                return (m, spread, len(xs), level)
        return None

    @staticmethod
    def judge_at(s: dict[str, Any], key: str) -> tuple[list[float], float, int] | None:
        """The norm a value is judged against: (its medians — the level's and, below the bucket, the bucket's own —, spread, n), or None while learning (only the season has enough, or the bucket's own values are too few)."""
        norm = SeriesNorm.norm_at(s, key)
        if norm is None or norm[3] not in judge():
            return None
        own = s['b'].get(key, [])
        if len(own) < judge()[norm[3]]:
            return None
        refs = [norm[0]] + ([statistics.median(own)] if norm[3] != 'bucket' and own else [])
        return (refs, norm[1], norm[2])

    def _sample(self, eid: str, s: dict[str, Any], spec: dict[str, Any], x: float, start: float) -> None:
        key = self.bucket(spec, start)
        kind = f"norm_{spec['kind']}"
        fkey = f'{kind}|{eid}'
        norm = self.judge_at(s, key) if self.ctx.observed(self.name, eid) else None
        if norm is not None:
            s['norm'], s['n'] = (round2(norm[0][0]), norm[2])
        else:
            s['norm'] = None
        f = self.ctx.factor(fkey, z())
        dev_own = norm is not None and all((abs(x - r) / norm[1] >= f and abs(x - r) >= spec['min_delta'] for r in norm[0]))
        tn = norms()[s['tn']] if s.get('tn') is not None else None
        bound = None
        if tn is not None:
            if 'max' in tn and x > tn['max']:
                bound = tn['max']
            elif 'min' in tn and x < tn['min']:
                bound = tn['min']
        s['bad'] = s['bad'] + 1 if dev_own else 0
        s['bb'] = s['bb'] + 1 if bound is not None else 0
        s['last'] = round2(x)
        op = s['open']
        if op is None and (s['bad'] >= hold() or (tn is not None and s['bb'] >= tn['hold'])):
            by_own = s['bad'] >= hold()
            held = s['bad'] if by_own else s['bb']
            s['open'] = {'since': start - (held - 1) * period(), 'was': round2(norm[0][0] if by_own else bound), 'now': round2(x), 'by': 'own' if by_own else 'type'}
        elif op is not None:
            calm = (norm is None or any((abs(x - r) / norm[1] < z_end() or abs(x - r) < spec['min_delta'] for r in norm[0]))) and bound is None
            if calm:
                s['open'] = None
            else:
                op['now'] = round2(x)
        if not dev_own or self.ctx.is_normal(fkey):
            ring = s['b'].setdefault(key, [])
            ring.append(round2(x))
            del ring[:-keep()]
        self.ctx.dirty()

    def tick(self, now: float) -> None:
        t0 = time.perf_counter()
        if self._found_at is None or now - self._found_at >= discover():
            self._found_at = now
            self._discover(now)
        for eid, s in self.series.items():
            spec = spec_cfg()[s['k']]
            if not spec.get('own', 1):
                continue
            a = self.acc.get(eid) or self._new_acc(eid, spec, now)
            self._roll(eid, s, spec, a, now)
        self.took_ms = (time.perf_counter() - t0) * 1000

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for eid, s in self.series.items():
            op = s['open']
            if op is None or not self.ctx.visible(eid):
                continue
            kind = f"norm_{s['k']}"
            was, cur = (op.get('was'), op.get('now'))
            out.append({'key': f'{kind}|{eid}', 'kind': kind, 'entityId': eid, **self.ctx.who(eid), 'since': iso(op['since']), 'role': s['k'], 'value': cur, 'limit': was, 'hours': round2((now - op['since']) / SECONDS_PER_HOUR), **({'change': round2((cur - was) / abs(was) * 100)} if num(was) and num(cur) is not None else {})})
        return out

    def keys(self) -> list[tuple[str, str]]:
        return [(f"norm_{s['k']}", eid) for eid, s in self.series.items()]

    def excluded(self, kind: str, eid: str) -> str | None:
        if (why := super().excluded(kind, eid)) is not None:
            return why
        s = self.series.get(eid)
        if s is not None and (not spec_cfg()[s['k']].get('own', 1)) and (s.get('tn') is None):
            return 'no_norm'
        return None

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        s = self.series.get(eid)
        spec = spec_cfg().get(kind.removeprefix('norm_')) or specs()[-1]
        unit = spec['unit']
        if s is not None and self.ctx.observed(self.name, eid) and ((norm := self.judge_at(s, self.bucket(spec, now))) is not None):
            return {'ready': {'norm': round2(norm[0][0]), 'unit': unit, 'n': norm[2]}}
        tn = norms()[s['tn']] if s is not None and s.get('tn') is not None else None
        if tn is not None:
            return {'ready': {'norm': tn.get('max', tn.get('min')), 'unit': unit, 'n': 0}}
        key = self.bucket(spec, now)
        have = len([x for k, v in (s or {}).get('b', {}).items() if k.split('|')[0] == key.split('|')[0] and k.endswith(f"|{key.split('|')[2]}") for x in v])
        return self.learning(eid, have, obs_eps(), now)

    def extra(self, kind: str, eid: str) -> dict[str, Any]:
        s = self.series.get(eid)
        if s is None:
            return {}
        out: dict[str, Any] = {'unit': spec_cfg()[s['k']]['unit']}
        if s['last'] is not None:
            out['last'] = s['last']
        if s.get('tn') is not None:
            out['standard'] = norms()[s['tn']]['src']
        elif not spec_cfg()[s['k']].get('own', 1):
            out['ask'] = True
        return out

    def reset(self, eid: str, kind: str) -> None:
        s = self.series.get(eid)
        if s is not None:
            s.update(b={}, bad=0, bb=0, open=None, norm=None, n=0)
            self.acc.pop(eid, None)
            self.ctx.dirty()

    def now_of(self, eid: str) -> float | None:
        """The series' value now (the correlation's before / after)."""
        s = self.series.get(eid)
        return value_of(spec_cfg()[s['k']], self.ctx.hass.states.get(eid)) if s is not None else None
