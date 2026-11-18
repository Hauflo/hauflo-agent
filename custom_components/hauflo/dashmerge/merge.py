"""The merge of one dashboard: the new tree, the edits kept and what was lost."""
from __future__ import annotations
from collections import Counter
from collections.abc import Callable
import json
import logging
from typing import Any
from .const import DEAD_LINK, ID, LAYOUT, NONE, ROOT
from .merge_build import MergeBuildMixin
from .merge_local import MergeLocalMixin
from .tree import Index, card_entity, child_level_of, cloud, count_cards, hid, holds, inner_of, kids_key, kids_of, leaves, own_of, rekey, strip_ids, title_of, view_of, ways_of
from .twins import stack
_LOGGER = logging.getLogger(__name__)

class Dead:
    """What `drop_dead` took out: the `removed` items, the prints of the integrator's items gone or changed with them
    (no
    loss to the guard), the Hauflo ids among them, the Hauflo pages they were on, the labels of the kept items gone."""

    def __init__(self) -> None:
        self.removed: list[dict[str, Any]] = []
        self.prints: Counter[str] = Counter()
        self.ids: set[str] = set()
        self.on: list[str] = []
        self.labels: list[dict[str, Any]] = []

def drop_dead(cfg: Any, url_path: str, mine: Callable[[dict[str, Any]], bool], labels: dict[int, dict[str, Any]] | None=None) -> Dead | None:
    """`cfg` (changed in place) less the integrator's tiles (`mine`) that open a page of THIS dashboard it no longer has
    moved the room to `servis` — a second «Сервис» on «Главная» that opened nothing). At any depth: a section, Hauflo's
    layout, a stack of his; his stack or section left empty by it goes too, a page stays. Each said in `removed` with
    `reason` «dead_link» and the page it led to; one with a label (`labels`, by the python id of its node) that carries
    a
    Hauflo id (`dropped`) is said by it. Never a card that leads anywhere else or anywhere that is (a path of another
    dashboard, a URL, a pop-up `#…`, an index of a page that is), never one with an entity / area of its own (a lamp's
    tile that also opens a page), never Hauflo's own card. None — a config with no pages: nothing to tell a dead way
    by."""
    views = [v for v in kids_of(cfg, 0) if isinstance(v, dict)] if isinstance(cfg, dict) else []
    if not views:
        return None
    paths = {v['path'] for v in views if isinstance(v.get('path'), str) and v['path']}
    out = Dead()
    used: set[str] = set()

    def gone(card: dict[str, Any]) -> str | None:
        ways = ways_of(card)
        pages = [view_of(w, url_path) for w in ways]
        if not ways or any((p is None for p in pages)):
            return None
        if any((p in paths or (p.isdigit() and int(p) < len(views)) for p in pages if p is not None)):
            return None
        return pages[0]

    def drop(card: dict[str, Any], to: str, view: dict[str, Any]) -> None:
        out.prints[Merge.fingerprint(card, 3)] += 1
        label = (labels or {}).get(id(card))
        if label is not None:
            out.labels.append(label)
        h = label.get('id') if label is not None and label.get('dropped') else None
        if h:
            out.ids.add(h)
        else:
            h, k = (f'~dead/{to}', 2)
            while h in used:
                h, k = (f'~dead/{to}#{k}', k + 1)
        used.add(h)
        item: dict[str, Any] = {'id': h, 'kind': 'card'}
        if isinstance(card.get('type'), str):
            item['type'] = card['type'][:60]
        if (t := title_of(card, 3)):
            item['title'] = t
        if (w := title_of(view, 1)):
            item['where'] = w
        out.removed.append({**item, 'reason': DEAD_LINK, 'to': to[:128]})
        if (vh := hid(view)) is not None:
            out.on.append(vh)

    def cards(items: list[Any], view: dict[str, Any]) -> list[Any]:
        kept: list[Any] = []
        for c in items:
            if not isinstance(c, dict):
                kept.append(c)
                continue
            his = mine(c)
            if his and (to := gone(c)) is not None:
                drop(c, to, view)
                continue
            inner = stack(c) if his or c.get('type') == LAYOUT else None
            if inner:
                before = Merge.fingerprint(c, 3)
                left = cards(inner, view)
                if len(left) != len(inner):
                    if his:
                        out.prints[before] += 1
                        if not left:
                            continue
                    c['cards'] = left
            kept.append(c)
        return kept
    for v in views:
        if kids_key(v, 1) == 'cards':
            if isinstance(v.get('cards'), list):
                v['cards'] = cards(v['cards'], v)
            continue
        if not isinstance(v.get('sections'), list):
            continue
        secs: list[Any] = []
        for s in v['sections']:
            if isinstance(s, dict) and isinstance(s.get('cards'), list) and s['cards']:
                before = Merge.fingerprint(s, 2)
                s['cards'] = cards(s['cards'], v)
                if not s['cards'] and mine(s):
                    out.prints[before] += 1
                    continue
            secs.append(s)
        v['sections'] = secs
    return out

class Merge(MergeBuildMixin, MergeLocalMixin):

    def __init__(self, new: dict[str, Any], current: Any, base: dict[str, Any], take: Any=(), restore: Any=(), skip: Any=(), url_path: str | None=None) -> None:
        self.new, self.current, self.base = (new, current if isinstance(current, dict) else {'views': []}, base)
        self.skip: set[str] = set(skip)
        self.B = Index(base)
        self.new = rekey(new, base)
        self.N, self.C = (Index(self.new), Index(self.current, expect=self.B))
        self.take_all, self.take = (take is True, set(take) if isinstance(take, list) else set())
        self.restore_all, self.restore = (restore is True, set(restore) if isinstance(restore, list) else set())
        self.local: list[dict[str, Any]] = []
        self.conflicts: list[dict[str, Any]] = []
        self.removed: list[dict[str, Any]] = []
        self.moved: list[dict[str, Any]] = []
        self.restored: list[str] = []
        self.taken: list[str] = []
        self.on_view: dict[int, str | None] = {}
        for v in kids_of(self.current, 0):
            if isinstance(v, dict):
                self._mark_view(v, 1, title_of(v, 1))
        self.adopt: dict[str, list[Any]] = {}
        self.dissolved: set[str] = set()
        self._dissolve()
        self.rehomed: set[int] = set()
        self.held: dict[int, list[tuple[int, dict[str, Any]]]] = {}
        self._labels: dict[int, dict[str, Any]] = {}
        self._rehome()
        self.dead_prints: Counter[str] = Counter()
        self.dead_ids: set[str] = set()
        self.dead_on: list[str] = []
        self.blank_prints: Counter[str] = Counter()
        self.result = self._build_root()
        self._drop_blank_doubles()
        if url_path:
            self._drop_dead(url_path)

    def _mark_view(self, node: dict[str, Any], level: int, title: str | None) -> None:
        for c in kids_of(node, level):
            if isinstance(c, dict):
                self.on_view[id(c)] = title
                cl = child_level_of(level, kids_key(node, level))
                if holds(c, cl):
                    self._mark_view(c, cl, title)

    def _hauflo(self, node: dict[str, Any]) -> str | None:
        h = hid(node)
        return h if h is not None and h in self.B.node and (id(node) in self.C.first) else None

    def _edited(self, h: str) -> bool:
        lvl = self.B.level[h]
        return own_of(self.C.node[h], lvl) != own_of(self.B.node[h], lvl)

    def _twin(self, h: str) -> str | None:
        lvl = self.B.level[h]
        b = self.B.node[h]

        def marks(node: dict[str, Any]) -> set[tuple[str, str]]:
            out = set()
            if isinstance(node.get('path'), str) and node.get('path'):
                out.add(('path', node['path']))
            if lvl == 3 and (e := card_entity(node)):
                out.add(('entity', e))
            if (t := title_of(node, lvl)):
                out.add(('title', t))
            return out
        want = marks(b)
        if not want:
            return None
        for nh, nn in self.N.node.items():
            if nh in self.B.node or self.N.level.get(nh) != lvl or (lvl == 3 and nn.get('type') != b.get('type')):
                continue
            if want & marks(nn):
                return nh
        return None

    def _keeps(self, h: str) -> bool:
        return self._edited(h) and self._twin(h) is None

    def _merge_own(self, h: str) -> tuple[dict[str, Any], bool]:
        lvl = self.B.level[h]
        b = cloud(own_of(self.B.node[h], lvl), lvl)
        c = cloud(own_of(self.C.node[h], lvl), lvl)
        n = cloud(own_of(self.N.node[h], self.N.level[h]), self.N.level[h])
        out, clash = (dict(n), False)
        for k in {*b, *c, *n}:
            bv, cv, nv = (b.get(k, NONE), c.get(k, NONE), n.get(k, NONE))
            if cv == bv:
                continue
            if nv in (bv, cv):
                if cv is NONE:
                    out.pop(k, None)
                else:
                    out[k] = cv
            else:
                clash = True
        return (out, clash)

    def _clash(self, h: str) -> bool:
        return self._merge_own(h)[1]

    def _moved(self, h: str) -> bool:
        return h in self.C.node and self.C.parent.get(h) != self.B.parent.get(h)

    def _in_current(self, h: str) -> bool:
        return h in self.C.node and h in self.B.node

    def _where(self, idx: Index, h: str) -> str | None:
        v = idx.view.get(h)
        return title_of(idx.node[v], 1) if v and v in idx.node and (v != h) else None

    def _label(self, idx: Index, h: str, **extra: Any) -> dict[str, Any]:
        node, lvl = (idx.node[h], idx.level[h])
        out: dict[str, Any] = {'id': h, 'kind': ('view', 'section', 'card')[lvl - 1]}
        if lvl == 3 and isinstance(node.get('type'), str):
            out['type'] = node['type'][:60]
        if (t := title_of(node, lvl)):
            out['title'] = t
        if holds(node, lvl):
            out['cards'] = count_cards(node, lvl)
            if (inner := inner_of(node, lvl)):
                out['inner'] = inner
        elif (e := card_entity(node)):
            out['entity'] = e
        if (w := self._where(idx, h)):
            out['where'] = w
        return {**out, **extra}

    def _drop_dead(self, url_path: str) -> None:
        gone = drop_dead(self.result, url_path, lambda c: hid(c) is None, self._labels)
        if gone is None:
            return
        self.removed.extend(gone.removed)
        self.dead_prints, self.dead_ids, self.dead_on = (gone.prints, gone.ids, gone.on)
        said = {id(x) for x in gone.labels}
        self.local = [x for x in self.local if id(x) not in said]

    @staticmethod
    def fingerprint(node: dict[str, Any], level: int) -> str:
        if not holds(node, level):
            return json.dumps(strip_ids(node), sort_keys=True, ensure_ascii=False, default=str)
        key = kids_key(node, level)
        return json.dumps([level, {k: v for k, v in node.items() if k not in (ID, key)}], sort_keys=True, ensure_ascii=False, default=str)

    def prints_of(self, cfg: dict[str, Any], local_only: bool) -> Counter[str]:
        out: Counter[str] = Counter()

        def walk(node: dict[str, Any], level: int) -> None:
            for c in kids_of(node, level):
                if not isinstance(c, dict):
                    continue
                cl = child_level_of(level, kids_key(node, level))
                if not local_only or self._hauflo(c) is None:
                    out[self.fingerprint(c, cl)] += 1
                if holds(c, cl):
                    walk(c, cl)
        walk(cfg, 0)
        return out

    def lost(self) -> int:
        """How many of the integrator's items (a view / section / card with no Hauflo id the base knows) the merged
        config no longer holds as they are — above 0 the save is refused, whatever the cloud sent."""
        missing = self.prints_of(self.current, True) - self.prints_of(self.result, False) - self.dead_prints - self.blank_prints
        return sum(missing.values())

    def updates(self) -> dict[str, int]:
        nb, nn = (leaves(self.B), leaves(self.N))
        return {'added': sum((1 for h in nn if h not in nb)), 'changed': sum((1 for h in nn if h in nb and nn[h] != nb[h])), 'removed': sum((1 for h in nb if h not in nn and h not in self.skip))}

    def pages(self) -> list[dict[str, Any]]:
        """        stays as it is): Hauflo's new blocks (and ones brought back), its blocks with a new version where HA holds
        them as Hauflo saved them (or the integrator took Hauflo's), its blocks gone that HA still held unedited. What
        the integrator removed and keeps removed, his edits he keeps, his own cards: no change."""
        nb, nn = (leaves(self.B), leaves(self.N))
        rows: dict[str, dict[str, Any]] = {}

        def bump(idx: Index, h: str, what: str) -> None:
            v = idx.view.get(h)
            if not v or v not in idx.node:
                return
            row = rows.setdefault(v, {'id': v, 'added': 0, 'changed': 0, 'removed': 0})
            if 'title' not in row and (t := title_of(idx.node[v], 1)):
                row['title'] = t
            row[what] += 1
        taken, restored = (set(self.taken), set(self.restored))
        for h in nn:
            if h not in nb:
                bump(self.N, h, 'added')
            elif not self._in_current(h):
                if h in restored or any((self._is_under(h, r) for r in restored)):
                    bump(self.N, h, 'added')
            elif nn[h] != nb[h] and (h in taken or not self._clash(h)):
                bump(self.N, h, 'changed')
        for h in nb:
            if h not in nn and h not in self.skip and self._in_current(h) and (not self._edited(h)):
                bump(self.B, h, 'removed')
        R = Index(self.result)
        for v in self.dead_on:
            bump(R, v, 'removed')
        for v, row in rows.items():
            row['before'] = count_cards(self.C.node[v], 1) if v in self.C.node else 0
            row['after'] = count_cards(R.node[v], 1) if v in R.node else 0
        return list(rows.values())[:200]

    def _is_under(self, h: str, top: str) -> bool:
        p = self.N.parent.get(h)
        while p and p != ROOT:
            if p == top:
                return True
            p = self.N.parent.get(p)
        return False

    def untouched(self) -> list[str]:
        """        (not updated by Hauflo, not taken, not brought back, not dropped) is in what is saved exactly as HA holds it,
        once; an edited one Hauflo dropped is kept as his. Returns the ids that aren't — above none, nothing is
        saved."""
        R = Index(self.result)
        count: Counter[str] = Counter()

        def walk(node: dict[str, Any], level: int) -> None:
            for c in kids_of(node, level):
                if isinstance(c, dict):
                    cl = child_level_of(level, kids_key(node, level))
                    if (h := hid(c)) is not None:
                        count[h] += 1
                    if holds(c, cl):
                        walk(c, cl)
        walk(self.result, 0)
        kept = self.prints_of(self.result, False)
        taken = set(self.taken)
        bad: list[str] = []
        for h, c in self.C.node.items():
            if h not in self.B.node or id(c) not in self.C.first:
                continue
            lvl = self.B.level[h]
            cur = own_of(c, lvl)
            if h in self.N.node:
                if count[h] > 1:
                    bad.append(h)
                    continue
                if h in taken or (not self._clash(h) and cloud(own_of(self.N.node[h], lvl), lvl) != cloud(own_of(self.B.node[h], lvl), lvl)):
                    continue
                r = R.node.get(h)
                if r is None or own_of(r, lvl) != cur:
                    bad.append(h)
            elif self._keeps(h) and h not in self.dead_ids and (self.fingerprint(c, lvl) not in kept):
                bad.append(h)
        return bad

    def summary(self) -> dict[str, Any]:
        return {'local': self.local, 'conflicts': self.conflicts, 'removed': self.removed, 'moved': self.moved, 'restored': len(self.restored), 'taken': len(self.taken), 'updates': self.updates(), 'pages': self.pages()}
