"""A re-link (reconfigure / reauth) restarts the agent with the new link; options stay live (HA reviewer r2:
async_update_reload_and_abort next to an update listener — «stops working in 2026.12»)."""

from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo.const import CONF_ALLOW_CHANGES, CONF_SECRET, CONF_SERVER, DOMAIN

PAIR = "https://app.hauflo.ru/api/agent/pair"


async def _idle(self):  # the link loop: no Hauflo server in tests
    return None


@pytest.fixture
async def entry(hass: HomeAssistant, aioclient_mock):
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — A", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "old"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


def _no_deprecation(caplog) -> None:
    assert "update listener" not in caplog.text and "2026.12" not in caplog.text


async def test_options_are_live_and_a_new_link_reloads(hass: HomeAssistant, entry, aioclient_mock, caplog) -> None:
    old = entry.runtime_data
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ALLOW_CHANGES: False})
    await hass.async_block_till_done()
    assert entry.runtime_data is old and old.locked  # options: no reload, read live
    aioclient_mock.post(PAIR, json={"secret": "new", "project": "B"})
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "RU-ABC-DEF-GHK"})
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.state is ConfigEntryState.LOADED and entry.runtime_data is not old
    assert entry.runtime_data.secret == "new" and entry.title == "Hauflo — B"
    _no_deprecation(caplog)


async def test_the_same_link_again_still_restarts_the_agent(hass: HomeAssistant, entry, aioclient_mock, caplog) -> None:
    old = entry.runtime_data
    aioclient_mock.post(PAIR, json={"secret": "old", "project": "A"})
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "RU-ABC-DEF-GHK"})
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful" and entry.runtime_data is not old
    assert entry.state is ConfigEntryState.LOADED
    _no_deprecation(caplog)


async def test_an_entry_that_isnt_running_is_set_up_by_the_relink(hass: HomeAssistant, entry, aioclient_mock, caplog) -> None:
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    aioclient_mock.post(PAIR, json={"secret": "new", "project": "B"})
    with patch("custom_components.hauflo.Agent.run", _idle):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "RU-ABC-DEF-GHK"})
        await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.state is ConfigEntryState.LOADED and entry.runtime_data.secret == "new"
    _no_deprecation(caplog)
