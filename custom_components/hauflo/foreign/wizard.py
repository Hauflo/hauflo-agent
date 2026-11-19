"""The wizard's changes to other integrations' devices: checked, prepared and taken back."""
from __future__ import annotations
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, label_registry as lr
from ..policy.own import protected
from .common import DEVICE_KEYS, DEVICE_UPDATE, ENTITY_KEYS, ENTITY_UPDATE, NEVER_PLATFORMS, PLAN_ID, PLAN_KEY, ROLE_PREFIX, ROLE_RE, UNSAFE_NAME, device_entry_ids, deviceless_domains, labels_max, name_max, roles
from .flags import flags
from .plans import active_plans, consume, holding

def _labels_ok(hass: HomeAssistant, labels: Any) -> str | None:
    if not isinstance(labels, list) or len(labels) > labels_max() or (not all((isinstance(x, str) for x in labels))):
        return 'bad_labels'
    if not all((ROLE_RE.match(x) and x[len(ROLE_PREFIX):] in roles() for x in labels)):
        return 'label_not_hauflo_role'
    alice = {str(e.options.get('label')) for e in hass.config_entries.async_entries('yandex_smart_home') if e.options.get('label')}
    if set(labels) & alice:
        return 'label_not_hauflo_role'
    return None

def _name_ok(v: Any) -> bool:
    return v is None or (isinstance(v, str) and bool(v.strip()) and (len(v) <= name_max()) and (not UNSAFE_NAME.search(v)))

def _device_refusal(hass: HomeAssistant, device_id: Any) -> str | None:
    device = dr.async_get(hass).async_get(device_id) if isinstance(device_id, str) else None
    if device is None:
        return 'unknown_device'
    if getattr(device, 'entry_type', None) is not None:
        return 'service_device'
    domains = {e.domain for eid in device_entry_ids(device) if (e := hass.config_entries.async_get_entry(eid))}
    ents = er.async_entries_for_device(er.async_get(hass), device.id, include_disabled_entities=True)
    if (domains | {e.platform for e in ents}) & NEVER_PLATFORMS:
        return 'platform_not_allowed'
    return None

def _area_refusal(hass: HomeAssistant, plan: dict[str, Any], area_id: Any, was: Any, allow_none: bool) -> str | None:
    if area_id is None:
        return None if allow_none or was is None else 'area_not_in_plan'
    if not isinstance(area_id, str):
        return 'bad_area'
    if area_id != was and area_id not in plan['areas']:
        return 'area_not_in_plan'
    return None if ar.async_get(hass).async_get_area(area_id) is not None else 'unknown_area'

def _journal_key(hass: HomeAssistant, msg: dict[str, Any]) -> str | None:
    if msg.get('type') == DEVICE_UPDATE:
        return f"d:{msg.get('device_id')}"
    entry = er.async_get(hass).async_get(str(msg.get('entity_id')))
    return f'e:{entry.id}' if entry is not None else None

def _was(hass: HomeAssistant, msg: dict[str, Any], field: str) -> Any:
    f = flags(hass)
    key = _journal_key(hass, msg)
    rec = f.journal.get(key) if f is not None and key else None
    if rec is not None and field in rec['was']:
        return rec['was'][field]
    return current(hass, msg).get(field)

def current(hass: HomeAssistant, msg: dict[str, Any]) -> dict[str, Any]:
    if msg.get('type') == DEVICE_UPDATE:
        d = dr.async_get(hass).async_get(str(msg.get('device_id')))
        return {} if d is None else {'area_id': d.area_id, 'name_by_user': d.name_by_user, 'labels': sorted(d.labels)}
    e = er.async_get(hass).async_get(str(msg.get('entity_id')))
    return {} if e is None else {'area_id': e.area_id, 'name': e.name, 'hidden_by': e.hidden_by.value if e.hidden_by is not None else None, 'labels': sorted(e.labels)}

def is_protected(hass: HomeAssistant, entity_id: str) -> bool:
    return protected(hass, entity_id)

def wizard_refusal(hass: HomeAssistant | None, msg: dict[str, Any]) -> str | None:
    """Why a device / entity registry update of another integration's item is refused (None — allowed)."""
    return decide(hass, msg)[0]

def decide(hass: HomeAssistant | None, msg: dict[str, Any]) -> tuple[str | None, str | None]:
    """(why refused, None) or (None, the id of the plan that allows it): the oldest live plan holding the item whose rooms allow the write — a room is never taken from another deploy's plan."""
    live = active_plans(hass) if hass is not None else {}
    held = holding(hass, msg)
    if not held:
        why = _refusal(hass, msg, {'areas': set(), 'devices': set(), 'entities': set()} if live else None)
        if PLAN_KEY in msg and why in ('not_in_plan', 'no_plan'):
            named = msg.get(PLAN_KEY)
            why = 'not_in_plan' if isinstance(named, str) and named in live else 'plan_gone'
        return (why, None)
    why: str | None = None
    for pid, plan in held:
        if (r := _refusal(hass, msg, plan)) is None:
            return (None, pid)
        why = why or r
    return (why, None)

def _refusal(hass: HomeAssistant | None, msg: dict[str, Any], plan: dict[str, Any] | None) -> str | None:
    t = msg.get('type')
    if hass is None or t not in (DEVICE_UPDATE, ENTITY_UPDATE):
        return 'not_allowed'
    device = t == DEVICE_UPDATE
    keys, ref = (DEVICE_KEYS, 'device_id') if device else (ENTITY_KEYS, 'entity_id')
    extra = set(msg) - {'id', 'type', ref, PLAN_KEY} - keys
    if extra:
        return f'field_not_allowed:{sorted(extra)[0]}'
    if not set(msg) & keys:
        return 'nothing_to_change'
    if PLAN_KEY in msg and (not (isinstance(msg[PLAN_KEY], str) and PLAN_ID.match(msg[PLAN_KEY]))):
        return 'plan_gone'
    if plan is None:
        return 'no_plan'
    if device:
        if (why := _device_refusal(hass, msg.get('device_id'))) is not None:
            return why
        if msg['device_id'] not in plan['devices']:
            return 'not_in_plan'
        if 'name_by_user' in msg and (not _name_ok(msg['name_by_user'])):
            return 'bad_name'
    else:
        entry = er.async_get(hass).async_get(str(msg.get('entity_id')))
        if entry is None:
            return 'unknown_entity'
        if entry.platform in NEVER_PLATFORMS:
            return 'platform_not_allowed'
        if entry.device_id:
            if (why := _device_refusal(hass, entry.device_id)) is not None:
                return why
        elif not entry.unique_id:
            return 'no_unique_id'
        elif entry.domain not in deviceless_domains():
            return 'system_entity'
        if entry.entity_id not in plan['entities']:
            return 'not_in_plan'
        if set(msg) & {'area_id', 'labels'} and is_protected(hass, entry.entity_id):
            return 'protected_domain'
        if 'hidden_by' in msg and msg['hidden_by'] not in ('user', None):
            return 'bad_hidden_by'
        if 'name' in msg and (not _name_ok(msg['name'])):
            return 'bad_name'
    out_of_room = device and msg.get('area_id', '') is None and (current(hass, msg).get('area_id') in plan['areas'])
    if 'area_id' in msg and (why := _area_refusal(hass, plan, msg['area_id'], _was(hass, msg, 'area_id'), allow_none=not device or out_of_room)) is not None:
        return why
    if 'labels' in msg and (why := _labels_ok(hass, msg['labels'])) is not None:
        return why
    return None

def wizard_prepare(hass: HomeAssistant, msg: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """Before an allowed write goes to HA: keeps the item's values as they were (first write only), merges the role labels with the family's own (HA replaces the whole list), creates a missing hauflo_role_* label."""
    f = flags(hass)
    created: list[str] = []
    _why, pid = decide(hass, msg)
    out = {k: v for k, v in msg.items() if k != PLAN_KEY}
    if msg.get('type') == DEVICE_UPDATE and 'area_id' in msg and (msg['area_id'] is None):
        was = _was(hass, msg, 'area_id')
        rooms = (active_plans(hass).get(pid) or {}).get('areas') or () if pid is not None else ()
        if isinstance(was, str) and was not in rooms and (ar.async_get(hass).async_get_area(was) is not None):
            out['area_id'] = was
    if 'labels' in msg:
        reg = lr.async_get(hass)
        for lid in dict.fromkeys(msg['labels']):
            if reg.async_get_label(lid) is not None:
                continue
            if reg.async_get_label_by_name(lid) is not None:
                return (None, created)
            label = reg.async_create(lid, icon='mdi:tag-outline', description='Hauflo')
            if label.label_id != lid:
                reg.async_delete(label.label_id)
                return (None, created)
            created.append(lid)
        cur = current(hass, msg).get('labels') or []
        out['labels'] = sorted({x for x in cur if not x.startswith(ROLE_PREFIX)} | set(msg['labels']))
    key = _journal_key(hass, msg)
    if f is not None and key is not None:
        cur = current(hass, msg)
        rec = f.journal.setdefault(key, {'was': {}, 'set': {}})
        fields = (DEVICE_KEYS if msg.get('type') == DEVICE_UPDATE else ENTITY_KEYS) & set(out)
        for k in fields:
            rec['was'].setdefault(k, cur.get(k))
            rec['set'][k] = sorted(out[k]) if k == 'labels' else out[k]
        f.save()
    if pid is not None:
        consume(hass, pid, msg)
    return (out, created)
