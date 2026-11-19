"""The base of every diagnostics detector."""
from __future__ import annotations
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from homeassistant.core import State
from .common import observe_h, round2
if TYPE_CHECKING:
    from .engine import Insights

class CanonAttr:
    """A class attribute taken from the canon (tuning.json) on its first read, then kept on the class: the detectors' kinds and domains without a file read at import (HA core review: no I/O at import)."""

    def __init__(self, fn: Callable[[], Any]) -> None:
        self.fn = fn

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def __get__(self, obj: object, owner: type) -> Any:
        value = self.fn()
        setattr(owner, self.name, value)
        return value

class Detector:
    """One kind of «off its own norm»."""
    name = ''
    domains: tuple[str, ...] | None = ()
    KINDS: tuple[str, ...] = ()
    UNIT = ''
    DEV: tuple[str, str] = ('', '')
    diagnostic = False

    def __init__(self, ctx: Insights) -> None:
        self.ctx = ctx

    def load(self, data: dict[str, Any]) -> None:
        raise NotImplementedError

    def dump(self) -> dict[str, Any]:
        raise NotImplementedError

    def on_state(self, new: State, now: float) -> None:
        raise NotImplementedError

    def on_event(self, old: State | None, new: State, now: float) -> None:
        """A state change with the state before it (a detector that counts transitions); by default — the new state."""
        self.on_state(new, now)

    def on_learned(self, eid: str, direction: str, rate: float, now: float, gap: float | None=None) -> None:
        """ClimateEffect learned a clean episode's rate (°C/h, from `gap` °C away) — a detector following the trend listens."""

    def tick(self, now: float) -> None:
        raise NotImplementedError

    def warm(self, now: float) -> None:
        """Switched on again with HA running (accept3 №5): what it derives from HA's states and not from its saved norms (the pairs) found at once — never a minute of a «shorter» list (117 → 105 rows, «Норма известна» 13 → 1) until the first tick."""

    def items(self, now: float) -> list[dict[str, Any]]:
        raise NotImplementedError

    def keys(self) -> list[tuple[str, str]]:
        """(kind, entity) it knows now — learning, ready or off its norm."""
        return []

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        return {'ready': {'norm': None, 'unit': self.UNIT, 'n': 0}}

    def excluded(self, kind: str, eid: str) -> str | None:
        """Why it is not judged at all (air_handler | hidden | no_outdoor | coarse_battery | season), None — judged."""
        return None if self.ctx.visible(eid, self.diagnostic) else 'hidden'

    def episodes(self, eid: str) -> list[dict[str, Any]]:
        return []

    def extra(self, kind: str, eid: str) -> dict[str, Any]:
        """More of the row for the integrator's view (analytics.py: its unit, the standard, a tip, «нормы нет — спросим»)."""
        return {}

    def inputs(self, kind: str, eid: str) -> list[dict[str, Any]]:
        return [{'role': 'self', 'entityId': eid}]

    def reset(self, eid: str, kind: str) -> None:
        """«Сбросить обучение»: its norms of this entity forgotten (learned anew)."""

    def learning(self, eid: str, have: int, need: int, now: float) -> dict[str, Any]:
        return {'learning': {'have': have, 'need': need, 'hours': round2(self.ctx.hours_seen(self.name, eid, now)), 'needH': observe_h()}}

    def state(self, eid: str, now: float, kind: str | None=None, open_items: dict[str, dict[str, Any]] | None=None) -> dict[str, Any]:
        """One of learning / ready / deviation{since, was, now} / normal{by, at} / excluded{reason}."""
        kind = kind or next((k for k, e in self.keys() if e == eid), self.KINDS[0] if self.KINDS else '')
        key = f'{kind}|{eid}'
        if (why := self.excluded(kind, eid)) is not None:
            return {'excluded': {'reason': why}}
        f = self.ctx.finding(key)
        if f and f.get('normal'):
            return {'normal': {'by': f.get('by'), 'at': f.get('at')}}
        opened = open_items if open_items is not None else {i['key']: i for i in self.items(now)}
        if (item := opened.get(key)) is not None:
            return {'deviation': {'since': item['since'], 'was': item.get(self.DEV[0]), 'now': item.get(self.DEV[1])}}
        return self.progress(kind, eid, now)
