"""Rule names, limits and patterns: the store, the kinds of «when», the house's own actions, the API's ops."""
from __future__ import annotations
import re
from homeassistant.const import STATE_CLOSED, STATE_OFF
from ..const import DOMAIN
STORE_KEY = f'{DOMAIN}.rules'
BRIGHTNESS_MAX = 255
ID_BYTES = 5
TODO = '_todo'
LANGS = ('ru', 'en')
AT = 'at'
FEATURES = ('schedule', 'actions_by_domain', 'repeat', 'state_is')
IS = 'is'
WHENS = ('below', 'above', 'outside', 'between', 'on', 'off', AT, IS)
IS_KINDS = ('option', 'percent', 'setpoint', 'number')
IS_NUMBERS = ('percent', 'setpoint', 'number')
READ_ATTR = {'power': None, 'closed': None, 'state': None, 'position': 'current_position', 'volume': 'volume_level'}
DAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
RANGES = ('below', 'above', 'outside', 'between')
VALUE_DOMAINS = {'sensor', 'input_number', 'number', 'climate', 'weather'}
BINARY_DOMAINS = {'binary_sensor', 'input_boolean', 'switch', 'fan', 'light'}
OFF_STATES = (STATE_OFF, STATE_CLOSED)
ENTITY_RE = re.compile('^[a-z_]+\\.[a-z0-9_]+$')
HHMM = re.compile('^([01]\\d|2[0-3]):[0-5]\\d$')
TIME = re.compile('^([01]\\d|2[0-3]):[0-5]\\d(:[0-5]\\d)?$')
DATE = re.compile('^\\d{4}-\\d\\d-\\d\\d$')
USER_RE = re.compile('^[0-9a-f]{32}$')
HOUSE_ACTS = {'season': 'season', 'notify': ''}
NUMERIC = ('percent', 'setpoint', 'number', 'seconds')
SEASON_MODES = ('heat', 'cool')
SEASON_TARGET = f'{DOMAIN}.season'
SUGGEST_ACTION = 'HAUFLO_RULE'
LATER = 'later'
UNDO_WHYS = ('changed', 'deleted', 'switched_off')
PRIORITY = {'comfort': 0, 'safety': 1}
GA_RE = re.compile('^\\d{1,5}(/\\d{1,4}){0,2}$')
KNX_WRITE = 'GroupValueWrite'
OPS = ('save', 'delete', 'enable', 'tune', 'preview', 'derived_save', 'derived_delete', 'quiet', 'bus_reads')
ADMIN_OPS = ('quiet', 'bus_reads')
