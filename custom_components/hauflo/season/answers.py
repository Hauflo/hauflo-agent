"""Season facts, answers and applying a switch."""
from __future__ import annotations
import logging
import time
from typing import Any
from homeassistant.components import persistent_notification
from homeassistant.core import Event, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from .. import derived as dv
from ..house import may_edit
from ..notify.common import label
from .common import ACTION, HEAT, LATER, MODES, NOTIFY_TAG, SEASON_RULE, SIGNAL, clean_config, climates, defaults, heat_cool_address, log_max, sensors, tick
_LOGGER = logging.getLogger(__name__)

class SeasonAnswersMixin:
    """Answers the cloud and applies a chosen season."""

    def facts(self) -> dict[str, Any]:
        """For the entities: the recommendation and why, the season now, the way, the street's daily means."""
        rules, rule = self._rule()
        to, why = self.recommendation()
        win_ = rules.win.get(SEASON_RULE) or {} if rule is not None else {}
        starts = win_.get('starts') or []
        vals = win_.get('values') or []
        means = {dt_util.as_local(dt_util.utc_from_timestamp(t)).date().isoformat(): v for t, v in zip(starts, vals, strict=False) if isinstance(t, (int, float))}
        w = (rule or {}).get('window') or {}
        return {'recommended': to, 'reason': why, 'current': self.current(), 'auto': self.auto_on(), 'sensor': win_.get('entity_id') or ((rule or {}).get('input') or {}).get('entity_id'), 'daily_means': means, 'below': (rule or {}).get('x'), 'above': float(rule['x']) + float(rule.get('y', 0.0)) if rule else None, 'days': w.get('n'), 'days_back': w.get('n_back'), 'auto_switched': self.auto_day, 'auto_checked': self.auto_checked}

    @callback
    def changed(self, force: bool=False) -> None:
        """Tell the entities when what they show changed (a tick looks; a switch / setting tells at once)."""
        f = self.facts()
        key = (f['recommended'], f['reason'], f['current'], f['auto'], tuple(f['daily_means'].items()), f['auto_switched'])
        if force or key != self._facts:
            self._facts = key
            async_dispatcher_send(self.hass, SIGNAL)

    async def answer(self) -> dict[str, Any]:
        items = self.chosen()
        return {'season': self.current(), 'chosen': self.season, 'at': self.at, 'labels': self.config['labels'], 'auto': self.auto_on(), 'suggestion': self.suggestion(), 'acs': [{'entity_id': s.entity_id, 'name': label(self.hass, s.entity_id, s), 'state': s.state} for s in items]}

    def setup_answer(self) -> dict[str, Any]:
        """For Hauflo's setting: what the agent holds, the house's climates and sensors, the suggestion's rule."""
        found = dv.outdoor(self.hass)
        rules, rule = self._rule()
        sug = self.suggestion()
        st = rules.st(SEASON_RULE) if rule is not None else {}
        if rule is None:
            status = 'no_rule' if self.migrated else 'off' if not self.config['suggest']['enabled'] else 'no_sensor'
        else:
            status = 'off' if not rule['enabled'] else 'no_data' if st.get('no_data') else 'suggest' if sug else 'ok'
        snoozed = (st.get('sug') or {}).get('snooze_until') or None
        return {'config': self.config, 'defaults': defaults(), 'season': self.current(), 'chosen': self.season, 'climates': climates(self.hass, set(self.config['exclude']), set(self.config['include']), self.owns, self.legacy), 'sensors': sensors(self.hass), 'auto_sensor': found['entity_id'] if found else None, 'suggest': {'status': status, 'to': sug['to'] if sug else None, 'sensor': rule['input']['entity_id'] if rule else None, 'means': (rules.win.get(SEASON_RULE) or {}).get('values') or [] if rule else [], 'snoozed_until': snoozed, 'rule': rules.rule_out(SEASON_RULE, False) if rule else None, 'pending': self.pending, 'recommended': self.recommendation()[0], 'reason': self.recommendation()[1]}, 'auto': self.auto_on(), 'auto_switched': self.auto_day, 'auto_checked': self.auto_checked, 'last_resend': self.last_resend, 'log': self.log[-log_max():]}

    async def async_set_config(self, raw: Any) -> dict[str, Any]:
        prev = self.config.get('suggest')
        prev_include = self.config['include']
        prev_auto = self.config.get('auto')
        self.config, dropped = clean_config(raw)
        if isinstance(raw, dict) and 'include' in raw:
            self.legacy = False
        else:
            self.config['include'] = prev_include
        sent = isinstance(raw, dict) and isinstance(raw.get('suggest'), dict)
        if not sent and prev:
            self.config['suggest'] = prev
        if not (isinstance(raw, dict) and isinstance(raw.get('auto'), bool)):
            self.config['auto'] = prev_auto
        await self._save()
        if sent and self.config['suggest'] != self.suggest_applied:
            await self.async_sync_rule(force=self.migrated)
        if isinstance(raw, dict) and isinstance(raw.get('auto'), bool):
            await self.set_auto(raw['auto'], 'hauflo')
        return {**self.setup_answer(), 'dropped': dropped}

    async def apply(self, mode: str, user: str | None) -> dict[str, Any]:
        hass = self.hass
        changed: list[str] = []
        sent: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        addresses: set[str] = set()
        for s in self.chosen():
            if s.state == mode:
                continue
            try:
                if s.state != 'off':
                    await hass.services.async_call('climate', 'set_hvac_mode', {'entity_id': s.entity_id, 'hvac_mode': mode}, blocking=True)
                    changed.append(s.entity_id)
                elif (ga := heat_cool_address(hass, s.entity_id)) is not None:
                    if ga not in addresses:
                        addresses.add(ga)
                        await hass.services.async_call('knx', 'send', {'address': ga, 'payload': 1 if mode == HEAT else 0}, blocking=True)
                    sent.append(s.entity_id)
                else:
                    skipped.append(s.entity_id)
            except Exception as err:
                _LOGGER.warning('Hauflo season: %s %s failed: %s', mode, s.entity_id, err)
                failed.append(s.entity_id)
        self.season, self.at, self.by = (mode, time.time(), user)
        self.last_resend = time.time()
        await self._save()
        if self.suggestion() is None:
            self._dismiss()
        self.changed(force=True)
        return {'season': mode, 'changed': changed, 'sent': sent, 'skipped': skipped, 'failed': failed}

    async def resend(self, why: str) -> dict[str, Any] | None:
        """The chosen season again — never one guessed, never a climate switched on. A KNX UI climate: its heat/cool
        address (once per address, whether it runs or is off — the address doesn't switch it on); another one: set only
        while it runs in the other mode."""
        mode = self.season
        if mode not in MODES or not self.config['resend_s']:
            return None
        hass = self.hass
        addresses: set[str] = set()
        sent: list[str] = []
        changed: list[str] = []
        failed: list[str] = []
        for s in self.chosen():
            try:
                if (ga := heat_cool_address(hass, s.entity_id)) is not None:
                    if ga not in addresses:
                        addresses.add(ga)
                        await hass.services.async_call('knx', 'send', {'address': ga, 'payload': 1 if mode == HEAT else 0}, blocking=True)
                    sent.append(s.entity_id)
                elif s.state in MODES and s.state != mode:
                    await hass.services.async_call('climate', 'set_hvac_mode', {'entity_id': s.entity_id, 'hvac_mode': mode}, blocking=True)
                    changed.append(s.entity_id)
            except Exception as err:
                _LOGGER.warning('Hauflo season resend: %s failed: %s', s.entity_id, err)
                failed.append(s.entity_id)
        self.last_resend = time.time()
        self.store.async_delay_save(self.data, tick())
        entry = {'at': self.last_resend, 'why': why, 'season': mode, 'addresses': sorted(addresses), 'changed': changed, 'failed': failed}
        self.log = [*self.log[-(log_max() - 1):], entry]
        _LOGGER.debug('Hauflo season resend (%s): %s', why, entry)
        return entry

    @callback
    def _dismiss(self) -> None:
        persistent_notification.async_dismiss(self.hass, NOTIFY_TAG)

    async def snooze(self) -> None:
        rules, rule = self._rule()
        if rule is not None:
            await rules.answer_suggestion(SEASON_RULE, LATER)
        self._dismiss()

    async def _on_action(self, event: Event) -> None:
        action = str(event.data.get('action') or '')
        if not action.startswith(ACTION + '|'):
            return
        what = action.split('|', 1)[1]
        user = await self.hass.auth.async_get_user(event.context.user_id) if event.context.user_id else None
        if not await may_edit(self.hass, user):
            return
        if what == LATER:
            await self.snooze()
        elif what in MODES and (sug := self.suggestion()) and (sug['to'] == what):
            rules, _rule = self._rule()
            await rules.answer_suggestion(SEASON_RULE, 'on' if rules.st(SEASON_RULE)['sug']['on'] else 'off', user.name if user else None)
