"""Diagnostics settings and the helpers shared by every detector."""
from __future__ import annotations
from datetime import timedelta
import functools
import re
import statistics
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from ..derived import num
from ..signals.store import policy as policy_of
from ..tuning import tuning
STORE_KEY = 'hauflo.insights'
META = '_meta'
_t = functools.cache(lambda: tuning()['insight'])
tick = functools.cache(lambda: _t()['tick_s'])
save_delay = functools.cache(lambda: _t()['save_delay_s'])
list_max = functools.cache(lambda: _t()['list_max'])
gone_after = functools.cache(lambda: _t()['gone_after_s'])
gap_cfg = functools.cache(lambda: _t()['gap_c'])
reach = functools.cache(lambda: _t()['reach_c'])
learn_gap = functools.cache(lambda: _t()['learn_gap_c'])
samples_cfg = functools.cache(lambda: _t()['samples'])
rate_min_h = functools.cache(lambda: _t()['rate_min_h'])
learn_min_h = functools.cache(lambda: _t()['learn_min_h'])
learn_min = functools.cache(lambda: _t()['learn_min'])
min_h = functools.cache(lambda: _t()['min_h'])
factor = functools.cache(lambda: _t()['factor'])
flat_c = functools.cache(lambda: _t()['flat_c'])
flat_h = functools.cache(lambda: _t()['flat_h'])
modes = functools.cache(lambda: {'heat': set(_t()['heat_modes']), 'cool': set(_t()['cool_modes'])})
actions = functools.cache(lambda: {'heat': set(_t()['heat_actions']), 'cool': set(_t()['cool_actions'])})
opening = functools.cache(lambda: set(_t()['opening_classes']))
stuck_classes = functools.cache(lambda: set(_t()['stuck_classes']))
stuck_h = functools.cache(lambda: _t()['stuck_h'])
stuck_days = functools.cache(lambda: _t()['stuck_days'])
learn_days = functools.cache(lambda: _t()['learn_days'])
stuck_norm = functools.cache(lambda: _t()['stuck_norm_changes'])
air_handler_words = functools.cache(lambda: re.compile(_t()['air_handler_words'], re.IGNORECASE))
air_handler_rate = functools.cache(lambda: _t()['air_handler_rate_c_h'])
observe_h = functools.cache(lambda: _t()['observe_min_h'])
obs_eps = functools.cache(lambda: _t()['observe_min_eps'])
gap_band = functools.cache(lambda: _t()['gap_band_c'])
gap_like_min = functools.cache(lambda: _t()['gap_like_min'])
offset_max = functools.cache(lambda: _t()['states_offset_max'])
offset_re = functools.cache(lambda: re.compile(f'[0-9]{{1,{len(str(offset_max()))}}}'))
limit_max = functools.cache(lambda: _t()['states_limit_max'])
choices_max = functools.cache(lambda: _t()['choices_max'])
local_max = functools.cache(lambda: _t()['local_max'])
limit_re = functools.cache(lambda: re.compile(f'[0-9]{{1,{len(str(limit_max()))}}}'))
episodes_keep = functools.cache(lambda: _t()['episodes_keep'])
max_per_day = functools.cache(lambda: _t()['insight_max_per_day'])
diag_visible_cfg = functools.cache(lambda: tuning()['signals']['diag']['default'])
DIAG_ALL = 'all'
inputs_max = functools.cache(lambda: _t()['inputs_max'])
input_gas_max = functools.cache(lambda: _t()['input_addresses_max'])
sections = functools.cache(lambda: _t()['sections'])
section_order = functools.cache(lambda: _t()['section_order'])
factor_min = functools.cache(lambda: _t()['factor_min'])
factor_max = functools.cache(lambda: _t()['factor_max'])
FEATURE = 'insights'
OBSERVE_FEATURE = 'insight_switch'
DAY_S = timedelta(days=1).total_seconds()
USED, DROPPED = ('used', 'dropped:')
ACTIONS_IN = ('normal', 'follow', 'reset')
HEAT, COOL = ('heat', 'cool')
KIND = {HEAT: 'no_heat', COOL: 'no_cool'}
DIR_OF = {v: k for k, v in KIND.items()}
SIGN = {HEAT: 1, COOL: -1}
KEEP = ('dir', 'start', 'gap', 'target', 'mode', 'last_on', 'alerted', 'c0')

def iso(ts: float) -> str:
    return dt_util.utc_from_timestamp(ts).isoformat()

def round2(v: float) -> float:
    return round(v, 2)

def sane_line(line: dict[str, Any]) -> dict[str, Any]:
    """A ring line written before 0.38: a rate from an episode of seconds (60 °C/h over 0.02 h) is a sensor jump, not a speed — the line keeps its facts, the rate goes (the table shows «—»)."""
    hours = num(line.get('hours'))
    if 'rate' in line and (hours is None or hours < rate_min_h()):
        return {k: v for k, v in line.items() if k != 'rate'}
    return line

def learned_rates(v: Any) -> list[list[float | None]]:
    """Learned rates [rate, start gap]: a plain rate (learned before 0.32) has no gap known."""
    out: list[list[float | None]] = []
    for x in v if isinstance(v, list) else []:
        r, g = (x[0], x[1]) if isinstance(x, list) and len(x) == 2 else (x, None)
        if num(r) is not None and float(r) > 0:
            out.append([float(r), float(g) if num(g) is not None else None])
    return out

def median_rate(rates: list[list[float | None]], gap: float | None) -> float | None:
    """The median rate; with `gap` — only of the episodes from as far away (±GAP_BAND), fewer than GAP_LIKE_MIN of them: None (that band is still learning; an episode learned before 0.32 has no gap and counts for none)."""
    if gap is None:
        like = [r for r, _g in rates]
    else:
        like = [r for r, g in rates if g is not None and abs(g - gap) <= gap_band()]
        if len(like) < gap_like_min():
            return None
    if not like:
        return None
    m = statistics.median(like)
    return m if m > 0 else None

def diag_visible(hass: HomeAssistant | None) -> str:
    """Who reads the diagnosis now: the integrator's choice Hauflo pushed with the site's signals, else the default."""
    pushed = ((policy_of(hass) or {}).get('diag') or {}).get('visible')
    return pushed if isinstance(pushed, str) else diag_visible_cfg()
