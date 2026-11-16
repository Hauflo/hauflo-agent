from __future__ import annotations
from typing import Any
from urllib.parse import urlparse
import aiohttp
import voluptuous as vol
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigEntryState, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.components import persistent_notification
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import BooleanSelector, NumberSelector, NumberSelectorConfig, NumberSelectorMode, ObjectSelector, ObjectSelectorConfig, SelectSelector, SelectSelectorConfig, SelectSelectorMode, TextSelector, TextSelectorConfig
from . import relinked as _relinked, servers, service
from . import ranges as ranges_mod
from .const import CONF_CARDS_VIEW_OLD, CONF_INDEPENDENT, CONF_INTERCOM, CONF_ACCEPT_PENDING, CONF_ALLOW_CHANGES, CONF_CODE, CONF_CONFIRM_TAKEOVER, CONF_RANGES, CONF_SECRET, CONF_SERVER, DATA_PENDING_RANGES, DATA_UNLINKED, DEFAULT_SERVER, DOMAIN, FALLBACK_SERVER, REGION_SERVERS
from .ranges import parse_ranges
from .ranges import range_key as _range_key
from .tuning import TUNING
CONNECT_TIMEOUT = TUNING['link']['connect_timeout_s']
PAIR_TIMEOUT = TUNING['link']['pair_timeout_s']

def _secure(server: str) -> bool:
    import ipaddress
    from urllib.parse import urlparse
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
    VERSION = 1
    _takeover = False
    _typed_code = ''
    _transfer_company = ''
    _transfer: tuple[str, str, str | None] | None = None

    @staticmethod
    def _server_for(code: str, typed: str) -> str:
        region = code.split('-', 1)[0]
        typed = typed.strip().rstrip('/')
        if (typed == '' or servers.ours(typed)) and region in REGION_SERVERS:
            return REGION_SERVERS[region]
        return typed

    @staticmethod
    def _plan_b(code: str, server: str, errors: dict[str, str]) -> bool:
        return server == DEFAULT_SERVER and code.split('-', 1)[0] not in REGION_SERVERS and bool(errors) and (CONF_SERVER not in errors)

    async def _pair(self, server: str, code: str, previous: str | None=None, confirm: bool=False, transfer: bool=False) -> tuple[dict[str, Any] | None, dict[str, str]]:
        if not _secure(server):
            return (None, {CONF_SERVER: 'https_required'})
        session = async_get_clientsession(self.hass)
        payload = {'code': code, **({'previous': previous} if previous else {}), **({'confirmTakeover': True} if confirm else {}), **({'confirmTransfer': True} if transfer else {})}

        async def at(host: str) -> tuple[dict[str, Any] | None, dict[str, str]]:
            timeout = aiohttp.ClientTimeout(total=PAIR_TIMEOUT, sock_connect=CONNECT_TIMEOUT)
            async with session.post(f'{host}/api/agent/pair', json=payload, timeout=timeout) as res:
                return await self._pair_answer(res)
        try:
            _host, answer = await servers.first_answer(servers.same_region(server), at)
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
            return (None, {'base': 'takeover_confirm'})
        if res.status >= 400:
            return (None, {'base': 'cannot_connect'})
        body = await res.json(content_type=None)
        if not isinstance(body, dict) or not body.get('secret'):
            return (None, {'base': 'cannot_connect'})
        return (body, {})

    def _schema(self, server: str) -> vol.Schema:
        extra: dict[Any, Any] = {}
        if self._takeover:
            extra[vol.Optional(CONF_CONFIRM_TAKEOVER, default=False)] = bool
        shown = '' if servers.ours(server) else server
        return vol.Schema({vol.Required(CONF_CODE, **{'default': self._typed_code} if self._typed_code else {}): str, **extra, vol.Optional('advanced'): section(vol.Schema({vol.Optional(CONF_SERVER, default=shown): str}), {'collapsed': True})})

    async def _code_step(self, step_id: str, user_input: dict[str, Any] | None, server: str, entry: Any=None) -> tuple[dict[str, Any] | None, str, dict[str, str]]:
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
            self._takeover = errors.get('base') == 'takeover_confirm'
            if errors.get('base') == 'transfer_ask':
                self._transfer = (server, code, previous)
            if body is None and self._plan_b(code, server, errors):
                prev_b = entry.data.get(CONF_SECRET) if entry is not None and entry.data.get(CONF_SERVER) == FALLBACK_SERVER else None
                body_b, errors_b = await self._pair(FALLBACK_SERVER, code, prev_b, confirm)
                if body_b is not None:
                    return (body_b, FALLBACK_SERVER, {})
                if errors_b.get(CONF_CODE) or errors_b.get('base') in ('takeover_confirm', 'transfer_ask', 'transfer_recorded', 'transfer_via_request'):
                    errors = errors_b
                    self._takeover = errors.get('base') == 'takeover_confirm'
                    if errors.get('base') == 'transfer_ask':
                        self._transfer = (FALLBACK_SERVER, code, prev_b)
            if body is not None:
                return (body, server, {})
        return (None, server, errors)

    async def async_step_user(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        body, server, errors = await self._code_step('user', user_input, DEFAULT_SERVER)
        if body is not None:
            self._paired = (body, server)
            return await self.async_step_ranges()
        return self.async_show_form(step_id='user', data_schema=self._schema(server), errors=errors)

    async def async_step_ranges(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        body, server = self._paired
        proposed = ', '.join(parse_ranges(body.get('addresses') or body.get('ranges')))
        if user_input is not None:
            ranges = parse_ranges(user_input.get(CONF_RANGES, ''))
            return self.async_create_entry(title=f"Hauflo — {body.get('project', '')}", data={CONF_SERVER: server, CONF_SECRET: body['secret']}, options={CONF_RANGES: ranges})
        return self.async_show_form(step_id='ranges', data_schema=vol.Schema({vol.Optional(CONF_RANGES, default=proposed): TextSelector(TextSelectorConfig(multiline=True))}), description_placeholders={'project': str(body.get('project', '')), 'ranges': ranges_mod.describe(proposed) or '—'})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HaufloOptionsFlow()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        body, server, errors = await self._code_step('reconfigure', user_input, entry.data.get(CONF_SERVER, DEFAULT_SERVER), entry)
        if body is not None:
            return await self._relinked(entry, body, server)
        if errors.get('base') == 'transfer_ask':
            return await self.async_step_transfer()
        if (done := self._transfer_end(errors)) is not None:
            return done
        return self.async_show_form(step_id='reconfigure', data_schema=self._schema(server), errors=errors)

    async def _relinked(self, entry: ConfigEntry, body: dict[str, Any], server: str) -> ConfigFlowResult:
        self._paired = (body, server)
        self._entry = entry
        if not parse_ranges(body.get('addresses') or body.get('ranges')):
            return self._finish_relink(None)
        return await self.async_step_relink_ranges()

    def _finish_relink(self, allowed: list[str] | None) -> ConfigFlowResult:
        body, server = self._paired
        self.hass.data.pop(DATA_UNLINKED, None)
        if allowed is not None:
            ranges_mod.reset(self.hass)
        entry = self._entry
        result = self.async_update_and_abort(entry, title=f"Hauflo — {body.get('project', '')}", data_updates={CONF_SERVER: server, CONF_SECRET: body['secret']}, **{'options': {**entry.options, CONF_RANGES: allowed}} if allowed is not None else {})
        agent = getattr(entry, 'runtime_data', None) if entry.state is ConfigEntryState.LOADED else None
        if agent is None or not entry.update_listeners or (not _relinked(agent, entry.data)):
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return result

    async def async_step_relink_ranges(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        body, _server = self._paired
        proposed = parse_ranges(body.get('addresses') or body.get('ranges'))
        if user_input is not None:
            return self._finish_relink(parse_ranges(user_input.get(CONF_RANGES, '')))
        old = [str(r) for r in self._entry.options.get(CONF_RANGES) or []]
        want = ranges_mod.expand(proposed)
        removed = [r for r in parse_ranges(old) if not ranges_mod.expand([r]) & want]
        text = ', '.join(proposed)
        return self.async_show_form(step_id='relink_ranges', data_schema=vol.Schema({vol.Optional(CONF_RANGES, default=text): TextSelector(TextSelectorConfig(multiline=True))}), description_placeholders={'project': str(body.get('project', '')), 'ranges': ranges_mod.describe(proposed) or '—', 'removed': ranges_mod.describe(removed) or '—'})

    def _transfer_end(self, errors: dict[str, str]) -> ConfigFlowResult | None:
        if errors.get('base') == 'transfer_recorded':
            return self.async_abort(reason='transfer_recorded', description_placeholders={'company': self._transfer_company or '—'})
        if errors.get('base') == 'transfer_via_request':
            return self.async_abort(reason='transfer_via_request')
        return None

    async def async_step_transfer(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
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
        return self.async_show_form(step_id='reauth_confirm' if reauth else 'reconfigure', data_schema=self._schema(server), errors=errors or {'base': 'cannot_connect'})

    async def async_step_transfer_no(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        return self.async_abort(reason='transfer_declined')

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        body, server, errors = await self._code_step('reauth_confirm', user_input, entry.data.get(CONF_SERVER, DEFAULT_SERVER), entry)
        if body is not None:
            return await self._relinked(entry, body, server)
        if errors.get('base') == 'transfer_ask':
            return await self.async_step_transfer()
        if (done := self._transfer_end(errors)) is not None:
            return done
        return self.async_show_form(step_id='reauth_confirm', data_schema=self._schema(server), errors=errors)

class HaufloOptionsFlow(OptionsFlow):
    _shown_pending: list[str] | None = None
    _saved: dict[str, Any] | None = None
    _report: str = ''
    _independent: bool = False

    async def async_step_init(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        if user_input is not None:
            shown_pending = self._shown_pending or []
            ticked = user_input.get(CONF_ACCEPT_PENDING) or []
            accepted = [r for r in ticked if r in shown_pending] if isinstance(ticked, list) else []
            ranges = parse_ranges(user_input.get(CONF_RANGES, ''))
            pending = self.hass.data.get(DATA_PENDING_RANGES)
            if isinstance(pending, set):
                pending -= set(shown_pending)
                ranges_mod.sync(self.hass)
            options = {**{k: v for k, v in self.config_entry.options.items() if k != CONF_CARDS_VIEW_OLD}, CONF_ALLOW_CHANGES: user_input.get(CONF_ALLOW_CHANGES) is not False, CONF_RANGES: parse_ranges(ranges + accepted)}
            self._saved = options
            self._independent = user_input.get(CONF_INDEPENDENT) is True
            if user_input.get(CONF_INTERCOM) is True:
                return await self.async_step_intercom()
            return await self._done()
        return await self._show_init()

    async def _done(self) -> ConfigFlowResult:
        if self._independent:
            return await self.async_step_independent()
        return self.async_create_entry(data=self._saved if self._saved is not None else dict(self.config_entry.options))

    async def async_step_intercom(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        from .intercom import door as door_mod, setup as setup_mod
        current = dict(self.config_entry.options.get(CONF_INTERCOM) or {})
        errors: dict[str, str] = {}
        placeholders = {'door': '', 'presets': ', '.join(dict.fromkeys((p['maker'] for p in door_mod.PRESETS.values() if p.get('maker'))))}
        if user_input is not None:
            doors = user_input.get('doors') or []
            doors = [d for d in (doors if isinstance(doors, list) else [doors]) if isinstance(d, dict)]
            try:
                value = setup_mod.normalize({**user_input, 'doors': doors}, current)
            except setup_mod.SetupError as err:
                errors[err.field] = f'intercom_{err.code}'
                placeholders['door'] = err.door
                value = {**current, **{k: user_input.get(k) for k in ('bind', 'port', 'public')}, 'doors': doors}
            else:
                self._saved = {**(self._saved or dict(self.config_entry.options)), CONF_INTERCOM: value}
                return await self._done()
            current = value
        binds = ['auto']
        try:
            from homeassistant.components import network
            for adapter in await network.async_get_adapters(self.hass):
                binds += [str(a['address']) for a in adapter['ipv4'] if str(a['address']) not in binds]
        except Exception:
            pass
        text = {'text': {}}
        secret = {'text': {'type': 'password'}}
        fields = {'name': {'required': True, 'selector': text}, 'area': {'selector': {'area': {}}}, 'preset': {'required': True, 'selector': {'select': {'options': list(door_mod.PRESETS), 'translation_key': 'intercom_preset', 'mode': 'dropdown'}}}, 'host': {'selector': text}, 'user': {'selector': text}, 'password': {'selector': secret}, 'mode': {'selector': {'select': {'options': list(door_mod.MODES), 'translation_key': 'intercom_mode', 'mode': 'dropdown'}}}, 'sip_server': {'selector': text}, 'sip_port': {'selector': {'number': {'min': 1, 'max': 65535, 'mode': 'box'}}}, 'sip_user': {'selector': text}, 'sip_password': {'selector': secret}, 'rtsp': {'selector': text}, 'open': {'selector': {'select': {'options': list(door_mod.OPENERS), 'translation_key': 'intercom_open', 'mode': 'dropdown'}}}, 'dtmf': {'selector': text}, 'relay': {'selector': {'number': {'min': 1, 'max': 16, 'mode': 'box'}}}, 'open_entity': {'selector': {'entity': {'domain': sorted(TUNING['intercom']['open_services'])}}}, 'codec': {'selector': {'select': {'options': ['PCMA', 'PCMU'], 'mode': 'dropdown'}}}}
        return self.async_show_form(step_id='intercom', data_schema=vol.Schema({vol.Required('bind', default=str(current.get('bind') or 'auto')): SelectSelector(SelectSelectorConfig(options=binds, custom_value=True, mode=SelectSelectorMode.DROPDOWN, translation_key='intercom_bind')), vol.Required('port', default=int(current.get('port') or TUNING['intercom']['sip_port'])): NumberSelector(NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)), vol.Optional('public', default=str(current.get('public') or '')): TextSelector(), vol.Optional('doors', default=list(current.get('doors') or [])): ObjectSelector(ObjectSelectorConfig(fields=fields, multiple=True, label_field='name', translation_key='intercom_doors'))}), errors=errors, description_placeholders=placeholders)

    async def _show_init(self) -> ConfigFlowResult:
        current = list(self.config_entry.options.get(CONF_RANGES) or [])
        pending = sorted(self.hass.data.get(DATA_PENDING_RANGES, set()), key=_range_key)
        self._shown_pending = pending
        shown = ', '.join(sorted(current, key=_range_key))
        extra: dict[Any, Any] = {}
        if pending:
            extra[vol.Optional(CONF_ACCEPT_PENDING, default=[])] = SelectSelector(SelectSelectorConfig(options=pending, multiple=True, mode=SelectSelectorMode.LIST))
        server = str(self.config_entry.data.get(CONF_SERVER) or '')
        who, history = service.describe(await service.async_load(self.hass), self.hass.config.language)
        title = self.config_entry.title or ''
        project = title.split(' — ', 1)[1] if title.startswith('Hauflo — ') else title
        return self.async_show_form(step_id='init_pending' if pending else 'init', data_schema=vol.Schema({vol.Required(CONF_ALLOW_CHANGES, default=self.config_entry.options.get(CONF_ALLOW_CHANGES, True) is True): BooleanSelector(), vol.Optional(CONF_RANGES, default=shown): TextSelector(TextSelectorConfig(multiline=True)), **extra, vol.Optional(CONF_INTERCOM, default=False): BooleanSelector(), vol.Optional(CONF_INDEPENDENT, default=False): BooleanSelector()}), description_placeholders={'pending': ranges_mod.describe(pending), 'project': project or '—', 'server': urlparse(server).hostname or server or '—', 'url': server, 'service': who, 'history': history})

    async def async_step_init_pending(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        return await self.async_step_init(user_input)

    async def async_step_independent(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id='independent', data_schema=vol.Schema({}))
        from . import independent
        agent = getattr(self.config_entry, 'runtime_data', None)
        engine = getattr(agent, 'engine', None)
        ru = str(self.hass.config.language or '').startswith('ru')
        result = await independent.async_export(self.hass, engine)
        self._report = independent.summary(result, ru)
        persistent_notification.async_create(self.hass, self._report, 'Hauflo', 'hauflo_independent')
        return await self.async_step_independent_done()

    async def async_step_independent_done(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id='independent_done', data_schema=vol.Schema({}), description_placeholders={'report': self._report})
        return self.async_create_entry(data=self._saved if self._saved is not None else dict(self.config_entry.options))
