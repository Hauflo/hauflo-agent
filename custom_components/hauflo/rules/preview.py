"""Previews what a rule would do now."""
from __future__ import annotations
import logging
from typing import Any
from .. import derived as dv, window as win
from ..derived import Invalid
from ..guard import contained
from .actions import check_house, clean_rule
from .answer import fmt
from .conditions import has_now, outputs
from .const import IS
from .schedule import in_row, past_on
from .spec import scheduled
_LOGGER = logging.getLogger(__name__)

class RulesPreviewMixin:
    """Previews rules before they are saved."""

    async def preview(self, raw: Any, *, client: bool, tunnel: bool=False) -> dict[str, Any]:
        """A dry run: the rule checked as a save would, and what it would do now — nothing written. Through the tunnel
        only what get_states would show is read, and «дома / никого» is not asked."""
        try:
            rule = clean_rule(raw, self._env(client))
            if tunnel:
                self._cloud_check(rule)
            check_house(rule, [r for k, r in self.rules.items() if k != rule['id']], self.derived_sources)
        except Invalid as err:
            return {'ok': False, **self.refusal(err)}
        live = {**rule, 'only_if': [c for c in rule['only_if'] if c['type'] != 'presence']} if tunnel else rule
        if rule.get('window'):
            return await self._preview_window(rule, live)
        if scheduled(rule):
            return self._preview_at(rule, live)
        v, bad = self.read(rule)
        ok, _cond = self.conditions(live)
        if bad:
            now = 'fault'
        elif not ok:
            now = 'waiting'
        elif past_on(rule, v):
            now = 'wait' if rule['for_min'] else 'now'
        else:
            now = 'normal_for' if rule['for_min'] else 'normal'
        ph = {'minutes': fmt(rule['for_min'], None)}

        def value(ru: bool) -> str:
            return self.value_word(rule['input'], v, ru) if rule['when'] == IS else fmt(v, rule['input'].get('unit'))
        acts = self._preview_actions(rule)
        return {'ok': True, 'rule': rule, 'value': v, 'unit': rule['input'].get('unit'), 'fault': bad, 'only_if': ok, 'now': now, 'actions': acts, 'why': self.say(f'rules_preview_{now}', True, **ph, value=value(True), threshold=self.threshold(rule, True)), 'why_en': self.say(f'rules_preview_{now}', False, **ph, value=value(False), threshold=self.threshold(rule, False)), **self.hint(rule['purpose'])}

    def _preview_actions(self, rule: dict[str, Any]) -> list[dict[str, Any]]:
        acts = []
        for a in rule['actions']:
            if not has_now(a):
                acts.append(dict(a))
                continue
            cur = self.current(a['entity_id'], a['do']) if 'entity_id' in a else self.season_now() if a['do'] == 'season' else None
            acts.append({**a, 'current': cur, 'already': 'entity_id' in a and cur is not None and (cur == self._wanted(a))})
        return acts

    def _preview_at(self, rule: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
        nxt = self.next_at(rule, dv.now())
        ok, _cond = self.conditions(live)
        now = 'schedule' if nxt is not None else 'no_next'
        return {'ok': True, 'rule': rule, 'value': None, 'unit': None, 'fault': None, 'only_if': ok, 'now': now, 'next_at': nxt, 'actions': self._preview_actions(rule), 'why': self.say(f'rules_preview_{now}', True, next=self._when_text(nxt)), 'why_en': self.say(f'rules_preview_{now}', False, next=self._when_text(nxt)), **self.hint(rule['purpose'])}

    async def _preview_window(self, rule: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
        w = rule['window']
        vals, starts, bad = ([], [], 'no_statistics')
        with contained(_LOGGER, 'Hauflo rule %s: window preview not read from the recorder', rule.get('id')):
            vals, starts, bad = await win.values(self.hass, rule['input']['entity_id'], rule['input'].get('attribute'), w)
        last = vals[-w['n']:]
        ok, _cond = self.conditions(live)
        held = in_row(rule, vals, past_on)
        if len(last) < w['n'] or any((v is None for v in last)):
            now = 'window_no_data'
        elif not ok:
            now = 'waiting'
        else:
            now = 'window_now' if held >= w['n'] else 'window_normal'
        unit = rule['input'].get('unit')
        ph = {'stat': self.window_words(rule, True), 'n': w['n'], 'held': held, 'threshold': self.threshold(rule, True), 'value': fmt(vals[-1] if vals else None, unit)}
        ph_en = {**ph, 'stat': self.window_words(rule, False), 'threshold': self.threshold(rule, False)}
        return {'ok': True, 'rule': rule, 'value': vals[-1] if vals else None, 'unit': unit, 'fault': bad, 'only_if': ok, 'now': now, 'values': vals, 'starts': starts, 'periods': held, 'outputs': outputs(rule), 'actions': [{**a, 'current': self.season_now() if a['do'] == 'season' else self.current(a['entity_id'], a['do']) if 'entity_id' in a else None} if has_now(a) else dict(a) for a in rule['actions']], 'why': self.say(f'rules_preview_{now}', True, **ph), 'why_en': self.say(f'rules_preview_{now}', False, **ph_en), **self.hint(rule['purpose'])}
