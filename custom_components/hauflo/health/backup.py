"""The age of the last backup — HA's own backup manager (HA ≥ 2025.1: `homeassistant.components.backup`), not a sensor:
the newest of the last automatic one and every backup in its list (a manual one counts, Hauflo's own pre-deploy ones
don't — settings only, no history). No backup at all: a fault once `backup_never_h` passed since it was first seen so
(a fresh house gets its first night). Levels: older than `backup_warn_days` — warn, `backup_crit_days` — crit."""
from __future__ import annotations
from typing import Any
from homeassistant.components.backup import async_get_manager
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from ..backup import is_ours
from .common import CRIT, DAY_S, KEEP, OK, WARN, Result, cfg, iso, num
NAME = 'backup'

async def check(hass: HomeAssistant, prev: dict[str, Any] | None, now: float) -> Result:
    try:
        manager = async_get_manager(hass)
    except HomeAssistantError:
        return None
    times: list[float] = []
    data = getattr(getattr(manager, 'config', None), 'data', None)
    auto = getattr(data, 'last_completed_automatic_backup', None)
    if auto is not None and hasattr(auto, 'timestamp'):
        times.append(auto.timestamp())
    try:
        backups, _agent_errors = await manager.async_get_backups()
    except HomeAssistantError:
        backups = None
    for b in (backups or {}).values():
        if is_ours(getattr(b, 'name', None)):
            continue
        at = dt_util.parse_datetime(getattr(b, 'date', '') or '')
        if at is not None:
            times.append(at.timestamp())
    if not times and backups is None:
        return KEEP
    return judge(max(times) if times else None, prev, now)

def judge(last: float | None, prev: dict[str, Any] | None, now: float) -> dict[str, Any]:
    if last is None:
        first = num((prev or {}).get('_first')) or now
        never = now - first >= cfg()['backup_never_h'] * 3600
        return {'level': CRIT if never else OK, 'lastAt': None, 'days': None, '_first': first}
    days = (now - last) / DAY_S
    level = CRIT if days > cfg()['backup_crit_days'] else WARN if days > cfg()['backup_warn_days'] else OK
    return {'level': level, 'lastAt': iso(last), 'days': int(days)}
