"""«Нет дома — имитация присутствия» (presence.py, owner 28.10): learned from 14 days of history, not a copy of last
week — lights only in the dark and only in rooms used in the evening, at most a few rooms at once, ± 15 min; blinds at
the usual times or by the sun; too little history said plainly; only Hauflo's own safe lights / covers; a device someone
touched is left alone; switches itself off after «until»; survives a restart; the tunnel can't reach it."""

import datetime as dt
import random
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import Context, HomeAssistant, ServiceCall, State
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockConfigEntry, async_fire_time_changed

from custom_components.hauflo import _http_allowed
from custom_components.hauflo import presence as P
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

pytestmark = pytest.mark.parametrize("expected_lingering_timers", [True])
URL = "/api/hauflo/presence"
LIVING, KITCHEN, DESK, BED = "light.living_ceiling", "light.kitchen", "light.study_desk", "light.bedroom"
BLIND, GARAGE, HAND, GROUP, HIDDEN = "cover.living_blind", "cover.garage_door", "light.integrator_hand", "light.living_group", "light.service_hidden"
TZ = "Europe/Moscow"


def _at(day: dt.date, hm: str) -> dt.datetime:
    h, m = map(int, hm.split(":"))
    return dt.datetime.combine(day, dt.time(h % 24, m), tzinfo=dt_util.get_default_time_zone()) + dt.timedelta(days=h // 24)


def _st(eid: str, state: str, t: dt.datetime, **attrs) -> State:
    return State(eid, state, attrs, last_changed=t, last_updated=t)


def _sessions(eid: str, days: list[dt.date], frm: str, to: str, bright: int | None = 128) -> list[State]:
    out = []
    for d in days:
        out.append(_st(eid, "on", _at(d, frm), **({"brightness": bright} if bright else {})))
        out.append(_st(eid, "off", _at(d, to)))
    return out


INFO = {
    LIVING: {"domain": "light", "dim": True, "pos": False},
    KITCHEN: {"domain": "light", "dim": False, "pos": False},
    DESK: {"domain": "light", "dim": False, "pos": False},
    BED: {"domain": "light", "dim": True, "pos": False},
    BLIND: {"domain": "cover", "dim": False, "pos": True},
}
ROOMS = {LIVING: ("living", "Гостиная"), KITCHEN: ("kitchen", "Кухня"), DESK: ("study", "Кабинет"), BED: ("bed", "Спальня"), BLIND: ("living", "Гостиная")}


def _family(now: dt.datetime, evenings: int = 13) -> dict[str, list[State]]:
    """A family: living room 19:00–22:30 on weekdays, kitchen 18:30–20:00, bedroom 22:30–23:15, a desk lamp 9–18 every
    day, the living-room blind closed at 21:00 to 20 %."""
    today = dt_util.as_local(now).date()
    days = [today - dt.timedelta(days=i) for i in range(evenings + 1, 1, -1)]  # whole days before the plan's
    weekdays = [d for d in days if d.weekday() < 5]
    start = now - dt.timedelta(days=P.LEARN_DAYS)
    blind = [_st(BLIND, "open", start, current_position=100)]
    for d in days:
        blind += [_st(BLIND, "open", _at(d, "21:00"), current_position=20)]
        blind += [_st(BLIND, "open", _at(d, "23:59"), current_position=100)]  # opened again late (not a morning open)
    return {
        LIVING: [_st(LIVING, "off", start), *_sessions(LIVING, weekdays, "19:00", "22:30")],
        KITCHEN: [_st(KITCHEN, "off", start), *_sessions(KITCHEN, days, "18:30", "20:00", None)],
        BED: [_st(BED, "off", start), *_sessions(BED, days, "22:30", "23:15", 40)],
        DESK: [_st(DESK, "off", start), *_sessions(DESK, days, "09:00", "18:00", None)],
        BLIND: blind,
    }


@pytest.fixture
async def tz(hass: HomeAssistant):
    await hass.config.async_set_time_zone(TZ)
    hass.config.latitude, hass.config.longitude = 55.75, 37.62


def _now(day: str = "2026-10-28", hm: str = "00:05") -> dt.datetime:
    return _at(dt.date.fromisoformat(day), hm)


# --- learning -----------------------------------------------------------------------------------------------------------
async def test_profile_building(hass: HomeAssistant, tz) -> None:
    now = _now()  # a Wednesday
    m = P.build_model(_family(now), INFO, ROOMS, now - dt.timedelta(days=14), now)
    liv = m["lights"][LIVING]
    slot = (19 * 60 + 30 - 240) // 15  # 19:30
    assert liv["wd"][slot] == 1.0 and liv["we"][slot] == 0.0  # weekdays only
    assert liv["wd"][(12 * 60 - 240) // 15] == 0.0
    assert liv["bright"] == 50  # brightness 128 → 50 %
    assert m["lights"][KITCHEN]["all"][(19 * 60 - 240) // 15] == 1.0
    assert m["evenings"] == 13 and m["days"] == 13
    assert m["rooms"]["living"]["durations"] and all(d == 210 for d in m["rooms"]["living"]["durations"])
    assert m["last_light"] and max(m["last_light"]) == (23 * 60 + 15 - 240)  # the bedroom's 23:15
    assert m["covers"][BLIND]["close"] and set(m["covers"][BLIND]["positions"]) == {20}
    assert not m["covers"][BLIND]["open"]  # opened at 23:59 — no morning habit
    # A light whose history starts later only counts the days it has.
    young = {LIVING: _sessions(LIVING, [dt_util.as_local(now).date() - dt.timedelta(days=i) for i in (2, 1)], "19:00", "22:00")}
    assert P.build_model(young, INFO, ROOMS, now - dt.timedelta(days=14), now)["days"] <= 2


async def test_dark_only_and_never_daytime(hass: HomeAssistant, tz) -> None:
    now = _now()
    m = P.build_model(_family(now), INFO, ROOMS, now - dt.timedelta(days=14), now)
    day = dt_util.as_local(now).date()
    sun = (_at(day, "06:30"), _at(day, "20:10"))  # a summer-like evening: dark late
    for seed in range(60):
        events, summary = P.plan_day(m, day, sun, now, random.Random(seed))
        ons = [e for e in events if e["do"] == "on"]
        assert all(e["at"] >= sun[1] - dt.timedelta(minutes=20) for e in ons), seed
        assert all(e["at"] <= _at(day, "25:00") + dt.timedelta(minutes=2) for e in events if e["entity_id"].startswith("light.")), seed
        assert not any(e["entity_id"] == DESK for e in events)  # the 9–18 desk lamp is invisible from the street
        assert summary["lights"] == "learned"


async def test_cap_of_rooms_at_once(hass: HomeAssistant, tz) -> None:
    now = _now()
    day = dt_util.as_local(now).date()
    past = [day - dt.timedelta(days=i) for i in range(14, 1, -1)]
    info, rooms, hist = {}, {}, {}
    for i in range(6):
        eid = f"light.room_{i}"
        info[eid] = {"domain": "light", "dim": False, "pos": False}
        rooms[eid] = (f"r{i}", f"Комната {i}")
        hist[eid] = _sessions(eid, past, "18:00", "23:30", None)
    m = P.build_model(hist, info, rooms, now - dt.timedelta(days=14), now)
    sun = (_at(day, "07:30"), _at(day, "17:00"))
    seen_rooms = set()
    for seed in range(40):
        events, summary = P.plan_day(m, day, sun, now, random.Random(seed), cap=3)
        lit: set[str] = set()
        peak = 0
        for e in events:
            (lit.add if e["do"] == "on" else lit.discard)(e["entity_id"])
            peak = max(peak, len(lit))
        assert peak <= 3, seed  # never every room
        seen_rooms |= {r["name"] for r in summary["rooms"]}
    assert len(seen_rooms) == 6  # all rooms take turns over the nights


async def test_randomness_bounds_and_not_a_copy(hass: HomeAssistant, tz) -> None:
    now = _now()
    m = P.build_model(_family(now), INFO, ROOMS, now - dt.timedelta(days=14), now)
    day = dt_util.as_local(now).date()  # Wednesday: the living room's weekday evenings
    sun = (_at(day, "07:30"), _at(day, "17:00"))
    starts, plans = [], set()
    for seed in range(200):
        events, _ = P.plan_day(m, day, sun, now, random.Random(seed))
        on = [e["at"] for e in events if e["entity_id"] == LIVING and e["do"] == "on"]
        assert len(on) == 1
        starts.append(on[0])
        assert all(e["value"] == 50 for e in events if e["entity_id"] == LIVING and e["do"] == "on")
        plans.add(tuple(e["at"] for e in events))
        off = [e["at"] for e in events if e["entity_id"] == LIVING and e["do"] == "off"][0]
        assert dt.timedelta(minutes=15) <= off - on[0] <= dt.timedelta(minutes=210 + 15 + 2)
    lo, hi = _at(day, "19:00") - dt.timedelta(minutes=15), _at(day, "19:00") + dt.timedelta(minutes=15, seconds=91)
    assert all(lo <= s <= hi for s in starts)
    assert max(starts) - min(starts) > dt.timedelta(minutes=15)  # really spread, not a copy
    assert len(plans) == 200


async def test_blinds_usual_times_or_sun(hass: HomeAssistant, tz) -> None:
    now = _now()
    m = P.build_model(_family(now), INFO, ROOMS, now - dt.timedelta(days=14), now)
    day = dt_util.as_local(now).date()
    sun = (_at(day, "07:30"), _at(day, "17:00"))
    for seed in range(50):
        events, summary = P.plan_day(m, day, sun, now, random.Random(seed))
        close = [e for e in events if e["entity_id"] == BLIND and e["do"] == "position"]
        assert len(close) == 1 and close[0]["value"] == 20
        assert _at(day, "20:45") <= close[0]["at"] <= _at(day, "21:16")
        opens = [e for e in events if e["entity_id"] == BLIND and e["do"] == "open"]  # no morning habit → by the sun
        assert len(opens) == 1 and _at(day, "07:50") <= opens[0]["at"] <= _at(day, "08:11")
        assert summary["covers"]["by"] == "mixed"


async def test_too_little_history_and_by_the_sun(hass: HomeAssistant, tz) -> None:
    now = _now()
    m = P.build_model(_family(now, evenings=2), INFO, ROOMS, now - dt.timedelta(days=14), now)
    assert m["evenings"] == 2
    day = dt_util.as_local(now).date()
    sun = (_at(day, "07:30"), _at(day, "17:00"))
    events, summary = P.plan_day(m, day, sun, now, random.Random(1))
    assert summary["lights"] == "few" and summary["rooms"] == []
    assert not [e for e in events if e["entity_id"].startswith("light.")]  # nothing invented
    assert {e["do"] for e in events} == {"open", "close"}  # blinds by the sun (a safe default)
    # «По солнцу»: a calm evening in at most 2 rooms — the ones used most — dimmed.
    for seed in range(30):
        events, summary = P.plan_day(m, day, sun, now, random.Random(seed), mode="sun")
        rooms = [r["name"] for r in summary["rooms"]]
        assert 1 <= len(rooms) <= 2 and summary["lights"] == "sun"
        assert all(e["at"] >= _at(day, "16:40") for e in events if e["do"] == "on")
        assert all(e["value"] in (None, P.SUN_BRIGHTNESS) for e in events if e["do"] == "on")
    # No place of the house: no lights at all (they follow the dark).
    assert P.plan_day(m, day, None, now, random.Random(1), mode="sun")[1]["lights"] == "no_location"
    # No history at all and «по солнцу»: a living room / kitchen by name.
    empty = P.build_model({}, INFO, ROOMS, now - dt.timedelta(days=14), now)
    assert set(P.sun_rooms(empty)[:2]) == {"kitchen", "living"}


# --- the engine in HA ---------------------------------------------------------------------------------------------------
@pytest.fixture
async def entry(hass: HomeAssistant, tmp_path: Path, tz):
    hass.config.config_dir = str(tmp_path)

    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    reg = er.async_get(hass)
    areas = ar.async_get(hass)
    living, kitchen = areas.async_create("Гостиная"), areas.async_create("Кухня")
    for eid, uid, platform, extra in (
        (LIVING, "u-living", "knx", {"supported_color_modes": ["brightness"]}),
        (KITCHEN, "u-kitchen", "knx", {"supported_color_modes": ["onoff"]}),
        (BLIND, "u-blind", "knx", {"supported_features": 15, "current_position": 100}),
        (GARAGE, "u-garage", "knx", {"device_class": "garage", "supported_features": 3}),
        (HAND, "u-hand", "knx", {"supported_color_modes": ["onoff"]}),
        (GROUP, "u-group", "group", {"supported_color_modes": ["onoff"], "entity_id": [LIVING]}),
        (HIDDEN, "u-hidden", "knx", {"supported_color_modes": ["onoff"]}),
    ):
        domain, obj = eid.split(".")
        reg.async_get_or_create(domain, platform, uid, suggested_object_id=obj)
        hass.states.async_set(eid, "off" if domain == "light" else "open", {"friendly_name": obj.replace("_", " "), **extra})
    reg.async_update_entity(LIVING, area_id=living.id)
    reg.async_update_entity(BLIND, area_id=living.id)
    reg.async_update_entity(KITCHEN, area_id=kitchen.id)
    reg.async_update_entity(HIDDEN, hidden_by=er.RegistryEntryHider.INTEGRATION)
    entry.runtime_data.owned |= {"u-living", "u-kitchen", "u-blind", "u-garage", "u-hidden"}  # «u-hand»: the integrator's
    yield entry
    if entry.runtime_data.presence:
        entry.runtime_data.presence.async_stop()


@pytest.fixture
def bus(hass: HomeAssistant):
    made: list[tuple[str, object, dict]] = []

    def record(domain: str, service: str):
        async def handler(call: ServiceCall) -> None:
            made.append((f"{domain}.{service}", call.data.get("entity_id"), {k: v for k, v in call.data.items() if k != "entity_id"}))

        hass.services.async_register(domain, service, handler)

    for d, s in (("cover", "open_cover"), ("cover", "close_cover"), ("cover", "set_cover_position"), ("light", "turn_on"), ("light", "turn_off"), ("lock", "lock")):
        record(d, s)
    return made


def _hist(engine):
    async def fake(entity_ids, start, end):
        fam = _family(dt_util.now())
        return {k: v for k, v in fam.items() if k in entity_ids}

    return patch.object(engine, "_history", side_effect=fake)


async def test_only_own_safe_lights_and_covers(hass: HomeAssistant, entry) -> None:
    engine = entry.runtime_data.presence
    assert set(engine.candidates()) == {LIVING, KITCHEN, BLIND}  # no garage, integrator's, group, hidden


async def test_view_switch_plan_and_refusals(hass: HomeAssistant, hass_client, hass_read_only_user, entry, freezer) -> None:
    freezer.move_to(_now("2026-10-28", "12:00"))
    engine = entry.runtime_data.presence
    refresh = await hass.auth.async_create_refresh_token(hass_read_only_user, CLIENT_ID)
    client = await hass_client(hass.auth.async_create_access_token(refresh))  # the client isn't an admin
    got = await (await client.get(URL)).json()
    assert got["enabled"] is False and got["mode"] == "learned" and got["devices"] == {"lights": 2, "covers": 1}
    with _hist(engine):
        res = await client.post(URL, json={"op": "set", "enabled": True, "until": "2026-11-05"})
        assert res.status == 200, await res.text()
        got = await res.json()
    assert got["enabled"] and got["status"] == "on" and got["until"] == "2026-11-05" and got["left"] > 0
    t = got["tonight"]
    assert [r["name"] for r in t["rooms"]] and all(r["name"] in ("Гостиная", "Кухня") for r in t["rooms"])
    assert t["covers"]["close"] and t["lights"] == "learned"
    assert all(e["entity_id"] in (LIVING, KITCHEN, BLIND) for _, e in engine.pending.values())
    assert got["log"][0]["sys"] == "on"
    for body in (
        {"op": "set", "until": "2020-01-01"},
        {"op": "set", "until": "05.11.2026"},
        {"op": "set", "mode": "replay"},
        {"op": "set", "enabled": "yes"},
        {"op": "set", "lights": False, "covers": False},
        {"op": "set", "entity_id": GARAGE},
        {"op": "run"},
    ):
        assert (await client.post(URL, json=body)).status == 400, body
    res = await client.post(URL, json={"op": "set", "enabled": False})
    assert (await res.json())["status"] == "off" and not engine.pending


async def test_manual_touch_is_left_alone(hass: HomeAssistant, entry, bus, freezer) -> None:
    freezer.move_to(_now("2026-10-28", "19:00"))
    engine = entry.runtime_data.presence
    with _hist(engine):
        await engine.set({"enabled": True}, "test")
    hass.states.async_set(LIVING, "on", {"supported_color_modes": ["brightness"], "brightness": 255})  # someone is home
    await engine.fire({"entity_id": LIVING, "do": "off", "value": None})
    assert not bus and engine.data["log"][0]["skip"] == "manual"
    # Its own command's echo is no person.
    freezer.tick(dt.timedelta(minutes=11))
    await engine.fire({"entity_id": LIVING, "do": "off", "value": None})
    assert bus[-1][0] == "light.turn_off"
    hass.states.async_set(KITCHEN, "off", {"supported_color_modes": ["onoff"]}, context=Context(id=engine._ctx[-1]))
    await engine.fire({"entity_id": KITCHEN, "do": "on", "value": None})
    assert bus[-1][:2] == ("light.turn_on", KITCHEN) and KITCHEN in engine.data["lit"]
    # Never the garage door or the integrator's light, whatever the plan says.
    n = len(bus)
    await engine.fire({"entity_id": GARAGE, "do": "open", "value": None})
    await engine.fire({"entity_id": HAND, "do": "on", "value": None})
    assert len(bus) == n and engine.data["log"][0]["skip"] == "gone"


async def test_auto_off_after_until(hass: HomeAssistant, entry, freezer) -> None:
    freezer.move_to(_now("2026-10-28", "20:00"))
    engine = entry.runtime_data.presence
    with _hist(engine):
        await engine.set({"enabled": True, "until": "2026-10-28"}, "test")
        assert engine.data["enabled"]
        freezer.move_to(_now("2026-10-29", "00:00") + dt.timedelta(seconds=1))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
    assert not engine.data["enabled"] and engine.data["log"][0]["sys"] == "auto_off" and not engine.pending
    assert engine.answer()["status"] == "off"


async def test_survives_restart(hass: HomeAssistant, entry, freezer) -> None:
    freezer.move_to(_now("2026-10-28", "12:00"))
    engine = entry.runtime_data.presence
    with _hist(engine):
        await engine.set({"enabled": True, "until": "2026-11-02", "mode": "sun", "covers": False}, "Мама")
    engine.data["lit"] = [KITCHEN]
    hass.states.async_set(KITCHEN, "on", {"supported_color_modes": ["onoff"]})
    await engine.store.async_save(engine.data)
    engine.async_stop()
    # HA restarts: a new engine reads the store and plans again.
    again = P.Presence(hass, engine.owns)
    with _hist(again):
        await again.async_start()
        await hass.async_block_till_done()
    assert again.data["enabled"] and again.data["until"] == "2026-11-02" and again.data["mode"] == "sun" and again.data["covers"] is False
    assert again.data["by"] == "Мама" and again.pending
    assert not any(e["entity_id"] == BLIND for _, e in again.pending.values())
    # What it left on gets an «off» soon.
    offs = [e for _, e in again.pending.values() if e["entity_id"] == KITCHEN and e["do"] == "off"]
    assert offs and offs[0]["at"] <= dt_util.now() + dt.timedelta(minutes=10)
    again.async_stop()
    # Away longer than said while HA was off: it starts switched off.
    freezer.move_to(_now("2026-11-04", "09:00"))
    late = P.Presence(hass, engine.owns)
    await late.async_start()
    assert not late.data["enabled"] and late.data["log"][0]["sys"] == "auto_off"


async def test_tunnel_cannot_reach(hass: HomeAssistant) -> None:
    for method in ("GET", "POST"):
        assert not _http_allowed(hass, method, URL, {"op": "set", "enabled": True})
