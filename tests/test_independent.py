"""«Сделать дом независимым» (service-company review 29.10): the owner, in the integration's options, keeps the dashboard
cards for good and turns Hauflo's alarm rules into plain HA automations — the same sensors and states, the same
phones, critical where the rule is, the rule's button — written like the dashboard automations (plain-file check,
backup copy); nothing is deleted, a second run adds nothing twice."""

from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import dump, load_yaml
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import independent, keep, notify
from custom_components.hauflo.const import CONF_ALLOW_CHANGES, CONF_RANGES, CONF_SECRET, CONF_SERVER, DOMAIN

THEIRS = {"id": "1700000000000", "alias": "Integrator: porch at night", "triggers": [{"trigger": "state", "entity_id": "binary_sensor.porch_motion", "to": "on"}], "actions": [{"action": "light.turn_off", "target": {"entity_id": "light.porch"}}]}
PHONES = [{"service": "mobile_app_anna", "name": "Pixel Анны", "os": "android", "person": "person.anna"}, {"service": "mobile_app_ivan", "name": "iPhone Ивана", "os": "ios", "person": "person.ivan"}]
LEAK = "binary_sensor.leak_kitchen"
VALVE = "valve.water_main"
RULES = {
    "lang": "ru",
    "rules": [
        {"id": "leak", "kind": "leak", "title": "Протечка", "sources": [LEAK], "picked": [LEAK], "alarm_states": ["on"], "priority": "critical",
         "recipients": {"all": True}, "action": {"title": "Перекрыть воду", "service": "valve.close_valve", "entity_id": VALVE}},
        {"id": "boiler", "kind": "fault", "title": "Котёл", "sources": ["binary_sensor.boiler"], "picked": ["binary_sensor.boiler"], "alarm_states": ["on"], "recipients": {"all": True}, "enabled": False},
    ],
}


async def _idle(self):
    return None


@pytest.fixture
async def house(hass: HomeAssistant, tmp_path: Path):
    hass.config.config_dir = str(tmp_path)
    hass.config.language = "ru"
    (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
    (tmp_path / "automations.yaml").write_text(dump([THEIRS]))
    assert await async_setup_component(hass, "automation", {"automation": [THEIRS]})
    assert await async_setup_component(hass, "lovelace", {})
    assert await async_setup_component(hass, "persistent_notification", {})
    hass.states.async_set(LEAK, "off", {"device_class": "moisture", "friendly_name": "Протечка на кухне"})
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — Вилла", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"}, options={CONF_RANGES: ["1/1"]})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    engine = entry.runtime_data.engine
    # The water valve is Hauflo's own (the rule's button may close only such one).
    engine.config = notify._clean_rules(RULES, hass, lambda eid: eid == VALVE)
    engine.phones = lambda: PHONES  # type: ignore[method-assign]
    pushes: list[ServiceCall] = []
    closed: list[ServiceCall] = []
    for p in PHONES:
        hass.services.async_register("notify", p["service"], pushes.append)
    hass.services.async_register("valve", "close_valve", closed.append)
    return entry, tmp_path, pushes, closed


async def _run_options(hass: HomeAssistant, entry) -> str:
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {CONF_ALLOW_CHANGES: True, CONF_RANGES: "1/1", "independent": True})
    assert flow["type"] is FlowResultType.FORM and flow["step_id"] == "independent"  # told first
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {})
    assert flow["step_id"] == "independent_done"
    report = flow["description_placeholders"]["report"]
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {})
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    return report


async def test_alarm_rules_become_ha_automations_and_cards_stay(hass: HomeAssistant, house) -> None:
    entry, config, pushes, closed = house
    report = await _run_options(hass, entry)
    await hass.async_block_till_done()
    assert entry.options[CONF_RANGES] == ["1/1"]  # the settings saved as they were
    data = load_yaml(str(config / "automations.yaml"))
    assert data[0] == THEIRS  # the integrator's own untouched
    ours = [a for a in data if str(a["id"]).startswith(independent.PREFIX)]
    assert [a["alias"] for a in ours] == ["Авария: Протечка"]  # the disabled rule skipped, said
    assert "Котёл" in report and "Авария: Протечка" in report and "/config/www/hauflo" in report
    assert list(config.glob("automations.yaml.hauflo-bak-*"))  # a copy before the write
    assert keep.ADDED and await keep.async_independent(hass)
    assert (config / "www" / "hauflo" / "hauflo-cards.js").is_file()
    eid = er.async_get(hass).async_get_entity_id("automation", "automation", ours[0]["id"])
    assert eid and hass.states.get(eid).state == "on"
    # It works without Hauflo: the leak → both phones (critical for each OS) and a note in HA.
    hass.states.async_set(LEAK, "on", {"device_class": "moisture", "friendly_name": "Протечка на кухне"})
    await hass.async_block_till_done()
    by = {c.service: c.data for c in pushes}
    assert set(by) == {"mobile_app_anna", "mobile_app_ivan"}
    assert by["mobile_app_anna"]["message"] == "Протечка на кухне" and by["mobile_app_anna"]["title"] == "Протечка"
    assert by["mobile_app_anna"]["data"]["channel"] == "alarm_stream"
    assert by["mobile_app_ivan"]["data"]["push"]["interruption-level"] == "critical"
    button = by["mobile_app_ivan"]["data"]["actions"][0]
    assert button["title"] == "Перекрыть воду"
    # The push's button closes the valve, as the rule's did.
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": button["action"]})
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in closed] == [[VALVE]]
    # Again: nothing twice, nothing removed.
    report2 = await _run_options(hass, entry)
    assert "Уже были" in report2
    again = load_yaml(str(config / "automations.yaml"))
    assert [a["id"] for a in again] == [a["id"] for a in data]


async def test_a_hand_formatted_file_is_not_rewritten(hass: HomeAssistant, house) -> None:
    entry, config, _, _ = house
    text = "# the integrator's notes\n" + dump([THEIRS])
    (config / "automations.yaml").write_text(text)
    report = await _run_options(hass, entry)
    assert (config / "automations.yaml").read_text() == text
    assert "Автоматизации не созданы" in report
    # The cards are kept all the same.
    assert await keep.async_independent(hass)


async def test_no_rules_no_automations(hass: HomeAssistant, house) -> None:
    entry, config, _, _ = house
    entry.runtime_data.engine.config = notify._clean_rules({"rules": []})
    result = await independent.async_export(hass, entry.runtime_data.engine)
    assert result["added"] == [] and result["error"] is None
    assert load_yaml(str(config / "automations.yaml")) == [THEIRS]
    assert "не нужны" in independent.summary(result, True)
