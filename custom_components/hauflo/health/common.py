"""What every health check shares: the levels, the «keep the last word» answer and the tuning section."""
from __future__ import annotations
from datetime import UTC, datetime, timedelta
import functools
from typing import Any, Final
from ..const import DOMAIN
from ..tuning import tuning
FEATURE = 'health_checks'
STORE_KEY = f'{DOMAIN}.health'
cfg = functools.cache(lambda: tuning()['health'])
OK, INFO, WARN, CRIT = ('ok', 'info', 'warn', 'crit')
LEVELS: Final = (OK, INFO, WARN, CRIT)
DAY_S: Final = timedelta(days=1).total_seconds()
GB: Final = 1024 ** 3

class _Keep:

    def __repr__(self) -> str:
        return 'KEEP'
KEEP: Final = _Keep()
Result = dict[str, Any] | None | _Keep

def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts is not None else None

def num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and (not isinstance(v, bool)) else None
