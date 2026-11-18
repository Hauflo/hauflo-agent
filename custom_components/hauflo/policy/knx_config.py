"""KNX scenes and config entries the cloud may change."""
from __future__ import annotations
import contextlib
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from .const import SCENE_EID
from .knx import foreign_holders, foreign_knx_writes, ga_key, ga_text, knx_store, knx_writes, range_allows

def _knx_scenes_owned(hass: HomeAssistant | None, owned: set[str] | None) -> dict[str, tuple[str, str, int]]:
    if hass is None or not owned:
        return {}
    configs = (knx_store(hass) or {}).get('scene') or {}
    if not configs or not isinstance(configs, dict):
        return {}
    reg = er.async_get(hass)
    out: dict[str, tuple[str, str, int]] = {}
    for uid, config in configs.items():
        if uid not in owned or not isinstance(config, dict) or (not isinstance(config.get('knx'), dict)):
            continue
        knx = config['knx']
        ga = knx.get('ga_scene')
        key = ga_key(ga.get('write')) if isinstance(ga, dict) else None
        number = knx.get('scene_number')
        if key is None or not key.isdigit() or isinstance(number, bool) or (not isinstance(number, int)) or (not 1 <= number <= 64):
            continue
        eid = reg.async_get_entity_id('scene', 'knx', uid)
        if eid and SCENE_EID.fullmatch(eid):
            out[eid] = (uid, key, number)
    return out

def knx_scene_target(hass: HomeAssistant | None, owned: set[str] | None, allowed: set[str], entity_id: Any) -> tuple[str, int]:
    """Where a KNX scene's learn telegram goes: (address «main/middle/sub», scene number). Only from the agent's own
    HA, or anything but a scene listens there (an actuator's switch object on the same address would take the byte
    as a command)."""
    if not isinstance(entity_id, str) or not SCENE_EID.fullmatch(entity_id):
        raise PermissionError('not a scene')
    found = _knx_scenes_owned(hass, owned).get(entity_id)
    if found is None:
        raise PermissionError("not a KNX scene of Hauflo's")
    uid, key, number = found
    if not range_allows(allowed, key):
        raise PermissionError("its address isn't allowed in Home Assistant")
    holders = foreign_holders(hass, None, uid)
    if holders is None or any((p != 'scene' for p, _n in holders.get(key, []))):
        raise PermissionError('not only scenes listen on its address')
    text = ga_text(key)
    if text is None:
        raise PermissionError('address')
    return (text, number)

def knx_scenes_learnable(hass: HomeAssistant | None, owned: set[str] | None, allowed: set[str]) -> dict[str, int]:
    """The KNX scenes the panel offers «Запомнить как сейчас» / «Переименовать» on: entity id → scene number."""
    out: dict[str, int] = {}
    for eid in _knx_scenes_owned(hass, owned):
        with contextlib.suppress(PermissionError):
            out[eid] = knx_scene_target(hass, owned, allowed, eid)[1]
    return out

def knx_config_ok(hass: HomeAssistant | None, msg: dict[str, Any], owned: set[str] | None) -> bool:
    data = msg.get('data')
    writes = knx_writes(data.get('knx') if isinstance(data, dict) else None)
    if not writes:
        return True
    uid = None
    if msg.get('type') == 'knx/update_entity' and hass is not None:
        entry = er.async_get(hass).async_get(msg.get('entity_id'))
        uid = entry.unique_id if entry is not None else None
    if '?' in writes:
        return False
    foreign = foreign_knx_writes(hass, owned, uid)
    if foreign is None:
        return False
    clash = writes & foreign
    if not clash:
        return True
    knx = data.get('knx') if isinstance(data, dict) else None
    number = knx.get('scene_number') if isinstance(knx, dict) else None
    holders = foreign_holders(hass, owned, uid)
    if holders is None:
        return False
    return all((_overlap_kind(msg.get('platform'), number, True, True, holders.get(ga) or [(None, None)]) != 'foreign_write' for ga in clash))

def _overlap_kind(platform: Any, number: Any, write: bool, main: bool, holders: list) -> str | None:
    if not holders:
        return None
    no = number if isinstance(number, int) and (not isinstance(number, bool)) else None
    if write:
        return 'scene_shared' if platform == 'scene' and no is not None and all((p == 'scene' and n != no for p, n in holders)) else 'foreign_write'
    if main and any((p == platform and (platform != 'scene' or n == no) for p, n in holders)):
        return 'duplicate'
    return 'shared_listen'

def _owned_knx_writes(hass: HomeAssistant | None, owned: set[str] | None) -> set[str]:
    store = knx_store(hass)
    if store is None:
        return set()
    out: set[str] = set()
    for configs in store.values():
        for uid, config in configs.items():
            if owned is not None and uid in owned:
                out |= knx_writes(config.get('knx') if isinstance(config, dict) else None)
    out.discard('?')
    return out
