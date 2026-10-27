"""The local backup location on every HA install: backup.local (Core/Container) and hassio.local (HA OS), which is
not in local_backup_agents (27.10, Горького 3 on HA OS: «coroutine raised StopIteration»)."""

from unittest.mock import patch

import pytest

from custom_components.hauflo import backup


class _Manager:
    def __init__(self, local, agents):
        self.local_backup_agents = local
        self.backup_agents = agents
        self.config = None
        self.created = None

    async def async_create_backup(self, **kw):
        self.created = kw

    async def async_get_backups(self):
        return {}, None


@pytest.mark.parametrize(
    ("local", "agents", "want"),
    [
        ({"backup.local": object()}, {"backup.local": object()}, "backup.local"),
        ({}, {"hassio.local": object(), "cloud.cloud": object()}, "hassio.local"),
    ],
)
async def test_local_location(hass, local, agents, want):
    m = _Manager(local, agents)
    with patch("homeassistant.components.backup.async_get_manager", return_value=m):
        await backup._create(hass, "Hauflo test")
    assert m.created["agent_ids"] == [want]


async def test_no_local_location_is_a_clear_error(hass):
    m = _Manager({}, {"cloud.cloud": object()})
    with patch("homeassistant.components.backup.async_get_manager", return_value=m), pytest.raises(Exception, match="no local backup location"):
        await backup._create(hass, "Hauflo test")
