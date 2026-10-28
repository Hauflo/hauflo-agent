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
back holds counts, card types and titles — never a foreign entity id. A plain `lovelace/config/save` of a dashboard
the agent manages goes through the same merge. Before a save that changes what the integrator made, the pre-save
config is kept as a hidden copy (the last 3 per dashboard).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

ID = "hauflo_id"
PLAN = "hauflo/dashboard/plan"
SAVE = "hauflo/dashboard/save"
STORE_KEY = "hauflo.dashboard_base"
MAX_COPIES = 3
_ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_TITLE_KEYS = ("title", "heading", "name", "text")
ROOT = "\x00root"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# --- the tree ---------------------------------------------------------------------------------------------------------
# Levels: 0 the config (views), 1 a view (sections, or cards in a masonry / panel view), 2 a section (cards), 3 a card.


def _kids_key(node: dict[str, Any], level: int) -> str | None:
    if level == 0:
        return "views"
    if level == 1:
        return "sections" if isinstance(node.get("sections"), list) or node.get("type") == "sections" else "cards"
    if level == 2:
        return "cards"
    return None


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


def _hid(node: Any) -> str | None:
    v = node.get(ID) if isinstance(node, dict) else None
    return v if isinstance(v, str) and v else None


class _Index:
    """Hauflo ids of a config: the node, its level, its parent's id and the view it is on (first occurrence wins)."""

    def __init__(self, cfg: Any) -> None:
        self.node: dict[str, dict[str, Any]] = {}
        self.level: dict[str, int] = {}
        self.parent: dict[str, str] = {}
        self.view: dict[str, str | None] = {}
        self.first: set[int] = set()  # python ids of the nodes that hold their hauflo_id first
        if isinstance(cfg, dict):
            self._walk(cfg, 0, ROOT, None)

    def _walk(self, node: dict[str, Any], level: int, pid: str, view: str | None) -> None:
        for c in _kids(node, level):
            if not isinstance(c, dict):
                continue
            cl = _child_level(level, _kids_key(node, level))
            h = _hid(c)
            here = pid
            if h is not None and h not in self.node:
                self.node[h], self.level[h], self.parent[h] = c, cl, pid
                self.view[h] = h if cl == 1 else view
                self.first.add(id(c))
                here = h
            else:
                # A local container: its children's parent is itself (by python id) — never a Hauflo id.
                here = f"\x00local{id(c)}"
            if cl < 3:
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
            if cl < 3:
                walk(c, cl, h)

    walk(cfg, 0, "")
    return cfg


def _leaves(idx: _Index) -> dict[str, dict[str, Any]]:
    return {h: n for h, n in idx.node.items() if idx.level[h] == 3}


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
    if level == 3:
        return 1
    return sum(_count_cards(c, _child_level(level, _kids_key(node, level))) for c in _kids(node, level) if isinstance(c, dict))


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
    def __init__(self, new: dict[str, Any], current: Any, base: dict[str, Any], take: Any = (), restore: Any = ()) -> None:
        self.new, self.current, self.base = new, current if isinstance(current, dict) else {"views": []}, base
        self.N, self.B, self.C = _Index(new), _Index(base), _Index(self.current)
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
        self.result = self._build_root()

    def _mark_view(self, node: dict[str, Any], level: int, title: str | None) -> None:
        for c in _kids(node, level):
            if isinstance(c, dict):
                self.on_view[id(c)] = title
                cl = _child_level(level, _kids_key(node, level))
                if cl < 3:
                    self._mark_view(c, cl, title)

    # what a current node is
    def _hauflo(self, node: dict[str, Any]) -> str | None:
        """Its Hauflo id when the base knows it and this is its first occurrence in HA."""
        h = _hid(node)
        return h if h is not None and h in self.B.node and id(node) in self.C.first else None

    def _edited(self, h: str) -> bool:
        lvl = self.B.level[h]
        return _own(self.C.node[h], lvl) != _own(self.B.node[h], lvl)

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
        if w := self._where(idx, h):
            out["where"] = w
        return {**out, **extra}

    # building
    def _build_root(self) -> dict[str, Any]:
        own = {k: v for k, v in self.new.items() if k != "views"}
        return {**copy.deepcopy(own), "views": self._children(ROOT, 0, self.new, self.current)}

    def _build(self, h: str) -> dict[str, Any]:
        """Hauflo's item as it goes to HA: its new version (or the integrator's, in a conflict he keeps) with merged children."""
        n, lvl = self.N.node[h], self.N.level[h]
        own = _own(n, lvl)
        if self._in_current(h) and self._edited(h):
            cur_own = _own(self.C.node[h], self.B.level[h])
            if cur_own != own:
                taken = self.take_all or h in self.take
                self.conflicts.append(self._label(self.B, h, kept="hauflo" if taken else "yours", updated=own != _own(self.B.node[h], lvl)))
                if not taken:
                    own = cur_own
                else:
                    self.taken.append(h)
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
            if nh in self.B.node and nh not in self.C.node:
                # Removed in HA: stays removed unless asked back.
                if self.restore_all or nh in self.restore:
                    self.restored.append(nh)
                    out.append(self._fresh(nh))
                else:
                    self.removed.append(self._label(self.B, nh))
                continue
            if self._in_current(nh) and self._moved(nh):
                continue  # where the integrator put it (placed with its current parent)
            out.append(self._build(nh))
        if c_node is None:
            return out
        self._float(out, level, c_node)
        return out

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
                self.local.append(self._local_label(c, child_level, dropped=True))
                put(self._local(c, child_level))
            else:
                tail.extend(self._fit(self._orphans(c, child_level), child_level, c))
            pos = None
        out.extend(tail)

    def _local(self, c: dict[str, Any], level: int) -> dict[str, Any]:
        """The integrator's item as it is, its Hauflo children (moved in) in their new version."""
        key = _kids_key(c, level)
        out = {k: copy.deepcopy(v) for k, v in c.items() if k not in (ID, key)}
        if key is None:
            return _strip_ids(c) if level == 3 else out
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
                self.moved.append(self._label(self.B, h))
                kids.append(self._build(h))
            elif self._edited(h):
                kids.append(self._local(x, cl))
            else:
                kids.extend(self._fit(self._orphans(x, cl), cl, x))
        out[key] = kids
        return out

    def _orphans(self, c: dict[str, Any], level: int) -> list[tuple[int, dict[str, Any]]]:
        """What a Hauflo item Hauflo dropped still holds that must stay: the integrator's, and Hauflo's moved into it."""
        if level == 3:
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
                self.local.append(self._local_label(x, cl, dropped=True))
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

    def _local_label(self, c: dict[str, Any], level: int, dropped: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": ("view", "section", "card")[level - 1]}
        if level == 3 and isinstance(c.get("type"), str):
            out["type"] = c["type"][:60]
        if t := _title(c, level):
            out["title"] = t
        if level < 3:
            out["cards"] = _count_cards(c, level)
        if level > 1 and (w := self.on_view.get(id(c))):
            out["where"] = w
        if dropped:
            out["dropped"] = True  # Hauflo's, edited in HA, no longer in Hauflo's version: kept as the integrator's
        return out

    # the summary
    def updates(self) -> dict[str, int]:
        nb, nn = _leaves(self.B), _leaves(self.N)
        return {
            "added": sum(1 for h in nn if h not in nb),
            "changed": sum(1 for h in nn if h in nb and nn[h] != nb[h]),
            "removed": sum(1 for h in nb if h not in nn),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "local": self.local,
            "conflicts": self.conflicts,
            "removed": self.removed,
            "moved": self.moved,
            "restored": len(self.restored),
            "taken": len(self.taken),
            "updates": self.updates(),
        }


def plan_and_merge(new: dict[str, Any], current: Any, base: dict[str, Any] | None, take: Any = (), restore: Any = ()) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the config to save, what the plan says). No base: Hauflo's config as it is, nothing known about HA's."""
    new = ensure_ids(copy.deepcopy(new))  # unique ids whatever the cloud sent
    if base is None:
        return new, {"base": False}
    m = Merge(new, current, base, take, restore)
    return m.result, {**m.summary(), "base": True, "edited": current != base, "changes": m.result != current}


# --- the agent's side -------------------------------------------------------------------------------------------------


class DashboardMerge:
    """Bases per dashboard (the agent's own store) and the two tunnel commands."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: Store = Store(hass, 1, STORE_KEY)
        self.bases: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        data = await self.store.async_load()
        self.bases = {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}

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

    async def plan(self, url_path: str, config: dict[str, Any]) -> dict[str, Any]:
        exists, current = await self._current(url_path)
        if not exists:
            return {"base": False, "exists": False}
        base = self.bases.get(url_path) if current is not None else None
        _, out = plan_and_merge(ensure_ids(copy.deepcopy(config)), current, base)
        return {**out, "exists": True}

    async def save(self, url_path: str, config: dict[str, Any], take: Any, restore: Any, replace: bool, title: str | None, local_ws: Callable[[list[dict[str, Any]]], Any], lang: str | None = None) -> dict[str, Any]:
        exists, current = await self._current(url_path)
        if not exists:
            raise MergeError("not_found", "no such storage dashboard")
        new = ensure_ids(copy.deepcopy(config))
        base = self.bases.get(url_path) if current is not None else None
        if base is None and current is not None and not replace:
            # Nothing known of what is Hauflo's here: no merge can tell the integrator's cards — refused, the cloud
            # decides (a hand-edited dashboard stays, or is rebuilt with a copy) as with an older agent.
            raise MergeError("no_base", "the agent has no Hauflo version of this dashboard to merge against")
        merged, report = plan_and_merge(new, current, base, take, restore)
        copy_url = None
        if current is not None and merged != current and (base is None or current != base):
            # What the integrator made is in it: kept as a hidden copy before anything is written.
            copy_url = await self._copy(url_path, current, title, local_ws, lang)
        if merged != current:
            await self._dashboard(url_path).async_save(merged)
        self.bases[url_path] = new
        await self.store.async_save(self.bases)
        # «upgrade»: no Hauflo version stored yet (first save by this agent) — the copy is only a precaution, nobody is
        # known to have edited the dashboard; «edited»: it differs from what Hauflo saved last.
        return {**report, "saved": merged != current, **({"copy": copy_url, "copy_reason": "upgrade" if base is None else "edited"} if copy_url else {})}

    async def _copy(self, url_path: str, config: Any, title: str | None, local_ws: Callable[[list[dict[str, Any]]], Any], lang: str | None = None) -> str:
        now = dt_util.now()
        stamp = now.strftime("%Y%m%d%H%M")
        prefix = f"{url_path[:40]}-copy-"
        copy_url = f"{prefix}{stamp}"
        # The dashboard's language (the deploy's), else HA's; HA's own time zone (dt_util.now()), no year: «Дом (копия 28.10 02:59)».
        ru = str(lang or self.hass.config.language or "").startswith("ru")
        shown = now.strftime("%d.%m %H:%M") if ru else f"{now.day} {_MONTHS[now.month - 1]} {now.strftime('%H:%M')}"
        name = f"{title or ('Дом' if ru else 'Home')} ({'копия' if ru else 'copy'} {shown})"
        listed = await local_ws([{"type": "lovelace/dashboards/list"}])
        dashboards = listed[0] if listed and isinstance(listed[0], list) else []
        if not any(isinstance(d, dict) and d.get("url_path") == copy_url for d in dashboards):
            await local_ws([{"type": "lovelace/dashboards/create", "url_path": copy_url, "title": name[:60], "icon": "mdi:content-copy", "show_in_sidebar": False, "require_admin": True, "mode": "storage"}])
            await local_ws([{"type": "lovelace/config/save", "url_path": copy_url, "config": config}])
            dashboards = [*dashboards, {"url_path": copy_url, "id": None}]
        # The last MAX_COPIES of this dashboard: the oldest go.
        mine = sorted((d for d in dashboards if isinstance(d, dict) and re.fullmatch(re.escape(prefix) + r"\d{12}", str(d.get("url_path", "")))), key=lambda d: str(d["url_path"]))
        for d in mine[:-MAX_COPIES]:
            if d.get("id"):
                try:
                    await local_ws([{"type": "lovelace/dashboards/delete", "dashboard_id": d["id"]}])
                except Exception as err:  # noqa: BLE001 — an old copy left is no harm
                    _LOGGER.warning("Hauflo: old dashboard copy %s not removed: %s", d.get("url_path"), err)
        return copy_url


class MergeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_COPY_RE = re.compile(r"-copy-\d{12}$")


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


async def handle(agent: Any, ch: int, msg: dict[str, Any], validate: Callable[[Any], bool], fresh_backup: Callable[[], bool], locked_code: str) -> None:
    """`hauflo/dashboard/plan` (reads), `hauflo/dashboard/save` and a plain `lovelace/config/save` of a dashboard the
    agent manages (writes): answered here, never relayed to HA as they are."""
    t, mid = msg.get("type"), msg.get("id")

    async def reply(result: Any = None, code: str | None = None, message: str = "") -> None:
        frame: dict[str, Any] = {"id": mid, "type": "result", "success": code is None}
        if code is None:
            frame["result"] = result
        else:
            frame["error"] = {"code": code, "message": f"Hauflo agent: {message}"}
        await agent.send({"t": "ws_msg", "ch": ch, "data": json.dumps(frame, ensure_ascii=False)})

    url, config = msg.get("url_path"), msg.get("config")
    # Hauflo's own dashboard only (never a copy, never the family's), and Hauflo's part only — the tunnel's dashboard
    # check exactly as for a plain save: no card, service or entity of the cloud's choosing.
    if not (isinstance(url, str) and url.startswith("hauflo-") and not _COPY_RE.search(url)) or not isinstance(config, dict) or not validate(config):
        _LOGGER.warning("Hauflo: «%s» isn't allowed through the tunnel", t)
        await reply(code="not_allowed", message="not allowed")
        return
    merger: DashboardMerge = agent.dashboards
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
            False if plain else msg.get("replace") is True,
            title[:60] if title else None,
            lambda cmds: local_ws(agent.local_session, f"{agent.local_base}/api/websocket", agent.token, cmds),
            lang[:5] if lang else None,
        )
        await reply(None if plain else res)
    except MergeError as err:
        await reply(code=err.code, message=str(err))
    except Exception as err:  # noqa: BLE001 — said to Hauflo, the link stays
        _LOGGER.exception("Hauflo: dashboard %s failed", t)
        await reply(code="failed", message=str(err)[:200])

