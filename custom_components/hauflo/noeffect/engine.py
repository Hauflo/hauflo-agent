"""The no-effect detector: its state, inputs and items."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any
from ..derived import SECONDS_PER_HOUR, num
from ..insight.common import iso, round2
from ..insight.detector import Detector
if TYPE_CHECKING:
    from ..insight.engine import Insights
from .common import KIND, effect_kinds, kinds_cfg, pairs_every
from .judge import PairEffectJudgeMixin
from .pairs import PairEffectPairsMixin

class PairEffect(PairEffectPairsMixin, PairEffectJudgeMixin, Detector):
    name = 'noeffect'
    domains = None
    KINDS = (KIND,)
    DEV = ('limit', 'change')

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        self.manual: dict[str, dict[str, str]] = {}
        self.kinds: dict[str, str] = {}
        self.saved: dict[str, dict[str, Any]] = {}
        self.pairs: dict[str, dict[str, Any]] = {}
        self.unpaired: list[dict[str, str]] = []
        self.why: dict[str, str] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self._paired_at = -pairs_every()
        self._involved: set[str] = set()

    def load(self, data: dict[str, Any]) -> None:
        for eff, m in (data.get('manual') or {}).items():
            if isinstance(m, dict) and m.get('kind') in kinds_cfg() and isinstance(m.get('exec'), str):
                self.manual[str(eff)] = {'kind': m['kind'], 'exec': m['exec']}
        for eff, r in (data.get('open') or {}).items():
            if isinstance(r, dict) and r.get('kind') in kinds_cfg() and (num(r.get('since')) is not None):
                self.saved[str(eff)] = {'kind': r['kind'], 'exec': str(r.get('exec') or ''), 'since': float(r['since']), 'said_at': num(r.get('said_at'))}
        for eff, said in (data.get('said') or {}).items():
            if num(said) is not None:
                self.runs.setdefault(str(eff), self._new())['said_at'] = float(said)
        self.kinds = {str(e): k for e, k in (data.get('kinds') or {}).items() if k in effect_kinds()}

    def dump(self) -> dict[str, Any]:
        opened = {e: {'kind': self.pairs[e]['kind'], 'exec': self.pairs[e]['exec'], 'since': r['since'], 'said_at': r['said_at']} for e, r in self.runs.items() if r['alerted'] and e in self.pairs}
        return {'manual': self.manual, 'open': {**self.saved, **opened}, 'said': {e: r['said_at'] for e, r in self.runs.items() if r['said_at'] is not None}, 'kinds': self.kinds}

    @staticmethod
    def _new() -> dict[str, Any]:
        return {'samples': [], 'bad_since': None, 'calm_since': None, 'alerted': False, 'since': None, 'said_at': None, 'last': None}

    def inputs(self, kind: str, eid: str) -> list[dict[str, Any]]:
        """The effect (a room's temperature / CO₂ / humidity), the executor that should move it, the room's windows."""
        p = self.pairs.get(eid) or self.saved.get(eid) or {}
        cfg = kinds_cfg().get(p.get('kind', '')) or {}
        return [{'role': 'effect', 'entityId': eid}, *([{'role': 'exec', 'entityId': p['exec']}] if p.get('exec') else []), *({'role': 'window', 'entityId': w} for w in (self.ctx.windows_of(eid) if cfg.get('windows') else [])), {'role': 'season'}]

    def keys(self) -> list[tuple[str, str]]:
        return [(KIND, eff) for eff in dict.fromkeys([*self.pairs, *self.saved])]

    def excluded(self, kind: str, eid: str) -> str | None:
        p = self.pairs.get(eid)
        if self._ahu(eid, p):
            return 'air_handler'
        cfg = kinds_cfg().get((p or self.saved.get(eid) or {}).get('kind', ''))
        if cfg and (not cfg['falls']) and (self.ctx.season_now() not in (None, 'heat')):
            return 'season'
        return super().excluded(kind, eid)

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        cfg = kinds_cfg().get((self.pairs.get(eid) or self.saved.get(eid) or {}).get('kind', ''))
        return {'ready': {'norm': cfg['min_change'] if cfg else None, 'unit': cfg['unit'] if cfg else '', 'n': len((self.runs.get(eid) or {}).get('samples') or [])}}

    def reset(self, eid: str, kind: str) -> None:
        self.saved.pop(eid, None)
        if eid in self.runs:
            self.runs[eid] = self._new()

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for eff, sv in self.saved.items():
            if self._ahu(eff, sv):
                continue
            xs = self.ctx.hass.states.get(sv['exec'])
            out.append({'key': f'{KIND}|{eff}', 'kind': KIND, 'role': sv['kind'], 'entityId': eff, **self.ctx.who(eff), 'exec': (xs.attributes.get('friendly_name') if xs else None) or sv['exec'], 'since': iso(sv['since']), 'hours': round2((now - sv['since']) / SECONDS_PER_HOUR), 'flatH': kinds_cfg()[sv['kind']]['window_h'], 'limit': kinds_cfg()[sv['kind']]['min_change']})
        for eff, run in self.runs.items():
            p = self.pairs.get(eff)
            if not run['alerted'] or p is None or run['since'] is None or self._ahu(eff, p):
                continue
            cfg = kinds_cfg()[p['kind']]
            xs = self.ctx.hass.states.get(p['exec'])
            win = self._delta(run['samples'], now, cfg['window_h'])
            last = run['last']
            out.append({'key': f'{KIND}|{eff}', 'kind': KIND, 'role': p['kind'], 'entityId': eff, **self.ctx.who(eff), 'exec': (xs.attributes.get('friendly_name') if xs else None) or p['exec'], 'since': iso(run['since']), 'hours': round2((now - run['since']) / SECONDS_PER_HOUR), 'flatH': cfg['window_h'], 'limit': cfg['min_change'], **({'change': round2(win[0])} if win else {}), **({'value': round2(last[1])} if last else {})})
        return out
NOEFFECT: tuple[type[Detector], ...] = (PairEffect,)
