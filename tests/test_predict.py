"""«Дом умнеет — B» (predict.py): trends toward a failure, said to the staff before it happens — and, adversarially, the
silences: learning, flat / coarse / noisy sensors, a replaced battery, a colder week, a restart, a mass drop, a busy day,
hidden entities, a meter reset — plus the store surviving a restart."""

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo.insight import STORE_KEY, Insights
from custom_components.hauflo.predict import _P, theil_sen

H = 3600
D = 24 * H


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def local(day: int, hour: float = 12) -> float:
    """A local moment: `day` days after 2027-01-11 (a Monday), at `hour`."""
    base = datetime(2027, 1, 11, tzinfo=dt_util.get_default_time_zone())
    return (base + timedelta(days=day, hours=hour)).timestamp()


@pytest.fixture
async def ins(hass: HomeAssistant):
    ar.async_get(hass).async_create("Кухня")
    engine = Insights(hass, lambda eid: True, lambda: None)
    engine.clock = Clock(local(0, 0))
    await engine.async_start()
    yield engine
    engine.async_stop()


async def step(hass: HomeAssistant, ins: Insights, t: float, eid: str | None = None, state=None, attrs=None) -> None:
    ins.clock.t = t
    if eid is not None:
        hass.states.async_set(eid, state, attrs or {})
        await hass.async_block_till_done()
    ins._tick()


def kinds(ins: Insights) -> dict[str, dict]:
    return {i["key"]: i for i in ins.items()}


def det(ins: Insights, name: str):
    return next(d for d in ins.detectors if d.name == name)


def reg_entity(hass: HomeAssistant, domain: str, uid: str, platform: str = "test", **kw) -> str:
    return er.async_get(hass).async_get_or_create(domain, platform, uid, suggested_object_id=uid, **kw).entity_id


# --- statistics ----------------------------------------------------------------------------------------------------------


def test_theil_sen_ignores_one_wild_point():
    pts = [(x, 100 - 2 * x) for x in range(10)]
    pts[5] = (5, 0)  # one wild reading
    m, b = theil_sen(pts)
    assert m == pytest.approx(-2) and b == pytest.approx(100)
    assert theil_sen([(1, 5)]) is None


# --- battery -------------------------------------------------------------------------------------------------------------

BAT = {"device_class": "battery", "unit_of_measurement": "%", "friendly_name": "Датчик двери"}


async def battery_days(hass, ins, eid, values, start=0, dip=None):
    """One reading a day at noon (and a load dip at 3:00 if `dip`), a tick after midnight."""
    for i, v in enumerate(values):
        d = start + i
        if dip is not None:
            await step(hass, ins, local(d, 3), eid, v - dip, BAT)
        await step(hass, ins, local(d, 12), eid, v, BAT)
        await step(hass, ins, local(d + 1, 0.1))


async def test_battery_forecast_says_days_and_date(hass, ins):
    eid = reg_entity(hass, "sensor", "door_bat", entity_category=er.EntityCategory.DIAGNOSTIC)
    # 2 %/day from 70: 20 % is ≈ 15 days away → under battery_warn_days; load dips of 30 % at night are ignored (max).
    await battery_days(hass, ins, eid, [70 - 2 * i for i in range(10)], dip=30)
    item = kinds(ins)[f"battery_forecast|{eid}"]
    assert item["kind"] == "battery_forecast" and item["name"] == "Датчик двери"
    assert 14 <= item["daysLeft"] <= 16, item  # the line at today's start: 50 %, 2 %/day to 20 %
    assert item["value"] == 52 and item["limit"] == _P["battery_low_pct"] and item["perWeek"] == pytest.approx(14, abs=1)
    when = datetime.fromisoformat(item["date"]).date() - dt_util.as_local(dt_util.utc_from_timestamp(ins.clock.t)).date()
    assert abs(when.days - item["daysLeft"]) <= 1


async def test_battery_learning_flat_coarse_noisy_are_silent(hass, ins):
    flat = reg_entity(hass, "sensor", "flat_bat")
    coarse = reg_entity(hass, "sensor", "coarse_bat")
    noisy = reg_entity(hass, "sensor", "noisy_bat")
    young = reg_entity(hass, "sensor", "young_bat")
    for i in range(14):
        await step(hass, ins, local(i, 12), flat, 80, BAT)
        await step(hass, ins, local(i, 12.1), coarse, 100 if i < 7 else 50, BAT)
        await step(hass, ins, local(i, 12.2), noisy, 60 + (25 if i % 2 else -25) - i * 0.2, BAT)
        if i >= 10:
            await step(hass, ins, local(i, 12.3), young, 60 - 5 * (i - 10), BAT)
        await step(hass, ins, local(i + 1, 0.1))
    assert not kinds(ins), kinds(ins)


async def test_battery_replaced_resets_and_below_low_never_starts(hass, ins):
    eid = reg_entity(hass, "sensor", "bat")
    low = reg_entity(hass, "sensor", "low_bat")
    await battery_days(hass, ins, eid, [70 - 2 * i for i in range(10)])
    await battery_days(hass, ins, low, [19 - 0.5 * i for i in range(10)], start=10)
    assert f"battery_forecast|{eid}" in kinds(ins)
    assert f"battery_forecast|{low}" not in kinds(ins)  # the fixed low-battery alarm's job
    # Falls below the limit: stays said (no «снова в норме»), daysLeft 0.
    await battery_days(hass, ins, eid, [18, 17], start=20)
    assert kinds(ins)[f"battery_forecast|{eid}"]["daysLeft"] == 0
    # Replaced: a jump up → the history restarts, the word ends.
    await battery_days(hass, ins, eid, [100], start=22)
    assert f"battery_forecast|{eid}" not in kinds(ins)
    assert [v for _d, v in det(ins, "battery").recs[eid]["pts"]] == [100.0]


async def test_battery_hidden_or_other_unit_ignored(hass, ins):
    hidden = reg_entity(hass, "sensor", "hidden_bat", hidden_by=er.RegistryEntryHider.USER)
    volts = reg_entity(hass, "sensor", "volt_bat")
    for i in range(10):
        await step(hass, ins, local(i, 12), hidden, 70 - 2 * i, BAT)
        await step(hass, ins, local(i, 12.1), volts, 3.0 - 0.05 * i, {**BAT, "unit_of_measurement": "V"})
        await step(hass, ins, local(i + 1, 0.1))
    assert not kinds(ins)


# --- heating slowing -----------------------------------------------------------------------------------------------------

FLOOR = "climate.floor"


async def test_slowing_like_for_like_at_once(hass, ins, monkeypatch):
    hass.states.async_set(FLOOR, "heat", {"friendly_name": "Тёплый пол"})
    out = {"c": 0.0}
    monkeypatch.setattr(ins, "outdoor_c", lambda: out["c"])
    t = local(0)
    for _ in range(8):  # old: 2 °C/h at 0 °C outside
        t += D
        ins.clock.t = t
        ins.learned(FLOOR, "heat", 2.0, t)
    for _ in range(_P["trend_recent"]):  # now: 1 °C/h at the same 0 °C — a real slowdown
        t += D
        ins.clock.t = t
        ins.learned(FLOOR, "heat", 1.0, t)
    item = kinds(ins)[f"heat_slowing|{FLOOR}"]
    assert item["wasRate"] == 2.0 and item["nowRate"] == 1.0
    # Back to normal: ends (below slow_end_factor).
    for _ in range(_P["trend_recent"]):
        t += D
        ins.learned(FLOOR, "heat", 1.9, t)
    assert f"heat_slowing|{FLOOR}" not in kinds(ins)


async def test_slowing_colder_week_is_not_a_fault(hass, ins, monkeypatch):
    """Slower in a frost than in a thaw is physics: with no like-for-like history it must persist slow_persist_days."""
    hass.states.async_set(FLOOR, "heat", {})
    out = {"c": 5.0}
    monkeypatch.setattr(ins, "outdoor_c", lambda: out["c"])
    t = local(0)
    for _ in range(8):
        t += D
        ins.learned(FLOOR, "heat", 2.0, t)
    out["c"] = -15.0  # a frost: nothing old to compare with
    for _ in range(_P["trend_recent"]):
        t += D
        ins.clock.t = t
        ins.learned(FLOOR, "heat", 1.0, t)
    ins._tick()
    assert not kinds(ins)
    # Still slow after the frost has lasted slow_persist_days: said then (a fault stays, weather passes).
    ins.clock.t = t + _P["slow_persist_days"] * D
    ins._tick()
    assert f"heat_slowing|{FLOOR}" in kinds(ins)


async def test_slowing_needs_history(hass, ins):
    hass.states.async_set(FLOOR, "heat", {})
    t = local(0)
    for rate in (2.0, 2.0, 1.0, 1.0, 1.0):
        t += D
        ins.learned(FLOOR, "heat", rate, t)
    ins.clock.t = t + 30 * D
    ins._tick()
    assert not kinds(ins)


async def test_climate_effect_feeds_slowing(hass, ins):
    """A clean ClimateEffect episode reaches HeatSlowing (the wiring)."""
    await step(hass, ins, local(1, 10), FLOOR, "heat", {"current_temperature": 20.0, "temperature": 22.0})
    await step(hass, ins, local(1, 11), FLOOR, "heat", {"current_temperature": 22.0, "temperature": 22.0})
    assert det(ins, "slowing").recs[FLOOR]["heat"]["hist"][0][1] == pytest.approx(2.0)


# --- short cycling -------------------------------------------------------------------------------------------------------


async def cycle(hass, ins, eid, start: float, n: int, every_s: float, attrs=None):
    t = start
    for _ in range(n):
        await step(hass, ins, t, eid, "heat", {"hvac_action": "heating", **(attrs or {})})
        await step(hass, ins, t + every_s / 2, eid, "heat", {"hvac_action": "idle", **(attrs or {})})
        t += every_s
    return t


async def test_short_cycling_against_own_norm(hass, ins):
    eid = reg_entity(hass, "climate", "fan_coil")
    for d in range(4):  # a norm of 1 start an hour over 8 hours
        await cycle(hass, ins, eid, local(d, 8), 8, H)
    await step(hass, ins, local(4, 0.1))
    assert not kinds(ins)
    await cycle(hass, ins, eid, local(4, 8), 16, 300)  # 12 starts an hour
    item = kinds(ins)[f"short_cycling|{eid}"]
    assert item["normPerHour"] == 1.0 and item["perHour"] >= _P["cycle_min_per_h"]
    # Calm again: ends.
    await step(hass, ins, local(4, 8) + _P["cycle_window_h"] * H + 3600)
    assert not kinds(ins)


async def test_short_cycling_silent_while_learning_and_on_restarts(hass, ins):
    eid = reg_entity(hass, "climate", "ac")
    await cycle(hass, ins, eid, local(0, 8), 30, 120)  # busy, but nothing learned yet
    assert not kinds(ins)
    for d in range(1, 5):
        await cycle(hass, ins, eid, local(d, 8), 4, H)
    # Coming back from unavailable many times is not a start.
    t = local(5, 8)
    for _ in range(20):
        await step(hass, ins, t, eid, "unavailable", {})
        await step(hass, ins, t + 60, eid, "heat", {"hvac_action": "heating"})
        t += 120
    assert not kinds(ins)


async def test_short_cycling_relay_on_climate_device_only(hass, ins):
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test", "tstat")})
    clim = reg_entity(hass, "climate", "tstat", device_id=dev.id)
    relay = reg_entity(hass, "switch", "tstat_relay", device_id=dev.id)
    lamp = reg_entity(hass, "switch", "lamp")
    hass.states.async_set(clim, "heat", {})
    await step(hass, ins, local(0, 1))

    async def toggle(eid, start, n, every):
        t = start
        for _ in range(n):
            await step(hass, ins, t, eid, "on")
            await step(hass, ins, t + every / 2, eid, "off")
            t += every

    for d in range(4):
        await toggle(relay, local(d, 8), 6, H)
        await toggle(lamp, local(d, 8.2), 6, H)
    await toggle(relay, local(4, 8), 16, 300)
    await toggle(lamp, local(4, 8.01), 16, 300)
    assert set(kinds(ins)) == {f"short_cycling|{relay}"}


# --- link quality --------------------------------------------------------------------------------------------------------


async def test_link_quality_lqi_and_wifi_decline(hass, ins):
    lqi = reg_entity(hass, "sensor", "plug_lqi", platform="zha", entity_category=er.EntityCategory.DIAGNOSTIC)
    wifi = reg_entity(hass, "sensor", "cam_signal", entity_category=er.EntityCategory.DIAGNOSTIC)
    steady = reg_entity(hass, "sensor", "lamp_lqi", entity_category=er.EntityCategory.DIAGNOSTIC)
    for d in range(10):
        # Several readings a day with spread: the day's median counts.
        for k, wiggle in enumerate((-15, 0, 15)):
            await step(hass, ins, local(d, 8 + k), lqi, 120 - 8 * d + wiggle, {})
            await step(hass, ins, local(d, 8.5 + k), wifi, -60 - 2.5 * d + wiggle / 5, {"device_class": "signal_strength", "unit_of_measurement": "dBm"})
            await step(hass, ins, local(d, 9.5 + k), steady, 140 + wiggle * 2, {})
        await step(hass, ins, local(d + 1, 0.1))
    got = kinds(ins)
    assert set(got) == {f"link_quality|{lqi}", f"link_quality|{wifi}"}, got
    assert got[f"link_quality|{lqi}"]["limit"] == _P["lqi_warn"] and got[f"link_quality|{lqi}"]["daysLeft"] <= _P["link_warn_days"]
    assert got[f"link_quality|{wifi}"]["limit"] == -_P["rssi_warn_dbm"]


async def test_link_quality_noise_is_silent(hass, ins):
    eid = reg_entity(hass, "sensor", "far_lqi")
    for d in range(14):
        await step(hass, ins, local(d, 8), eid, 90 + (60 if d % 2 else -30) - d, {})
        await step(hass, ins, local(d + 1, 0.1))
    assert not kinds(ins)


# --- flapping ------------------------------------------------------------------------------------------------------------


async def drops(hass, ins, eid, day, n, hour=10):
    for k in range(n):
        t = local(day, hour + k * 0.5)
        await step(hass, ins, t, eid, "unavailable", {})
        await step(hass, ins, t + 120, eid, "on", {})
    await step(hass, ins, local(day, 23.9))


async def test_flapping_more_than_its_norm(hass, ins):
    eid = reg_entity(hass, "light", "hall")
    hass.states.async_set(eid, "on", {})
    for d in range(1, 10):
        await drops(hass, ins, eid, d, 1 if d % 3 == 0 else 0)
    assert not kinds(ins)
    for d in range(10, 13):
        await drops(hass, ins, eid, d, 5)
    item = kinds(ins)[f"flapping|{eid}"]
    assert item["perDay"] >= _P["flap_min_per_day"] and item["normPerDay"] == 0
    for d in range(13, 17):
        await drops(hass, ins, eid, d, 0)
    assert not kinds(ins)


async def test_flapping_mass_drop_and_startup_not_counted(hass, ins):
    lights = [reg_entity(hass, "light", f"l{i}") for i in range(12)]
    for eid in lights:
        hass.states.async_set(eid, "on", {})
    # Right after start: everything comes and goes — not counted.
    for eid in lights[:2]:
        await step(hass, ins, local(0, 0) + 60, eid, "unavailable", {})
        await step(hass, ins, local(0, 0) + 90, eid, "on", {})
    for d in range(1, 14):  # the bus drops: all 12 at once, 5 times a day
        for k in range(5):
            t = local(d, 8 + k)
            for i, eid in enumerate(lights):
                await step(hass, ins, t + i, eid, "unavailable", {})
            for eid in lights:
                await step(hass, ins, t + 300, eid, "on", {})
        await step(hass, ins, local(d, 23.9))
    assert not kinds(ins)
    assert not det(ins, "flapping").recs


# --- HA host -------------------------------------------------------------------------------------------------------------


async def test_disk_fills_and_old_backup(hass, ins):
    disk = reg_entity(hass, "sensor", "disk_use", platform="systemmonitor", translation_key="disk_use_percent")
    other = reg_entity(hass, "sensor", "cpu_use", platform="systemmonitor", translation_key="processor_use")
    backup = reg_entity(hass, "sensor", "backup_last", platform="backup", translation_key="last_successful_automatic_backup")
    last = dt_util.utc_from_timestamp(local(0, 3)).isoformat()
    hass.states.async_set(backup, last, {"device_class": "timestamp"})
    for d in range(10):
        await step(hass, ins, local(d, 12), disk, 70 + 1.5 * d, {"unit_of_measurement": "%"})
        await step(hass, ins, local(d, 12.1), other, 90 + d, {"unit_of_measurement": "%"})
        await step(hass, ins, local(d + 1, 0.1))
    got = kinds(ins)
    assert set(got) == {f"disk_full|{disk}", f"backup_old|{backup}"}, got
    assert got[f"disk_full|{disk}"]["limit"] == _P["disk_full_pct"] and got[f"disk_full|{disk}"]["daysLeft"] < _P["disk_warn_days"]
    assert got[f"backup_old|{backup}"]["days"] > _P["backup_max_days"]
    # A fresh backup ends that line.
    await step(hass, ins, local(10, 4), backup, dt_util.utc_from_timestamp(local(10, 3)).isoformat(), {"device_class": "timestamp"})
    assert f"backup_old|{backup}" not in kinds(ins)


async def test_disk_free_in_mib_and_unknown_backup(hass, ins):
    free = reg_entity(hass, "sensor", "disk_free", platform="systemmonitor", translation_key="disk_free")
    backup = reg_entity(hass, "sensor", "backup_last", platform="backup", translation_key="last_successful_automatic_backup")
    hass.states.async_set(backup, "unknown", {})
    for d in range(10):
        await step(hass, ins, local(d, 12), free, (12 - 0.8 * d) * 1024, {"unit_of_measurement": "MiB"})
        await step(hass, ins, local(d + 1, 0.1))
    got = kinds(ins)
    assert set(got) == {f"disk_full|{free}"}, got
    assert got[f"disk_full|{free}"]["limit"] == _P["disk_min_free_gb"]


# --- night water ---------------------------------------------------------------------------------------------------------

WATER = {"device_class": "water", "state_class": "total_increasing", "unit_of_measurement": "m³"}


async def nights(hass, ins, eid, value, first, uses, day_use=0.2):
    """Nights `first`..: `use` m³ between night_from_h and night_to_h, day_use m³ in the day. Returns the meter value."""
    a, b = _P["night_from_h"], _P["night_to_h"]
    for i, use in enumerate(uses):
        d = first + i
        await step(hass, ins, local(d, a - 0.5), eid, value, WATER)
        await step(hass, ins, local(d, a + 0.1))
        value += use / 2
        await step(hass, ins, local(d, (a + b) / 2), eid, value, WATER)
        value += use / 2
        await step(hass, ins, local(d, b - 0.2), eid, value, WATER)
        await step(hass, ins, local(d, b + 0.1))
        value += day_use
        await step(hass, ins, local(d, 14), eid, value, WATER)
    return value


async def test_night_water_in_a_row(hass, ins):
    eid = reg_entity(hass, "sensor", "water_meter")
    v = await nights(hass, ins, eid, 100.0, 1, [0.002] * 8)
    v = await nights(hass, ins, eid, v, 9, [0.06] * (_P["night_days"] - 1))
    assert not kinds(ins)  # not yet night_days in a row
    v = await nights(hass, ins, eid, v, 9 + _P["night_days"] - 1, [0.06])
    item = kinds(ins)[f"night_water|{eid}"]
    assert item["nightL"] == pytest.approx(60) and item["normL"] == pytest.approx(2) and item["nights"] == _P["night_days"]
    await nights(hass, ins, eid, v, 9 + _P["night_days"], [0.002])
    assert not kinds(ins)


async def test_night_water_day_use_and_reset_ignored(hass, ins):
    eid = reg_entity(hass, "sensor", "water2")
    v = await nights(hass, ins, eid, 100.0, 1, [0.002] * 8, day_use=5.0)  # a busy day is not a leak
    # A replaced meter reading 0 at night: no negative night, no word.
    a = _P["night_from_h"]
    await step(hass, ins, local(9, a + 1), eid, 0.0, WATER)
    await step(hass, ins, local(9, _P["night_to_h"] + 0.1))
    assert not kinds(ins)
    assert all(n[1] >= 0 for n in det(ins, "water").recs[eid]["nights"])


# --- restart -------------------------------------------------------------------------------------------------------------


async def test_store_survives_restart(hass, ins, hass_storage):
    eid = reg_entity(hass, "sensor", "bat")
    await battery_days(hass, ins, eid, [70 - 2 * i for i in range(10)])
    lights = reg_entity(hass, "light", "hall")
    hass.states.async_set(lights, "on", {})
    before = kinds(ins)
    assert f"battery_forecast|{eid}" in before
    saved = ins.data()
    import json

    saved = json.loads(json.dumps(saved))  # the store's JSON round trip
    hass_storage[STORE_KEY] = {"version": 1, "minor_version": 1, "key": STORE_KEY, "data": saved}
    ins.async_stop()
    second = Insights(hass, lambda eid: True, lambda: None)
    second.clock = Clock(ins.clock.t + 60)
    await second.async_start()
    second._tick()
    after = {i["key"]: i for i in second.items()}
    assert after[f"battery_forecast|{eid}"]["since"] == before[f"battery_forecast|{eid}"]["since"]
    assert len(det(second, "battery").recs[eid]["pts"]) == len(det(ins, "battery").recs[eid]["pts"])
    second.async_stop()


async def test_garbage_store_learns_anew(hass, hass_storage):
    hass_storage[STORE_KEY] = {"version": 1, "minor_version": 1, "key": STORE_KEY, "data": {
        "battery": {"series": {"sensor.x": {"kind": "battery", "pts": [["a", 1], [1]], "ring": "zz"}, "y": 5}},
        "slowing": {"climates": {"climate.a": {"heat": {"hist": [[1, -2, None], "x"]}}}},
        "cycling": {"entities": {"climate.a": {"ring": [1, "b"], "pts": None}}},
        "flapping": {"first": "x", "units": {"u": {"eid": 5}}},
        "water": {"meters": {"sensor.w": {"nights": [[1, 2, 3]]}}},
    }}
    engine = Insights(hass, lambda eid: True, lambda: None)
    engine.clock = Clock(local(0))
    await engine.async_start()
    engine._tick()
    assert engine.items() == []
    engine.async_stop()
