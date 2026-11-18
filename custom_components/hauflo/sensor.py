from __future__ import annotations
from typing import Any
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN
from .device import device_info
from .house_state import LEVELS, HouseEntity, facts
from .intercom.entities import entities as intercom_entities
from .ownership import SIGNAL
from .panel import SIGNAL as SIGNAL_PANEL, STATES as PANEL_STATES
from .season.entities import SeasonCurrent, SeasonRecommended
PARALLEL_UPDATES = 0

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([HaufloOwnership(entry), HaufloHouseState(entry), HaufloPanel(entry)])
    add(intercom_entities(getattr(entry.runtime_data, 'intercom', None), 'sensor'))
    if (season := getattr(entry.runtime_data, 'season', None)) is not None:
        add([SeasonRecommended(entry, season), SeasonCurrent(entry, season)])
    if (engine := getattr(entry.runtime_data, 'rules', None)) is not None:
        engine.add_sensors = add
        engine.sensors.clear()
        engine.sync_entities()

class HaufloOwnership(SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = 'ownership'
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({'by_kind', 'hand_edited', 'renamed', 'disabled', 'kept_by_plan', 'next_deploy', 'label'})

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f'{entry.entry_id}_ownership'
        self._attr_device_info = device_info(entry.entry_id, entry)
        self._facts: dict[str, Any] = {}

    def _own(self) -> Any:
        agent = getattr(self._entry, 'runtime_data', None)
        return getattr(agent, 'ownership', None)

    @property
    def native_value(self) -> int | None:
        return self._facts.get('owned')

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {k: v for k, v in self._facts.items() if k != 'owned'}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL, self._changed))
        self._refresh()

    @callback
    def _refresh(self) -> None:
        own = self._own()
        self._facts = own.facts() if own is not None else {}

    @callback
    def _changed(self) -> None:
        self._refresh()
        self.async_write_ha_state()

class HaufloHouseState(HouseEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = LEVELS
    _unrecorded_attributes = frozenset({'signals', 'version'})

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, 'house_state', 'sensor')

    @property
    def native_value(self) -> str | None:
        st = self._state()
        return st['level'] if st else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        st = self._state()
        return facts(st) if st else None

class HaufloPanel(SensorEntity):
    """«Панель» (panel.py): what the cloud last said of the house's Hauflo panel, kept over restarts."""
    _attr_has_entity_name = True
    _attr_translation_key = 'panel'
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = PANEL_STATES
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f'{entry.entry_id}_panel'
        self._attr_device_info = device_info(entry.entry_id, entry)
        self.entity_id = f'sensor.{DOMAIN}_panel'

    def _data(self) -> dict[str, Any] | None:
        panel = getattr(getattr(self._entry, 'runtime_data', None), 'panel', None)
        return getattr(panel, 'data', None)

    @property
    def native_value(self) -> str | None:
        d = self._data()
        return d['state'] if d else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        d = self._data()
        return {k: d[k] for k in ('reason', 'agent', 'at')} if d else None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_PANEL, self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
