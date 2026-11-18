"""Hauflo agent setup: the integrator types the pairing code from Hauflo (project → «Агент Hauflo»)."""
from __future__ import annotations
import functools
import ipaddress
from typing import Any
from urllib.parse import urlparse
import aiohttp
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigEntryState, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig
import voluptuous as vol
from . import ranges, servers
from .const import CONF_CODE, CONF_CONFIRM_TAKEOVER, CONF_RANGES, CONF_SECRET, CONF_SERVER, DATA_UNLINKED, DOMAIN
from .entry import hooks as entry_hooks
from .link.common import good_hosts
from .options_flow import HaufloOptionsFlow
from .preload import async_preload
from .tuning import tuning
connect_timeout = functools.cache(lambda: tuning()['link']['connect_timeout_s'])
pair_timeout = functools.cache(lambda: tuning()['link']['pair_timeout_s'])
ASKS_CONFIRM = ('takeover_confirm', 'relink_confirm')

def _secure(server: str) -> bool:
    u = urlparse(server)
    if u.scheme == 'https':
        return True
    if u.scheme != 'http' or not u.hostname:
        return False
    host = u.hostname
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return host == 'localhost' or '.' not in host

def _pair_code(typed: str) -> str:
    chars = ''.join((ch for ch in typed.upper() if ch.isascii() and ch.isalnum()))
    if len(chars) == 9:
        return f'{chars[:3]}-{chars[3:6]}-{chars[6:]}'
    if len(chars) == 11 and chars[:2].isalpha():
        return f'{chars[:2]}-{chars[2:5]}-{chars[5:8]}-{chars[8:]}'
    return typed.strip().upper()

class HaufloConfigFlow(ConfigFlow, domain=DOMAIN):
    """One Hauflo agent per Home Assistant."""
    VERSION = 1
    _takeover = False
    _relink_project = ''
    _typed_code = ''
    _transfer_company = ''
    _transfer: tuple[str, str, str | None] | None = None

    @staticmethod
    def _server_for(code: str, typed: str) -> str:
        region = code.split('-', 1)[0]
        typed = typed.strip().rstrip('/')
        if (typed == '' or servers.ours(typed)) and region in servers.region_servers():
            return servers.region_servers()[region]
        return typed

    @staticmethod
    def _plan_b(code: str, server: str, errors: dict[str, str]) -> bool:
        return server == servers.default_server() and code.split('-', 1)[0] not in servers.region_servers() and bool(errors) and (CONF_SERVER not in errors)

    async def _pair(self, server: str, code: str, previous: str | None=None, confirm: bool=False, transfer: bool=False) -> tuple[dict[str, Any] | None, dict[str, str]]:
        if not _secure(server):
            return (None, {CONF_SERVER: 'https_required'})
        session = async_get_clientsession(self.hass)
        payload = {'code': code, **({'previous': previous} if previous else {}), **({'confirmTakeover': True} if confirm else {}), **({'confirmTransfer': True} if transfer else {})}

        async def at(host: str) -> tuple[dict[str, Any] | None, dict[str, str]]:
            timeout = aiohttp.ClientTimeout(total=pair_timeout(), sock_connect=connect_timeout())
            async with session.post(f'{host}/api/agent/pair', json=payload, timeout=timeout) as res:
                return await self._pair_answer(res)
        try:
            _host, answer = await servers.first_answer(servers.same_region(server, (good := good_hosts(self.hass))), at, good)
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
            return (None, {'base': 'cannot_connect'})
        return answer

    async def _pair_answer(self, res: aiohttp.ClientResponse) -> tuple[dict[str, Any] | None, dict[str, str]]:
        if res.status == 404:
            return (None, {CONF_CODE: 'invalid_code'})
        if res.status == 409:
            try:
                why = await res.json(content_type=None)
            except ValueError:
                why = None
            code_ = why.get('code') if isinstance(why, dict) else None
            if code_ in ('pair_transfer', 'pair_transfer_recorded'):
                self._transfer_company = str(why.get('company') or '')[:120]
            if code_ == 'pair_transfer':
                return (None, {'base': 'transfer_ask'})
            if code_ == 'pair_transfer_recorded':
                return (None, {'base': 'transfer_recorded'})
            if code_ == 'pair_transfer_request':
                return (None, {'base': 'transfer_via_request'})
            if code_ == 'pair_relink':
                self._relink_project = str(why.get('project') or '')[:120]
                return (None, {'base': 'relink_confirm'})
            return (None, {'base': 'takeover_confirm'})
        if res.status >= 400:
            return (None, {'base': 'cannot_connect'})
        body = await res.json(content_type=None)
        if not isinstance(body, dict) or not body.get('secret'):
            return (None, {'base': 'cannot_connect'})
        return (body, {})

    def _asked(self) -> dict[str, str]:
        return {'project': self._relink_project or '—'}

    def _schema(self, server: str) -> vol.Schema:
        extra: dict[Any, Any] = {}
        if self._takeover:
            extra[vol.Optional(CONF_CONFIRM_TAKEOVER, default=False)] = bool
        shown = '' if servers.ours(server) else server
        return vol.Schema({vol.Required(CONF_CODE, **{'default': self._typed_code} if self._typed_code else {}): str, **extra, vol.Optional('advanced'): section(vol.Schema({vol.Optional(CONF_SERVER, default=shown): str}), {'collapsed': True})})

    async def _code_step(self, step_id: str, user_input: dict[str, Any] | None, server: str, entry: Any=None) -> tuple[dict[str, Any] | None, str, dict[str, str]]:
        await async_preload(self.hass)
        errors: dict[str, str] = {}
        if user_input is not None:
            code = _pair_code(user_input[CONF_CODE])
            confirm = user_input.get(CONF_CONFIRM_TAKEOVER) is True
            self._typed_code = user_input[CONF_CODE]
            typed = (user_input.get('advanced') or {}).get(CONF_SERVER, server)
            typed = str(typed or '').strip().rstrip('/')
            server = self._server_for(code, typed) or server
            previous = entry.data.get(CONF_SECRET) if entry is not None and entry.data.get(CONF_SERVER) == server else None
            body, errors = await self._pair(server, code, previous, confirm)
            self._takeover = errors.get('base') in ASKS_CONFIRM
            if errors.get('base') == 'transfer_ask':
                self._transfer = (server, code, previous)
            if body is None and self._plan_b(code, server, errors):
                prev_b = entry.data.get(CONF_SECRET) if entry is not None and entry.data.get(CONF_SERVER) == servers.pair_fallback_server() else None
                body_b, errors_b = await self._pair(servers.pair_fallback_server(), code, prev_b, confirm)
                if body_b is not None:
                    return (body_b, servers.pair_fallback_server(), {})
                if errors_b.get(CONF_CODE) or errors_b.get('base') in (*ASKS_CONFIRM, 'transfer_ask', 'transfer_recorded', 'transfer_via_request'):
                    errors = errors_b
                    self._takeover = errors.get('base') in ASKS_CONFIRM
                    if errors.get('base') == 'transfer_ask':
                        self._transfer = (servers.pair_fallback_server(), code, prev_b)
            if body is not None:
                return (body, server, {})
        return (None, server, errors)

    async def async_step_user(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        body, server, errors = await self._code_step('user', user_input, servers.default_server())
        if body is not None:
            self._paired = (body, server)
            return await self.async_step_ranges()
        return self.async_show_form(step_id='user', data_schema=self._schema(server), description_placeholders=self._asked(), errors=errors)

    async def async_step_ranges(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        """Which group addresses Hauflo may send to — confirmed here, in the house, not by the cloud (narrow round"""
        body, server = self._paired
        proposed = ', '.join(ranges.parse_ranges(body.get('addresses') or body.get('ranges')))
        if user_input is not None:
            typed = ranges.parse_ranges(user_input.get(CONF_RANGES, ''))
            return self.async_create_entry(title=f"Hauflo — {body.get('project', '')}", data={CONF_SERVER: server, CONF_SECRET: body['secret']}, options={CONF_RANGES: typed})
        return self.async_show_form(step_id='ranges', data_schema=vol.Schema({vol.Optional(CONF_RANGES, default=proposed): TextSelector(TextSelectorConfig(multiline=True))}), description_placeholders={'project': str(body.get('project', '')), 'ranges': ranges.describe(proposed) or '—'})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HaufloOptionsFlow()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        """Linked to another project, or again after an unlink: a new code updates this entry (its HA user stays)."""
        entry = self._get_reconfigure_entry()
        body, server, errors = await self._code_step('reconfigure', user_input, entry.data.get(CONF_SERVER, servers.default_server()), entry)
        if body is not None:
            return await self._relinked(entry, body, server)
        if errors.get('base') == 'transfer_ask':
            return await self.async_step_transfer()
        if (done := self._transfer_end(errors)) is not None:
            return done
        return self.async_show_form(step_id='reconfigure', data_schema=self._schema(server), description_placeholders=self._asked(), errors=errors)

    async def _relinked(self, entry: ConfigEntry, body: dict[str, Any], server: str) -> ConfigFlowResult:
        self._paired = (body, server)
        self._entry = entry
        if not ranges.parse_ranges(body.get('addresses') or body.get('ranges')):
            return self._finish_relink(None)
        return await self.async_step_relink_ranges()

    def _finish_relink(self, allowed: list[str] | None) -> ConfigFlowResult:
        body, server = self._paired
        self.hass.data.pop(DATA_UNLINKED, None)
        if allowed is not None:
            ranges.reset(self.hass)
        entry = self._entry
        result = self.async_update_and_abort(entry, title=f"Hauflo — {body.get('project', '')}", data_updates={CONF_SERVER: server, CONF_SECRET: body['secret']}, **{'options': {**entry.options, CONF_RANGES: allowed}} if allowed is not None else {})
        agent = getattr(entry, 'runtime_data', None) if entry.state is ConfigEntryState.LOADED else None
        if agent is None or not entry.update_listeners or (not entry_hooks.relinked(agent, entry.data)):
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return result

    async def async_step_relink_ranges(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        """The new project's addresses, prefilled, and what goes of the old allowed list — said, never kept silently."""
        body, _server = self._paired
        proposed = ranges.parse_ranges(body.get('addresses') or body.get('ranges'))
        if user_input is not None:
            return self._finish_relink(ranges.parse_ranges(user_input.get(CONF_RANGES, '')))
        old = [str(r) for r in self._entry.options.get(CONF_RANGES) or []]
        want = ranges.expand(proposed)
        removed = [r for r in ranges.parse_ranges(old) if not ranges.expand([r]) & want]
        text = ', '.join(proposed)
        return self.async_show_form(step_id='relink_ranges', data_schema=vol.Schema({vol.Optional(CONF_RANGES, default=text): TextSelector(TextSelectorConfig(multiline=True))}), description_placeholders={'project': str(body.get('project', '')), 'ranges': ranges.describe(proposed) or '—', 'removed': ranges.describe(removed) or '—'})

    def _transfer_end(self, errors: dict[str, str]) -> ConfigFlowResult | None:
        if errors.get('base') == 'transfer_recorded':
            return self.async_abort(reason='transfer_recorded', description_placeholders={'company': self._transfer_company or '—'})
        if errors.get('base') == 'transfer_via_request':
            return self.async_abort(reason='transfer_via_request')
        return None

    async def async_step_transfer(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        """        is only evidence on the request — Hauflo support decides after telling the previous company; this HA keeps its
        link until then (_transfer_end)."""
        return self.async_show_menu(step_id='transfer', menu_options=['transfer_yes', 'transfer_no'], description_placeholders={'company': self._transfer_company or '—'})

    async def async_step_transfer_yes(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        reauth = self.source == SOURCE_REAUTH
        entry = self._get_reauth_entry() if reauth else self._get_reconfigure_entry()
        if self._transfer is None:
            return self.async_abort(reason='transfer_declined')
        server, code, previous = self._transfer
        body, errors = await self._pair(server, code, previous, confirm=True, transfer=True)
        if body is not None:
            return await self._relinked(entry, body, server)
        if (done := self._transfer_end(errors)) is not None:
            return done
        self._takeover = False
        return self.async_show_form(step_id='reauth_confirm' if reauth else 'reconfigure', data_schema=self._schema(server), description_placeholders=self._asked(), errors=errors or {'base': 'cannot_connect'})

    async def async_step_transfer_no(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        return self.async_abort(reason='transfer_declined')

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Hauflo no longer knows this agent (unlinked, project deleted): HA's own «re-configure» with a new code."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        body, server, errors = await self._code_step('reauth_confirm', user_input, entry.data.get(CONF_SERVER, servers.default_server()), entry)
        if body is not None:
            return await self._relinked(entry, body, server)
        if errors.get('base') == 'transfer_ask':
            return await self.async_step_transfer()
        if (done := self._transfer_end(errors)) is not None:
            return done
        return self.async_show_form(step_id='reauth_confirm', data_schema=self._schema(server), description_placeholders=self._asked(), errors=errors)
