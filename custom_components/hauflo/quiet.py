"""Quiet hours of the house for the value rules' pushes (owner 30.10: a night CO₂ «проветрить» woke people up).

A window «from … to» in the house's local time; it may cross midnight (22:00–08:00). Numbers and the classes that are
never silenced (safety: leak, frost…) are data: rules_canon.json «quiet». Pure functions — rules.py decides what is held.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any

from homeassistant.util import dt as dt_util

from .canon import CANON
from .derived import SECONDS_PER_MINUTE as MINUTES_PER_HOUR, Invalid  # 60 either way

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _q() -> dict[str, Any]:
    return CANON["quiet"]


def default() -> dict[str, Any]:
    return dict(_q()["default"])


def minutes(hhmm: Any) -> int | None:
    m = _HHMM.match(hhmm) if isinstance(hhmm, str) else None
    return int(m[1]) * MINUTES_PER_HOUR + int(m[2]) if m else None


def clean(raw: Any, old: dict[str, Any]) -> dict[str, Any]:
    """The house's setting from a POST (only the keys sent change); «from» = «to» is no window — refused."""
    if not isinstance(raw, dict):
        raise Invalid("bad", "rules_bad_quiet")
    out = {**old}
    if "on" in raw:
        if not isinstance(raw["on"], bool):
            raise Invalid("bad", "rules_bad_quiet")
        out["on"] = raw["on"]
    for k in ("from", "to"):
        if k in raw:
            if minutes(raw[k]) is None:
                raise Invalid("bad", "rules_bad_quiet")
            out[k] = raw[k]
    if out["from"] == out["to"]:
        raise Invalid("bad", "rules_bad_quiet")
    return out


def inside(cfg: dict[str, Any], t: datetime) -> bool:
    """Is the local time `t` in the quiet window (off → never)? The start counts, the end does not."""
    if not cfg.get("on"):
        return False
    local = dt_util.as_local(t)
    now = local.hour * MINUTES_PER_HOUR + local.minute
    a, b = minutes(cfg["from"]), minutes(cfg["to"])
    if a is None or b is None or a == b:
        return False
    return a <= now < b if a < b else now >= a or now < b


def ends_at(cfg: dict[str, Any], t: datetime) -> datetime | None:
    """When the window `t` is in ends (None outside it)."""
    if not inside(cfg, t):
        return None
    local = dt_util.as_local(t)
    b = minutes(cfg["to"]) or 0
    end = local.replace(hour=b // MINUTES_PER_HOUR, minute=b % MINUTES_PER_HOUR, second=0, microsecond=0)
    return end if end > local else end + timedelta(days=1)
