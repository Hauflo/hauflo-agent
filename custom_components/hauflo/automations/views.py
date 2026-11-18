"""HTTP view of the simple automations."""
from __future__ import annotations
from collections.abc import Callable
import logging
from typing import TYPE_CHECKING, Any
from homeassistant.components.http import HomeAssistantView
from ..const import wants_ru
from ..house import refuse
from ..rules import actions as rules_actions, const as rules_const
from .common import NO_LOCATION_EN, NO_LOCATION_RU, Changed, NotPlain, bad
if TYPE_CHECKING:
    from .engine import Automations
_LOGGER = logging.getLogger(__name__)

class AutomationsView(HomeAssistantView):
    url = '/api/hauflo/automations'
    name = 'api:hauflo:automations'
    requires_auth = True

    def __init__(self, auto_of: Callable[[], Automations | None]) -> None:
        self.auto_of = auto_of

    async def get(self, request):
        engine = self.auto_of()
        if engine is None:
            return self.json_message('Hauflo agent not set up', 503)
        return self.json(await engine.answer())

    async def post(self, request):
        engine = self.auto_of()
        if engine is None:
            return self.json_message('Hauflo agent not set up', 503)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict):
            return self.json_message('invalid JSON', 400)
        user = request['hass_user']
        op = body.get('op')
        ru = wants_ru(body, engine.hass.config.language)
        if op in ('location', 'detect') and (not (user is not None and user.is_admin)):
            msg = 'Место объекта задаёт администратор Home Assistant.' if ru else "The site's location is set by the Home Assistant administrator."
            return self.json({'message': msg, 'code': 'admin_only'}, 403)
        if op in ('save', 'enable', 'delete') and (no := (await refuse(request))) is not None:
            return no
        try:
            if op == 'save':
                saved = await engine.save(body.get('rule'), user.name if user else None, body.get('force') is True, rules_const.LANGS[0] if ru else rules_const.LANGS[1])
                if saved.get('warning') == 'no_location':
                    saved['warning_text'] = NO_LOCATION_RU if ru else NO_LOCATION_EN
                return self.json(saved)
            if op == 'enable':
                return self.json(await engine.set_enabled(body.get('id'), body.get('enabled')))
            if op == 'delete':
                await engine.delete(body.get('id'))
                return self.json({'deleted': body.get('id')})
            if op == 'run':
                return self.json(await engine.run(body.get('id')))
            if op == 'location':
                return self.json(await engine.set_location(body))
            if op == 'detect':
                return self.json(await engine.detect())
            if op in ('dismiss_habit', 'habits'):
                if (no := (await refuse(request))) is not None:
                    return no
                if (h := engine.habits()) is None:
                    return self.json_message('habits not running', 503)
                if op == 'habits':
                    if not isinstance(body.get('enabled'), bool):
                        raise bad('enabled')
                    await h.set_enabled(body['enabled'])
                else:
                    await h.dismiss(body.get('key'))
                return self.json(await engine.answer())
            if op == 'timezone':
                if not (user and user.is_admin):
                    return self.json_message('admin only', 403)
                return self.json(await engine.set_time_zone(body))
        except rules_actions.Invalid as err:
            await engine.texts()
            out = {'message': engine.message(err, ru), 'code': err.code, 'key': err.key}
            if err.code == 'bad_request':
                out['field'] = err.ph.get('what')
            return self.json(out, 400)
        except NotPlain as err:
            _LOGGER.warning('Hauflo automations: automations.yaml left as it is (%s)', err)
            return self.json({'message': NotPlain.RU if ru else NotPlain.EN, 'code': 'not_plain'}, 409)
        except KeyError:
            return self.json(await self._said(engine, 'no_rule', ru), 404)
        except Changed:
            return self.json(await self._said(engine, 'changed', ru), 409)
        except (LookupError, PermissionError) as err:
            return self.json(await self._said(engine, str(err).removeprefix('automations_'), ru), 409)
        except Exception as err:
            _LOGGER.warning('Hauflo sun: %s failed: %s', op, err)
            return self.json_message(f'Home Assistant: {err}', 500)
        return self.json_message('op: save, enable, delete, run, detect, location, timezone, dismiss_habit or habits', 400)

    @staticmethod
    async def _said(engine: Automations, code: str, ru: bool) -> dict[str, Any]:
        tx = await engine.texts()
        return {'message': tx.say(f'automations_{code}', ru), 'code': code}
