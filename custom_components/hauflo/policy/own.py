"""What Hauflo owns in this HA and what stays protected."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from ..guard import contained
from .const import ENTITY_REF, HELPER_DOMAINS, NEVER_EXPOSE, OWN_PLATFORMS, PROTECT_DATA, PROTECTED_COVERS, SERVICE_KEYS, UNKNOWN_DOMAINS, WRAPPER_PLATFORMS, entry_platforms, gate_name
from .knx import knx_store, knx_writes, owned_knx
_LOGGER = logging.getLogger(__name__)

def own_entity(hass: HomeAssistant | None, entity_id: Any, owned_entries: set[str] | None=None) -> bool:
    """Hauflo's own: KNX / its agent's entities, a group or a rule sensor it set up through the tunnel, a scene it wrote (hauflo_…) — never the integrator's own group or template helper or scene (security re-review round 5)."""
    if hass is None or not isinstance(entity_id, str):
        return False
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return False
    if entry.platform in entry_platforms():
        return owned_entries is not None and entry.config_entry_id in owned_entries
    if entry.platform == 'homeassistant' and entry.domain == 'scene':
        return str(entry.unique_id).startswith('hauflo_')
    return entry.platform in OWN_PLATFORMS

def protected(hass: HomeAssistant | None, entity_id: Any, depth: int=0, links: list[tuple[set[str], set[str]]] | None=None) -> bool:
    """What opens the house or stops its protection: a lock, the alarm, a valve, a garage door / gate / door cover — never in a voice assistant, a scene or a bridge, whatever path asks (security re-review round 5); nor a group with one of them inside (panel round 6)."""
    if not isinstance(entity_id, str):
        return True
    if entity_id.startswith(NEVER_EXPOSE):
        return True
    st = hass.states.get(entity_id) if hass is not None and hasattr(hass, 'states') else None
    members = st.attributes.get('entity_id') if st is not None else None
    if isinstance(members, list) and members and (depth < 3) and any((protected(hass, m, depth + 1, links) for m in members)):
        return True
    if hass is None:
        return False
    src = wrapped_source(hass, entity_id)
    if src is not None and (src == '?' or depth >= 3 or src == entity_id or protected(hass, src, depth + 1, links)):
        return True
    links = _helper_links(hass) if links is None else links
    for wrappers, sources in links:
        if entity_id in wrappers and depth < 3 and any((protected(hass, x, depth + 1, links) for x in sources - {entity_id})):
            return True
        if entity_id in sources and any((_self_protected(hass, w) for w in wrappers - {entity_id})):
            return True
    return _self_protected(hass, entity_id)

def _self_protected(hass: HomeAssistant, entity_id: str) -> bool:
    return entity_id.startswith(NEVER_EXPOSE) or sticky(hass, entity_id) or class_protected(hass, entity_id) or unknown_source(hass, entity_id)

def recent_event(state: str, within: float) -> str | None:
    """An event entity's state is the ISO time it last fired: that time when it is within `within` seconds of now (a clock a little ahead counts), else None (unknown / unavailable / old)."""
    at = dt_util.parse_datetime(state) if isinstance(state, str) else None
    if at is None or at.tzinfo is None:
        return None
    age = (dt_util.utcnow() - at).total_seconds()
    return dt_util.as_utc(at).isoformat() if -within <= age <= within else None

def unknown_source(hass: HomeAssistant | None, entity_id: str) -> bool:
    """N6: a switch / cover / button / script whose actions nobody can read — made in YAML (a template, a command line, a REST one: no config entry) or a UI helper whose options name no entity (empty actions, a service call only) — and whose class or name says a gate / garage door / door / lock: counted as one, the source unknown (fail closed)."""
    if hass is None or not isinstance(entity_id, str) or (not entity_id.startswith(UNKNOWN_DOMAINS)):
        return False
    reg = None
    with contained(_LOGGER, 'Hauflo: registry entry of %s not readable — the state alone', entity_id, expect=KeyError):
        reg = er.async_get(hass).async_get(entity_id)
    if reg is not None and getattr(reg, 'config_entry_id', None) and (helper_operates_known(hass, reg) is not False):
        return False
    st = hass.states.get(entity_id) if hasattr(hass, 'states') else None
    attrs = st.attributes if st is not None else {}
    classes = {attrs.get('device_class'), getattr(reg, 'device_class', None), getattr(reg, 'original_device_class', None)}
    names = (attrs.get('friendly_name'), getattr(reg, 'name', None), getattr(reg, 'original_name', None), entity_id.split('.', 1)[1].replace('_', ' '))
    return bool(classes & PROTECTED_COVERS) or any((isinstance(n, str) and gate_name().search(n) for n in names))

def _option_refs(value: Any, nested: bool=False) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_option_refs(v, True) for k, v in value.items() if k not in SERVICE_KEYS)) if value else set()
    if isinstance(value, list):
        return set().union(*(_option_refs(v, True) for v in value)) if value else set()
    return {value} if nested and isinstance(value, str) and ENTITY_REF.match(value) else set()

def _helper_links(hass: HomeAssistant) -> list[tuple[set[str], set[str]]]:
    out: list[tuple[set[str], set[str]]] = []
    try:
        reg = er.async_get(hass)
        entries = [(d, e) for d in HELPER_DOMAINS for e in hass.config_entries.async_entries(d)]
    except (KeyError, AttributeError):
        _LOGGER.debug('Hauflo: no entity registry / config entries yet: no helper links')
        return out
    for domain, entry in entries:
        with contained(_LOGGER, 'Hauflo: helper %s: wrapped entities / sources not read', domain):
            wrappers = {e.entity_id for e in er.async_entries_for_config_entry(reg, entry.entry_id)}
            sources = _entry_sources(reg, domain, entry)
            if wrappers and sources:
                out.append((wrappers, sources))
    return out

def _entry_sources(reg: er.EntityRegistry, domain: str, entry: Any) -> set[str]:
    opts = dict(entry.options or {})
    if domain == 'switch_as_x':
        src = opts.get('entity_id')
        sources = {getattr(reg.async_get(src), 'entity_id', src)} if isinstance(src, str) and src else set()
    else:
        sources = _option_refs(opts)
    return {x for x in sources if isinstance(x, str) and '.' in x}

def helper_operates_known(hass: HomeAssistant, reg_entry: Any) -> bool | None:
    """For an entity of a config entry: True when it is a UI helper (template / Switch as X) whose options name at least one entity it operates (then _helper_links judges it), False when it is such a helper naming none (empty actions, a service like rest_command / shell_command / mqtt.publish — what it does can't be read), None when the entry is an integration's own (judged by its class, its mark, what it holds — not by its name)."""
    try:
        entry = hass.config_entries.async_get_entry(reg_entry.config_entry_id)
    except Exception:
        _LOGGER.warning('Hauflo: config entry of %s not readable: as a helper naming nothing', reg_entry.entity_id, exc_info=True)
        return False
    if entry is None or entry.domain not in HELPER_DOMAINS:
        return None
    with contained(_LOGGER, 'Hauflo: sources of helper %s not read', reg_entry.entity_id):
        return bool(_entry_sources(er.async_get(hass), entry.domain, entry))
    return False

def protected_ids(hass: HomeAssistant) -> list[str]:
    """Every entity of the house that is protected now — the card's one rule with the agent's (red team L3)."""
    links = _helper_links(hass)
    return sorted((eid for eid in hass.states.async_entity_ids() if protected(hass, eid, links=links)))

def wrapped_source(hass: HomeAssistant, entity_id: str) -> str | None:
    """The entity a «Switch as X» helper operates; «?» when it can't be told (then it counts as protected — fail closed); None for anything that wraps nothing."""
    reg = er.async_get(hass)
    entry = reg.async_get(entity_id)
    if entry is None or entry.platform not in WRAPPER_PLATFORMS:
        return None
    try:
        cfg = hass.config_entries.async_get_entry(entry.config_entry_id) if entry.config_entry_id else None
        src = cfg.options.get('entity_id') if cfg is not None else None
        if isinstance(src, str) and '.' not in src:
            src = getattr(reg.async_get(src), 'entity_id', None)
    except Exception:
        _LOGGER.warning('Hauflo: not knowable: protected', exc_info=True)
        return '?'
    return src if isinstance(src, str) and src else '?'

def class_protected(hass: HomeAssistant, entity_id: str) -> bool:
    """A cover that is a garage door / gate / door by its state, its registry class or its integration's."""
    if not entity_id.startswith('cover.'):
        return False
    st = hass.states.get(entity_id) if hasattr(hass, 'states') else None
    reg = er.async_get(hass).async_get(entity_id)
    classes = {st.attributes.get('device_class') if st is not None else None, getattr(reg, 'device_class', None), getattr(reg, 'original_device_class', None)}
    return bool(classes & PROTECTED_COVERS)

def sticky(hass: HomeAssistant | None, entity_id: str) -> bool:
    """A KNX entity once seen as a gate / garage door / door, or one sending to the addresses that open it (protect.py: deleted and remade, under another class or platform, it stays protected until HA's admin clears it)."""
    marks = getattr(hass, 'data', {}).get(PROTECT_DATA)
    if marks is None or not (marks.uids or marks.gas):
        return False
    reg = er.async_get(hass).async_get(entity_id)
    if reg is None or reg.platform != 'knx':
        return False
    if reg.unique_id in marks.uids:
        return True
    config = ((knx_store(hass) or {}).get(reg.domain) or {}).get(reg.unique_id)
    return bool(knx_writes(config.get('knx') if isinstance(config, dict) else None) & marks.gas)

def hauflo_entity(hass: HomeAssistant | None, eid: Any, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    """Hauflo's own and safe to hand on (a bridge, a voice assistant): a KNX one only if Hauflo created it, never a lock / the alarm / a valve / a garage door."""
    return not protected(hass, eid) and own_entity(hass, eid, owned_entries) and (platform(hass, eid) != 'knx' or owned_knx(hass, eid, owned))

def platform(hass: HomeAssistant | None, entity_id: Any) -> str | None:
    entry = er.async_get(hass).async_get(entity_id) if hass is not None and isinstance(entity_id, str) else None
    return entry.platform if entry else None
