"""Notify groups of the alarm rules and their categories."""
from __future__ import annotations
import asyncio
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import category_registry as cr, entity_registry as er
from .automations import our_entries
from .common import GROUP_ICON, SCRIPT, SHARED_IDS, grouped, helper_state

def group_entity(hass: HomeAssistant, object_id: str) -> str:
    """The helper's entity (renamed in HA — still found by its entry), else the id it will get."""
    if (entry := our_entries(hass).get(object_id)) is not None:
        reg = er.async_get(hass)
        if (eid := reg.async_get_entity_id('binary_sensor', entry.domain, entry.entry_id)):
            return eid
    return f'binary_sensor.{object_id}'

def _wanted(rule: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if grouped(rule):
        return ('group', {'entities': list(rule['sources']), 'all': False, 'hide_members': False})
    return ('template', {'state': helper_state(rule)})

async def ensure_group(hass: HomeAssistant, object_id: str, rule: dict[str, Any], name: str) -> str:
    """The rule's «все датчики» helper, as HA's helper editor makes it (group, else template); its members / state kept as the rule's; named «<Правило> · все датчики»."""
    domain, opts = _wanted(rule)
    reg = er.async_get(hass)
    entry = our_entries(hass).get(object_id)
    if entry is not None and entry.domain != domain:
        await hass.config_entries.async_remove(entry.entry_id)
        entry = None
    if entry is None:
        flow = await hass.config_entries.flow.async_init(domain, context={'source': 'user'})
        flow = await hass.config_entries.flow.async_configure(flow['flow_id'], {'next_step_id': 'binary_sensor'})
        res = await hass.config_entries.flow.async_configure(flow['flow_id'], {'name': object_id, **opts})
        entry = res.get('result')
        if entry is None:
            raise RuntimeError(f"helper {object_id} not made: {res.get('errors') or res.get('reason')}")
    elif any((entry.options.get(k) != v for k, v in opts.items())):
        hass.config_entries.async_update_entry(entry, options={**entry.options, **opts})
        await hass.config_entries.async_reload(entry.entry_id)
    eid = None
    for _ in range(50):
        if (eid := reg.async_get_entity_id('binary_sensor', domain, entry.entry_id)):
            break
        await asyncio.sleep(0.1)
    if eid is None:
        raise RuntimeError(f'helper {object_id} not set up')
    if entry.title != name:
        hass.config_entries.async_update_entry(entry, title=name)
    ent = reg.async_get(eid)
    if ent is not None and (ent.name != name or ent.icon != GROUP_ICON):
        reg.async_update_entity(eid, name=name, icon=GROUP_ICON)
    return eid

async def drop_group(hass: HomeAssistant, object_id: str) -> None:
    if (entry := our_entries(hass).get(object_id)) is not None:
        await hass.config_entries.async_remove(entry.entry_id)

async def drop_all_groups(hass: HomeAssistant) -> None:
    for entry in list(our_entries(hass).values()):
        await hass.config_entries.async_remove(entry.entry_id)

def categorize(hass: HomeAssistant, name: str, entity_ids: dict[str, list[str]]) -> None:
    """The alarm objects under one category per list (HA's own categories: the automations / scripts / helpers page groups them)."""
    cats = cr.async_get(hass)
    reg = er.async_get(hass)
    for scope, eids in entity_ids.items():
        if not eids:
            continue
        cat = next((c for c in cats.async_list_categories(scope=scope) if c.name == name), None)
        if cat is None:
            cat = cats.async_create(scope=scope, name=name, icon='mdi:alarm-light')
        for eid in eids:
            ent = reg.async_get(eid)
            if ent is not None and (not ent.categories.get(scope)):
                reg.async_update_entity(eid, categories={**ent.categories, scope: cat.category_id})

def category_members(hass: HomeAssistant, recs: list[dict[str, Any]]) -> dict[str, list[str]]:
    """The alarm objects of the flat form's records (and the shared ones) by the list they show in."""
    reg = er.async_get(hass)
    autos = [*SHARED_IDS, *(x for rec in recs for x in (rec.get('automation'), rec.get('back')) if x)]
    scripts = [SCRIPT, *(rec['run_script'] for rec in recs if rec.get('run_script'))]
    return {'automation': [e for a in autos if (e := reg.async_get_entity_id('automation', 'automation', a))], 'script': [e for k in scripts if (e := reg.async_get_entity_id('script', 'script', k))], 'helpers': [x for rec in recs for x in (rec.get('helper'), rec.get('group')) if x]}
