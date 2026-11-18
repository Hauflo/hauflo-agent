"""What a rule's save is checked against: the target the rule may change, its entity, attribute, numbers, name."""
from __future__ import annotations
from collections.abc import Callable
import re
from typing import Any
from homeassistant.core import HomeAssistant
from ..derived import Invalid, num
from ..ownership import service_entity
from .const import ENTITY_RE
from .spec import C
_ATTR_RE = re.compile('^[a-z_]+$')

class Env:
    """What a save is checked against."""

    def __init__(self, hass: HomeAssistant, owns: Callable[[str], bool], own_raw: Callable[[str], bool], client: bool=False) -> None:
        self.hass, self.owns, self.own_raw, self.client = (hass, owns, own_raw, client)

    def may_change(self, eid: str) -> bool:
        return may_change(self.hass, eid, self.owns, self.own_raw)

def may_change(hass: HomeAssistant, eid: str, owns: Callable[[str], bool], own_raw: Callable[[str], bool]) -> bool:
    """A rule's target — one test for the picker (targets), a save (clean_action) and the moment it runs (_write): a
    valve only Hauflo's own raw one (it only closes), anything else Hauflo's own and never a lock / the alarm / a gate,
    and never «Служебное» (a rule's own on / off switch, «Зима / Лето автоматически» — a rule could switch itself
    off)."""
    domain = eid.split('.')[0]
    return (own_raw(eid) if domain == 'valve' else owns(eid)) and (not service_entity(hass, eid))

def valid_entity(v: Any, what: str) -> str:
    if not isinstance(v, str) or not ENTITY_RE.match(v):
        raise Invalid('bad_entity', 'rules_bad_entity', what=what)
    return v

def attribute(v: Any) -> str | None:
    if v is not None and (not (isinstance(v, str) and _ATTR_RE.match(v))):
        raise Invalid('bad_input', 'rules_bad_attribute')
    return v

def minutes(v: Any) -> float:
    if v is None:
        return 0.0
    f = num(v)
    if f is None or not 0 <= f <= C('limits', 'for_min'):
        raise Invalid('bad_time', 'rules_bad_time', n=C('limits', 'for_min'))
    return f

def number(v: Any, what: str) -> float:
    f = num(v)
    if f is None or abs(f) > C('limits', 'number_abs'):
        raise Invalid('bad_number', 'rules_bad_number', what=what, n=C('limits', 'number_abs'))
    return f

def valid_name(v: Any) -> str:
    if not isinstance(v, str) or not v.strip() or len(v) > C('limits', 'name_len') or (not v.isprintable()):
        raise Invalid('bad_name', 'rules_bad_name', n=C('limits', 'name_len'))
    return v.strip()
