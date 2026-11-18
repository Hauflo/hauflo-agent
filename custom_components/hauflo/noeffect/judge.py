"""Samples a pair and judges whether the executor has an effect."""
from __future__ import annotations
import statistics
from typing import Any
from homeassistant.core import State
from ..derived import NOT_A_STATE, SECONDS_PER_HOUR, num
from ..insight.climate import ClimateEffect
from ..insight.common import actions, gone_after, modes
from .common import active_cfg, hold, kinds_cfg, min_share, repeat_s, sample_s

class PairEffectJudgeMixin:
    """Samples and judges the pairs."""

    def _read(self, eff: str, p: dict[str, Any]) -> tuple[int, float, float | None] | None:
        es, xs = (self.ctx.hass.states.get(eff), self.ctx.hass.states.get(p['exec']))
        if es is None or xs is None or es.state in NOT_A_STATE or (xs.state in NOT_A_STATE):
            return None
        if es.domain == 'climate':
            val, tgt = (num(es.attributes.get('current_temperature')), num(es.attributes.get('temperature')))
            action = es.attributes.get('hvac_action')
            if val is None or tgt is None or es.state not in modes()['heat']:
                return None
            return (1 if action in actions()['heat'] else 0, val, tgt)
        val = num(es.state)
        if val is None:
            return None
        tgt = None
        ts = self.ctx.hass.states.get(p['target']) if p.get('target') else None
        if ts is not None and ts.state not in NOT_A_STATE and (ts.state != 'off'):
            tgt = num(ts.attributes.get('temperature'))
        return (1 if xs.state in active_cfg() else 0, val, tgt)

    def on_state(self, new: State, now: float) -> None:
        if new.entity_id in self._involved:
            self._sample(now, only=new.entity_id)

    def _sample(self, now: float, only: str | None=None) -> None:
        for eff, p in self.pairs.items():
            if only is not None and only not in (eff, p['exec'], p.get('target')):
                continue
            run = self.runs.setdefault(eff, self._new())
            got = self._read(eff, p)
            if got is None:
                run['samples'], run['bad_since'], run['last'] = ([], None, None)
                continue
            s = run['samples']
            run['last'] = got
            if s and s[-1][1:] == got and (only is not None or now - s[-1][0] < sample_s()):
                continue
            s.append((now, got[0], got[1], got[2]))
            edge = now - kinds_cfg()[p['kind']]['window_h'] * SECONDS_PER_HOUR
            while len(s) > 1 and s[1][0] <= edge:
                s.pop(0)

    def warm(self, now: float) -> None:
        """The pairs are the mapping's, never saved: found at once when switched on again (insight.Detector.warm)."""
        self.refresh(now)

    def tick(self, now: float) -> None:
        self.refresh(now)
        self._sample(now)
        execs = {p['exec'] for p in self.pairs.values()}
        self.ctx.not_windows = execs
        for eff, p in self.pairs.items():
            self._judge(eff, p, self.runs[eff], now, execs)
        if now - self.ctx.started > gone_after():
            for eff in [e for e in self.runs if self.ctx.hass.states.get(e) is None and (not self.runs[e]['alerted'])]:
                self.runs.pop(eff, None)

    def _delta(self, samples: list, now: float, hours: float) -> tuple[float, float] | None:
        edge = now - hours * SECONDS_PER_HOUR
        if not samples or samples[0][0] > edge:
            return None
        anchor = max((s for s in samples if s[0] <= edge), key=lambda s: s[0])
        pts = [(edge, anchor[1])] + [(s[0], s[1]) for s in samples if s[0] > edge]
        active = sum(((nxt[0] - cur[0]) * cur[1] for cur, nxt in zip(pts, [*pts[1:], (now, 0)], strict=False)))
        return (samples[-1][2] - anchor[2], active / (now - edge))

    def _room_left(self, eff: str, p: dict[str, Any], cfg: dict[str, Any], val: float, tgt: float | None) -> bool:
        if cfg['falls']:
            return val >= cfg['needs']
        if tgt is not None:
            return tgt - val >= cfg['margin']
        if cfg['peer_gap']:
            area = self.ctx.area_id(eff)
            peers = [r['last'][1] for e, r in self.runs.items() if e != eff and r['last'] and (self.pairs.get(e, {}).get('kind') == p['kind']) and (self.ctx.area_id(e) == area) and area]
            return len(peers) >= 2 and statistics.median(peers) - val >= cfg['peer_gap']
        return False

    def _judge(self, eff: str, p: dict[str, Any], run: dict[str, Any], now: float, execs: set[str]) -> None:
        cfg = kinds_cfg()[p['kind']]
        s, last = (run['samples'], run['last'])
        if last is None or not s:
            return
        season = self.ctx.season_now()
        wrong_season = not cfg['falls'] and season not in (None, 'heat')
        if self._ahu(eff, p) or (eff.startswith('climate.') and (wrong_season or self._learned(eff))):
            run['bad_since'] = None
            return
        if cfg['windows'] and self.ctx.opening_open(eff):
            run['samples'], run['bad_since'] = ([s[-1]], None)
            return
        if wrong_season:
            run['bad_since'] = None
            return
        if any((x[3] != s[-1][3] for x in s)):
            run['samples'], run['bad_since'] = ([s[-1]], None)
            return
        win = self._delta(s, now, cfg['window_h'])
        if win is None:
            return
        change, share = win
        sign = -1 if cfg['falls'] else 1
        failing = share >= min_share() and change * sign < cfg['min_change'] and self._room_left(eff, p, cfg, last[1], last[2])
        if failing:
            run['calm_since'] = None
            run['bad_since'] = run['bad_since'] if run['bad_since'] is not None else now
            if not run['alerted'] and now - run['bad_since'] >= (hold() - 1) * cfg['window_h'] * SECONDS_PER_HOUR and (run['said_at'] is None or now - run['said_at'] >= repeat_s()):
                run.update(alerted=True, since=run['bad_since'] - cfg['window_h'] * SECONDS_PER_HOUR, said_at=now)
                self.ctx.dirty()
            return
        run['bad_since'] = None
        if not run['alerted']:
            return
        recent = self._delta(s, now, cfg['clear_h'])
        if recent is not None and recent[0] * sign >= cfg['clear_change'] or (run['calm_since'] is not None and now - run['calm_since'] >= cfg['clear_h'] * SECONDS_PER_HOUR):
            run.update(alerted=False, since=None, calm_since=None)
            self.ctx.dirty()
        elif run['calm_since'] is None:
            run['calm_since'] = now

    def _ahu(self, eff: str, p: dict[str, Any] | None) -> bool:
        return self.ctx.air_handler(eff) or bool(p and self.ctx.air_handler(p['exec']))

    def _learned(self, eff: str) -> bool:
        return any((isinstance(d, ClimateEffect) and d.norm(eff, 'heat') is not None for d in self.ctx.detectors))
