"""«Сочетание состояний» (0.34 п.10, combos.py): a condition type of the one signal calculator (signals.cond_holds →
alarm_of), the wire's validation, the combination's problem sensor — on only after every condition held for T, off at
once when one ends, nothing switched; Hauflo watches it by the site's own policy for it."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hauflo import combos, signals
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN
from custom_components.hauflo.tuning import TUNING

LIM = TUNING["signals_agent"]["combos"]
SUPPLY = {"name": "Приток выкл, калорифер вкл", "all": [{"entityId": "fan.supply", "alarm": {"states": ["off"]}}, {"entityId": "switch.heater", "alarm": {"states": ["on"]}}], "holdS": 120, "preset": "supplyHeater"}
EID = "binary_sensor.hauflo_combo_abcd"
HASH = "0123456789abcdef"
AT = "2026-11-10T00:00:00.000Z"


def test_combo_in_takes_the_wire_shape():
    assert signals.combo_in(SUPPLY) == SUPPLY
    lvl = {"name": "Насос", "all": [{"entityId": "switch.pump", "alarm": {"states": ["on"]}}, {"entityId": "sensor.pressure", "alarm": {"below": 0.1}}], "holdS": 0}
    assert signals.combo_in(lvl) == lvl


@pytest.mark.parametrize(
    "bad",
    [
        {**SUPPLY, "extra": 1},  # unknown key
        {**SUPPLY, "all": SUPPLY["all"][:1]},  # one condition is no combination
        {**SUPPLY, "all": [SUPPLY["all"][0]] * 2},  # the same entity twice
        {**SUPPLY, "all": [*SUPPLY["all"], *({"entityId": f"switch.x{i}", "alarm": {"states": ["on"]}} for i in range(LIM["conds_max"]))]},
        {**SUPPLY, "holdS": -1},
        {**SUPPLY, "holdS": LIM["hold_max_s"] + 1},
        {**SUPPLY, "holdS": True},
        {**SUPPLY, "name": " "},
        {**SUPPLY, "name": "x" * (LIM["name_max"] + 1)},
        {**SUPPLY, "all": [{"entityId": "fan.supply", "alarm": {"none": True}}, SUPPLY["all"][1]]},  # «не авария» is no condition
        {**SUPPLY, "all": [{"entityId": "fan.supply", "alarm": {"below": 5, "above": 1}}, SUPPLY["all"][1]]},
        {**SUPPLY, "all": [{"entityId": "Fan.Supply", "alarm": {"states": ["off"]}}, SUPPLY["all"][1]]},
        {**SUPPLY, "preset": "a b"},
    ],
)
def test_combo_in_refuses(bad):
    assert signals.combo_in(bad) is None


def test_combos_in_drops_a_bad_one_refuses_over_the_limit():
    got = signals.combos_in({"abcd": SUPPLY, "BAD!": SUPPLY, "efgh": {**SUPPLY, "holdS": -5}})
    assert got == ({"abcd": SUPPLY}, 2)
    assert signals.combos_in({f"c{i:04d}": SUPPLY for i in range(LIM["max"] + 1)}) is None
    assert signals.combos_in([SUPPLY]) is None


def test_signals_in_carries_combos_and_an_older_body_is_the_same():
    base = {"scopes": [], "entities": {}, "findings": {}}
    assert "combos" not in signals.signals_in(base)[0]
    got, dropped = signals.signals_in({**base, "combos": {"abcd": SUPPLY, "x": SUPPLY}})
    assert got["combos"] == {"abcd": SUPPLY} and dropped == 1
    got, dropped = signals.signals_in({**base, "combos": "nope"})
    assert "combos" not in got and dropped == 1


@pytest.mark.parametrize(
    ("states", "want"),
    [
        (["off", "on"], True),
        (["on", "on"], False),
        (["off", "off"], False),
        (["unavailable", "on"], False),  # a lost source is its own signal, never half of a combination
        (["off", "unknown"], False),
        ([None, "on"], False),
    ],
)
def test_combo_holds(states, want):
    assert signals.combo_holds(states, SUPPLY) is want


def test_level_condition_reads_like_a_signal():
    c = {"below": 0.1}
    assert signals.cond_holds("0", c) and signals.cond_holds("0.05", c)
    assert not signals.cond_holds("1.8", c) and not signals.cond_holds("abc", c) and not signals.cond_holds("unavailable", c)


# --- in HA ------------------------------------------------------------------------------------------------------------


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _body(combo=SUPPLY, policy=True, hash_=HASH):
    ents = {EID: {"watch": True, "alarm": {"states": ["on"]}, "severity": "important", "house": "fault", "by": "t", "at": AT}} if policy else {}
    return {"signals": {"scopes": [], "entities": ents, "findings": {}, "combos": {"abcd": combo} if combo else {}}, "hash": hash_}


async def test_feature_is_said(entry):
    from custom_components.hauflo import FEATURES

    assert combos.FEATURE in FEATURES


async def _later(hass, freezer, seconds):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_combo_sensor_holds_then_warns_then_clears(hass: HomeAssistant, entry, hass_client, freezer):
    agent = entry.runtime_data
    hass.states.async_set("fan.supply", "on")
    hass.states.async_set("switch.heater", "on")
    client = await hass_client()
    res = await client.post("/api/hauflo/signals", json=_body())
    assert res.status == 200
    await hass.async_block_till_done()
    st = hass.states.get(EID)
    assert st is not None and st.state == "off" and st.attributes["device_class"] == "problem"
    assert st.attributes["conditions"] == [{"entity_id": "fan.supply", "holds": False}, {"entity_id": "switch.heater", "holds": True}]
    # Watched by the site's own policy for it (the agent's own platform is skipped by class) — every alarm channel.
    assert EID in agent._monitored_ids() and agent.watches(EID)
    # The supply stops: not a warning yet — only after holdS.
    hass.states.async_set("fan.supply", "off")
    await hass.async_block_till_done()
    assert hass.states.get(EID).state == "off" and "since" in hass.states.get(EID).attributes
    await _later(hass, freezer, SUPPLY["holdS"] - 5)
    assert hass.states.get(EID).state == "off"
    await _later(hass, freezer, 6)
    assert hass.states.get(EID).state == "on"
    assert hass.states.get("switch.heater").state == "on" and hass.states.get("fan.supply").state == "off"  # nothing switched
    h = agent.house()
    assert h["_house"] == "fault" and EID in [f["entity"] for f in h["_faults"]]
    # The supply back: off at once.
    hass.states.async_set("fan.supply", "on")
    await hass.async_block_till_done()
    assert hass.states.get(EID).state == "off" and "since" not in hass.states.get(EID).attributes


async def test_short_blip_never_warns(hass: HomeAssistant, entry, hass_client, freezer):
    hass.states.async_set("fan.supply", "on")
    hass.states.async_set("switch.heater", "on")
    client = await hass_client()
    assert (await client.post("/api/hauflo/signals", json=_body())).status == 200
    await hass.async_block_till_done()
    hass.states.async_set("fan.supply", "off")
    await hass.async_block_till_done()
    await _later(hass, freezer, SUPPLY["holdS"] // 2)
    hass.states.async_set("fan.supply", "on")
    await hass.async_block_till_done()
    await _later(hass, freezer, SUPPLY["holdS"] * 2)
    assert hass.states.get(EID).state == "off"


async def test_held_already_is_on_at_once_and_unavailable_holds_nothing(hass: HomeAssistant, entry, hass_client):
    """A combination that has held long already (its sources' last change older than T) is on as soon as it comes; a
    source going unavailable ends it (the lost link is that source's own signal)."""
    hass.states.async_set("fan.supply", "off")
    hass.states.async_set("switch.heater", "on")
    later = dt_util.utcnow() + timedelta(seconds=SUPPLY["holdS"] + 5)
    client = await hass_client()
    with patch("homeassistant.util.dt.utcnow", return_value=later):
        assert (await client.post("/api/hauflo/signals", json=_body())).status == 200
        await hass.async_block_till_done()
        assert hass.states.get(EID).state == "on"
        hass.states.async_set("switch.heater", "unavailable")
        await hass.async_block_till_done()
        assert hass.states.get(EID).state == "off"


async def test_combo_name_in_the_house_language(hass: HomeAssistant, entry, hass_client):
    """w5 acceptance: «Hauflo Combination · Приток…» in a Russian project on an English HA — the name follows the
    project's language (const.site_ru: the alarm engine's `lang`), else HA's."""
    client = await hass_client()
    hass.config.language = "en"
    assert (await client.post("/api/hauflo/signals", json=_body())).status == 200
    await hass.async_block_till_done()
    assert hass.states.get(EID).attributes["friendly_name"] == f"Hauflo Combination · {SUPPLY['name']}"
    entry.runtime_data.engine.config["lang"] = "ru"
    hass.states.async_set("fan.supply", "on")
    await hass.async_block_till_done()
    assert hass.states.get(EID).attributes["friendly_name"] == f"Hauflo Сочетание · {SUPPLY['name']}"


async def test_combo_gone_takes_its_sensor(hass: HomeAssistant, entry, hass_client):
    client = await hass_client()
    assert (await client.post("/api/hauflo/signals", json=_body())).status == 200
    await hass.async_block_till_done()
    reg = er.async_get(hass)
    assert reg.async_get(EID) is not None and reg.async_get(EID).platform == DOMAIN
    # Renamed: the sensor's name follows; another definition starts its time again.
    renamed = {**SUPPLY, "name": "Калорифер без притока"}
    assert (await client.post("/api/hauflo/signals", json=_body(renamed, hash_="1123456789abcdef"))).status == 200
    await hass.async_block_till_done()
    assert "Калорифер без притока" in hass.states.get(EID).attributes["friendly_name"]
    # An older cloud's push (no combos): the combination goes with its sensor.
    assert (await client.post("/api/hauflo/signals", json=_body(None, policy=False, hash_="2123456789abcdef"))).status == 200
    await hass.async_block_till_done()
    assert reg.async_get(EID) is None and hass.states.get(EID) is None


async def test_kept_over_a_restart(hass: HomeAssistant, entry, hass_client):
    client = await hass_client()
    assert (await client.post("/api/hauflo/signals", json=_body())).status == 200
    await hass.async_block_till_done()
    data = signals.store(hass)._data()
    s2 = signals.Signals(hass)

    async def load():
        return data

    s2.store.async_load = load
    await s2.async_load()
    assert s2.combos() == {"abcd": SUPPLY} and s2.hash == HASH


DOOR = "binary_sensor.door_hall"


async def test_combo_in_a_rule_alarms_at_its_t_the_door_beside_keeps_its_minute(hass: HomeAssistant, entry, hass_client, freezer):
    """A combination has held its T already — that is its hold (notify.hold.own «combo»: 0), never T + the rule's minute;
    a door in the same rule still holds its class's minute. Told by what the source is (the site's combinations, the
    registry's own kind), never by its name — in the engine and in HA's own automation alike."""
    from custom_components.hauflo import independent, notify

    engine = entry.runtime_data.engine
    hass.states.async_set("fan.supply", "on")
    hass.states.async_set("switch.heater", "on")
    hass.states.async_set(DOOR, "off", {"device_class": "door", "friendly_name": "Дверь"})
    client = await hass_client()
    assert (await client.post("/api/hauflo/signals", json=_body())).status == 200
    await hass.async_block_till_done()
    await engine.async_set({"rules": [{"id": "r1", "kind": "custom", "title": "Вентиляция", "sources": [EID, DOOR], "picked": [EID, DOOR], "alarm_states": ["on"], "recipients": {"all": True}, "repeat_min": 0}]})
    rule = engine.config["rules"][0]
    assert rule["hold"] == TUNING["notify"]["hold"]["classes"]["door"] and rule["holds"] == {EID: TUNING["notify"]["hold"]["own"]["combo"]} == {EID: 0}
    # HA's own automation: the combination's `for:` is HA's flicker floor, the door's its minute.
    t = await independent.texts(hass, False)
    alarm = [x for x in independent.automation_for(rule, [], t, "input_boolean.a", "input_boolean.o")["triggers"] if x["id"] == "alarm"]
    assert [(x["entity_id"], x["for"]) for x in alarm] == [([EID], {"seconds": independent.HOLD}), ([DOOR], {"seconds": 60})]
    # The supply stops and the door opens at the same moment.
    hass.states.async_set("fan.supply", "off")
    hass.states.async_set(DOOR, "on", {"device_class": "door", "friendly_name": "Дверь"})
    await hass.async_block_till_done()
    await _later(hass, freezer, SUPPLY["holdS"] - 5)
    assert hass.states.get(EID).state == "off" and f"r1|{EID}" not in engine.active
    await _later(hass, freezer, 6)  # T: the combination is on
    assert hass.states.get(EID).state == "on"
    await _later(hass, freezer, notify.NATIVE_HOLD + 1)  # its alarm at T (HA's few seconds against a flicker at most)
    assert f"r1|{EID}" in engine.active, "the combination's alarm must come at T, not T + the rule's minute"
    # The door, open since the start (T + 6 s > its minute): its alarm too; a door opened now holds its minute.
    assert f"r1|{DOOR}" in engine.active
    hass.states.async_set(DOOR, "off", {"device_class": "door"})
    await _later(hass, freezer, 70)
    hass.states.async_set(DOOR, "on", {"device_class": "door"})
    await _later(hass, freezer, 30)
    assert f"r1|{DOOR}" in engine._holding
    await _later(hass, freezer, 35)
    assert f"r1|{DOOR}" not in engine._holding


async def test_own_hold_by_what_the_source_is_not_its_name(hass: HomeAssistant, entry, hass_client):
    """notify.own_kind: a combination of the site's policy, a Hauflo «rule» sensor in the registry (its own «дольше»);
    a look-alike id of another integration, a combination the site no longer has — their class's hold. The site's own
    hold of the combination wins; the rule's own hold_s is for every source."""
    from custom_components.hauflo import notify

    client = await hass_client()
    assert (await client.post("/api/hauflo/signals", json=_body())).status == 200
    await hass.async_block_till_done()
    reg = er.async_get(hass)
    fake = reg.async_get_or_create("binary_sensor", "zha", "x", suggested_object_id="hauflo_combo_zzzz")
    rule_sensor = reg.async_get_or_create("binary_sensor", DOMAIN, "rule_x", suggested_object_id="hauflo_rule_x", translation_key="rule")
    sw = reg.async_get_or_create("switch", DOMAIN, "rule_x_enabled", suggested_object_id="hauflo_rule_x_enabled", translation_key="rule")
    assert notify.own_kind(hass, EID) == "combo" and notify.own_kind(hass, rule_sensor.entity_id) == "rule"
    assert notify.own_kind(hass, fake.entity_id) is None and notify.own_kind(hass, sw.entity_id) is None
    assert notify.own_kind(hass, "binary_sensor.hauflo_combo_gone") is None
    for e in (fake.entity_id, rule_sensor.entity_id):
        hass.states.async_set(e, "off")
    raw = {"rules": [
        {"id": "a", "kind": "custom", "title": "А", "sources": [EID, fake.entity_id, rule_sensor.entity_id], "alarm_states": ["on"], "recipients": {"all": True}},
        {"id": "b", "kind": "custom", "title": "Б", "sources": [EID], "alarm_states": ["on"], "recipients": {"all": True}},
        {"id": "c", "kind": "custom", "title": "В", "sources": [EID], "alarm_states": ["on"], "recipients": {"all": True}, "hold_s": 30},
    ]}
    got = {r["id"]: (r["hold"], r.get("holds")) for r in notify._clean_rules(raw, hass, lambda e: True)["rules"]}
    assert got == {"a": (60, {EID: 0, rule_sensor.entity_id: 0}), "b": (0, {EID: 0}), "c": (30, None)}
    # The site's own hold of that very combination (its «Подробнее»): kept.
    body = _body(hash_="3123456789abcdef")
    body["signals"]["entities"][EID]["holdS"] = 15
    assert (await client.post("/api/hauflo/signals", json=body)).status == 200
    await hass.async_block_till_done()
    got = {r["id"]: (r["hold"], r.get("holds")) for r in notify._clean_rules(raw, hass, lambda e: True)["rules"]}
    assert got["b"] == (15, {EID: 15})
