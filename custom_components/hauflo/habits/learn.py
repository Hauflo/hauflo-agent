"""Finds repeated actions: clusters by time and their share of the days."""
from __future__ import annotations
import datetime as dt
import statistics
from typing import Any
from ..automations.common import MAX_OFFSET
from .common import CLASSES, median_fit, now, t_cfg, wrap

class HabitsLearnMixin:
    """Finds the repeated actions."""

    def mine(self) -> list[dict[str, Any]]:
        """Every habit found now, strongest first (before rules / dismissed / validate are applied)."""
        today = now().date().toordinal()
        if self.data['enabled'] and self._since is not None:
            self.observed(today)
        old = today - t_cfg()['keep_days']
        for where in ('events', 'auto'):
            for key in list(self.data[where]):
                rows = [r for r in self.data[where][key] if r[0] > old]
                if rows:
                    self.data[where][key] = rows
                else:
                    self.data[where].pop(key)
        self.data['days'] = [d for d in self.data['days'] if d > old]
        found: list[dict[str, Any]] = []
        for key, rows in self.data['events'].items():
            eid, act = key.rsplit('|', 1)
            kept: list[dict[str, Any]] = []
            for f in self._fits(rows, 'all', today):
                if all((self._share_in(f, cls, today) >= t_cfg()['min_share'] for cls in ('workdays', 'weekend'))):
                    kept.append(f)
            for f in sorted((f for cls in ('workdays', 'weekend') for f in self._fits(rows, cls, today)), key=lambda f: -f['share']):
                if not any((set(f['days']) & set(k['days']) and self._same(f, k) for k in kept)):
                    kept.append(f)
            for f in kept:
                f.pop('_rows')
                if not self._automated_then(eid, f, today):
                    found.append({**f, 'entity_id': eid, 'act': act})
        self._mined = found
        return found

    @staticmethod
    def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
        if set(map(id, a['_rows'])) & set(map(id, b['_rows'])):
            return True
        if a['kind'] != b['kind']:
            return False
        if a['kind'] == 'time':
            return abs(wrap(a['minute'] - b['minute'])) <= t_cfg()['window_min']
        return abs(a['offset'] - b['offset']) <= t_cfg()['window_min']

    def _window(self, cls: str, today: int, any_day: bool=False) -> list[int]:
        span = t_cfg()['weekend_window_days'] if cls == 'weekend' else t_cfg()['window_days']
        return [d for d in self.data['days'] if today - span < d <= today and (any_day or dt.date.fromordinal(d).weekday() in CLASSES[cls])]

    def _fits(self, rows: list[list[Any]], cls: str, today: int) -> list[dict[str, Any]]:
        days = self._window(cls, today)
        need = t_cfg()['weekend_min_days'] if cls == 'weekend' else t_cfg()['min_days']
        if len(days) < need:
            return []
        pool = [r for r in rows if r[0] in set(days)]
        axes = {kind: self._clusters([r for r in pool if r[col] is not None], col, kind == 'time', need, len(days)) for kind, col in (('time', 1), ('sunset', 2), ('sunrise', 3))}
        used: set[tuple[str, int]] = set()
        out = []
        for c in axes['time']:
            ids = {id(r) for r in c['rows']}
            best = None
            for kind in ('sunset', 'sunrise'):
                for i, sc in enumerate(axes[kind]):
                    if len(ids & {id(r) for r in sc['rows']}) * 2 >= len(ids) and (best is None or sc['spread'] < best[1]['spread']):
                        best = ((kind, i), sc, kind)
            if best is not None:
                used.add(best[0])
                if best[1]['drift'] + t_cfg()['sun_better_min'] <= c['drift'] and abs(best[1]['center']) <= MAX_OFFSET:
                    out.append(self._fit_of(cls, best[2], best[1], len(days)))
                    continue
            out.append(self._fit_of(cls, 'time', c, len(days)))
        for kind in ('sunset', 'sunrise'):
            for i, sc in enumerate(axes[kind]):
                if (kind, i) not in used and abs(sc['center']) <= MAX_OFFSET and (not any((set(map(id, sc['rows'])) & set(map(id, f['_rows'])) for f in out))):
                    out.append(self._fit_of(cls, kind, sc, len(days)))
        return out

    def _share_in(self, fit: dict[str, Any], cls: str, today: int) -> float:
        days = {d for d in self._window('all', today) if dt.date.fromordinal(d).weekday() in CLASSES[cls]}
        return len(days & {r[0] for r in fit['_rows']}) / len(days) if days else 0.0

    @staticmethod
    def _clusters(pool: list[list[Any]], col: int, circular: bool, need: int, of: int) -> list[dict[str, Any]]:
        gap = (lambda a, b: abs(wrap(a - b))) if circular else lambda a, b: abs(a - b)
        out = []
        while pool:
            best, best_days = (None, 0)
            for r in pool:
                n = len({x[0] for x in pool if gap(x[col], r[col]) <= t_cfg()['window_min']})
                if n > best_days:
                    best, best_days = (r, n)
            if best is None or best_days < need or best_days / of < t_cfg()['min_share']:
                break
            near = [x for x in pool if gap(x[col], best[col]) <= t_cfg()['window_min']]
            per_day: dict[int, list[Any]] = {}
            for x in sorted(near, key=lambda x: gap(x[col], best[col])):
                per_day.setdefault(x[0], x)
            pool = [x for x in pool if x not in near]
            picked = list(per_day.values())
            center, spread, hits = median_fit([x[col] for x in picked], circular)
            rel = [wrap(x[col] - center) if circular else x[col] - center for x in picked]
            if hits >= need and hits / of >= t_cfg()['min_share']:
                out.append({'center': center, 'spread': spread, 'drift': max(rel) - min(rel), 'hits': hits, 'rows': picked})
        return out

    @staticmethod
    def _fit_of(cls: str, kind: str, c: dict[str, Any], of: int) -> dict[str, Any]:
        rows = c['rows']
        levels = [x[4] for x in rows if isinstance(x[4], (int, float))]
        clock = median_fit([x[1] for x in rows], True)[0]
        fit = {'class': cls, 'kind': kind, 'minute': clock, 'spread': c['spread'], 'hits': c['hits'], 'of': of, 'share': c['hits'] / of, 'days': [*CLASSES[cls]], 'level': statistics.median(levels) if levels else None, '_rows': rows}
        if kind != 'time':
            fit['offset'] = c['center']
        return fit

    def _automated_then(self, eid: str, fit: dict[str, Any], today: int) -> bool:
        days = set(self._window(fit['class'], today, any_day=True))
        hit = {r[0] for key, rows in self.data['auto'].items() if key.split('|')[0] == eid for r in rows if r[0] in days and abs(wrap(r[1] - fit['minute'])) <= t_cfg()['window_min']}
        return len(hit) >= t_cfg()['auto_min_days']
