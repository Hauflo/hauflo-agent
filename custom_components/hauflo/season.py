from __future__ import annotations
import logging
import re
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, CoreState, Event, HomeAssistant, callback
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, translation
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util
from . import derived as dv
from .const import DOMAIN, site_ru
from .device import device_info
from .house import may_edit, refuse
from .storage import HaufloStore
from .rules import window_off
from .tuning import TUNING
from .notify import label
_LOGGER = logging.getLogger(__name__)
STORE_KEY = 'hauflo.season'
HEAT, COOL = ('heat', 'cool')
MODES = (HEAT, COOL)
RULE_SEASON = {HEAT: 'winter', COOL: 'summer'}
LABELS = ('season', 'mode')
LANGS = ('ru', 'en')
_GONE = ('unavailable', 'unknown')
_ENTITY_RE = re.compile('^[a-z_]+\\.[a-z0-9_]+$')
_T = TUNING['season']
RESEND_S = _T['resend_s']
RESEND_LO, RESEND_HI = _T['resend_bounds_s']
TICK = _T['tick_s']
START_DELAY = _T['start_delay_s']
BELOW, ABOVE, DAYS, SNOOZE_DAYS = (_T['suggest_below'], _T['suggest_above'], _T['suggest_days'], _T['snooze_days'])
DAYS_LO, DAYS_HI = _T['days_bounds']
SNOOZE_LO, SNOOZE_HI = _T['snooze_bounds']
T_LO, T_HI = _T['threshold_bounds']
MAX_EXCLUDE = _T['max_exclude']
MAX_INCLUDE = _T['max_include']
LOG_MAX = _T['log_max']
ACTION = 'HAUFLO_SEASON'
LATER = 'later'
NOTIFY_TAG = 'hauflo_season_suggest'
SEASON_RULE = 'season'
AUTO_HOUR = _T['auto_hour']
AUTO_BY = 'auto'
NONE, NO_DATA = ('none', 'no_data')
RECOMMENDED = (HEAT, COOL, NONE, NO_DATA)
SIGNAL = f'{DOMAIN}_season'

def mode_outputs(auto: bool) -> dict[str, bool]:
    return {'suggest': not auto, 'suggest_back': not auto, 'auto': auto}

def can_switch(state: Any) -> bool:
    modes = state.attributes.get('hvac_modes') or []
    return HEAT in modes and COOL in modes

def _shown(reg: er.EntityRegistry, eid: str) -> bool:
    ent = reg.async_get(eid)
    return not (ent and (ent.disabled_by or ent.hidden_by))

def is_chosen(state: Any, reg: er.EntityRegistry, ours: bool, exclude: Any, include: Any, legacy: bool=False) -> bool:
    if not (can_switch(state) and _shown(reg, state.entity_id)):
        return False
    if ours:
        return state.entity_id not in exclude
    return state.entity_id in include or (legacy and state.entity_id not in exclude)

def acs(hass: HomeAssistant, exclude: frozenset[str] | set[str]=frozenset(), include: frozenset[str] | set[str]=frozenset(), owns: Callable[[str], bool] | None=None, legacy: bool=False) -> list[Any]:
    reg = er.async_get(hass)
    return [s for s in hass.states.async_all('climate') if s.state not in _GONE and is_chosen(s, reg, bool(owns and owns(s.entity_id)), exclude, include, legacy)]

def climates(hass: HomeAssistant, exclude: frozenset[str] | set[str]=frozenset(), include: frozenset[str] | set[str]=frozenset(), owns: Callable[[str], bool] | None=None, legacy: bool=False) -> list[dict[str, Any]]:
    reg = er.async_get(hass)
    out = []
    for s in sorted(hass.states.async_all('climate'), key=lambda x: x.entity_id):
        ent = reg.async_get(s.entity_id)
        ours = bool(owns and owns(s.entity_id))
        out.append({'entity_id': s.entity_id, 'name': label(hass, s.entity_id, s), 'platform': ent.platform if ent else None, 'modes': list(s.attributes.get('hvac_modes') or []), 'state': s.state, 'capable': can_switch(s), 'shown': _shown(reg, s.entity_id), 'heat_cool_address': heat_cool_address(hass, s.entity_id) is not None, 'ours': ours, 'chosen': is_chosen(s, reg, ours, exclude, include, legacy)})
    return out

def sensors(hass: HomeAssistant) -> list[dict[str, Any]]:
    out = []
    for s in sorted(hass.states.async_all('sensor'), key=lambda x: x.entity_id):
        if s.attributes.get('device_class') == 'temperature' and s.attributes.get('state_class'):
            out.append({'entity_id': s.entity_id, 'name': label(hass, s.entity_id, s), 'kind': 'sensor'})
    for s in sorted(hass.states.async_all('weather'), key=lambda x: x.entity_id):
        if dv.num(s.attributes.get('temperature')) is not None:
            out.append({'entity_id': s.entity_id, 'name': label(hass, s.entity_id, s), 'kind': 'weather'})
    return out

def heat_cool_address(hass: HomeAssistant, entity_id: str) -> str | None:
    from . import knx_data
    try:
        cfg = knx_data(hass).config_store.get_entity_config(entity_id)
        ga = ((cfg.get('data') or {}).get('knx') or {}).get('ga_heat_cool') or {}
        w = ga.get('write')
        return w if isinstance(w, str) and w else None
    except Exception:
        return None

def knx_connected(hass: HomeAssistant) -> bool | None:
    from . import knx_data
    try:
        return bool(knx_data(hass).xknx.connection_manager.connected.is_set())
    except Exception:
        return None

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def defaults() -> dict[str, Any]:
    return {'exclude': [], 'include': [], 'labels': LABELS[0], 'resend_s': RESEND_S, 'auto': None, 'suggest': {'enabled': True, 'sensor': None, 'below': BELOW, 'above': ABOVE, 'days': DAYS, 'snooze_days': SNOOZE_DAYS}}

def clean_config(raw: Any) -> tuple[dict[str, Any], list[str]]:
    out = defaults()
    dropped: list[str] = []
    if not isinstance(raw, dict):
        return (out, dropped)
    ex = raw.get('exclude')
    for eid in (ex if isinstance(ex, list) else [])[:MAX_EXCLUDE]:
        if isinstance(eid, str) and eid.startswith('climate.') and _ENTITY_RE.match(eid):
            if eid not in out['exclude']:
                out['exclude'].append(eid)
        else:
            dropped.append(f'exclude {str(eid)[:80]!r} is not a climate')
    inc = raw.get('include')
    for eid in (inc if isinstance(inc, list) else [])[:MAX_INCLUDE]:
        if isinstance(eid, str) and eid.startswith('climate.') and _ENTITY_RE.match(eid):
            if eid not in out['include']:
                out['include'].append(eid)
        else:
            dropped.append(f'include {str(eid)[:80]!r} is not a climate')
    if raw.get('labels') in LABELS:
        out['labels'] = raw['labels']
    if isinstance(raw.get('auto'), bool):
        out['auto'] = raw['auto']
    elif raw.get('auto') is not None:
        dropped.append(f"auto {str(raw.get('auto'))[:20]!r} is not true / false")
    if (r := dv.num(raw.get('resend_s'))) is not None:
        out['resend_s'] = 0 if r <= 0 else int(_clamp(r, RESEND_LO, RESEND_HI))
    sg = raw.get('suggest')
    if isinstance(sg, dict):
        s = out['suggest']
        s['enabled'] = sg.get('enabled') is not False
        sensor = sg.get('sensor')
        if isinstance(sensor, str) and _ENTITY_RE.match(sensor) and (sensor.split('.')[0] in ('sensor', 'weather')):
            s['sensor'] = sensor
        elif sensor is not None:
            dropped.append(f'sensor {str(sensor)[:80]!r} is not a sensor or a weather entity')
        for key, lo, hi in (('below', T_LO, T_HI), ('above', T_LO, T_HI), ('days', DAYS_LO, DAYS_HI), ('snooze_days', SNOOZE_LO, SNOOZE_HI)):
            if (v := dv.num(sg.get(key))) is not None:
                s[key] = int(_clamp(v, lo, hi)) if key in ('days', 'snooze_days') else _clamp(v, lo, hi)
    return (out, dropped)

def rule_body(s: dict[str, Any]) -> dict[str, Any]:
    return {'id': SEASON_RULE, 'name': SEASON_RULE, 'name_gen': {'recipe': SEASON_RULE}, 'purpose': SEASON_RULE, 'enabled': s['enabled'], 'input': {'entity_id': s['sensor']} if s['sensor'] else 'outdoor', 'when': 'below', 'x': s['below'], 'y': max(0.0, s['above'] - s['below']), 'window': {'stat': 'mean', 'period': 'day', 'n': int(s['days'])}, 'outputs': {'suggest': True, 'suggest_back': True, 'auto': False}, 'snooze_days': int(s['snooze_days']), 'actions': [{'do': 'season', 'value': HEAT}], 'back': 'reverse'}

class Season:

    def __init__(self, hass: HomeAssistant, notify_of: Callable[[], Any] | None=None, rules_of: Callable[[], Any] | None=None, owns: Callable[[str], bool] | None=None) -> None:
        self.hass = hass
        self.owns = owns
        self.legacy = False
        self.notify_of = notify_of or (lambda: None)
        self.rules_of = rules_of or (lambda: None)
        self.store = HaufloStore(hass, STORE_KEY)
        self.config: dict[str, Any] = defaults()
        self.season: str | None = None
        self.at: float | None = None
        self.by: str | None = None
        self.migrated = False
        self.suggest_applied: dict[str, Any] | None = None
        self.pending: str | None = None
        self.last_resend: float | None = None
        self.auto_day: str | None = None
        self.auto_checked: str | None = None
        self.log: list[dict[str, Any]] = []
        self._facts: tuple[Any, ...] | None = None
        self.texts: dict[str, dict[str, str]] = {lang: {} for lang in LANGS}
        self._knx_up: bool | None = None
        self._unsubs: list[CALLBACK_TYPE] = []

    async def async_load(self) -> None:
        data = await self.store.async_load() or {}
        raw = data.get('config')
        self.config = clean_config(raw)[0]
        self.legacy = isinstance(raw, dict) and 'include' not in raw
        self.season = data.get('season') if data.get('season') in MODES else None
        at = data.get('at')
        self.at = at if isinstance(at, (int, float)) and (not isinstance(at, bool)) else None
        self.by = data.get('by')
        self.migrated = data.get('migrated') is True
        self.suggest_applied = data.get('suggest_applied') if isinstance(data.get('suggest_applied'), dict) else None
        self.last_resend = data.get('last_resend') if isinstance(data.get('last_resend'), (int, float)) else None
        self.auto_day = data.get('auto_day') if isinstance(data.get('auto_day'), str) else None
        self.auto_checked = data.get('auto_checked') if isinstance(data.get('auto_checked'), str) else None

    def data(self) -> dict[str, Any]:
        return {'config': self.config, 'season': self.season, 'at': self.at, 'by': self.by, 'migrated': self.migrated, 'suggest_applied': self.suggest_applied, 'last_resend': self.last_resend, 'auto_day': self.auto_day, 'auto_checked': self.auto_checked}

    async def _save(self) -> None:
        await self.store.async_save(self.data())

    async def async_seed(self, mode: str) -> None:
        if mode in MODES and self.season is None:
            self.season = mode
            await self._save()

    async def async_start(self) -> None:
        try:
            await self.async_texts()
        except Exception:
            _LOGGER.debug('Hauflo season: translations not loaded', exc_info=True)
        self._knx_up = knx_connected(self.hass)
        if (rules := self.rules_of()) is not None:
            rules.ask_text[SEASON_RULE] = self._ask_text
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=TICK), name='hauflo season'))
        self._unsubs.append(self.hass.bus.async_listen('mobile_app_notification_action', self._on_action))

        async def _later(_now: Any) -> None:
            await self.async_migrate_include()
            await self.async_sync_rule()
            await self.resend('start')
        if self.hass.state == CoreState.running:
            self._unsubs.append(async_call_later(self.hass, START_DELAY, _later))
        else:

            @callback
            def _started(_event: Event) -> None:
                if once in self._unsubs:
                    self._unsubs.remove(once)
                self._unsubs.append(async_call_later(self.hass, START_DELAY, _later))
            once = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started)
            self._unsubs.append(once)

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except ValueError:
                pass
        self._unsubs.clear()

    async def _tick(self, _now: Any) -> None:
        up = knx_connected(self.hass)
        back = up is True and self._knx_up is False
        self._knx_up = up
        resend = self.config['resend_s']
        if back:
            await self.resend('knx back')
        elif resend and (self.last_resend is None or time.time() - self.last_resend >= resend):
            await self.resend('interval')
        if not self.migrated:
            await self.async_sync_rule()
        await self._auto_daily()
        self.changed()

    async def async_texts(self) -> None:
        prefix, suffix = (f'component.{DOMAIN}.exceptions.', '.message')
        for lang in LANGS:
            tr = await translation.async_get_translations(self.hass, lang, 'exceptions', [DOMAIN])
            self.texts[lang] = {k[len(prefix):-len(suffix)]: v for k, v in tr.items() if k.startswith(prefix) and k.endswith(suffix)}

    def _lang(self) -> str:
        engine = self.notify_of()
        return LANGS[0] if site_ru(self.hass.config.language, getattr(engine, 'config', None)) else LANGS[1]

    def say(self, key: str, lang: str, **ph: Any) -> str:
        text = self.texts.get(lang, {}).get(key) or self.texts[LANGS[1]].get(key) or key
        try:
            return text.format(**ph)
        except (KeyError, IndexError, ValueError):
            return text

    def chosen(self) -> list[Any]:
        return acs(self.hass, set(self.config['exclude']), set(self.config['include']), self.owns, self.legacy)

    async def async_migrate_include(self) -> None:
        if not self.legacy:
            return
        reg = er.async_get(self.hass)
        ex = set(self.config['exclude'])
        found = [s.entity_id for s in sorted(self.hass.states.async_all('climate'), key=lambda x: x.entity_id) if not (self.owns and self.owns(s.entity_id)) and is_chosen(s, reg, False, ex, (), True)]
        self.config['include'] = sorted({*self.config['include'], *found})[:MAX_INCLUDE]
        self.legacy = False
        await self._save()

    def current(self) -> str | None:
        on = [s.state for s in self.chosen() if s.state in MODES]
        return (HEAT if on.count(HEAT) >= on.count(COOL) else COOL) if on else self.season

    def rule_season(self) -> str:
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
        self.config['auto'] = on
        await self._save()
        await self._sync_auto()
        _LOGGER.info('Hauflo season: automatic switch %s (%s)', 'on' if on else 'off', why)
        if on:
            await self.auto_check(why)
        self.changed(force=True)

    def recommendation(self) -> tuple[str, str]:
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
        if not self.auto_on() or self.auto_checked == today or now.hour < AUTO_HOUR or (not self._means_today()):
            return
        self.auto_checked = today
        await self._save()
        await self.auto_check('daily')

    async def auto_check(self, why: str) -> str:
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
        if now.hour < AUTO_HOUR:
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
        rules, rule = self._rule()
        mode, days, threshold = self._to(rule, to == self._to(rule, True)[0]) if rule is not None else (to, 0, 0.0)
        name = self.say(f"season_to_{self.config['labels']}_{to}", lang)
        why = self.say(f'season_suggest_why_{to}', lang, days=days, threshold=f'{threshold:g}')
        try:
            await engine.async_tell(self.say('season_auto_title', lang, to=name), self.say('season_auto_message', lang, to=name, why=why), None, tag=NOTIFY_TAG)
        except Exception:
            _LOGGER.warning('Hauflo season: the automatic switch not announced', exc_info=True)

    def facts(self) -> dict[str, Any]:
        rules, rule = self._rule()
        to, why = self.recommendation()
        win_ = rules.win.get(SEASON_RULE) or {} if rule is not None else {}
        starts = win_.get('starts') or []
        vals = win_.get('values') or []
        means = {dt_util.as_local(dt_util.utc_from_timestamp(t)).date().isoformat(): v for t, v in zip(starts, vals) if isinstance(t, (int, float))}
        w = (rule or {}).get('window') or {}
        return {'recommended': to, 'reason': why, 'current': self.current(), 'auto': self.auto_on(), 'sensor': win_.get('entity_id') or ((rule or {}).get('input') or {}).get('entity_id'), 'daily_means': means, 'below': (rule or {}).get('x'), 'above': float(rule['x']) + float(rule.get('y', 0.0)) if rule else None, 'days': w.get('n'), 'days_back': w.get('n_back'), 'auto_switched': self.auto_day, 'auto_checked': self.auto_checked}

    @callback
    def changed(self, force: bool=False) -> None:
        f = self.facts()
        key = (f['recommended'], f['reason'], f['current'], f['auto'], tuple(f['daily_means'].items()), f['auto_switched'])
        if force or key != self._facts:
            self._facts = key
            async_dispatcher_send(self.hass, SIGNAL)

    async def answer(self) -> dict[str, Any]:
        items = self.chosen()
        return {'season': self.current(), 'chosen': self.season, 'at': self.at, 'labels': self.config['labels'], 'auto': self.auto_on(), 'suggestion': self.suggestion(), 'acs': [{'entity_id': s.entity_id, 'name': label(self.hass, s.entity_id, s), 'state': s.state} for s in items]}

    def setup_answer(self) -> dict[str, Any]:
        found = dv.outdoor(self.hass)
        rules, rule = self._rule()
        sug = self.suggestion()
        st = rules.st(SEASON_RULE) if rule is not None else {}
        if rule is None:
            status = 'no_rule' if self.migrated else 'off' if not self.config['suggest']['enabled'] else 'no_sensor'
        else:
            status = 'off' if not rule['enabled'] else 'no_data' if st.get('no_data') else 'suggest' if sug else 'ok'
        snoozed = (st.get('sug') or {}).get('snooze_until') or None
        return {'config': self.config, 'defaults': defaults(), 'season': self.current(), 'chosen': self.season, 'climates': climates(self.hass, set(self.config['exclude']), set(self.config['include']), self.owns, self.legacy), 'sensors': sensors(self.hass), 'auto_sensor': found['entity_id'] if found else None, 'suggest': {'status': status, 'to': sug['to'] if sug else None, 'sensor': rule['input']['entity_id'] if rule else None, 'means': (rules.win.get(SEASON_RULE) or {}).get('values') or [] if rule else [], 'snoozed_until': snoozed, 'rule': rules.rule_out(SEASON_RULE, False) if rule else None, 'pending': self.pending, 'recommended': self.recommendation()[0], 'reason': self.recommendation()[1]}, 'auto': self.auto_on(), 'auto_switched': self.auto_day, 'auto_checked': self.auto_checked, 'last_resend': self.last_resend, 'log': self.log[-LOG_MAX:]}

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
                _LOGGER.debug('Hauflo season resend: %s failed: %s', s.entity_id, err)
                failed.append(s.entity_id)
        self.last_resend = time.time()
        self.store.async_delay_save(self.data, TICK)
        entry = {'at': self.last_resend, 'why': why, 'season': mode, 'addresses': sorted(addresses), 'changed': changed, 'failed': failed}
        self.log = [*self.log[-(LOG_MAX - 1):], entry]
        _LOGGER.debug('Hauflo season resend (%s): %s', why, entry)
        return entry

    @callback
    def _dismiss(self) -> None:
        from homeassistant.components import persistent_notification
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

class SeasonView(HomeAssistantView):
    url = '/api/hauflo/season'
    name = 'api:hauflo:season'
    requires_auth = True

    def __init__(self, season_of: Callable[[], Season | None]) -> None:
        self.season_of = season_of

    async def get(self, request):
        season = self.season_of()
        if season is None:
            return self.json_message('Hauflo agent not set up', 503)
        return self.json(await season.answer())

    async def post(self, request):
        season = self.season_of()
        if season is None:
            return self.json_message('Hauflo agent not set up', 503)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        body = body if isinstance(body, dict) else {}
        mode = body.get('mode')
        if mode not in MODES and body.get('snooze') is not True:
            return self.json_message('mode: heat or cool, or snooze', 400)
        if (no := (await refuse(request))) is not None:
            return no
        if mode not in MODES:
            await season.snooze()
            return self.json(await season.answer())
        user = request['hass_user']
        return self.json(await season.apply(mode, user.name if user else None))

class SeasonConfigView(HomeAssistantView):
    url = '/api/hauflo/season/config'
    name = 'api:hauflo:season:config'
    requires_auth = True

    def __init__(self, season_of: Callable[[], Season | None]) -> None:
        self.season_of = season_of

    async def get(self, request):
        season = self.season_of()
        if season is None:
            return self.json_message('Hauflo agent not set up', 503)
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        return self.json(season.setup_answer())

    async def post(self, request):
        season = self.season_of()
        if season is None:
            return self.json_message('Hauflo agent not set up', 503)
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict):
            return self.json_message('an object expected', 400)
        return self.json(await season.async_set_config(body))

class _SeasonEntity:
    _attr_should_poll = False
    _attr_has_entity_name = True

    def _setup(self, entry: Any, season: Season, domain: str, key: str) -> None:
        self.season = season
        self._attr_unique_id = f'{entry.entry_id}_season_{key}'
        self.entity_id = f'{domain}.{DOMAIN}_season_{key}'
        self._attr_device_info = device_info(entry.entry_id, entry)
        self._attr_translation_key = f"season_{key}_{season.config['labels']}"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL, self.async_write_ha_state))

class SeasonRecommended(_SeasonEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(RECOMMENDED)
    _unrecorded_attributes = frozenset({'daily_means', 'below', 'above', 'days', 'days_back', 'sensor', 'auto_checked'})

    def __init__(self, entry: Any, season: Season) -> None:
        self._setup(entry, season, 'sensor', 'recommended')

    @property
    def native_value(self) -> str:
        return self.season.recommendation()[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        f = self.season.facts()
        return {k: f[k] for k in ('reason', 'daily_means', 'below', 'above', 'days', 'days_back', 'sensor', 'auto', 'auto_switched', 'auto_checked')}

class SeasonCurrent(_SeasonEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(MODES)

    def __init__(self, entry: Any, season: Season) -> None:
        self._setup(entry, season, 'sensor', 'current')

    @property
    def native_value(self) -> str | None:
        return self.season.current()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {'chosen': self.season.season, 'by': self.season.by, 'at': dt_util.utc_from_timestamp(self.season.at).isoformat() if self.season.at else None}

class SeasonAuto(_SeasonEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: Any, season: Season) -> None:
        self._setup(entry, season, 'switch', 'auto')
        self._attr_translation_key = 'season_auto'

    @property
    def is_on(self) -> bool:
        return self.season.auto_on()

    async def _staff(self) -> None:
        uid = self._context.user_id if self._context else None
        user = await self.hass.auth.async_get_user(uid) if uid else None
        if user is not None and (not user.is_admin):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key='season_auto_staff')

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._staff()
        await self.season.set_auto(True, 'ha')

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._staff()
        await self.season.set_auto(False, 'ha')
