"""The stages of the entry's setup."""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util
from .. import combos, daylight, house, intercom as intercom_mod, issues, keep, locks as locks_mod, ownership, protect, quiet, retired, self_update, sidebar, stock, theme, voice, watchdog
from ..const import CONF_INTERCOM, DATA_CARDS_LOCAL, DOMAIN, site_ru
from ..foreign import flags as foreign_flags
from ..guest import access as guest_access
from ..habits import engine as habits_engine
from ..health import runner as health
from ..insight import engine as insight_engine
from ..install import installer as install_installer
from ..issues import degrade, degrading
from ..link.agent import Agent
from ..notify import engine as notify_engine
from ..policy.areas import load_stock_area_names
from ..policy.knx import knx_addresses, owned_knx
from ..policy.marks import protect_refresh
from ..policy.own import hauflo_entity, own_entity, platform, protected_ids
from ..policy.scrub import cloud_reads
from ..preload import async_preload
from ..presence import engine as presence_engine
from ..rules import engine as rules_engine
from ..runtime import analysis_panel, automations_of, policy_changed
from ..season import engine as season_engine
from ..signals import store as signals_store
from ..theme import asset_tag
from .const import INTERCOM_PLATFORMS, PLATFORMS
from .hooks import entry_changed, lock_info, locks_changed, remerge_on_start
from .register import register_views
if TYPE_CHECKING:
    from ..link.agent import HaufloConfigEntry
_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: HaufloConfigEntry) -> bool:
    await async_preload(hass)
    with degrading(hass, 'house settings', issues.AGENT):
        await house.async_init(hass)
    agent = Agent(hass, entry)
    await agent.async_load()
    entry.runtime_data = agent
    with degrading(hass, 'stock rooms', issues.HA):
        await load_stock_area_names(hass)
    await protect.async_load(hass)
    await foreign_flags.async_setup(hass)
    sig = await signals_store.async_setup(hass)
    agent.combos = combos.Combos(hass, entry.entry_id, lambda: site_ru(hass.config.language, getattr(agent.engine, 'config', None)))
    protect_refresh(hass)
    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, callback(lambda _e: protect_refresh(hass))))
    owns = lambda eid: own_entity(hass, eid, agent.owned_entries) and (platform(hass, eid) != 'knx' or owned_knx(hass, eid, agent.owned))
    agent.season = season_engine.Season(hass, lambda: agent.engine, lambda: agent.rules, owns)
    await agent.season.async_load()
    agent.daylight = daylight.Daylight(hass)
    with degrading(hass, 'comfort floor removal', issues.AGENT):
        await retired.async_retire_comfort(hass, entry.entry_id, agent.season.async_seed)

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
    agent.rules = rules_engine.Rules(hass, owns=lambda eid: hauflo_entity(hass, eid, agent.owned, agent.owned_entries), own_raw=owns, season=agent.season.rule_season, tell=_tell, season_now=agent.season.current, season_set=_season_set, ask=_ask, notify_of=lambda: agent.engine)
    agent.rules.entry_id = entry.entry_id
    agent.rules.cloud_reads = lambda eid, attr: cloud_reads(hass, eid, attr, agent.state_visible)
    with degrading(hass, 'value rules', issues.AGENT):
        await agent.rules.async_load()
    agent.ownership = ownership.Ownership(hass, lambda: agent.owned, lambda: agent.owned_entries, lambda: agent.locked, lambda: set(protected_ids(hass)))
    with degrading(hass, 'hauflo label', issues.AGENT):
        await agent.ownership.async_start()
    agent.locks = locks_mod.Locks(hass, lambda: agent.engine)
    with degrading(hass, 'intercom', issues.AGENT):
        agent.intercom = intercom_mod.Intercom(hass, entry.entry_id, dict(entry.options.get(CONF_INTERCOM) or {}))
        agent.intercom.locks = agent.locks
        agent.intercom.cloud_fetch = agent.cloud_ice
        if agent.intercom.doors:
            agent.intercom.hub_device_id = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}).id
    agent.platforms = PLATFORMS + (INTERCOM_PLATFORMS if agent.intercom is not None and agent.intercom.doors else [])
    await hass.config_entries.async_forward_entry_setups(entry, agent.platforms)
    if agent.intercom is not None:
        with degrading(hass, 'intercom', issues.AGENT):
            await agent.intercom.start()
    with degrading(hass, 'season', issues.AGENT):
        await agent.season.async_start()
    with degrading(hass, 'daylight', issues.AGENT):
        await agent.daylight.async_start()
    with degrading(hass, 'value rules', issues.AGENT):
        await agent.rules.async_start()
    with degrading(hass, 'lovelace resources', issues.HA):
        hass.data[DATA_CARDS_LOCAL] = await keep.async_on_start(hass, asset_tag())
    for feature, setup in (('voice sentences', voice.async_setup), ('theme', theme.async_setup), ('menu for new people', sidebar.async_setup)):
        with degrading(hass, feature, issues.HA):
            await setup(hass)
    entry.async_create_background_task(hass, agent.run(), 'hauflo agent link')
    agent.presence = presence_engine.Presence(hass, lambda eid: hauflo_entity(hass, eid, agent.owned, agent.owned_entries))
    with degrading(hass, 'presence simulation', issues.AGENT):
        await agent.presence.async_start()
    agent.habits = habits_engine.Habits(hass, lambda eid: (a := automations_of(hass)) is not None and a.pickable(eid), lambda: [agent.rules, agent.presence], lambda eid: (a := automations_of(hass)) is not None and a.area(eid) or None)
    with degrading(hass, 'habit suggestions', issues.AGENT):
        await agent.habits.async_start()
    agent.guest = guest_access.GuestAccess(hass, lambda eid: hauflo_entity(hass, eid, agent.owned, agent.owned_entries), lambda: f"{agent.server.rstrip('/')}/g/{guest_access.route_of(agent.secret)}")
    with degrading(hass, 'guest access', issues.AGENT):
        await agent.guest.async_load()
    agent.guest.dashboards = agent.dashboards
    agent.dashboards.access_of = agent.guest.access
    with degrading(hass, 'guest pages', issues.AGENT):
        await agent.guest.async_start()
    engine = notify_engine.NotifyEngine(hass)
    engine.on_change = agent.status_now
    engine.owns = owns
    engine.locked = lambda: agent.locked
    engine.watches = agent.watches
    engine.quiet_now = lambda: agent.rules is not None and quiet.inside(agent.rules.quiet, dt_util.utcnow())
    engine.lock_owns = lambda eid: agent.locks is not None and agent.locks.owns_event(eid)
    await engine.async_start()
    agent.engine = engine
    sig_flags = foreign_flags.flags(hass)
    if sig_flags is not None:
        sig_flags.on_locks = lambda _was, _now: locks_changed(hass, agent)
        sig_flags.lock_info = lambda: lock_info(agent)
    with degrading(hass, 'locks', issues.AGENT):
        await agent.locks.async_start()
    agent.insights = insight_engine.Insights(hass, owns, agent.season.current)
    agent.insights.on_change = agent.status_now
    agent.insights.watched = engine.watched
    agent.insights.finding_of = sig.finding
    agent.insights.addresses = lambda: knx_addresses(hass)
    agent.insights.on = lambda: signals_store.observing(hass)
    try:
        await agent.insights.async_start()
    except Exception as err:
        agent.insights = None
        degrade(hass, 'insights', err, issues.AGENT)
    agent.watchdog = watchdog.Watchdog(hass)
    agent.watchdog.on_change = agent.status_now
    agent.watchdog.off = lambda: signals_store.watchdog_off(hass)
    try:
        await agent.watchdog.async_start()
    except Exception as err:
        agent.watchdog = None
        degrade(hass, 'integration watchdog', err, issues.AGENT)
    agent.health = health.Health(hass)
    agent.health.on_change = agent.status_now
    try:
        await agent.health.async_start()
    except Exception as err:
        agent.health = None
        degrade(hass, 'health checks', err, issues.AGENT)
    entry.async_on_unload(sig.listen(lambda: policy_changed(hass, agent)))
    analysis_panel(hass)

    @callback
    def _house_start(_hass: HomeAssistant) -> None:
        agent.watch_signals()
        agent.house_now()
    entry.async_on_unload(async_at_started(hass, _house_start))
    remerge_on_start(hass, entry, agent)
    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, callback(lambda _e: agent.rewatch_soon() if hass.is_running else None)))
    await hass.async_add_executor_job(stock.table)
    with degrading(hass, 'install', issues.AGENT):
        await install_installer.installer(hass).async_load()
    with degrading(hass, 'self_update', issues.AGENT):
        await self_update.updater(hass).async_load()
    register_views(hass)

    async def _stop(_event: Event) -> None:
        if agent.presence is not None:
            agent.presence.async_stop()
        if agent.intercom is not None:
            await agent.intercom.stop()
        if agent.locks is not None:
            agent.locks.stop()
        await agent.stop()
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop))
    entry.async_on_unload(entry.add_update_listener(entry_changed))
    issues.sync_degraded(hass)
    return True
