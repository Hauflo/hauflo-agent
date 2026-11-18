"""The simple automations engine: texts, location and answers."""
from __future__ import annotations
import asyncio
from collections.abc import Callable
import datetime as dt
import logging
import re
from typing import Any
import astral
import astral.sun
from homeassistant.components.automation.config import async_validate_config_item
from homeassistant.core import HomeAssistant
from homeassistant.core_config import ConfigSource
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from homeassistant.util.location import async_detect_location_info
from .. import recipes
from ..rules import actions as rules_actions, checks as rules_checks, const as rules_const, spec as rules_spec, text as rules_text
from ..storage import HaufloStore
from .build import AutomationsBuildMixin
from .common import DAYS, FILE_LOCK, MAX_ACTIONS, MAX_OFFSET, MOVED, STORE_KEY, UNSET, Invalid, bad, bounded_float, group_domains, max_ramps, max_set, repeat_cfg
from .describe import AutomationsDescribeMixin
from .specs import AutomationsSpecsMixin
from .store import AutomationsStoreMixin
from .targets import AutomationsTargetsMixin
_LOGGER = logging.getLogger(__name__)

async def async_add_new(hass: HomeAssistant, configs: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Adds the automations whose ids automations.yaml doesn't have yet — one write, with the same backup copy and
    plain-file check as Hauflo's own rules; never replaces or removes one (one already there may have been edited by a
    person). Each is validated by HA first. Returns (added ids, ids already there); NotPlain when the file isn't
    plain."""
    for cfg in configs:
        await async_validate_config_item(hass, cfg['id'], cfg)
    helper = Automations(hass, lambda _eid: False)
    async with FILE_LOCK:
        data, before = await helper.read_file()
        have = {a.get('id') for a in data if isinstance(a, dict)}
        new = [c for c in configs if c['id'] not in have]
        if new:

            def redo(fresh: list[Any]) -> list[Any]:
                there = {a.get('id') for a in fresh if isinstance(a, dict)}
                return [*fresh, *(c for c in new if c['id'] not in there)]
            await helper.write_file([*data, *new], before, redo)
    if new:
        await hass.services.async_call('automation', 'reload', {}, blocking=True)
    return ([c['id'] for c in new], [c['id'] for c in configs if c['id'] in have])

class Automations(AutomationsTargetsMixin, AutomationsSpecsMixin, AutomationsBuildMixin, AutomationsDescribeMixin, AutomationsStoreMixin):

    def __init__(self, hass: HomeAssistant, owns: Callable[[str], bool], protected: Callable[[str], bool] | None=None, habits: Callable[[], Any] | None=None) -> None:
        self.hass = hass
        self.owns = owns
        self.protected = protected or (lambda eid: False)
        self.habits = habits or (lambda: None)
        self.store = HaufloStore(hass, STORE_KEY)
        self.lock = asyncio.Lock()
        self._env = rules_checks.Env(hass, owns=self.pickable, own_raw=lambda _eid: False, client=True)
        self.tx = rules_text.Texts(hass)

    async def texts(self) -> rules_text.Texts:
        """The translations, loaded once (the first request / load)."""
        if not self.tx.texts[rules_const.LANGS[1]]:
            await self.tx.async_texts()
        return self.tx

    def message(self, err: rules_actions.Invalid, ru: bool) -> str:
        """A refusal in the request's language: a field by its name, a kind of device by its word, a room by its name,
        the
        actions by the canon's names (rules_do_…), a device by what a person calls it — never an id or a key."""
        tx, ph = (self.tx, dict(err.ph))
        if isinstance((f := ph.get('field')), str):
            ph['field'] = tx.say(f'automations_f_{f}', ru)
        if isinstance((k := ph.get('kind')), str):
            ph['kind'] = self._d(ru, f'dom_{k}') if k in group_domains() else k
        if isinstance((ks := ph.get('kinds')), list):
            ph['kinds'] = ', '.join((self._d(ru, f'dom_{k}') for k in ks))
        if isinstance((acts := ph.get('acts')), list):
            ph['acts'] = ', '.join((self._lower(tx.say(f'rules_do_{a}', ru)) for a in acts)) or '—'
        if isinstance((room := ph.get('room')), str):
            ph['room'] = self._room_name(room)
        return tx.say(err.key, ru, **tx.words(ru, ph))

    def location(self) -> dict[str, Any]:
        c = self.hass.config
        lat, lon = (round(float(c.latitude), 5), round(float(c.longitude), 5))
        return {'latitude': c.latitude, 'longitude': c.longitude, 'elevation': c.elevation, 'set': (lat, lon) not in UNSET, 'yaml': c.config_source == ConfigSource.YAML, 'time_zone': c.time_zone}

    async def set_location(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.hass.config.config_source == ConfigSource.YAML:
            raise PermissionError('automations_yaml_location')
        if set(body) - {'op', 'lang', 'latitude', 'longitude', 'elevation'}:
            raise bad('location')
        lat = bounded_float(body.get('latitude'), -90, 90, 'latitude')
        lon = bounded_float(body.get('longitude'), -180, 180, 'longitude')
        if (round(lat, 5), round(lon, 5)) in UNSET:
            raise Invalid('bad_place')
        kwargs: dict[str, Any] = {'latitude': round(lat, 6), 'longitude': round(lon, 6)}
        if body.get('elevation') is not None:
            kwargs['elevation'] = round(bounded_float(body['elevation'], -500, 9000, 'elevation'))
        await self.hass.config.async_update(**kwargs)
        return self.location()

    async def set_time_zone(self, body: dict[str, Any]) -> dict[str, Any]:
        """        zone."""
        if self.hass.config.config_source == ConfigSource.YAML:
            raise PermissionError('automations_yaml_time_zone')
        tz = body.get('time_zone')
        if set(body) - {'op', 'lang', 'time_zone'} or not isinstance(tz, str) or (not re.fullmatch('[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){0,2}', tz)) or (dt_util.get_time_zone(tz) is None):
            raise Invalid('bad_time_zone')
        await self.hass.config.async_update(time_zone=tz)
        return self.location()

    async def detect(self) -> dict[str, Any]:
        """Where the house is by its internet address — HA's own onboarding helper (the HA box has no GPS). Nothing is
        saved: the person confirms first."""
        info = await async_detect_location_info(async_get_clientsession(self.hass))
        if info is None or info.latitude is None or info.longitude is None:
            raise LookupError('automations_no_detect')
        return {'latitude': info.latitude, 'longitude': info.longitude, 'city': info.city, 'region': getattr(info, 'region_name', None), 'country': info.country_code, 'time_zone': info.time_zone}

    def days(self, sun: bool=True) -> list[dict[str, Any]]:
        """Today and the next 7 days in HA's time zone with their sunrise and sunset (none while the house's place isn't
        known) — the dashboard says when a rule runs."""
        tz = dt_util.get_time_zone(self.hass.config.time_zone) or dt_util.DEFAULT_TIME_ZONE
        obs = astral.Observer(self.hass.config.latitude, self.hass.config.longitude, self.hass.config.elevation)
        today = dt_util.now(tz).date()
        out = []
        for n in range(8):
            day = today + dt.timedelta(days=n)
            row: dict[str, Any] = {'date': day.isoformat(), 'wd': DAYS[day.weekday()], 'midnight': dt.datetime.combine(day, dt.time(), tz).isoformat()}
            for event in ('sunrise', 'sunset'):
                if not sun:
                    row[event] = None
                    continue
                try:
                    row[event] = getattr(astral.sun, event)(obs, date=day, tzinfo=tz).isoformat()
                except ValueError:
                    row[event] = None
            out.append(row)
        return out

    async def answer(self) -> dict[str, Any]:
        async with self.lock:
            data = await self.load()
            if data.get(MOVED):
                await self._migrate(data)
        edited = await self.changed(data)
        rules = []
        for rule_id, rule in data['rules'].items():
            eid = self.entity_of(rule_id)
            s = self.hass.states.get(eid) if eid else None
            rules.append({'id': rule_id, **{k: v for k, v in rule.items() if k not in ('by', 'hash')}, 'entity_id': eid, 'state': s.state if s else 'missing', 'enabled': s.state == 'on' if s else rule['enabled'], 'changed': rule_id in edited})
        rules.sort(key=lambda r: r.get('at') or 0)
        targets = self.targets()
        out = {'location': self.location(), 'days': self.days(self.location()['set']), 'rules': rules, 'targets': targets, 'max_offset': MAX_OFFSET}
        out['features'] = {'rules_v2': True}
        out['limits'] = {'times': rules_spec.C('schedule', 'moments'), 'repeat': {k: repeat_cfg()[k] for k in ('count', 'every_s', 'acts')}, 'targets': max_set(), 'actions': MAX_ACTIONS, 'ramps': max_ramps(), 'pulse_s': rules_spec.C('actions', 'pulse_s')}
        out['recipes'] = recipes.resolve(recipes.recipes_data(), targets, self.validate)
        out['recipe_groups'] = recipes.recipes_data()['groups']
        if (h := self.habits()) is not None:
            out['habits_on'] = h.enabled
            out['habits'] = h.suggestions(self, data) if h.enabled else []
        return out
