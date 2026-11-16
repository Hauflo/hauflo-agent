from __future__ import annotations
from http import HTTPStatus
from collections.abc import Callable
from datetime import timedelta
import logging
import re
import time
from typing import Any
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, CoreState, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, translation
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util
from . import derived
from .derived import NOT_A_STATE
from .const import DOMAIN
from .device import device_info
from .house import house
from .tuning import TUNING
_LOGGER = logging.getLogger(__name__)
_T = TUNING['daylight']
KEY = 'daylight'
SIGNAL = f'{DOMAIN}_daylight'
SUN = 'sun.sun'
SENSOR_DOMAIN = 'sensor'
LUX = 'lx'
LANGS = ('ru', 'en')
LUX_DEFAULT = _T['lux_default']
LUX_MIN, LUX_MAX = _T['lux_bounds']
LUX_OFF = _T['lux_off_share']
SUN_ON = _T['sun_on_deg']
SUN_OFF = _T['sun_off_deg']
HOLD = _T['hold_s']
TICK = _T['tick_s']
MAX_SKIP = _T['max_skip']
KEYS = {'sensor', 'lux', 'skip'}
_SENSOR = re.compile('sensor\\.[a-z0-9_]{1,200}')
_SKIP = re.compile('(?:light|switch)\\.[a-z0-9_]{1,200}')
_clock = time.time

class Invalid(ValueError):
    pass

class Refused(Invalid, derived.Refused):

    def __init__(self, code: str, status: int=HTTPStatus.BAD_REQUEST, **ph: Any) -> None:
        derived.Refused.__init__(self, code, f'daylight_{code}', status, **ph)

class SensorRefused(Invalid):

    def __init__(self, why: str) -> None:
        super().__init__(f'sensor: {why}')
        self.why = why

    @property
    def key(self) -> str:
        return f'daylight_sensor_{self.why}'

    async def text(self, hass: HomeAssistant, ru: bool) -> str:
        tr = await translation.async_get_translations(hass, LANGS[0 if ru else 1], 'exceptions', [DOMAIN])
        return tr.get(f'component.{DOMAIN}.exceptions.{self.key}.message') or str(self)

def clean_sensor(v: Any) -> str | None:
    if v is None or v == '':
        return None
    if not isinstance(v, str) or not _SENSOR.fullmatch(v):
        raise Invalid('sensor: a sensor entity id')
    return v

def clean_lux(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (not LUX_MIN <= v <= LUX_MAX):
        raise Invalid(f'lux: a number {LUX_MIN}–{LUX_MAX}')
    return float(v)

def clean_skip(v: Any) -> list[str]:
    if not isinstance(v, list) or len(v) > MAX_SKIP or (not all((isinstance(x, str) and _SKIP.fullmatch(x) for x in v))):
        raise Invalid(f'skip: up to {MAX_SKIP} light / switch ids')
    return list(dict.fromkeys(v))

def clean(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - KEYS:
        raise Invalid(f'daylight: an object of {sorted(KEYS)}')
    out: dict[str, Any] = {'skip': clean_skip(raw.get('skip', []))}
    if (sensor := clean_sensor(raw.get('sensor'))) is not None:
        out['sensor'] = sensor
    if (lux := clean_lux(raw.get('lux'))) is not None:
        out['lux'] = lux
    return out

class Daylight:

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.on: bool | None = None
        self.source: str | None = None
        self.value: float | None = None
        self.threshold: float | None = None
        self.since: float | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._watch: CALLBACK_TYPE | None = None
        self._later: CALLBACK_TYPE | None = None
        self._warned = False
        self._ready = True
        self._start: CALLBACK_TYPE | None = None

    async def config(self) -> dict[str, Any]:
        raw = (await house(self.hass).load()).get(KEY)
        raw = raw if isinstance(raw, dict) else {}
        skip = raw.get('skip')
        good = [x for x in skip if isinstance(x, str) and _SKIP.fullmatch(x)] if isinstance(skip, list) else []
        out: dict[str, Any] = {'skip': list(dict.fromkeys(good))[:MAX_SKIP]}
        try:
            if (sensor := clean_sensor(raw.get('sensor'))) is not None:
                out['sensor'] = sensor
        except Invalid:
            pass
        lux = raw.get('lux')
        if isinstance(lux, (int, float)) and (not isinstance(lux, bool)) and (derived.num(lux) is not None):
            out['lux'] = float(min(max(lux, LUX_MIN), LUX_MAX))
        if not self._warned and (set(raw) - set(out) or any((raw[k] != out[k] for k in out if k in raw))):
            self._warned = True
            _LOGGER.warning('Hauflo daylight: a stored field is not valid — dropped, the rest kept')
        return out

    async def _save(self, cfg: dict[str, Any]) -> None:
        h = house(self.hass)
        data = await h.load()
        data[KEY] = clean(cfg)
        await h.save()

    async def set_skip(self, entity_id: Any, on: Any) -> list[str]:
        if not isinstance(entity_id, str) or not _SKIP.fullmatch(entity_id) or (not isinstance(on, bool)):
            raise Invalid('entity_id: a light / switch, on: true or false')
        if self.hass.states.get(entity_id) is None:
            raise Invalid('entity_id: no such entity')
        cfg = await self.config()
        skip = [x for x in cfg['skip'] if x != entity_id] + ([entity_id] if on else [])
        if len(skip) > MAX_SKIP:
            raise Refused('skip_full', n=MAX_SKIP)
        await self._save({**cfg, 'skip': skip})
        return skip

    async def set_config(self, body: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(body, dict) or not body or set(body) - {'sensor', 'lux'}:
            raise Invalid('sensor and / or lux')
        cfg = await self.config()
        if 'sensor' in body:
            sensor = clean_sensor(body['sensor'])
            if sensor:
                self._usable(sensor)
            cfg.pop('sensor', None)
            if sensor:
                cfg['sensor'] = sensor
        if 'lux' in body:
            lux = clean_lux(body['lux'])
            cfg.pop('lux', None)
            if lux is not None:
                cfg['lux'] = lux
        await self._save(cfg)
        self._track(cfg.get('sensor'))
        await self.refresh(force=True)
        return cfg

    def _usable(self, sensor: str) -> None:
        st = self.hass.states.get(sensor)
        entry = er.async_get(self.hass).async_get(sensor)
        if st is None and entry is None:
            raise SensorRefused('missing')
        unit = st.attributes.get(ATTR_UNIT_OF_MEASUREMENT) if st is not None else None
        silent = st is None or st.state in NOT_A_STATE
        if unit is None and silent and (entry is not None):
            unit = (entry.options.get(SENSOR_DOMAIN) or {}).get(ATTR_UNIT_OF_MEASUREMENT) or entry.unit_of_measurement
        if unit is None and (not silent) or (unit is not None and unit != LUX):
            raise SensorRefused('unit')

    def _read(self, cfg: dict[str, Any]) -> tuple[bool | None, str | None, float | None, float | None]:
        was = self.on is True
        if (sensor := cfg.get('sensor')):
            v, unit, _why = derived.read(self.hass, sensor)
            if v is not None and unit == LUX:
                thr = cfg.get('lux', LUX_DEFAULT)
                return (v >= thr * LUX_OFF if was else v >= thr, 'sensor', v, thr)
        st = self.hass.states.get(SUN)
        elev = derived.num(st.attributes.get('elevation')) if st is not None and st.state not in NOT_A_STATE else None
        if elev is None:
            return (None, None, None, None)
        return (elev >= SUN_OFF if was else elev >= SUN_ON, 'sun', elev, SUN_ON)

    async def refresh(self, force: bool=False) -> None:
        if not self._ready and (not force):
            return
        cfg = await self.config()
        on, source, value, thr = self._read(cfg)
        self.source, self.value, self.threshold = (source, value, thr)
        now = _clock()
        if on is not None and on != self.on:
            held = self.on is not None and self.since is not None and (now - self.since < HOLD) and (not force)
            if held:
                self._again(self.since + HOLD - now)
            else:
                self.on, self.since = (on, now)
                _LOGGER.debug('Hauflo daylight: %s (%s %s / %s)', 'light' if on else 'dark', source, value, thr)
        elif on is None and self.on is not None:
            self.on, self.since = (None, now)
        async_dispatcher_send(self.hass, SIGNAL)

    def is_light(self) -> bool:
        return self.on is True

    def _again(self, delay: float) -> None:
        if self._later is not None:
            return

        @callback
        def run(_now: Any) -> None:
            self._later = None
            self.hass.async_create_task(self.refresh())
        self._later = async_call_later(self.hass, max(1.0, delay), run)

    def _track(self, sensor: str | None) -> None:
        if self._watch is not None:
            self._watch()

        @callback
        def changed(event: Event) -> None:
            new = event.data.get('new_state')
            if new is not None and new.state not in NOT_A_STATE:
                self._ready = True
            self.hass.async_create_task(self.refresh())
        self._watch = async_track_state_change_event(self.hass, [SUN, *([sensor] if sensor else [])], changed)

    async def async_start(self) -> None:
        cfg = await self.config()
        self._track(cfg.get('sensor'))

        async def tick(_now: Any) -> None:
            await self.refresh()
        self._unsubs.append(async_track_time_interval(self.hass, tick, timedelta(seconds=TICK), name='hauflo daylight'))
        self._ready = self.hass.state is CoreState.running
        if self._ready:
            await self.refresh()
            return

        async def started(_event: Event) -> None:
            self._start = None
            self._ready = True
            await self.refresh()
        self._start = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, started)

    def async_stop(self) -> None:
        for u in (*self._unsubs, self._watch, self._later, self._start):
            if u is not None:
                u()
        self._unsubs, self._watch, self._later, self._start = ([], None, None, None)

    async def answer(self) -> dict[str, Any]:
        cfg = await self.config()
        return {'on': self.on, 'source': self.source, 'skip': cfg['skip'], 'sensor': cfg.get('sensor'), 'lux': cfg.get('lux', LUX_DEFAULT)}

class HaufloDaylight(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = 'daylight'
    _attr_device_class = BinarySensorDeviceClass.LIGHT
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({'value', 'threshold', 'since'})

    def __init__(self, entry: Any, daylight: Callable[[], Daylight | None]) -> None:
        self._daylight = daylight
        self._attr_unique_id = f'{entry.entry_id}_daylight'
        self.entity_id = f'binary_sensor.{DOMAIN}_daylight'
        self._attr_device_info = device_info(entry.entry_id, entry)

    @property
    def available(self) -> bool:
        d = self._daylight()
        return d is not None and d.on is not None

    @property
    def is_on(self) -> bool | None:
        d = self._daylight()
        return d.on if d is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self._daylight()
        if d is None:
            return {}
        since = dt_util.utc_from_timestamp(d.since).isoformat() if d.since else None
        return {'source': d.source, 'value': d.value, 'threshold': d.threshold, 'since': since}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL, self.async_write_ha_state))
