"""norm, what is off it and why — as its OWN item of HA's sidebar next to «Дом», not a tab of the dashboard. A panel of
HA's own kind (`custom`, like HACS' or Energy's own page): the element is the cards' `hauflo-analysis-panel`
(hauflo-cards.js), loaded from the same file that serves the cards; the data is the agent's GET /api/hauflo/insights.

Who sees it is the integrator's choice for the diagnosis (`insight.diag_visible`, pushed with the site's signals): HA's
administrators only, or everyone — re-registered when that choice changes. The address, icon, element and title are
tuning.json «analysis»."""
from __future__ import annotations
import functools
import logging
from homeassistant.components import frontend
from homeassistant.core import HomeAssistant
from .const import DATA_CARDS_FILE, site_ru
from .guard import contained
from .tuning import tuning
_LOGGER = logging.getLogger(__name__)
_a = functools.cache(lambda: tuning()['analysis'])
url_path = functools.cache(lambda: _a()['url_path'])
icon = functools.cache(lambda: _a()['icon'])
element = functools.cache(lambda: _a()['element'])
titles = functools.cache(lambda: _a()['title'])
DATA_ADMIN = 'hauflo_analysis_admin'
DATA_TITLE = 'hauflo_analysis_title'

def title_of(language: str | None, config: object=None) -> str:
    """    the alarm engine's config, else HA's — «Analysis» stood beside a Russian dashboard on an English HA."""
    return titles()['ru'] if site_ru(language, config) else titles()['en']

def panel_config(module_url: str) -> dict:
    """HA's `custom` panel config: our element from the cards' file, in HA's own page (no iframe)."""
    return {'_panel_custom': {'name': element(), 'module_url': module_url, 'embed_iframe': False, 'trust_external': False}}

def async_register(hass: HomeAssistant, admin_only: bool, module_url: str | None=None, config: object=None) -> bool:
    """The panel in the sidebar; again only when who may see it or its title (the house's language) changed. Returns
    True when (re)registered. `config` — the alarm engine's (its `lang`: the project's language)."""
    url = module_url or hass.data.get(DATA_CARDS_FILE)
    if not url:
        return False
    was = hass.data.get(DATA_ADMIN)
    title = title_of(hass.config.language, config)
    if was is admin_only and hass.data.get(DATA_TITLE) == title:
        return False
    frontend.async_register_built_in_panel(hass, component_name='custom', sidebar_title=title, sidebar_icon=icon(), frontend_url_path=url_path(), config=panel_config(url), require_admin=admin_only, update=was is not None)
    hass.data[DATA_ADMIN] = admin_only
    hass.data[DATA_TITLE] = title
    return True

def async_unregister(hass: HomeAssistant) -> None:
    hass.data.pop(DATA_TITLE, None)
    if hass.data.pop(DATA_ADMIN, None) is None:
        return
    with contained(_LOGGER, 'Hauflo: analysis panel not removed'):
        frontend.async_remove_panel(hass, url_path())
