"""Reactions to the entry's changes and the wizard's lock settings."""
from __future__ import annotations
from collections.abc import Mapping
import logging
from typing import TYPE_CHECKING, Any
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.start import async_at_started
from ..const import CONF_INTERCOM, CONF_SECRET, CONF_SERVER, VERSION
from ..dashmerge import ws as dashmerge_ws
if TYPE_CHECKING:
    from ..link.agent import Agent, HaufloConfigEntry
_LOGGER = logging.getLogger(__name__)

def locks_changed(hass: HomeAssistant, agent: Agent) -> None:
    """The wizard's «Замок» saved: the locks listen / read anew (a door's link is read live at «Открыть»)."""
    if agent.locks is not None:
        hass.async_create_task(agent.locks.apply())

async def lock_info(agent: Agent) -> dict[str, Any] | None:
    """The wizard's «Замок» facts — None while the house has no lock (the wizard shows no section then)."""
    if agent.locks is None or not agent.locks.devices:
        return None
    doors = [{'id': d.cfg.id, 'name': d.cfg.name} for d in agent.intercom.doors.values()] if agent.intercom is not None else []
    return await agent.locks.wizard(doors)

def relinked(agent: Agent, data: Mapping[str, Any]) -> bool:
    """The entry's link differs from the one the running agent uses: a reload takes the new one."""
    return data.get(CONF_SECRET) != agent.secret or data.get(CONF_SERVER) != agent.server

async def entry_changed(hass: HomeAssistant, entry: HaufloConfigEntry) -> None:
    agent = entry.runtime_data
    doors_now = agent.intercom.options if agent.intercom is not None else {}
    if relinked(agent, entry.data) or dict(entry.options.get(CONF_INTERCOM) or {}) != doors_now:
        hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    entry.runtime_data.status_now()
    if (own := entry.runtime_data.ownership) is not None:
        own.schedule(1)

def remerge_on_start(hass: HomeAssistant, entry: HaufloConfigEntry, agent: Agent) -> None:
    """Once HA has started: the dashboards another version merged last are merged again by this one."""

    async def _remerge() -> None:
        if not agent.dashboards.outdated():
            return
        try:
            res = await agent.dashboards.remerge(lambda cmds: dashmerge_ws.local_ws(agent.local_session, f'{agent.local_base}/api/websocket', agent.token, cmds), agent.locked)
        except Exception as err:
            _LOGGER.warning('Hauflo: dashboards not merged again after the update: %s', err)
            return
        _LOGGER.info('Hauflo: dashboards merged again by %s: %s', VERSION, {u: r.get('outcome') for u, r in res.items()})
        if res and all((r.get('outcome') in ('saved', 'same') for r in res.values())):
            await agent.panel.remerged(any((r.get('outcome') == 'saved' for r in res.values())))
            agent.status_now()

    @callback
    def _remerge_start(_hass: HomeAssistant) -> None:
        entry.async_create_background_task(hass, _remerge(), 'hauflo dashboards merged again')
    entry.async_on_unload(async_at_started(hass, _remerge_start))
