"""Marks on the protected covers, gates and doors, and their voice exposure."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from .. import issues, protect
from ..foreign import common as foreign_common
from ..guard import contained
from ..issues import degrading
from .const import ASSISTANTS, OWN_DEVICE_DOMAINS, PROTECTED_COVERS, WRAPPER_PLATFORMS
from .knx import ga_key, knx_store, knx_writes
from .own import wrapped_source
_LOGGER = logging.getLogger(__name__)

def _opening_gas(config: Any) -> set[str]:
    knx = config.get('knx') if isinstance(config, dict) else None
    out: set[str] = set()
    for key, v in knx.items() if isinstance(knx, dict) else ():
        if 'stop' in str(key) or not isinstance(v, dict):
            continue
        passive = v.get('passive') if isinstance(v.get('passive'), list) else []
        out |= {k for k in (ga_key(ga) for ga in [v.get('write'), *passive] if ga not in (None, '')) if k is not None}
    return out

def protect_refresh(hass: HomeAssistant | None) -> None:
    """Mark every KNX cover protected by its class now (sticky from here on)."""
    marks = protect.marks(hass)
    store = knx_store(hass) if marks is not None else None
    if not store:
        return
    with degrading(hass, 'protected covers', issues.AGENT):
        reg = er.async_get(hass)
        for uid, config in (store.get('cover') or {}).items():
            eid = reg.async_get_entity_id('cover', 'knx', uid)
            st = hass.states.get(eid) if eid else None
            entry = reg.async_get(eid) if eid else None
            classes = {st.attributes.get('device_class') if st else None, getattr(entry, 'device_class', None), getattr(entry, 'original_device_class', None)}
            if classes & PROTECTED_COVERS:
                marks.add(str(uid), _opening_gas(config))
        issues.recover(hass, 'protected covers')

def protect_marks_of(hass: HomeAssistant, entity_id: str) -> tuple[str | None, set[str]]:
    """What «hauflo.unprotect» clears for an entity: its KNX unique id and opening addresses."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None or entry.platform != 'knx':
        return (None, set())
    config = ((knx_store(hass) or {}).get(entry.domain) or {}).get(entry.unique_id)
    return (entry.unique_id, _opening_gas(config))

def protect_mark(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    """The deploy says what a KNX switch is FOR (red-team 2, A1): a gate's / an electric lock's relay is a plain switch to HA, so neither its class nor its domain tells the agent."""
    marks = protect.marks(hass)
    if marks is None:
        return []
    done: list[str] = []
    for eid in entity_ids:
        uid, gas = protect_marks_of(hass, eid)
        if uid is None:
            continue
        marks.add_deploy(str(uid), gas)
        done.append(eid)
    if done:
        _unexpose(hass, [*done, *_wrappers_of(hass, set(done))])
    return done

def protect_unmark(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    marks = protect.marks(hass)
    if marks is None:
        return []
    by_uid: dict[str, str] = {}
    store = knx_store(hass) or {}
    alive = {str(u) for configs in store.values() for u in configs or {}}
    for eid in entity_ids:
        uid, gas = protect_marks_of(hass, eid)
        if uid is None:
            continue
        by_uid[str(uid)] = eid
        entry = er.async_get(hass).async_get(eid)
        config = (store.get(entry.domain) or {}).get(entry.unique_id) if entry is not None else None
        sends = gas | knx_writes(config.get('knx') if isinstance(config, dict) else None)
        for old, old_gas in marks.deploy.items():
            if old not in alive and old_gas & sends:
                by_uid.setdefault(old, eid)
    gone = marks.unmark_deploy(set(by_uid))
    protect_refresh(hass)
    return list(dict.fromkeys((by_uid[u] for u in sorted(gone))))

def _wrappers_of(hass: HomeAssistant, sources: set[str]) -> list[str]:
    out: list[str] = []
    with contained(_LOGGER, 'Hauflo: entities wrapping the protected ones not listed'):
        for ent in list(er.async_get(hass).entities.values()):
            if ent.platform in WRAPPER_PLATFORMS and wrapped_source(hass, ent.entity_id) in sources:
                out.append(ent.entity_id)
    return out

def _unexpose(hass: HomeAssistant, entity_ids: list[str]) -> None:
    for eid in entity_ids:
        for assistant in ASSISTANTS:
            with contained(_LOGGER, 'Hauflo: %s not taken off %s', eid, assistant, expect=(HomeAssistantError, KeyError)):
                async_expose_entity(hass, assistant, eid, False)

def own_device(hass: HomeAssistant | None, device_id: Any) -> bool:
    if hass is None or not isinstance(device_id, str):
        return False
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return False
    domains = {e.domain for eid in foreign_common.device_entry_ids(device) if (e := hass.config_entries.async_get_entry(eid))}
    return bool(domains) and domains <= OWN_DEVICE_DOMAINS
