"""HA's helpers (input_boolean, input_text…) made, renamed and removed through HA's own websocket commands — what HA's helper editor sends (``<domain>/list|create|update|delete``), run in-process on a websocket connection of the agent's own system user."""
from __future__ import annotations
import asyncio
import contextvars
import functools
import json
import logging
from typing import Any
from homeassistant.auth.const import GROUP_ID_ADMIN
from homeassistant.auth.models import User
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from .const import CONF_REFRESH_TOKEN_ID, CONF_USER_ID, DOMAIN
from .tuning import tuning
_LOGGER = logging.getLogger(__name__)
_USER_LOCK = f'{DOMAIN}_user_lock'
timeout = functools.cache(lambda: tuning()['notify']['helper_command_s'])

class HelperError(Exception):
    """HA refused a helper command (its code and message)."""

async def agent_user(hass: HomeAssistant, entry: ConfigEntry) -> User:
    """The agent's own system user (an admin, made once and kept with the entry) — the tunnel's tokens and the helper commands are its."""
    async with hass.data.setdefault(_USER_LOCK, asyncio.Lock()):
        if (uid := entry.data.get(CONF_USER_ID)) and (user := (await hass.auth.async_get_user(uid))) is not None:
            return user
        user = await hass.auth.async_create_system_user('Hauflo agent', group_ids=[GROUP_ID_ADMIN])
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_USER_ID: user.id, CONF_REFRESH_TOKEN_ID: None})
        return user

class HelperCommands:
    """One helper domain's items (``data``: id → item, as HA's ``<domain>/list`` gives them) and its changes — each a websocket command with the same checks (schema, admin) as the helper editor's."""

    def __init__(self, hass: HomeAssistant, domain: str, user: User) -> None:
        self.hass = hass
        self.domain = domain
        self.data: dict[str, dict[str, Any]] = {}
        self._id = 0
        self._waiting: dict[int, asyncio.Future[dict[str, Any]]] = {}
        log = logging.LoggerAdapter(_LOGGER, {'connid': f'{DOMAIN}-{domain}'})
        self._conn = contextvars.copy_context().run(websocket_api.ActiveConnection, log, hass, self._answer, user, None, None)

    @callback
    def _answer(self, msg: bytes | str | dict[str, Any]) -> None:
        if isinstance(msg, (bytes, str)):
            msg = json.loads(msg)
        if isinstance(msg, dict) and (fut := self._waiting.pop(msg.get('id'), None)) is not None and (not fut.done()):
            fut.set_result(msg)

    async def _call(self, command: str, **fields: Any) -> Any:
        self._id += 1
        msg_id = self._id
        fut: asyncio.Future[dict[str, Any]] = self.hass.loop.create_future()
        self._waiting[msg_id] = fut
        self._conn.async_handle({'id': msg_id, 'type': f'{self.domain}/{command}', **fields})
        try:
            msg = await asyncio.wait_for(fut, timeout())
        finally:
            self._waiting.pop(msg_id, None)
        if not msg.get('success'):
            err = msg.get('error') or {}
            raise HelperError(f"{self.domain}/{command}: {err.get('code')} {err.get('message')}")
        return msg.get('result')

    async def async_load(self) -> None:
        self.data = {str(i['id']): i for i in await self._call('list') or [] if isinstance(i, dict) and 'id' in i}

    async def async_create_item(self, data: dict[str, Any]) -> dict[str, Any]:
        item = await self._call('create', **data)
        self.data[str(item['id'])] = item
        return item

    async def async_update_item(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        item = await self._call('update', **{f'{self.domain}_id': item_id}, **data)
        self.data[str(item['id'])] = item
        return item

    async def async_delete_item(self, item_id: str) -> None:
        await self._call('delete', **{f'{self.domain}_id': item_id})
        self.data.pop(item_id, None)

async def async_store(hass: HomeAssistant, domain: str) -> HelperCommands | None:
    """A helper domain's store with its items read; None when HA has no such commands (the domain not set up) or the agent isn't set up (no user of its own)."""
    if domain not in hass.config.components or not (entries := hass.config_entries.async_entries(DOMAIN)):
        return None
    try:
        store = HelperCommands(hass, domain, await agent_user(hass, entries[0]))
        await store.async_load()
    except Exception as err:
        _LOGGER.warning('Hauflo: %s helpers out of reach: %s', domain, err)
        return None
    return store
