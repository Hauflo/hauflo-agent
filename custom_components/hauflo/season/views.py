"""HTTP views of the season."""
from __future__ import annotations
from collections.abc import Callable
from typing import TYPE_CHECKING
from homeassistant.components.http import HomeAssistantView
from ..house import refuse
from .common import MODES
if TYPE_CHECKING:
    from .engine import Season

class SeasonView(HomeAssistantView):
    url = '/api/hauflo/season'
    name = 'api:hauflo:season'
    requires_auth = True

    def __init__(self, season_of: Callable[[], Season | None]) -> None:
        self.season_of = season_of

    async def get(self, request):
        season = self.season_of()
        if season is None:
            return self.json_message('Hauflo agent not set up', 503)
        return self.json(await season.answer())

    async def post(self, request):
        season = self.season_of()
        if season is None:
            return self.json_message('Hauflo agent not set up', 503)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        body = body if isinstance(body, dict) else {}
        mode = body.get('mode')
        if mode not in MODES and body.get('snooze') is not True:
            return self.json_message('mode: heat or cool, or snooze', 400)
        if (no := (await refuse(request))) is not None:
            return no
        if mode not in MODES:
            await season.snooze()
            return self.json(await season.answer())
        user = request['hass_user']
        return self.json(await season.apply(mode, user.name if user else None))

class SeasonConfigView(HomeAssistantView):
    """Hauflo's settings of the season (through the tunnel, admin only): GET what the agent holds, POST new ones."""
    url = '/api/hauflo/season/config'
    name = 'api:hauflo:season:config'
    requires_auth = True

    def __init__(self, season_of: Callable[[], Season | None]) -> None:
        self.season_of = season_of

    async def get(self, request):
        season = self.season_of()
        if season is None:
            return self.json_message('Hauflo agent not set up', 503)
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        return self.json(season.setup_answer())

    async def post(self, request):
        season = self.season_of()
        if season is None:
            return self.json_message('Hauflo agent not set up', 503)
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict):
            return self.json_message('an object expected', 400)
        return self.json(await season.async_set_config(body))
