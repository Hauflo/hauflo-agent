"""The wizard's write plans: what they hold and how long."""
from __future__ import annotations
import time
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from .common import DEVICE_UPDATE, ENTITY_UPDATE, PLAN_KEY, max_ids, plan_ttl
from .flags import flags

def ids(v: Any, check: Any=None) -> set[str] | None:
    if not isinstance(v, list) or len(v) > max_ids() or (not all((isinstance(x, str) and x and (len(x) <= 255) for x in v))):
        return None
    if check is not None and (not all((check(x) for x in v))):
        return None
    return set(v)

def plan_ok(p: Any) -> dict[str, set[str]] | None:
    """{"areas": [area_id], "devices": [device_id], "entities": [entity_id]} — each list required (may be empty)."""
    if not isinstance(p, dict) or set(p) != {'areas', 'devices', 'entities'}:
        return None
    out = {'areas': ids(p['areas']), 'devices': ids(p['devices']), 'entities': ids(p['entities'], cv.valid_entity_id)}
    return out if all((v is not None for v in out.values())) else None

def active_plans(hass: HomeAssistant | None) -> dict[str, dict[str, Any]]:
    """The live plans by id (one idle past PLAN_TTL is dropped here)."""
    f = flags(hass)
    if f is None:
        return {}
    now = time.time()
    for pid in [k for k, p in f.plans.items() if now - float(p.get('at') or 0) > plan_ttl()]:
        f.plans.pop(pid, None)
    return f.plans

def _part(msg: dict[str, Any]) -> tuple[str, Any] | None:
    if msg.get('type') == DEVICE_UPDATE:
        return ('devices', msg.get('device_id'))
    if msg.get('type') == ENTITY_UPDATE:
        return ('entities', msg.get('entity_id'))
    return None

def holding(hass: HomeAssistant | None, msg: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The live plans that hold this message's item, the oldest first — only the one it names, when it names one."""
    ref = _part(msg)
    if ref is None:
        return []
    named = msg.get(PLAN_KEY) if PLAN_KEY in msg else None
    held = [(k, p) for k, p in active_plans(hass).items() if ref[1] in p[ref[0]] and (PLAN_KEY not in msg or k == named)]
    return sorted(held, key=lambda kp: float(kp[1].get('at') or 0))

def planned(hass: HomeAssistant | None, msg: dict[str, Any]) -> bool:
    """The item of this registry update is in a deploy's live plan — or the update names a plan (N7: then the wizard's
    rules alone decide, a gone plan refused with its reason, never the click's placing): the wizard's rules decide."""
    return PLAN_KEY in msg or bool(holding(hass, msg))

def drop_items(hass: HomeAssistant | None, devices: set[str], entities: set[str]) -> None:
    """These items leave every plan (put back by «Вернуть как было»: only a plan sent after writes them again)."""
    live = active_plans(hass)
    for pid, p in list(live.items()):
        p['devices'] = p['devices'] - devices
        p['entities'] = p['entities'] - entities
        if not p['devices'] and (not p['entities']):
            live.pop(pid, None)

def consume(hass: HomeAssistant, pid: str, msg: dict[str, Any]) -> None:
    """A plan's item written: gone from that plan (the same write again is refused), the plan kept alive."""
    live = active_plans(hass)
    plan, ref = (live.get(pid), _part(msg))
    if plan is None or ref is None:
        return
    plan[ref[0]] = plan[ref[0]] - {ref[1]}
    plan['at'] = time.time()
    if not plan['devices'] and (not plan['entities']):
        live.pop(pid, None)
