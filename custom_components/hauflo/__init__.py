from __future__ import annotations
import asyncio
from collections.abc import Callable, Mapping
import contextlib
import copy
import ipaddress
import json
import logging
import math
import random
import re
import time
from urllib.parse import urlparse
from typing import Any
import aiohttp
from homeassistant.auth.const import GROUP_ID_ADMIN
from homeassistant.components import persistent_notification
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, __version__ as HA_VERSION
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, label_registry as lr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.event import async_call_later, async_track_point_in_utc_time, async_track_state_change_event
from homeassistant.util import dt as dt_util
from . import intercom as intercom_mod
from . import locks as locks_mod
from .intercom import setup as intercom_setup
from . import alarms_now, analysis, automations, backup, combos, dashmerge, daylight, exposure, foreign, guest, house, independent, insight, install, issues, keep, noeffect, notify, ownership, presence, protect, quiet, ranges, retired, rules, scenes, season, self_update, servers, service, sidebar, signals, stock, theme, timers, titles, voice, watchdog
from . import derived as dv
from . import order as block_order
from . import room_pictures
from . import habits as habits_mod
from . import history as history_mod
from .issues import DEGRADED, degrade
from .storage import HaufloStore
from .const import ASSET_TAG, DATA_CARDS_LOCAL, CONF_REFRESH_TOKEN_ID, CONF_SECRET, CONF_SERVER, CONF_USER_ID, DATA_NOTIFY_VIEWS, DATA_UNLINKED, DOMAIN, MONITOR_LABEL, MONITOR_LABEL_EN, MONITOR_LABELS, OWNED_STORE, STATUS_EVERY, VERSION, CONF_RANGES, CONF_ALLOW_CHANGES, LOCKED_CODE, CONF_INTERCOM, site_ru
from .tuning import TUNING
from .tunnel import TUNNEL
_LOGGER = logging.getLogger(__name__)
KNX_ROUND_GAP = TUNING['link']['knx_round_gap_s']
KNX_ROUND_MIN_READS = TUNING['link']['knx_round_min_reads']
EVENT_RECENT = TUNING['link']['event_recent_s']

async def _local_token(hass: HomeAssistant, entry: ConfigEntry):
    user = None
    if (uid := entry.data.get(CONF_USER_ID)):
        user = await hass.auth.async_get_user(uid)
    if user is None:
        user = await hass.auth.async_create_system_user('Hauflo agent', group_ids=[GROUP_ID_ADMIN])
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_USER_ID: user.id, CONF_REFRESH_TOKEN_ID: None})
    refresh = None
    if (rid := entry.data.get(CONF_REFRESH_TOKEN_ID)):
        refresh = hass.auth.async_get_refresh_token(rid)
    if refresh is None:
        refresh = await hass.auth.async_create_refresh_token(user)
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_REFRESH_TOKEN_ID: refresh.id})
    return refresh
_TW = TUNNEL['ws']
_TH = TUNNEL['http']
_TF = TUNNEL['flows']
_WS_EXACT = set(_TW['commands'])
_SERVICES = {(d, s) for d, s in _TW['services']}
_WS_READS = set(_TW['reads'])
_READ_SERVICES = {(d, s) for d, s in _TW['read_services']}
_CREATES = {'config/area_registry/create': 'area', 'config/floor_registry/create': 'floor', 'config/label_registry/create': 'label'}
_BACKUP_FIRST = {'/api/hauflo/sidebar', '/api/hauflo/install', '/api/hauflo/install/restart', '/api/hauflo/self_update'}

def _ws_changes(msg: dict[str, Any]) -> bool:
    t = msg.get('type')
    if t == 'call_service':
        return (str(msg.get('domain')), str(msg.get('service'))) not in _READ_SERVICES
    return t not in _WS_READS and t != 'auth'

def _http_changes(method: str, path: str) -> bool:
    bare = path.split('?')[0]
    return method in ('POST', 'DELETE') and (bare.startswith('/api/config/') or bare in _BACKUP_FIRST)
_LOCKED_HTTP_OK = {(p, m) for p, m in _TH['locked_ok']}

def _ws_locked(msg: dict[str, Any]) -> bool:
    return _ws_changes(msg)
_LOCKED_READ_OPS = {(r['path'], r['method']): set(r['ops']) for r in _TH['locked_read_ops']}
_LOCKED_NEVER_KEYS = {(r['path'], r['method']): set(r['keys']) for r in _TH['locked_never_keys']}

def _http_locked(method: str, path: str, body: Any=None) -> bool:
    bare = path.split('?')[0]
    if (keys := _LOCKED_NEVER_KEYS.get((bare, method))) is not None:
        try:
            data = json.loads(body) if isinstance(body, (str, bytes)) and body else body
        except ValueError:
            return True
        if not isinstance(data, dict) or any((data.get(k) for k in keys)):
            return True
    if method == 'GET' or (bare, method) in _LOCKED_HTTP_OK:
        return False
    if (ops := _LOCKED_READ_OPS.get((bare, method))) is not None:
        try:
            data = json.loads(body) if isinstance(body, (str, bytes)) and body else body
        except ValueError:
            return True
        return not (isinstance(data, dict) and data.get('op') in ops)
    return True
_FLOW_DOMAINS = set(_TF['setup'])
_OPTIONS_DOMAINS = set(_TF['options'])
_OWNED_ONLY = set(_TF['owned_only'])
_DELETE_DOMAINS = set(_TF['delete'])
_TRACKED_DOMAINS = set(_TF['tracked'])
_DIAG_DOMAINS = set(_TF['diagnostics'])
_ENTRY_PLATFORMS = set(_TF['entities_by_entry'])
_ENTRY_DOMAINS = set(_TW['config_entries'])
_ENTRY_OWNED_ONLY = set(_TW['config_entries_owned_only'])
_MANIFESTS = set(_TW['manifests'])
_ROUTES = [(r['id'], r['path'].split('/'), set(r['methods'])) for r in _TH['routes']]
_HAUFLO_VIEWS = {(r['path'], m) for r in _TH['routes'] if r['id'] == 'hauflo' for m in r['methods']}
_MAX_CHANNELS = 8
HA_UP = dt_util.utcnow().isoformat()
FEATURES = [foreign.FEATURE, foreign.PLAN_FEATURE, foreign.WRITE_PLAN_FEATURE, foreign.MEMBERS_FEATURE, foreign.SHOWN_FEATURE, signals.FEATURE, signals.MUTE_FEATURE, signals.SHELVE_FEATURE, combos.FEATURE, insight.FEATURE, insight.OBSERVE_FEATURE, watchdog.FEATURE, install.FEATURE, self_update.FEATURE, intercom_setup.FEATURE, locks_mod.FEATURE, exposure.FEATURE]
_OWN_PLATFORMS = {'knx', 'group', DOMAIN}
_OWN_DEVICE_DOMAINS = {'knx', 'group'}
_NEVER_EXPOSE = ('lock.', 'alarm_control_panel.', 'valve.')
_NEVER_DOMAINS = {'lock', 'alarm_control_panel', 'valve'}
_SCENE_DOMAINS = {'light', 'cover', 'switch', 'climate', 'fan', 'media_player'}

def _own_entity(hass: HomeAssistant | None, entity_id: Any, owned_entries: set[str] | None=None) -> bool:
    if hass is None or not isinstance(entity_id, str):
        return False
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return False
    if entry.platform in _ENTRY_PLATFORMS:
        return owned_entries is not None and entry.config_entry_id in owned_entries
    if entry.platform == 'homeassistant' and entry.domain == 'scene':
        return str(entry.unique_id).startswith('hauflo_')
    return entry.platform in _OWN_PLATFORMS
_PROTECTED_COVERS = {'garage', 'gate', 'door'}

def _protected(hass: HomeAssistant | None, entity_id: Any, depth: int=0, links: list[tuple[set[str], set[str]]] | None=None) -> bool:
    if not isinstance(entity_id, str):
        return True
    if entity_id.startswith(_NEVER_EXPOSE):
        return True
    st = hass.states.get(entity_id) if hass is not None and hasattr(hass, 'states') else None
    members = st.attributes.get('entity_id') if st is not None else None
    if isinstance(members, list) and members and (depth < 3) and any((_protected(hass, m, depth + 1, links) for m in members)):
        return True
    if hass is None:
        return False
    src = _wrapped_source(hass, entity_id)
    if src is not None and (src == '?' or depth >= 3 or src == entity_id or _protected(hass, src, depth + 1, links)):
        return True
    links = _helper_links(hass) if links is None else links
    for wrappers, sources in links:
        if entity_id in wrappers and depth < 3 and any((_protected(hass, x, depth + 1, links) for x in sources - {entity_id})):
            return True
        if entity_id in sources and any((_self_protected(hass, w) for w in wrappers - {entity_id})):
            return True
    return _self_protected(hass, entity_id)

def _self_protected(hass: HomeAssistant, entity_id: str) -> bool:
    return entity_id.startswith(_NEVER_EXPOSE) or _sticky(hass, entity_id) or _class_protected(hass, entity_id) or unknown_source(hass, entity_id)
_GATE_WORDS = TUNING['ownership']['gate_words']
_GATE_NAME = re.compile('|'.join((w.replace('\\p{L}', '[^\\W\\d_]') for w in _GATE_WORDS)), re.IGNORECASE)
_UNKNOWN_DOMAINS = ('switch.', 'cover.', 'button.', 'script.')

def _recent_event(state: str, within: float) -> str | None:
    at = dt_util.parse_datetime(state) if isinstance(state, str) else None
    if at is None or at.tzinfo is None:
        return None
    age = (dt_util.utcnow() - at).total_seconds()
    return dt_util.as_utc(at).isoformat() if -within <= age <= within else None

def unknown_source(hass: HomeAssistant | None, entity_id: str) -> bool:
    if hass is None or not isinstance(entity_id, str) or (not entity_id.startswith(_UNKNOWN_DOMAINS)):
        return False
    try:
        reg = er.async_get(hass).async_get(entity_id)
    except Exception:
        reg = None
    if reg is not None and getattr(reg, 'config_entry_id', None) and (_helper_operates_known(hass, reg) is not False):
        return False
    st = hass.states.get(entity_id) if hasattr(hass, 'states') else None
    attrs = st.attributes if st is not None else {}
    classes = {attrs.get('device_class'), getattr(reg, 'device_class', None), getattr(reg, 'original_device_class', None)}
    names = (attrs.get('friendly_name'), getattr(reg, 'name', None), getattr(reg, 'original_name', None), entity_id.split('.', 1)[1].replace('_', ' '))
    return bool(classes & _PROTECTED_COVERS) or any((isinstance(n, str) and _GATE_NAME.search(n) for n in names))
_WRAPPER_PLATFORMS = {'switch_as_x'}
_HELPER_DOMAINS = ('switch_as_x', 'template')
_ENTITY_REF = re.compile('^[a-z0-9_]+\\.[a-z0-9_]+$')
_SERVICE_KEYS = frozenset({'action', 'service'})

def _option_refs(value: Any, nested: bool=False) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_option_refs(v, True) for k, v in value.items() if k not in _SERVICE_KEYS)) if value else set()
    if isinstance(value, list):
        return set().union(*(_option_refs(v, True) for v in value)) if value else set()
    return {value} if nested and isinstance(value, str) and _ENTITY_REF.match(value) else set()

def _helper_links(hass: HomeAssistant) -> list[tuple[set[str], set[str]]]:
    out: list[tuple[set[str], set[str]]] = []
    try:
        reg = er.async_get(hass)
        entries = [(d, e) for d in _HELPER_DOMAINS for e in hass.config_entries.async_entries(d)]
    except Exception:
        return out
    for domain, entry in entries:
        with contextlib.suppress(Exception):
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

def _helper_operates_known(hass: HomeAssistant, reg_entry: Any) -> bool | None:
    try:
        entry = hass.config_entries.async_get_entry(reg_entry.config_entry_id)
    except Exception:
        return False
    if entry is None or entry.domain not in _HELPER_DOMAINS:
        return None
    try:
        return bool(_entry_sources(er.async_get(hass), entry.domain, entry))
    except Exception:
        return False

def protected_ids(hass: HomeAssistant) -> list[str]:
    links = _helper_links(hass)
    return sorted((eid for eid in hass.states.async_entity_ids() if _protected(hass, eid, links=links)))

def _wrapped_source(hass: HomeAssistant, entity_id: str) -> str | None:
    reg = er.async_get(hass)
    entry = reg.async_get(entity_id)
    if entry is None or entry.platform not in _WRAPPER_PLATFORMS:
        return None
    try:
        cfg = hass.config_entries.async_get_entry(entry.config_entry_id) if entry.config_entry_id else None
        src = cfg.options.get('entity_id') if cfg is not None else None
        if isinstance(src, str) and '.' not in src:
            src = getattr(reg.async_get(src), 'entity_id', None)
    except Exception:
        return '?'
    return src if isinstance(src, str) and src else '?'

def _class_protected(hass: HomeAssistant, entity_id: str) -> bool:
    if not entity_id.startswith('cover.'):
        return False
    st = hass.states.get(entity_id) if hasattr(hass, 'states') else None
    reg = er.async_get(hass).async_get(entity_id)
    classes = {st.attributes.get('device_class') if st is not None else None, getattr(reg, 'device_class', None), getattr(reg, 'original_device_class', None)}
    return bool(classes & _PROTECTED_COVERS)
_PROTECT_DATA = f'{DOMAIN}_protected'
_PROTECT_BEFORE = ('knx/', 'config/entity_registry/', 'config/device_registry/')

def _sticky(hass: HomeAssistant | None, entity_id: str) -> bool:
    marks = getattr(hass, 'data', {}).get(_PROTECT_DATA)
    if marks is None or not (marks.uids or marks.gas):
        return False
    reg = er.async_get(hass).async_get(entity_id)
    if reg is None or reg.platform != 'knx':
        return False
    if reg.unique_id in marks.uids:
        return True
    config = ((_knx_store(hass) or {}).get(reg.domain) or {}).get(reg.unique_id)
    return bool(_knx_writes(config.get('knx') if isinstance(config, dict) else None) & marks.gas)

def _opening_gas(config: Any) -> set[str]:
    knx = config.get('knx') if isinstance(config, dict) else None
    out: set[str] = set()
    for key, v in knx.items() if isinstance(knx, dict) else ():
        if 'stop' in str(key) or not isinstance(v, dict):
            continue
        passive = v.get('passive') if isinstance(v.get('passive'), list) else []
        out |= {k for k in (_ga_key(ga) for ga in [v.get('write'), *passive] if ga not in (None, '')) if k is not None}
    return out

def _protect_refresh(hass: HomeAssistant | None) -> None:
    marks = protect.marks(hass)
    store = _knx_store(hass) if marks is not None else None
    if not store:
        return
    try:
        reg = er.async_get(hass)
        for uid, config in (store.get('cover') or {}).items():
            eid = reg.async_get_entity_id('cover', 'knx', uid)
            st = hass.states.get(eid) if eid else None
            entry = reg.async_get(eid) if eid else None
            classes = {st.attributes.get('device_class') if st else None, getattr(entry, 'device_class', None), getattr(entry, 'original_device_class', None)}
            if classes & _PROTECTED_COVERS:
                marks.add(str(uid), _opening_gas(config))
    except Exception as err:
        degrade('protected covers', err)

def _protect_marks_of(hass: HomeAssistant, entity_id: str) -> tuple[str | None, set[str]]:
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None or entry.platform != 'knx':
        return (None, set())
    config = ((_knx_store(hass) or {}).get(entry.domain) or {}).get(entry.unique_id)
    return (entry.unique_id, _opening_gas(config))
_ASSISTANTS = ('conversation', 'cloud.alexa', 'cloud.google_assistant')

def _protect_mark(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    marks = protect.marks(hass)
    if marks is None:
        return []
    done: list[str] = []
    for eid in entity_ids:
        uid, gas = _protect_marks_of(hass, eid)
        if uid is None:
            continue
        marks.add_deploy(str(uid), gas)
        done.append(eid)
    if done:
        _unexpose(hass, [*done, *_wrappers_of(hass, set(done))])
    return done

def _protect_unmark(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    marks = protect.marks(hass)
    if marks is None:
        return []
    by_uid: dict[str, str] = {}
    store = _knx_store(hass) or {}
    alive = {str(u) for configs in store.values() for u in configs or {}}
    for eid in entity_ids:
        uid, gas = _protect_marks_of(hass, eid)
        if uid is None:
            continue
        by_uid[str(uid)] = eid
        entry = er.async_get(hass).async_get(eid)
        config = (store.get(entry.domain) or {}).get(entry.unique_id) if entry is not None else None
        sends = gas | _knx_writes(config.get('knx') if isinstance(config, dict) else None)
        for old, old_gas in marks.deploy.items():
            if old not in alive and old_gas & sends:
                by_uid.setdefault(old, eid)
    gone = marks.unmark_deploy(set(by_uid))
    _protect_refresh(hass)
    return list(dict.fromkeys((by_uid[u] for u in sorted(gone))))

def _wrappers_of(hass: HomeAssistant, sources: set[str]) -> list[str]:
    out: list[str] = []
    with contextlib.suppress(Exception):
        for ent in list(er.async_get(hass).entities.values()):
            if ent.platform in _WRAPPER_PLATFORMS and _wrapped_source(hass, ent.entity_id) in sources:
                out.append(ent.entity_id)
    return out

def _unexpose(hass: HomeAssistant, entity_ids: list[str]) -> None:
    try:
        from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
    except ImportError:
        return
    for eid in entity_ids:
        for assistant in _ASSISTANTS:
            with contextlib.suppress(Exception):
                async_expose_entity(hass, assistant, eid, False)

def _own_device(hass: HomeAssistant | None, device_id: Any) -> bool:
    if hass is None or not isinstance(device_id, str):
        return False
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return False
    domains = {e.domain for eid in foreign.device_entry_ids(device) if (e := hass.config_entries.async_get_entry(eid))}
    return bool(domains) and domains <= _OWN_DEVICE_DOMAINS
_PRIVATE_DOMAINS = {'person', 'device_tracker', 'camera', 'zone', 'calendar', 'media_player', 'image', 'image_processing', 'todo'}
_PRIVATE_KEYS = {'access_token', 'entity_picture', 'latitude', 'longitude', 'gps_accuracy', 'altitude', 'course', 'speed', 'vertical_accuracy', 'user_id', 'device_trackers', 'source', 'token', 'location', 'message', 'description'}
_KEEP_ON_PRIVATE = {'friendly_name', 'device_class', 'icon', 'supported_features'}
_CONFIG_KEEP = {'version', 'components', 'time_zone', 'language', 'unit_system', 'state', 'recovery_mode', 'safe_mode', 'currency', 'external_url'}

def _scrub_config(cfg: Any) -> Any:
    if not isinstance(cfg, dict):
        return {}
    out = {k: v for k, v in cfg.items() if k in _CONFIG_KEEP}
    url = out.get('external_url')
    if url is not None:
        m = re.match('^(https?://)(?:[^@/?#]*@)?([^/?#@\\s]+)(/[^?#@\\s]*)?', url) if isinstance(url, str) else None
        if m:
            out['external_url'] = m.group(1) + m.group(2) + (m.group(3) or '')
        else:
            out.pop('external_url')
    return out
_STATE_ATTRS = {'friendly_name', 'device_class', 'state_class', 'unit_of_measurement', 'icon', 'supported_features', 'hvac_modes', 'hvac_action', 'min_temp', 'max_temp', 'target_temp_step', 'temperature', 'current_temperature', 'target_temp_high', 'target_temp_low', 'preset_modes', 'preset_mode', 'fan_modes', 'fan_mode', 'swing_modes', 'swing_mode', 'current_position', 'current_tilt_position', 'brightness', 'color_mode', 'supported_color_modes', 'color_temp_kelvin', 'min_color_temp_kelvin', 'max_color_temp_kelvin', 'percentage', 'percentage_step', 'options', 'min', 'max', 'step', 'mode', 'humidity', 'current_humidity', 'entity_id', 'restored'}
_PRIVATE_STATE = {'person', 'device_tracker', 'zone', 'sun', 'weather', 'camera', 'calendar', 'media_player', 'image', 'image_processing', 'todo', 'notify', 'tts', 'stt', 'conversation', 'alarm_control_panel', 'lock'}
_PRIVATE_PLATFORMS = {'mobile_app', 'sun'}
_STATE_TIMES = ('context', 'last_changed', 'last_updated', 'last_reported')
_SCRUBBED = ('get_states', 'get_config', 'cloud/status', 'config/device_registry/list', 'knx/group_monitor_info', 'get_panels', 'config/entity_registry/list', 'config/entity_registry/get', 'config/entity_registry/get_entries')
_PHONE_PLATFORMS = {'mobile_app'}

def _phone_ids(hass: HomeAssistant | None) -> set[str]:
    if hass is None:
        return set()
    with contextlib.suppress(Exception):
        return {e.entity_id for e in er.async_get(hass).entities.values() if e.platform in _PHONE_PLATFORMS}
    return set()

def _scrub_entities(result: Any, hass: HomeAssistant | None) -> Any:
    phones = _phone_ids(hass)

    def phone(e: Any) -> bool:
        return isinstance(e, dict) and (e.get('platform') in _PHONE_PLATFORMS or e.get('entity_id') in phones)
    if isinstance(result, list):
        return [e for e in result if not phone(e)]
    if isinstance(result, dict) and 'entity_id' in result:
        return None if phone(result) else result
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if k not in phones and (not phone(v))}
    return result

def _scrub_panels(panels: Any) -> Any:
    return {str(k): {'url_path': str(k)} for k in panels if k in sidebar.MENU_EXTRAS} if isinstance(panels, dict) else {}
_PRESENCE_CLASSES = {'motion', 'occupancy', 'presence', 'door', 'window', 'opening', 'garage_door', 'lock'}

def _scrub_states(states: Any, hass: HomeAssistant | None=None, visible: Callable[[str], bool] | None=None, hidden: set[str] | None=None) -> Any:
    if not isinstance(states, list):
        return []
    phones = set()
    if hass is not None:
        with contextlib.suppress(Exception):
            phones = {e.entity_id for e in er.async_get(hass).entities.values() if e.platform in _PRIVATE_PLATFORMS}
    gone = _phone_ids(hass)
    states[:] = [st for st in states if not (isinstance(st, dict) and st.get('entity_id') in gone)]
    for st in states:
        if not isinstance(st, dict):
            continue
        eid = str(st.get('entity_id', ''))
        attrs = st.get('attributes') if isinstance(st.get('attributes'), dict) else {}
        presence = eid.startswith('binary_sensor.') and attrs.get('device_class') in _PRESENCE_CLASSES
        if presence and visible is not None:
            try:
                presence = not visible(eid)
            except Exception:
                presence = True
        if eid.split('.')[0] in _PRIVATE_STATE or eid in phones or presence or (eid in (hidden or ())):
            st['attributes'] = {k: v for k, v in attrs.items() if k in _KEEP_ON_PRIVATE}
            st['state'] = 'hidden'
            for k in _STATE_TIMES:
                st.pop(k, None)
        else:
            st['attributes'] = {k: v for k, v in attrs.items() if k in _STATE_ATTRS}
    return states
_WEATHER_READS = {'temperature', 'humidity'}

def _cloud_reads(hass: HomeAssistant | None, eid: str, attr: str | None, visible: Callable[[str], bool]) -> bool:
    domain = eid.split('.')[0]
    if domain == 'weather':
        return attr in _WEATHER_READS
    if domain in _PRIVATE_STATE:
        return False
    if attr is not None and attr not in _STATE_ATTRS and (attr != dv.SETPOINT_GAP):
        return False
    try:
        reg = er.async_get(hass).async_get(eid) if hass is not None else None
        if reg is not None and reg.platform in _PRIVATE_PLATFORMS | _PHONE_PLATFORMS:
            return False
        if domain == 'binary_sensor':
            st = hass.states.get(eid) if hass is not None else None
            classes = {st.attributes.get('device_class') if st else None} | ({reg.device_class, reg.original_device_class} if reg else set())
            if classes & _PRESENCE_CLASSES and (not visible(eid)):
                return False
    except Exception:
        return False
    return True

def _history_reads(hass: HomeAssistant, eid: str, attr: str | None) -> bool:
    loaded = hass.config_entries.async_loaded_entries(DOMAIN)
    return bool(loaded) and _cloud_reads(hass, eid, attr, loaded[0].runtime_data._state_visible)

def _scrub_cloud(status: Any) -> Any:
    if not isinstance(status, dict):
        return {}
    prefs = status.get('prefs') if isinstance(status.get('prefs'), dict) else {}
    return {'logged_in': status.get('logged_in'), 'cloud': status.get('cloud'), 'prefs': {k: prefs.get(k) for k in ('alexa_enabled', 'google_enabled') if k in prefs}}

def _owned_knx(hass: HomeAssistant | None, entity_id: Any, owned: set[str] | None) -> bool:
    if hass is None or owned is None or (not isinstance(entity_id, str)):
        return False
    entry = er.async_get(hass).async_get(entity_id)
    return entry is not None and entry.platform == 'knx' and (entry.unique_id in owned)

def _ga_key(value: Any) -> str | None:
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

def _ga_range(key: str) -> str | None:
    try:
        raw = int(key)
    except (TypeError, ValueError):
        return None
    return f'{raw >> 11}/{raw >> 8 & 7}'

def _ga_text(key: str) -> str | None:
    try:
        raw = int(key)
    except (TypeError, ValueError):
        return None
    return f'{raw >> 11}/{raw >> 8 & 7}/{raw & 255}'
_ALLOWED_ITEM = re.compile('(\\d{1,2})/(\\d)(?:/(\\d{1,3})(?:-(\\d{1,3}))?)?')

def _range_allows(allowed: set[str], key: str) -> bool:
    try:
        raw = int(key)
    except (TypeError, ValueError):
        return False
    main, middle, sub = (raw >> 11, raw >> 8 & 7, raw & 255)
    for item in allowed:
        m = _ALLOWED_ITEM.fullmatch(str(item).strip())
        if not m or int(m.group(1)) != main or int(m.group(2)) != middle:
            continue
        if m.group(3) is None:
            return True
        lo = int(m.group(3))
        hi = int(m.group(4)) if m.group(4) is not None else lo
        if lo <= sub <= hi:
            return True
    return False

def _knx_writes(value: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(value, dict):
        if 'write' in value and value['write'] not in (None, ''):
            out.add(_ga_key(value['write']) or '?')
        for v in value.values():
            out |= _knx_writes(v)
    elif isinstance(value, list):
        for v in value:
            out |= _knx_writes(v)
    return out

def _knx_listens(value: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(value, dict):
        for key in ('write', 'state'):
            if value.get(key) not in (None, ''):
                out.add(_ga_key(value[key]) or '?')
        passive = value.get('passive')
        for ga in passive if isinstance(passive, list) else []:
            out.add(_ga_key(ga) or '?')
        for v in value.values():
            out |= _knx_listens(v)
    elif isinstance(value, list):
        for v in value:
            out |= _knx_listens(v)
    return out
_KNX_KEY = []

def knx_data(hass: HomeAssistant | None) -> Any | None:
    data = getattr(hass, 'data', None)
    if data is None:
        return None
    if not _KNX_KEY:
        try:
            from homeassistant.components.knx.const import KNX_MODULE_KEY
            _KNX_KEY.append(KNX_MODULE_KEY)
        except Exception:
            _KNX_KEY.append('knx')
    try:
        return data.get(_KNX_KEY[0]) or data.get('knx')
    except Exception as err:
        degrade('knx integration data', err)
        return None

def _knx_store(hass: HomeAssistant | None) -> dict[str, Any] | None:
    knx = knx_data(hass)
    if knx is None:
        return None
    try:
        store = knx.config_store.data['entities']
        if not isinstance(store, dict):
            raise TypeError(f'entities is {type(store).__name__}')
        return store
    except Exception as err:
        degrade('knx entity store', err)
        return None

def _foreign_knx_writes(hass: HomeAssistant | None, owned: set[str] | None, skip: str | None=None) -> set[str] | None:
    store = _knx_store(hass)
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
            key = str(raw) if isinstance(raw, int) else _ga_key(str(ga))
            if key:
                out.add(key)
    try:
        mine: set[str] = set()
        for configs in store.values():
            for uid, config in configs.items():
                if owned is not None and uid in owned:
                    mine |= _knx_listens(config.get('knx') if isinstance(config, dict) else None)
        for device in list(knx_data(hass).xknx.devices):
            for ga in device.group_addresses():
                raw = getattr(ga, 'raw', None)
                key = str(raw) if isinstance(raw, int) else _ga_key(str(ga))
                if key and key not in mine:
                    out.add(key)
    except Exception:
        _LOGGER.debug('Hauflo: KNX devices not read', exc_info=True)
    out.discard('?')
    return out

def knx_addresses(hass: HomeAssistant | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for ga, ents in (getattr(knx_data(hass), 'group_address_entities', None) or {}).items():
        raw = getattr(ga, 'raw', None)
        text = _ga_text(str(raw)) if isinstance(raw, int) else str(ga)
        for ent in ents or ():
            eid = getattr(ent, 'entity_id', None)
            if isinstance(eid, str) and text and (text not in out.setdefault(eid, [])):
                out[eid].append(text)
    for eid in out:
        out[eid].sort(key=lambda t: [int(x) if x.isdigit() else 0 for x in t.split('/')])
    return out

def _foreign_holders(hass: HomeAssistant | None, owned: set[str] | None, skip: str | None=None) -> dict[str, list[tuple[str | None, int | None]]] | None:
    store = _knx_store(hass)
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
        key = str(raw) if isinstance(raw, int) else _ga_key(str(ga))
        for i in ids:
            if getattr(i, 'ui', False) or getattr(i, 'unique_id', None) == skip or (owned is not None and getattr(i, 'unique_id', None) in owned):
                continue
            if key:
                out.setdefault(key, []).append((getattr(i, 'platform', None), None))
    with contextlib.suppress(Exception):
        for device in list(knx_data(hass).xknx.devices):
            for ga in device.group_addresses():
                raw = getattr(ga, 'raw', None)
                key = str(raw) if isinstance(raw, int) else _ga_key(str(ga))
                if key and key not in mine and (key not in out):
                    out[key] = [(None, None)]
    return out
_SCENE_EID = re.compile('scene\\.[a-z0-9_]{1,128}')

def _knx_scenes_owned(hass: HomeAssistant | None, owned: set[str] | None) -> dict[str, tuple[str, str, int]]:
    if hass is None or not owned:
        return {}
    configs = (_knx_store(hass) or {}).get('scene') or {}
    if not configs or not isinstance(configs, dict):
        return {}
    reg = er.async_get(hass)
    out: dict[str, tuple[str, str, int]] = {}
    for uid, config in configs.items():
        if uid not in owned or not isinstance(config, dict) or (not isinstance(config.get('knx'), dict)):
            continue
        knx = config['knx']
        ga = knx.get('ga_scene')
        key = _ga_key(ga.get('write')) if isinstance(ga, dict) else None
        number = knx.get('scene_number')
        if key is None or not key.isdigit() or isinstance(number, bool) or (not isinstance(number, int)) or (not 1 <= number <= 64):
            continue
        eid = reg.async_get_entity_id('scene', 'knx', uid)
        if eid and _SCENE_EID.fullmatch(eid):
            out[eid] = (uid, key, number)
    return out

def _knx_scene_target(hass: HomeAssistant | None, owned: set[str] | None, allowed: set[str], entity_id: Any) -> tuple[str, int]:
    if not isinstance(entity_id, str) or not _SCENE_EID.fullmatch(entity_id):
        raise PermissionError('not a scene')
    found = _knx_scenes_owned(hass, owned).get(entity_id)
    if found is None:
        raise PermissionError("not a KNX scene of Hauflo's")
    uid, key, number = found
    if not _range_allows(allowed, key):
        raise PermissionError("its address isn't allowed in Home Assistant")
    holders = _foreign_holders(hass, None, uid)
    if holders is None or any((p != 'scene' for p, _n in holders.get(key, []))):
        raise PermissionError('not only scenes listen on its address')
    text = _ga_text(key)
    if text is None:
        raise PermissionError('address')
    return (text, number)

def _knx_scenes_learnable(hass: HomeAssistant | None, owned: set[str] | None, allowed: set[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for eid in _knx_scenes_owned(hass, owned):
        with contextlib.suppress(PermissionError):
            out[eid] = _knx_scene_target(hass, owned, allowed, eid)[1]
    return out

def _knx_config_ok(hass: HomeAssistant | None, msg: dict[str, Any], owned: set[str] | None) -> bool:
    data = msg.get('data')
    writes = _knx_writes(data.get('knx') if isinstance(data, dict) else None)
    if not writes:
        return True
    uid = None
    if msg.get('type') == 'knx/update_entity' and hass is not None:
        entry = er.async_get(hass).async_get(msg.get('entity_id'))
        uid = entry.unique_id if entry is not None else None
    if '?' in writes:
        return False
    foreign = _foreign_knx_writes(hass, owned, uid)
    if foreign is None:
        return False
    clash = writes & foreign
    if not clash:
        return True
    knx = data.get('knx') if isinstance(data, dict) else None
    number = knx.get('scene_number') if isinstance(knx, dict) else None
    holders = _foreign_holders(hass, owned, uid)
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

def _device_entities_owned(hass: HomeAssistant | None, device_id: Any, owned: set[str] | None) -> bool:
    if hass is None or owned is None or (not isinstance(device_id, str)):
        return False
    ents = er.async_entries_for_device(er.async_get(hass), device_id, include_disabled_entities=True)
    return all((e.platform != 'knx' or e.unique_id in owned for e in ents))

def _foreign_device_placeable(hass: HomeAssistant | None, device_id: Any, area_id: Any, owned_entries: set[str] | None) -> bool:
    if hass is None or not isinstance(device_id, str) or (not isinstance(area_id, str)) or (not area_id):
        return False
    device = dr.async_get(hass).async_get(device_id)
    if device is None or getattr(device, 'entry_type', None) is not None:
        return False
    ents = er.async_entries_for_device(er.async_get(hass), device_id, include_disabled_entities=True)
    if not ents or any((getattr(e, 'platform', None) in ('knx', DOMAIN) for e in ents)):
        return False
    try:
        if ar.async_get(hass).async_get_area(area_id) is None:
            return False
    except Exception:
        return False
    return getattr(device, 'area_id', None) is None or _owns(owned_entries, 'area', area_id)
_FOREIGN_NAME_MAX = TUNING['ownership']['foreign_name_max']

def _foreign_entity_shown(hass: HomeAssistant | None, eid: str, msg: dict[str, Any]) -> bool:
    if hass is None or not set(msg) <= {'id', 'type', 'entity_id', 'hidden_by', 'name'} or (not set(msg) & {'hidden_by', 'name'}):
        return False
    if 'hidden_by' in msg and msg['hidden_by'] not in ('user', None):
        return False
    if 'name' in msg and msg['name'] is not None and (not (isinstance(msg['name'], str) and msg['name'].strip() and (len(msg['name']) <= _FOREIGN_NAME_MAX) and (not foreign.UNSAFE_NAME.search(msg['name'])))):
        return False
    entry = er.async_get(hass).async_get(eid)
    if entry is None or not getattr(entry, 'device_id', None) or entry.platform in ('knx', DOMAIN, 'mobile_app'):
        return False
    device = dr.async_get(hass).async_get(entry.device_id)
    if device is None or getattr(device, 'entry_type', None) is not None:
        return False
    ents = er.async_entries_for_device(er.async_get(hass), entry.device_id, include_disabled_entities=True)
    return not any((getattr(e, 'platform', None) in ('knx', DOMAIN) for e in ents))

def _owns(owned_entries: set[str] | None, kind: str, item_id: Any) -> bool:
    return owned_entries is not None and isinstance(item_id, str) and (f'{kind}:{item_id}' in owned_entries)

def _area_floor_empty(hass: HomeAssistant | None, area_id: Any) -> bool:
    if hass is None or not isinstance(area_id, str):
        return False
    try:
        from homeassistant.helpers import area_registry as ar
        area = ar.async_get(hass).async_get_area(area_id)
    except Exception:
        return False
    return area is not None and (not area.floor_id)

def _empty_area(hass: HomeAssistant | None, area_id: Any) -> bool:
    if hass is None or not isinstance(area_id, str):
        return False
    from homeassistant.helpers import area_registry as ar
    if ar.async_get(hass).async_get_area(area_id) is None:
        return False
    return not er.async_entries_for_area(er.async_get(hass), area_id) and (not dr.async_entries_for_area(dr.async_get(hass), area_id))
_STOCK_AREAS = f'{DOMAIN}_stock_area_names'

async def _load_stock_area_names(hass: HomeAssistant) -> dict[str, str]:
    from homeassistant.components.onboarding.const import DEFAULT_AREAS
    from homeassistant.helpers.translation import async_get_translations
    names: dict[str, str] = {}
    for lang in {hass.config.language or 'en', 'en'}:
        tr = await async_get_translations(hass, lang, 'area', {'onboarding'})
        for area in DEFAULT_AREAS:
            if (name := tr.get(f'component.onboarding.area.{area.key}')):
                names[name] = area.icon
    hass.data[_STOCK_AREAS] = names
    return names

def _stock_area(hass: HomeAssistant | None, area_id: Any) -> bool:
    names = hass.data.get(_STOCK_AREAS) if hass is not None else None
    if not names or not isinstance(area_id, str):
        return False
    from homeassistant.helpers import area_registry as ar
    area = ar.async_get(hass).async_get_area(area_id)
    if area is None or area.name not in names or names[area.name] != area.icon:
        return False
    if area.floor_id or area.aliases or area.picture or area.labels:
        return False
    if getattr(area, 'temperature_entity_id', None) or getattr(area, 'humidity_entity_id', None):
        return False
    return _empty_area(hass, area_id)

def _unused_label(hass: HomeAssistant | None, label_id: Any) -> bool:
    if hass is None or not isinstance(label_id, str):
        return False
    if er.async_entries_for_label(er.async_get(hass), label_id):
        return False
    if dr.async_entries_for_label(dr.async_get(hass), label_id):
        return False
    from homeassistant.helpers import area_registry as ar
    return not ar.async_entries_for_label(ar.async_get(hass), label_id)

def _forbidden(value: Any) -> bool:
    if isinstance(value, str):
        return value in _NEVER_DOMAINS or value.startswith(_NEVER_EXPOSE)
    if isinstance(value, dict):
        return any((_forbidden(k) or _forbidden(v) for k, v in value.items()))
    if isinstance(value, list):
        return any((_forbidden(v) for v in value))
    return False
_BENCH_NET = ipaddress.ip_network('198.18.0.0/15')
_KNX_SECURE_KEYS = ('user_id', 'user_password', 'device_authentication', 'backbone_key', 'sync_latency_tolerance', 'knxkeys', 'secure_')
_KNX_CONN_KEYS = {'connection_type': 'type', 'host': 'host', 'port': 'port', 'local_ip': 'localIp', 'route_back': 'routeBack', 'multicast_group': 'multicastGroup', 'multicast_port': 'multicastPort', 'individual_address': 'individualAddress', 'tunnel_endpoint_ia': 'tunnelEndpoint'}

def knx_conn(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, Mapping):
        return None
    out: dict[str, Any] = {}
    for key, name in _KNX_CONN_KEYS.items():
        v = data.get(key)
        if isinstance(v, bool) or (isinstance(v, int) and 0 <= v <= 65535):
            out[name] = v
        elif isinstance(v, str) and v:
            out[name] = v[:64]
    ctype = str(data.get('connection_type') or '')
    out['secure'] = ctype.endswith('_secure') or bool(data.get('knxkeys_filename'))
    return out

def ha_url(raw: Any) -> str | None:
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

def _lan_host(value: Any) -> bool:
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
    if ip.version != 4 or ip.is_loopback or ip.is_unspecified or ip.is_reserved or ip.is_multicast or (ip in _BENCH_NET):
        return False
    return ip.is_private or ip.is_link_local
_HK_FILTER_KEYS = {'include_domains', 'include_entities', 'exclude_domains', 'exclude_entities', 'domains', 'entities', 'mode', 'include_exclude_mode', 'filter'}

def _hauflo_entity(hass: HomeAssistant | None, eid: Any, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    return not _protected(hass, eid) and _own_entity(hass, eid, owned_entries) and (_platform(hass, eid) != 'knx' or _owned_knx(hass, eid, owned))

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
        if not all((_hauflo_entity(hass, e, owned, owned_entries) for e in entities)):
            return False
        return set(data) <= {'entities'}
    if step in ('cameras', 'climate', 'bridged_device_triggers'):
        return not set(data) & _HK_FILTER_KEYS
    return False

def _flow_step(manager: Any, flow_id: str) -> str | None:
    try:
        return str(manager.async_get(flow_id).get('step_id'))
    except Exception:
        return None
_TPL = _TF['template']
_TPL_TOKENS = [(re.compile(t['re']), t.get('entity')) for t in _TPL['tokens']]
_TPL_MENU = set(_TPL['menu'])
_TPL_KEYS = {k: set(v) for k, v in _TPL['keys'].items()}
_TPL_OPTION_KEYS = {k: set(v) for k, v in _TPL['options_keys'].items()}
_TPL_CLASSES = set(_TPL['device_classes'])

def _template_entities(text: Any) -> list[str] | None:
    if not isinstance(text, str) or not text.strip() or len(text) > _TPL['max_state']:
        return None
    pos, ents = (0, [])
    while pos < len(text):
        for rx, group in _TPL_TOKENS:
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
    return eid.split('.')[0] not in _PRIVATE_STATE and _own_entity(hass, eid, owned_entries) and (_platform(hass, eid) != 'knx' or _owned_knx(hass, eid, owned))

def _template_step_ok(hass: HomeAssistant | None, data: dict[str, Any], step: str | None, owned: set[str] | None, owned_entries: set[str] | None, options: bool) -> bool:
    if step == 'user' and (not options):
        return set(data) == {'next_step_id'} and str(data.get('next_step_id')) in _TPL_MENU
    keys = (_TPL_OPTION_KEYS if options else _TPL_KEYS).get(str(step))
    if keys is None or not set(data) <= keys or any((k not in data for k in _TPL['required'])):
        return False
    if 'device_class' in data and str(data['device_class']) not in _TPL_CLASSES:
        return False
    if 'name' in data and (not (isinstance(data['name'], str) and 0 < len(data['name']) <= _TPL['max_name'])):
        return False
    ents = _template_entities(data.get('state'))
    return ents is not None and all((_rule_reads_ok(hass, e, owned, owned_entries) for e in ents))
_SAX = _TF['switch_as_x']
_SAX_KEYS = {k: set(v) for k, v in _SAX['keys'].items()}
_SAX_OPTION_KEYS = {k: set(v) for k, v in _SAX['options_keys'].items()}

def _switch_as_x_step_ok(hass: HomeAssistant | None, data: dict[str, Any], step: str | None, owned: set[str] | None, owned_entries: set[str] | None, options: bool) -> bool:
    keys = (_SAX_OPTION_KEYS if options else _SAX_KEYS).get(str(step))
    if keys is None or not set(data) <= keys:
        return False
    if 'invert' in data and (not isinstance(data['invert'], bool)):
        return False
    if options:
        return True
    if any((k not in data for k in _SAX['required'])) or str(data.get('target_domain')) not in _SAX['target_domains']:
        return False
    eid = data.get('entity_id')
    return isinstance(eid, str) and eid.split('.', 1)[0] in _SAX['source_domains'] and _hauflo_entity(hass, eid, owned, owned_entries)

def _flow_input_ok(hass: HomeAssistant, domain: str | None, data: dict[str, Any], memo: dict[str, list[str]] | None=None, flow_id: str='', step: str | None=None, owned: set[str] | None=None, owned_entries: set[str] | None=None, options: bool=False) -> bool:
    if _forbidden(data):
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
        rest = [e for e in ents if not _hauflo_entity(hass, e, owned, owned_entries)]
        if not rest:
            return True
        return step in foreign.MEMBER_DOMAINS and (not data.get('hide_members')) and all((isinstance(e, str) and e.split('.', 1)[0] == step and foreign.member_ok(hass, e) for e in rest))
    if domain == 'yandex_smart_home':
        if any((str(k).startswith(('skill', 'cloud', 'instance', 'user_id', 'token')) for k in data)):
            return False
        if not (data.get('filter_source', 'label') == 'label' and data.get('connection_type', 'cloud') == 'cloud'):
            return False
        return 'label' not in data or _label_ok(hass, data.get('label'), owned, owned_entries)
    if domain == 'knx':
        if step is not None and (step.startswith(('secure', 'knxkeys')) or 'postgres' in step):
            return False
        if any((str(k).startswith(_KNX_SECURE_KEYS) for k in data)):
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
        if not all((_lan_host(data.get(k)) for k in ('host', 'local_ip', 'gateway'))):
            return False
        mcast = data.get('multicast_group')
        if mcast not in (None, ''):
            try:
                return ipaddress.ip_address(str(mcast)).is_multicast
            except ValueError:
                return False
    return True
_LOVELACE_TYPES = {'sections', 'panel', 'masonry', 'grid', 'tile', 'heading', 'horizontal-stack', 'vertical-stack', 'button', 'picture-glance', 'cover-open-close', 'cover-position', 'target-temperature', 'light-brightness', 'climate-preset-modes', 'climate-hvac-modes', 'climate-fan-modes', 'numeric-input', 'thermostat', 'custom:hauflo-scenes', 'custom:hauflo-alert', 'custom:hauflo-room', 'custom:hauflo-lights', 'custom:hauflo-hero', 'custom:hauflo-climate', 'custom:hauflo-status', 'custom:hauflo-devices', 'custom:hauflo-sliders', 'custom:hauflo-covers', 'custom:hauflo-acs', 'custom:hauflo-automations', 'custom:hauflo-title', 'custom:hauflo-area', 'custom:hauflo-layout', 'custom:hauflo-section', 'custom:hauflo-insights', 'custom:hauflo-camera'}
_READ_ONLY_DOMAINS = {'sensor', 'binary_sensor', 'weather', 'sun', 'person', 'zone', 'device_tracker', 'calendar', 'image', 'camera', 'event'}
_ACTIONS = {'more-info', 'toggle', 'none', 'navigate', 'perform-action', 'call-service'}
_CARD_SERVICES = {'homeassistant.toggle', 'homeassistant.turn_on', 'homeassistant.turn_off', 'light.turn_on', 'light.turn_off', 'light.toggle', 'switch.turn_on', 'switch.turn_off', 'switch.toggle', 'cover.open_cover', 'cover.close_cover', 'cover.stop_cover', 'cover.set_cover_position', 'cover.toggle', 'scene.turn_on', 'fan.turn_on', 'fan.turn_off', 'fan.set_percentage', 'climate.set_temperature', 'climate.set_hvac_mode', 'climate.set_preset_mode', 'climate.set_fan_mode', 'climate.set_swing_mode', 'climate.turn_off', 'knx.send'}
_ENTITY_RE = re.compile('[a-z_]+\\.[a-z0-9_]+')
_HAUFLO_ID_RE = re.compile('^[^\\x00-\\x1f\\x7f]{1,256}$')
_COPY_RE = re.compile('-copy-\\d')
_NAV_RE = re.compile('^/(hauflo-[a-z0-9_-]+(/[a-z0-9_-]*)?|config/scene/edit/hauflo_[a-z0-9_]+)$')

def _owned_knx_writes(hass: HomeAssistant | None, owned: set[str] | None) -> set[str]:
    store = _knx_store(hass)
    if store is None:
        return set()
    out: set[str] = set()
    for configs in store.values():
        for uid, config in configs.items():
            if owned is not None and uid in owned:
                out |= _knx_writes(config.get('knx') if isinstance(config, dict) else None)
    out.discard('?')
    return out
_TARGET_KEYS = {'entity_id', 'device_id', 'area_id', 'floor_id', 'label_id'}
_TEXT_KEYS = {'icon', 'name', 'title', 'label', 'text', 'theme', 'url_path', 'path', 'color'}
_RUNS = ('perform-action', 'call-service')
_IMAGE_KEYS = {'image', 'picture', 'camera_image', 'background', 'icon_image', 'entity_picture'}
_IMAGE_RE = re.compile('(?:/local/|/api/image/serve/|/api/image_proxy/|/api/camera_proxy/|/hauflo_static/)[A-Za-z0-9_./%?=&+-]*')
_DATA_IMAGE_RE = re.compile('data:image/(?:png|jpeg|gif|webp)(?:;[a-z0-9=_-]+)*;base64,[A-Za-z0-9+/=]*')
_DATA_SVG_RE = re.compile('data:image/svg\\+xml;base64,([A-Za-z0-9+/=]*)')
_SVG_TAGS = {'svg', 'defs', 'g', 'linearGradient', 'radialGradient', 'stop', 'rect', 'path', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'title'}
_SVG_LOCAL_URL = re.compile('url\\(#[A-Za-z0-9_-]+\\)')
_ENCODED_PATH = re.compile('%(?:2e|2f|5c|25)', re.IGNORECASE)

def _svg_ok(b64: str) -> bool:
    import base64
    import xml.etree.ElementTree as ET
    try:
        text = base64.b64decode(b64, validate=True).decode('utf-8')
        if '<!' in text or '<?' in text:
            return False
        root = ET.fromstring(text)
    except Exception:
        return False
    for el in root.iter():
        if not isinstance(el.tag, str) or el.tag.rsplit('}', 1)[-1] not in _SVG_TAGS:
            return False
        for key, val in el.attrib.items():
            name = key.rsplit('}', 1)[-1].lower()
            if name.startswith('on') or 'href' in name or name == 'style' or ('url(' in val and (not _SVG_LOCAL_URL.fullmatch(val.strip()))):
                return False
    return True

def _image_ok(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if _DATA_IMAGE_RE.fullmatch(value):
        return True
    if (svg := _DATA_SVG_RE.fullmatch(value)) is not None:
        return _svg_ok(svg.group(1))
    return bool(_IMAGE_RE.fullmatch(value)) and '..' not in value and ('//' not in value) and (not _ENCODED_PATH.search(value))

def _area_lights_owned(hass: HomeAssistant | None, area_id: Any, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    if hass is None or not isinstance(area_id, str) or (not re.fullmatch('[a-z0-9_]+', area_id)) or (area_id in ('all', 'none')):
        return False
    try:
        ents = er.async_get(hass)
        lights = [e for e in er.async_entries_for_area(ents, area_id) if e.domain == 'light']
        for device in dr.async_entries_for_area(dr.async_get(hass), area_id):
            lights += [e for e in er.async_entries_for_device(ents, device.id) if e.domain == 'light' and (not e.area_id)]
        return all((_hauflo_entity(hass, e.entity_id, owned, owned_entries) for e in lights))
    except Exception:
        return False

def _owned_scene_sends(hass: HomeAssistant | None, owned: set[str] | None) -> dict[str, set[int]]:
    store = _knx_store(hass)
    if store is None or owned is None:
        return {}
    out: dict[str, set[int]] = {}
    for uid, config in (store.get('scene') or {}).items():
        knx = config.get('knx') if isinstance(config, dict) else None
        if uid not in owned or not isinstance(knx, dict):
            continue
        key = _ga_key((knx.get('ga_scene') or {}).get('write') if isinstance(knx.get('ga_scene'), dict) else None)
        number = knx.get('scene_number')
        if key is None:
            continue
        out.setdefault(key, set()).update({128 | number - 1} if type(number) is int and 1 <= number <= 64 else set(range(128, 192)))
    marks = getattr(hass, 'data', {}).get(_PROTECT_DATA)
    for key in set(getattr(marks, 'gas', None) or ()) | _protected_writes(hass):
        out.pop(key, None)
    return out

def _protected_writes(hass: HomeAssistant | None) -> set[str]:
    out: set[str] = set()
    store = _knx_store(hass)
    if store is None:
        return out
    with contextlib.suppress(Exception):
        reg = er.async_get(hass)
        for domain, configs in store.items():
            for uid, config in (configs or {}).items():
                eid = reg.async_get_entity_id(domain, 'knx', uid)
                if eid and _protected(hass, eid):
                    out |= _knx_writes(config.get('knx') if isinstance(config, dict) else None)
    out.discard('?')
    return out

def _shown_protected(hass: HomeAssistant | None, eid: Any, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    if hass is None or not isinstance(eid, str) or eid.startswith(_NEVER_EXPOSE):
        return False
    if not (_sticky(hass, eid) or _class_protected(hass, eid)):
        return False
    return _own_entity(hass, eid, owned_entries) and (_platform(hass, eid) != 'knx' or _owned_knx(hass, eid, owned))
_WORD_RE = re.compile('[^\\W_]+', re.UNICODE)

def _words(text: Any) -> set[str]:
    return set(_WORD_RE.findall(voice._norm(str(text or ''))))

def _named_as(hass: HomeAssistant, eid: str, name: Any) -> bool:
    if not isinstance(name, str):
        return False
    st = hass.states.get(eid) if hasattr(hass, 'states') else None
    reg = er.async_get(hass).async_get(eid)
    known = _words(st.attributes.get('friendly_name') if st is not None else None) | _words(getattr(reg, 'name', None)) | _words(getattr(reg, 'original_name', None))
    rooms: set[str] = set()
    with contextlib.suppress(Exception):
        from homeassistant.helpers import area_registry as ar
        for area in ar.async_get(hass).async_list_areas():
            rooms |= _words(area.name)
    words = _words(name)
    rest = words - known
    return bool(words & known) and all((w in rooms or len(w) <= 3 or any((w.startswith(r[:-1]) for r in rooms if len(r) > 3)) for w in rest))
_ROW_KEYS = {'entity', 'name', 'secondary', 'icon', 'icon_type', 'hidden', 'block', 'tap_action', 'hold_action', 'double_tap_action', 'view', 'min', 'max', 'step'}
_ROW_VIEWS = set(TUNING['cards']['regulator']['views'])
_ROW_NUM_ABS = 1000000000.0
_ROW_BLOCKS = {'control', 'readings', 'diagnostics'}
_ROW_TEXT_MAX = 120
_ROWS_MAX = 300
_ORDER_RE = re.compile('^(?:[a-z_]+\\.[a-z0-9_]+|[a-z][a-z0-9_]{0,39})$')
_SUB_SHOW = {'auto', 'show', 'hide'}
_SUB_MODES_MAX = 64

def _subs_why(card: dict[str, Any]) -> str | None:
    for k in dashmerge.SUB_KEYS:
        if k not in card:
            continue
        v = card[k]
        if k.startswith('show_') and v not in _SUB_SHOW:
            return f'{k} not auto / show / hide'
        if k.endswith('_modes') and (not (isinstance(v, list) and len(v) <= _SUB_MODES_MAX and all((isinstance(m, (str, int)) and (not isinstance(m, bool)) and (len(str(m)) <= _ROW_TEXT_MAX) for m in v)))):
            return f'{k} not a list of modes'
    return None

def _rows_why(card: dict[str, Any], hass: HomeAssistant | None=None) -> str | None:
    bad = 'rows not in their form'
    if 'auto' in card and (not isinstance(card['auto'], bool)):
        return bad
    order = card.get('order')
    if 'order' in card and (not (isinstance(order, list) and len(order) <= _ROWS_MAX and all((isinstance(x, str) and _ORDER_RE.fullmatch(x) for x in order)))):
        return bad
    items = card.get('items')
    if 'items' not in card:
        return None
    if not isinstance(items, list) or len(items) > _ROWS_MAX:
        return bad
    for row in items:
        if not isinstance(row, dict) or set(row) - _ROW_KEYS or (not (isinstance(row.get('entity'), str) and _ENTITY_RE.fullmatch(row['entity']))):
            return bad
        if any((k in row and (not (isinstance(row[k], str) and len(row[k]) <= _ROW_TEXT_MAX)) for k in ('name', 'secondary', 'icon', 'icon_type'))):
            return bad
        if 'hidden' in row and (not isinstance(row['hidden'], bool)):
            return bad
        if 'block' in row and row['block'] not in _ROW_BLOCKS:
            return bad
        if 'view' in row and row['view'] not in _ROW_VIEWS:
            return bad
        if any((k in row and (not _row_num(row[k])) for k in ('min', 'max', 'step'))):
            return bad
        code = _row_reg_why(row, _reg_range(hass, row['entity']))
        if code:
            return f"{row['entity']} regulator {code}"
    return None
_REG_RANGES = TUNING['cards']['regulator']['ranges']
_REG_RANGE_ATTRS = set(TUNING['cards']['regulator']['range_attrs'])
_REG_FEATURE = {'fan': 1, 'cover': 4, 'valve': 4, 'media_player': 4}

def _reg_range(hass: HomeAssistant | None, entity_id: str) -> tuple[float, float] | None:
    state = hass.states.get(entity_id) if hass is not None else None
    if state is None:
        return None
    dom = entity_id.split('.', 1)[0]
    a = state.attributes or {}

    def n(x: Any) -> float | None:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None
    if dom in _REG_RANGE_ATTRS:
        lo, hi = (n(a.get('min')), n(a.get('max')))
        return (lo, hi) if lo is not None and hi is not None and (hi > lo) else None
    if dom not in _REG_RANGES:
        return None
    if dom == 'light':
        on = any((m != 'onoff' for m in a.get('supported_color_modes') or []))
    else:
        on = bool(int(n(a.get('supported_features')) or 0) & _REG_FEATURE.get(dom, 0))
    return (float(_REG_RANGES[dom][0]), float(_REG_RANGES[dom][1])) if on else None

def _row_reg_why(row: dict[str, Any], rng: tuple[float, float] | None) -> str | None:
    mn, mx, st = (row.get('min'), row.get('max'), row.get('step'))
    lo = mn if mn is not None else rng[0] if rng else None
    hi = mx if mx is not None else rng[1] if rng else None
    if rng and any((x is not None and (x < rng[0] or x > rng[1]) for x in (mn, mx))):
        return 'reg_out_of_range'
    if lo is not None and hi is not None and (lo >= hi):
        return 'reg_min_max'
    if st is not None and st <= 0:
        return 'reg_step'
    if st is not None and lo is not None and (hi is not None) and (st > hi - lo + 1e-09):
        return 'reg_step_range'
    return None

def _row_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and (not isinstance(v, bool)) and math.isfinite(v) and (abs(v) <= _ROW_NUM_ABS)

def _lovelace_ok(hass: HomeAssistant | None, config: Any, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    return _lovelace_refusal(hass, config, owned, owned_entries) is None
_WHY_MAX = 300

def _lovelace_refusal(hass: HomeAssistant | None, config: Any, owned: set[str] | None, owned_entries: set[str] | None) -> str | None:
    got = _lovelace_refusal_at(hass, config, owned, owned_entries)
    return got[0] if got is not None else None

def _lovelace_refusal_at(hass: HomeAssistant | None, config: Any, owned: set[str] | None, owned_entries: set[str] | None) -> tuple[str, list[Any]] | None:
    if not isinstance(config, dict):
        return ('config: not an object', [])
    scene_sends: list[dict[str, set[int]]] = []
    path: list[Any] = []
    why: list[tuple[str, list[Any]]] = []

    def no(reason: str) -> bool:
        if not why:
            where = ''.join((f'[{p}]' if isinstance(p, int) else f'.{p}' for p in path)).lstrip('.') or 'config'
            why.append((f'{where}: {reason}'[:_WHY_MAX], list(path)))
        return False

    def entity_why(eid: str) -> str:
        if _protected(hass, eid):
            return f'{eid} — protected, not shown by Hauflo'
        return f"{eid} — not Hauflo's ({foreign.shown_refusal(hass, eid) or 'not_shown'})"

    def entity_ok(eid: str) -> bool:
        domain = eid.split('.', 1)[0]
        if domain in _READ_ONLY_DOMAINS:
            return True
        return _hauflo_entity(hass, eid, owned, owned_entries) or _shown_protected(hass, eid, owned, owned_entries) or foreign.shown_ok(hass, eid) or no(entity_why(eid))

    def target_ids(value: Any) -> list[str] | None:
        items = value if isinstance(value, list) else [value]
        if not items or not all((isinstance(i, str) and _ENTITY_RE.fullmatch(i) for i in items)):
            return None
        return items

    def service_ok(v: dict[str, Any], card: str | None=None) -> bool:
        names = [v[k] for k in ('perform_action', 'service') if k in v and (not (k == 'service' and isinstance(v[k], dict) and ('perform_action' not in v)))]
        if not names or not all((isinstance(n, str) and n in _CARD_SERVICES for n in names)) or len(set(names)) != 1:
            return no(f"service {', '.join(map(str, names)) or '—'} not allowed")
        service = names[0]
        for k in ('target', 'data', 'service_data'):
            if k in v and (not isinstance(v[k], dict)):
                return no(f'{service}: {k} not an object')
        if service == 'knx.send':
            if set(v) & ({'target', 'service_data'} | _TARGET_KEYS) or 'data' not in v:
                return no('knx.send: only data.address and data.payload')
            data = v['data']
            if not scene_sends:
                scene_sends.append(_owned_scene_sends(hass, owned))
            payload = data.get('payload')
            allowed = scene_sends[0].get(_ga_key(data.get('address')) or '?')
            if set(data) - {'address', 'payload'} or not allowed:
                return no(f"knx.send to {data.get('address')} — not a scene Hauflo made")
            return isinstance(payload, list) and len(payload) == 1 and (type(payload[0]) is int) and (payload[0] in allowed) or no(f"knx.send to {data.get('address')} — not a scene's store")
        ids: list[str] = []
        areas: list[Any] = []
        for part, is_target in ((v.get('target') or {}, True), (v.get('data') or {}, False), (v.get('service_data') or {}, False), (v, False)):
            for k, x in part.items():
                if not isinstance(k, str):
                    return no(f'{service}: a key that is not text')
                lk = k.strip().lower()
                if k == 'entity_id':
                    got = target_ids(x)
                    if got is None:
                        return no(f'{service}: entity_id not exact ids')
                    ids += got
                elif k == 'area_id' and is_target and (service == 'light.turn_off'):
                    areas += x if isinstance(x, list) else [x]
                elif lk in _TARGET_KEYS or is_target:
                    return no(f'{service}: target {k} not allowed')
        if not ids and (not areas):
            return no(f'{service}: no entity_id named')
        if areas and (not all((_area_lights_owned(hass, a, owned, owned_entries) for a in areas))):
            return no(f"{service}: an area with lights not Hauflo's")
        for i in ids:
            if not (_hauflo_entity(hass, i, owned, owned_entries) or (i == card and _shown_protected(hass, i, owned, owned_entries))):
                return no(f"{service} on {i} — runs Hauflo's own entities only")
        return True

    def walk(v: Any, key: str='', card: str | None=None) -> bool:
        if isinstance(v, dict):
            if isinstance(v.get('entity'), str):
                card = v['entity']
                if 'name' in v and _shown_protected(hass, card, owned, owned_entries) and (not _named_as(hass, card, v['name'])):
                    return no(f'{card} — a protected one under another name')
            kind = v.get('type')
            if kind is not None and (not isinstance(kind, str) or kind not in _LOVELACE_TYPES):
                return no(f'card type {kind} not allowed')
            if set(v) & {'items', 'auto', 'order'} and str(kind).startswith('custom:hauflo-') and (rows_why := _rows_why(v, hass)):
                return no(f'{kind}: {rows_why}')
            if set(v) & set(dashmerge.SUB_KEYS) and str(kind).startswith('custom:hauflo-') and (subs_why := _subs_why(v)):
                return no(f'{kind}: {subs_why}')
            if 'hauflo_id' in v and (not (isinstance(v['hauflo_id'], str) and _HAUFLO_ID_RE.match(v['hauflo_id']))):
                return no('hauflo_id not plain text')
            action = v.get('action')
            if key.endswith('_action') or isinstance(action, str):
                if action not in _ACTIONS:
                    return no(f'action {action} not allowed')
                if action == 'navigate' and (not _NAV_RE.match(str(v.get('navigation_path', '')))):
                    return no("navigate outside Hauflo's dashboards")
                if action in _RUNS:
                    if not service_ok(v, card):
                        return False
                elif set(v) & ({'perform_action', 'service', 'target', 'data', 'service_data'} | _TARGET_KEYS):
                    return no(f'{action}: a service or a target on an action that runs none')
            elif 'perform_action' in v or isinstance(v.get('service'), str):
                if not service_ok(v, card):
                    return False
            elif 'target' in v or 'service_data' in v:
                return no('a target with no service')
            if key == 'state_image' and (not all((_image_ok(x) for x in v.values()))):
                return no('state_image: a picture from outside the house')
            for k, x in v.items():
                path.append(str(k))
                ok = walk(x, str(k), card)
                path.pop()
                if not ok:
                    return False
            return True
        if isinstance(v, list):
            for i, x in enumerate(v):
                path.append(i)
                ok = walk(x, key, card)
                path.pop()
                if not ok:
                    return False
            return True
        if isinstance(v, str):
            if key in ('perform_action', 'service', 'type', 'action', 'navigation_path', 'hauflo_id'):
                return True
            if key in _IMAGE_KEYS:
                return _image_ok(v) or no('a picture from outside the house')
            if v.startswith('/') and key == 'path':
                return bool(_NAV_RE.match(v)) or no("a path outside Hauflo's dashboards")
            if key not in _TEXT_KEYS:
                norm = v.strip().lower()
                if norm == 'all':
                    return no('«all» — every entity')
                pieces = [p.strip().lower() for p in v.split(',')]
                if any((_ENTITY_RE.fullmatch(p) for p in pieces)) and (len(pieces) > 1 or norm != v):
                    return no('entity ids not in their exact form')
                if _ENTITY_RE.fullmatch(v):
                    return entity_ok(v)
                if v.lower().startswith(('http://', 'https://', '//', 'javascript:', 'data:')):
                    return no('an address outside the house')
            return key not in ('url', 'url_path') or v.startswith('hauflo-') or no("url_path not Hauflo's")
        return True
    if walk(config):
        return None
    return why[0] if why else ('config: refused', [])
_NO_PRUNE_LISTS = {'conditions', 'visibility', 'entity_id', 'area_id', 'device_id', 'floor_id', 'label_id', 'payload', 'views'}
_PRUNE_MAX = 100
_EID_IN = re.compile('\\b([a-z_]+\\.[a-z0-9_]+)\\b')

def _prune_at(path: list[Any]) -> int | None:
    for i in range(len(path) - 1, 1, -1):
        if isinstance(path[i], int) and isinstance(path[i - 1], str) and (path[i - 1] not in _NO_PRUNE_LISTS):
            return i
    return None

def _first_entity(v: Any) -> str | None:
    if isinstance(v, str):
        return v if _ENTITY_RE.fullmatch(v) else None
    if isinstance(v, dict):
        if isinstance(v.get('entity'), str) and _ENTITY_RE.fullmatch(v['entity']):
            return v['entity']
        v = list(v.values())
    if isinstance(v, list):
        for x in v:
            if (e := _first_entity(x)) is not None:
                return e
    return None

def _lovelace_prune(hass: HomeAssistant | None, config: Any, owned: set[str] | None, owned_entries: set[str] | None) -> tuple[Any, list[dict[str, Any]]]:
    if not isinstance(config, dict):
        return (config, [])
    cfg = copy.deepcopy(config)
    dropped: list[dict[str, Any]] = []
    for _ in range(_PRUNE_MAX):
        got = _lovelace_refusal_at(hass, cfg, owned, owned_entries)
        at = _prune_at(got[1]) if got is not None else None
        if got is None or at is None:
            break
        holder: Any = cfg
        for k in got[1][:at]:
            holder = holder[k]
        item = holder.pop(got[1][at])
        why = got[0]
        named = _EID_IN.findall(why.split(': ', 1)[-1])
        eid = next((e for e in named if _ENTITY_RE.fullmatch(e) and e not in _CARD_SERVICES), None) or _first_entity(item)
        kind = 'protected' if 'protected' in why else 'not_hauflo' if "not Hauflo's" in why else 'refused'
        dropped.append({**({'entity': eid} if eid else {}), 'why': kind, 'detail': why[:_WHY_MAX]})
    return (cfg, dropped)

def _own_dashboard(hass: HomeAssistant | None, dashboard_id: Any) -> bool:
    if hass is None or not isinstance(dashboard_id, str):
        return False
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        for url_path, dash in hass.data[LOVELACE_DATA].dashboards.items():
            if (getattr(dash, 'config', None) or {}).get('id') == dashboard_id:
                return str(url_path).startswith('hauflo-') and (not _COPY_RE.search(str(url_path)))
    except Exception:
        return False
    return False

def _label_ok(hass: HomeAssistant | None, label_id: Any, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    if hass is None or not isinstance(label_id, str) or (not label_id):
        return False
    try:
        if dr.async_entries_for_label(dr.async_get(hass), label_id):
            return False
        return all((_hauflo_entity(hass, e.entity_id, owned, owned_entries) for e in er.async_entries_for_label(er.async_get(hass), label_id)))
    except Exception:
        return False

def _alice_labels(hass: HomeAssistant | None) -> set[str]:
    if hass is None:
        return set()
    return {str(e.options.get('label')) for e in hass.config_entries.async_entries('yandex_smart_home') if e.options.get('label')}

def _theme_ok(msg: dict[str, Any]) -> bool:
    data = msg.get('service_data')
    if not isinstance(data, dict) or not data or (not set(data) <= {'name', 'name_dark'}) or set(msg) - {'id', 'type', 'domain', 'service', 'service_data'}:
        return False
    return all((isinstance(v, str) and v in theme.names() for v in data.values()))

def _ws_allowed(msg: dict[str, Any], hass: HomeAssistant | None=None, owned: set[str] | None=None, owned_entries: set[str] | None=None) -> bool:
    t = str(msg.get('type', ''))
    if t == 'call_service':
        if (msg.get('domain'), msg.get('service')) == ('frontend', 'set_theme'):
            return _theme_ok(msg)
        return (str(msg.get('domain')), str(msg.get('service'))) in _SERVICES
    if t not in _WS_EXACT:
        return False
    if t == 'manifest/get':
        return isinstance(msg.get('integration'), str) and msg['integration'] in _MANIFESTS
    if t == 'config_entries/get':
        if 'domain' not in msg:
            tf = msg.get('type_filter')
            return set(msg) <= {'id', 'type', 'type_filter'} and (tf is None or (isinstance(tf, list) and len(tf) <= 5 and all((isinstance(x, str) for x in tf))))
        return isinstance(msg.get('domain'), str) and msg['domain'] in _ENTRY_DOMAINS
    if t == 'config/entity_registry/update':
        eid = msg.get('entity_id')
        if not isinstance(eid, str) or eid.startswith(_NEVER_EXPOSE):
            return False
        if not _own_entity(hass, eid, owned_entries):
            return foreign.wizard_refusal(hass, msg) is None if foreign.planned(hass, msg) else _foreign_entity_shown(hass, eid, msg)
        if _protected(hass, eid) and set(msg.get('labels') or []) & _alice_labels(hass):
            return False
        if 'device_class' in msg and _protected(hass, eid) and (_class_protected(hass, eid) or msg['device_class'] not in _PROTECTED_COVERS):
            return False
        if _forbidden(msg.get('new_entity_id')):
            return False
        reg_entry = er.async_get(hass).async_get(eid) if hass is not None else None
        if reg_entry is not None and reg_entry.platform == 'knx' and (not _owned_knx(hass, eid, owned)):
            return False
        return 'options_domain' not in msg
    if t in ('config/device_registry/remove', 'config/device_registry/remove_config_entry'):
        return _own_device(hass, msg.get('device_id')) and _device_entities_owned(hass, msg.get('device_id'), owned)
    if t == 'config/device_registry/update':
        placing = set(msg) <= {'id', 'type', 'device_id', 'area_id'}
        if _own_device(hass, msg.get('device_id')):
            return placing and _device_entities_owned(hass, msg.get('device_id'), owned)
        if foreign.planned(hass, msg):
            return foreign.wizard_refusal(hass, msg) is None
        return placing and _foreign_device_placeable(hass, msg.get('device_id'), msg.get('area_id'), owned_entries)
    if t in ('config/area_registry/update', 'config/floor_registry/update'):
        kind = 'area' if t.startswith('config/area') else 'floor'
        key = f'{kind}_id'
        if _owns(owned_entries, kind, msg.get(key)):
            return set(msg) <= {'id', 'type', key, 'name', 'aliases', 'icon', 'floor_id', 'level'}
        if kind == 'area' and set(msg) <= {'id', 'type', 'area_id', 'floor_id'} and msg.get('floor_id'):
            return _area_floor_empty(hass, msg.get('area_id'))
        return False
    if t in ('knx/delete_entity', 'knx/update_entity'):
        if t == 'knx/update_entity' and _protected(hass, msg.get('entity_id')):
            data = msg.get('data')
            ent = data.get('entity') if isinstance(data, dict) else None
            if isinstance(ent, dict) and 'device_class' in ent and (_class_protected(hass, str(msg.get('entity_id'))) or ent['device_class'] not in _PROTECTED_COVERS):
                return False
        return _owned_knx(hass, msg.get('entity_id'), owned) and (t == 'knx/delete_entity' or _knx_config_ok(hass, msg, owned))
    if t == 'knx/create_entity':
        return _knx_config_ok(hass, msg, owned)
    if t == 'config/area_registry/delete':
        aid = msg.get('area_id')
        return set(msg) <= {'id', 'type', 'area_id'} and (_owns(owned_entries, 'area', aid) and _empty_area(hass, aid) or _stock_area(hass, aid))
    if t == 'config/label_registry/delete':
        return _owns(owned_entries, 'label', msg.get('label_id')) and _unused_label(hass, msg.get('label_id'))
    if t.startswith('lovelace/') and _COPY_RE.search(str(msg.get('url_path') or '')):
        return False
    if t == 'lovelace/dashboards/create':
        return str(msg.get('url_path') or '').startswith('hauflo-')
    if t == 'lovelace/config':
        return str(msg.get('url_path') or '').startswith('hauflo-')
    if t == 'lovelace/config/save':
        return str(msg.get('url_path') or '').startswith('hauflo-') and _lovelace_ok(hass, msg.get('config'), owned, owned_entries)
    if t == 'lovelace/dashboards/update':
        return _own_dashboard(hass, msg.get('dashboard_id'))
    if t == 'lovelace/dashboards/delete':
        return set(msg) <= {'id', 'type', 'dashboard_id'} and _own_dashboard(hass, msg.get('dashboard_id'))
    if t == 'homeassistant/expose_entity':
        ids = msg.get('entity_ids')
        if not isinstance(ids, list):
            return False
        if msg.get('should_expose') is not False and any((_protected(hass, i) for i in ids)):
            return False
        if msg.get('should_expose') is False:
            return all((_own_entity(hass, i, owned_entries) and (_platform(hass, i) != 'knx' or _owned_knx(hass, i, owned)) for i in ids))
        return all((_hauflo_entity(hass, i, owned, owned_entries) for i in ids))
    return True

def _scrub_diagnostics(text: str) -> str:
    try:
        d = json.loads(text)
    except ValueError:
        return '{}'
    data = d.get('data') if isinstance(d, dict) and isinstance(d.get('data'), dict) else {}
    out: dict[str, Any] = {}
    ce = data.get('config-entry') if isinstance(data.get('config-entry'), dict) else None
    if ce is not None:
        flt = (ce.get('options') or {}).get('filter') if isinstance(ce.get('options'), dict) else None
        out['config-entry'] = {'options': {'filter': flt if isinstance(flt, dict) else {}}}
    entry = data.get('entry') if isinstance(data.get('entry'), dict) else None
    if entry is not None:
        opts = entry.get('options') if isinstance(entry.get('options'), dict) else {}
        out['entry'] = {'options': {k: opts.get(k) for k in ('filter_source', 'label', 'entry_aliases') if k in opts}}
    return json.dumps({'data': out})

def _scrub_devices(devices: Any, hass: HomeAssistant | None) -> Any:
    if not isinstance(devices, list):
        return []
    for d in devices:
        if isinstance(d, dict) and any((_entry_domain(hass, e) == 'mobile_app' for e in foreign.device_dict_entry_ids(d))):
            for k in ('name', 'name_by_user', 'model', 'manufacturer'):
                if k in d:
                    d[k] = None
    return devices
_CONTACT = re.compile('[^\\s@()<>\\[\\]\\"\',;]*@[^\\s()<>\\[\\]\\"\',;]*|\\+?\\d[\\d\\s()-]{8,}\\d')

def _redact_title(title: Any) -> str | None:
    if not isinstance(title, str):
        return None
    r = _CONTACT.sub('…', title).strip()[:200]
    return r if any((c.isalnum() for c in r)) else None

def _scrub_entries(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        return []
    out = []
    for e in entries:
        if not isinstance(e, dict) or e.get('domain') in _PHONE_PLATFORMS or (not isinstance(e.get('entry_id'), str)):
            continue
        out.append({'entry_id': e['entry_id'], 'domain': e.get('domain'), 'title': _redact_title(e.get('title'))})
    return out

def _scrub_monitor(info: Any) -> Any:
    if not isinstance(info, dict):
        return {}
    tg = info.get('recent_telegrams') if isinstance(info.get('recent_telegrams'), list) else []
    return {'project_loaded': info.get('project_loaded'), 'recent_telegrams': [{k: x.get(k) for k in ('destination', 'direction', 'telegramtype', 'timestamp')} for x in tg if isinstance(x, dict)]}

def _scrub_notes(event: Any, hass: HomeAssistant, owned_entries: set[str] | None=None) -> Any:
    if not isinstance(event, dict) or not isinstance(event.get('notifications'), dict):
        return event
    keep = {k: v for k, v in event['notifications'].items() if _entry_domain(hass, str(k)) == 'homekit' and owned_entries is not None and (str(k) in owned_entries)}
    return {**event, 'notifications': keep}
ALICE_NOTE = 'hauflo_alisa'

def _alice_creds_to_ha(hass: HomeAssistant, text: str) -> str:
    try:
        res = json.loads(text)
    except ValueError:
        return text
    if not isinstance(res, dict) or res.get('handler') != 'yandex_smart_home' or res.get('type') != 'create_entry':
        return text
    p = res.get('description_placeholders') or {}
    if not isinstance(p, dict) or not p.get('id'):
        return text
    lines = [f"ID: `{p.get('id')}`", f"Пароль / password: `{p.get('password', '')}`"]
    if p.get('otp') and p.get('otp') != '-':
        lines.append(f"Одноразовый код / one-time code: `{p['otp']}`")
    persistent_notification.async_create(hass, 'Для приложения «Дом с Алисой» / for the Yandex app:\n\n' + '\n\n'.join(lines), 'Hauflo: Алиса', ALICE_NOTE)
    res['description_placeholders'] = {'hauflo_note': ALICE_NOTE}
    return json.dumps(res)

def _platform(hass: HomeAssistant | None, entity_id: Any) -> str | None:
    entry = er.async_get(hass).async_get(entity_id) if hass is not None and isinstance(entity_id, str) else None
    return entry.platform if entry else None

def _entry_domain(hass: HomeAssistant, entry_id: str) -> str | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    return entry.domain if entry else None

def _flow_handler(manager: Any, flow_id: str) -> str | None:
    try:
        return str(manager.async_get(flow_id).get('handler'))
    except Exception:
        return None

def _route(method: str, bare: str) -> str | None:
    segs = bare.split('/')
    for rid, pattern, methods in _ROUTES:
        if method in methods and len(pattern) == len(segs) and all((p == s or (p[:1] == '{' and p[-1:] == '}' and bool(s)) for p, s in zip(pattern, segs))):
            return rid
    return None

def _owned_entry(owned_entries: set[str] | None, entry_id: Any) -> bool:
    return owned_entries is not None and isinstance(entry_id, str) and (entry_id in owned_entries)

def _http_allowed(hass: HomeAssistant, method: str, path: str, body: Any, owned_entries: set[str] | None=None, memo: dict[str, list[str]] | None=None, owned: set[str] | None=None) -> bool:
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
        return isinstance(ents, dict) and all((isinstance(k, str) and k.split('.')[0] in _SCENE_DOMAINS and (_hauflo_entity(hass, k, owned, owned_entries) or foreign.member_ok(hass, k)) for k in ents))
    if route == 'flow':
        handler = str(data.get('handler'))
        if handler == 'yandex_smart_home' and hass.config_entries.async_entries(handler):
            return False
        if data.get('entry_id') is not None:
            return handler == 'knx' and _owned_entry(owned_entries, str(data.get('entry_id')))
        if handler == 'knx' and hass.config_entries.async_entries('knx'):
            return False
        return handler in _FLOW_DOMAINS
    if route == 'flow_step':
        domain = _flow_handler(hass.config_entries.flow, parts[4])
        step = _flow_step(hass.config_entries.flow, parts[4])
        return domain in _FLOW_DOMAINS and (method != 'POST' or _flow_input_ok(hass, domain, data, memo, parts[4], step, owned, owned_entries))
    if route == 'options':
        handler = str(data.get('handler'))
        domain = _entry_domain(hass, handler)
        if domain in _OWNED_ONLY and (not _owned_entry(owned_entries, handler)):
            return False
        return domain in _OPTIONS_DOMAINS
    if route == 'options_step':
        handler = _flow_handler(hass.config_entries.options, parts[5])
        domain = _entry_domain(hass, handler) if handler is not None else None
        if domain in _OWNED_ONLY and (not _owned_entry(owned_entries, handler)):
            return False
        step = _flow_step(hass.config_entries.options, parts[5])
        return domain in _OPTIONS_DOMAINS and (method != 'POST' or _flow_input_ok(hass, domain, data, memo, parts[5], step, owned, owned_entries, options=True))
    if route == 'entry_delete':
        return _entry_domain(hass, parts[4]) in _DELETE_DOMAINS and _owned_entry(owned_entries, parts[4])
    if route == 'diagnostics':
        domain = _entry_domain(hass, parts[3])
        return domain in _DIAG_DOMAINS and (domain not in _OWNED_ONLY or _owned_entry(owned_entries, parts[3]))
    return False
_T = TUNING['link']
RECONNECT_HINT_WAIT = tuple(_T['reconnect_hint_wait_s'])
LINK_LOST_ISSUE_AFTER = _T['lost_issue_after_s']
BIG_FRAME = _T['big_frame_bytes']
CONNECT_TIMEOUT = _T['connect_timeout_s']
UNPAIR_TIMEOUT = _T['unpair_timeout_s']

def _next_wait(delay: float, hinted: bool, uniform: Callable[[float, float], float]=random.uniform) -> float:
    if hinted:
        return uniform(*RECONNECT_HINT_WAIT)
    return delay * uniform(0.5, 1.5)

class Agent:

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.server = entry.data[CONF_SERVER]
        self.secret = entry.data[CONF_SECRET]
        self.session = async_get_clientsession(hass, verify_ssl=True)
        self.local_session = async_get_clientsession(hass, verify_ssl=False)
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.channels: dict[int, aiohttp.ClientWebSocketResponse] = {}
        self.tasks: set[asyncio.Task] = set()
        self.refresh = None
        self.stopped = False
        self.engine: notify.NotifyEngine | None = None
        self.automations: Any = None
        self.presence: presence.Presence | None = None
        self.habits: habits_mod.Habits | None = None
        self.daylight: daylight.Daylight | None = None
        self.season: season.Season | None = None
        self.rules: rules.Rules | None = None
        self.combos: combos.Combos | None = None
        self.intercom: intercom_mod.Intercom | None = None
        self.platforms: list[str] = list(PLATFORMS)
        self.guest: guest.GuestAccess | None = None
        self.ownership: ownership.Ownership | None = None
        self.insights: insight.Insights | None = None
        self.watchdog: watchdog.Watchdog | None = None
        self.locks: locks_mod.Locks | None = None
        self.owned: set[str] = set()
        self.owned_entries: set[str] = set()
        self._flow_memo: dict[str, list[str]] = {}
        self._owned_store: HaufloStore = HaufloStore(hass, OWNED_STORE)
        self.dashboards = dashmerge.DashboardMerge(hass)
        self._pending: dict[tuple[int, Any], tuple[str, Any]] = {}
        self._note_subs: set[tuple[int, Any]] = set()
        self._unsub_signals = None
        self._status_soon: Callable[[], None] | None = None
        self.house_state: dict[str, Any] | None = None
        self._unshelve_at: Any = None
        self._unshelve_timer: Callable[[], None] | None = None
        self._rewatch: Callable[[], None] | None = None
        self.linked = False
        self._lost_timer: Callable[[], None] | None = None
        self._knx_heard: float | None = None
        self._knx_since = time.monotonic()
        self._knx_asked: float | None = None
        self._knx_asked_n = 0
        self._knx_read_last: float | None = None
        self._admins: list[str] = []
        self._unsub_knx = None
        try:
            from homeassistant.components.knx.const import SIGNAL_KNX_TELEGRAM
            from homeassistant.helpers.dispatcher import async_dispatcher_connect

            @callback
            def _heard(_telegram, telegram_dict) -> None:
                t = telegram_dict or {}
                direction = str(t.get('direction', '')).lower()
                now = time.monotonic()
                if direction.startswith('incoming'):
                    self._knx_heard = now
                    self._knx_asked = None
                    self._knx_asked_n = 0
                elif direction.startswith('outgoing') and str(t.get('telegramtype', '')) == 'GroupValueRead':
                    new_round = self._knx_read_last is None or now - self._knx_read_last > KNX_ROUND_GAP
                    self._knx_read_last = now
                    if self._knx_asked is not None:
                        self._knx_asked_n += 1
                    elif new_round:
                        self._knx_asked, self._knx_asked_n = (now, 1)
            self._unsub_knx = async_dispatcher_connect(hass, SIGNAL_KNX_TELEGRAM, _heard)
        except Exception as err:
            degrade('knx telegram signal', err)

    @property
    def local_base(self) -> str:
        api = self.hass.config.api
        scheme = 'https' if api and api.use_ssl else 'http'
        host = '127.0.0.1'
        if api and api.host and (api.host not in ('0.0.0.0', '::')):
            host = f'[{api.host}]' if ':' in api.host else api.host
        return f'{scheme}://{host}:{(api.port if api else 8123)}'

    def _spawn(self, coro) -> None:
        task = self.hass.async_create_background_task(coro, 'hauflo agent')
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    @property
    def token(self) -> str:
        return self.hass.auth.async_create_access_token(self.refresh) if self.refresh else ''

    async def send(self, frame: dict[str, Any]) -> None:
        if self.ws and (not self.ws.closed):
            await self.ws.send_str(json.dumps(frame))

    async def run(self) -> None:
        self.refresh = await _local_token(self.hass, self.entry)
        await self._ensure_label()
        delay = 5
        hosts = servers.same_region(self.server)
        missed = 0
        self._watch_link(False)
        while not self.stopped:
            hinted = False
            welcomed = False
            host = hosts[missed % len(hosts)]
            try:
                async with contextlib.AsyncExitStack() as stack:
                    async with asyncio.timeout(CONNECT_TIMEOUT):
                        ws = await stack.enter_async_context(self.session.ws_connect(servers.ws_url(host), heartbeat=30, max_msg_size=16 * 1024 * 1024))
                    self.ws = ws
                    await self.send({'t': 'hello', 'token': self.secret, 'version': VERSION, 'haVersion': HA_VERSION, 'instance': await self._instance(), 'features': FEATURES, 'signalsHash': signals.current_hash(self.hass)})
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            frame = await self._parse(msg.data)
                        except ValueError:
                            _LOGGER.warning('Hauflo: unreadable frame dropped')
                            continue
                        if not isinstance(frame, dict):
                            continue
                        if frame.get('t') == 'reconnect':
                            hinted = True
                            continue
                        if frame.get('t') == 'welcome':
                            self._set_linked(True)
                            delay = 5
                            welcomed = True
                            if host != hosts[0]:
                                servers.remember(host)
                                hosts = servers.same_region(self.server)
                            missed = 0
                            self.hass.data.pop(DATA_UNLINKED, None)
                            persistent_notification.async_dismiss(self.hass, 'hauflo_unlinked')
                            issues.clear_issue(self.hass, issues.UNLINKED)
                            issues.sync_version(self.hass, VERSION, frame.get('minVersion'), frame.get('latest'))
                            self._spawn(self._status_loop(ws))
                            if (engine := self.engine):
                                engine.on_change = self.status_now
                            continue
                        try:
                            await self._on_frame(frame)
                        except Exception:
                            _LOGGER.exception('Hauflo: frame %s failed', frame.get('t'))
                    if ws.close_code == 4003:
                        self.hass.data[DATA_UNLINKED] = True
                        issues.raise_issue(self.hass, issues.UNLINKED)
                        self.entry.async_start_reauth(self.hass)
                        delay = 600
                        await asyncio.sleep(delay)
                        continue
                    hinted = hinted or ws.close_code in (1001, 1012)
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                _LOGGER.debug('Hauflo link down: %s', err)
            finally:
                self.ws = None
                self._set_linked(False)
                for ch in list(self.channels):
                    await self._close_channel(ch, tell=False)
            if not self.stopped:
                if not welcomed:
                    missed += 1
                    if missed % len(hosts):
                        continue
                await asyncio.sleep(_next_wait(delay, hinted))
                delay = min(delay * 2, 60)

    async def _parse(self, data: str) -> Any:
        if len(data) > BIG_FRAME:
            return await self.hass.async_add_executor_job(json.loads, data)
        return json.loads(data)

    @callback
    def _watch_link(self, on: bool) -> None:
        if on:
            if self._lost_timer:
                self._lost_timer()
                self._lost_timer = None
            issues.clear_issue(self.hass, issues.LINK_LOST)
        elif self._lost_timer is None and (not self.stopped):
            self._lost_timer = async_call_later(self.hass, LINK_LOST_ISSUE_AFTER, self._link_lost)

    @callback
    def _link_lost(self, _now: Any) -> None:
        self._lost_timer = None
        if not self.linked and (not self.stopped) and (not self.hass.data.get(DATA_UNLINKED)):
            issues.raise_issue(self.hass, issues.LINK_LOST, server=str(urlparse(self.server).hostname), hours=str(LINK_LOST_ISSUE_AFTER // 3600))

    async def async_load(self) -> None:
        data = await self._owned_store.async_load()
        self.owned = set(data.get('unique_ids', [])) if isinstance(data, dict) else set()
        self.owned_entries = set(data.get('entries', [])) if isinstance(data, dict) else set()
        await self.dashboards.async_load()

    def _knx_uid(self, entity_id: Any) -> str | None:
        entry = er.async_get(self.hass).async_get(entity_id) if isinstance(entity_id, str) else None
        return entry.unique_id if entry is not None and entry.platform == 'knx' else None

    def _track_entry(self, text: str) -> None:
        try:
            res = json.loads(text)
        except ValueError:
            return
        entry_id = (res.get('result') or {}).get('entry_id') if isinstance(res, dict) and res.get('type') == 'create_entry' else None
        if isinstance(entry_id, str) and _entry_domain(self.hass, entry_id) in _TRACKED_DOMAINS:
            self.owned_entries.add(entry_id)
            self._owned_store.async_delay_save(self._owned_data, 0)
            self._owned_changed()

    def _owned_changed(self) -> None:
        if (own := self.ownership) is not None:
            own.schedule()

    def _owned_data(self) -> dict[str, Any]:
        return {'unique_ids': sorted(self.owned), 'entries': sorted(self.owned_entries)}

    def _track(self, ch: int, data: str) -> str:
        try:
            msg = json.loads(data)
        except ValueError:
            return data
        changed = scrubbed = False
        for r in msg if isinstance(msg, list) else [msg]:
            if isinstance(r, dict) and r.get('type') == 'event' and ((ch, r.get('id')) in self._note_subs):
                r['event'] = _scrub_notes(r.get('event'), self.hass, self.owned_entries)
                scrubbed = True
                continue
            if not isinstance(r, dict) or r.get('type') != 'result' or (key := (ch, r.get('id'))) not in self._pending:
                continue
            kind, ref = self._pending.pop(key)
            res = r.get('result')
            if kind == 'config_entries/get:all':
                r['result'] = _scrub_entries(res)
                scrubbed = True
                continue
            if kind == 'config_entries/get':
                r['result'] = [e for e in res if isinstance(e, dict) and e.get('entry_id') in self.owned_entries] if isinstance(res, list) else []
                scrubbed = True
                continue
            if kind in _SCRUBBED:
                r['result'] = _scrub_states(res, self.hass, self._state_visible, self._rules_hidden()) if kind == 'get_states' else _scrub_config(res) if kind == 'get_config' else _scrub_cloud(res) if kind == 'cloud/status' else _scrub_devices(res, self.hass) if kind == 'config/device_registry/list' else _scrub_panels(res) if kind == 'get_panels' else _scrub_entities(res, self.hass) if kind.startswith('config/entity_registry/') else _scrub_monitor(res)
                scrubbed = True
                continue
            if kind in _CREATES:
                k = _CREATES[kind]
                if r.get('success') and isinstance(res, dict) and isinstance(res.get(f'{k}_id'), str):
                    self.owned_entries.add(f"{k}:{res[f'{k}_id']}")
                    changed = True
                continue
            if kind == 'config/entity_registry/update':
                reg_entry = res.get('entity_entry') if r.get('success') and isinstance(res, dict) else None
                if isinstance(reg_entry, dict) and (own := self.ownership) is not None:
                    old_id, named = ref
                    own.remember_name(reg_entry.get('entity_id'), reg_entry.get('name'), old_id, named)
                continue
            if not r.get('success') or (kind != 'knx/delete_entity' and (not (isinstance(res, dict) and res.get('success')))):
                continue
            if kind == 'knx/update_entity':
                continue
            uid = ref if kind == 'knx/delete_entity' else self._knx_uid(res.get('entity_id'))
            if uid:
                (self.owned.discard if kind == 'knx/delete_entity' else self.owned.add)(uid)
                changed = True
        if changed:
            self._owned_store.async_delay_save(self._owned_data, 0)
            self._owned_changed()
        return json.dumps(msg) if scrubbed else data

    async def _on_frame(self, f: dict[str, Any]) -> None:
        t = f.get('t')
        if t == 'ws_open':
            self._spawn(self._open_channel(int(f['ch'])))
        elif t == 'ws_send':
            await self._channel_send(int(f['ch']), f['data'])
        elif t == 'ws_close':
            await self._close_channel(int(f['ch']), tell=False)
        elif t == 'http':
            self._spawn(self._http(f))
        elif t == 'guest':
            self._spawn(self._guest(f))
        elif t == 'service':
            await service.async_save(self.hass, f.get('info'))

    async def cloud_ice(self) -> Any:
        it = TUNING['intercom']

        async def ask(host: str) -> Any:
            async with self.session.get(f"{host}{it['cloud_ice_path']}", headers={'Authorization': f'Bearer {self.secret}'}, timeout=aiohttp.ClientTimeout(total=it['cloud_ice_timeout_s'], sock_connect=CONNECT_TIMEOUT)) as res:
                if res.status != 200:
                    raise ValueError(f'status {res.status}')
                return await res.json()
        return (await servers.first_answer(servers.same_region(self.server), ask))[1]

    async def _guest(self, f: dict[str, Any]) -> None:
        status, body = (503, {'code': 'not_ready'})
        if (g := self.guest) is not None:
            try:
                await g.async_load()
                status, body = await g.handle(f.get('body'))
            except Exception:
                _LOGGER.exception('Hauflo guest: request failed')
                status, body = (500, {'code': 'failed'})
        await self.send({'t': 'guest_res', 'id': f.get('id'), 'status': status, 'body': json.dumps(body)})

    async def _open_channel(self, ch: int) -> None:
        if len(self.channels) >= _MAX_CHANNELS or ch in self.channels:
            await self.send({'t': 'ws_closed', 'ch': ch})
            return
        try:
            local = await self.local_session.ws_connect(f'{self.local_base}/api/websocket', max_msg_size=0)
        except aiohttp.ClientError:
            await self.send({'t': 'ws_closed', 'ch': ch})
            return
        self.channels[ch] = local
        try:
            async for msg in local:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = self._track(ch, msg.data) if self._pending or self._note_subs else msg.data
                    await self.send({'t': 'ws_msg', 'ch': ch, 'data': data})
        finally:
            for key in [k for k in self._pending if k[0] == ch]:
                del self._pending[key]
            self._note_subs = {k for k in self._note_subs if k[0] != ch}
            if self.channels.pop(ch, None) is not None:
                await self.send({'t': 'ws_closed', 'ch': ch})

    async def _instance(self) -> str | None:
        try:
            from homeassistant.helpers import instance_id
            return await instance_id.async_get(self.hass)
        except Exception:
            return None

    def _rules_hidden(self) -> set[str]:
        try:
            return self.rules.cloud_hidden_entities() if self.rules is not None else set()
        except Exception:
            _LOGGER.debug('rules: hidden entities', exc_info=True)
            return set()

    def _state_visible(self, eid: str) -> bool:
        own = _own_entity(self.hass, eid, self.owned_entries) and (_platform(self.hass, eid) != 'knx' or _owned_knx(self.hass, eid, self.owned))
        return own or notify.labelled(self.hass, eid)

    @property
    def locked(self) -> bool:
        return self.entry.options.get(CONF_ALLOW_CHANGES, True) is not True

    def allowed_ranges(self) -> set[str]:
        allowed = {str(r) for r in self.entry.options.get(CONF_RANGES) or []}
        if CONF_RANGES not in self.entry.options:
            try:
                for configs in (_knx_store(self.hass) or {}).values():
                    for uid, config in configs.items():
                        if uid in self.owned:
                            allowed |= {r for r in (_ga_range(k) for k in _knx_writes(config.get('knx') if isinstance(config, dict) else None)) if r}
            except Exception:
                pass
        return allowed

    def _outside_ranges(self, msg: dict[str, Any]) -> set[str]:
        if msg.get('type') not in ('knx/create_entity', 'knx/update_entity'):
            return set()
        data = msg.get('data')
        writes = _knx_writes(data.get('knx') if isinstance(data, dict) else None) - {'?'}
        allowed = self.allowed_ranges()
        return {t for k in writes if (t := _ga_text(k)) and (not _range_allows(allowed, k))}

    def _ask_ranges(self, items: set[str]) -> None:
        ranges.ask(self.hass, items)

    def _foreign_write(self, msg: dict[str, Any]) -> bool:
        t = msg.get('type')
        if t == 'config/device_registry/update':
            own = _own_device(self.hass, msg.get('device_id'))
        elif t == 'config/entity_registry/update':
            own = _own_entity(self.hass, msg.get('entity_id'), self.owned_entries)
        else:
            return False
        return not own and foreign.wizard_refusal(self.hass, msg) is None

    def _foreign_reason(self, msg: dict[str, Any]) -> str | None:
        t = msg.get('type')
        if t == 'config/entity_registry/update':
            eid = msg.get('entity_id')
            if isinstance(eid, str) and eid.startswith(_NEVER_EXPOSE):
                return 'protected_domain'
            if _own_entity(self.hass, eid, self.owned_entries):
                return None
        elif t == 'config/device_registry/update':
            if _own_device(self.hass, msg.get('device_id')):
                return None
        else:
            return None
        return foreign.wizard_refusal(self.hass, msg) or 'not_allowed'

    def _dashboard_why(self, config: Any) -> str | None:
        return _lovelace_refusal(self.hass, config, self.owned, self.owned_entries)

    def _dashboard_prune(self, config: Any) -> tuple[Any, list[dict[str, Any]]]:
        return _lovelace_prune(self.hass, config, self.owned, self.owned_entries)

    async def _channel_send(self, ch: int, data: str) -> None:
        local = self.channels.get(ch)
        if local is None:
            return
        try:
            msg = json.loads(data)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if msg.get('type') != 'auth':
            mid = msg.get('id')
            if type(mid) is not int or (ch, mid) in self._pending or (ch, mid) in self._note_subs:
                _LOGGER.warning('Hauflo: a tunnel message with a reused or bad id was refused')
                await self.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps({'id': mid if type(mid) is int else None, 'type': 'result', 'success': False, 'error': {'code': 'id_reuse', 'message': 'Hauflo agent: message id reused or not a number'}})})
                return
        if str(msg.get('type')).startswith(_PROTECT_BEFORE):
            _protect_refresh(self.hass)
        if msg.get('type') == 'auth':
            data = json.dumps({'type': 'auth', 'access_token': self.token})
        elif msg.get('type') == dashmerge.PLAN or (msg.get('type') == 'lovelace/config' and self.dashboards.manages(msg.get('url_path')) and (not dashmerge.is_copy(msg.get('url_path')))):
            await dashmerge.handle(self, ch, msg, self._dashboard_why, backup.fresh, LOCKED_CODE, self._dashboard_prune)
            return
        elif self.locked and _ws_locked(msg):
            _LOGGER.info('Hauflo: «%s» refused — changes by Hauflo are switched off in HA', msg.get('type'))
            await self.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps({'id': msg.get('id'), 'type': 'result', 'success': False, 'error': {'code': LOCKED_CODE, 'message': 'Hauflo agent: changes by Hauflo are switched off in Home Assistant (Hauflo → Configure)'}})})
            return
        elif (outside := self._outside_ranges(msg)):
            self._ask_ranges(outside)
            await self.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps({'id': msg.get('id'), 'type': 'result', 'success': False, 'error': {'code': 'address_not_allowed', 'message': f"Hauflo agent: group addresses {', '.join(sorted(outside))} are not allowed in HA (Hauflo → Configure)"}})})
            return
        elif msg.get('type') == dashmerge.SAVE or (msg.get('type') == 'lovelace/config/save' and str(msg.get('url_path') or '').startswith('hauflo-')):
            await dashmerge.handle(self, ch, msg, self._dashboard_why, backup.fresh, LOCKED_CODE, self._dashboard_prune)
            return
        elif not _ws_allowed(msg, self.hass, self.owned, self.owned_entries):
            why = self._foreign_reason(msg)
            _LOGGER.warning("Hauflo: «%s» isn't allowed through the tunnel%s", msg.get('type'), f' ({why})' if why else '')
            await self.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps({'id': msg.get('id'), 'type': 'result', 'success': False, 'error': {'code': 'not_allowed', 'message': f'Hauflo agent: not allowed ({why})' if why else 'Hauflo agent: not allowed'}})})
            return
        if _ws_changes(msg) and (not backup.fresh()):
            await self.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps({'id': msg.get('id'), 'type': 'result', 'success': False, 'error': {'code': 'backup_required', 'message': 'Hauflo agent: a backup first'}})})
            return
        if self._foreign_write(msg):
            sent, created = foreign.wizard_prepare(self.hass, msg)
            if created:
                self.owned_entries.update((f'label:{lid}' for lid in created))
                self._owned_store.async_delay_save(self._owned_data, 0)
            if sent is None:
                await self.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps({'id': msg.get('id'), 'type': 'result', 'success': False, 'error': {'code': 'not_allowed', 'message': 'Hauflo agent: not allowed (label_conflict)'}})})
                return
            msg, data = (sent, json.dumps(sent))
        if msg.get('type') == 'config_entries/get' and 'domain' not in msg:
            self._pending[ch, msg.get('id')] = ('config_entries/get:all', None)
        if msg.get('type') in _SCRUBBED:
            self._pending[ch, msg.get('id')] = (str(msg['type']), None)
        if msg.get('type') == 'config_entries/get' and msg.get('domain') in _ENTRY_OWNED_ONLY:
            self._pending[ch, msg.get('id')] = ('config_entries/get', None)
        if msg.get('type') == 'persistent_notification/subscribe':
            self._note_subs.add((ch, msg.get('id')))
        if msg.get('type') in _CREATES:
            self._pending[ch, msg.get('id')] = (str(msg['type']), None)
        if (t := msg.get('type')) in ('knx/create_entity', 'knx/update_entity', 'knx/delete_entity'):
            ref = self._knx_uid(msg.get('entity_id')) if t == 'knx/delete_entity' else msg.get('entity_id')
            self._pending[ch, msg.get('id')] = (t, ref)
        if msg.get('type') == 'config/entity_registry/update':
            self._pending[ch, msg.get('id')] = ('config/entity_registry/update', (msg.get('entity_id'), 'name' in msg))
        await local.send_str(data)

    async def _close_channel(self, ch: int, tell: bool) -> None:
        local = self.channels.pop(ch, None)
        if local is not None:
            await local.close()
        if tell:
            await self.send({'t': 'ws_closed', 'ch': ch})

    async def _http(self, f: dict[str, Any]) -> None:
        path = str(f.get('path', ''))
        if self.locked and _http_locked(str(f.get('method', 'GET')).upper(), path, f.get('body')):
            _LOGGER.info('Hauflo: %s %s refused — changes by Hauflo are switched off in HA', f.get('method'), path.split('?')[0])
            await self.send({'t': 'http_res', 'id': f.get('id'), 'status': 423, 'body': json.dumps({'code': LOCKED_CODE})})
            return
        if not _http_allowed(self.hass, str(f.get('method', 'GET')).upper(), path, f.get('body'), self.owned_entries, self._flow_memo, self.owned):
            _LOGGER.warning("Hauflo: %s %s isn't allowed through the tunnel", f.get('method'), path.split('?')[0])
            await self.send({'t': 'http_res', 'id': f.get('id'), 'status': 403, 'body': 'Hauflo agent: not allowed'})
            return
        if _http_changes(str(f.get('method', 'GET')).upper(), path) and (not backup.fresh()):
            await self.send({'t': 'http_res', 'id': f.get('id'), 'status': 428, 'body': json.dumps({'code': 'backup_required'})})
            return
        try:
            async with self.local_session.request(f.get('method', 'GET'), f'{self.local_base}{path}', data=f.get('body'), headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'}, timeout=aiohttp.ClientTimeout(total=60)) as res:
                text = await res.text()
                if res.status == 200 and path.startswith('/api/config/config_entries/flow/'):
                    self._track_entry(text)
                    text = _alice_creds_to_ha(self.hass, text)
                if res.status == 200 and path.split('?')[0].startswith('/api/diagnostics/'):
                    text = _scrub_diagnostics(text)
                if res.status == 200 and path.split('?')[0] == '/api/config':
                    with contextlib.suppress(ValueError):
                        text = json.dumps(_scrub_config(json.loads(text)))
                await self.send({'t': 'http_res', 'id': f['id'], 'status': res.status, 'body': text})
        except (aiohttp.ClientError, TimeoutError) as err:
            await self.send({'t': 'http_res', 'id': f['id'], 'status': 502, 'body': str(err)})

    async def _ensure_label(self) -> None:
        reg = lr.async_get(self.hass)
        if not any((label.name.casefold() in MONITOR_LABELS for label in reg.async_list_labels())):
            ru = str(self.hass.config.language or '').startswith('ru')
            reg.async_create(MONITOR_LABEL if ru else MONITOR_LABEL_EN, icon='mdi:bell-alert', color='red', description=ownership.MONITOR_TEXT['ru' if ru else 'en'])

    def _watch_ctx(self) -> dict[str, Any]:
        label = next((l for l in lr.async_get(self.hass).async_list_labels() if l.name.casefold() in MONITOR_LABELS), None)
        reg = er.async_get(self.hass)
        sig = signals.store(self.hass)
        return {'reg': reg, 'label': label.label_id if label is not None else None, 'chosen': foreign.notify_on(self.hass) | (sig.watch_on() if sig else set()) | (self.intercom.watched() if self.intercom is not None else set()), 'off': foreign.notify_off(self.hass) | (sig.watch_off() if sig else set()), 'pol': signals.policy(self.hass)}

    def _kept(self, i: str, ctx: dict[str, Any]) -> bool:
        if i in ctx['off']:
            return False
        pol = ctx['pol']
        if pol and pol['scopes'] and ((ent := ctx['reg'].async_get(i)) is not None):
            st = self.hass.states.get(i)
            return bool(signals.resolve(i, platform=ent.platform, device_class=st.attributes.get('device_class') if st else ent.device_class or ent.original_device_class, name=st.name if st else None, policy=pol, device_kind=signals.device_kind_of(self.hass, i))['watch'])
        return True

    def watches(self, eid: str) -> bool:
        ctx = self._watch_ctx()
        e = ctx['reg'].async_get(eid)
        if e is None:
            return False
        labelled_ = ctx['label'] is not None and ctx['label'] in e.labels
        by_class = not e.disabled_by and (signals.watch_by_class(eid, e.device_class or e.original_device_class, e.platform) or notify.watched_press(self.hass.states.get(eid), e.platform))
        return (labelled_ or by_class or eid in ctx['chosen']) and self._kept(eid, ctx)

    def _monitored_ids(self) -> list[str]:
        ctx = self._watch_ctx()
        reg = ctx['reg']
        ids = [e.entity_id for e in er.async_entries_for_label(reg, ctx['label'])] if ctx['label'] is not None else []
        by_class = sorted((e.entity_id for e in reg.entities.values() if not e.disabled_by and (signals.watch_by_class(e.entity_id, e.device_class or e.original_device_class, e.platform) or (e.entity_id.startswith('event.') and notify.watched_press(self.hass.states.get(e.entity_id), e.platform)))))
        chosen = sorted((i for i in ctx['chosen'] if reg.async_get(i) is not None))
        return [i for i in dict.fromkeys([*ids, *by_class, *chosen]) if self._kept(i, ctx)]

    def house(self, summary: bool=False) -> dict[str, Any]:
        v = self.house_now()
        out = {**v, '_signals': self.signal_verdicts()}
        if summary:
            out['_summary'] = self.alarms_now(v['_faults'])
        return out

    @callback
    def house_now(self, mon: list[dict[str, Any]] | None=None) -> dict[str, Any]:
        faults, shelved, held = self._faults(self.monitored() if mon is None else mon)
        live = [f for f in faults if not f['offline']]
        level = 'alarm' if any((f['house'] == 'alarm' for f in live)) else 'fault' if live else 'offline' if faults else 'ok'
        v = {'_house': level, '_faults': faults, '_shelved': shelved, '_held': held}
        prev = self.house_state
        since = prev['since'] if prev and prev['level'] == level else dt_util.utcnow().isoformat()
        now = {'level': level, 'since': since, 'faults': faults, 'undetermined': len(shelved), 'held': held}
        if now != prev:
            self.house_state = now
            async_dispatcher_send(self.hass, f'{DOMAIN}_house')
        self._unshelve_soon()
        return v

    @callback
    def _unshelve_soon(self) -> None:
        at = signals.next_unshelve(signals.policy(self.hass))
        if at == self._unshelve_at:
            return
        if self._unshelve_timer:
            self._unshelve_timer()
        self._unshelve_at = at
        self._unshelve_timer = async_track_point_in_utc_time(self.hass, self._unshelved, at) if at is not None else None

    @callback
    def _unshelved(self, _now: Any) -> None:
        self._unshelve_timer = None
        self._unshelve_at = None
        self.status_now()

    def alarms_now(self, faults: list[dict[str, Any]], cap: int | None=None) -> dict[str, Any]:
        engine = self.engine
        where = getattr(engine, '_where', None)
        items = getattr(engine, 'alarm_items', None)
        rich = []
        for f in faults:
            s = self.hass.states.get(f['entity'])
            rich.append({**f, 'area': where(s) if where and s else '', 'since': s.last_changed.isoformat() if s else None})
        acks = getattr(engine, 'house_acks', None)
        if acks and (keep := getattr(engine, 'house_acks_keep', None)):
            keep({f['entity'] for f in faults})
        return alarms_now.summary(items() if items else [], rich, cap, acks)

    def _faults(self, mon: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
        faults: list[dict[str, Any]] = []
        shelved: list[str] = []
        held: list[dict[str, Any]] = []
        for m in mon:
            if not m['problem']:
                continue
            if m['undetermined']:
                shelved.append(m['entityId'])
            elif m['house'] == 'none':
                continue
            elif m.get('shelvedUntil'):
                held.append({'entity': m['entityId'], 'until': m['shelvedUntil']})
            else:
                faults.append({'entity': m['entityId'], 'name': m['name'], 'house': m['house'], 'severity': m['severity'], 'offline': m['state'] == 'unavailable'})
        return (faults, shelved, held)

    def signal_verdicts(self) -> dict[str, dict[str, Any]]:
        reg = er.async_get(self.hass)
        pol = signals.policy(self.hass)
        own = (pol or {}).get('entities') or {}
        monitored = set(self._monitored_ids())
        sig = signals.store(self.hass)
        off = foreign.notify_off(self.hass) | (sig.watch_off() if sig else set())
        ruled = {e for r in (self.engine.config['rules'] if self.engine is not None else []) if r.get('enabled', True) for e in notify.live_sources(r)}
        out: dict[str, dict[str, Any]] = {}
        kinds = signals.device_kinds(self.hass)
        for s in sorted(self.hass.states.async_all(signals.SIGNAL_DOMAINS), key=lambda x: (x.entity_id not in own, x.entity_id)):
            if len(out) >= signals.CARDS_MAX:
                break
            eid = s.entity_id
            ent = reg.async_get(eid)
            platform = str(ent.platform) if ent is not None and ent.platform else None
            if platform in signals.WATCH_SKIP:
                continue
            ev = signals.evaluate(s, platform=platform, policy=pol, device_kind=kinds.get(ent.device_id) if ent is not None and ent.device_id else None)
            by_canon = signals.watch_by_class(eid, s.attributes.get('device_class'), platform) or ev['base'] in signals.CLASSLESS_WATCHED
            if not (eid in monitored or eid in off or by_canon or (eid in own)) or (ev['undetermined'] and eid not in off and (eid not in own)):
                continue
            alarm = ev['alarm']
            cond = alarm if alarm and 'none' not in alarm else None
            watched = eid not in off and ev['watch'] and (eid in monitored or by_canon) and (ev['house'] != 'none') and (not ev['shelved'])
            v: dict[str, Any] = {'house': ev['house'], 'alarm': alarm} if watched and cond else {'house': 'none', **({'alarm': cond} if cond else {})}
            who = sig.own_of(eid) if sig else {'meaning': None, 'ha': False}
            v['src'] = 'ha' if who['ha'] else ev['source']
            if who['meaning']:
                v['meaning'] = who['meaning']
            if eid in ruled:
                v['rule'] = True
            if ev['shelved']:
                v['until'] = ev['shelvedUntil']
            out[eid] = v
        return out

    def _knx(self) -> dict[str, Any]:
        entries = self.hass.config_entries.async_entries('knx')
        loaded = any((e.state is ConfigEntryState.LOADED for e in entries))
        connected = None
        if loaded:
            try:
                connected = knx_data(self.hass).xknx.connection_manager.connected.is_set()
            except Exception as err:
                degrade('knx connection state', err)
        now = time.monotonic()
        silent = int((now - (self._knx_heard or self._knx_since)) // 60)
        unanswered = int((now - self._knx_asked) // 60) if self._knx_asked is not None and self._knx_asked_n >= KNX_ROUND_MIN_READS else None
        return {'loaded': loaded, 'connected': connected, 'state': entries[0].state.value if entries else 'missing', 'silentMin': silent, 'unansweredMin': unanswered, 'unansweredReads': self._knx_asked_n, 'heard': self._knx_heard is not None}

    def _knx_conn(self) -> dict[str, Any] | None:
        entries = self.hass.config_entries.async_entries('knx')
        if not entries:
            return None
        return knx_conn(entries[0].data)

    def _ha_url(self) -> str | None:
        return ha_url(self.hass.config.external_url or self.hass.config.internal_url)

    async def _refresh_admins(self) -> None:
        with contextlib.suppress(Exception):
            users = await self.hass.auth.async_get_users()
            self._admins = sorted({str(u.name)[:80] for u in users if u.is_admin and u.is_active and (not u.system_generated) and u.name})[:20]

    def monitored(self) -> list[dict[str, Any]]:
        reg = er.async_get(self.hass)
        pol = signals.policy(self.hass)
        kinds = signals.device_kinds(self.hass)
        out = []
        for eid in self._monitored_ids():
            s = self.hass.states.get(eid)
            if s is None:
                continue
            dc = s.attributes.get('device_class')
            ent = reg.async_get(eid)
            platform = str(ent.platform) if ent is not None and ent.platform else None
            kind = kinds.get(ent.device_id) if ent is not None and ent.device_id else None
            ev = signals.evaluate(s, platform=platform, policy=pol, device_kind=kind)
            item: dict[str, Any] = {'entityId': eid, 'name': s.name, 'state': s.state, 'problem': ev['problem']}
            if kind:
                item['deviceKind'] = kind
            if dc:
                item['deviceClass'] = str(dc)
            item['platform'] = platform
            item.update({'severity': ev['defaultSeverity'], 'house': ev['house'], 'undetermined': ev['undetermined'], 'source': ev['source'], 'lifeSafety': ev['lifeSafety']})
            item['policy'] = {'alarm': ev['alarm'], 'holdS': ev['holdS'], 'from': ev['from']}
            if ev['shelved']:
                item['shelvedUntil'] = ev['shelvedUntil']
            if s.domain == 'event' and (ring := _recent_event(self.engine.ring_said(s) if self.engine is not None else s.state, EVENT_RECENT)):
                item['ring'] = ring
                item['eventClass'] = kind if (kind := notify.event_kind(s)) != 'event' else str(dc) if dc else ''
            out.append(item)
        return out

    def status(self) -> dict[str, Any]:
        issues.sync_degraded(self.hass)
        reg = er.async_get(self.hass)
        knx_ids = [e.entity_id for e in reg.entities.values() if e.platform == 'knx' and (not e.disabled_by)]
        unavailable = [i for i in knx_ids if (s := self.hass.states.get(i)) is not None and s.state == 'unavailable']
        mon = self.monitored()
        monitored = [{k: v for k, v in m.items() if v is not None and v is not False or k == 'problem'} for m in mon]
        return {'haVersion': HA_VERSION, 'haStarted': HA_UP, 'agentVersion': VERSION, 'features': FEATURES, 'signalsHash': signals.current_hash(self.hass), 'knx': self._knx(), 'unavailable': {'count': len(unavailable), 'sample': unavailable[:10]}, 'monitored': monitored, 'alarms': engine.active_list() if (engine := self.engine) else [], 'alarmsNow': self.alarms_now(self.house_now(mon)['_faults'], alarms_now.ITEMS_MAX), 'alarmsReady': bool(engine and engine.ready), 'insights': ins.items() if (ins := self.insights) else [], 'insightChoices': ins.choices() if (ins := self.insights) else {}, 'integrations': wd.items() if (wd := self.watchdog) else {}, 'signalChoices': sig.choices() if (sig := signals.store(self.hass)) else {}, 'locked': self.locked, 'timeZone': str(self.hass.config.time_zone or ''), 'knxConn': self._knx_conn(), 'haUrl': self._ha_url(), 'haAdmins': list(self._admins), 'installs': install.installer(self.hass).items()}

    @callback
    def _set_linked(self, on: bool) -> None:
        if self.linked != on:
            self.linked = on
            _LOGGER.info('Hauflo: link to %s %s', urlparse(self.server).hostname, 'restored' if on else 'lost — reconnecting')
            async_dispatcher_send(self.hass, f'{DOMAIN}_link')
            self._watch_link(on)

    @callback
    def status_now(self) -> None:
        _analysis_panel(self.hass)
        if self.ws is not None and (not self.ws.closed):
            self._spawn(self.send({'t': 'status', 'status': self.status()}))
        else:
            self.house_now()

    async def _status_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while self.ws is ws and (not ws.closed):
            _protect_refresh(self.hass)
            await self._refresh_admins()
            await self.send({'t': 'status', 'status': self.status()})
            self._watch_signals()
            await asyncio.sleep(STATUS_EVERY)

    @callback
    def _watch_signals(self) -> None:
        if self._unsub_signals:
            self._unsub_signals()
        ids = self._monitored_ids()
        self._unsub_signals = async_track_state_change_event(self.hass, ids, self._signal_changed) if ids else None

    @callback
    def _signal_changed(self, _event: Event) -> None:
        if self._status_soon:
            self._status_soon()
        self._status_soon = async_call_later(self.hass, 2, self._status_tick)

    @callback
    def _status_tick(self, _now: Any) -> None:
        self._status_soon = None
        self.status_now()

    @callback
    def rewatch_soon(self) -> None:
        if self._rewatch:
            self._rewatch()
        self._rewatch = async_call_later(self.hass, 2, self._rewatched)

    @callback
    def _rewatched(self, _now: Any) -> None:
        self._rewatch = None
        self._watch_signals()
        self.status_now()

    async def stop(self) -> None:
        if self._unsub_knx:
            self._unsub_knx()
            self._unsub_knx = None
        self.stopped = True
        if self._unsub_signals:
            self._unsub_signals()
            self._unsub_signals = None
        if self._status_soon:
            self._status_soon()
            self._status_soon = None
        if self._unshelve_timer:
            self._unshelve_timer()
            self._unshelve_timer = None
        if self._rewatch:
            self._rewatch()
            self._rewatch = None
        if self._lost_timer:
            self._lost_timer()
            self._lost_timer = None
        if self.ws:
            await self.ws.close()
        for task in list(self.tasks):
            task.cancel()
PLATFORMS = ['binary_sensor', 'sensor', 'switch']
INTERCOM_PLATFORMS = ['button', 'camera', 'event']
type HaufloConfigEntry = ConfigEntry[Agent]

async def async_setup_entry(hass: HomeAssistant, entry: HaufloConfigEntry) -> bool:
    try:
        await house.async_init(hass)
    except Exception as err:
        degrade('house settings', err)
    agent = Agent(hass, entry)
    await agent.async_load()
    entry.runtime_data = agent
    try:
        await _load_stock_area_names(hass)
    except Exception as err:
        degrade('stock rooms', err)
    await protect.async_setup(hass, lambda eid: _protect_marks_of(hass, eid), _ga_key, lambda: _protect_refresh(hass))
    await foreign.async_setup(hass)
    sig = await signals.async_setup(hass)
    agent.combos = combos.Combos(hass, entry.entry_id, lambda: site_ru(hass.config.language, getattr(agent.engine, 'config', None)))
    _protect_refresh(hass)
    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, callback(lambda _e: _protect_refresh(hass))))
    owns = lambda eid: _own_entity(hass, eid, agent.owned_entries) and (_platform(hass, eid) != 'knx' or _owned_knx(hass, eid, agent.owned))
    agent.season = season.Season(hass, lambda: agent.engine, lambda: agent.rules, owns)
    await agent.season.async_load()
    agent.daylight = daylight.Daylight(hass)
    try:
        await retired.async_retire_comfort(hass, entry.entry_id, agent.season.async_seed)
    except Exception as err:
        degrade('comfort floor removal', err)

    async def _tell(title: str, message: str, user_id: str | None) -> None:
        if agent.engine is not None:
            await agent.engine.async_tell(title, message, user_id)

    async def _ask(title: str, message: str, actions: list, tag: str) -> None:
        if agent.engine is not None:
            await agent.engine.async_tell(title, message, None, actions=actions, tag=tag)

    async def _season_set(mode: str, why: str | None) -> str | None:
        if why == 'suggest':
            await agent.season.apply(mode, None)
            return None
        return await agent.season.auto_check(why or 'rule')
    agent.rules = rules.Rules(hass, owns=lambda eid: _hauflo_entity(hass, eid, agent.owned, agent.owned_entries), own_raw=owns, season=agent.season.rule_season, tell=_tell, season_now=agent.season.current, season_set=_season_set, ask=_ask, notify_of=lambda: agent.engine)
    agent.rules.entry_id = entry.entry_id
    agent.rules.cloud_reads = lambda eid, attr: _cloud_reads(hass, eid, attr, agent._state_visible)
    try:
        await agent.rules.async_load()
    except Exception as err:
        degrade('value rules', err)
    agent.ownership = ownership.Ownership(hass, lambda: agent.owned, lambda: agent.owned_entries, lambda: agent.locked, lambda: set(protected_ids(hass)))
    try:
        await agent.ownership.async_start()
    except Exception as err:
        degrade('hauflo label', err)
    agent.locks = locks_mod.Locks(hass, lambda: agent.engine)
    try:
        agent.intercom = intercom_mod.Intercom(hass, entry.entry_id, dict(entry.options.get(CONF_INTERCOM) or {}))
        agent.intercom.locks = agent.locks
        agent.intercom.cloud_fetch = agent.cloud_ice
        if agent.intercom.doors:
            agent.intercom.hub_device_id = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}).id
    except Exception as err:
        degrade('intercom', err)
    agent.platforms = PLATFORMS + (INTERCOM_PLATFORMS if agent.intercom is not None and agent.intercom.doors else [])
    await hass.config_entries.async_forward_entry_setups(entry, agent.platforms)
    if agent.intercom is not None:
        try:
            await agent.intercom.start()
        except Exception as err:
            degrade('intercom', err)
    try:
        await agent.season.async_start()
    except Exception as err:
        degrade('season', err)
    try:
        await agent.daylight.async_start()
    except Exception as err:
        degrade('daylight', err)
    try:
        await agent.rules.async_start()
    except Exception as err:
        degrade('value rules', err)
    try:
        hass.data[DATA_CARDS_LOCAL] = await keep.async_on_start(hass, ASSET_TAG)
    except Exception as err:
        degrade('lovelace resources', err)
    for feature, setup in (('voice sentences', voice.async_setup), ('theme', theme.async_setup), ('menu for new people', sidebar.async_setup)):
        try:
            await setup(hass)
        except Exception as err:
            degrade(feature, err)
    entry.async_create_background_task(hass, agent.run(), 'hauflo agent link')
    agent.presence = presence.Presence(hass, lambda eid: _hauflo_entity(hass, eid, agent.owned, agent.owned_entries))
    try:
        await agent.presence.async_start()
    except Exception as err:
        degrade('presence simulation', err)
    agent.habits = habits_mod.Habits(hass, lambda eid: (a := _automations(hass)) is not None and a._pickable(eid), lambda: [agent.rules, agent.presence], lambda eid: (a := _automations(hass)) is not None and a._area(eid) or None)
    try:
        await agent.habits.async_start()
    except Exception as err:
        degrade('habit suggestions', err)
    agent.guest = guest.GuestAccess(hass, lambda eid: _hauflo_entity(hass, eid, agent.owned, agent.owned_entries), lambda: f"{agent.server.rstrip('/')}/g/{guest.route_of(agent.secret)}")
    try:
        await agent.guest.async_load()
    except Exception as err:
        degrade('guest access', err)
    agent.guest.dashboards = agent.dashboards
    agent.dashboards.access_of = agent.guest.access
    try:
        await agent.guest.async_start()
    except Exception as err:
        degrade('guest pages', err)
    engine = notify.NotifyEngine(hass)
    engine.on_change = agent.status_now
    engine.owns = owns
    engine.locked = lambda: agent.locked
    engine.watches = agent.watches
    engine.quiet_now = lambda: agent.rules is not None and quiet.inside(agent.rules.quiet, dt_util.utcnow())
    engine.lock_owns = lambda eid: agent.locks is not None and agent.locks.owns_event(eid)
    await engine.async_start()
    agent.engine = engine
    sig_flags = foreign.flags(hass)
    if sig_flags is not None:
        sig_flags.on_locks = lambda _was, _now: _locks_changed(hass, agent)
        sig_flags.lock_info = lambda: _lock_info(agent)
    try:
        await agent.locks.async_start()
    except Exception as err:
        degrade('locks', err)
    agent.insights = insight.Insights(hass, owns, agent.season.current)
    agent.insights.on_change = agent.status_now
    agent.insights.watched = engine.watched
    agent.insights.finding_of = sig.finding
    agent.insights.addresses = lambda: knx_addresses(hass)
    agent.insights.on = lambda: signals.observing(hass)
    try:
        await agent.insights.async_start()
    except Exception as err:
        agent.insights = None
        degrade('insights', err)
    agent.watchdog = watchdog.Watchdog(hass)
    agent.watchdog.on_change = agent.status_now
    agent.watchdog.off = lambda: signals.watchdog_off(hass)
    try:
        await agent.watchdog.async_start()
    except Exception as err:
        agent.watchdog = None
        degrade('integration watchdog', err)
    entry.async_on_unload(sig.listen(lambda: _policy_changed(hass, agent)))
    _analysis_panel(hass)

    @callback
    def _house_start(_hass: HomeAssistant) -> None:
        agent._watch_signals()
        agent.house_now()
    entry.async_on_unload(async_at_started(hass, _house_start))
    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, callback(lambda _e: agent.rewatch_soon() if hass.is_running else None)))
    await hass.async_add_executor_job(stock.table)
    try:
        await install.installer(hass).async_load()
    except Exception as err:
        degrade('install', err)
    try:
        await self_update.updater(hass).async_load()
    except Exception as err:
        degrade('self_update', err)
    if not hass.data.get(DATA_NOTIFY_VIEWS):
        hass.data[DATA_NOTIFY_VIEWS] = True
        hass.http.register_view(notify.NotifyView(lambda: _engine(hass)))
        hass.http.register_view(notify.NotifyTestView(lambda: _engine(hass)))
        hass.http.register_view(backup.BackupView())
        hass.http.register_view(install.InstallView())
        hass.http.register_view(install.RestartView())
        hass.http.register_view(self_update.SelfUpdateView())
        hass.http.register_view(notify.AlarmsView(lambda: _engine(hass), lambda: _house(hass)))
        hass.http.register_view(signals.SignalsView())
        hass.http.register_view(signals.SignalChooseView())
        hass.http.register_view(insight.InsightsView(lambda: _insights(hass), lambda: _habits_summary(hass), lambda on: _habits_set(hass, on)))
        hass.http.register_view(OwnedView())
        hass.http.register_view(ownership.OwnershipView(_ownership))
        hass.http.register_view(RangesView())
        hass.http.register_view(rules.RulesView(lambda: _rules(hass)))
        hass.http.register_view(sidebar.SidebarView())
        hass.http.register_view(season.SeasonView(lambda: _season(hass)))
        hass.http.register_view(season.SeasonConfigView(lambda: _season(hass)))
        hass.http.register_view(titles.TitlesView())
        hass.http.register_view(block_order.OrderView())
        room_pictures.register(hass)
        hass.http.register_view(automations.AutomationsView(lambda: _automations(hass)))
        hass.http.register_view(scenes.ScenesView(lambda: _scenes(hass)))
        hass.http.register_view(presence.PresenceView(lambda: _presence(hass)))
        hass.http.register_view(guest.GuestView(lambda: _guest(hass), lambda: _engine(hass)))
        hass.http.register_view(house.HouseView())
        hass.http.register_view(stock.StockView())
        hass.http.register_view(protect.ProtectView(_protect_mark, _protect_unmark))
        hass.http.register_view(foreign.ForeignView())
        hass.http.register_view(exposure.ExposureView(_exposure))
        hass.http.register_view(intercom_setup.IntercomView(lambda: _intercom(hass)))
        hass.http.register_view(noeffect.PairsView(lambda: _pair_effect(hass)))
        hass.http.register_view(history_mod.HistoryView(lambda eid, attr: _history_reads(hass, eid, attr)))

    async def _stop(_event: Event) -> None:
        if agent.presence is not None:
            agent.presence.async_stop()
        if agent.intercom is not None:
            await agent.intercom.stop()
        if agent.locks is not None:
            agent.locks.stop()
        await agent.stop()
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop))
    entry.async_on_unload(entry.add_update_listener(_entry_changed))
    timers.async_setup(hass)
    independent.async_setup_service(hass, lambda: _engine(hass))
    intercom_mod.async_setup_services(hass, lambda: _intercom(hass))
    locks_mod.async_setup_services(hass, lambda: _locks(hass))
    issues.sync_degraded(hass)
    return True

def _locks_changed(hass: HomeAssistant, agent: Agent) -> None:
    if agent.locks is not None:
        hass.async_create_task(agent.locks.apply())

async def _lock_info(agent: Agent) -> dict[str, Any] | None:
    if agent.locks is None or not agent.locks.devices:
        return None
    doors = [{'id': d.cfg.id, 'name': d.cfg.name} for d in agent.intercom.doors.values()] if agent.intercom is not None else []
    return await agent.locks.wizard(doors)

def _locks(hass: HomeAssistant) -> locks_mod.Locks | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.locks
    return None

def relinked(agent: Agent, data: Mapping[str, Any]) -> bool:
    return data.get(CONF_SECRET) != agent.secret or data.get(CONF_SERVER) != agent.server

async def _entry_changed(hass: HomeAssistant, entry: HaufloConfigEntry) -> None:
    agent = entry.runtime_data
    doors_now = agent.intercom.options if agent.intercom is not None else {}
    if relinked(agent, entry.data) or dict(entry.options.get(CONF_INTERCOM) or {}) != doors_now:
        hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    entry.runtime_data.status_now()
    if (own := entry.runtime_data.ownership) is not None:
        own.schedule(1)

class OwnedView(HomeAssistantView):
    url = '/api/hauflo/owned'
    name = 'api:hauflo:owned'
    requires_auth = True

    async def get(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        hass = request.app[KEY_HASS]
        owned: set[str] = set()
        entries: set[str] = set()
        areas: set[str] = set()
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            owned |= entry.runtime_data.owned
            entries |= {e for e in entry.runtime_data.owned_entries if ':' not in e}
            areas |= {e.split(':', 1)[1] for e in entry.runtime_data.owned_entries if e.startswith('area:')}
        stock: list[str] = []
        try:
            from homeassistant.helpers import area_registry as ar
            await _load_stock_area_names(hass)
            stock = sorted((a.id for a in ar.async_get(hass).async_list_areas() if a.id not in areas and _stock_area(hass, a.id)))
        except Exception as err:
            degrade('stock rooms', err)
        return self.json({'unique_ids': sorted(owned), 'entries': sorted(entries), 'areas': sorted(areas), 'stock_areas': stock})

class RangesView(HomeAssistantView):
    url = '/api/hauflo/ranges'
    name = 'api:hauflo:ranges'
    requires_auth = True

    async def get(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        hass = request.app[KEY_HASS]
        allowed: set[str] = set()
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            allowed |= entry.runtime_data.allowed_ranges()
        return self.json({'allowed': sorted(allowed), **ranges.state(hass)})

    async def post(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        hass = request.app[KEY_HASS]
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('bad body', 400)
        wanted = body.get('addresses') if isinstance(body, dict) else None
        if not isinstance(wanted, list) or len(wanted) > ranges.MAX_ITEMS or (not all((isinstance(x, str) for x in wanted))):
            return self.json_message('bad addresses', 400)
        loaded = hass.config_entries.async_loaded_entries(DOMAIN)
        if not loaded:
            return self.json_message('not linked', 409)
        allowed = loaded[0].runtime_data.allowed_ranges()
        project = str(body.get('project') or '') or (loaded[0].title or '').removeprefix('Hauflo — ')
        return self.json({'allowed': sorted(allowed), **ranges.request(hass, allowed, wanted, project)})

def _exposure(hass: HomeAssistant) -> exposure.Exposure | None:
    if not hass.config_entries.async_loaded_entries(DOMAIN):
        return None
    return exposure.Exposure(hass, lambda: set(protected_ids(hass)), _NEVER_DOMAINS)

def _ownership(hass: HomeAssistant) -> ownership.Ownership | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.ownership
    return None

def _house(hass: HomeAssistant) -> dict[str, Any] | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.house(summary=True)
    return None

def _insights(hass: HomeAssistant) -> insight.Insights | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if entry.runtime_data.insights is not None:
            return entry.runtime_data.insights
    return None

@callback
def _policy_changed(hass: HomeAssistant, agent: Any) -> None:
    if (engine := agent.engine) is not None and engine.policy_changed() and engine.native:
        hass.async_create_task(independent.async_sync(hass, engine), 'hauflo policy resync')
    if (ins := agent.insights) is not None:
        ins.switch()
        ins.findings_changed()
    agent._watch_signals()
    _analysis_panel(hass)
    agent.status_now()

def _analysis_panel(hass: HomeAssistant) -> None:
    try:
        engine = _engine(hass)
        analysis.async_register(hass, insight.diag_visible(hass) != insight.DIAG_ALL, config=getattr(engine, 'config', None))
    except Exception as err:
        degrade('analysis panel', err)

def _engine(hass: HomeAssistant) -> notify.NotifyEngine | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if entry.runtime_data.engine is not None:
            return entry.runtime_data.engine
    return None

def rule_states(hass: HomeAssistant, admin: bool) -> dict[str, str]:
    found: dict[str, list[str]] = {}
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        agent = entry.runtime_data
        if (eng := agent.rules) is not None:
            for rid, rule in eng.rules.items():
                if not (admin or rule['client'].get('visible', True)):
                    continue
                st = 'off' if not rule['enabled'] else 'fired' if eng.st(rid)['active'] else 'ok'
                for eid in rules.inputs_of(rule):
                    found.setdefault(eid, []).append(st)
        if (alarms := agent.engine) is not None:
            for rule in alarms.config['rules']:
                for eid in rule['sources']:
                    open_ = alarms.active.get(f"{rule['id']}|{eid}")
                    found.setdefault(eid, []).append('off' if not rule['enabled'] else 'fired' if open_ and (not open_.get('nodata')) else 'ok')
    return {eid: 'fired' if 'fired' in v else 'off' if all((x == 'off' for x in v)) else 'ok' for eid, v in sorted(found.items())}

def _pair_effect(hass: HomeAssistant) -> noeffect.PairEffect | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if (ins := entry.runtime_data.insights):
            return next((d for d in ins.detectors if isinstance(d, noeffect.PairEffect)), None)
    return None

def _season(hass: HomeAssistant) -> season.Season | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.season
    return None

def _habits(hass: HomeAssistant) -> habits_mod.Habits | None:
    return next((e.runtime_data.habits for e in hass.config_entries.async_loaded_entries(DOMAIN) if getattr(e.runtime_data, 'habits', None) is not None), None)

async def _habits_set(hass: HomeAssistant, on: bool) -> bool:
    if (h := _habits(hass)) is None:
        return False
    await h.set_enabled(on)
    return True

async def _habits_summary(hass: HomeAssistant) -> dict[str, Any]:
    auto = _automations(hass)
    h = _habits(hass)
    if auto is None or h is None:
        return {}
    try:
        found = len(h.suggestions(auto, await auto._load())) if h.enabled else 0
    except Exception:
        _LOGGER.debug('Hauflo insights: habits not counted', exc_info=True)
        return {'habits': {'on': h.enabled}}
    return {'habits': {'on': h.enabled, 'found': found}}

def _automations(hass: HomeAssistant) -> automations.Automations | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        agent = entry.runtime_data
        if getattr(agent, 'automations', None) is None:
            agent.automations = automations.Automations(hass, lambda eid: _hauflo_entity(hass, eid, agent.owned, agent.owned_entries), lambda eid: _protected(hass, eid), lambda: agent.habits)
        return agent.automations
    return None

def _scenes(hass: HomeAssistant) -> scenes.FamilyScenes | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        agent = entry.runtime_data
        if getattr(agent, 'family_scenes', None) is None:
            agent.family_scenes = scenes.FamilyScenes(hass, lambda eid: _hauflo_entity(hass, eid, agent.owned, agent.owned_entries) or foreign.member_ok(hass, eid), knx=scenes.KnxScenes(numbers=lambda: _knx_scenes_learnable(hass, agent.owned, agent.allowed_ranges()), target=lambda eid: _knx_scene_target(hass, agent.owned, agent.allowed_ranges(), eid), locked=lambda: agent.locked), daylight=lambda: agent.daylight)
        return agent.family_scenes
    return None

def _presence(hass: HomeAssistant) -> presence.Presence | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.presence
    return None

def _intercom(hass: HomeAssistant) -> intercom_mod.Intercom | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.intercom
    return None

def _guest(hass: HomeAssistant) -> guest.GuestAccess | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.guest
    return None

def _rules(hass: HomeAssistant) -> rules.Rules | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.rules
    return None

async def async_unload_entry(hass: HomeAssistant, entry: HaufloConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, getattr(entry.runtime_data, 'platforms', PLATFORMS)):
        return False
    if (ic := entry.runtime_data.intercom) is not None:
        entry.runtime_data.intercom = None
        await ic.stop()
    if (lk := entry.runtime_data.locks) is not None:
        entry.runtime_data.locks = None
        lk.stop()
        await lk.async_save()
    if (ff := foreign.flags(hass)) is not None:
        ff.plans.clear()
    if (dl := entry.runtime_data.daylight) is not None:
        dl.async_stop()
    if (sn := entry.runtime_data.season) is not None:
        sn.async_stop()
        await sn.store.async_save(sn.data())
    if (cb := entry.runtime_data.combos) is not None:
        cb.stop()
    if (vr := entry.runtime_data.rules) is not None:
        vr.async_stop()
        await vr.store.async_save(vr.data())
    if (sim := entry.runtime_data.presence) is not None:
        entry.runtime_data.presence = None
        sim.async_stop()
        if not hass.is_stopping:
            try:
                async with asyncio.timeout(15):
                    await sim.async_lights_out()
            except Exception:
                _LOGGER.warning('Hauflo presence: lights not switched off on unload', exc_info=True)
    if (hb := entry.runtime_data.habits) is not None:
        entry.runtime_data.habits = None
        await hb.async_stop()
    await entry.runtime_data.stop()
    if (guests := entry.runtime_data.guest) is not None:
        entry.runtime_data.guest = None
        await guests.async_stop()
    if (own := entry.runtime_data.ownership) is not None:
        own.async_stop()
        await own.async_flush()
    voice.async_unload(hass)
    sidebar.async_unload(hass)
    analysis.async_unregister(hass)
    theme.async_unload(hass)
    if (wd := entry.runtime_data.watchdog):
        entry.runtime_data.watchdog = None
        wd.async_stop()
        await wd.async_flush()
    if (ins := entry.runtime_data.insights):
        entry.runtime_data.insights = None
        ins.async_stop()
        await ins.store.async_save(ins.data())
    if (engine := entry.runtime_data.engine):
        entry.runtime_data.engine = None
        engine.async_stop()
    return True

async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await voice.async_remove(hass)
    issues.clear_all(hass)
    try:
        await keep.async_keep_dashboard(hass, ASSET_TAG)
    except Exception:
        _LOGGER.exception('Hauflo: dashboard cards not kept')
    with contextlib.suppress(Exception):
        await notify.NotifyEngine(hass).async_remove()
    try:
        gone = guest.GuestAccess(hass, lambda _eid: False, lambda: '')
        gone.dashboards = dashmerge.DashboardMerge(hass)
        await gone.dashboards.async_load()
        gone.dashboards.access_of = gone.access
        await gone.async_remove()
    except Exception:
        _LOGGER.exception('Hauflo: guests with a login not removed')
    try:
        await independent.async_removed(hass)
    except Exception:
        _LOGGER.exception('Hauflo: kept alarms not told')
    with contextlib.suppress(Exception):
        await HaufloStore(hass, retired.COMFORT_STORE).async_remove()
    with contextlib.suppress(Exception):
        await HaufloStore(hass, season.STORE_KEY).async_remove()
    with contextlib.suppress(Exception):
        await HaufloStore(hass, foreign.STORE_KEY).async_remove()
    hass.data.pop(foreign.DATA, None)
    with contextlib.suppress(Exception):
        await HaufloStore(hass, rules.STORE_KEY).async_remove()
    with contextlib.suppress(Exception):
        await HaufloStore(hass, insight.STORE_KEY).async_remove()
    with contextlib.suppress(Exception):
        await HaufloStore(hass, watchdog.STORE_KEY).async_remove()
    with contextlib.suppress(Exception):
        await HaufloStore(hass, habits_mod.STORE_KEY).async_remove()
    try:
        await HaufloStore(hass, protect.STORE_KEY).async_remove()
    except Exception:
        _LOGGER.exception('Hauflo: protected covers not removed')
    hass.data.pop(protect.DATA, None)
    try:
        await HaufloStore(hass, guest.STORE_KEY).async_remove()
    except Exception:
        _LOGGER.exception('Hauflo: guest access log not removed')
    with contextlib.suppress(Exception):
        await service.async_remove(hass)
    with contextlib.suppress(Exception):
        await HaufloStore(hass, ownership.STORE_KEY).async_remove()
    try:
        await sidebar.async_remove(hass)
    except Exception:
        _LOGGER.exception('Hauflo: sidebar panels not restored')
    with contextlib.suppress(Exception):
        session = async_get_clientsession(hass)

        async def unpair(host: str) -> None:
            await session.post(f'{host}/api/agent/unpair', headers={'Authorization': f'Bearer {entry.data[CONF_SECRET]}'}, json={}, timeout=aiohttp.ClientTimeout(total=UNPAIR_TIMEOUT, sock_connect=CONNECT_TIMEOUT))
        await servers.first_answer(servers.same_region(entry.data[CONF_SERVER]), unpair)
    if (uid := entry.data.get(CONF_USER_ID)) and (user := (await hass.auth.async_get_user(uid))):
        await hass.auth.async_remove_user(user)
