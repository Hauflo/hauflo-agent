"""399-42 «События»: an incident's value graph (history.py) on a real HA with its real recorder — the state's numbers, a
climate's measured temperature, admin only, the tunnel's privacy (a phone's sensor refused), no domain / no number /
a broken window refused, nothing recorded said, the answer thinned with a spike kept, the window clipped."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_component
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.hauflo import history
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

URL = "/api/hauflo/history"
T = "sensor.kitchen_temperature"


@pytest.fixture(autouse=True)
def _custom_integrations(recorder_mock, enable_custom_integrations, hass):
    mock_component(hass, "frontend")
    yield


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _q(entity: str, back_h: float = 1) -> str:
    start = int((dt_util.utcnow() - timedelta(hours=back_h)).timestamp() * 1000)
    return f"{URL}?entity={entity}&start={start}"


async def test_numeric_sensor_points(hass: HomeAssistant, hass_client) -> None:
    await _setup(hass)
    for v in ("20", "unavailable", "21.5", "22"):
        hass.states.async_set(T, v, {"unit_of_measurement": "°C", "device_class": "temperature"})
        await hass.async_block_till_done()
    await async_wait_recording_done(hass)
    client = await hass_client()
    r = await client.get(_q(T))
    assert r.status == 200
    j = await r.json()
    assert j["why"] is None and j["unit"] == "°C" and j["attribute"] is None
    assert [p[1] for p in j["points"]] == [20.0, 21.5, 22.0]  # «unavailable» is a gap, not a zero
    assert all(j["from"] <= p[0] <= j["to"] for p in j["points"])


async def test_climate_current_temperature(hass: HomeAssistant, hass_client) -> None:
    await _setup(hass)
    for cur in (19.5, 20.5):
        hass.states.async_set("climate.living", "heat", {"current_temperature": cur, "temperature": 22})
        await hass.async_block_till_done()
    await async_wait_recording_done(hass)
    j = await (await (await hass_client()).get(_q("climate.living"))).json()
    assert j["attribute"] == "current_temperature"
    assert [p[1] for p in j["points"]] == [19.5, 20.5]
    assert j["unit"] == hass.config.units.temperature_unit


async def test_refused(hass: HomeAssistant, hass_client, hass_read_only_access_token) -> None:
    await _setup(hass)
    er.async_get(hass).async_get_or_create("sensor", "mobile_app", "u-phone", suggested_object_id="phone_battery_level")
    hass.states.async_set("sensor.phone_battery_level", "80", {"unit_of_measurement": "%"})
    hass.states.async_set(T, "20")
    await hass.async_block_till_done()
    ro = await hass_client(hass_read_only_access_token)
    assert (await ro.get(_q(T))).status == 403  # not an admin
    admin = await hass_client()
    assert (await admin.get(_q("sensor.phone_battery_level"))).status == 403  # a phone: private
    for bad in ("light.hall", "person.anna", "sensor.x;drop", ""):
        assert (await admin.get(_q(bad))).status == 400, bad
    assert (await admin.get(f"{URL}?entity={T}&start=yesterday")).status == 400
    assert (await admin.get(f"{URL}?entity={T}")).status == 400


async def test_nothing_recorded(hass: HomeAssistant, hass_client) -> None:
    await _setup(hass)
    await async_wait_recording_done(hass)
    j = await (await (await hass_client()).get(_q("sensor.never_seen"))).json()
    assert j["points"] == [] and j["why"] == "no_history"


def test_thin_keeps_a_spike_and_the_order() -> None:
    pts = [(float(i), 20.0) for i in range(1000)]
    pts[537] = (537.0, 90.0)
    got = history.thin(pts, 0, 1000, 20)
    assert len(got) <= 20
    assert (537.0, 90.0) in got
    assert [p[0] for p in got] == sorted(p[0] for p in got)
    assert history.thin(pts[:5], 0, 5, 20) == pts[:5]


def test_window_clipped() -> None:
    now = dt_util.utcnow()
    ms = lambda d: str(int(d.timestamp() * 1000))  # noqa: E731
    a, b = history.window(ms(now - timedelta(days=30)), ms(now + timedelta(hours=5)), now)
    assert b == now and b - a == history.MAX_WINDOW  # the end side kept
    a, b = history.window((now - timedelta(hours=2)).isoformat(), None, now)
    assert b == now and b - a == timedelta(hours=2)
    assert history.window(ms(now + timedelta(hours=1)), None, now) is None
    assert history.window("nonsense", None, now) is None
