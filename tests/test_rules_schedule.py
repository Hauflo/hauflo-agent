"""«Правила» по расписанию и действия из канона (rules.py `when: "at"`, rules_canon.json «actions_by_domain», wave 5
п.13): on a real HA with a fake clock and a fake sun. Adversarial: a moment fires once and only once; a moment missed
while HA was off is said, not done late; «только если» false → said, nothing written; weekdays, sunrise / sunset ± N;
every new action (press, option, value, pulse, toggle, preset) really calls its HA service with the entity's own bounds;
what a save refuses (moments, offsets, options, bounds, a pulse shorter than the purpose's minimum, a lock, a foreign
button, the client's pulse); the API's features / targets / limits."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.hauflo import rules as rules_mod
from custom_components.hauflo.derived import Invalid

from .test_rules import BLIND, FAN_FEATURES, FOREIGN, HALL, HEATER2, LOCK, ROOM, URL, VENT, _engine, _post, calls, clock, entry  # noqa: F401 — fixtures

BUTTON = "button.vent_valves"
MODE = "select.vent_mode"
LEVEL = "number.vent_level"
OTHER_BUTTON = "button.not_ours"
EXTRA = {BUTTON: "u-button", MODE: "u-mode", LEVEL: "u-level"}


@pytest.fixture
async def more(hass: HomeAssistant, entry, calls):
    """A button, a select and a number of Hauflo's (KNX) and a foreign button; their services recorded like the others."""
    reg = er.async_get(hass)
    for eid, uid in (*EXTRA.items(), (OTHER_BUTTON, "u-other-button")):
        reg.async_get_or_create(eid.split(".")[0], "knx", uid, suggested_object_id=eid.split(".")[1])
    entry.runtime_data.owned |= set(EXTRA.values())
    hass.states.async_set(BUTTON, "unknown", {"friendly_name": "Клапаны"})
    hass.states.async_set(OTHER_BUTTON, "unknown")
    hass.states.async_set(MODE, "auto", {"options": ["auto", "boost", "night"]})
    hass.states.async_set(LEVEL, "20", {"min": 0, "max": 100, "step": 5})

    async def handler(call: ServiceCall) -> None:
        eid = call.data["entity_id"]
        calls.append((call.domain, call.service, eid, {k: v for k, v in call.data.items() if k != "entity_id"}))
        s = hass.states.get(eid)
        attrs = dict(s.attributes) if s else {}
        if call.service == "select_option":
            hass.states.async_set(eid, call.data["option"], attrs, context=call.context)
        elif call.service == "set_value":
            hass.states.async_set(eid, str(call.data["value"]), attrs, context=call.context)
        elif call.service == "press":
            hass.states.async_set(eid, dt_util.utcnow().isoformat(), attrs, context=call.context)
        elif call.service == "toggle":
            hass.states.async_set(eid, "off" if s and s.state == "on" else "on", attrs, context=call.context)
        elif call.service == "set_preset_mode":
            hass.states.async_set(eid, "on", {**attrs, "preset_mode": call.data["preset_mode"]}, context=call.context)

    for domain, service in (("button", "press"), ("select", "select_option"), ("number", "set_value"), ("switch", "toggle"), ("fan", "set_preset_mode")):
        hass.services.async_register(domain, service, handler)
    return calls


def _at(clock, day: dt.date, hh: int, mm: int, ss: int = 0) -> None:
    """The fake clock to a local wall time of the house."""
    clock.t = dt.datetime.combine(day, dt.time(hh, mm, ss), tzinfo=dt_util.get_default_time_zone()).timestamp()


def _monday() -> dt.date:
    """A Monday a week ahead (later than the fixtures' clock: the devices' minimum times are over)."""
    d = dt_util.now().date() + dt.timedelta(days=7)
    return d - dt.timedelta(days=d.weekday())


def vent(**kw):
    rule = {"id": "vent", "name": "Вентиляция утро", "purpose": "other", "when": "at", "at": {"times": ["07:00"]}, "actions": [{"do": "speed", "entity_id": VENT, "value": 60}, {"do": "press", "entity_id": BUTTON}]}
    return {**rule, **kw}


async def _tick(hass, engine, clock, s):
    clock.advance(s)
    await engine._tick()
    await hass.async_block_till_done()


def _made(calls, eid):
    return [(srv, d) for _dom, srv, e, d in calls if e == eid]


def _events(engine, rid):
    return [i["event"] for i in engine.st(rid)["log"]]


# --- the moment ------------------------------------------------------------------------------------------------------


def test_next_fire_days_sun_dst_and_strictly_after() -> None:
    tz = dt_util.get_time_zone("Europe/Berlin")
    with patch.object(rules_mod, "C", wraps=rules_mod.C):
        at = {"times": ["07:00", "22:00"], "sun": [], "days": ["mon", "tue", "wed", "thu", "fri"]}
        fri = dt.datetime(2026, 11, 13, 22, 0, tzinfo=tz)  # a Friday, exactly at a moment: the next one, not this
        assert rules_mod.next_fire(at, fri, lambda e, d: None) == dt.datetime(2026, 11, 16, 7, 0, tzinfo=tz)
        # Across the spring DST change the moment stays 07:00 on the wall.
        sat = dt.datetime(2026, 3, 28, 12, 0, tzinfo=tz)
        nxt = rules_mod.next_fire({**at, "days": ["sun"]}, sat, lambda e, d: None)
        assert nxt.hour == 7 and nxt.date() == dt.date(2026, 3, 29) and nxt.utcoffset() == dt.timedelta(hours=2)
        # Sunset − 30 min; a polar night (no sunset at all) has no moment within the horizon.
        sunset = lambda e, d: dt.datetime.combine(d, dt.time(15, 0), tzinfo=dt.UTC) if e == "sunset" else None  # noqa: E731
        got = rules_mod.next_fire({"times": [], "sun": [{"event": "sunset", "offset_min": -30}], "days": list(rules_mod.DAYS)}, dt.datetime(2026, 11, 12, 18, 0, tzinfo=tz), sunset)
        assert got == dt.datetime(2026, 11, 13, 15, 30, tzinfo=tz)  # 15:00 UTC = 16:00 Berlin, −30
        assert rules_mod.next_fire({"times": [], "sun": [{"event": "sunrise", "offset_min": 0}], "days": list(rules_mod.DAYS)}, sat, sunset) is None


async def test_fires_once_fan_speed_and_button_press(hass: HomeAssistant, hass_client, entry, more, clock) -> None:
    client, engine = await hass_client(), _engine(entry)
    mon = _monday()
    _at(clock, mon, 6, 59, 30)
    got = await _post(client, {"op": "save", "rule": vent()})
    assert got["state"] == "scheduled" and got["at"] == {"times": ["07:00"], "sun": [], "days": list(rules_mod.DAYS)}
    assert got["next_at"] == dt.datetime.combine(mon, dt.time(7, 0), tzinfo=dt_util.get_default_time_zone()).timestamp()
    assert "07:00" in got["why"] and got["rule"]["back"] == "nothing"
    await _tick(hass, engine, clock, 20)  # 06:59:50 — not yet
    assert not _made(more, VENT) and not _made(more, BUTTON)
    await _tick(hass, engine, clock, 15)  # 07:00:05
    assert _made(more, VENT) == [("set_percentage", {"percentage": 60})]
    assert _made(more, BUTTON) == [("press", {})]
    st = engine.st("vent")
    assert st["last_result"] == "fired" and "fired" in _events(engine, "vent")
    assert st["next_at"] == dt.datetime.combine(mon + dt.timedelta(days=1), dt.time(7, 0), tzinfo=dt_util.get_default_time_zone()).timestamp()
    for _ in range(5):  # the next ticks of the same minute: once, never again
        await _tick(hass, engine, clock, 60)
    assert len(_made(more, VENT)) == 1 and len(_made(more, BUTTON)) == 1
    assert "сработало" in engine.explain(engine.rules["vent"], True).lower()
    # Next morning: the fan is at 60 already → «already», the button is pressed again (a press has no state).
    _at(clock, mon + dt.timedelta(days=1), 7, 0, 10)
    await engine._tick()
    await hass.async_block_till_done()
    assert len(_made(more, VENT)) == 1 and len(_made(more, BUTTON)) == 2 and "already" in _events(engine, "vent")
    out = await (await client.get(URL)).json()
    card = next(r for r in out["rules"] if r["id"] == "vent")
    assert card["last_result"] == "fired" and card["next_at"] > card["last_at"]


async def test_missed_while_off_is_said_not_done_late(hass: HomeAssistant, hass_client, entry, more, clock) -> None:
    client, engine = await hass_client(), _engine(entry)
    mon = _monday()
    _at(clock, mon, 6, 0)
    await _post(client, {"op": "save", "rule": vent()})
    _at(clock, mon, 9, 0)  # HA was off from 06:00 to 09:00
    await engine._start_all()
    await hass.async_block_till_done()
    assert not _made(more, VENT) and not _made(more, BUTTON)
    st = engine.st("vent")
    assert st["last_result"] == "missed" and "missed" in _events(engine, "vent")
    assert "пропущено" in engine.explain(engine.rules["vent"], True)
    assert st["next_at"] > clock.t  # tomorrow's 07:00 armed
    # A restart within the catch-up (canon) still runs it.
    _at(clock, mon + dt.timedelta(days=1), 7, 0, 40)
    await engine._start_all()
    await hass.async_block_till_done()
    assert _made(more, BUTTON) == [("press", {})] and st["last_result"] == "fired"


async def test_only_if_false_disabled_and_weekdays(hass: HomeAssistant, hass_client, entry, more, clock) -> None:
    client, engine = await hass_client(), _engine(entry)
    mon = _monday()
    _at(clock, mon - dt.timedelta(days=2), 12, 0)  # a Saturday: «по будням» → Monday 07:00
    rule = vent(at={"times": ["07:00"], "days": ["mon", "tue", "wed", "thu", "fri"]}, only_if=[{"type": "value", "entity_id": "sensor.room_t", "when": "above", "x": 30}])
    got = await _post(client, {"op": "save", "rule": rule})
    assert got["next_at"] == dt.datetime.combine(mon, dt.time(7, 0), tzinfo=dt_util.get_default_time_zone()).timestamp()
    _at(clock, mon, 7, 0, 5)
    await engine._tick()
    await hass.async_block_till_done()
    assert not _made(more, BUTTON) and engine.st("vent")["last_result"] == "skipped"
    assert "sched_skip" in _events(engine, "vent") and "только если" in engine.explain(engine.rules["vent"], True)
    # Off → nothing at its moment; on again → counted from now, never «пропущено» for the time it was off.
    await _post(client, {"op": "save", "rule": vent()})
    await _post(client, {"op": "enable", "id": "vent", "enabled": False})
    _at(clock, mon + dt.timedelta(days=1), 7, 0, 5)
    await engine._tick()
    _at(clock, mon + dt.timedelta(days=1), 12, 0)
    got = await _post(client, {"op": "enable", "id": "vent", "enabled": True})
    assert not _made(more, BUTTON) and got["state"] == "scheduled" and "missed" not in _events(engine, "vent")


async def test_sunset_offset_with_fake_sun(hass: HomeAssistant, hass_client, entry, more, clock) -> None:
    client, engine = await hass_client(), _engine(entry)
    mon = _monday()
    tz = dt_util.get_default_time_zone()
    sunset = lambda _h, e, d: dt.datetime.combine(d, dt.time(17, 0), tzinfo=tz).astimezone(dt.UTC) if e == "sunset" else None  # noqa: E731
    with patch.object(rules_mod, "get_astral_event_date", sunset):
        _at(clock, mon, 12, 0)
        got = await _post(client, {"op": "save", "rule": vent(at={"sun": [{"event": "sunset", "offset_min": 15}]})})
        assert got["next_at"] == dt.datetime.combine(mon, dt.time(17, 15), tzinfo=tz).timestamp()
        _at(clock, mon, 17, 15, 1)
        await engine._tick()
        await hass.async_block_till_done()
    assert _made(more, BUTTON) == [("press", {})]


# --- the actions -----------------------------------------------------------------------------------------------------


async def test_every_new_action_calls_its_service(hass: HomeAssistant, hass_client, entry, more, clock) -> None:
    client, engine = await hass_client(), _engine(entry)
    mon = _monday()
    _at(clock, mon, 6, 59, 50)
    acts = [
        {"do": "option", "entity_id": MODE, "value": "boost"},
        {"do": "value", "entity_id": LEVEL, "value": 47},  # the number's own step 5 → 45
        {"do": "pulse", "entity_id": HEATER2, "value": 3},
        {"do": "preset", "entity_id": VENT, "value": "eco"},
    ]
    hass.states.async_set(VENT, "off", {"percentage": 0, "preset_modes": ["eco", "max"], "supported_features": FAN_FEATURES})
    got = await _post(client, {"op": "save", "rule": vent(actions=acts)})
    assert got["rule"]["actions"][1]["value"] == 45
    await _tick(hass, engine, clock, 15)
    assert _made(more, MODE) == [("select_option", {"option": "boost"})]
    assert _made(more, LEVEL) == [("set_value", {"value": 45})]
    assert _made(more, HEATER2) == [("turn_on", {})] and hass.states.get(HEATER2).state == "on"
    assert _made(more, VENT) == [("set_preset_mode", {"preset_mode": "eco"})]
    await _tick(hass, engine, clock, 4)  # the pulse is over: off by itself, said
    assert _made(more, HEATER2) == [("turn_on", {}), ("turn_off", {})]
    assert any(i.get("why") == "pulse" for i in engine.st("vent")["log"])
    # A toggle flips whatever the state is.
    await _post(client, {"op": "save", "rule": {"id": "flip", "name": "Переключить", "purpose": "other", "when": "at", "at": {"times": ["08:00"]}, "actions": [{"do": "toggle", "entity_id": HEATER2}]}})
    _at(clock, mon, 8, 0, 5)
    await engine._tick()
    await hass.async_block_till_done()
    assert _made(more, HEATER2)[-1] == ("toggle", {}) and hass.states.get(HEATER2).state == "on"


async def test_value_rule_restores_an_option_press_restores_nothing(hass: HomeAssistant, hass_client, entry, more, clock) -> None:
    client, engine = await hass_client(), _engine(entry)
    hass.states.async_set("sensor.co2", "500", {"unit_of_measurement": "ppm"})
    rule = {"id": "co2", "name": "CO2", "purpose": "other", "input": {"entity_id": "sensor.co2"}, "when": "above", "x": 1000, "y": 100, "actions": [{"do": "option", "entity_id": MODE, "value": "boost"}, {"do": "press", "entity_id": BUTTON}]}
    await _post(client, {"op": "save", "rule": rule})
    hass.states.async_set("sensor.co2", "1200", {"unit_of_measurement": "ppm"})
    await hass.async_block_till_done()
    await engine.evaluate("co2")
    assert hass.states.get(MODE).state == "boost" and len(_made(more, BUTTON)) == 1
    hass.states.async_set("sensor.co2", "600", {"unit_of_measurement": "ppm"})
    await hass.async_block_till_done()
    await engine.evaluate("co2")
    assert hass.states.get(MODE).state == "auto" and len(_made(more, BUTTON)) == 1


# --- refusals, limits, rights -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("patch_rule", "key"),
    [
        ({"at": {"times": [f"0{i}:00" for i in range(9)]}}, "rules_bad_schedule"),
        ({"at": {"times": ["25:00"]}}, "rules_bad_schedule"),
        ({"at": {"times": ["07:00"], "days": []}}, "rules_bad_schedule"),
        ({"at": {"times": ["07:00"], "days": ["xx"]}}, "rules_bad_schedule"),
        ({"at": {}}, "rules_bad_schedule"),
        ({"at": {"sun": [{"event": "sunset", "offset_min": 999}]}}, "rules_bad_sun_offset"),
        ({"at": {"sun": [{"event": "noon"}]}}, "rules_bad_schedule"),
        ({"actions": [{"do": "option", "entity_id": MODE, "value": "turbo"}]}, "rules_bad_option"),
        ({"actions": [{"do": "value", "entity_id": LEVEL, "value": 150}]}, "rules_bad_action_value"),
        ({"actions": [{"do": "pulse", "entity_id": HEATER2, "value": 601}]}, "rules_bad_action_value"),
        ({"purpose": "heater", "actions": [{"do": "pulse", "entity_id": HEATER2, "value": 5}]}, "rules_pulse_min_on"),
        ({"actions": [{"do": "press", "entity_id": LOCK}]}, "rules_bad_action"),
        ({"actions": [{"do": "on", "entity_id": LOCK}]}, "rules_bad_action"),
        ({"actions": [{"do": "press", "entity_id": OTHER_BUTTON}]}, "rules_target_not_allowed"),
        ({"actions": [{"do": "press", "entity_id": FOREIGN}]}, "rules_bad_action"),
        ({"actions": [{"do": "open", "entity_id": "valve.water"}]}, "rules_bad_action"),
        ({"actions": [{"do": "run", "entity_id": "script.anything"}]}, "rules_target_not_allowed"),
    ],
)
async def test_refused(hass: HomeAssistant, hass_client, entry, more, patch_rule, key) -> None:
    got = await _post(await hass_client(), {"op": "save", "rule": vent(**patch_rule)}, 400)
    assert got["key"] == key, got


async def test_client_may_press_not_pulse(hass: HomeAssistant, entry, more) -> None:
    engine = _engine(entry)
    got = await engine.save(vent(actions=[{"do": "press", "entity_id": BUTTON}, {"do": "option", "entity_id": MODE, "value": "night"}]), client=True, user_id=None)
    assert got["owner"] == "client"
    for bad in ({"do": "pulse", "entity_id": HEATER2, "value": 2}, {"do": "on", "entity_id": HEATER2}, {"do": "toggle", "entity_id": HEATER2}):
        with pytest.raises(Invalid) as err:
            await engine.save(vent(id="kid", actions=[bad]), client=True, user_id=None)
        assert err.value.code == "not_allowed"


async def test_api_features_targets_and_old_rules_untouched(hass: HomeAssistant, hass_client, entry, more, clock) -> None:
    client = await hass_client()
    got = await (await client.get(URL)).json()
    assert set(got["features"]) >= {"schedule", "actions_by_domain"}
    ids = {t["entity_id"]: t for t in got["targets"]}
    assert ids[BUTTON]["dos"] == ["press"] and ids[MODE]["attrs"]["options"] == ["auto", "boost", "night"]
    assert ids[LEVEL]["attrs"] == {"max": 100, "min": 0, "step": 5}
    assert ids[BUTTON]["now"] == {} and ids[MODE]["now"] == {"option": hass.states.get(MODE).state}
    assert LOCK not in ids and OTHER_BUTTON not in ids and FOREIGN not in ids and "valve.water" in ids
    assert got["limits"]["schedule"]["moments"] == 8 and got["limits"]["pulse_s"] == [1, 600]
    assert "button" in got["actions_by_domain"] and "lock" not in got["actions_by_domain"] and "alarm_control_panel" not in got["actions_by_domain"]
    assert {r["id"] for r in got["schedule_recipes"]} >= {"schedule_ventilation"} and all(r["title"] and r["title_en"] for r in got["schedule_recipes"])
    # A value rule of before (no «at») still saves, runs and answers as it did; the preview of a schedule says its next run.
    pv = await _post(client, {"op": "preview", "rule": vent()})
    assert pv["ok"] and pv["now"] == "schedule" and pv["next_at"] and "07:00" in pv["why"]
    assert "current" in pv["actions"][0] and "current" not in pv["actions"][1]  # a press has no «now», not «unknown»


async def test_only_what_the_entity_supports(hass: HomeAssistant, hass_client, entry, more, clock) -> None:
    """As HA decides (the service's required_features): a fan without speed, a climate without on / off, an on / off
    light, a cover without tilt, a select without options — the picker never offers it and a save is refused (HA would
    answer «does not support this service» only at the moment, unseen)."""
    client = await hass_client()
    hass.states.async_set(VENT, "off", {"supported_features": 16 | 32})  # on / off only (a relay fan)
    hass.states.async_set(ROOM, "heat", {"temperature": 21, "hvac_modes": ["heat"], "supported_features": 1})  # KNX: no on / off object
    hass.states.async_set(HALL, "off", {"supported_color_modes": ["onoff"]})
    hass.states.async_set(BLIND, "open", {"current_position": 100, "supported_features": 15})
    hass.states.async_set(MODE, "auto", {"options": []})
    ids = {t["entity_id"]: t for t in (await (await client.get(URL)).json())["targets"]}
    assert ids[VENT]["dos"] == ["on", "off", "toggle"] and "speed" not in ids[VENT]["now"]
    assert ids[ROOM]["dos"] == ["setpoint", "mode"]
    assert ids[HALL]["dos"] == ["on", "off", "toggle", "pulse"]
    assert ids[BLIND]["dos"] == ["open", "close", "stop", "toggle", "position"]
    assert MODE not in ids  # nothing it could do: not offered at all (an empty item in the picker)
    for act in ({"do": "speed", "entity_id": VENT, "value": 60}, {"do": "on", "entity_id": ROOM}, {"do": "brightness", "entity_id": HALL, "value": 50}, {"do": "tilt", "entity_id": BLIND, "value": 50}):
        got = await (await client.post(URL, json={"op": "save", "rule": vent(actions=[act])})).json()
        assert got["code"] == "bad_action", (act, got)
    # Unavailable: not knowable, not refused (a KNX device back after a restart keeps its rules).
    hass.states.async_set(VENT, "unavailable", {})
    assert (await _post(client, {"op": "save", "rule": vent(actions=[{"do": "speed", "entity_id": VENT, "value": 60}])}))["rule"]


# --- acceptance 0.34 (w5-fix-b): the picker = what the agent may do -------------------------------------------------

SERVICE_SW = "switch.hauflo_rule_vent"
HIDDEN = "switch.kalorifer_relay"


async def test_targets_supported_dos_rooms_and_no_service(hass: HomeAssistant, hass_client, entry, more, clock) -> None:
    """A KNX climate with features 17 (target temperature + preset, no on / off, no fan): the targets say exactly
    setpoint / mode / preset — the wizard shows these and nothing else. The agent's own switch (a rule's on / off) and a
    hidden relay of Hauflo's are «Служебное»: not offered, a save refused, a stored one not written at its moment. A
    target carries its room by name (not the area id)."""
    reg = er.async_get(hass)
    reg.async_get_or_create("switch", "hauflo", "rule_vent_enabled", suggested_object_id=SERVICE_SW.split(".")[1], config_entry=entry)
    reg.async_get_or_create("switch", "knx", "u-hidden", suggested_object_id=HIDDEN.split(".")[1])
    reg.async_update_entity(HIDDEN, hidden_by=er.RegistryEntryHider.INTEGRATION)
    entry.runtime_data.owned.add("u-hidden")
    for eid in (SERVICE_SW, HIDDEN):
        hass.states.async_set(eid, "on")
    area = ar.async_get(hass).async_create("Гостиная")
    reg.async_update_entity(ROOM, area_id=area.id)
    hass.states.async_set(ROOM, "heat", {"temperature": 21, "hvac_modes": ["heat", "cool"], "preset_modes": ["comfort", "eco"], "supported_features": 17})
    client = await hass_client()
    got = await (await client.get(URL)).json()
    ids = {t["entity_id"]: t for t in got["targets"]}
    assert ids[ROOM]["dos"] == ["setpoint", "mode", "preset"], ids[ROOM]
    assert ids[ROOM]["room"] == "Гостиная" and area.id != "Гостиная"
    assert all(t["dos"] for t in got["targets"]) and SERVICE_SW not in ids and HIDDEN not in ids
    for act in ({"do": "on", "entity_id": ROOM}, {"do": "off", "entity_id": ROOM}, {"do": "fan_mode", "entity_id": ROOM, "value": "auto"}):
        assert (await _post(client, {"op": "save", "rule": vent(actions=[act])}, 400))["code"] == "bad_action", act
    for eid in (SERVICE_SW, HIDDEN):
        assert (await _post(client, {"op": "save", "rule": vent(actions=[{"do": "off", "entity_id": eid}])}, 400))["key"] == "rules_target_not_allowed"
    # The refusal is words: the action by its name, the device by its name — never «on» or «climate.room».
    got = await _post(client, {"op": "save", "rule": vent(actions=[{"do": "on", "entity_id": ROOM}])}, 400)
    for text in (got["message_ru"], got["message"]):
        assert ROOM not in text and "«on»" not in text and "“on”" not in text, text
    assert "«Включить»" in got["message_ru"] and "“Switch on”" in got["message"], got
    # Options by HA's own labels (climate preset «Не дома» / «Away»), a bad number by the action's name — never raw ids.
    got = await _post(client, {"op": "save", "rule": vent(actions=[{"do": "preset", "entity_id": ROOM, "value": "away"}])}, 400)
    assert got["key"] == "rules_bad_option" and "Комфорт, Эко" in got["message_ru"] and "Comfort, Eco" in got["message"], got
    got = await _post(client, {"op": "save", "rule": vent(actions=[{"do": "setpoint", "entity_id": ROOM, "value": "warm"}])}, 400)
    assert got["key"] == "rules_bad_number" and "setpoint" not in got["message_ru"] + got["message"], got
    assert got["message_ru"].startswith("Уставка") and got["message"].startswith("Setpoint"), got


async def test_client_targets_only_client_actions(hass: HomeAssistant, entry, more) -> None:
    """The client's picker: only what a client may save (no pulse / toggle / speed) — never an item a save answers 403;
    each offered action without a value really saves as the client's."""
    engine = _engine(entry)
    admin_t = {t["entity_id"]: t["dos"] for t in engine.targets()}
    client_t = {t["entity_id"]: t["dos"] for t in engine.targets(client=True)}
    acts = set(rules_mod.C("actions", "client_acts"))
    assert "pulse" in admin_t[HEATER2] and "toggle" in admin_t[HEATER2] and client_t
    for n, (eid, dos) in enumerate(client_t.items()):
        assert dos and set(dos) <= set(admin_t[eid]) & acts, (eid, dos)
        for do in dos:
            spec = rules_mod.C("actions_by_domain")[eid.split(".")[0]][do]
            if "value" not in spec:
                await engine.save(vent(id=f"kid{n}{do}", actions=[{"do": do, "entity_id": eid}]), client=True, user_id=None)


async def test_weekday_skip_is_logged(hass: HomeAssistant, hass_client, entry, more, clock) -> None:
    """«По будням»: on Saturday the log says «пропущен: не тот день недели» once at the day's moment (as HA's own trace
    of a weekday condition), not «missed», nothing written; a day HA was off then is not said late."""
    client, engine = await hass_client(), _engine(entry)
    mon = _monday()
    _at(clock, mon + dt.timedelta(days=4), 12, 0)  # Friday noon
    await _post(client, {"op": "save", "rule": vent(at={"times": ["07:00"], "days": ["mon", "tue", "wed", "thu", "fri"]})})
    sat7 = dt.datetime.combine(mon + dt.timedelta(days=5), dt.time(7, 0), tzinfo=dt_util.get_default_time_zone()).timestamp()
    assert engine.st("vent")["off_at"] == sat7
    _at(clock, mon + dt.timedelta(days=5), 7, 0, 5)
    await engine._tick()
    await hass.async_block_till_done()
    log = [i for i in engine.st("vent")["log"] if i["event"] == "sched_skip"]
    assert len(log) == 1 and log[0]["result"] == "weekday" and log[0]["planned"] == sat7
    assert not _made(more, BUTTON) and "missed" not in _events(engine, "vent") and engine.st("vent")["last_result"] is None
    await engine._tick()  # once only
    assert len([i for i in engine.st("vent")["log"] if i["event"] == "sched_skip"]) == 1
    # Sunday: HA off at 07:00, back at 10:00 → not said late; Monday's moment still armed.
    _at(clock, mon + dt.timedelta(days=6), 10, 0)
    await engine._start_all()
    await hass.async_block_till_done()
    assert len([i for i in engine.st("vent")["log"] if i["event"] == "sched_skip"]) == 1
    assert engine.st("vent")["next_at"] == dt.datetime.combine(mon + dt.timedelta(days=7), dt.time(7, 0), tzinfo=dt_util.get_default_time_zone()).timestamp()


def test_supports_input_datetime_by_its_kind() -> None:
    """A date-only helper offers «date», never «time» (HA: «does not have time»); a full one all three."""
    from types import SimpleNamespace as NS  # noqa: PLC0415

    table = rules_mod.C("actions_by_domain")["input_datetime"]
    got = lambda attrs: [do for do, spec in table.items() if rules_mod.supports(NS(state="2026-11-13", attributes=attrs), spec)]  # noqa: E731
    assert got({"has_date": True, "has_time": False}) == ["date"]
    assert got({"has_date": False, "has_time": True}) == ["time"]
    assert got({"has_date": True, "has_time": True}) == ["time", "date", "datetime"]
