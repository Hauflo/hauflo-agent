from __future__ import annotations
import logging
from pathlib import Path
import shutil
from homeassistant.components import persistent_notification
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from . import cards
from .const import CARDS_FILE, OLD_CARDS_FILES
from .storage import HaufloStore
_LOGGER = logging.getLogger(__name__)
LOCAL = '/local/hauflo/'
SERVED = '/hauflo_static/'
ICONS = 'hauflo-icons.js'
MODULES = (ICONS, CARDS_FILE)

def modules(hass: HomeAssistant) -> tuple[str, ...]:
    """The icons and the cards file."""
    return MODULES
STORE_KEY = 'hauflo.independent'
NOTE_ID = 'hauflo_dashboard_resources'
ADDED = 'added'
YAML = 'yaml'

def _resources(hass: HomeAssistant):
    data = hass.data.get(LOVELACE_DATA)
    res = getattr(data, 'resources', None)
    return res if res is not None and hasattr(res, 'async_create_item') else None

async def _copy(hass: HomeAssistant) -> None:
    src = Path(__file__).parent / 'www'
    dst = Path(hass.config.path('www', 'hauflo'))
    await hass.async_add_executor_job(lambda: shutil.copytree(src, dst, dirs_exist_ok=True))

async def async_keep_dashboard(hass: HomeAssistant, version: str) -> str:
    """Copies the cards to /config/www/hauflo and adds them as resources (a resource of another version is moved to this one)."""
    plugin = await cards.async_find_plugin(hass)
    if plugin is not None and cards.at_least(plugin['version'], cards.own_cards().get('version')):
        await _release_ours(hass)
        return ADDED
    await _copy(hass)
    res = _resources(hass)
    if res is None:
        _say_yaml(hass)
        return YAML
    await res.async_get_info()
    changed = await _drop(res, SERVED)
    changed = await _drop_old(hass, res, LOCAL) or changed
    for name in modules(hass):
        url = f'{LOCAL}{name}?v={version}'
        have = [item for item in res.async_items() if str(item.get('url', '')).startswith(LOCAL + name)]
        if not have:
            await res.async_create_item({'res_type': 'module', 'url': url})
            changed = True
        elif have[0].get('url') != url:
            await res.async_update_item(have[0]['id'], {'res_type': 'module', 'url': url})
            changed = True
    if changed:
        await _save_now(res)
    return ADDED

def _say_yaml(hass: HomeAssistant) -> None:
    ru = str(hass.config.language or '').startswith('ru')
    lines = '\n'.join((f'    - url: {LOCAL}{n}\n      type: module' for n in modules(hass)))
    persistent_notification.async_create(hass, f'Карточки Hauflo скопированы в /config/www/hauflo, но ресурсы панелей здесь задаются в configuration.yaml — добавьте их сами, иначе карточки Hauflo без агента не загрузятся:\n\n```yaml\nlovelace:\n  resources:\n{lines}\n```' if ru else f"The Hauflo cards are copied to /config/www/hauflo, but this Home Assistant keeps dashboard resources in configuration.yaml — add them yourself, or the Hauflo cards won't load without the agent:\n\n```yaml\nlovelace:\n  resources:\n{lines}\n```", 'Hauflo', NOTE_ID)

async def _save_now(res) -> None:
    store = getattr(res, 'store', None)
    if store is not None:
        await store.async_save(res._data_to_save())

async def async_independent(hass: HomeAssistant) -> bool:
    data = await HaufloStore(hass, STORE_KEY).async_load()
    return isinstance(data, dict) and data.get('cards') is True

async def async_make_independent(hass: HomeAssistant, version: str) -> str:
    """The copy and its resources for good; remembered only when HA really took the resources."""
    how = await async_keep_dashboard(hass, version)
    if how == ADDED:
        await HaufloStore(hass, STORE_KEY).async_save({'cards': True})
    return how

async def async_on_start(hass: HomeAssistant, version: str) -> bool:
    """At setup: an independent house gets its copy refreshed (this agent's cards); otherwise the fallback resources an earlier removal left go — the agent serves the files."""
    source, plugin = await cards.async_choose(hass)
    if source == cards.PLUGIN:
        await _release_ours(hass)
        return True
    if await async_independent(hass):
        if await async_keep_dashboard(hass, version) == ADDED:
            return plugin is None
        return False
    await async_release(hass, version)
    return False

async def _drop_old(hass: HomeAssistant, res, base: str) -> bool:
    removed = False
    for name in OLD_CARDS_FILES:
        for item in list(res.async_items()):
            url = str(item.get('url', ''))
            if url == base + name or url.startswith(base + name + '?'):
                await res.async_delete_item(item['id'])
                removed = True
    return removed

async def _drop(res, prefix: str) -> bool:
    removed = False
    for item in list(res.async_items()):
        if str(item.get('url', '')).startswith(prefix):
            await res.async_delete_item(item['id'])
            removed = True
    return removed

async def _release_ours(hass: HomeAssistant) -> None:
    res = _resources(hass)
    if res is None:
        return
    await res.async_get_info()
    changed = await _drop(res, SERVED)
    changed = await _drop(res, LOCAL) or changed
    if changed:
        await _save_now(res)

async def async_release(hass: HomeAssistant, version: str | None=None) -> None:
    """The agent serves the cards: the fallback copies' resources go; with `version`, its own addresses are resources (of this version — a browser's cached copy of another one mustn't stay)."""
    res = _resources(hass)
    if res is None:
        return
    await res.async_get_info()
    changed = await _drop(res, LOCAL)
    if version:
        changed = await _drop_old(hass, res, SERVED) or changed
    for name in modules(hass) if version else ():
        url = f'{SERVED}{name}?v={version}'
        have = [item for item in res.async_items() if str(item.get('url', '')).startswith(SERVED + name)]
        for extra in have[1:]:
            await res.async_delete_item(extra['id'])
            changed = True
        if not have:
            await res.async_create_item({'res_type': 'module', 'url': url})
            changed = True
        elif have[0].get('url') != url:
            await res.async_update_item(have[0]['id'], {'res_type': 'module', 'url': url})
            changed = True
    if changed:
        await _save_now(res)
