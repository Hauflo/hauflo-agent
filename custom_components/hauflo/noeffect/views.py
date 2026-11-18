"""HTTP view of the executor and sensor pairs."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any
from homeassistant.components.http import HomeAssistantView
from .common import effect_kinds, kinds_cfg
if TYPE_CHECKING:
    from .engine import PairEffect

class PairsView(HomeAssistantView):
    """      {"set": {"effect": entity_id, "kind": "floor|air|co2|humidity", "exec": entity_id | ""}} — this executor is this
      effect's;
      {"clear": entity_id} — back to what the mapping finds;"""
    url = '/api/hauflo/pairs'
    name = 'api:hauflo:pairs'
    requires_auth = True

    def __init__(self, det_of) -> None:
        self.det_of = det_of

    def _fresh(self) -> PairEffect | None:
        det = self.det_of()
        if det is not None:
            det.refresh(det.ctx.clock())
        return det

    async def get(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        det = self._fresh()
        if det is None:
            return self.json_message('not ready', 409)
        return self.json(self._view(det))

    @staticmethod
    def _loops(det: PairEffect):
        lt = det.ctx.owner.get('loop_slow')
        return lt if lt is not None and hasattr(lt, 'supply_view') else None

    def _view(self, det: PairEffect) -> dict[str, Any]:
        lt = self._loops(det)
        return {**det.pairs_view(), 'supply': lt.supply_view() if lt is not None else []}

    async def post(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        det = self.det_of()
        if det is None:
            return self.json_message('not ready', 409)
        ok_id = lambda v: isinstance(v, str) and 0 < len(v) <= 255 and ('.' in v)
        if isinstance(body, dict) and set(body) == {'set'} and isinstance(body['set'], dict):
            b = body['set']
            if set(b) != {'effect', 'kind', 'exec'} or not ok_id(b['effect']) or b['kind'] not in kinds_cfg() or (not (b['exec'] == '' or ok_id(b['exec']))):
                return self.json_message('set: {effect, kind, exec} expected', 400)
            for eid in (b['effect'], b['exec']):
                if eid and det.ctx.hass.states.get(eid) is None:
                    return self.json_message(f'unknown_entity: {eid}', 400)
            det.set_pair(b['effect'], b['kind'], b['exec'])
        elif isinstance(body, dict) and set(body) == {'clear'} and ok_id(body['clear']):
            det.clear_pair(body['clear'])
        elif isinstance(body, dict) and set(body) == {'kinds'} and isinstance(body['kinds'], dict):
            k = body['kinds']
            if len(k) > 5000 or not all((ok_id(e) and e.startswith('climate.') and (v in effect_kinds()) for e, v in k.items())):
                return self.json_message('kinds: {climate entity_id: air | floor} expected', 400)
            det.set_kinds(k)
        elif isinstance(body, dict) and set(body) == {'supply'} and isinstance(body['supply'], dict):
            b = body['supply']
            lt = self._loops(det)
            if set(b) != {'loop', 'sensor'} or not ok_id(b['loop']) or (not (b['sensor'] == '' or ok_id(b['sensor']))) or (lt is None):
                return self.json_message('supply: {loop, sensor} expected', 400)
            if b['loop'] not in {x['loop'] for x in lt.supply_view()}:
                return self.json_message(f"not_a_loop: {b['loop']}", 400)
            if b['sensor'] and det.ctx.hass.states.get(b['sensor']) is None:
                return self.json_message(f"unknown_entity: {b['sensor']}", 400)
            lt.set_supply(b['loop'], b['sensor'])
        else:
            return self.json_message('set / clear / kinds / supply expected', 400)
        return self.json(self._view(self._fresh()))
