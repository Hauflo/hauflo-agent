"""Upgrades a stored dashboard to the current card layout."""
from __future__ import annotations
from collections import Counter
import copy
import json
import re
from typing import Any
import unicodedata
from .const import ID, LAYOUT, ROOT
from .merge import Merge
from .tree import Index, find, hid, holds, kids_of, leaves, own_of

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

def _old_ids(idx: Index) -> bool:
    return any((idx.level[h] == 3 and idx.level.get(idx.parent[h]) == 2 and h.startswith(f'{idx.parent[h]}/') for h in idx.node))

def _new_ids(idx: Index) -> bool:
    cards = [h for h in idx.node if idx.level[h] == 3 and idx.level.get(idx.parent[h]) == 2]
    return bool(cards) and (not any((h.startswith(f'{idx.parent[h]}/') for h in cards)))

def upgrade(base: dict[str, Any], current: Any, new: dict[str, Any]) -> tuple[dict[str, Any], Any, dict[str, Any]] | None:
    """(the base, HA's config — copies, in the new ids and blocks; what it did) when the base has the old ids and the
    new version the new ones, else None. Nothing of HA's is dropped here: only Hauflo's ids are renamed, Hauflo's copy
    sections read as the block's section and his cards in them moved next to the block (an exact repeat of his card in
    another copy of the same section — once: `allow` says how many of which)."""
    if not isinstance(base, dict) or not isinstance(current, dict):
        return None
    B0, N = (Index(base), Index(new))
    if not _old_ids(B0) or not _new_ids(N):
        return None
    base, current = (copy.deepcopy(base), copy.deepcopy(current))
    B = Index(base)
    C = Index(current, expect=B)

    def media(h: str) -> str:
        p = B.parent.get(h, ROOT)
        return json.dumps(B.node[p].get('visibility'), sort_keys=True, default=str) if B.level.get(p) == 2 and B.node[p].get('visibility') else ''

    def phone(h: str) -> bool:
        """A card (or a section) shown on a phone: no screen condition, or the phone's."""
        node = B.node[h] if B.level.get(h) == 2 else B.node.get(B.parent.get(h, ROOT), {})
        m = json.dumps(node.get('visibility') or '', default=str)
        return 'media_query' not in m or 'max-width' in m
    by_content: dict[tuple[str | None, str], list[str]] = {}
    for h, n in leaves(N).items():
        by_content.setdefault((N.view.get(h), _sizeless(n)), []).append(h)
    seen_c: Counter[tuple[Any, ...]] = Counter()
    seen_k: Counter[tuple[Any, ...]] = Counter()
    used: set[tuple[str | None, str, str]] = set()
    target: dict[str, str] = {}
    for h, n in leaves(B).items():
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
    for h, n in leaves(B).items():
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
        first = next((x for x in (hid(c) for c in kids_of(B.node[h], 2) if isinstance(c, dict)) if x in target), None)
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
        dest = kids_of(C.node[keep], 2)
        prints = {Merge.fingerprint(x, 3) for x in dest if isinstance(x, dict) and hid(x) not in B.node}
        for o in others:
            if own_of(C.node[o], 2) != own_of(B.node[o], 2):
                continue
            stay: list[Any] = []
            anchor: str | None = None
            for x in kids_of(C.node[o], 2):
                hx = hid(x) if isinstance(x, dict) else None
                if isinstance(x, dict) and hx in B.node and (id(x) in C.first):
                    anchor = hx
                    stay.append(x)
                    continue
                if not isinstance(x, dict):
                    stay.append(x)
                    continue
                fp = Merge.fingerprint(x, 3) if not holds(x, 3) else None
                if fp is not None and fp in prints:
                    allow[fp] += 1
                    continue
                if fp is not None:
                    prints.add(fp)
                t = target.get(anchor or '')
                at = next((i + 1 for i, y in enumerate(dest) if isinstance(y, dict) and target.get(hid(y) or '') == t), len(dest)) if t else len(dest)
                dest.insert(at, x)
                moved_local += 1
            C.node[o]['cards'] = stay
    for old, t in survivor.items():
        for node in (B.node.get(old), C.node.get(old) if here(old) else None):
            if node is not None:
                node[ID] = t
    for ns in sec_groups:
        kids = kids_of(N.node[ns], 2)
        if len(kids) != 1 or not isinstance(kids[0], dict) or kids[0].get('type') != LAYOUT or (hid(kids[0]) in B.node):
            continue
        lay = own_of(kids[0], 3)
        for cfg_node in (find(base, ns), find(current, ns)):
            if cfg_node is not None and isinstance(cfg_node.get('cards'), list):
                cfg_node['cards'] = [{**copy.deepcopy(lay), 'cards': cfg_node['cards']}]
    return (base, current, {'copies': copies, 'allow': allow, 'blocks': len(groups), 'moved': moved_local})
