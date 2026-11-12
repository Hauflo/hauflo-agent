"""Р65 «читать после команды» (rules.py, rules_canon.json «bus_reads»): a KNX device that answers a read but never sends
its state by itself (a mode button rewrites several valves inside the device) — a write to a trigger group address
makes the agent call knx.read of the status addresses after the canon's delay. Proven on a real HA: the KNX telegram
signal, the timer, the service call, the cap, the store over a restart, who may set it."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.hauflo import rules as rules_mod
from custom_components.hauflo.canon import CANON
from custom_components.hauflo.derived import Invalid

from .test_rules import URL, _engine, _post, clock, entry  # noqa: F401 — the shared fixtures

BR = CANON["bus_reads"]
SIGNAL = "knx_telegram"  # HA KNX const.SIGNAL_KNX_TELEGRAM


@pytest.fixture(autouse=True)
def knx_signal():
    """HA KNX's dispatcher signal, without loading the KNX integration (xknx) into the test HA."""
    import sys
    import types

    real = sys.modules.get("homeassistant.components.knx.const")
    if real is None:
        mod = types.ModuleType("homeassistant.components.knx.const")
        mod.SIGNAL_KNX_TELEGRAM = SIGNAL
        sys.modules["homeassistant.components.knx.const"] = mod
    yield getattr(sys.modules["homeassistant.components.knx.const"], "SIGNAL_KNX_TELEGRAM")
    if real is None:
        sys.modules.pop("homeassistant.components.knx.const", None)


@pytest.fixture
def reads(hass: HomeAssistant):
    made: list[list[str]] = []

    async def handler(call: ServiceCall) -> None:
        a = call.data["address"]
        made.append(list(a) if isinstance(a, list) else [a])

    hass.services.async_register("knx", "read", handler)
    return made


def _rule(**kw):
    return {"id": "valves_mode", "name": "Режим клапанов", "triggers": ["1/0/10"], "reads": ["2/1/1", "2/1/2", "2/1/3"], **kw}


async def _telegram(hass, signal, ga, kind="GroupValueWrite", direction="Incoming"):
    async_dispatcher_send(hass, signal, None, {"destination": ga, "telegramtype": kind, "direction": direction, "value": 1})
    await hass.async_block_till_done()


async def _later(hass, s):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=s))
    await hass.async_block_till_done()


def test_group_addresses_and_the_cleaner() -> None:
    assert rules_mod.ga_raw("1/0/10") == (1 << 11) | 10
    assert rules_mod.ga_raw("1/2/3") == (1 << 11) | (2 << 8) | 3
    assert rules_mod.ga_raw("1/522") == rules_mod.ga_raw("1/2/10")  # two-level and three-level notations meet
    assert rules_mod.ga_raw("2058") == rules_mod.ga_raw("1/0/10")
    for bad in ("32/0/0", "1/8/0", "1/0/256", "1/2048", "65536", "", "1/a/2", None, True, "1/2/3/4"):
        assert rules_mod.ga_raw(bad) is None, bad
    got = rules_mod.clean_bus_reads([_rule(reads=["2/1/1", "2/1/1", " 2/1/2 "])])
    assert got == [{"id": "valves_mode", "name": "Режим клапанов", "triggers": ["1/0/10"], "reads": ["2/1/1", "2/1/2"], "delay_s": BR["delay_s"], "enabled": True}]
    lo, hi = BR["delay_bounds_s"]
    for bad, key in (
        ([_rule(triggers=[])], "rules_bus_read_gas"),
        ([_rule(reads=["9/9/9"])], "rules_bus_read_ga"),
        ([_rule(delay_s=hi + 1)], "rules_bus_read_delay"),
        ([_rule(delay_s=lo - 0.5)], "rules_bus_read_delay"),
        ([_rule(delay_s=True)], "rules_bus_read_delay"),
        ([_rule(id="Bad id")], "rules_bad_id"),
        ([_rule(), _rule()], "rules_bad_id"),
        ([_rule(name="x" * (int(BR["name_len"]) + 1))], "rules_bad_name"),
        ([_rule(reads=[f"3/0/{i}" for i in range(int(BR["reads"]) + 1)])], "rules_bus_read_gas"),
        ([_rule(id=f"r{i}") for i in range(int(BR["rules"]) + 1)], "rules_limit_bus_reads"),
        ({"id": "x"}, "rules_bad_object"),
    ):
        with pytest.raises(Invalid) as err:
            rules_mod.clean_bus_reads(bad)
        assert err.value.key == key


async def test_a_write_to_the_command_reads_every_status_after_the_delay(hass: HomeAssistant, hass_client, entry, reads, knx_signal) -> None:  # noqa: F811
    client = await hass_client()
    got = await _post(client, {"op": "bus_reads", "rules": [_rule(), _rule(id="other", name=None, triggers=["1/0/11"], reads=["2/2/1"])]})
    assert [r["id"] for r in got["bus_reads"]["rules"]] == ["valves_mode", "other"] and got["bus_reads"]["listening"]

    # Not a write (a read / an answer) and an address no rule waits for: nothing.
    await _telegram(hass, knx_signal, "1/0/10", "GroupValueRead", "Outgoing")
    await _telegram(hass, knx_signal, "1/0/10", "GroupValueResponse")
    await _telegram(hass, knx_signal, "1/0/12")
    await _later(hass, BR["delay_bounds_s"][1] + 1)
    assert reads == []

    # The mode button (one bit) → the three valves read once, not before the canon's delay.
    await _telegram(hass, knx_signal, "1/0/10")
    await _later(hass, BR["delay_s"] - 1)
    assert reads == []
    await _later(hass, BR["delay_s"] + 0.5)
    assert reads == [["2/1/1", "2/1/2", "2/1/3"]]

    # HA's own write (the app's button → Outgoing) counts the same; the second rule waits for its own address only.
    await _telegram(hass, knx_signal, "1/0/11", direction="Outgoing")
    await _later(hass, BR["delay_s"] + 0.5)
    assert reads[-1] == ["2/2/1"] and len(reads) == 2

    # The log says it (the integrator's «Подробнее»), GET shows the rules to the admin.
    out = await (await client.get(URL)).json()
    assert [(i["rule"], i["result"]) for i in out["bus_reads"]["log"]] == [("valves_mode", "read"), ("other", "read")]
    assert out["bus_reads"]["log"][0]["trigger"] == "1/0/10"


async def test_a_burst_of_writes_reads_once_and_never_later_than_the_bound(hass: HomeAssistant, hass_client, entry, reads, knx_signal) -> None:  # noqa: F811
    await _post(await hass_client(), {"op": "bus_reads", "rules": [_rule(delay_s=BR["delay_s"])]})
    for _ in range(3):
        await _telegram(hass, knx_signal, "1/0/10")
        await _later(hass, 1)
    assert reads == []
    await _later(hass, BR["delay_s"] + 2)
    assert reads == [["2/1/1", "2/1/2", "2/1/3"]]


async def test_two_level_addresses_meet_the_bus_notation(hass: HomeAssistant, hass_client, entry, reads, knx_signal) -> None:  # noqa: F811
    await _post(await hass_client(), {"op": "bus_reads", "rules": [_rule(triggers=["1/10"])]})
    await _telegram(hass, knx_signal, "1/0/10")
    await _later(hass, BR["delay_s"] + 1)
    assert reads == [["2/1/1", "2/1/2", "2/1/3"]]


async def test_switched_off_rule_and_the_reads_cap(hass: HomeAssistant, hass_client, entry, reads, knx_signal, clock) -> None:  # noqa: F811
    client = await hass_client()
    await _post(client, {"op": "bus_reads", "rules": [_rule(enabled=False)]})
    await _telegram(hass, knx_signal, "1/0/10")
    await _later(hass, BR["delay_s"] + 1)
    assert reads == []
    many = [f"3/0/{i}" for i in range(int(BR["reads"]))]
    await _post(client, {"op": "bus_reads", "rules": [_rule(reads=many)]})
    fits = int(BR["reads_per_min"]) // len(many)
    for _ in range(fits + 1):
        await _telegram(hass, knx_signal, "1/0/10")
        await _later(hass, BR["delay_s"] + 1)
    assert len(reads) == fits  # the one over the cap is skipped and said
    assert _engine(entry).bus_reads_out()["log"][-1]["result"] == "capped"
    clock.advance(61)
    await _telegram(hass, knx_signal, "1/0/10")
    await _later(hass, BR["delay_s"] + 1)
    assert len(reads) == fits + 1  # a minute later — reads again


async def test_a_failed_read_is_logged_and_the_next_write_tries_again(hass: HomeAssistant, hass_client, entry, knx_signal) -> None:  # noqa: F811
    await _post(await hass_client(), {"op": "bus_reads", "rules": [_rule()]})
    await _telegram(hass, knx_signal, "1/0/10")  # no knx.read service: KNX not set up
    await _later(hass, BR["delay_s"] + 1)
    assert _engine(entry).bus_reads_out()["log"][-1]["result"] == "error"


async def test_kept_over_a_restart_and_replaced_whole(hass: HomeAssistant, hass_client, entry, reads, knx_signal) -> None:  # noqa: F811
    client = await hass_client()
    await _post(client, {"op": "bus_reads", "rules": [_rule()]})
    engine = _engine(entry)
    assert engine.data()["bus_reads"][0]["id"] == "valves_mode"
    fresh = rules_mod.Rules(hass, engine.owns, engine.own_raw)
    fresh.store = engine.store
    await fresh.async_load()
    assert [r["reads"] for r in fresh.bus_reads] == [["2/1/1", "2/1/2", "2/1/3"]]
    got = await _post(client, {"op": "bus_reads", "rules": []})
    assert got["bus_reads"]["rules"] == []
    await _telegram(hass, knx_signal, "1/0/10")
    await _later(hass, BR["delay_s"] + 1)
    assert reads == []


async def test_only_the_integrator_sets_them_and_a_bad_list_is_refused(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry) -> None:  # noqa: F811
    kid = await hass_client(hass_read_only_access_token)
    assert (await kid.post(URL, json={"op": "bus_reads", "rules": [_rule()]})).status == 403
    assert "bus_reads" not in await (await kid.get(URL)).json()
    bad = await _post(await hass_client(), {"op": "bus_reads", "rules": [_rule(reads=["1/9/0"])]}, 400)
    assert bad["key"] == "rules_bus_read_ga" and "1/9/0" in bad["message"] and "1/9/0" in bad["message_ru"]
    assert _engine(entry).bus_reads == []
