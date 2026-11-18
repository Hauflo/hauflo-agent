"""Config flow steps the cloud may answer."""
from __future__ import annotations
import ipaddress
import logging
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import UnknownFlow
from ..foreign import common as foreign_common, refusals as foreign_refusals
from ..guard import contained
from .areas import forbidden, label_ok, lan_host
from .const import HK_FILTER_KEYS, KNX_SECURE_KEYS, PRIVATE_STATE, sax, sax_keys, sax_option_keys, tpl, tpl_classes, tpl_keys, tpl_menu, tpl_option_keys, tpl_tokens
from .knx import owned_knx
from .own import hauflo_entity, own_entity, platform
_LOGGER = logging.getLogger(__name__)

def _homekit_step_ok(hass: HomeAssistant | None, data: dict[str, Any], memo: dict[str, list[str]] | None, flow_id: str, step: str | None, owned: set[str] | None=None, owned_entries: set[str] | None=None) -> bool:
    if step == 'user':
        return data.get('include_domains') == ['person'] and set(data) <= {'include_domains'}
    if step == 'pairing':
        return not data
    if step == 'init':
        domains = data.get('domains')
        if data.get('mode') != 'bridge' or data.get('include_exclude_mode') != 'include' or (not isinstance(domains, list)) or (not domains):
            return False
        if not all((isinstance(d, str) for d in domains)) or memo is None:
            return False
        memo[flow_id] = list(domains)
        return True
    if step == 'include':
        entities = data.get('entities')
        if memo is None or flow_id not in memo or (not isinstance(entities, list)) or (not all((isinstance(e, str) for e in entities))):
            return False
        picked = {e.split('.')[0] for e in entities}
        if any((d not in picked for d in memo.pop(flow_id))):
            return False
        if not all((hauflo_entity(hass, e, owned, owned_entries) for e in entities)):
            return False
        return set(data) <= {'entities'}
    if step in ('cameras', 'climate', 'bridged_device_triggers'):
        return not set(data) & HK_FILTER_KEYS
    return False

def flow_step(manager: Any, flow_id: str) -> str | None:
    with contained(_LOGGER, 'Hauflo: flow %s unknown — no step', flow_id, expect=UnknownFlow):
        return str(manager.async_get(flow_id).get('step_id'))
    return None

def _template_entities(text: Any) -> list[str] | None:
    if not isinstance(text, str) or not text.strip() or len(text) > tpl()['max_state']:
        return None
    pos, ents = (0, [])
    while pos < len(text):
        for rx, group in tpl_tokens():
            m = rx.match(text, pos)
            if m is not None and m.end() > pos:
                if group:
                    ents.append(m.group(group))
                pos = m.end()
                break
        else:
            return None
    return ents

def _rule_reads_ok(hass: HomeAssistant | None, eid: str, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    return eid.split('.')[0] not in PRIVATE_STATE and own_entity(hass, eid, owned_entries) and (platform(hass, eid) != 'knx' or owned_knx(hass, eid, owned))

def _template_step_ok(hass: HomeAssistant | None, data: dict[str, Any], step: str | None, owned: set[str] | None, owned_entries: set[str] | None, options: bool) -> bool:
    if step == 'user' and (not options):
        return set(data) == {'next_step_id'} and str(data.get('next_step_id')) in tpl_menu()
    keys = (tpl_option_keys() if options else tpl_keys()).get(str(step))
    if keys is None or not set(data) <= keys or any((k not in data for k in tpl()['required'])):
        return False
    if 'device_class' in data and str(data['device_class']) not in tpl_classes():
        return False
    if 'name' in data and (not (isinstance(data['name'], str) and 0 < len(data['name']) <= tpl()['max_name'])):
        return False
    ents = _template_entities(data.get('state'))
    return ents is not None and all((_rule_reads_ok(hass, e, owned, owned_entries) for e in ents))

def _switch_as_x_step_ok(hass: HomeAssistant | None, data: dict[str, Any], step: str | None, owned: set[str] | None, owned_entries: set[str] | None, options: bool) -> bool:
    keys = (sax_option_keys() if options else sax_keys()).get(str(step))
    if keys is None or not set(data) <= keys:
        return False
    if 'invert' in data and (not isinstance(data['invert'], bool)):
        return False
    if options:
        return True
    if any((k not in data for k in sax()['required'])) or str(data.get('target_domain')) not in sax()['target_domains']:
        return False
    eid = data.get('entity_id')
    return isinstance(eid, str) and eid.split('.', 1)[0] in sax()['source_domains'] and hauflo_entity(hass, eid, owned, owned_entries)

def flow_input_ok(hass: HomeAssistant, domain: str | None, data: dict[str, Any], memo: dict[str, list[str]] | None=None, flow_id: str='', step: str | None=None, owned: set[str] | None=None, owned_entries: set[str] | None=None, options: bool=False) -> bool:
    """What a flow step may set (security re-review round 3: only the handler was checked, not the input): never
    a lock / alarm / valve for a voice assistant or a group, HomeKit as a bridge of included entities, Alice by the
    label, KNX to an interface in the house and its telegram history kept in HA."""
    if forbidden(data):
        return False
    if domain == 'homekit':
        return _homekit_step_ok(hass, data, memo, flow_id, step, owned, owned_entries)
    if domain == 'template':
        return _template_step_ok(hass, data, step, owned, owned_entries, options)
    if domain == 'switch_as_x':
        return _switch_as_x_step_ok(hass, data, step, owned, owned_entries, options)
    if domain == 'group' and ('entities' in data or data.get('hide_members')):
        ents = data.get('entities')
        if not isinstance(ents, list):
            return False
        rest = [e for e in ents if not hauflo_entity(hass, e, owned, owned_entries)]
        if not rest:
            return True
        return step in foreign_common.MEMBER_DOMAINS and (not data.get('hide_members')) and all((isinstance(e, str) and e.split('.', 1)[0] == step and foreign_refusals.member_ok(hass, e) for e in rest))
    if domain == 'yandex_smart_home':
        if any((str(k).startswith(('skill', 'cloud', 'instance', 'user_id', 'token')) for k in data)):
            return False
        if not (data.get('filter_source', 'label') == 'label' and data.get('connection_type', 'cloud') == 'cloud'):
            return False
        return 'label' not in data or label_ok(hass, data.get('label'), owned, owned_entries)
    if domain == 'knx':
        if step is not None and (step.startswith(('secure', 'knxkeys')) or 'postgres' in step):
            return False
        if any((str(k).startswith(KNX_SECURE_KEYS) for k in data)):
            return False
        for k, v in data.items():
            if str(k) == 'telegram_store_section':
                if not isinstance(v, dict) or v.get('telegram_db_backend', 'sqlite') != 'sqlite' or any(('postgres' in str(x) for x in v)):
                    return False
            elif str(k).startswith('telegram_'):
                return False
        if 'rate_limit' in data and (not (isinstance(data['rate_limit'], int) and (not isinstance(data['rate_limit'], bool)) and (1 <= data['rate_limit'] <= 60))):
            return False
        if 'state_updater' in data and (not isinstance(data['state_updater'], bool)):
            return False
        if not all((lan_host(data.get(k)) for k in ('host', 'local_ip', 'gateway'))):
            return False
        mcast = data.get('multicast_group')
        if mcast not in (None, ''):
            try:
                return ipaddress.ip_address(str(mcast)).is_multicast
            except ValueError:
                return False
    return True
