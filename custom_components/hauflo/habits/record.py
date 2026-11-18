"""Records what people do by hand with the house's devices."""
from __future__ import annotations
import datetime as dt
import logging
from homeassistant.core import Event, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util
from .common import DOMAINS, NOT_A_STATE, now, t_cfg
_LOGGER = logging.getLogger(__name__)

class HabitsRecordMixin:
    """Records the actions done by hand."""

    @callback
    def _automated(self, event: Event) -> None:
        self._auto_ctx.append(event.context.id)

    def _ours(self, st: State) -> bool:
        ctx, t = (st.context, st.last_updated.timestamp())
        for engine in self.engines():
            if engine is None:
                continue
            if isinstance(getattr(engine, 'data', None), dict) and engine.data.get('enabled') is True:
                return True
            seen = getattr(engine, 'own_contexts', ())
            if ctx.id in seen or (ctx.parent_id and ctx.parent_id in seen):
                return True
            at = engine.sent_at(st.entity_id) if hasattr(engine, 'sent_at') else None
            if at is not None and at - 1 <= t <= at + t_cfg()['echo_s']:
                return True
        return False

    def _actions(self, domain: str, old: State, new: State) -> list[tuple[str, float | None]]:
        a, b = (old.state, new.state)
        if domain == 'scene':
            return [('on', None)] if b != a and b not in NOT_A_STATE else []
        if a in NOT_A_STATE or b in NOT_A_STATE:
            return []
        if domain in ('light', 'fan', 'switch'):
            if a == b or b not in ('on', 'off'):
                return []
            level = new.attributes.get('brightness') if domain == 'light' and b == 'on' else None
            return [(b, round(level * 100 / 255) if isinstance(level, (int, float)) and level > 0 else None)]
        if domain == 'cover':
            if b in ('opening', 'closing'):
                return []
            act, was = (self._cover_action(new), self._cover_action(old))
            if act is None or act[0] == self._rest.get(new.entity_id, was[0] if was else None):
                return []
            return [act]
        out: list[tuple[str, float | None]] = []
        if b != a and b in ('heat', 'cool', 'off'):
            out.append((b, None))
        temp, before = (new.attributes.get('temperature'), old.attributes.get('temperature'))
        if b != 'off' and isinstance(temp, (int, float)) and (temp != before):
            out.append(('temp', float(temp)))
        return out

    @staticmethod
    def _cover_action(st: State) -> tuple[str, float | None] | None:
        pos, step = (st.attributes.get('current_position'), t_cfg()['position_step'])
        if isinstance(pos, (int, float)):
            if pos <= 0:
                return ('close', None)
            if pos >= 100:
                return ('open', None)
            bucket = int(min(100 - step, max(step, round(pos / step) * step)))
            return (f'pos{bucket}', float(pos))
        return {'open': ('open', None), 'closed': ('close', None)}.get(st.state)

    @callback
    def _changed(self, event: Event) -> None:
        new, old = (event.data.get('new_state'), event.data.get('old_state'))
        if not self.data['enabled'] or new is None or old is None or ((domain := new.entity_id.split('.')[0]) not in DOMAINS):
            return
        eid, ctx, when = (new.entity_id, new.context, new.last_updated)
        if domain == 'cover' and new.state in ('opening', 'closing'):
            t0 = self._travel.get(eid)
            if t0 is None or when.timestamp() - t0[0] > t_cfg()['travel_s']:
                self._travel[eid] = (when.timestamp(), ctx)
            return
        acts = self._actions(domain, old, new)
        if domain == 'cover':
            if (start := self._travel.pop(eid, None)) is not None and when.timestamp() - start[0] <= t_cfg()['travel_s']:
                when, ctx = (dt_util.utc_from_timestamp(start[0]), start[1])
            if acts:
                self._rest[eid] = acts[0][0]
        if not acts or self._since is None or when.timestamp() < self._since:
            return
        if domain == 'scene':
            self._scene_ctx.append(ctx.id)
        elif ctx.id in self._scene_ctx:
            return
        if (ent := er.async_get(self.hass).async_get(eid)) is not None and ent.platform == 'group':
            return
        if ctx.parent_id is not None or ctx.id in self._auto_ctx:
            for act, _ in acts:
                self._add('auto', f'{eid}|{act}', when, None)
            return
        if self._ours(new) or not self.pickable(eid):
            return
        for act, level in acts:
            self.record(eid, act, when, level)

    def record(self, eid: str, act: str, when: dt.datetime, level: float | None=None) -> None:
        """One change by a person's hand (also the seeding path of tests)."""
        key, t = (f'{eid}|{act}', when.timestamp())
        last = self._last.get(eid)
        if last is not None and 0 <= t - last[0] < t_cfg()['settle_s']:
            if last[1] == key and (rows := self.data['events'].get(key)):
                rows[-1][4] = level
                self._save_soon()
            return
        self._last[eid] = (t, key)
        self._add('events', key, when, level)

    def _add(self, where: str, key: str, when: dt.datetime, level: float | None) -> None:
        local = dt_util.as_local(when)
        day = local.date().toordinal()
        rows = self.data[where].setdefault(key, [])
        rows.append([day, local.hour * 60 + local.minute, self._from_sun('sunset', local), self._from_sun('sunrise', local), level])
        del rows[:max(0, len(rows) - int(t_cfg()['max_per_key']))]
        if len(self.data[where]) > t_cfg()['max_keys']:
            stale = min(self.data[where], key=lambda k: self.data[where][k][-1][0] if self.data[where][k] else 0)
            self.data[where].pop(stale, None)
        if where == 'events':
            self.observed(day)
        self._save_soon()

    def observed(self, day: int | None=None) -> None:
        """A day the agent watched the house (with or without a habit in it)."""
        day = day if day is not None else now().date().toordinal()
        if day not in self.data['days']:
            self.data['days'] = sorted([*self.data['days'], day])[-int(t_cfg()['keep_days']):]
            self._save_soon()

    def _from_sun(self, event: str, local: dt.datetime) -> int | None:
        key = (local.date().toordinal(), event)
        if key not in self._sun:
            at = None
            at = get_astral_event_date(self.hass, event, local.date())
            self._sun[key] = at
            if len(self._sun) > 4 * t_cfg()['keep_days']:
                self._sun.pop(next(iter(self._sun)))
        at = self._sun[key]
        return None if at is None else round((local - at).total_seconds() / 60)
