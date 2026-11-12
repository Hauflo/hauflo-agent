"""Р83 «Правила» v2 (automations.py): one action form on the canon (rules_canon.json actions_by_domain) — target
entities / rooms + domain / the whole house, several times a day, «повторить N раз через M с», the new client domains.
Every rule saved in the old forms (set / room_off / room_covers / per-entity, `at`) makes byte for byte the automation it
made before v2 (fixtures/automations_v1_yaml.json, written by the v1 code), and a stored v1 rule is migrated on load
without a rewrite of automations.yaml."""

import json
import os
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.hauflo import _automations

from .test_automations import BLIND, HAND, LAMP, LIGHT, LOCK, PHONE_ALARM, SCENE, _file, bus, entry  # noqa: F401

pytestmark = pytest.mark.parametrize("expected_lingering_timers", [True])
SNAP = Path(__file__).parent / "fixtures" / "automations_v1_yaml.json"
RID = "hauflo_auto_0123456789ab"
CLIMATE, FAN, PLUG, PUMP = "climate.living", "fan.living", "switch.living_plug", "switch.pump"
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _more(hass: HomeAssistant, entry) -> None:
    reg = er.async_get(hass)
    for eid, uid, extra in (
        (CLIMATE, "u-climate", {"supported_features": 1, "min_temp": 7, "max_temp": 30, "target_temp_step": 0.5, "hvac_modes": ["off", "heat", "cool"], "temperature": 21}),
        (FAN, "u-fan", {"supported_features": 48}),
        (PLUG, "u-plug", {"device_class": "outlet"}),
        (PUMP, "u-pump", {}),
    ):
        domain, obj = eid.split(".")
        reg.async_get_or_create(domain, "knx", uid, suggested_object_id=obj)
        hass.states.async_set(eid, "off", {"friendly_name": obj, **extra})
        reg.async_update_entity(eid, area_id=entry.area)
    entry.runtime_data.owned |= {"u-climate", "u-fan", "u-plug", "u-pump"}


def v1_forms(area: str) -> dict[str, dict]:
    """Every action form and trigger the v1 card / recipes / habits saved."""
    return {
        "per_entity": {"when": {"event": "sunset", "offset": -15}, "days": DAYS, "actions": [{"entity_id": BLIND, "do": "close"}, {"entity_id": LIGHT, "do": "on", "value": 30}, {"entity_id": LAMP, "do": "off"}, {"entity_id": BLIND, "do": "open"}], "name": "Закат"},
        "position_scene": {"when": {"event": "sunrise", "offset": 20}, "days": ["mon", "fri"], "actions": [{"entity_id": BLIND, "do": "position", "value": 40}, {"entity_id": SCENE, "do": "on"}]},
        "room_off": {"when": {"event": "time", "at": "23:30"}, "days": ["sat", "sun"], "actions": [{"do": "room_off", "area_id": area}, {"entity_id": SCENE, "do": "on"}]},
        "room_covers": {"when": {"event": "time", "at": "21:00"}, "actions": [{"do": "room_covers", "area_id": area, "cover": "position", "value": 30}, {"do": "room_covers", "area_id": area, "cover": "close"}]},
        "set": {"when": {"event": "time", "at": "07:00"}, "actions": [{"do": "set", "domain": "light", "cmd": "on", "all": True, "value": 60}, {"do": "set", "domain": "cover", "cmd": "position", "rooms": [area], "value": 40}, {"do": "set", "domain": "scene", "cmd": "on", "entities": [SCENE]}, {"do": "set", "domain": "light", "cmd": "off", "rooms": [area], "entities": [LIGHT]}, {"do": "set", "domain": "cover", "cmd": "open", "all": True}, {"do": "set", "domain": "light", "cmd": "on", "entities": [LAMP]}]},
        "climate_fan_plug": {"when": {"event": "time", "at": "06:30"}, "actions": [{"entity_id": CLIMATE, "do": "temp", "value": 21.3}, {"entity_id": CLIMATE, "do": "heat"}, {"entity_id": CLIMATE, "do": "off"}, {"entity_id": FAN, "do": "on"}, {"entity_id": FAN, "do": "off"}, {"entity_id": PLUG, "do": "on"}, {"entity_id": PLUG, "do": "off"}]},
        "ramp_android": {"when": {"event": "alarm_android", "sensor": PHONE_ALARM, "offset": -20}, "from": "wake_light", "actions": [{"entity_id": LIGHT, "do": "ramp", "value": 25, "minutes": 20}, {"entity_id": BLIND, "do": "position", "value": 40}]},
        "two_ramps": {"when": {"event": "time", "at": "06:00"}, "actions": [{"entity_id": LIGHT, "do": "ramp", "value": 50, "minutes": 5}, {"entity_id": "light.bed", "do": "ramp", "value": 10, "minutes": 3}]},
        "iphone_days": {"when": {"event": "alarm_iphone"}, "days": ["mon", "tue"], "actions": [{"entity_id": BLIND, "do": "open"}]},
        "only_if": {"when": {"event": "time", "at": "23:00"}, "only_if": [{"if": "between", "after": "22:00", "before": "06:00"}, {"if": "dark"}], "actions": [{"do": "set", "domain": "light", "cmd": "off", "all": True}]},
        "presence": {"when": {"event": "presence", "to": "away"}, "actions": [{"do": "room_off", "area_id": area}]},
    }


def _setup_v1(hass: HomeAssistant, entry, hand: bool = True) -> None:
    _more(hass, entry)
    reg = er.async_get(hass)
    reg.async_get_or_create("light", "knx", "u-bed", suggested_object_id="bed")
    hass.states.async_set("light.bed", "off", {"supported_color_modes": ["brightness"]})
    entry.runtime_data.owned |= {"u-bed"}
    if hand:
        reg.async_update_entity(HAND, area_id=entry.area)  # a room with a hidden light: by name, else the room itself


def _v1_yaml(hass: HomeAssistant, entry) -> dict[str, dict]:
    eng = _automations(hass)
    out = {}
    for key, raw in v1_forms(entry.area).items():
        cfg = eng.automation(RID, eng.validate(raw))
        if raw["when"]["event"] == "alarm_iphone":
            cfg["triggers"] = [{"trigger": "event", "event_type": "CODE"}]
        out[key] = cfg
    return out


@pytest.mark.parametrize("hand", [True, False])
async def test_v1_forms_make_the_same_yaml(hass: HomeAssistant, entry, bus, hand) -> None:
    _setup_v1(hass, entry, hand)
    await _automations(hass).texts()
    got = json.loads(json.dumps(_v1_yaml(hass, entry)).replace(entry.area, "AREA"))
    snap = SNAP.with_name(SNAP.stem + ("" if hand else "_room") + ".json")
    if os.environ.get("SNAP_WRITE"):
        snap.write_text(json.dumps(got, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    want = json.loads(snap.read_text())
    assert set(got) == set(want)
    for key in want:
        assert got[key] == want[key], key


# --- v2 ----------------------------------------------------------------------------------------------------------------
BTN, MODE, LEVEL = "button.living_bell", "select.living_mode", "number.living_level"
URL = "/api/hauflo/automations"


async def _v2(hass: HomeAssistant, entry) -> None:
    """The new client domains: a button, a select, a number, scripts (a safe one, one that unlocks the door, one that
    runs that one)."""
    from homeassistant.setup import async_setup_component  # noqa: PLC0415

    _more(hass, entry)
    reg = er.async_get(hass)
    for eid, extra in (
        (BTN, {}),
        (MODE, {"options": ["comfort", "eco", "off"]}),
        (LEVEL, {"min": 0, "max": 40, "step": 0.5, "unit_of_measurement": "°C"}),
    ):
        domain, obj = eid.split(".")
        reg.async_get_or_create(domain, "knx", f"u-{obj}", suggested_object_id=obj)
        hass.states.async_set(eid, "unknown", {"friendly_name": obj, **extra})
        reg.async_update_entity(eid, area_id=entry.area)
    seq = lambda action, eid: {"sequence": [{"action": action, "target": {"entity_id": eid}}]}  # noqa: E731
    assert await async_setup_component(hass, "script", {"script": {"evening": seq("light.turn_on", LIGHT), "unlock": seq("lock.unlock", LOCK), "calls_unlock": seq("script.turn_on", "script.unlock")}})
    await hass.async_block_till_done()


def _rule(actions, **when):
    return {"when": {"event": "time", "times": ["19:30", "07:00"], **when}, "actions": actions}


async def _save(client, rule, status=200):
    res = await client.post(URL, json={"op": "save", "rule": rule})
    assert res.status == status, await res.text()
    return await res.json()


async def test_v2_actions_make_ha_yaml(hass: HomeAssistant, hass_client, entry, bus) -> None:
    """press ×3 every 2 s → HA's repeat; a script, a select's option, a number's value (its step), a fan's speed, a room,
    a pulse repeated (the pause counted from its start), several times a day — each the canon's service with its field."""
    await _v2(hass, entry)
    hass.states.async_set(FAN, "off", {"supported_features": 49})
    client = await hass_client()
    saved = await _save(client, _rule([
        {"do": "press", "target": {"entity_id": [BTN]}, "repeat": {"count": 3, "every_s": 2}},
        {"do": "run", "target": {"entity_id": ["script.evening"]}},
        {"do": "option", "target": {"entity_id": [MODE]}, "value": "eco"},
        {"do": "value", "target": {"entity_id": [LEVEL]}, "value": 21.3},
        {"do": "speed", "target": {"entity_id": [FAN]}, "value": 40},
        {"do": "off", "target": {"domain": "light", "area_id": [entry.area]}},
        {"do": "pulse", "target": {"entity_id": [PLUG]}, "value": 3, "repeat": {"count": 2, "every_s": 10}},
    ]))
    assert saved["when"] == {"event": "time", "times": ["07:00", "19:30"]}
    assert saved["actions"][3]["value"] == 21.5  # the number's own step
    [cfg] = [a for a in _file(hass) if a["id"] == saved["id"]]
    assert cfg["triggers"] == [{"trigger": "time", "at": ["07:00:00", "19:30:00"]}]
    acts = cfg["actions"]
    assert acts[0] == {"repeat": {"count": 3, "sequence": [{"action": "button.press", "target": {"entity_id": [BTN]}}, {"delay": {"seconds": 2}}]}}
    assert acts[1] == {"action": "script.turn_on", "target": {"entity_id": ["script.evening"]}}
    assert acts[2] == {"action": "select.select_option", "target": {"entity_id": [MODE]}, "data": {"option": "eco"}}
    assert acts[3] == {"action": "number.set_value", "target": {"entity_id": [LEVEL]}, "data": {"value": 21.5}}
    assert acts[4] == {"action": "fan.set_percentage", "target": {"entity_id": [FAN]}, "data": {"percentage": 40}}
    assert acts[5]["action"] == "light.turn_off" and acts[5]["target"] in ({"area_id": [entry.area]}, {"entity_id": [LIGHT, LAMP]})
    on, off = ({"action": f"switch.turn_{x}", "target": {"entity_id": [PLUG]}} for x in ("on", "off"))
    assert acts[6] == {"repeat": {"count": 2, "sequence": [on, {"delay": {"seconds": 3}}, off, {"delay": {"seconds": 7}}]}}


@pytest.mark.parametrize(
    "action",
    [
        {"do": "on", "target": {"entity_id": [LOCK]}},  # a lock: never
        {"do": "open", "target": {"entity_id": ["cover.garage_door"]}},  # opens the house
        {"do": "on", "target": {"entity_id": [PUMP]}},  # a bare switch: may be a pump
        {"do": "on", "target": {"entity_id": [HAND]}},  # hidden by the integrator
        {"do": "run", "target": {"entity_id": ["script.unlock"]}},  # a script that unlocks the door
        {"do": "run", "target": {"entity_id": ["script.calls_unlock"]}},  # …or runs one that does
        {"do": "press", "target": {"domain": "button", "area_id": ["x"]}},  # «вся комната» only for lights, blinds…
        {"do": "press", "target": {"domain": "button", "all": True}},
        {"do": "on", "target": {"domain": "light", "area_id": ["nowhere"]}},  # a room without lights
        {"do": "on", "target": {"entity_id": [LIGHT, BLIND]}},  # two kinds
        {"do": "on", "target": {"entity_id": [LIGHT, LIGHT]}},
        {"do": "on", "target": {}},
        {"do": "on", "target": {"entity_id": [LIGHT]}, "extra": 1},
        {"do": "on", "target": {"entity_id": [LIGHT], "device_id": ["d"]}},
        {"do": "option", "target": {"entity_id": [MODE]}, "value": "turbo"},  # not its option
        {"do": "value", "target": {"entity_id": [LEVEL]}, "value": 41},  # over its max
        {"do": "speed", "target": {"entity_id": [FAN]}, "value": 40},  # this fan has no speed
        {"do": "press", "target": {"entity_id": [BTN]}, "repeat": {"count": 11, "every_s": 2}},
        {"do": "on", "target": {"entity_id": [LIGHT]}, "repeat": {"count": 2, "every_s": 2}},  # no repeat for «on»
        {"do": "unlock", "target": {"entity_id": [LIGHT]}},
        {"do": "notify", "target": {"entity_id": [LIGHT]}},
        {"do": "ramp", "target": {"entity_id": [LAMP]}, "value": 20, "minutes": 5},  # not dimmable
    ],
)
async def test_v2_refused(hass: HomeAssistant, hass_client, entry, bus, action) -> None:
    await _v2(hass, entry)
    await _save(await hass_client(), _rule([action]), 400)
    assert not any(str(a.get("id", "")).startswith("hauflo_auto_") for a in _file(hass))


@pytest.mark.parametrize("times", [[], ["07:00", "07:00"], ["7:00"], [f"{h:02d}:00" for h in range(9)], "07:00", ["{{ now() }}"]])
async def test_v2_bad_times(hass: HomeAssistant, hass_client, entry, bus, times) -> None:
    await _save(await hass_client(), {"when": {"event": "time", "times": times}, "actions": [{"do": "on", "target": {"entity_id": [LIGHT]}}]}, 400)


async def test_v2_between_checks_every_time(hass: HomeAssistant, hass_client, entry, bus) -> None:
    client = await hass_client()
    rule = {**_rule([{"do": "on", "target": {"entity_id": [LIGHT]}}]), "only_if": [{"if": "between", "after": "06:00", "before": "08:00"}]}
    await _save(client, rule, 400)  # 19:30 is never in it
    rule["when"]["times"] = ["07:00", "07:30"]
    await _save(client, rule)


async def test_v2_get_says_what_the_editor_needs(hass: HomeAssistant, hass_client, entry, bus) -> None:
    await _v2(hass, entry)
    got = await (await (await hass_client()).get(URL)).json()
    assert got["features"] == {"rules_v2": True}
    assert got["limits"]["times"] == 8 and got["limits"]["repeat"]["count"] == [2, 10] and "press" in got["limits"]["repeat"]["acts"]
    ents = {e["entity_id"]: e for e in got["targets"]["entities"]}
    assert {BTN, MODE, LEVEL, FAN, PLUG, CLIMATE, LIGHT, LAMP, BLIND, SCENE, "script.evening"} <= set(ents)
    assert not {LOCK, PUMP, HAND, "cover.garage_door", "script.unlock", "script.calls_unlock"} & set(ents)
    assert set(ents[BTN]["acts"]) == {"press"} and ents[BTN]["area"] == entry.area
    assert ents[MODE]["acts"]["option"]["options"] == ["comfort", "eco", "off"]
    assert ents[LEVEL]["acts"]["value"] == {"kind": "number", "min": 0, "max": 40, "step": 0.5, "unit": "°C"}
    assert "ramp" in ents[LIGHT]["acts"] and "ramp" not in ents[LAMP]["acts"] and "brightness" not in ents[LAMP]["acts"]
    assert ents[CLIMATE]["acts"]["setpoint"]["min"] == 7 and ents[CLIMATE]["acts"]["mode"]["options"] == ["off", "heat", "cool"]
    assert set(ents[FAN]["acts"]) == {"on", "off", "toggle"}  # no speed feature
    assert got["targets"]["group_domains"] == ["light", "cover", "switch", "fan", "climate"]
    assert got["targets"]["areas"][entry.area] == "Гостиная"


async def test_v1_rule_migrated_on_load_file_untouched(hass: HomeAssistant, hass_client, entry, bus) -> None:
    """Rules the v1 agent stored: rewritten in the canon form on load; automations.yaml not written (the same YAML); a
    rule edited in HA's own editor stays «changed» and untouched."""
    from homeassistant.util.yaml import dump  # noqa: PLC0415

    _setup_v1(hass, entry)
    eng = _automations(hass)
    await eng.texts()
    forms = v1_forms(entry.area)
    ids = {key: f"hauflo_auto_{i:012x}" for i, key in enumerate(("room_off", "set", "climate_fan_plug"))}
    stored = {}
    for key, rid in ids.items():
        raw = forms[key]
        rule = {"when": raw["when"], "days": raw.get("days", DAYS), "actions": raw["actions"], "enabled": True, "name": key}
        stored[rid] = rule
        await eng._put(rid, eng.automation(rid, rule))
    hand = ids["climate_fan_plug"]
    path = Path(hass.config.path("automations.yaml"))
    path.write_text(dump([a if a.get("id") != hand else {**a, "actions": [{"action": "light.turn_on", "target": {"entity_id": LIGHT}}]} for a in _file(hass)]))
    await eng.store.async_save({"rules": json.loads(json.dumps(stored))})
    before = path.read_text()
    got = await (await (await hass_client()).get(URL)).json()
    assert path.read_text() == before  # nothing rewritten
    rules = {r["id"]: r for r in got["rules"]}
    assert rules[hand]["changed"] is True and not rules[ids["set"]]["changed"] and not rules[ids["room_off"]]["changed"]
    assert rules[ids["room_off"]]["when"] == {"event": "time", "times": ["23:30"]}
    assert rules[ids["room_off"]]["actions"] == [{"do": "off", "target": {"domain": "light", "area_id": entry.area}}, {"do": "scene", "target": {"entity_id": SCENE}}]
    assert rules[hand]["actions"][:3] == [{"do": "setpoint", "target": {"entity_id": CLIMATE}, "value": 21.3}, {"do": "mode", "target": {"entity_id": CLIMATE}, "value": "heat"}, {"do": "mode", "target": {"entity_id": CLIMATE}, "value": "off"}]
    data = await eng.store.async_load()
    assert "_moved" not in data and all("at" not in r["when"] for r in data["rules"].values())
    # Each migrated rule makes the YAML the file has.
    for rid in (ids["room_off"], ids["set"]):
        assert eng.automation(rid, data["rules"][rid]) == next(a for a in _file(hass) if a.get("id") == rid)
