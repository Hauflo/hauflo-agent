"""The client's dashboard outlives the agent (integrator run 24.10: removing the integration and
restarting HA turned every «Atelier» card into «Configuration error»).

Removed: the cards, icons and font are copied to /config/www/hauflo and added as Lovelace resources
(HA serves /local itself) — the dashboard keeps working without the agent (theme aside). Set up
again: those resources are taken off — the agent serves the files.

«Сделать дом независимым» (independent.py) makes it permanent: the copy and the resources stay while the agent is
installed too (refreshed on every start, the agent no longer adds its own copy), so the dashboard doesn't depend on
the agent however it is removed (HACS first, a restart, a broken update).

Installed (not independent): the agent's own addresses (/hauflo_static/…) are Lovelace resources too, besides HA's
extra JS. HA's frontend reads the extra JS only when a page loads, the resources when its first dashboard opens: a
browser tab opened before the agent was set up (paired in that very tab) showed «Configuration error» on every Hauflo
card of the new dashboard until a reload (integrator review r2, D2). Removed or independent: those go, /local ones come.

Dashboards' resources kept in configuration.yaml (Lovelace YAML mode) can't be added by anyone but a person: the copy
is made and HA says in a notification what to add.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant

from .storage import HaufloStore

_LOGGER = logging.getLogger(__name__)
LOCAL = "/local/hauflo/"
SERVED = "/hauflo_static/"  # the agent's own static path (theme.py)
MODULES = ("hauflo-icons.js", "hauflo-cards.js")
STORE_KEY = "hauflo.independent"
NOTE_ID = "hauflo_dashboard_resources"
# The result of a keep: resources added (or already there), or HA keeps them in YAML (a person adds them).
ADDED = "added"
YAML = "yaml"


def _resources(hass: HomeAssistant):
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    data = hass.data.get(LOVELACE_DATA)
    res = getattr(data, "resources", None)
    # Only the storage collection can be changed (YAML resources are the human's file).
    return res if res is not None and hasattr(res, "async_create_item") else None


async def _copy(hass: HomeAssistant) -> None:
    src = Path(__file__).parent / "www"
    dst = Path(hass.config.path("www", "hauflo"))
    await hass.async_add_executor_job(lambda: shutil.copytree(src, dst, dirs_exist_ok=True))


async def async_keep_dashboard(hass: HomeAssistant, version: str) -> str:
    """Copies the cards to /config/www/hauflo and adds them as resources (a resource of another version is moved to
    this one). Returns ADDED, or YAML when only a person can add them (said in a notification)."""
    await _copy(hass)
    res = _resources(hass)
    if res is None:
        _say_yaml(hass)
        return YAML
    await res.async_get_info()
    # The agent's own addresses die with it.
    changed = await _drop(res, SERVED)
    for name in MODULES:
        url = f"{LOCAL}{name}?v={version}"
        have = [item for item in res.async_items() if str(item.get("url", "")).startswith(LOCAL + name)]
        if not have:
            await res.async_create_item({"res_type": "module", "url": url})
            changed = True
        elif have[0].get("url") != url:
            # The browser's cached copy of the old version mustn't stay: the new one under its own address.
            await res.async_update_item(have[0]["id"], {"res_type": "module", "url": url})
            changed = True
    if changed:
        await _save_now(res)
    return ADDED


def _say_yaml(hass: HomeAssistant) -> None:
    ru = str(hass.config.language or "").startswith("ru")
    lines = "\n".join(f"    - url: {LOCAL}{n}\n      type: module" for n in MODULES)
    persistent_notification.async_create(
        hass,
        (f"Карточки Hauflo скопированы в /config/www/hauflo, но ресурсы панелей здесь задаются в configuration.yaml — "
         f"добавьте их сами, иначе карточки Hauflo без агента не загрузятся:\n\n```yaml\nlovelace:\n  resources:\n{lines}\n```"
         if ru else
         f"The Hauflo cards are copied to /config/www/hauflo, but this Home Assistant keeps dashboard resources in "
         f"configuration.yaml — add them yourself, or the Hauflo cards won't load without the agent:\n\n```yaml\nlovelace:\n  resources:\n{lines}\n```"),
        "Hauflo",
        NOTE_ID,
    )


async def _save_now(res) -> None:
    """The collection saves after a delay; an HA restart right after the removal lost the resources."""
    store = getattr(res, "store", None)
    data = getattr(res, "_data_to_save", None)
    if store is not None and callable(data):
        await store.async_save(data())


async def async_independent(hass: HomeAssistant) -> bool:
    data = await HaufloStore(hass, STORE_KEY).async_load()
    return isinstance(data, dict) and data.get("cards") is True


async def async_make_independent(hass: HomeAssistant, version: str) -> str:
    """The copy and its resources for good; remembered only when HA really took the resources."""
    how = await async_keep_dashboard(hass, version)
    if how == ADDED:
        await HaufloStore(hass, STORE_KEY).async_save({"cards": True})
    return how


async def async_on_start(hass: HomeAssistant, version: str) -> bool:
    """At setup: an independent house gets its copy refreshed (this agent's cards); otherwise the fallback resources an
    earlier removal left go — the agent serves the files. Returns whether the house is independent."""
    if await async_independent(hass):
        if await async_keep_dashboard(hass, version) == ADDED:
            return True
        # The resources can't be kept any more (switched to YAML): the agent serves its cards again.
        return False
    await async_release(hass, version)
    return False


async def _drop(res, prefix: str) -> bool:
    removed = False
    for item in list(res.async_items()):
        if str(item.get("url", "")).startswith(prefix):
            await res.async_delete_item(item["id"])
            removed = True
    return removed


async def async_release(hass: HomeAssistant, version: str | None = None) -> None:
    """The agent serves the cards: the fallback copies' resources go; with `version`, its own addresses are resources
    (of this version — a browser's cached copy of another one mustn't stay). YAML resources: the extra JS alone."""
    res = _resources(hass)
    if res is None:
        return
    await res.async_get_info()
    changed = await _drop(res, LOCAL)
    for name in MODULES if version else ():
        url = f"{SERVED}{name}?v={version}"
        have = [item for item in res.async_items() if str(item.get("url", "")).startswith(SERVED + name)]
        for extra in have[1:]:
            await res.async_delete_item(extra["id"])
            changed = True
        if not have:
            await res.async_create_item({"res_type": "module", "url": url})
            changed = True
        elif have[0].get("url") != url:
            await res.async_update_item(have[0]["id"], {"res_type": "module", "url": url})
            changed = True
    if changed:
        await _save_now(res)
