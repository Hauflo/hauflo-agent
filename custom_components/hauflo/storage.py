"""Every store Hauflo keeps in HA's .storage: one class, so a later shape change is a migration, never a crash.

HA's Store raises NotImplementedError when the saved version differs from the code's and the store has no
migration (HA core review 29.10): an agent rolled back after a newer one saved would lose the feature. Here the
data is kept as is — every reader already checks its shape — until a real migration is written.
"""

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
        # No shape change yet: the data as it was (a newer minor written by a later agent reads the same way).
        return old_data
