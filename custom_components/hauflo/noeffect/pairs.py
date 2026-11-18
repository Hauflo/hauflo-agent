"""Pairs of an executor and its sensor: set, offered and found."""
from __future__ import annotations
from collections import defaultdict
import re
from typing import Any
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from ..insight.common import gone_after
from ..notify.levels import labelled
from .common import effect_kinds, exclude, integration, kinds_cfg, pairs_every, tail, text, words

class PairEffectPairsMixin:
    """Keeps the executor and sensor pairs."""

    def set_pair(self, effect: str, kind: str, executor: str) -> None:
        """The integrator's word: this executor is this effect's (`executor` "" — this effect has no executor to
        judge)."""
        if kind in kinds_cfg():
            self.manual[effect] = {'kind': kind, 'exec': executor}
            self._paired_at = -pairs_every()
            self.ctx.dirty()

    def set_kinds(self, kinds: dict[str, str]) -> None:
        """The deploy's map of thermostats' «воздух / пол» (the whole of it: one not in it is read by its words
        again)."""
        kinds = {e: k for e, k in kinds.items() if k in effect_kinds()}
        if kinds != self.kinds:
            self.kinds = kinds
            self._paired_at = -pairs_every()
            self.ctx.dirty()

    def _floor(self, st: State, floor_re: re.Pattern[str] | None) -> bool:
        k = self.kinds.get(st.entity_id)
        return k == 'floor' if k else bool(floor_re and floor_re.search(text(st)))

    def climate_kind(self, eid: str) -> str | None:
        """«floor» / «air» of a thermostat: the integrator's word, a floor pair's target, else its words
        (analytics.py loops)."""
        if eid in self.kinds:
            return self.kinds[eid]
        if any((p.get('kind') == 'floor' and p.get('target') == eid for p in self.pairs.values())):
            return 'floor'
        st = self.ctx.hass.states.get(eid)
        if st is None or st.domain != 'climate':
            return None
        floor_re = words(kinds_cfg()['floor']['target_words']) if 'floor' in kinds_cfg() else None
        return 'floor' if self._floor(st, floor_re) else 'air'

    def clear_pair(self, effect: str) -> None:
        if self.manual.pop(effect, None) is not None:
            self._paired_at = -pairs_every()
            self.ctx.dirty()

    def pairs_view(self) -> dict[str, Any]:
        """What the integrator sees and may refine: the pairs found, the effects left without an unambiguous
        executor."""
        who = self.ctx.who
        here = lambda eid: self.ctx.hass.states.get(eid) is not None
        pairs = []
        for e, p in self.pairs.items():
            if not here(e) or not here(p['exec']):
                continue
            m = e in self.manual
            st = self.ctx.hass.states.get(e)
            pairs.append({'effect': e, 'kind': p['kind'], 'exec': p['exec'], 'manual': m, **{'name': who(e)['name'], 'area': who(e)['area'], 'execName': who(p['exec'])['name'], 'why': 'manual' if m else self.why.get(e, 'single'), 'number': tail(st) if st else None}, **integration(self.ctx.hass, e)})
        unpaired = [{**u, 'name': who(u['effect'])['name'], 'area': who(u['effect'])['area'], **integration(self.ctx.hass, u['effect'])} for u in self.unpaired if here(u['effect'])]
        cands = {k: [{'id': s.entity_id, **who(s.entity_id), **integration(self.ctx.hass, s.entity_id)} for s in self._executors(k)] for k in kinds_cfg()}
        ignored = [{'effect': e, 'kind': m['kind'], 'name': who(e)['name'], 'area': who(e)['area']} for e, m in self.manual.items() if m['exec'] == '']
        return {'pairs': pairs, 'unpaired': unpaired, 'ignored': ignored, 'candidates': cands, 'offered': self._offered()}

    def _offered(self) -> list[dict[str, Any]]:
        who, taken = (self.ctx.who, {*self.pairs, *self.manual})
        floor_re = words(kinds_cfg()['floor']['target_words']) if 'floor' in kinds_cfg() else None
        out = []
        for s in self.ctx.hass.states.async_all('climate'):
            if s.entity_id in taken or not self.ctx.visible(s.entity_id) or self.ctx.owns(s.entity_id):
                continue
            kind = 'floor' if self._floor(s, floor_re) else 'air'
            if kind in kinds_cfg():
                out.append({'effect': s.entity_id, 'kind': kind, 'name': who(s.entity_id)['name'], 'area': who(s.entity_id)['area'], **integration(self.ctx.hass, s.entity_id)})
        return sorted(out, key=lambda o: (o['area'], o['name']))

    def _executors(self, kind: str) -> list[State]:
        cfg = kinds_cfg()[kind]
        if kind == 'air':
            found = self._states(['climate'])
        else:
            exe_re = words(cfg['exec_words'])
            found = [s for s in self._states(cfg['exec_domains']) if (exe_re is None or exe_re.search(text(s))) and (not exclude().search(text(s))) and (s.attributes.get('device_class') != 'problem')]
        return sorted(found, key=lambda s: text(s))

    def _states(self, domains: list[str]) -> list[State]:
        return [s for d in domains for s in self.ctx.hass.states.async_all(d) if self.ctx.visible(s.entity_id) and self._monitored(s.entity_id)]

    def _monitored(self, eid: str) -> bool:
        if self.ctx.owns(eid) or labelled(self.ctx.hass, eid):
            return True
        watched = self.ctx.watched()
        if not watched:
            return False
        if eid in watched:
            return True
        reg = er.async_get(self.ctx.hass)
        entry = reg.async_get(eid)
        return bool(entry and entry.device_id and any(((w := reg.async_get(x)) is not None and w.device_id == entry.device_id for x in watched)))

    def _find(self, kind: str) -> tuple[dict[str, str | None], list[dict[str, str]]]:
        cfg = kinds_cfg()[kind]
        eff_re, exe_re, tgt_re = (words(cfg['effect_words']), words(cfg['exec_words']), words(cfg['target_words']))
        if kind == 'air':
            own = [s for s in self._states(['climate']) if self.ctx.area_id(s.entity_id) is not None]
            self.why.update({s.entity_id: 'own' for s in own})
            return ({s.entity_id: s.entity_id for s in own}, [])
        effects = [s for s in self._states([cfg['effect_domain']]) if (not cfg['effect_class'] or s.attributes.get('device_class') == cfg['effect_class']) and (eff_re is None or eff_re.search(text(s))) and (not exclude().search(text(s)))]
        eff_ids = {s.entity_id for s in effects}
        execs = [s for s in self._states(cfg['exec_domains']) if s.entity_id not in eff_ids and (exe_re is None or exe_re.search(text(s))) and (not exclude().search(text(s))) and (s.attributes.get('device_class') != 'problem')]
        by_area: dict[str | None, dict[str, list[State]]] = defaultdict(lambda: {'e': [], 'x': []})
        for s in effects:
            by_area[self.ctx.area_id(s.entity_id)]['e'].append(s)
        for s in execs:
            by_area[self.ctx.area_id(s.entity_id)]['x'].append(s)
        found: dict[str, str | None] = {}
        left: list[dict[str, str]] = []
        for area, grp in by_area.items():
            e, x = (grp['e'], grp['x'])
            if not e or area is None:
                continue
            tails_e, tails_x = ([tail(s) for s in e], [tail(s) for s in x])
            if area is not None and len(e) == 1 and (len(x) == 1):
                found[e[0].entity_id] = x[0].entity_id
                self.why[e[0].entity_id] = 'single'
            elif area is not None and x and (None not in tails_e + tails_x) and (len(set(tails_e)) == len(e)) and (set(tails_e) == set(tails_x)) and (len(set(tails_x)) == len(x)):
                by_n = {tail(s): s.entity_id for s in x}
                for s in e:
                    found[s.entity_id] = by_n[tail(s)]
                    self.why[s.entity_id] = 'number'
            elif cfg['house_wide'] and len(execs) == 1:
                for s in e:
                    found[s.entity_id] = execs[0].entity_id
                    self.why[s.entity_id] = 'house'
            elif x:
                left.extend(({'effect': s.entity_id, 'kind': kind} for s in e))
        if kind == 'floor':
            self._targets = {}
            for s in effects:
                area = self.ctx.area_id(s.entity_id)
                cl = [c for c in self._states(['climate']) if area and self._floor(c, tgt_re) and (self.ctx.area_id(c.entity_id) == area)]
                if len(cl) == 1:
                    self._targets[s.entity_id] = cl[0].entity_id
        return (found, left)

    def refresh(self, now: float) -> None:
        if now - self._paired_at < pairs_every():
            return
        self._paired_at = now
        pairs: dict[str, dict[str, Any]] = {}
        self.unpaired = []
        self.why = {}
        self._targets: dict[str, str] = {}
        for kind in kinds_cfg():
            found, left = self._find(kind)
            targets = dict(self._targets) if kind == 'floor' else {}
            self.unpaired += [u for u in left if u['effect'] not in self.manual]
            for eff, exe in found.items():
                if exe:
                    pairs[eff] = {'kind': kind, 'exec': exe, 'target': exe if kind == 'air' else targets.get(eff)}
        floor_thermostats = {p['target'] for p in pairs.values() if p['kind'] == 'floor' and p['target']}
        floor_re = words(kinds_cfg()['floor']['target_words']) if 'floor' in kinds_cfg() else None
        for eff in [e for e, p in pairs.items() if p['kind'] == 'air']:
            if eff in floor_thermostats:
                del pairs[eff]
            elif (st := self.ctx.hass.states.get(eff)) is not None and self._floor(st, floor_re):
                pairs[eff]['kind'] = 'floor'
        for eff, m in self.manual.items():
            if m['exec'] == '':
                pairs.pop(eff, None)
            else:
                pairs[eff] = {'kind': m['kind'], 'exec': m['exec'], 'target': pairs.get(eff, {}).get('target')}
        for eff in [e for e in self.runs if e not in pairs and (not self.runs[e]['alerted'])]:
            if self.runs[eff]['said_at'] is None:
                del self.runs[eff]
        self.pairs = pairs
        self._involved = {i for e, p in pairs.items() for i in (e, p['exec'], p['target']) if i}
        for eff, p in pairs.items():
            run = self.runs.setdefault(eff, self._new())
            sv = self.saved.pop(eff, None)
            if sv and sv['kind'] == p['kind']:
                run.update(alerted=True, since=sv['since'], said_at=sv['said_at'] if sv['said_at'] is not None else run['said_at'])
        if now - self.ctx.started > gone_after():
            self.saved.clear()
