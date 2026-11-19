"""Takes the wizard's changes back from the journal."""
from __future__ import annotations
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from .common import DEVICE_UPDATE, ENTITY_UPDATE, ROLE_PREFIX
from .flags import flags
from .plans import drop_items
from .wizard import current

def revert(hass: HomeAssistant, devices: set[str], entities: set[str]) -> dict[str, Any]:
    """«Вернуть как было»: what Hauflo wrote to these items back to the values before it."""
    f = flags(hass)
    out: dict[str, Any] = {'devices': [], 'entities': [], 'kept': {}, 'unknown': []}
    if f is None:
        return out
    dreg, ereg = (dr.async_get(hass), er.async_get(hass))
    targets: list[tuple[str, str, Any]] = [('d', d, dreg.async_get(d)) for d in sorted(devices)]
    seen = set(entities)
    for d in sorted(devices):
        if dreg.async_get(d) is None:
            continue
        for e in er.async_entries_for_device(ereg, d, include_disabled_entities=True):
            if e.entity_id not in seen and f'e:{e.id}' in f.journal:
                seen.add(e.entity_id)
                entities = entities | {e.entity_id}
    for eid in sorted(entities):
        e = ereg.async_get(eid)
        targets.append(('e', eid, e))
    for kind, ref, item in targets:
        key = f'{kind}:{ref}' if kind == 'd' else f'e:{item.id}' if item is not None else None
        rec = f.journal.get(key) if key else None
        if item is None or rec is None:
            out['unknown'].append(ref)
            continue
        msg = {'type': DEVICE_UPDATE, 'device_id': ref} if kind == 'd' else {'type': ENTITY_UPDATE, 'entity_id': ref}
        cur = current(hass, msg)
        changes: dict[str, Any] = {}
        kept: list[str] = []
        for k, was in rec['was'].items():
            if k == 'labels':
                if cur.get('labels') != rec['set'].get('labels'):
                    kept.append(k)
                ours = {x for x in cur.get('labels') or [] if x.startswith(ROLE_PREFIX) and x not in (was or [])}
                if ours:
                    changes['labels'] = {x for x in cur.get('labels') or [] if x not in ours}
            elif cur.get(k) == rec['set'].get(k):
                if cur.get(k) != was:
                    changes[k] = was
            else:
                kept.append(k)
        if 'area_id' in changes and changes['area_id'] is not None and (ar.async_get(hass).async_get_area(changes['area_id']) is None):
            changes.pop('area_id')
            kept.append('area_id')
        if kind == 'd':
            dreg.async_update_device(ref, **changes)
        else:
            if 'hidden_by' in changes:
                changes['hidden_by'] = er.RegistryEntryHider(changes['hidden_by']) if changes['hidden_by'] else None
            ereg.async_update_entity(ref, **changes)
        f.journal.pop(key, None)
        drop_items(hass, {ref} if kind == 'd' else set(), {ref} if kind == 'e' else set())
        out['devices' if kind == 'd' else 'entities'].append(ref)
        if kept:
            out['kept'][ref] = sorted(set(kept))
    f.save()
    return out

def journal_list(hass: HomeAssistant) -> dict[str, list[str]]:
    """What Hauflo changed and can put back: device ids, entity ids (as they are now)."""
    f = flags(hass)
    if f is None:
        return {'devices': [], 'entities': []}
    ereg = er.async_get(hass)
    ids = {e.id: e.entity_id for e in ereg.entities.values()}
    return {'devices': sorted((k[2:] for k in f.journal if k.startswith('d:'))), 'entities': sorted((ids[k[2:]] for k in f.journal if k.startswith('e:') and k[2:] in ids))}
