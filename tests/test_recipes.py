"""Client recipes (automation_recipes.json + recipes.py, p6d): the data is valid (RU+EN, defaults inside limits, only
what the engine has), the resolver offers a recipe only where the house has the devices, and the numbers are the
standard ones the docs name. No Home Assistant needed — the engine side is in test_automations.py."""

import copy

from custom_components.hauflo import recipes

DATA = recipes.DATA


def _house(**extra):
    t = {
        "areas": {"bed": "Спальня", "liv": "Living room", "hall": "Коридор", "bath": "Ванная"},
        "people": 1,
        "alarms": [],
        "lights": [{"entity_id": "light.bed", "area": "bed", "dim": True}, {"entity_id": "light.liv", "area": "liv", "dim": True}, {"entity_id": "light.hall", "area": "hall", "dim": True}],
        "rooms": [{"area_id": "bed", "name": "Спальня", "lights": 1}, {"area_id": "liv", "name": "Living room", "lights": 1}],
        "cover_rooms": [{"area_id": "bed", "name": "Спальня", "covers": 1, "position": True}, {"area_id": "liv", "name": "Living room", "covers": 2, "position": True}],
        "climates": [{"entity_id": "climate.bed", "area": "bed", "temp": True, "min": 7.0, "max": 30.0}, {"entity_id": "climate.bath", "area": "bath", "temp": True, "min": 7.0, "max": 30.0}],
        "fans": [{"entity_id": "fan.bath", "area": "bath"}, {"entity_id": "fan.bed", "area": "bed"}],
        "sensors": [
            {"entity_id": "sensor.bath_h", "area": "bath", "kind": "humidity", "unit": "%"},
            {"entity_id": "sensor.bed_co2", "area": "bed", "kind": "carbon_dioxide", "unit": "ppm"},
        ],
    }
    t.update(extra)
    return t


def _by_key(t):
    return {r["key"]: r for r in recipes.resolve(DATA, t)}


def test_data_is_valid():
    assert recipes.check(DATA) == []
    assert 15 <= len(DATA["recipes"]) <= 40


def test_every_text_and_parameter_has_ru_and_en_and_defaults_inside_limits():
    for r in DATA["recipes"]:
        for f in ("title", "sub"):
            assert r[f]["ru"] and r[f]["en"], (r["id"], f)
            assert f == "sub" or r[f]["ru"] != r[f]["en"], (r["id"], f)  # an untranslated title is a bug
        for p, s in r.get("params", {}).items():
            assert s["label"]["ru"] and s["label"]["en"], (r["id"], p)
            if s["type"] != "time":
                assert s["min"] <= s["default"] <= s["max"], (r["id"], p)


def test_check_catches_broken_data():
    bad = copy.deepcopy(DATA)
    bad["recipes"][0]["title"]["en"] = ""
    bad["recipes"][1]["id"] = bad["recipes"][0]["id"]
    p = next(r for r in bad["recipes"] if r.get("params"))
    k = next(iter(p["params"]))
    if p["params"][k]["type"] != "time":
        p["params"][k]["default"] = p["params"][k]["max"] + 1
    p["when"] = {"event": "teleport"}
    out = " ".join(recipes.check(bad))
    assert "title" in out and "unique" in out and "outside" in out and "when.event" in out


def test_full_house_offers_everything_and_each_rule_is_well_formed():
    t = _house(alarms=[{"entity_id": "sensor.pixel_next_alarm", "phone": "Pixel"}])
    got = _by_key(t)
    assert set(got) == {r["id"] for r in DATA["recipes"]}
    for key, r in got.items():
        assert r["rule"]["from"] == key and r["rule"]["actions"] and set(r["rule"]["days"]) <= set(recipes.DAYS)
        assert r["title"]["ru"] and r["title"]["en"] and "{" not in r["title"]["ru"] + r["sub"]["en"], key


def test_empty_house_offers_nothing_and_missing_devices_drop_only_their_recipes():
    assert recipes.resolve(DATA, {"areas": {}}) == []
    no_people = _by_key(_house(people=0))
    assert "away_off" not in no_people and "sunset_covers" in no_people
    no_fan = _by_key(_house(fans=[]))
    assert "bath_humidity_on" not in no_fan and "co2_fan_on" not in no_fan
    no_bath = _by_key(_house(climates=[{"entity_id": "climate.bed", "area": "bed", "temp": True, "min": 7.0, "max": 30.0}]))
    assert "bath_morning" not in no_bath and "night_climate" in no_bath
    plain = _by_key(_house(lights=[{"entity_id": "light.bed", "area": "bed", "dim": False}]))
    assert "wake_light" not in plain and "corridor_night" not in plain  # a ramp needs a dimmable light


def test_standard_values():
    got = _by_key(_house())
    assert got["sunset_covers"]["rule"]["when"] == {"event": "sunset", "offset": 0}
    assert got["sunrise_covers"]["rule"]["when"] == {"event": "sunrise", "offset": 30}
    assert {a["area_id"] for a in got["sunrise_covers"]["rule"]["actions"]} == {"liv"}  # not the bedroom
    assert got["night_off"]["rule"]["when"] == {"event": "time", "at": "23:00"}
    assert [a["value"] for a in got["away_climate"]["rule"]["actions"]] == [16.0, 16.0]  # DOE setback / Nest Eco
    assert got["away_climate"]["rule"]["when"] == {"event": "presence", "to": "away"}
    assert got["bath_morning"]["rule"]["actions"] == [{"entity_id": "climate.bath", "do": "temp", "value": 24.0}]
    assert got["home_lights_dark"]["rule"]["only_if"] == [{"if": "dark"}]
    on, off = got["co2_fan_on"], got["bath_humidity_off"]
    assert on["rule"]["when"]["op"] == "above" and on["rule"]["when"]["x"] == 1000 and on["rule"]["when"]["sensors"] == ["sensor.bed_co2"]
    assert off["rule"]["when"]["op"] == "below" and off["rule"]["when"]["x"] == 55


def test_climate_is_kept_inside_the_thermostats_own_limits():
    narrow = [{"entity_id": "climate.bed", "area": "bed", "temp": True, "min": 18.0, "max": 24.0}]
    got = _by_key(_house(climates=narrow))
    assert got["away_climate"]["rule"]["actions"][0]["value"] == 18.0


def test_alarm_android_and_iphone():
    android = _by_key(_house(alarms=[{"entity_id": "sensor.pixel_next_alarm", "phone": "Pixel"}]))["wake_light"]
    assert android["rule"]["when"] == {"event": "alarm_android", "sensor": "sensor.pixel_next_alarm", "offset": -20}
    assert android["rule"]["actions"] == [{"entity_id": "light.bed", "do": "ramp", "value": 25, "minutes": 20}]
    assert "Pixel" in android["sub"]["ru"] and "Pixel" in android["sub"]["en"]
    iphone = _by_key(_house())["wake_light"]
    assert iphone["rule"]["when"] == {"event": "alarm_iphone"} and "iPhone" in iphone["sub"]["en"]
    assert [p["id"] for p in android["params"]] == ["lead", "m", "b"]


def test_check_catches_more_broken_recipes():
    bad = copy.deepcopy(DATA)
    bad["matchers"]["broken"] = "("
    bad["alarm_src"]["x"] = {"ru": "", "en": "a"}
    bad["limits"]["max_recipes"] = 1
    r0, r1 = bad["recipes"][0], bad["recipes"][1]
    r1["id"] = r0["id"]  # duplicate id
    r0["group"] = "nope"
    r0["basis"] = "short"
    r0["params"]["off"].update(type="weird")
    r0["params"]["t"] = {"type": "time", "default": "25:99", "label": r0["title"]}
    r0["params"]["n"] = {"type": "int", "default": 999, "min": 0, "max": 5, "label": r0["title"]}
    r0["when"]["event"] = "bogus"
    r0["alarm"] = True
    r0["days"] = ["xx"]
    r0["actions"] = [{"do": "nope"}]
    r0["when"]["x"] = "$ghost"
    out = " ".join(bad_out := recipes.check(bad))
    for part in ("not a regex", "alarm_src", "count out of limits", "unique", "group", "basis", "type", "default HH:MM", "outside", "unit", "unknown parameters", "when.event", "alarm", "days", "actions"):
        assert part in out, part
    r2 = copy.deepcopy(DATA["recipes"][0])
    r2["id"] = "m"
    r2["actions"][0]["match"] = "no_such_matcher"
    ok = copy.deepcopy(DATA)
    ok["recipes"] = [r2]
    assert "matcher no_such_matcher" in " ".join(recipes.check(ok))


def test_validate_drops_refused_recipes_and_two_sensors_are_averaged():
    t = _house()
    assert len(recipes.resolve(DATA, t, lambda rule: None)) == len(recipes.resolve(DATA, t))

    def refuse(rule):
        raise ValueError("engine says no")

    assert recipes.resolve(DATA, t, refuse) == []
    two = _house(sensors=[
        {"entity_id": "sensor.bath_h1", "area": "bath", "kind": "humidity", "unit": "%"},
        {"entity_id": "sensor.bath_h2", "area": "bath", "kind": "humidity", "unit": "%"},
    ])
    rule = _by_key(two)["bath_humidity_on"]["rule"]
    assert rule["when"]["stat"] == "mean" and len(rule["when"]["sensors"]) == 2


def test_set_action_needs_lights():
    sets = [r["id"] for r in DATA["recipes"] if any(a["do"] == "set" for a in r["actions"])]
    for rid in sets:
        assert rid not in _by_key(_house(lights=[]))


def test_wake_light_follows_the_phone_whose_alarm_ha_reads_now():
    """Acceptance A 06.11: the first phone by name had no «next alarm» (unavailable) — the rule followed a dead sensor."""
    alarms = [
        {"entity_id": "sensor.iphone_next_alarm", "phone": "Anna", "next": "unavailable"},
        {"entity_id": "sensor.pixel_next_alarm", "phone": "Pixel", "next": "2026-11-07T06:30:00+00:00"},
        {"entity_id": "sensor.old_next_alarm", "phone": "Zed", "next": None},
    ]
    wake = _by_key(_house(alarms=alarms))["wake_light"]["rule"]
    assert wake["when"]["sensor"] == "sensor.pixel_next_alarm"
    # None reads now: the first by name stays the target rather than no recipe at all
    dead = _by_key(_house(alarms=[alarms[0], alarms[2]]))["wake_light"]["rule"]
    assert dead["when"]["sensor"] == "sensor.iphone_next_alarm"

