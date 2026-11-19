"""The rules canon (rules_canon.json): its values, the action table and what an action / a rule is by it."""
from __future__ import annotations
import functools
from typing import Any
from ..canon import rules_canon
from ..derived import num
from .const import AT, HOUSE_ACTS

def C(*path: str) -> Any:
    """A canon value (rules_canon.json)."""
    v: Any = rules_canon()
    for p in path:
        v = v[p]
    return v
option_domains = functools.cache(lambda: {d for d, acts in C('actions_by_domain').items() if any((a.get('value') == 'option' for a in acts.values()))})

def purpose(name: str) -> dict[str, Any]:
    return C('purposes', name)

def spec_of(eid: Any, do: Any) -> dict[str, Any] | None:
    """The canon's row of an entity action (None: out of the table)."""
    return C('actions_by_domain').get(eid.split('.')[0], {}).get(do) if isinstance(eid, str) else None

def act_spec(a: dict[str, Any]) -> dict[str, Any] | None:
    return spec_of(a.get('entity_id'), a.get('do'))

def slot_of(a: dict[str, Any]) -> str:
    """What two rules of one priority may not both hold on a target (power, position, setpoint…)."""
    if a['do'] in HOUSE_ACTS:
        return HOUSE_ACTS[a['do']]
    spec = act_spec(a)
    return spec['slot'] if spec else a['do']

def restores(a: dict[str, Any]) -> bool:
    """Its reverse is the value before (a speed, a setpoint, an option); a press / a pulse restores nothing."""
    spec = act_spec(a)
    return bool(spec and spec.get('restore'))

def restore_key(a: dict[str, Any]) -> str:
    """One remembered value per target and action (a fan's speed and its preset are two)."""
    return f"{a['entity_id']}:{a['do']}"

def powering(a: dict[str, Any]) -> bool:
    """May switch its target on (the interlock's and the safe state's)."""
    spec = act_spec(a)
    return bool(spec and spec.get('power') in ('on', 'value', 'toggle'))

def all_dos() -> set[str]:
    return {do for dos in C('actions_by_domain').values() for do in dos} | set(HOUSE_ACTS)

def scheduled(rule: dict[str, Any]) -> bool:
    return rule.get('when') == AT

def client_locked(rule: dict[str, Any]) -> bool:
    """A protective rule (safety priority: frost, leak) or one that works a valve is never the client's to switch or tune, whatever the integrator ticked (docs/acceptance.md C6: lock / alarm / valve / gate never; the engine itself never targets a lock, the alarm or a gate)."""
    return rule.get('priority') == 'safety' or any((str(a.get('entity_id', '')).startswith('valve.') for a in rule.get('actions') or []))

def client_can(rule: dict[str, Any]) -> dict[str, bool]:
    """What a client's card may offer on a rule the integrator left him: the switch, the threshold."""
    c, locked = (rule['client'], client_locked(rule))
    return {'toggle': c.get('toggle', True) is not False and (not locked), 'tune': 'lo' in c and (not locked)}

def bounds(spec: dict[str, Any], attrs: Any, fallback: Any) -> tuple[float, float]:
    """The entity's own min / max (its attributes named by the canon), else the canon's."""
    names = spec.get('bounds')
    lo, hi = (num(attrs.get(names[0])), num(attrs.get(names[1]))) if names else (None, None)
    return (lo, hi) if lo is not None and hi is not None and (lo <= hi) else (fallback[0], fallback[1])
