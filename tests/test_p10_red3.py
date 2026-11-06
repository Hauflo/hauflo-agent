"""Red team round 3 (п.10) on the wizard of other integrations (п.57): what the plan may still never touch. Each case
failed on the agent before its fix."""

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er, label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hauflo as hauflo
from custom_components.hauflo import foreign
from tests.test_agent_tunnel import Out
from tests.test_foreign_wizard import Send, _errors, _plan, house  # noqa: F401 — the fixture

E, D = foreign.ENTITY_UPDATE, foreign.DEVICE_UPDATE


async def _refusals(hass, hass_client, house, extra_entities, cases):  # noqa: F811
    plan = {**house["plan"], "entities": house["plan"]["entities"] + extra_entities}
    await _plan(hass, hass_client, plan)
    send = Send(house["agent"])
    with Out(house["agent"]) as out, patch.object(hauflo.backup, "fresh", return_value=True):
        for msg, _ in cases:
            await send(**msg)
    errs = _errors(out)
    got = [(msg, e["message"]) for (msg, _), e in zip(cases, errs)]
    want = [(msg, f"Hauflo agent: not allowed ({why})") for msg, why in cases]
    return house["local"].sent, got, want, len(errs)


async def test_protected_beyond_the_three_prefixes(hass: HomeAssistant, hass_client, house) -> None:  # noqa: F811
    """A gate (another integration's cover of class gate) and a group with the lock inside: never placed in a room nor
    given a role by the wizard — the card would draw them where one tap is near (the same _protected as voice / scenes)."""
    ents, d, a = er.async_get(hass), house["devices"], house["areas"]
    gate = ents.async_get_or_create("cover", "zha", "u-gate", suggested_object_id="gate", device_id=d["hub"], original_device_class="gate").entity_id
    grp = ents.async_get_or_create("light", "group", "u-grp", suggested_object_id="with_lock").entity_id
    hass.states.async_set(grp, "on", {"entity_id": ["lock.door"]})
    cases = [
        ({"type": E, "entity_id": gate, "area_id": a["Hall"]}, "protected_domain"),
        ({"type": E, "entity_id": gate, "labels": ["hauflo_role_covers"]}, "protected_domain"),
        ({"type": E, "entity_id": grp, "labels": ["hauflo_role_lights"]}, "protected_domain"),
    ]
    sent, got, want, n = await _refusals(hass, hass_client, house, [gate, grp], cases)
    assert sent == [] and n == len(cases), sent
    assert got == want


async def test_ha_machinery_never_the_wizards(hass: HomeAssistant, hass_client, house) -> None:  # noqa: F811
    """A device-less entity of HA's own machinery (a person, an automation, a zone) has a unique_id too — the plan must
    not make it hidden / labelled / placed: only the domains the cards draw."""
    ents = er.async_get(hass)
    person = ents.async_get_or_create("person", "person", "anna", suggested_object_id="anna").entity_id
    auto = ents.async_get_or_create("automation", "automation", "a1", suggested_object_id="morning").entity_id
    cases = [
        ({"type": E, "entity_id": person, "hidden_by": "user"}, "system_entity"),
        ({"type": E, "entity_id": auto, "labels": ["hauflo_role_status"]}, "system_entity"),
    ]
    sent, got, want, n = await _refusals(hass, hass_client, house, [person, auto], cases)
    assert sent == [] and n == len(cases), sent
    assert got == want


async def test_role_labels_only_of_the_canon(hass: HomeAssistant, hass_client, house) -> None:  # noqa: F811
    """hauflo_role_<anything> made the agent create a label per name: 10 a message, every message — the label registry
    flooded. Only the canon's roles (the card's)."""
    d = house["devices"]
    before = len(lr.async_get(hass).async_list_labels())
    cases = [({"type": D, "device_id": d["leak"], "labels": [f"hauflo_role_x{i}" for i in range(10)]}, "label_not_hauflo_role")]
    sent, got, want, _ = await _refusals(hass, hass_client, house, [], cases)
    assert sent == [] and got == want
    assert len(lr.async_get(hass).async_list_labels()) == before


async def test_names_without_control_or_bidi(hass: HomeAssistant, hass_client, house) -> None:  # noqa: F811
    d = house["devices"]
    cases = [
        ({"type": D, "device_id": d["leak"], "name_by_user": "Протечка‮ кухня"}, "bad_name"),
        ({"type": E, "entity_id": "binary_sensor.leak", "name": "Протечка\x07"}, "bad_name"),
        ({"type": E, "entity_id": "sensor.leak_battery", "name": "⁦Батарея"}, "bad_name"),
    ]
    sent, got, want, _ = await _refusals(hass, hass_client, house, [], cases)
    assert sent == [] and got == want
    # The older path (п.63.7, no plan: a name from Hauflo's list) — the same rule.
    agent = house["agent"]
    foreign.flags(hass).plans.clear()
    assert not hauflo._ws_allowed({"id": 1, "type": E, "entity_id": "sensor.hub_link", "name": "Hub‮"}, hass, agent.owned, agent.owned_entries)
    assert hauflo._ws_allowed({"id": 1, "type": E, "entity_id": "sensor.hub_link", "name": "Hub"}, hass, agent.owned, agent.owned_entries)


def test_titles_lose_the_contact_not_the_hub() -> None:
    got = hauflo._scrub_entries(
        [
            {"entry_id": "a", "domain": "tuya", "title": "Tuya (+7 912 345-67-89)"},
            {"entry_id": "b", "domain": "google_mail", "title": "ivan.petrov@gmail.com"},
            {"entry_id": "c", "domain": "mqtt", "title": "mqtt://user:secret@192.168.1.5"},
            {"entry_id": "d", "domain": "zha", "title": "Zigbee"},
        ]
    )
    titles = [e["title"] for e in got]
    assert titles[0] == "Tuya (…)" and titles[1] is None and titles[3] == "Zigbee"
    assert "secret" not in str(titles) and "@" not in str(titles) and "912" not in str(titles)
