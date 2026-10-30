"""«Зима / лето» from the dashboard settings (season.py, owner 27.10): the working ACs switch, the ones off are never
switched on (a KNX UI one gets its heat/cool address written once — a central switchover shared by two thermostats
goes once), floors and heat-only thermostats are left alone; any signed-in person may do it, the tunnel may not."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import _http_allowed
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

ON = "climate.living_ac"
OFF_KNX = "climate.bedroom_ac"
OFF_KNX_2 = "climate.study_ac"  # the same central heat/cool address as the bedroom
OFF_OTHER = "climate.garage_ac"
FLOOR = "climate.kitchen_floor"


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    yield entry


@pytest.fixture
def bus(hass: HomeAssistant, entry):
    entry.runtime_data.season.owns = lambda eid: True  # these are Hauflo's own climates (the ownership rule: test_season_setup)
    made: list[tuple[str, object]] = []

    async def hvac(call: ServiceCall) -> None:
        eid = call.data["entity_id"]
        eid = eid[0] if isinstance(eid, list) else eid
        made.append((eid, call.data["hvac_mode"]))
        hass.states.async_set(eid, call.data["hvac_mode"], hass.states.get(eid).attributes)

    async def send(call: ServiceCall) -> None:
        made.append((call.data["address"], call.data["payload"]))

    hass.services.async_register("climate", "set_hvac_mode", hvac)
    hass.services.async_register("knx", "send", send)
    both = {"hvac_modes": ["heat", "cool", "off"], "friendly_name": "AC"}
    hass.states.async_set(ON, "cool", both)
    hass.states.async_set(OFF_KNX, "off", both)
    hass.states.async_set(OFF_KNX_2, "off", both)
    hass.states.async_set(OFF_OTHER, "off", both)
    hass.states.async_set(FLOOR, "heat", {"hvac_modes": ["heat", "off"]})
    gas = {OFF_KNX: "1/3/100", OFF_KNX_2: "1/3/100"}

    def config(eid):
        if eid not in gas:
            raise ValueError("not a KNX UI entity")
        return {"platform": "climate", "data": {"knx": {"ga_heat_cool": {"write": gas[eid], "state": None, "passive": []}}}}

    hass.data["knx"] = SimpleNamespace(config_store=SimpleNamespace(get_entity_config=config))
    return made


async def test_winter_one_tap(hass: HomeAssistant, hass_client, hass_read_only_user, hass_read_only_access_token, entry, bus) -> None:
    # The client isn't an admin: allowed by default; not as a restricted (child) profile (family review rounds 2–3:
    # a child switched every AC).
    client = await hass_client(hass_read_only_access_token)
    await (await hass_client()).post("/api/hauflo/house", json={"restricted": [hass_read_only_user.id]})
    assert (await client.post("/api/hauflo/season", json={"mode": "heat"})).status == 403 and bus == []
    await (await hass_client()).post("/api/hauflo/house", json={"restricted": []})
    got = await (await client.get("/api/hauflo/season")).json()
    assert {a["entity_id"] for a in got["acs"]} == {ON, OFF_KNX, OFF_KNX_2, OFF_OTHER}  # no floor
    assert got["season"] == "cool"

    res = await client.post("/api/hauflo/season", json={"mode": "heat"})
    assert res.status == 200
    r = await res.json()
    assert r["changed"] == [ON] and sorted(r["sent"]) == sorted([OFF_KNX, OFF_KNX_2]) and r["skipped"] == [OFF_OTHER]
    # The working one to heat; the central address once, «1» = heat (DPT 1.100); nothing switched on, floor untouched.
    assert bus == [(ON, "heat"), ("1/3/100", 1)]
    assert hass.states.get(OFF_KNX).state == "off" and hass.states.get(FLOOR).state == "heat"

    got = await (await client.get("/api/hauflo/season")).json()
    assert got["season"] == "heat" and got["chosen"] == "heat"

    bus.clear()
    r = await (await client.post("/api/hauflo/season", json={"mode": "cool"})).json()
    assert bus == [(ON, "cool"), ("1/3/100", 0)]
    assert (await client.post("/api/hauflo/season", json={"mode": "auto"})).status == 400


async def test_all_off_says_the_last_choice(hass: HomeAssistant, hass_client, entry, bus) -> None:
    hass.states.async_set(ON, "off", hass.states.get(ON).attributes)
    client = await hass_client()
    await client.post("/api/hauflo/season", json={"mode": "cool"})
    got = await (await client.get("/api/hauflo/season")).json()
    assert got["season"] == "cool"


async def test_tunnel_cannot_switch_the_season(hass: HomeAssistant) -> None:
    assert not _http_allowed(hass, "POST", "/api/hauflo/season", {"mode": "heat"})
    assert not _http_allowed(hass, "GET", "/api/hauflo/season", "")
