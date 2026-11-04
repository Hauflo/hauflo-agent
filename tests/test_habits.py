"""«Дом умнеет — привычки» (habits.py, owner 04.11): a person's repeated hand actions on lights / blinds / scenes / fans /
sockets / thermostats of ANY integration become rule suggestions of «Автоматизации» — never an automation's or Hauflo's
own change, never a lock / valve / garage door / bare switch, only what validate() would save."""

import datetime as dt
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import dump
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import habits as hb
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

URL = "/api/hauflo/automations"
ZIG = "light.hall_zigbee"  # another integration's lamp (MQTT / Zigbee)
CEIL = "light.bed_ceiling"  # Hauflo's KNX light
BLIND1, BLIND2 = "cover.bed_blind_1", "cover.bed_blind_2"  # Hauflo's
CURTAIN = "cover.hall_curtain"  # another integration's curtain
GARAGE = "cover.garage"  # another integration's garage door
OUTLET, BARE = "switch.kettle_plug", "switch.pump_relay"
CLIMATE = "climate.bedroom"
FAN = "fan.bath"
HIDDEN, DISABLED, CONFIG = "light.hidden_one", "light.disabled_one", "light.config_one"
LOCK, VALVE, BOILER, SIREN = "lock.front", "valve.water_main", "water_heater.boiler", "siren.alarm"
pytestmark = pytest.mark.parametrize("expected_lingering_timers", [True])


@pytest.fixture
async def entry(hass: HomeAssistant, tmp_path: Path):
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
    (tmp_path / "automations.yaml").write_text(dump([]))
    assert await async_setup_component(hass, "automation", {"automation": []})
    await hass.config.async_update(time_zone="Europe/Moscow", latitude=59.94, longitude=30.31)
    await hass.async_block_till_done()

    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    reg = er.async_get(hass)
    areas = ar.async_get(hass)
    bed, hall = areas.async_create("Спальня").id, areas.async_create("Холл").id
    for eid, platform, extra, opts in (
        (ZIG, "mqtt", {"supported_color_modes": ["brightness"], "brightness": 255}, {"area_id": hall}),
        (CEIL, "knx", {"supported_color_modes": ["brightness"]}, {"area_id": bed}),
        (BLIND1, "knx", {"supported_features": 15, "current_position": 100}, {"area_id": bed}),
        (BLIND2, "knx", {"supported_features": 15, "current_position": 100}, {"area_id": bed}),
        (CURTAIN, "zha", {"supported_features": 15, "device_class": "curtain", "current_position": 100}, {"area_id": hall}),
        (GARAGE, "mqtt", {"supported_features": 3, "device_class": "garage"}, {}),
        (OUTLET, "tuya", {"device_class": "outlet"}, {}),
        (BARE, "mqtt", {}, {}),
        (CLIMATE, "mqtt", {"supported_features": 1, "min_temp": 7, "max_temp": 30, "target_temp_step": 0.5, "temperature": 20, "hvac_modes": ["off", "heat"]}, {"area_id": bed}),
        (FAN, "mqtt", {}, {}),
        (HIDDEN, "mqtt", {}, {"hidden_by": er.RegistryEntryHider.USER}),
        (DISABLED, "mqtt", {}, {"disabled_by": er.RegistryEntryDisabler.USER}),
        (CONFIG, "mqtt", {}, {"entity_category": er.EntityCategory.CONFIG}),
        (LOCK, "mqtt", {}, {}),
        (VALVE, "mqtt", {}, {}),
        (BOILER, "mqtt", {"supported_features": 1, "min_temp": 30, "max_temp": 70}, {}),
        (SIREN, "mqtt", {}, {}),
    ):
        domain, obj = eid.split(".")
        reg.async_get_or_create(domain, platform, f"u-{obj}", suggested_object_id=obj)
        if opts:
            reg.async_update_entity(eid, **opts)
        state = {"cover": "open", "climate": "heat", "lock": "locked", "valve": "closed", "water_heater": "eco"}.get(domain, "off")
        hass.states.async_set(eid, state, {"friendly_name": obj.replace("_", " "), **extra})
    entry.runtime_data.owned |= {"u-bed_ceiling", "u-bed_blind_1", "u-bed_blind_2"}
    entry.bed, entry.hall = bed, hall
    habits = entry.runtime_data.habits
    habits._since = 0  # HA has been up long enough
    yield entry


def _h(entry) -> hb.Habits:
    return entry.runtime_data.habits


def _today() -> dt.date:
    return dt_util.now().date()


def _at(day: dt.date, minute: int) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(0, 0), tzinfo=dt_util.get_default_time_zone()) + dt.timedelta(minutes=minute)


def _observe(h: hb.Habits, n: int = 13) -> None:
    for i in range(1, n + 1):
        h.observed((_today() - dt.timedelta(days=i)).toordinal())


def _seed(h: hb.Habits, eid: str, act: str, minute: int, ago, level=None, jitter=(0, 4, -3, 2, -2, 5, -4), where="events") -> None:
    for n, i in enumerate(ago):
        when = _at(_today() - dt.timedelta(days=i), minute + jitter[n % len(jitter)])
        if where == "events":
            h.record(eid, act, when, level)
        else:
            h._add(where, f"{eid}|{act}", when, None)


def _workdays(n: int = 13) -> list[int]:
    return [i for i in range(1, n + 1) if (_today() - dt.timedelta(days=i)).weekday() < 5]


def _weekend(n: int = 27) -> list[int]:
    return [i for i in range(1, n + 1) if (_today() - dt.timedelta(days=i)).weekday() >= 5]


async def _got(hass: HomeAssistant, hass_client, entry, mine: bool = True) -> dict:
    if mine:
        _h(entry).mine()
    client = await hass_client()
    return await (await client.get(URL)).json()


# --- what a rule may use: any integration, only the safe kinds ---------------------------------------------------------
async def test_safe_domains_of_any_integration(hass: HomeAssistant, hass_client, entry) -> None:
    t = (await _got(hass, hass_client, entry))["targets"]
    assert {x["entity_id"] for x in t["lights"]} == {ZIG, CEIL}  # never hidden / disabled / a config entity
    assert {x["entity_id"] for x in t["covers"]} == {BLIND1, BLIND2, CURTAIN}  # never the garage door
    assert [x["entity_id"] for x in t["switches"]] == [OUTLET]  # a socket, never a bare relay
    assert [x["entity_id"] for x in t["fans"]] == [FAN]
    c = t["climates"]
    assert [x["entity_id"] for x in c] == [CLIMATE] and (c[0]["min"], c[0]["max"], c[0]["step"], c[0]["modes"]) == (7.0, 30.0, 0.5, ["heat", "off"])
    assert t["areas"][entry.bed] == "Спальня"
    client = await hass_client()
    rule = {"when": {"event": "time", "at": "06:30"}, "actions": []}
    for bad in (
        {"entity_id": GARAGE, "do": "open"},
        {"entity_id": BARE, "do": "on"},
        {"entity_id": LOCK, "do": "on"},
        {"entity_id": VALVE, "do": "open"},
        {"entity_id": BOILER, "do": "temp", "value": 60},
        {"entity_id": SIREN, "do": "on"},
        {"entity_id": HIDDEN, "do": "on"},
        {"entity_id": DISABLED, "do": "on"},
        {"entity_id": CONFIG, "do": "on"},
        {"entity_id": CLIMATE, "do": "temp", "value": 35},  # over the thermostat's max
        {"entity_id": CLIMATE, "do": "temp", "value": 6},
        {"entity_id": CLIMATE, "do": "cool"},  # it can't
        {"entity_id": CLIMATE, "do": "temp"},
        {"entity_id": FAN, "do": "position", "value": 3},
        {"do": "set", "domain": "switch", "cmd": "on", "entities": [OUTLET]},  # sockets one by one
    ):
        res = await client.post(URL, json={"op": "save", "rule": {**rule, "actions": [bad]}})
        assert res.status == 400, bad
    ok = {**rule, "actions": [{"entity_id": CLIMATE, "do": "temp", "value": 21.3}, {"entity_id": CLIMATE, "do": "off"}, {"entity_id": OUTLET, "do": "on"}, {"entity_id": FAN, "do": "off"}, {"entity_id": ZIG, "do": "on", "value": 40}, {"entity_id": CURTAIN, "do": "close"}]}
    res = await client.post(URL, json={"op": "save", "rule": ok})
    assert res.status == 200, await res.text()
    saved = await res.json()
    assert saved["actions"][0]["value"] == 21.5  # the thermostat's step
    auto = hass.config_entries.async_loaded_entries(DOMAIN)[0].runtime_data.automations
    calls = auto.automation(saved["id"], saved)["actions"]
    assert calls[0] == {"action": "climate.set_temperature", "target": {"entity_id": CLIMATE}, "data": {"temperature": 21.5}}
    assert calls[1] == {"action": "climate.set_hvac_mode", "target": {"entity_id": CLIMATE}, "data": {"hvac_mode": "off"}}
    assert calls[2] == {"action": "switch.turn_on", "target": {"entity_id": OUTLET}}
    # A scene of HA's own with a lock inside is never offered (protected); one of safe lamps is.
    reg = er.async_get(hass)
    for obj, members in (("with_lock", [ZIG, LOCK]), ("lamps", [ZIG, CEIL]), ("with_pump", [ZIG, BARE])):
        reg.async_get_or_create("scene", "homeassistant", f"u-{obj}", suggested_object_id=obj)
        hass.states.async_set(f"scene.{obj}", "unknown", {"entity_id": members})
    t = (await _got(hass, hass_client, entry))["targets"]
    assert [x["entity_id"] for x in t["scenes"]] == ["scene.lamps"]


# --- recording: a person's hand, not an automation ----------------------------------------------------------------------
async def test_manual_counted_automation_not(hass: HomeAssistant, entry) -> None:
    h = _h(entry)
    on = {"supported_color_modes": ["brightness"], "brightness": 128}
    hass.states.async_set(ZIG, "on", on, context=Context())  # a Zigbee wall remote / the app / voice: no parent
    await hass.async_block_till_done()
    assert [r[4] for r in h.data["events"][f"{ZIG}|on"]] == [50]  # its level kept
    # An automation's change (parent context) → «already automated», not a habit.
    hass.states.async_set(CEIL, "on", on, context=Context(parent_id="trigger"))
    # A time-triggered automation: its own context has no parent — known by automation_triggered.
    run = Context()
    hass.bus.async_fire("automation_triggered", {"name": "x", "entity_id": "automation.x"}, context=run)
    await hass.async_block_till_done()
    hass.states.async_set(FAN, "on", {}, context=run)
    await hass.async_block_till_done()
    assert f"{CEIL}|on" not in h.data["events"] and f"{FAN}|on" not in h.data["events"]
    assert f"{CEIL}|on" in h.data["auto"] and f"{FAN}|on" in h.data["auto"]
    # Hauflo's own value rule (rules.py) and «Нет дома» (presence.py): theirs.
    ours = Context()
    entry.runtime_data.rules._ctx.append(ours.id)
    hass.states.async_set(OUTLET, "on", {"device_class": "outlet"}, context=ours)
    entry.runtime_data.presence.data["enabled"] = True
    hass.states.async_set(CURTAIN, "closed", {"device_class": "curtain", "current_position": 0}, context=Context())
    await hass.async_block_till_done()
    entry.runtime_data.presence.data["enabled"] = False
    assert f"{OUTLET}|on" not in h.data["events"] and f"{CURTAIN}|close" not in h.data["events"]
    # Never recorded: hidden / disabled / config, a lock, a bare switch, a garage door.
    for eid, state in ((HIDDEN, "on"), (CONFIG, "on"), (LOCK, "unlocked"), (BARE, "on"), (GARAGE, "closed"), (VALVE, "open")):
        hass.states.async_set(eid, state, {}, context=Context())
    await hass.async_block_till_done()
    assert not {k.split("|")[0] for k in h.data["events"]} & {HIDDEN, CONFIG, LOCK, BARE, GARAGE, VALVE}
    # Back from «unavailable» is the device's, not a person's.
    hass.states.async_set(FAN, "unavailable", {})
    hass.states.async_set(FAN, "off", {}, context=Context())
    await hass.async_block_till_done()
    assert f"{FAN}|off" not in h.data["events"]
    # While HA is starting (the first minutes): nothing.
    h._since = dt_util.utcnow().timestamp() + 300
    hass.states.async_set(ZIG, "off", {}, context=Context())
    await hass.async_block_till_done()
    assert f"{ZIG}|off" not in h.data["events"]


async def test_settle_scene_fanout_and_cover_travel(hass: HomeAssistant, entry) -> None:
    h = _h(entry)
    # On and off again within settle_s: a double press — the first counts.
    hass.states.async_set(ZIG, "on", {"brightness": 255}, context=Context())
    hass.states.async_set(ZIG, "off", {}, context=Context())
    await hass.async_block_till_done()
    assert f"{ZIG}|on" in h.data["events"] and f"{ZIG}|off" not in h.data["events"]
    # A thermostat turned 20 → 21 → 21.5 in a few seconds: one change, the last value.
    for v in (20.5, 21, 21.5):
        hass.states.async_set(CLIMATE, "heat", {"temperature": v, "min_temp": 7, "max_temp": 30, "supported_features": 1, "hvac_modes": ["off", "heat"]}, context=Context())
    await hass.async_block_till_done()
    assert [r[4] for r in h.data["events"][f"{CLIMATE}|temp"]] == [21.5]
    # A scene a person ran: the scene is the habit, not its lamps.
    reg = er.async_get(hass)
    reg.async_get_or_create("scene", "homeassistant", "u-evening", suggested_object_id="evening")
    hass.states.async_set("scene.evening", "unknown", {"entity_id": [CEIL]})
    ctx = Context(user_id="someone")
    hass.states.async_set("scene.evening", "2026-11-04T19:00:00+00:00", {"entity_id": [CEIL]}, context=ctx)
    hass.states.async_set(CEIL, "on", {"supported_color_modes": ["brightness"], "brightness": 100}, context=ctx)
    await hass.async_block_till_done()
    assert "scene.evening|on" in h.data["events"] and f"{CEIL}|on" not in h.data["events"]
    # A blind: pressed (closing, a person) — its stop is reported by the bus later in another context: the press counts.
    press = Context()
    hass.states.async_set(BLIND1, "closing", {"supported_features": 15, "current_position": 60}, context=press)
    hass.states.async_set(BLIND1, "closed", {"supported_features": 15, "current_position": 0}, context=Context(parent_id="bus"))
    await hass.async_block_till_done()
    assert f"{BLIND1}|close" in h.data["events"] and f"{BLIND1}|close" not in h.data["auto"]
    # Stopped half way by a person: a position bucket.
    hass.states.async_set(BLIND2, "closing", {"supported_features": 15, "current_position": 80}, context=Context())
    hass.states.async_set(BLIND2, "open", {"supported_features": 15, "current_position": 48}, context=Context())
    await hass.async_block_till_done()
    assert [r[4] for r in h.data["events"][f"{BLIND2}|pos50"]] == [48]


# --- mining ------------------------------------------------------------------------------------------------------------
async def test_workday_clock_habit(hass: HomeAssistant, hass_client, entry) -> None:
    h = _h(entry)
    _observe(h)
    _seed(h, ZIG, "on", 6 * 60 + 45, _workdays(), level=62)
    got = await _got(hass, hass_client, entry)
    assert got["habits_on"] is True
    [s] = got["habits"]
    assert s["key"].startswith("habit_") and s["class"] == "workdays" and s["hits"] == len(_workdays()) and s["of"] - s["hits"] <= 1  # today: watched, not yet
    assert s["rule"]["when"] == {"event": "time", "at": "06:45"}
    assert s["rule"]["days"] == ["mon", "tue", "wed", "thu", "fri"]
    assert s["rule"]["actions"] == [{"entity_id": ZIG, "do": "on", "value": 60}]
    assert s["rule"]["from"] == s["key"] and s["area"] == entry.hall
    # The same habit, mined again / after a restart: the same key (a dismissal sticks).
    await h.async_stop()
    again = hb.Habits(hass, h.pickable, h.engines, h.area)
    await again.async_start()
    again._since = 0
    entry.runtime_data.habits = again
    assert [x["key"] for x in (await _got(hass, hass_client, entry))["habits"]] == [s["key"]]
    await again.async_stop()


async def test_not_enough_or_scattered(hass: HomeAssistant, hass_client, entry) -> None:
    h = _h(entry)
    _observe(h)
    _seed(h, ZIG, "on", 7 * 60, [1, 2, 3, 6, 7])  # 5 of 13 days
    _seed(h, CEIL, "off", 0, range(1, 14), jitter=(60, 180, 300, 420, 540, 660, 780, 900, 1020, 1140, 1260, 1380, 30))  # every day, any time
    assert (await _got(hass, hass_client, entry))["habits"] == []
    # The thresholds are tuning.json's: a lower bar finds the 5 days.
    with patch.dict(hb.T, {"min_days": 4, "min_share": 0.3}):
        found = (await _got(hass, hass_client, entry))["habits"]
    assert [s["entities"] for s in found] == [[ZIG]]


async def test_weekend_apart_from_workdays(hass: HomeAssistant, hass_client, entry) -> None:
    h = _h(entry)
    _observe(h, 27)
    _seed(h, CURTAIN, "open", 7 * 60, _workdays())
    _seed(h, CURTAIN, "open", 9 * 60 + 30, _weekend())
    got = {s["class"]: s["rule"] for s in (await _got(hass, hass_client, entry))["habits"]}
    assert set(got) == {"workdays", "weekend"}
    assert got["workdays"]["when"]["at"] == "07:00" and got["weekend"]["when"]["at"] == "09:30"
    assert got["weekend"]["days"] == ["sat", "sun"] and got["weekend"]["actions"] == [{"entity_id": CURTAIN, "do": "open"}]


async def test_every_day_is_one_suggestion(hass: HomeAssistant, hass_client, entry) -> None:
    h = _h(entry)
    _observe(h, 27)
    _seed(h, FAN, "off", 23 * 60 + 50, range(1, 28), jitter=(0, 3, -3, 5))
    got = (await _got(hass, hass_client, entry))["habits"]
    [s] = got
    assert s["class"] == "all" and s["rule"]["days"] == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    assert s["rule"]["when"]["at"] == "23:50"
    # The clock is a circle: 23:58 and 00:02 are one time, not a 23-hour spread.
    center, spread, hits = hb._fit([1438, 2, 1435, 5], True)
    assert min(center, hb.DAY - center) <= 5 and spread <= 5 and hits == 4


async def test_sun_fit_preferred_when_drifting_with_sunset(hass: HomeAssistant, hass_client, entry) -> None:
    h = _h(entry)
    _observe(h)
    for i in range(1, 14):
        day = _today() - dt.timedelta(days=i)
        sunset = dt_util.as_local(get_astral_event_date(hass, "sunset", day))
        h.record(CURTAIN, "close", sunset + dt.timedelta(minutes=12 + (i % 3)))
    [s] = (await _got(hass, hass_client, entry))["habits"]
    rows = list(h.data["events"].values())[0]
    assert s["rule"]["when"] == {"event": "sunset", "offset": 15} or s["rule"]["when"] == {"event": "sunset", "offset": 10}
    # Four weeks of it: one «every day» suggestion, not a second «at the weekend» copy of the same habit (the weekend
    # window is longer, so its clock median drifted with the season).
    _observe(h, 27)
    for i in range(14, 28):
        day = _today() - dt.timedelta(days=i)
        h.record(CURTAIN, "close", dt_util.as_local(get_astral_event_date(hass, "sunset", day)) + dt.timedelta(minutes=12 + (i % 3)))
    [s] = (await _got(hass, hass_client, entry))["habits"]
    assert s["class"] == "all" and s["rule"]["when"]["event"] == "sunset"
    # The same at a fixed clock time: by the clock.
    h.data["events"].clear()
    _seed(h, CURTAIN, "close", 22 * 60, range(1, 14))
    [s] = (await _got(hass, hass_client, entry))["habits"]
    assert s["rule"]["when"] == {"event": "time", "at": "22:00"}


async def test_room_merge(hass: HomeAssistant, hass_client, entry) -> None:
    h = _h(entry)
    _observe(h)
    for eid in (BLIND1, BLIND2):
        _seed(h, eid, "close", 22 * 60 + 30, _workdays())
    _seed(h, ZIG, "off", 22 * 60 + 30, _workdays())  # another room: its own suggestion
    got = (await _got(hass, hass_client, entry))["habits"]
    room = [s for s in got if s["area"] == entry.bed]
    assert len(room) == 1 and room[0]["rule"]["actions"] == [{"do": "room_covers", "area_id": entry.bed, "cover": "close"}]
    assert room[0]["entities"] == [BLIND1, BLIND2]
    # One of two blinds only: by name.
    h.data["events"].pop(f"{BLIND2}|close")
    got = (await _got(hass, hass_client, entry))["habits"]
    assert [s["rule"]["actions"] for s in got if s["area"] == entry.bed] == [[{"entity_id": BLIND1, "do": "close"}]]


async def test_climate_setpoint_habit_within_bounds(hass: HomeAssistant, hass_client, entry) -> None:
    h = _h(entry)
    _observe(h)
    _seed(h, CLIMATE, "temp", 6 * 60 + 30, _workdays(), level=21.5)
    [s] = (await _got(hass, hass_client, entry))["habits"]
    assert s["rule"]["actions"] == [{"entity_id": CLIMATE, "do": "temp", "value": 21.5}]
    # A level past the thermostat's own max (its bounds changed since): validate() drops it.
    h.data["events"].clear()
    _seed(h, CLIMATE, "temp", 6 * 60 + 30, _workdays(), level=31)
    assert (await _got(hass, hass_client, entry))["habits"] == []


async def test_never_a_lock_valve_garage_or_bare_switch(hass: HomeAssistant, hass_client, entry) -> None:
    """Even a store with such keys in it (another version, a hand edit) never makes them a suggestion."""
    h = _h(entry)
    _observe(h)
    for eid, act in ((LOCK, "unlocked"), (VALVE, "open"), (GARAGE, "open"), (BARE, "on"), (SIREN, "on"), (HIDDEN, "on")):
        _seed(h, eid, act, 8 * 60, range(1, 14))
    assert (await _got(hass, hass_client, entry))["habits"] == []


async def test_skips_existing_rule_dismissed_and_ha_automation(hass: HomeAssistant, hass_client, entry) -> None:
    h = _h(entry)
    _observe(h)
    _seed(h, ZIG, "on", 6 * 60 + 45, _workdays())
    _seed(h, CURTAIN, "close", 22 * 60, range(1, 14))
    _seed(h, FAN, "on", 19 * 60, range(1, 14))
    _seed(h, FAN, "off", 19 * 60 + 5, [1, 2, 3], where="auto")  # an HA automation moves the fan then
    client = await hass_client()
    got = (await _got(hass, hass_client, entry))["habits"]
    assert {s["entities"][0] for s in got} == {ZIG, CURTAIN}
    zig = next(s for s in got if s["entities"] == [ZIG])
    # «Сделать правилом»: saved with its key — gone from the list.
    res = await client.post(URL, json={"op": "save", "rule": zig["rule"]})
    assert res.status == 200, await res.text()
    # A rule of the person's own at about that time on that curtain: gone too.
    mine = {"when": {"event": "time", "at": "22:10"}, "actions": [{"do": "set", "domain": "cover", "cmd": "close", "entities": [CURTAIN]}]}
    assert (await client.post(URL, json={"op": "save", "rule": mine})).status == 200
    assert (await _got(hass, hass_client, entry))["habits"] == []
    # «Не предлагать»: a key not ours is refused; a dismissed one never comes back (after a restart too).
    await client.post(URL, json={"op": "delete", "id": (await res.json())["id"]})
    assert [s["key"] for s in (await _got(hass, hass_client, entry))["habits"]] == [zig["key"]]
    assert (await client.post(URL, json={"op": "dismiss_habit", "key": "../x"})).status == 400
    res = await client.post(URL, json={"op": "dismiss_habit", "key": zig["key"]})
    assert res.status == 200 and (await res.json())["habits"] == []
    stored = await hb.HaufloStore(hass, hb.STORE_KEY).async_load()
    assert zig["key"] in stored["dismissed"]


async def test_switch_off_forgets(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry) -> None:
    h = _h(entry)
    _observe(h)
    _seed(h, ZIG, "on", 6 * 60 + 45, _workdays())
    client = await hass_client(hass_read_only_access_token)  # any person of the house, as rules
    res = await client.post(URL, json={"op": "habits", "enabled": False})
    assert res.status == 200
    body = await res.json()
    assert body["habits_on"] is False and body["habits"] == []
    assert h.data["events"] == {} and h.data["days"] == []
    hass.states.async_set(ZIG, "on", {"brightness": 255}, context=Context())
    await hass.async_block_till_done()
    assert h.data["events"] == {}
    assert (await client.post(URL, json={"op": "habits", "enabled": "yes"})).status == 400
    assert (await (await client.post(URL, json={"op": "habits", "enabled": True})).json())["habits_on"] is True
