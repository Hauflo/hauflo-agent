"""Quiet hours for the value rules' pushes (quiet.py, rules.py): a window across midnight, safety rules never silenced,
a held push told once at the end of the window (if the cause is still there), the setting through the API."""

from __future__ import annotations

from datetime import datetime

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.hauflo import quiet
from custom_components.hauflo.derived import Invalid
from test_rules import HEATER, OUT, URL, calls, clock, entry, _engine, _post, _value, _settle  # noqa: F401

ON = {"on": True, "from": "22:00", "to": "08:00"}


def _at(h: int, m: int = 0) -> float:
    return dt_util.as_timestamp(datetime(2026, 10, 30, h, m, tzinfo=dt_util.get_default_time_zone()))


def test_window_crossing_midnight_and_same_day(hass: HomeAssistant) -> None:
    def inside(cfg, h, m=0):
        return quiet.inside(cfg, dt_util.utc_from_timestamp(_at(h, m)))

    assert [inside(ON, h, m) for h, m in ((21, 59), (22, 0), (23, 30), (0, 0), (7, 59), (8, 0), (12, 0))] == [False, True, True, True, True, False, False]
    day = {"on": True, "from": "13:00", "to": "15:00"}
    assert [inside(day, h) for h in (12, 13, 14, 15)] == [False, True, True, False]
    assert not inside({**ON, "on": False}, 23) and not inside({"on": True, "from": "09:00", "to": "09:00"}, 9)
    assert quiet.ends_at(ON, dt_util.utc_from_timestamp(_at(23, 30))).day == 31  # the next morning
    assert quiet.ends_at(ON, dt_util.utc_from_timestamp(_at(3))).day == 30
    assert quiet.ends_at(ON, dt_util.utc_from_timestamp(_at(12))) is None


def test_clean_refuses_bad_settings() -> None:
    assert quiet.clean({"to": "07:30"}, ON) == {**ON, "to": "07:30"} and quiet.clean({"on": False}, ON)["on"] is False
    for bad in ("x", {"on": "yes"}, {"from": "25:00"}, {"to": "8:00"}, {"to": "22:00"}, {"from": 5}):
        with pytest.raises(Invalid):
            quiet.clean(bad, ON)


def _co2(**kw):
    rule = {"id": "co2", "name": "CO2", "purpose": "notify", "input": {"entity_id": OUT}, "when": "above", "x": 10, "y": 1, "for_min": 0, "back_min": 0, "actions": [{"do": "notify", "text": "Проветрить"}]}
    return {**rule, **kw}


async def _setup(hass, client, entry, clock, hour, rule):
    told: list[tuple] = []

    async def tell(title, text, user):
        told.append((title, text))

    engine = _engine(entry)
    engine.tell = tell
    clock.t = _at(hour, 30)
    await _post(client, {"op": "quiet", "quiet": ON})
    await _value(hass, "5.0")
    await _post(client, {"op": "save", "rule": rule})
    return engine, told


def _events(engine, rid="co2"):
    return [e["event"] for e in engine.st(rid)["log"]]


async def test_night_push_is_held_and_told_once_after_the_window(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    client = await hass_client()
    engine, told = await _setup(hass, client, entry, clock, 23, _co2())
    await _value(hass, "15.0")
    await _settle(hass, engine, clock, 60)
    assert told == [] and len(engine.st("co2")["held"]) == 1 and "quiet_hold" in _events(engine)
    await _settle(hass, engine, clock, 60)  # still night, nothing repeats
    assert told == [] and len(engine.st("co2")["held"]) == 1
    clock.t = _at(8, 1)
    await _settle(hass, engine, clock, 1)
    assert told == [("CO2", "Проветрить")] and engine.st("co2")["held"] == [] and "quiet_release" in _events(engine)
    await _settle(hass, engine, clock, 60)
    assert len(told) == 1


async def test_held_push_dropped_when_the_cause_is_gone(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    client = await hass_client()
    engine, told = await _setup(hass, client, entry, clock, 2, _co2())
    await _value(hass, "15.0")
    await _settle(hass, engine, clock, 60)
    assert len(engine.st("co2")["held"]) == 1
    await _value(hass, "5.0")
    await _settle(hass, engine, clock, 60)
    clock.t = _at(8, 5)
    await _settle(hass, engine, clock, 1)
    assert told == [] and "quiet_drop" in _events(engine) and engine.st("co2")["held"] == []


async def test_safety_class_and_per_rule_override_bypass(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    client = await hass_client()
    engine, told = await _setup(hass, client, entry, clock, 3, _co2(id="leak", name="Протечка", purpose="leak", actions=[{"do": "notify", "text": "Течёт!"}], back="nothing"))
    assert engine.rules["leak"]["priority"] == "safety" and engine._bypass(engine.rules["leak"])
    await _value(hass, "15.0")
    await _settle(hass, engine, clock, 1)
    assert told == [("Протечка", "Течёт!")]  # at 03:30, not held
    # The integrator may silence even a safety rule; and let a comfort one through.
    await _post(client, {"op": "save", "rule": {**engine.rules["leak"], "quiet_ok": False}})
    assert not engine._bypass(engine.rules["leak"]) and engine.rule_out("leak", False)["quiet_bypass"] is False
    await _post(client, {"op": "save", "rule": _co2(quiet_ok=True)})
    await _settle(hass, engine, clock, 1)
    assert engine.rules["co2"]["quiet_ok"] is True and engine.rule_out("co2", False)["quiet_bypass"] is True
    assert ("CO2", "Проветрить") in told


async def test_a_warning_is_held_too_and_told_if_still_true(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    client = await hass_client()
    engine, told = await _setup(hass, client, entry, clock, 23, _co2())
    rule = engine.rules["co2"]
    engine.st("co2")["fault"] = "stale"
    await engine._warn(rule, "fault", "Нет данных")
    assert told == [] and engine.st("co2")["held"][0]["kind"] == "fault"
    clock.t = _at(8, 2)
    await engine._release_held()
    assert told == [("CO2", "Нет данных")]
    engine.st("co2")["held"] = [{"key": "warn:fault", "k": "warn", "kind": "fault", "text": "x", "at": 0}, {"key": "warn:rate", "k": "warn", "kind": "rate", "text": "Часто", "at": 0}, {"key": "warn:interlock", "k": "warn", "kind": "interlock", "text": "i", "at": 0}]
    engine.st("co2")["fault"] = None
    await engine._release_held()  # fault over, no interlock: dropped; a rate warning has no cause to re-check
    assert told[-1] == ("CO2", "Часто") and len(told) == 2
    rule["enabled"] = False
    engine.st("co2")["held"] = [{"key": "warn:rate", "k": "warn", "kind": "rate", "text": "Часто", "at": 0}]
    await engine._release_held()
    assert len(told) == 2


async def test_suggestion_waits_for_the_morning(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    client = await hass_client()
    engine, told = await _setup(hass, client, entry, clock, 1, _co2(id="sug", name="Совет", purpose="fan", actions=[{"do": "on", "entity_id": HEATER}]))
    asked = []

    async def ask(title, message, buttons, tag):
        asked.append(title)

    engine.ask = ask
    hass.states.async_set(HEATER, "off")
    engine.st("sug")["active"] = True
    await engine._suggest(engine.rules["sug"], True)
    await engine._suggest_due("sug")
    assert asked == [] and engine.st("sug")["sug"]["quiet"] and _events(engine, "sug").count("quiet_hold") == 1
    clock.t = _at(8, 3)
    await engine._suggest_due("sug")
    assert len(asked) == 1


async def test_setting_through_the_api_and_old_agent_data(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    client = await hass_client()
    engine, told = await _setup(hass, client, entry, clock, 23, _co2())
    got = await (await client.get(URL)).json()
    assert got["quiet"]["from"] == "22:00" and got["quiet"]["default"] == {**ON, "on": False} and got["quiet"]["bypass_priorities"] == ["safety"]
    await _value(hass, "15.0")
    await _settle(hass, engine, clock, 60)
    assert told == []
    out = await _post(client, {"op": "quiet", "quiet": {"on": False}})  # switched off: the held one goes at once
    assert out["quiet"]["on"] is False and len(told) == 1
    assert engine.data()["quiet"]["on"] is False
    await _post(client, {"op": "quiet", "quiet": {"from": "22:00", "to": "22:00"}}, 400)
    await _post(client, {"op": "quiet", "quiet": "x"}, 400)
    # A stored setting that is not valid is ignored (default), an absent one is the canon's default.
    engine.quiet = quiet.default()
    engine.store.async_load = lambda: _stored({"quiet": {"from": "bad"}})
    await engine.async_load()
    assert engine.quiet == quiet.default()
    engine.store.async_load = lambda: _stored({"quiet": {"to": "06:30"}})
    await engine.async_load()
    assert engine.quiet["to"] == "06:30"


async def _stored(data):
    return data


async def test_client_cannot_change_it(hass: HomeAssistant, hass_client, hass_read_only_user, hass_read_only_access_token, entry, calls, clock) -> None:
    client = await hass_client(hass_read_only_access_token)
    res = await client.post(URL, json={"op": "quiet", "quiet": {"on": False}})
    assert res.status == 403
