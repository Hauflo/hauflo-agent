"""The input helpers the alarm automations keep their state in."""
from __future__ import annotations
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from .. import helper_store, issues
from .common import HELPERS_FEATURE, KIND

async def collection(hass: HomeAssistant, domain: str='input_boolean') -> Any:
    """A helper domain's items and changes through HA's own websocket commands — what HA's helper editor sends
    (helper_store.py; HA master review 0.40: never its private handler registry). None when out of reach."""
    return await helper_store.async_store(hass, domain)

async def helpers(hass: HomeAssistant) -> Any:
    """HA's input_boolean storage, None when its internals changed (named in Repairs; the rules go to the agent's
    engine)."""
    if 'input_boolean' not in hass.config.components:
        await async_setup_component(hass, 'input_boolean', {})
    if (coll := (await collection(hass))) is None:
        issues.degrade(hass, HELPERS_FEATURE, LookupError('input_boolean helper commands not found'), issues.HA)
        return None
    issues.recover(hass, HELPERS_FEATURE)
    return coll

def ack_entity(hass: HomeAssistant, object_id: str) -> str:
    return er.async_get(hass).async_get_entity_id('input_boolean', 'input_boolean', object_id) or f'input_boolean.{object_id}'

async def ensure_helper(hass: HomeAssistant, coll: Any, object_id: str, name: str, icon: str) -> str:
    """One of the rule's helpers («Принято», «авария открыта») — made like HA's helper editor makes it (the id from its
    first name), then named."""
    if object_id not in coll.data:
        await coll.async_create_item({'name': object_id, 'icon': icon})
    if object_id not in coll.data:
        raise RuntimeError(f'helper {object_id} not made')
    item = coll.data[object_id]
    if item.get('name') != name or item.get('icon') != icon:
        await coll.async_update_item(object_id, {'name': name, 'icon': icon})
    return ack_entity(hass, object_id)

async def drop_helper(hass: HomeAssistant, coll: Any, object_id: str) -> None:
    if coll is not None and object_id in coll.data:
        await coll.async_delete_item(object_id)

async def drop_ours(hass: HomeAssistant, colls: dict[str, Any], object_id: str) -> None:
    """One of the rules' helpers, by its prefix (anything else is never touched)."""
    for domain, prefix in KIND:
        if object_id.startswith(prefix):
            await drop_helper(hass, colls.get(domain), object_id)

def empty() -> dict[str, Any]:
    return {'rules': 0, 'added': [], 'updated': [], 'restored': [], 'unchanged': [], 'removed': [], 'kept_edited': [], 'engine': [], 'warnings': [], 'migrate': [], 'error': None, 'error_code': None}
