"""Free space where HA keeps its configuration (and its database, by default): `shutil.disk_usage` of the config directory — the same on HA OS, Supervised, Container and Core."""
from __future__ import annotations
import shutil
from typing import Any
from homeassistant.core import HomeAssistant
from .common import CRIT, GB, KEEP, OK, WARN, Result, cfg
NAME = 'disk'

def limits(total_gb: float) -> dict[str, float]:
    c = cfg()
    return {lvl: max(min(total_gb * c[f'disk_{lvl}_pct'] / 100, c[f'disk_{lvl}_cap_gb']), c[f'disk_{lvl}_floor_gb']) for lvl in (WARN, CRIT)}

async def check(hass: HomeAssistant, prev: dict[str, Any] | None, now: float) -> Result:
    try:
        usage = await hass.async_add_executor_job(shutil.disk_usage, hass.config.config_dir)
    except OSError:
        return KEEP
    return judge(usage.total / GB, usage.free / GB, prev)

def judge(total_gb: float, free_gb: float, prev: dict[str, Any] | None) -> dict[str, Any]:
    lim = limits(total_gb)
    level = CRIT if free_gb < lim[CRIT] else WARN if free_gb < lim[WARN] else OK
    was = (prev or {}).get('level')
    order = (OK, WARN, CRIT)
    if was in (WARN, CRIT) and order.index(was) > order.index(level) and (free_gb < lim[was] * cfg()['disk_clear_factor']):
        level = was
    return {'level': level, 'freeGb': round(free_gb, 1), 'totalGb': round(total_gb, 1), 'limitGb': round(lim[CRIT] if level == CRIT else lim[WARN], 1)}
