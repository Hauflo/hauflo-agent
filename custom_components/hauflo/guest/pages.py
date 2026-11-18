"""The guest's own HA user, its dashboard pages and menu."""
from __future__ import annotations
import datetime as dt
from http import HTTPStatus
import logging
import re
import secrets
from typing import Any
from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.auth.providers import homeassistant as auth_ha
from homeassistant.components.frontend import DATA_PANELS
from homeassistant.components.frontend.storage import async_user_store
from homeassistant.util import dt as dt_util, slugify
from ..house import is_guest
from .common import PAGES, PASSWORD_ABC, Invalid, Refused, clean, iso, log_max, pages_max, pages_max_active, pages_max_days, password_len, roles, slug_max
_LOGGER = logging.getLogger(__name__)

class GuestPagesMixin:
    """Creates and removes the guest's user and pages."""

    @staticmethod
    def _username(label: str, taken: set[str]) -> str:
        base = re.sub('^((guest|gost|gosti)(-|$))+', '', slugify(label, separator='-'))[:slug_max()].strip('-')
        if base in ('', 'unknown'):
            base = secrets.token_hex(2)
        name, n = (f'guest-{base}', 1)
        while name in taken:
            n += 1
            name = f'guest-{base}-{n}'
        return name

    def _urls(self, first: dict[str, str]) -> list[str]:
        path = f"/{first['dashboard']}/{first['view']}"
        out: list[str] = []
        for base in (self.hass.config.external_url, self.hass.config.internal_url):
            if isinstance(base, str) and base and ((url := (base.rstrip('/') + path)) not in out):
                out.append(url)
        return out

    async def issue_pages(self, user: Any, body: dict[str, Any]) -> dict[str, Any]:
        """A guest with a login: a non-admin HA user, his password shown once (HA keeps only its hash)."""
        if user is None or not user.is_admin or user.system_generated or (not user.is_active) or is_guest(self.hass, user):
            raise Refused('not_admin', HTTPStatus.FORBIDDEN)
        label = clean(body.get('label'), 40)
        if not label:
            raise Refused('no_label')
        pages = body.get('pages')
        if not isinstance(pages, list) or not 1 <= len(pages) <= pages_max() or (not all((isinstance(x, dict) and set(x) == {'dashboard', 'view'} and isinstance(x['dashboard'], str) and isinstance(x['view'], str) for x in pages))):
            raise Invalid(f'pages: 1…{pages_max()} of {{dashboard, view}}')
        chosen = list(dict.fromkeys(((x['dashboard'], x['view']) for x in pages)))
        offered = {(d['url_path'], v['path']) for d in await self.available() for v in d['views']}
        if any((c not in offered for c in chosen)):
            raise Refused('no_page')
        hours = body.get('hours')
        if isinstance(hours, bool) or not isinstance(hours, int) or (not 1 <= hours <= pages_max_days() * 24):
            raise Invalid(f'hours: 1…{pages_max_days() * 24}')
        role = body.get('role')
        if role is not None and role not in roles():
            raise Invalid(f"role: one of {', '.join(roles()) or '—'}")
        provider = auth_ha.async_get_provider(self.hass)
        if provider.data is None:
            await provider.async_initialize()
        async with self.lock:
            self._prune()
            if sum((1 for g in self.grants if g.get('kind') == PAGES and self._status(g) == 'active')) >= pages_max_active():
                raise Refused('too_many_logins', n=pages_max_active())
            taken = {str(u.get('username', '')) for u in provider.data.users}
            username = self._username(label, taken)
            password = ''.join((secrets.choice(PASSWORD_ABC) for _ in range(password_len())))
            guest = await self.hass.auth.async_create_user(label, group_ids=[GROUP_ID_USER])
            self._pending.add(guest.id)
            self._mark()
            try:
                await provider.async_add_auth(username, password)
                creds = await provider.async_get_or_create_credentials({'username': username})
                await self.hass.auth.async_link_user(guest, creds)
            except Exception:
                await self.hass.auth.async_remove_user(guest)
                self._pending.discard(guest.id)
                self._mark()
                raise
            now = dt_util.utcnow()
            grant = {'id': secrets.token_urlsafe(9), 'kind': PAGES, 'label': label, 'user_id': guest.id, 'username': username, 'pages': [{'dashboard': d, 'view': v} for d, v in chosen], 'until': iso(now + dt.timedelta(hours=hours)), 'issued_by': user.name or '', 'by_id': user.id, 'created': iso(now), 'revoked': None, 'removed': None, **({'role': role} if role else {})}
            self.grants.insert(0, grant)
            self._pending.discard(guest.id)
            self._mark()
            self.log.insert(0, {'t': iso(now), 'sys': 'pages_issued', 'grant': grant['id'], 'label': label, 'by': user.name or '', 'hours': hours, 'n': len(chosen), **({'role': role} if role else {})})
            del self.log[log_max():]
            await self.store.async_save(self._data())
        await self.sync()
        urls = self._urls(grant['pages'][0])
        return {'id': grant['id'], 'username': username, 'password': password, 'urls': urls, 'urls_source': 'configured' if urls else 'fallback', 'until': grant['until'], 'pages': grant['pages']}

    async def _remove_user(self, g: dict[str, Any]) -> None:
        u = await self.hass.auth.async_get_user(g.get('user_id') or '')
        if u is not None and (not u.system_generated):
            try:
                await self.hass.auth.async_remove_user(u)
            except Exception:
                _LOGGER.warning('Hauflo guest: user of %s not deleted, switched off', g.get('id'), exc_info=True)
                await self.hass.auth.async_update_user(u, is_active=False)
                return
        g['removed'] = iso(dt_util.utcnow())

    async def _guest_menu(self, g: dict[str, Any]) -> None:
        mine = list(dict.fromkeys((x['dashboard'] for x in g.get('pages', []))))
        if not mine:
            return
        store = await async_user_store(self.hass, g['user_id'])
        sidebar = dict(store.data.get('sidebar') or {})
        want = {**sidebar, 'hiddenPanels': sorted((p for p in self.hass.data.get(DATA_PANELS) or {} if p not in mine)), 'panelOrder': mine}
        if want != sidebar:
            await store.async_set_item('sidebar', want)
        core = dict(store.data.get('core') or {})
        if core.get('default_panel') != mine[0]:
            await store.async_set_item('core', {**core, 'default_panel': mine[0]})
