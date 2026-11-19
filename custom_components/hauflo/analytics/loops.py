"""Control loop episodes and their tuning hints."""
from __future__ import annotations
import statistics
from typing import TYPE_CHECKING, Any
from homeassistant.core import State
from ..derived import NOT_A_STATE, SECONDS_PER_HOUR, num
from ..insight.common import gone_after, iso, obs_eps, round2
from ..insight.detector import CanonAttr, Detector
if TYPE_CHECKING:
    from ..insight.engine import Insights
from .common import Facts, day, discover, factor, loop_days, loop_keep, loop_max_s, loop_metrics, loop_min_days, loop_min_h, loop_types, loop_units, loops, matches, overshoot_s, peers_min, readers

class LoopTuning(Detector):
    """Heating / cooling / humidity loops (analytics.loops[], first match wins): each episode «wants → reached» gives a rate and a time to setpoint, the OVERSHOOT_H after it an overshoot; each day — the starts and the share of time active."""
    name = 'loops'
    domains = CanonAttr(lambda: tuple(readers()))
    KINDS = CanonAttr(lambda: tuple(dict.fromkeys(loop_metrics().values())))
    DEV = ('limit', 'value')

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        self.loops: dict[str, dict[str, Any]] = {}
        self.run: dict[str, dict[str, Any]] = {}
        self.supply: dict[str, str] = {}
        self.facts = Facts(ctx)
        self._found_at: float | None = None

    def load(self, data: dict[str, Any]) -> None:
        for eid, r in (data.get('loops') or {}).items():
            if not isinstance(r, dict) or r.get('t') not in loop_types():
                continue
            eps = [[float(v) for v in e] for e in r.get('ep') or [] if isinstance(e, list) and len(e) == 3 and all((num(v) is not None for v in e))]
            days = {str(d): [float(v[0]), int(v[1])] for d, v in (r.get('days') or {}).items() if isinstance(v, list) and len(v) == 2 and (num(v[0]) is not None) and (num(v[1]) is not None)}
            opened = {m: o for m, o in (r.get('open') or {}).items() if m in loop_metrics() and isinstance(o, dict) and (num(o.get('since')) is not None)}
            log = [x for x in r.get('log') or [] if isinstance(x, dict)][-loop_keep():]
            jumps = {x['rate'] for x in log if num(x.get('rate')) is not None and (num(x.get('hours')) is None or num(x.get('hours')) < loop_min_h())}
            log = [{k: v for k, v in x.items() if k != 'rate'} if x.get('rate') in jumps else x for x in log]
            eps = [e for e in eps if e[0] not in jumps]
            self.loops[str(eid)] = {'t': r['t'], 'ep': eps[-loop_keep():], 'days': dict(sorted(days.items())[-loop_days():]), 'open': opened, 'log': log}
        self.supply = {str(k): str(v) for k, v in (data.get('supply') or {}).items() if isinstance(v, str)}

    def dump(self) -> dict[str, Any]:
        return {'loops': self.loops, 'supply': self.supply}

    def type_of(self, st: State) -> str | None:
        pe = self.ctx.owner.get('no_effect')
        facts = self.facts.of(st, air_handler=lambda: self.ctx.air_handler(st.entity_id), pair=(pe.climate_kind(st.entity_id) if pe is not None and hasattr(pe, 'climate_kind') else None) or '')
        for lp in loops():
            m = dict(lp['match'])
            want_modes = m.pop('modes', None)
            if want_modes is not None and (not any((want_modes.search(str(x)) for x in st.attributes.get('hvac_modes') or []))):
                continue
            if matches(m, facts):
                return lp['type']
        return None

    def _discover(self, now: float) -> None:
        hass = self.ctx.hass
        for domain in readers():
            for st in hass.states.async_all(domain):
                eid = st.entity_id
                if not self.ctx.visible(eid):
                    continue
                t = self.type_of(st)
                cur = self.loops.get(eid)
                if t is None:
                    continue
                if cur is None:
                    self.loops[eid] = {'t': t, 'ep': [], 'days': {}, 'open': {}, 'log': []}
                    self.ctx.mark(self.name, eid, now)
                    self.ctx.dirty()
                elif cur['t'] != t:
                    self.loops[eid] = {'t': t, 'ep': [], 'days': {}, 'open': {}, 'log': []}
                    self.ctx.dirty()
        if now - self.ctx.started > gone_after():
            for eid in [e for e in self.loops if hass.states.get(e) is None]:
                del self.loops[eid]
                self.ctx.dirty()

    @staticmethod
    def read(st: State) -> dict[str, Any] | None:
        """{cur, tgt, active} by the domain's reader; None — off / unavailable."""
        r = readers().get(st.domain)
        if r is None or (r['off'] is not None and r['off'].search(st.state or '')) or st.state in NOT_A_STATE:
            return None
        cur = num(st.attributes.get(r['current'])) if r['current'] else None
        tgt = num(st.attributes.get(r['target'])) if r['target'] else None
        action = st.attributes.get(r['action']) if r['action'] else None
        if action is not None and r['active'] is not None:
            active: bool | None = bool(r['active'].search(str(action)))
        elif not r['action'] and r['active'] is not None:
            active = bool(r['active'].search(st.state or ''))
        else:
            active = None
        return {'cur': cur, 'tgt': tgt, 'active': active}

    def on_state(self, new: State, now: float) -> None:
        return

    def on_event(self, old: State | None, new: State, now: float) -> None:
        lp = self.loops.get(new.entity_id)
        if lp is None:
            return
        self._step(new.entity_id, lp, new, now)

    def _day_rec(self, lp: dict[str, Any], now: float) -> list[float]:
        d = day(now)
        if d not in lp['days']:
            lp['days'][d] = [0.0, 0]
            for old in sorted(lp['days'])[:-loop_days()]:
                del lp['days'][old]
        return lp['days'][d]

    def _step(self, eid: str, lp: dict[str, Any], st: State, now: float) -> None:
        cfg = loop_types()[lp['t']]
        rd = self.read(st)
        run = self.run.setdefault(eid, {'ep': None, 'active': None, 'at': now})
        if run['active'] is True:
            self._day_rec(lp, now)[0] += max(0.0, now - run['at'])
        act = rd['active'] if rd is not None else None
        if act is True and run['active'] is False:
            self._day_rec(lp, now)[1] += 1
            self.ctx.dirty()
        run['active'], run['at'] = (act, now)
        self._episode(eid, lp, cfg, run, rd, now)

    def _log(self, lp: dict[str, Any], ep: dict[str, Any], now: float, result: str, rate: float | None=None) -> None:
        line = {'start': iso(ep['t0']), 'dir': 'heat' if ep['sign'] > 0 else 'cool', 'gap': round2(ep['gap']), 'hours': round2((now - ep['t0']) / SECONDS_PER_HOUR), 'result': result}
        line['from'] = round2(ep['c0'])
        if ep.get('c1') is not None:
            line['to'] = round2(ep['c1'])
        if rate is not None:
            line['rate'] = round2(rate)
        lp['log'] = [*lp['log'], line][-loop_keep():]
        self.ctx.dirty()

    def _episode(self, eid: str, lp: dict[str, Any], cfg: dict[str, Any], run: dict[str, Any], rd: dict[str, Any] | None, now: float) -> None:
        if not {'rate', 'warmup', 'overshoot'} & set(cfg['metrics']):
            return
        ep = run['ep']
        if rd is None or rd['cur'] is None or rd['tgt'] is None:
            if ep is not None:
                self._log(lp, ep, now, 'gone')
                run['ep'] = None
            return
        cur, tgt = (rd['cur'], rd['tgt'])
        if ep is None:
            gap = tgt - cur
            if abs(gap) >= max(cfg['gap'], 1e-09) and rd['active'] is not False:
                run['ep'] = {'t0': now, 'c0': cur, 'tgt': tgt, 'sign': 1 if gap > 0 else -1, 'gap': abs(gap), 'reach': None, 'peak': 0.0}
            return
        if abs(tgt - ep['tgt']) > cfg['reach']:
            ep['c1'] = cur
            self._log(lp, ep, now, 'target')
            run['ep'] = None
            return
        left = ep['sign'] * (tgt - cur)
        if ep['reach'] is None:
            if left <= cfg['reach']:
                hours = (now - ep['t0']) / SECONDS_PER_HOUR
                ep['c1'] = cur
                if hours < loop_min_h():
                    self._log(lp, ep, now, 'short')
                    run['ep'] = None
                    return
                ep['reach'] = now
                ep['warm'] = hours / max(ep['gap'], 1e-06)
                ep['rate'] = abs(cur - ep['c0']) / hours
            elif now - ep['t0'] > loop_max_s():
                ep['c1'] = cur
                self._log(lp, ep, now, 'long')
                run['ep'] = None
            return
        ep['peak'] = max(ep['peak'], -left)

    def _close_episodes(self, now: float) -> None:
        for eid, run in self.run.items():
            ep = run['ep']
            lp = self.loops.get(eid)
            if ep is None or lp is None or ep['reach'] is None or (now - ep['reach'] < overshoot_s()):
                continue
            lp['ep'] = [*lp['ep'], [round2(ep['rate']), round2(ep['warm']), round2(ep['peak'])]][-loop_keep():]
            self._log(lp, ep, now, 'used', ep['rate'])
            run['ep'] = None
            self._judge(eid, lp, now)

    def metric(self, lp: dict[str, Any], metric: str, now: float) -> tuple[float, int] | None:
        """(own median, n) — None while learning."""
        if metric in ('rate', 'warmup', 'overshoot'):
            i = ('rate', 'warmup', 'overshoot').index(metric)
            xs = [e[i] for e in lp['ep']]
            return (statistics.median(xs), len(xs)) if len(xs) >= obs_eps() else None
        today = day(now)
        days = [v for d, v in lp['days'].items() if d != today]
        if len(days) < loop_min_days():
            return None
        xs = [v[1] for v in days] if metric == 'cycles' else [v[0] / 86400 for v in days]
        return (statistics.median(xs), len(xs))

    def bar(self, eid: str, lp: dict[str, Any], metric: str, now: float) -> tuple[float, str] | None:
        """The bar it is judged against and where it is from: "peers" (median of the same type ×/÷ N) or "type"."""
        cfg = loop_types()[lp['t']]
        key = f'{loop_metrics()[metric]}|{eid}'
        f = self.ctx.factor(key, factor())
        peers = [m[0] for e, o in self.loops.items() if e != eid and o['t'] == lp['t'] and self.ctx.observed(self.name, e) and ((m := self.metric(o, metric, now)) is not None)]
        if len(peers) >= peers_min() and metric != 'duty':
            med = statistics.median(peers)
            return (med / f if metric == 'rate' else med * f, 'peers')
        limit = {'rate': 'rate_min', 'warmup': 'warmup_max_h_per', 'overshoot': 'overshoot_max', 'cycles': 'cycles_max', 'duty': 'duty_max'}[metric]
        v = cfg.get(limit, 0)
        return (v, 'type') if v else None

    def _judge(self, eid: str, lp: dict[str, Any], now: float) -> None:
        cfg = loop_types()[lp['t']]
        if not self.ctx.observed(self.name, eid):
            return
        cyc = self.ctx.owner.get('short_cycling')
        for metric in cfg['metrics']:
            own = self.metric(lp, metric, now)
            bar = self.bar(eid, lp, metric, now) if own is not None else None
            off = False
            if own is not None and bar is not None:
                off = own[0] < bar[0] if metric == 'rate' else own[0] > bar[0]
            if metric == 'cycles' and off and (cyc is not None) and ((getattr(cyc, 'recs', {}).get(eid) or {}).get('open') is not None):
                off = False
            if metric == 'warmup' and off and ('rate' in lp['open']):
                off = False
            op = lp['open'].get(metric)
            if off and op is None:
                lp['open'][metric] = {'since': now, 'was': round2(bar[0]), 'now': round2(own[0]), 'by': bar[1]}
                self.ctx.dirty()
            elif off:
                op.update(was=round2(bar[0]), now=round2(own[0]))
            elif op is not None:
                del lp['open'][metric]
                self.ctx.dirty()

    def tick(self, now: float) -> None:
        if self._found_at is None or now - self._found_at >= discover():
            self._found_at = now
            self._discover(now)
            for eid, lp in self.loops.items():
                self._judge(eid, lp, now)
        for eid, lp in self.loops.items():
            st = self.ctx.hass.states.get(eid)
            if st is None:
                continue
            run = self.run.setdefault(eid, {'ep': None, 'active': None, 'at': now})
            if run['active'] is True:
                self._day_rec(lp, now)[0] += max(0.0, now - run['at'])
                run['at'] = now
            self._episode(eid, lp, loop_types()[lp['t']], run, self.read(st), now)
        self._close_episodes(now)

    def _fields(self, metric: str, op: dict[str, Any], eid: str) -> dict[str, Any]:
        was, cur = (op['was'], op['now'])
        if metric == 'rate':
            out = {'wasRate': was, 'nowRate': cur, 'limit': was, 'value': cur}
            if (sensor := self.supply.get(eid)) and (st := self.ctx.hass.states.get(sensor)) is not None and (num(st.state) is not None):
                out['supply'] = num(st.state)
            return out
        if metric == 'cycles':
            return {'normPerDay': was, 'perDay': cur, 'limit': was, 'value': cur}
        if metric == 'duty':
            return {'limit': round2(was * 100), 'value': round2(cur * 100)}
        return {'limit': was, 'value': cur}

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for eid, lp in self.loops.items():
            if not lp['open'] or not self.ctx.visible(eid):
                continue
            for metric, op in lp['open'].items():
                kind = loop_metrics()[metric]
                out.append({'key': f'{kind}|{eid}', 'kind': kind, 'entityId': eid, **self.ctx.who(eid), 'since': iso(op['since']), 'role': lp['t'], **self._fields(metric, op, eid)})
        return out

    def inputs(self, kind: str, eid: str) -> list[dict[str, Any]]:
        """The loop (its temperature, setpoint, starts) and its supply sensor when the integrator named one; its peers of the same type are the norm."""
        return [{'role': 'self', 'entityId': eid}, *([{'role': 'supply', 'entityId': self.supply[eid]}] if eid in self.supply else []), {'role': 'peers'}]

    def keys(self) -> list[tuple[str, str]]:
        return [(loop_metrics()[m], eid) for eid, lp in self.loops.items() for m in loop_types()[lp['t']]['metrics']]

    def _metric_of(self, kind: str) -> str:
        return next((m for m, k in loop_metrics().items() if k == kind))

    def unit(self, eid: str, metric: str) -> str:
        base = loop_units().get(eid.split('.')[0], 'raw')
        return {'rate': f'{base}_per_h', 'warmup': f'h_per_{base}', 'overshoot': base, 'cycles': 'per_day', 'duty': 'pct'}[metric]

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        lp = self.loops.get(eid)
        metric = self._metric_of(kind)
        if lp is None:
            return self.learning(eid, 0, obs_eps(), now)
        own = self.metric(lp, metric, now)
        if own is not None and self.ctx.observed(self.name, eid):
            v = own[0] * 100 if metric == 'duty' else own[0]
            return {'ready': {'norm': round2(v), 'unit': self.unit(eid, metric), 'n': own[1]}}
        if metric in ('cycles', 'duty'):
            return self.learning(eid, len([d for d in lp['days'] if d != day(now)]), loop_min_days(), now)
        return self.learning(eid, len(lp['ep']), obs_eps(), now)

    def extra(self, kind: str, eid: str) -> dict[str, Any]:
        lp = self.loops.get(eid)
        if lp is None:
            return {}
        cfg = loop_types()[lp['t']]
        out: dict[str, Any] = {'role': lp['t'], 'tip': f"{lp['t']}_{self._metric_of(kind)}"}
        if cfg.get('supply'):
            if eid in self.supply:
                out['supply'] = self.supply[eid]
            else:
                out['ask'] = 'supply'
        op = lp['open'].get(self._metric_of(kind))
        if op is not None:
            out['by'] = op.get('by')
        return out

    def episodes(self, eid: str) -> list[dict[str, Any]]:
        return [] if eid.startswith('climate.') else list((self.loops.get(eid) or {}).get('log', []))

    def reset(self, eid: str, kind: str) -> None:
        lp = self.loops.get(eid)
        if lp is None:
            return
        metric = self._metric_of(kind)
        if metric in ('rate', 'warmup', 'overshoot'):
            lp['ep'], lp['log'] = ([], [])
            self.run.pop(eid, None)
        else:
            lp['days'] = {}
        lp['open'].pop(metric, None)
        self.ctx.dirty()

    def set_supply(self, loop: str, sensor: str) -> None:
        """«Кто греет пол, где датчик подачи»: the integrator's word ("" — forgotten)."""
        if sensor:
            self.supply[loop] = sensor
        else:
            self.supply.pop(loop, None)
        self.ctx.dirty()

    def supply_view(self) -> list[dict[str, Any]]:
        """Loops that want a supply sensor, with the one said (the «Пары» slot)."""
        out = []
        for eid, lp in self.loops.items():
            if loop_types()[lp['t']].get('supply') and self.ctx.hass.states.get(eid) is not None:
                s = self.supply.get(eid)
                out.append({'loop': eid, 'type': lp['t'], **self.ctx.who(eid), 'sensor': s, 'sensorName': self.ctx.who(s)['name'] if s else None})
        return out
