"""Correlations: an event that should follow another and did not."""
from __future__ import annotations
import statistics
from typing import TYPE_CHECKING, Any
from homeassistant.core import State
from ..derived import NOT_A_STATE, num
from ..insight.common import gone_after, iso, obs_eps, round2
from ..insight.detector import CanonAttr, Detector
if TYPE_CHECKING:
    from ..insight.engine import Insights
from .common import Facts, corr_keep, corrs, discover, factor, hold, matches, spec_cfg
from .series import SeriesNorm

class Correlation(Detector):
    """A trigger's switching on and the change of its bound series `window_min` later (analytics.correlations[]): the expected change learned (median of the clean events); judged once it is at least `min_delta` the expected way — HOLD events in a row below max(min_delta, expected / ×N), or the other way, say it; the next good one ends it."""
    name = 'correlation'
    domains = None
    KINDS = CanonAttr(lambda: tuple((f"corr_{c['name']}" for c in corrs())))
    DEV = ('limit', 'change')
    UNIT = 'raw'

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        self.links: dict[str, dict[str, Any]] = {}
        self.pending: dict[str, dict[str, float]] = {}
        self._found_at: float | None = None

    def load(self, data: dict[str, Any]) -> None:
        names = {c['name'] for c in corrs()}
        for eid, r in (data.get('links') or {}).items():
            if isinstance(r, dict) and r.get('c') in names and isinstance(r.get('s'), str):
                op = r.get('open')
                self.links[str(eid)] = {'c': r['c'], 's': r['s'], 'd': [float(x) for x in r.get('d') or [] if num(x) is not None][-corr_keep():], 'a': [float(x) for x in r.get('a') or [] if num(x) is not None][-corr_keep():], 'bad': int(num(r.get('bad')) or 0), 'last': num(r.get('last')), 'open': op if isinstance(op, dict) and num(op.get('since')) is not None else None}

    def dump(self) -> dict[str, Any]:
        return {'links': self.links}

    def _series(self) -> SeriesNorm | None:
        return next((d for d in self.ctx.detectors if isinstance(d, SeriesNorm)), None)

    def _bind(self, trig: str, cfg: dict[str, Any], sn: SeriesNorm) -> str | None:
        kind = cfg['series']
        mine = [e for e, s in sn.series.items() if s['k'] == kind]
        pe = self.ctx.owner.get('no_effect')
        paired = [e for e, p in (getattr(pe, 'pairs', {}) or {}).items() if p.get('exec') == trig and e in mine]
        if len(paired) == 1:
            return paired[0]
        if cfg['scope'] == 'device':
            entry = self.ctx.entry(trig)
            dev = entry.device_id if entry else None
            cands = [e for e in mine if dev and (x := self.ctx.entry(e)) is not None and (x.device_id == dev)]
        else:
            area = self.ctx.area_id(trig)
            cands = [e for e in mine if area and self.ctx.area_id(e) == area]
        return cands[0] if len(cands) == 1 else None

    def _discover(self, now: float) -> None:
        sn = self._series()
        if sn is None:
            return
        hass = self.ctx.hass
        facts = Facts(self.ctx)
        if now - self.ctx.started > gone_after():
            for eid in [e for e in self.links if hass.states.get(e) is None]:
                del self.links[eid]
                self.ctx.dirty()
        for st in hass.states.async_all():
            eid = st.entity_id
            if not self.ctx.visible(eid):
                continue
            f = facts.of(st)
            cfg, target = next(((c, t) for c in corrs() if matches(c['trigger'], f) and (t := self._bind(eid, c, sn)) is not None), (None, None))
            cur = self.links.get(eid)
            if target is None:
                if cur is not None and now - self.ctx.started > gone_after():
                    del self.links[eid]
                    self.ctx.dirty()
                continue
            if cur is None or cur['c'] != cfg['name'] or cur['s'] != target:
                self.links[eid] = {'c': cfg['name'], 's': target, 'd': [], 'a': [], 'bad': 0, 'last': None, 'open': None}
                self.ctx.mark(self.name, eid, now)
                self.ctx.dirty()

    def on_state(self, new: State, now: float) -> None:
        return

    def on_event(self, old: State | None, new: State, now: float) -> None:
        link = self.links.get(new.entity_id)
        if link is None:
            return
        cfg = next((c for c in corrs() if c['name'] == link['c']))
        on = bool(cfg['to'].search(new.state or ''))
        was = old is not None and old.state not in NOT_A_STATE and bool(cfg['to'].search(old.state or ''))
        if on and (not was) and (old is not None) and (old.state not in NOT_A_STATE):
            sn = self._series()
            v0 = sn.now_of(link['s']) if sn else None
            if v0 is not None:
                self.pending[new.entity_id] = {'t': now, 'v': v0}
        elif not on:
            self.pending.pop(new.entity_id, None)

    def tick(self, now: float) -> None:
        if self._found_at is None or now - self._found_at >= discover():
            self._found_at = now
            self._discover(now)
        sn = self._series()
        for trig in list(self.pending):
            link = self.links.get(trig)
            p = self.pending[trig]
            cfg = next((c for c in corrs() if link and c['name'] == link['c']), None)
            if link is None or cfg is None:
                del self.pending[trig]
                continue
            if now - p['t'] < cfg['window_min'] * 60:
                continue
            del self.pending[trig]
            v1 = sn.now_of(link['s']) if sn else None
            if v1 is not None:
                self._event(trig, link, cfg, p['v'], v1, p['t'])

    def expected(self, link: dict[str, Any]) -> float | None:
        return statistics.median(link['d']) if len(link['d']) >= obs_eps() else None

    def level(self, link: dict[str, Any], cfg: dict[str, Any]) -> float | None:
        """Where the trigger brings the series: the learned level after the clean events, else the canon's `level` (an extract fan can't take a room's humidity / CO2 below it)."""
        a = link.setdefault('a', [])
        return statistics.median(a) if len(a) >= obs_eps() else cfg.get('level')

    def _event(self, trig: str, link: dict[str, Any], cfg: dict[str, Any], v0: float, v1: float, t: float) -> None:
        sign = 1 if cfg['expect'] == 'rise' else -1
        signed = sign * (v1 - v0)
        exp = self.expected(link) if self.ctx.observed(self.name, trig) else None
        key = f"corr_{cfg['name']}|{trig}"
        judged = exp is not None and exp >= cfg['min_delta']
        pe = self.ctx.owner.get('no_effect')
        if judged and pe is not None and (getattr(pe, 'runs', {}).get(link['s']) or {}).get('alerted'):
            judged = False
        bar = max(cfg['min_delta'], exp / self.ctx.factor(key, factor())) if judged else None
        lvl = self.level(link, cfg)
        if lvl is not None and sign * (lvl - v0) < max(cfg['min_delta'], bar or 0):
            return
        fail = judged and signed < bar
        link['last'] = round2(signed)
        normal = self.ctx.is_normal(key)
        if fail and normal:
            link['d'] = [*link['d'], round2(signed)][-corr_keep():]
            link['a'] = [*link['a'], round2(v1)][-corr_keep():]
        if fail:
            link['bad'] += 1
            if link['open'] is None and link['bad'] >= hold():
                link['open'] = {'since': t, 'was': round2(exp), 'now': round2(signed)}
            elif link['open'] is not None:
                link['open']['now'] = round2(signed)
        else:
            link['bad'] = 0
            link['open'] = None
            link['d'] = [*link['d'], round2(signed)][-corr_keep():]
            link['a'] = [*link['a'], round2(v1)][-corr_keep():]
        self.ctx.dirty()

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for trig, link in self.links.items():
            op = link['open']
            if op is None or not self.ctx.visible(trig):
                continue
            cfg = next((c for c in corrs() if c['name'] == link['c']), None)
            if cfg is None:
                continue
            kind = f"corr_{cfg['name']}"
            out.append({'key': f'{kind}|{trig}', 'kind': kind, 'entityId': trig, **self.ctx.who(trig), 'since': iso(op['since']), 'role': cfg['series'], 'exec': self.ctx.who(link['s'])['name'], 'limit': op['was'], 'change': op['now'], 'flatH': round2(cfg['window_min'] / 60)})
        return out

    def inputs(self, kind: str, eid: str) -> list[dict[str, Any]]:
        """What is switched on (the trigger) and the reading that should move after it."""
        link = self.links.get(eid)
        return [{'role': 'trigger', 'entityId': eid}, *([{'role': 'effect', 'entityId': link['s']}] if link else [])]

    def keys(self) -> list[tuple[str, str]]:
        return [(f"corr_{link['c']}", trig) for trig, link in self.links.items()]

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        link = self.links.get(eid)
        cfg = next((c for c in corrs() if f"corr_{c['name']}" == kind), None)
        unit = spec_cfg()[cfg['series']]['unit'] if cfg and cfg['series'] in spec_cfg() else self.UNIT
        exp = self.expected(link) if link is not None and self.ctx.observed(self.name, eid) else None
        if exp is not None:
            return {'ready': {'norm': round2(exp), 'unit': unit, 'n': len(link['d'])}}
        return self.learning(eid, len(link['d']) if link else 0, obs_eps(), now)

    def extra(self, kind: str, eid: str) -> dict[str, Any]:
        link = self.links.get(eid)
        return {'effect': link['s']} if link else {}

    def reset(self, eid: str, kind: str) -> None:
        link = self.links.get(eid)
        if link is not None:
            link.update(d=[], a=[], bad=0, last=None, open=None)
            self.pending.pop(eid, None)
            self.ctx.dirty()
