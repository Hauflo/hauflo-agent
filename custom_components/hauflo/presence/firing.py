"""Plans the day and fires each simulated light session."""
from __future__ import annotations
import datetime as dt
import logging
import time
from typing import Any
from homeassistant.core import CALLBACK_TYPE, Context, State, callback
from homeassistant.helpers.event import async_track_point_in_time, async_track_state_change_event
from homeassistant.util import dt as dt_util
from ..notify.common import label
from .common import action_of, brief, learn_days, log_size, manual, max_ours, max_rooms, ours_after, watch_off
from .model import build_model, drop_ours
from .plan import plan_day
from .views import service_for
_LOGGER = logging.getLogger(__name__)

class PresenceFiringMixin:
    """Schedules and fires the day's light sessions."""

    async def _history(self, entity_ids: list[str], start: dt.datetime, end: dt.datetime) -> dict[str, list[State]] | None:
        if 'recorder' not in self.hass.config.components:
            return None
        from homeassistant.components.recorder import get_instance, history
        got = await get_instance(self.hass).async_add_executor_job(history.get_significant_states, self.hass, start, end, entity_ids, None, True, False, False, False)
        return {k: [s for s in v if isinstance(s, State)] for k, v in got.items()}

    async def plan(self) -> None:
        """Learns from the last 14 days, plans today (from now) and adds it."""
        now = dt_util.now()
        day = dt_util.as_local(now).date()
        info = self.candidates()
        skip = set(self.data.get('skip_rooms') or [])
        if skip and info:
            where = self.rooms(sorted(info))
            info = {e: m for e, m in info.items() if where[e][0] not in skip}
        if not info:
            self.status, self.tonight = ('no_devices', None)
            return
        try:
            history = await self._history(sorted(info), now - dt.timedelta(days=learn_days()), now)
        except Exception as err:
            _LOGGER.warning('Hauflo presence: history unreadable: %s', err)
            history = None
        if history is None:
            self.status, self.tonight = ('no_recorder', None)
            return
        history = drop_ours(history, self.data.get('ours') or [])
        rooms = self.rooms(sorted(info))
        model = await self.hass.async_add_executor_job(build_model, history, info, rooms, now - dt.timedelta(days=learn_days()), now)
        n_rooms = len({rooms[e][0] for e, m in info.items() if m['domain'] == 'light'})
        cap = min(max_rooms(), max(1, n_rooms - 1))
        events, self.tonight = plan_day(model, day, self._sun(day), now, self.rng, self.data['mode'], cap, self.data['lights'], self.data['covers'])
        self.status = self.tonight['lights'] if self.tonight['lights'] in ('few', 'no_location') else 'on'
        for ev in events:
            self._schedule(ev)

    def _schedule(self, ev: dict[str, Any]) -> None:
        self._n += 1
        key = self._n
        ev['key'] = key

        async def run(_now: dt.datetime) -> None:
            if (item := self.pending.pop(key, None)) is not None:
                await self.fire(item[1])
        self.pending[key] = (async_track_point_in_time(self.hass, run, ev['at']), ev)

    def _touched(self, st: State) -> bool:
        if (dt_util.utcnow() - st.last_updated).total_seconds() > manual():
            return False
        if self._boot is not None and st.last_updated.timestamp() <= self._boot:
            return False
        if st.context.id in self._ctx or (st.context.parent_id and st.context.parent_id in self._ctx):
            return False
        sent = self._sent.get(st.entity_id)
        return not (sent is not None and sent - 1 <= st.last_updated.timestamp() <= sent + ours_after())

    def _person_since(self, st: State) -> bool:
        c = st.context
        if not c.user_id or c.id in self._ctx or (c.parent_id and c.parent_id in self._ctx):
            return False
        return st.last_updated.timestamp() > float(self.data['lit_at'].get(st.entity_id) or 0) + 1

    def _unlit(self, eid: str) -> None:
        self.data['lit'] = [e for e in self.data['lit'] if e != eid]
        self.data['lit_at'] = {k: v for k, v in self.data['lit_at'].items() if k != eid}

    def _claim(self, eid: str) -> None:
        if eid in self._claims:
            return
        unsubs: list[CALLBACK_TYPE] = []

        def done() -> None:
            self._claims.pop(eid, None)
            for unsub in unsubs:
                unsub()

        @callback
        def changed(event: Any) -> None:
            new = event.data.get('new_state')
            if new is None or new.state != 'on' or eid not in self._claims:
                return
            done()
            if not new.context.user_id:
                self._remember(eid)
                self.store.async_delay_save(lambda: self.data, 5)

        @callback
        def expired(_now: dt.datetime) -> None:
            if eid in self._claims:
                done()
        unsubs.append(async_track_state_change_event(self.hass, [eid], changed))
        unsubs.append(async_track_point_in_time(self.hass, expired, dt_util.now() + dt.timedelta(seconds=watch_off())))
        self._claims[eid] = done

    def _wait(self, eid: str, until: dt.datetime | None=None) -> None:
        if eid in self._waits:
            return
        unsubs: list[CALLBACK_TYPE] = []

        def done() -> None:
            self._waits.pop(eid, None)
            for unsub in unsubs:
                unsub()

        @callback
        def changed(event: Any) -> None:
            new = event.data.get('new_state')
            if new is None or new.state in ('unavailable', 'unknown') or eid not in self._waits:
                return
            if new.state == 'on':
                done()
                self._schedule({'at': dt_util.now() + dt.timedelta(seconds=self.rng.uniform(30, 120)), 'entity_id': eid, 'do': 'off', 'value': None})
            elif until is None:
                done()
                self._unlit(eid)

        @callback
        def expired(_now: dt.datetime) -> None:
            if eid in self._waits:
                done()
                if (st := self.hass.states.get(eid)) is None or st.state != 'on':
                    self._unlit(eid)
        unsubs.append(async_track_state_change_event(self.hass, [eid], changed))
        if until is not None:
            unsubs.append(async_track_point_in_time(self.hass, expired, until))
        self._waits[eid] = done

    async def _send(self, ev: dict[str, Any], name: str) -> bool:
        eid = ev['entity_id']
        domain = eid.split('.')[0]
        service, data = service_for(domain, ev['do'], ev['value'])
        ctx = Context()
        self._ctx.append(ctx.id)
        self._sent[eid] = time.time()
        try:
            await self.hass.services.async_call(domain, service, {'entity_id': eid, **data}, blocking=True, context=ctx)
        except Exception as err:
            _LOGGER.warning('Hauflo presence: %s %s failed: %s', eid, service, err)
            self._log({**brief(ev), 'name': name, 'skip': 'error'})
            return False
        self._remember(eid)
        return True

    def _remember(self, eid: str) -> None:
        cut = time.time() - (learn_days() + 1) * 86400
        kept = [x for x in self.data.get('ours') or [] if isinstance(x, list) and len(x) == 2 and isinstance(x[1], (int, float)) and (x[1] >= cut)]
        self.data['ours'] = [*kept[-(max_ours() - 1):], [eid, round(time.time(), 1)]]

    async def fire(self, ev: dict[str, Any]) -> None:
        if not self.data['enabled']:
            return
        if self._expired():
            await self._switch_off('auto_off')
            return
        eid = ev['entity_id']
        domain = eid.split('.')[0]
        mine = domain == 'light' and ev['do'] == 'off' and (eid in self.data['lit'])
        if not mine and (domain == 'light' and (not self.data['lights']) or (domain == 'cover' and (not self.data['covers']))):
            return
        if ev['do'] != 'off' and self.rooms([eid])[eid][0] in set(self.data.get('skip_rooms') or []):
            return
        st = self.hass.states.get(eid)
        if mine and (st is None or st.state in ('unavailable', 'unknown')):
            if eid not in self._waits:
                self._log({**brief(ev), 'name': (st.attributes.get('friendly_name') if st else None) or eid, 'skip': 'offline'})
            self._wait(eid)
            return
        if not self._pickable(eid) or st is None:
            self._log({**brief(ev), 'name': eid, 'skip': 'gone'})
            if mine:
                self._unlit(eid)
            return
        name = label(self.hass, eid, st)
        if st.state in ('unavailable', 'unknown'):
            self._log({**brief(ev), 'name': name, 'skip': 'offline'})
            return
        if self._person_since(st) if mine else self._touched(st):
            self._log({**brief(ev), 'name': name, 'skip': 'manual'})
            if mine:
                self._unlit(eid)
            return
        info = self.candidates(every=True).get(eid) or {'dim': False, 'pos': False}
        now_is = action_of(domain, st, info['dim'], info['pos'])
        if now_is == (ev['do'], ev['value']) or (ev['do'] == 'on' and ev['value'] is None and now_is and (now_is[0] == 'on')):
            if mine:
                self._unlit(eid)
            return
        if not await self._send(ev, name):
            return
        if domain == 'light':
            self._unlit(eid)
            if ev['do'] == 'on':
                self.data['lit'] = [*self.data['lit'], eid]
                self.data['lit_at'] = {**self.data['lit_at'], eid: round(time.time(), 1)}
        key, room = self.rooms([eid])[eid]
        self._log({**brief(ev), 'name': name, 'room': room if key != eid else None})

    def _log(self, item: dict[str, Any]) -> None:
        self.data['log'] = [{'t': dt_util.now().isoformat(timespec='seconds'), **item}, *self.data['log']][:log_size()]
        self.store.async_delay_save(lambda: self.data, 5)
