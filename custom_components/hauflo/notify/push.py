"""The payload of one alarm push and its buttons."""
from __future__ import annotations
from typing import Any
from homeassistant.core import callback
from .common import quiet_channel

def push_data(os: str | None, *, tag: str, critical: bool=False, quiet: bool=False, actions: list | None=None, silent: bool=False) -> dict[str, Any]:
    """The `data` of a push to the HA app — the engine's and the alarm automations' (independent.py) one shape."""
    data: dict[str, Any] = {'tag': tag}
    if actions:
        data['actions'] = actions
    if quiet:
        data.update({'push': {'interruption-level': 'passive'}} if os == 'ios' else {'alert_once': True})
    if silent and (not critical):
        data.update({'push': {'interruption-level': 'passive'}} if os == 'ios' else {'channel': quiet_channel(), 'importance': 'low'})
    if critical:
        if os == 'ios':
            data['push'] = {'sound': {'name': 'default', 'critical': 1, 'volume': 1.0}, 'interruption-level': 'critical'}
        else:
            data.update({'priority': 'high', 'ttl': 0, 'channel': 'alarm_stream', 'importance': 'high'})
    return data

def button(action: dict[str, Any], name: str) -> str:
    """Said by the house, not by the server: what the button does and to what (integrator run 22: a server's «Принял» on a button that turned the gate relay off)."""
    return f"{('Закрыть' if action['service'] == 'valve.close_valve' else 'Выключить')}: {name}"[:60]

@callback
def event_entity(data: Any) -> bool:
    """State changes of event entities only (a doorbell press)."""
    return str(data.get('entity_id') or '').startswith('event.')

@callback
def automation_event(data: Any) -> bool:
    """State changes of automations only (the engine's watch of the alarm rules' own, H-1)."""
    return str(data.get('entity_id') or '').startswith('automation.')
