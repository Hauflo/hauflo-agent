"""Who gets a push and how many (owner 27.10): «только себе» without groups, HA's push limit said as such, the
agent's own cap so a flapping house can't use the day up, repeats slowing down, a test per phone."""

import logging
from datetime import timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.hauflo import notify

PHONES = [
    {"service": "mobile_app_ivan", "name": "iPhone Ивана", "os": "ios", "person": "person.ivan"},
    {"service": "mobile_app_anna", "name": "Pixel Анны", "os": "android", "person": "person.anna"},
    {"service": "mobile_app_tablet", "name": "Планшет в холле", "os": "android", "person": None},
]


def _engine(hass: HomeAssistant, audience: dict | None = None) -> notify.NotifyEngine:
    eng = notify.NotifyEngine(hass)
    eng.config = notify._clean_rules({"rules": [], "groups": [], **({"audience": audience} if audience is not None else {})})
    eng.phones = lambda: PHONES  # type: ignore[method-assign]
    return eng


def _rule(priority: str = "normal", **rec) -> dict:
    return {"priority": priority, "recipients": {"all": True, "people": [], "groups": [], **rec}}


async def test_only_me_without_groups(hass: HomeAssistant) -> None:
    """Owner 27.10: «не смог настроить только себе — выбирается группа». Everyone else off: the rules «to everyone»
    reach his phone only; «critical» — only the critical ones."""
    eng = _engine(hass, {"*": "off", "person.ivan": "all", "person.anna": "critical", "junk": "all", "person.x": "loud"})
    assert eng.config["audience"] == {"*": "off", "person.ivan": "all", "person.anna": "critical"}
    assert [p["service"] for p in eng._targets(_rule())] == ["mobile_app_ivan"]
    assert [p["service"] for p in eng._targets(_rule("critical"))] == ["mobile_app_ivan", "mobile_app_anna"]
    # A phone's own setting wins over its person's; a phone without a person — by its own or «*».
    eng.config = notify._clean_rules({"rules": [], "groups": [], "audience": {"person.ivan": "off", "mobile_app_ivan": "all"}})
    assert [p["service"] for p in eng._targets(_rule())] == ["mobile_app_ivan", "mobile_app_anna", "mobile_app_tablet"]
    # Picked by hand in a rule: a person or a single phone.
    assert [p["service"] for p in eng._targets({"priority": "normal", "recipients": {"all": False, "people": ["person.anna"], "groups": [], "phones": ["mobile_app_tablet"]}})] == ["mobile_app_anna", "mobile_app_tablet"]
    # Nothing set: everyone with the app, as before.
    assert len(_engine(hass)._targets(_rule())) == 3


async def test_has_push_limit_is_said_as_a_limit(hass: HomeAssistant) -> None:
    """Owner 27.10: HA's relay answers 429 — mobile_app logs «Rate limit exceeded…» as a warning and returns."""

    async def limited(call):
        logging.getLogger("homeassistant.components.mobile_app.notify").warning("Rate limit exceeded sending notification to iPhone Ивана")

    async def broken(call):
        logging.getLogger("homeassistant.components.mobile_app.notify").error("Error sending notification to Pixel Анны")

    async def fine(call):
        return None

    hass.services.async_register("notify", "mobile_app_ivan", limited)
    hass.services.async_register("notify", "mobile_app_anna", broken)
    hass.services.async_register("notify", "mobile_app_tablet", fine)
    eng = _engine(hass)
    out = await eng.async_test({"all": True, "priority": "critical"})
    assert {r["service"]: r["status"] for r in out["results"]} == {"mobile_app_ivan": "rate_limited", "mobile_app_anna": "failed", "mobile_app_tablet": "sent"}
    assert out["sent"] == ["mobile_app_tablet"]
    assert "mobile_app_ivan" in eng.limited


async def test_a_flapping_house_cant_use_the_day_up(hass: HomeAssistant) -> None:
    calls: list[str] = []

    async def fine(call):
        calls.append(call.data["message"])

    hass.services.async_register("notify", "mobile_app_tablet", fine)
    eng = _engine(hass)
    target = [PHONES[2]]
    for i in range(notify.PUSH_HOUR_MAX + 5):
        await eng._send(_rule(), "t", f"normal {i}", tag="x", critical=False, targets=target)
    assert len(calls) == notify.PUSH_HOUR_MAX
    assert eng.sent_log[0]["held"] == ["mobile_app_tablet"]
    # A critical alarm still goes.
    await eng._send(_rule("critical"), "t", "leak", tag="x", critical=True, targets=target)
    assert calls[-1] == "leak"
    # A restart doesn't give the house a fresh hour / day: the push log is kept (active_store).
    await hass.async_block_till_done()
    await eng.active_store.async_save(eng._snapshot())
    again = _engine(hass)
    await again.async_start()
    n = len(calls)
    await again._send(_rule(), "t", "after restart", tag="x", critical=False, targets=target)
    assert len(calls) == n and again.sent_log[0]["held"] == ["mobile_app_tablet"]
    again.async_stop()


async def test_repeats_slow_down_when_nobody_accepts(hass: HomeAssistant) -> None:
    eng = _engine(hass)
    rule = {"repeat_min": 5}
    last = dt_util.utcnow().isoformat()
    assert eng._left(rule, last, 3) <= timedelta(minutes=5)
    assert eng._left(rule, last, notify.REPEAT_FAST + 1) > timedelta(minutes=55)


async def test_a_test_per_phone_not_one_a_minute(hass: HomeAssistant) -> None:
    """Owner 27.10: «Проверить» then «Проверить критичное» answered a red «HA 429»."""

    async def fine(call):
        return None

    for p in PHONES:
        hass.services.async_register("notify", p["service"], fine)
    eng = _engine(hass)
    assert eng.test_wait({"people": ["person.ivan"]}) == 0
    await eng.async_test({"people": ["person.ivan"], "priority": "normal"})
    # Another phone: at once; the same phone: a few seconds.
    assert eng.test_wait({"people": ["person.anna"]}) == 0
    assert 0 < eng.test_wait({"people": ["person.ivan"]}) <= notify.TEST_GAP
    with patch.object(notify.time, "monotonic", return_value=notify.time.monotonic() + notify.TEST_GAP + 1):
        assert eng.test_wait({"people": ["person.ivan"]}) == 0


async def test_suggestions_know_foreign_sensors(hass: HomeAssistant) -> None:
    """Owner 27.10: a suggested rule rang about another integration's gas boiler — candidates say whose they are."""
    hass.states.async_set("binary_sensor.boiler_state", "off", {"device_class": "problem", "friendly_name": "Газовый котёл — состояние"})
    eng = _engine(hass)
    eng.owns = lambda eid: eid == "binary_sensor.leak"
    hass.states.async_set("binary_sensor.leak", "off", {"device_class": "moisture", "friendly_name": "Протечка"})
    by = {c["entity_id"]: c for c in eng.status()["candidates"]}
    assert by["binary_sensor.leak"]["own"] is True
    assert by["binary_sensor.boiler_state"]["own"] is False


async def test_push_times_are_the_houses_clock(hass: HomeAssistant) -> None:
    """Integrator review 29.10 (defect 9): the alarm card said «с 03:46», the push «01:46» (UTC). Pushes say HA's
    configured time zone; the stored «since» stays ISO with its offset."""
    pushed: list[str] = []

    async def fine(call):
        pushed.append(call.data["message"])

    hass.services.async_register("notify", "mobile_app_tablet", fine)
    await hass.config.async_update(time_zone="Asia/Dubai")
    eng = _engine(hass)
    assert eng._hhmm("2026-10-29T01:46:00+00:00") == "05:46"
    assert eng._hhmm("2026-10-29T01:46:00") == "05:46"  # no offset: read as UTC, as the agent stores it
    await hass.config.async_update(time_zone="Europe/Moscow")
    assert eng._hhmm("2026-10-29T01:46:00+00:00") == "04:46"
    # The alarm push itself.
    hass.states.async_set("binary_sensor.leak", "on", {"device_class": "moisture", "friendly_name": "Протечка"})
    rule = {"id": "r1", "title": "Протечка", "priority": "normal", "recipients": {"all": False, "people": [], "groups": [], "phones": ["mobile_app_tablet"]}}
    eng.active["r1|binary_sensor.leak"] = {"since": "2026-10-29T01:46:00+00:00", "acked_by": None, "repeat": None, "count": 0, "group": False}
    with patch.object(eng, "_schedule"), patch.object(eng, "_save"):
        await eng._send_alarm(rule, hass.states.get("binary_sensor.leak"), "r1|binary_sensor.leak")
    assert pushed and "04:46" in pushed[-1] and "01:46" not in pushed[-1]
    assert dt_util.parse_datetime(eng.active["r1|binary_sensor.leak"]["since"]).utcoffset() is not None
