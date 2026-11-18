"""The canon files the agent reads — tuning, tunnel, servers, rules, recipes, integrations, theme, room pictures,
cards, intercom presets and their labels — read once, in the executor, before anything on the event loop needs them.

Each file is a functools.cache loader read on first use (nothing is read at import, HA core review); the first use is
here: async_setup_entry and the config flow await it, so no loader ever reads a file on the loop («Detected blocking
call to open»)."""
from __future__ import annotations
from homeassistant.core import HomeAssistant
from . import cards, room_pictures, theme
from .canon import rules_canon
from .install.common import integrations
from .intercom.door_config import presets_cfg
from .intercom.setup import preset_labels
from .recipes import recipes_data
from .servers import servers_cfg
from .tuning import tuning
from .tunnel import tunnel_cfg

def preload() -> None:
    """Read every canon file (blocking: run it in the executor). Cached — a second call costs nothing."""
    tuning()
    tunnel_cfg()
    servers_cfg()
    rules_canon()
    recipes_data()
    integrations()
    presets_cfg()
    preset_labels()
    room_pictures.pictures_canon()
    cards.own_cards()
    theme.aura()
    theme.extra()
    theme.asset_tag()

async def async_preload(hass: HomeAssistant) -> None:
    await hass.async_add_executor_job(preload)
