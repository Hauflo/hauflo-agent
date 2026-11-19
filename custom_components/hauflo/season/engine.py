"""The season engine: stored choice, tick and texts."""
from __future__ import annotations
from collections.abc import Callable
import contextlib
from datetime import timedelta
import logging
import time
from typing import Any
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, CoreState, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, translation
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from ..const import DOMAIN, site_ru
from ..guard import contained
from ..storage import HaufloStore
from .answers import SeasonAnswersMixin
from .auto import SeasonAutoMixin
from .common import COOL, HEAT, LANGS, MODES, SEASON_RULE, STORE_KEY, acs, clean_config, defaults, is_chosen, knx_connected, max_include, start_delay, tick
_LOGGER = logging.getLogger(__name__)

class Season(SeasonAutoMixin, SeasonAnswersMixin):
    """The house's season: the client's switch, the resend; the suggestion is the value rule «season» (rules.py)."""

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
        """A season known from elsewhere (the retired «Комфортный пол»'s house mode) — only if none was chosen here."""
        if mode in MODES and self.season is None:
            self.season = mode
            await self._save()

    async def async_start(self) -> None:
        with contained(_LOGGER, 'Hauflo season: translations not loaded'):
            await self.async_texts()
        self._knx_up = knx_connected(self.hass)
        if (rules := self.rules_of()) is not None:
            rules.ask_text[SEASON_RULE] = self._ask_text
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=tick()), name='hauflo season'))
        self._unsubs.append(self.hass.bus.async_listen('mobile_app_notification_action', self._on_action))

        async def _later(_now: Any) -> None:
            await self.async_migrate_include()
            await self.async_sync_rule()
            await self.resend('start')
        if self.hass.state == CoreState.running:
            self._unsubs.append(async_call_later(self.hass, start_delay(), _later))
        else:

            @callback
            def _started(_event: Event) -> None:
                if once in self._unsubs:
                    self._unsubs.remove(once)
                self._unsubs.append(async_call_later(self.hass, start_delay(), _later))
            once = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started)
            self._unsubs.append(once)

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            with contextlib.suppress(ValueError):
                unsub()
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
        """An old config (no `include`): the foreign climates it switched by the old rule become `include`, once."""
        if not self.legacy:
            return
        reg = er.async_get(self.hass)
        ex = set(self.config['exclude'])
        found = [s.entity_id for s in sorted(self.hass.states.async_all('climate'), key=lambda x: x.entity_id) if not (self.owns and self.owns(s.entity_id)) and is_chosen(s, reg, False, ex, (), True)]
        self.config['include'] = sorted({*self.config['include'], *found})[:max_include()]
        self.legacy = False
        await self._save()

    def current(self) -> str | None:
        """What the working chosen ACs do now (the most of them); with all off — the last one chosen here."""
        on = [s.state for s in self.chosen() if s.state in MODES]
        return (HEAT if on.count(HEAT) >= on.count(COOL) else COOL) if on else self.season
