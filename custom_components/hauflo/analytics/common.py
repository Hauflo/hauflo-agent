"""Analytics settings and the helpers shared by its detectors."""
from __future__ import annotations
import functools
import re
import statistics
from typing import TYPE_CHECKING, Any
from homeassistant.core import State
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from ..derived import NOT_A_STATE, SECONDS_PER_HOUR, num
from ..tuning import tuning
if TYPE_CHECKING:
    from ..insight.engine import Insights
_a = functools.cache(lambda: tuning()['analytics'])
period = functools.cache(lambda: _a()['period_s'])
discover = functools.cache(lambda: _a()['discover_s'])
max_series = functools.cache(lambda: _a()['max_series'])
budget_ms = functools.cache(lambda: _a()['tick_budget_ms'])
keep = functools.cache(lambda: _a()['keep'])
z = functools.cache(lambda: _a()['z'])
z_end = functools.cache(lambda: _a()['z_end'])
mad_k = functools.cache(lambda: _a()['mad_k'])
spread_floor = functools.cache(lambda: _a()['spread_floor'])
hold = functools.cache(lambda: _a()['hold'])
factor = functools.cache(lambda: _a()['factor'])
note_prefixes = functools.cache(lambda: tuple(_a()['note_prefixes']))
seasons = functools.cache(lambda: _a()['seasons'])
mid = functools.cache(lambda: seasons()[-1])
ANY = 'any'
days = functools.cache(lambda: _a()['buckets']['days'])
hour_span = functools.cache(lambda: _a()['buckets']['hour_span'])
levels = functools.cache(lambda: _a()['buckets']['levels'])
judge = functools.cache(lambda: _a()['buckets']['judge'])
corr_keep = functools.cache(lambda: _a()['corr_keep'])
loop_keep = functools.cache(lambda: _a()['loop_keep'])
loop_days = functools.cache(lambda: _a()['loop_days'])
loop_min_days = functools.cache(lambda: _a()['loop_min_days'])
loop_min_h = functools.cache(lambda: _a()['loop_min_h'])
loop_max_s = functools.cache(lambda: _a()['loop_max_h'] * SECONDS_PER_HOUR)
overshoot_s = functools.cache(lambda: _a()['overshoot_h'] * SECONDS_PER_HOUR)
peers_min = functools.cache(lambda: _a()['peers_min'])
loop_units = functools.cache(lambda: _a()['loop_units'])
loop_metrics = functools.cache(lambda: _a()['loop_metrics'])
cover = functools.cache(lambda: _a()['cover'])
max_catchup = functools.cache(lambda: _a()['max_catchup'])
sections = functools.cache(lambda: tuning()['insight']['sections'])

def _rx(v: Any) -> Any:
    return {k: _rx(x) for k, x in v.items()} if isinstance(v, dict) else re.compile(v) if isinstance(v, str) else v
specs = functools.cache(lambda: _a()['series'])
spec_cfg = functools.cache(lambda: {s['kind']: s for s in specs()})
match_cfg = functools.cache(lambda: {s['kind']: _rx(s['match']) for s in specs()})
to_cfg = functools.cache(lambda: {s['kind']: re.compile(s['to']) for s in specs() if 'to' in s})
norms = functools.cache(lambda: [{**n, 'match': _rx(n['match'])} for n in _a()['norms']])
corrs = functools.cache(lambda: [{**c, 'trigger': _rx(c['trigger']), 'to': re.compile(c['to'])} for c in _a()['correlations']])
readers = functools.cache(lambda: {d: {k: (re.compile(v) if v else None) if k in ('active', 'off') else v for k, v in r.items()} for d, r in _a()['readers'].items()})
loops = functools.cache(lambda: [{**lp, 'match': _rx(lp['match'])} for lp in _a()['loops']])
loop_types = functools.cache(lambda: {lp['type']: lp for lp in loops()})

def is_note(kind: str) -> bool:
    """An analytics hint (a «заметка» of the digest), not one of the trend detectors' findings."""
    return kind.startswith(note_prefixes())

def section_of(kind: str) -> str | None:
    return sections().get(kind)

def local(ts: float):
    return dt_util.as_local(dt_util.utc_from_timestamp(ts))

def day(ts: float) -> str:
    return local(ts).date().isoformat()

def matches(match: dict[str, Any], facts: dict[str, Any]) -> bool:
    for k, want in match.items():
        have = facts.get(k)
        if isinstance(want, re.Pattern):
            if not isinstance(have, str) or not want.search(have):
                return False
        elif callable(have):
            if bool(have()) != bool(want):
                return False
        elif have != want:
            return False
    return True

def robust(xs: list[float]) -> tuple[float, float]:
    """(median, spread): the spread is MAD × MAD_K (≈ σ of a normal series), floored by SPREAD_FLOOR of the median — a series that never moves is not «off» by a hair."""
    m = statistics.median(xs)
    mad = statistics.median((abs(x - m) for x in xs))
    return (m, max(mad_k() * mad, spread_floor() * abs(m), 1e-09))

def value_of(spec: dict[str, Any], st: State | None) -> float | None:
    """The state in the spec's unit (`units` factor, `minus` first: °F → °C); None — no number, or a unit it doesn't know."""
    if st is None or st.state in NOT_A_STATE:
        return None
    x = num(st.state)
    if x is None:
        return None
    units = spec.get('units')
    if units is None:
        return x
    unit = st.attributes.get('unit_of_measurement')
    if unit not in units:
        return None
    return (x - (spec.get('minus') or {}).get(unit, 0)) * units[unit]

class Facts:
    """What a reference table matches an entity by: domain, device class, state class, unit, integration, model."""

    def __init__(self, ctx: Insights) -> None:
        self.ctx = ctx

    def of(self, st: State, **more: Any) -> dict[str, Any]:
        a = st.attributes
        entry = self.ctx.entry(st.entity_id)
        dev = dr.async_get(self.ctx.hass).async_get(entry.device_id) if entry and entry.device_id else None
        return {'domain': st.domain, 'device_class': a.get('device_class') or (entry.original_device_class if entry else None), 'state_class': a.get('state_class'), 'unit': a.get('unit_of_measurement'), 'platform': entry.platform if entry else None, 'model': dev.model if dev else None, 'manufacturer': dev.manufacturer if dev else None, **more}
