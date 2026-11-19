"""Unloading and removing the entry."""
from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING
import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .. import analysis, issues, keep, ownership, protect, retired, servers, service, sidebar, theme, voice, watchdog
from ..const import CONF_SECRET, CONF_SERVER, CONF_USER_ID
from ..dashmerge import store as dashmerge_store
from ..foreign import common as foreign_common, flags as foreign_flags
from ..guard import contained
from ..guest import access as guest_access, common as guest_common
from ..habits import common as habits_common
from ..health import common as health_common
from ..independent import service as independent_service
from ..insight import common as insight_common
from ..link.common import connect_timeout, good_hosts, unpair_timeout
from ..notify import engine as notify_engine
from ..preload import async_preload
from ..rules import const as rules_const
from ..season import common as season_common
from ..storage import HaufloStore
from ..theme import asset_tag
from .const import PLATFORMS
if TYPE_CHECKING:
    from ..link.agent import HaufloConfigEntry
_LOGGER = logging.getLogger(__name__)

async def async_unload_entry(hass: HomeAssistant, entry: HaufloConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, getattr(entry.runtime_data, 'platforms', PLATFORMS)):
        return False
    if (ic := entry.runtime_data.intercom) is not None:
        entry.runtime_data.intercom = None
        await ic.stop()
    if (lk := entry.runtime_data.locks) is not None:
        entry.runtime_data.locks = None
        lk.stop()
        await lk.async_save()
    if (ff := foreign_flags.flags(hass)) is not None:
        ff.plans.clear()
    if (dl := entry.runtime_data.daylight) is not None:
        dl.async_stop()
    if (sn := entry.runtime_data.season) is not None:
        sn.async_stop()
        await sn.store.async_save(sn.data())
    if (cb := entry.runtime_data.combos) is not None:
        cb.stop()
    if (vr := entry.runtime_data.rules) is not None:
        vr.async_stop()
        await vr.store.async_save(vr.data())
    if (sim := entry.runtime_data.presence) is not None:
        entry.runtime_data.presence = None
        sim.async_stop()
        if not hass.is_stopping:
            with contained(_LOGGER, 'Hauflo presence: lights not switched off on unload'):
                async with asyncio.timeout(15):
                    await sim.async_lights_out()
    if (hb := entry.runtime_data.habits) is not None:
        entry.runtime_data.habits = None
        await hb.async_stop()
    await entry.runtime_data.stop()
    if (guests := entry.runtime_data.guest) is not None:
        entry.runtime_data.guest = None
        await guests.async_stop()
    if (own := entry.runtime_data.ownership) is not None:
        own.async_stop()
        await own.async_flush()
    voice.async_unload(hass)
    sidebar.async_unload(hass)
    analysis.async_unregister(hass)
    theme.async_unload(hass)
    if (wd := entry.runtime_data.watchdog):
        entry.runtime_data.watchdog = None
        wd.async_stop()
        await wd.async_flush()
    if (hl := entry.runtime_data.health):
        entry.runtime_data.health = None
        hl.async_stop()
        await hl.async_flush()
    if (ins := entry.runtime_data.insights):
        entry.runtime_data.insights = None
        ins.async_stop()
        await ins.store.async_save(ins.data())
    if (engine := entry.runtime_data.engine):
        entry.runtime_data.engine = None
        engine.async_stop()
    issues.clear_degraded(hass)
    return True

async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Removed in HA: its system user and voice sentences go too, and Hauflo is told (the site is no longer paired — no «agent gone» alarm)."""
    await async_preload(hass)
    await voice.async_remove(hass)
    issues.clear_all(hass)
    with contained(_LOGGER, 'Hauflo: dashboard cards not kept', level=logging.ERROR):
        await keep.async_keep_dashboard(hass, asset_tag())
    with contained(_LOGGER, 'Hauflo: notifications engine not removed'):
        await notify_engine.NotifyEngine(hass).async_remove()
    with contained(_LOGGER, 'Hauflo: guests with a login not removed', level=logging.ERROR):
        gone = guest_access.GuestAccess(hass, lambda _eid: False, lambda: '')
        gone.dashboards = dashmerge_store.DashboardMerge(hass)
        await gone.dashboards.async_load()
        gone.dashboards.access_of = gone.access
        await gone.async_remove()
    with contained(_LOGGER, 'Hauflo: kept alarms not told', level=logging.ERROR):
        await independent_service.async_removed(hass)
    with contained(_LOGGER, 'Hauflo: climate store not removed'):
        await HaufloStore(hass, retired.COMFORT_STORE).async_remove()
    with contained(_LOGGER, 'Hauflo: season store not removed'):
        await HaufloStore(hass, season_common.STORE_KEY).async_remove()
    with contained(_LOGGER, 'Hauflo: foreign-devices store not removed'):
        await HaufloStore(hass, foreign_common.STORE_KEY).async_remove()
    hass.data.pop(foreign_common.DATA, None)
    with contained(_LOGGER, 'Hauflo: rules store not removed'):
        await HaufloStore(hass, rules_const.STORE_KEY).async_remove()
    with contained(_LOGGER, 'Hauflo: insights store not removed'):
        await HaufloStore(hass, insight_common.STORE_KEY).async_remove()
    with contained(_LOGGER, 'Hauflo: watchdog store not removed'):
        await HaufloStore(hass, watchdog.STORE_KEY).async_remove()
    with contained(_LOGGER, 'Hauflo: health store not removed'):
        await HaufloStore(hass, health_common.STORE_KEY).async_remove()
    with contained(_LOGGER, 'Hauflo: habits store not removed'):
        await HaufloStore(hass, habits_common.STORE_KEY).async_remove()
    with contained(_LOGGER, 'Hauflo: protected covers not removed', level=logging.ERROR):
        await HaufloStore(hass, protect.STORE_KEY).async_remove()
    hass.data.pop(protect.DATA, None)
    with contained(_LOGGER, 'Hauflo: guest access log not removed', level=logging.ERROR):
        await HaufloStore(hass, guest_common.STORE_KEY).async_remove()
    with contained(_LOGGER, 'Hauflo: service log not removed'):
        await service.async_remove(hass)
    with contained(_LOGGER, 'Hauflo: ownership store not removed'):
        await HaufloStore(hass, ownership.STORE_KEY).async_remove()
    with contained(_LOGGER, 'Hauflo: sidebar panels not restored', level=logging.ERROR):
        await sidebar.async_remove(hass)
    with contained(_LOGGER, 'Hauflo: server not told of the unlink', expect=servers.UNREACHABLE):
        session = async_get_clientsession(hass)

        async def unpair(host: str) -> None:
            await session.post(f'{host}/api/agent/unpair', headers={'Authorization': f'Bearer {entry.data[CONF_SECRET]}'}, json={}, timeout=aiohttp.ClientTimeout(total=unpair_timeout(), sock_connect=connect_timeout()))
        await servers.first_answer(servers.same_region(entry.data[CONF_SERVER], (good := good_hosts(hass))), unpair, good)
    if (uid := entry.data.get(CONF_USER_ID)) and (user := (await hass.auth.async_get_user(uid))):
        await hass.auth.async_remove_user(user)
