"""Guest access expiry, sync and cleanup."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util
from ..guard import contained
from ..house import GUEST_USERS
from .common import PAGES, iso, log_max, pages_check, pages_debounce
_LOGGER = logging.getLogger(__name__)

class GuestLifecycleMixin:
    """Ends expired accesses and keeps HA users in sync."""

    async def check(self) -> None:
        """Every minute and on HA's own changes: an expired guest's user deleted, one deleted in HA by hand noted."""
        await self.async_load()
        present = {u.id for u in await self.hass.auth.async_get_users()}
        async with self.lock:
            changed = False
            now = iso(dt_util.utcnow())
            for g in self.grants:
                if g.get('kind') != PAGES or g.get('removed'):
                    continue
                status = self._status(g)
                if g.get('user_id') not in present:
                    if status == 'active':
                        g['revoked'], g['revoked_by'] = (now, '')
                    g['removed'] = now
                    self.log.insert(0, {'t': now, 'sys': 'pages_gone', 'grant': g['id'], 'label': g.get('label') or ''})
                    changed = True
                elif status != 'active':
                    await self._remove_user(g)
                    if status == 'expired':
                        self.log.insert(0, {'t': now, 'sys': 'pages_expired', 'grant': g['id'], 'label': g.get('label') or ''})
                    changed = True
            if changed:
                del self.log[log_max():]
                self._prune()
                await self.store.async_save(self._data())
        await self.sync()

    async def sync(self, force: bool=False) -> None:
        """The guests' menus and every page's list of who sees it, brought to now: when who-sees-what changed, or (force) a dashboard was saved in HA — a page added there gets its list."""
        async with self._sync_lock:
            access = await self.access()
            for g in self.grants:
                if g.get('kind') == PAGES and (not g.get('removed')) and (self._status(g) == 'active'):
                    with contained(_LOGGER, 'Hauflo guest: menu of %s not set', g.get('id')):
                        await self._guest_menu(g)
            if self.dashboards is None or (not force and access.key() == self._applied):
                return
            if force and (not access.guests) and (self._applied is not None):
                return
            await self.dashboards.apply_access()
            self._applied = access.key()

    def _soon(self, force: bool=False) -> None:
        if self._later is not None:
            self._later()

        async def _run(_now: Any) -> None:
            self._later = None
            with contained(_LOGGER, 'Hauflo guest: check failed'):
                await self.check()
                if force:
                    await self.sync(force=True)
        self._later = async_call_later(self.hass, pages_debounce(), _run)

    async def async_start(self) -> None:
        """The checks of guests with a login: every minute, on a user added / changed / deleted, on a dashboard saved."""
        await self.async_load()

        async def _tick(_now: Any) -> None:
            with contained(_LOGGER, 'Hauflo guest: check failed'):
                await self.check()
        self._unsubs.append(async_track_time_interval(self.hass, _tick, pages_check()))

        @callback
        def _users(_event: Any) -> None:
            self._soon()
        for event in ('user_added', 'user_updated', 'user_removed'):
            self._unsubs.append(self.hass.bus.async_listen(event, _users))

        @callback
        def _saved(event: Any) -> None:
            if self.dashboards is not None and event.data.get('url_path') in self.dashboards.pages():
                self._soon(force=True)
        self._unsubs.append(self.hass.bus.async_listen('lovelace_updated', _saved))
        await self.check()

    async def async_remove(self) -> int:
        """The integration removed: every guest's user deleted, every page back to what it was."""
        await self.async_load()
        n = 0
        async with self.lock:
            now = iso(dt_util.utcnow())
            for g in self.grants:
                if g.get('kind') == PAGES and (not g.get('removed')):
                    g['revoked'] = g.get('revoked') or now
                    await self._remove_user(g)
                    n += 1
        self._applied = None
        await self.sync()
        self.hass.data.pop(GUEST_USERS, None)
        return n
