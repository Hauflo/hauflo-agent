"""Rebuilds the merged tree in the new layout."""
from __future__ import annotations
from collections import Counter
import copy
import re
from typing import TYPE_CHECKING, Any
from .const import ID, ROOT
from .tree import child_level_of, cloud, hid, holds, kids_key, kids_of, own_of, title_of, user, user_keyed
from .twins import same_name
if TYPE_CHECKING:
    from .tree import Index

class MergeBuildMixin:
    """Builds the merged tree."""

    def _dissolve(self) -> None:
        for h, lvl in self.B.level.items():
            if lvl < 2 or h in self.C.node or h not in self.N.node or (not holds(self.B.node[h], lvl)):
                continue
            where = Counter((self.C.parent[x] for x, p in self.B.parent.items() if p == h and x in self.C.node and (self.C.parent[x] in self.N.node) and (self.C.parent[x] != h)))
            if not where:
                continue
            back = self.restore_all or h in self.restore
            kids = [self._fresh(i) for x in kids_of(self.N.node[h], lvl) if isinstance(x, dict) and (i := hid(x)) and (back or i not in self.B.node) and (i not in self.C.node)]
            self.dissolved.add(h)
            self.adopt.setdefault(where.most_common(1)[0][0], []).extend(kids)
            self.restored.extend((k[ID] for k in kids if back and k[ID] in self.B.node))

    def _home(self, c: dict[str, Any]) -> str | None:
        h = hid(c)
        top = [x for x in kids_of(self.new, 0) if isinstance(x, dict) and (i := hid(x)) and (i in self.N.node) and (not (i in self.B.node and i not in self.C.node))]
        if not top or h is None:
            return None
        hit = [x for x in top if re.fullmatch(re.escape(h) + '~\\d+', hid(x) or '')]
        path = c.get('path') if isinstance(c.get('path'), str) and c.get('path') else None
        if not hit and path:
            hit = [x for x in top if x.get('path') == path]
        if not hit and (name := same_name(title_of(c, 1))):
            hit = [x for x in top if same_name(title_of(x, 1)) == name and bool(x.get('subview')) == bool(c.get('subview'))]
            if len(hit) > 1 and path:

                def tail(x: dict[str, Any]) -> int:
                    a, b = (path[::-1], str(x.get('path') or '')[::-1])
                    n = 0
                    while n < min(len(a), len(b)) and a[n] == b[n]:
                        n += 1
                    return n
                best = max((tail(x) for x in hit))
                hit = [x for x in hit if tail(x) == best]
        return hid(hit[0]) if len(hit) == 1 else None

    def _rehome(self) -> None:
        for c in kids_of(self.current, 0):
            if not isinstance(c, dict) or (h := self._hauflo(c)) is None or h in self.N.node or self._edited(h):
                continue
            t = self._home(c)
            if t is None:
                continue
            cl = child_level_of(1, kids_key(self.N.node[t], 1))
            items = self._orphans(c, 1)
            if cl == 3 and any((lvl != 3 for lvl, _ in items)):
                self.held[id(c)] = items
                continue
            self.rehomed.add(id(c))
            self.adopt.setdefault(t, []).extend(self._fit(items, cl, c))

    def _build_root(self) -> dict[str, Any]:
        own = {k: v for k, v in self.new.items() if k != 'views'}
        return {**copy.deepcopy(own), 'views': self._children(ROOT, 0, self.new, self.current)}

    def _build(self, h: str) -> dict[str, Any]:
        n, lvl = (self.N.node[h], self.N.level[h])
        own = cloud(own_of(n, lvl), lvl)
        if self._in_current(h):
            cur_own = own_of(self.C.node[h], self.B.level[h])
            merged, clash = self._merge_own(h)
            if clash:
                taken = self.take_all or h in self.take
                self.conflicts.append(self._label(self.B, h, kept='hauflo' if taken else 'yours', updated=own != cloud(own_of(self.B.node[h], lvl), lvl)))
                if not taken:
                    own = cloud(cur_own, lvl)
                else:
                    self.taken.append(h)
            else:
                own = merged
            if user_keyed(own, lvl):
                own = {**own, **user(cur_own, lvl)}
        out = copy.deepcopy(own)
        out[ID] = h
        key = kids_key(n, lvl)
        if key is not None:
            out[key] = self._children(h, lvl, n, self.C.node.get(h) if self._in_current(h) else None)
        return out

    def _children(self, h: str, level: int, n_node: dict[str, Any], c_node: dict[str, Any] | None) -> list[Any]:
        out: list[Any] = []
        for n in kids_of(n_node, level):
            if not isinstance(n, dict):
                continue
            nh = hid(n)
            if nh is None:
                continue
            if nh in self.dissolved:
                continue
            if nh in self.B.node and nh not in self.C.node:
                if self.restore_all or nh in self.restore:
                    back = self._fresh(nh)
                    key = kids_key(back, self.N.level[nh])
                    if key is not None and (not back[key]) and kids_of(self.N.node[nh], self.N.level[nh]):
                        continue
                    self.restored.append(nh)
                    out.append(back)
                else:
                    self.removed.append(self._label(self.B, nh))
                continue
            if self._in_current(nh) and self._moved(nh):
                continue
            out.append(self._build(nh))
        if c_node is None:
            return out + self.adopt.get(h, [])
        out = self._keep_order(h, level, out, c_node)
        self._float(out, level, c_node)
        return out + self.adopt.get(h, [])

    def _keep_order(self, h: str, level: int, out: list[Any], c_node: dict[str, Any]) -> list[Any]:
        b_node = self.base if h == ROOT else self.B.node.get(h)
        if not isinstance(b_node, dict):
            return out

        def pos(node: dict[str, Any], idx: Index, mine: bool) -> dict[str, int]:
            got: dict[str, int] = {}
            for i, c in enumerate(kids_of(node, level)):
                x = self._hauflo(c) if mine else hid(c)
                if x is not None and idx.parent.get(x) == h and (x not in got):
                    got[x] = i
            return got
        cur, was = (pos(c_node, self.C, True), pos(b_node, self.B, False))
        ids = [x.get(ID) for x in out if isinstance(x, dict)]
        common = [x for x in ids if x in cur and x in was]
        mine = sorted(common, key=lambda x: cur[x])
        if mine == sorted(common, key=lambda x: was[x]):
            return out
        by = {x.get(ID): x for x in out if isinstance(x, dict)}
        keep = set(common)
        return [by[x] for x in mine] + [x for x in out if not (isinstance(x, dict) and x.get(ID) in keep)]

    def _fresh(self, h: str) -> dict[str, Any]:

        def strip(node: dict[str, Any], level: int) -> dict[str, Any]:
            key = kids_key(node, level)
            out = {k: copy.deepcopy(v) for k, v in node.items() if k != key}
            if key is not None:
                cl = child_level_of(level, key)
                out[key] = [strip(x, cl) for x in kids_of(node, level) if isinstance(x, dict) and (not self._in_current(hid(x) or ''))]
            return out
        return strip(self.N.node[h], self.N.level[h])
