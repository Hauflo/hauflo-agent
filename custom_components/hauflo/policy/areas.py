"""Rooms, devices and labels the cloud may change, and the house's own address."""
from __future__ import annotations
import ipaddress
import logging
from typing import Any
from urllib.parse import urlparse
from homeassistant.components.onboarding.const import DEFAULT_AREAS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from ..const import DOMAIN
from ..foreign import common as foreign_common
from ..guard import contained
from .const import BENCH_NET, NEVER_DOMAINS, NEVER_EXPOSE, STOCK_AREAS, foreign_name_max
from .own import hauflo_entity
_LOGGER = logging.getLogger(__name__)

def device_entities_owned(hass: HomeAssistant | None, device_id: Any, owned: set[str] | None) -> bool:
    if hass is None or owned is None or (not isinstance(device_id, str)):
        return False
    ents = er.async_entries_for_device(er.async_get(hass), device_id, include_disabled_entities=True)
    return all((e.platform != 'knx' or e.unique_id in owned for e in ents))

def foreign_device_placeable(hass: HomeAssistant | None, device_id: Any, area_id: Any, owned_entries: set[str] | None) -> bool:
    """Another integration's device (a leak sensor, a camera…) put in a room on the integrator's click («Изменения в
    HA»,
    or into a room Hauflo made. Never taken out of the family's own room into theirs, never a KNX one, never cleared."""
    if hass is None or not isinstance(device_id, str) or (not isinstance(area_id, str)) or (not area_id):
        return False
    device = dr.async_get(hass).async_get(device_id)
    if device is None or getattr(device, 'entry_type', None) is not None:
        return False
    ents = er.async_entries_for_device(er.async_get(hass), device_id, include_disabled_entities=True)
    if not ents or any((getattr(e, 'platform', None) in ('knx', DOMAIN) for e in ents)):
        return False
    if ar.async_get(hass).async_get_area(area_id) is None:
        return False
    return getattr(device, 'area_id', None) is None or owns(owned_entries, 'area', area_id)

def foreign_entity_shown(hass: HomeAssistant | None, eid: str, msg: dict[str, Any]) -> bool:
    """Another integration's entity on a device in the house (a thermostat's, a leak kit's): hidden from / shown on the
    voice, id), never Hauflo's or KNX's, never a phone's, never one without a device (a helper, a template)."""
    if hass is None or not set(msg) <= {'id', 'type', 'entity_id', 'hidden_by', 'name'} or (not set(msg) & {'hidden_by', 'name'}):
        return False
    if 'hidden_by' in msg and msg['hidden_by'] not in ('user', None):
        return False
    if 'name' in msg and msg['name'] is not None and (not (isinstance(msg['name'], str) and msg['name'].strip() and (len(msg['name']) <= foreign_name_max()) and (not foreign_common.UNSAFE_NAME.search(msg['name'])))):
        return False
    entry = er.async_get(hass).async_get(eid)
    if entry is None or not getattr(entry, 'device_id', None) or entry.platform in ('knx', DOMAIN, 'mobile_app'):
        return False
    device = dr.async_get(hass).async_get(entry.device_id)
    if device is None or getattr(device, 'entry_type', None) is not None:
        return False
    ents = er.async_entries_for_device(er.async_get(hass), entry.device_id, include_disabled_entities=True)
    return not any((getattr(e, 'platform', None) in ('knx', DOMAIN) for e in ents))

def owns(owned_entries: set[str] | None, kind: str, item_id: Any) -> bool:
    """A room / floor / label Hauflo created through the tunnel (kept with its config entries as «kind:id»)."""
    return owned_entries is not None and isinstance(item_id, str) and (f'{kind}:{item_id}' in owned_entries)

def area_floor_empty(hass: HomeAssistant | None, area_id: Any) -> bool:
    if hass is None or not isinstance(area_id, str):
        return False
    area = ar.async_get(hass).async_get_area(area_id)
    return area is not None and (not area.floor_id)

def empty_area(hass: HomeAssistant | None, area_id: Any) -> bool:
    """No entity and no device in it — a room deleted there takes nothing with it."""
    if hass is None or not isinstance(area_id, str):
        return False
    if ar.async_get(hass).async_get_area(area_id) is None:
        return False
    return not er.async_entries_for_area(er.async_get(hass), area_id) and (not dr.async_entries_for_area(dr.async_get(hass), area_id))

async def load_stock_area_names(hass: HomeAssistant) -> dict[str, str]:
    """The rooms HA's onboarding makes («Гостиная», «Кухня», «Спальня»): name in the house's language and English →
    icon."""
    names: dict[str, str] = {}
    for lang in {hass.config.language or 'en', 'en'}:
        tr = await async_get_translations(hass, lang, 'area', {'onboarding'})
        for area in DEFAULT_AREAS:
            if (name := tr.get(f'component.onboarding.area.{area.key}')):
                names[name] = area.icon
    hass.data[STOCK_AREAS] = names
    return names

def stock_area(hass: HomeAssistant | None, area_id: Any) -> bool:
    """    its stock name and icon, no floor, alias, picture, label or sensor, nothing in it."""
    names = hass.data.get(STOCK_AREAS) if hass is not None else None
    if not names or not isinstance(area_id, str):
        return False
    area = ar.async_get(hass).async_get_area(area_id)
    if area is None or area.name not in names or names[area.name] != area.icon:
        return False
    if area.floor_id or area.aliases or area.picture or area.labels:
        return False
    if getattr(area, 'temperature_entity_id', None) or getattr(area, 'humidity_entity_id', None):
        return False
    return empty_area(hass, area_id)

def unused_label(hass: HomeAssistant | None, label_id: Any) -> bool:
    if hass is None or not isinstance(label_id, str):
        return False
    if er.async_entries_for_label(er.async_get(hass), label_id):
        return False
    if dr.async_entries_for_label(dr.async_get(hass), label_id):
        return False
    return not ar.async_entries_for_label(ar.async_get(hass), label_id)

def forbidden(value: Any) -> bool:
    """A lock, the alarm or a valve anywhere in a flow's input — as an entity or a whole domain."""
    if isinstance(value, str):
        return value in NEVER_DOMAINS or value.startswith(NEVER_EXPOSE)
    if isinstance(value, dict):
        return any((forbidden(k) or forbidden(v) for k, v in value.items()))
    if isinstance(value, list):
        return any((forbidden(v) for v in value))
    return False

def ha_url(raw: Any) -> str | None:
    """An URL without its login, query or fragment («https://user:pw@x/a?b» → «https://x/a»)."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        u = urlparse(raw)
        port = f':{u.port}' if u.port else ''
    except ValueError:
        return None
    if u.scheme not in ('http', 'https') or not u.hostname:
        return None
    return f"{u.scheme}://{u.hostname}{port}{u.path.rstrip('/')}"[:200]

def lan_host(value: Any) -> bool:
    """KNX talks to an interface in the house: a private / link-local address (or a gateway choice with one)."""
    if not isinstance(value, str) or not value:
        return value in (None, '')
    host = value.rsplit('@', 1)[1].strip() if '@' in value else value
    host = host.rsplit(':', 1)[0] if host.count(':') == 1 else host
    if '.' not in host and ':' not in host:
        return host in ('manual', 'automatic', 'auto')
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version != 4 or ip.is_loopback or ip.is_unspecified or ip.is_reserved or ip.is_multicast or (ip in BENCH_NET):
        return False
    return ip.is_private or ip.is_link_local

def label_ok(hass: HomeAssistant | None, label_id: Any, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    if hass is None or not isinstance(label_id, str) or (not label_id):
        return False
    with contained(_LOGGER, 'Hauflo: label %s not checkable — not allowed', label_id):
        if dr.async_entries_for_label(dr.async_get(hass), label_id):
            return False
        return all((hauflo_entity(hass, e.entity_id, owned, owned_entries) for e in er.async_entries_for_label(er.async_get(hass), label_id)))
    return False
