from __future__ import annotations
import logging
from homeassistant.core import HomeAssistant
from .const import DATA_CARDS_FILE, site_ru
from .tuning import TUNING
_LOGGER = logging.getLogger(__name__)
_A = TUNING['analysis']
URL_PATH = _A['url_path']
ICON = _A['icon']
ELEMENT = _A['element']
TITLES = _A['title']
DATA_ADMIN = 'hauflo_analysis_admin'
DATA_TITLE = 'hauflo_analysis_title'

def title_of(language: str | None, config: object=None) -> str:
    return TITLES['ru'] if site_ru(language, config) else TITLES['en']

def panel_config(module_url: str) -> dict:
    return {'_panel_custom': {'name': ELEMENT, 'module_url': module_url, 'embed_iframe': False, 'trust_external': False}}

def async_register(hass: HomeAssistant, admin_only: bool, module_url: str | None=None, config: object=None) -> bool:
    from homeassistant.components import frontend
    url = module_url or hass.data.get(DATA_CARDS_FILE)
    if not url:
        return False
    was = hass.data.get(DATA_ADMIN)
    title = title_of(hass.config.language, config)
    if was is admin_only and hass.data.get(DATA_TITLE) == title:
        return False
    frontend.async_register_built_in_panel(hass, component_name='custom', sidebar_title=title, sidebar_icon=ICON, frontend_url_path=URL_PATH, config=panel_config(url), require_admin=admin_only, update=was is not None)
    hass.data[DATA_ADMIN] = admin_only
    hass.data[DATA_TITLE] = title
    return True

def async_unregister(hass: HomeAssistant) -> None:
    from homeassistant.components import frontend
    hass.data.pop(DATA_TITLE, None)
    if hass.data.pop(DATA_ADMIN, None) is None:
        return
    try:
        frontend.async_remove_panel(hass, URL_PATH)
    except Exception:
        _LOGGER.debug('Hauflo: analysis panel not removed', exc_info=True)
