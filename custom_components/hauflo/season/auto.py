"""The season's automatic switch and its recommendation."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.util import dt as dt_util
from .. import derived as dv
from ..guard import contained
from ..rules.schedule import window_off
from .common import AUTO_BY, HEAT, MODES, NO_DATA, NONE, NOTIFY_TAG, RULE_SEASON, SEASON_RULE, auto_hour, mode_outputs, rule_body
_LOGGER = logging.getLogger(__name__)

class SeasonAutoMixin:
    """Recommends and, when allowed, switches the season itself."""

    def rule_season(self) -> str:
        """For the value rules' «season» condition: winter unless summer was chosen."""
        return RULE_SEASON.get(self.season or HEAT, RULE_SEASON[HEAT])

    def _rule(self) -> tuple[Any, dict[str, Any] | None]:
        rules = self.rules_of()
        return (rules, rules.rules.get(SEASON_RULE) if rules is not None else None)

    def _to(self, rule: dict[str, Any], on: bool) -> tuple[str | None, int, float]:
        act = next((a for a in rule['actions'] if a['do'] == 'season'), None)
        w = rule.get('window') or {}
        if act is None:
            return (None, 0, 0.0)
        to = act['value'] if on else MODES[1 - MODES.index(act['value'])]
        if on:
            return (to, int(w.get('n', 0)), float(rule.get('x', 0.0)))
        return (to, int(w.get('n_back', w.get('n', 0))), float(rule.get('x', 0.0)) + float(rule.get('y', 0.0)))

    def suggestion(self) -> dict[str, Any] | None:
        """The suggestion the family sees now (not snoozed, not done already), or None."""
        rules, rule = self._rule()
        sug = rules.suggestion(SEASON_RULE) if rule is not None else None
        if sug is None:
            return None
        to, days, threshold = self._to(rule, sug['on'])
        return {'to': to, 'days': days, 'threshold': threshold} if to in MODES else None

    def _ask_text(self, rule: dict[str, Any], on: bool, _ru: bool) -> tuple[str, str, str]:
        lang = self._lang()
        mode, days, threshold = self._to(rule, on)
        to = self.say(f"season_to_{self.config['labels']}_{mode}", lang)
        why = self.say(f'season_suggest_why_{mode}', lang, days=days, threshold=f'{threshold:g}')
        return (self.say('season_suggest_title', lang, to=to), self.say('season_suggest_message', lang, to=to, why=why), NOTIFY_TAG)

    async def async_sync_rule(self, force: bool=False) -> None:
        """`suggest` → the value rule: once (the migration), and again when Hauflo sends other settings (`force`)."""
        rules, rule = self._rule()
        if rules is None or (self.migrated and (not force)):
            return
        s = self.config['suggest']
        if rule is not None and (not force):
            self.migrated, self.suggest_applied = (True, dict(s))
            await self._save()
            await self._sync_auto()
            return
        body = rule_body(s)
        if isinstance(self.config.get('auto'), bool):
            body['outputs'] = mode_outputs(self.config['auto'])
        if rule is not None:
            body = {**rule, **{k: body[k] for k in ('enabled', 'input', 'x', 'y', 'window', 'snooze_days')}, 'name': rule['name']}
        try:
            try:
                await rules.save(body, client=False, user_id=None)
            except dv.Invalid as err:
                if err.code != 'bad_hysteresis':
                    raise
                await rules.save({**body, 'y': 0.0}, client=False, user_id=None)
        except dv.Invalid as err:
            self.pending = err.code
            return
        self.migrated, self.suggest_applied, self.pending = (True, dict(s), None)
        await self._save()

    def auto_on(self) -> bool:
        """The house switches by itself: the season rule acts (its outputs) — before the rule exists, Hauflo's setting."""
        _rules, rule = self._rule()
        if rule is not None:
            return bool(rule.get('outputs', {}).get('auto'))
        return self.config.get('auto') is True

    async def _sync_auto(self) -> None:
        rules, rule = self._rule()
        want = self.config.get('auto')
        if rule is None or not isinstance(want, bool) or bool(rule.get('outputs', {}).get('auto')) == want:
            return
        await rules.set_outputs(SEASON_RULE, mode_outputs(want))

    async def set_auto(self, on: bool, why: str) -> None:
        """«Переключать автоматически» on / off (Hauflo's setting or the HA switch): the rule keeps its state; turned on, the day's look runs now (still in the morning only, still once a day)."""
        self.config['auto'] = on
        await self._save()
        await self._sync_auto()
        _LOGGER.info('Hauflo season: automatic switch %s (%s)', 'on' if on else 'off', why)
        if on:
            await self.auto_check(why)
        self.changed(force=True)

    def recommendation(self) -> tuple[str, str]:
        """(heat / cool / none / no_data, why) — the street rule's verdict with its hysteresis: winter while the rule is ACTIVE (n cold days in a row, held until n_back warm ones), summer after n_back warm days in a row, anything between — «без изменений»."""
        rules, rule = self._rule()
        if rule is None:
            return (NO_DATA, 'no_sensor' if self.config['suggest']['enabled'] else 'off')
        if not rule['enabled']:
            return (NONE, 'off')
        st = rules.st(SEASON_RULE)
        if st.get('no_data'):
            return (NO_DATA, 'no_data')
        w = rule['window']
        vals = (rules.win.get(SEASON_RULE) or {}).get('values') or []
        if st['active']:
            return (self._to(rule, True)[0] or NONE, 'cold')
        back = vals[-int(w.get('n_back', w['n'])):]
        if back and len(back) == int(w.get('n_back', w['n'])) and all((v is not None and window_off(rule, v) for v in back)):
            return (self._to(rule, False)[0] or NONE, 'warm')
        return (NONE, 'between')

    def _means_today(self) -> bool:
        rules, rule = self._rule()
        at = (rules.win.get(SEASON_RULE) or {}).get('at') if rule is not None else None
        return isinstance(at, (int, float)) and dt_util.as_local(dt_util.utc_from_timestamp(at)).date() == dt_util.now().date()

    async def _auto_daily(self) -> None:
        now = dt_util.now()
        today = now.date().isoformat()
        if not self.auto_on() or self.auto_checked == today or now.hour < auto_hour() or (not self._means_today()):
            return
        self.auto_checked = today
        await self._save()
        await self.auto_check('daily')

    async def auto_check(self, why: str) -> str:
        """The automatic switch itself — what it did, said back: ok, or why not (off / none / no_data / already / today / later)."""
        if not self.auto_on():
            return 'off'
        to, _why = self.recommendation()
        if to not in MODES:
            return to
        if to == self.current():
            return 'already'
        now = dt_util.now()
        today = now.date().isoformat()
        if self.auto_day == today:
            return 'today'
        if now.hour < auto_hour():
            return 'later'
        self.auto_day = today
        result = await self.apply(to, AUTO_BY)
        _LOGGER.info('Hauflo season: switched to %s automatically (%s): %s', to, why, result)
        await self._notify_auto(to)
        return 'ok'

    async def _notify_auto(self, to: str) -> None:
        engine = self.notify_of()
        if engine is None:
            return
        lang = self._lang()
        _rules, rule = self._rule()
        _mode, days, threshold = self._to(rule, to == self._to(rule, True)[0]) if rule is not None else (to, 0, 0.0)
        name = self.say(f"season_to_{self.config['labels']}_{to}", lang)
        why = self.say(f'season_suggest_why_{to}', lang, days=days, threshold=f'{threshold:g}')
        with contained(_LOGGER, 'Hauflo season: the automatic switch not announced'):
            await engine.async_tell(self.say('season_auto_title', lang, to=name), self.say('season_auto_message', lang, to=name, why=why), None, tag=NOTIFY_TAG)
