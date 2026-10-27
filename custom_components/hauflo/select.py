"""«Режим дома» (зима / лето) — with the first room of «Комфортный пол» (comfort.py)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    engine = entry.runtime_data.comfort
    engine.add_selects = add
    engine.select = None
    engine.sync_entities()
