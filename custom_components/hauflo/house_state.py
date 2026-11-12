"""«Состояние объекта» (п.12): the house's level by the one calculator (Agent.house_now — the same as the cards' and
Hauflo's monitoring) as HA entities for the client's own automations: `sensor.hauflo_house_state` (ok / fault / alarm /
offline, with the counts, the active signals and since when) and `binary_sensor.hauflo_house_problem` (on — anything but
ok). Push-updated by the agent's recompute (dispatcher), never polled, no timer of their own."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .alarms_now import ITEMS_MAX
from .const import DOMAIN, VERSION
from .device import device_info

SIGNAL_HOUSE = f"{DOMAIN}_house"
LEVELS = ["ok", "fault", "alarm", "offline"]


def facts(st: dict[str, Any]) -> dict[str, Any]:
    """The sensor's attributes from Agent.house_state: counts by level, «не решено», «отложено», since, the signals."""
    faults = st["faults"]
    live = [f for f in faults if not f["offline"]]
    return {
        "alarm": sum(1 for f in live if f["house"] == "alarm"),
        "fault": sum(1 for f in live if f["house"] != "alarm"),
        "offline": len(faults) - len(live),
        "undetermined": st["undetermined"],
        "shelved": len(st["held"]),
        "since": st["since"],
        # Only the id and its level — what an automation needs; names and texts stay in the cards.
        "signals": [{"entity": f["entity"], "level": "offline" if f["offline"] else f["house"]} for f in faults[:ITEMS_MAX]],
        "version": VERSION,
    }


class HouseEntity(Entity):
    """The two entities' one way: the agent's last house_now, refreshed on its dispatch."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, key: str, domain: str) -> None:
        self._entry = entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry.entry_id, entry)
        # A fixed id the client's automations name (not the translated name's: it stays the same in any language).
        self.entity_id = f"{domain}.{DOMAIN}_{key}"

    def _state(self) -> dict[str, Any] | None:
        return getattr(getattr(self._entry, "runtime_data", None), "house_state", None)

    @property
    def available(self) -> bool:
        # The house's own calculator, not the link to Hauflo: it works with the link down too.
        return self._state() is not None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_HOUSE, self._changed))
        agent = getattr(self._entry, "runtime_data", None)
        if self._state() is None and agent is not None and hasattr(agent, "house_now"):
            agent.house_now()

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
