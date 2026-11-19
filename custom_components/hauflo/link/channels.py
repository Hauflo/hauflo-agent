"""Channels the cloud opens to this HA and what each frame may do."""
from __future__ import annotations
import contextlib
import json
import logging
from typing import Any
import aiohttp
from homeassistant.helpers import instance_id
from .. import backup, panel as panel_mod, ranges, servers
from ..const import CONF_ALLOW_CHANGES, CONF_RANGES, LOCKED_CODE
from ..dashmerge import const as dashmerge_const, store as dashmerge_store, ws as dashmerge_ws
from ..foreign import wizard as foreign_wizard
from ..guard import contained
from ..notify import levels as notify_levels
from ..policy.const import CREATES, NEVER_EXPOSE, PROTECT_BEFORE, SCRUBBED, entry_owned_only, max_channels
from ..policy.dashboards import lovelace_prune, lovelace_refusal
from ..policy.http import http_allowed
from ..policy.knx import ga_range, ga_text, knx_store, knx_writes, owned_knx, range_allows
from ..policy.marks import own_device, protect_refresh
from ..policy.own import own_entity, platform
from ..policy.scrub import alice_creds_to_ha, scrub_config, scrub_diagnostics
from ..policy.ws import http_changes, http_locked, ws_allowed, ws_changes, ws_locked
from ..tuning import tuning
from .common import connect_timeout, good_hosts
_LOGGER = logging.getLogger(__name__)

class AgentChannelsMixin:
    """Opens and serves the cloud's channels."""

    async def cloud_ice(self) -> Any:
        it = tuning()['intercom']

        async def ask(host: str) -> Any:
            async with self.session.get(f"{host}{it['cloud_ice_path']}", headers={'Authorization': f'Bearer {self.secret}'}, timeout=aiohttp.ClientTimeout(total=it['cloud_ice_timeout_s'], sock_connect=connect_timeout())) as res:
                if res.status != 200:
                    raise ValueError(f'status {res.status}')
                return await res.json()
        return (await servers.first_answer(servers.same_region(self.server, (good := good_hosts(self.hass))), ask, good))[1]

    async def _guest(self, f: dict[str, Any]) -> None:
        status, body = (503, {'code': 'not_ready'})
        if (g := self.guest) is not None:
            try:
                await g.async_load()
                status, body = await g.handle(f.get('body'))
            except Exception:
                _LOGGER.exception('Hauflo guest: request failed')
                status, body = (500, {'code': 'failed'})
        await self.send({'t': 'guest_res', 'id': f.get('id'), 'status': status, 'body': json.dumps(body)})

    async def _open_channel(self, ch: int) -> None:
        if len(self.channels) >= max_channels() or ch in self.channels:
            await self.send({'t': 'ws_closed', 'ch': ch})
            return
        try:
            local = await self.local_session.ws_connect(f'{self.local_base}/api/websocket', max_msg_size=0)
        except aiohttp.ClientError:
            await self.send({'t': 'ws_closed', 'ch': ch})
            return
        self.channels[ch] = local
        try:
            async for msg in local:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = self._track(ch, msg.data) if self._pending or self._note_subs else msg.data
                    await self.send({'t': 'ws_msg', 'ch': ch, 'data': data})
        finally:
            for key in [k for k in self._pending if k[0] == ch]:
                del self._pending[key]
            self._note_subs = {k for k in self._note_subs if k[0] != ch}
            if self.channels.pop(ch, None) is not None:
                await self.send({'t': 'ws_closed', 'ch': ch})

    async def _instance(self) -> str | None:
        with contained(_LOGGER, "Hauflo: this HA's instance id not read — no takeover check"):
            return await instance_id.async_get(self.hass)
        return None

    def _rules_hidden(self) -> set[str]:
        with contained(_LOGGER, 'Hauflo: entities hidden by the rules not listed'):
            return self.rules.cloud_hidden_entities() if self.rules is not None else set()
        return set()

    def state_visible(self, eid: str) -> bool:
        """A presence sensor's state goes to Hauflo only when it is Hauflo's own or marked «Hauflo мониторинг»."""
        own = own_entity(self.hass, eid, self.owned_entries) and (platform(self.hass, eid) != 'knx' or owned_knx(self.hass, eid, self.owned))
        return own or notify_levels.labelled(self.hass, eid)

    @property
    def locked(self) -> bool:
        return self.entry.options.get(CONF_ALLOW_CHANGES, True) is not True

    def allowed_ranges(self) -> set[str]:
        """Main/middle groups Hauflo may send to: what the integrator allowed in HA, and — for sites paired before this existed — the groups of the entities Hauflo already made."""
        allowed = {str(r) for r in self.entry.options.get(CONF_RANGES) or []}
        if CONF_RANGES not in self.entry.options:
            for configs in (knx_store(self.hass) or {}).values():
                for uid, config in configs.items():
                    if uid in self.owned:
                        allowed |= {r for r in (ga_range(k) for k in knx_writes(config.get('knx') if isinstance(config, dict) else None)) if r}
        return allowed

    def _outside_ranges(self, msg: dict[str, Any]) -> set[str]:
        if msg.get('type') not in ('knx/create_entity', 'knx/update_entity'):
            return set()
        data = msg.get('data')
        writes = knx_writes(data.get('knx') if isinstance(data, dict) else None) - {'?'}
        allowed = self.allowed_ranges()
        return {t for k in writes if (t := ga_text(k)) and (not range_allows(allowed, k))}

    def _ask_ranges(self, items: set[str]) -> None:
        ranges.ask(self.hass, items)

    def _foreign_write(self, msg: dict[str, Any]) -> bool:
        t = msg.get('type')
        if t == 'config/device_registry/update':
            own = own_device(self.hass, msg.get('device_id'))
        elif t == 'config/entity_registry/update':
            own = own_entity(self.hass, msg.get('entity_id'), self.owned_entries)
        else:
            return False
        return not own and foreign_wizard.wizard_refusal(self.hass, msg) is None

    def _foreign_reason(self, msg: dict[str, Any]) -> str | None:
        t = msg.get('type')
        if t == 'config/entity_registry/update':
            eid = msg.get('entity_id')
            if isinstance(eid, str) and eid.startswith(NEVER_EXPOSE):
                return 'protected_domain'
            if own_entity(self.hass, eid, self.owned_entries):
                return None
        elif t == 'config/device_registry/update':
            if own_device(self.hass, msg.get('device_id')):
                return None
        else:
            return None
        return foreign_wizard.wizard_refusal(self.hass, msg) or 'not_allowed'

    def _dashboard_why(self, config: Any) -> str | None:
        return lovelace_refusal(self.hass, config, self.owned, self.owned_entries)

    def _dashboard_prune(self, config: Any) -> tuple[Any, list[dict[str, Any]]]:
        return lovelace_prune(self.hass, config, self.owned, self.owned_entries)

    async def _channel_send(self, ch: int, data: str) -> None:
        local = self.channels.get(ch)
        if local is None:
            return
        try:
            msg = json.loads(data)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if msg.get('type') != 'auth':
            mid = msg.get('id')
            if type(mid) is not int or (ch, mid) in self._pending or (ch, mid) in self._note_subs:
                _LOGGER.warning('Hauflo: a tunnel message with a reused or bad id was refused')
                await self.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps({'id': mid if type(mid) is int else None, 'type': 'result', 'success': False, 'error': {'code': 'id_reuse', 'message': 'Hauflo agent: message id reused or not a number'}})})
                return
        if str(msg.get('type')).startswith(PROTECT_BEFORE):
            protect_refresh(self.hass)
        if msg.get('type') == 'auth':
            data = json.dumps({'type': 'auth', 'access_token': self.token})
        elif msg.get('type') == dashmerge_const.PLAN or (msg.get('type') == 'lovelace/config' and self.dashboards.manages(msg.get('url_path')) and (not dashmerge_store.is_copy(msg.get('url_path')))):
            await dashmerge_ws.handle(self, ch, msg, self._dashboard_why, lambda: backup.fresh(self.hass), LOCKED_CODE, self._dashboard_prune)
            return
        elif msg.get('type') == panel_mod.STATE:
            await panel_mod.handle(self, ch, msg)
            return
        elif self.locked and ws_locked(msg):
            _LOGGER.info('Hauflo: «%s» refused — changes by Hauflo are switched off in HA', msg.get('type'))
            await self.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps({'id': msg.get('id'), 'type': 'result', 'success': False, 'error': {'code': LOCKED_CODE, 'message': 'Hauflo agent: changes by Hauflo are switched off in Home Assistant (Hauflo → Configure)'}})})
            return
        elif (outside := self._outside_ranges(msg)):
            self._ask_ranges(outside)
            await self.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps({'id': msg.get('id'), 'type': 'result', 'success': False, 'error': {'code': 'address_not_allowed', 'message': f"Hauflo agent: group addresses {', '.join(sorted(outside))} are not allowed in HA (Hauflo → Configure)"}})})
            return
        elif msg.get('type') == dashmerge_const.SAVE or (msg.get('type') == 'lovelace/config/save' and str(msg.get('url_path') or '').startswith('hauflo-')):
            await dashmerge_ws.handle(self, ch, msg, self._dashboard_why, lambda: backup.fresh(self.hass), LOCKED_CODE, self._dashboard_prune)
            return
        elif not ws_allowed(msg, self.hass, self.owned, self.owned_entries):
            why = self._foreign_reason(msg)
            _LOGGER.warning("Hauflo: «%s» isn't allowed through the tunnel%s", msg.get('type'), f' ({why})' if why else '')
            await self.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps({'id': msg.get('id'), 'type': 'result', 'success': False, 'error': {'code': 'not_allowed', 'message': f'Hauflo agent: not allowed ({why})' if why else 'Hauflo agent: not allowed'}})})
            return
        if ws_changes(msg) and (not backup.fresh(self.hass)):
            await self.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps({'id': msg.get('id'), 'type': 'result', 'success': False, 'error': {'code': 'backup_required', 'message': 'Hauflo agent: a backup first'}})})
            return
        if self._foreign_write(msg):
            sent, created = foreign_wizard.wizard_prepare(self.hass, msg)
            if created:
                self.owned_entries.update((f'label:{lid}' for lid in created))
                self._owned_store.async_delay_save(self._owned_data, 0)
            if sent is None:
                await self.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps({'id': msg.get('id'), 'type': 'result', 'success': False, 'error': {'code': 'not_allowed', 'message': 'Hauflo agent: not allowed (label_conflict)'}})})
                return
            msg, data = (sent, json.dumps(sent))
        if msg.get('type') == 'config_entries/get' and 'domain' not in msg:
            self._pending[ch, msg.get('id')] = ('config_entries/get:all', None)
        if msg.get('type') in SCRUBBED:
            self._pending[ch, msg.get('id')] = (str(msg['type']), None)
        if msg.get('type') == 'config_entries/get' and msg.get('domain') in entry_owned_only():
            self._pending[ch, msg.get('id')] = ('config_entries/get', None)
        if msg.get('type') == 'persistent_notification/subscribe':
            self._note_subs.add((ch, msg.get('id')))
        if msg.get('type') in CREATES:
            self._pending[ch, msg.get('id')] = (str(msg['type']), None)
        if (t := msg.get('type')) in ('knx/create_entity', 'knx/update_entity', 'knx/delete_entity'):
            ref = self._knx_uid(msg.get('entity_id')) if t == 'knx/delete_entity' else msg.get('entity_id')
            self._pending[ch, msg.get('id')] = (t, ref)
        if msg.get('type') == 'config/entity_registry/update':
            self._pending[ch, msg.get('id')] = ('config/entity_registry/update', (msg.get('entity_id'), 'name' in msg))
        await local.send_str(data)

    async def _close_channel(self, ch: int, tell: bool) -> None:
        local = self.channels.pop(ch, None)
        if local is not None:
            await local.close()
        if tell:
            await self.send({'t': 'ws_closed', 'ch': ch})

    async def _http(self, f: dict[str, Any]) -> None:
        path = str(f.get('path', ''))
        if self.locked and http_locked(str(f.get('method', 'GET')).upper(), path, f.get('body')):
            _LOGGER.info('Hauflo: %s %s refused — changes by Hauflo are switched off in HA', f.get('method'), path.split('?')[0])
            await self.send({'t': 'http_res', 'id': f.get('id'), 'status': 423, 'body': json.dumps({'code': LOCKED_CODE})})
            return
        if not http_allowed(self.hass, str(f.get('method', 'GET')).upper(), path, f.get('body'), self.owned_entries, self._flow_memo, self.owned):
            _LOGGER.warning("Hauflo: %s %s isn't allowed through the tunnel", f.get('method'), path.split('?')[0])
            await self.send({'t': 'http_res', 'id': f.get('id'), 'status': 403, 'body': 'Hauflo agent: not allowed'})
            return
        if http_changes(str(f.get('method', 'GET')).upper(), path) and (not backup.fresh(self.hass)):
            await self.send({'t': 'http_res', 'id': f.get('id'), 'status': 428, 'body': json.dumps({'code': 'backup_required'})})
            return
        try:
            async with self.local_session.request(f.get('method', 'GET'), f'{self.local_base}{path}', data=f.get('body'), headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'}, timeout=aiohttp.ClientTimeout(total=60)) as res:
                text = await res.text()
                if res.status == 200 and path.startswith('/api/config/config_entries/flow/'):
                    self._track_entry(text)
                    text = alice_creds_to_ha(self.hass, text)
                if res.status == 200 and path.split('?')[0].startswith('/api/diagnostics/'):
                    text = scrub_diagnostics(text)
                if res.status == 200 and path.split('?')[0] == '/api/config':
                    with contextlib.suppress(ValueError):
                        text = json.dumps(scrub_config(json.loads(text)))
                await self.send({'t': 'http_res', 'id': f['id'], 'status': res.status, 'body': text})
        except (aiohttp.ClientError, TimeoutError) as err:
            await self.send({'t': 'http_res', 'id': f['id'], 'status': 502, 'body': str(err)})
