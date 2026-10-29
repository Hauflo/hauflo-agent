"""The KNX group addresses Hauflo may send to — a security boundary owned by the house, never by the cloud.

Hauflo may only ASK (a refused write, or the deploy plan saying what the project writes): the ask waits in HA's
Repairs as one «Allow / Decline» with exactly what is added and what goes (integrator review 29.10, defect 4: a
reconfigure kept the previous project's ranges and the only fix was a text field). Nothing here grants by itself.
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback

from . import issues
from .const import CONF_RANGES, DATA_PENDING_RANGES

# What the ask would take away (allowed items no address of the project falls into), its project, what was declined.
DATA_PENDING_REMOVE = "hauflo_pending_ranges_remove"
DATA_PENDING_PROJECT = "hauflo_pending_ranges_project"
DATA_DECLINED = "hauflo_declined_ranges"
NOTIFICATION_ID = "hauflo_ranges"
# A request names at most this many items (a villa's project compacts to a few hundred spans).
MAX_ITEMS = 2048


def range_key(r: str) -> tuple[int, ...]:
    """«main/middle[/first[-last]]» in bus order; a whole group before its addresses."""
    nums = [int(p) if p.isdigit() else 999 for p in re.split(r"[/-]", r)]
    return tuple((nums + [-1, -1, -1])[:3])


def parse_ranges(value: Any) -> list[str]:
    """«1/1, 1/2/5 7/0/1-21» or a list → ["1/1", "1/2/5", "7/0/1-21"]: a main/middle group, one address or a span of
    one middle group's addresses (KNX review 27.10: Hauflo proposes the addresses it writes, not whole groups); KNX
    limits 0–31 / 0–7 / 0–255; anything else is dropped."""
    items = value if isinstance(value, list) else re.split(r"[\s,;]+", str(value or ""))
    out = set()
    for x in items:
        m = re.fullmatch(r"(\d{1,2})/(\d)(?:/(\d{1,3})(?:\s*-\s*(\d{1,3}))?)?", str(x).strip())
        if not m or int(m.group(1)) > 31 or int(m.group(2)) > 7:
            continue
        group = f"{int(m.group(1))}/{int(m.group(2))}"
        if m.group(3) is None:
            out.add(group)
            continue
        lo = int(m.group(3))
        hi = int(m.group(4)) if m.group(4) is not None else lo
        if hi > 255 or lo > hi:
            continue
        out.add(f"{group}/{lo}" if lo == hi else f"{group}/{lo}-{hi}")
    return sorted(out, key=range_key)


def expand(items: Any) -> set[int]:
    """Allowed items → the raw group addresses they cover."""
    out: set[int] = set()
    for item in parse_ranges(sorted(items) if isinstance(items, (set, frozenset, tuple)) else items):
        nums = [int(p) for p in re.split(r"[/-]", item)]
        base = (nums[0] << 11) | (nums[1] << 8)
        lo, hi = (0, 255) if len(nums) == 2 else (nums[2], nums[3] if len(nums) == 4 else nums[2])
        out.update(base | s for s in range(lo, hi + 1))
    return out


def compress(raws: set[int]) -> list[str]:
    """Raw group addresses → «1/1/5», «1/1/1-21», «1/2» (a whole middle group), in bus order."""
    out: list[str] = []
    by_group: dict[int, list[int]] = {}
    for raw in raws:
        by_group.setdefault(raw >> 8, []).append(raw & 0xFF)
    for g in sorted(by_group):
        main, middle, subs = g >> 3, g & 7, sorted(by_group[g])
        if len(subs) == 256:
            out.append(f"{main}/{middle}")
            continue
        start = prev = subs[0]
        for s in [*subs[1:], None]:
            if s is not None and s == prev + 1:
                prev = s
                continue
            out.append(f"{main}/{middle}/{start}" if start == prev else f"{main}/{middle}/{start}-{prev}")
            if s is not None:
                start = prev = s
    return out


def count(items: Any) -> int:
    return len(expand(items))


def pending(hass: HomeAssistant) -> set[str]:
    return hass.data.setdefault(DATA_PENDING_RANGES, set())


def state(hass: HomeAssistant) -> dict[str, Any]:
    """What the plan reads: waiting adds, what they'd take away, what the owner declined."""
    add = sorted(pending(hass), key=range_key)
    return {
        "pending": add,
        "pendingCount": count(add),
        "remove": sorted(hass.data.get(DATA_PENDING_REMOVE) or set(), key=range_key),
        "declined": sorted(hass.data.get(DATA_DECLINED) or set(), key=range_key),
    }


@callback
def sync(hass: HomeAssistant) -> None:
    """The Repairs issue and the bell follow what waits; nothing waits — both go."""
    add = pending(hass)
    if not add:
        hass.data.pop(DATA_PENDING_REMOVE, None)
        hass.data.pop(DATA_PENDING_PROJECT, None)
        persistent_notification.async_dismiss(hass, NOTIFICATION_ID)
    issues.sync_ranges(hass, add, str(hass.data.get(DATA_PENDING_PROJECT) or ""))


@callback
def notify(hass: HomeAssistant) -> None:
    add = sorted(pending(hass), key=range_key)
    if not add:
        return
    ru = str(hass.config.language or "").startswith("ru")
    persistent_notification.async_create(
        hass,
        (f"Hauflo просит разрешить отправлять команды в групповые адреса KNX {', '.join(add)}. Разрешите или отклоните в один клик: [Настройки → Ремонт](/config/repairs). Ворота, замки и чужие группы не разрешайте."
         if ru else f"Hauflo asks to send commands to KNX group addresses {', '.join(add)}. Allow or decline in one click: [Settings → Repairs](/config/repairs). Don't allow gates, locks or others' groups."),
        "Hauflo",
        NOTIFICATION_ID,
    )


@callback
def ask(hass: HomeAssistant, items: set[str]) -> None:
    """A write the agent refused: its addresses join what waits (never a whole group around them)."""
    before = set(pending(hass))
    pending(hass).update(items)
    sync(hass)
    if pending(hass) != before:
        notify(hass)


@callback
def request(hass: HomeAssistant, allowed_now: set[str], wanted: Any, project: str) -> dict[str, Any]:
    """The plan sends everything the project writes: what isn't allowed yet waits for the owner (declined ones aren't
    asked again until HA restarts — Configure still allows them), and allowed items none of the project's addresses
    fall into are offered for removal with it (a previous project's). Never grants."""
    items = parse_ranges(wanted if isinstance(wanted, list) else [])[:MAX_ITEMS]
    want = expand(items)
    declined = expand(hass.data.get(DATA_DECLINED) or set())
    add = set(compress(want - expand(allowed_now) - declined))
    remove = {r for r in parse_ranges(sorted(allowed_now)) if not (expand([r]) & want)} if add else set()
    before = (set(pending(hass)), set(hass.data.get(DATA_PENDING_REMOVE) or set()))
    hass.data[DATA_PENDING_RANGES] = add
    hass.data[DATA_PENDING_REMOVE] = remove
    hass.data[DATA_PENDING_PROJECT] = str(project or "")[:100]
    sync(hass)
    if (add, remove) != before:
        notify(hass)
    return state(hass)


@callback
def resolve(hass: HomeAssistant, entry: Any, allowed_now: set[str], allow: bool, add: list[str], remove: list[str]) -> None:
    """The owner's one click in Repairs on exactly what the form SHOWED (security: an ask changed by the cloud between
    the form and the click grants nothing new): allow — those addresses join the options and the removals named go;
    decline — all stays as it is and the same addresses aren't asked again. What else waits stays in Repairs."""
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
    """A new project linked: what the old one asked or the owner declined no longer applies."""
    for key in (DATA_PENDING_REMOVE, DATA_PENDING_PROJECT, DATA_DECLINED):
        hass.data.pop(key, None)
    hass.data[DATA_PENDING_RANGES] = set()
    sync(hass)
