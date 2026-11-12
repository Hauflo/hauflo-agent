"""Р83 «Повторить N раз через M с» (rules.py clean_repeat / _repeat_arm, canon actions.repeat) and the client's switch
narrowed to a socket (canon actions.client_classes). Adversarial: a press really goes N times, not N+1, not 1; a rule
deleted / switched off mid-way stops what's left; a repeat on an action without it, out of bounds, a pulse's pause
shorter than the pulse — 400; a client's non-socket switch — refused, a socket — allowed."""

from __future__ import annotations

import datetime as dt

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.hauflo import rules as rules_mod
from custom_components.hauflo.derived import Invalid

from .test_rules import HEATER2, URL, _engine, _post, calls, clock, entry  # noqa: F401 — fixtures
from .test_rules_schedule import BUTTON, _at, _made, _monday, _tick, more, vent  # noqa: F401 — fixtures


async def _later(hass: HomeAssistant, s: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + dt.timedelta(seconds=s))
    await hass.async_block_till_done()


def press3(**kw):
    return vent(actions=[{"do": "press", "entity_id": BUTTON, "repeat": {"count": 3, "every_s": 2}}], **kw)


async def test_press_three_times_every_two_seconds(hass: HomeAssistant, hass_client, entry, more, clock) -> None:
    client, engine = await hass_client(), _engine(entry)
    got = await (await client.get(URL)).json()
    assert "repeat" in got["features"] and got["limits"]["repeat"]["count"] == [2, 10] and "press" in got["limits"]["repeat"]["acts"]
    mon = _monday()
    _at(clock, mon, 6, 59, 50)
    saved = await _post(client, {"op": "save", "rule": press3()})
    assert saved["rule"]["actions"][0]["repeat"] == {"count": 3, "every_s": 2}
    await _tick(hass, engine, clock, 15)
    assert len(_made(more, BUTTON)) == 1
    await _later(hass, 1)
    assert len(_made(more, BUTTON)) == 1  # not before its pause
    await _later(hass, 2.5)
    assert len(_made(more, BUTTON)) == 2
    await _later(hass, 5)
    await _later(hass, 10)
    assert len(_made(more, BUTTON)) == 3  # exactly N, never more
    assert not engine._repeat_timers.get("vent")


async def test_delete_or_switch_off_stops_the_rest(hass: HomeAssistant, hass_client, entry, more, clock) -> None:
    client, engine = await hass_client(), _engine(entry)
    mon = _monday()
    _at(clock, mon, 6, 59, 50)
    await _post(client, {"op": "save", "rule": press3()})
    await _tick(hass, engine, clock, 15)
    assert len(_made(more, BUTTON)) == 1
    await engine.delete("vent")
    await _later(hass, 30)
    assert len(_made(more, BUTTON)) == 1 and not engine._repeat_timers.get("vent")
    # Switched off mid-way: what's left is not done.
    _at(clock, mon + dt.timedelta(days=1), 6, 59, 50)
    await _post(client, {"op": "save", "rule": press3(id="vent2")})
    await _tick(hass, engine, clock, 15)
    n = len(_made(more, BUTTON))
    engine.rules["vent2"]["enabled"] = False
    await _later(hass, 30)
    assert len(_made(more, BUTTON)) == n


@pytest.mark.parametrize(
    "action",
    [
        {"do": "press", "entity_id": BUTTON, "repeat": {"count": 1, "every_s": 2}},
        {"do": "press", "entity_id": BUTTON, "repeat": {"count": 11, "every_s": 2}},
        {"do": "press", "entity_id": BUTTON, "repeat": {"count": 3, "every_s": 0}},
        {"do": "press", "entity_id": BUTTON, "repeat": {"count": 3, "every_s": 3601}},
        {"do": "press", "entity_id": BUTTON, "repeat": {"count": True, "every_s": 2}},
        {"do": "press", "entity_id": BUTTON, "repeat": {"count": 3, "every_s": 2, "extra": 1}},
        {"do": "press", "entity_id": BUTTON, "repeat": [3, 2]},
        {"do": "pulse", "entity_id": HEATER2, "value": 3, "repeat": {"count": 3, "every_s": 3}},
        {"do": "on", "entity_id": HEATER2, "repeat": {"count": 3, "every_s": 2}},
    ],
)
async def test_bad_repeat_refused(hass: HomeAssistant, hass_client, entry, more, action) -> None:
    got = await _post(await hass_client(), {"op": "save", "rule": vent(actions=[action])}, 400)
    assert got["key"] == "rules_bad_repeat", got


async def test_pulse_repeat_after_the_pulse(hass: HomeAssistant, hass_client, entry, more) -> None:
    got = await _post(await hass_client(), {"op": "save", "rule": vent(actions=[{"do": "pulse", "entity_id": HEATER2, "value": 3, "repeat": {"count": 2, "every_s": 4}}])})
    assert got["rule"]["actions"][0]["repeat"] == {"count": 2, "every_s": 4}


async def test_client_switch_only_a_socket(hass: HomeAssistant, entry, more) -> None:
    engine = _engine(entry)
    assert HEATER2 not in {t["entity_id"] for t in engine.targets(client=True)}
    with pytest.raises(Invalid) as err:
        await engine.save(vent(id="kid", actions=[{"do": "on", "entity_id": HEATER2}]), client=True, user_id=None)
    assert err.value.code == "not_allowed"
    er.async_get(hass).async_update_entity(HEATER2, device_class="outlet")
    assert rules_mod.client_class_ok(hass, HEATER2)
    assert HEATER2 in {t["entity_id"] for t in engine.targets(client=True)}
    got = await engine.save(vent(id="kid", actions=[{"do": "on", "entity_id": HEATER2}]), client=True, user_id=None)
    assert got["owner"] == "client"
