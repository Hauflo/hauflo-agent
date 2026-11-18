"""Dashboard merge constants."""
from __future__ import annotations
import re
ID = 'hauflo_id'
PLAN = 'hauflo/dashboard/plan'
SAVE = 'hauflo/dashboard/save'
STORE_KEY = 'hauflo.dashboard_base'
META_KEY = 'hauflo.dashboard_meta'
MAX_COPIES = 3
MAX_TAKEN_COPIES = 10
COPY_RE = re.compile('-copy-\\d')
LOCAL = 'hauflo-local'
ENTITY_RE = re.compile('^[a-z_]+\\.[a-z0-9_]+$')
TITLE_KEYS = ('title', 'heading', 'name', 'text')
ROOT = '\x00root'
ADMINS = 'hauflo:admins'
USER_MARKER = 'hauflo:user:'
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
LAYOUT = 'custom:hauflo-layout'
SUB_KEYS = ('show_modes', 'show_presets', 'show_fan', 'show_swing', 'show_humidity', 'hvac_modes', 'preset_modes', 'fan_modes', 'swing_modes')
USER_KEYS = ('items', 'auto', 'order', *SUB_KEYS)
CARD_PREFIX = 'custom:hauflo-'
NONE = object()
CONTENT_KEYS = ('entity', 'entities', 'area')
DEAD_LINK = 'dead_link'
KEY = re.compile('^[A-Za-z0-9_:-]{1,40}$')
ACCESS_ALL = 'hauflo:all'
ACCESS_ALL_TRUE = 'hauflo:all-true'
ACCESS_MORE = 'hauflo:guests'
_ACCESS_TAGS = (ACCESS_ALL, ACCESS_ALL_TRUE, ACCESS_MORE)
