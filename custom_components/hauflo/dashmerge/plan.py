"""Where a dashboard was edited and the plan of its merge."""
from __future__ import annotations
from collections import Counter
import copy
import hashlib
import json
from typing import Any
from .const import KEY
from .merge import Merge
from .tree import Index, child_level_of, ensure_ids, hid, holds, kids_key, kids_of, rekey
from .twins import unique_paths, unpanel
from .upgrade import upgrade

def _local_prints(cfg: Any, base: dict[str, Any]) -> Counter[str]:
    B = Index(base)
    C = Index(cfg, expect=B)
    out: Counter[str] = Counter()

    def walk(node: dict[str, Any], level: int) -> None:
        for c in kids_of(node, level):
            if isinstance(c, dict):
                cl = child_level_of(level, kids_key(node, level))
                h = hid(c)
                if h is None or h not in B.node or id(c) not in C.first:
                    out[Merge.fingerprint(c, cl)] += 1
                if holds(c, cl):
                    walk(c, cl)
    if isinstance(cfg, dict):
        walk(cfg, 0)
    return out

def edited_where(seen: Any, base: Any, limit: int=5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def place(path: list[Any], key: str | None, op: str) -> None:
        item: dict[str, Any] = {'path': '/'.join((str(p) for p in path)), 'op': op}
        if key is not None:
            item['key'] = key if KEY.match(key) else '?'
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
            for k in [*b.keys(), *(k for k in a if k not in b)]:
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
            for i, (x, y) in enumerate(zip(a, b, strict=False)):
                walk(x, y, [*path, i])
        else:
            place(path, None, 'changed')
    walk(seen, base, [])
    return out

def digest(config: Any) -> str:
    """A dashboard's config as one short string: the same config, the same digest, whatever order its keys come in."""
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

def plan_and_merge(new: dict[str, Any], current: Any, base: dict[str, Any] | None, take: Any=(), restore: Any=(), url_path: str | None=None) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the config to save, what the plan says)."""
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
    lost = sum((_local_prints(seen, base) - m.prints_of(m.result, False) - (info['allow'] if info else Counter()) - m.dead_prints - m.blank_prints).values())
    report = {**m.summary(), 'base': True, 'edited': seen != base, 'changes': m.result != current, 'lost': max(m.lost(), lost), 'untouched': len(m.untouched())}
    if seen != base:
        report['edited_where'] = edited_where(seen, base)
    if info:
        report['layout'] = {'blocks': info['blocks'], 'copies': len(info['copies']), 'moved': info['moved']}
    return (m.result, report)
