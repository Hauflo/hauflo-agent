"""HTTP views that receive the signal policy and the choices made in HA."""
from __future__ import annotations
from typing import Any
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from .canon import MEANING_DEFAULT, entity_id_cfg, hash, meanings, signal_domains
from .policy import signals_in
from .store import evaluate_in, store

class SignalsView(HomeAssistantView):
    """GET → {"hash": <kept> | null, "applied": counts, "at"}; POST (admin only, Hauflo through its tunnel) {"signals": {scopes, entities, findings}, "hash": "<16 hex>"} → {"hash", "applied": false when that hash is kept already (nothing written), "counts", "dropped"}."""
    url = '/api/hauflo/signals'
    name = 'api:hauflo:signals'
    requires_auth = True

    async def get(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        s = store(request.app[KEY_HASS])
        if s is None:
            return self.json_message('not ready', 409)
        return self.json({'hash': s.hash, 'applied': s.counts(), 'at': s.at})

    async def post(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict) or set(body) != {'signals', 'hash'}:
            return self.json_message('{signals, hash} expected', 400)
        if not isinstance(body['hash'], str) or not hash().match(body['hash']):
            return self.json_message('hash: 16 hex expected', 400)
        got = signals_in(body['signals'])
        if got is None:
            return self.json_message("signals: {scopes, entities, findings} within the canon's limits expected", 400)
        s = store(request.app[KEY_HASS])
        if s is None:
            return self.json_message('not ready', 409)
        if body['hash'] == s.hash:
            return self.json({'hash': s.hash, 'applied': False, 'counts': s.counts(), 'dropped': got[1]})
        s.apply(got[0], body['hash'])
        return self.json({'hash': s.hash, 'applied': True, 'counts': s.counts(), 'dropped': got[1]})

class SignalChooseView(HomeAssistantView):
    url = '/api/hauflo/signals/choose'
    name = 'api:hauflo:signals:choose'
    requires_auth = True

    async def post(self, request):
        user = request['hass_user']
        if not user.is_admin:
            return self.json_message('admin only', 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict) or set(body) != {'entity', 'meaning'}:
            return self.json_message('{entity, meaning} expected', 400)
        eid, meaning = (body['entity'], body['meaning'])
        if not isinstance(eid, str) or not entity_id_cfg().match(eid) or eid.split('.', 1)[0] not in signal_domains():
            return self.json_message("entity: a signal's entity id", 400)
        if not isinstance(meaning, str) or (meaning not in meanings() and meaning != MEANING_DEFAULT):
            return self.json_message(f"meaning: one of {', '.join([*meanings(), MEANING_DEFAULT])}", 400)
        hass = request.app[KEY_HASS]
        s = store(hass)
        if s is None:
            return self.json_message('not ready', 409)
        s.choose(eid, meaning, user.name or user.id)
        st = hass.states.get(eid)
        ev = evaluate_in(hass, st) if st is not None else None
        out: dict[str, Any] = {'entity': eid, **s.own_of(eid)}
        if ev is not None:
            out.update({'problem': ev['problem'], 'house': ev['house'], 'alarm': ev['alarm']})
        return self.json(out)
