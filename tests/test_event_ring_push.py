"""A doorbell press (event.*) is one push to the HA app by the alarms' own path: once per visit, not for a restored old
press, not for an event nobody watches, in the project's language."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.hauflo import foreign, notify
from custom_components.hauflo.const import CONF_RANGES, CONF_SECRET, CONF_SERVER, DOMAIN

BELL = "event.door_bell"
BUTTON = "event.wall_button"


@pytest.fixture
async def engine(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"}, options={CONF_RANGES: ["1/1"]})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    reg = er.async_get(hass)
    for eid in (BELL, BUTTON):
        reg.async_get_or_create("event", "zha", eid, suggested_object_id=eid.split(".")[1])
    hass.states.async_set(BELL, "unknown", {"device_class": "doorbell", "friendly_name": "Домофон"})
    hass.states.async_set(BUTTON, "unknown", {"device_class": "button", "friendly_name": "Кнопка"})
    eng = entry.runtime_data.engine
    eng.phones = lambda: [{"service": "mobile_app_p", "os": "android", "person": "x"}]
    eng.scope = lambda phone, config=None: "all"
    eng.config = {**eng.config, "lang": "ru"}
    calls = async_mock_service(hass, "notify", "mobile_app_p")
    yield eng, calls


def _ring(hass: HomeAssistant, eid: str, ago: float = 0) -> None:
    hass.states.async_set(eid, (dt_util.utcnow() - timedelta(seconds=ago)).isoformat(), {"device_class": hass.states.get(eid).attributes["device_class"], "friendly_name": hass.states.get(eid).attributes["friendly_name"]})


async def test_one_push_per_visit(hass: HomeAssistant, engine) -> None:
    eng, calls = engine
    foreign.flags(hass).notify[BELL] = True  # the integrator's «Уведомлять» (the doorbell's default class)
    _ring(hass, BELL)
    await hass.async_block_till_done()
    assert len(calls) == 1 and "звонок в дверь" in calls[0].data["message"] and "Домофон" in calls[0].data["message"]
    # The visitor presses again within the window: the same visit, no second push.
    _ring(hass, BELL, ago=-1)
    await hass.async_block_till_done()
    assert len(calls) == 1
    # After the window: a new visit.
    eng._rang[BELL] -= notify.RING_WINDOW + 1
    _ring(hass, BELL, ago=-2)
    await hass.async_block_till_done()
    assert len(calls) == 2


async def test_english(hass: HomeAssistant, engine) -> None:
    eng, calls = engine
    eng.config["lang"] = "en"
    foreign.flags(hass).notify[BELL] = True
    _ring(hass, BELL)
    await hass.async_block_till_done()
    assert len(calls) == 1 and "the doorbell rang" in calls[0].data["message"]


async def test_not_watched_old_or_unavailable(hass: HomeAssistant, engine) -> None:
    eng, calls = engine
    # A button nobody turned on: silent.
    _ring(hass, BUTTON)
    # A watched doorbell: an old restored press, then unavailable — silent.
    foreign.flags(hass).notify[BELL] = True
    _ring(hass, BELL, ago=3600)
    hass.states.async_set(BELL, "unavailable", {"device_class": "doorbell"})
    await hass.async_block_till_done()
    assert calls == []
    # The integrator turns the button on: it fires as a plain event line.
    foreign.flags(hass).notify[BUTTON] = True
    _ring(hass, BUTTON, ago=-1)
    await hass.async_block_till_done()
    assert len(calls) == 1 and "сработало событие" in calls[0].data["message"]
