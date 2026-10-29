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
    return NS(config={"rules": rules}, _english=lambda: english, _targets=lambda r: [{"service": "mobile_app_ivan", "os": "ios"}])


async def test_export_skips_disabled_and_says_what_it_made(hass: HomeAssistant) -> None:
    hass.config.components.add("automation")
    async def add_new(_hass, configs):
        return [configs[0]["id"]], []

    with patch.object(keep, "async_make_independent", AsyncMock(return_value=keep.ADDED)), patch.object(automations, "async_add_new", add_new):
        out = await independent.async_export(hass, engine([rule(), rule(id="off", enabled=False, title="Off"), rule(id="empty", sources=[], title="Empty")]))
    assert out["added"] == ["Авария: Протечка"] and out["already"] == [] and out["skipped"] == ["Off", "Empty"] and out["cards"] == keep.ADDED


async def test_export_failures_are_said_never_half_written(hass: HomeAssistant) -> None:
    ru, en = engine([rule()]), engine([rule()], english=True)
    # cards fail: the automations are still made
    hass.config.components.add("automation")
    with patch.object(keep, "async_make_independent", AsyncMock(side_effect=RuntimeError("disk full"))), patch.object(automations, "async_add_new", AsyncMock(return_value=([], []))):
        out = await independent.async_export(hass, ru)
    assert out["cards"] == "error: disk full" and out["error"] is None
    # no `automation:` in configuration.yaml
    hass.config.components.remove("automation")
    with patch.object(keep, "async_make_independent", AsyncMock(return_value=keep.ADDED)):
        assert "automation: !include" in (await independent.async_export(hass, ru))["error"]
        assert "no «automation: !include" in (await independent.async_export(hass, en))["error"]
    hass.config.components.add("automation")
    with patch.object(keep, "async_make_independent", AsyncMock(return_value=keep.ADDED)):
        with patch.object(automations, "async_add_new", AsyncMock(side_effect=automations.NotPlain())):
            assert (await independent.async_export(hass, ru))["error"] == automations.NotPlain.RU
            assert (await independent.async_export(hass, en))["error"] == automations.NotPlain.EN
        with patch.object(automations, "async_add_new", AsyncMock(side_effect=OSError("read-only"))):
            assert (await independent.async_export(hass, ru))["error"] == "read-only"
        # no engine at all: only the cards
        assert (await independent.async_export(hass, None))["added"] == []


def test_summary_in_both_languages():
    for ru in (True, False):
        s = independent.summary({"cards": keep.ADDED, "added": ["A"], "already": ["B"], "skipped": ["C"], "error": None}, ru)
        assert "A" in s and "B" in s and "C" in s and s.count("\n") == 4
        assert "configuration.yaml" in independent.summary({"cards": keep.YAML}, ru)
        assert "boom" in independent.summary({"cards": "boom", "error": "bad"}, ru) and "bad" in independent.summary({"cards": "boom", "error": "bad"}, ru)
        assert independent.summary({"cards": keep.ADDED}, ru).count("\n") == 1  # "no alarm rules"


def test_alarm_automation_with_a_button():
    rl = rule(action={"title": "Закрыть", "service": "valve.close_valve", "entity_id": "valve.main"})
    cfg = independent.automation_for({**rl, "kind": "leak"}, [{"service": "mobile_app_ivan", "os": "ios"}], True, "2026-10-29")
    assert cfg["mode"] == "parallel" and cfg["alias"] == "Авария: Протечка"
    en = independent.automation_for(rule(), [], False, "2026-10-29")
    assert en["alias"] == "Alarm: Протечка" and en["triggers"][0]["to"] == ["on"]


# --- the season switch ------------------------------------------------------------------------------------------------
def ac(hass, eid, state="heat", modes=("heat", "cool", "off"), platform="test", disabled=False):
    reg = er.async_get(hass)
    reg.async_get_or_create("climate", platform, eid, suggested_object_id=eid, disabled_by=er.RegistryEntryDisabler.USER if disabled else None)
    hass.states.async_set(f"climate.{eid}", state, {"hvac_modes": list(modes), "friendly_name": eid.upper()})


async def test_season_picks_the_acs_that_can_heat_and_cool(hass: HomeAssistant) -> None:
    ac(hass, "both")
    ac(hass, "floor", modes=("heat", "off"))
    ac(hass, "gone", state="unavailable")
    ac(hass, "ours", platform="hauflo")
    ac(hass, "hidden", disabled=True)
    assert [s.entity_id for s in season.acs(hass)] == ["climate.both"]
    sea = season.Season(hass, lambda: None)
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
        out = await season.Season(hass, lambda: None).apply("heat", "Ivan")
    assert calls == ["climate.run"] and out["changed"] == ["climate.run"] and sends == [{"address": "1/1/1", "payload": 1}]
    assert sorted(out["sent"]) == ["climate.off1", "climate.off2"] and out["skipped"] == ["climate.off3"] and out["failed"] == ["climate.bad"]
    # the room engine takes its own ACs (and the mode with them)
    set_mode_called = []
    comfort = NS(rooms=lambda: {"r": {"acs": ["climate.run"]}}, set_mode=AsyncMock(side_effect=lambda m: set_mode_called.append(m)))
    calls.clear()
    out = await season.Season(hass, lambda: comfort).apply("cool", None)
    assert set_mode_called == ["summer"] and "climate.run" in out["changed"] and "climate.run" not in calls


def test_heat_cool_address_reads_the_knx_store(hass: HomeAssistant) -> None:
    hass.data["knx"] = NS(config_store=NS(get_entity_config=lambda eid: {"data": {"knx": {"ga_heat_cool": {"write": "5/1/1"}}}} if eid == "climate.a" else {"data": {}}))
    assert season.heat_cool_address(hass, "climate.a") == "5/1/1" and season.heat_cool_address(hass, "climate.b") is None
    hass.data["knx"] = NS()
    assert season.heat_cool_address(hass, "climate.a") is None


async def test_season_view(hass: HomeAssistant) -> None:
    sea = season.Season(hass, lambda: None)
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
