"""Settings of the foreign entities and the config entries of a device."""
from __future__ import annotations
import functools
import re
from typing import Any
from ..const import DOMAIN
from ..tuning import tuning
STORE_KEY = f'{DOMAIN}.foreign'
DATA = f'{DOMAIN}_foreign'
max_ids = functools.cache(lambda: tuning()['ownership']['foreign_max'])
name_max = functools.cache(lambda: tuning()['ownership']['foreign_name_max'])
plan_ttl = functools.cache(lambda: tuning()['ownership']['foreign_plan_ttl_s'])
plans_max = functools.cache(lambda: tuning()['ownership']['foreign_plans_max'])
PLAN_FEATURE = 'foreign_plan_id'
WRITE_PLAN_FEATURE = 'foreign_write_plan'
PLAN_KEY = 'hauflo_plan'
PLAN_ID = re.compile('^[A-Za-z0-9_-]{1,64}$')
LEGACY_PLAN = ''
labels_max = functools.cache(lambda: tuning()['ownership']['foreign_labels_max'])
FEATURE = 'foreign_wizard'
MEMBERS_FEATURE = 'foreign_scene_members'
MEMBER_DOMAINS = frozenset({'light', 'cover'})
shown_domains = functools.cache(lambda: frozenset(tuning()['ownership']['foreign_shown_domains']))
SHOWN_FEATURE = 'foreign_shown'
ROLE_PREFIX = 'hauflo_role_'
ROLE_RE = re.compile('^hauflo_role_[a-z0-9_]{1,40}$')
roles = functools.cache(lambda: frozenset(tuning()['ownership']['foreign_roles']))
deviceless_domains = functools.cache(lambda: frozenset(tuning()['ownership']['foreign_deviceless_domains']))
UNSAFE_NAME = re.compile('[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]')
NEVER_PLATFORMS = frozenset({'knx', DOMAIN, 'mobile_app'})
DEVICE_KEYS = frozenset({'area_id', 'name_by_user', 'labels'})
ENTITY_KEYS = frozenset({'area_id', 'name', 'hidden_by', 'labels'})
DEVICE_UPDATE = 'config/device_registry/update'
ENTITY_UPDATE = 'config/entity_registry/update'
LOCKS_FEATURE = 'foreign_locks'
LOCK_KEYS = frozenset({'push', 'alarms', 'users', 'names', 'door', 'poll'})
lt = functools.cache(lambda: tuning()['locks'])
USER_ID = re.compile('^[0-9a-f]{32}$')
DOOR_ID = re.compile('^[a-z0-9_]{1,64}$')
USER_KEY = re.compile('^[A-Za-z0-9_-]{1,16}$')

def device_entry_ids(device: Any) -> set[str]:
    """The config entries a device belongs to, on every HA the agent supports. HA 2026.x: one `config_entry_id` (the
    old id of a split one) still answers `config_entries` with the union, unlogged; older HA: only `config_entries`."""
    one = getattr(device, 'config_entry_id', None)
    if isinstance(one, str) and one and (getattr(device, 'is_composite_device', False) is not True):
        return {one}
    return {x for x in getattr(device, 'config_entries', None) or () if isinstance(x, str)}

def device_dict_entry_ids(d: dict[str, Any]) -> list[str]:
    """The same for a device as `config/device_registry/list` sends it: `config_entry_id` first (the `config_entries`
    list is kept there only until HA 2027.8)."""
    one = d.get('config_entry_id')
    if isinstance(one, str) and one:
        return [one]
    return [x for x in d.get('config_entries') or [] if isinstance(x, str)]
