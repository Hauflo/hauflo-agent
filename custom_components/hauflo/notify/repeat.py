"""Repeats an alarm until someone accepts it, alone or in a group."""
from __future__ import annotations
from datetime import timedelta
from typing import Any
from homeassistant.components import persistent_notification
from homeassistant.core import State, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util
from .common import ACK, RUN, label, repeat_fast, repeat_slow
from .levels import in_alarm, level_text
from .push import button
from .rules import devices

class NotifyRepeatMixin:
    """Schedules the alarm repeats."""

    def _hhmm(self, iso: str | None) -> str:
        at = dt_util.parse_datetime(iso or '') or dt_util.utcnow()
        if at.tzinfo is None:
            at = at.replace(tzinfo=dt_util.UTC)
        tz = dt_util.get_time_zone(str(self.hass.config.time_zone or '')) or dt_util.get_default_time_zone()
        return at.astimezone(tz).strftime('%H:%M')

    async def _send_alarm(self, rule: dict[str, Any], state: State, key: str) -> None:
        inc = self.active.get(key)
        if not inc or inc.get('acked_by'):
            return
        inc['count'] += 1
        inc['last'] = dt_util.utcnow().isoformat()
        inc['own'] = True
        where = self._where(state)
        name = label(self.hass, state.entity_id, state) + level_text(rule, state)
        title = f"🚨 {rule['title']}" + (f' — {where}' if where else '')
        at = self._hhmm(inc['since'])
        message = f'{name}: авария в {at}.' if inc['count'] == 1 else f"{name}: авария с {at}, никто не подтвердил. Повтор {inc['count']}."
        actions = [{'action': f'{ACK}|{key}', 'title': 'Принято'}]
        if rule.get('action'):
            actions.insert(0, {'action': f'{RUN}|{key}', 'title': button(rule['action'], self.name_of(rule['action']['entity_id'])), 'destructive': True, 'authenticationRequired': True})
        first = rule['id'] in self._native_first
        if await self.send(rule, title, message, tag=self.native[rule['id']]['automation'] if first else self.tag(key), critical=rule['priority'] == 'critical', actions=actions, force=first):
            inc['pushed'] = True
        self._note(f'{message}', title=title, notification_id=self.tag(key), rule=None if first else rule)
        self._schedule(rule, key)
        self._save()

    def _left(self, rule: dict[str, Any], last: str | None, count: int=0) -> timedelta:
        full = timedelta(minutes=rule['repeat_min'] if count <= repeat_fast() else max(rule['repeat_min'], repeat_slow()))
        sent = dt_util.parse_datetime(last) if last else None
        return max(timedelta(seconds=5), full - (dt_util.utcnow() - sent)) if sent else full

    @callback
    def _schedule(self, rule: dict[str, Any], key: str) -> None:
        inc = self.active.get(key)
        if not rule['repeat_min'] or not inc or self._in_ha(rule['id']):
            return
        rule_id, entity_id = (rule['id'], key.partition('|')[2])

        @callback
        def _again(_now) -> None:
            self.hass.async_create_task(self._repeat(rule_id, entity_id))
        if inc.get('repeat'):
            inc['repeat']()
        inc['repeat'] = async_call_later(self.hass, self._left(rule, inc.get('last'), int(inc.get('count') or 0)), _again)

    async def _repeat(self, rule_id: str, entity_id: str) -> None:
        rule = next((r for r in self.config['rules'] if r['id'] == rule_id), None)
        state = self.hass.states.get(entity_id)
        key = f'{rule_id}|{entity_id}'
        if rule and state and (key in self.active) and in_alarm(rule, state, open_now=True):
            await self._send_alarm(rule, state, key)

    async def _send_group(self, rule: dict[str, Any], *, repeat: bool=False, back: list[str] | None=None) -> None:
        rule_id = rule['id']
        group = self.groups.get(rule_id)
        members = [k for k, inc in self.active.items() if k.startswith(rule_id + '|') and inc.get('group') and (not inc.get('acked_by'))]
        if not group or group['acked_by'] or (not members):
            return
        if not back:
            group['count'] += 1
            group['last'] = dt_util.utcnow().isoformat()
        starts = sorted((self.active[k]['since'] for k in members))
        first = min(starts[0], group['since'])
        at = self._hhmm(first)
        latest = self._hhmm(starts[-1])
        if latest != at:
            at = f'{at} (последняя в {latest})'
        n = len(members)
        title = f"🚨 {rule['title']} — {n} {devices(n)}"
        lines = [f'С {at}: {self._where_list(members, rule)}.']
        if back:
            lines.append('Снова в норме: ' + self._where_list(back) + '.')
        if repeat:
            lines.append(f"Никто не подтвердил. Повтор {group['count']}.")
        gkey = f'{rule_id}|*'
        actions = [{'action': f'{ACK}|{gkey}', 'title': 'Принято'}]
        if rule.get('action'):
            actions.insert(0, {'action': f'{RUN}|{gkey}', 'title': button(rule['action'], self.name_of(rule['action']['entity_id'])), 'destructive': True, 'authenticationRequired': True})
        first = rule_id in self._native_first and (not back) and (not repeat)
        if await self.send(rule, title, ' '.join(lines), tag=self.native[rule_id]['automation'] if first else self._gtag(rule_id), critical=rule['priority'] == 'critical' and (not back), actions=actions, quiet=bool(back), force=first):
            for k in members:
                self.active[k]['pushed'] = True
        self._note(' '.join(lines), title=title, notification_id=self._gtag(rule_id), rule=None if first else rule)
        if not back:
            if group.get('repeat'):
                group['repeat']()
                group['repeat'] = None
            self._schedule_group(rule)

    @callback
    def _schedule_group(self, rule: dict[str, Any]) -> None:
        group = self.groups.get(rule['id'])
        if not rule['repeat_min'] or not group or self._in_ha(rule['id']):
            return
        rule_id = rule['id']

        @callback
        def _again(_now) -> None:
            group['repeat'] = None
            if (r := next((x for x in self.config['rules'] if x['id'] == rule_id), None)) and self.groups.get(rule_id) is group:
                self.hass.async_create_task(self._send_group(r, repeat=True))
        if group.get('repeat'):
            group['repeat']()
        group['repeat'] = async_call_later(self.hass, self._left(rule, group.get('last'), int(group.get('count') or 0)), _again)

    @callback
    def _end_group(self, rule_id: str) -> None:
        group = self.groups.pop(rule_id, None)
        if group and group.get('repeat'):
            group['repeat']()
        if group:
            persistent_notification.async_dismiss(self.hass, self._gtag(rule_id))

    @callback
    def _end_quietly(self, key: str) -> None:
        inc = self.active.pop(key, None)
        if inc and inc.get('repeat'):
            inc['repeat']()
        if inc:
            persistent_notification.async_dismiss(self.hass, self.tag(key))
