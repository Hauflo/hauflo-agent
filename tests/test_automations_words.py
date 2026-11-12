"""Р83 polish: a rule's automation says in words what it does (its description, in the language of the page it was saved
from), every refusal is said in that language (never a key or an id), a name is never empty or taken twice, an edited
rule stays in its place in automations.yaml, and «вся комната» / «везде» are HA's own targets where HA follows them."""

import json

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.target import TargetSelectorData, async_extract_referenced_entity_ids

from custom_components.hauflo import _automations

from .test_automations import BLIND, HAND, LAMP, LIGHT, T1, URL, _file, _sensors, bus, entry  # noqa: F401
from .test_automations_v2 import BTN, _v2

pytestmark = pytest.mark.parametrize("expected_lingering_timers", [True])
WORKDAYS = ["mon", "tue", "wed", "thu", "fri"]


async def _post(client, body, status=200):
    res = await client.post(URL, json=body)
    text = await res.text()
    assert res.status == status, text
    return json.loads(text)


def _auto(hass, rid):
    return next(a for a in _file(hass) if a.get("id") == rid)


def _say(hass, key, lang, **ph):
    tx = _automations(hass).tx
    return tx.say(key, lang == "ru", **ph)


RULE = {
    "name": "Утро",
    "when": {"event": "time", "times": ["07:30", "07:00"]},
    "days": WORKDAYS,
    "only_if": [{"if": "dark"}],
    "actions": [{"do": "off", "target": {"domain": "light", "area_id": ["AREA"]}}, {"do": "position", "target": {"entity_id": [BLIND]}, "value": 40}],
}
WORDS = {
    "ru": "Каждый будний день в 07:00 и 07:30: выключить — свет (Гостиная); положение 40 % — living blind. Только если темно. Создано в Hauflo.",
    "en": "Every weekday at 07:00 and 07:30: switch off — lights (Гостиная); position 40 % — living blind. Only if it's dark. Made in Hauflo.",
}


@pytest.mark.parametrize("lang", ["ru", "en"])
async def test_description_in_the_pages_language(hass: HomeAssistant, hass_client, entry, bus, lang) -> None:
    """The page's language, not HA's (HA here is English): the days, the times, the room by its name, the canon's action."""
    reg = er.async_get(hass)
    reg.async_update_entity(HAND, area_id=None)
    hass.config.language = "en"
    client = await hass_client()
    rule = json.loads(json.dumps(RULE).replace("AREA", entry.area))
    saved = await _post(client, {"op": "save", "rule": rule, "lang": lang})
    cfg = _auto(hass, saved["id"])
    assert cfg["description"] == WORDS[lang]
    assert cfg["alias"] == "Hauflo · Утро"


async def test_description_of_every_trigger_and_repeat(hass: HomeAssistant, hass_client, entry, bus) -> None:
    """A reading held for minutes, motion, presence, a phone's alarm, a repeated press — each in words, devices by name;
    blinds of a room by HA's own room target can't be — the description says how many it took when saved."""
    await _v2(hass, entry)
    _sensors(hass)
    hass.states.async_set("binary_sensor.living_motion", "off", {"device_class": "motion", "friendly_name": "Движение"})
    er.async_get(hass).async_get_or_create("binary_sensor", "zha", "u-mo", suggested_object_id="living_motion")
    client = await hass_client()
    act = {"do": "press", "target": {"entity_id": [BTN]}, "repeat": {"count": 3, "every_s": 90}}
    covers = {"do": "close", "target": {"domain": "cover", "area_id": [entry.area]}}
    cases = {
        "value": ({"event": "value", "sensors": [T1], "op": "below", "x": 18.5, "for_min": 75}, "Когда температура «Sensor.living temp» ниже 18,5 °C в течение 1 ч 15 мин: нажать — Living bell, повторить 3 раза через 90 с; "),
        "state": ({"event": "state", "kind": "motion", "sensors": ["binary_sensor.living_motion"], "to": "on", "for_min": 0}, "Когда есть движение (Движение)"),
        "presence": ({"event": "presence", "to": "away"}, "Когда все ушли"),
        "sunset": ({"event": "sunset", "offset": 90}, "Через 1 ч 30 мин после заката"),
    }
    for key, (when, start) in cases.items():
        saved = await _post(client, {"op": "save", "rule": {"name": key, "when": when, "actions": [act, covers]}, "lang": "ru"})
        text = _auto(hass, saved["id"])["description"]
        assert text.startswith(start), (key, text)
        if key != "value":
            assert ": нажать — Living bell, повторить 3 раза через 90 с; " in text, text
        assert "закрыть — шторы и жалюзи (Гостиная; 1 устройство на момент сохранения)" in text, text
        assert text.endswith(". Создано в Hauflo."), text
        assert "hauflo_" not in text and "automations_" not in text and "rules_" not in text


async def test_refusals_said_in_the_request_language(hass: HomeAssistant, hass_client, entry, bus) -> None:
    """A refusal is a sentence of the page's language with the device / field by name: never «press: rules_bad_repeat»."""
    await _v2(hass, entry)
    client = await hass_client()
    await _automations(hass).texts()
    base = {"when": {"event": "time", "times": ["07:00"]}, "actions": [{"do": "on", "target": {"entity_id": [LIGHT]}}]}
    for lang in ("ru", "en"):
        got = await _post(client, {"op": "save", "rule": {**base, "days": []}, "lang": lang}, 400)
        assert got == {"message": _say(hass, "automations_bad_days", lang), "code": "bad_days", "key": "automations_bad_days"}
        # The canon's own refusal of an action (rules.py): its text, not its key.
        got = await _post(client, {"op": "save", "rule": {**base, "actions": [{"do": "press", "target": {"entity_id": [BTN]}, "repeat": {"count": 11, "every_s": 2}}]}, "lang": lang}, 400)
        assert got["key"] == "rules_bad_repeat" and "{" not in got["message"] and got["message"].split(":")[0] == _say(hass, "rules_bad_repeat", lang).split(":")[0], got
        # A device that may not be picked: by its name.
        got = await _post(client, {"op": "save", "rule": {**base, "actions": [{"do": "on", "target": {"entity_id": [HAND]}}]}, "lang": lang}, 400)
        assert got["code"] == "no_device" and "integrator hand" in got["message"] and HAND not in got["message"], got
        # A number out of range: the field by its name.
        got = await _post(client, {"op": "save", "rule": {**base, "when": {"event": "sunset", "offset": 500}}, "lang": lang}, 400)
        assert got["code"] == "bad_range" and got["message"] == _say(hass, "automations_bad_range", lang, field=_say(hass, "automations_f_offset", lang), lo=-120, hi=120), got
        # A page out of date (a shape a person can't make): one sentence and the field for the log.
        got = await _post(client, {"op": "save", "rule": {**base, "when": "07:00"}, "lang": lang}, 400)
        assert got["code"] == "bad_request" and got["message"] == _say(hass, "automations_bad_request", lang)
        # No such rule any more.
        got = await _post(client, {"op": "delete", "id": "hauflo_auto_0123456789ab", "lang": lang}, 404)
        assert got == {"message": _say(hass, "automations_no_rule", lang), "code": "no_rule"}
    assert _say(hass, "automations_bad_days", "ru") != _say(hass, "automations_bad_days", "en")
    assert not any(str(a.get("id", "")).startswith("hauflo_auto_") for a in _file(hass))


async def test_names_never_empty_or_twice_and_an_edit_keeps_its_place(hass: HomeAssistant, hass_client, entry, bus) -> None:
    hass.config.language = "en"
    client = await hass_client()
    rule = {"when": {"event": "time", "times": ["07:00"]}, "actions": [{"do": "on", "target": {"entity_id": [LIGHT]}}]}
    a = await _post(client, {"op": "save", "rule": {**rule, "name": "Свет"}, "lang": "ru"})
    b = await _post(client, {"op": "save", "rule": {**rule, "name": "Свет"}, "lang": "ru"})
    c = await _post(client, {"op": "save", "rule": {**rule, "name": ""}, "lang": "ru"})
    d = await _post(client, {"op": "save", "rule": {**rule, "name": "  "}, "lang": "en"})
    assert [_auto(hass, x["id"])["alias"] for x in (a, b, c, d)] == ["Hauflo · Свет", "Hauflo · Свет (2)", "Hauflo · Автоматизация", "Hauflo · Automation"]
    order = [x.get("id") for x in _file(hass)]
    # Edit the first: same place in the file, its own name kept (not «(3)»), the new words.
    await _post(client, {"op": "save", "rule": {**rule, "id": a["id"], "name": "Свет", "when": {"event": "time", "times": ["08:00"]}}, "lang": "ru"})
    assert [x.get("id") for x in _file(hass)] == order
    first = _auto(hass, a["id"])
    assert first["alias"] == "Hauflo · Свет" and first["description"].startswith("В 08:00")
    # A third «Свет» after the second was renamed away takes the free «(2)».
    await _post(client, {"op": "save", "rule": {**rule, "id": b["id"], "name": "Ночь"}, "lang": "ru"})
    e = await _post(client, {"op": "save", "rule": {**rule, "name": "Свет"}, "lang": "ru"})
    assert _auto(hass, e["id"])["alias"] == "Hauflo · Свет (2)"
    assert len({x.get("alias") for x in _file(hass)}) == len(_file(hass))


async def test_room_lights_follow_the_room(hass: HomeAssistant, hass_client, entry, bus) -> None:
    """«Свет в комнате»: HA's area target — a lamp put in the room after the rule was saved is switched by it."""
    er.async_get(hass).async_update_entity(HAND, area_id=None)
    client = await hass_client()
    saved = await _post(client, {"op": "save", "rule": {"when": {"event": "time", "times": ["07:00"]}, "actions": [{"do": "off", "target": {"domain": "light", "area_id": [entry.area]}}]}, "lang": "en"})
    cfg = _auto(hass, saved["id"])
    target = cfg["actions"][0]["target"]
    assert target == {"area_id": [entry.area]}
    assert "as of saving" not in cfg["description"]
    reg = er.async_get(hass)
    reg.async_get_or_create("light", "zha", "u-new", suggested_object_id="new_lamp")
    hass.states.async_set("light.new_lamp", "off", {"supported_color_modes": ["onoff"]})
    reg.async_update_entity("light.new_lamp", area_id=entry.area)
    got = async_extract_referenced_entity_ids(hass, TargetSelectorData(target))
    assert {LIGHT, LAMP, "light.new_lamp"} <= got.indirectly_referenced


async def test_lights_everywhere_follow_the_house_unless_one_is_hidden(hass: HomeAssistant, hass_client, entry, bus) -> None:
    """«Свет везде»: HA's `entity_id: all` while every light of the house may be picked (a new room's lamp follows); one
    hidden light (the integrator's) — the list, and the description says how many it took."""
    client = await hass_client()
    rule = {"when": {"event": "time", "times": ["23:00"]}, "actions": [{"do": "off", "target": {"domain": "light", "all": True}}]}
    snap = await _post(client, {"op": "save", "rule": rule, "lang": "ru"})
    cfg = _auto(hass, snap["id"])
    assert cfg["actions"][0]["target"] != {"entity_id": "all"} and HAND not in json.dumps(cfg["actions"])
    assert "свет (везде; 2 устройства на момент сохранения)" in cfg["description"]
    er.async_get(hass).async_update_entity(HAND, hidden_by=None)
    house = await _post(client, {"op": "save", "rule": rule, "lang": "ru"})
    cfg = _auto(hass, house["id"])
    assert cfg["actions"][0] == {"action": "light.turn_off", "target": {"entity_id": "all"}}
    assert "свет (везде)" in cfg["description"]
    # A light group there: a toggle by the house would switch the group and its lamps — each back; the list instead.
    reg = er.async_get(hass)
    reg.async_get_or_create("light", "group", "u-grp", suggested_object_id="all_living")
    hass.states.async_set("light.all_living", "off", {"supported_color_modes": ["onoff"], "entity_id": [LIGHT, LAMP]})
    tog = await _post(client, {"op": "save", "rule": {**rule, "actions": [{"do": "toggle", "target": {"domain": "light", "all": True}}]}, "lang": "en"})
    cfg = _auto(hass, tog["id"])
    assert cfg["actions"][0]["target"] != {"entity_id": "all"} and "as of saving" in cfg["description"]


async def test_room_toggle_with_a_group_by_the_list(hass: HomeAssistant, hass_client, entry, bus) -> None:
    reg = er.async_get(hass)
    reg.async_update_entity(HAND, area_id=None)
    reg.async_get_or_create("light", "group", "u-grp", suggested_object_id="all_living")
    hass.states.async_set("light.all_living", "off", {"supported_color_modes": ["onoff"], "entity_id": [LIGHT, LAMP]})
    reg.async_update_entity("light.all_living", area_id=entry.area)
    client = await hass_client()
    rule = {"when": {"event": "time", "times": ["07:00"]}, "actions": [{"do": "toggle", "target": {"domain": "light", "area_id": [entry.area]}}]}
    saved = await _post(client, {"op": "save", "rule": rule, "lang": "en"})
    target = _auto(hass, saved["id"])["actions"][0]["target"]
    assert "area_id" not in target, target
    off = await _post(client, {"op": "save", "rule": {**rule, "actions": [{"do": "off", "target": {"domain": "light", "area_id": [entry.area]}}]}, "lang": "en"})
    assert _auto(hass, off["id"])["actions"][0]["target"] == {"area_id": [entry.area]}
    assert ar.async_get(hass).async_get_area(entry.area).name == "Гостиная"
