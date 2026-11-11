"""Р55: the doors' cameras — `camera.<door>` (the panel's video) and `camera.<door>_talk` (the call's sound both ways
through HA's go2rtc: the card's WebRTC sendrecv); the rest of a door's entities: intercom/entities.py."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .intercom.door import Door
from .intercom.entities import DoorEntity
from .intercom.manager import Intercom

# Push-updated, never polled.
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    mgr: Intercom | None = getattr(entry.runtime_data, "intercom", None)
    if mgr is None:
        return
    out: list[Camera] = []
    for door in mgr.doors.values():
        if door.cfg.rtsp:
            out.append(DoorCamera(mgr, door))
        out.append(DoorTalkCamera(mgr, door))
    add(out)


class DoorCamera(DoorEntity, Camera):
    """The panel's own RTSP (its preset's path): video for the card, through HA's go2rtc."""

    KIND, DOMAIN_ = "camera", "camera"
    _attr_name = None  # the door's name itself
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, mgr: Intercom, door: Door) -> None:
        DoorEntity.__init__(self, mgr, door)
        Camera.__init__(self)

    async def stream_source(self) -> str | None:
        return self.door.cfg.rtsp or None

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        if (img := await self.mgr.snapshot(self.door)) is not None:
            return img
        provider = getattr(self, "_webrtc_provider", None)  # HA's go2rtc: a frame of the stream
        if provider is not None and hasattr(provider, "async_get_image"):
            return await provider.async_get_image(self, width, height)
        return None


class DoorTalkCamera(DoorEntity, Camera):
    """The call's sound: the agent's loopback RTSP with ONVIF backchannel — the card's microphone goes back to the panel
    only while its person is the call's talker (the sensor's `talker`)."""

    KIND, DOMAIN_, SUFFIX = "talk", "camera", "_talk"
    _attr_translation_key = "intercom_talk"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, mgr: Intercom, door: Door) -> None:
        DoorEntity.__init__(self, mgr, door)
        Camera.__init__(self)

    async def stream_source(self) -> str | None:
        return self.mgr.rtsp.url(self.door.cfg.id) if self.mgr.rtsp is not None else None

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        return None  # sound only
