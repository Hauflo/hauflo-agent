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
    got = signals.evaluate(case["state"], eid=ref["entityId"], platform=ref.get("platform"), device_class=ref.get("deviceClass"), name=ref.get("name"), device_kind=ref.get("deviceKind"), policy=SITE if case["policy"] else None)
    want = case["want"]
    assert {k: got[k] for k in want if k != "affects"} | {"affects": got["affects"]} == want


def test_parity_covers_the_canon():
    """Every class of the canon has its cases, the site policy came through whole (nothing dropped)."""
    seen = {f"{c['ref']['entityId'].split('.')[0]}.{c['ref']['deviceClass'].lower()}" if c["ref"].get("deviceClass") else c["ref"]["entityId"].split(".")[0] for c in PARITY["cases"]}
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


async def test_hidden_fault_sensor_is_still_watched(hass: HomeAssistant, entry):
    """0.33 fix3 п.19: Hauflo hides floor-fault sensors from the panel (hidden_by=user) — «скрыт» is not «не наблюдается»:
    by class it stays watched and moves the house; only disabled (no state) or an explicit «off» drops it."""
    agent = entry.runtime_data
    reg = er.async_get(hass)
    reg.async_get_or_create("binary_sensor", "knx", "floor_fault", suggested_object_id="floor_fault", original_device_class="problem")
    reg.async_update_entity("binary_sensor.floor_fault", hidden_by=er.RegistryEntryHider.USER)
    _set(hass, "binary_sensor.floor_fault", "on", device_class="problem", friendly_name="Тёплый пол")
    assert "binary_sensor.floor_fault" in agent._monitored_ids() and agent.watches("binary_sensor.floor_fault")
    h = agent.house()
    assert h["_house"] == "fault" and "binary_sensor.floor_fault" in [f["entity"] for f in h["_faults"]]
    assert "binary_sensor.floor_fault" in [m["entityId"] for m in agent.monitored()]
    # Disabled — no state, not watched by class.
    reg.async_update_entity("binary_sensor.floor_fault", hidden_by=None, disabled_by=er.RegistryEntryDisabler.USER)
    assert "binary_sensor.floor_fault" not in agent._monitored_ids() and not agent.watches("binary_sensor.floor_fault")


async def test_cards_verdicts_follow_the_policy(hass: HomeAssistant, entry, hass_client):
    """Owner 10.11: «Авария когда: Не авария» for a ZONT heat sensor — the cards' `_signals` say «none» for it at once (the
    tile's red frame and «авария» go), a policy change back says it again; the canon's ones and the classless ones by
    their words are listed, «не решено» and the unlisted are not (the cards' canon, the same)."""
    agent = entry.runtime_data
    reg = er.async_get(hass)
    reg.async_get_or_create("binary_sensor", "zont_ha", "gk", suggested_object_id="gk", original_device_class="heat")
    reg.async_get_or_create("binary_sensor", "hauflo", "own", suggested_object_id="own", original_device_class="problem")
    _set(hass, "binary_sensor.gk", "on", device_class="heat", friendly_name="ГК Состояние")
    _set(hass, "binary_sensor.zont", "off", device_class="problem", friendly_name="Zont")
    _set(hass, "binary_sensor.knopka", "on", friendly_name="Кнопка")  # «не решено»
    _set(hass, "binary_sensor.boiler", "on", friendly_name="Авария котла")  # classless by its words
    _set(hass, "binary_sensor.door", "on", device_class="door", friendly_name="Дверь")  # unlisted
    _set(hass, "binary_sensor.own", "on", device_class="problem", friendly_name="Hauflo")  # the agent's own
    v = agent.house()["_signals"]
    assert v["binary_sensor.gk"] == {"house": "alarm", "alarm": {"states": ["on"]}, "src": "class"}
    assert v["binary_sensor.zont"] == {"house": "fault", "alarm": {"states": ["on"]}, "src": "class"}
    assert v["binary_sensor.boiler"]["house"] == "fault"
    assert not {"binary_sensor.knopka", "binary_sensor.door", "binary_sensor.own"} & set(v)
    client = await hass_client()

    async def push(ents, h):
        res = await client.post("/api/hauflo/signals", json={"signals": {"entities": ents}, "hash": h})
        assert res.status == 200

    # «Не авария»: never an alarm here — in the view the cards read, at once.
    await push({"binary_sensor.gk": {"alarm": {"none": True}, "by": "t", "at": AT}}, "0123456789abcdef")
    body = await (await client.get("/api/hauflo/alarms")).json()
    assert body["_signals"]["binary_sensor.gk"] == {"house": "none", "src": "entity", "meaning": "none"} and body["_house"] == "ok"
    # «не влияет» / «не следить» alike; a «не решено» one decided but not watched moves nothing — listed with its condition
    # (a tile listing it frames by it, Р9/Р19), never moving the house.
    await push({"binary_sensor.gk": {"house": "none", "by": "t", "at": AT}, "binary_sensor.zont": {"watch": False, "by": "t", "at": AT}, "binary_sensor.knopka": {"house": "fault", "by": "t", "at": AT}}, "1123456789abcdef")
    v = agent.house()["_signals"]
    assert v["binary_sensor.gk"] == v["binary_sensor.zont"] == {"house": "none", "alarm": {"states": ["on"]}, "src": "entity"}
    assert v["binary_sensor.knopka"] == {"house": "none", "alarm": {"states": ["on"]}, "src": "entity"} and agent.house()["_house"] == "ok"
    # Watched by the site's «следить»: listed with its fault.
    await push({"binary_sensor.knopka": {"house": "fault", "watch": True, "by": "t", "at": AT}}, "3123456789abcdef")
    assert agent.house()["_signals"]["binary_sensor.knopka"] == {"house": "fault", "alarm": {"states": ["on"]}, "src": "entity"}
    # Back to the canon: an alarm again; the site's own condition goes as it is (the card compares HA's live state).
    await push({"binary_sensor.gk": {"alarm": {"states": ["off"]}, "by": "t", "at": AT}}, "2123456789abcdef")
    v = agent.house()["_signals"]
    assert v["binary_sensor.gk"] == {"house": "alarm", "alarm": {"states": ["off"]}, "src": "entity", "meaning": "off"} and v["binary_sensor.zont"]["house"] == "fault"
    # The size: at most cards_max, the site's own choices first.
    with patch.object(signals, "CARDS_MAX", 1):
        assert list(agent.house()["_signals"]) == ["binary_sensor.gk"]


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


def test_meanings_are_the_canons_policies():
    """Р9/Р19: «Авария когда» on a card — on / off / none / reading — is a policy of the canon's table, read back the same."""
    assert list(signals.MEANINGS) == ["on", "off", "none", "reading"]
    assert signals.meaning_policy("on") == {"alarm": {"states": ["on"]}}
    assert signals.meaning_policy("off") == {"alarm": {"states": ["off"]}}
    assert signals.meaning_policy("none") == {"alarm": {"none": True}}
    assert signals.meaning_policy("reading") == {"alarm": {"none": True}, "watch": False}
    assert signals.meaning_policy("default") == {}
    for k in signals.MEANINGS:
        assert signals.meaning_of({**signals.meaning_policy(k), "severity": "info"}) == k
    assert signals.meaning_of({"alarm": {"states": ["on", "off"]}}) is None and signals.meaning_of({"house": "none"}) is None
    # Both clocks alike: the cloud's «…Z» with milliseconds and HA's «+00:00».
    assert signals.newer("2026-11-10T10:00:00.500+00:00", "2026-11-10T10:00:00.000Z") and not signals.newer("2026-11-10T10:00:00Z", "2026-11-10T10:00:00.000+00:00")


async def test_meaning_chosen_in_ha_applies_now_and_goes_to_the_site(hass: HomeAssistant, entry, hass_client, hass_read_only_access_token):
    """Р9/Р19: «Авария когда: выкл» chosen on a tile in HA — the cards' verdict and «Всё хорошо» follow at once (no cloud),
    the status carries it to Hauflo with its time; Hauflo's push of it (the same `at`) ends it; a newer site's choice wins,
    an older one doesn't; kept over a restart. Only admins; a bad body is a 400, never a 500."""
    agent = entry.runtime_data
    client = await hass_client()
    _set(hass, "binary_sensor.zont", "off", device_class="problem", friendly_name="Zont")
    assert agent.house()["_house"] == "ok"
    user = await hass_client(hass_read_only_access_token)
    assert (await user.post("/api/hauflo/signals/choose", json={"entity": "binary_sensor.zont", "meaning": "off"})).status == 403
    for body in ({"entity": "binary_sensor.zont"}, {"entity": "light.x", "meaning": "on"}, {"entity": "binary_sensor.zont", "meaning": "maybe"}, {"entity": [], "meaning": "on"}, {"entity": "binary_sensor.zont", "meaning": []}, {"entity": "binary_sensor.zont", "meaning": "on", "x": 1}, []):
        assert (await client.post("/api/hauflo/signals/choose", json=body)).status == 400, str(body)
    res = await client.post("/api/hauflo/signals/choose", json={"entity": "binary_sensor.zont", "meaning": "off"})
    got = await res.json()
    assert res.status == 200 and got["meaning"] == "off" and got["ha"] is True and got["problem"] is True and got["house"] == "fault"
    h = agent.house()
    assert h["_house"] == "fault" and h["_signals"]["binary_sensor.zont"] == {"house": "fault", "alarm": {"states": ["off"]}, "src": "ha", "meaning": "off"}
    ch = agent.status()["signalChoices"]
    assert list(ch) == ["binary_sensor.zont"] and ch["binary_sensor.zont"]["meaning"] == "off" and ch["binary_sensor.zont"]["by"]
    at = ch["binary_sensor.zont"]["at"]
    # An older site's choice for it (a push raced the status): HA's stays.
    await client.post("/api/hauflo/signals", json={"signals": {"entities": {"binary_sensor.zont": {"severity": "info", "alarm": {"none": True}, "by": "t", "at": AT}}}, "hash": "0123456789abcdef"})
    assert agent.house()["_signals"]["binary_sensor.zont"]["meaning"] == "off" and signals.policy(hass)["entities"]["binary_sensor.zont"]["severity"] == "info"
    assert list(agent.status()["signalChoices"]) == ["binary_sensor.zont"]
    # «Только показание»: never an alarm, not watched — and kept over a restart.
    await client.post("/api/hauflo/signals/choose", json={"entity": "binary_sensor.zont", "meaning": "reading"})
    s = signals.store(hass)
    data = s._data()
    s2 = signals.Signals(hass)

    async def load():
        return data

    s2.store.async_load = load
    await s2.async_load()
    assert s2.policy["entities"]["binary_sensor.zont"]["watch"] is False and signals.meaning_of(s2.policy["entities"]["binary_sensor.zont"]) == "reading"
    assert "binary_sensor.zont" in s.watch_off() and agent.house()["_signals"]["binary_sensor.zont"]["house"] == "none"
    at = agent.status()["signalChoices"]["binary_sensor.zont"]["at"]
    # Hauflo holds it (its push carries the same `at`): HA's choice is done, nothing more to carry.
    held = {"binary_sensor.zont": {"alarm": {"none": True}, "watch": False, "by": "admin, в HA", "at": at, "ha": True}}
    await client.post("/api/hauflo/signals", json={"signals": {"entities": held}, "hash": "1123456789abcdef"})
    assert agent.status()["signalChoices"] == {} and s.local == {} and agent.house()["_signals"]["binary_sensor.zont"]["src"] == "entity"
    # A newer site's choice wins over an older HA one.
    await client.post("/api/hauflo/signals/choose", json={"entity": "binary_sensor.zont", "meaning": "on"})
    await client.post("/api/hauflo/signals", json={"signals": {"entities": {"binary_sensor.zont": {"alarm": {"states": ["off"]}, "by": "t", "at": "2099-01-01T00:00:00.000Z"}}}, "hash": "2123456789abcdef"})
    assert agent.house()["_signals"]["binary_sensor.zont"]["meaning"] == "off" and agent.status()["signalChoices"] == {}
    # «По умолчанию» in HA: the site's own condition dropped (the canon's again), the rest of its choice kept.
    await client.post("/api/hauflo/signals", json={"signals": {"entities": {"binary_sensor.zont": {"alarm": {"states": ["off"]}, "severity": "info", "by": "t", "at": AT}}}, "hash": "3123456789abcdef"})
    await client.post("/api/hauflo/signals/choose", json={"entity": "binary_sensor.zont", "meaning": "default"})
    own = signals.policy(hass)["entities"]["binary_sensor.zont"]
    # 0.33 L4: «По умолчанию» is no card choice of its own — the row reads the site's (its severity), not «на карточке».
    assert "alarm" not in own and own["severity"] == "info" and agent.house()["_signals"]["binary_sensor.zont"] == {"house": "fault", "alarm": {"states": ["on"]}, "src": "entity"}
    assert agent.status()["signalChoices"]["binary_sensor.zont"]["meaning"] == "default"
    # Hauflo dropped the signal's own altogether: the card's «По умолчанию» is done, not carried in every status for good.
    await client.post("/api/hauflo/signals", json={"signals": {"entities": {}}, "hash": "4123456789abcdef"})
    assert agent.status()["signalChoices"] == {} and agent.house()["_signals"]["binary_sensor.zont"]["src"] != "ha"
    # «По умолчанию» for a signal with nothing of its own: nothing to carry.
    await client.post("/api/hauflo/signals/choose", json={"entity": "binary_sensor.zont", "meaning": "default"})
    assert agent.status()["signalChoices"] == {}


async def test_a_rule_naming_a_not_an_alarm_signal_is_said(hass: HomeAssistant, entry, hass_client):
    """0.33 M6: «не авария» never silences an integrator's alarm rule naming the signal (notify.policy_overlay) — its
    verdict says `rule`, so the card's editor never reads a bare «не авария» beside a red tile."""
    from custom_components.hauflo import notify

    agent = entry.runtime_data
    client = await hass_client()
    _set(hass, "binary_sensor.zont", "on", device_class="problem", friendly_name="Zont")
    _set(hass, "binary_sensor.other", "off", device_class="problem", friendly_name="Other")
    assert agent.engine is not None
    rule = {"id": "fault", "kind": "custom", "title": "Fault", "alarm_states": ["on"], "recipients": {"all": True}, "repeat_min": 0, "sources": ["binary_sensor.zont"], "picked": ["binary_sensor.zont"]}
    agent.engine.config = notify._clean_rules({"lang": "ru", "rules": [rule]}, hass, agent.engine.owns)
    await client.post("/api/hauflo/signals/choose", json={"entity": "binary_sensor.zont", "meaning": "none"})
    v = agent.house()["_signals"]
    assert v["binary_sensor.zont"]["house"] == "none" and v["binary_sensor.zont"]["rule"] is True
    assert "rule" not in v["binary_sensor.other"]
