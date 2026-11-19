"""Panels, duplicate paths and twin cards of a dashboard."""
from __future__ import annotations
import copy
from typing import Any
from .const import ID, LAYOUT
from .tree import child_level_of, count_cards, hid, kids_key, kids_of, title_of

def unpanel(cfg: Any, new: dict[str, Any]) -> Any:
    if not isinstance(cfg, dict) or not isinstance(new, dict):
        return cfg
    to_sections: dict[str, str] = {}
    for v in kids_of(new, 0):
        h = hid(v)
        secs = v.get('sections') if isinstance(v, dict) else None
        if h and isinstance(secs, list):
            first = next((hid(s) for s in secs if hid(s)), None)
            to_sections[h] = first or f'{h}/s:panel'
    views = cfg.get('views')
    if not to_sections or not isinstance(views, list):
        return cfg
    out_views: list[Any] = []
    changed = False
    for v in views:
        h = hid(v)
        if h in to_sections and isinstance(v, dict) and (kids_key(v, 1) == 'cards'):
            cards = [c for c in v.get('cards', []) if isinstance(c, dict)] if isinstance(v.get('cards'), list) else []
            own = {k: copy.deepcopy(x) for k, x in v.items() if k not in ('cards', 'type')}
            out_views.append({**own, 'type': 'sections', 'sections': [{'type': 'grid', ID: to_sections[h], 'cards': copy.deepcopy(cards)}]})
            changed = True
        else:
            out_views.append(v)
    return {**cfg, 'views': out_views} if changed else cfg

def same_name(t: Any) -> str:
    return ' '.join(t.split()).casefold() if isinstance(t, str) else ''

def _leads_to(card: dict[str, Any], path: str) -> bool:
    ways = [card.get('path'), card.get('navigation_path')]
    for k in ('tap_action', 'hold_action'):
        a = card.get(k)
        if isinstance(a, dict):
            ways.append(a.get('navigation_path'))
    return any((isinstance(w, str) and (w.strip('/') == path or w.rstrip('/').endswith(f'/{path}')) for w in ways))

def stack(card: dict[str, Any]) -> list[Any] | None:
    """The cards a card holds (a stack, a grid card, Hauflo's layout) — the integrator's tile may sit in one."""
    v = card.get('cards')
    return v if isinstance(v, list) else None

def _tiles_to(cfg: dict[str, Any], path: str) -> int:
    n = 0

    def card(c: dict[str, Any]) -> None:
        nonlocal n
        n += hid(c) is None and _leads_to(c, path)
        for x in (stack(c) if hid(c) is None or c.get('type') == LAYOUT else None) or []:
            if isinstance(x, dict):
                card(x)

    def walk(node: dict[str, Any], level: int) -> None:
        for c in kids_of(node, level):
            if isinstance(c, dict):
                cl = child_level_of(level, kids_key(node, level))
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
        for x in stack(c) or []:
            if isinstance(x, dict):
                card(x)

    def walk(node: dict[str, Any], level: int) -> None:
        for c in kids_of(node, level):
            if isinstance(c, dict):
                cl = child_level_of(level, kids_key(node, level))
                card(c) if cl == 3 else walk(c, cl)
    walk(cfg, 0)
    return found

def unique_paths(cfg: Any) -> None:
    views = [v for v in (cfg.get('views') if isinstance(cfg, dict) else None) or [] if isinstance(v, dict)]
    his = {v['path'] for v in views if hid(v) is None and isinstance(v.get('path'), str)}
    taken = {v.get('path') for v in views}
    for v in views:
        p = v.get('path')
        if hid(v) is None or not isinstance(p, str) or p not in his or _opened(cfg, p):
            continue
        n = 2
        while f'{p}_{n}' in taken:
            n += 1
        v['path'] = f'{p}_{n}'
        taken.add(v['path'])

def twins(current: Any, new: Any) -> list[dict[str, Any]]:
    if not isinstance(current, dict) or not isinstance(new, dict):
        return []
    names = {same_name(title_of(v, 1)) for v in kids_of(new, 0) if isinstance(v, dict) and hid(v)} - {''}
    out: list[dict[str, Any]] = []
    for v in kids_of(current, 0):
        if not isinstance(v, dict) or hid(v) is not None:
            continue
        path, title = (v.get('path'), title_of(v, 1))
        if isinstance(path, str) and path.strip() and (same_name(title) in names) and (count_cards(v, 1) == 0):
            out.append({'id': path.strip()[:128], 'title': title, 'tiles': _tiles_to(current, path.strip())})
    return out[:50]

def drop_twins(current: Any, new: Any, ids: Any) -> tuple[Any, int]:
    """`current` less the ticked twins (only what `twins` offers now) and the integrator's tiles that open them."""
    want = {x for x in ids if isinstance(x, str)} if isinstance(ids, list) else set()
    gone = {t['id'] for t in twins(current, new) if t['id'] in want}
    if not gone:
        return (current, 0)

    def emptied(before: dict[str, Any], after: dict[str, Any], key: str) -> bool:
        b, a = (before.get(key), after.get(key))
        return hid(before) is None and isinstance(b, list) and bool(b) and isinstance(a, list) and (not a)

    def card(c: dict[str, Any]) -> dict[str, Any] | None:
        if hid(c) is None and any((_leads_to(c, p) for p in gone)):
            return None
        inner = stack(c) if hid(c) is None or c.get('type') == LAYOUT else None
        if inner is None:
            return c
        out = {**c, 'cards': [y for y in (card(x) if isinstance(x, dict) else x for x in inner) if y is not None]}
        return None if emptied(c, out, 'cards') else out

    def keep(node: dict[str, Any], level: int) -> dict[str, Any]:
        key = kids_key(node, level)
        if key is None:
            return node
        cl = child_level_of(level, key)
        kids = []
        for c in kids_of(node, level):
            if not isinstance(c, dict):
                kids.append(c)
                continue
            if level == 0 and hid(c) is None and isinstance(c.get('path'), str) and (c['path'].strip() in gone):
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
