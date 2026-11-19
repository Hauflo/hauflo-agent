from __future__ import annotations
import logging
from typing import Any
from urllib.parse import urlparse
from homeassistant.components import network, persistent_notification
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import BooleanSelector, NumberSelector, NumberSelectorConfig, NumberSelectorMode, ObjectSelector, ObjectSelectorConfig, SelectSelector, SelectSelectorConfig, SelectSelectorMode, TextSelector, TextSelectorConfig
import voluptuous as vol
from . import ranges, service
from .const import CONF_ACCEPT_PENDING, CONF_ALLOW_CHANGES, CONF_CARDS_VIEW_OLD, CONF_INDEPENDENT, CONF_INTERCOM, CONF_RANGES, CONF_SERVER, DATA_PENDING_RANGES
from .guard import contained
from .independent import service as independent_service
from .intercom import door_config as intercom_door_config, setup as setup_mod
from .preload import async_preload
from .tuning import tuning
_LOGGER = logging.getLogger(__name__)

class HaufloOptionsFlow(OptionsFlow):
    _shown_pending: list[str] | None = None
    _saved: dict[str, Any] | None = None
    _report: str = ''
    _independent: bool = False

    async def async_step_init(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        await async_preload(self.hass)
        if user_input is not None:
            shown_pending = self._shown_pending or []
            ticked = user_input.get(CONF_ACCEPT_PENDING) or []
            accepted = [r for r in ticked if r in shown_pending] if isinstance(ticked, list) else []
            typed = ranges.parse_ranges(user_input.get(CONF_RANGES, ''))
            pending = self.hass.data.get(DATA_PENDING_RANGES)
            if isinstance(pending, set):
                pending -= set(shown_pending)
                ranges.sync(self.hass)
            options = {**{k: v for k, v in self.config_entry.options.items() if k != CONF_CARDS_VIEW_OLD}, CONF_ALLOW_CHANGES: user_input.get(CONF_ALLOW_CHANGES) is not False, CONF_RANGES: ranges.parse_ranges(typed + accepted)}
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
        """«Домофон»: the address the agent takes calls on (auto — HA's own LAN address; another network card / VLAN by hand), its port, the router's public address when a panel calls from outside, and the doors (a preset of intercom/presets.json + the panel's address and accounts)."""
        current = dict(self.config_entry.options.get(CONF_INTERCOM) or {})
        errors: dict[str, str] = {}
        placeholders = {'door': '', 'presets': ', '.join(dict.fromkeys((p['maker'] for p in intercom_door_config.presets_cfg().values() if p.get('maker'))))}
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
        with contained(_LOGGER, 'Hauflo: network adapters not listed', expect=(KeyError, HomeAssistantError)):
            for adapter in await network.async_get_adapters(self.hass):
                binds += [str(a['address']) for a in adapter['ipv4'] if str(a['address']) not in binds]
        text = {'text': {}}
        secret = {'text': {'type': 'password'}}
        fields = {'name': {'required': True, 'selector': text}, 'area': {'selector': {'area': {}}}, 'preset': {'required': True, 'selector': {'select': {'options': list(intercom_door_config.presets_cfg()), 'translation_key': 'intercom_preset', 'mode': 'dropdown'}}}, 'host': {'selector': text}, 'user': {'selector': text}, 'password': {'selector': secret}, 'mode': {'selector': {'select': {'options': list(intercom_door_config.MODES), 'translation_key': 'intercom_mode', 'mode': 'dropdown'}}}, 'sip_server': {'selector': text}, 'sip_port': {'selector': {'number': {'min': 1, 'max': 65535, 'mode': 'box'}}}, 'sip_user': {'selector': text}, 'sip_password': {'selector': secret}, 'rtsp': {'selector': text}, 'open': {'selector': {'select': {'options': list(intercom_door_config.OPENERS), 'translation_key': 'intercom_open', 'mode': 'dropdown'}}}, 'dtmf': {'selector': text}, 'relay': {'selector': {'number': {'min': 1, 'max': 16, 'mode': 'box'}}}, 'open_entity': {'selector': {'entity': {'domain': sorted(tuning()['intercom']['open_services'])}}}, 'codec': {'selector': {'select': {'options': ['PCMA', 'PCMU'], 'mode': 'dropdown'}}}}
        return self.async_show_form(step_id='intercom', data_schema=vol.Schema({vol.Required('bind', default=str(current.get('bind') or 'auto')): SelectSelector(SelectSelectorConfig(options=binds, custom_value=True, mode=SelectSelectorMode.DROPDOWN, translation_key='intercom_bind')), vol.Required('port', default=int(current.get('port') or tuning()['intercom']['sip_port'])): NumberSelector(NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)), vol.Optional('public', default=str(current.get('public') or '')): TextSelector(), vol.Optional('doors', default=list(current.get('doors') or [])): ObjectSelector(ObjectSelectorConfig(fields=fields, multiple=True, label_field='name', translation_key='intercom_doors'))}), errors=errors, description_placeholders=placeholders)

    async def _show_init(self) -> ConfigFlowResult:
        current = list(self.config_entry.options.get(CONF_RANGES) or [])
        pending = sorted(self.hass.data.get(DATA_PENDING_RANGES, set()), key=ranges.range_key)
        self._shown_pending = pending
        shown = ', '.join(sorted(current, key=ranges.range_key))
        extra: dict[Any, Any] = {}
        if pending:
            extra[vol.Optional(CONF_ACCEPT_PENDING, default=[])] = SelectSelector(SelectSelectorConfig(options=pending, multiple=True, mode=SelectSelectorMode.LIST))
        server = str(self.config_entry.data.get(CONF_SERVER) or '')
        who, history = service.describe(await service.async_load(self.hass), self.hass.config.language)
        title = self.config_entry.title or ''
        project = title.split(' — ', 1)[1] if title.startswith('Hauflo — ') else title
        return self.async_show_form(step_id='init_pending' if pending else 'init', data_schema=vol.Schema({vol.Required(CONF_ALLOW_CHANGES, default=self.config_entry.options.get(CONF_ALLOW_CHANGES, True) is True): BooleanSelector(), vol.Optional(CONF_RANGES, default=shown): TextSelector(TextSelectorConfig(multiline=True)), **extra, vol.Optional(CONF_INTERCOM, default=False): BooleanSelector(), vol.Optional(CONF_INDEPENDENT, default=False): BooleanSelector()}), description_placeholders={'pending': ranges.describe(pending), 'project': project or '—', 'server': urlparse(server).hostname or server or '—', 'url': server, 'service': who, 'history': history})

    async def async_step_init_pending(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        return await self.async_step_init(user_input)

    async def async_step_independent(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id='independent', data_schema=vol.Schema({}))
        agent = getattr(self.config_entry, 'runtime_data', None)
        engine = getattr(agent, 'engine', None)
        ru = str(self.hass.config.language or '').startswith('ru')
        result = await independent_service.async_export(self.hass, engine)
        self._report = independent_service.summary(result, ru)
        persistent_notification.async_create(self.hass, self._report, 'Hauflo', 'hauflo_independent')
        return await self.async_step_independent_done()

    async def async_step_independent_done(self, user_input: dict[str, Any] | None=None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id='independent_done', data_schema=vol.Schema({}), description_placeholders={'report': self._report})
        return self.async_create_entry(data=self._saved if self._saved is not None else dict(self.config_entry.options))
