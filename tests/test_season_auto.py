"""«Зима / Лето» — «автоматически раз в день» and the season's entities (season.py, spec 3e, owner 04.11), on a real HA
with its real recorder. Adversarial: no data never switches, a day AT the threshold never flips the house back and forth,
one automatic switch a day at most (a restart, a re-read, the rule's own action never repeat it), the morning waits, the
integrator editing the rule never undoes the season, the HA switch refuses a family member who isn't an admin."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_component

from custom_components.hauflo import rules as rules_mod, season
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN
from tests.test_rules_window import MEASURE, OUT, _import_days

AC = "climate.living_ac"
REC, CUR, AUTO = "sensor.hauflo_season_recommended", "sensor.hauflo_season_current", "switch.hauflo_season_auto"


@pytest.fixture(autouse=True)
def _custom_integrations(recorder_mock, enable_custom_integrations, hass):
    mock_component(hass, "frontend")
    yield


async def _idle(self):
    return None


async def _setup(hass: HomeAssistant, freezer, hour: int, now: str = "cool") -> tuple[MockConfigEntry, list]:
    freezer.move_to(dt_util.start_of_local_day() + timedelta(hours=hour))
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    hass.config.language = "ru"
    made: list = []

    async def hvac(call: ServiceCall) -> None:
        made.append(call.data["hvac_mode"])
        hass.states.async_set(AC, call.data["hvac_mode"], hass.states.get(AC).attributes)

    hass.services.async_register("climate", "set_hvac_mode", hvac)
    hass.states.async_set(AC, now, {"hvac_modes": ["heat", "cool", "off"], "friendly_name": "AC"})
    hass.states.async_set(OUT, "3", MEASURE)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.season.owns = lambda eid: True
    return entry, made


async def _auto(hass: HomeAssistant, entry, means: list) -> season.Season:
    """The integrator chose «автоматически раз в день» in Hauflo (the deploy's config), then the street's days came in."""
    sea = entry.runtime_data.season
    await _import_days(hass, OUT, "°C", means)
    await sea.async_set_config({**sea.config, "suggest": {**sea.config["suggest"], "sensor": OUT}, "auto": True})
    await hass.async_block_till_done()
    return sea


async def test_cold_days_switch_to_winter_once_and_the_entities_say_it(recorder_mock, hass: HomeAssistant, freezer) -> None:
    entry, made = await _setup(hass, freezer, 10)
    sea = await _auto(hass, entry, [7.9, 5, 3, 2, 1])
    rule = entry.runtime_data.rules.rules[season.SEASON_RULE]
    # The integrator's way is the rule's outputs: it acts, it never asks.
    assert rules_mod.outputs(rule) == {"suggest": False, "suggest_back": False, "auto": True} and sea.auto_on()
    assert made == ["heat"] and sea.season == "heat" and sea.by == season.AUTO_BY and sea.auto_day == dt_util.now().date().isoformat()
    assert sea.recommendation() == ("heat", "cold") and sea.suggestion() is None
    # The entities: fixed English ids, push-updated.
    assert hass.states.get(REC).state == "heat" and hass.states.get(CUR).state == "heat" and hass.states.get(AUTO).state == "on"
    attrs = hass.states.get(REC).attributes
    assert attrs["reason"] == "cold" and attrs["below"] == 8 and attrs["days"] == 5 and len(attrs["daily_means"]) == 5 and attrs["sensor"] == OUT
    # The daily look, the rule's re-read, the next tick: nothing more today.
    await sea._tick(None)
    await entry.runtime_data.rules.window_refresh(season.SEASON_RULE, "period")
    assert made == ["heat"]
    # Hauflo's setup view says it.
    got = sea.setup_answer()
    assert got["auto"] is True and got["suggest"]["recommended"] == "heat" and got["auto_switched"] == sea.auto_day


@pytest.mark.parametrize(
    ("means", "now", "want", "reason"),
    [
        ([7, 7, None, 7, 7], "cool", "no_data", "no_data"),  # a day missing — never a guess, the house keeps its season
        ([7, 7, 7, 7], "cool", "no_data", "no_data"),  # too little history
        ([8.0, 12, 15, 9, 10], "heat", "none", "between"),  # a day AT the threshold: neither way (no flip back and forth)
        ([7, 7, 7, 7, 9], "cool", "none", "between"),  # yesterday warm: no winter yet
    ],
)
async def test_no_data_and_the_border_never_switch(recorder_mock, hass: HomeAssistant, freezer, means, now, want, reason) -> None:
    entry, made = await _setup(hass, freezer, 10, now)
    sea = await _auto(hass, entry, means)
    sea.auto_checked = None
    await sea._tick(None)
    assert made == [] and sea.current() == now
    assert sea.recommendation() == (want, reason) and hass.states.get(REC).state == want
    assert await sea.auto_check("test") == want


async def test_warm_days_back_to_summer_and_the_hysteresis_holds(recorder_mock, hass: HomeAssistant, freezer) -> None:
    entry, made = await _setup(hass, freezer, 10, "heat")
    sea = await _auto(hass, entry, [8.1, 12, 15, 9, 10])
    assert made == ["cool"] and sea.recommendation() == ("cool", "warm")


async def test_the_morning_waits_and_one_switch_a_day_survives_a_restart(recorder_mock, hass: HomeAssistant, freezer) -> None:
    entry, made = await _setup(hass, freezer, season.AUTO_HOUR - 2)
    sea = await _auto(hass, entry, [5, 4, 3, 2, 1])
    # Night: the rule went ACTIVE, the house waits for the morning (the family reads «Переключено на …» awake).
    assert made == [] and await sea.auto_check("test") == "later"
    await sea._tick(None)
    assert made == [] and sea.auto_checked is None
    freezer.tick(timedelta(hours=3))
    await sea._tick(None)
    assert made == ["heat"] and sea.auto_checked == dt_util.now().date().isoformat()
    # The family switches back by hand: held until tomorrow's look.
    await sea.apply("cool", "family")
    assert made == ["heat", "cool"]
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    sea = entry.runtime_data.season
    sea.owns = lambda eid: True
    assert sea.auto_day == dt_util.now().date().isoformat() and sea.auto_on()
    await sea._tick(None)
    assert await sea.auto_check("window") == "today" and made == ["heat", "cool"]
    # Tomorrow (a new day's means in): winter again, once.
    freezer.tick(timedelta(days=1))
    await _import_days(hass, OUT, "°C", [4, 3, 2, 1, 0])
    await entry.runtime_data.rules.window_refresh(season.SEASON_RULE, "period")
    await sea._tick(None)
    await sea._tick(None)
    assert made == ["heat", "cool", "heat"]


async def test_manual_mode_asks_and_editing_the_rule_never_undoes_the_season(recorder_mock, hass: HomeAssistant, freezer) -> None:
    entry, made = await _setup(hass, freezer, 10)
    sea = await _auto(hass, entry, [5, 4, 3, 2, 1])
    rules = entry.runtime_data.rules
    assert made == ["heat"]
    # The integrator moves the threshold / switches the rule off: the house stays in winter (no «reverse» on an edit).
    await rules.tune(season.SEASON_RULE, {"x": 2}, client=False)
    await rules.set_enabled(season.SEASON_RULE, False)
    await hass.async_block_till_done()
    assert made == ["heat"] and sea.current() == "heat"
    assert sea.recommendation() == ("none", "off") and await sea.auto_check("test") == "none"
    await rules.set_enabled(season.SEASON_RULE, True)
    # «Вручную»: outputs back to the suggestion, the rule keeps its state (no restart, nothing undone).
    active = rules.st(season.SEASON_RULE)["active"]
    await sea.async_set_config({**sea.config, "auto": False})
    assert rules_mod.outputs(rules.rules[season.SEASON_RULE]) == {"suggest": True, "suggest_back": True, "auto": False}
    assert rules.st(season.SEASON_RULE)["active"] == active and made == ["heat"] and not sea.auto_on()
    assert hass.states.get(AUTO).state == "off"
    # An older Hauflo's deploy (no «auto» sent): what is held stays.
    await sea.async_set_config({k: v for k, v in sea.config.items() if k != "auto"})
    assert not sea.auto_on()


async def test_the_switch_is_for_staff(recorder_mock, hass: HomeAssistant, freezer, hass_admin_user) -> None:
    entry, made = await _setup(hass, freezer, 10)
    sea = await _auto(hass, entry, [5, 4, 3, 2, 1])
    assert made == ["heat"]
    kid = await hass.auth.async_create_user("Kid", group_ids=["system-users"])
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("switch", "turn_off", {"entity_id": AUTO}, blocking=True, context=Context(user_id=kid.id))
    assert sea.auto_on()
    await hass.services.async_call("switch", "turn_off", {"entity_id": AUTO}, blocking=True, context=Context(user_id=hass_admin_user.id))
    assert not sea.auto_on() and sea.config["auto"] is False and hass.states.get(AUTO).state == "off"
    # The integrator's automation (no user): allowed; turned on, the day's look runs — already winter, nothing sent.
    await hass.services.async_call("switch", "turn_on", {"entity_id": AUTO}, blocking=True)
    assert sea.auto_on() and made == ["heat"]
    # Translated names (the house's language, the labels the integrator picked); the switch is a setting (config).
    assert hass.states.get(AUTO).attributes["friendly_name"].endswith("Переключать автоматически")
    assert hass.states.get(REC).attributes["friendly_name"].endswith("Рекомендуемый сезон")
    from homeassistant.helpers import entity_registry as er

    assert er.async_get(hass).async_get(AUTO).entity_category == "config"
