from __future__ import annotations
from urllib.parse import urlparse
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import CONF_SERVER, DOMAIN, VERSION
from .daylight import HaufloDaylight
from .device import device_info
from .house_state import HouseEntity
SIGNAL_LINK = f'{DOMAIN}_link'
PARALLEL_UPDATES = 0

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([HaufloLink(entry), HaufloDaylight(entry, lambda: getattr(entry.runtime_data, 'daylight', None)), HaufloHouseProblem(entry)])
    if (engine := getattr(entry.runtime_data, 'rules', None)) is not None:
        engine.add_binary = add
        engine.binaries.clear()
        engine.sync_entities()
    if (cb := getattr(entry.runtime_data, 'combos', None)) is not None:
        cb.add_binary = add
        cb.entities.clear()
        await cb.async_names()
        cb.start()

class HaufloLink(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = 'link'
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f'{entry.entry_id}_link'
        self._attr_device_info = device_info(entry.entry_id, entry)

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {'server': urlparse(self._entry.data.get(CONF_SERVER, '')).hostname, 'version': VERSION}

    @property
    def is_on(self) -> bool:
        agent = getattr(self._entry, 'runtime_data', None)
        return bool(agent and agent.linked)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_LINK, self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()

class HaufloHouseProblem(HouseEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, 'house_problem', 'binary_sensor')

    @property
    def is_on(self) -> bool | None:
        st = self._state()
        return st['level'] != 'ok' if st else None
