"""Walking a dashboard tree: card ids, keys and titles."""
from __future__ import annotations
import copy
from typing import Any
from .const import CARD_PREFIX, CONTENT_KEYS, ENTITY_RE, ID, LAYOUT, ROOT, TITLE_KEYS, USER_KEYS

def kids_key(node: dict[str, Any], level: int) -> str | None:
    if level == 0:
        return 'views'
    if level == 1:
        return 'sections' if isinstance(node.get('sections'), list) or node.get('type') == 'sections' else 'cards'
    if level == 2:
        return 'cards'
    if level == 3 and node.get('type') == LAYOUT:
        return 'cards'
    return None

def holds(node: dict[str, Any], level: int) -> bool:
    """A node with children of its own (a view, a section, a layout card) — not a plain card."""
    return kids_key(node, level) is not None

def child_level_of(level: int, key: str | None) -> int:
    return 1 if level == 0 else 2 if key == 'sections' else 3

def kids_of(node: dict[str, Any], level: int) -> list[Any]:
    key = kids_key(node, level)
    v = node.get(key) if key else None
    return v if isinstance(v, list) else []

def own_of(node: dict[str, Any], level: int) -> dict[str, Any]:
    """The node without its children: what an edit of the node itself changes."""
    key = kids_key(node, level)
    return {k: v for k, v in node.items() if k != key}

def user_keyed(node: dict[str, Any], level: int) -> bool:
    kind = node.get('type')
    return level == 3 and isinstance(kind, str) and kind.startswith(CARD_PREFIX) and (kind != LAYOUT)

def cloud(own: dict[str, Any], level: int) -> dict[str, Any]:
    """A node's own keys that are Hauflo's (its card without the integrator's rows)."""
    return {k: v for k, v in own.items() if k not in USER_KEYS} if user_keyed(own, level) else own

def user(own: dict[str, Any], level: int) -> dict[str, Any]:
    """The integrator's rows of a Hauflo card, as HA holds them."""
    return {k: copy.deepcopy(own[k]) for k in USER_KEYS if k in own} if user_keyed(own, level) else {}

def hid(node: Any) -> str | None:
    v = node.get(ID) if isinstance(node, dict) else None
    return v if isinstance(v, str) and v else None

def _kind(node: dict[str, Any], level: int) -> tuple[int, str | None]:
    return (level, kids_key(node, level))

class Index:
    """Hauflo ids of a config: the node, its level, its parent's id and the view it is on (first occurrence wins).
    With `expect` (the base), an id of another kind there is no Hauflo id here — the node is local."""

    def __init__(self, cfg: Any, expect: Index | None=None) -> None:
        self.node: dict[str, dict[str, Any]] = {}
        self.level: dict[str, int] = {}
        self.kind: dict[str, tuple[int, str | None]] = {}
        self.parent: dict[str, str] = {}
        self.view: dict[str, str | None] = {}
        self.first: set[int] = set()
        self.expect = expect
        if isinstance(cfg, dict):
            self._walk(cfg, 0, ROOT, None)

    def _walk(self, node: dict[str, Any], level: int, pid: str, view: str | None) -> None:
        for c in kids_of(node, level):
            if not isinstance(c, dict):
                continue
            cl = child_level_of(level, kids_key(node, level))
            h = hid(c)
            here = pid
            if h is not None and self.expect is not None and (h in self.expect.kind) and (self.expect.kind[h] != _kind(c, cl)):
                h = None
            if h is not None and h not in self.node:
                self.node[h], self.level[h], self.kind[h], self.parent[h] = (c, cl, _kind(c, cl), pid)
                self.view[h] = h if cl == 1 else view
                self.first.add(id(c))
                here = h
            else:
                here = f'\x00local{id(c)}'
            if holds(c, cl):
                self._walk(c, cl, here, (h if here == h else None) if cl == 1 else view)

def ensure_ids(cfg: dict[str, Any]) -> dict[str, Any]:
    """Every view / section / card of Hauflo's config gets an id (positional where the cloud sent none), unique."""
    seen: set[str] = set()

    def walk(node: dict[str, Any], level: int, pid: str) -> None:
        for i, c in enumerate(kids_of(node, level)):
            if not isinstance(c, dict):
                continue
            h = hid(c) or f'~{pid}/{i}'
            base, k = (h, 2)
            while h in seen:
                h, k = (f'{base}#{k}', k + 1)
            seen.add(h)
            c[ID] = h
            cl = child_level_of(level, kids_key(node, level))
            if holds(c, cl):
                walk(c, cl, h)
    walk(cfg, 0, '')
    return cfg

def rekey(cfg: dict[str, Any], base: Any) -> dict[str, Any]:
    """An id the base knows as another kind (a section's sent as a card, a view's as a section) is a new id here: the
    base's item goes as dropped (its local children stay), never matched to something else (security re-review"""
    if not isinstance(base, dict):
        return cfg
    known = Index(base).kind
    used = set(Index(cfg).node) | set(known)

    def walk(node: dict[str, Any], level: int) -> None:
        for c in kids_of(node, level):
            if not isinstance(c, dict):
                continue
            cl = child_level_of(level, kids_key(node, level))
            h = hid(c)
            if h is not None and h in known and (known[h] != _kind(c, cl)):
                k = 2
                while f'{h}~{k}' in used:
                    k += 1
                c[ID] = f'{h}~{k}'
                used.add(c[ID])
            if holds(c, cl):
                walk(c, cl)
    walk(cfg, 0)
    return cfg

def leaves(idx: Index) -> dict[str, dict[str, Any]]:
    """The cards (a layout card is its blocks, not one more card)."""
    return {h: n for h, n in idx.node.items() if idx.level[h] == 3 and (not holds(n, 3))}

def title_of(node: dict[str, Any], level: int) -> str | None:
    """A human label of a node — never an entity id."""
    for k in TITLE_KEYS:
        v = node.get(k)
        if isinstance(v, str) and v.strip() and (not ENTITY_RE.match(v.strip())):
            return v.strip()[:80]
    if level in (1, 2):
        for c in kids_of(node, level):
            if isinstance(c, dict) and c.get('type') == 'heading':
                t = title_of(c, 3)
                if t:
                    return t
    return None

def count_cards(node: dict[str, Any], level: int) -> int:
    if level == 3 and (not holds(node, 3)):
        return 1
    return sum((count_cards(c, child_level_of(level, kids_key(node, level))) for c in kids_of(node, level) if isinstance(c, dict)))

def inner_of(node: dict[str, Any], level: int, n: int=3) -> list[str]:
    """    the types of its first cards, never their content."""
    out: list[str] = []

    def walk(c: dict[str, Any], lvl: int) -> None:
        for x in kids_of(c, lvl):
            if len(out) >= n or not isinstance(x, dict):
                continue
            cl = child_level_of(lvl, kids_key(c, lvl))
            if holds(x, cl):
                walk(x, cl)
            elif isinstance(x.get('type'), str) and x['type'] != 'heading':
                out.append(x['type'][:60])
    walk(node, level)
    return out

def card_entity(node: dict[str, Any]) -> str | None:
    """A Hauflo card's first entity (its own config, never the integrator's): the server names it from the project."""
    for k in ('entity', 'entities'):
        v = node.get(k)
        v = v[0] if isinstance(v, list) and v else v
        v = v.get('entity') if isinstance(v, dict) else v
        if isinstance(v, str) and ENTITY_RE.match(v):
            return v
    return None

def strip_ids(node: Any) -> Any:
    """A local node keeps no Hauflo id (an unknown or duplicated one) — the next merge sees it as the integrator's."""
    if isinstance(node, dict):
        out = {k: v for k, v in node.items() if k != ID}
        for key in ('views', 'sections', 'cards'):
            if isinstance(out.get(key), list):
                out[key] = [strip_ids(x) for x in out[key]]
        return out
    return node

def view_of(target: Any, url_path: str) -> str | None:
    """The page of THIS dashboard a navigation target names — `/<url_path>/<view>` (a query / a `#…` aside); None for
    anything else: another dashboard, an external or protocol-relative URL, a relative path, a pop-up `#…`, the
    dashboard itself."""
    if not isinstance(target, str):
        return None
    t = target.strip().split('#', 1)[0].split('?', 1)[0]
    if not t.startswith('/') or t.startswith('//'):
        return None
    parts = [x for x in t.split('/') if x]
    return parts[1] if len(parts) >= 2 and parts[0] == url_path else None

def ways_of(card: dict[str, Any]) -> list[Any]:
    """Where a card leads when it is a way to a page by its very purpose: Hauflo's tile's `path` (a room / zone tile,
    whatever it also shows), else — a card with nothing of its own to show — its `navigation_path` or a tap's
    `navigate`. A hold's navigation is a by-the-way, never the card's purpose."""
    out: list[Any] = []
    if isinstance(card.get('path'), str) and card['path'].strip():
        out.append(card['path'])
    if not any((k in card for k in CONTENT_KEYS)):
        if isinstance(card.get('navigation_path'), str):
            out.append(card['navigation_path'])
        tap = card.get('tap_action')
        if isinstance(tap, dict) and tap.get('action') == 'navigate':
            out.append(tap.get('navigation_path'))
    return out

def find(cfg: dict[str, Any], h: str) -> dict[str, Any] | None:
    found: dict[str, Any] | None = None

    def walk(node: dict[str, Any], level: int) -> None:
        nonlocal found
        for c in kids_of(node, level):
            if found is None and isinstance(c, dict):
                cl = child_level_of(level, kids_key(node, level))
                if hid(c) == h:
                    found = c
                elif holds(c, cl):
                    walk(c, cl)
    walk(cfg, 0)
    return found
