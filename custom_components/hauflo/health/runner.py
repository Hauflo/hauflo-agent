"""The registry of health checks and their runs: each check on its own period (`every_s`), one run at a time, after
HA has started; the results kept in HA's storage (`hauflo.health`), so a restart says the same at once. The status
block `health` is {check: {"level", "since", …facts}} — `since` is when the level last changed. A change of level
pushes the status (the cloud turns a non-ok level into a fault line for the company's people). No Repairs here: HA
raises its own for a full disk or a failed backup."""
from __future__ import annotations
import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
import logging
from types import ModuleType
from typing import Any
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util
from ..const import DOMAIN
from ..storage import HaufloStore
from . import backup, cert, disk, updates
from .common import KEEP, LEVELS, STORE_KEY, cfg, iso, num
_LOGGER = logging.getLogger(__name__)
CHECKS: dict[str, ModuleType] = {m.NAME: m for m in (backup, disk, updates, cert)}
Check = Callable[[HomeAssistant, dict[str, Any] | None, float], Awaitable[Any]]

class Health:

    def __init__(self, hass: HomeAssistant, checks: dict[str, Check] | None=None) -> None:
        self.hass = hass
        self.store = HaufloStore(hass, STORE_KEY)
        self.checks: dict[str, Check] = checks if checks is not None else {n: m.check for n, m in CHECKS.items()}
        self.recs: dict[str, dict[str, Any]] = {}
        self.ran: dict[str, float] = {}
        self.on_change: Callable[[], None] | None = None
        self._unsubs: list[Callable[[], None]] = []
        self._task: asyncio.Task | None = None

    @staticmethod
    def _now() -> float:
        return dt_util.utcnow().timestamp()

    async def async_start(self) -> None:
        data = await self.store.async_load()
        if isinstance(data, dict):
            self.recs = {k: r for k, v in (data.get('recs') or {}).items() if k in self.checks and (r := _rec_in(v))}
            self.ran = {k: t for k, v in (data.get('ran') or {}).items() if k in self.checks and (t := num(v))}

        @callback
        def started(_hass: HomeAssistant) -> None:
            self._unsubs.append(async_call_later(self.hass, cfg()['start_delay_s'], self._tick))
            self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=cfg()['tick_s'])))
        self._unsubs.append(async_at_started(self.hass, started))

    @callback
    def async_stop(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        if self._task is not None and (not self._task.done()):
            self._task.cancel()

    async def async_flush(self) -> None:
        await self.store.async_save(self._data())

    def _data(self) -> dict[str, Any]:
        return {'recs': self.recs, 'ran': self.ran}

    @callback
    def _tick(self, _now: Any=None) -> None:
        if self._task is not None and (not self._task.done()):
            return
        self._task = self.hass.async_create_background_task(self.run(), f'{DOMAIN} health checks')

    async def run(self, force: bool=False) -> None:
        """The checks due now (all with `force`); a change of any level pushes the status once."""
        changed = False
        for name, fn in self.checks.items():
            now = self._now()
            if not force and now - self.ran.get(name, 0) < cfg()['every_s'].get(name, cfg()['tick_s']):
                continue
            self.ran[name] = now
            prev = self.recs.get(name)
            try:
                res = await fn(self.hass, prev, now)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.warning('Hauflo: health check %s failed — its last result stays', name, exc_info=True)
                res = KEEP
            changed |= self._apply(name, prev, res, now)
        if changed and self.on_change is not None:
            self.on_change()
        self.store.async_delay_save(self._data, cfg()['save_delay_s'])

    def _apply(self, name: str, prev: dict[str, Any] | None, res: Any, now: float) -> bool:
        if res is KEEP:
            if prev is not None and now - prev['at'] > cfg()['stale_h'] * 3600:
                del self.recs[name]
                return True
            return False
        if res is None or not isinstance(res, dict) or res.get('level') not in LEVELS:
            return self.recs.pop(name, None) is not None
        same = prev is not None and prev['level'] == res['level']
        self.recs[name] = {**res, 'since': prev['since'] if same else now, 'at': now}
        return not same

    def items(self) -> dict[str, dict[str, Any]]:
        """The status block `health`: the checks this HA has, each with its level, since when, and its facts."""
        return {name: {**{k: v for k, v in r.items() if not k.startswith('_') and k not in ('since', 'at')}, 'since': iso(r['since'])} for name, r in self.recs.items()}

def _rec_in(v: Any) -> dict[str, Any] | None:
    if not isinstance(v, dict) or v.get('level') not in LEVELS or num(v.get('since')) is None or (num(v.get('at')) is None):
        return None
    return v
