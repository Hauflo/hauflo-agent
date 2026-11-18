"""The agent: connects, keeps the link and relays the cloud's frames."""
from __future__ import annotations
import asyncio
from collections.abc import Callable
import contextlib
import json
import logging
import random
import time
from typing import Any
from urllib.parse import urlparse
import aiohttp
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_call_later
from .. import combos, daylight, intercom as intercom_mod, issues, locks as locks_mod, ownership, panel as panel_mod, servers, service, watchdog
from ..const import CONF_SECRET, CONF_SERVER, DATA_UNLINKED, OWNED_STORE, VERSION
from ..dashmerge import store as dashmerge_store
from ..entry.const import PLATFORMS
from ..guard import contained
from ..guest import access as guest_access
from ..habits import engine as habits_engine
from ..health import runner as health
from ..insight import engine as insight_engine
from ..issues import degrading
from ..notify import engine as notify_engine
from ..policy.const import CREATES, SCRUBBED, tracked_domains
from ..policy.http import entry_domain
from ..policy.scrub import scrub_cloud, scrub_config, scrub_devices, scrub_entities, scrub_entries, scrub_monitor, scrub_notes, scrub_panels, scrub_states
from ..presence import engine as presence_engine
from ..rules import engine as rules_engine
from ..season import engine as season_engine
from ..signals import store as signals_store
from ..storage import HaufloStore
from .channels import AgentChannelsMixin
from .common import FEATURES, big_frame, connect_timeout, good_hosts, knx_round_gap, link_lost_issue_after, local_token, reconnect_hint_wait
from .house import AgentHouseMixin
from .status import AgentStatusMixin
_LOGGER = logging.getLogger(__name__)

def _next_wait(delay: float, hinted: bool, uniform: Callable[[float, float], float]=random.uniform) -> float:
    if hinted:
        return uniform(*reconnect_hint_wait())
    return delay * uniform(0.5, 1.5)

class Agent(AgentChannelsMixin, AgentHouseMixin, AgentStatusMixin):

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.server = entry.data[CONF_SERVER]
        self.secret = entry.data[CONF_SECRET]
        self.session = async_get_clientsession(hass, verify_ssl=True)
        self.local_session = async_get_clientsession(hass, verify_ssl=False)
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.channels: dict[int, aiohttp.ClientWebSocketResponse] = {}
        self.tasks: set[asyncio.Task] = set()
        self.refresh = None
        self.stopped = False
        self.engine: notify_engine.NotifyEngine | None = None
        self.automations: Any = None
        self.presence: presence_engine.Presence | None = None
        self.habits: habits_engine.Habits | None = None
        self.daylight: daylight.Daylight | None = None
        self.season: season_engine.Season | None = None
        self.rules: rules_engine.Rules | None = None
        self.combos: combos.Combos | None = None
        self.intercom: intercom_mod.Intercom | None = None
        self.platforms: list[str] = list(PLATFORMS)
        self.guest: guest_access.GuestAccess | None = None
        self.ownership: ownership.Ownership | None = None
        self.insights: insight_engine.Insights | None = None
        self.watchdog: watchdog.Watchdog | None = None
        self.health: health.Health | None = None
        self.locks: locks_mod.Locks | None = None
        self.owned: set[str] = set()
        self.owned_entries: set[str] = set()
        self._flow_memo: dict[str, list[str]] = {}
        self._owned_store: HaufloStore = HaufloStore(hass, OWNED_STORE)
        self.dashboards = dashmerge_store.DashboardMerge(hass)
        self.panel = panel_mod.Panel(hass)
        self._pending: dict[tuple[int, Any], tuple[str, Any]] = {}
        self._note_subs: set[tuple[int, Any]] = set()
        self._unsub_signals = None
        self._status_soon: Callable[[], None] | None = None
        self.house_state: dict[str, Any] | None = None
        self._unshelve_at: Any = None
        self._unshelve_timer: Callable[[], None] | None = None
        self._rewatch: Callable[[], None] | None = None
        self.linked = False
        self._lost_timer: Callable[[], None] | None = None
        self._knx_heard: float | None = None
        self._knx_since = time.monotonic()
        self._knx_asked: float | None = None
        self._knx_asked_n = 0
        self._knx_read_last: float | None = None
        self._admins: list[str] = []
        self._unsub_knx = None
        with degrading(hass, 'knx telegram signal', issues.HA):
            from homeassistant.components.knx.const import SIGNAL_KNX_TELEGRAM

            @callback
            def _heard(_telegram, telegram_dict) -> None:
                t = telegram_dict or {}
                direction = str(t.get('direction', '')).lower()
                now = time.monotonic()
                if direction.startswith('incoming'):
                    self._knx_heard = now
                    self._knx_asked = None
                    self._knx_asked_n = 0
                elif direction.startswith('outgoing') and str(t.get('telegramtype', '')) == 'GroupValueRead':
                    new_round = self._knx_read_last is None or now - self._knx_read_last > knx_round_gap()
                    self._knx_read_last = now
                    if self._knx_asked is not None:
                        self._knx_asked_n += 1
                    elif new_round:
                        self._knx_asked, self._knx_asked_n = (now, 1)
            self._unsub_knx = async_dispatcher_connect(hass, SIGNAL_KNX_TELEGRAM, _heard)
            issues.recover(hass, 'knx telegram signal')

    @property
    def local_base(self) -> str:
        api = self.hass.config.api
        scheme = 'https' if api and api.use_ssl else 'http'
        host = '127.0.0.1'
        if api and api.host and (api.host not in ('0.0.0.0', '::')):
            host = f'[{api.host}]' if ':' in api.host else api.host
        return f'{scheme}://{host}:{(api.port if api else 8123)}'

    def _spawn(self, coro) -> None:
        task = self.hass.async_create_background_task(coro, 'hauflo agent')
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    @property
    def token(self) -> str:
        """A fresh access token of the agent's user for one request / one tunnel login."""
        return self.hass.auth.async_create_access_token(self.refresh) if self.refresh else ''

    async def send(self, frame: dict[str, Any]) -> None:
        if self.ws and (not self.ws.closed):
            await self.ws.send_str(json.dumps(frame))

    async def run(self) -> None:
        self.refresh = await local_token(self.hass, self.entry)
        await self._ensure_label()
        delay = 5
        hosts = servers.same_region(self.server, good_hosts(self.hass))
        missed = 0
        self._watch_link(False)
        while not self.stopped:
            hinted = False
            welcomed = False
            host = hosts[missed % len(hosts)]
            try:
                async with contextlib.AsyncExitStack() as stack:
                    async with asyncio.timeout(connect_timeout()):
                        ws = await stack.enter_async_context(self.session.ws_connect(servers.ws_url(host), heartbeat=30, max_msg_size=16 * 1024 * 1024))
                    self.ws = ws
                    await self.send({'t': 'hello', 'token': self.secret, 'version': VERSION, 'haVersion': HA_VERSION, 'instance': await self._instance(), 'features': FEATURES, 'signalsHash': signals_store.current_hash(self.hass)})
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            frame = await self._parse(msg.data)
                        except ValueError:
                            _LOGGER.warning('Hauflo: unreadable frame dropped')
                            continue
                        if not isinstance(frame, dict):
                            continue
                        if frame.get('t') == 'reconnect':
                            hinted = True
                            continue
                        if frame.get('t') == 'welcome':
                            self._set_linked(True)
                            delay = 5
                            welcomed = True
                            if host != hosts[0]:
                                good_hosts(self.hass).remember(host)
                                hosts = servers.same_region(self.server, good_hosts(self.hass))
                            missed = 0
                            self.hass.data.pop(DATA_UNLINKED, None)
                            persistent_notification.async_dismiss(self.hass, 'hauflo_unlinked')
                            issues.clear_issue(self.hass, issues.UNLINKED)
                            issues.sync_version(self.hass, VERSION, frame.get('minVersion'), frame.get('latest'))
                            self._spawn(self._status_loop(ws))
                            if (engine := self.engine):
                                engine.on_change = self.status_now
                            continue
                        with contained(_LOGGER, 'Hauflo: frame %s failed', frame.get('t'), level=logging.ERROR):
                            await self._on_frame(frame)
                    if ws.close_code == 4003:
                        self.hass.data[DATA_UNLINKED] = True
                        issues.raise_issue(self.hass, issues.UNLINKED)
                        self.entry.async_start_reauth(self.hass)
                        delay = 600
                        await asyncio.sleep(delay)
                        continue
                    hinted = hinted or ws.close_code in (1001, 1012)
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                _LOGGER.debug('Hauflo link down: %s', err)
            finally:
                self.ws = None
                self._set_linked(False)
                for ch in list(self.channels):
                    await self._close_channel(ch, tell=False)
            if not self.stopped:
                if not welcomed:
                    missed += 1
                    if missed % len(hosts):
                        continue
                await asyncio.sleep(_next_wait(delay, hinted))
                delay = min(delay * 2, 60)

    async def _parse(self, data: str) -> Any:
        if len(data) > big_frame():
            return await self.hass.async_add_executor_job(json.loads, data)
        return json.loads(data)

    @callback
    def _watch_link(self, on: bool) -> None:
        if on:
            if self._lost_timer:
                self._lost_timer()
                self._lost_timer = None
            issues.clear_issue(self.hass, issues.LINK_LOST)
        elif self._lost_timer is None and (not self.stopped):
            self._lost_timer = async_call_later(self.hass, link_lost_issue_after(), self._link_lost)

    @callback
    def _link_lost(self, _now: Any) -> None:
        self._lost_timer = None
        if not self.linked and (not self.stopped) and (not self.hass.data.get(DATA_UNLINKED)):
            issues.raise_issue(self.hass, issues.LINK_LOST, server=str(urlparse(self.server).hostname), hours=str(link_lost_issue_after() // 3600))

    async def async_load(self) -> None:
        data = await self._owned_store.async_load()
        self.owned = set(data.get('unique_ids', [])) if isinstance(data, dict) else set()
        self.owned_entries = set(data.get('entries', [])) if isinstance(data, dict) else set()
        await self.dashboards.async_load()
        await self.panel.async_load()

    def _knx_uid(self, entity_id: Any) -> str | None:
        entry = er.async_get(self.hass).async_get(entity_id) if isinstance(entity_id, str) else None
        return entry.unique_id if entry is not None and entry.platform == 'knx' else None

    def _track_entry(self, text: str) -> None:
        try:
            res = json.loads(text)
        except ValueError:
            return
        entry_id = (res.get('result') or {}).get('entry_id') if isinstance(res, dict) and res.get('type') == 'create_entry' else None
        if isinstance(entry_id, str) and entry_domain(self.hass, entry_id) in tracked_domains():
            self.owned_entries.add(entry_id)
            self._owned_store.async_delay_save(self._owned_data, 0)
            self._owned_changed()

    def _owned_changed(self) -> None:
        if (own := self.ownership) is not None:
            own.schedule()

    def _owned_data(self) -> dict[str, Any]:
        return {'unique_ids': sorted(self.owned), 'entries': sorted(self.owned_entries)}

    def _track(self, ch: int, data: str) -> str:
        try:
            msg = json.loads(data)
        except ValueError:
            return data
        changed = scrubbed = False
        for r in msg if isinstance(msg, list) else [msg]:
            if isinstance(r, dict) and r.get('type') == 'event' and ((ch, r.get('id')) in self._note_subs):
                r['event'] = scrub_notes(r.get('event'), self.hass, self.owned_entries)
                scrubbed = True
                continue
            if not isinstance(r, dict) or r.get('type') != 'result' or (key := (ch, r.get('id'))) not in self._pending:
                continue
            kind, ref = self._pending.pop(key)
            res = r.get('result')
            if kind == 'config_entries/get:all':
                r['result'] = scrub_entries(res)
                scrubbed = True
                continue
            if kind == 'config_entries/get':
                r['result'] = [e for e in res if isinstance(e, dict) and e.get('entry_id') in self.owned_entries] if isinstance(res, list) else []
                scrubbed = True
                continue
            if kind in SCRUBBED:
                r['result'] = scrub_states(res, self.hass, self.state_visible, self._rules_hidden()) if kind == 'get_states' else scrub_config(res) if kind == 'get_config' else scrub_cloud(res) if kind == 'cloud/status' else scrub_devices(res, self.hass) if kind == 'config/device_registry/list' else scrub_panels(res) if kind == 'get_panels' else scrub_entities(res, self.hass) if kind.startswith('config/entity_registry/') else scrub_monitor(res)
                scrubbed = True
                continue
            if kind in CREATES:
                k = CREATES[kind]
                if r.get('success') and isinstance(res, dict) and isinstance(res.get(f'{k}_id'), str):
                    self.owned_entries.add(f"{k}:{res[f'{k}_id']}")
                    changed = True
                continue
            if kind == 'config/entity_registry/update':
                reg_entry = res.get('entity_entry') if r.get('success') and isinstance(res, dict) else None
                if isinstance(reg_entry, dict) and (own := self.ownership) is not None:
                    old_id, named = ref
                    own.remember_name(reg_entry.get('entity_id'), reg_entry.get('name'), old_id, named)
                continue
            if not r.get('success') or (kind != 'knx/delete_entity' and (not (isinstance(res, dict) and res.get('success')))):
                continue
            if kind == 'knx/update_entity':
                continue
            uid = ref if kind == 'knx/delete_entity' else self._knx_uid(res.get('entity_id'))
            if uid:
                (self.owned.discard if kind == 'knx/delete_entity' else self.owned.add)(uid)
                changed = True
        if changed:
            self._owned_store.async_delay_save(self._owned_data, 0)
            self._owned_changed()
        return json.dumps(msg) if scrubbed else data

    async def _on_frame(self, f: dict[str, Any]) -> None:
        t = f.get('t')
        if t == 'ws_open':
            self._spawn(self._open_channel(int(f['ch'])))
        elif t == 'ws_send':
            await self._channel_send(int(f['ch']), f['data'])
        elif t == 'ws_close':
            await self._close_channel(int(f['ch']), tell=False)
        elif t == 'http':
            self._spawn(self._http(f))
        elif t == 'guest':
            self._spawn(self._guest(f))
        elif t == 'service':
            await service.async_save(self.hass, f.get('info'))

    async def stop(self) -> None:
        if self._unsub_knx:
            self._unsub_knx()
            self._unsub_knx = None
        self.stopped = True
        if self._unsub_signals:
            self._unsub_signals()
            self._unsub_signals = None
        if self._status_soon:
            self._status_soon()
            self._status_soon = None
        if self._unshelve_timer:
            self._unshelve_timer()
            self._unshelve_timer = None
        if self._rewatch:
            self._rewatch()
            self._rewatch = None
        if self._lost_timer:
            self._lost_timer()
            self._lost_timer = None
        if self.ws:
            await self.ws.close()
        for task in list(self.tasks):
            task.cancel()
type HaufloConfigEntry = ConfigEntry[Agent]
