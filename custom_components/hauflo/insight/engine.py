"""The diagnostics engine: one store, one tick and the findings of every detector."""
from __future__ import annotations
from collections.abc import Callable
from datetime import timedelta
import logging
import time
from typing import TYPE_CHECKING, Any
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from ..analytics import ANALYTICS
from ..analytics.common import is_note, section_of
from ..derived import SECONDS_PER_HOUR, num
from ..guard import contained
from ..noeffect.engine import NOEFFECT
from ..predict import TRENDS
from ..storage import HaufloStore
from .climate import ClimateEffect
from .common import DAY_S, META, STORE_KEY, choices_max, gone_after, iso, list_max, local_max, max_per_day, observe_h, save_delay, tick
from .house import InsightsHouseMixin
from .stuck import StuckSensor
if TYPE_CHECKING:
    from .detector import Detector
_LOGGER = logging.getLogger(__name__)
DETECTORS: tuple[type[Detector], ...] = (ClimateEffect, StuckSensor, *TRENDS, *NOEFFECT, *ANALYTICS)

class Insights(InsightsHouseMixin):
    """The engine: feeds the detectors, keeps their norms (debounced save), lists their findings, says when the list changed (the agent sends its status then)."""

    def __init__(self, hass: HomeAssistant, owns: Callable[[str], bool], season_now: Callable[[], str | None]) -> None:
        self.hass = hass
        self.owns = owns
        self.watched: Callable[[], set[str]] = set
        self.season_now = season_now
        self.store: HaufloStore = HaufloStore(hass, STORE_KEY)
        self.clock: Callable[[], float] = time.time
        self.detectors: list[Detector] = [cls(self) for cls in DETECTORS]
        self.on_change: Callable[[], None] | None = None
        self.on: Callable[[], bool] = lambda: True
        self.off = False
        self.running = False
        self.started = 0.0
        self.not_windows: set[str] = set()
        self.finding_of: Callable[[str], dict[str, Any] | None] | None = None
        self.first: dict[str, float] = {}
        self.opens: dict[str, list[float]] = {}
        self.resets: dict[str, str] = {}
        self.local: dict[str, dict[str, Any]] = {}
        self.owner: dict[str, Detector] = {k: det for det in self.detectors for k in det.KINDS}
        self._listed: frozenset[str] = frozenset()
        self._unsubs: list[Callable[[], None]] = []
        self.addresses: Callable[[], dict[str, list[str]]] | None = None

    def _addresses(self) -> dict[str, list[str]]:
        with contained(_LOGGER, 'Hauflo insights: KNX addresses not read'):
            return (self.addresses() if self.addresses else None) or {}
        return {}

    async def async_start(self) -> None:
        data = await self.store.async_load() or {}
        self._load(data if isinstance(data, dict) else {})
        self.off = not self._wanted()
        if not self.off:
            self._subscribe()

    def _wanted(self) -> bool:
        with contained(_LOGGER, 'Hauflo insights: the observation switch not readable — taken as on'):
            return bool(self.on())
        return True

    @callback
    def switch(self) -> None:
        """The integrator switched «Фоновые наблюдения» (signals.py `observe`): off — the listener and the tick stop, the norms are saved as they are, the list empties (no finding stays open behind a stopped engine); on — the detectors start from the saved norms like after a restart (what was open then opens anew, the observation gap is no evidence)."""
        want = self._wanted()
        if want != self.off:
            return
        self.off = not want
        if want:
            was = self.data()
            self.detectors = [cls(self) for cls in DETECTORS]
            self.owner = {k: det for det in self.detectors for k in det.KINDS}
            self.not_windows = set()
            self._load(was)
            now = self.clock()
            for det in self.detectors:
                self._run(det.warm, now)
            self._subscribe()
        else:
            self.async_stop()
            self.store.async_delay_save(self.data, 0)
            self._listed = frozenset()
        if self.on_change:
            self.on_change()

    def _subscribe(self) -> None:
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_event))
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=tick())))
        self.running = True

    def _load(self, data: dict[str, Any]) -> None:
        for det in self.detectors:
            with contained(_LOGGER, 'Hauflo insights: %s norms unreadable — learning anew', det.name):
                saved = data.get(det.name) if isinstance(data, dict) else None
                det.load(saved if isinstance(saved, dict) else {})
        self.started = self.clock()
        meta = data.get(META) if isinstance(data, dict) else None
        self._load_meta(meta if isinstance(meta, dict) else {})
        for det in self.detectors:
            for _kind, eid in self._keys(det):
                self.first.setdefault(f'{det.name}|{eid}', self.started - observe_h() * SECONDS_PER_HOUR)
        self._listed = frozenset((i['key'] for i in self._raw(self.started)))

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self.running = False

    def _load_meta(self, meta: dict[str, Any]) -> None:
        self.first = {str(k): float(v) for k, v in (meta.get('first') or {}).items() if num(v) is not None}
        self.opens = {str(k): [float(t) for t in v if num(t) is not None][-max_per_day():] for k, v in (meta.get('opens') or {}).items() if isinstance(v, list)}
        self.resets = {str(k): str(v) for k, v in (meta.get('resets') or {}).items() if isinstance(v, str)}
        self.local = {str(k): v for k, v in (meta.get('local') or {}).items() if isinstance(v, dict)}
        self._trim_local()

    def _trim_local(self) -> None:
        if len(self.local) > local_max():
            keep = sorted(self.local.items(), key=lambda kv: str(kv[1].get('at') or ''), reverse=True)[:local_max()]
            self.local = dict(keep)

    def data(self) -> dict[str, Any]:
        return {**{det.name: det.dump() for det in self.detectors}, META: {'first': self.first, 'opens': self.opens, 'resets': self.resets, 'local': self.local}}

    @callback
    def dirty(self) -> None:
        self.store.async_delay_save(self.data, save_delay())

    @callback
    def _on_event(self, event: Event) -> None:
        new: State | None = event.data.get('new_state')
        if new is None:
            return
        old: State | None = event.data.get('old_state')
        handled = False
        for det in self.detectors:
            if det.domains is None or new.domain in det.domains:
                handled = handled or det.domains is not None
                self._run(det.on_event, old, new, self.clock())
        if handled:
            self._after()

    @callback
    def learned(self, eid: str, direction: str, rate: float, now: float, gap: float | None=None) -> None:
        for det in self.detectors:
            self._run(det.on_learned, eid, direction, rate, now, gap)

    @callback
    def _tick(self, _now: Any=None) -> None:
        now = self.clock()
        for det in self.detectors:
            self._run(det.tick, now)
        self._seen(now)
        self._after()

    @staticmethod
    def _keys(det: Detector) -> list[tuple[str, str]]:
        with contained(_LOGGER, 'Hauflo insights: %s keys failed', det.name, level=logging.ERROR):
            return det.keys()
        return []

    def _seen(self, now: float) -> None:
        known = set()
        for det in self.detectors:
            for _kind, eid in self._keys(det):
                k = f'{det.name}|{eid}'
                known.add(k)
                if k not in self.first:
                    self.first[k] = now
                    self.dirty()
        if now - self.started > gone_after() and set(self.first) - known:
            self.first = {k: v for k, v in self.first.items() if k in known}
            self.dirty()

    def hours_seen(self, name: str, eid: str, now: float) -> float:
        first = self.first.get(f'{name}|{eid}')
        return max(0.0, (now - first) / SECONDS_PER_HOUR) if first is not None else 0.0

    def mark(self, name: str, eid: str, now: float) -> None:
        """«First seen» right away — for what a detector learns between the ticks (a learned rate)."""
        if f'{name}|{eid}' not in self.first:
            self.first[f'{name}|{eid}'] = now
            self.dirty()

    def observed(self, name: str, eid: str) -> bool:
        return self.hours_seen(name, eid, self.clock()) >= observe_h()

    def finding(self, key: str) -> dict[str, Any] | None:
        """The choice about this finding: the site's (Hauflo), or one made in HA since (the newer wins)."""
        cloud = self.finding_of(key) if self.finding_of else None
        loc = self.local.get(key)
        if loc is not None and (cloud is None or str(loc.get('at') or '') >= str(cloud.get('at') or '')):
            return loc
        return cloud

    def is_normal(self, key: str) -> bool:
        """The site said «это норма» for this finding: hidden, and a detector with a norm keeps learning through it."""
        return bool((self.finding(key) or {}).get('normal'))

    def factor(self, key: str, default: float) -> float:
        """The detector's «×N» bar — the integrator's own for this finding if set («Порог…»)."""
        f = self.finding(key)
        return float(f['factor']) if f and num(f.get('factor')) is not None else default

    @staticmethod
    def _run(fn: Callable[..., None], *args: Any) -> None:
        with contained(_LOGGER, 'Hauflo insights: %s failed', getattr(fn, '__qualname__', fn), level=logging.ERROR):
            fn(*args)

    @callback
    def _after(self) -> None:
        if self.off:
            return
        now = self.clock()
        keys = frozenset((i['key'] for i in self._shown(now)))
        for k in keys - self._listed:
            self.opens[k] = [*(t for t in self.opens.get(k, []) if now - t < DAY_S), now][-max_per_day():]
            self.dirty()
        for k in [k for k, ts in self.opens.items() if k not in keys and all((now - t >= DAY_S for t in ts))]:
            del self.opens[k]
            self.dirty()
        if keys != self._listed:
            self._listed = keys
            if self.on_change:
                self.on_change()

    def _raw(self, now: float) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for det in self.detectors:
            with contained(_LOGGER, 'Hauflo insights: %s items failed', det.name, level=logging.ERROR):
                out.extend(det.items(now))
        return [i for i in out if not (self.finding(i['key']) or {}).get('normal')]

    def _shown(self, now: float) -> list[dict[str, Any]]:
        out = []
        for i in self._raw(now):
            k = i['key']
            if k in self._listed or sum((1 for t in self.opens.get(k, []) if now - t < DAY_S)) < max_per_day():
                out.append(i)
        return sorted(out, key=lambda i: (is_note(i['kind']), i['since'], i['key']))[:list_max()]

    def items(self) -> list[dict[str, Any]]:
        return [] if self.off else self._shown(self.clock())

    def summary(self) -> dict[str, Any]:
        eids = {eid for det in self.detectors for _kind, eid in self._keys(det)}
        since = min(self.first.values(), default=None)
        return {'on': not self.off, 'devices': len(eids), 'since': dt_util.utc_from_timestamp(since).isoformat() if since is not None else None}

    def states(self, entity: str | None=None, offset: int=0, limit: int | None=None) -> dict[str, Any]:
        """Every (kind, entity) a detector knows with its state, `limit` a page (list_max by default); with `entity` — its episodes too."""
        limit = list_max() if limit is None else limit
        now = self.clock()
        rows: list[dict[str, Any]] = []
        for det in self.detectors:
            with contained(_LOGGER, 'Hauflo insights: %s states failed', det.name, level=logging.ERROR):
                opened = {i['key']: i for i in det.items(now)}
                for kind, eid in det.keys():
                    if entity is None or eid == entity:
                        rows.append({'key': f'{kind}|{eid}', 'kind': kind, 'detector': det.name, 'entityId': eid, 'section': section_of(kind), **det.extra(kind, eid), **det.state(eid, now, kind, opened)})
        rows.sort(key=lambda r: (r['entityId'], r['kind']))
        page = rows[offset:offset + limit]
        addresses = self._addresses()
        for r in page:
            r.update(self.who(r['entityId']))
            if (det := self.owner.get(r['kind'])) is not None:
                r['uses'] = self.uses(det, r['kind'], r['entityId'], addresses)
                r['how'] = det.name
        out: dict[str, Any] = {'total': len(rows), 'offset': offset, 'items': page}
        if entity is not None:
            out['episodes'] = [e for det in self.detectors for e in det.episodes(entity)]
        return out

    def reset(self, kind: str, eid: str, at: str) -> None:
        """«Сбросить обучение»: the detector's norms of this entity forgotten, observed anew from now."""
        det = self.owner.get(kind)
        if det is None:
            return
        self._run(det.reset, eid, kind)
        key = f'{kind}|{eid}'
        self.first[f'{det.name}|{eid}'] = self.clock()
        self.resets[key] = at
        self.opens.pop(key, None)
        self.dirty()

    @callback
    def findings_changed(self) -> None:
        """The site's findings changed (signals.py): a new resetAt resets once; «это норма» leaves the list at once."""
        for det in self.detectors:
            for kind, eid in self._keys(det):
                key = f'{kind}|{eid}'
                at = (self.finding(key) or {}).get('resetAt')
                if at and at != self.resets.get(key):
                    self.reset(kind, eid, at)
        self._after()

    @callback
    def choose(self, kind: str, eid: str, action: str, by: str, factor: float | None=None) -> None:
        """A choice made in HA (an admin): normal — «это норма»; follow — «снова следить» (with its own ×N); reset."""
        at = iso(self.clock())
        key = f'{kind}|{eid}'
        if action == 'reset':
            self.reset(kind, eid, at)
        else:
            self.local.pop(key, None)
            self.local[key] = {'by': by, 'at': at, **({'normal': True} if action == 'normal' else {}), **({'factor': factor} if factor is not None else {})}
            self._trim_local()
            self.dirty()
        self._after()

    def choices(self) -> dict[str, dict[str, Any]]:
        """Choices made in HA (an admin's «Это норма» / «Снова следить») the site's findings don't hold yet — the status carries them and Hauflo folds them in with the same `at`; the push back then equals them and they stop going."""
        out: dict[str, dict[str, Any]] = {}
        for key, loc in sorted(self.local.items(), key=lambda kv: str(kv[1].get('at') or ''), reverse=True):
            cloud = (self.finding_of(key) if self.finding_of else None) or {}
            if str(loc.get('at') or '') > str(cloud.get('at') or '') and (cloud or 'normal' in loc or 'factor' in loc):
                out[key] = {k: loc[k] for k in ('normal', 'factor', 'by', 'at') if k in loc}
            if len(out) >= choices_max():
                break
        return out
