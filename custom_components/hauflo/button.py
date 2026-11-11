"""Р55: the doors' button entities (intercom/entities.py)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .intercom.entities import entities

# Push-updated, never polled.
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add(entities(getattr(entry.runtime_data, "intercom", None), "button"))
