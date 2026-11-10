"""The integrator's pairs over HTTP (п.68): GET the view, POST set / clear, bad bodies, rights, kept across the engine's save."""

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo.const import CONF_RANGES, CONF_SECRET, CONF_SERVER, DOMAIN

V = ["binary_sensor.ward_valve_8", "binary_sensor.ward_valve_10"]
F = ["sensor.ward_floor_1", "sensor.ward_floor_2"]


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"}, options={CONF_RANGES: ["1/1"]})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    area = ar.async_get(hass).async_create("Гардероб")
    reg = er.async_get(hass)
    for eid in V + F:
        # Hauflo's own (its own platform): pairs are never made of another integration's entities (w6a2 D-B10).
        e = reg.async_get_or_create(eid.split(".")[0], DOMAIN, eid, suggested_object_id=eid.split(".")[1])
        reg.async_update_entity(e.entity_id, area_id=area.id)
    for eid, name in zip(V, ("Клапан 2.8", "Клапан 2.10")):
        hass.states.async_set(eid, "on", {"device_class": "opening", "friendly_name": name})
    for n, eid in enumerate(F, 1):
        hass.states.async_set(eid, "22", {"device_class": "temperature", "friendly_name": f"Температура пола {n}"})
    return entry


async def test_get_set_clear(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    got = await (await client.get("/api/hauflo/pairs")).json()
    assert {u["effect"] for u in got["unpaired"]} == set(F)
    assert {c["id"] for c in got["candidates"]["floor"]} == set(V)
    res = await client.post("/api/hauflo/pairs", json={"set": {"effect": F[0], "kind": "floor", "exec": V[0]}})
    assert res.status == 200
    body = await res.json()
    assert any(p["effect"] == F[0] and p["exec"] == V[0] and p["manual"] for p in body["pairs"])
    assert {u["effect"] for u in body["unpaired"]} == {F[1]}
    body = await (await client.post("/api/hauflo/pairs", json={"set": {"effect": F[1], "kind": "floor", "exec": ""}})).json()
    assert [i["effect"] for i in body["ignored"]] == [F[1]] and not body["unpaired"]
    body = await (await client.post("/api/hauflo/pairs", json={"clear": F[0]})).json()
    assert {u["effect"] for u in body["unpaired"]} == {F[0]}


async def test_bad_bodies_and_rights(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry) -> None:
    client = await hass_client()
    for body in ({}, [], {"set": {"effect": F[0]}}, {"set": {"effect": F[0], "kind": "pump", "exec": V[0]}}, {"set": {"effect": "nodot", "kind": "floor", "exec": V[0]}}, {"set": {"effect": F[0], "kind": "floor", "exec": "bad"}}, {"clear": 5}, {"clear": "x.y", "set": {}}):
        assert (await client.post("/api/hauflo/pairs", json=body)).status == 400, str(body)
    assert (await client.post("/api/hauflo/pairs", data="not json")).status == 400
    user = await hass_client(hass_read_only_access_token)
    assert (await user.get("/api/hauflo/pairs")).status == 403
    assert (await user.post("/api/hauflo/pairs", json={"clear": F[0]})).status == 403


async def test_not_ready_without_insights(hass: HomeAssistant, hass_client, entry) -> None:
    entry.runtime_data.insights.async_stop()
    entry.runtime_data.insights = None
    client = await hass_client()
    assert (await client.get("/api/hauflo/pairs")).status == 409
    assert (await client.post("/api/hauflo/pairs", json={"clear": F[0]})).status == 409


async def test_unknown_entity_is_rejected(hass: HomeAssistant, hass_client, entry) -> None:
    """Review B4 D-B4-4: a pair with an entity HA doesn't have is 400, nothing saved."""
    client = await hass_client()
    for eff, ex in (("climate.nonexistent_x", V[0]), (F[0], "binary_sensor.nonexistent_x")):
        res = await client.post("/api/hauflo/pairs", json={"set": {"effect": eff, "kind": "floor", "exec": ex}})
        assert res.status == 400
        assert "unknown_entity" in await res.text()
    got = await (await client.get("/api/hauflo/pairs")).json()
    assert not any(p.get("manual") for p in got["pairs"])



async def test_kinds_from_the_deploy(hass: HomeAssistant, hass_client, entry) -> None:
    """Batch «Внедрения» п.19: the deploy sends the thermostats' «воздух / пол» (the whole map); a bad one is refused."""
    client = await hass_client()
    res = await client.post("/api/hauflo/pairs", json={"kinds": {"climate.ward": "floor"}})
    assert res.status == 200
    for body in ({"kinds": {"climate.ward": "pump"}}, {"kinds": {"sensor.x": "air"}}, {"kinds": ["climate.ward"]}, {"kinds": {}, "clear": F[0]}):
        assert (await client.post("/api/hauflo/pairs", json=body)).status == 400, str(body)
    assert (await client.post("/api/hauflo/pairs", json={"kinds": {}})).status == 200
