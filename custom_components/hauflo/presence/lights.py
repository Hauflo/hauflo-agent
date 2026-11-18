"""Turns the lights out when everyone has left."""
from __future__ import annotations
import datetime as dt
import time
from typing import Any
from homeassistant.core import CALLBACK_TYPE, State, callback
from homeassistant.helpers.event import async_track_point_in_time, async_track_state_change_event
from homeassistant.util import dt as dt_util
from ..notify.common import label
from .common import brief, watch_off

class PresenceLightsMixin:
    """Lights out after everyone leaves."""

    def _stop_outs(self) -> None:
        for done in list(self._outs.values()):
            done()
        self._outs.clear()

    async def _switch_off(self, why: str) -> None:
        self._cancel()
        was = self.data['enabled']
        self.data['enabled'] = False
        self.status = 'off'
        self.tonight = None
        await self._lights_out()
        if was:
            self._log({'sys': why})
        await self.store.async_save(self.data)

    async def _lights_out(self, final: bool=False) -> None:
        for eid in list(self.data['lit']):
            st = self.hass.states.get(eid)
            if st is not None and st.state == 'on':
                person = self._person_since(st)
                self._unlit(eid)
                if self._pickable(eid) and (not person):
                    await self._off_now(eid, st)
                continue
            if st is not None and st.state == 'off' and (not self._restarting()):
                self._unlit(eid)
                continue
            if not final:
                self._watch_out(eid)
                continue
            self._unlit(eid)
            if st is not None and self._pickable(eid):
                await self._off_now(eid, st)

    async def _lights_out_saved(self) -> None:
        await self._lights_out()
        await self.store.async_save(self.data)

    async def _off_now(self, eid: str, st: State) -> None:
        ev = {'entity_id': eid, 'do': 'off', 'value': None}
        name = label(self.hass, eid, st)
        if await self._send(ev, name):
            key, room = self.rooms([eid])[eid]
            self._log({**brief(ev), 'name': name, 'room': room if key != eid else None})

    def _restarting(self) -> bool:
        return self._t0 is not None and time.time() < self._t0 + watch_off()

    def _watch_out(self, eid: str) -> None:
        if eid in self._outs:
            return
        unsubs: list[CALLBACK_TYPE] = []

        def done() -> None:
            self._outs.pop(eid, None)
            for unsub in unsubs:
                unsub()

        async def settle(st: State | None) -> None:
            if eid not in self.data['lit'] or self.data['enabled']:
                return
            on = st is not None and st.state == 'on'
            person = on and self._person_since(st)
            self._unlit(eid)
            if on and (not person) and self._pickable(eid):
                await self._off_now(eid, st)
            await self.store.async_save(self.data)

        @callback
        def changed(event: Any) -> None:
            new = event.data.get('new_state')
            if new is None or new.state != 'on' or eid not in self._outs:
                return
            done()
            self.hass.async_create_task(settle(new), eager_start=True)

        @callback
        def expired(_now: dt.datetime) -> None:
            if eid in self._outs:
                done()
                self.hass.async_create_task(settle(self.hass.states.get(eid)), eager_start=True)
        unsubs.append(async_track_state_change_event(self.hass, [eid], changed))
        unsubs.append(async_track_point_in_time(self.hass, expired, dt_util.now() + dt.timedelta(seconds=watch_off())))
        self._outs[eid] = done

    async def async_lights_out(self) -> None:
        """The agent unloaded or removed: nothing it lit keeps burning with nobody left to switch it off."""
        self.async_stop()
        await self._lights_out(final=True)
        await self.store.async_save(self.data)
