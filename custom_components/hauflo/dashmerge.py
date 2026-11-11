"""The client's dashboard keeps the integrator's own cards through every Hauflo deploy (owner 28.10: «я ведь буду
добавлять карточки и для других автоматизаций»).

Every view / section / card Hauflo generates carries a stable `hauflo_id`. The agent keeps the last config Hauflo
saved per dashboard (the «base», in its own store — never the cloud's word). On a deploy the cloud sends only Hauflo's
part (checked by the tunnel's `_lovelace_ok` like any dashboard); the merge happens HERE, against what HA holds now:

- what has no `hauflo_id`, or one the base doesn't know (the integrator's cards, sections, views) is kept and put back
  next to the Hauflo item it followed — anchor gone: at the end of the same parent; whole local views in place;
- a Hauflo item edited in HA (≠ base) is a conflict: the integrator's version stays unless he takes Hauflo's;
- a Hauflo item removed in HA stays removed unless he asks to bring it back; one moved stays where he put it;
- the rest becomes Hauflo's new version.

The cloud can't name a local item (it has no id the base knows), so it can't remove or rewrite one; the plan it gets
back holds counts, card types and titles — never a foreign entity id. An id is its kind too (security re-review 28.10:
a section's id sent as a card dropped the integrator's cards in it): sent at another level, it is a new item and the
old one goes as dropped. And whatever the merge, every item of the integrator's must still be in what is saved —
else the save is refused (`merge_lost_local`). Every `lovelace/config/save` of a `hauflo-…` dashboard goes through the
same merge (none stored yet: the pre-save config is copied first). Before a save that changes what the integrator
made, the pre-save config is kept as a hidden copy (the last 3 per dashboard — only copies the agent made itself; one
made before a save that took Hauflo's version over an edit of the integrator's is never pruned for those, up to 10);
copies are never read or written through the tunnel. A read of a merged dashboard through the tunnel shows Hauflo's
part only: the integrator's items are opaque placeholders (a keyed hash — a change still changes the config's hash).
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import logging
import re
import secrets
import unicodedata
from collections import Counter
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import stock as stock_cards
from .storage import HaufloStore
_LOGGER = logging.getLogger(__name__)

ID = "hauflo_id"
PLAN = "hauflo/dashboard/plan"
SAVE = "hauflo/dashboard/save"
STORE_KEY = "hauflo.dashboard_base"
META_KEY = "hauflo.dashboard_meta"  # the copies the agent made, the key of the read placeholders
MAX_COPIES = 3
# Copies made before a save that took Hauflo's version over the integrator's edit (`take`): the only place his version
# is left, so never pruned for ordinary copies (verifier 28.10: a take-save, then three trivial saves erased it). Only a
# real edit of his can be taken over, so these grow with his edits, not with the cloud's saves; capped all the same.
MAX_TAKEN_COPIES = 10
# A copy of a dashboard (hauflo-…-copy-<stamp>[-<random>]): never read or written through the tunnel.
COPY_RE = re.compile(r"-copy-\d")
LOCAL = "hauflo-local"  # a placeholder of the integrator's item in a read through the tunnel
_ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_TITLE_KEYS = ("title", "heading", "name", "text")
ROOT = "\x00root"
# A view for HA's administrators (owner 04.11 п.21: the «Сервис» floor): the cloud says this marker, the agent puts the
# admins' user ids in here, in the house — and a read through the tunnel says the marker again (no user ids leave it).
ADMINS = "hauflo:admins"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# --- the tree ---------------------------------------------------------------------------------------------------------
# Levels: 0 the config (views), 1 a view (sections, or cards in a masonry / panel view), 2 a section (cards), 3 a card.
# Hauflo's page layout (`custom:hauflo-layout`, agent 0.21: every block of a page once, owner 05.11 п.49) is a card that
# holds cards: its blocks are cards of the page (level 3) like any other — each its own id, kept, edited, removed, moved.
LAYOUT = "custom:hauflo-layout"


def _kids_key(node: dict[str, Any], level: int) -> str | None:
    if level == 0:
        return "views"
    if level == 1:
        return "sections" if isinstance(node.get("sections"), list) or node.get("type") == "sections" else "cards"
    if level == 2:
        return "cards"
    if level == 3 and node.get("type") == LAYOUT:
        return "cards"
    return None


def _holds(node: dict[str, Any], level: int) -> bool:
    """A node with children of its own (a view, a section, a layout card) — not a plain card."""
    return _kids_key(node, level) is not None


def _child_level(level: int, key: str | None) -> int:
    return 1 if level == 0 else 2 if key == "sections" else 3


def _kids(node: dict[str, Any], level: int) -> list[Any]:
    key = _kids_key(node, level)
    v = node.get(key) if key else None
    return v if isinstance(v, list) else []


def _own(node: dict[str, Any], level: int) -> dict[str, Any]:
    """The node without its children: what an edit of the node itself changes."""
    key = _kids_key(node, level)
    return {k: v for k, v in node.items() if k != key}


# The integrator's keys of a Hauflo card (0.32 п.6: the block's own rows — renamed, hidden, reordered, added in the card's
# editor): his, field by field. The cloud never sends or reads them; a re-apply keeps them, an edit of them alone is no
# conflict and doesn't freeze the card — Hauflo's new version of its own keys (area, block, others…) still applies.
USER_KEYS = ("items", "auto", "order")
_CARD_PREFIX = "custom:hauflo-"


def _user_keyed(node: dict[str, Any], level: int) -> bool:
    kind = node.get("type")
    return level == 3 and isinstance(kind, str) and kind.startswith(_CARD_PREFIX) and kind != LAYOUT


def _cloud(own: dict[str, Any], level: int) -> dict[str, Any]:
    """A node's own keys that are Hauflo's (its card without the integrator's rows)."""
    return {k: v for k, v in own.items() if k not in USER_KEYS} if _user_keyed(own, level) else own


def _user(own: dict[str, Any], level: int) -> dict[str, Any]:
    """The integrator's rows of a Hauflo card, as HA holds them."""
    return {k: copy.deepcopy(own[k]) for k in USER_KEYS if k in own} if _user_keyed(own, level) else {}


def _hid(node: Any) -> str | None:
    v = node.get(ID) if isinstance(node, dict) else None
    return v if isinstance(v, str) and v else None


def _kind(node: dict[str, Any], level: int) -> tuple[int, str | None]:
    """What an id stands for: its level and what its children are (a sections view ≠ a masonry view)."""
    return level, _kids_key(node, level)


class _Index:
    """Hauflo ids of a config: the node, its level, its parent's id and the view it is on (first occurrence wins).
    With `expect` (the base), an id of another kind there is no Hauflo id here — the node is local."""

    def __init__(self, cfg: Any, expect: _Index | None = None) -> None:
        self.node: dict[str, dict[str, Any]] = {}
        self.level: dict[str, int] = {}
        self.kind: dict[str, tuple[int, str | None]] = {}
        self.parent: dict[str, str] = {}
        self.view: dict[str, str | None] = {}
        self.first: set[int] = set()  # python ids of the nodes that hold their hauflo_id first
        self.expect = expect
        if isinstance(cfg, dict):
            self._walk(cfg, 0, ROOT, None)

    def _walk(self, node: dict[str, Any], level: int, pid: str, view: str | None) -> None:
        for c in _kids(node, level):
            if not isinstance(c, dict):
                continue
            cl = _child_level(level, _kids_key(node, level))
            h = _hid(c)
            here = pid
            if h is not None and self.expect is not None and h in self.expect.kind and self.expect.kind[h] != _kind(c, cl):
                h = None  # the base's id on another kind of item: not the base's item
            if h is not None and h not in self.node:
                self.node[h], self.level[h], self.kind[h], self.parent[h] = c, cl, _kind(c, cl), pid
                self.view[h] = h if cl == 1 else view
                self.first.add(id(c))
                here = h
            else:
                # A local container: its children's parent is itself (by python id) — never a Hauflo id.
                here = f"\x00local{id(c)}"
            if _holds(c, cl):
                self._walk(c, cl, here, (h if here == h else None) if cl == 1 else view)


def ensure_ids(cfg: dict[str, Any]) -> dict[str, Any]:
    """Every view / section / card of Hauflo's config gets an id (positional where the cloud sent none), unique."""
    seen: set[str] = set()

    def walk(node: dict[str, Any], level: int, pid: str) -> None:
        for i, c in enumerate(_kids(node, level)):
            if not isinstance(c, dict):
                continue
            h = _hid(c) or f"~{pid}/{i}"
            base, k = h, 2
            while h in seen:
                h, k = f"{base}#{k}", k + 1
            seen.add(h)
            c[ID] = h
            cl = _child_level(level, _kids_key(node, level))
            if _holds(c, cl):
                walk(c, cl, h)

    walk(cfg, 0, "")
    return cfg


def rekey(cfg: dict[str, Any], base: Any) -> dict[str, Any]:
    """An id the base knows as another kind (a section's sent as a card, a view's as a section) is a new id here: the
    base's item goes as dropped (its local children stay), never matched to something else (security re-review 28.10)."""
    if not isinstance(base, dict):
        return cfg
    known = _Index(base).kind
    used = set(_Index(cfg).node) | set(known)

    def walk(node: dict[str, Any], level: int) -> None:
        for c in _kids(node, level):
            if not isinstance(c, dict):
                continue
            cl = _child_level(level, _kids_key(node, level))
            h = _hid(c)
            if h is not None and h in known and known[h] != _kind(c, cl):
                k = 2
                while f"{h}~{k}" in used:
                    k += 1
                c[ID] = f"{h}~{k}"
                used.add(c[ID])
            if _holds(c, cl):
                walk(c, cl)

    walk(cfg, 0)
    return cfg


def _leaves(idx: _Index) -> dict[str, dict[str, Any]]:
    """The cards (a layout card is its blocks, not one more card)."""
    return {h: n for h, n in idx.node.items() if idx.level[h] == 3 and not _holds(n, 3)}


# --- the merge --------------------------------------------------------------------------------------------------------


def _title(node: dict[str, Any], level: int) -> str | None:
    """A human label of a node — never an entity id."""
    for k in _TITLE_KEYS:
        v = node.get(k)
        if isinstance(v, str) and v.strip() and not _ENTITY_RE.match(v.strip()):
            return v.strip()[:80]
    if level in (1, 2):
        for c in _kids(node, level):
            if isinstance(c, dict) and c.get("type") == "heading":
                t = _title(c, 3)
                if t:
                    return t
    return None


def _count_cards(node: dict[str, Any], level: int) -> int:
    if level == 3 and not _holds(node, 3):
        return 1
    return sum(_count_cards(c, _child_level(level, _kids_key(node, level))) for c in _kids(node, level) if isinstance(c, dict))


def _inner(node: dict[str, Any], level: int, n: int = 3) -> list[str]:
    """What a page / block / stack holds, for a label (tester 05.11: «по одной» said only «блок», «блок», «блок»):
    the types of its first cards, never their content."""
    out: list[str] = []

    def walk(c: dict[str, Any], lvl: int) -> None:
        for x in _kids(c, lvl):
            if len(out) >= n or not isinstance(x, dict):
                continue
            cl = _child_level(lvl, _kids_key(c, lvl))
            if _holds(x, cl):
                walk(x, cl)
            elif isinstance(x.get("type"), str) and x["type"] != "heading":
                out.append(x["type"][:60])

    walk(node, level)
    return out


def _card_entity(node: dict[str, Any]) -> str | None:
    """A Hauflo card's first entity (its own config, never the integrator's): the server names it from the project."""
    for k in ("entity", "entities"):
        v = node.get(k)
        v = v[0] if isinstance(v, list) and v else v
        v = v.get("entity") if isinstance(v, dict) else v
        if isinstance(v, str) and _ENTITY_RE.match(v):
            return v
    return None


def _strip_ids(node: Any) -> Any:
    """A local node keeps no Hauflo id (an unknown or duplicated one) — the next merge sees it as the integrator's."""
    if isinstance(node, dict):
        out = {k: v for k, v in node.items() if k != ID}
        for key in ("views", "sections", "cards"):
            if isinstance(out.get(key), list):
                out[key] = [_strip_ids(x) for x in out[key]]
        return out
    return node


class Merge:
    def __init__(self, new: dict[str, Any], current: Any, base: dict[str, Any], take: Any = (), restore: Any = (), skip: Any = ()) -> None:
        self.new, self.current, self.base = new, current if isinstance(current, dict) else {"views": []}, base
        # The old layout's extra copies of a block (`upgrade`): Hauflo's layout going, not a change to say.
        self.skip: set[str] = set(skip)
        self.B = _Index(base)
        # HA's items of another kind than the base's under its id are the integrator's; the new config's are new ids.
        self.new = rekey(new, base)
        self.N, self.C = _Index(self.new), _Index(self.current, expect=self.B)
        self.take_all, self.take = take is True, set(take) if isinstance(take, list) else set()
        self.restore_all, self.restore = restore is True, set(restore) if isinstance(restore, list) else set()
        self.local: list[dict[str, Any]] = []
        self.conflicts: list[dict[str, Any]] = []
        self.removed: list[dict[str, Any]] = []
        self.moved: list[dict[str, Any]] = []
        self.restored: list[str] = []
        self.taken: list[str] = []
        # The view (its title) each of HA's nodes is on: the plan says where a kept card stays.
        self.on_view: dict[int, str | None] = {}
        for v in _kids(self.current, 0):
            if isinstance(v, dict):
                self._mark_view(v, 1, _title(v, 1))
        # Hauflo's containers the integrator dissolved: new items for them go to where their children went.
        self.adopt: dict[str, list[Any]] = {}
        self.dissolved: set[str] = set()
        self._dissolve()
        # Hauflo's pages gone whose integrator's items moved onto the new page of the same room (W1-5): python ids.
        self.rehomed: set[int] = set()
        self.held: dict[int, list[tuple[int, dict[str, Any]]]] = {}
        self._rehome()
        self.result = self._build_root()

    def _mark_view(self, node: dict[str, Any], level: int, title: str | None) -> None:
        for c in _kids(node, level):
            if isinstance(c, dict):
                self.on_view[id(c)] = title
                cl = _child_level(level, _kids_key(node, level))
                if _holds(c, cl):
                    self._mark_view(c, cl, title)

    # what a current node is
    def _hauflo(self, node: dict[str, Any]) -> str | None:
        """Its Hauflo id when the base knows it and this is its first occurrence in HA."""
        h = _hid(node)
        return h if h is not None and h in self.B.node and id(node) in self.C.first else None

    def _edited(self, h: str) -> bool:
        """Touched by the integrator at all (his rows included): Hauflo dropping it keeps it as his."""
        lvl = self.B.level[h]
        return _own(self.C.node[h], lvl) != _own(self.B.node[h], lvl)

    def _cloud_edited(self, h: str) -> bool:
        """Hauflo's own keys edited by the integrator (his rows aside): Hauflo's new version of them is a conflict."""
        lvl = self.B.level[h]
        return _cloud(_own(self.C.node[h], lvl), lvl) != _cloud(_own(self.B.node[h], lvl), lvl)

    def _moved(self, h: str) -> bool:
        return h in self.C.node and self.C.parent.get(h) != self.B.parent.get(h)

    def _in_current(self, h: str) -> bool:
        return h in self.C.node and h in self.B.node

    def _where(self, idx: _Index, h: str) -> str | None:
        v = idx.view.get(h)
        return _title(idx.node[v], 1) if v and v in idx.node and v != h else None

    def _label(self, idx: _Index, h: str, **extra: Any) -> dict[str, Any]:
        node, lvl = idx.node[h], idx.level[h]
        out: dict[str, Any] = {"id": h, "kind": ("view", "section", "card")[lvl - 1]}
        if lvl == 3 and isinstance(node.get("type"), str):
            out["type"] = node["type"][:60]
        if t := _title(node, lvl):
            out["title"] = t
        if _holds(node, lvl):
            out["cards"] = _count_cards(node, lvl)
            if inner := _inner(node, lvl):
                out["inner"] = inner
        elif e := _card_entity(node):
            out["entity"] = e
        if w := self._where(idx, h):
            out["where"] = w
        return {**out, **extra}

    def _dissolve(self) -> None:
        """A Hauflo container (section, stack) gone from HA while some of its Hauflo children live on elsewhere (moved
        out by the integrator before he removed it) is not brought back empty (W1-3): what Hauflo would put into it
        that HA lacks goes to the end of the Hauflo container holding most of those children (with restore asked for,
        the removed ones too). No such container in the new config: the old way, `_children` brings it back."""
        for h, lvl in self.B.level.items():
            if lvl < 2 or h in self.C.node or h not in self.N.node or not _holds(self.B.node[h], lvl):
                continue
            where = Counter(self.C.parent[x] for x, p in self.B.parent.items() if p == h and x in self.C.node and self.C.parent[x] in self.N.node and self.C.parent[x] != h)
            if not where:
                continue
            back = self.restore_all or h in self.restore
            kids = [self._fresh(i) for x in _kids(self.N.node[h], lvl) if isinstance(x, dict) and (i := _hid(x)) and (back or i not in self.B.node) and i not in self.C.node]
            self.dissolved.add(h)
            self.adopt.setdefault(where.most_common(1)[0][0], []).extend(kids)
            self.restored.extend(k[ID] for k in kids if back and k[ID] in self.B.node)

    def _home(self, c: dict[str, Any]) -> str | None:
        """The new config's page for the same room / purpose as Hauflo's page `c` it no longer has (a design switch:
        Atelier `komnaty_spalnia` ↔ classic `etazh_1_spalnia`, or the same path as another kind of view): its id under
        a new key (`rekey`), else its path, else its title — unique among Hauflo's new pages of the same kind (a room's
        subview ↔ a room's subview), two «Спальня» on two floors told apart by the longest common end of the path.
        Never a page the integrator removed (it stays removed, nothing is put on it)."""
        h = _hid(c)
        top = [x for x in _kids(self.new, 0) if isinstance(x, dict) and (i := _hid(x)) and i in self.N.node and not (i in self.B.node and i not in self.C.node)]
        if not top or h is None:
            return None
        hit = [x for x in top if re.fullmatch(re.escape(h) + r"~\d+", _hid(x) or "")]
        path = c.get("path") if isinstance(c.get("path"), str) and c.get("path") else None
        if not hit and path:
            hit = [x for x in top if x.get("path") == path]
        if not hit and (name := _same_name(_title(c, 1))):
            hit = [x for x in top if _same_name(_title(x, 1)) == name and bool(x.get("subview")) == bool(c.get("subview"))]
            if len(hit) > 1 and path:

                def tail(x: dict[str, Any]) -> int:
                    a, b = path[::-1], str(x.get("path") or "")[::-1]
                    n = 0
                    while n < min(len(a), len(b)) and a[n] == b[n]:
                        n += 1
                    return n

                best = max(tail(x) for x in hit)
                hit = [x for x in hit if tail(x) == best]
        return _hid(hit[0]) if len(hit) == 1 else None

    def _rehome(self) -> None:
        """The integrator's items on a page of Hauflo's the new config no longer has (a design switch, W1-5) go to the
        end of the new page of the same room (`_home`), not to a page of their own («<path>-kept», an extra tab for
        ever). No such page, or one that can't hold them as they are (his sections on a page of cards): the old way."""
        for c in _kids(self.current, 0):
            if not isinstance(c, dict) or (h := self._hauflo(c)) is None or h in self.N.node or self._edited(h):
                continue
            t = self._home(c)
            if t is None:
                continue
            cl = _child_level(1, _kids_key(self.N.node[t], 1))
            items = self._orphans(c, 1)
            if cl == 3 and any(lvl != 3 for lvl, _ in items):
                self.held[id(c)] = items  # his sections can't sit on a page of cards as they are: a page of their own
                continue
            self.rehomed.add(id(c))
            self.adopt.setdefault(t, []).extend(self._fit(items, cl, c))

    # building
    def _build_root(self) -> dict[str, Any]:
        own = {k: v for k, v in self.new.items() if k != "views"}
        return {**copy.deepcopy(own), "views": self._children(ROOT, 0, self.new, self.current)}

    def _build(self, h: str) -> dict[str, Any]:
        """Hauflo's item as it goes to HA: its new version (or the integrator's, in a conflict he keeps) with merged children."""
        n, lvl = self.N.node[h], self.N.level[h]
        own = _cloud(_own(n, lvl), lvl)
        if self._in_current(h):
            cur_own = _own(self.C.node[h], self.B.level[h])
            if self._cloud_edited(h) and _cloud(cur_own, lvl) != own:
                taken = self.take_all or h in self.take
                self.conflicts.append(self._label(self.B, h, kept="hauflo" if taken else "yours", updated=own != _cloud(_own(self.B.node[h], lvl), lvl)))
                if not taken:
                    own = _cloud(cur_own, lvl)
                else:
                    self.taken.append(h)
            # His rows stay his whichever version of Hauflo's keys goes (taken or not).
            if _user_keyed(own, lvl):
                own = {**own, **_user(cur_own, lvl)}
        out = copy.deepcopy(own)
        out[ID] = h
        key = _kids_key(n, lvl)
        if key is not None:
            out[key] = self._children(h, lvl, n, self.C.node.get(h) if self._in_current(h) else None)
        return out

    def _children(self, h: str, level: int, n_node: dict[str, Any], c_node: dict[str, Any] | None) -> list[Any]:
        out: list[Any] = []
        for n in _kids(n_node, level):
            if not isinstance(n, dict):
                continue
            nh = _hid(n)
            if nh is None:
                continue
            if nh in self.dissolved:
                continue  # its items are placed in the section its children went to
            if nh in self.B.node and nh not in self.C.node:
                # Removed in HA: stays removed unless asked back.
                if self.restore_all or nh in self.restore:
                    back = self._fresh(nh)
                    # Not an empty shell of a container whose items all live elsewhere (W1-3).
                    key = _kids_key(back, self.N.level[nh])
                    if key is not None and not back[key] and _kids(self.N.node[nh], self.N.level[nh]):
                        continue
                    self.restored.append(nh)
                    out.append(back)
                else:
                    self.removed.append(self._label(self.B, nh))
                continue
            if self._in_current(nh) and self._moved(nh):
                continue  # where the integrator put it (placed with its current parent)
            out.append(self._build(nh))
        if c_node is None:
            return out + self.adopt.get(h, [])
        out = self._keep_order(h, level, out, c_node)
        self._float(out, level, c_node)
        return out + self.adopt.get(h, [])

    def _keep_order(self, h: str, level: int, out: list[Any], c_node: dict[str, Any]) -> list[Any]:
        """The integrator's order of Hauflo's items under one parent (dragged in HA's edit mode) beats Hauflo's: when
        the items still in both are in another order than the base's, they stay in his order and Hauflo's new or
        restored items go after them (HA's own rule for an added card: the end of the section). Untouched order:
        Hauflo's, its new items where Hauflo puts them."""
        b_node = self.base if h == ROOT else self.B.node.get(h)
        if not isinstance(b_node, dict):
            return out

        def pos(node: dict[str, Any], idx: _Index, mine: bool) -> dict[str, int]:
            got: dict[str, int] = {}
            for i, c in enumerate(_kids(node, level)):
                x = self._hauflo(c) if mine else _hid(c)
                if x is not None and idx.parent.get(x) == h and x not in got:
                    got[x] = i
            return got

        cur, was = pos(c_node, self.C, True), pos(b_node, self.B, False)
        ids = [x.get(ID) for x in out if isinstance(x, dict)]
        common = [x for x in ids if x in cur and x in was]
        mine = sorted(common, key=lambda x: cur[x])
        if mine == sorted(common, key=lambda x: was[x]):
            return out
        by = {x.get(ID): x for x in out if isinstance(x, dict)}
        keep = set(common)
        return [by[x] for x in mine] + [x for x in out if not (isinstance(x, dict) and x.get(ID) in keep)]

    def _fresh(self, h: str) -> dict[str, Any]:
        """Hauflo's new item with its new children (a removed item brought back) — less what HA still has elsewhere
        (moved out by the integrator before he removed this one: it stays where it is, not twice)."""

        def strip(node: dict[str, Any], level: int) -> dict[str, Any]:
            key = _kids_key(node, level)
            out = {k: copy.deepcopy(v) for k, v in node.items() if k != key}
            if key is not None:
                cl = _child_level(level, key)
                out[key] = [strip(x, cl) for x in _kids(node, level) if isinstance(x, dict) and not self._in_current(_hid(x) or "")]
            return out

        return strip(self.N.node[h], self.N.level[h])

    def _float(self, out: list[Any], level: int, c_node: dict[str, Any]) -> None:
        """The current children of a Hauflo parent that aren't in place by Hauflo's order: the integrator's own, moved
        Hauflo items, edited ones Hauflo dropped — each after the Hauflo item it followed (gone: at the end)."""
        child_level = _child_level(level, _kids_key(c_node, level))
        pos: int | None = 0
        tail: list[Any] = []

        def index_of(h: str) -> int | None:
            for i, x in enumerate(out):
                if isinstance(x, dict) and x.get(ID) == h:
                    return i
            return None

        def put(node: Any) -> None:
            nonlocal pos
            if pos is None:
                tail.append(node)
            else:
                out.insert(pos, node)
                pos += 1

        for c in _kids(c_node, level):
            if not isinstance(c, dict):
                continue
            h = self._hauflo(c)
            if h is None:
                node = self._local(c, child_level)
                self.local.append(self._local_label(c, child_level))
                put(node)
                continue
            if h in self.N.node and not self._moved(h):
                i = index_of(h)
                pos = None if i is None else i + 1
                continue
            if h in self.N.node:  # moved here by the integrator
                self.moved.append(self._label(self.B, h))
                put(self._build(h))
                continue
            # Hauflo dropped it.
            if self._edited(h):
                self.local.append(self._local_label(c, child_level, dropped=h))
                put(self._local(c, child_level))
            elif id(c) not in self.rehomed:
                items = self.held.pop(id(c)) if id(c) in self.held else self._orphans(c, child_level)
                tail.extend(self._fit(items, child_level, c))
            pos = None
        out.extend(tail)

    def _local(self, c: dict[str, Any], level: int) -> dict[str, Any]:
        """The integrator's item as it is, its Hauflo children (moved in) in their new version."""
        key = _kids_key(c, level)
        out = {k: copy.deepcopy(v) for k, v in c.items() if k not in (ID, key)}
        if key is None:
            return _strip_ids(c)
        kids: list[Any] = []
        cl = _child_level(level, key)
        for x in _kids(c, level):
            if not isinstance(x, dict):
                kids.append(copy.deepcopy(x))
                continue
            h = self._hauflo(x)
            if h is None:
                kids.append(self._local(x, cl))
            elif h in self.N.node:
                if self._moved(h):
                    self.moved.append(self._label(self.B, h))
                    kids.append(self._build(h))
                # else: still under its Hauflo parent in HA — built there by Hauflo's order, not twice (a Hauflo
                # section the integrator edited and Hauflo dropped, its card now in another of Hauflo's sections).
            elif self._edited(h):
                kids.append(self._local(x, cl))
            else:
                kids.extend(self._fit(self._orphans(x, cl), cl, x))
        out[key] = kids
        return out

    def _orphans(self, c: dict[str, Any], level: int) -> list[tuple[int, dict[str, Any]]]:
        """What a Hauflo item Hauflo dropped still holds that must stay: the integrator's, and Hauflo's moved into it."""
        if not _holds(c, level):
            return []
        out: list[tuple[int, dict[str, Any]]] = []
        cl = _child_level(level, _kids_key(c, level))
        for x in _kids(c, level):
            if not isinstance(x, dict):
                continue
            h = self._hauflo(x)
            if h is None:
                self.local.append(self._local_label(x, cl))
                out.append((cl, self._local(x, cl)))
            elif h in self.N.node:
                if self._moved(h):
                    self.moved.append(self._label(self.B, h))
                    out.append((cl, self._build(h)))
            elif self._edited(h):
                self.local.append(self._local_label(x, cl, dropped=h))
                out.append((cl, self._local(x, cl)))
            else:
                out.extend(self._orphans(x, cl))
        return out

    def _fit(self, items: list[tuple[int, dict[str, Any]]], level: int, holder: dict[str, Any]) -> list[dict[str, Any]]:
        """Items of any level as children of `level`: cards wrapped in a section, sections and cards in a view (made
        from the dropped `holder` — its title — when it was one)."""
        if not items:
            return []
        if level == 3:
            out: list[dict[str, Any]] = []
            for lvl, o in items:
                out.extend([o] if lvl == 3 else [x for x in o.get("cards", []) if isinstance(x, dict)])
            return out
        if level == 2:
            out, run = [], []
            for lvl, o in items:
                if lvl == 3:
                    run.append(o)
                    continue
                if run:
                    out.append({"type": "grid", "cards": run})
                    run = []
                out.append(o)
            if run:
                out.append({"type": "grid", "cards": run})
            return out
        # level 1: a view of its own
        views = [o for lvl, o in items if lvl == 1]
        rest = [(lvl, o) for lvl, o in items if lvl != 1]
        if rest:
            src = holder if isinstance(holder, dict) and _hid(holder) and self.B.level.get(_hid(holder) or "") == 1 else {}
            view = {k: copy.deepcopy(v) for k, v in src.items() if k in ("title", "path", "icon", "type", "max_columns", "subview", "back_path")}
            if view.get("path"):
                view["path"] = f"{view['path']}-kept"
            if any(lvl == 2 for lvl, _ in rest) or view.get("type") == "sections" or not view:
                view["type"] = "sections"
                view["sections"] = self._fit(rest, 2, holder)
            else:
                view["cards"] = self._fit(rest, 3, holder)
            views.append(view)
        return views

    def _local_label(self, c: dict[str, Any], level: int, dropped: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": ("view", "section", "card")[level - 1]}
        if level == 3 and isinstance(c.get("type"), str):
            out["type"] = c["type"][:60]
        if t := _title(c, level):
            out["title"] = t
        if _holds(c, level):
            out["cards"] = _count_cards(c, level)
            if inner := _inner(c, level):
                out["inner"] = inner
        if level > 1 and (w := self.on_view.get(id(c))):
            out["where"] = w
        if dropped:
            # Hauflo's, edited in HA, no longer in Hauflo's version: kept as the integrator's. Its Hauflo id (the cloud's
            # own) lets the loss guard say «сохранится как ваше» instead of «удалится» (0.33 Р2).
            out["dropped"] = True
            out["id"] = dropped
        return out

    # the integrator's items: all still there
    @staticmethod
    def _fp(node: dict[str, Any], level: int) -> str:
        if not _holds(node, level):
            return json.dumps(_strip_ids(node), sort_keys=True, ensure_ascii=False, default=str)
        key = _kids_key(node, level)
        return json.dumps([level, {k: v for k, v in node.items() if k not in (ID, key)}], sort_keys=True, ensure_ascii=False, default=str)

    def _prints(self, cfg: dict[str, Any], local_only: bool) -> Counter[str]:
        out: Counter[str] = Counter()

        def walk(node: dict[str, Any], level: int) -> None:
            for c in _kids(node, level):
                if not isinstance(c, dict):
                    continue
                cl = _child_level(level, _kids_key(node, level))
                if not local_only or self._hauflo(c) is None:
                    out[self._fp(c, cl)] += 1
                if _holds(c, cl):
                    walk(c, cl)

        walk(cfg, 0)
        return out

    def lost(self) -> int:
        """How many of the integrator's items (a view / section / card with no Hauflo id the base knows) the merged
        config no longer holds as they are — above 0 the save is refused, whatever the cloud sent."""
        missing = self._prints(self.current, True) - self._prints(self.result, False)
        return sum(missing.values())

    # the summary
    def updates(self) -> dict[str, int]:
        nb, nn = _leaves(self.B), _leaves(self.N)
        return {
            "added": sum(1 for h in nn if h not in nb),
            "changed": sum(1 for h in nn if h in nb and nn[h] != nb[h]),
            "removed": sum(1 for h in nb if h not in nn and h not in self.skip),
        }

    def pages(self) -> list[dict[str, Any]]:
        """What this save changes in HA, page by page (owner 05.11 п.54б: «изменится N блоков на страницах …», the rest
        stays as it is): Hauflo's new blocks (and ones brought back), its blocks with a new version where HA holds
        them as Hauflo saved them (or the integrator took Hauflo's), its blocks gone that HA still held unedited. What
        the integrator removed and keeps removed, his edits he keeps, his own cards: no change."""
        nb, nn = _leaves(self.B), _leaves(self.N)
        rows: dict[str, dict[str, Any]] = {}

        def bump(idx: _Index, h: str, what: str) -> None:
            v = idx.view.get(h)
            if not v or v not in idx.node:
                return
            row = rows.setdefault(v, {"id": v, "added": 0, "changed": 0, "removed": 0})
            if "title" not in row and (t := _title(idx.node[v], 1)):
                row["title"] = t
            row[what] += 1

        taken, restored = set(self.taken), set(self.restored)
        for h in nn:
            if h not in nb:
                bump(self.N, h, "added")
            elif not self._in_current(h):
                if h in restored or any(self._is_under(h, r) for r in restored):
                    bump(self.N, h, "added")
            elif nn[h] != nb[h] and (not self._cloud_edited(h) or h in taken):
                bump(self.N, h, "changed")
        for h in nb:
            if h not in nn and h not in self.skip and self._in_current(h) and not self._edited(h):
                bump(self.B, h, "removed")
        # Before / after: the cards on that page in HA now and once saved (his included).
        R = _Index(self.result)
        for v, row in rows.items():
            row["before"] = _count_cards(self.C.node[v], 1) if v in self.C.node else 0
            row["after"] = _count_cards(R.node[v], 1) if v in R.node else 0
        return list(rows.values())[:200]

    def _is_under(self, h: str, top: str) -> bool:
        p = self.N.parent.get(h)
        while p and p != ROOT:
            if p == top:
                return True
            p = self.N.parent.get(p)
        return False

    def untouched(self) -> list[str]:
        """Owner 05.11 п.54а — the agent's own check before a save: every Hauflo item of HA's this save does not change
        (not updated by Hauflo, not taken, not brought back, not dropped) is in what is saved exactly as HA holds it,
        once; an edited one Hauflo dropped is kept as his. Returns the ids that aren't — above none, nothing is saved."""
        R = _Index(self.result)
        count: Counter[str] = Counter()

        def walk(node: dict[str, Any], level: int) -> None:
            for c in _kids(node, level):
                if isinstance(c, dict):
                    cl = _child_level(level, _kids_key(node, level))
                    if (h := _hid(c)) is not None:
                        count[h] += 1
                    if _holds(c, cl):
                        walk(c, cl)

        walk(self.result, 0)
        kept = self._prints(self.result, False)
        taken = set(self.taken)
        bad: list[str] = []
        for h, c in self.C.node.items():
            if h not in self.B.node or id(c) not in self.C.first:
                continue
            lvl = self.B.level[h]
            cur = _own(c, lvl)
            if h in self.N.node:
                if count[h] > 1:
                    bad.append(h)
                    continue
                if h in taken or (not self._cloud_edited(h) and _cloud(_own(self.N.node[h], lvl), lvl) != _cloud(_own(self.B.node[h], lvl), lvl)):
                    continue  # Hauflo's new version: in the plan
                r = R.node.get(h)
                if r is None or _own(r, lvl) != cur:
                    bad.append(h)
            elif self._edited(h):
                # Dropped by Hauflo, edited by him: his, as he has it (no id now).
                if self._fp(c, lvl) not in kept:
                    bad.append(h)
        return bad

    def summary(self) -> dict[str, Any]:
        return {
            "local": self.local,
            "conflicts": self.conflicts,
            "removed": self.removed,
            "moved": self.moved,
            "restored": len(self.restored),
            "taken": len(self.taken),
            "updates": self.updates(),
            "pages": self.pages(),
        }


def unpanel(cfg: Any, new: dict[str, Any]) -> Any:
    """A Hauflo panel view the new version sends as a sections view (owner live test 28.10, «Котельная»: HA's panel view
    showed only its first card — what the family or HA's «Добавить на панель» put on the room page was hidden): read as
    that sections view, its cards in the new version's first section — in the base and in HA's config alike, so what the
    family added there stays on that page after Hauflo's card, not on a hidden «-kept» page. A copy; the rest as it is."""
    if not isinstance(cfg, dict) or not isinstance(new, dict):
        return cfg
    to_sections: dict[str, str] = {}
    for v in _kids(new, 0):
        h = _hid(v)
        secs = v.get("sections") if isinstance(v, dict) else None
        if h and isinstance(secs, list):
            first = next((_hid(s) for s in secs if _hid(s)), None)
            to_sections[h] = first or f"{h}/s:panel"
    views = cfg.get("views")
    if not to_sections or not isinstance(views, list):
        return cfg
    out_views: list[Any] = []
    changed = False
    for v in views:
        h = _hid(v)
        if h in to_sections and isinstance(v, dict) and _kids_key(v, 1) == "cards":
            cards = [c for c in v.get("cards", []) if isinstance(c, dict)] if isinstance(v.get("cards"), list) else []
            own = {k: copy.deepcopy(x) for k, x in v.items() if k not in ("cards", "type")}
            out_views.append({**own, "type": "sections", "sections": [{"type": "grid", ID: to_sections[h], "cards": copy.deepcopy(cards)}]})
            changed = True
        else:
            out_views.append(v)
    return {**cfg, "views": out_views} if changed else cfg


def _same_name(t: Any) -> str:
    return " ".join(t.split()).casefold() if isinstance(t, str) else ""


def _leads_to(card: dict[str, Any], path: str) -> bool:
    """A card of the integrator's that opens this page: its `path` / `navigation_path` (a tap's too) is the page's."""
    ways = [card.get("path"), card.get("navigation_path")]
    for k in ("tap_action", "hold_action"):
        a = card.get(k)
        if isinstance(a, dict):
            ways.append(a.get("navigation_path"))
    return any(isinstance(w, str) and (w.strip("/") == path or w.rstrip("/").endswith(f"/{path}")) for w in ways)


def _stack(card: dict[str, Any]) -> list[Any] | None:
    """The cards a card holds (a stack, a grid card, Hauflo's layout) — the integrator's tile may sit in one."""
    v = card.get("cards")
    return v if isinstance(v, list) else None


def _tiles_to(cfg: dict[str, Any], path: str) -> int:
    n = 0

    def card(c: dict[str, Any]) -> None:
        nonlocal n
        n += _hid(c) is None and _leads_to(c, path)
        for x in (_stack(c) if _hid(c) is None or c.get("type") == LAYOUT else None) or []:
            if isinstance(x, dict):
                card(x)

    def walk(node: dict[str, Any], level: int) -> None:
        for c in _kids(node, level):
            if isinstance(c, dict):
                cl = _child_level(level, _kids_key(node, level))
                if cl == 3:
                    card(c)
                else:
                    walk(c, cl)

    walk(cfg, 0)
    return n


def _opened(cfg: dict[str, Any], path: str) -> bool:
    """Some card of the dashboard — Hauflo's or the integrator's, in a stack too — opens this page by its path."""
    found = False

    def card(c: dict[str, Any]) -> None:
        nonlocal found
        found = found or _leads_to(c, path)
        for x in _stack(c) or []:
            if isinstance(x, dict):
                card(x)

    def walk(node: dict[str, Any], level: int) -> None:
        for c in _kids(node, level):
            if isinstance(c, dict):
                cl = _child_level(level, _kids_key(node, level))
                card(c) if cl == 3 else walk(c, cl)

    walk(cfg, 0)
    return found


def unique_paths(cfg: Any) -> None:
    """A page of Hauflo's on the path of a page of the integrator's (his own «diagnostics», acceptance 10.11 L8): HA opens
    the first of two pages with one path for both — Hauflo's gets the next free `<path>_2`… Only a page nothing on the
    dashboard opens by its path (the «Диагностика» tab): a room's page its tiles open keeps it (they'd lead to his)."""
    views = [v for v in (cfg.get("views") if isinstance(cfg, dict) else None) or [] if isinstance(v, dict)]
    his = {v["path"] for v in views if _hid(v) is None and isinstance(v.get("path"), str)}
    taken = {v.get("path") for v in views}
    for v in views:
        p = v.get("path")
        if _hid(v) is None or not isinstance(p, str) or p not in his or _opened(cfg, p):
            continue
        n = 2
        while f"{p}_{n}" in taken:
            n += 1
        v["path"] = f"{p}_{n}"
        taken.add(v["path"])


def twins(current: Any, new: Any) -> list[dict[str, Any]]:
    """The integrator's own EMPTY pages named as one of Hauflo's (owner 04.11 п.51, Горького 3: his «Протечки» page and
    tile, then the room made in Hauflo — two «Протечки» on «Сервис»). Offered for removal with the tiles that open them;
    nothing is lost (no card on the page) and nothing goes without his tick."""
    if not isinstance(current, dict) or not isinstance(new, dict):
        return []
    names = {_same_name(_title(v, 1)) for v in _kids(new, 0) if isinstance(v, dict) and _hid(v)} - {""}
    out: list[dict[str, Any]] = []
    for v in _kids(current, 0):
        if not isinstance(v, dict) or _hid(v) is not None:
            continue
        path, title = v.get("path"), _title(v, 1)
        if isinstance(path, str) and path.strip() and _same_name(title) in names and _count_cards(v, 1) == 0:
            out.append({"id": path.strip()[:128], "title": title, "tiles": _tiles_to(current, path.strip())})
    return out[:50]


def drop_twins(current: Any, new: Any, ids: Any) -> tuple[Any, int]:
    """`current` less the ticked twins (only what `twins` offers now) and the integrator's tiles that open them."""
    want = {x for x in ids if isinstance(x, str)} if isinstance(ids, list) else set()
    gone = {t["id"] for t in twins(current, new) if t["id"] in want}
    if not gone:
        return current, 0

    def emptied(before: dict[str, Any], after: dict[str, Any], key: str) -> bool:
        """The integrator's own section / stack that held something and holds nothing now — because of the tiles
        removed here (tester 05.11: an empty `{"type": "grid", "cards": []}` was left). One of Hauflo's stays (an
        empty Hauflo section gone from HA would read as removed by him)."""
        b, a = before.get(key), after.get(key)
        return _hid(before) is None and isinstance(b, list) and bool(b) and isinstance(a, list) and not a

    def card(c: dict[str, Any]) -> dict[str, Any] | None:
        if _hid(c) is None and any(_leads_to(c, p) for p in gone):
            return None
        # His stacks and Hauflo's layout (its blocks are cards of the page); a Hauflo card's own content is Hauflo's.
        inner = _stack(c) if _hid(c) is None or c.get("type") == LAYOUT else None
        if inner is None:
            return c
        out = {**c, "cards": [y for y in (card(x) if isinstance(x, dict) else x for x in inner) if y is not None]}
        return None if emptied(c, out, "cards") else out

    def keep(node: dict[str, Any], level: int) -> dict[str, Any]:
        key = _kids_key(node, level)
        if key is None:
            return node
        cl = _child_level(level, key)
        kids = []
        for c in _kids(node, level):
            if not isinstance(c, dict):
                kids.append(c)
                continue
            if level == 0 and _hid(c) is None and isinstance(c.get("path"), str) and c["path"].strip() in gone:
                continue
            if cl == 3:
                x = card(c)
            else:
                x = keep(c, cl)
                # A page stays (his page, its title and path); a section of his emptied by us goes.
                if cl == 2 and emptied(c, x, "cards"):
                    x = None
            if x is not None:
                kids.append(x)
        return {**node, key: kids}

    return keep(copy.deepcopy(current), 0), len(gone)


# --- a dashboard of the old ids and layout moved over (agent 0.21, owner 05.11 п.49 / п.54в) -----------------------------
# Before: a card's id hung on its section and its words (`v/<page>/s:<first card>/<kind>:<title>`), and a room page held
# each block up to three times — one section per screen width (phone / tablet / desktop) — HA's edit mode showed all.
# Now: one block once (Hauflo's layout card lays the page out by the screen), its id from what it shows. The first save
# with the new ids reads the old base and HA's config in the new ids, then merges as always:
# - each new block's old copies are found (the same content but the size, else the same thing shown — `_skey`); one
#   copy becomes the block (one the integrator edited, else the phone's, else the first), the other copies are Hauflo's
#   old layout — they go, unless he edited one into something else (kept as his card);
# - removed in HA: a block stays while any copy of it is there; all copies gone — removed by him, stays removed;
# - his cards in a copy section go to the block's section, after the block they followed (the same card in two copies:
#   once); his own sections, pages and edits stay as they are.


def _slug(v: Any) -> str:
    """haufloIds.ts `slug`."""
    s = unicodedata.normalize("NFC", "" if v is None else str(v))
    return re.sub(r"[^\w.-]+", "-", s).strip("-")[:48]


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _entity_of(v: Any) -> str | None:
    return v if isinstance(v, str) else _str(v.get("entity")) if isinstance(v, dict) else None


def _first_of(v: Any) -> str | None:
    return _entity_of(v[0]) if isinstance(v, list) and v else None


def _skey(c: dict[str, Any]) -> str:
    """haufloIds.ts `stableKey`: what a card shows, in no language."""
    kids = c.get("cards")
    inner = kids[0] if isinstance(kids, list) and kids and isinstance(kids[0], dict) else None
    tap = c.get("tap_action")
    nav = _str(tap.get("navigation_path")) if isinstance(tap, dict) else None
    key: Any
    if c.get("type") == LAYOUT:
        key = _skey(inner) if inner else ""
    else:
        key = next(
            (
                x
                for x in (
                    _str(c.get("entity")),
                    _str(c.get("intercom")),
                    _first_of(c.get("entities")),
                    _str(c.get("block")),
                    _str(c.get("area")),
                    _str(c.get("room")),
                    _str(c.get("floor")),
                    _str(c.get("path")),
                    nav,
                    _first_of(c.get("scenes")),
                    _first_of(c.get("alarms")),
                    (_entity_of(inner) or _str(inner.get("block"))) if inner else None,
                    _str(c.get("icon")) if c.get("type") == "heading" else None,
                )
                if x is not None
            ),
            "",
        )
    t = c.get("type")
    kind = _slug(re.sub(r"^custom:(hauflo-)?", "", "card" if t is None else str(t)))
    sub = f"{_slug(c['kind'])}/" if _str(c.get("kind")) and key != c.get("kind") else ""
    return f"{kind}:{sub}{_slug(key)}"


def _sizeless(node: dict[str, Any]) -> str:
    """A card's content without its id and size: the copies of one block differ only there."""
    return json.dumps({k: v for k, v in node.items() if k not in (ID, "grid_options")}, sort_keys=True, ensure_ascii=False, default=str)


def _old_ids(idx: _Index) -> bool:
    """A card's id under its section's: the old scheme."""
    return any(idx.level[h] == 3 and idx.level.get(idx.parent[h]) == 2 and h.startswith(f"{idx.parent[h]}/") for h in idx.node)


def _new_ids(idx: _Index) -> bool:
    cards = [h for h in idx.node if idx.level[h] == 3 and idx.level.get(idx.parent[h]) == 2]
    return bool(cards) and not any(h.startswith(f"{idx.parent[h]}/") for h in cards)


def upgrade(base: dict[str, Any], current: Any, new: dict[str, Any]) -> tuple[dict[str, Any], Any, dict[str, Any]] | None:
    """(the base, HA's config — copies, in the new ids and blocks; what it did) when the base has the old ids and the
    new version the new ones, else None. Nothing of HA's is dropped here: only Hauflo's ids are renamed, Hauflo's copy
    sections read as the block's section and his cards in them moved next to the block (an exact repeat of his card in
    another copy of the same section — once: `allow` says how many of which)."""
    if not isinstance(base, dict) or not isinstance(current, dict):
        return None
    B0, N = _Index(base), _Index(new)
    if not _old_ids(B0) or not _new_ids(N):
        return None
    base, current = copy.deepcopy(base), copy.deepcopy(current)
    B = _Index(base)
    C = _Index(current, expect=B)

    def media(h: str) -> str:
        p = B.parent.get(h, ROOT)
        return json.dumps(B.node[p].get("visibility"), sort_keys=True, default=str) if B.level.get(p) == 2 and B.node[p].get("visibility") else ""

    def phone(h: str) -> bool:
        """A card (or a section) shown on a phone: no screen condition, or the phone's."""
        node = B.node[h] if B.level.get(h) == 2 else B.node.get(B.parent.get(h, ROOT), {})
        m = json.dumps(node.get("visibility") or "", default=str)
        return "media_query" not in m or "max-width" in m

    # Old card → new block: the same content on the page (the k-th of its kind in its screen's copy), else the same thing.
    by_content: dict[tuple[str | None, str], list[str]] = {}
    for h, n in _leaves(N).items():
        by_content.setdefault((N.view.get(h), _sizeless(n)), []).append(h)
    seen_c: Counter[tuple[Any, ...]] = Counter()
    seen_k: Counter[tuple[Any, ...]] = Counter()
    used: set[tuple[str | None, str, str]] = set()
    target: dict[str, str] = {}
    for h, n in _leaves(B).items():
        v, m, c = B.view.get(h), media(h), _sizeless(n)
        seen_c[(v, m, c)] += 1
        options = by_content.get((v, c), [])
        i = seen_c[(v, m, c)] - 1
        t = options[i] if i < len(options) else None
        if t is None or (v, m, t) in used:
            k = _skey(n)
            seen_k[(v, m, k)] += 1
            j = seen_k[(v, m, k)]
            cand = f"{v}/{k}" if j == 1 else f"{v}/{k}#{j}"
            t = cand if cand in N.node and N.view.get(cand) == v and N.level.get(cand) == 3 and (v, m, cand) not in used else None
        if t is not None and t not in B.node:
            used.add((v, m, t))
            target[h] = t
    groups: dict[str, list[str]] = {}
    for h, t in target.items():
        groups.setdefault(t, []).append(h)
    # A block Hauflo drops: its copies are one block gone, not three.
    copies: set[str] = set()
    gone: dict[tuple[str | None, str], set[str]] = {}
    for h, n in _leaves(B).items():
        if h not in target:
            media_seen = gone.setdefault((B.view.get(h), _sizeless(n)), set())
            if media_seen and media(h) not in media_seen:
                copies.add(h)
            media_seen.add(media(h))

    def here(h: str) -> bool:
        return h in C.node and id(C.node[h]) in C.first

    def edited(h: str) -> bool:
        return _sizeless(C.node[h]) != _sizeless(B.node[h])

    def same(h: str) -> None:
        """HA's copy read as Hauflo saved it (its size or a repeat of the block's edit): not an edit of his."""
        B.node[h].clear()
        B.node[h].update(copy.deepcopy(C.node[h]))

    survivor: dict[str, str] = {}  # old id → new id
    for t, olds in groups.items():
        present = [h for h in olds if here(h)]
        changed = [h for h in present if edited(h)]
        if changed:
            keep = changed[0]
        elif present:
            keep = next((h for h in present if phone(h)), present[0])
        else:
            keep = next((h for h in olds if phone(h)), olds[0])
        survivor[keep] = t
        for h in olds:
            if h == keep:
                continue
            copies.add(h)
            if here(h) and (not edited(h) or (keep in C.node and _sizeless(C.node[h]) == _sizeless(C.node[keep]))):
                same(h)
        if here(keep) and not edited(keep):
            # A size only: the layout card lays the block out now — Hauflo's, not an edit to keep.
            same(keep)
            for node in (B.node[keep], C.node[keep]):
                node.pop("grid_options", None)
                if "grid_options" in N.node[t]:
                    node["grid_options"] = copy.deepcopy(N.node[t]["grid_options"])

    # Sections: an old section → the new section its first block is in; of a section's copies, one (the phone's, else
    # one HA still has) becomes it, his cards in the others move to it.
    def new_section(t: str) -> str | None:
        p = N.parent.get(t)
        while p and p != ROOT and N.level.get(p, 0) > 2:
            p = N.parent.get(p)
        return p if p and N.level.get(p) == 2 else None

    sec_groups: dict[str, list[str]] = {}
    for h in B.node:
        if B.level[h] != 2:
            continue
        first = next((x for x in (_hid(c) for c in _kids(B.node[h], 2) if isinstance(c, dict)) if x in target), None)
        ns = new_section(target[first]) if first else None
        if ns and ns not in B.node and N.view.get(ns) == B.view.get(h):
            sec_groups.setdefault(ns, []).append(h)
    allow: Counter[str] = Counter()
    moved_local = 0
    for ns, olds in sec_groups.items():
        present = [h for h in olds if here(h)]
        keep = next((h for h in present if phone(h)), present[0] if present else next((h for h in olds if phone(h)), olds[0]))
        others = [h for h in present if h != keep]
        survivor[keep] = ns
        if not here(keep):
            continue
        dest = _kids(C.node[keep], 2)
        prints = {Merge._fp(x, 3) for x in dest if isinstance(x, dict) and _hid(x) not in B.node}
        for o in others:
            if _own(C.node[o], 2) != _own(B.node[o], 2):
                continue  # a section he edited: his, as it is
            stay: list[Any] = []
            anchor: str | None = None
            for x in _kids(C.node[o], 2):
                hx = _hid(x) if isinstance(x, dict) else None
                if isinstance(x, dict) and hx in B.node and id(x) in C.first:
                    anchor = hx
                    stay.append(x)
                    continue
                if not isinstance(x, dict):
                    stay.append(x)
                    continue
                fp = Merge._fp(x, 3) if not _holds(x, 3) else None
                if fp is not None and fp in prints:
                    allow[fp] += 1  # the same card of his in another copy: once
                    continue
                if fp is not None:
                    prints.add(fp)
                # After the copy of the block it followed in the kept section; none — at the section's end.
                t = target.get(anchor or "")
                at = next((i + 1 for i, y in enumerate(dest) if isinstance(y, dict) and target.get(_hid(y) or "") == t), len(dest)) if t else len(dest)
                dest.insert(at, x)
                moved_local += 1
            C.node[o]["cards"] = stay

    # The new ids on the base's and HA's copies alike.
    for old, t in survivor.items():
        for node in (B.node.get(old), C.node.get(old) if here(old) else None):
            if node is not None:
                node[ID] = t
    # A new section of one layout card: the old section's cards are that card's blocks (base and HA alike), so his
    # cards in it stay after the block they followed — inside the layout.
    for ns in sec_groups:
        kids = _kids(N.node[ns], 2)
        if len(kids) != 1 or not isinstance(kids[0], dict) or kids[0].get("type") != LAYOUT or _hid(kids[0]) in B.node:
            continue
        lay = _own(kids[0], 3)
        for cfg_node in (_find(base, ns), _find(current, ns)):
            if cfg_node is not None and isinstance(cfg_node.get("cards"), list):
                cfg_node["cards"] = [{**copy.deepcopy(lay), "cards": cfg_node["cards"]}]
    return base, current, {"copies": copies, "allow": allow, "blocks": len(groups), "moved": moved_local}


def _find(cfg: dict[str, Any], h: str) -> dict[str, Any] | None:
    found: dict[str, Any] | None = None

    def walk(node: dict[str, Any], level: int) -> None:
        nonlocal found
        for c in _kids(node, level):
            if found is None and isinstance(c, dict):
                cl = _child_level(level, _kids_key(node, level))
                if _hid(c) == h:
                    found = c
                elif _holds(c, cl):
                    walk(c, cl)

    walk(cfg, 0)
    return found


def _local_prints(cfg: Any, base: dict[str, Any]) -> Counter[str]:
    """The integrator's items of a config (no Hauflo id the base knows), as `Merge.lost` counts them."""
    B = _Index(base)
    C = _Index(cfg, expect=B)
    out: Counter[str] = Counter()

    def walk(node: dict[str, Any], level: int) -> None:
        for c in _kids(node, level):
            if isinstance(c, dict):
                cl = _child_level(level, _kids_key(node, level))
                h = _hid(c)
                if h is None or h not in B.node or id(c) not in C.first:
                    out[Merge._fp(c, cl)] += 1
                if _holds(c, cl):
                    walk(c, cl)

    if isinstance(cfg, dict):
        walk(cfg, 0)
    return out


_KEY = re.compile(r"^[A-Za-z0-9_:-]{1,40}$")


def edited_where(seen: Any, base: Any, limit: int = 5) -> list[dict[str, Any]]:
    """Where HA's config differs from Hauflo's last saved version (owner 11.11 Р33: «edited» with nothing to choose and a
    copy at every save — what differs was nowhere to see). Only places, never content: the path of list indexes and
    keys, the key that differs and how (changed / added / removed / count), Hauflo's own view title and the card's type
    when the place is in one. At most `limit` places, the first found in config order."""
    out: list[dict[str, Any]] = []

    def place(path: list[Any], key: str | None, op: str) -> None:
        item: dict[str, Any] = {"path": "/".join(str(p) for p in path), "op": op}
        if key is not None:
            item["key"] = key if _KEY.match(key) else "?"
        views = base.get("views") if isinstance(base, dict) else None
        if len(path) >= 2 and path[0] == "views" and isinstance(path[1], int) and isinstance(views, list) and path[1] < len(views):
            title = views[path[1]].get("title") if isinstance(views[path[1]], dict) else None
            if isinstance(title, str) and title:
                item["view"] = title[:80]
        node: Any = base
        for p in path:
            if isinstance(node, dict) and isinstance(p, str):
                node = node.get(p)
            elif isinstance(node, list) and isinstance(p, int) and p < len(node):
                node = node[p]
            else:
                node = None
                break
        if isinstance(node, dict) and isinstance(node.get("type"), str):
            item["type"] = node["type"][:60]
        out.append(item)

    def walk(a: Any, b: Any, path: list[Any]) -> None:
        if len(out) >= limit or a == b:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for k in [*b.keys(), *(k for k in a.keys() if k not in b)]:
                if len(out) >= limit:
                    return
                if k not in a:
                    place(path, str(k), "removed")
                elif k not in b:
                    place(path, str(k), "added")
                elif a[k] != b[k]:
                    if isinstance(a[k], (dict, list)) and type(a[k]) is type(b[k]):
                        walk(a[k], b[k], [*path, str(k)])
                    else:
                        place(path, str(k), "changed")
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                place(path, None, "count")
                return
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, [*path, i])
        else:
            place(path, None, "changed")

    walk(seen, base, [])
    return out


def plan_and_merge(new: dict[str, Any], current: Any, base: dict[str, Any] | None, take: Any = (), restore: Any = ()) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the config to save, what the plan says). No base: Hauflo's config as it is, nothing known about HA's."""
    new = ensure_ids(copy.deepcopy(new))
    if base is not None:
        # A panel page of Hauflo's that is a sections page now: base and HA's config read that way first (`unpanel`).
        base, seen = unpanel(base, new), unpanel(current, new)
    new = rekey(new, base)  # unique ids whatever the cloud sent, none of another kind
    if base is None:
        return new, {"base": False}
    up = upgrade(base, seen, new)
    b2, c2, info = up if up is not None else (base, seen, None)
    m = Merge(new, c2, b2, take, restore, skip=info["copies"] if info else ())
    # His items against HA's config as it is (not only as `upgrade` read it): an exact repeat of his card in another
    # copy of a section is the only thing allowed to go.
    unique_paths(m.result)
    lost = sum((_local_prints(seen, base) - m._prints(m.result, False) - (info["allow"] if info else Counter())).values())
    report = {**m.summary(), "base": True, "edited": seen != base, "changes": m.result != current, "lost": max(m.lost(), lost), "untouched": len(m.untouched())}
    if seen != base:
        report["edited_where"] = edited_where(seen, base)
    if info:
        report["layout"] = {"blocks": info["blocks"], "copies": len(info["copies"]), "moved": info["moved"]}
    return m.result, report


def _walk_admins(node: Any, fix: Callable[[str, Any], Any]) -> Any:
    """Every view's `visible` and every card's / section's «user» visibility condition, through the whole config."""
    if isinstance(node, list):
        for x in node:
            _walk_admins(x, fix)
    elif isinstance(node, dict):
        if isinstance(node.get("visible"), list):
            node["visible"] = fix("visible", node["visible"])
        if node.get("condition") == "user" and isinstance(node.get("users"), list):
            node["users"] = fix("users", node["users"])
        for k, x in node.items():
            if k not in ("visible", "users"):
                _walk_admins(x, fix)
    return node


def expand_admins(cfg: Any, ids: list[str]) -> Any:
    """The marker (a view's `visible: [{user: ADMINS}]`, a card's `{condition: user, users: [ADMINS]}`) → HA's
    administrators (`ids`); none known — the marker stays, a list no user matches (seen in edit mode only)."""
    if not ids:
        return cfg

    def fix(key: str, v: list[Any]) -> list[Any]:
        if key == "visible" and any(isinstance(x, dict) and x.get("user") == ADMINS for x in v):
            return [x for x in v if not (isinstance(x, dict) and x.get("user") == ADMINS)] + [{"user": i} for i in ids]
        if key == "users" and ADMINS in v:
            return [x for x in v if x != ADMINS] + list(ids)
        return v

    return _walk_admins(cfg, fix)


def collapse_admins(cfg: Any, ids: list[str]) -> Any:
    """A read for the cloud: exactly the marker's expansion (a list of users equal to HA's administrators now, `ids`)
    says the marker again. Any other list (the integrator's own choice of people, or admins since changed) stays as it
    is — a read never turns someone's chosen people into «администраторы» (п.9: the cloud then pushed that back)."""
    admins = set(ids)

    def fix(key: str, v: list[Any]) -> list[Any]:
        if not admins:
            return v
        if key == "visible" and all(isinstance(x, dict) and set(x) == {"user"} for x in v) and {x["user"] for x in v} == admins and len(v) == len(admins):
            return [{"user": ADMINS}]
        if key == "users" and set(v) == admins and len(v) == len(admins):
            return [ADMINS]
        return v

    return _walk_admins(cfg, fix)


# --- who sees which page: guests with a login (guest.py «pages», owner 10.11) ----------------------------------------
# HA hides a view only by a list of users (`visible: [{user: id}]`); «everyone» is no list. While a guest with a login
# exists, every page of Hauflo's dashboards gets a list: the family (every active person of this HA but the guests) and
# the guests given this page; a page only for some people keeps its list plus those guests. A tag in the list says what
# it was before — so a read for the cloud, the merge and the base never see guests (collapse_access is the exact
# inverse). No guest: nothing is touched, the config is byte for byte what it was.
ACCESS_ALL = "hauflo:all"  # the page was for everyone (no `visible`)
ACCESS_ALL_TRUE = "hauflo:all-true"  # … said as `visible: true`
ACCESS_MORE = "hauflo:guests"  # the page had its own list: what follows the tag are guests added to it
_ACCESS_TAGS = (ACCESS_ALL, ACCESS_ALL_TRUE, ACCESS_MORE)


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


def _views(cfg: Any) -> list[Any]:
    return cfg["views"] if isinstance(cfg, dict) and isinstance(cfg.get("views"), list) else []


def expand_access(cfg: Any, access: Access | None, url_path: str) -> Any:
    """Every page of this dashboard → its list of people while a guest with a login exists (in place)."""
    if access is None or not access.active:
        return cfg
    given = access.pages.get(url_path, {})
    for v in _views(cfg):
        if not isinstance(v, dict):
            continue
        path = v.get("path")
        mine = list(given.get(path, [])) if isinstance(path, str) else []
        vis = v.get("visible", None)
        if "visible" not in v or vis is True:
            people = [*access.family, *[g for g in mine if g not in access.family]]
            v["visible"] = [{"user": ACCESS_ALL if "visible" not in v else ACCESS_ALL_TRUE}, *({"user": u} for u in people)]
        elif isinstance(vis, list):
            had = {x.get("user") for x in vis if isinstance(x, dict)}
            add = [g for g in mine if g not in had]
            if add:
                v["visible"] = [*vis, {"user": ACCESS_MORE}, *({"user": g} for g in add)]
        # `visible: false` (nobody) or anything else: as it is.
    return cfg


def collapse_access(cfg: Any, guests: set[str] | frozenset[str] = frozenset()) -> Any:
    """The pages as they were before expand_access (in place): by the tag; a list whose tag HA's own editor dropped —
    the known guests out of it, the rest is a list someone chose."""
    for v in _views(cfg):
        if not isinstance(v, dict) or not isinstance(v.get("visible"), list):
            continue
        vis = v["visible"]
        users = [x.get("user") if isinstance(x, dict) else None for x in vis]
        if ACCESS_ALL in users:
            del v["visible"]
        elif ACCESS_ALL_TRUE in users:
            v["visible"] = True
        elif ACCESS_MORE in users:
            v["visible"] = vis[: users.index(ACCESS_MORE)]
        elif guests and any(u in guests for u in users):
            v["visible"] = [x for x, u in zip(vis, users) if u not in guests]
    return cfg


# --- the agent's side -------------------------------------------------------------------------------------------------


def redact(current: Any, base: dict[str, Any], key: bytes) -> Any:
    """HA's config as the cloud may read it: Hauflo's items as they are; the integrator's (no Hauflo id the base knows,
    or one of another kind) and what he changed in a Hauflo item — opaque placeholders, a keyed hash of what they hold
    (security re-review 28.10: the cloud read his cards). Equal configs read equal, a change changes the read."""
    if not isinstance(current, dict):
        return current
    B = _Index(base)
    C = _Index(current, expect=B)

    def sign(v: Any) -> str:
        return hmac.new(key, json.dumps(v, sort_keys=True, ensure_ascii=False, default=str).encode(), hashlib.sha256).hexdigest()[:24]

    def node_out(c: dict[str, Any], level: int) -> dict[str, Any]:
        h = _hid(c)
        if h is None or h not in B.node or id(c) not in C.first:
            return {"type": LOCAL, "hash": sign(c)}
        key_ = _kids_key(c, level)
        own = _own(c, level)
        # The integrator's rows of a Hauflo card (foreign entity ids) never leave the house: his edit of Hauflo's keys
        # reads as a placeholder, his rows alone read as Hauflo's card.
        mine = _cloud(own, level)
        out = copy.deepcopy(mine) if mine == _cloud(_own(B.node[h], level), level) else {ID: h, "type": LOCAL, "edited": sign(own)}
        if key_ is not None:
            out[key_] = kids_out(c, level)
        return out

    def kids_out(node: dict[str, Any], level: int) -> list[Any]:
        cl = _child_level(level, _kids_key(node, level))
        return [node_out(x, cl) if isinstance(x, dict) else {"type": LOCAL, "hash": sign(x)} for x in _kids(node, level)]

    own = {k: v for k, v in current.items() if k != "views"}
    base_own = {k: v for k, v in base.items() if k != "views"}
    out: dict[str, Any] = copy.deepcopy(own) if own == base_own else {"type": LOCAL, "edited": sign(own)}
    if "views" in current:
        out["views"] = kids_out(current, 0)
    return out


class DashboardMerge:
    """Bases per dashboard (the agent's own store), the copies it made, and the tunnel's dashboard commands."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: HaufloStore = HaufloStore(hass, STORE_KEY)
        self.meta_store: HaufloStore = HaufloStore(hass, META_KEY)
        self.bases: dict[str, dict[str, Any]] = {}
        # url → the copies of it the agent made itself, oldest first: only these are ever pruned.
        self.copies: dict[str, list[str]] = {}
        self.taken_copies: set[str] = set()  # of those, the ones made before a take-save
        self.key: bytes = b""
        # Who sees which page (guest.py sets it: guests with a login); None — no guest module, nothing expanded.
        self.access_of: Callable[[], Any] | None = None
        # One writer of a dashboard at a time: a cloud save and a guest's grant / revoke never interleave (a revoked
        # guest's id put back by a save that read the grants before).
        self.write_lock = asyncio.Lock()

    async def _access(self) -> Access | None:
        if self.access_of is None:
            return None
        try:
            return await self.access_of()
        except Exception:  # noqa: BLE001 — not knowable now: no expansion (the next check of guest.py does it)
            _LOGGER.warning("Hauflo: who sees which page not known", exc_info=True)
            return None

    def _plain(self, current: Any, access: Access | None) -> Any:
        """HA's config without the guests' lists (collapse_access) — what the merge, the base and the cloud see."""
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
            item = getattr(self._dashboard(url_path), "config", None) or {}
            if not exists or not isinstance(current, dict) or not isinstance(item, dict) or item.get("require_admin"):
                continue
            views = []
            for v in _views(self._plain(current, access)):
                if not isinstance(v, dict) or not isinstance(v.get("path"), str) or not v["path"] or v.get("visible") is False:
                    continue
                vis = v.get("visible")
                users = [x.get("user") for x in vis if isinstance(x, dict)] if isinstance(vis, list) else []
                title = v.get("title") if isinstance(v.get("title"), str) and v.get("title") else v["path"]
                views.append({"path": v["path"], "title": title, "admin": bool(users) and all(u in admins for u in users), "sub": bool(v.get("subview"))})
            if views:
                title = item.get("title") if isinstance(item.get("title"), str) and item.get("title") else url_path
                out.append({"url_path": url_path, "title": title, "views": views})
        return out

    async def async_load(self) -> None:
        data = await self.store.async_load()
        self.bases = {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
        meta = await self.meta_store.async_load()
        meta = meta if isinstance(meta, dict) else {}
        copies = meta.get("copies")
        self.copies = {k: [x for x in v if isinstance(x, str)] for k, v in copies.items() if isinstance(v, list)} if isinstance(copies, dict) else {}
        taken = meta.get("taken")
        self.taken_copies = {x for x in taken if isinstance(x, str)} if isinstance(taken, list) else set()
        key = meta.get("key")
        if not (isinstance(key, str) and len(key) == 64):
            key = secrets.token_hex(32)
            await self._save_meta(key)
        self.key = bytes.fromhex(key)

    async def _save_meta(self, key: str | None = None) -> None:
        await self.meta_store.async_save({"copies": self.copies, "taken": sorted(self.taken_copies), "key": key or self.key.hex()})

    def manages(self, url_path: Any) -> bool:
        return isinstance(url_path, str) and url_path in self.bases

    def _dashboard(self, url_path: str) -> Any:
        from homeassistant.components.lovelace.const import LOVELACE_DATA  # noqa: PLC0415

        return self.hass.data[LOVELACE_DATA].dashboards.get(url_path)

    async def _current(self, url_path: str) -> tuple[bool, Any]:
        """(the dashboard exists in storage mode, its config or None when it has none yet)."""
        from homeassistant.components.lovelace.const import ConfigNotFound  # noqa: PLC0415

        dash = self._dashboard(url_path)
        if dash is None or getattr(dash, "mode", "storage") != "storage":
            return False, None
        try:
            return True, await dash.async_load(False)
        except ConfigNotFound:
            return True, None

    async def read(self, url_path: str) -> Any:
        """`lovelace/config` of a dashboard the agent merges, as the cloud may see it (redact). None: no config yet."""
        exists, current = await self._current(url_path)
        if not exists:
            raise MergeError("not_found", "no such storage dashboard")
        if current is None:
            raise MergeError("config_not_found", "no config yet")
        plain = self._plain(current, await self._access())
        return collapse_admins(redact(plain, self.bases[url_path], self.key), await self._admin_ids())

    async def _admin_ids(self) -> list[str]:
        """HA's administrators (active people, not HA's own system users), read here — never sent to the cloud."""
        try:
            users = await self.hass.auth.async_get_users()
        except Exception:  # noqa: BLE001 — not knowable: the marker stays (edit mode only)
            return []
        return sorted(u.id for u in users if u.is_admin and u.is_active and not u.system_generated)

    async def plan(self, url_path: str, config: dict[str, Any]) -> dict[str, Any]:
        exists, current = await self._current(url_path)
        if not exists:
            return {"base": False, "exists": False}
        current = self._plain(current, await self._access())
        base = self.bases.get(url_path) if current is not None else None
        _, out = plan_and_merge(expand_admins(ensure_ids(copy.deepcopy(config)), await self._admin_ids()), current, base)
        # «Заменить на Hauflo»: the integrator's stock cards here, offered — made only by his ticks (`save` … `stock`).
        offers = stock_cards.offers(current, stock_cards.hass_info(self.hass)) if base is not None and current is not None else []
        # His empty pages named as Hauflo's (п.51): offered, removed only by his tick (`save` … `twins`).
        pairs = twins(current, config) if base is not None else []
        return {**out, "exists": True, **({"stock": offers[:200]} if offers else {}), **({"twins": pairs} if pairs else {})}

    async def save(self, url_path: str, config: dict[str, Any], take: Any, restore: Any, replace: bool, title: str | None, local_ws: Callable[[list[dict[str, Any]]], Any], lang: str | None = None, stock: Any = (), drop: Any = ()) -> dict[str, Any]:
        async with self.write_lock:
            return await self._save(url_path, config, take, restore, replace, title, local_ws, lang, stock, drop)

    async def _save(self, url_path: str, config: dict[str, Any], take: Any, restore: Any, replace: bool, title: str | None, local_ws: Callable[[list[dict[str, Any]]], Any], lang: str | None, stock: Any, drop: Any) -> dict[str, Any]:
        exists, raw = await self._current(url_path)
        if not exists:
            raise MergeError("not_found", "no such storage dashboard")
        # The guests' lists of who sees a page (expand_access) are the agent's, not the integrator's edit: merged without
        # them, put back on what is written.
        access = await self._access()
        current = self._plain(raw, access)
        base = self.bases.get(url_path) if current is not None else None
        new = expand_admins(ensure_ids(copy.deepcopy(config)), await self._admin_ids())
        new = rekey(new, unpanel(base, new) if base is not None else None)
        if base is None and current is not None and not replace:
            # Nothing known of what is Hauflo's here: no merge can tell the integrator's cards — refused, the cloud
            # decides (a hand-edited dashboard stays, or is rebuilt with a copy) as with an older agent.
            raise MergeError("no_base", "the agent has no Hauflo version of this dashboard to merge against")
        # The integrator's ticked stock cards become Hauflo's cards (still his: no Hauflo id), then the merge as always.
        src, made = stock_cards.apply(current, list(stock), stock_cards.hass_info(self.hass)) if base is not None and current is not None and stock else (current, 0)
        # His ticked empty twins of Hauflo's pages (and his tiles to them) go before the merge — checked again here.
        src, dropped = drop_twins(src, new, list(drop)) if base is not None and src is not None and drop else (src, 0)
        merged, report = plan_and_merge(new, src, base, take, restore)
        if made:
            report["stock"] = made
        if dropped:
            report["twins"] = dropped
        if report.get("lost"):
            # Whatever the cloud sent: an item of the integrator's would go — nothing is written (security re-review 28.10).
            _LOGGER.warning("Hauflo: dashboard %s not saved — %s of the integrator's items would be lost", url_path, report["lost"])
            raise MergeError("merge_lost_local", f"{report['lost']} of the integrator's own items would be lost; nothing saved")
        if report.get("untouched"):
            # Owner 05.11 п.54а: something of HA's the plan doesn't change would change — the agent's own check, whatever
            # the cloud sent or the merge did; nothing is written.
            _LOGGER.warning("Hauflo: dashboard %s not saved — %s blocks outside the plan would change", url_path, report["untouched"])
            raise MergeError("merge_untouched_changed", f"{report['untouched']} blocks the plan doesn't change would change; nothing saved")
        copy_url, pruned = None, 0
        if current is not None and merged != current and (base is None or current != base):
            # What the integrator made is in it: kept as a hidden copy before anything is written.
            if title is None:
                shown = getattr(self._dashboard(url_path), "config", None) or {}
                title = shown.get("title") if isinstance(shown.get("title"), str) else None
            copy_url, pruned = await self._copy(url_path, current, title, local_ws, lang, before_take=bool(report.get("taken")))
        out = expand_access(copy.deepcopy(merged), access, url_path)
        if out != raw:
            await self._dashboard(url_path).async_save(out)
            # Owner 05.11 п.54д: read back from HA — exactly what was planned is there, or the alarm.
            _, back = await self._current(url_path)
            report["verify"] = "ok" if back == out else "mismatch"
            if back != out:
                _LOGGER.warning("Hauflo: dashboard %s reads back other than saved", url_path)
        self.bases[url_path] = new
        await self.store.async_save(self.bases)
        # «upgrade»: no Hauflo version stored yet (first save by this agent) — the copy is only a precaution, nobody is
        # known to have edited the dashboard; «edited»: it differs from what Hauflo saved last.
        # «copies_removed»: the oldest of the agent's own copies pruned with this one (п.8 acceptance A: the backend said nothing of them).
        return {**report, "saved": out != raw, **({"copy": copy_url, "copy_reason": "upgrade" if base is None else "edited"} if copy_url else {}), **({"copies_removed": pruned} if pruned else {})}

    async def _copy(self, url_path: str, config: Any, title: str | None, local_ws: Callable[[list[dict[str, Any]]], Any], lang: str | None = None, before_take: bool = False) -> tuple[str, int]:
        """The copy's url_path and how many old copies went with it."""
        now = dt_util.now()
        # Seconds and a random tail (security re-review 28.10): two saves a minute apart (or less) never meet one copy,
        # and the cloud can't guess a name ahead.
        prefix = f"{url_path[:40]}-copy-"
        # The dashboard's language (the deploy's), else HA's; HA's own time zone (dt_util.now()), no year: «Панель (копия 28.10 02:59)».
        ru = str(lang or self.hass.config.language or "").startswith("ru")
        shown = now.strftime("%d.%m %H:%M") if ru else f"{now.day} {_MONTHS[now.month - 1]} {now.strftime('%H:%M')}"
        name = f"{title or ('Панель' if ru else 'Dashboard')} ({'копия' if ru else 'copy'} {shown})"
        listed = await local_ws([{"type": "lovelace/dashboards/list"}])
        dashboards = listed[0] if listed and isinstance(listed[0], list) else []
        taken = {str(d.get("url_path")) for d in dashboards if isinstance(d, dict)}
        copy_url = f"{prefix}{now.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
        while copy_url in taken:
            copy_url = f"{prefix}{now.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
        await local_ws([{"type": "lovelace/dashboards/create", "url_path": copy_url, "title": name[:60], "icon": "mdi:content-copy", "show_in_sidebar": False, "require_admin": True, "mode": "storage"}])
        await local_ws([{"type": "lovelace/config/save", "url_path": copy_url, "config": config}])
        mine = [*self.copies.get(url_path, []), copy_url]
        if before_take:
            self.taken_copies.add(copy_url)
        # The last MAX_COPIES of this dashboard: the oldest of the agent's own copies go — never a dashboard it didn't make,
        # never one made before a take-save for ordinary ones (those: the last MAX_TAKEN_COPIES of them).
        plain = [u for u in mine if u not in self.taken_copies]
        marked = [u for u in mine if u in self.taken_copies]
        drop = set(plain[: max(0, len(plain) - MAX_COPIES)] + marked[: max(0, len(marked) - MAX_TAKEN_COPIES)])
        ids = {str(d.get("url_path")): d.get("id") for d in dashboards if isinstance(d, dict)}
        pruned = 0
        for old in [u for u in mine if u in drop]:
            mine.remove(old)
            self.taken_copies.discard(old)
            if ids.get(old):
                try:
                    await local_ws([{"type": "lovelace/dashboards/delete", "dashboard_id": ids[old]}])
                    pruned += 1
                except Exception as err:  # noqa: BLE001 — an old copy left is no harm
                    _LOGGER.warning("Hauflo: old dashboard copy %s not removed: %s", old, err)
        self.copies[url_path] = mine
        await self._save_meta()
        return copy_url, pruned


class MergeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def is_copy(url: Any) -> bool:
    return isinstance(url, str) and bool(COPY_RE.search(url))




def _choice(v: Any) -> Any:
    """«all» (True) or a list of Hauflo ids — anything else is nothing chosen."""
    if v is True:
        return True
    if isinstance(v, list):
        return [x for x in v[:2000] if isinstance(x, str) and len(x) <= 256]
    return []


async def local_ws(session: Any, url: str, token: str, cmds: list[dict[str, Any]]) -> list[Any]:
    """Commands to this HA's own websocket as the agent's user (a copy of the dashboard, the old copies off) — the
    public API, one connection, in order; the first failure raises."""
    async with asyncio.timeout(60), session.ws_connect(url, max_msg_size=0) as ws:
        await ws.receive_json()  # auth_required
        await ws.send_json({"type": "auth", "access_token": token})
        if (await ws.receive_json()).get("type") != "auth_ok":
            raise MergeError("local_auth", "this HA refused the agent's sign-in")
        out: list[Any] = []
        for i, c in enumerate(cmds, 1):
            await ws.send_json({**c, "id": i})
            while True:
                r = await ws.receive_json()
                if isinstance(r, dict) and r.get("id") == i and r.get("type") == "result":
                    break
            if not r.get("success"):
                raise MergeError("local_failed", f"{c.get('type')}: {(r.get('error') or {}).get('message')}")
            out.append(r.get("result"))
        return out


async def handle(agent: Any, ch: int, msg: dict[str, Any], validate: Callable[[Any], str | None], fresh_backup: Callable[[], bool], locked_code: str) -> None:
    """`hauflo/dashboard/plan` and `lovelace/config` of a dashboard the agent merges (reads), `hauflo/dashboard/save`
    and every plain `lovelace/config/save` of a `hauflo-…` dashboard (writes): answered here, never relayed to HA."""
    t, mid = msg.get("type"), msg.get("id")

    async def reply(result: Any = None, code: str | None = None, message: str = "") -> None:
        frame: dict[str, Any] = {"id": mid, "type": "result", "success": code is None}
        if code is None:
            frame["result"] = result
        else:
            frame["error"] = {"code": code, "message": f"Hauflo agent: {message}"}
        await agent.send({"t": "ws_msg", "ch": ch, "data": json.dumps(frame, ensure_ascii=False)})

    url, config = msg.get("url_path"), msg.get("config")
    merger: DashboardMerge = agent.dashboards
    if t == "lovelace/config" and isinstance(url, str) and merger.manages(url) and not is_copy(url):
        # Hauflo's part as it is, the integrator's as opaque placeholders (security re-review 28.10).
        try:
            await reply(await merger.read(url))
        except MergeError as err:
            await reply(code=err.code, message=str(err))
        return
    # Hauflo's own dashboard only (never a copy, never the family's), and Hauflo's part only — the tunnel's dashboard
    # check exactly as for a plain save: no card, service or entity of the cloud's choosing.
    if t == "lovelace/config" or not (isinstance(url, str) and url.startswith("hauflo-") and not is_copy(url)) or not isinstance(config, dict):
        _LOGGER.warning("Hauflo: «%s» isn't allowed through the tunnel", t)
        await reply(code="not_allowed", message="not allowed")
        return
    # `validate`: why the dashboard is refused (None — it passes). The first item refused goes to the log and to the
    # cloud — its deploy message and journal name it (acceptance 0.33 K1: a bare «not allowed» named nothing to fix).
    why = validate(config)
    if why is not None:
        _LOGGER.warning("Hauflo: «%s» of %s isn't allowed through the tunnel — %s", t, url, why)
        await reply(code="not_allowed", message=f"not allowed ({why})")
        return
    try:
        if t == PLAN:
            await reply(await merger.plan(url, config))
            return
        if agent.locked:
            await reply(code=locked_code, message="changes by Hauflo are switched off in Home Assistant (Hauflo → Configure)")
            return
        if not fresh_backup():
            await reply(code="backup_required", message="a backup first")
            return
        plain = t != SAVE
        title = msg.get("title") if isinstance(msg.get("title"), str) else None
        lang = msg.get("lang") if isinstance(msg.get("lang"), str) else None
        res = await merger.save(
            url,
            config,
            [] if plain else _choice(msg.get("take")),
            [] if plain else _choice(msg.get("restore")),
            # A plain save: merged where the agent knows Hauflo's version; none yet (deployed by an older agent) —
            # Hauflo's config replaces it, the pre-save one kept as a hidden copy first (security re-review 28.10).
            not merger.manages(url) if plain else msg.get("replace") is True,
            title[:60] if title else None,
            lambda cmds: local_ws(agent.local_session, f"{agent.local_base}/api/websocket", agent.token, cmds),
            lang[:5] if lang else None,
            [] if plain else [x for x in (msg.get("stock") if isinstance(msg.get("stock"), list) else [])[:200] if isinstance(x, str) and len(x) <= 64],
            [] if plain else [x for x in (msg.get("twins") if isinstance(msg.get("twins"), list) else [])[:50] if isinstance(x, str) and len(x) <= 128],
        )
        await reply(None if plain else res)
    except MergeError as err:
        await reply(code=err.code, message=str(err))
    except Exception as err:  # noqa: BLE001 — said to Hauflo, the link stays
        _LOGGER.exception("Hauflo: dashboard %s failed", t)
        await reply(code="failed", message=str(err)[:200])

