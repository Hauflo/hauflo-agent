"""The status the agent sends every minute."""
from __future__ import annotations
import asyncio
import logging
from typing import Any
from urllib.parse import urlparse
import aiohttp
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import Event, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from .. import alarms_now, issues, panel as panel_mod
from ..const import DOMAIN, STATUS_EVERY, VERSION
from ..install import installer as install_installer
from ..policy.marks import protect_refresh
from ..runtime import analysis_panel
from ..signals import store as signals_store
from .common import FEATURES, HA_UP
_LOGGER = logging.getLogger(__name__)

class AgentStatusMixin:
    """Sends the status."""

    def status(self) -> dict[str, Any]:
        issues.sync_degraded(self.hass)
        reg = er.async_get(self.hass)
        knx_ids = [e.entity_id for e in reg.entities.values() if e.platform == 'knx' and (not e.disabled_by)]
        unavailable = [i for i in knx_ids if (s := self.hass.states.get(i)) is not None and s.state == 'unavailable']
        mon = self.monitored()
        monitored = [{k: v for k, v in m.items() if v is not None and v is not False or k == 'problem'} for m in mon]
        return {'haVersion': HA_VERSION, 'haStarted': HA_UP, 'degraded': issues.degraded(self.hass), **({'dashboardsRemerged': {'agent': VERSION, 'at': p['at']}} if (p := self.panel.data) and p.get('reason') == panel_mod.REMERGED and (p.get('agent') == VERSION) and p.get('at') else {}), 'agentVersion': VERSION, 'features': FEATURES, 'signalsHash': signals_store.current_hash(self.hass), 'knx': self._knx(), 'unavailable': {'count': len(unavailable), 'sample': unavailable[:10]}, 'monitored': monitored, 'alarms': engine.active_list() if (engine := self.engine) else [], 'alarmsNow': self.alarms_now(self.house_now(mon)['_faults'], alarms_now.items_max()), 'alarmsReady': bool(engine and engine.ready), 'insights': ins.items() if (ins := self.insights) else [], 'insightChoices': ins.choices() if (ins := self.insights) else {}, 'integrations': wd.items() if (wd := self.watchdog) else {}, 'health': hl.items() if (hl := self.health) else {}, 'signalChoices': sig.choices() if (sig := signals_store.store(self.hass)) else {}, 'locked': self.locked, 'timeZone': str(self.hass.config.time_zone or ''), 'knxConn': self._knx_conn(), 'haUrl': self._ha_url(), 'haAdmins': list(self._admins), 'installs': install_installer.installer(self.hass).items()}

    @callback
    def _set_linked(self, on: bool) -> None:
        if self.linked != on:
            self.linked = on
            _LOGGER.info('Hauflo: link to %s %s', urlparse(self.server).hostname, 'restored' if on else 'lost — reconnecting')
            async_dispatcher_send(self.hass, f'{DOMAIN}_link')
            self._watch_link(on)

    @callback
    def status_now(self) -> None:
        """Status to Hauflo right away (alarms or rules changed) — when linked; otherwise the next link sends it."""
        analysis_panel(self.hass)
        if self.ws is not None and (not self.ws.closed):
            self._spawn(self.send({'t': 'status', 'status': self.status()}))
        else:
            self.house_now()

    async def _status_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while self.ws is ws and (not ws.closed):
            protect_refresh(self.hass)
            await self._refresh_admins()
            await self.send({'t': 'status', 'status': self.status()})
            self.watch_signals()
            await asyncio.sleep(STATUS_EVERY)

    @callback
    def watch_signals(self) -> None:
        """A monitored signal changes → status right away (an alarm shouldn't wait a minute)."""
        if self._unsub_signals:
            self._unsub_signals()
        ids = self._monitored_ids()
        self._unsub_signals = async_track_state_change_event(self.hass, ids, self._signal_changed) if ids else None

    @callback
    def _signal_changed(self, _event: Event) -> None:
        if self._status_soon:
            self._status_soon()
        self._status_soon = async_call_later(self.hass, 2, self._status_tick)

    @callback
    def _status_tick(self, _now: Any) -> None:
        self._status_soon = None
        self.status_now()

    @callback
    def rewatch_soon(self) -> None:
        if self._rewatch:
            self._rewatch()
        self._rewatch = async_call_later(self.hass, 2, self._rewatched)

    @callback
    def _rewatched(self, _now: Any) -> None:
        self._rewatch = None
        self.watch_signals()
        self.status_now()
