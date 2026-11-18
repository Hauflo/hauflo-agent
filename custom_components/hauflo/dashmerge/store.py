"""Reads, merges and saves the managed dashboards."""
from __future__ import annotations
import asyncio
from collections.abc import Callable
import copy
import logging
import secrets
from typing import TYPE_CHECKING, Any
from homeassistant.components.lovelace.const import LOVELACE_DATA, ConfigNotFound
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from .. import order as block_order, stock as stock_cards
from ..const import VERSION
from ..guard import contained
from ..storage import HaufloStore
from .access import collapse_access, collapse_admins, collapse_users, expand_access, expand_admins, expand_users, redact, views_of
from .const import COPY_RE, DEAD_LINK, MAX_COPIES, MAX_TAKEN_COPIES, META_KEY, MONTHS, STORE_KEY
from .plan import digest, plan_and_merge
from .tree import ensure_ids, rekey
from .twins import drop_twins, twins, unpanel
if TYPE_CHECKING:
    from .access import Access
_LOGGER = logging.getLogger(__name__)

class DashboardMerge:
    """Bases per dashboard (the agent's own store), the copies it made, and the tunnel's dashboard commands."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: HaufloStore = HaufloStore(hass, STORE_KEY)
        self.meta_store: HaufloStore = HaufloStore(hass, META_KEY)
        self.bases: dict[str, dict[str, Any]] = {}
        self.copies: dict[str, list[str]] = {}
        self.taken_copies: set[str] = set()
        self.written: dict[str, str] = {}
        self.merged_by: str | None = None
        self.langs: dict[str, str] = {}
        self.key: bytes = b''
        self.access_of: Callable[[], Any] | None = None
        self.write_lock = asyncio.Lock()

    async def _access(self) -> Access | None:
        if self.access_of is None:
            return None
        with contained(_LOGGER, 'Hauflo: who sees which page not known'):
            return await self.access_of()
        return None

    def _plain(self, current: Any, access: Access | None) -> Any:
        return collapse_access(copy.deepcopy(current), access.guests if access else frozenset()) if current is not None else None

    def pages(self) -> list[str]:
        """Hauflo's dashboards whose pages may be given to a guest: the merged ones, never a copy."""
        return [u for u in self.bases if not is_copy(u)]

    async def apply_access(self) -> int:
        """Every merged dashboard's pages → the lists of who sees them now (a guest given / revoked / expired, a person
        added to HA). Only `visible` of the pages changes; the base and the merge are not touched. How many saved."""
        access = await self._access()
        saved = 0
        async with self.write_lock:
            for url_path in self.pages():
                exists, current = await self._current(url_path)
                if not exists or not isinstance(current, dict):
                    continue
                out = expand_access(self._plain(current, access), access, url_path)
                if out != current:
                    await self._dashboard(url_path).async_save(out)
                    saved += 1
        return saved

    async def page_list(self) -> list[dict[str, Any]]:
        """The pages a guest with a login may be given (guest.py): every page with a path of the merged dashboards, as
        the family sees them (collapse_access) — a page only for the administrators marked, a hidden one (`visible:
        false`) and a dashboard only for administrators (`require_admin`: HA refuses it to a guest) left out."""
        access = await self._access()
        admins = set(await self._admin_ids())
        out: list[dict[str, Any]] = []
        for url_path in self.pages():
            exists, current = await self._current(url_path)
            item = getattr(self._dashboard(url_path), 'config', None) or {}
            if not exists or not isinstance(current, dict) or (not isinstance(item, dict)) or item.get('require_admin'):
                continue
            views = []
            for v in views_of(self._plain(current, access)):
                if not isinstance(v, dict) or not isinstance(v.get('path'), str) or (not v['path']) or (v.get('visible') is False):
                    continue
                vis = v.get('visible')
                users = [u for u in (x.get('user') for x in vis if isinstance(x, dict)) if not (isinstance(u, str) and u.startswith('hauflo:'))] if isinstance(vis, list) else []
                title = v.get('title') if isinstance(v.get('title'), str) and v.get('title') else v['path']
                views.append({'path': v['path'], 'title': title, 'admin': bool(users) and all((u in admins for u in users)), 'sub': bool(v.get('subview'))})
            if views:
                title = item.get('title') if isinstance(item.get('title'), str) and item.get('title') else url_path
                out.append({'url_path': url_path, 'title': title, 'views': views})
        return out

    async def async_load(self) -> None:
        data = await self.store.async_load()
        self.bases = {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
        meta = await self.meta_store.async_load()
        meta = meta if isinstance(meta, dict) else {}
        copies = meta.get('copies')
        self.copies = {k: [x for x in v if isinstance(x, str)] for k, v in copies.items() if isinstance(v, list)} if isinstance(copies, dict) else {}
        taken = meta.get('taken')
        self.taken_copies = {x for x in taken if isinstance(x, str)} if isinstance(taken, list) else set()
        written = meta.get('written')
        self.written = {k: v for k, v in written.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(written, dict) else {}
        self.merged_by = meta.get('merged_by') if isinstance(meta.get('merged_by'), str) else None
        langs = meta.get('langs')
        self.langs = {k: v[:5] for k, v in langs.items() if isinstance(k, str) and isinstance(v, str) and v} if isinstance(langs, dict) else {}
        key = meta.get('key')
        if not (isinstance(key, str) and len(key) == 64):
            key = secrets.token_hex(32)
            await self._save_meta(key)
        self.key = bytes.fromhex(key)

    async def reorder_views(self, url_path: str, order: dict[str, list[str]]) -> bool:
        """        of
        what the agent wrote stays true (no hidden copy for it at the next deploy). False — nothing to change."""
        if not self.manages(url_path) or is_copy(url_path):
            return False
        exists, raw = await self._current(url_path)
        if not exists or not isinstance(raw, dict):
            return False
        views = block_order.view_order(raw, order, url_path, self.bases.get(url_path))
        if views is None:
            return False
        out = {**raw, 'views': views}
        await self._dashboard(url_path).async_save(out)
        _, back = await self._current(url_path)
        self.written[url_path] = digest(back if back is not None else out)
        await self._save_meta()
        return True

    async def _save_meta(self, key: str | None=None) -> None:
        await self.meta_store.async_save({'copies': self.copies, 'taken': sorted(self.taken_copies), 'written': self.written, 'key': key or self.key.hex(), **({'merged_by': self.merged_by} if self.merged_by else {}), **({'langs': self.langs} if self.langs else {})})

    def unchanged(self, url_path: str, raw: Any) -> bool:
        """HA's dashboard is exactly what the agent left there last (Р33) — nothing edited in HA since."""
        return raw is not None and self.written.get(url_path) == digest(raw)

    def manages(self, url_path: Any) -> bool:
        return isinstance(url_path, str) and url_path in self.bases

    def _dashboard(self, url_path: str) -> Any:
        return self.hass.data[LOVELACE_DATA].dashboards.get(url_path)

    async def _current(self, url_path: str) -> tuple[bool, Any]:
        dash = self._dashboard(url_path)
        if dash is None or getattr(dash, 'mode', 'storage') != 'storage':
            return (False, None)
        try:
            return (True, await dash.async_load(False))
        except ConfigNotFound:
            return (True, None)

    async def read(self, url_path: str) -> Any:
        """`lovelace/config` of a dashboard the agent merges, as the cloud may see it (redact). None: no config yet."""
        exists, current = await self._current(url_path)
        if not exists:
            raise MergeError('not_found', 'no such storage dashboard')
        if current is None:
            raise MergeError('config_not_found', 'no config yet')
        plain = self._plain(current, await self._access())
        return collapse_users(collapse_admins(redact(plain, self.bases[url_path], self.key), await self._admin_ids()), await self._users())

    async def _admin_ids(self) -> list[str]:
        try:
            users = await self.hass.auth.async_get_users()
        except Exception:
            _LOGGER.warning('Hauflo: not knowable: the marker stays (edit mode only)', exc_info=True)
            return []
        return sorted((u.id for u in users if u.is_admin and u.is_active and (not u.system_generated)))

    async def _users(self) -> list[tuple[str, str]]:
        try:
            users = await self.hass.auth.async_get_users()
        except Exception:
            _LOGGER.warning('Hauflo: not knowable: the markers stay alone (edit mode only)', exc_info=True)
            return []
        return sorted(((u.id, u.name) for u in users if u.is_active and (not u.system_generated) and isinstance(u.name, str) and u.name))

    async def _expand(self, config: dict[str, Any]) -> Any:
        return expand_users(expand_admins(ensure_ids(copy.deepcopy(config)), await self._admin_ids()), await self._users())

    async def plan(self, url_path: str, config: dict[str, Any]) -> dict[str, Any]:
        exists, current = await self._current(url_path)
        if not exists:
            return {'base': False, 'exists': False}
        current = self._plain(current, await self._access())
        base = self.bases.get(url_path) if current is not None else None
        _, out = plan_and_merge(await self._expand(config), current, base, url_path=url_path)
        offers = stock_cards.offers(current, stock_cards.hass_info(self.hass)) if base is not None and current is not None else []
        pairs = twins(current, config) if base is not None else []
        return {**out, 'exists': True, **({'stock': offers[:200]} if offers else {}), **({'twins': pairs} if pairs else {})}

    async def save(self, url_path: str, config: dict[str, Any], take: Any, restore: Any, replace: bool, title: str | None, local_ws: Callable[[list[dict[str, Any]]], Any], lang: str | None=None, stock: Any=(), drop: Any=()) -> dict[str, Any]:
        async with self.write_lock:
            return await self._save(url_path, config, take, restore, replace, title, local_ws, lang, stock, drop)

    async def _save(self, url_path: str, config: dict[str, Any], take: Any, restore: Any, replace: bool, title: str | None, local_ws: Callable[[list[dict[str, Any]]], Any], lang: str | None, stock: Any, drop: Any) -> dict[str, Any]:
        exists, raw = await self._current(url_path)
        if not exists:
            raise MergeError('not_found', 'no such storage dashboard')
        lang = lang or self.langs.get(url_path)
        access = await self._access()
        current = self._plain(raw, access)
        base = self.bases.get(url_path) if current is not None else None
        new = await self._expand(config)
        new = rekey(new, unpanel(base, new) if base is not None else None)
        if base is None and current is not None and (not replace):
            raise MergeError('no_base', 'the agent has no Hauflo version of this dashboard to merge against')
        src, made = stock_cards.apply(current, list(stock), stock_cards.hass_info(self.hass)) if base is not None and current is not None and stock else (current, 0)
        src, dropped = drop_twins(src, new, list(drop)) if base is not None and src is not None and drop else (src, 0)
        merged, report = plan_and_merge(new, src, base, take, restore, url_path=url_path)
        if made:
            report['stock'] = made
        if dropped:
            report['twins'] = dropped
        if report.get('lost'):
            _LOGGER.warning("Hauflo: dashboard %s not saved — %s of the integrator's items would be lost", url_path, report['lost'])
            raise MergeError('merge_lost_local', f"{report['lost']} of the integrator's own items would be lost; nothing saved")
        if report.get('untouched'):
            _LOGGER.warning('Hauflo: dashboard %s not saved — %s blocks outside the plan would change', url_path, report['untouched'])
            raise MergeError('merge_untouched_changed', f"{report['untouched']} blocks the plan doesn't change would change; nothing saved")
        copy_url, pruned = (None, 0)
        if current is not None and merged != current and (base is None or current != base) and (not self.unchanged(url_path, raw)):
            if title is None:
                shown = getattr(self._dashboard(url_path), 'config', None) or {}
                title = shown.get('title') if isinstance(shown.get('title'), str) else None
            copy_url, pruned = await self._copy(url_path, current, title, local_ws, lang, before_take=bool(report.get('taken')))
        if (views := block_order.view_order(merged, await block_order.async_load(self.hass), url_path)) is not None:
            merged = {**merged, 'views': views}
        out = expand_access(copy.deepcopy(merged), access, url_path)
        if out != raw:
            await self._dashboard(url_path).async_save(out)
            _, back = await self._current(url_path)
            report['verify'] = 'ok' if back == out else 'mismatch'
            if back != out:
                _LOGGER.warning('Hauflo: dashboard %s reads back other than saved', url_path)
            self.written[url_path] = digest(back if back is not None else out)
        else:
            self.written[url_path] = digest(raw)
        self.bases[url_path] = new
        if lang:
            self.langs[url_path] = lang
        self.merged_by = VERSION
        await self.store.async_save(self.bases)
        await self._save_meta()
        return {**report, 'saved': out != raw, **({'copy': copy_url, 'copy_reason': 'upgrade' if base is None else 'edited'} if copy_url else {}), **({'copies_removed': pruned} if pruned else {})}

    def outdated(self) -> bool:
        """The dashboards were merged last by another version of the agent (or none recorded yet): `remerge` is due."""
        return self.merged_by != VERSION

    async def remerge(self, local_ws: Callable[[list[dict[str, Any]]], Any], locked: bool=False) -> dict[str, dict[str, Any]]:
        """        dashboard
        again by its own rules, with no deploy — the new version N is the base B (what Hauflo saved last), HA's config
        the current. N = B keeps every edit of the integrator's as it is (the three-way merge of an untouched key is
        his),
        so only what the merge itself now decides changes: a tile to a page gone (`_drop_dead`), the tabs in the
        client's
        order (order.py). Written only then, a hidden copy of the panel first (what of his goes is kept there); any
        other
        difference the merge would make is not written (no deploy asked for it). Never raises for one dashboard: each
        written, the version not marked (the next start tries again)."""
        out: dict[str, dict[str, Any]] = {}
        failed = False
        for url_path in [u for u in self.bases if not is_copy(u)]:
            try:
                out[url_path] = await self._remerge_one(url_path, local_ws, locked)
            except Exception as err:
                failed = True
                _LOGGER.warning('Hauflo: dashboard %s not merged again after the update: %s', url_path, err)
                out[url_path] = {'outcome': 'failed'}
        if not locked and (not failed):
            self.merged_by = VERSION
            await self._save_meta()
        return out

    async def _remerge_one(self, url_path: str, local_ws: Callable[[list[dict[str, Any]]], Any], locked: bool) -> dict[str, Any]:
        async with self.write_lock:
            exists, raw = await self._current(url_path)
            base = self.bases.get(url_path)
            if not exists or not isinstance(raw, dict) or (not isinstance(base, dict)):
                return {'outcome': 'skipped:no_dashboard'}
            access = await self._access()
            current = self._plain(raw, access)
            merged, report = plan_and_merge(copy.deepcopy(base), current, base, url_path=url_path)
            if report.get('lost') or report.get('untouched'):
                _LOGGER.warning('Hauflo: dashboard %s not merged again — %s of his items / %s blocks would change', url_path, report.get('lost'), report.get('untouched'))
                return {'outcome': 'skipped:guard'}
            dead = [r for r in report.get('removed', []) if r.get('reason') == DEAD_LINK]
            if dead:
                plain, _ = plan_and_merge(copy.deepcopy(base), current, base)
                if plain != current:
                    _LOGGER.warning('Hauflo: dashboard %s not merged again — the merge would change more than the tiles to pages gone', url_path)
                    return {'outcome': 'skipped:other_changes'}
            else:
                merged = current
            if (views := block_order.view_order(merged, await block_order.async_load(self.hass), url_path)) is not None:
                merged = {**merged, 'views': views}
            out = expand_access(copy.deepcopy(merged), access, url_path)
            if out == raw:
                return {'outcome': 'same'}
            if locked:
                _LOGGER.info('Hauflo: dashboard %s would be merged again after the update — changes by Hauflo are switched off', url_path)
                return {'outcome': 'skipped:locked'}
            copy_url = None
            if dead:
                shown = getattr(self._dashboard(url_path), 'config', None) or {}
                title = shown.get('title') if isinstance(shown.get('title'), str) else None
                copy_url, _ = await self._copy(url_path, current, title, local_ws, self.langs.get(url_path))
            await self._dashboard(url_path).async_save(out)
            _, back = await self._current(url_path)
            self.written[url_path] = digest(back if back is not None else out)
            await self._save_meta()
            _LOGGER.info('Hauflo: dashboard %s merged again after the update — %s tile(s) to pages gone removed, tabs reordered: %s', url_path, len(dead), views is not None)
            return {'outcome': 'saved', 'removed': dead, 'reordered': views is not None, 'verify': 'ok' if back == out else 'mismatch', **({'copy': copy_url} if copy_url else {})}

    async def _copy(self, url_path: str, config: Any, title: str | None, local_ws: Callable[[list[dict[str, Any]]], Any], lang: str | None=None, before_take: bool=False) -> tuple[str, int]:
        now = dt_util.now()
        prefix = f'{url_path[:40]}-copy-'
        ru = str(lang or self.hass.config.language or '').startswith('ru')
        shown = now.strftime('%d.%m %H:%M') if ru else f"{now.day} {MONTHS[now.month - 1]} {now.strftime('%H:%M')}"
        name = f"{title or ('Панель' if ru else 'Dashboard')} ({('копия' if ru else 'copy')} {shown})"
        listed = await local_ws([{'type': 'lovelace/dashboards/list'}])
        dashboards = listed[0] if listed and isinstance(listed[0], list) else []
        taken = {str(d.get('url_path')) for d in dashboards if isinstance(d, dict)}
        copy_url = f"{prefix}{now.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
        while copy_url in taken:
            copy_url = f"{prefix}{now.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
        await local_ws([{'type': 'lovelace/dashboards/create', 'url_path': copy_url, 'title': name[:60], 'icon': 'mdi:content-copy', 'show_in_sidebar': False, 'require_admin': True, 'mode': 'storage'}])
        await local_ws([{'type': 'lovelace/config/save', 'url_path': copy_url, 'config': config}])
        mine = [*self.copies.get(url_path, []), copy_url]
        if before_take:
            self.taken_copies.add(copy_url)
        plain = [u for u in mine if u not in self.taken_copies]
        marked = [u for u in mine if u in self.taken_copies]
        drop = set(plain[:max(0, len(plain) - MAX_COPIES)] + marked[:max(0, len(marked) - MAX_TAKEN_COPIES)])
        ids = {str(d.get('url_path')): d.get('id') for d in dashboards if isinstance(d, dict)}
        pruned = 0
        for old in [u for u in mine if u in drop]:
            mine.remove(old)
            self.taken_copies.discard(old)
            if ids.get(old):
                with contained(_LOGGER, 'Hauflo: old dashboard copy %s not removed: %s', old, err=True):
                    await local_ws([{'type': 'lovelace/dashboards/delete', 'dashboard_id': ids[old]}])
                    pruned += 1
        self.copies[url_path] = mine
        await self._save_meta()
        return (copy_url, pruned)

class MergeError(Exception):

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

def is_copy(url: Any) -> bool:
    return isinstance(url, str) and bool(COPY_RE.search(url))

def choice(v: Any) -> Any:
    """«all» (True) or a list of Hauflo ids — anything else is nothing chosen."""
    if v is True:
        return True
    if isinstance(v, list):
        return [x for x in v[:2000] if isinstance(x, str) and len(x) <= 256]
    return []
