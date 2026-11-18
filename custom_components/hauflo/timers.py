"""«Выключить через время» for a light (the remote's timer): one pending switch-off per light, kept until HA restarts.
Minutes 0 cancels. The remote shows the note it keeps itself; the switch-off is made here, so it holds with the tab
closed."""
from __future__ import annotations
from collections.abc import Callable
from datetime import datetime
import functools
from typing import Any
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later
import voluptuous as vol
from .const import DOMAIN, HassKey
from .tuning import tuning
DATA_OFF_TIMERS: HassKey[dict[str, Callable[[], None]]] = HassKey(f'{DOMAIN}_off_timers')
SERVICE = 'off_in'
max_minutes = functools.cache(lambda: tuning()['cards']['off_in_max_min'])
schema = functools.cache(lambda: vol.Schema({vol.Required('entity_id'): cv.entity_id, vol.Required('minutes'): vol.All(vol.Coerce(float), vol.Range(min=0, max=max_minutes()))}))

@callback
def async_setup(hass: HomeAssistant, loaded: Callable[[], Any]) -> None:
    """«hauflo.off_in», registered once for HA's whole run (integration setup); loaded() raises «not_loaded» without a
    loaded entry."""
    timers: dict[str, Callable[[], None]] = hass.data.setdefault(DATA_OFF_TIMERS, {})

    async def handle(call: ServiceCall) -> None:
        loaded()
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
    hass.services.async_register(DOMAIN, SERVICE, handle, schema=schema())
