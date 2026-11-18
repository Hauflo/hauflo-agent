"""Keeps the cards added in HA in the merged tree."""
from __future__ import annotations
import copy
from typing import Any
from .access import blank_view
from .const import ID
from .tree import child_level_of, count_cards, hid, holds, inner_of, kids_key, kids_of, strip_ids, title_of

class MergeLocalMixin:
    """Places the cards added in HA."""

    def _float(self, out: list[Any], level: int, c_node: dict[str, Any]) -> None:
        child_level = child_level_of(level, kids_key(c_node, level))
        pos: int | None = 0
        tail: list[Any] = []
        anchored = False

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
        for c in kids_of(c_node, level):
            if not isinstance(c, dict):
                continue
            h = self._hauflo(c)
            if h is None:
                node = self._local(c, child_level)
                self.local.append(self._said(node, self._local_label(c, child_level)))
                put(node)
                continue
            if h in self.N.node and (not self._moved(h)):
                i = index_of(h)
                pos = None if i is None else i + 1
                anchored = anchored or i is not None
                continue
            if h in self.N.node:
                self.moved.append(self._label(self.B, h))
                put(self._build(h))
                continue
            if self._keeps(h) and (not (child_level == 1 and blank_view(c))):
                node = self._local(c, child_level)
                self.local.append(self._said(node, self._local_label(c, child_level, dropped=h)))
                put(node) if anchored else tail.append(node)
            else:
                if self._edited(h) and (not (child_level == 1 and blank_view(c))):
                    self.removed.append(self._label(self.B, h, twin=self._twin(h)))
                if id(c) not in self.rehomed:
                    items = self.held.pop(id(c)) if id(c) in self.held else self._orphans(c, child_level)
                    tail.extend(self._fit(items, child_level, c))
            pos = None
        out.extend(tail)

    def _local(self, c: dict[str, Any], level: int) -> dict[str, Any]:
        key = kids_key(c, level)
        out = {k: copy.deepcopy(v) for k, v in c.items() if k not in (ID, key)}
        if key is None:
            return strip_ids(c)
        kids: list[Any] = []
        cl = child_level_of(level, key)
        for x in kids_of(c, level):
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
            elif self._edited(h):
                kids.append(self._local(x, cl))
            else:
                kids.extend(self._fit(self._orphans(x, cl), cl, x))
        out[key] = kids
        return out

    def _orphans(self, c: dict[str, Any], level: int) -> list[tuple[int, dict[str, Any]]]:
        if not holds(c, level):
            return []
        out: list[tuple[int, dict[str, Any]]] = []
        cl = child_level_of(level, kids_key(c, level))
        for x in kids_of(c, level):
            if not isinstance(x, dict):
                continue
            h = self._hauflo(x)
            if h is None:
                node = self._local(x, cl)
                self.local.append(self._said(node, self._local_label(x, cl)))
                out.append((cl, node))
            elif h in self.N.node:
                if self._moved(h):
                    self.moved.append(self._label(self.B, h))
                    out.append((cl, self._build(h)))
            elif self._keeps(h) and (not (cl == 1 and blank_view(x))):
                node = self._local(x, cl)
                self.local.append(self._said(node, self._local_label(x, cl, dropped=h)))
                out.append((cl, node))
            else:
                if self._edited(h) and (not (cl == 1 and blank_view(x))):
                    self.removed.append(self._label(self.B, h, twin=self._twin(h)))
                out.extend(self._orphans(x, cl))
        return out

    def _fit(self, items: list[tuple[int, dict[str, Any]]], level: int, holder: dict[str, Any]) -> list[dict[str, Any]]:
        if not items:
            return []
        if level == 3:
            out: list[dict[str, Any]] = []
            for lvl, o in items:
                out.extend([o] if lvl == 3 else [x for x in o.get('cards', []) if isinstance(x, dict)])
            return out
        if level == 2:
            out, run = ([], [])
            for lvl, o in items:
                if lvl == 3:
                    run.append(o)
                    continue
                if run:
                    out.append({'type': 'grid', 'cards': run})
                    run = []
                out.append(o)
            if run:
                out.append({'type': 'grid', 'cards': run})
            return out
        views = [o for lvl, o in items if lvl == 1]
        rest = [(lvl, o) for lvl, o in items if lvl != 1]
        if rest:
            src = holder if isinstance(holder, dict) and hid(holder) and (self.B.level.get(hid(holder) or '') == 1) else {}
            view = {k: copy.deepcopy(v) for k, v in src.items() if k in ('title', 'path', 'icon', 'type', 'max_columns', 'subview', 'back_path')}
            if view.get('path'):
                view['path'] = f"{view['path']}-kept"
            if any((lvl == 2 for lvl, _ in rest)) or view.get('type') == 'sections' or (not view):
                view['type'] = 'sections'
                view['sections'] = self._fit(rest, 2, holder)
            else:
                view['cards'] = self._fit(rest, 3, holder)
            views.append(view)
        return views

    def _said(self, node: dict[str, Any], label: dict[str, Any]) -> dict[str, Any]:
        self._labels[id(node)] = label
        return label

    def _drop_blank_doubles(self) -> None:
        views = self.result.get('views')
        if not isinstance(views, list):
            return
        ours = {v.get('path') for v in views if isinstance(v, dict) and hid(v) and v.get('path')}
        gone = [v for v in views if isinstance(v, dict) and hid(v) is None and (v.get('path') in ours) and blank_view(v)]
        if not gone:
            return
        self.result['views'] = [v for v in views if not any((v is g for g in gone))]
        self.blank_prints = self.prints_of({'views': gone}, True)
        said = {id(self._labels[id(v)]) for v in gone if id(v) in self._labels}
        self.local = [x for x in self.local if id(x) not in said]

    def _local_label(self, c: dict[str, Any], level: int, dropped: str | None=None) -> dict[str, Any]:
        out: dict[str, Any] = {'kind': ('view', 'section', 'card')[level - 1]}
        if level == 3 and isinstance(c.get('type'), str):
            out['type'] = c['type'][:60]
        if (t := title_of(c, level)):
            out['title'] = t
        if holds(c, level):
            out['cards'] = count_cards(c, level)
            if (inner := inner_of(c, level)):
                out['inner'] = inner
        if level > 1 and (w := self.on_view.get(id(c))):
            out['where'] = w
        if dropped:
            out['dropped'] = True
            out['id'] = dropped
        return out
