"""Shared guest access limits, errors and helpers."""
from __future__ import annotations
from collections import deque
import datetime as dt
import functools
import hashlib
from http import HTTPStatus
import logging
import re
import time
from typing import Any
from homeassistant.auth.permissions.const import POLICY_CONTROL
from .. import derived
from ..guard import contained
from ..tuning import tuning
_LOGGER = logging.getLogger(__name__)
STORE_KEY = 'hauflo.guest'
DOMAINS = ('light', 'cover', 'climate')
_t = functools.cache(lambda: tuning()['guest'])
max_hours = functools.cache(lambda: _t()['max_hours'])
max_active = functools.cache(lambda: _t()['max_active'])
max_devices = functools.cache(lambda: _t()['max_devices'])
default_devices = functools.cache(lambda: _t()['default_devices'])
max_rooms = functools.cache(lambda: _t()['max_rooms'])
KEEP_ENDED = dt.timedelta(days=7)
log_days = functools.cache(lambda: _t()['log_days'])
log_max = functools.cache(lambda: _t()['log_max'])
temp_band = functools.cache(lambda: tuple(_t()['temp_band']))
rate_act = functools.cache(lambda: _t()['rate_act'])
rate_state = functools.cache(lambda: _t()['rate_state'])
rate_act_device = functools.cache(lambda: _t()['rate_act_device'])
rate_state_device = functools.cache(lambda: _t()['rate_state_device'])
rate_bad = functools.cache(lambda: _t()['rate_bad'])
PAGES = 'pages'
pages_max_days = functools.cache(lambda: _t()['pages_max_days'])
pages_max_active = functools.cache(lambda: _t()['pages_max_active'])
pages_max = functools.cache(lambda: _t()['pages_max'])
pages_hours = functools.cache(lambda: [h for h in _t()['pages_hours'] if h <= pages_max_days() * 24])
pages_check = functools.cache(lambda: dt.timedelta(seconds=_t()['pages_check_s']))
pages_debounce = functools.cache(lambda: _t()['pages_debounce_s'])
password_len = functools.cache(lambda: max(12, int(_t()['pages_password_len'])))
slug_max = functools.cache(lambda: int(_t()['pages_slug_max']))
roles = functools.cache(lambda: {k: {'pick': v['pick'], 'hours': v['hours']} for k, v in (_t().get('pages_roles') or {}).items() if isinstance(v, dict) and v.get('pick') in ('admin', 'none') and (v.get('hours') in pages_hours())})
PASSWORD_ABC = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
TOKEN = re.compile('([A-Za-z0-9_-]{12})\\.([A-Za-z0-9_-]{32})')
DEVICE = re.compile('[A-Za-z0-9_-]{16,64}')
ENTITY = re.compile('(light|cover|climate)\\.[a-z0-9_]{1,128}')
UNSAFE_TEXT = re.compile('[\\x00-\\x1f\\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]')
ACTIONS: dict[str, tuple[str, str]] = {'on': ('light', 'turn_on'), 'off': ('light', 'turn_off'), 'open': ('cover', 'open_cover'), 'close': ('cover', 'close_cover'), 'stop': ('cover', 'stop_cover'), 'position': ('cover', 'set_cover_position'), 'temperature': ('climate', 'set_temperature')}
COVER_FEATURE = {'open': 1, 'close': 2, 'position': 4, 'stop': 8}

class Invalid(ValueError):
    """A request the family's screen sent wrong (400)."""

class Refused(Invalid, derived.Refused):
    """A refusal a person can meet (not a request sent wrong): said in the page's language — translations
    guest_<code>."""

    def __init__(self, code: str, status: int=HTTPStatus.BAD_REQUEST, **ph: Any) -> None:
        derived.Refused.__init__(self, code, f'guest_{code}', status, **ph)

def secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()

def clean(text: Any, limit: int) -> str:
    return UNSAFE_TEXT.sub('', text if isinstance(text, str) else '').strip()[:limit]

def iso(t: dt.datetime) -> str:
    return t.isoformat(timespec='seconds')

class Window:
    """Events in the last minute."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.at: deque[float] = deque()

    def full(self) -> bool:
        now = time.monotonic()
        while self.at and now - self.at[0] > 60:
            self.at.popleft()
        return len(self.at) >= self.limit

    def add(self) -> None:
        self.at.append(time.monotonic())

def issuer_ok(user: Any) -> bool:
    """A person (not a system user) who may control the house in HA — a read-only one may not hand control on."""
    if user is None or getattr(user, 'system_generated', True) or (not getattr(user, 'is_active', False)):
        return False
    with contained(_LOGGER, 'Hauflo: user permissions not readable — not allowed (all entities)'):
        return bool(user.is_admin or user.permissions.access_all_entities(POLICY_CONTROL))
    return False

def may_control(user: Any, eid: str) -> bool:
    with contained(_LOGGER, 'Hauflo: user permissions not readable — %s not allowed', eid):
        return bool(user.is_admin or user.permissions.check_entity(eid, POLICY_CONTROL))
    return False
ask_gap = functools.cache(lambda: _t()['ask_gap_s'])
