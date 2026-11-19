from __future__ import annotations
import functools
import json
import logging
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from .const import DOMAIN, VERSION
from .storage import HaufloStore
from .tuning import tuning
_LOGGER = logging.getLogger(__name__)
STATE = 'hauflo/panel/state'
STATES = ['current', 'refreshed', 'apply', 'update']
STORE_KEY = 'hauflo.panel'
SIGNAL = f'{DOMAIN}_panel'
REMERGED = 'remerged'
REMERGED_SAME = 'remerged_same'
max_reason = functools.cache(lambda: tuning()['panel']['reason_max'])
max_agent = functools.cache(lambda: tuning()['panel']['agent_max'])

def check(payload: Any) -> dict[str, Any] | None:
    """The payload as kept, or None when it is not one: a state of STATES, a short reason or none, the agent's version the cloud saw, an ISO time."""
    if not isinstance(payload, dict) or payload.get('state') not in STATES:
        return None
    reason, agent, at = (payload.get('reason'), payload.get('agent'), payload.get('at'))
    if reason is not None and (not isinstance(reason, str) or len(reason) > max_reason() or any((ord(c) < 32 or ord(c) == 127 for c in reason))):
        return None
    if agent is not None and (not isinstance(agent, str) or not agent or len(agent) > max_agent() or (not agent.isprintable())):
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

    async def remerged(self, changed: bool=True) -> None:
        """The agent merged its dashboards again on an update: they are what Hauflo has now."""
        await self.async_set({'state': 'current', 'reason': REMERGED if changed else REMERGED_SAME, 'agent': VERSION, 'at': dt_util.utcnow().isoformat()})

async def handle(agent: Any, ch: int, msg: dict[str, Any]) -> None:
    """`hauflo/panel/state`: answered here, never relayed to HA."""
    mid = msg.get('id')
    panel = getattr(agent, 'panel', None)
    ok = panel is not None and await panel.async_set({k: msg.get(k) for k in ('state', 'reason', 'agent', 'at')})
    frame: dict[str, Any] = {'id': mid, 'type': 'result', 'success': ok}
    if ok:
        frame['result'] = None
    else:
        frame['error'] = {'code': 'invalid_format', 'message': 'Hauflo agent: not a panel state'}
    await agent.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps(frame, ensure_ascii=False)})
