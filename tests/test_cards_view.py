"""«Вид карточек» (cards v5): the option picks the cards file the house loads, the default stays the current file, the
options flow saves it; `hauflo.off_in` switches a light off after a time and cancels with 0."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.hauflo import keep
from custom_components.hauflo.const import CARDS_CURRENT, CARDS_FILES, CARDS_NEW, CONF_CARDS_VIEW, CONF_SECRET, CONF_SERVER, DOMAIN, cards_file


def _entry(hass: HomeAssistant, options: dict) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — t", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"}, options=options)
    entry.add_to_hass(hass)
    return entry


async def test_default_is_current(hass: HomeAssistant):
    assert cards_file(hass) == CARDS_FILES[CARDS_CURRENT]
    _entry(hass, {})
    assert cards_file(hass) == "hauflo-cards.js"
    assert keep.modules(hass) == keep.MODULES


async def test_new_picks_v5_and_keep_follows(hass: HomeAssistant):
    _entry(hass, {CONF_CARDS_VIEW: CARDS_NEW})
    assert cards_file(hass) == "hauflo-cards-v5.js"
    assert keep.modules(hass) == ("hauflo-icons.js", "hauflo-cards-v5.js")


async def test_garbage_option_is_current(hass: HomeAssistant):
    _entry(hass, {CONF_CARDS_VIEW: "../../etc"})
    assert cards_file(hass) == "hauflo-cards.js"


async def test_options_flow_saves_view(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = _entry(hass, {})
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        form = await hass.config_entries.options.async_init(entry.entry_id)
        res = await hass.config_entries.options.async_configure(form["flow_id"], {"allow_changes": True, "ranges": "", CONF_CARDS_VIEW: CARDS_NEW})
        await hass.async_block_till_done()
    assert res["data"][CONF_CARDS_VIEW] == CARDS_NEW
    assert cards_file(hass) == "hauflo-cards-v5.js"


@pytest.fixture
async def timers_ready(hass: HomeAssistant):
    from custom_components.hauflo import timers

    timers.async_setup(hass)
    return async_mock_service(hass, "light", "turn_off")


async def test_off_in_switches_off_after_time(hass: HomeAssistant, timers_ready):
    await hass.services.async_call("hauflo", "off_in", {"entity_id": "light.a", "minutes": 5}, blocking=True)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=4))
    await hass.async_block_till_done()
    assert timers_ready == []
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in timers_ready] == ["light.a"]


async def test_off_in_zero_cancels_and_rearm_replaces(hass: HomeAssistant, timers_ready):
    await hass.services.async_call("hauflo", "off_in", {"entity_id": "light.a", "minutes": 5}, blocking=True)
    await hass.services.async_call("hauflo", "off_in", {"entity_id": "light.a", "minutes": 0}, blocking=True)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=10))
    await hass.async_block_till_done()
    assert timers_ready == []
    await hass.services.async_call("hauflo", "off_in", {"entity_id": "light.a", "minutes": 5}, blocking=True)
    await hass.services.async_call("hauflo", "off_in", {"entity_id": "light.a", "minutes": 30}, blocking=True)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=10))
    await hass.async_block_till_done()
    assert timers_ready == []  # the second call replaced the first
    await hass.services.async_call("hauflo", "off_in", {"entity_id": "light.a", "minutes": 0}, blocking=True)


async def test_off_in_refuses_non_light_and_bad_minutes(hass: HomeAssistant, timers_ready):
    import voluptuous as vol

    with pytest.raises(vol.Invalid):
        await hass.services.async_call("hauflo", "off_in", {"entity_id": "lock.door", "minutes": 5}, blocking=True)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call("hauflo", "off_in", {"entity_id": "light.a", "minutes": 99999}, blocking=True)
