"""«Уведомлять» from Hauflo's list (п.63.6), apart from the panel: the deploy sends {entity_id: bool}; True works as the
«Hauflo мониторинг» label (a leak sensor hidden from the room card still alarms), False silences one the label watches
(a rule picking it included), not in the map — the label decides. Hauflo's own are never silenced by it."""

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import foreign, notify
from custom_components.hauflo.const import CONF_RANGES, CONF_SECRET, CONF_SERVER, DOMAIN, MONITOR_LABEL

LEAK = "binary_sensor.neptun_leak"  # another integration's, no label: alarms by the switch
BOILER = "binary_sensor.boiler_fault"  # labelled, switched off in Hauflo
FLOOR = "sensor.gtc_floor"  # labelled, not in the map: the label decides
OWN = "binary_sensor.knx_leak"  # Hauflo's own


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"}, options={CONF_RANGES: ["1/1"]})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    reg = er.async_get(hass)
    label = lr.async_get(hass).async_create(MONITOR_LABEL)
    for eid, dc, state in ((LEAK, "moisture", "on"), (BOILER, "problem", "on"), (FLOOR, "temperature", "21"), (OWN, "moisture", "off")):
        reg.async_get_or_create(eid.split(".")[0], "zha", eid, suggested_object_id=eid.split(".")[1])
        hass.states.async_set(eid, state, {"device_class": dc})
    reg.async_update_entity(BOILER, labels={label.label_id})
    reg.async_update_entity(FLOOR, labels={label.label_id})
    entry.runtime_data.engine.owns = lambda eid: eid == OWN
    yield entry


async def test_the_switch_over_the_label(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    got = await (await client.get("/api/hauflo/foreign")).json()
    assert got["notify"] == {} and got["features"] == ["foreign_wizard", "foreign_plan_id"] and got["plan"] is False
    assert not notify.labelled(hass, LEAK) and notify.labelled(hass, BOILER)
    res = await client.post("/api/hauflo/foreign", json={"notify": {LEAK: True, BOILER: False, OWN: False}})
    assert res.status == 200, await res.text()
    assert notify.labelled(hass, LEAK) and not notify.labelled(hass, BOILER) and notify.labelled(hass, FLOOR)
    # Alarm sources: off silences a picked one too; Hauflo's own stays.
    assert notify._source_ok(hass, LEAK) and not notify._source_ok(hass, BOILER, picked=frozenset({BOILER}))
    assert notify._source_ok(hass, OWN, lambda e: e == OWN)
    # The status the cloud's monitoring / Telegram / mail read.
    mon = {m["entityId"]: m for m in entry.runtime_data.status()["monitored"]}
    assert set(mon) == {LEAK, FLOOR} and mon[LEAK]["problem"] is True
    # The whole map is replaced: empty — the label decides again.
    assert (await client.post("/api/hauflo/foreign", json={"notify": {}})).status == 200
    assert {m["entityId"] for m in entry.runtime_data.status()["monitored"]} == {BOILER, FLOOR}


async def test_bad_bodies_and_rights(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry) -> None:
    client = await hass_client()
    for body in ({"notify": {LEAK: "yes"}}, {"notify": {"not an id": True}}, {"notify": [LEAK]}, {"notify": {}, "x": 1}, [], {"notify": {f"sensor.s{i}": True for i in range(foreign.MAX_IDS + 1)}}):
        assert (await client.post("/api/hauflo/foreign", json=body)).status == 400, str(body)[:80]
    user = await hass_client(hass_read_only_access_token)
    assert (await user.post("/api/hauflo/foreign", json={"notify": {LEAK: True}})).status == 403
    assert (await user.get("/api/hauflo/foreign")).status == 403
    assert foreign.notify_flag(hass, LEAK) is None


async def test_kept_across_a_restart(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    await client.post("/api/hauflo/foreign", json={"notify": {LEAK: True}})
    await hass.async_block_till_done()
    again = foreign.Flags(hass)
    await again.async_load()
    assert again.notify == {LEAK: True}
