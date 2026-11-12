"""п.12: «Состояние объекта» — sensor.hauflo_house_state / binary_sensor.hauflo_house_problem by the one calculator
(Agent.house_now), pushed on every recompute; the site's «Отложить до» (shelvedUntil) holds a signal off the house until
its end and lets it back at that time without a restart."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hauflo import signals
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN, VERSION

SENSOR = "sensor.hauflo_house_state"
PROBLEM = "binary_sensor.hauflo_house_problem"
AT = "2026-11-10T00:00:00.000Z"
HASH = "0123456789abcdef"


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    reg = er.async_get(hass)
    for uid, dc in (("zont", "problem"), ("leak", "moisture")):
        reg.async_get_or_create("binary_sensor", "zha", uid, suggested_object_id=uid, original_device_class=dc)
    hass.states.async_set("binary_sensor.zont", "off", {"device_class": "problem", "friendly_name": "Zont"})
    hass.states.async_set("binary_sensor.leak", "off", {"device_class": "moisture", "friendly_name": "Протечка"})
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def _policy(hass, client, entities, hash_=HASH):
    res = await client.post("/api/hauflo/signals", json={"signals": {"entities": entities}, "hash": hash_})
    assert res.status == 200, await res.text()
    await hass.async_block_till_done()


async def _changed(hass, freezer=None):
    """A watched signal's change → the agent's 2 s debounce → status_now → the sensors."""
    if freezer is not None:
        freezer.tick(timedelta(seconds=3))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()


async def test_entities_are_made_translated_and_not_diagnostic(hass: HomeAssistant, entry):
    reg = er.async_get(hass)
    s, b = reg.async_get(SENSOR), reg.async_get(PROBLEM)
    assert s and b and s.unique_id == f"{entry.entry_id}_house_state" and b.unique_id == f"{entry.entry_id}_house_problem"
    assert s.entity_category is None and b.entity_category is None  # the client's automations read them
    assert s.translation_key == "house_state" and b.translation_key == "house_problem" and s.device_id and s.device_id == b.device_id
    st = hass.states.get(SENSOR)
    assert st.state == "ok" and st.attributes["device_class"] == "enum" and st.attributes["options"] == ["ok", "fault", "alarm", "offline"]
    assert st.attributes["friendly_name"] == "Hauflo Site status"
    assert {k: st.attributes[k] for k in ("alarm", "fault", "offline", "undetermined", "shelved", "signals", "version")} == {"alarm": 0, "fault": 0, "offline": 0, "undetermined": 0, "shelved": 0, "signals": [], "version": VERSION}
    assert dt_util.parse_datetime(st.attributes["since"]) is not None
    pb = hass.states.get(PROBLEM)
    assert pb.state == "off" and pb.attributes["device_class"] == "problem" and pb.attributes["friendly_name"] == "Hauflo Site problem"


async def test_state_follows_the_signals_without_the_link(hass: HomeAssistant, entry, freezer):
    agent = entry.runtime_data
    assert agent.ws is None  # not linked: the house's own calculator still runs
    hass.states.async_set("binary_sensor.zont", "on", {"device_class": "problem", "friendly_name": "Zont"})
    await _changed(hass, freezer)
    st = hass.states.get(SENSOR)
    assert st.state == "fault" and st.attributes["fault"] == 1 and st.attributes["signals"] == [{"entity": "binary_sensor.zont", "level": "fault"}]
    assert hass.states.get(PROBLEM).state == "on"
    since = st.attributes["since"]
    hass.states.async_set("binary_sensor.leak", "on", {"device_class": "moisture", "friendly_name": "Протечка"})
    await _changed(hass, freezer)
    st = hass.states.get(SENSOR)
    assert st.state == "alarm" and st.attributes["alarm"] == 1 and st.attributes["fault"] == 1 and st.attributes["since"] != since
    hass.states.async_set("binary_sensor.leak", "unavailable", {"device_class": "moisture", "friendly_name": "Протечка"})
    hass.states.async_set("binary_sensor.zont", "off", {"device_class": "problem", "friendly_name": "Zont"})
    await _changed(hass, freezer)
    st = hass.states.get(SENSOR)
    assert st.state == "offline" and st.attributes["offline"] == 1 and st.attributes["signals"] == [{"entity": "binary_sensor.leak", "level": "offline"}]
    hass.states.async_set("binary_sensor.leak", "off", {"device_class": "moisture", "friendly_name": "Протечка"})
    await _changed(hass, freezer)
    assert hass.states.get(SENSOR).state == "ok" and hass.states.get(PROBLEM).state == "off"
    # The same level as the cards' read and the link's status (one calculator).
    assert agent.house()["_house"] == "ok" and agent.status()["alarmsNow"]["total"] == 0


async def test_policy_change_moves_it_at_once(hass: HomeAssistant, entry, hass_client):
    hass.states.async_set("binary_sensor.zont", "on", {"device_class": "problem", "friendly_name": "Zont"})
    entry.runtime_data.status_now()
    assert hass.states.get(SENSOR).state == "fault"
    await _policy(hass, await hass_client(), {"binary_sensor.zont": {"house": "none", "by": "t", "at": AT}})
    assert hass.states.get(SENSOR).state == "ok"


async def test_unavailable_without_the_calculator(hass: HomeAssistant, entry):
    entry.runtime_data.house_state = None
    hass.states.async_set("binary_sensor.zont", "off", {"device_class": "problem"})
    ent = hass.data["entity_components"]["sensor"].get_entity(SENSOR)
    ent.async_write_ha_state()
    assert hass.states.get(SENSOR).state == STATE_UNAVAILABLE


async def test_shelved_until_holds_then_lets_back_without_a_restart(hass: HomeAssistant, entry, hass_client, freezer):
    agent = entry.runtime_data
    client = await hass_client()
    hass.states.async_set("binary_sensor.leak", "on", {"device_class": "moisture", "friendly_name": "Протечка"})
    agent.status_now()
    assert hass.states.get(SENSOR).state == "alarm"
    until = (dt_util.utcnow() + timedelta(hours=1)).isoformat()
    await _policy(hass, client, {"binary_sensor.leak": {"shelvedUntil": until, "by": "t", "at": AT}})
    h = agent.house()
    assert h["_house"] == "ok" and h["_faults"] == [] and h["_held"] == [{"entity": "binary_sensor.leak", "until": until}]
    assert h["_signals"]["binary_sensor.leak"]["house"] == "none" and h["_signals"]["binary_sensor.leak"]["until"] == until
    st = hass.states.get(SENSOR)
    assert st.state == "ok" and st.attributes["shelved"] == 1 and hass.states.get(PROBLEM).state == "off"
    assert [m.get("shelvedUntil") for m in agent.status()["monitored"] if m["entityId"] == "binary_sensor.leak"] == [until]
    assert agent.status()["alarmsNow"]["total"] == 0
    # Its end: the one timer fires, the leak counts again — no restart, no new policy.
    freezer.tick(timedelta(hours=1, seconds=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    st = hass.states.get(SENSOR)
    assert st.state == "alarm" and st.attributes["shelved"] == 0 and hass.states.get(PROBLEM).state == "on"
    assert agent.house()["_held"] == [] and "until" not in agent.house()["_signals"]["binary_sensor.leak"]


async def test_shelved_until_removed_early_counts_at_once(hass: HomeAssistant, entry, hass_client):
    agent = entry.runtime_data
    client = await hass_client()
    hass.states.async_set("binary_sensor.zont", "on", {"device_class": "problem", "friendly_name": "Zont"})
    until = (dt_util.utcnow() + timedelta(days=2)).isoformat()
    await _policy(hass, client, {"binary_sensor.zont": {"shelvedUntil": until, "by": "t", "at": AT}})
    assert hass.states.get(SENSOR).state == "ok" and agent._unshelve_at is not None  # noqa: SLF001
    await _policy(hass, client, {}, "fedcba9876543210")  # «Снять» in Hauflo: the policy without it
    assert hass.states.get(SENSOR).state == "fault" and agent._unshelve_timer is None  # noqa: SLF001


def test_shelved_now_and_the_nearest_end():
    now = dt_util.utcnow()
    soon, later, past = (now + timedelta(hours=1)).isoformat(), (now + timedelta(days=1)).isoformat(), (now - timedelta(minutes=1)).isoformat()
    assert signals.shelved_now(soon) and not signals.shelved_now(past) and not signals.shelved_now(None) and not signals.shelved_now("tomorrow")
    pol = {"entities": {"a.x": {"shelvedUntil": later}, "a.y": {"shelvedUntil": past}}, "scopes": [{"match": {"domain": "binary_sensor"}, "set": {"shelvedUntil": soon}}]}
    assert signals.next_unshelve(pol, now) == dt_util.parse_datetime(soon)
    assert signals.next_unshelve({"entities": {"a.y": {"shelvedUntil": past}}}, now) is None and signals.next_unshelve(None) is None
    # A past one counts again; a future one holds the problem off the house but says it.
    ev = signals.evaluate("on", eid="binary_sensor.leak", device_class="moisture", policy={"entities": {"binary_sensor.leak": {"shelvedUntil": soon}}, "scopes": []})
    assert ev["problem"] and ev["shelved"] and not ev["affects"]
    ev = signals.evaluate("on", eid="binary_sensor.leak", device_class="moisture", policy={"entities": {"binary_sensor.leak": {"shelvedUntil": past}}, "scopes": []})
    assert ev["problem"] and not ev["shelved"] and ev["affects"]
