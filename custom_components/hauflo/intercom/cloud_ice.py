from __future__ import annotations
import asyncio
from collections.abc import Awaitable, Callable
import functools
import logging
import time
from typing import Any
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from ..tuning import tuning
_LOGGER = logging.getLogger(__name__)
t = functools.cache(lambda: tuning()['intercom'])

def parse(raw: Any) -> tuple[list[dict[str, Any]], int]:
    """Hauflo's answer {ice_servers: [{urls, username?, credential?}], ttl} → the servers and their TTL (clamped); ValueError when it is not one."""
    if not isinstance(raw, dict) or not isinstance(raw.get('ice_servers'), list):
        raise ValueError('no ice_servers')
    out: list[dict[str, Any]] = []
    for s in raw['ice_servers']:
        if not isinstance(s, dict):
            raise ValueError('bad server')
        urls = s.get('urls')
        urls = [urls] if isinstance(urls, str) else urls
        if not isinstance(urls, list) or not urls or (not all((isinstance(u, str) and u.split(':', 1)[0] in t()['ice_schemes'] for u in urls))):
            raise ValueError('bad urls')
        item: dict[str, Any] = {'urls': urls}
        if isinstance(s.get('username'), str) and isinstance(s.get('credential'), str):
            item['username'], item['credential'] = (s['username'], s['credential'])
        out.append(item)
    low, high = t()['cloud_ice_ttl_s']
    try:
        ttl = int(raw.get('ttl'))
    except (TypeError, ValueError) as err:
        raise ValueError('bad ttl') from err
    return (out, min(high, max(low, ttl)))

class CloudIce:
    """The credentials Hauflo gave this house, with their expiry."""

    def __init__(self, hass: HomeAssistant, fetch: Callable[[], Awaitable[Any]]) -> None:
        self.hass = hass
        self._fetch = fetch
        self._servers: list[dict[str, Any]] = []
        self._expires = 0.0
        self._ttl = 0
        self._turn = False
        self._lock = asyncio.Lock()
        self._unsub: Callable[[], None] | None = None
        self._stopped = False

    def current(self, now: float | None=None) -> list[dict[str, Any]]:
        now = time.time() if now is None else now
        if now + min(t()['cloud_ice_min_left_s'], self._ttl / 4) >= self._expires:
            return []
        return [dict(s) for s in self._servers]

    def state(self, now: float | None=None) -> dict[str, Any]:
        """For the wizard's GET: whether a phone outside gets a TURN now (never the credentials)."""
        now = time.time() if now is None else now
        live = bool(self.current(now))
        return {'source': 'cloud', 'turn': live and self._turn, 'expires_in': max(0, int(self._expires - now)) if live else 0}

    async def refresh(self) -> None:
        """Ask Hauflo once (a second caller meanwhile waits for the same answer); the next ask is scheduled either way."""
        if self._stopped:
            return
        if self._lock.locked():
            async with self._lock:
                return
        async with self._lock:
            delay = t()['cloud_ice_retry_s']
            try:
                servers, ttl = parse(await self._fetch())
            except Exception as err:
                _LOGGER.warning('Hauflo intercom: no TURN from Hauflo now (%s)', type(err).__name__)
            else:
                self._servers, self._expires, self._ttl = (servers, time.time() + ttl, ttl)
                self._turn = any((u.startswith(('turn:', 'turns:')) for s in servers for u in s['urls']))
                delay = max(1.0, ttl * t()['cloud_ice_refresh_share'])
            self._schedule(delay)

    def _schedule(self, delay: float) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._stopped:
            return

        @callback
        def _due(_now: Any) -> None:
            self._unsub = None
            self.hass.async_create_background_task(self.refresh(), 'hauflo intercom ice')
        self._unsub = async_call_later(self.hass, delay, _due)

    def stop(self) -> None:
        self._stopped = True
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._servers, self._expires = ([], 0.0)
