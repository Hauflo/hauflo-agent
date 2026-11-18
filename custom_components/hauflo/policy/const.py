"""The tunnel policy's constants."""
from __future__ import annotations
import functools
import ipaddress
import re
from ..const import DOMAIN
from ..tuning import tuning
from ..tunnel import tunnel_cfg
_tw = functools.cache(lambda: tunnel_cfg()['ws'])
_th = functools.cache(lambda: tunnel_cfg()['http'])
_tf = functools.cache(lambda: tunnel_cfg()['flows'])
ws_exact = functools.cache(lambda: set(_tw()['commands']))
services = functools.cache(lambda: {(d, s) for d, s in _tw()['services']})
ws_reads = functools.cache(lambda: set(_tw()['reads']))
read_services = functools.cache(lambda: {(d, s) for d, s in _tw()['read_services']})
CREATES = {'config/area_registry/create': 'area', 'config/floor_registry/create': 'floor', 'config/label_registry/create': 'label'}
BACKUP_FIRST = {'/api/hauflo/sidebar', '/api/hauflo/install', '/api/hauflo/install/restart', '/api/hauflo/self_update'}
locked_http_ok = functools.cache(lambda: {(p, m) for p, m in _th()['locked_ok']})
locked_read_ops = functools.cache(lambda: {(r['path'], r['method']): set(r['ops']) for r in _th()['locked_read_ops']})
locked_never_keys = functools.cache(lambda: {(r['path'], r['method']): set(r['keys']) for r in _th()['locked_never_keys']})
flow_domains = functools.cache(lambda: set(_tf()['setup']))
options_domains = functools.cache(lambda: set(_tf()['options']))
owned_only = functools.cache(lambda: set(_tf()['owned_only']))
delete_domains = functools.cache(lambda: set(_tf()['delete']))
tracked_domains = functools.cache(lambda: set(_tf()['tracked']))
diag_domains = functools.cache(lambda: set(_tf()['diagnostics']))
entry_platforms = functools.cache(lambda: set(_tf()['entities_by_entry']))
entry_domains = functools.cache(lambda: set(_tw()['config_entries']))
entry_owned_only = functools.cache(lambda: set(_tw()['config_entries_owned_only']))
manifests = functools.cache(lambda: set(_tw()['manifests']))
routes = functools.cache(lambda: [(r['id'], r['path'].split('/'), set(r['methods'])) for r in _th()['routes']])
_hauflo_views = functools.cache(lambda: {(r['path'], m) for r in _th()['routes'] if r['id'] == 'hauflo' for m in r['methods']})
max_channels = functools.cache(lambda: tuning()['link']['channels_max'])
OWN_PLATFORMS = {'knx', 'group', DOMAIN}
OWN_DEVICE_DOMAINS = {'knx', 'group'}
NEVER_EXPOSE = ('lock.', 'alarm_control_panel.', 'valve.')
NEVER_DOMAINS = {'lock', 'alarm_control_panel', 'valve'}
SCENE_DOMAINS = {'light', 'cover', 'switch', 'climate', 'fan', 'media_player'}
PROTECTED_COVERS = {'garage', 'gate', 'door'}
_gate_words = functools.cache(lambda: tuning()['ownership']['gate_words'])
gate_name = functools.cache(lambda: re.compile('|'.join((w.replace('\\p{L}', '[^\\W\\d_]') for w in _gate_words())), re.IGNORECASE))
UNKNOWN_DOMAINS = ('switch.', 'cover.', 'button.', 'script.')
WRAPPER_PLATFORMS = {'switch_as_x'}
HELPER_DOMAINS = ('switch_as_x', 'template')
ENTITY_REF = re.compile('^[a-z0-9_]+\\.[a-z0-9_]+$')
SERVICE_KEYS = frozenset({'action', 'service'})
PROTECT_DATA = f'{DOMAIN}_protected'
PROTECT_BEFORE = ('knx/', 'config/entity_registry/', 'config/device_registry/')
ASSISTANTS = ('conversation', 'cloud.alexa', 'cloud.google_assistant')
_PRIVATE_DOMAINS = {'person', 'device_tracker', 'camera', 'zone', 'calendar', 'media_player', 'image', 'image_processing', 'todo'}
_PRIVATE_KEYS = {'access_token', 'entity_picture', 'latitude', 'longitude', 'gps_accuracy', 'altitude', 'course', 'speed', 'vertical_accuracy', 'user_id', 'device_trackers', 'source', 'token', 'location', 'message', 'description'}
KEEP_ON_PRIVATE = {'friendly_name', 'device_class', 'icon', 'supported_features'}
CONFIG_KEEP = {'version', 'components', 'time_zone', 'language', 'unit_system', 'state', 'recovery_mode', 'safe_mode', 'currency', 'external_url'}
STATE_ATTRS = {'friendly_name', 'device_class', 'state_class', 'unit_of_measurement', 'icon', 'supported_features', 'hvac_modes', 'hvac_action', 'min_temp', 'max_temp', 'target_temp_step', 'temperature', 'current_temperature', 'target_temp_high', 'target_temp_low', 'preset_modes', 'preset_mode', 'fan_modes', 'fan_mode', 'swing_modes', 'swing_mode', 'current_position', 'current_tilt_position', 'brightness', 'color_mode', 'supported_color_modes', 'color_temp_kelvin', 'min_color_temp_kelvin', 'max_color_temp_kelvin', 'percentage', 'percentage_step', 'options', 'min', 'max', 'step', 'mode', 'humidity', 'current_humidity', 'entity_id', 'restored'}
PRIVATE_STATE = {'person', 'device_tracker', 'zone', 'sun', 'weather', 'camera', 'calendar', 'media_player', 'image', 'image_processing', 'todo', 'notify', 'tts', 'stt', 'conversation', 'alarm_control_panel', 'lock'}
PRIVATE_PLATFORMS = {'mobile_app', 'sun'}
STATE_TIMES = ('context', 'last_changed', 'last_updated', 'last_reported')
SCRUBBED = ('get_states', 'get_config', 'cloud/status', 'config/device_registry/list', 'knx/group_monitor_info', 'get_panels', 'config/entity_registry/list', 'config/entity_registry/get', 'config/entity_registry/get_entries')
PHONE_PLATFORMS = {'mobile_app'}
PRESENCE_CLASSES = {'motion', 'occupancy', 'presence', 'door', 'window', 'opening', 'garage_door', 'lock'}
WEATHER_READS = {'temperature', 'humidity'}
ALLOWED_ITEM = re.compile('(\\d{1,2})/(\\d)(?:/(\\d{1,3})(?:-(\\d{1,3}))?)?')
SCENE_EID = re.compile('scene\\.[a-z0-9_]{1,128}')
foreign_name_max = functools.cache(lambda: tuning()['ownership']['foreign_name_max'])
STOCK_AREAS = f'{DOMAIN}_stock_area_names'
BENCH_NET = ipaddress.ip_network('198.18.0.0/15')
KNX_SECURE_KEYS = ('user_id', 'user_password', 'device_authentication', 'backbone_key', 'sync_latency_tolerance', 'knxkeys', 'secure_')
KNX_CONN_KEYS = {'connection_type': 'type', 'host': 'host', 'port': 'port', 'local_ip': 'localIp', 'route_back': 'routeBack', 'multicast_group': 'multicastGroup', 'multicast_port': 'multicastPort', 'individual_address': 'individualAddress', 'tunnel_endpoint_ia': 'tunnelEndpoint'}
HK_FILTER_KEYS = {'include_domains', 'include_entities', 'exclude_domains', 'exclude_entities', 'domains', 'entities', 'mode', 'include_exclude_mode', 'filter'}
tpl = functools.cache(lambda: _tf()['template'])
tpl_tokens = functools.cache(lambda: [(re.compile(t['re']), t.get('entity')) for t in tpl()['tokens']])
tpl_menu = functools.cache(lambda: set(tpl()['menu']))
tpl_keys = functools.cache(lambda: {k: set(v) for k, v in tpl()['keys'].items()})
tpl_option_keys = functools.cache(lambda: {k: set(v) for k, v in tpl()['options_keys'].items()})
tpl_classes = functools.cache(lambda: set(tpl()['device_classes']))
sax = functools.cache(lambda: _tf()['switch_as_x'])
sax_keys = functools.cache(lambda: {k: set(v) for k, v in sax()['keys'].items()})
sax_option_keys = functools.cache(lambda: {k: set(v) for k, v in sax()['options_keys'].items()})
LOVELACE_TYPES = {'sections', 'panel', 'masonry', 'grid', 'tile', 'heading', 'horizontal-stack', 'vertical-stack', 'button', 'picture-glance', 'cover-open-close', 'cover-position', 'target-temperature', 'light-brightness', 'climate-preset-modes', 'climate-hvac-modes', 'climate-fan-modes', 'numeric-input', 'thermostat', 'custom:hauflo-scenes', 'custom:hauflo-alert', 'custom:hauflo-room', 'custom:hauflo-lights', 'custom:hauflo-hero', 'custom:hauflo-climate', 'custom:hauflo-status', 'custom:hauflo-devices', 'custom:hauflo-sliders', 'custom:hauflo-covers', 'custom:hauflo-acs', 'custom:hauflo-automations', 'custom:hauflo-title', 'custom:hauflo-area', 'custom:hauflo-layout', 'custom:hauflo-section', 'custom:hauflo-insights', 'custom:hauflo-camera'}
READ_ONLY_DOMAINS = {'sensor', 'binary_sensor', 'weather', 'sun', 'person', 'zone', 'device_tracker', 'calendar', 'image', 'camera', 'event'}
ACTIONS = {'more-info', 'toggle', 'none', 'navigate', 'perform-action', 'call-service'}
CARD_SERVICES = {'homeassistant.toggle', 'homeassistant.turn_on', 'homeassistant.turn_off', 'light.turn_on', 'light.turn_off', 'light.toggle', 'switch.turn_on', 'switch.turn_off', 'switch.toggle', 'cover.open_cover', 'cover.close_cover', 'cover.stop_cover', 'cover.set_cover_position', 'cover.toggle', 'scene.turn_on', 'fan.turn_on', 'fan.turn_off', 'fan.set_percentage', 'climate.set_temperature', 'climate.set_hvac_mode', 'climate.set_preset_mode', 'climate.set_fan_mode', 'climate.set_swing_mode', 'climate.turn_off', 'knx.send'}
ENTITY_RE = re.compile('[a-z_]+\\.[a-z0-9_]+')
HAUFLO_ID_RE = re.compile('^[^\\x00-\\x1f\\x7f]{1,256}$')
COPY_RE = re.compile('-copy-\\d')
NAV_RE = re.compile('^/(hauflo-[a-z0-9_-]+(/[a-z0-9_-]*)?|config/scene/edit/hauflo_[a-z0-9_]+)$')
TARGET_KEYS = {'entity_id', 'device_id', 'area_id', 'floor_id', 'label_id'}
TEXT_KEYS = {'icon', 'name', 'title', 'label', 'text', 'theme', 'url_path', 'path', 'color'}
RUNS = ('perform-action', 'call-service')
IMAGE_KEYS = {'image', 'picture', 'camera_image', 'background', 'icon_image', 'entity_picture'}
IMAGE_RE = re.compile('(?:/local/|/api/image/serve/|/api/image_proxy/|/api/camera_proxy/|/hauflo_static/)[A-Za-z0-9_./%?=&+-]*')
DATA_IMAGE_RE = re.compile('data:image/(?:png|jpeg|gif|webp)(?:;[a-z0-9=_-]+)*;base64,[A-Za-z0-9+/=]*')
DATA_SVG_RE = re.compile('data:image/svg\\+xml;base64,([A-Za-z0-9+/=]*)')
SVG_TAGS = {'svg', 'defs', 'g', 'linearGradient', 'radialGradient', 'stop', 'rect', 'path', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'title'}
SVG_LOCAL_URL = re.compile('url\\(#[A-Za-z0-9_-]+\\)')
ENCODED_PATH = re.compile('%(?:2e|2f|5c|25)', re.IGNORECASE)
WORD_RE = re.compile('[^\\W_]+', re.UNICODE)
ROW_KEYS = {'entity', 'name', 'secondary', 'icon', 'icon_type', 'hidden', 'block', 'tap_action', 'hold_action', 'double_tap_action', 'view', 'min', 'max', 'step'}
row_views = functools.cache(lambda: set(tuning()['cards']['regulator']['views']))
row_num_abs = functools.cache(lambda: tuning()['lovelace']['row_num_abs'])
ROW_BLOCKS = {'control', 'readings', 'diagnostics'}
row_text_max = functools.cache(lambda: tuning()['lovelace']['row_text_max'])
rows_max = functools.cache(lambda: tuning()['lovelace']['rows_max'])
ORDER_RE = re.compile('^(?:[a-z_]+\\.[a-z0-9_]+|[a-z][a-z0-9_]{0,39})$')
SUB_SHOW = {'auto', 'show', 'hide'}
sub_modes_max = functools.cache(lambda: tuning()['lovelace']['sub_modes_max'])
reg_ranges = functools.cache(lambda: tuning()['cards']['regulator']['ranges'])
reg_range_attrs = functools.cache(lambda: set(tuning()['cards']['regulator']['range_attrs']))
REG_FEATURE = {'fan': 1, 'cover': 4, 'valve': 4, 'media_player': 4}
why_max = functools.cache(lambda: tuning()['lovelace']['why_max'])
NO_PRUNE_LISTS = {'conditions', 'visibility', 'entity_id', 'area_id', 'device_id', 'floor_id', 'label_id', 'payload', 'views'}
prune_max = functools.cache(lambda: tuning()['lovelace']['prune_max'])
EID_IN = re.compile('\\b([a-z_]+\\.[a-z0-9_]+)\\b')
CONTACT = re.compile('[^\\s@()<>\\[\\]\\"\',;]*@[^\\s()<>\\[\\]\\"\',;]*|\\+?\\d[\\d\\s()-]{8,}\\d')
ALICE_NOTE = 'hauflo_alisa'
