"""Which REST calls the cloud may make."""
from __future__ import annotations
import json
import logging
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import UnknownFlow
from ..foreign import refusals as foreign_refusals
from ..guard import contained
from .const import SCENE_DOMAINS, delete_domains, diag_domains, flow_domains, options_domains, owned_only, routes
from .flows import flow_input_ok, flow_step
from .own import hauflo_entity
_LOGGER = logging.getLogger(__name__)

def entry_domain(hass: HomeAssistant, entry_id: str) -> str | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    return entry.domain if entry else None

def _flow_handler(manager: Any, flow_id: str) -> str | None:
    with contained(_LOGGER, 'Hauflo: flow %s unknown — not allowed', flow_id, expect=UnknownFlow):
        return str(manager.async_get(flow_id).get('handler'))
    return None

def _route(method: str, bare: str) -> str | None:
    segs = bare.split('/')
    for rid, pattern, methods in routes():
        if method in methods and len(pattern) == len(segs) and all((p == s or (p[:1] == '{' and p[-1:] == '}' and bool(s)) for p, s in zip(pattern, segs, strict=False))):
            return rid
    return None

def _owned_entry(owned_entries: set[str] | None, entry_id: Any) -> bool:
    return owned_entries is not None and isinstance(entry_id, str) and (entry_id in owned_entries)

def http_allowed(hass: HomeAssistant, method: str, path: str, body: Any, owned_entries: set[str] | None=None, memo: dict[str, list[str]] | None=None, owned: set[str] | None=None) -> bool:
    if '..' in path or '%' in path or '\\' in path:
        return False
    bare = path.split('?')[0]
    parts = bare.strip('/').split('/')
    try:
        data = json.loads(body) if isinstance(body, str) and body else body if isinstance(body, dict) else {}
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    route = _route(method, bare)
    if route in ('info', 'hauflo', 'flow_handlers'):
        return True
    if route == 'homekit_qr':
        return _owned_entry(owned_entries, path.split('?', 1)[1].split('-', 1)[0] if '?' in path else '')
    if route == 'scene':
        if not parts[4].startswith('hauflo_'):
            return False
        if parts[4].startswith('hauflo_fam_'):
            return False
        ents = data.get('entities', {}) if method == 'POST' else {}
        return isinstance(ents, dict) and all((isinstance(k, str) and k.split('.')[0] in SCENE_DOMAINS and (hauflo_entity(hass, k, owned, owned_entries) or foreign_refusals.member_ok(hass, k)) for k in ents))
    if route == 'flow':
        handler = str(data.get('handler'))
        if handler == 'yandex_smart_home' and hass.config_entries.async_entries(handler):
            return False
        if data.get('entry_id') is not None:
            return handler == 'knx' and _owned_entry(owned_entries, str(data.get('entry_id')))
        if handler == 'knx' and hass.config_entries.async_entries('knx'):
            return False
        return handler in flow_domains()
    if route == 'flow_step':
        domain = _flow_handler(hass.config_entries.flow, parts[4])
        step = flow_step(hass.config_entries.flow, parts[4])
        return domain in flow_domains() and (method != 'POST' or flow_input_ok(hass, domain, data, memo, parts[4], step, owned, owned_entries))
    if route == 'options':
        handler = str(data.get('handler'))
        domain = entry_domain(hass, handler)
        if domain in owned_only() and (not _owned_entry(owned_entries, handler)):
            return False
        return domain in options_domains()
    if route == 'options_step':
        handler = _flow_handler(hass.config_entries.options, parts[5])
        domain = entry_domain(hass, handler) if handler is not None else None
        if domain in owned_only() and (not _owned_entry(owned_entries, handler)):
            return False
        step = flow_step(hass.config_entries.options, parts[5])
        return domain in options_domains() and (method != 'POST' or flow_input_ok(hass, domain, data, memo, parts[5], step, owned, owned_entries, options=True))
    if route == 'entry_delete':
        return entry_domain(hass, parts[4]) in delete_domains() and _owned_entry(owned_entries, parts[4])
    if route == 'diagnostics':
        domain = entry_domain(hass, parts[3])
        return domain in diag_domains() and (domain not in owned_only() or _owned_entry(owned_entries, parts[3]))
    return False
