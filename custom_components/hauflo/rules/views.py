"""HTTP view of the rules."""
from __future__ import annotations
from collections.abc import Callable
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from homeassistant.components.http import HomeAssistantView
from ..derived import Invalid
from ..house import refuse
from .const import ADMIN_OPS, OPS
from .spec import client_can
if TYPE_CHECKING:
    from .engine import Rules

class RulesView(HomeAssistantView):
    """GET: the rules (admin — everything; a client — what he may see); POST op …. Through the tunnel (integrator)."""
    url = '/api/hauflo/rules'
    name = 'api:hauflo:rules'
    requires_auth = True

    def __init__(self, engine_of: Callable[[], Rules | None]) -> None:
        self.engine_of = engine_of

    async def get(self, request):
        engine = self.engine_of()
        if engine is None:
            return self.json_message('Hauflo agent not set up', HTTPStatus.SERVICE_UNAVAILABLE)
        user = request['hass_user']
        admin = bool(user and user.is_admin)
        tunnel = bool(user and user.system_generated)
        if (rid := request.query.get('log')) is not None:
            rule = engine.rules.get(rid)
            if rule is None or not (admin or rule['client'].get('visible', True)):
                return self.json_message('no such rule', HTTPStatus.NOT_FOUND)
            return self.json({'id': rid, 'log': engine.log_of(rid, tunnel)})
        return self.json(engine.answer(admin, tunnel))

    async def post(self, request):
        engine = self.engine_of()
        if engine is None:
            return self.json_message('Hauflo agent not set up', HTTPStatus.SERVICE_UNAVAILABLE)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', HTTPStatus.BAD_REQUEST)
        if not isinstance(body, dict) or body.get('op') not in OPS:
            return self.json_message(f"op: {' / '.join(OPS)}", HTTPStatus.BAD_REQUEST)
        user = request['hass_user']
        client = not (user and user.is_admin)
        op = body['op']
        if (op.startswith('derived') or op in ADMIN_OPS) and client:
            return self.json_message('admin only', HTTPStatus.FORBIDDEN)
        if op != 'preview' and client and ((no := (await refuse(request))) is not None):
            return no
        try:
            return self.json(await self._op(engine, op, body, client, user.id if user else None, bool(user and user.system_generated)))
        except Invalid as err:
            return self.json(engine.refusal(err), HTTPStatus.FORBIDDEN if err.code == 'not_allowed' else HTTPStatus.BAD_REQUEST)

    @staticmethod
    async def _op(engine: Rules, op: str, body: dict[str, Any], client: bool, user_id: str | None, tunnel: bool=False) -> dict[str, Any]:
        if op == 'preview':
            return await engine.preview(body.get('rule'), client=client, tunnel=tunnel)
        if op == 'save':
            return await engine.save(body.get('rule'), client=client, user_id=user_id, tunnel=tunnel)
        if op == 'quiet':
            return {'quiet': await engine.set_quiet(body.get('quiet'))}
        if op == 'bus_reads':
            return await engine.set_bus_reads(body.get('rules'))
        if op == 'derived_save':
            return await engine.save_derived(body.get('derived'), tunnel=tunnel)
        if op == 'derived_delete':
            await engine.delete_derived(body.get('id'))
            return {'deleted': body.get('id')}
        rule = engine.existing_rule(body.get('id'))
        if op == 'delete':
            if client and rule.get('owner') != 'client':
                raise Invalid('not_allowed', 'rules_delete_not_allowed')
            await engine.delete(rule['id'])
            return {'deleted': rule['id']}
        if op == 'enable':
            if client and rule.get('owner') != 'client' and (not client_can(rule)['toggle']):
                raise Invalid('not_allowed', 'rules_toggle_not_allowed')
            return await engine.set_enabled(rule['id'], body.get('enabled'), tunnel)
        return await engine.tune(rule['id'], body, client=client, tunnel=tunnel)
