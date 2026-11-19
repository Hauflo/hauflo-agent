"""The running engines of the loaded entry, for views and other modules."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.core import HomeAssistant, callback
from . import analysis, exposure, intercom as intercom_mod, issues, ownership
from .automations import engine as automations_engine
from .const import DOMAIN
from .foreign import refusals as foreign_refusals
from .guest import access as guest_access
from .habits import engine as habits_engine
from .independent import sync as independent_sync
from .insight import common as insight_common, engine as insight_engine
from .issues import degrading
from .noeffect import engine as noeffect_engine
from .notify import engine as notify_engine
from .policy.const import NEVER_DOMAINS
from .policy.knx_config import knx_scene_target, knx_scenes_learnable
from .policy.own import hauflo_entity, protected, protected_ids
from .presence import engine as presence_engine
from .rules import actions as rules_actions, engine as rules_engine
from .scenes import common as scenes_common, engine as scenes_engine
from .season import engine as season_engine
_LOGGER = logging.getLogger(__name__)

def exposure_of(hass: HomeAssistant) -> exposure.Exposure | None:
    if not hass.config_entries.async_loaded_entries(DOMAIN):
        return None
    return exposure.Exposure(hass, lambda: set(protected_ids(hass)), NEVER_DOMAINS)

def ownership_of(hass: HomeAssistant) -> ownership.Ownership | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.ownership
    return None

def house_of(hass: HomeAssistant) -> dict[str, Any] | None:
    """The loaded entry's «Всё хорошо» (Agent.house) — for the alarm card's view."""
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.house(summary=True)
    return None

def insights_of(hass: HomeAssistant) -> insight_engine.Insights | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if entry.runtime_data.insights is not None:
            return entry.runtime_data.insights
    return None

@callback
def policy_changed(hass: HomeAssistant, agent: Any) -> None:
    """Hauflo sent the site's policy (signals.py): the rules' overlay, the findings' choices, what is watched and the status — at once."""
    if (engine := agent.engine) is not None and engine.policy_changed() and engine.native:
        hass.async_create_task(independent_sync.async_sync(hass, engine), 'hauflo policy resync')
    if (ins := agent.insights) is not None:
        ins.switch()
        ins.findings_changed()
    agent.watch_signals()
    analysis_panel(hass)
    agent.status_now()

def analysis_panel(hass: HomeAssistant) -> None:
    """The «Анализ» panel registered / re-registered when who may read the diagnosis changed (analysis.py)."""
    with degrading(hass, 'analysis panel', issues.HA):
        engine = notify_of(hass)
        analysis.async_register(hass, insight_common.diag_visible(hass) != insight_common.DIAG_ALL, config=getattr(engine, 'config', None))

def notify_of(hass: HomeAssistant) -> notify_engine.NotifyEngine | None:
    """The loaded entry's alarm engine — for the views, registered once per HA run."""
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
                for eid in rules_actions.inputs_of(rule):
                    found.setdefault(eid, []).append(st)
        if (alarms := agent.engine) is not None:
            for rule in alarms.config['rules']:
                for eid in rule['sources']:
                    open_ = alarms.active.get(f"{rule['id']}|{eid}")
                    found.setdefault(eid, []).append('off' if not rule['enabled'] else 'fired' if open_ and (not open_.get('nodata')) else 'ok')
    return {eid: 'fired' if 'fired' in v else 'off' if all((x == 'off' for x in v)) else 'ok' for eid, v in sorted(found.items())}

def pair_effect_of(hass: HomeAssistant) -> noeffect_engine.PairEffect | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if (ins := entry.runtime_data.insights):
            return next((d for d in ins.detectors if isinstance(d, noeffect_engine.PairEffect)), None)
    return None

def season_of(hass: HomeAssistant) -> season_engine.Season | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.season
    return None

def _habits(hass: HomeAssistant) -> habits_engine.Habits | None:
    return next((e.runtime_data.habits for e in hass.config_entries.async_loaded_entries(DOMAIN) if getattr(e.runtime_data, 'habits', None) is not None), None)

async def habits_set(hass: HomeAssistant, on: bool) -> bool:
    if (h := _habits(hass)) is None:
        return False
    await h.set_enabled(on)
    return True

async def habits_summary(hass: HomeAssistant) -> dict[str, Any]:
    auto = automations_of(hass)
    h = _habits(hass)
    if auto is None or h is None:
        return {}
    try:
        found = len(h.suggestions(auto, await auto.load())) if h.enabled else 0
    except Exception:
        _LOGGER.warning('Hauflo insights: habits not counted', exc_info=True)
        return {'habits': {'on': h.enabled}}
    return {'habits': {'on': h.enabled, 'found': found}}

def automations_of(hass: HomeAssistant) -> automations_engine.Automations | None:
    """One per loaded entry (its lock keeps two saves from writing automations.yaml at once)."""
    if not (entries := hass.config_entries.async_loaded_entries(DOMAIN)):
        return None
    agent = entries[0].runtime_data
    if getattr(agent, 'automations', None) is None:
        agent.automations = automations_engine.Automations(hass, lambda eid: hauflo_entity(hass, eid, agent.owned, agent.owned_entries), lambda eid: protected(hass, eid), lambda: agent.habits)
    return agent.automations

def scenes_of(hass: HomeAssistant) -> scenes_engine.FamilyScenes | None:
    """The family's own room scenes (scenes.py): Hauflo's lights and blinds only, as automations."""
    if not (entries := hass.config_entries.async_loaded_entries(DOMAIN)):
        return None
    agent = entries[0].runtime_data
    if getattr(agent, 'family_scenes', None) is None:
        agent.family_scenes = scenes_engine.FamilyScenes(hass, lambda eid: hauflo_entity(hass, eid, agent.owned, agent.owned_entries) or foreign_refusals.member_ok(hass, eid), knx=scenes_common.KnxScenes(numbers=lambda: knx_scenes_learnable(hass, agent.owned, agent.allowed_ranges()), target=lambda eid: knx_scene_target(hass, agent.owned, agent.allowed_ranges(), eid), locked=lambda: agent.locked), daylight=lambda: agent.daylight)
    return agent.family_scenes

def presence_of(hass: HomeAssistant) -> presence_engine.Presence | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.presence
    return None

def intercom_of(hass: HomeAssistant) -> intercom_mod.Intercom | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.intercom
    return None

def guest_of(hass: HomeAssistant) -> guest_access.GuestAccess | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.guest
    return None

def rules_of(hass: HomeAssistant) -> rules_engine.Rules | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.rules
    return None
