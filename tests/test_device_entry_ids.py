"""HA 2026.x: a device belongs to one config entry (`config_entry_id`); reading `DeviceEntry.config_entries` logs a
deprecation WARNING on every call and breaks in HA 2027.10 (acceptance A on HA1, 0.34). The agent reads the current
field, and the old one only on an HA without it."""

import logging
from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import _own_device, foreign


async def test_a_current_device_is_read_without_the_deprecated_field(hass: HomeAssistant, caplog) -> None:
    entry = MockConfigEntry(domain="hue")
    entry.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("hue", "lamp1")})
    caplog.set_level(logging.WARNING)
    assert foreign.device_entry_ids(dev) == {entry.entry_id}
    assert foreign._device_refusal(hass, dev.id) is None
    assert _own_device(hass, dev.id) is False
    assert "config_entries" not in caplog.text


def test_an_older_ha_device_falls_back_to_the_set():
    old = SimpleNamespace(config_entries={"e1", "e2"})
    assert foreign.device_entry_ids(old) == {"e1", "e2"}
    composite = SimpleNamespace(config_entry_id="e1", is_composite_device=True, config_entries={"e1", "e2"})
    assert foreign.device_entry_ids(composite) == {"e1", "e2"}
    assert foreign.device_entry_ids(SimpleNamespace()) == set()


def test_the_registry_list_dict_prefers_config_entry_id():
    assert foreign.device_dict_entry_ids({"config_entry_id": "e1", "config_entries": ["e1", "e9"]}) == ["e1"]
    assert foreign.device_dict_entry_ids({"config_entries": ["e2", 3]}) == ["e2"]
    assert foreign.device_dict_entry_ids({}) == []
