"""0.34 п.7б: what each voice assistant / bridge is given, the dangerous ones (the agent's protection canon + cameras)
first; «закрыть опасное» takes off only those — HA's expose settings, Alice's label, a HomeKit bridge's filter — after a
backup; nothing else moves. Adversarial: a foreign lock / camera / gate cover, an accessory-mode bridge, a bad body."""

from unittest.mock import patch

import pytest
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity, async_should_expose
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, label_registry as lr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import _http_locked, exposure
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN


@pytest.fixture
async def house(hass: HomeAssistant):
    assert await async_setup_component(hass, "homeassistant", {})

    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    reg = er.async_get(hass)
    ids = {
        "lamp": reg.async_get_or_create("light", "knx", "u-lamp", suggested_object_id="lamp").entity_id,
        "lock": reg.async_get_or_create("lock", "zwave_js", "z-lock", suggested_object_id="front").entity_id,
        "cam": reg.async_get_or_create("camera", "onvif", "c1", suggested_object_id="yard").entity_id,
        "gate": reg.async_get_or_create("cover", "zha", "g1", suggested_object_id="gate", original_device_class="gate").entity_id,
        "blind": reg.async_get_or_create("cover", "knx", "u-blind", suggested_object_id="blind").entity_id,
        "diag": reg.async_get_or_create("sensor", "zha", "s1", suggested_object_id="rssi", entity_category=er.EntityCategory.DIAGNOSTIC).entity_id,
    }
    hass.states.async_set(ids["lamp"], "on", {"friendly_name": "Люстра"})
    hass.states.async_set(ids["lock"], "locked", {"friendly_name": "Входная дверь"})
    hass.states.async_set(ids["cam"], "idle", {"friendly_name": "Двор"})
    hass.states.async_set(ids["gate"], "closed", {"friendly_name": "Ворота", "device_class": "gate"})
    hass.states.async_set(ids["blind"], "open", {"friendly_name": "Штора"})
    hass.states.async_set(ids["diag"], "-60", {"friendly_name": "RSSI"})
    for eid in ids.values():
        async_expose_entity(hass, "conversation", eid, True)
    # Alice by a label: on everything, the lock and the gate too (someone labelled them by hand).
    alice = lr.async_get(hass).async_create("Алиса")
    for key in ("lamp", "lock", "gate", "blind"):
        reg.async_update_entity(ids[key], labels={alice.label_id, "family_tag"})
    MockConfigEntry(domain="yandex_smart_home", title="Yandex", options={"filter_source": "label", "label": alice.label_id}).add_to_hass(hass)
    # HomeKit: one bridge on everything, one including the lock by name, one accessory (the camera).
    hk_all = MockConfigEntry(domain="homekit", title="HASS Bridge A", options={"mode": "bridge", "filter": {"include_domains": [], "exclude_domains": [], "include_entities": [], "exclude_entities": []}})
    hk_lock = MockConfigEntry(domain="homekit", title="HASS Bridge B", options={"mode": "bridge", "filter": {"include_domains": ["light"], "exclude_domains": [], "include_entities": [ids["lock"]], "exclude_entities": []}})
    hk_acc = MockConfigEntry(domain="homekit", title="Камера", options={"mode": "accessory", "filter": {"include_entities": [ids["cam"]]}})
    for e in (hk_all, hk_lock, hk_acc):
        e.add_to_hass(hass)
    return {"ids": ids, "alice": alice.label_id, "hk": (hk_all, hk_lock, hk_acc), "entry": entry}


def _by(audit, aid, title=None):
    return next(a for a in audit["assistants"] if a["id"] == aid and (title is None or a.get("title") == title))


async def test_audit_lists_each_assistant_with_danger_first(hass: HomeAssistant, hass_client, house) -> None:
    ids = house["ids"]
    res = await (await hass_client()).get("/api/hauflo/exposure")
    assert res.status == 200
    got = await res.json()
    assist = _by(got, "conversation")
    assert assist["on"] is True
    danger = {r["entity_id"]: r["danger"] for r in assist["entities"]}
    assert danger[ids["lock"]] == "lock" and danger[ids["cam"]] == "camera" and danger[ids["gate"]] == "protected"
    assert danger[ids["lamp"]] is None and danger[ids["blind"]] is None
    assert [r["danger"] is None for r in assist["entities"]] == sorted(r["danger"] is None for r in assist["entities"])  # dangerous first
    assert next(r for r in assist["entities"] if r["entity_id"] == ids["lamp"])["name"] == "Люстра"
    # Alexa / Google without a logged-in cloud: off, nothing listed.
    assert _by(got, "cloud.alexa") == {"id": "cloud.alexa", "on": False, "count": 0, "dangerous": 0, "cut": 0, "entities": []}
    alice = _by(got, "alice")
    assert alice["mode"] == "label" and {r["entity_id"] for r in alice["entities"]} == {ids["lamp"], ids["lock"], ids["gate"], ids["blind"]}
    hk_all = _by(got, "homekit", "HASS Bridge A")
    assert ids["diag"] not in {r["entity_id"] for r in hk_all["entities"]}  # HomeKit leaves diagnostics out by itself
    assert {ids["lock"], ids["cam"], ids["gate"]} <= {r["entity_id"] for r in hk_all["entities"]}
    hk_lock = _by(got, "homekit", "HASS Bridge B")
    assert {r["entity_id"] for r in hk_lock["entities"]} == {ids["lamp"], ids["lock"]}
    assert {r["entity_id"] for r in _by(got, "homekit", "Камера")["entities"]} == {ids["cam"]}
    assert got["dangerous"] == sum(a["dangerous"] for a in got["assistants"]) and got["dangerous"] >= 9


async def test_close_takes_off_only_the_dangerous(hass: HomeAssistant, hass_client, house) -> None:
    ids, alice = house["ids"], house["alice"]
    reg = er.async_get(hass)
    client = await hass_client()
    with patch.object(exposure.backup, "fresh", return_value=False):
        res = await client.post("/api/hauflo/exposure", json={"close": True})
    assert res.status == 428 and (await res.json())["code"] == "backup_required"
    assert async_should_expose(hass, "conversation", ids["lock"])  # nothing closed without a backup
    with patch.object(exposure.backup, "fresh", return_value=True):
        res = await client.post("/api/hauflo/exposure", json={"close": True})
    assert res.status == 200, await res.text()
    got = await res.json()
    closed = {c["entity_id"]: c["assistants"] for c in got["closed"]}
    assert closed[ids["lock"]] == ["alice", "conversation", "homekit"]
    assert set(closed) == {ids["lock"], ids["cam"], ids["gate"]}
    # Assist: the dangerous off, the rest still on.
    assert not async_should_expose(hass, "conversation", ids["lock"]) and not async_should_expose(hass, "conversation", ids["cam"])
    assert async_should_expose(hass, "conversation", ids["lamp"]) and async_should_expose(hass, "conversation", ids["blind"])
    # Alice: her label off the lock and the gate only; the family's own label stays.
    assert reg.async_get(ids["lock"]).labels == {"family_tag"} and reg.async_get(ids["gate"]).labels == {"family_tag"}
    assert alice in reg.async_get(ids["lamp"]).labels
    # HomeKit: bridge A excludes the dangerous, bridge B no longer includes the lock (and excludes it), its domains stay.
    hk_all, hk_lock, hk_acc = (hass.config_entries.async_get_entry(e.entry_id) for e in house["hk"])
    assert set(hk_all.options["filter"]["exclude_entities"]) == {ids["lock"], ids["cam"], ids["gate"]}
    assert hk_lock.options["filter"]["include_entities"] == [] and hk_lock.options["filter"]["include_domains"] == ["light"]
    assert hk_lock.options["filter"]["exclude_entities"] == [ids["lock"]]
    assert hk_acc.options == {"mode": "accessory", "filter": {"include_entities": [ids["cam"]]}}  # an accessory: said, never unpaired
    # The answer is the audit after: only the accessory's camera left dangerous.
    assert got["dangerous"] == 1 and _by(got, "homekit", "Камера")["dangerous"] == 1
    # Again: nothing more to close.
    with patch.object(exposure.backup, "fresh", return_value=True):
        again = await (await client.post("/api/hauflo/exposure", json={"close": True})).json()
    assert again["closed"] == []


async def test_bad_body_and_locked_house(hass: HomeAssistant, hass_client, house) -> None:
    client = await hass_client()
    for body in ({}, {"close": False}, {"close": True, "only": ["lock.front"]}, ["close"]):
        res = await client.post("/api/hauflo/exposure", json=body)
        assert res.status == 400
    # The owner closed the house: the tunnel reads the audit, never closes (it changes other integrations' settings).
    assert _http_locked("GET", "/api/hauflo/exposure") is False
    assert _http_locked("POST", "/api/hauflo/exposure", '{"close": true}') is True


def test_danger_of_canon() -> None:
    never = {"lock", "alarm_control_panel", "valve"}
    assert exposure.danger_of("lock.x", set(), never) == "lock"
    assert exposure.danger_of("alarm_control_panel.x", set(), never) == "alarm_control_panel"
    assert exposure.danger_of("camera.x", set(), never) == "camera"
    assert exposure.danger_of("switch.gate_relay", {"switch.gate_relay"}, never) == "protected"
    assert exposure.danger_of("light.x", set(), never) is None


def test_feature_in_hello():
    """The backend asks only an agent that says it has the audit (an older one has no route)."""
    from custom_components import hauflo

    assert exposure.FEATURE == "exposure" and exposure.FEATURE in hauflo.FEATURES
