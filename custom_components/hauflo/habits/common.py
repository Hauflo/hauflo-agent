"""Limits of the habits and the fit of a time cluster."""
from __future__ import annotations
import datetime as dt
import functools
import logging
import re
import statistics
from homeassistant.util import dt as dt_util
from ..tuning import tuning
_LOGGER = logging.getLogger(__name__)
STORE_KEY = 'hauflo.habits'
t_cfg = functools.cache(lambda: tuning()['habits'])
DOMAINS = ('light', 'cover', 'scene', 'fan', 'switch', 'climate')
NOT_A_STATE = ('unavailable', 'unknown')
DAY = int(dt.timedelta(days=1).total_seconds()) // 60
CLASSES = {'all': range(7), 'workdays': range(5), 'weekend': range(5, 7)}
DAY_NAMES = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
KEY = re.compile('habit_[0-9a-f]{10}')
DEFAULT = {'enabled': True, 'dismissed': [], 'days': [], 'events': {}, 'auto': {}}

def now() -> dt.datetime:
    return dt_util.now()

def wrap(delta: float) -> float:
    """A minute-of-day difference as -720…720 (23:55 and 00:05 are 10 minutes apart)."""
    return (delta + DAY / 2) % DAY - DAY / 2

def median_fit(values: list[float], circular: bool) -> tuple[float, float, int]:
    """The median, its median absolute deviation and how many values lie within ±window_min of it."""
    if circular:
        c = values[0]
        rel = [wrap(v - c) for v in values]
        med = statistics.median(rel)
        dev = [abs(r - med) for r in rel]
        center = (c + med) % DAY
    else:
        center = statistics.median(values)
        dev = [abs(v - center) for v in values]
    return (center, statistics.median(dev), sum((1 for d in dev if d <= t_cfg()['window_min'])))
