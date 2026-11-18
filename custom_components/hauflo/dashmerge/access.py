"""Per-user and admin-only parts of a dashboard."""
from __future__ import annotations
from collections.abc import Callable
import copy
import hashlib
import hmac
import json
from typing import Any
from .const import ACCESS_ALL, ACCESS_ALL_TRUE, ACCESS_MORE, ADMINS, ID, LOCAL, USER_MARKER
from .tree import Index, child_level_of, cloud, hid, kids_key, kids_of, own_of

def _walk_admins(node: Any, fix: Callable[[str, Any], Any]) -> Any:
    if isinstance(node, list):
        for x in node:
            _walk_admins(x, fix)
    elif isinstance(node, dict):
        if isinstance(node.get('visible'), list):
            node['visible'] = fix('visible', node['visible'])
        if node.get('condition') == 'user' and isinstance(node.get('users'), list):
            node['users'] = fix('users', node['users'])
        for k, x in node.items():
            if k not in ('visible', 'users'):
                _walk_admins(x, fix)
    return node

def expand_admins(cfg: Any, ids: list[str]) -> Any:
    """The marker (a view's `visible: [{user: ADMINS}]`, a card's `{condition: user, users: [ADMINS]}`) → HA's
    administrators (`ids`); none known — the marker stays, a list no user matches (seen in edit mode only)."""
    if not ids:
        return cfg

    def fix(key: str, v: list[Any]) -> list[Any]:
        if key == 'visible' and any((isinstance(x, dict) and x.get('user') == ADMINS for x in v)):
            return [x for x in v if not (isinstance(x, dict) and x.get('user') == ADMINS)] + [{'user': i} for i in ids]
        if key == 'users' and ADMINS in v:
            return [x for x in v if x != ADMINS] + list(ids)
        return v
    return _walk_admins(cfg, fix)

def collapse_admins(cfg: Any, ids: list[str]) -> Any:
    """A read for the cloud: exactly the marker's expansion (a list of users equal to HA's administrators now, `ids`)
    says the marker again. Any other list (the integrator's own choice of people, or admins since changed) stays as it"""
    admins = set(ids)

    def fix(key: str, v: list[Any]) -> list[Any]:
        if not admins:
            return v
        if key == 'visible' and all((isinstance(x, dict) and set(x) == {'user'} for x in v)) and ({x['user'] for x in v} == admins) and (len(v) == len(admins)):
            return [{'user': ADMINS}]
        if key == 'users' and set(v) == admins and (len(v) == len(admins)):
            return [ADMINS]
        return v
    return _walk_admins(cfg, fix)

def _marked_names(v: list[Any], key: str) -> set[str]:
    raw = [x.get('user') for x in v if isinstance(x, dict)] if key == 'visible' else list(v)
    return {x[len(USER_MARKER):] for x in raw if isinstance(x, str) and x.startswith(USER_MARKER)}

def expand_users(cfg: Any, users: list[tuple[str, str]]) -> Any:
    """`users` — (id, name) of HA's active people. A list with markers ({user: USER_MARKER + name}, a card's
    `users: [USER_MARKER + name]`) keeps them and gets the ids of the people of those names (all of them, if two share a
    name); a name nobody has adds nothing — the list then shows the page to nobody (seen in edit mode only)."""

    def fix(key: str, v: list[Any]) -> list[Any]:
        names = _marked_names(v, key)
        if not names:
            return v
        have = {x.get('user') for x in v if isinstance(x, dict)} if key == 'visible' else set(v)
        ids = [i for i, n in users if n in names and i not in have]
        ids = list(dict.fromkeys(ids))
        return v + ([{'user': i} for i in ids] if key == 'visible' else ids)
    return _walk_admins(cfg, fix)

def collapse_users(cfg: Any, users: list[tuple[str, str]]) -> Any:
    """A read for the cloud: a list with markers loses the ids of the people of those names — exactly what expand_users
    added; a list without markers (the integrator's own choice) is never touched."""

    def fix(key: str, v: list[Any]) -> list[Any]:
        names = _marked_names(v, key)
        if not names:
            return v
        ids = {i for i, n in users if n in names}
        if key == 'visible':
            return [x for x in v if not (isinstance(x, dict) and set(x) == {'user'} and (x['user'] in ids))]
        return [x for x in v if x not in ids]
    return _walk_admins(cfg, fix)

class Access:
    """Who may see Hauflo's pages now (built by guest.py): `family` — HA's active people but guests; `guests` — every
    guest user the agent made and still knows (ended ones too: never family); `pages` — dashboard → view path → the
    active guests given it; `active` — a guest with a login exists, so pages get lists."""

    def __init__(self, family: list[str], guests: set[str], pages: dict[str, dict[str, list[str]]], active: bool) -> None:
        self.family = family
        self.guests = guests
        self.pages = pages
        self.active = active

    def key(self) -> tuple[Any, ...]:
        return (tuple(self.family), tuple(sorted(self.guests)), json.dumps(self.pages, sort_keys=True), self.active)

def blank_view(v: Any) -> bool:
    """A view with no card at all — none of its own, none in any section."""
    if not isinstance(v, dict):
        return False
    if any((isinstance(c, dict) for c in v.get('cards') or [])):
        return False
    return not any((isinstance(c, dict) for s in v.get('sections') or [] if isinstance(s, dict) for c in s.get('cards') or []))

def views_of(cfg: Any) -> list[Any]:
    return cfg['views'] if isinstance(cfg, dict) and isinstance(cfg.get('views'), list) else []

def expand_access(cfg: Any, access: Access | None, url_path: str) -> Any:
    """Every page of this dashboard → its list of people while a guest with a login exists (in place)."""
    if access is None or not access.active:
        return cfg
    given = access.pages.get(url_path, {})
    for v in views_of(cfg):
        if not isinstance(v, dict):
            continue
        path = v.get('path')
        mine = list(given.get(path, [])) if isinstance(path, str) else []
        vis = v.get('visible', None)
        if 'visible' not in v or vis is True:
            people = [*access.family, *[g for g in mine if g not in access.family]]
            v['visible'] = [{'user': ACCESS_ALL if 'visible' not in v else ACCESS_ALL_TRUE}, *({'user': u} for u in people)]
        elif isinstance(vis, list):
            had = {x.get('user') for x in vis if isinstance(x, dict)}
            add = [g for g in mine if g not in had]
            if add:
                v['visible'] = [*vis, {'user': ACCESS_MORE}, *({'user': g} for g in add)]
    return cfg

def collapse_access(cfg: Any, guests: set[str] | frozenset[str]=frozenset()) -> Any:
    """The pages as they were before expand_access (in place): by the tag; a list whose tag HA's own editor dropped —
    the known guests out of it, the rest is a list someone chose."""
    for v in views_of(cfg):
        if not isinstance(v, dict) or not isinstance(v.get('visible'), list):
            continue
        vis = v['visible']
        users = [x.get('user') if isinstance(x, dict) else None for x in vis]
        if ACCESS_ALL in users:
            del v['visible']
        elif ACCESS_ALL_TRUE in users:
            v['visible'] = True
        elif ACCESS_MORE in users:
            v['visible'] = vis[:users.index(ACCESS_MORE)]
        elif guests and any((u in guests for u in users)):
            v['visible'] = [x for x, u in zip(vis, users, strict=False) if u not in guests]
    return cfg

def redact(current: Any, base: dict[str, Any], key: bytes) -> Any:
    """HA's config as the cloud may read it: Hauflo's items as they are; the integrator's (no Hauflo id the base knows,
    or one of another kind) and what he changed in a Hauflo item — opaque placeholders, a keyed hash of what they hold"""
    if not isinstance(current, dict):
        return current
    B = Index(base)
    C = Index(current, expect=B)

    def sign(v: Any) -> str:
        return hmac.new(key, json.dumps(v, sort_keys=True, ensure_ascii=False, default=str).encode(), hashlib.sha256).hexdigest()[:24]

    def node_out(c: dict[str, Any], level: int) -> dict[str, Any]:
        h = hid(c)
        if h is None or h not in B.node or id(c) not in C.first:
            return {'type': LOCAL, 'hash': sign(c)}
        key_ = kids_key(c, level)
        own = own_of(c, level)
        mine = cloud(own, level)
        out = copy.deepcopy(mine) if mine == cloud(own_of(B.node[h], level), level) else {ID: h, 'type': LOCAL, 'edited': sign(own)}
        if key_ is not None:
            out[key_] = kids_out(c, level)
        return out

    def kids_out(node: dict[str, Any], level: int) -> list[Any]:
        cl = child_level_of(level, kids_key(node, level))
        return [node_out(x, cl) if isinstance(x, dict) else {'type': LOCAL, 'hash': sign(x)} for x in kids_of(node, level)]
    own = {k: v for k, v in current.items() if k != 'views'}
    base_own = {k: v for k, v in base.items() if k != 'views'}
    out: dict[str, Any] = copy.deepcopy(own) if own == base_own else {'type': LOCAL, 'edited': sign(own)}
    if 'views' in current:
        out['views'] = kids_out(current, 0)
    return out
