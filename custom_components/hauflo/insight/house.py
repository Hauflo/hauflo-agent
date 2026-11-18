"""Facts about the house that the detectors read."""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any
from homeassistant.const import STATE_ON
from homeassistant.helpers import area_registry as ar, entity_registry as er
from ..derived import num, outdoor
from ..notify.common import label
from ..ownership import entity_room
from .climate import ClimateEffect
from .common import COOL, HEAT, air_handler_rate, air_handler_words, input_gas_max, inputs_max, opening
if TYPE_CHECKING:
    from .detector import Detector
_LOGGER = logging.getLogger(__name__)

class InsightsHouseMixin:
    """Reads the house for the detectors."""

    def entry(self, eid: str) -> er.RegistryEntry | None:
        return er.async_get(self.hass).async_get(eid)

    def visible(self, eid: str, diagnostic: bool=False) -> bool:
        """Shown in HA: not disabled, not hidden; a diagnostic / config entity only for a detector that needs one (a
        battery, a link quality — diagnostic by nature). An entity HA's registry doesn't know (YAML) is shown."""
        entry = self.entry(eid)
        if entry is None:
            return True
        if entry.disabled_by is not None or entry.hidden_by is not None:
            return False
        return diagnostic or entry.entity_category is None

    def outdoor_c(self) -> float | None:
        """The street's temperature now, from the source the rules use (derived.outdoor); None — none known."""
        src = outdoor(self.hass, self.owns)
        st = self.hass.states.get(src['entity_id']) if src else None
        if st is None or src is None:
            return None
        return num(st.attributes.get(src['attribute'])) if src['attribute'] else num(st.state)

    def area_id(self, eid: str) -> str | None:
        return entity_room(self.hass, eid)

    def air_handler(self, eid: str) -> bool:
        """A climate of an air handler (AIR_HANDLER_WORDS / AIR_HANDLER_RATE): no «slower», no short cycling for it."""
        if not eid.startswith('climate.'):
            return False
        w = self.who(eid)
        if air_handler_words().search(f"{eid} {w['name']} {w['area']}"):
            return True
        ce = next((det for det in self.detectors if isinstance(det, ClimateEffect)), None)
        rates = [r for r in (ce.raw(eid, d) if ce else None for d in (HEAT, COOL)) if r is not None]
        return any((r >= air_handler_rate() for r in rates))

    def who(self, eid: str) -> dict[str, str]:
        st = self.hass.states.get(eid)
        area_id = self.area_id(eid)
        area = ar.async_get(self.hass).async_get_area(area_id) if area_id else None
        return {'name': label(self.hass, eid, st), 'area': area.name if area else ''}

    def opening_open(self, eid: str, ignore: set[str] | frozenset[str]=frozenset()) -> bool:
        """A window / door of the same room is open now (a room unknown: none known open). `ignore`: entities that are"""
        area_id = self.area_id(eid)
        if not area_id:
            return False
        return any((s.state == STATE_ON and s.entity_id not in ignore and (s.entity_id not in self.not_windows) and (s.attributes.get('device_class') in opening()) and (self.area_id(s.entity_id) == area_id) for s in self.hass.states.async_all('binary_sensor')))

    def windows_of(self, eid: str) -> list[str]:
        """The windows / doors of the entity's room the checks look at (an open one pauses them)."""
        area_id = self.area_id(eid)
        if not area_id:
            return []
        return sorted((s.entity_id for s in self.hass.states.async_all('binary_sensor') if s.entity_id not in self.not_windows and s.attributes.get('device_class') in opening() and (self.area_id(s.entity_id) == area_id)))

    def climate_inputs(self, eid: str) -> list[dict[str, Any]]:
        """A climate check's signals: the climate itself, its room's windows, the street's temperature, the season."""
        src = outdoor(self.hass, self.owns)
        return [{'role': 'self', 'entityId': eid}, *({'role': 'window', 'entityId': w} for w in self.windows_of(eid)), *([{'role': 'outdoor', 'entityId': src['entity_id']}] if src else []), {'role': 'season'}]

    def uses(self, det: Detector, kind: str, eid: str, addresses: dict[str, list[str]]) -> list[dict[str, Any]]:
        """`inputs` for the reader: each entity's name, room and KNX group addresses (what the integrator knows it
        by)."""
        out: list[dict[str, Any]] = []
        try:
            got = det.inputs(kind, eid)
        except Exception:
            _LOGGER.exception('Hauflo insights: %s inputs failed', det.name)
            got = [{'role': 'self', 'entityId': eid}]
        for i in got[:inputs_max()]:
            e = i.get('entityId')
            out.append({**i, **(self.who(e) if e else {}), **({'knx': addresses[e][:input_gas_max()]} if e and addresses.get(e) else {})})
        return out
