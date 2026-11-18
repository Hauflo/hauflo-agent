"""HTTP view of the family's scenes."""
from __future__ import annotations
from collections.abc import Callable
from http import HTTPStatus
import logging
from typing import TYPE_CHECKING
from homeassistant.components.http import HomeAssistantView
from .. import derived
from ..const import LOCKED_CODE, wants_ru
from ..daylight import Invalid as DayInvalid, SensorRefused
from ..house import refuse
from ..rules.text import said
from .common import Invalid, Locked, NotPlain, Refused, TooSoon
if TYPE_CHECKING:
    from .engine import FamilyScenes
_LOGGER = logging.getLogger(__name__)

class ScenesView(HomeAssistantView):
    url = '/api/hauflo/scenes'
    name = 'api:hauflo:scenes'
    requires_auth = True

    def __init__(self, scenes_of: Callable[[], FamilyScenes | None]) -> None:
        self.scenes_of = scenes_of

    async def get(self, request):
        engine = self.scenes_of()
        if engine is None:
            return self.json_message('Hauflo agent not set up', 503)
        return self.json(await engine.answer())

    async def post(self, request):
        engine = self.scenes_of()
        if engine is None:
            return self.json_message('Hauflo agent not set up', 503)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict):
            return self.json_message('invalid JSON', 400)
        user = request['hass_user']
        ru = wants_ru(body, engine.hass.config.language)
        op = body.get('op')
        if op in ('add', 'rename', 'recapture', 'delete', 'unhide', 'knx_name', 'knx_learn', 'daylight_skip') and (no := (await refuse(request))) is not None:
            return no
        if op == 'daylight_config' and (not (user and user.is_admin)):
            return self.json_message('only an administrator sets the daylight sensor', 403)
        try:
            if op == 'apply':
                return self.json(await engine.apply(body.get('entity_id')))
            if op == 'daylight_skip':
                return self.json(await engine.daylight_skip(body.get('entity_id'), body.get('on')))
            if op == 'daylight_config':
                return self.json(await engine.daylight_config(body))
            if op == 'add':
                return self.json(await engine.add(body.get('room'), body.get('kind'), body.get('name'), body.get('entities'), user.name if user else None))
            if op == 'rename':
                return self.json(await engine.rename(body.get('entity_id'), body.get('name')))
            if op == 'recapture':
                return self.json(await engine.recapture(body.get('entity_id'), body.get('entities')))
            if op == 'delete':
                return self.json(await engine.delete(body.get('entity_id')))
            if op == 'unhide':
                return self.json(await engine.unhide(body.get('entity_id')))
            if op == 'knx_name':
                return self.json(await engine.name(body.get('entity_id'), body.get('name')))
            if op == 'knx_learn':
                return self.json(await engine.learn(body.get('entity_id'), user.name if user else None))
        except SensorRefused as err:
            return self.json({'message': await err.text(engine.hass, ru), 'code': err.key}, 400)
        except derived.Refused as err:
            return self.json(await said(engine.hass, err, ru), err.status)
        except (Invalid, DayInvalid) as err:
            return self.json({'message': f'bad request: {err}', 'code': 'bad_body', 'field': str(err)}, 400)
        except NotPlain as err:
            _LOGGER.warning('Hauflo scenes: scenes.yaml left as it is (%s)', err)
            return self.json({'message': NotPlain.RU if ru else NotPlain.EN, 'code': 'not_plain'}, 409)
        except Locked:
            return self.json({'message': Locked.RU if ru else Locked.EN, 'code': LOCKED_CODE}, 423)
        except TooSoon as err:
            return self.json({'message': err.text(ru), 'code': 'too_soon'}, 429)
        except PermissionError as err:
            return self.json_message(str(err), 403)
        except KeyError:
            return self.json(await said(engine.hass, Refused('no_scene', HTTPStatus.NOT_FOUND), ru), 404)
        except LookupError:
            msg = 'Нечего запомнить: свет и шторы комнаты не сообщают своё состояние.' if ru else "Nothing to remember: the room's lights and blinds don't report their state."
            return self.json({'message': msg, 'code': 'nothing'}, 409)
        except Exception as err:
            _LOGGER.warning('Hauflo scenes: %s failed: %s', op, err)
            return self.json_message(f'Home Assistant: {err}', 500)
        return self.json_message('op: apply, add, rename, recapture, delete, unhide, knx_name, knx_learn, daylight_skip or daylight_config', 400)
