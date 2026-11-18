"""HTTP view of the diagnostics."""
from __future__ import annotations
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from ..signals.canon import entity_id_cfg
from .common import ACTIONS_IN, DIAG_ALL, diag_visible, factor_max, factor_min, limit_max, limit_re, list_max, offset_max, offset_re, section_order, sections, tick
if TYPE_CHECKING:
    from .engine import Insights

class InsightsView(HomeAssistantView):
    """The per-entity diagnosis (0.32). GET → Insights.states() (`?entity=` — with its episodes, `?offset=` — the next
    page, `?limit=` its length up to LIMIT_MAX; with the canon's sections and the engine's tick and whether the reader
    may answer): HA's admins, everyone when the diagnosis is
    shown to all (`diag_visible`). POST (admins only) {entity, kind, action: normal | follow |
    reset, factor?} — «это норма» / «снова следить» (its own ×N within FACTOR_MIN..FACTOR_MAX) / «сбросить обучение»;
    `summary`; «Фоновые наблюдения» off — no rows, `off: true`."""
    url = '/api/hauflo/insights'
    name = 'api:hauflo:insights'
    requires_auth = True

    def __init__(self, ins_of: Callable[[], Insights | None], extra: Callable[[], Awaitable[dict[str, Any]]] | None=None, habits_set: Callable[[bool], Awaitable[bool]] | None=None) -> None:
        self.ins_of = ins_of
        self.extra = extra
        self.habits_set = habits_set

    async def _summary(self, ins: Insights) -> dict[str, Any]:
        return {**ins.summary(), **(await self.extra() if self.extra else {})}

    async def get(self, request):
        admin = request['hass_user'].is_admin
        if not admin and diag_visible(request.app[KEY_HASS]) != DIAG_ALL:
            return self.json_message('admin only', 403)
        ins = self.ins_of()
        if ins is None:
            return self.json_message('not ready', 409)
        entity = request.query.get('entity')
        offset = request.query.get('offset', '0')
        limit = request.query.get('limit', str(list_max()))
        if entity is not None and (not entity_id_cfg().match(entity)) or not offset_re().fullmatch(offset) or int(offset) > offset_max():
            return self.json_message('entity / offset', 400)
        if not limit_re().fullmatch(limit) or not 1 <= int(limit) <= limit_max():
            return self.json_message(f'limit: 1..{limit_max()}', 400)
        out = ins.states(entity, int(offset), int(limit)) if not ins.off else {'total': 0, 'offset': int(offset), 'items': [], 'off': True}
        if int(offset) == 0 and entity is None:
            out['summary'] = await self._summary(ins)
        return self.json({**out, 'sections': sections(), 'sectionOrder': section_order(), 'tickS': tick(), 'canEdit': admin})

    async def post(self, request):
        user = request['hass_user']
        if not user.is_admin:
            return self.json_message('admin only', 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        ins = self.ins_of()
        if ins is None:
            return self.json_message('not ready', 409)
        if isinstance(body, dict) and set(body) == {'habits'}:
            if not isinstance(body['habits'], bool) or self.habits_set is None:
                return self.json_message('{habits: true | false} expected', 400)
            if not await self.habits_set(body['habits']):
                return self.json_message('habits not running', 503)
            return self.json({'summary': await self._summary(ins)})
        if not isinstance(body, dict) or not {'entity', 'kind', 'action'} <= set(body) <= {'entity', 'kind', 'action', 'factor'}:
            return self.json_message('{entity, kind, action, factor?} expected', 400)
        entity, kind, action, factor = (body['entity'], body['kind'], body['action'], body.get('factor'))
        if not isinstance(entity, str) or not entity_id_cfg().match(entity) or (not isinstance(kind, str)) or (kind not in ins.owner) or (not isinstance(action, str)) or (action not in ACTIONS_IN):
            return self.json_message('entity / kind / action', 400)
        if factor is not None and (action != 'follow' or isinstance(factor, bool) or (not isinstance(factor, (int, float))) or (not factor_min() <= float(factor) <= factor_max())):
            return self.json_message(f'factor: {factor_min()}..{factor_max()} with follow', 400)
        ins.choose(kind, entity, action, user.name or user.id, float(factor) if factor is not None else None)
        return self.json(ins.states(entity))
