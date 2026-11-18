"""Turns a habit into a suggested automation rule."""
from __future__ import annotations
import hashlib
import logging
import statistics
from typing import Any
from ..automations.common import Invalid
from .common import DAY, DAY_NAMES, t_cfg, wrap
_LOGGER = logging.getLogger(__name__)

class HabitsSuggestMixin:
    """Suggests rules from the habits."""

    def suggestions(self, auto: Any, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Ready rules for the card's «Предложения»: merged by room, not already a rule / dismissed, valid."""
        if not self.data['enabled']:
            return []
        found = self._mined if self._mined is not None else self.mine()
        found = [f for f in found if auto.pickable(f['entity_id'])]
        rules = [r for r in data.get('rules', {}).values() if isinstance(r, dict)]
        done = {r.get('from') for r in rules}
        out: list[dict[str, Any]] = []
        for group in self._groups(found):
            rule = self._rule(auto, group)
            if rule is None:
                continue
            key = 'habit_' + hashlib.sha1('|'.join([*sorted((f['entity_id'] for f in group)), group[0]['act'], group[0]['class'], group[0]['kind']]).encode()).hexdigest()[:10]
            if key in self.data['dismissed'] or key in done or self._ruled(auto, rules, group):
                continue
            try:
                clean = auto.validate({**rule, 'from': key})
            except Invalid as err:
                _LOGGER.debug('Hauflo habits: %s dropped (%s)', key, err)
                continue
            share = min((f['share'] for f in group))
            out.append({'key': key, 'rule': clean, 'class': group[0]['class'], 'hits': min((f['hits'] for f in group)), 'of': group[0]['of'], 'share': round(share, 2), 'entities': sorted((f['entity_id'] for f in group)), 'area': self.area(group[0]['entity_id'])})
        out.sort(key=lambda s: (-s['share'], -s['hits']))
        return out[:int(t_cfg()['max_suggestions'])]

    def _groups(self, found: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        groups: list[list[dict[str, Any]]] = []
        for f in sorted(found, key=lambda f: (-f['share'], f['entity_id'])):
            area = self.area(f['entity_id'])
            for g in groups:
                h = g[0]
                if area and self.area(h['entity_id']) == area and (h['entity_id'].split('.')[0] == f['entity_id'].split('.')[0]) and (h['act'] == f['act']) and (h['class'] == f['class']) and (h['kind'] == f['kind']) and (abs(wrap(h['minute'] - f['minute'])) <= t_cfg()['window_min']) and (len(g) < 20):
                    g.append(f)
                    break
            else:
                groups.append([f])
        return groups

    @staticmethod
    def _round(value: float) -> int:
        return int(round(value / t_cfg()['round_min']) * t_cfg()['round_min'])

    def _rule(self, auto: Any, group: list[dict[str, Any]]) -> dict[str, Any] | None:
        head, eids = (group[0], sorted((f['entity_id'] for f in group)))
        domain, act = (head['entity_id'].split('.')[0], head['act'])
        if head['kind'] == 'time':
            minute = self._round(statistics.median([f['minute'] for f in group])) % DAY
            when: dict[str, Any] = {'event': 'time', 'at': f'{minute // 60:02d}:{minute % 60:02d}'}
        else:
            when = {'event': head['kind'], 'offset': self._round(statistics.median([f['offset'] for f in group]))}
        do = act if not act.startswith('pos') else 'position'
        levels = [f['level'] for f in group if f['level'] is not None]
        level = statistics.median(levels) if levels else None
        area = self.area(head['entity_id'])
        actions: list[dict[str, Any]] = []
        if area and len(eids) > 1 and (domain == 'light') and (act == 'off') and (set(auto.room_lights(area)) <= set(eids)):
            actions = [{'do': 'room_off', 'area_id': area}]
        elif area and len(eids) > 1 and (domain == 'cover') and (set(auto.room_covers(area, do == 'position')) <= set(eids)):
            actions = [{'do': 'room_covers', 'area_id': area, 'cover': do, **({'value': round(level)} if do == 'position' and level is not None else {})}]
        else:
            for f in sorted(group, key=lambda f: f['entity_id']):
                a: dict[str, Any] = {'entity_id': f['entity_id'], 'do': do}
                v = f['level']
                if do == 'position' and v is not None:
                    a['value'] = round(v)
                elif domain == 'light' and do == 'on' and (v is not None) and self._dims(f['entity_id']):
                    a['value'] = max(1, min(100, int(round(v / t_cfg()['brightness_step']) * t_cfg()['brightness_step']) or 1))
                elif do == 'temp':
                    if v is None:
                        return None
                    a['value'] = v
                actions.append(a)
        return {'when': when, 'days': [DAY_NAMES[d] for d in head['days']], 'actions': actions, 'enabled': True, 'name': ''}

    def _dims(self, eid: str) -> bool:
        st = self.hass.states.get(eid)
        return st is not None and any((m != 'onoff' for m in st.attributes.get('supported_color_modes') or []))

    def _ruled(self, auto: Any, rules: list[dict[str, Any]], group: list[dict[str, Any]]) -> bool:
        head = group[0]
        want = 'position' if head['act'].startswith('pos') else head['act']
        eids = {f['entity_id'] for f in group}
        for r in rules:
            w = r.get('when') or {}
            if head['kind'] == 'time':
                times = w.get('times') or [w.get('at')]
                if w.get('event') != 'time' or not any((isinstance(at, str) and ':' in at and (abs(wrap(int(at[:2]) * 60 + int(at[3:5]) - head['minute'])) <= t_cfg()['rule_near_min']) for at in times)):
                    continue
            elif w.get('event') != head['kind']:
                continue
            for a in r.get('actions') or []:
                if self._does(auto, a) & {(e, want) for e in eids}:
                    return True
        return False

    @staticmethod
    def _does(auto: Any, a: dict[str, Any]) -> set[tuple[str, str]]:
        return auto.covers(a) if isinstance(a, dict) else set()
