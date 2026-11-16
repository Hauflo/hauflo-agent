from __future__ import annotations
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
VERSION = 1
MINOR_VERSION = 1

class HaufloStore(Store):

    def __init__(self, hass: HomeAssistant, key: str) -> None:
        super().__init__(hass, VERSION, key, minor_version=MINOR_VERSION)

    async def _async_migrate_func(self, old_major_version: int, old_minor_version: int, old_data: Any) -> Any:
        return old_data
