"""The habits engine: its store, schedule and switches."""
from __future__ import annotations
from collections import deque
from collections.abc import Callable
import datetime as dt
from typing import Any
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from ..automations.common import Invalid
from ..storage import HaufloStore
from .common import DEFAULT, KEY, STORE_KEY, t_cfg
from .learn import HabitsLearnMixin
from .record import HabitsRecordMixin
from .suggest import HabitsSuggestMixin

class Habits(HabitsRecordMixin, HabitsLearnMixin, HabitsSuggestMixin):
    """One per loaded entry. `pickable`: automations.pickable; `engines`: Hauflo's own engines (rules, presence)
    whose commands aren't a person's."""

    def __init__(self, hass: HomeAssistant, pickable: Callable[[str], bool], engines: Callable[[], list[Any]], area: Callable[[str], str | None]) -> None:
        self.hass = hass
        self.pickable = pickable
        self.engines = engines
        self.area = area
        self.store = HaufloStore(hass, STORE_KEY)
        self.data: dict[str, Any] = {k: v.copy() if isinstance(v, (list, dict)) else v for k, v in DEFAULT.items()}
        self._since: float | None = None
        self._auto_ctx: deque[str] = deque(maxlen=int(t_cfg()['auto_ctx']))
        self._scene_ctx: deque[str] = deque(maxlen=int(t_cfg()['auto_ctx']))
        self._last: dict[str, tuple[float, str]] = {}
        self._travel: dict[str, tuple[float, Any]] = {}
        self._rest: dict[str, str] = {}
        self._sun: dict[tuple[int, str], dt.datetime | None] = {}
        self._mined: list[dict[str, Any]] | None = None
        self._unsubs: list[Callable[[], None]] = []

    @property
    def enabled(self) -> bool:
        return bool(self.data['enabled'])

    async def async_start(self) -> None:
        stored = await self.store.async_load()
        if isinstance(stored, dict):
            for k, v in DEFAULT.items():
                if isinstance(stored.get(k), type(v)):
                    self.data[k] = stored[k]
        bus = self.hass.bus
        self._unsubs += [bus.async_listen('state_changed', self._changed), bus.async_listen('automation_triggered', self._automated), bus.async_listen('script_started', self._automated), async_track_time_interval(self.hass, self._tick, dt.timedelta(seconds=t_cfg()['mine_every_s']))]
        if self.hass.state is CoreState.running:
            self._since = dt_util.utcnow().timestamp() + t_cfg()['start_delay_s']
        else:

            @callback
            def _up(_event: Event) -> None:
                if once in self._unsubs:
                    self._unsubs.remove(once)
                self._since = dt_util.utcnow().timestamp() + t_cfg()['start_delay_s']
            once = bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _up)
            self._unsubs.append(once)
        self.mine()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self.store.async_save(self.data)

    @callback
    def _tick(self, _now: dt.datetime) -> None:
        self.mine()

    def _save_soon(self) -> None:
        self.store.async_delay_save(lambda: self.data, t_cfg()['save_delay_s'])

    async def set_enabled(self, on: bool) -> None:
        """«Предлагать по привычкам»: off forgets everything recorded and records nothing."""
        self.data['enabled'] = on
        if not on:
            self.data.update(days=[], events={}, auto={})
        self._mined = None
        await self.store.async_save(self.data)

    async def dismiss(self, key: Any) -> None:
        """«Не предлагать»: this suggestion never again (the same habit keeps its key across re-mines)."""
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise Invalid('bad_request', what='key')
        if key not in self.data['dismissed']:
            self.data['dismissed'] = [*self.data['dismissed'], key][-int(t_cfg()['max_keys']):]
        await self.store.async_save(self.data)
