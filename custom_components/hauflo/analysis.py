"""«Анализ» (0.33, owner 10.11/11.11 Р8/Р26): the house's diagnosis — what each detector learns about each device, its
norm, what is off it and why — as its OWN item of HA's sidebar next to «Дом», not a tab of the dashboard. A panel of
HA's own kind (`custom`, like HACS' or Energy's own page): the element is the cards' `hauflo-analysis-panel`
(hauflo-cards.js), loaded from the same file that serves the cards; the data is the agent's GET /api/hauflo/insights.

Who sees it is the integrator's choice for the diagnosis (`insight.diag_visible`, pushed with the site's signals): HA's
administrators only, or everyone — re-registered when that choice changes. The address, icon, element and title are
tuning.json «analysis»."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .const import DATA_CARDS_FILE, site_ru
from .tuning import TUNING

_LOGGER = logging.getLogger(__name__)
_A = TUNING["analysis"]
URL_PATH = _A["url_path"]
ICON = _A["icon"]
ELEMENT = _A["element"]
TITLES = _A["title"]
DATA_ADMIN = "hauflo_analysis_admin"  # the require_admin the panel was registered with (None — not registered)
DATA_TITLE = "hauflo_analysis_title"  # the sidebar title it was registered with


def title_of(language: str | None, config: object = None) -> str:
    """The sidebar's title in the house's ONE language (const.site_ru, fix3 п.3): the project's, as Hauflo wrote it into
    the alarm engine's config, else HA's — «Analysis» stood beside a Russian dashboard on an English HA."""
    return TITLES["ru"] if site_ru(language, config) else TITLES["en"]


def panel_config(module_url: str) -> dict:
    """HA's `custom` panel config: our element from the cards' file, in HA's own page (no iframe)."""
    return {"_panel_custom": {"name": ELEMENT, "module_url": module_url, "embed_iframe": False, "trust_external": False}}


def async_register(hass: HomeAssistant, admin_only: bool, module_url: str | None = None, config: object = None) -> bool:
    """The panel in the sidebar; again only when who may see it or its title (the house's language) changed. Returns
    True when (re)registered. `config` — the alarm engine's (its `lang`: the project's language)."""
    from homeassistant.components import frontend  # noqa: PLC0415

    url = module_url or hass.data.get(DATA_CARDS_FILE)
    if not url:
        return False  # the cards are not served (no www): no element to show
    was = hass.data.get(DATA_ADMIN)
    title = title_of(hass.config.language, config)
    if was is admin_only and hass.data.get(DATA_TITLE) == title:
        return False
    frontend.async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title=title,
        sidebar_icon=ICON,
        frontend_url_path=URL_PATH,
        config=panel_config(url),
        require_admin=admin_only,
        update=was is not None,
    )
    hass.data[DATA_ADMIN] = admin_only
    hass.data[DATA_TITLE] = title
    return True


def async_unregister(hass: HomeAssistant) -> None:
    from homeassistant.components import frontend  # noqa: PLC0415

    hass.data.pop(DATA_TITLE, None)
    if hass.data.pop(DATA_ADMIN, None) is None:
        return
    try:
        frontend.async_remove_panel(hass, URL_PATH)
    except Exception:  # noqa: BLE001 — HA unloading: nothing to keep
        _LOGGER.debug("Hauflo: analysis panel not removed", exc_info=True)
