"""Signals — the refusal / edge branches (bad rows, bad times, the store missing): a bad input is dropped or refused, never a 500."""

import math
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import signals
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

AT = "2026-11-10T00:00:00.000Z"


def test_state_of_reads_numbers_as_js_text():
    assert signals.state_of("x") == "x"
    assert signals.state_of(3) == "3" and signals.state_of(3.0) == "3" and signals.state_of(2.5) == "2.5"
    assert signals.state_of(True) == "" and signals.state_of(math.inf) == "" and signals.state_of(None) == ""


def test_evaluate_needs_an_entity_id():
    with pytest.raises(ValueError):
        signals.evaluate("on")


def test_resolve_carries_hold_watch_and_shelf_of_the_policy():
    pol = signals.signals_in({"entities": {"binary_sensor.a": {"alarm": {"states": ["on"]}, "holdS": 30, "watch": False, "shelvedUntil": "2030-01-01T00:00:00Z"}}})[0]
    r = signals.resolve("binary_sensor.a", policy=pol)
    assert r["holdS"] == 30 and r["watch"] is False and r["shelvedUntil"] == "2030-01-01T00:00:00Z"


@pytest.mark.parametrize(
    "v",
    [
        {"alarm": {}},
        {"alarm": "x"},
        {"alarm": {"states": []}},
        {"alarm": {"states": ["a", "a"]}},
        {"alarm": {"states": [1]}},
        {"alarm": {"below": 5, "above": 3}},
        {"alarm": {"below": "x"}},
        {"alarm": {"step": 1}},
        {"alarm": {"below": 1, "step": -1}},
        {"alarm": {"bogus": 1}},
        {"holdS": True},
        {"holdS": -1},
        {"holdS": 1.5},
        {"severity": "nope"},
        {"house": "nope"},
        {"watch": "yes"},
        {"shelvedUntil": "tomorrow"},
        {"alarm": {"none": False}},
    ],
)
def test_policy_in_refuses_bad_rows(v):
    assert signals.policy_in(v) is None


def test_policy_in_keeps_good_levels_and_none():
    assert signals.policy_in({"alarm": {"none": True}, "holdS": 0, "watch": True})["alarm"] == {"none": True}
    got = signals.policy_in({"alarm": {"below": 1, "above": 5, "step": 2}})
    assert got["alarm"] == {"below": 1, "above": 5, "step": 2}
    assert signals.policy_in("x") is None


@pytest.mark.parametrize("v", ["x", {"platform": 1}, {"domain": "Not Valid!"}, {"deviceClass": 5}, {"deviceClass": "bad class!"}])
def test_match_in_refuses_bad(v):
    assert signals.match_in(v) is None


def test_match_in_keeps_platform_domain_class():
    got = signals.match_in({"platform": "zha", "domain": "binary_sensor", "deviceClass": " Moisture "})
    assert got == {"platform": "zha", "domain": "binary_sensor", "deviceClass": "moisture"}


def test_finding_and_diag_in():
    assert signals.finding_in("x") is None
    assert signals.finding_in({"by": "t"}) is None
    f = signals.finding_in({"by": "t", "at": AT, "normal": True, "factor": 2, "resetAt": AT})
    assert f == {"by": "t", "at": AT, "normal": True, "factor": 2, "resetAt": AT}
    assert signals.finding_in({"factor": 0}) is None
    assert signals.diag_in({"visible": "admins"}) == {"visible": "admins"}
    assert signals.diag_in({"visible": "nobody"}) is None and signals.diag_in(5) is None


def test_signals_in_drops_bad_rows_and_refuses_bad_shapes():
    assert signals.signals_in("x") is None
    assert signals.signals_in({"junk": 1}) is None
    assert signals.signals_in({"scopes": "x"}) is None
    assert signals.signals_in({"scopes": [{}] * (signals.CANON["scopesMax"] + 1)}) is None
    got, dropped = signals.signals_in({"findings": {"bad key!": {"normal": True}, "also": "x"}, "diag": {"visible": "admins"}})
    assert got["findings"] == {} and dropped == 2 and got["diag"] == {"visible": "admins"}
    assert signals.signals_in({"diag": {"visible": "x"}})[1] == 1
    got, dropped = signals.signals_in({"findings": {}, "entities": {"binary_sensor.a": {"watch": True, "at": AT}}})
    assert got["entities"]["binary_sensor.a"]["at"] == AT and dropped == 0


def test_local_in_and_times():
    assert signals.local_in({"meaning": "bogus", "at": AT}) is None
    assert signals.local_in({"meaning": "on", "at": "x"}) is None
    assert signals.local_in({"meaning": "default", "at": AT, "by": None}) == {"meaning": "default", "by": "", "at": AT}
    assert signals.newer(AT, None) and not signals.newer(None, AT) and not signals.newer(AT, AT)
    assert signals.newer("2026-11-10T00:00:01+00:00", AT)


def test_meaning_policy_and_back():
    assert signals.meaning_policy("default") == {}
    for k in signals.MEANINGS:
        assert signals.meaning_of(signals.meaning_policy(k)) == k
    assert signals.meaning_of(None) is None and signals.meaning_of({"alarm": {"states": ["zzz"]}}) is None


async def test_store_load_garbage_and_merge_rules(hass: HomeAssistant):
    s = signals.Signals(hass)
    data = {
        "entities": {"binary_sensor.a": {"house": "fault", "alarm": {"states": ["on"]}, "at": AT}},
        "hash": "not-a-hash",
        "at": 5,
        "local": {
            "binary_sensor.a": {"meaning": "none", "by": "me", "at": "2026-11-11T00:00:00Z"},
            "binary_sensor.old": {"meaning": "on", "by": "me", "at": AT},
            "bad id": {"meaning": "on", "at": AT},
            "binary_sensor.b": {"meaning": "nope", "at": AT},
        },
    }
    with patch.object(s.store, "async_load", return_value=data):
        await s.async_load()
    assert s.hash is None and s.at is None
    assert set(s.local) == {"binary_sensor.a", "binary_sensor.old"}
    assert s.policy["entities"]["binary_sensor.a"]["alarm"] == {"none": True}
    # an older HA choice than the site's does not win
    s.local["binary_sensor.a"]["at"] = "2020-01-01T00:00:00Z"
    s._merge()
    assert s.policy["entities"]["binary_sensor.a"]["alarm"] == {"states": ["on"]}
    # «default» over a policy with nothing else of its own drops the entry
    s.local = {"binary_sensor.zz": {"meaning": "default", "by": "x", "at": AT}}
    s._merge()
    assert "binary_sensor.zz" not in s.policy["entities"]


async def test_store_trims_local_and_survives_a_bad_listener(hass: HomeAssistant):
    s = signals.Signals(hass)
    for i in range(signals.LOCAL_MAX + 3):
        s.local[f"binary_sensor.e{i}"] = {"meaning": "on", "by": "", "at": f"2026-11-10T00:{i % 60:02d}:00Z"}
    s._trim()
    assert len(s.local) == signals.LOCAL_MAX

    def boom():
        raise RuntimeError("x")

    calls = []
    s.listen(boom)
    s.listen(lambda: calls.append(1))
    s._told()
    assert calls == [1]


def test_store_none_and_helpers():
    assert signals.store(None) is None and signals.policy(None) is None and signals.current_hash(None) is None


async def test_views_say_not_ready_without_a_store(hass: HomeAssistant, hass_client, hass_admin_user):
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)

    async def idle(self):
        return None

    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    er.async_get(hass)
    client = await hass_client()
    hass.data.pop(signals.DATA, None)
    assert (await client.get("/api/hauflo/signals")).status == 409
    assert (await client.post("/api/hauflo/signals", json={"signals": {}, "hash": "0123456789abcdef"})).status == 409
    assert (await client.post("/api/hauflo/signals/choose", json={"entity": "binary_sensor.a", "meaning": "on"})).status == 409


async def test_choose_for_an_unknown_entity_still_keeps_the_choice(hass: HomeAssistant, hass_client):
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)

    async def idle(self):
        return None

    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    client = await hass_client()
    res = await client.post("/api/hauflo/signals/choose", json={"entity": "binary_sensor.ghost", "meaning": "on"})
    got = await res.json()
    assert res.status == 200 and got["entity"] == "binary_sensor.ghost" and "problem" not in got
