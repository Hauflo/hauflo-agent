from __future__ import annotations
import re
from typing import Any
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback
from . import issues
from .const import CONF_RANGES, DATA_PENDING_RANGES
from .tuning import TUNING
DATA_PENDING_REMOVE = 'hauflo_pending_ranges_remove'
DATA_PENDING_PROJECT = 'hauflo_pending_ranges_project'
DATA_DECLINED = 'hauflo_declined_ranges'
NOTIFICATION_ID = 'hauflo_ranges'
MAX_ITEMS = 2048
SHOWN_ITEMS = TUNING['ranges']['shown_items']

def range_key(r: str) -> tuple[int, ...]:
    nums = [int(p) if p.isdigit() else 999 for p in re.split('[/-]', r)]
    return tuple((nums + [-1, -1, -1])[:3])

def parse_ranges(value: Any) -> list[str]:
    items = value if isinstance(value, list) else re.split('[\\s,;]+', str(value or ''))
    out = set()
    for x in items:
        m = re.fullmatch('(\\d{1,2})/(\\d)(?:/(\\d{1,3})(?:\\s*-\\s*(\\d{1,3}))?)?', str(x).strip())
        if not m or int(m.group(1)) > 31 or int(m.group(2)) > 7:
            continue
        group = f'{int(m.group(1))}/{int(m.group(2))}'
        if m.group(3) is None:
            out.add(group)
            continue
        lo = int(m.group(3))
        hi = int(m.group(4)) if m.group(4) is not None else lo
        if hi > 255 or lo > hi:
            continue
        out.add(f'{group}/{lo}' if lo == hi else f'{group}/{lo}-{hi}')
    return sorted(out, key=range_key)

def expand(items: Any) -> set[int]:
    out: set[int] = set()
    for item in parse_ranges(sorted(items) if isinstance(items, (set, frozenset, tuple)) else items):
        nums = [int(p) for p in re.split('[/-]', item)]
        base = nums[0] << 11 | nums[1] << 8
        lo, hi = (0, 255) if len(nums) == 2 else (nums[2], nums[3] if len(nums) == 4 else nums[2])
        out.update((base | s for s in range(lo, hi + 1)))
    return out

def compress(raws: set[int]) -> list[str]:
    out: list[str] = []
    by_group: dict[int, list[int]] = {}
    for raw in raws:
        by_group.setdefault(raw >> 8, []).append(raw & 255)
    for g in sorted(by_group):
        main, middle, subs = (g >> 3, g & 7, sorted(by_group[g]))
        if len(subs) == 256:
            out.append(f'{main}/{middle}')
            continue
        start = prev = subs[0]
        for s in [*subs[1:], None]:
            if s is not None and s == prev + 1:
                prev = s
                continue
            out.append(f'{main}/{middle}/{start}' if start == prev else f'{main}/{middle}/{start}-{prev}')
            if s is not None:
                start = prev = s
    return out

def describe(items: Any) -> str:
    out: list[str] = []
    run: list[int] = []

    def close() -> None:
        if run:
            a, b = (run[0], run[-1])
            out.append(f'{a >> 3}/{a & 7}' if a == b else f'{a >> 3}/{a & 7} – {b >> 3}/{b & 7}')
            run.clear()
    for item in compress(expand(items)):
        nums = [int(p) for p in re.split('[/-]', item)]
        if len(nums) == 2:
            g = nums[0] << 3 | nums[1]
            if run and g != run[-1] + 1:
                close()
            run.append(g)
            continue
        close()
        out.append(item)
    close()
    if len(out) > SHOWN_ITEMS:
        return f"{', '.join(out[:SHOWN_ITEMS])} … +{len(out) - SHOWN_ITEMS}"
    return ', '.join(out)

def count(items: Any) -> int:
    return len(expand(items))

def pending(hass: HomeAssistant) -> set[str]:
    return hass.data.setdefault(DATA_PENDING_RANGES, set())

def state(hass: HomeAssistant) -> dict[str, Any]:
    add = sorted(pending(hass), key=range_key)
    return {'pending': add, 'pendingCount': count(add), 'remove': sorted(hass.data.get(DATA_PENDING_REMOVE) or set(), key=range_key), 'declined': sorted(hass.data.get(DATA_DECLINED) or set(), key=range_key)}

@callback
def sync(hass: HomeAssistant) -> None:
    add = pending(hass)
    if not add:
        hass.data.pop(DATA_PENDING_REMOVE, None)
        hass.data.pop(DATA_PENDING_PROJECT, None)
        persistent_notification.async_dismiss(hass, NOTIFICATION_ID)
    issues.sync_ranges(hass, add, str(hass.data.get(DATA_PENDING_PROJECT) or ''))

@callback
def notify(hass: HomeAssistant) -> None:
    add = sorted(pending(hass), key=range_key)
    if not add:
        return
    ru = str(hass.config.language or '').startswith('ru')
    persistent_notification.async_create(hass, f'Hauflo просит разрешить отправлять команды в групповые адреса KNX {describe(add)}. Разрешите или отклоните в один клик: [Настройки → Ремонт](/config/repairs). Ворота, замки и чужие группы не разрешайте.' if ru else f"Hauflo asks to send commands to KNX group addresses {describe(add)}. Allow or decline in one click: [Settings → Repairs](/config/repairs). Don't allow gates, locks or others' groups.", 'Hauflo', NOTIFICATION_ID)

@callback
def ask(hass: HomeAssistant, items: set[str]) -> None:
    before = set(pending(hass))
    pending(hass).update(items)
    sync(hass)
    if pending(hass) != before:
        notify(hass)

@callback
def request(hass: HomeAssistant, allowed_now: set[str], wanted: Any, project: str) -> dict[str, Any]:
    items = parse_ranges(wanted if isinstance(wanted, list) else [])[:MAX_ITEMS]
    want = expand(items)
    declined = expand(hass.data.get(DATA_DECLINED) or set())
    add = set(compress(want - expand(allowed_now) - declined))
    remove = {r for r in parse_ranges(sorted(allowed_now)) if not expand([r]) & want} if add else set()
    before = (set(pending(hass)), set(hass.data.get(DATA_PENDING_REMOVE) or set()))
    hass.data[DATA_PENDING_RANGES] = add
    hass.data[DATA_PENDING_REMOVE] = remove
    hass.data[DATA_PENDING_PROJECT] = str(project or '')[:100]
    sync(hass)
    if (add, remove) != before:
        notify(hass)
    return state(hass)

@callback
def resolve(hass: HomeAssistant, entry: Any, allowed_now: set[str], allow: bool, add: list[str], remove: list[str]) -> None:
    if allow:
        kept = [r for r in parse_ranges(sorted(allowed_now)) if r not in set(remove)]
        hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_RANGES: compress(expand(kept) | expand(add))})
    else:
        hass.data[DATA_DECLINED] = set(hass.data.get(DATA_DECLINED) or set()) | set(add)
    hass.data[DATA_PENDING_RANGES] = set(compress(expand(pending(hass)) - expand(add) - expand(allowed_now if allow else set())))
    if not pending(hass):
        hass.data.pop(DATA_PENDING_REMOVE, None)
    sync(hass)

@callback
def reset(hass: HomeAssistant) -> None:
    for key in (DATA_PENDING_REMOVE, DATA_PENDING_PROJECT, DATA_DECLINED):
        hass.data.pop(key, None)
    hass.data[DATA_PENDING_RANGES] = set()
    sync(hass)
