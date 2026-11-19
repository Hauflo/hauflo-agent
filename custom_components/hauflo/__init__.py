"""Hauflo agent: one outbound WebSocket from this Home Assistant to Hauflo."""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from . import intercom as intercom_mod, locks as locks_mod, protect, timers
from .const import DOMAIN
from .entry.remove import async_remove_entry, async_unload_entry
from .entry.stages import async_setup_entry
from .independent import service as independent_service
from .policy.knx import ga_key
from .policy.marks import protect_marks_of, protect_refresh
from .preload import async_preload
__all__ = ['CONFIG_SCHEMA', 'async_remove_entry', 'async_setup', 'async_setup_entry', 'async_unload_entry']
if TYPE_CHECKING:
    from .link.agent import Agent
_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

def _loaded(hass: HomeAssistant) -> Agent:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key='not_loaded')

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """The six actions, registered once: valid in automations before the entry loads, answering «not loaded» meanwhile."""
    await async_preload(hass)
    timers.async_setup(hass, lambda: _loaded(hass))
    protect.async_setup_service(hass, lambda: _loaded(hass), lambda eid: protect_marks_of(hass, eid), ga_key, lambda: protect_refresh(hass))
    independent_service.async_setup_service(hass, lambda: _loaded(hass).engine)
    intercom_mod.async_setup_services(hass, lambda: _loaded(hass).intercom)
    locks_mod.async_setup_services(hass, lambda: _loaded(hass).locks)
    return True
