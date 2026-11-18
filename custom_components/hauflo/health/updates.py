"""An update of HA core, its OS or Supervisor is out — the Supervisor's own update entities (`hassio` platform, found
by their unique ids in `update_ids`, never by name). HA Container / Core have none: the check isn't shown there."""
from __future__ import annotations
from typing import Any
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from .common import INFO, KEEP, OK, Result, cfg
NAME = 'updates'
PLATFORM = 'hassio'

async def check(hass: HomeAssistant, prev: dict[str, Any] | None, now: float) -> Result:
    reg = er.async_get(hass)
    found = False
    items: list[dict[str, Any]] = []
    for part, uid in cfg()['update_ids'].items():
        eid = reg.async_get_entity_id('update', PLATFORM, uid)
        st = hass.states.get(eid) if eid else None
        if st is None or st.state not in (STATE_ON, STATE_OFF):
            continue
        found = True
        if st.state == STATE_ON:
            items.append({'part': part, 'installed': str(st.attributes.get('installed_version') or ''), 'latest': str(st.attributes.get('latest_version') or '')})
    if not found:
        return KEEP if prev else None
    return {'level': INFO if items else OK, 'items': items}
