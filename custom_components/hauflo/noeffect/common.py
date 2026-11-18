"""Limits, kinds and words of the no-effect detector."""
from __future__ import annotations
import functools
import re
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from ..derived import SECONDS_PER_HOUR
from ..tuning import tuning
_n = functools.cache(lambda: tuning()['noeffect'])
min_share = functools.cache(lambda: _n()['min_share'])
hold = functools.cache(lambda: _n()['hold_windows'])
repeat_s = functools.cache(lambda: _n()['repeat_h'] * SECONDS_PER_HOUR)
sample_s = functools.cache(lambda: _n()['sample_s'])
pairs_every = functools.cache(lambda: _n()['pairs_every_s'])
active_cfg = functools.cache(lambda: set(_n()['active_states']))
exclude = functools.cache(lambda: re.compile(_n()['exclude_words'], re.I))
kinds_cfg = functools.cache(lambda: {k: tuning()[f'noeffect_{k}'] for k in _n()['kinds']})
KIND = 'no_effect'
effect_kinds = functools.cache(lambda: {'air', 'floor'} & set(kinds_cfg()))
_TAIL = re.compile('(\\d+)(?!.*\\d)')

def words(pattern: str) -> re.Pattern[str] | None:
    return re.compile(pattern, re.I) if pattern else None

def text(st: State) -> str:
    return f"{st.attributes.get('friendly_name') or ''} {st.entity_id}"

def integration(hass: HomeAssistant, eid: str) -> dict[str, str]:
    entry = er.async_get(hass).async_get(eid)
    if entry is None:
        return {'platform': '', 'integration': ''}
    ce = hass.config_entries.async_get_entry(entry.config_entry_id) if entry.config_entry_id else None
    return {'platform': entry.platform, 'integration': (ce.title if ce else '') or entry.platform}

def tail(st: State) -> int | None:
    """The last number of the friendly name («Клапан 2.3» → 3, «Температура пола 3» → 3); none — not numbered."""
    m = _TAIL.search(str(st.attributes.get('friendly_name') or ''))
    return int(m.group(1)) if m else None
