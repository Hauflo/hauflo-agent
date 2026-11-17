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
from .const import VERSION
from .storage import HaufloStore
_LOGGER = logging.getLogger(__name__)
ID = 'hauflo_id'
PLAN = 'hauflo/dashboard/plan'
SAVE = 'hauflo/dashboard/save'
STORE_KEY = 'hauflo.dashboard_base'
META_KEY = 'hauflo.dashboard_meta'
MAX_COPIES = 3
MAX_TAKEN_COPIES = 10
COPY_RE = re.compile('-copy-\\d')
LOCAL = 'hauflo-local'
_ENTITY_RE = re.compile('^[a-z_]+\\.[a-z0-9_]+$')
_TITLE_KEYS = ('title', 'heading', 'name', 'text')
ROOT = '\x00root'
ADMINS = 'hauflo:admins'
USER_MARKER = 'hauflo:user:'
_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
LAYOUT = 'custom:hauflo-layout'

def _kids_key(node: dict[str, Any], level: int) -> str | None:
    if level == 0:
        return 'views'
    if level == 1:
        return 'sections' if isinstance(node.get('sections'), list) or node.get('type') == 'sections' else 'cards'
    if level == 2:
        return 'cards'
    if level == 3 and node.get('type') == LAYOUT:
        return 'cards'
    return None

def _holds(node: dict[str, Any], level: int) -> bool:
    return _kids_key(node, level) is not None

def _child_level(level: int, key: str | None) -> int:
    return 1 if level == 0 else 2 if key == 'sections' else 3

def _kids(node: dict[str, Any], level: int) -> list[Any]:
    key = _kids_key(node, level)
    v = node.get(key) if key else None
    return v if isinstance(v, list) else []

def _own(node: dict[str, Any], level: int) -> dict[str, Any]:
    key = _kids_key(node, level)
    return {k: v for k, v in node.items() if k != key}
SUB_KEYS = ('show_modes', 'show_presets', 'show_fan', 'show_swing', 'show_humidity', 'hvac_modes', 'preset_modes', 'fan_modes', 'swing_modes')
USER_KEYS = ('items', 'auto', 'order', *SUB_KEYS)
_CARD_PREFIX = 'custom:hauflo-'
_NONE = object()

def _user_keyed(node: dict[str, Any], level: int) -> bool:
    kind = node.get('type')
    return level == 3 and isinstance(kind, str) and kind.startswith(_CARD_PREFIX) and (kind != LAYOUT)

def _cloud(own: dict[str, Any], level: int) -> dict[str, Any]:
    return {k: v for k, v in own.items() if k not in USER_KEYS} if _user_keyed(own, level) else own

def _user(own: dict[str, Any], level: int) -> dict[str, Any]:
    return {k: copy.deepcopy(own[k]) for k in USER_KEYS if k in own} if _user_keyed(own, level) else {}

def _hid(node: Any) -> str | None:
    v = node.get(ID) if isinstance(node, dict) else None
    return v if isinstance(v, str) and v else None

def _kind(node: dict[str, Any], level: int) -> tuple[int, str | None]:
    return (level, _kids_key(node, level))

class _Index:

    def __init__(self, cfg: Any, expect: _Index | None=None) -> None:
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
        for c in _kids(node, level):
            if not isinstance(c, dict):
                continue
            cl = _child_level(level, _kids_key(node, level))
            h = _hid(c)
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
            if _holds(c, cl):
                self._walk(c, cl, here, (h if here == h else None) if cl == 1 else view)

def ensure_ids(cfg: dict[str, Any]) -> dict[str, Any]:
    seen: set[str] = set()

    def walk(node: dict[str, Any], level: int, pid: str) -> None:
        for i, c in enumerate(_kids(node, level)):
            if not isinstance(c, dict):
                continue
            h = _hid(c) or f'~{pid}/{i}'
            base, k = (h, 2)
            while h in seen:
                h, k = (f'{base}#{k}', k + 1)
            seen.add(h)
            c[ID] = h
            cl = _child_level(level, _kids_key(node, level))
            if _holds(c, cl):
                walk(c, cl, h)
    walk(cfg, 0, '')
    return cfg

def rekey(cfg: dict[str, Any], base: Any) -> dict[str, Any]:
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
            if h is not None and h in known and (known[h] != _kind(c, cl)):
                k = 2
                while f'{h}~{k}' in used:
                    k += 1
                c[ID] = f'{h}~{k}'
                used.add(c[ID])
            if _holds(c, cl):
                walk(c, cl)
    walk(cfg, 0)
    return cfg

def _leaves(idx: _Index) -> dict[str, dict[str, Any]]:
    return {h: n for h, n in idx.node.items() if idx.level[h] == 3 and (not _holds(n, 3))}

def _title(node: dict[str, Any], level: int) -> str | None:
    for k in _TITLE_KEYS:
        v = node.get(k)
        if isinstance(v, str) and v.strip() and (not _ENTITY_RE.match(v.strip())):
            return v.strip()[:80]
    if level in (1, 2):
        for c in _kids(node, level):
            if isinstance(c, dict) and c.get('type') == 'heading':
                t = _title(c, 3)
                if t:
                    return t
    return None

def _count_cards(node: dict[str, Any], level: int) -> int:
    if level == 3 and (not _holds(node, 3)):
        return 1
    return sum((_count_cards(c, _child_level(level, _kids_key(node, level))) for c in _kids(node, level) if isinstance(c, dict)))

def _inner(node: dict[str, Any], level: int, n: int=3) -> list[str]:
    out: list[str] = []

    def walk(c: dict[str, Any], lvl: int) -> None:
        for x in _kids(c, lvl):
            if len(out) >= n or not isinstance(x, dict):
                continue
            cl = _child_level(lvl, _kids_key(c, lvl))
            if _holds(x, cl):
                walk(x, cl)
            elif isinstance(x.get('type'), str) and x['type'] != 'heading':
                out.append(x['type'][:60])
    walk(node, level)
    return out

def _card_entity(node: dict[str, Any]) -> str | None:
    for k in ('entity', 'entities'):
        v = node.get(k)
        v = v[0] if isinstance(v, list) and v else v
        v = v.get('entity') if isinstance(v, dict) else v
        if isinstance(v, str) and _ENTITY_RE.match(v):
            return v
    return None

def _strip_ids(node: Any) -> Any:
    if isinstance(node, dict):
        out = {k: v for k, v in node.items() if k != ID}
        for key in ('views', 'sections', 'cards'):
            if isinstance(out.get(key), list):
                out[key] = [_strip_ids(x) for x in out[key]]
        return out
    return node

def _view_of(target: Any, url_path: str) -> str | None:
    if not isinstance(target, str):
        return None
    t = target.strip().split('#', 1)[0].split('?', 1)[0]
    if not t.startswith('/') or t.startswith('//'):
        return None
    parts = [x for x in t.split('/') if x]
    return parts[1] if len(parts) >= 2 and parts[0] == url_path else None
_CONTENT_KEYS = ('entity', 'entities', 'area')

def _ways(card: dict[str, Any]) -> list[Any]:
    out: list[Any] = []
    if isinstance(card.get('path'), str) and card['path'].strip():
        out.append(card['path'])
    if not any((k in card for k in _CONTENT_KEYS)):
        if isinstance(card.get('navigation_path'), str):
            out.append(card['navigation_path'])
        tap = card.get('tap_action')
        if isinstance(tap, dict) and tap.get('action') == 'navigate':
            out.append(tap.get('navigation_path'))
    return out
DEAD_LINK = 'dead_link'

class Dead:

    def __init__(self) -> None:
        self.removed: list[dict[str, Any]] = []
        self.prints: Counter[str] = Counter()
        self.ids: set[str] = set()
        self.on: list[str] = []
        self.labels: list[dict[str, Any]] = []

def drop_dead(cfg: Any, url_path: str, mine: Callable[[dict[str, Any]], bool], labels: dict[int, dict[str, Any]] | None=None) -> Dead | None:
    views = [v for v in _kids(cfg, 0) if isinstance(v, dict)] if isinstance(cfg, dict) else []
    if not views:
        return None
    paths = {v['path'] for v in views if isinstance(v.get('path'), str) and v['path']}
    out = Dead()
    used: set[str] = set()

    def gone(card: dict[str, Any]) -> str | None:
        ways = _ways(card)
        pages = [_view_of(w, url_path) for w in ways]
        if not ways or any((p is None for p in pages)):
            return None
        if any((p in paths or (p.isdigit() and int(p) < len(views)) for p in pages if p is not None)):
            return None
        return pages[0]

    def drop(card: dict[str, Any], to: str, view: dict[str, Any]) -> None:
        out.prints[Merge._fp(card, 3)] += 1
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
        if (t := _title(card, 3)):
            item['title'] = t
        if (w := _title(view, 1)):
            item['where'] = w
        out.removed.append({**item, 'reason': DEAD_LINK, 'to': to[:128]})
        if (vh := _hid(view)) is not None:
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
            inner = _stack(c) if his or c.get('type') == LAYOUT else None
            if inner:
                before = Merge._fp(c, 3)
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
        if _kids_key(v, 1) == 'cards':
            if isinstance(v.get('cards'), list):
                v['cards'] = cards(v['cards'], v)
            continue
        if not isinstance(v.get('sections'), list):
            continue
        secs: list[Any] = []
        for s in v['sections']:
            if isinstance(s, dict) and isinstance(s.get('cards'), list) and s['cards']:
                before = Merge._fp(s, 2)
                s['cards'] = cards(s['cards'], v)
                if not s['cards'] and mine(s):
                    out.prints[before] += 1
                    continue
            secs.append(s)
        v['sections'] = secs
    return out

class Merge:

    def __init__(self, new: dict[str, Any], current: Any, base: dict[str, Any], take: Any=(), restore: Any=(), skip: Any=(), url_path: str | None=None) -> None:
        self.new, self.current, self.base = (new, current if isinstance(current, dict) else {'views': []}, base)
        self.skip: set[str] = set(skip)
        self.B = _Index(base)
        self.new = rekey(new, base)
        self.N, self.C = (_Index(self.new), _Index(self.current, expect=self.B))
        self.take_all, self.take = (take is True, set(take) if isinstance(take, list) else set())
        self.restore_all, self.restore = (restore is True, set(restore) if isinstance(restore, list) else set())
        self.local: list[dict[str, Any]] = []
        self.conflicts: list[dict[str, Any]] = []
        self.removed: list[dict[str, Any]] = []
        self.moved: list[dict[str, Any]] = []
        self.restored: list[str] = []
        self.taken: list[str] = []
        self.on_view: dict[int, str | None] = {}
        for v in _kids(self.current, 0):
            if isinstance(v, dict):
                self._mark_view(v, 1, _title(v, 1))
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
        for c in _kids(node, level):
            if isinstance(c, dict):
                self.on_view[id(c)] = title
                cl = _child_level(level, _kids_key(node, level))
                if _holds(c, cl):
                    self._mark_view(c, cl, title)

    def _hauflo(self, node: dict[str, Any]) -> str | None:
        h = _hid(node)
        return h if h is not None and h in self.B.node and (id(node) in self.C.first) else None

    def _edited(self, h: str) -> bool:
        lvl = self.B.level[h]
        return _own(self.C.node[h], lvl) != _own(self.B.node[h], lvl)

    def _twin(self, h: str) -> str | None:
        lvl = self.B.level[h]
        b = self.B.node[h]

        def marks(node: dict[str, Any]) -> set[tuple[str, str]]:
            out = set()
            if isinstance(node.get('path'), str) and node.get('path'):
                out.add(('path', node['path']))
            if lvl == 3 and (e := _card_entity(node)):
                out.add(('entity', e))
            if (t := _title(node, lvl)):
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
        b = _cloud(_own(self.B.node[h], lvl), lvl)
        c = _cloud(_own(self.C.node[h], lvl), lvl)
        n = _cloud(_own(self.N.node[h], self.N.level[h]), self.N.level[h])
        out, clash = (dict(n), False)
        for k in {*b, *c, *n}:
            bv, cv, nv = (b.get(k, _NONE), c.get(k, _NONE), n.get(k, _NONE))
            if cv == bv:
                continue
            if nv == bv or nv == cv:
                if cv is _NONE:
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

    def _where(self, idx: _Index, h: str) -> str | None:
        v = idx.view.get(h)
        return _title(idx.node[v], 1) if v and v in idx.node and (v != h) else None

    def _label(self, idx: _Index, h: str, **extra: Any) -> dict[str, Any]:
        node, lvl = (idx.node[h], idx.level[h])
        out: dict[str, Any] = {'id': h, 'kind': ('view', 'section', 'card')[lvl - 1]}
        if lvl == 3 and isinstance(node.get('type'), str):
            out['type'] = node['type'][:60]
        if (t := _title(node, lvl)):
            out['title'] = t
        if _holds(node, lvl):
            out['cards'] = _count_cards(node, lvl)
            if (inner := _inner(node, lvl)):
                out['inner'] = inner
        elif (e := _card_entity(node)):
            out['entity'] = e
        if (w := self._where(idx, h)):
            out['where'] = w
        return {**out, **extra}

    def _dissolve(self) -> None:
        for h, lvl in self.B.level.items():
            if lvl < 2 or h in self.C.node or h not in self.N.node or (not _holds(self.B.node[h], lvl)):
                continue
            where = Counter((self.C.parent[x] for x, p in self.B.parent.items() if p == h and x in self.C.node and (self.C.parent[x] in self.N.node) and (self.C.parent[x] != h)))
            if not where:
                continue
            back = self.restore_all or h in self.restore
            kids = [self._fresh(i) for x in _kids(self.N.node[h], lvl) if isinstance(x, dict) and (i := _hid(x)) and (back or i not in self.B.node) and (i not in self.C.node)]
            self.dissolved.add(h)
            self.adopt.setdefault(where.most_common(1)[0][0], []).extend(kids)
            self.restored.extend((k[ID] for k in kids if back and k[ID] in self.B.node))

    def _home(self, c: dict[str, Any]) -> str | None:
        h = _hid(c)
        top = [x for x in _kids(self.new, 0) if isinstance(x, dict) and (i := _hid(x)) and (i in self.N.node) and (not (i in self.B.node and i not in self.C.node))]
        if not top or h is None:
            return None
        hit = [x for x in top if re.fullmatch(re.escape(h) + '~\\d+', _hid(x) or '')]
        path = c.get('path') if isinstance(c.get('path'), str) and c.get('path') else None
        if not hit and path:
            hit = [x for x in top if x.get('path') == path]
        if not hit and (name := _same_name(_title(c, 1))):
            hit = [x for x in top if _same_name(_title(x, 1)) == name and bool(x.get('subview')) == bool(c.get('subview'))]
            if len(hit) > 1 and path:

                def tail(x: dict[str, Any]) -> int:
                    a, b = (path[::-1], str(x.get('path') or '')[::-1])
                    n = 0
                    while n < min(len(a), len(b)) and a[n] == b[n]:
                        n += 1
                    return n
                best = max((tail(x) for x in hit))
                hit = [x for x in hit if tail(x) == best]
        return _hid(hit[0]) if len(hit) == 1 else None

    def _rehome(self) -> None:
        for c in _kids(self.current, 0):
            if not isinstance(c, dict) or (h := self._hauflo(c)) is None or h in self.N.node or self._edited(h):
                continue
            t = self._home(c)
            if t is None:
                continue
            cl = _child_level(1, _kids_key(self.N.node[t], 1))
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
        own = _cloud(_own(n, lvl), lvl)
        if self._in_current(h):
            cur_own = _own(self.C.node[h], self.B.level[h])
            merged, clash = self._merge_own(h)
            if clash:
                taken = self.take_all or h in self.take
                self.conflicts.append(self._label(self.B, h, kept='hauflo' if taken else 'yours', updated=own != _cloud(_own(self.B.node[h], lvl), lvl)))
                if not taken:
                    own = _cloud(cur_own, lvl)
                else:
                    self.taken.append(h)
            else:
                own = merged
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
                continue
            if nh in self.B.node and nh not in self.C.node:
                if self.restore_all or nh in self.restore:
                    back = self._fresh(nh)
                    key = _kids_key(back, self.N.level[nh])
                    if key is not None and (not back[key]) and _kids(self.N.node[nh], self.N.level[nh]):
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

        def pos(node: dict[str, Any], idx: _Index, mine: bool) -> dict[str, int]:
            got: dict[str, int] = {}
            for i, c in enumerate(_kids(node, level)):
                x = self._hauflo(c) if mine else _hid(c)
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
            key = _kids_key(node, level)
            out = {k: copy.deepcopy(v) for k, v in node.items() if k != key}
            if key is not None:
                cl = _child_level(level, key)
                out[key] = [strip(x, cl) for x in _kids(node, level) if isinstance(x, dict) and (not self._in_current(_hid(x) or ''))]
            return out
        return strip(self.N.node[h], self.N.level[h])

    def _float(self, out: list[Any], level: int, c_node: dict[str, Any]) -> None:
        child_level = _child_level(level, _kids_key(c_node, level))
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
        for c in _kids(c_node, level):
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
            if self._keeps(h) and (not (child_level == 1 and _blank_view(c))):
                node = self._local(c, child_level)
                self.local.append(self._said(node, self._local_label(c, child_level, dropped=h)))
                put(node) if anchored else tail.append(node)
            else:
                if self._edited(h) and (not (child_level == 1 and _blank_view(c))):
                    self.removed.append(self._label(self.B, h, twin=self._twin(h)))
                if id(c) not in self.rehomed:
                    items = self.held.pop(id(c)) if id(c) in self.held else self._orphans(c, child_level)
                    tail.extend(self._fit(items, child_level, c))
            pos = None
        out.extend(tail)

    def _local(self, c: dict[str, Any], level: int) -> dict[str, Any]:
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
            elif self._edited(h):
                kids.append(self._local(x, cl))
            else:
                kids.extend(self._fit(self._orphans(x, cl), cl, x))
        out[key] = kids
        return out

    def _orphans(self, c: dict[str, Any], level: int) -> list[tuple[int, dict[str, Any]]]:
        if not _holds(c, level):
            return []
        out: list[tuple[int, dict[str, Any]]] = []
        cl = _child_level(level, _kids_key(c, level))
        for x in _kids(c, level):
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
            elif self._keeps(h) and (not (cl == 1 and _blank_view(x))):
                node = self._local(x, cl)
                self.local.append(self._said(node, self._local_label(x, cl, dropped=h)))
                out.append((cl, node))
            else:
                if self._edited(h) and (not (cl == 1 and _blank_view(x))):
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
            src = holder if isinstance(holder, dict) and _hid(holder) and (self.B.level.get(_hid(holder) or '') == 1) else {}
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
        ours = {v.get('path') for v in views if isinstance(v, dict) and _hid(v) and v.get('path')}
        gone = [v for v in views if isinstance(v, dict) and _hid(v) is None and (v.get('path') in ours) and _blank_view(v)]
        if not gone:
            return
        self.result['views'] = [v for v in views if not any((v is g for g in gone))]
        self.blank_prints = self._prints({'views': gone}, True)
        said = {id(self._labels[id(v)]) for v in gone if id(v) in self._labels}
        self.local = [x for x in self.local if id(x) not in said]

    def _drop_dead(self, url_path: str) -> None:
        gone = drop_dead(self.result, url_path, lambda c: _hid(c) is None, self._labels)
        if gone is None:
            return
        self.removed.extend(gone.removed)
        self.dead_prints, self.dead_ids, self.dead_on = (gone.prints, gone.ids, gone.on)
        said = {id(x) for x in gone.labels}
        self.local = [x for x in self.local if id(x) not in said]

    def _local_label(self, c: dict[str, Any], level: int, dropped: str | None=None) -> dict[str, Any]:
        out: dict[str, Any] = {'kind': ('view', 'section', 'card')[level - 1]}
        if level == 3 and isinstance(c.get('type'), str):
            out['type'] = c['type'][:60]
        if (t := _title(c, level)):
            out['title'] = t
        if _holds(c, level):
            out['cards'] = _count_cards(c, level)
            if (inner := _inner(c, level)):
                out['inner'] = inner
        if level > 1 and (w := self.on_view.get(id(c))):
            out['where'] = w
        if dropped:
            out['dropped'] = True
            out['id'] = dropped
        return out

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
        missing = self._prints(self.current, True) - self._prints(self.result, False) - self.dead_prints - self.blank_prints
        return sum(missing.values())

    def updates(self) -> dict[str, int]:
        nb, nn = (_leaves(self.B), _leaves(self.N))
        return {'added': sum((1 for h in nn if h not in nb)), 'changed': sum((1 for h in nn if h in nb and nn[h] != nb[h])), 'removed': sum((1 for h in nb if h not in nn and h not in self.skip))}

    def pages(self) -> list[dict[str, Any]]:
        nb, nn = (_leaves(self.B), _leaves(self.N))
        rows: dict[str, dict[str, Any]] = {}

        def bump(idx: _Index, h: str, what: str) -> None:
            v = idx.view.get(h)
            if not v or v not in idx.node:
                return
            row = rows.setdefault(v, {'id': v, 'added': 0, 'changed': 0, 'removed': 0})
            if 'title' not in row and (t := _title(idx.node[v], 1)):
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
        R = _Index(self.result)
        for v in self.dead_on:
            bump(R, v, 'removed')
        for v, row in rows.items():
            row['before'] = _count_cards(self.C.node[v], 1) if v in self.C.node else 0
            row['after'] = _count_cards(R.node[v], 1) if v in R.node else 0
        return list(rows.values())[:200]

    def _is_under(self, h: str, top: str) -> bool:
        p = self.N.parent.get(h)
        while p and p != ROOT:
            if p == top:
                return True
            p = self.N.parent.get(p)
        return False

    def untouched(self) -> list[str]:
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
                if h in taken or (not self._clash(h) and _cloud(_own(self.N.node[h], lvl), lvl) != _cloud(_own(self.B.node[h], lvl), lvl)):
                    continue
                r = R.node.get(h)
                if r is None or _own(r, lvl) != cur:
                    bad.append(h)
            elif self._keeps(h) and h not in self.dead_ids:
                if self._fp(c, lvl) not in kept:
                    bad.append(h)
        return bad

    def summary(self) -> dict[str, Any]:
        return {'local': self.local, 'conflicts': self.conflicts, 'removed': self.removed, 'moved': self.moved, 'restored': len(self.restored), 'taken': len(self.taken), 'updates': self.updates(), 'pages': self.pages()}

def unpanel(cfg: Any, new: dict[str, Any]) -> Any:
    if not isinstance(cfg, dict) or not isinstance(new, dict):
        return cfg
    to_sections: dict[str, str] = {}
    for v in _kids(new, 0):
        h = _hid(v)
        secs = v.get('sections') if isinstance(v, dict) else None
        if h and isinstance(secs, list):
            first = next((_hid(s) for s in secs if _hid(s)), None)
            to_sections[h] = first or f'{h}/s:panel'
    views = cfg.get('views')
    if not to_sections or not isinstance(views, list):
        return cfg
    out_views: list[Any] = []
    changed = False
    for v in views:
        h = _hid(v)
        if h in to_sections and isinstance(v, dict) and (_kids_key(v, 1) == 'cards'):
            cards = [c for c in v.get('cards', []) if isinstance(c, dict)] if isinstance(v.get('cards'), list) else []
            own = {k: copy.deepcopy(x) for k, x in v.items() if k not in ('cards', 'type')}
            out_views.append({**own, 'type': 'sections', 'sections': [{'type': 'grid', ID: to_sections[h], 'cards': copy.deepcopy(cards)}]})
            changed = True
        else:
            out_views.append(v)
    return {**cfg, 'views': out_views} if changed else cfg

def _same_name(t: Any) -> str:
    return ' '.join(t.split()).casefold() if isinstance(t, str) else ''

def _leads_to(card: dict[str, Any], path: str) -> bool:
    ways = [card.get('path'), card.get('navigation_path')]
    for k in ('tap_action', 'hold_action'):
        a = card.get(k)
        if isinstance(a, dict):
            ways.append(a.get('navigation_path'))
    return any((isinstance(w, str) and (w.strip('/') == path or w.rstrip('/').endswith(f'/{path}')) for w in ways))

def _stack(card: dict[str, Any]) -> list[Any] | None:
    v = card.get('cards')
    return v if isinstance(v, list) else None

def _tiles_to(cfg: dict[str, Any], path: str) -> int:
    n = 0

    def card(c: dict[str, Any]) -> None:
        nonlocal n
        n += _hid(c) is None and _leads_to(c, path)
        for x in (_stack(c) if _hid(c) is None or c.get('type') == LAYOUT else None) or []:
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
    views = [v for v in (cfg.get('views') if isinstance(cfg, dict) else None) or [] if isinstance(v, dict)]
    his = {v['path'] for v in views if _hid(v) is None and isinstance(v.get('path'), str)}
    taken = {v.get('path') for v in views}
    for v in views:
        p = v.get('path')
        if _hid(v) is None or not isinstance(p, str) or p not in his or _opened(cfg, p):
            continue
        n = 2
        while f'{p}_{n}' in taken:
            n += 1
        v['path'] = f'{p}_{n}'
        taken.add(v['path'])

def twins(current: Any, new: Any) -> list[dict[str, Any]]:
    if not isinstance(current, dict) or not isinstance(new, dict):
        return []
    names = {_same_name(_title(v, 1)) for v in _kids(new, 0) if isinstance(v, dict) and _hid(v)} - {''}
    out: list[dict[str, Any]] = []
    for v in _kids(current, 0):
        if not isinstance(v, dict) or _hid(v) is not None:
            continue
        path, title = (v.get('path'), _title(v, 1))
        if isinstance(path, str) and path.strip() and (_same_name(title) in names) and (_count_cards(v, 1) == 0):
            out.append({'id': path.strip()[:128], 'title': title, 'tiles': _tiles_to(current, path.strip())})
    return out[:50]

def drop_twins(current: Any, new: Any, ids: Any) -> tuple[Any, int]:
    want = {x for x in ids if isinstance(x, str)} if isinstance(ids, list) else set()
    gone = {t['id'] for t in twins(current, new) if t['id'] in want}
    if not gone:
        return (current, 0)

    def emptied(before: dict[str, Any], after: dict[str, Any], key: str) -> bool:
        b, a = (before.get(key), after.get(key))
        return _hid(before) is None and isinstance(b, list) and bool(b) and isinstance(a, list) and (not a)

    def card(c: dict[str, Any]) -> dict[str, Any] | None:
        if _hid(c) is None and any((_leads_to(c, p) for p in gone)):
            return None
        inner = _stack(c) if _hid(c) is None or c.get('type') == LAYOUT else None
        if inner is None:
            return c
        out = {**c, 'cards': [y for y in (card(x) if isinstance(x, dict) else x for x in inner) if y is not None]}
        return None if emptied(c, out, 'cards') else out

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
            if level == 0 and _hid(c) is None and isinstance(c.get('path'), str) and (c['path'].strip() in gone):
                continue
            if cl == 3:
                x = card(c)
            else:
                x = keep(c, cl)
                if cl == 2 and emptied(c, x, 'cards'):
                    x = None
            if x is not None:
                kids.append(x)
        return {**node, key: kids}
    return (keep(copy.deepcopy(current), 0), len(gone))

def _slug(v: Any) -> str:
    s = unicodedata.normalize('NFC', '' if v is None else str(v))
    return re.sub('[^\\w.-]+', '-', s).strip('-')[:48]

def _str(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None

def _entity_of(v: Any) -> str | None:
    return v if isinstance(v, str) else _str(v.get('entity')) if isinstance(v, dict) else None

def _first_of(v: Any) -> str | None:
    return _entity_of(v[0]) if isinstance(v, list) and v else None

def _skey(c: dict[str, Any]) -> str:
    kids = c.get('cards')
    inner = kids[0] if isinstance(kids, list) and kids and isinstance(kids[0], dict) else None
    tap = c.get('tap_action')
    nav = _str(tap.get('navigation_path')) if isinstance(tap, dict) else None
    key: Any
    if c.get('type') == LAYOUT:
        key = _skey(inner) if inner else ''
    else:
        key = next((x for x in (_str(c.get('entity')), _str(c.get('intercom')), _first_of(c.get('entities')), _str(c.get('block')), _str(c.get('area')), _str(c.get('room')), _str(c.get('floor')), _str(c.get('path')), nav, _first_of(c.get('scenes')), _first_of(c.get('alarms')), _entity_of(inner) or _str(inner.get('block')) if inner else None, _str(c.get('icon')) if c.get('type') == 'heading' else None) if x is not None), '')
    t = c.get('type')
    kind = _slug(re.sub('^custom:(hauflo-)?', '', 'card' if t is None else str(t)))
    sub = f"{_slug(c['kind'])}/" if _str(c.get('kind')) and key != c.get('kind') else ''
    return f'{kind}:{sub}{_slug(key)}'

def _sizeless(node: dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in node.items() if k not in (ID, 'grid_options')}, sort_keys=True, ensure_ascii=False, default=str)

def _old_ids(idx: _Index) -> bool:
    return any((idx.level[h] == 3 and idx.level.get(idx.parent[h]) == 2 and h.startswith(f'{idx.parent[h]}/') for h in idx.node))

def _new_ids(idx: _Index) -> bool:
    cards = [h for h in idx.node if idx.level[h] == 3 and idx.level.get(idx.parent[h]) == 2]
    return bool(cards) and (not any((h.startswith(f'{idx.parent[h]}/') for h in cards)))

def upgrade(base: dict[str, Any], current: Any, new: dict[str, Any]) -> tuple[dict[str, Any], Any, dict[str, Any]] | None:
    if not isinstance(base, dict) or not isinstance(current, dict):
        return None
    B0, N = (_Index(base), _Index(new))
    if not _old_ids(B0) or not _new_ids(N):
        return None
    base, current = (copy.deepcopy(base), copy.deepcopy(current))
    B = _Index(base)
    C = _Index(current, expect=B)

    def media(h: str) -> str:
        p = B.parent.get(h, ROOT)
        return json.dumps(B.node[p].get('visibility'), sort_keys=True, default=str) if B.level.get(p) == 2 and B.node[p].get('visibility') else ''

    def phone(h: str) -> bool:
        node = B.node[h] if B.level.get(h) == 2 else B.node.get(B.parent.get(h, ROOT), {})
        m = json.dumps(node.get('visibility') or '', default=str)
        return 'media_query' not in m or 'max-width' in m
    by_content: dict[tuple[str | None, str], list[str]] = {}
    for h, n in _leaves(N).items():
        by_content.setdefault((N.view.get(h), _sizeless(n)), []).append(h)
    seen_c: Counter[tuple[Any, ...]] = Counter()
    seen_k: Counter[tuple[Any, ...]] = Counter()
    used: set[tuple[str | None, str, str]] = set()
    target: dict[str, str] = {}
    for h, n in _leaves(B).items():
        v, m, c = (B.view.get(h), media(h), _sizeless(n))
        seen_c[v, m, c] += 1
        options = by_content.get((v, c), [])
        i = seen_c[v, m, c] - 1
        t = options[i] if i < len(options) else None
        if t is None or (v, m, t) in used:
            k = _skey(n)
            seen_k[v, m, k] += 1
            j = seen_k[v, m, k]
            cand = f'{v}/{k}' if j == 1 else f'{v}/{k}#{j}'
            t = cand if cand in N.node and N.view.get(cand) == v and (N.level.get(cand) == 3) and ((v, m, cand) not in used) else None
        if t is not None and t not in B.node:
            used.add((v, m, t))
            target[h] = t
    groups: dict[str, list[str]] = {}
    for h, t in target.items():
        groups.setdefault(t, []).append(h)
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
        B.node[h].clear()
        B.node[h].update(copy.deepcopy(C.node[h]))
    survivor: dict[str, str] = {}
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
        if here(keep) and (not edited(keep)):
            same(keep)
            for node in (B.node[keep], C.node[keep]):
                node.pop('grid_options', None)
                if 'grid_options' in N.node[t]:
                    node['grid_options'] = copy.deepcopy(N.node[t]['grid_options'])

    def new_section(t: str) -> str | None:
        p = N.parent.get(t)
        while p and p != ROOT and (N.level.get(p, 0) > 2):
            p = N.parent.get(p)
        return p if p and N.level.get(p) == 2 else None
    sec_groups: dict[str, list[str]] = {}
    for h in B.node:
        if B.level[h] != 2:
            continue
        first = next((x for x in (_hid(c) for c in _kids(B.node[h], 2) if isinstance(c, dict)) if x in target), None)
        ns = new_section(target[first]) if first else None
        if ns and ns not in B.node and (N.view.get(ns) == B.view.get(h)):
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
                continue
            stay: list[Any] = []
            anchor: str | None = None
            for x in _kids(C.node[o], 2):
                hx = _hid(x) if isinstance(x, dict) else None
                if isinstance(x, dict) and hx in B.node and (id(x) in C.first):
                    anchor = hx
                    stay.append(x)
                    continue
                if not isinstance(x, dict):
                    stay.append(x)
                    continue
                fp = Merge._fp(x, 3) if not _holds(x, 3) else None
                if fp is not None and fp in prints:
                    allow[fp] += 1
                    continue
                if fp is not None:
                    prints.add(fp)
                t = target.get(anchor or '')
                at = next((i + 1 for i, y in enumerate(dest) if isinstance(y, dict) and target.get(_hid(y) or '') == t), len(dest)) if t else len(dest)
                dest.insert(at, x)
                moved_local += 1
            C.node[o]['cards'] = stay
    for old, t in survivor.items():
        for node in (B.node.get(old), C.node.get(old) if here(old) else None):
            if node is not None:
                node[ID] = t
    for ns in sec_groups:
        kids = _kids(N.node[ns], 2)
        if len(kids) != 1 or not isinstance(kids[0], dict) or kids[0].get('type') != LAYOUT or (_hid(kids[0]) in B.node):
            continue
        lay = _own(kids[0], 3)
        for cfg_node in (_find(base, ns), _find(current, ns)):
            if cfg_node is not None and isinstance(cfg_node.get('cards'), list):
                cfg_node['cards'] = [{**copy.deepcopy(lay), 'cards': cfg_node['cards']}]
    return (base, current, {'copies': copies, 'allow': allow, 'blocks': len(groups), 'moved': moved_local})

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
_KEY = re.compile('^[A-Za-z0-9_:-]{1,40}$')

def edited_where(seen: Any, base: Any, limit: int=5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def place(path: list[Any], key: str | None, op: str) -> None:
        item: dict[str, Any] = {'path': '/'.join((str(p) for p in path)), 'op': op}
        if key is not None:
            item['key'] = key if _KEY.match(key) else '?'
        views = base.get('views') if isinstance(base, dict) else None
        if len(path) >= 2 and path[0] == 'views' and isinstance(path[1], int) and isinstance(views, list) and (path[1] < len(views)):
            title = views[path[1]].get('title') if isinstance(views[path[1]], dict) else None
            if isinstance(title, str) and title:
                item['view'] = title[:80]
        node: Any = base
        for p in path:
            if isinstance(node, dict) and isinstance(p, str):
                node = node.get(p)
            elif isinstance(node, list) and isinstance(p, int) and (p < len(node)):
                node = node[p]
            else:
                node = None
                break
        if isinstance(node, dict) and isinstance(node.get('type'), str):
            item['type'] = node['type'][:60]
        out.append(item)

    def walk(a: Any, b: Any, path: list[Any]) -> None:
        if len(out) >= limit or a == b:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for k in [*b.keys(), *(k for k in a.keys() if k not in b)]:
                if len(out) >= limit:
                    return
                if k not in a:
                    place(path, str(k), 'removed')
                elif k not in b:
                    place(path, str(k), 'added')
                elif a[k] != b[k]:
                    if isinstance(a[k], (dict, list)) and type(a[k]) is type(b[k]):
                        walk(a[k], b[k], [*path, str(k)])
                    else:
                        place(path, str(k), 'changed')
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                place(path, None, 'count')
                return
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, [*path, i])
        else:
            place(path, None, 'changed')
    walk(seen, base, [])
    return out

def _digest(config: Any) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

def plan_and_merge(new: dict[str, Any], current: Any, base: dict[str, Any] | None, take: Any=(), restore: Any=(), url_path: str | None=None) -> tuple[dict[str, Any], dict[str, Any]]:
    new = ensure_ids(copy.deepcopy(new))
    if base is not None:
        base, seen = (unpanel(base, new), unpanel(current, new))
    new = rekey(new, base)
    if base is None:
        return (new, {'base': False})
    up = upgrade(base, seen, new)
    b2, c2, info = up if up is not None else (base, seen, None)
    m = Merge(new, c2, b2, take, restore, skip=info['copies'] if info else (), url_path=url_path)
    unique_paths(m.result)
    lost = sum((_local_prints(seen, base) - m._prints(m.result, False) - (info['allow'] if info else Counter()) - m.dead_prints - m.blank_prints).values())
    report = {**m.summary(), 'base': True, 'edited': seen != base, 'changes': m.result != current, 'lost': max(m.lost(), lost), 'untouched': len(m.untouched())}
    if seen != base:
        report['edited_where'] = edited_where(seen, base)
    if info:
        report['layout'] = {'blocks': info['blocks'], 'copies': len(info['copies']), 'moved': info['moved']}
    return (m.result, report)

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

    def fix(key: str, v: list[Any]) -> list[Any]:
        names = _marked_names(v, key)
        if not names:
            return v
        ids = {i for i, n in users if n in names}
        if key == 'visible':
            return [x for x in v if not (isinstance(x, dict) and set(x) == {'user'} and (x['user'] in ids))]
        return [x for x in v if x not in ids]
    return _walk_admins(cfg, fix)
ACCESS_ALL = 'hauflo:all'
ACCESS_ALL_TRUE = 'hauflo:all-true'
ACCESS_MORE = 'hauflo:guests'
_ACCESS_TAGS = (ACCESS_ALL, ACCESS_ALL_TRUE, ACCESS_MORE)

class Access:

    def __init__(self, family: list[str], guests: set[str], pages: dict[str, dict[str, list[str]]], active: bool) -> None:
        self.family = family
        self.guests = guests
        self.pages = pages
        self.active = active

    def key(self) -> tuple[Any, ...]:
        return (tuple(self.family), tuple(sorted(self.guests)), json.dumps(self.pages, sort_keys=True), self.active)

def _blank_view(v: Any) -> bool:
    if not isinstance(v, dict):
        return False
    if any((isinstance(c, dict) for c in v.get('cards') or [])):
        return False
    return not any((isinstance(c, dict) for s in v.get('sections') or [] if isinstance(s, dict) for c in s.get('cards') or []))

def _views(cfg: Any) -> list[Any]:
    return cfg['views'] if isinstance(cfg, dict) and isinstance(cfg.get('views'), list) else []

def expand_access(cfg: Any, access: Access | None, url_path: str) -> Any:
    if access is None or not access.active:
        return cfg
    given = access.pages.get(url_path, {})
    for v in _views(cfg):
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
    for v in _views(cfg):
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
            v['visible'] = [x for x, u in zip(vis, users) if u not in guests]
    return cfg

def redact(current: Any, base: dict[str, Any], key: bytes) -> Any:
    if not isinstance(current, dict):
        return current
    B = _Index(base)
    C = _Index(current, expect=B)

    def sign(v: Any) -> str:
        return hmac.new(key, json.dumps(v, sort_keys=True, ensure_ascii=False, default=str).encode(), hashlib.sha256).hexdigest()[:24]

    def node_out(c: dict[str, Any], level: int) -> dict[str, Any]:
        h = _hid(c)
        if h is None or h not in B.node or id(c) not in C.first:
            return {'type': LOCAL, 'hash': sign(c)}
        key_ = _kids_key(c, level)
        own = _own(c, level)
        mine = _cloud(own, level)
        out = copy.deepcopy(mine) if mine == _cloud(_own(B.node[h], level), level) else {ID: h, 'type': LOCAL, 'edited': sign(own)}
        if key_ is not None:
            out[key_] = kids_out(c, level)
        return out

    def kids_out(node: dict[str, Any], level: int) -> list[Any]:
        cl = _child_level(level, _kids_key(node, level))
        return [node_out(x, cl) if isinstance(x, dict) else {'type': LOCAL, 'hash': sign(x)} for x in _kids(node, level)]
    own = {k: v for k, v in current.items() if k != 'views'}
    base_own = {k: v for k, v in base.items() if k != 'views'}
    out: dict[str, Any] = copy.deepcopy(own) if own == base_own else {'type': LOCAL, 'edited': sign(own)}
    if 'views' in current:
        out['views'] = kids_out(current, 0)
    return out

class DashboardMerge:

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: HaufloStore = HaufloStore(hass, STORE_KEY)
        self.meta_store: HaufloStore = HaufloStore(hass, META_KEY)
        self.bases: dict[str, dict[str, Any]] = {}
        self.copies: dict[str, list[str]] = {}
        self.taken_copies: set[str] = set()
        self.written: dict[str, str] = {}
        self.merged_by: str | None = None
        self.key: bytes = b''
        self.access_of: Callable[[], Any] | None = None
        self.write_lock = asyncio.Lock()

    async def _access(self) -> Access | None:
        if self.access_of is None:
            return None
        try:
            return await self.access_of()
        except Exception:
            _LOGGER.warning('Hauflo: who sees which page not known', exc_info=True)
            return None

    def _plain(self, current: Any, access: Access | None) -> Any:
        return collapse_access(copy.deepcopy(current), access.guests if access else frozenset()) if current is not None else None

    def pages(self) -> list[str]:
        return [u for u in self.bases if not is_copy(u)]

    async def apply_access(self) -> int:
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
        access = await self._access()
        admins = set(await self._admin_ids())
        out: list[dict[str, Any]] = []
        for url_path in self.pages():
            exists, current = await self._current(url_path)
            item = getattr(self._dashboard(url_path), 'config', None) or {}
            if not exists or not isinstance(current, dict) or (not isinstance(item, dict)) or item.get('require_admin'):
                continue
            views = []
            for v in _views(self._plain(current, access)):
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
        key = meta.get('key')
        if not (isinstance(key, str) and len(key) == 64):
            key = secrets.token_hex(32)
            await self._save_meta(key)
        self.key = bytes.fromhex(key)

    async def reorder_views(self, url_path: str, order: dict[str, list[str]]) -> bool:
        from . import order as block_order
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
        self.written[url_path] = _digest(back if back is not None else out)
        await self._save_meta()
        return True

    async def _save_meta(self, key: str | None=None) -> None:
        await self.meta_store.async_save({'copies': self.copies, 'taken': sorted(self.taken_copies), 'written': self.written, 'key': key or self.key.hex(), **({'merged_by': self.merged_by} if self.merged_by else {})})

    def unchanged(self, url_path: str, raw: Any) -> bool:
        return raw is not None and self.written.get(url_path) == _digest(raw)

    def manages(self, url_path: Any) -> bool:
        return isinstance(url_path, str) and url_path in self.bases

    def _dashboard(self, url_path: str) -> Any:
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        return self.hass.data[LOVELACE_DATA].dashboards.get(url_path)

    async def _current(self, url_path: str) -> tuple[bool, Any]:
        from homeassistant.components.lovelace.const import ConfigNotFound
        dash = self._dashboard(url_path)
        if dash is None or getattr(dash, 'mode', 'storage') != 'storage':
            return (False, None)
        try:
            return (True, await dash.async_load(False))
        except ConfigNotFound:
            return (True, None)

    async def read(self, url_path: str) -> Any:
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
            return []
        return sorted((u.id for u in users if u.is_admin and u.is_active and (not u.system_generated)))

    async def _users(self) -> list[tuple[str, str]]:
        try:
            users = await self.hass.auth.async_get_users()
        except Exception:
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
        from . import order as block_order
        if (views := block_order.view_order(merged, await block_order.async_load(self.hass), url_path)) is not None:
            merged = {**merged, 'views': views}
        out = expand_access(copy.deepcopy(merged), access, url_path)
        if out != raw:
            await self._dashboard(url_path).async_save(out)
            _, back = await self._current(url_path)
            report['verify'] = 'ok' if back == out else 'mismatch'
            if back != out:
                _LOGGER.warning('Hauflo: dashboard %s reads back other than saved', url_path)
            self.written[url_path] = _digest(back if back is not None else out)
        else:
            self.written[url_path] = _digest(raw)
        self.bases[url_path] = new
        self.merged_by = VERSION
        await self.store.async_save(self.bases)
        await self._save_meta()
        return {**report, 'saved': out != raw, **({'copy': copy_url, 'copy_reason': 'upgrade' if base is None else 'edited'} if copy_url else {}), **({'copies_removed': pruned} if pruned else {})}

    def outdated(self) -> bool:
        return self.merged_by != VERSION

    async def remerge(self, local_ws: Callable[[list[dict[str, Any]]], Any], locked: bool=False) -> dict[str, dict[str, Any]]:
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
        from . import order as block_order
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
                copy_url, _ = await self._copy(url_path, current, title, local_ws)
            await self._dashboard(url_path).async_save(out)
            _, back = await self._current(url_path)
            self.written[url_path] = _digest(back if back is not None else out)
            await self._save_meta()
            _LOGGER.info('Hauflo: dashboard %s merged again after the update — %s tile(s) to pages gone removed, tabs reordered: %s', url_path, len(dead), views is not None)
            return {'outcome': 'saved', 'removed': dead, 'reordered': views is not None, 'verify': 'ok' if back == out else 'mismatch', **({'copy': copy_url} if copy_url else {})}

    async def _copy(self, url_path: str, config: Any, title: str | None, local_ws: Callable[[list[dict[str, Any]]], Any], lang: str | None=None, before_take: bool=False) -> tuple[str, int]:
        now = dt_util.now()
        prefix = f'{url_path[:40]}-copy-'
        ru = str(lang or self.hass.config.language or '').startswith('ru')
        shown = now.strftime('%d.%m %H:%M') if ru else f"{now.day} {_MONTHS[now.month - 1]} {now.strftime('%H:%M')}"
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
                try:
                    await local_ws([{'type': 'lovelace/dashboards/delete', 'dashboard_id': ids[old]}])
                    pruned += 1
                except Exception as err:
                    _LOGGER.warning('Hauflo: old dashboard copy %s not removed: %s', old, err)
        self.copies[url_path] = mine
        await self._save_meta()
        return (copy_url, pruned)

class MergeError(Exception):

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

def is_copy(url: Any) -> bool:
    return isinstance(url, str) and bool(COPY_RE.search(url))

def _choice(v: Any) -> Any:
    if v is True:
        return True
    if isinstance(v, list):
        return [x for x in v[:2000] if isinstance(x, str) and len(x) <= 256]
    return []

async def local_ws(session: Any, url: str, token: str, cmds: list[dict[str, Any]]) -> list[Any]:
    async with asyncio.timeout(60), session.ws_connect(url, max_msg_size=0) as ws:
        await ws.receive_json()
        await ws.send_json({'type': 'auth', 'access_token': token})
        if (await ws.receive_json()).get('type') != 'auth_ok':
            raise MergeError('local_auth', "this HA refused the agent's sign-in")
        out: list[Any] = []
        for i, c in enumerate(cmds, 1):
            await ws.send_json({**c, 'id': i})
            while True:
                r = await ws.receive_json()
                if isinstance(r, dict) and r.get('id') == i and (r.get('type') == 'result'):
                    break
            if not r.get('success'):
                raise MergeError('local_failed', f"{c.get('type')}: {(r.get('error') or {}).get('message')}")
            out.append(r.get('result'))
        return out

async def handle(agent: Any, ch: int, msg: dict[str, Any], validate: Callable[[Any], str | None], fresh_backup: Callable[[], bool], locked_code: str, prune: Callable[[Any], tuple[Any, list[dict[str, Any]]]] | None=None) -> None:
    t, mid = (msg.get('type'), msg.get('id'))

    async def reply(result: Any=None, code: str | None=None, message: str='') -> None:
        frame: dict[str, Any] = {'id': mid, 'type': 'result', 'success': code is None}
        if code is None:
            frame['result'] = result
        else:
            frame['error'] = {'code': code, 'message': f'Hauflo agent: {message}'}
        await agent.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps(frame, ensure_ascii=False)})
    url, config = (msg.get('url_path'), msg.get('config'))
    merger: DashboardMerge = agent.dashboards
    if t == 'lovelace/config' and isinstance(url, str) and merger.manages(url) and (not is_copy(url)):
        try:
            await reply(await merger.read(url))
        except MergeError as err:
            await reply(code=err.code, message=str(err))
        return
    if t == 'lovelace/config' or not (isinstance(url, str) and url.startswith('hauflo-') and (not is_copy(url))) or (not isinstance(config, dict)):
        _LOGGER.warning("Hauflo: «%s» isn't allowed through the tunnel", t)
        await reply(code='not_allowed', message='not allowed')
        return
    why = validate(config)
    dropped: list[dict[str, Any]] = []
    if why is not None and prune is not None:
        config, dropped = prune(config)
        why = validate(config)
    if why is not None:
        _LOGGER.warning("Hauflo: «%s» of %s isn't allowed through the tunnel — %s", t, url, why)
        await reply(code='not_allowed', message=f'not allowed ({why})')
        return
    for d in dropped:
        _LOGGER.warning('Hauflo: %s of %s — left off the dashboard: %s', t, url, d.get('detail'))
    said = [{k: v for k, v in d.items() if k != 'detail'} for d in dropped][:100]
    try:
        if t == PLAN:
            planned = await merger.plan(url, config)
            await reply({**planned, 'dropped': said} if said and isinstance(planned, dict) else planned)
            return
        if agent.locked:
            await reply(code=locked_code, message='changes by Hauflo are switched off in Home Assistant (Hauflo → Configure)')
            return
        if not fresh_backup():
            await reply(code='backup_required', message='a backup first')
            return
        plain = t != SAVE
        title = msg.get('title') if isinstance(msg.get('title'), str) else None
        lang = msg.get('lang') if isinstance(msg.get('lang'), str) else None
        res = await merger.save(url, config, [] if plain else _choice(msg.get('take')), [] if plain else _choice(msg.get('restore')), not merger.manages(url) if plain else msg.get('replace') is True, title[:60] if title else None, lambda cmds: local_ws(agent.local_session, f'{agent.local_base}/api/websocket', agent.token, cmds), lang[:5] if lang else None, [] if plain else [x for x in (msg.get('stock') if isinstance(msg.get('stock'), list) else [])[:200] if isinstance(x, str) and len(x) <= 64], [] if plain else [x for x in (msg.get('twins') if isinstance(msg.get('twins'), list) else [])[:50] if isinstance(x, str) and len(x) <= 128])
        await reply(None if plain else {**res, 'dropped': said} if said and isinstance(res, dict) else res)
    except MergeError as err:
        await reply(code=err.code, message=str(err))
    except Exception as err:
        _LOGGER.exception('Hauflo: dashboard %s failed', t)
        await reply(code='failed', message=str(err)[:200])
