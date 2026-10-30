"""Value rules over time windows (rules.py + window.py, owner 30.10: «5 дней среднесуточная ниже +8 °C → зима, в обе
стороны»), on a real HA with its real recorder: long-term statistics imported into the recorder and read back by
`statistics_during_period`; on-time and weather means from recorded states. Adversarial: a missing day never triggers,
a sensor without statistics can't be chosen, a period at the threshold breaks the run, the auto action runs at most once
a period, «Не сейчас» never changes the entity, the season's old settings become one rule once and keep behaving."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_component
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.hauflo import rules as rules_mod, season, window
from custom_components.hauflo.canon import CANON
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

OUT = "sensor.outdoor_temperature"
HUM = "sensor.bath_humidity"
METER = "sensor.house_energy"
PUMP = "switch.pump"
HALL = "light.hall"
WEATHER = "weather.home"
URL = "/api/hauflo/rules"
MEASURE = {"device_class": "temperature", "state_class": "measurement", "unit_of_measurement": "°C", "friendly_name": "Улица"}


@pytest.fixture(autouse=True)
def _custom_integrations(recorder_mock, enable_custom_integrations, hass):
    """conftest's, with HA's real recorder set up before hass (its fixtures' order)."""
    mock_component(hass, "frontend")
    yield


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create("light", "knx", "u-hall", suggested_object_id="hall")
    hass.states.async_set(HALL, "off")
    hass.config.language = "ru"
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.owned |= {"u-hall"}
    return entry


def _day(back: int):
    """The start of the local day `back` days ago (UTC)."""
    return dt_util.as_utc(dt_util.start_of_local_day((dt_util.start_of_local_day() - timedelta(days=back)).date()))


async def _import_days(hass: HomeAssistant, eid: str, unit: str, daily: list[float | None]) -> None:
    """Hourly long-term statistics for the last len(daily) complete local days, oldest first (None: no rows that day)."""
    rows = []
    for i, v in enumerate(daily):
        back = len(daily) - i
        t, end = _day(back), _day(back - 1)
        while v is not None and t < end:
            rows.append(StatisticData(start=t, mean=v, min=v - 1, max=v + 1))
            t += timedelta(hours=1)
    meta = StatisticMetaData(mean_type=StatisticMeanType.ARITHMETIC, has_sum=False, name=None, source="recorder", statistic_id=eid, unit_class=None, unit_of_measurement=unit)
    if rows:
        async_import_statistics(hass, meta, rows)
    await async_wait_recording_done(hass)


async def _import_hours(hass: HomeAssistant, eid: str, unit: str, hourly: list[float | None]) -> None:
    """Hourly means for the last len(hourly) complete hours, oldest first."""
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    rows = [StatisticData(start=now - timedelta(hours=len(hourly) - i), mean=v, min=v, max=v) for i, v in enumerate(hourly) if v is not None]
    meta = StatisticMetaData(mean_type=StatisticMeanType.ARITHMETIC, has_sum=False, name=None, source="recorder", statistic_id=eid, unit_class=None, unit_of_measurement=unit)
    async_import_statistics(hass, meta, rows)
    await async_wait_recording_done(hass)


def _rules(entry) -> rules_mod.Rules:
    return entry.runtime_data.rules


def _sea(entry) -> season.Season:
    return entry.runtime_data.season


async def _season_rule(hass: HomeAssistant, entry, **suggest) -> dict:
    sea = _sea(entry)
    sea.config["suggest"] = {**sea.config["suggest"], "sensor": OUT, **suggest}
    await sea.async_sync_rule()
    assert sea.migrated and sea.pending is None
    return _rules(entry).rules[season.SEASON_RULE]


# --- the season: the same table its own suggestion had, now a value rule on the recorder's statistics -----------------

TABLE = [
    ([7.9, 5, 3, 2, 1], "cool", "heat"),  # five days in a row below 8 while cooling
    ([8.0, 5, 3, 2, 1], "cool", None),  # a day AT the threshold breaks the run (strictly below)
    ([9, 7, 7, 7, 7, 7], "cool", "heat"),  # only the last five count
    ([7, 7, 7, 7, 9], "cool", None),  # yesterday warm
    ([7, 7, None, 7, 7], "cool", "no_data"),  # a day without data — never a guess
    ([7, 7, 7, 7], "cool", "no_data"),  # too little history
    ([], "cool", "no_data"),
    ([1, 1, 1, 1, 1], "heat", None),  # already heating
    ([8.1, 12, 15, 9, 10], "heat", "cool"),  # warm while heating
    ([8.0, 12, 15, 9, 10], "heat", None),
    ([1, 1, 1, 1, 1], None, None),  # no season known: nothing suggested
]


@pytest.mark.parametrize(("means", "now", "want"), TABLE)
async def test_season_rule_keeps_the_suggestion_table(recorder_mock, hass: HomeAssistant, means, now, want) -> None:
    entry = await _setup(hass)
    hass.states.async_set(OUT, "3", MEASURE)
    await _import_days(hass, OUT, "°C", means)
    sea = _sea(entry)
    sea.season = now
    rule = await _season_rule(hass, entry)
    rules = _rules(entry)
    st = rules.st(rule["id"])
    if want == "no_data":
        assert sea.suggestion() is None and st["no_data"] and rules.status(rule["id"]) == "no_data"
        assert sea.setup_answer()["suggest"]["status"] == "no_data"
    elif want is None:
        assert sea.suggestion() is None
    else:
        sug = sea.suggestion()
        assert sug is not None and sug["to"] == want and sug["days"] == 5 and sug["threshold"] == 8.0
        assert sea.setup_answer()["suggest"]["status"] == "suggest"
    # The recorder's daily means, oldest first, as the period values of the rule.
    vals = rules.win[rule["id"]]["values"]
    assert vals[-len(means) :] == [None if m is None else pytest.approx(m) for m in means][-len(vals) :] if means else all(v is None for v in vals)


async def test_season_push_buttons_snooze_and_do_it(recorder_mock, hass: HomeAssistant, hass_admin_user, hass_read_only_user) -> None:
    entry = await _setup(hass)
    hass.states.async_set(OUT, "3", MEASURE)
    await _import_days(hass, OUT, "°C", [5, 4, 3, 2, 1])
    rules, sea = _rules(entry), _sea(entry)
    rules.ask = ask = AsyncMock()
    applied = AsyncMock(return_value={})
    sea.season = "cool"
    with patch.object(sea, "apply", applied):
        rule = await _season_rule(hass, entry)
        # Pushed once: the season's own wording, «Сделать / Не сейчас», its old tag.
        assert ask.call_count == 1
        title, message, buttons, tag = ask.call_args[0]
        assert "зиму" in title and "8°" in message and "5" in message and tag == season.NOTIFY_TAG
        assert [b["action"] for b in buttons] == ["HAUFLO_RULE|season|on", "HAUFLO_RULE|season|later"] and buttons[0]["title"] == "Сделать"
        # The same state again (a re-read, the next tick, a restart) never resends.
        await rules.window_refresh(rule["id"], "period")
        await rules._tick()
        assert ask.call_count == 1
        ent = rules.binaries[rule["id"]].entity_id
        assert ent.startswith("binary_sensor.hauflo_value_rule_") and hass.states.get(ent).state == "on"
        attrs = hass.states.get(ent).attributes
        assert attrs["statistic"] == pytest.approx(1) and attrs["periods_in_row"] == 5 and attrs["periods_needed"] == 5 and attrs["since"] and attrs["suggestion"] == "on"
        # A restricted (child) profile's phone: refused.
        with patch.object(rules_mod, "may_edit", AsyncMock(return_value=False)):
            hass.bus.async_fire("mobile_app_notification_action", {"action": "HAUFLO_RULE|season|on"}, context=Context(user_id=hass_read_only_user.id))
            await hass.async_block_till_done()
        assert applied.call_count == 0
        # «Не сейчас»: snoozed for snooze_days, the entity untouched; an old «Сделать» after it runs nothing.
        hass.bus.async_fire("mobile_app_notification_action", {"action": "HAUFLO_RULE|season|later"}, context=Context(user_id=hass_admin_user.id))
        await hass.async_block_till_done()
        assert sea.suggestion() is None and hass.states.get(ent).state == "on"
        assert rules.st(rule["id"])["sug"]["snooze_until"] > dt_util.utcnow().timestamp() + 3 * 86400 - 60
        hass.bus.async_fire("mobile_app_notification_action", {"action": "HAUFLO_RULE|season|on"}, context=Context(user_id=hass_admin_user.id))
        await hass.async_block_till_done()
        assert applied.call_count == 0
        # After the snooze: sent again (once).
        rules.st(rule["id"])["sug"]["snooze_until"] = 1.0
        rules._sug_sent.clear()
        await rules._tick()
        assert ask.call_count == 2
        # «Сделать»: the season switches (season.py), the suggestion is over.
        hass.bus.async_fire("mobile_app_notification_action", {"action": "HAUFLO_RULE|season|on"}, context=Context(user_id=hass_admin_user.id))
        await hass.async_block_till_done()
        assert applied.call_args[0][0] == "heat" and rules.st(rule["id"])["sug"] is None


async def test_season_old_buttons_and_dashboard_snooze(recorder_mock, hass: HomeAssistant, hass_client, hass_admin_user) -> None:
    """A push sent before the suggestion was a rule («HAUFLO_SEASON|…») and the dashboard's «Не сейчас» still work."""
    entry = await _setup(hass)
    hass.states.async_set(OUT, "3", MEASURE)
    await _import_days(hass, OUT, "°C", [5, 4, 3, 2, 1])
    rules, sea = _rules(entry), _sea(entry)
    rules.ask = AsyncMock()
    sea.season = "cool"
    await _season_rule(hass, entry)
    client = await hass_client()
    got = await (await client.post("/api/hauflo/season", json={"snooze": True})).json()
    assert got["suggestion"] is None and sea.setup_answer()["suggest"]["snoozed_until"]
    rules.st(season.SEASON_RULE)["sug"]["snooze_until"] = 0.0
    with patch.object(sea, "apply", AsyncMock(return_value={})) as applied:
        hass.bus.async_fire("mobile_app_notification_action", {"action": "HAUFLO_SEASON|heat"}, context=Context(user_id=hass_admin_user.id))
        await hass.async_block_till_done()
    assert applied.call_args[0][0] == "heat"


async def test_migration_once_keeps_the_settings_and_the_integrators_edits(recorder_mock, hass: HomeAssistant, hass_client, hass_storage) -> None:
    # A house from before: the season store holds the integrator's suggestion settings (sent by an earlier deploy).
    old = {"config": {"exclude": [], "labels": "mode", "resend_s": 3600, "suggest": {"enabled": True, "sensor": OUT, "below": 6, "above": 9, "days": 4, "snooze_days": 2}}, "season": "cool", "notified": None, "snooze_until": 0}
    hass_storage[season.STORE_KEY] = {"version": 1, "minor_version": 1, "key": season.STORE_KEY, "data": old}
    hass.states.async_set(OUT, "3", MEASURE)
    entry = await _setup(hass)
    sea, rules = _sea(entry), _rules(entry)
    assert not sea.migrated
    await sea.async_sync_rule()
    rule = rules.rules[season.SEASON_RULE]
    assert rule["input"]["entity_id"] == OUT and rule["x"] == 6 and rule["y"] == 3 and rule["window"] == {"stat": "mean", "period": "day", "n": 4, "n_back": 4, "source": "statistics"}
    assert rule["snooze_days"] == 2 and rules_mod.outputs(rule) == {"suggest": True, "suggest_back": True, "auto": False} and rule["enabled"]
    ent = rules.binaries[rule["id"]].entity_id
    # Once: a second start finds it made and leaves it.
    await sea.async_sync_rule()
    assert len([r for r in rules.rules if r == season.SEASON_RULE]) == 1 and rules.binaries[rule["id"]].entity_id == ent
    # The integrator edits it in Hauflo's wizard; an older Hauflo deploys the same old settings again → kept.
    await rules.tune(rule["id"], {"x": 7}, client=False)
    client = await hass_client()
    await client.post("/api/hauflo/season/config", json=old["config"])
    assert rules.rules[season.SEASON_RULE]["x"] == 7
    # Other settings sent → they change what they say (the entity id stays).
    await client.post("/api/hauflo/season/config", json={**old["config"], "suggest": {**old["config"]["suggest"], "days": 6}})
    assert rules.rules[season.SEASON_RULE]["window"]["n"] == 6 and rules.rules[season.SEASON_RULE]["x"] == 6 and rules.binaries[rule["id"]].entity_id == ent
    # This Hauflo's deploy sends no «suggest» (the rule is edited in the wizard): the rule and the old settings stay.
    await rules.tune(rule["id"], {"x": 5}, client=False)
    await client.post("/api/hauflo/season/config", json={k: v for k, v in old["config"].items() if k != "suggest"})
    assert rules.rules[season.SEASON_RULE]["x"] == 5 and sea.config["suggest"]["days"] == 6
    # The settings view says it.
    got = await (await client.get("/api/hauflo/season/config")).json()
    assert got["suggest"]["rule"]["id"] == season.SEASON_RULE and got["suggest"]["sensor"] == OUT


async def test_migration_waits_for_the_street_sensor(recorder_mock, hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    sea = _sea(entry)
    await sea.async_sync_rule()
    assert not sea.migrated and sea.pending == "no_outdoor" and sea.setup_answer()["suggest"]["status"] == "no_sensor"
    # The KNX weather station comes up: made on the next tick, following «улица».
    er.async_get(hass).async_get_or_create("sensor", "knx", "u-out", suggested_object_id="outdoor_temperature")
    hass.states.async_set(OUT, "3", MEASURE)
    await sea._tick(None)
    rule = _rules(entry).rules[season.SEASON_RULE]
    assert sea.migrated and rule["input"]["entity_id"] == OUT and rule["input"]["outdoor"] is True


# --- any window rule: the entity, the auto action, no data, what can't be chosen -----------------------------------


def _hum_rule(**kw) -> dict:
    return {
        "id": "bath",
        "name": "Сырость",
        "purpose": "notify",
        "input": {"entity_id": HUM},
        "when": "above",
        "x": 70,
        "y": 5,
        "window": {"stat": "mean", "period": "hour", "n": 3},
        "outputs": {"suggest": False, "auto": True},
        "actions": [{"do": "notify", "text": "Сыро в санузле"}],
        "back": "nothing",
        **kw,
    }


async def test_hourly_mean_auto_once_a_period_and_no_data(recorder_mock, hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    hass.states.async_set(HUM, "75", {"device_class": "humidity", "state_class": "measurement", "unit_of_measurement": "%"})
    await _import_hours(hass, HUM, "%", [60, 72, 80, 75])
    rules = _rules(entry)
    told = AsyncMock()
    rules.tell = told
    out = await rules.save(_hum_rule(), client=False, user_id=None)
    assert out["state"] == "active" and out["values"][-3:] == [72, 80, 75] and out["periods"] == 3
    assert told.call_count == 1 and told.call_args[0][1] == "Сыро в санузле"
    ent = hass.states.get(rules.binaries["bath"].entity_id)
    assert ent.state == "on" and ent.attributes["statistic"] == 75 and ent.attributes["stat"] == "mean" and ent.attributes["period"] == "hour"
    assert any(e["event"] == "auto" for e in rules.log_of("bath"))
    # A re-read in the same period (a restart): the auto action never runs twice.
    rules.st("bath")["active"] = False
    await rules.window_refresh("bath", "start")
    assert told.call_count == 1 and any(e["event"] == "skip" and e.get("result") == "period" for e in rules.log_of("bath"))
    # The live value doesn't decide a window rule (the statistics do), and a missing hour never triggers.
    hass.states.async_set(HUM, "20", {"device_class": "humidity", "state_class": "measurement", "unit_of_measurement": "%"})
    await hass.async_block_till_done()
    await rules.save(_hum_rule(id="bath2", name="Сырость 2", window={"stat": "mean", "period": "hour", "n": 5}), client=False, user_id=None)
    assert rules.status("bath2") == "no_data" and not rules.st("bath2")["active"] and told.call_count == 1
    assert "нет данных" in rules.explain(rules.rules["bath2"], True) and "no data" in rules.explain(rules.rules["bath2"], False)
    # A period is read when it closes (+ the compile delay), never every minute.
    due = rules._win_timers["bath"]
    assert due is not None


async def test_daily_sum_of_a_meter_and_hours_on(recorder_mock, hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass)
    rules = _rules(entry)
    # An energy meter (total_increasing): «сумма за день» = the day's change from the recorder's statistics.
    hass.states.async_set(METER, "100", {"device_class": "energy", "state_class": "total_increasing", "unit_of_measurement": "kWh"})
    rows, total = [], 0.0
    for back, per_hour in ((2, 0.5), (1, 1.5)):
        t, end = _day(back), _day(back - 1)
        while t < end:
            total += per_hour
            rows.append(StatisticData(start=t, state=total, sum=total))
            t += timedelta(hours=1)
    meta = StatisticMetaData(mean_type=StatisticMeanType.NONE, has_sum=True, name=None, source="recorder", statistic_id=METER, unit_class="energy", unit_of_measurement="kWh")
    async_import_statistics(hass, meta, rows)
    await async_wait_recording_done(hass)
    w = {"stat": "sum", "period": "day", "n": 2}
    vals, _starts, why = await window.values(hass, METER, None, w)
    hours = [(_day(b - 1) - _day(b)).total_seconds() / 3600 for b in (2, 1)]
    assert why is None and vals[-1] == pytest.approx(1.5 * hours[1])
    # A pump: «сумма» = hours on in the day, from its recorded states (the recorder's history, not ours).
    hass.states.async_set(PUMP, "off")
    await async_wait_recording_done(hass)
    start, today = _day(1), _day(0)  # before the clock moves
    freezer.move_to(start + timedelta(hours=2))
    hass.states.async_set(PUMP, "on")
    await async_wait_recording_done(hass)
    freezer.move_to(start + timedelta(hours=8))
    hass.states.async_set(PUMP, "off")
    await async_wait_recording_done(hass)
    freezer.move_to(today + timedelta(hours=3))
    vals, _starts, why = await window.values(hass, PUMP, None, {"stat": "sum", "period": "day", "n": 1})
    assert why is None and vals == [pytest.approx(6.0)]


async def test_weather_means_from_its_recorded_states(recorder_mock, hass: HomeAssistant, freezer) -> None:
    await _setup(hass)
    start, today = _day(1), _day(0)
    for h, t in ((1, 4.0), (9, 6.0), (17, 8.0)):
        freezer.move_to(start + timedelta(hours=h))
        hass.states.async_set(WEATHER, "sunny", {"temperature": t, "temperature_unit": "°C"})
        await async_wait_recording_done(hass)
    freezer.move_to(today + timedelta(hours=1))
    vals, _s, why = await window.values(hass, WEATHER, "temperature", {"stat": "mean", "period": "day", "n": 2})
    assert why is None and vals == [None, pytest.approx(6.0)]  # the day before: no samples — no data, never a guess


async def test_what_a_window_rule_refuses(recorder_mock, hass: HomeAssistant, hass_client) -> None:
    entry = await _setup(hass)
    client = await hass_client()
    hass.states.async_set("sensor.plain", "5", {"unit_of_measurement": "°C"})  # no state_class: no statistics
    hass.states.async_set(HUM, "50", {"state_class": "measurement", "unit_of_measurement": "%"})
    hass.states.async_set(METER, "1", {"state_class": "total_increasing", "unit_of_measurement": "kWh"})
    hass.states.async_set(PUMP, "off")
    hass.states.async_set(WEATHER, "sunny", {"temperature": 3, "temperature_unit": "°C"})

    async def refused(rule: dict) -> dict:
        res = await client.post(URL, json={"op": "save", "rule": rule})
        assert res.status == 400, rule
        return await res.json()

    got = await refused(_hum_rule(input={"entity_id": "sensor.plain"}, x=5, y=1))
    assert got["code"] == "no_statistics" and "state_class" in got["message"] and "state_class" in got["message_ru"] and "статистики" in got["message_ru"]
    assert (await refused(_hum_rule(window={"stat": "sum", "period": "hour", "n": 3})))["code"] == "bad_window"  # a measurement has no sum
    assert (await refused(_hum_rule(input={"entity_id": METER}, x=5, y=1)))["code"] == "bad_window"  # a counter has no mean
    assert (await refused(_hum_rule(input={"entity_id": PUMP}, x=5, y=1)))["code"] == "bad_window"  # on / off: only a sum
    assert (await refused(_hum_rule(input={"entity_id": WEATHER}, x=5, y=1, window={"stat": "sum", "period": "day", "n": 2})))["code"] == "bad_window"
    lo, hi = CANON["window"]["n"]["hour"]
    assert (await refused(_hum_rule(window={"stat": "mean", "period": "hour", "n": hi + 1})))["code"] == "bad_window"
    assert (await refused(_hum_rule(window={"stat": "mean", "period": "fortnight", "n": 2})))["code"] == "bad_window"
    assert (await refused(_hum_rule(x=120)))["code"] == "bad_number"  # 120 % — not a humidity
    assert (await refused(_hum_rule(when="on")))["code"] == "bad_when"
    # On / off from the recorder's history: the whole window inside its keep.
    assert (await refused(_hum_rule(input={"entity_id": PUMP}, x=5, y=1, window={"stat": "sum", "period": "week", "n": 2})))["code"] == "bad_window"
    assert (await refused(_hum_rule(outputs={"auto": "yes"})))["code"] == "bad_outputs"
    assert (await refused(_hum_rule(snooze_days=99, outputs={"suggest": True})))["code"] == "bad_snooze"
    # A pump's daily hours on and a weather's daily mean are fine.
    ok = await client.post(URL, json={"op": "save", "rule": _hum_rule(id="p", name="Насос", input={"entity_id": PUMP}, x=16, y=0, window={"stat": "sum", "period": "day", "n": 1})})
    assert ok.status == 200 and (await ok.json())["unit"] == "h"
    # The dry run shows the last periods.
    pv = await (await client.post(URL, json={"op": "preview", "rule": _hum_rule(id="q", name="Q")})).json()
    assert pv["ok"] and pv["now"] == "window_no_data" and len(pv["values"]) == 3 and pv["why"] and pv["why_en"]
    assert set(_rules(entry).rules) == {"p"}


async def test_suggestion_runs_the_actions_and_never_touches_the_entity(recorder_mock, hass: HomeAssistant, hass_admin_user) -> None:
    entry = await _setup(hass)
    made: list[str] = []

    async def turn_on(call: ServiceCall) -> None:
        made.append(call.service)
        hass.states.async_set(HALL, "on")

    hass.services.async_register("light", "turn_on", turn_on)
    hass.services.async_register("light", "turn_off", turn_on)
    hass.states.async_set(HUM, "75", {"state_class": "measurement", "unit_of_measurement": "%"})
    await _import_hours(hass, HUM, "%", [80, 80, 80])
    rules = _rules(entry)
    rules.ask = ask = AsyncMock()
    await rules.save(_hum_rule(outputs={"suggest": True, "auto": False}, actions=[{"do": "on", "entity_id": HALL}], back="reverse"), client=False, user_id=None)
    assert made == [] and ask.call_count == 1  # suggested, not done
    title, message, buttons, tag = ask.call_args[0]
    assert title == "Сырость" and "Сделать?" in message and tag == "hauflo_rule_suggest_bath"
    # The daily cap: another state change the same day waits.
    assert rules.st("bath")["sug"]["sent"]
    hass.bus.async_fire("mobile_app_notification_action", {"action": "HAUFLO_RULE|bath|on"}, context=Context(user_id=hass_admin_user.id))
    await hass.async_block_till_done()
    assert made == ["turn_on"] and hass.states.get(HALL).state == "on" and rules.st("bath")["sug"] is None
    assert any(e["event"] == "suggest_done" for e in rules.log_of("bath"))
    # Done already → nothing more to suggest, and a garbage button is ignored.
    hass.bus.async_fire("mobile_app_notification_action", {"action": "HAUFLO_RULE|bath"}, context=Context(user_id=hass_admin_user.id))
    hass.bus.async_fire("mobile_app_notification_action", {"action": "HAUFLO_RULE|nope|on"}, context=Context(user_id=hass_admin_user.id))
    await hass.async_block_till_done()
    assert made == ["turn_on"]


def test_periods_are_local_and_dst_safe() -> None:
    for period in ("hour", "day", "week"):
        now = dt_util.now()
        s = window.starts(3, period, now)
        assert len(s) == 3 and s == sorted(s) and s[-1] < window.period_start(now, period)
        nxt = window.next_start(now, period)
        assert nxt > now and window.period_start(nxt, period) == nxt
    assert window.period_start(dt_util.now(), "week").weekday() == 0
