"""HTTP view and service of the presence simulation."""
from __future__ import annotations
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from homeassistant.components.http import HomeAssistantView
from ..const import wants_ru
from ..house import refuse
from ..rules.text import said
from .common import Invalid, Refused
if TYPE_CHECKING:
    from .engine import Presence

def service_for(domain: str, do: str, value: int | None) -> tuple[str, dict[str, Any]]:
    if domain == 'light':
        return ('turn_on', {'brightness_pct': value} if value else {}) if do == 'on' else ('turn_off', {})
    if do == 'position':
        return ('set_cover_position', {'position': value})
    return ('open_cover' if do == 'open' else 'close_cover', {})

class PresenceView(HomeAssistantView):
    url = '/api/hauflo/presence'
    name = 'api:hauflo:presence'
    requires_auth = True

    def __init__(self, engine_of: Callable[[], Presence | None]) -> None:
        self.engine_of = engine_of

    async def get(self, request):
        engine = self.engine_of()
        if engine is None:
            return self.json_message('Hauflo agent not set up', 503)
        return self.json(engine.answer())

    async def post(self, request):
        engine = self.engine_of()
        if engine is None:
            return self.json_message('Hauflo agent not set up', 503)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict) or body.get('op') != 'set':
            return self.json_message('op: set', 400)
        if set(body) - {'op', 'enabled', 'until', 'lights', 'covers', 'mode', 'skip_rooms', 'lang'}:
            return self.json_message('only enabled, until, lights, covers, mode, skip_rooms', 400)
        if (no := (await refuse(request))) is not None:
            return no
        user = request['hass_user']
        try:
            return self.json(await engine.set(body, user.name if user else None))
        except Refused as err:
            return self.json(await said(engine.hass, err, wants_ru(body, engine.hass.config.language)), err.status)
        except Invalid as err:
            return self.json_message(str(err), 400)
