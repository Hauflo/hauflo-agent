from __future__ import annotations
from urllib.parse import urlparse
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from .const import CONF_SERVER, DOMAIN, VERSION

def device_info(entry_id: str, entry: ConfigEntry | None=None) -> DeviceInfo:
    server = str((entry.data.get(CONF_SERVER) if entry is not None else None) or '')
    link = {'configuration_url': server.rstrip('/')} if urlparse(server).scheme in ('http', 'https') and urlparse(server).hostname else {}
    return DeviceInfo(identifiers={(DOMAIN, entry_id)}, name='Hauflo', manufacturer='Hauflo', entry_type=DeviceEntryType.SERVICE, sw_version=VERSION, **link)
