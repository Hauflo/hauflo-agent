from __future__ import annotations
import json
import logging
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from .const import DOMAIN, VERSION
from .storage import HaufloStore
from .tuning import TUNING
_LOGGER = logging.getLogger(__name__)
STATE = 'hauflo/panel/state'
STATES = ['current', 'refreshed', 'apply', 'update']
STORE_KEY = 'hauflo.panel'
SIGNAL = f'{DOMAIN}_panel'
REMERGED = 'remerged'
MAX_REASON = TUNING['panel']['reason_max']
MAX_AGENT = TUNING['panel']['agent_max']

def check(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or payload.get('state') not in STATES:
        return None
    reason, agent, at = (payload.get('reason'), payload.get('agent'), payload.get('at'))
    if reason is not None and (not isinstance(reason, str) or len(reason) > MAX_REASON or any((ord(c) < 32 or ord(c) == 127 for c in reason))):
        return None
    if agent is not None and (not isinstance(agent, str) or not agent or len(agent) > MAX_AGENT or (not agent.isprintable())):
        return None
    if at is not None and (not isinstance(at, str) or dt_util.parse_datetime(at) is None):
        return None
    return {'state': payload['state'], 'reason': reason, 'agent': agent, 'at': at}

class Panel:

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = HaufloStore(hass, STORE_KEY)
        self.data: dict[str, Any] | None = None

    async def async_load(self) -> None:
        try:
            self.data = check(await self.store.async_load())
        except Exception:
            _LOGGER.warning("Hauflo: the panel's last state could not be read", exc_info=True)
            self.data = None

    async def async_set(self, payload: Any) -> bool:
        data = check(payload)
        if data is None:
            return False
        self.data = data
        await self.store.async_save(data)
        async_dispatcher_send(self.hass, SIGNAL)
        return True

    async def remerged(self) -> None:
        await self.async_set({'state': 'current', 'reason': REMERGED, 'agent': VERSION, 'at': dt_util.utcnow().isoformat()})

async def handle(agent: Any, ch: int, msg: dict[str, Any]) -> None:
    mid = msg.get('id')
    panel = getattr(agent, 'panel', None)
    ok = panel is not None and await panel.async_set({k: msg.get(k) for k in ('state', 'reason', 'agent', 'at')})
    frame: dict[str, Any] = {'id': mid, 'type': 'result', 'success': ok}
    if ok:
        frame['result'] = None
    else:
        frame['error'] = {'code': 'invalid_format', 'message': 'Hauflo agent: not a panel state'}
    await agent.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps(frame, ensure_ascii=False)})
