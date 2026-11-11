"""Аналитика п.8 (analytics.py): one engine of series / correlations / loops, driven by tuning.json `analytics` — and,
adversarially, its silences: learning (the shared 72 h gate), outliers, an ambiguous binding, «это норма», a restart,
300 series within the tick budget, every kind in a section."""

import json
from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import analytics as an
from custom_components.hauflo.insight import DETECTORS, STORE_KEY, Insights
from custom_components.hauflo.tuning import TUNING

H = 3600


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def local(day: int, hour: float = 0) -> float:
    base = datetime(2027, 1, 11, tzinfo=dt_util.get_default_time_zone())  # a Monday
    return (base + timedelta(days=day, hours=hour)).timestamp()


@pytest.fixture
async def ins(hass: HomeAssistant):
    engine = Insights(hass, lambda eid: True, lambda: None)
    engine.clock = Clock(local(0))
    await engine.async_start()
    yield engine
    engine.async_stop()


def det(ins: Insights, name: str):
    return next(d for d in ins.detectors if d.name == name)


async def put(hass, ins, t, eid, state, attrs=None):
    ins.clock.t = t
    hass.states.async_set(eid, state, attrs or {})
    await hass.async_block_till_done()


def tick(ins, t):
    ins.clock.t = t
    ins._tick()


def keys(ins) -> set[str]:
    return {i["key"] for i in ins.items()}


def row(ins, kind, eid):
    return next(r for r in ins.states(eid)["items"] if r["kind"] == kind)


POWER = {"device_class": "power", "unit_of_measurement": "W", "friendly_name": "Холодильник"}
BASE_H = 14 * 24  # two weeks hourly: the weekday bucket has its OBS_EPS values, the hour level too — the own norm judges


async def hourly(hass, ins, eid, attrs, values, start_h=0):
    """One value an hour (set a minute after the hour), the engine's tick right after."""
    for i, v in enumerate(values):
        t = local(0, start_h + i) + 60
        await put(hass, ins, t, eid, v, attrs)
        tick(ins, t + 1)


# --- statistics ----------------------------------------------------------------------------------------------------------


def test_robust_ignores_outliers():
    m, spread = an.robust([100, 101, 99, 100, 102, 98, 100, 101, 10000, -5000])
    assert m == pytest.approx(100.0)
    assert spread < 5.1  # MAD-based: the two wild values move nothing
    _m, flat = an.robust([50.0] * 12)
    assert flat == pytest.approx(an.SPREAD_FLOOR * 50)  # a series that never moves is not «off» by a hair


def test_buckets_season_daygroup_hour():
    sn = an.SeriesNorm.__new__(an.SeriesNorm)
    sn.ctx = type("C", (), {"season_now": staticmethod(lambda: "heat")})()
    power, water = an.SPEC["power"], an.SPEC["water"]
    assert sn.bucket(power, local(0, 7.5)) == "heat|0|7"  # Monday — a workday
    assert sn.bucket(power, local(5, 23)) == "heat|1|23"  # Saturday — the weekend
    assert sn.bucket(water, local(0, 7)) == "any|0|7"  # water: not split by season
    sn.ctx = type("C", (), {"season_now": staticmethod(lambda: None)})()
    assert sn.bucket(power, local(0, 7)) == f"{an.MID}|0|7"
    # Fallback: the bucket, else that hour on any day, else the season — the first with OBS_EPS values.
    s = {"b": {"heat|0|7": [1.0] * 3, "heat|1|7": [2.0] * 3, "heat|0|8": [3.0] * 10}}
    assert an.SeriesNorm.norm_at(s, "heat|0|7")[3] == "all"
    s["b"]["heat|1|7"] = [2.0] * 8
    assert an.SeriesNorm.norm_at(s, "heat|0|7")[3] == "hour"
    s["b"]["heat|0|7"] = [1.0] * 10
    assert an.SeriesNorm.norm_at(s, "heat|0|7")[:1] == (1.0,)


def test_value_of_units():
    st = type("S", (), {"state": "2.5", "attributes": {"unit_of_measurement": "kW"}})()
    assert an.value_of(an.SPEC["power"], st) == 2500
    st = type("S", (), {"state": "68", "attributes": {"unit_of_measurement": "°F"}})()
    assert an.value_of(an.SPEC["temperature"], st) == pytest.approx(20, abs=0.01)
    st = type("S", (), {"state": "5", "attributes": {"unit_of_measurement": "BTU"}})()
    assert an.value_of(an.SPEC["power"], st) is None  # a unit it doesn't know: no number, never a wrong one


# --- the canon decides, not the code -------------------------------------------------------------------------------------


def _example(rx: str) -> str:
    """A value a canon regex accepts: ^(a|b)$ → a, ^total → total."""
    return rx.strip("^$").strip("()").split("|")[0]


async def test_every_series_kind_found_by_canon(hass, ins):
    sn = det(ins, "series")
    for spec in an.SPECS:
        m = spec["match"]
        attrs = {k: _example(v) for k, v in m.items() if k in ("device_class", "state_class")}
        if spec.get("units"):
            attrs["unit_of_measurement"] = next(iter(spec["units"]))
        eid = f"{_example(m['domain'])}.probe_{spec['kind']}"
        hass.states.async_set(eid, "1", attrs)
        assert sn.spec_of(hass.states.get(eid))[0] == spec["kind"], spec["kind"]
    assert sn.spec_of(hass.states.get("sensor.probe_carbon_dioxide"))[1] is not None  # EN 16798 bound attached
    assert sn.spec_of(hass.states.get("sensor.probe_power"))[1] is None


async def test_new_series_type_by_canon_only(hass, ins, monkeypatch):
    """A type the code never names (illuminance), added to the canon tables only, is learned and said."""
    spec = {"kind": "lux", "match": {"domain": "^sensor$", "device_class": "^illuminance$"}, "units": {"lx": 1}, "unit": "raw", "agg": "mean", "season": 0, "min_delta": 50}
    monkeypatch.setattr(an, "SPECS", [spec, *an.SPECS])
    monkeypatch.setitem(an.SPEC, "lux", spec)
    monkeypatch.setitem(an.MATCH, "lux", an._rx(spec["match"]))
    attrs = {"device_class": "illuminance", "unit_of_measurement": "lx"}
    await hourly(hass, ins, "sensor.hall_lux", attrs, [300 + 10 * (i % 2) for i in range(BASE_H)] + [5] * 4)
    assert "norm_lux|sensor.hall_lux" in keys(ins)


# --- 1. series against their norm ------------------------------------------------------------------------------------------


async def test_power_off_its_norm_opens_and_closes(hass, ins):
    eid = "sensor.fridge_power"
    await hourly(hass, ins, eid, POWER, [95 + 10 * (i % 2) for i in range(BASE_H)])
    key = f"norm_power|{eid}"
    assert key not in keys(ins)
    assert "ready" in row(ins, "norm_power", eid)
    await hourly(hass, ins, eid, POWER, [600] * 2, start_h=BASE_H)
    assert key not in keys(ins)  # HOLD hours in a row, not one
    await hourly(hass, ins, eid, POWER, [600] * 2, start_h=BASE_H + 2)
    item = next(i for i in ins.items() if i["key"] == key)
    assert item["value"] == pytest.approx(600, rel=0.02) and item["limit"] == pytest.approx(100, abs=5)
    assert item["role"] == "power" and item["change"] > 400
    r = row(ins, "norm_power", eid)
    assert "deviation" in r and r["section"] == "energy" and r["unit"] == "w"
    # The failure is not learned as the norm; back to normal — it ends.
    await hourly(hass, ins, eid, POWER, [100, 95, 105], start_h=BASE_H + 4)
    assert key not in keys(ins)


async def test_shared_gate_no_word_before_72h(hass, ins):
    eid = "sensor.boiler_power"
    await hourly(hass, ins, eid, POWER, [95 + 10 * (i % 2) for i in range(40)] + [900] * 20)
    assert f"norm_power|{eid}" not in keys(ins)  # 60 h seen: learning, whatever the values
    assert "learning" in row(ins, "norm_power", eid)


async def test_co2_by_standard_without_own_norm(hass, ins):
    eid = "sensor.bedroom_co2"
    attrs = {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm", "friendly_name": "CO₂ спальня"}
    # Always high: its own norm is 1500 — still over the standard's bar (EN 16798 cat. III), said after `hold` hours.
    await hourly(hass, ins, eid, attrs, [1500 + 20 * (i % 2) for i in range(4)])
    item = next(i for i in ins.items() if i["key"] == f"norm_carbon_dioxide|{eid}")
    assert item["limit"] == 1200
    r = row(ins, "norm_carbon_dioxide", eid)
    assert r["standard"] == "en16798" and r["section"] == "air"


async def test_generic_measurement_asks_for_a_norm(hass, ins):
    hass.states.async_set("sensor.pump_pressure", "2.1", {"state_class": "measurement", "unit_of_measurement": "bar"})
    tick(ins, local(0, 1))
    r = row(ins, "norm_generic", "sensor.pump_pressure")
    assert r["excluded"]["reason"] == "no_norm" and r["ask"] is True


async def test_this_is_normal_mutes_the_note(hass, ins):
    eid = "sensor.fridge_power"
    await hourly(hass, ins, eid, POWER, [95 + 10 * (i % 2) for i in range(BASE_H)] + [600] * 4)
    key = f"norm_power|{eid}"
    assert key in keys(ins)
    ins.choose("norm_power", eid, "normal", "admin")
    assert key not in keys(ins)
    assert "normal" in row(ins, "norm_power", eid)


# --- 2. correlations ---------------------------------------------------------------------------------------------------------


async def _device_pair(hass, trig_domain, trig, sensor, attrs):
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("test", trig)})
    reg = er.async_get(hass)
    t = reg.async_get_or_create(trig_domain, "test", trig, suggested_object_id=trig, device_id=dev.id).entity_id
    s = reg.async_get_or_create("sensor", "test", sensor, suggested_object_id=sensor, device_id=dev.id).entity_id
    return t, s


async def kettle(hass, ins, sw, pw, t, rise):
    await put(hass, ins, t, pw, 0, POWER)
    await put(hass, ins, t + 1, sw, "on")
    await put(hass, ins, t + 30, pw, rise, POWER)
    tick(ins, t + 200)
    await put(hass, ins, t + 300, sw, "off")
    await put(hass, ins, t + 301, pw, 0, POWER)


async def test_load_on_without_consumption(hass, ins):
    sw, pw = await _device_pair(hass, "switch", "kettle", "kettle_power", POWER)
    hass.states.async_set(sw, "off")
    hass.states.async_set(pw, "0", POWER)
    tick(ins, local(0))
    link = det(ins, "correlation").links[sw]
    assert link["s"] == pw and link["c"] == "load"
    for d in range(4):
        for k in range(3):
            await kettle(hass, ins, sw, pw, local(d, 8 + k), 1500)
    key = f"corr_load|{sw}"
    assert key not in keys(ins)
    for k in range(2):
        await kettle(hass, ins, sw, pw, local(4, 8 + k), 0)
    assert key not in keys(ins)  # HOLD events in a row
    await kettle(hass, ins, sw, pw, local(4, 10), 0)
    item = next(i for i in ins.items() if i["key"] == key)
    assert item["limit"] == 1500 and item["change"] == 0 and item["role"] == "power"
    assert row(ins, "corr_load", sw)["effect"] == pw
    await kettle(hass, ins, sw, pw, local(4, 11), 1500)
    assert key not in keys(ins)


HUM = {"device_class": "humidity", "unit_of_measurement": "%"}


async def _in_area(hass, area, *eids):
    a = ar.async_get(hass).async_get_or_create(area)
    reg = er.async_get(hass)
    out = []
    for e in eids:
        dom, obj = e.split(".")
        ent = reg.async_get_or_create(dom, "test", obj, suggested_object_id=obj)
        reg.async_update_entity(ent.entity_id, area_id=a.id)
        out.append(ent.entity_id)
    return out


async def extract(hass, ins, fan, hum, t, start, end):
    await put(hass, ins, t, hum, start, HUM)
    await put(hass, ins, t + 1, fan, "on")
    await put(hass, ins, t + 20 * 60, hum, end, HUM)
    tick(ins, t + 31 * 60)
    await put(hass, ins, t + 32 * 60, fan, "off")


async def test_extract_fan_and_humidity_fall(hass, ins):
    fan, hum = await _in_area(hass, "Ванная", "fan.bath", "sensor.bath_humidity")
    hass.states.async_set(fan, "off")
    hass.states.async_set(hum, "60", HUM)
    tick(ins, local(0))
    assert det(ins, "correlation").links[fan]["c"] == "vent_humidity"
    for d in range(4):
        for k in range(3):
            await extract(hass, ins, fan, hum, local(d, 8 + k), 80, 65)
    key = f"corr_vent_humidity|{fan}"
    for k in range(3):
        await extract(hass, ins, fan, hum, local(4, 8 + k), 80, 82)  # the other way: no fall
    assert key in keys(ins)
    assert row(ins, "corr_vent_humidity", fan)["section"] == "air"


async def test_two_candidates_bind_nothing(hass, ins):
    fan, _h1, _h2 = await _in_area(hass, "Санузел", "fan.wc", "sensor.wc_humidity", "sensor.wc_humidity_2")
    hass.states.async_set(fan, "off")
    hass.states.async_set("sensor.wc_humidity", "60", HUM)
    hass.states.async_set("sensor.wc_humidity_2", "60", HUM)
    tick(ins, local(0))
    assert fan not in det(ins, "correlation").links  # a guess is worse than silence


# --- 3. loops: tuning against peers ----------------------------------------------------------------------------------------

FLOORS = ["climate.floor_1", "climate.floor_2", "climate.floor_3", "climate.floor_slow"]


def floor_attrs(cur, tgt, heating):
    return {"current_temperature": round(cur, 2), "temperature": tgt, "hvac_action": "heating" if heating else "idle", "hvac_modes": ["off", "heat"]}


async def floor_days(hass, ins, days, start=0):
    rate = {e: (1 / 3 if e.endswith("slow") else 1.0) for e in FLOORS}
    for d in range(start, start + days):
        for h in range(24):
            t = local(d, h) + 30
            for e in FLOORS:
                if 6 <= h < 22:
                    cur = min(24.0, 20 + rate[e] * (h - 6))
                    hass.states.async_set(e, "heat", floor_attrs(cur, 24, cur < 24))
                else:
                    hass.states.async_set(e, "heat", floor_attrs(20, 20, False))
            ins.clock.t = t
            await hass.async_block_till_done()
            tick(ins, t + 1)


@pytest.fixture
async def floors(hass, ins):
    for e in FLOORS:
        hass.states.async_set(e, "heat", floor_attrs(20, 20, False))
    det(ins, "noeffect").set_kinds({e: "floor" for e in FLOORS})
    tick(ins, local(0))
    lt = det(ins, "loops")
    assert {lt.loops[e]["t"] for e in FLOORS} == {"floor"}
    await floor_days(hass, ins, 12)
    return lt


async def test_one_floor_loop_slower_than_its_peers(hass, ins, floors):
    lt = floors
    by = {}
    for i in lt.items(ins.clock.t):
        by.setdefault(i["entityId"], set()).add(i["kind"])
    # Per °C of the gap the time to setpoint is the rate's other side: said once, as the rate.
    assert by == {"climate.floor_slow": {"loop_slow"}}
    assert len(lt.loops["climate.floor_slow"]["ep"]) >= 10
    item = next(i for i in ins.items() if i["key"] == "loop_slow|climate.floor_slow")
    assert item["nowRate"] == pytest.approx(1 / 3, abs=0.02) and item["wasRate"] == pytest.approx(0.5, abs=0.02)
    r = row(ins, "loop_slow", "climate.floor_slow")
    assert r["tip"] == "floor_rate" and r["section"] == "climate" and r["ask"] == "supply"
    # 12 h for a 4 °C gap = 3 h/°C (peers 1 h/°C): kept per unit of the gap, the slow loop is said once — as its rate.
    assert lt.loops["climate.floor_slow"]["ep"][-1][1] == pytest.approx(3, abs=0.05)
    assert "deviation" not in row(ins, "loop_warmup", "climate.floor_slow")
    # The supply sensor said: no more asking, its reading goes with the hint.
    hass.states.async_set("sensor.floor_supply", "35", {"device_class": "temperature", "unit_of_measurement": "°C"})
    lt.set_supply("climate.floor_slow", "sensor.floor_supply")
    assert row(ins, "loop_slow", "climate.floor_slow")["supply"] == "sensor.floor_supply"
    assert next(i for i in lt.items(ins.clock.t) if i["kind"] == "loop_slow")["supply"] == 35
    # Notes come after the findings in the list.
    assert all(an.is_note(i["kind"]) for i in ins.items()[-2:])


async def test_loop_note_muted_and_kept_over_restart(hass, ins, floors, hass_storage):
    key = "loop_slow|climate.floor_slow"
    since = next(i for i in ins.items() if i["key"] == key)["since"]
    saved = json.loads(json.dumps(ins.data()))
    hass_storage[STORE_KEY] = {"version": 1, "minor_version": 1, "key": STORE_KEY, "data": saved}
    ins.async_stop()
    second = Insights(hass, lambda eid: True, lambda: None)
    second.clock = Clock(ins.clock.t + 60)
    await second.async_start()
    second._tick()
    assert next(i for i in second.items() if i["key"] == key)["since"] == since
    assert det(second, "loops").loops["climate.floor_slow"]["ep"] == floors.loops["climate.floor_slow"]["ep"]
    second.choose("loop_slow", "climate.floor_slow", "normal", "admin")
    assert key not in {i["key"] for i in second.items()}
    second.async_stop()


async def toggles(hass, ins, eid, attrs, per_hour, days, start=0):
    for d in range(start, start + days):
        for h in range(24):
            n = per_hour if h < 20 else 0
            for k in range(2 * n):
                await put(hass, ins, local(d, h) + k * (H / (2 * n)) + 5, eid, "heat", {**attrs, "hvac_action": "heating" if k % 2 == 0 else "idle"})
            tick(ins, local(d, h) + H - 1)


async def test_air_handler_stage_chatter(hass, ins):
    eid = "climate.pritochnaia"
    attrs = {"friendly_name": "Приточная установка", "hvac_modes": ["off", "heat"]}
    hass.states.async_set(eid, "heat", {**attrs, "hvac_action": "idle"})
    tick(ins, local(0))
    lt = det(ins, "loops")
    assert lt.loops[eid]["t"] == "ahu"
    await toggles(hass, ins, eid, attrs, 2, 5)  # 40 starts a day, the type's bar is 24
    item = next(i for i in ins.items() if i["key"] == f"loop_cycles|{eid}")
    assert item["perDay"] >= 39 and item["normPerDay"] == 24
    assert row(ins, "loop_cycles", eid)["tip"] == "ahu_cycles"


async def test_boiler_short_cycles_by_integration(hass, ins):
    eid = er.async_get(hass).async_get_or_create("climate", "opentherm_gw", "gw1", suggested_object_id="kotel").entity_id
    attrs = {"friendly_name": "Котёл", "hvac_modes": ["off", "heat"]}
    hass.states.async_set(eid, "heat", {**attrs, "hvac_action": "idle"})
    tick(ins, local(0))
    lt = det(ins, "loops")
    assert lt.loops[eid]["t"] == "boiler"
    await toggles(hass, ins, eid, attrs, 5, 5)  # 100 starts a day, the type's bar is 72
    assert f"loop_cycles|{eid}" in {i["key"] for i in lt.items(ins.clock.t)}


async def test_quiet_boiler_says_nothing(hass, ins):
    eid = er.async_get(hass).async_get_or_create("climate", "opentherm_gw", "gw2", suggested_object_id="kotel2").entity_id
    attrs = {"friendly_name": "Котёл", "hvac_modes": ["off", "heat"]}
    hass.states.async_set(eid, "heat", {**attrs, "hvac_action": "idle"})
    tick(ins, local(0))
    await toggles(hass, ins, eid, attrs, 1, 5)  # 20 a day
    assert det(ins, "loops").items(ins.clock.t) == []


# --- limits ------------------------------------------------------------------------------------------------------------------


async def test_series_capped_and_tick_within_budget(hass, ins):
    for i in range(an.MAX_SERIES + 20):
        hass.states.async_set(f"sensor.p{i}", str(100 + i % 7), POWER)
    tick(ins, local(0))
    sn = det(ins, "series")
    assert len(sn.series) == an.MAX_SERIES
    for h in range(1, 4):  # every series closes an hour on these ticks
        tick(ins, local(0, h) + 1)
        assert sn.took_ms < an.BUDGET_MS, sn.took_ms


# --- sections ------------------------------------------------------------------------------------------------------------------


def test_every_kind_has_a_section():
    sections = TUNING["insight"]["sections"]
    order = TUNING["insight"]["section_order"]
    kinds = {k for cls in DETECTORS for k in cls.KINDS}
    assert kinds <= set(sections), kinds - set(sections)
    assert set(sections.values()) <= set(order)
    assert all(an.is_note(k) for k in kinds if k.startswith(tuple(TUNING["analytics"]["note_prefixes"])))
    # The cloud keeps a kind only as `^[a-z_]{1,30}`: no digits, short.
    import re

    assert all(re.fullmatch(r"[a-z_]{1,30}", k) for k in kinds)


# --- acceptance п.8: a daily profile is the norm; «это норма» keeps learning; nothing to move; warm-up per °C ---------------


async def test_daily_profile_is_its_norm_not_a_note(hass, ins):
    """C1: a healthy 50 W night / 500 W day says nothing in two weeks; the 03:00 norm is the night's, the 15:00 one the
    day's; while only the season has enough values it is «learning», never «off»."""
    eid = "sensor.profile_power"
    key = f"norm_power|{eid}"
    sn, spec = det(ins, "series"), an.SPEC["power"]
    for i in range(BASE_H):
        await hourly(hass, ins, eid, POWER, [50 if i % 24 < 7 else 500], start_h=i)
        assert key not in keys(ins), i
        if i == 100:  # past the 72 h gate, each hour seen 4 times: the season stands in, nothing is judged
            assert "learning" in row(ins, "norm_power", eid)
            assert sn.judge_at(sn.series[eid], sn.bucket(spec, local(0, i))) is None
    s = sn.series[eid]
    assert sn.judge_at(s, sn.bucket(spec, local(14, 3)))[0] == [50]  # a weekday's 03:00: its bucket
    assert sn.judge_at(s, sn.bucket(spec, local(14, 15)))[0] == [500]
    assert sn.judge_at(s, sn.bucket(spec, local(12, 3)))[0] == [50, 50]  # a Saturday: the hour, and its own agree
    assert "ready" in row(ins, "norm_power", eid)
    # A real one is still said: the day's 500 W through the night.
    await hourly(hass, ins, eid, POWER, [500] * 6, start_h=BASE_H)
    assert key in keys(ins)


async def test_this_is_normal_keeps_learning(hass, ins):
    """M1: after «это норма» the norm follows the new level (a new fridge), the deviation closes on its own."""
    eid = "sensor.fridge_power"
    key = f"norm_power|{eid}"
    await hourly(hass, ins, eid, POWER, [100] * BASE_H + [600] * 4)
    assert key in keys(ins)
    ins.choose("norm_power", eid, "normal", "admin")
    await hourly(hass, ins, eid, POWER, [600] * BASE_H, start_h=BASE_H + 4)
    sn = det(ins, "series")
    s = sn.series[eid]
    assert sn.judge_at(s, sn.bucket(an.SPEC["power"], ins.clock.t))[0][0] == 600
    assert s["open"] is None


async def _healthy_fan(hass, ins, area, fan, hum):
    fan, hum = await _in_area(hass, area, fan, hum)
    hass.states.async_set(fan, "off")
    hass.states.async_set(hum, "60", HUM)
    tick(ins, local(0))
    return fan, hum


async def test_corr_this_is_normal_keeps_learning(hass, ins):
    """M1 for correlations: after «это норма» a weaker fan's change is learned, the expected one follows it."""
    fan, hum = await _healthy_fan(hass, ins, "Ванная", "fan.bath", "sensor.bath_humidity")
    for d in range(4):
        for k in range(3):
            await extract(hass, ins, fan, hum, local(d, 8 + k), 80, 65)
    key = f"corr_vent_humidity|{fan}"
    for k in range(3):
        await extract(hass, ins, fan, hum, local(4, 8 + k), 80, 78)
    assert key in keys(ins)
    ins.choose("corr_vent_humidity", fan, "normal", "admin")
    for k in range(3):
        await extract(hass, ins, fan, hum, local(5, 8 + k), 80, 74)
    assert det(ins, "correlation").links[fan]["d"][-3:] == [6, 6, 6]


async def test_fan_in_a_dry_room_is_not_a_failure(hass, ins):
    """M2: the fan on a timer in a dry bathroom — already at the level it brings the room to — is neither a failure nor
    learned; by the canon's level before it has learned one, by its own after. A real failure is still caught."""
    fan, hum = await _healthy_fan(hass, ins, "Ванная", "fan.bath", "sensor.bath_humidity")
    link = det(ins, "correlation").links[fan]
    for k in range(2):
        await extract(hass, ins, fan, hum, local(0, 6 + k), 45, 45)  # below the canon's 50 %
    assert link["d"] == [] and link["bad"] == 0
    for d in range(4):
        for k in range(3):
            await extract(hass, ins, fan, hum, local(d, 8 + k), 80, 65)
    key = f"corr_vent_humidity|{fan}"
    for k in range(3):
        await extract(hass, ins, fan, hum, local(4, 8 + k), 62, 62)  # below its learned 65 %
    assert key not in keys(ins) and link["bad"] == 0 and len(link["d"]) == 12
    for k in range(3):
        await extract(hass, ins, fan, hum, local(5, 8 + k), 80, 82)
    assert key in keys(ins)


async def test_big_gap_is_not_a_long_warmup(hass, ins):
    """M3: an AC cooling 6 °C at 1.5 °C/h needs 4 h — 0.67 h/°C, healthy (an absolute 1.5 h said «long»)."""
    e = "climate.split"
    modes = {"hvac_modes": ["off", "cool", "dry", "fan_only"]}
    hass.states.async_set(e, "cool", {**floor_attrs(28, 28, False), **modes})
    tick(ins, local(0))
    lt = det(ins, "loops")
    for d in range(12):
        for h in range(24):
            t = local(d, h) + 30
            if 12 <= h < 20:
                cur = max(22.0, 28 - 1.5 * (h - 12))
                hass.states.async_set(e, "cool", {**floor_attrs(cur, 22, False), **modes, "hvac_action": "cooling" if cur > 22 else "idle"})
            else:
                hass.states.async_set(e, "cool", {**floor_attrs(28, 28, False), **modes})
            ins.clock.t = t
            await hass.async_block_till_done()
            tick(ins, t + 1)
    eps = lt.loops[e]["ep"]
    assert len(eps) >= 10 and all(w == pytest.approx(4 / 6, abs=0.05) for _r, w, _o in eps)
    assert lt.items(ins.clock.t) == []
    assert row(ins, "loop_warmup", e)["ready"]["unit"] == "h_per_c"


# --- edge branches: bad stored rows, a vanished entity, units it doesn't know ---------------------------------------------


def test_matches_flags_regexes_and_callables():
    import re

    assert an._matches({"a": re.compile("x")}, {"a": "xy"}) and not an._matches({"a": re.compile("x")}, {"a": None})
    assert not an._matches({"a": re.compile("x")}, {"a": "zz"})
    assert an._matches({"f": 1}, {"f": lambda: True}) and not an._matches({"f": 0}, {"f": lambda: True})
    assert an._matches({"v": 3}, {"v": 3}) and not an._matches({"v": 3}, {"v": 4})


def test_value_of_edges():
    from homeassistant.core import State

    spec = {"units": {"W": 1, "kW": 1000}, "minus": {"kW": 0}}
    assert an.value_of(spec, None) is None
    assert an.value_of(spec, State("sensor.a", "unavailable")) is None
    assert an.value_of(spec, State("sensor.a", "abc", {"unit_of_measurement": "W"})) is None
    assert an.value_of({}, State("sensor.a", "5")) == 5
    assert an.value_of(spec, State("sensor.a", "5", {"unit_of_measurement": "var"})) is None
    assert an.value_of(spec, State("sensor.a", "2", {"unit_of_measurement": "kW"})) == 2000


async def test_load_drops_garbage_and_keeps_the_good(hass, ins):
    d = det(ins, "series")
    kind = next(iter(an.SPEC))
    d.load(
        {
            "series": {
                "sensor.bad1": "x",
                "sensor.bad2": {"k": "no_such_kind"},
                "sensor.ok": {"k": kind, "b": {"1": [1, "x", 2], "2": [], "3": "nope"}, "bad": "x", "open": {"since": "nan?"}, "tn": 9999, "n": 3, "last": 1.5},
            }
        }
    )
    assert set(d.series) == {"sensor.ok"}
    s = d.series["sensor.ok"]
    assert s["b"] == {"1": [1.0, 2.0]} and s["bad"] == 0 and s["open"] is None and s["tn"] is None and s["n"] == 3 and s["last"] == 1.5
    assert d.dump() == {"series": d.series}


async def test_an_entity_gone_from_ha_is_forgotten_after_the_grace(hass, ins):
    d = det(ins, "series")
    kind = next(iter(an.SPEC))
    d.series["sensor.vanished"] = {"k": kind, "b": {}, "bad": 0, "bb": 0, "open": None, "norm": None, "n": 0, "tn": None, "last": None}
    d.acc["sensor.vanished"] = {}
    d._discover(ins.started + 1)
    assert "sensor.vanished" in d.series  # HA may still be loading
    d._discover(ins.started + an.GONE_AFTER + 1)
    assert "sensor.vanished" not in d.series and "sensor.vanished" not in d.acc


def _own(kind):
    return {"k": kind, "b": {}, "bad": 0, "bb": 0, "open": None, "norm": None, "n": 0, "tn": None, "last": None}


async def test_a_meter_counts_increases_and_a_reset_from_zero(hass, ins):
    from homeassistant.core import State

    d = det(ins, "series")
    eid = "sensor.meter"
    attrs = {"unit_of_measurement": "kWh"}
    d.series[eid] = _own("energy")
    t0 = local(0, 1) + 10
    hass.states.async_set(eid, "10", attrs)
    d._new_acc(eid, an.SPEC["energy"], t0)
    st = lambda v: State(eid, v, attrs)  # noqa: E731
    d.on_event(st("10"), st("12"), t0 + 60)
    d.on_event(st("12"), st("unavailable"), t0 + 120)  # no number: nothing counted, last value kept
    d.on_event(st("unavailable"), st("3"), t0 + 180)  # a fall = a reset: counted from zero
    assert d.acc[eid]["inc"] == pytest.approx(5)  # +2, then 3 counted from zero
    assert d.acc[eid]["v"] == 3
    assert d._value(an.SPEC["energy"], d.acc[eid]) is None  # a partial hour is never judged


async def test_motion_counts_only_real_activations_and_generic_is_not_its_own(hass, ins):
    from homeassistant.core import State

    d = det(ins, "series")
    d.series["binary_sensor.pir"] = _own("motion")
    t0 = local(0, 1) + 10
    d._new_acc("binary_sensor.pir", an.SPEC["motion"], t0)
    mv = lambda v: State("binary_sensor.pir", v)  # noqa: E731
    d.on_event(mv("off"), mv("on"), t0 + 1)
    d.on_event(mv("on"), mv("on"), t0 + 2)  # not a change to on
    d.on_event(None, mv("on"), t0 + 3)  # no previous state
    d.on_event(mv("unavailable"), mv("on"), t0 + 4)
    assert d.acc["binary_sensor.pir"]["n"] == 1
    a = d.acc["binary_sensor.pir"]
    a["partial"] = False
    assert d._value(an.SPEC["motion"], a) == pytest.approx(an.SECONDS_PER_HOUR / an.PERIOD)
    d.series["sensor.g"] = _own("generic")
    d.on_event(State("sensor.g", "1"), State("sensor.g", "2"), t0 + 5)
    assert "sensor.g" not in d.acc
    d.on_event(State("sensor.zz", "1"), State("sensor.zz", "2"), t0 + 6)  # not in the series at all


async def test_a_long_silence_restarts_the_hour_instead_of_replaying_it(hass, ins):
    d = det(ins, "series")
    eid = "sensor.p"
    d.series[eid] = _own("power")
    t0 = local(0, 1)
    a = d._new_acc(eid, an.SPEC["power"], t0)
    d._roll(eid, d.series[eid], an.SPEC["power"], a, t0 + (an.MAX_CATCHUP + 2) * an.PERIOD)
    assert d.acc[eid] is not a and d.acc[eid]["p"] > a["p"]


async def test_loop_reset_and_supply_sensor(hass, ins):
    d = det(ins, "loops")
    lt = next(iter(an.LOOP_TYPES))
    d.reset("climate.none", "x")  # an unknown loop: nothing happens
    d.loops["sensor.l"] = {"t": lt, "ep": [[1, 1, 1]], "days": {"2027-01-11": [1, 1]}, "open": {}, "log": [{"a": 1}]}
    rate_kind = an.LOOP_METRICS["rate"]
    d.reset("sensor.l", rate_kind)
    assert d.loops["sensor.l"]["ep"] == [] and d.loops["sensor.l"]["log"] == []
    d.loops["sensor.l"]["days"] = {"x": [1, 1]}
    d.reset("sensor.l", an.LOOP_METRICS["duty"])
    assert d.loops["sensor.l"]["days"] == {}
    d.set_supply("sensor.l", "sensor.sup")
    assert d.supply["sensor.l"] == "sensor.sup"
    d.set_supply("sensor.l", "")
    assert "sensor.l" not in d.supply
    assert d.episodes("climate.x") == [] and d.episodes("sensor.l") == []
