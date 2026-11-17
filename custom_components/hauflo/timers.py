from __future__ import annotations
from datetime import datetime
from typing import Any, Callable
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later
from .const import DOMAIN, HassKey
from .tuning import TUNING
DATA_OFF_TIMERS: HassKey[dict[str, Callable[[], None]]] = HassKey(f'{DOMAIN}_off_timers')
SERVICE = 'off_in'
MAX_MINUTES = TUNING['cards']['off_in_max_min']
SCHEMA = vol.Schema({vol.Required('entity_id'): cv.entity_id, vol.Required('minutes'): vol.All(vol.Coerce(float), vol.Range(min=0, max=MAX_MINUTES))})

@callback
def async_setup(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE):
        return
    timers: dict[str, Callable[[], None]] = hass.data.setdefault(DATA_OFF_TIMERS, {})

    async def handle(call: ServiceCall) -> None:
        eid: str = call.data['entity_id']
        if not eid.startswith('light.'):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key='timer_not_light')
        if hass.states.get(eid) is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key='timer_unknown_light')
        if (old := timers.pop(eid, None)) is not None:
            old()
        minutes = call.data['minutes']
        if minutes <= 0:
            return

        async def fire(_now: datetime) -> None:
            timers.pop(eid, None)
            await hass.services.async_call('light', 'turn_off', {'entity_id': eid}, blocking=False)
        timers[eid] = async_call_later(hass, minutes * 60, fire)
    hass.services.async_register(DOMAIN, SERVICE, handle, schema=SCHEMA)
