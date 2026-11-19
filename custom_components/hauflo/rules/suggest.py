"""Rule suggestions sent to the staff and their answers."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.components import persistent_notification
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, callback
from .. import derived as dv, issues
from ..const import DOMAIN
from ..derived import SECONDS_PER_MINUTE
from ..guard import contained
from ..house import may_edit
from .const import LATER, SUGGEST_ACTION
from .spec import C
_LOGGER = logging.getLogger(__name__)

class RulesSuggestMixin:
    """Suggests rule actions and takes the answers."""

    def _auto_ok(self, rule: dict[str, Any], on: bool) -> bool:
        starts = (self.win.get(rule['id']) or {}).get('starts') or []
        if not rule.get('window') or not starts:
            return True
        st, key = (self.st(rule['id']), 'on' if on else 'off')
        if st['auto_at'].get(key) == starts[-1]:
            return False
        st['auto_at'] = {**st['auto_at'], key: starts[-1]}
        return True

    def _sug_acts(self, rule: dict[str, Any], on: bool) -> list[dict[str, Any]]:
        if on:
            return list(rule['actions'])
        return self._reverse(rule) if rule['back'] == 'reverse' else [] if rule['back'] == 'nothing' else list(rule['back'])

    def _needed(self, acts: list[dict[str, Any]]) -> bool:
        for a in acts:
            if a['do'] == 'notify':
                continue
            cur = self.season_now() if a['do'] == 'season' else self.current(a['entity_id'], a['do']) if 'entity_id' in a else None
            if (a['do'] != 'season' or cur is not None) and cur != self._wanted(a):
                return True
        return False

    async def _suggest(self, rule: dict[str, Any], on: bool) -> None:
        st = self.st(rule['id'])
        if not self._needed(self._sug_acts(rule, on)):
            if st['sug'] is not None:
                st['sug'] = None
                self._dismiss(rule)
            return
        st['sug'] = {'on': on, 'at': dv.now(), 'sent': None, 'snooze_until': 0.0}
        await self._suggest_send(rule)

    async def _suggest_due(self, rid: str) -> None:
        rule = self.rules.get(rid)
        st = self.st(rid) if rule else None
        sug = st['sug'] if st else None
        if rule is None or sug is None or (not rule['enabled']):
            return
        if sug['on'] != st['active'] or not self._needed(self._sug_acts(rule, sug['on'])):
            st['sug'] = None
            self._dismiss(rule)
            self._save_soon()
            return
        t = dv.now()
        if sug['snooze_until'] and t >= sug['snooze_until']:
            sug['snooze_until'], sug['sent'] = (0.0, None)
        if sug['sent'] is None and (not sug['snooze_until']):
            await self._suggest_send(rule)

    def suggestion(self, rid: str) -> dict[str, Any] | None:
        """The suggestion the family sees now (not snoozed, still wanted), or None."""
        rule = self.rules.get(rid)
        sug = self.st(rid)['sug'] if rule else None
        if rule is None or sug is None or (not rule['enabled']) or (sug['snooze_until'] > dv.now()):
            return None
        acts = self._sug_acts(rule, sug['on'])
        return {'on': sug['on'], 'actions': acts, 'sent': sug['sent']} if self._needed(acts) else None

    async def _suggest_send(self, rule: dict[str, Any]) -> None:
        rid = rule['id']
        st, t = (self.st(rid), dv.now())
        day = C('suggest', 'day_s')
        if self._quiet(rule):
            if not st['sug'].get('quiet'):
                st['sug']['quiet'] = True
                self._log(rid, 'quiet_hold', result='suggest')
                self._save_soon()
            return
        sent = [x for x in self._sug_sent.get(rid, []) if t - x < day]
        self._sug_sent[rid] = sent
        if len(sent) >= C('suggest', 'per_day'):
            return
        on = st['sug']['on']
        ru = self.ru
        if (hook := self.ask_text.get((rule.get('name_gen') or {}).get('recipe', ''))) is not None:
            title, message, tag = hook(rule, on, ru)
        else:
            title = self.display_name(rule, ru)
            message = self.say('rules_suggest_on' if on else 'rules_suggest_off', ru, why=self.explain(rule, ru).rstrip('.'))
            tag = f'{DOMAIN}_rule_suggest_{rid}'
        buttons = [{'action': f"{SUGGEST_ACTION}|{rid}|{('on' if on else 'off')}", 'title': self.say('rules_act_do', ru)}, {'action': f'{SUGGEST_ACTION}|{rid}|{LATER}', 'title': self.say('rules_act_later', ru)}]
        st['sug']['sent'] = t
        self._sug_sent[rid] = [*sent, t]
        self._log(rid, 'suggest', result='on' if on else 'off')
        self._logbook(rule, message)
        self._save_soon()
        if self.ask is not None:
            with contained(_LOGGER, 'Hauflo rule %s: suggestion not sent: %s', rid, err=True):
                await self.ask(title, message, buttons, tag)

    def _tag(self, rule: dict[str, Any]) -> str:
        if (hook := self.ask_text.get((rule.get('name_gen') or {}).get('recipe', ''))) is not None:
            *_, tag = hook(rule, True, self.ru)
            return tag
        return f"{DOMAIN}_rule_suggest_{rule['id']}"

    @callback
    def _dismiss(self, rule: dict[str, Any]) -> None:
        persistent_notification.async_dismiss(self.hass, self._tag(rule))

    async def answer_suggestion(self, rid: Any, what: str, who: str | None=None) -> bool:
        """«Сделать» runs the actions of the suggestion there is now (an old push runs nothing); «Не сейчас» snoozes it for the rule's snooze_days."""
        rule = self.rules.get(rid) if isinstance(rid, str) else None
        sug = self.st(rid)['sug'] if rule else None
        if rule is None or sug is None:
            return False
        st = self.st(rid)
        if what == LATER:
            sug['snooze_until'] = dv.now() + rule.get('snooze_days', C('suggest', 'snooze_days')) * C('suggest', 'day_s')
            self._log(rid, 'suggest_later', result=who)
            self._dismiss(rule)
            await self._save()
            self._refresh(rid)
            return True
        on = what == STATE_ON
        if what not in (STATE_ON, STATE_OFF) or sug['on'] != on or st['active'] != on or (self.suggestion(rid) is None):
            return False
        st['sug'] = None
        self._log(rid, 'suggest_done', result=who)
        self._dismiss(rule)
        await self._apply(rule, back=not on, why='suggest')
        if not on:
            st['restore'] = {}
        await self._save()
        self._refresh(rid)
        return True

    async def _on_action(self, event: Event) -> None:
        action = str(event.data.get('action') or '')
        if not action.startswith(SUGGEST_ACTION + '|'):
            return
        rid, _, what = action[len(SUGGEST_ACTION) + 1:].partition('|')
        user = await self.hass.auth.async_get_user(event.context.user_id) if event.context.user_id else None
        if not await may_edit(self.hass, user):
            return
        await self.answer_suggestion(rid, what, user.name if user else None)

    async def _hold_end(self, rule: dict[str, Any]) -> None:
        st = self.st(rule['id'])
        rate = st['hold_why'] == 'rate'
        st['hold_until'] = st['hold_why'] = None
        issues.clear_issue(self.hass, f"rule_rate_{rule['id']}")
        self._log(rule['id'], 'hold_end')
        if st['active']:
            await self._apply(rule, back=False, why='hold_end')
        elif rate:
            await self._apply(rule, back=True, why='hold_end')
            st['restore'] = {}

    async def _pending(self, rule: dict[str, Any], on: bool, v: float, t: float) -> None:
        st = self.st(rule['id'])
        need = (rule['for_min'] if on else rule['back_min']) * SECONDS_PER_MINUTE
        if st['pend'] is None:
            st['pend'] = t
            self._log(rule['id'], 'pending_on' if on else 'pending_off', value=v)
        if t - st['pend'] >= need:
            await self._go(rule, on, v, 'level')
            return
        self._wake(rule['id'], st['pend'] + need - t)
