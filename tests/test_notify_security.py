"""Alarm rules from the cloud (security review 28.10, agent 0.14.4 live): while the owner kept the house closed the cloud
stored a critical rule repeating every minute to everyone on the family's motion sensor (another integration's), and a
push button closing a valve that isn't Hauflo's. Now: sources are Hauflo's own or marked «Hauflo мониторинг» in HA;
buttons only on Hauflo's own; critical only for safety classes; a repeat no more often than 5 minutes; while locked the
rules may only narrow."""

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er, label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import notify
from custom_components.hauflo.const import CONF_ALLOW_CHANGES, CONF_RANGES, CONF_SECRET, CONF_SERVER, DOMAIN, LOCKED_CODE, MONITOR_LABEL

MOTION = "binary_sensor.hall_motion"  # the family's, another integration's
OWN_MOTION = "binary_sensor.knx_motion"  # Hauflo's own, but motion
LEAK = "binary_sensor.knx_leak"  # Hauflo's own leak sensor
BOILER = "binary_sensor.boiler_fault"  # another integration's, marked «Hauflo мониторинг» by the integrator
PUMP = "binary_sensor.pump_fault"  # another integration's, its device marked
VALVE = "valve.front_garden"  # not Hauflo's
OWN_VALVE = "valve.knx_water"
OWNED = {OWN_MOTION, LEAK, OWN_VALVE}


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — Вилла Юг", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"}, options={CONF_RANGES: ["1/1"]})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    reg = er.async_get(hass)
    label = lr.async_get(hass).async_create(MONITOR_LABEL)
    other = MockConfigEntry(domain="zha")
    other.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=other.entry_id, identifiers={("zha", "pump")})
    dr.async_get(hass).async_update_device(dev.id, labels={label.label_id})
    for eid, dc in ((MOTION, "motion"), (OWN_MOTION, "motion"), (LEAK, "moisture"), (BOILER, "problem"), (PUMP, "problem")):
        reg.async_get_or_create("binary_sensor", "zha", eid, suggested_object_id=eid.split(".")[1], device_id=dev.id if eid == PUMP else None)
        hass.states.async_set(eid, "off", {"device_class": dc})
    reg.async_update_entity(BOILER, labels={label.label_id})
    entry.runtime_data.engine.owns = lambda eid: eid in OWNED
    yield entry


def _rule(**kw) -> dict:
    return {"id": "r", "kind": "custom", "title": "Авария", "sources": [MOTION], "recipients": {"all": True}, "priority": "critical", "repeat_min": 1, **kw}


async def test_the_reviewers_rule_is_cleaned(hass: HomeAssistant, entry) -> None:
    eng = entry.runtime_data.engine
    cfg = await eng.async_set({"rules": [_rule(action={"title": "Закрыть", "service": "valve.close_valve", "entity_id": VALVE})], "groups": []})
    r = cfg["rules"][0]
    assert r["sources"] == [] and r["priority"] == "normal" and r["repeat_min"] == 5 and "action" not in r


async def test_sources_priority_repeat_and_buttons(hass: HomeAssistant, entry) -> None:
    eng = entry.runtime_data.engine
    cfg = await eng.async_set(
        {
            "rules": [
                _rule(id="motion", sources=[OWN_MOTION]),  # Hauflo's, but motion: never critical
                _rule(id="leak", sources=[LEAK], repeat_min=5, action={"title": "Закрыть", "service": "valve.close_valve", "entity_id": OWN_VALVE}),
                _rule(id="marked", sources=[BOILER, PUMP, MOTION], repeat_min=0),
                _rule(id="mixed", sources=[LEAK, OWN_MOTION], repeat_min=30),
            ],
            "groups": [],
        }
    )
    by = {r["id"]: r for r in cfg["rules"]}
    assert (by["motion"]["sources"], by["motion"]["priority"], by["motion"]["repeat_min"]) == ([OWN_MOTION], "normal", 5)
    assert (by["leak"]["priority"], by["leak"]["repeat_min"], by["leak"]["action"]["entity_id"]) == ("critical", 5, OWN_VALVE)
    assert (by["marked"]["sources"], by["marked"]["priority"], by["marked"]["repeat_min"]) == ([BOILER, PUMP], "critical", 0)
    assert by["mixed"]["priority"] == "normal" and by["mixed"]["repeat_min"] == 30


async def test_locked_only_narrows(hass: HomeAssistant, hass_client, entry) -> None:
    eng = entry.runtime_data.engine
    base = {"rules": [_rule(id="leak", sources=[LEAK, BOILER], recipients={"people": ["person.ivan", "person.anna"]}, repeat_min=15)], "groups": [{"id": "g", "people": ["person.ivan"]}], "audience": {"*": "critical"}}
    await eng.async_set(base)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ALLOW_CHANGES: False})
    client = await hass_client()
    before = dict(eng.config)
    widen = [
        {**base, "rules": [*base["rules"], _rule(id="new", sources=[LEAK])]},  # a new rule
        {**base, "rules": [{**base["rules"][0], "sources": [LEAK, BOILER, PUMP]}]},  # a new source
        {**base, "rules": [{**base["rules"][0], "recipients": {"all": True}}]},  # everyone
        {**base, "rules": [{**base["rules"][0], "repeat_min": 5}]},  # more often
        {**base, "rules": [{**base["rules"][0], "action": {"title": "x", "service": "valve.close_valve", "entity_id": OWN_VALVE}}]},  # a button
        {**base, "rules": [{**base["rules"][0], "title": "Позвоните +7 900 000-00-00"}]},  # another text
        {**base, "groups": [{"id": "g", "people": ["person.ivan", "person.guest"]}]},
        {**base, "audience": {"*": "all"}},
        {**base, "audience": {}},
    ]
    for body in widen:
        res = await client.post("/api/hauflo/notify", json=body)
        assert res.status == 423, body
        assert (await res.json())["code"] == LOCKED_CODE
        assert eng.config == before
    narrow = {**base, "rules": [{**base["rules"][0], "sources": [LEAK], "recipients": {"people": ["person.ivan"]}, "repeat_min": 0, "enabled": False}], "audience": {"*": "off"}}
    res = await client.post("/api/hauflo/notify", json=narrow)
    assert res.status == 200, await res.text()
    assert eng.config["rules"][0]["sources"] == [LEAK] and eng.config["rules"][0]["enabled"] is False
    # Switched back on while locked: that widens again.
    assert (await client.post("/api/hauflo/notify", json=base)).status == 423
    # Opened: anything goes through the usual checks.
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ALLOW_CHANGES: True})
    assert (await client.post("/api/hauflo/notify", json=widen[0])).status == 200


def test_narrowing_is_pure() -> None:
    clean = lambda raw: notify._clean_rules(raw, None, None)  # noqa: E731
    a = clean({"rules": [{"id": "r", "sources": []}], "groups": []})
    assert notify.narrowing(a, a) is None
    assert notify.narrowing(clean({"rules": [], "groups": []}), a) is None  # a rule removed


# Owner 28.10: a critical rule must not go quiet unseen. Loud: safety classes or no class at all (KNX); never motion,
# door, window, occupancy, presence (nor anything else); a classless sensor named like a motion one stays normal too.
LOUD_TABLE = [
    # (device classes of the sources, name of the first, priority kept, reason)
    ([None], "Протечка кухня", "critical", None),
    (["moisture"], "Leak", "critical", None),
    (["smoke", "gas", "carbon_monoxide", "heat", "problem", "safety"], "Mix", "critical", None),
    (["moisture", None], "Leak + KNX", "critical", None),
    (["motion"], "Hall", "normal", "quiet_class:motion"),
    (["door"], "Door", "normal", "quiet_class:door"),
    (["window"], "Window", "normal", "quiet_class:window"),
    (["occupancy"], "Occ", "normal", "quiet_class:occupancy"),
    (["presence"], "Pres", "normal", "quiet_class:presence"),
    (["moisture", "motion"], "Mixed", "normal", "quiet_class:motion"),
    ([None], "Датчик движения коридор", "normal", "quiet_name"),
    ([None, "moisture"], "Presence hall", "normal", "quiet_name"),
    # Д8 (29.10): a classless door / window / gate contact is no alarm either — RU and EN.
    ([None], "Окно спальня", "normal", "quiet_name"),
    ([None], "Входная дверь", "normal", "quiet_name"),
    ([None], "Створка гостиная", "normal", "quiet_name"),
    ([None], "Ворота", "normal", "quiet_name"),
    ([None], "Геркон кухня", "normal", "quiet_name"),
    ([None], "Front door", "normal", "quiet_name"),
    ([None], "Window kitchen", "normal", "quiet_name"),
    ([None], "Garage gate", "normal", "quiet_name"),
    ([None], "Contact terrace", "normal", "quiet_name"),
    # …but a leak sensor by the window still rings, and «outdoor» / «поворот» are no door or gate.
    ([None], "Протечка у окна", "critical", None),
    ([None], "Leak under window", "critical", None),
    ([None], "Outdoor temperature alarm", "critical", None),
    ([None], "Авария поворотного крана", "critical", None),
    ([], "None", "normal", "no_sources"),
]


@pytest.mark.parametrize(("classes", "name", "priority", "why"), LOUD_TABLE)
async def test_critical_decision_table(hass: HomeAssistant, entry, classes, name, priority, why) -> None:
    eng = entry.runtime_data.engine
    ids = []
    for i, dc in enumerate(classes):
        eid = f"binary_sensor.t{i}_x"
        hass.states.async_set(eid, "off", {**({"device_class": dc} if dc else {}), **({"friendly_name": name} if i == 0 else {})})
        ids.append(eid)
    eng.owns = lambda eid: eid in ids
    cfg = await eng.async_set({"rules": [_rule(sources=ids)], "groups": []})
    r = cfg["rules"][0]
    assert r["priority"] == priority
    assert r.get("downgrade") == why
    # Stored and read again (a restart): the reason stays.
    again = await eng.async_set({"rules": [{**r}], "groups": []})
    assert again["rules"][0].get("downgrade") == why


async def test_test_push_names_the_phone_os(hass: HomeAssistant, entry) -> None:
    eng = entry.runtime_data.engine
    eng.phones = lambda: [{"service": "mobile_app_a", "name": "A", "os": "ios", "person": None}]
    hass.services.async_register("notify", "mobile_app_a", lambda call: None)
    out = await eng.async_test({"all": True, "priority": "critical"})
    assert out["results"][0]["os"] == "ios"
