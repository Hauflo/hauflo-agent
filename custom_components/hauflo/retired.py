"""What an earlier agent had and this one doesn't: taken away cleanly on update.

«Комфортный пол» (0.12–0.16, admin-only; owner 30.10: integrator logic done in KNX — removed): its «Комфорт · <комната>»
climates, the «Режим дома» select and its store go. Only the agent's own registry entries and its own store — never the
KNX thermostats it drove, never a service call: their setpoints and modes stay exactly as they are. Its house mode
(winter / summer) becomes the season's last choice if the season has none (the value rules' «season» condition keeps
its answer).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .storage import HaufloStore

_LOGGER = logging.getLogger(__name__)
COMFORT_STORE = f"{DOMAIN}.comfort"
_COMFORT_MODE = {"winter": "heat", "summer": "cool"}


def comfort_entities(hass: HomeAssistant, entry_id: str) -> list[str]:
    """The registry entries «Комфортный пол» made: the agent's own climates («comfort_<room>») and its house-mode select
    («house_mode», later «<entry_id>_house_mode»)."""
    reg = er.async_get(hass)
    out = []
    for ent in list(reg.entities.values()):
        if ent.platform != DOMAIN:
            continue
        uid = str(ent.unique_id)
        if (ent.domain == "climate" and uid.startswith("comfort_")) or (ent.domain == "select" and uid in ("house_mode", f"{entry_id}_house_mode")):
            out.append(ent.entity_id)
    return sorted(out)


async def async_retire_comfort(hass: HomeAssistant, entry_id: str, seed: Any = None) -> dict[str, Any]:
    """Removes «Комфортный пол»'s entities and store. `seed(mode)`: the season told its house mode (heat / cool)."""
    reg = er.async_get(hass)
    removed = comfort_entities(hass, entry_id)
    for eid in removed:
        reg.async_remove(eid)
    store = HaufloStore(hass, COMFORT_STORE)
    data = await store.async_load()
    mode = None
    if data is not None:
        mode = _COMFORT_MODE.get(str((data or {}).get("mode")))
        if mode and seed is not None:
            await seed(mode)
        await store.async_remove()
    if removed or data is not None:
        _LOGGER.info("Hauflo: the retired comfort floor removed (%d entities, store %s)", len(removed), "removed" if data is not None else "none")
    return {"entities": removed, "store": data is not None, "mode": mode}
