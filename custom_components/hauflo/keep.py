"""The client's dashboard outlives the agent (integrator run 24.10: removing the integration and
restarting HA turned every «Atelier» card into «Configuration error»).

Removed: the cards, icons and font are copied to /config/www/hauflo and added as Lovelace resources
(HA serves /local itself) — the dashboard keeps working without the agent (theme aside). Set up
again: those resources are taken off — the agent serves the files.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
LOCAL = "/local/hauflo/"
MODULES = ("hauflo-icons.js", "hauflo-cards.js")


def _resources(hass: HomeAssistant):
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    data = hass.data.get(LOVELACE_DATA)
    res = getattr(data, "resources", None)
    # Only the storage collection can be changed (YAML resources are the human's file).
    return res if res is not None and hasattr(res, "async_create_item") else None


async def async_keep_dashboard(hass: HomeAssistant, version: str) -> None:
    src = Path(__file__).parent / "www"
    dst = Path(hass.config.path("www", "hauflo"))
    await hass.async_add_executor_job(lambda: shutil.copytree(src, dst, dirs_exist_ok=True))
    res = _resources(hass)
    if res is None:
        return
    await res.async_get_info()
    have = [item.get("url", "") for item in res.async_items()]
    for name in MODULES:
        if not any(url.startswith(LOCAL + name) for url in have):
            await res.async_create_item({"res_type": "module", "url": f"{LOCAL}{name}?v={version}"})
    await _save_now(res)


async def _save_now(res) -> None:
    """The collection saves after a delay; an HA restart right after the removal lost the resources."""
    store = getattr(res, "store", None)
    data = getattr(res, "_data_to_save", None)
    if store is not None and callable(data):
        await store.async_save(data())


async def async_release(hass: HomeAssistant) -> None:
    res = _resources(hass)
    if res is None:
        return
    await res.async_get_info()
    removed = False
    for item in list(res.async_items()):
        if str(item.get("url", "")).startswith(LOCAL):
            await res.async_delete_item(item["id"])
            removed = True
    if removed:
        await _save_now(res)
