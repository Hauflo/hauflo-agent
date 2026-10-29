"""The agent's one service device «Hauflo»: every entity of the agent sits on it (HA reviewer r2: with the agent's
version and a link to the Hauflo app on the device page)."""

from __future__ import annotations

from urllib.parse import urlparse

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import CONF_SERVER, DOMAIN, VERSION


def device_info(entry_id: str, entry: ConfigEntry | None = None) -> DeviceInfo:
    info = DeviceInfo(identifiers={(DOMAIN, entry_id)}, name="Hauflo", manufacturer="Hauflo", entry_type=DeviceEntryType.SERVICE, sw_version=VERSION)
    server = str((entry.data.get(CONF_SERVER) if entry is not None else None) or "")
    # The Hauflo app this home is linked to (the project's page needs the integrator's login there anyway).
    if urlparse(server).scheme in ("http", "https") and urlparse(server).hostname:
        info["configuration_url"] = server.rstrip("/")
    return info
