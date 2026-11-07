"""Edges of the small modules: «make independent» export, the season switch, backups, Repairs."""

import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.hauflo import automations, independent, keep, season
from tests.test_agent_tunnel import Req


# --- «Сделать независимым» ---------------------------------------------------------------------------------------------
def rule(**kw):
    return {"id": "r1", "title": "Протечка", "kind": "leak", "enabled": True, "sources": ["binary_sensor.l"], "alarm_states": ["on"], "recipients": {"all": True}, **kw}


def engine(rules, english=False):
    async def set_native(native):
        eng.native = native

    eng = NS(config={"rules": rules}, native={}, locked=None, _english=lambda config=None: english, _targets=lambda r, config=None: [{"service": "mobile_app_ivan", "os": "ios"}],
             _name=lambda e: e, async_set_native=set_native)
    return eng


async def test_export_without_automations_leaves_the_rules_with_the_agent(hass: HomeAssistant) -> None:
    ru, en = engine([rule(), rule(id="off", enabled=False, title="Off"), rule(id="empty", sources=[], title="Empty")]), engine([rule()], english=True)
    ru.native = {"r1": {"automation": "hauflo_alarm_x", "helper": "input_boolean.x"}}
    with patch.object(keep, "async_make_independent", AsyncMock(return_value=keep.ADDED)):
        out = await independent.async_export(hass, ru)
        assert out["skipped"] == ["Off", "Empty"] and out["cards"] == keep.ADDED
        assert [(e["title"], e["reason"]) for e in out["engine"]] == [("Протечка", "no_automation")] and "automation: !include" in out["error"]
        assert ru.native == {}  # nothing in HA runs them: the engine does
        assert "automation: !include" in (await independent.async_export(hass, en))["error"]
        # no engine at all: only the cards
        assert (await independent.async_export(hass, None))["added"] == []
    # dry run says the same, changes nothing
    ru.native = {"r1": {}}
    assert (await independent.async_sync(hass, ru, dry_run=True))["engine"] and ru.native == {"r1": {}}


async def test_export_failures_are_said_never_half_written(hass: HomeAssistant) -> None:
    ru = engine([rule()])
    with patch.object(keep, "async_make_independent", AsyncMock(side_effect=RuntimeError("disk full"))), patch.object(independent, "async_sync", AsyncMock(side_effect=OSError("read-only"))):
        out = await independent.async_export(hass, ru)
    assert out["cards"] == "error: disk full" and out["error"] == "read-only"


def test_summary_in_both_languages():
    for ru in (True, False):
        s = independent.summary({"cards": keep.ADDED, "added": ["A"], "unchanged": ["B"], "kept_edited": ["K"], "engine": [{"title": "E"}], "skipped": ["C"],
                                 "warnings": [{"code": "double", "title": "Old"}], "error": None}, ru)
        assert all(x in s for x in "ABKECO") and s.count("\n") == 5
        assert "configuration.yaml" in independent.summary({"cards": keep.YAML}, ru)
        assert "boom" in independent.summary({"cards": "boom", "error": "bad"}, ru) and "bad" in independent.summary({"cards": "boom", "error": "bad"}, ru)
        assert independent.summary({"cards": keep.ADDED}, ru).count("\n") == 1  # "no alarm rules"


async def test_alarm_automation_shape(hass: HomeAssistant) -> None:
    ru, en = await independent.texts(hass, False), await independent.texts(hass, True)
    the = independent.ids("r1")
    rl = rule(title="Вода {50%}", priority="critical", repeat_min=10, action={"title": "Закрыть", "service": "valve.close_valve", "entity_id": "valve.main"})
    cfg = independent.automation_for(rl, [{"service": "mobile_app_ivan", "os": "ios"}], ru, "input_boolean.x", names={"valve.main": "Кран #1"})
    assert cfg["id"] == the["automation"] and cfg["alias"] == "Авария: Вода {50%}" and cfg["mode"] == "queued"
    ids = [t["id"] for t in cfg["triggers"]]
    assert ids == ["alarm", "back", "ack", "accepted", "entered", "returned", "lost", "repeat", "run", "nodata", "data", "nodata_tick", "start", "calm", "calm"]
    assert cfg["triggers"][0]["for"] == {"seconds": independent.HOLD}
    # Told unless its source chatters (R3-1): the push inside, the memory written last and never stopping the run.
    alarm = cfg["actions"][2]["choose"][0]["sequence"][1]["then"]
    assert cfg["actions"][2]["choose"][0]["sequence"][-1]["continue_on_error"] is True
    assert cfg["trigger_variables"]["chatter"] == "input_text." + the["chatter"]
    push = alarm[-1]["parallel"][0]
    # A person's text with template characters is said as it is (a JSON literal, never a template of theirs).
    assert push["data"]["title"].startswith('🚨 {{ "') and "{50%}" not in push["data"]["title"]
    assert push["continue_on_error"] is True and push["data"]["data"]["push"]["interruption-level"] == "critical"
    assert push["data"]["data"]["actions"][0]["action"] == the["run"] and "Кран" in push["data"]["data"]["actions"][0]["title"]
    # English; a link sensor: no «no data» of its own, no repeat, no button; nobody to tell — said in the description.
    link = independent.automation_for(rule(kind="link", title="Связь"), [], en, "input_boolean.y")
    assert link["alias"] == "Alarm: Связь" and [t["id"] for t in link["triggers"]] == ["alarm", "back", "ack", "accepted", "entered", "returned", "lost", "start", "calm", "calm"]
    assert en["nobody"] in link["description"]
    assert all("parallel" not in step for b in link["actions"][2]["choose"] for step in b["sequence"])
    # A button HA's own service can't run (not in RUN_SERVICES): no button at all.
    odd = independent.automation_for(rule(action={"title": "x", "service": "script.turn_on", "entity_id": "script.s"}), [], ru, "input_boolean.z")
    assert "run" not in [t["id"] for t in odd["triggers"]]
    assert independent._lit("plain") == "plain" and independent._lit('a"{#}') == '{{ "a\\"\\u007b\\u0023\\u007d" }}'


# --- the season switch ------------------------------------------------------------------------------------------------
def ac(hass, eid, state="heat", modes=("heat", "cool", "off"), platform="test", disabled=False):
    reg = er.async_get(hass)
    reg.async_get_or_create("climate", platform, eid, suggested_object_id=eid, disabled_by=er.RegistryEntryDisabler.USER if disabled else None)
    hass.states.async_set(f"climate.{eid}", state, {"hvac_modes": list(modes), "friendly_name": eid.upper()})


async def test_season_picks_the_acs_that_can_heat_and_cool(hass: HomeAssistant) -> None:
    ac(hass, "both")
    ac(hass, "floor", modes=("heat", "off"))
    ac(hass, "gone", state="unavailable")
    ac(hass, "hidden", disabled=True)
    assert [s.entity_id for s in season.acs(hass, owns=lambda e: True)] == ["climate.both"]
    sea = season.Season(hass, lambda: None, owns=lambda e: True)
    assert (await sea.answer())["season"] == "heat" and (await sea.answer())["acs"][0]["name"] == "BOTH"
    hass.states.async_set("climate.both", "off", {"hvac_modes": ["heat", "cool", "off"]})
    assert (await sea.answer())["season"] is None


async def test_season_apply_switches_the_running_ones_and_sends_the_address_once(hass: HomeAssistant) -> None:
    ac(hass, "run", state="cool")
    ac(hass, "off1", state="off")
    ac(hass, "off2", state="off")
    ac(hass, "off3", state="off")
    ac(hass, "same", state="heat")
    ac(hass, "bad", state="cool")
    calls, sends = [], []

    async def set_mode(call):
        if call.data["entity_id"] == "climate.bad":
            raise RuntimeError("device offline")
        calls.append(call.data["entity_id"])

    async def send(call):
        sends.append(call.data)

    hass.services.async_register("climate", "set_hvac_mode", set_mode)
    hass.services.async_register("knx", "send", send)
    addresses = {"climate.off1": "1/1/1", "climate.off2": "1/1/1", "climate.off3": None}
    with patch.object(season, "heat_cool_address", lambda h, e: addresses.get(e)):
        out = await season.Season(hass, lambda: None, owns=lambda e: True).apply("heat", "Ivan")
    assert calls == ["climate.run"] and out["changed"] == ["climate.run"] and sends == [{"address": "1/1/1", "payload": 1}]
    assert sorted(out["sent"]) == ["climate.off1", "climate.off2"] and out["skipped"] == ["climate.off3"] and out["failed"] == ["climate.bad"]


def test_heat_cool_address_reads_the_knx_store(hass: HomeAssistant) -> None:
    hass.data["knx"] = NS(config_store=NS(get_entity_config=lambda eid: {"data": {"knx": {"ga_heat_cool": {"write": "5/1/1"}}}} if eid == "climate.a" else {"data": {}}))
    assert season.heat_cool_address(hass, "climate.a") == "5/1/1" and season.heat_cool_address(hass, "climate.b") is None
    hass.data["knx"] = NS()
    assert season.heat_cool_address(hass, "climate.a") is None


async def test_season_view(hass: HomeAssistant) -> None:
    sea = season.Season(hass, lambda: None, owns=lambda e: True)
    view = season.SeasonView(lambda: sea)
    gone = season.SeasonView(lambda: None)
    assert (await gone.get(Req(hass))).status == 503 and (await gone.post(Req(hass))).status == 503
    assert json.loads((await view.get(Req(hass))).body)["acs"] == []
    assert (await view.post(Req(hass, body=ValueError()))).status == 400
    for bad in ({"mode": "off"}, [], {}):
        assert (await view.post(Req(hass, body=bad))).status == 400
    with patch.object(season, "refuse", AsyncMock(return_value=None)):
        ok = await view.post(Req(hass, body={"mode": "cool"}))
    assert ok.status == 200 and json.loads(ok.body)["season"] == "cool" and (await sea.answer())["chosen"] == "cool"
    # a person the admin hasn't allowed to change the house's settings is refused
    with patch.object(season, "refuse", AsyncMock(return_value=NS(status=403))):
        assert (await view.post(Req(hass, body={"mode": "heat"}))).status == 403


def test_answer_language_follows_the_card():
    """A card's errors in the house's language (the card's `lang`), not HA's: an English HA under a Russian dashboard."""
    from custom_components.hauflo.const import wants_ru

    assert wants_ru({"lang": "ru"}, "en") is True
    assert wants_ru({"lang": "en"}, "ru") is False
    assert wants_ru({}, "ru") is True
    assert wants_ru({"lang": " "}, "en") is False
    assert wants_ru(None, None) is False


async def test_started_once_listener_is_not_removed_twice(hass: HomeAssistant, caplog) -> None:
    """HA review round 2 N-1: Season and Habits started before HA was up, HA starts, then the entry reloads — no
    «Unable to remove unknown job listener» ERROR (HA drops a fired once-listener itself)."""
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState

    from custom_components.hauflo import habits

    hass.set_state(CoreState.starting)
    sea = season.Season(hass, lambda: None)
    hab = habits.Habits(hass, lambda _e: False, lambda: [], lambda _e: None)
    await sea.async_start()
    await hab.async_start()
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    sea.async_stop()
    await hab.async_stop()
    assert "Unable to remove unknown job listener" not in caplog.text
