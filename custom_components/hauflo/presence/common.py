"""Presence simulation settings, errors and time helpers."""
from __future__ import annotations
import datetime as dt
import functools
from http import HTTPStatus
import re
from typing import Any
from homeassistant.core import State
from homeassistant.util import dt as dt_util
from .. import derived
from ..tuning import tuning
STORE_KEY = 'hauflo.presence'
_t = functools.cache(lambda: tuning()['presence'])
learn_days = functools.cache(lambda: _t()['learn_days'])
jitter = functools.cache(lambda: _t()['jitter_min'])
slot = functools.cache(lambda: _t()['slot_min'])
slots = functools.cache(lambda: _t()['slots'])
evening = functools.cache(lambda: tuple(_t()['evening']))
morning = functools.cache(lambda: tuple(_t()['morning']))
afternoon = functools.cache(lambda: tuple(_t()['afternoon']))
cap_end = functools.cache(lambda: _t()['cap_end'])
default_end = functools.cache(lambda: _t()['default_end'])
dark_before = functools.cache(lambda: _t()['dark_before_min'])
p_min = functools.cache(lambda: _t()['p_min'])
min_evenings = functools.cache(lambda: _t()['min_evenings'])
max_rooms = functools.cache(lambda: _t()['max_rooms'])
lights_per_room = functools.cache(lambda: _t()['lights_per_room'])
sun_brightness = functools.cache(lambda: _t()['sun_brightness'])
coalesce = functools.cache(lambda: _t()['coalesce_s'])
manual = functools.cache(lambda: _t()['manual_s'])
watch_off = functools.cache(lambda: _t()['watch_off_s'])
ours_after = functools.cache(lambda: _t()['ours_after_s'])
max_events = functools.cache(lambda: _t()['max_events'])
log_size = functools.cache(lambda: _t()['log_size'])
max_until_days = functools.cache(lambda: _t()['max_until_days'])
replan = functools.cache(lambda: tuple(_t()['replan']))
MODES = ('learned', 'sun')
DATE = re.compile('\\d{4}-\\d{2}-\\d{2}')
living = functools.cache(lambda: re.compile(_t()['living_words'], re.I))
max_skip = functools.cache(lambda: _t()['max_skip'])
max_ours = functools.cache(lambda: _t()['max_ours'])
DEFAULT: dict[str, Any] = {'enabled': False, 'until': None, 'lights': True, 'covers': True, 'mode': 'learned', 'since': None, 'by': None, 'log': [], 'lit': [], 'lit_at': {}, 'ours': [], 'skip_rooms': []}

class Invalid(ValueError):
    """A request the agent refuses (400)."""

class Refused(Invalid, derived.Refused):
    """A refusal a person can meet (not a request sent wrong): said in the page's language — translations presence_<code>."""

    def __init__(self, code: str, status: int=HTTPStatus.BAD_REQUEST, **ph: Any) -> None:
        derived.Refused.__init__(self, code, f'presence_{code}', status, **ph)

def _wall(t: dt.datetime) -> dt.datetime:
    return dt_util.as_local(t).replace(tzinfo=None)

def day_min(t: dt.datetime) -> tuple[dt.date, float]:
    """The day (04:00 → 04:00) a moment belongs to and its minutes from that day's 04:00."""
    w = _wall(t) - dt.timedelta(hours=4)
    return (w.date(), w.hour * 60 + w.minute + w.second / 60)

def rel_min(day: dt.date, t: dt.datetime) -> float:
    """Minutes of a moment from `day`'s 04:00 (negative before it)."""
    return (_wall(t) - dt.datetime.combine(day, dt.time(4))).total_seconds() / 60

def at(day: dt.date, minutes: float) -> dt.datetime:
    """`day`'s 04:00 + minutes, wall-clock in HA's time zone (a clock change keeps 19:40 at 19:40)."""
    return (dt.datetime.combine(day, dt.time(4)) + dt.timedelta(minutes=minutes)).replace(tzinfo=dt_util.get_default_time_zone())

def action_of(domain: str, st: State, dim: bool, pos: bool) -> tuple[str, int | None] | None:
    """A light on (brightness %) / off; a blind open / closed / at a position."""
    s = st.state
    if domain == 'light':
        if s == 'off':
            return ('off', None)
        if s != 'on':
            return None
        b = st.attributes.get('brightness')
        if dim and isinstance(b, (int, float)) and (not isinstance(b, bool)) and (b > 0):
            return ('on', max(1, min(100, round(b * 100 / 255))))
        return ('on', None)
    if domain == 'cover':
        if s not in ('open', 'closed'):
            return None
        p = st.attributes.get('current_position')
        if pos and isinstance(p, (int, float)) and (not isinstance(p, bool)):
            p = round(p)
            return ('close', None) if p <= 0 else ('open', None) if p >= 100 else ('position', int(p))
        return ('open', None) if s == 'open' else ('close', None)
    return None

def brief(ev: dict[str, Any]) -> dict[str, Any]:
    return {'entity_id': ev['entity_id'], 'do': ev['do'], 'value': ev['value']}
