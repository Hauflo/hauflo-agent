"""A rule's runtime state and its answer: the blank state, a value as shown, what the tunnel may not read."""
from __future__ import annotations
from typing import Any

def fmt(v: float | None, unit: str | None) -> str:
    if v is None:
        return '—'
    s = f'{v:.1f}'.rstrip('0').rstrip('.').replace('-', '−')
    return f'{s} {unit}' if unit else s

def blank() -> dict[str, Any]:
    return {'active': False, 'pend': None, 'hold_until': None, 'hold_why': None, 'paused': False, 'fault': None, 'restore': {}, 'holds': [], 'since': None, 'log': [], 'sug': None, 'confirmed': None, 'auto_at': {}, 'no_data': False, 'held': [], 'next_at': None, 'last_at': None, 'last_result': None, 'off_at': None}
_LIVE = ('state', 'active', 'value', 'why', 'why_en', 'since', 'hold_until', 'values', 'starts', 'periods', 'suggestion')

def redact(out: dict[str, Any]) -> dict[str, Any]:
    out = {**out, **{k: None for k in _LIVE if k in out}, 'hidden': True}
    if 'state' in out:
        out['state'] = 'hidden'
    if 'log' in out:
        out['log'] = [{'at': i.get('at'), 'event': 'hidden'} for i in out['log']]
    return out
