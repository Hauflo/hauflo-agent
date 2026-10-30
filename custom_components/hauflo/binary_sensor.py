"""«Связь с Hauflo»: the agent's link to the Hauflo server as an entity (HA core review 25.10 — the tunnel's state
was visible nowhere in HA). On while Hauflo has said «welcome»; off when the link is down or the agent unlinked."""

from __future__ import annotations

from urllib.parse import urlparse

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device import device_info
from .const import CONF_SERVER, DOMAIN, VERSION

SIGNAL_LINK = f"{DOMAIN}_link"
# One push-updated entity, never polled.
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([HaufloLink(entry)])
    # «Правила по значениям»: one «сработало» sensor per rule (rules.py).
    if (engine := getattr(entry.runtime_data, "rules", None)) is not None:
        engine.add_binary = add
        engine.binaries.clear()
        engine.sync_entities()


class HaufloLink(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "link"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_link"
        self._attr_device_info = device_info(entry.entry_id, entry)

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        # Which server this house is linked to — seen in HA, not only in Hauflo (security re-review round 4). The agent's
        # version: the cards compare it with their own and reload a page left from before an update (hauflo-cards.js SELF).
        return {"server": urlparse(self._entry.data.get(CONF_SERVER, "")).hostname, "version": VERSION}

    @property
    def is_on(self) -> bool:
        agent = getattr(self._entry, "runtime_data", None)
        return bool(agent and agent.linked)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_LINK, self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
