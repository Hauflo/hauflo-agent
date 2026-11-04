"""«Правила по значениям»: one on / off switch per value rule (rules.py)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .season import SeasonAuto

# Entities the agent itself writes (push, no polling); their actions run as they come.
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    # «Переключать автоматически» (season.py): staff only, push-updated.
    if (season := getattr(entry.runtime_data, "season", None)) is not None:
        add([SeasonAuto(entry, season)])
    engine = getattr(entry.runtime_data, "rules", None)
    if engine is None:
        return
    engine.add_switches = add
    engine.switches.clear()
    engine.sync_entities()
