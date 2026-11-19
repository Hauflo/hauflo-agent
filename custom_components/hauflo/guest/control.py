"""What a guest may see and press, checked per request."""
from __future__ import annotations
import asyncio
import hmac
import logging
from typing import Any
from homeassistant.core import Context
from homeassistant.helpers import area_registry as ar
from ..natural import natural_key
from ..notify.common import label
from .common import ACTIONS, COVER_FEATURE, DEVICE, TOKEN, Window, clean, default_devices, issuer_ok, may_control, rate_act, rate_act_device, rate_state, rate_state_device, secret_hash, temp_band
_LOGGER = logging.getLogger(__name__)

class GuestControlMixin:
    """Serves the guest's requests within the granted rooms."""

    async def handle(self, body: Any) -> tuple[int, dict[str, Any]]:
        """What the guest's page asked: {token, device, op: state | act, entity_id?, action?, value?}."""
        if not isinstance(body, dict) or set(body) - {'token', 'device', 'op', 'entity_id', 'action', 'value'}:
            return (400, {'code': 'bad_request'})
        token, device, op = (body.get('token'), body.get('device'), body.get('op'))
        if op not in ('state', 'act') or not isinstance(device, str) or (not DEVICE.fullmatch(device)):
            return (400, {'code': 'bad_request'})
        m = TOKEN.fullmatch(token) if isinstance(token, str) else None
        if self.bad.full() and (m is None or self._grant(m.group(1), m.group(2)) is None):
            return (429, {'code': 'slow_down'})
        grant = self._grant(m.group(1), m.group(2)) if m else None
        if grant is None:
            self.bad.add()
            return (404, {'code': 'invalid'})
        if (status := self._status(grant)) != 'active':
            return (410, {'code': status})
        issuer = await self.hass.auth.async_get_user(grant.get('by_id') or '')
        if issuer is None or not issuer.is_active or (not issuer_ok(issuer)):
            return (410, {'code': 'revoked'})
        dev = secret_hash(device)[:16]
        if dev not in grant['devices']:
            if len(grant['devices']) >= grant.get('max_devices', default_devices()):
                if (grant['id'], dev) not in self.refused:
                    self.refused.add((grant['id'], dev))
                    self._note({'grant': grant['id'], 'label': grant.get('label') or '', 'sys': 'device_refused'})
                return (403, {'code': 'device_limit'})
            grant['devices'].append(dev)
            self._note({'grant': grant['id'], 'label': grant.get('label') or '', 'sys': 'opened', 'n': len(grant['devices'])})
        mine = self.rates.setdefault((grant['id'], op, dev), Window(rate_act_device() if op == 'act' else rate_state_device()))
        link = self.rates.setdefault((grant['id'], op), Window(rate_act() if op == 'act' else rate_state()))
        if mine.full() or link.full():
            return (429, {'code': 'slow_down'})
        mine.add()
        link.add()
        allowed = self._allowed(grant, issuer)
        if op == 'state':
            return (200, self._view(grant, allowed))
        return await self._act(grant, allowed, body.get('entity_id'), body.get('action'), body.get('value'))

    def _grant(self, gid: str, secret: str) -> dict[str, Any] | None:
        want = secret_hash(secret)
        found = None
        for g in self.grants:
            if hmac.compare_digest(str(g.get('id')), gid) & hmac.compare_digest(str(g.get('hash')), want):
                found = g
        return found

    def _allowed(self, grant: dict[str, Any], issuer: Any) -> list[str]:
        rooms = set(grant.get('rooms', []))
        return [e for e in grant.get('entities', []) if self._safe(e) and self._area(e) in rooms and may_control(issuer, e)]

    def _item(self, eid: str) -> dict[str, Any]:
        st = self.hass.states.get(eid)
        a = st.attributes if st is not None else {}
        domain = eid.split('.')[0]
        out: dict[str, Any] = {'id': eid, 'name': clean(label(self.hass, eid, st) if st else eid, 60), 'domain': domain, 'state': st.state if st else 'unavailable'}
        if domain == 'light':
            modes = a.get('supported_color_modes') or []
            out['dim'] = any((mode != 'onoff' for mode in modes))
            b = a.get('brightness')
            out['brightness'] = round(b / 255 * 100) if isinstance(b, (int, float)) and st and (st.state == 'on') else None
        elif domain == 'cover':
            f = int(a.get('supported_features') or 0)
            out['can'] = [k for k, bit in COVER_FEATURE.items() if f & bit]
            pos = a.get('current_position')
            out['position'] = pos if isinstance(pos, (int, float)) else None
        else:
            lo, hi = self._temp_range(a)
            out.update({'target': a.get('temperature') if isinstance(a.get('temperature'), (int, float)) else None, 'current': a.get('current_temperature') if isinstance(a.get('current_temperature'), (int, float)) else None, 'min': lo, 'max': hi, 'step': 0.5, 'settable': bool(int(a.get('supported_features') or 0) & 1) and lo < hi})
        return out

    @staticmethod
    def _temp_range(a: Any) -> tuple[float, float]:
        lo = a.get('min_temp') if isinstance(a.get('min_temp'), (int, float)) else temp_band()[0]
        hi = a.get('max_temp') if isinstance(a.get('max_temp'), (int, float)) else temp_band()[1]
        return (max(float(lo), temp_band()[0]), min(float(hi), temp_band()[1]))

    def _view(self, grant: dict[str, Any], allowed: list[str]) -> dict[str, Any]:
        names = {a.id: a.name for a in ar.async_get(self.hass).async_list_areas()}
        rooms = []
        for r in grant.get('rooms', []):
            items = sorted((self._item(e) for e in allowed if self._area(e) == r), key=lambda i: natural_key(i['name']))
            if items:
                rooms.append({'name': clean(names.get(r, ''), 60), 'items': items})
        return {'label': grant.get('label') or '', 'expires': grant.get('expires'), 'timeZone': str(self.hass.config.time_zone or ''), 'rooms': rooms}

    async def _act(self, grant: dict[str, Any], allowed: list[str], eid: Any, action: Any, value: Any) -> tuple[int, dict[str, Any]]:
        if not isinstance(eid, str) or eid not in allowed:
            return (403, {'code': 'not_allowed'})
        if not isinstance(action, str) or action not in ACTIONS:
            return (400, {'code': 'bad_action'})
        domain, service = ACTIONS[action]
        if eid.split('.')[0] != domain:
            return (403, {'code': 'not_allowed'})
        st = self.hass.states.get(eid)
        a = st.attributes if st is not None else {}
        data: dict[str, Any] = {'entity_id': eid}
        num = value if isinstance(value, (int, float)) and (not isinstance(value, bool)) else None
        if action == 'on' and value is not None:
            if num is None or not 1 <= num <= 100 or (not any((mode != 'onoff' for mode in a.get('supported_color_modes') or []))):
                return (400, {'code': 'bad_value'})
            data['brightness_pct'] = round(num)
        elif action in COVER_FEATURE:
            if not int(a.get('supported_features') or 0) & COVER_FEATURE[action]:
                return (400, {'code': 'bad_action'})
            if action == 'position':
                if num is None or not 0 <= num <= 100:
                    return (400, {'code': 'bad_value'})
                data['position'] = round(num)
            elif value is not None:
                return (400, {'code': 'bad_value'})
        elif action == 'temperature':
            lo, hi = self._temp_range(a)
            if num is None or not int(a.get('supported_features') or 0) & 1 or (not lo <= num <= hi):
                return (400, {'code': 'bad_value'})
            data['temperature'] = round(num * 2) / 2
        elif value is not None:
            return (400, {'code': 'bad_value'})
        entry = {'grant': grant['id'], 'label': grant.get('label') or '', 'entity_id': eid, 'name': self._item(eid)['name'], 'do': action, 'value': data.get('brightness_pct', data.get('position', data.get('temperature')))}
        try:
            async with asyncio.timeout(10):
                await self.hass.services.async_call(domain, service, data, blocking=True, context=Context())
        except Exception as err:
            _LOGGER.warning('Hauflo guest: %s.%s on %s failed: %s', domain, service, eid, err)
            self._note({**entry, 'ok': False})
            return (502, {'code': 'failed'})
        self._note({**entry, 'ok': True})
        return (200, {'ok': True, 'item': self._item(eid)})
