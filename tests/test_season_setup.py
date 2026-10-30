"""«Зима / Лето» set up by the integrator (season.py, owner 30.10): which thermostats it switches, the wording, the resend
after a KNX device lost its mode, and the suggestion from the outdoor temperature's daily means — a suggestion, never a
switch. Adversarial: a climate that is off must never come on, a resend sends a central address once, a missing sensor
or a short history is «no data», a snooze holds."""

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import _http_allowed, season
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

RUN = "climate.living_ac"  # cools, KNX UI with its own heat/cool address
OFF_A = "climate.bedroom_ac"  # off, KNX UI, central address shared with OFF_B
OFF_B = "climate.study_ac"
DAIKIN = "climate.daikin_hall"  # a foreign one, HA says heat + cool, no KNX address
HEAT_ONLY = "climate.vyborg_room"  # KNX thermostat without a heat/cool object: HA offers heat only
OUT = "sensor.outdoor_temperature"
S = {"enabled": True, "sensor": None, "below": 8.0, "above": 8.0, "days": 5, "snooze_days": 3}


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    yield entry


@pytest.fixture
def bus(hass: HomeAssistant, entry):
    entry.runtime_data.season.owns = lambda eid: eid != DAIKIN  # Hauflo's own, except the Daikin (another integration)
    made: list[tuple[str, object]] = []

    async def hvac(call: ServiceCall) -> None:
        eid = call.data["entity_id"]
        eid = eid[0] if isinstance(eid, list) else eid
        made.append((eid, call.data["hvac_mode"]))
        hass.states.async_set(eid, call.data["hvac_mode"], hass.states.get(eid).attributes)

    async def send(call: ServiceCall) -> None:
        made.append((call.data["address"], call.data["payload"]))

    hass.services.async_register("climate", "set_hvac_mode", hvac)
    hass.services.async_register("knx", "send", send)
    both = {"hvac_modes": ["heat", "cool", "off"], "friendly_name": "AC"}
    hass.states.async_set(RUN, "cool", both)
    hass.states.async_set(OFF_A, "off", both)
    hass.states.async_set(OFF_B, "off", both)
    hass.states.async_set(DAIKIN, "cool", {**both, "friendly_name": "Daikin"})
    hass.states.async_set(HEAT_ONLY, "heat", {"hvac_modes": ["heat", "off"], "friendly_name": "Vyborg"})
    gas = {RUN: "1/3/1", OFF_A: "1/3/100", OFF_B: "1/3/100"}
    connected = SimpleNamespace(up=True)

    def config(eid):
        if eid not in gas:
            raise ValueError("not a KNX UI entity")
        return {"platform": "climate", "data": {"knx": {"ga_heat_cool": {"write": gas[eid], "state": None, "passive": []}}}}

    hass.data["knx"] = SimpleNamespace(
        config_store=SimpleNamespace(get_entity_config=config),
        xknx=SimpleNamespace(connection_manager=SimpleNamespace(connected=SimpleNamespace(is_set=lambda: connected.up))),
    )
    made_ns = SimpleNamespace(calls=made, knx=connected)
    return made_ns


def _sea(entry) -> season.Season:
    return entry.runtime_data.season


# --- which thermostats -------------------------------------------------------------------------------------------------


async def test_default_only_hauflos_own_and_the_integrator_ticks_a_foreign_one(hass: HomeAssistant, hass_client, entry, bus) -> None:
    client = await hass_client()
    got = await (await client.get("/api/hauflo/season/config")).json()
    rows = {c["entity_id"]: c for c in got["climates"]}
    # Default (owner 30.10): Hauflo's own that HA says can heat and cool; the foreign Daikin is not switched.
    assert {e for e, c in rows.items() if c["chosen"]} == {RUN, OFF_A, OFF_B}
    assert rows[DAIKIN]["ours"] is False and rows[RUN]["ours"] is True
    assert rows[HEAT_ONLY]["capable"] is False and rows[HEAT_ONLY]["modes"] == ["heat", "off"] and rows[HEAT_ONLY]["chosen"] is False
    assert rows[RUN]["heat_cool_address"] is True and rows[DAIKIN]["heat_cool_address"] is False
    assert got["config"]["labels"] == "season" and got["config"]["resend_s"] == 3600 and got["config"]["suggest"]["days"] == 5

    r = await (await client.post("/api/hauflo/season/config", json={"exclude": [RUN, "light.x", "climate.Bad Id"], "include": [DAIKIN, "sensor.x"], "labels": "mode"})).json()
    assert r["config"]["exclude"] == [RUN] and r["config"]["include"] == [DAIKIN] and len(r["dropped"]) == 3 and r["config"]["labels"] == "mode"
    got = await (await client.get("/api/hauflo/season")).json()
    assert {a["entity_id"] for a in got["acs"]} == {OFF_A, OFF_B, DAIKIN} and got["labels"] == "mode"
    # An older Hauflo sends no `include`: what is held stays.
    r = await (await client.post("/api/hauflo/season/config", json={"exclude": [RUN]})).json()
    assert r["config"]["include"] == [DAIKIN]
    r = await (await client.post("/api/hauflo/season/config", json={"exclude": [RUN, DAIKIN], "include": [DAIKIN]})).json()
    assert {a["entity_id"] for a in (await (await client.get("/api/hauflo/season")).json())["acs"]} == {OFF_A, OFF_B, DAIKIN}  # exclude is for Hauflo's own only
    r = await (await client.post("/api/hauflo/season/config", json={"exclude": [RUN], "include": [DAIKIN]})).json()
    r = await (await client.post("/api/hauflo/season", json={"mode": "heat"})).json()
    assert r["changed"] == [DAIKIN] and sorted(r["sent"]) == [OFF_A, OFF_B] and hass.states.get(RUN).state == "cool"  # RUN is unticked
    # off ones stay off; the shared address goes once
    assert hass.states.get(OFF_A).state == "off" and bus.calls.count(("1/3/100", 1)) == 1
    # Survives a reload (HA's storage), entity ids untouched.
    async def idle(self):
        return None

    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert _sea(entry).config["exclude"] == [RUN] and _sea(entry).config["include"] == [DAIKIN] and _sea(entry).season == "heat"


async def test_config_is_admin_only_and_bounded(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry, bus) -> None:
    ro = await hass_client(hass_read_only_access_token)
    assert (await ro.get("/api/hauflo/season/config")).status == 403
    assert (await ro.post("/api/hauflo/season/config", json={})).status == 403
    client = await hass_client()
    assert (await client.post("/api/hauflo/season/config", json=[1])).status == 400
    cfg = (await (await client.post("/api/hauflo/season/config", json={"labels": "x", "resend_s": 5, "suggest": {"below": -40, "above": 99, "days": 0, "snooze_days": 400, "sensor": "light.x"}})).json())["config"]
    assert cfg["labels"] == "season" and cfg["resend_s"] == 600
    assert cfg["suggest"] == {"enabled": True, "sensor": None, "below": 0, "above": 25, "days": 1, "snooze_days": 30}
    assert (await (await client.post("/api/hauflo/season/config", json={"resend_s": 0})).json())["config"]["resend_s"] == 0
    assert _http_allowed(hass, "GET", "/api/hauflo/season/config", "") and _http_allowed(hass, "POST", "/api/hauflo/season/config", {"exclude": []})
    assert not _http_allowed(hass, "POST", "/api/hauflo/season", {"mode": "heat"})


# --- resend ------------------------------------------------------------------------------------------------------------


async def test_resend_never_switches_an_off_one_on_and_sends_a_central_address_once(hass: HomeAssistant, entry, bus) -> None:
    sea = _sea(entry)
    assert await sea.resend("interval") is None and bus.calls == []  # no season chosen yet: never a guess
    sea.season = "heat"
    hass.states.async_set(DAIKIN, "heat", hass.states.get(DAIKIN).attributes)
    # A foreign Daikin is not resent to by default.
    sea.config["resend_s"] = 3600
    hass.states.async_set(DAIKIN, "cool", hass.states.get(DAIKIN).attributes)
    got = await sea.resend("interval")
    assert (DAIKIN, "heat") not in bus.calls and got["changed"] == []
    bus.calls.clear()
    sea.config["include"] = [DAIKIN]
    hass.states.async_set(DAIKIN, "heat", hass.states.get(DAIKIN).attributes)
    got = await sea.resend("interval")
    # KNX UI ones: their heat/cool address (1 = heat), the shared one once; the Daikin already heats — left alone.
    assert sorted(bus.calls) == sorted([("1/3/1", 1), ("1/3/100", 1)]) and got["changed"] == []
    assert hass.states.get(OFF_A).state == "off" and hass.states.get(OFF_B).state == "off" and hass.states.get(RUN).state == "cool"
    # A foreign one that runs in the other mode is put back; one that is off is never switched on.
    bus.calls.clear()
    hass.states.async_set(DAIKIN, "cool", hass.states.get(DAIKIN).attributes)
    got = await sea.resend("interval")
    assert (DAIKIN, "heat") in bus.calls and got["changed"] == [DAIKIN]
    bus.calls.clear()
    hass.states.async_set(DAIKIN, "off", hass.states.get(DAIKIN).attributes)
    await sea.resend("interval")
    assert all(c[0] != DAIKIN for c in bus.calls) and hass.states.get(DAIKIN).state == "off"
    # Unticked ones are never touched; resend off → nothing at all.
    sea.config["exclude"] = [RUN]
    bus.calls.clear()
    await sea.resend("interval")
    assert ("1/3/1", 1) not in bus.calls and ("1/3/100", 1) in bus.calls
    sea.config["resend_s"] = 0
    bus.calls.clear()
    assert await sea.resend("knx back") is None and bus.calls == []
    assert sea.log and sea.log[-1]["why"] == "interval"


async def test_resend_on_interval_and_when_knx_comes_back(hass: HomeAssistant, entry, bus) -> None:
    sea = _sea(entry)
    sea.season = "cool"
    sea.last_resend = time.time()
    sea.evaluated = dt_util.now().date().isoformat()
    await sea._tick(None)
    assert bus.calls == []  # the interval isn't over
    bus.knx.up = False
    await sea._tick(None)
    assert bus.calls == []
    bus.knx.up = True
    await sea._tick(None)  # the KNX connection is back: at once
    assert ("1/3/100", 0) in bus.calls and sea.log[-1]["why"] == "knx back"
    bus.calls.clear()
    sea.last_resend = time.time() - 3601
    await sea._tick(None)
    assert ("1/3/100", 0) in bus.calls and sea.log[-1]["why"] == "interval"


async def test_resend_after_start(hass: HomeAssistant, entry, bus) -> None:
    sea = _sea(entry)
    sea.season = "heat"
    with patch.object(season, "async_call_later") as later:
        await sea.async_start()
    delay, job = later.call_args[0][1], later.call_args[0][2]
    assert delay == season.START_DELAY
    await job(None)
    assert ("1/3/100", 1) in bus.calls and sea.log[-1]["why"] == "start"
    sea.async_stop()


# --- the suggestion: the value rule «season» (its table, buttons, migration: test_rules_window.py, real recorder) -------


async def test_no_sensor_is_no_data(hass: HomeAssistant, hass_client, entry, bus) -> None:
    sea = _sea(entry)
    await sea.async_sync_rule()
    got = sea.setup_answer()
    # No street sensor and no forecast: the rule can't be made yet — «нет датчика», never a guess; tried again.
    assert got["suggest"]["status"] == "no_sensor" and got["auto_sensor"] is None and (await sea.answer())["suggestion"] is None
    assert not sea.migrated and sea.pending == "no_outdoor"


async def test_suggestion_off_makes_a_switched_off_rule(hass: HomeAssistant, entry, bus) -> None:
    sea = _sea(entry)
    hass.states.async_set(OUT, "3", {"device_class": "temperature", "state_class": "measurement", "unit_of_measurement": "°C"})
    sea.config["suggest"] = {**S, "sensor": OUT, "enabled": False}
    await sea.async_sync_rule()
    rule = entry.runtime_data.rules.rules[season.SEASON_RULE]
    assert rule["enabled"] is False and sea.setup_answer()["suggest"]["status"] == "off" and sea.suggestion() is None
    # A suggestion, never a switch: its actions don't run by themselves.
    assert rule["outputs"]["auto"] is False and rule["actions"] == [{"do": "season", "value": "heat"}]
    assert bus.calls == []


# --- ours only (owner 30.10) -------------------------------------------------------------------------------------------


async def test_foreign_not_switched_unless_included_owned_unless_excluded(hass: HomeAssistant, entry, bus) -> None:
    sea = _sea(entry)
    names = lambda: {s.entity_id for s in sea.chosen()}  # noqa: E731
    assert names() == {RUN, OFF_A, OFF_B}  # the Daikin (foreign) is not there by default
    sea.config["include"] = [DAIKIN]
    assert names() == {RUN, OFF_A, OFF_B, DAIKIN}
    sea.config["exclude"] = [RUN, DAIKIN]  # unticking an own one works; `exclude` doesn't override a ticked foreign one
    assert names() == {OFF_A, OFF_B, DAIKIN}
    sea.config["include"] = []
    assert names() == {OFF_A, OFF_B}
    # A foreign one ticked that can't switch (heat only) stays out; the demo's `climate.hvac` with no registry entry is foreign.
    hass.states.async_set("climate.hvac", "cool", {"hvac_modes": ["heat", "cool", "off"]})
    sea.owns = lambda eid: er.async_get(hass).async_get(eid) is not None
    assert "climate.hvac" not in names()
    sea.config["include"] = ["climate.hvac", HEAT_ONLY]
    assert "climate.hvac" in names() and HEAT_ONLY not in names()
    rows = {c["entity_id"]: c for c in sea.setup_answer()["climates"]}
    assert rows["climate.hvac"]["ours"] is False and rows["climate.hvac"]["chosen"] is True


async def test_apply_and_resend_follow_the_same_rule(hass: HomeAssistant, entry, bus) -> None:
    sea = _sea(entry)
    out = await sea.apply("heat", None)
    assert DAIKIN not in out["changed"] + out["sent"] + out["skipped"] and hass.states.get(DAIKIN).state == "cool"
    bus.calls.clear()
    sea.config["resend_s"] = 3600
    await sea.resend("interval")
    assert all(c[0] != DAIKIN for c in bus.calls)
    sea.config["include"] = [DAIKIN]
    await sea.resend("interval")
    assert (DAIKIN, "heat") in bus.calls


async def test_old_config_without_include_keeps_switching_foreign_ones_once(hass: HomeAssistant, entry, bus) -> None:
    # An existing house: a saved config from before `include`. It switches what it switched; then they are written down.
    sea = _sea(entry)
    sea.config = season.clean_config({"exclude": [OFF_B]})[0]
    sea.legacy = True
    assert {s.entity_id for s in sea.chosen()} == {RUN, OFF_A, DAIKIN}
    assert {c["entity_id"] for c in sea.setup_answer()["climates"] if c["chosen"]} == {RUN, OFF_A, DAIKIN}
    await sea.async_migrate_include()
    assert sea.legacy is False and sea.config["include"] == [DAIKIN]
    assert {s.entity_id for s in sea.chosen()} == {RUN, OFF_A, DAIKIN}
    # A foreign climate that shows up later is not switched; the stored include survives a reload of the store.
    hass.states.async_set("climate.new_daikin", "cool", {"hvac_modes": ["heat", "cool", "off"]})
    sea.owns = lambda eid: eid not in (DAIKIN, "climate.new_daikin")
    sea.legacy = False
    assert "climate.new_daikin" not in {s.entity_id for s in sea.chosen()}
    sea.legacy = False
    await sea._save()
    sea2 = season.Season(hass, lambda: None, owns=sea.owns)
    await sea2.async_load()
    assert sea2.legacy is False and sea2.config["include"] == [DAIKIN] and sea2.config["exclude"] == [OFF_B]


async def test_load_marks_only_an_old_saved_config_as_legacy(hass: HomeAssistant, entry) -> None:
    sea = season.Season(hass, lambda: None)
    await sea.async_load()
    assert sea.legacy is False  # nothing saved: a new install gets the new default
    sea.store.async_load = lambda: _async({"config": {"exclude": [], "labels": "season"}})
    await sea.async_load()
    assert sea.legacy is True and sea.config["include"] == []
    sea.store.async_load = lambda: _async({"config": {"include": [], "exclude": []}})
    await sea.async_load()
    assert sea.legacy is False


async def _async(v):
    return v
