"""The KNX addresses Hauflo owns, may write and may listen to."""
from __future__ import annotations
from collections.abc import Mapping
import functools
import logging
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from .. import issues
from ..guard import contained
from ..issues import degrade
from .const import ALLOWED_ITEM, KNX_CONN_KEYS
_LOGGER = logging.getLogger(__name__)

def owned_knx(hass: HomeAssistant | None, entity_id: Any, owned: set[str] | None) -> bool:
    """A KNX UI entity Hauflo created or took over (its unique_id is in the agent's store) — never one the
    integrator made by hand (security re-review round 3: knx/delete_entity reached any KNX entity)."""
    if hass is None or owned is None or (not isinstance(entity_id, str)):
        return False
    entry = er.async_get(hass).async_get(entity_id)
    return entry is not None and entry.platform == 'knx' and (entry.unique_id in owned)

def ga_key(value: Any) -> str | None:
    """One spelling per group address (integrator run 22: «7/1793», «16129», 16129 and «07/07/001» all reached the
    gate relay's 7/7/1). Three-level, two-level, free and numeric forms → the raw number; an internal address stays
    itself; anything else → None (refused)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if 0 <= value <= 65535 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith('i-') or text.startswith('i_'):
        return text
    parts = text.split('/')
    try:
        nums = [int(x, 10) for x in parts]
    except ValueError:
        return None
    if any((n < 0 for n in nums)):
        return None
    if len(nums) == 3 and nums[0] <= 31 and (nums[1] <= 7) and (nums[2] <= 255):
        return str(nums[0] << 11 | nums[1] << 8 | nums[2])
    if len(nums) == 2 and nums[0] <= 31 and (nums[1] <= 2047):
        return str(nums[0] << 11 | nums[1])
    if len(nums) == 1 and nums[0] <= 65535:
        return str(nums[0])
    return None

def ga_range(key: str) -> str | None:
    """A group address key (its raw number) → «main/middle»."""
    try:
        raw = int(key)
    except (TypeError, ValueError):
        return None
    return f'{raw >> 11}/{raw >> 8 & 7}'

def ga_text(key: str) -> str | None:
    """A group address key (its raw number) → «main/middle/sub»."""
    try:
        raw = int(key)
    except (TypeError, ValueError):
        return None
    return f'{raw >> 11}/{raw >> 8 & 7}/{raw & 255}'

def range_allows(allowed: set[str], key: str) -> bool:
    """Is this group address (its raw number) in what the integrator allowed: a main/middle group «1/1», an address"""
    try:
        raw = int(key)
    except (TypeError, ValueError):
        return False
    main, middle, sub = (raw >> 11, raw >> 8 & 7, raw & 255)
    for item in allowed:
        m = ALLOWED_ITEM.fullmatch(str(item).strip())
        if not m or int(m.group(1)) != main or int(m.group(2)) != middle:
            continue
        if m.group(3) is None:
            return True
        lo = int(m.group(3))
        hi = int(m.group(4)) if m.group(4) is not None else lo
        if lo <= sub <= hi:
            return True
    return False

def knx_writes(value: Any) -> set[str]:
    """Every group address a KNX UI config sends to («write»), however deep and however spelled; one that can't be
    read is «?» — matches nothing of the integrator's, so the caller refuses it separately."""
    out: set[str] = set()
    if isinstance(value, dict):
        if 'write' in value and value['write'] not in (None, ''):
            out.add(ga_key(value['write']) or '?')
        for v in value.values():
            out |= knx_writes(v)
    elif isinstance(value, list):
        for v in value:
            out |= knx_writes(v)
    return out

def _knx_listens(value: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(value, dict):
        for key in ('write', 'state'):
            if value.get(key) not in (None, ''):
                out.add(ga_key(value[key]) or '?')
        passive = value.get('passive')
        for ga in passive if isinstance(passive, list) else []:
            out.add(ga_key(ga) or '?')
        for v in value.values():
            out |= _knx_listens(v)
    elif isinstance(value, list):
        for v in value:
            out |= _knx_listens(v)
    return out

@functools.cache
def knx_key() -> Any:
    """HA's key of the running KNX integration (KNX_MODULE_KEY), imported on first use — HA's knx only when a house
    has it; «knx» when HA has none to import. Cached: a constant of this HA, no module state to append to."""
    try:
        from homeassistant.components.knx.const import KNX_MODULE_KEY
    except ImportError:
        _LOGGER.debug('Hauflo: no xknx installed / an older HA: the plain key', exc_info=True)
        return 'knx'
    return KNX_MODULE_KEY

@functools.cache
def knx_store_errors() -> tuple[type[Exception], ...]:
    """What KNX's config store raises for an entity it has no UI config for (ConfigStoreException, imported when KNX is
    there; the store's own «not found» as KeyError/ValueError otherwise)."""
    try:
        from homeassistant.components.knx.storage.config_store import ConfigStoreException
    except ImportError:
        return (KeyError, ValueError)
    return (ConfigStoreException, KeyError, ValueError)

def knx_data(hass: HomeAssistant | None) -> Any | None:
    """The running KNX integration (KNXModule), None without KNX."""
    data = getattr(hass, 'data', None)
    if data is None:
        return None
    try:
        mod = data.get(knx_key()) or data.get('knx')
    except (AttributeError, TypeError) as err:
        degrade(hass, 'knx integration data', err, issues.HA)
        return None
    issues.recover(hass, 'knx integration data')
    return mod

def knx_store(hass: HomeAssistant | None) -> dict[str, Any] | None:
    """The KNX UI entity store ({platform: {unique id: config}}); None without KNX or when its shape changed."""
    knx = knx_data(hass)
    if knx is None:
        return None
    try:
        store = knx.config_store.data['entities']
        if not isinstance(store, dict):
            raise TypeError(f'entities is {type(store).__name__}')
    except (AttributeError, KeyError, TypeError) as err:
        degrade(hass, 'knx entity store', err, issues.HA)
        return None
    issues.recover(hass, 'knx entity store')
    return store

def foreign_knx_writes(hass: HomeAssistant | None, owned: set[str] | None, skip: str | None=None) -> set[str] | None:
    """The addresses the integrator's own KNX UI entities send to or listen on — Hauflo's never send there (panel
    round 6: a Siri-exposed «Kitchen light» rewired to the gate relay's address; integrator run 23: to its passive
    7/7/5, which the same actuator obeys). None when it can't be known."""
    store = knx_store(hass)
    if store is None:
        return set() if hass is not None and knx_data(hass) is None else None
    out: set[str] = set()
    for configs in store.values():
        for uid, config in configs.items():
            if uid != skip and (owned is None or uid not in owned):
                out |= _knx_listens(config.get('knx') if isinstance(config, dict) else None)
    for ga, ids in (getattr(knx_data(hass), 'group_address_entities', None) or {}).items():
        if any((getattr(i, 'unique_id', None) != skip and (owned is None or getattr(i, 'unique_id', None) not in owned) for i in ids)):
            raw = getattr(ga, 'raw', None)
            key = str(raw) if isinstance(raw, int) else ga_key(str(ga))
            if key:
                out.add(key)
    with contained(_LOGGER, 'Hauflo: xknx devices not read — the lists above still hold'):
        mine: set[str] = set()
        for configs in store.values():
            for uid, config in configs.items():
                if owned is not None and uid in owned:
                    mine |= _knx_listens(config.get('knx') if isinstance(config, dict) else None)
        for device in list(knx_data(hass).xknx.devices):
            for ga in device.group_addresses():
                raw = getattr(ga, 'raw', None)
                key = str(raw) if isinstance(raw, int) else ga_key(str(ga))
                if key and key not in mine:
                    out.add(key)
    out.discard('?')
    return out

def knx_addresses(hass: HomeAssistant | None) -> dict[str, list[str]]:
    """entity → the KNX group addresses («main/middle/sub») it sends to or listens on — what the integrator knows a
    signal
    by (0.33 Р26 «Анализ»: what is analysed). HA's running KNX entities, UI and YAML; no KNX here — empty."""
    out: dict[str, list[str]] = {}
    for ga, ents in (getattr(knx_data(hass), 'group_address_entities', None) or {}).items():
        raw = getattr(ga, 'raw', None)
        text = ga_text(str(raw)) if isinstance(raw, int) else str(ga)
        for ent in ents or ():
            eid = getattr(ent, 'entity_id', None)
            if isinstance(eid, str) and text and (text not in out.setdefault(eid, [])):
                out[eid].append(text)
    for eid in out:
        out[eid].sort(key=lambda t: [int(x) if x.isdigit() else 0 for x in t.split('/')])
    return out

def foreign_holders(hass: HomeAssistant | None, owned: set[str] | None, skip: str | None=None) -> dict[str, list[tuple[str | None, int | None]]] | None:
    """Per address, who else listens there: (platform, scene number if known). An address only xknx knows (an
    exposure, the time server) gets a holder of no platform — never shared."""
    store = knx_store(hass)
    if store is None:
        return None
    gae = getattr(knx_data(hass), 'group_address_entities', None) or {}
    out: dict[str, list[tuple[str | None, int | None]]] = {}
    mine: set[str] = set()
    for platform, configs in store.items():
        for uid, config in configs.items():
            knx = config.get('knx') if isinstance(config, dict) else None
            gas = _knx_listens(knx)
            if uid == skip or (owned is not None and uid in owned):
                mine |= gas
                continue
            num = knx.get('scene_number') if isinstance(knx, dict) else None
            for ga in gas - {'?'}:
                out.setdefault(ga, []).append((platform, num if isinstance(num, int) else None))
    for ga, ids in gae.items():
        raw = getattr(ga, 'raw', None)
        key = str(raw) if isinstance(raw, int) else ga_key(str(ga))
        for i in ids:
            if getattr(i, 'ui', False) or getattr(i, 'unique_id', None) == skip or (owned is not None and getattr(i, 'unique_id', None) in owned):
                continue
            if key:
                out.setdefault(key, []).append((getattr(i, 'platform', None), None))
    with contained(_LOGGER, 'Hauflo: xknx devices not read — the addresses above still hold'):
        for device in list(knx_data(hass).xknx.devices):
            for ga in device.group_addresses():
                raw = getattr(ga, 'raw', None)
                key = str(raw) if isinstance(raw, int) else ga_key(str(ga))
                if key and key not in mine and (key not in out):
                    out[key] = [(None, None)]
    return out

def knx_conn(data: Any) -> dict[str, Any] | None:
    """HA's KNX config entry data → the passport's connection facts (an allowlist; secure only as a yes/no).
    empty)."""
    if not isinstance(data, Mapping):
        return None
    out: dict[str, Any] = {}
    for key, name in KNX_CONN_KEYS.items():
        v = data.get(key)
        if isinstance(v, bool) or (isinstance(v, int) and 0 <= v <= 65535):
            out[name] = v
        elif isinstance(v, str) and v:
            out[name] = v[:64]
    ctype = str(data.get('connection_type') or '')
    out['secure'] = ctype.endswith('_secure') or bool(data.get('knxkeys_filename'))
    return out
