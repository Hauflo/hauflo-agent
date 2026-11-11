"""The door's entities (one device per door, under the «Hauflo» service device): `camera.<door>` (the panel's video),
`camera.<door>_talk` (the call's sound both ways through HA's go2rtc — the card's WebRTC sendrecv), `event.<door>_call`
(a doorbell: its ring is the push), `sensor.<door>_intercom` (idle / ringing / talking / answered_elsewhere and the
plate's diagnosis), `button.<door>_open` (the door's opening — only here and never from a push)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from ..const import DOMAIN
from .door import STATES, Door
from .manager import Intercom, unique_id

# Big, changing, or only for the plate: shown, not kept in the history database.
_UNRECORDED = frozenset({"last_call", "last_open", "last_rejected", "audio", "listen", "camera", "talk_camera", "open_button", "rtp_bytes_in", "rtp_bytes_out", "last_register_at", "last_invite_at", "panel_contact", "public_address"})


class DoorEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    KIND = ""
    DOMAIN_ = ""
    SUFFIX = ""

    def __init__(self, mgr: Intercom, door: Door) -> None:
        self.mgr, self.door = mgr, door
        cfg = door.cfg
        self._attr_unique_id = unique_id(mgr.entry_id, cfg.id, self.KIND)
        self.entity_id = f"{self.DOMAIN_}.{cfg.id}{self.SUFFIX}"  # a suggestion: the registry keeps a renamed one
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{mgr.entry_id}_intercom_{cfg.id}")},
            name=cfg.name,
            manufacturer=cfg.maker or None,
            model=cfg.model or None,
            **({"suggested_area": area} if (area := _area_name(mgr, cfg.area)) else {}),
            **({"via_device_id": mgr.hub_device_id} if mgr.hub_device_id else {}),
        )

    async def async_added_to_hass(self) -> None:
        self.mgr.entities[(self.door.cfg.id, self.KIND)] = self.entity_id
        self.door.listeners.add(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        self.door.listeners.discard(self.async_write_ha_state)
        self.mgr.entities.pop((self.door.cfg.id, self.KIND), None)


class DoorCall(DoorEntity, EventEntity):
    """A ring of the door: the push to the phones (notify.py) — and nothing else of the call moves it (every state change
    of an event entity is a push; the call's other moments are the sensor and the bus event)."""

    KIND, DOMAIN_, SUFFIX = "call", "event", "_call"
    _attr_translation_key = "intercom_call"
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = ["ring"]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.door.rings.add(self._ring)

    async def async_will_remove_from_hass(self) -> None:
        self.door.rings.discard(self._ring)
        await super().async_will_remove_from_hass()

    def _ring(self, data: dict[str, Any]) -> None:
        self._trigger_event("ring", data)
        self.async_write_ha_state()


class DoorState(DoorEntity, SensorEntity):
    KIND, DOMAIN_, SUFFIX = "state", "sensor", "_intercom"
    _attr_translation_key = "intercom"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(STATES)
    _unrecorded_attributes = _UNRECORDED

    @property
    def native_value(self) -> str:
        return self.door.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.door.attributes()


class DoorOpen(DoorEntity, ButtonEntity):
    KIND, DOMAIN_, SUFFIX = "open", "button", "_open"
    _attr_translation_key = "intercom_open"

    async def async_press(self) -> None:
        ctx = getattr(self, "_context", None)
        await self.mgr.open(self.door, ctx.user_id if ctx is not None else None)


def _area_name(mgr: Intercom, area: str) -> str:
    """The wizard names the room by its HA area id, the options form by its name: HA's device wants the name."""
    if not area:
        return ""
    found = ar.async_get(mgr.hass).async_get_area(area)
    return found.name if found is not None else area


def entities(mgr: Intercom | None, platform: str) -> list[Entity]:
    """This platform's entities of every door (the cameras: camera.py — the camera component loads only with a door)."""
    if mgr is None:
        return []
    out: list[Entity] = []
    for door in mgr.doors.values():
        if platform == "event":
            out.append(DoorCall(mgr, door))
        elif platform == "sensor":
            out.append(DoorState(mgr, door))
        elif platform == "button" and (door.cfg.open or mgr.linked_lock(door.cfg.id)):
            out.append(DoorOpen(mgr, door))
    return out
