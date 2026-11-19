"""Who gets an alarm, how it is said and how it is accepted."""
from __future__ import annotations
import asyncio
from collections.abc import Callable
import logging
import time
from typing import Any
from homeassistant.components import persistent_notification
from homeassistant.core import Event, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util, slugify
from ..const import ACK_ACTION, DO_ACTION, OK_ACTION, RUN_ACTION, site_ru
from .common import ACK, PHONE, RUN, RUN_SERVICES, PushErrors, in_en, push_day_critical, push_day_max, push_event_day_max, push_event_hour_max, push_hour_max, test_gap, test_per_min
from .push import button, push_data
from .rules import devices
_LOGGER = logging.getLogger(__name__)

class NotifyDeliveryMixin:
    """Delivers and accepts alarm pushes."""

    async def _on_action(self, event: Event) -> None:
        action = str(event.data.get('action') or '')
        if action.startswith((ACK_ACTION, RUN_ACTION, OK_ACTION, DO_ACTION)):
            if (rule_id := next((r for r, rec in self.native.items() if action in (rec.get('ack'), rec.get('run'))), None)):
                self._native_who[rule_id] = await self._who(event)
            return
        if not action.startswith((ACK + '|', RUN + '|')):
            return
        kind, key = action.split('|', 1)
        await self._accept(kind, key, await self._who(event), event.context)

    async def async_ack(self, key: str, who: str) -> bool:
        if key in self.unacked_rtn and key not in self.active:
            self.unacked_rtn.pop(key, None)
            self._save()
            self._changed()
            return True
        if key not in self.active and (not (key.endswith('|*') and key.partition('|')[0] in self.groups)):
            return False
        await self._accept(ACK, key, who, None)
        return True

    @callback
    def ack_house(self, entity_id: str, who: str) -> bool:
        """«Принято» for a house problem (alarms_now.HOUSE_KEY): no push or siren is its — only who accepted it, until it clears (house_acks_keep)."""
        if self.house_acks.get(entity_id):
            return False
        self.house_acks[entity_id] = who[:80]
        self._save()
        self._changed()
        return True

    @callback
    def house_acks_keep(self, problems: set[str]) -> None:
        """A house problem that cleared forgets its «Принято»: the next one is a new alarm (ISA-18.2)."""
        gone = [e for e in self.house_acks if e not in problems]
        for e in gone:
            del self.house_acks[e]
        if gone:
            self._save()

    async def _accept(self, kind: str, key: str, who: str, context: Any) -> None:
        rule_id, _, entity_id = key.partition('|')
        rule = next((r for r in self.config['rules'] if r['id'] == rule_id), None)
        open_now = key in self.active or (entity_id == '*' and rule_id in self.groups)
        if not open_now:
            return
        if kind == RUN and rule and rule.get('action') and open_now and (rule['action']['service'] in RUN_SERVICES):
            domain, _, service = rule['action']['service'].partition('.')
            try:
                await self.hass.services.async_call(domain, service, {'entity_id': rule['action']['entity_id']}, blocking=True, context=context)
                done = f"{button(rule['action'], self.name_of(rule['action']['entity_id']))}: выполнено ({who})."
            except Exception as err:
                _LOGGER.warning('Hauflo: alarm action %s failed', rule['action']['service'], exc_info=True)
                done = f"{button(rule['action'], self.name_of(rule['action']['entity_id']))}: не получилось — {err}."
        else:
            done = f'Принято: {who}.'
        if entity_id == '*':
            incs = [inc for k, inc in self.active.items() if k.startswith(rule_id + '|') and inc.get('group')]
            if (group := self.groups.get(rule_id)):
                group['acked_by'] = who
                if group.get('repeat'):
                    group['repeat']()
                    group['repeat'] = None
            name, tag = (f'{len(incs)} {devices(len(incs))}' if incs else rule['title'] if rule else '', self._gtag(rule_id))
        else:
            incs = [inc] if (inc := self.active.get(key)) else []
            state = self.hass.states.get(entity_id)
            where = self._where(state) if state else ''
            name, tag = (self.name_of(entity_id) + (f' ({where})' if where else ''), self.tag(key))
        for inc in incs:
            inc['acked_by'] = who
            if inc.get('repeat'):
                inc['repeat']()
                inc['repeat'] = None
        if rule and rule.get('outputs'):
            await self._switch(rule_id, [o['entity_id'] for o in rule['outputs'] if o['on_end']], 'on_end', f'accepted: {who}')
        self._save()
        self._changed()
        native = self.native.get(rule_id)
        if native:
            if not any((not i.get('acked_by') and (not i.get('nodata')) for k, i in self.active.items() if k.startswith(rule_id + '|'))):
                await self._set_helper(rule_id, True)
            if self._in_ha(rule_id):
                tag = native['automation']
        if rule:
            await self.send(rule, f"{rule['title']}", f'{name}: {done}', tag=tag, critical=False, force=bool(native))

    async def _who(self, event: Event) -> str:
        user_id = event.context.user_id if event.context else None
        if user_id and (user := (await self.hass.auth.async_get_user(user_id))):
            return user.name or 'кто-то'
        return 'кто-то'

    def phones(self) -> list[dict[str, Any]]:
        ents = er.async_get(self.hass)
        devs = dr.async_get(self.hass)
        owners: dict[str, str] = {}
        for state in self.hass.states.async_all('person'):
            for tracker in state.attributes.get('device_trackers') or []:
                owners[tracker] = state.entity_id
        out = []
        for entry in ents.entities.values():
            if entry.platform != 'mobile_app' or entry.domain != 'device_tracker' or (not entry.device_id):
                continue
            device = devs.async_get(entry.device_id)
            if not device:
                continue
            service = slugify(f"mobile_app_{device.name or ''}")
            if not self.hass.services.has_service('notify', service):
                continue
            os = 'ios' if (device.manufacturer or '').lower() == 'apple' else 'android'
            out.append({'service': service, 'name': device.name_by_user or device.name, 'os': os, 'person': owners.get(entry.entity_id)})
        return out

    def people(self) -> list[dict[str, Any]]:
        phones = self.phones()
        return [{'id': s.entity_id, 'name': s.attributes.get('friendly_name') or s.entity_id, 'phones': [p for p in phones if p['person'] == s.entity_id]} for s in self.hass.states.async_all('person')]

    def scope(self, phone: dict[str, Any], config: dict[str, Any] | None=None) -> str:
        """What a phone gets of the rules sent «to everyone»: its own setting, else its person's, else «*»."""
        aud = (config or self.config).get('audience') or {}
        return aud.get(phone['service']) or (aud.get(phone['person']) if phone.get('person') else None) or aud.get('*') or 'all'

    def targets(self, rule: dict[str, Any], config: dict[str, Any] | None=None) -> list[dict[str, Any]]:
        """The phones of a rule — by the rules in force, or by `config` (rules not saved yet: Hauflo's plan)."""
        phones = self.phones()
        rec = rule['recipients']
        if rec['all']:
            critical = rule.get('priority') == 'critical'
            return [p for p in phones if (s := self.scope(p, config)) == 'all' or (s == 'critical' and critical)]
        people = set(rec['people'])
        for g in (config or self.config)['groups']:
            if g['id'] in (rec.get('groups') or []):
                people.update(g['people'])
        chosen = set(rec.get('phones') or [])
        return [p for p in phones if p['person'] in people or p['service'] in chosen]

    @staticmethod
    def _recent(when: dict[str, str], seconds: int) -> dict[str, str]:
        now = dt_util.utcnow()
        return {k: v for k, v in when.items() if (t := dt_util.parse_datetime(v)) is not None and (now - t).total_seconds() < seconds}

    @staticmethod
    def _lane_key(service: str, lane: str) -> str:
        return service if lane == 'alarm' else f'{service}|{lane}'

    def _room(self, service: str, critical: bool, lane: str='alarm') -> bool:
        now = time.time()
        key = self._lane_key(service, lane)
        log = [t for t in self._pushes.get(key, []) if 0 <= now - t < 86400]
        self._pushes[key] = log
        if critical:
            return len(log) < push_day_critical()
        day, hour = (push_event_day_max(), push_event_hour_max()) if lane == 'event' else (push_day_max(), push_hour_max())
        return len(log) < day and sum((1 for t in log if now - t < 3600)) < hour

    def english(self, config: dict[str, Any] | None=None) -> bool:
        """The project's language when Hauflo said it (in ``config`` — rules not saved yet), else Home Assistant's."""
        return not site_ru(getattr(self.hass.config, 'language', None), config or self.config)

    def _say(self, text: str) -> str:
        return in_en(text) if text and self.english() else text

    def _note(self, message: str, *, title: str, notification_id: str, rule: dict[str, Any] | None=None) -> None:
        if rule is not None and self._in_ha(rule.get('id')):
            return
        persistent_notification.async_create(self.hass, self._say(message), title=self._say(title), notification_id=notification_id)

    async def send(self, rule: dict[str, Any], title: str, message: str, *, tag: str, critical: bool, actions: list | None=None, targets: list | None=None, quiet: bool=False, results: dict[str, str] | None=None, force: bool=False, silent: bool=False, extra: Callable[[str | None], dict[str, Any]] | None=None, lane: str='alarm') -> list[str]:
        testing = results is not None
        if not testing and (not force) and self._in_ha(rule.get('id')):
            return [p['service'] for p in (targets if targets is not None else self.targets(rule))]
        if message != 'clear_notification':
            title, message = (self._say(title), self._say(message))
        if actions:
            actions = [{**a, 'title': self._say(a['title'])} for a in actions]
        sent: list[str] = []
        failed: list[str] = []
        limited: list[str] = []
        held: list[str] = []

        async def one(phone: dict[str, Any]) -> None:
            if not testing and (not self._room(phone['service'], critical, lane)):
                held.append(phone['service'])
                self.throttled[phone['service']] = dt_util.utcnow().isoformat()
                return
            self._pushes.setdefault(self._lane_key(phone['service'], lane), []).append(time.time())
            self._save()
            data = push_data(phone['os'], tag=tag, critical=critical, quiet=quiet, actions=actions, silent=silent)
            for k, v in (extra(phone['os']) if extra is not None else {}).items():
                data[k] = {**data[k], **v} if isinstance(v, dict) and isinstance(data.get(k), dict) else v
            PHONE.set(phone['service'])
            try:
                payload = {'message': message, 'data': data, **({'title': title} if title else {})}
                await self.hass.services.async_call('notify', phone['service'], payload, blocking=True)
                if phone['service'] in errors.limited:
                    limited.append(phone['service'])
                    self.limited[phone['service']] = dt_util.utcnow().isoformat()
                    _LOGGER.warning("Hauflo alarm to %s: the HA app's push limit for this phone is used up for today", phone['service'])
                    return
                if phone['service'] in errors.failed:
                    raise RuntimeError("the HA app's push service said it failed (see HA log)")
                sent.append(phone['service'])
                self.limited.pop(phone['service'], None)
            except Exception as err:
                failed.append(phone['service'])
                _LOGGER.warning('Hauflo alarm to %s failed: %s', phone['service'], err)
        errors = PushErrors()
        app_log = logging.getLogger('homeassistant.components.mobile_app')
        app_log.addHandler(errors)
        try:
            await asyncio.gather(*(asyncio.ensure_future(one(p)) for p in (targets if targets is not None else self.targets(rule))))
        finally:
            app_log.removeHandler(errors)
        self.sent_log = [{'at': dt_util.utcnow().isoformat(), 'title': title, 'sent': sent, 'failed': failed, **({'limited': limited} if limited else {}), **({'held': held} if held else {})}, *self.sent_log][:30]
        if results is not None:
            results.update(dict.fromkeys(sent, 'sent') | dict.fromkeys(failed, 'failed') | dict.fromkeys(limited, 'rate_limited'))
        return sent

    async def async_tell(self, title: str, message: str, user_id: str | None=None, *, actions: list | None=None, tag: str | None=None) -> list[str]:
        """A value rule's message (rules.py), the season's suggestion (season.py): to one HA user's phones («уведомить меня»), else to every phone that gets the house's messages (its «audience»), and — to everyone — a note in HA too."""
        phones = self.phones()
        if user_id:
            people = {s.entity_id for s in self.hass.states.async_all('person') if s.attributes.get('user_id') == user_id}
            targets = [p for p in phones if p['person'] in people]
        else:
            targets = [p for p in phones if self.scope(p) == 'all']
            self._note(message, title=title, notification_id=tag or f'hauflo_rule_{slugify(title)[:40]}')
        return await self.send({}, title, message, tag=tag or f'hauflo-rule-{slugify(title)[:40]}', critical=False, targets=targets, actions=actions)

    def test_wait(self, body: dict[str, Any]) -> int:
        now = time.monotonic()
        self._tests = [t for t in self._tests if now - t < 60]
        if len(self._tests) >= test_per_min():
            return int(60 - (now - self._tests[0])) + 1
        waits = [test_gap() - (now - self._tested[p['service']]) for p in self._test_targets(body) if p['service'] in self._tested]
        return max([0, *[int(w) + 1 for w in waits if w > 0]])

    def _test_targets(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        rec = {'all': False, 'people': [str(p) for p in body.get('people') or []], 'groups': [str(g) for g in body.get('groups') or []], 'phones': [str(p) for p in body.get('phones') or []]}
        return self.phones() if body.get('all') else self.targets({'recipients': rec})

    async def async_test(self, body: dict[str, Any]) -> dict[str, Any]:
        rule = {'recipients': {'all': False, 'people': [], 'groups': []}}
        critical = body.get('priority') == 'critical'
        targets = self._test_targets(body)
        now = time.monotonic()
        self._tests.append(now)
        for p in targets:
            self._tested[p['service']] = now
        results: dict[str, str] = {}
        sent = await self.send(rule, 'Hauflo: проверка уведомлений', 'Так будут приходить аварии.' + (' Критичные — со звуком даже в беззвучном режиме.' if critical else ''), tag='hauflo_test', critical=critical, targets=targets, results=results)
        return {'targets': len(targets), 'sent': sent, 'results': [{'service': p['service'], 'name': p['name'], 'os': p.get('os'), 'status': results.get(p['service'], 'failed')} for p in targets]}
