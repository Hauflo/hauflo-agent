"""The site's signal policy in the agent (0.32 п.3, signals.py): ONE calculator with the cloud's (signals.ts) — replayed
from the cloud's own verdicts (fixtures/signal_parity.json, written by signals.test.ts from the canon); a Cyrillic enum,
«не решено», scope vs entity; «Всё хорошо» (_house) by it; POST/GET /api/hauflo/signals."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import signals
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN
from custom_components.hauflo.tuning import TUNING

PARITY = json.loads((Path(__file__).parent / "fixtures" / "signal_parity.json").read_text("utf8"))
SITE = signals.signals_in(PARITY["policy"])[0]
AT = "2026-11-10T00:00:00.000Z"


def _case_id(c: dict) -> str:
    return f"{c['policy']}:{c['ref']['entityId']}:{c['state'][:12]}"


@pytest.mark.parametrize("case", PARITY["cases"], ids=_case_id)
def test_evaluate_is_the_clouds(case):
    ref = case["ref"]
    got = signals.evaluate(case["state"], eid=ref["entityId"], platform=ref.get("platform"), device_class=ref.get("deviceClass"), name=ref.get("name"), policy=SITE if case["policy"] else None)
    want = case["want"]
    assert {k: got[k] for k in want if k != "affects"} | {"affects": got["affects"]} == want


def test_parity_covers_the_canon():
    """Every class of the canon has its cases, the site policy came through whole (nothing dropped)."""
    seen = {f"{c['ref']['entityId'].split('.')[0]}.{c['ref']['deviceClass'].lower()}" for c in PARITY["cases"] if c["ref"].get("deviceClass")}
    assert set(TUNING["signals"]["classes"]) <= seen
    assert signals.signals_in(PARITY["policy"])[1] == 0
    assert len(SITE["scopes"]) == len(PARITY["policy"]["scopes"]) and set(SITE["entities"]) == set(PARITY["policy"]["entities"])


def test_cyrillic_enum_is_compared_as_is():
    pol = signals.signals_in({"entities": {"sensor.zont": {"alarm": {"states": ["Авария котла"]}, "house": "fault", "by": "t", "at": AT}}})[0]
    ev = lambda s: signals.evaluate(s, eid="sensor.zont", policy=pol)  # noqa: E731
    assert ev("Авария котла")["problem"] and ev("Авария котла")["affects"]
    assert not ev("авария котла")["problem"] and not ev("Норма")["problem"]
    assert signals.evaluate("Авария котла", eid="sensor.zont")["house"] == TUNING["signals"]["unlisted"]["house"]  # without the policy: the canon's


def test_undetermined_never_moves_the_house():
    ev = signals.evaluate("on", eid="binary_sensor.knopka", name="Кнопка")
    assert ev["problem"] and ev["undetermined"] and not ev["affects"] and ev["source"] == "undetermined"
    pol = signals.signals_in({"entities": {"binary_sensor.knopka": {"house": "fault", "by": "t", "at": AT}}})[0]
    ev = signals.evaluate("on", eid="binary_sensor.knopka", name="Кнопка", policy=pol)
    assert not ev["undetermined"] and ev["affects"] and ev["source"] == "entity"


def test_entity_wins_over_scope_and_the_specific_scope_over_the_broad():
    pol = signals.signals_in(
        {
            "scopes": [
                {"match": {"domain": "binary_sensor", "deviceClass": "problem"}, "set": {"house": "alarm"}, "by": "t", "at": AT},
                {"match": {"platform": "knx"}, "set": {"house": "none"}, "by": "t", "at": AT},
            ],
            "entities": {"binary_sensor.own": {"house": "fault", "by": "t", "at": AT}},
        }
    )[0]
    assert signals.evaluate("on", eid="binary_sensor.x", device_class="problem", policy=pol)["house"] == "alarm"
    assert signals.evaluate("on", eid="binary_sensor.x", device_class="problem", platform="knx", policy=pol)["house"] == "none"  # platform 4 > domain+class 3
    own = signals.evaluate("on", eid="binary_sensor.own", device_class="problem", platform="knx", policy=pol)
    assert own["house"] == "fault" and own["source"] == "entity" and own["from"]["house"] == "entity"


def test_bad_rows_are_dropped_not_the_whole():
    got = signals.signals_in({"scopes": [{"match": {"domain": "BAD"}, "set": {"house": "fault"}}], "entities": {"Bad Id": {"house": "fault"}, "sensor.ok": {"house": "fault", "by": "t", "at": AT}}, "findings": {"x": {}}})
    assert got is not None and got[1] == 3 and list(got[0]["entities"]) == ["sensor.ok"]
    assert signals.signals_in({"scopes": "no"}) is None and signals.signals_in({"other": 1}) is None


# --- in HA: the view, «Всё хорошо» ---------------------------------------------------------------------------------------


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    reg = er.async_get(hass)
    for uid, dc in (("zont", "problem"), ("leak", "moisture"), ("knopka", None)):
        reg.async_get_or_create("binary_sensor", "zha", uid, suggested_object_id=uid, original_device_class=dc)
    return entry


def _set(hass, eid, state, **attrs):
    hass.states.async_set(eid, state, attrs)


async def test_house_by_the_policy(hass: HomeAssistant, entry, hass_client):
    agent = entry.runtime_data
    _set(hass, "binary_sensor.zont", "off", device_class="problem", friendly_name="Zont")
    _set(hass, "binary_sensor.leak", "off", device_class="moisture", friendly_name="Протечка")
    assert agent.house()["_house"] == "ok"
    _set(hass, "binary_sensor.zont", "on", device_class="problem", friendly_name="Zont")
    h = agent.house()
    assert h["_house"] == "fault" and [f["entity"] for f in h["_faults"]] == ["binary_sensor.zont"]
    # «Zont не авария»: the site says house none — «Всё хорошо» again, at once.
    client = await hass_client()
    res = await client.post("/api/hauflo/signals", json={"signals": {"entities": {"binary_sensor.zont": {"house": "none", "by": "t", "at": AT}}}, "hash": "0123456789abcdef"})
    assert res.status == 200 and (await res.json())["hash"] == "0123456789abcdef"
    assert agent.house()["_house"] == "ok"
    body = await (await client.get("/api/hauflo/alarms")).json()
    assert body["_house"] == "ok" and body["_faults"] == []
    # The leak: alarm.
    _set(hass, "binary_sensor.leak", "on", device_class="moisture", friendly_name="Протечка")
    assert agent.house()["_house"] == "alarm"
    # Lost only: offline; «не решено» listed, never moving it.
    _set(hass, "binary_sensor.leak", "unavailable", device_class="moisture", friendly_name="Протечка")
    assert agent.house()["_house"] == "offline"
    assert agent.status()["signalsHash"] == "0123456789abcdef"


async def test_signals_view_rights_and_bodies(hass: HomeAssistant, entry, hass_client, hass_read_only_access_token):
    client = await hass_client()
    got = await (await client.get("/api/hauflo/signals")).json()
    assert got["hash"] is None
    for body in ({"signals": {}}, {"signals": {}, "hash": "nothex"}, {"signals": {"scopes": "x"}, "hash": "0123456789abcdef"}, {"signals": {}, "hash": "0123456789abcdef", "x": 1}, []):
        assert (await client.post("/api/hauflo/signals", json=body)).status == 400, str(body)
    assert (await client.post("/api/hauflo/signals", data="not json")).status == 400
    user = await hass_client(hass_read_only_access_token)
    assert (await user.get("/api/hauflo/signals")).status == 403
    assert (await user.post("/api/hauflo/signals", json={"signals": {}, "hash": "0123456789abcdef"})).status == 403
    res = await (await client.post("/api/hauflo/signals", json={"signals": {"entities": {"sensor.ok": {"house": "fault", "by": "t", "at": AT}, "Bad": {}}}, "hash": "fedcba9876543210"})).json()
    assert res == {"hash": "fedcba9876543210", "applied": True, "counts": {"scopes": 0, "entities": 1, "findings": 0}, "dropped": 1}
    got = await (await client.get("/api/hauflo/signals")).json()
    assert got["hash"] == "fedcba9876543210"
    # The same hash again (acceptance 10.11 L2): nothing written, no listener told, «applied: false».
    told = []
    s = signals.store(hass)
    off = s.listen(lambda: told.append(1))
    saves = []
    s.store.async_delay_save = lambda *a, **k: saves.append(a)
    res = await (await client.post("/api/hauflo/signals", json={"signals": {"entities": {}}, "hash": "fedcba9876543210"})).json()
    assert res == {"hash": "fedcba9876543210", "applied": False, "counts": {"scopes": 0, "entities": 1, "findings": 0}, "dropped": 0}
    assert told == [] and saves == [] and s.at == got["at"] and list(s.policy["entities"]) == ["sensor.ok"]
    # Another hash: applied, told.
    res = await (await client.post("/api/hauflo/signals", json={"signals": {"entities": {}}, "hash": "0123456789abcdef"})).json()
    assert res["applied"] is True and res["counts"]["entities"] == 0 and told == [1] and len(saves) == 1
    off()
