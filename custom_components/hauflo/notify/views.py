"""HTTP views of the alarm pushes."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.components.http import HomeAssistantView
from .. import alarms_now
from ..const import LOCKED_CODE
from ..independent import sync as independent_sync
from .common import LockedChange
_LOGGER = logging.getLogger(__name__)

class NotifyView(HomeAssistantView):
    url = '/api/hauflo/notify'
    name = 'api:hauflo:notify'
    requires_auth = True

    def __init__(self, engine_of) -> None:
        self.engine_of = engine_of

    async def get(self, request):
        engine = self.engine_of()
        if not engine:
            return self.json_message('Hauflo agent not set up', 503)
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        return self.json(engine.status())

    async def post(self, request):
        engine = self.engine_of()
        if not engine:
            return self.json_message('Hauflo agent not set up', 503)
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict):
            return self.json_message('an object expected', 400)
        migrate = body.pop('migrate_alarms', None) is True
        try:
            if body.get('dry_run') is True:
                config = engine.check(body)
                return self.json({'config': config, 'native': await independent_sync.async_sync(engine.hass, engine, dry_run=True, config=config, migrate=migrate)})
            config = await engine.async_set(body)
        except LockedChange as err:
            return self.json({'code': LOCKED_CODE, 'reason': str(err)}, 423)
        try:
            native = await independent_sync.async_sync(engine.hass, engine, migrate=migrate)
        except Exception as err:
            _LOGGER.exception('Hauflo: alarm automations not synced')
            native = {'error': str(err)}
        return self.json({'config': config, 'native': native})

class NotifyTestView(HomeAssistantView):
    url = '/api/hauflo/notify/test'
    name = 'api:hauflo:notify:test'
    requires_auth = True

    def __init__(self, engine_of) -> None:
        self.engine_of = engine_of

    async def post(self, request):
        engine = self.engine_of()
        if not engine:
            return self.json_message('Hauflo agent not set up', 503)
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict):
            return self.json_message('an object expected', 400)
        if (wait := engine.test_wait(body)):
            return self.json({'message': 'a test to this phone was just sent', 'code': 'test_wait', 'retry_after': wait}, 429)
        return self.json(await engine.async_test(body))

class AlarmsView(HomeAssistantView):
    """Since when each open alarm is on — for the dashboard's alarm card (HA's last_changed restarts with HA;"""
    url = '/api/hauflo/alarms'
    name = 'api:hauflo:alarms'
    requires_auth = True

    def __init__(self, engine_of, house_of=None) -> None:
        self.engine_of = engine_of
        self.house_of = house_of

    async def get(self, request):
        engine = self.engine_of()
        out: dict[str, Any] = {}
        pushed: set[str] = set()
        for key, inc in engine.active.items() if engine else []:
            entity_id = key.partition('|')[2]
            if entity_id not in out or inc['since'] < out[entity_id]:
                out[entity_id] = inc['since']
            if inc.get('pushed'):
                pushed.add(entity_id)
        out['_pushed'] = sorted(pushed)
        out['_list'] = [{'key': key, 'entity': key.partition('|')[2], 'since': inc['since'], 'ackedBy': inc.get('acked_by'), 'critical': bool(r and r['priority'] == 'critical'), 'noData': bool(inc.get('nodata')), 'title': r['title'] if r else ''} for key, inc in (engine.active.items() if engine else []) for r in [next((x for x in engine.config['rules'] if x['id'] == key.partition('|')[0]), None)]] + [{'key': key, 'entity': key.partition('|')[2], 'since': v.get('since'), 'returned': v.get('cleared'), 'ackedBy': None, 'critical': True, 'noData': False, 'title': v.get('title', '')} for key, v in (engine.unacked_rtn.items() if engine else []) if key not in engine.active]
        if self.house_of is not None and (house := self.house_of()) is not None:
            out.update(house)
        return self.json(out)

    async def post(self, request):
        """{"ack": "<rule>|<entity>"} — accepted by the signed-in person, as the push button does."""
        engine = self.engine_of()
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        user = request['hass_user']
        who = (user.name if user else None) or 'кто-то'
        if user is not None and user.system_generated and (user.name == 'Hauflo agent') and isinstance(body, dict) and isinstance(body.get('by'), str):
            by = ''.join((ch for ch in body['by'] if ch.isprintable())).strip()[:80]
            if by:
                who = f'{by} (Hauflo)'
        if engine is not None and isinstance(body, dict) and (body.get('ack_all') is True):
            keys = [k for k, inc in engine.active.items() if not inc.get('acked_by')] + [k for k in engine.unacked_rtn if k not in engine.active]
            keys += [k for e in self._house_open() if not e['acked'] for k in e['keys']]
            done = [k for k in keys if await self._ack(engine, k, who)]
            return self.json({'ok': True, 'acked': len(done)})
        if engine is not None and isinstance(body, dict) and ('ack_keys' in body):
            keys = body['ack_keys']
            if not isinstance(keys, list) or len(keys) > 200 or (not all((isinstance(k, str) and '|' in k and (len(k) <= 300) for k in keys))):
                return self.json_message('ack_keys: a list of alarm keys', 400)
            done = [k for k in dict.fromkeys(keys) if await self._ack(engine, k, who)]
            return self.json({'ok': True, 'acked': len(done)})
        key = body.get('ack') if isinstance(body, dict) else None
        if engine is None or not isinstance(key, str) or '|' not in key or (len(key) > 300):
            return self.json_message('ack: a key expected', 400)
        ok = await self._ack(engine, key, who)
        return self.json({'ok': ok}, 200 if ok else 404)

    def _house_open(self) -> list[dict[str, Any]]:
        house = self.house_of() if self.house_of is not None else None
        items = ((house or {}).get('_summary') or {}).get('items') or []
        return [e for e in items if e.get('state') == 'open' and e.get('source') == 'house' and e.get('keys')]

    async def _ack(self, engine: Any, key: str, who: str) -> bool:
        if key.startswith(alarms_now.HOUSE_KEY + '|'):
            entity_id = key.partition('|')[2]
            return any((e['entity'] == entity_id for e in self._house_open())) and engine.ack_house(entity_id, who)
        return await engine.async_ack(key, who)
