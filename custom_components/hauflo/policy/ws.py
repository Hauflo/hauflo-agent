"""Which websocket commands the cloud may send, and which of them change the house."""
from __future__ import annotations
import json
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from ..foreign import plans as foreign_plans, wizard as foreign_wizard
from .areas import area_floor_empty, device_entities_owned, empty_area, forbidden, foreign_device_placeable, foreign_entity_shown, owns, stock_area, unused_label
from .const import BACKUP_FIRST, COPY_RE, NEVER_EXPOSE, PROTECTED_COVERS, entry_domains, locked_http_ok, locked_never_keys, locked_read_ops, manifests, read_services, services, ws_exact, ws_reads
from .dashboards import alice_labels, lovelace_ok, own_dashboard, theme_ok
from .knx import owned_knx
from .knx_config import knx_config_ok
from .marks import own_device
from .own import class_protected, hauflo_entity, own_entity, platform, protected

def ws_changes(msg: dict[str, Any]) -> bool:
    t = msg.get('type')
    if t == 'call_service':
        return (str(msg.get('domain')), str(msg.get('service'))) not in read_services()
    return t not in ws_reads() and t != 'auth'

def http_changes(method: str, path: str) -> bool:
    bare = path.split('?')[0]
    return method in ('POST', 'DELETE') and (bare.startswith('/api/config/') or bare in BACKUP_FIRST)

def ws_locked(msg: dict[str, Any]) -> bool:
    return ws_changes(msg)

def http_locked(method: str, path: str, body: Any=None) -> bool:
    bare = path.split('?')[0]
    if (keys := locked_never_keys().get((bare, method))) is not None:
        try:
            data = json.loads(body) if isinstance(body, (str, bytes)) and body else body
        except ValueError:
            return True
        if not isinstance(data, dict) or any((data.get(k) for k in keys)):
            return True
    if method == 'GET' or (bare, method) in locked_http_ok():
        return False
    if (ops := locked_read_ops().get((bare, method))) is not None:
        try:
            data = json.loads(body) if isinstance(body, (str, bytes)) and body else body
        except ValueError:
            return True
        return not (isinstance(data, dict) and data.get('op') in ops)
    return True

def ws_allowed(msg: dict[str, Any], hass: HomeAssistant | None=None, owned: set[str] | None=None, owned_entries: set[str] | None=None) -> bool:
    t = str(msg.get('type', ''))
    if t == 'call_service':
        if (msg.get('domain'), msg.get('service')) == ('frontend', 'set_theme'):
            return theme_ok(msg)
        return (str(msg.get('domain')), str(msg.get('service'))) in services()
    if t not in ws_exact():
        return False
    if t == 'manifest/get':
        return isinstance(msg.get('integration'), str) and msg['integration'] in manifests()
    if t == 'config_entries/get':
        if 'domain' not in msg:
            tf = msg.get('type_filter')
            return set(msg) <= {'id', 'type', 'type_filter'} and (tf is None or (isinstance(tf, list) and len(tf) <= 5 and all((isinstance(x, str) for x in tf))))
        return isinstance(msg.get('domain'), str) and msg['domain'] in entry_domains()
    if t == 'config/entity_registry/update':
        eid = msg.get('entity_id')
        if not isinstance(eid, str) or eid.startswith(NEVER_EXPOSE):
            return False
        if not own_entity(hass, eid, owned_entries):
            return foreign_wizard.wizard_refusal(hass, msg) is None if foreign_plans.planned(hass, msg) else foreign_entity_shown(hass, eid, msg)
        if protected(hass, eid) and set(msg.get('labels') or []) & alice_labels(hass):
            return False
        if 'device_class' in msg and protected(hass, eid) and (class_protected(hass, eid) or msg['device_class'] not in PROTECTED_COVERS):
            return False
        if forbidden(msg.get('new_entity_id')):
            return False
        reg_entry = er.async_get(hass).async_get(eid) if hass is not None else None
        if reg_entry is not None and reg_entry.platform == 'knx' and (not owned_knx(hass, eid, owned)):
            return False
        return 'options_domain' not in msg
    if t in ('config/device_registry/remove', 'config/device_registry/remove_config_entry'):
        return own_device(hass, msg.get('device_id')) and device_entities_owned(hass, msg.get('device_id'), owned)
    if t == 'config/device_registry/update':
        placing = set(msg) <= {'id', 'type', 'device_id', 'area_id'}
        if own_device(hass, msg.get('device_id')):
            return placing and device_entities_owned(hass, msg.get('device_id'), owned)
        if foreign_plans.planned(hass, msg):
            return foreign_wizard.wizard_refusal(hass, msg) is None
        return placing and foreign_device_placeable(hass, msg.get('device_id'), msg.get('area_id'), owned_entries)
    if t in ('config/area_registry/update', 'config/floor_registry/update'):
        kind = 'area' if t.startswith('config/area') else 'floor'
        key = f'{kind}_id'
        if owns(owned_entries, kind, msg.get(key)):
            return set(msg) <= {'id', 'type', key, 'name', 'aliases', 'icon', 'floor_id', 'level'}
        if kind == 'area' and set(msg) <= {'id', 'type', 'area_id', 'floor_id'} and msg.get('floor_id'):
            return area_floor_empty(hass, msg.get('area_id'))
        return False
    if t in ('knx/delete_entity', 'knx/update_entity'):
        if t == 'knx/update_entity' and protected(hass, msg.get('entity_id')):
            data = msg.get('data')
            ent = data.get('entity') if isinstance(data, dict) else None
            if isinstance(ent, dict) and 'device_class' in ent and (class_protected(hass, str(msg.get('entity_id'))) or ent['device_class'] not in PROTECTED_COVERS):
                return False
        return owned_knx(hass, msg.get('entity_id'), owned) and (t == 'knx/delete_entity' or knx_config_ok(hass, msg, owned))
    if t == 'knx/create_entity':
        return knx_config_ok(hass, msg, owned)
    if t == 'config/area_registry/delete':
        aid = msg.get('area_id')
        return set(msg) <= {'id', 'type', 'area_id'} and (owns(owned_entries, 'area', aid) and empty_area(hass, aid) or stock_area(hass, aid))
    if t == 'config/label_registry/delete':
        return owns(owned_entries, 'label', msg.get('label_id')) and unused_label(hass, msg.get('label_id'))
    if t.startswith('lovelace/') and COPY_RE.search(str(msg.get('url_path') or '')):
        return False
    if t == 'lovelace/dashboards/create':
        return str(msg.get('url_path') or '').startswith('hauflo-')
    if t == 'lovelace/config':
        return str(msg.get('url_path') or '').startswith('hauflo-')
    if t == 'lovelace/config/save':
        return str(msg.get('url_path') or '').startswith('hauflo-') and lovelace_ok(hass, msg.get('config'), owned, owned_entries)
    if t == 'lovelace/dashboards/update':
        return own_dashboard(hass, msg.get('dashboard_id'))
    if t == 'lovelace/dashboards/delete':
        return set(msg) <= {'id', 'type', 'dashboard_id'} and own_dashboard(hass, msg.get('dashboard_id'))
    if t == 'homeassistant/expose_entity':
        ids = msg.get('entity_ids')
        if not isinstance(ids, list):
            return False
        if msg.get('should_expose') is not False and any((protected(hass, i) for i in ids)):
            return False
        if msg.get('should_expose') is False:
            return all((own_entity(hass, i, owned_entries) and (platform(hass, i) != 'knx' or owned_knx(hass, i, owned)) for i in ids))
        return all((hauflo_entity(hass, i, owned, owned_entries) for i in ids))
    return True
