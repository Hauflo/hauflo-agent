"""«Правила по значениям» (rules.py, docs/automations.md) on a real HA: the level-based state machine (hysteresis, for-N
both ways, conditions, restart), every helper of the design (min on/off by purpose, climate gap, rate cap, bus cap,
unavailable → safe state + Repairs, manual hold + pause, interlock), what a save refuses (loops, conflicts, targets,
limits), who may do what (integrator / client / restricted profile / the tunnel under the owner's lock), the dry run.
Targets are KNX-like entities Hauflo made; their service calls are recorded and applied to their state."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from homeassistant.core import Context, HomeAssistant, ServiceCall, StateMachine
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hauflo as hauflo
from custom_components.hauflo import rules as rules_mod
from custom_components.hauflo.canon import CANON
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN
from custom_components.hauflo.house import house

URL = "/api/hauflo/rules"
OUT = "input_number.outdoor"
SUPPLY = "input_boolean.supply"
HEATER = "switch.heater"
HEATER2 = "switch.heater2"
ROOM = "climate.room"
FLOOR = "climate.floor"
VENT = "fan.vent"
HALL = "light.hall"
BLIND = "cover.blind"
VALVE = "valve.water"
LOCK = "lock.door"
GARAGE = "cover.garage"
FOREIGN = "switch.not_ours"
LEAK = "binary_sensor.leak"
ACTIVE = "binary_sensor.hauflo_value_rule_kalorifer_po_ulitse"
SWITCH = "switch.hauflo_value_rule_kalorifer_po_ulitse"
OWNED = {HEATER: "u-heater", HEATER2: "u-heater2", ROOM: "u-room", FLOOR: "u-floor", VENT: "u-vent", HALL: "u-hall", BLIND: "u-blind", VALVE: "u-valve", LOCK: "u-lock", GARAGE: "u-garage", "sensor.room_t": "u-room-t", LEAK: "u-leak"}


class Clock:
    """The rules' clock; every state set in a test is stamped with it (last_changed: the minimum on / off times)."""

    def __init__(self) -> None:
        self.t = time.time()

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


async def _none() -> None:
    return None


@pytest.fixture
def clock():
    c = Clock()
    real = StateMachine.async_set

    def stamped(self, entity_id, new_state, attributes=None, force_update=False, context=None, state_info=None, timestamp=None):
        return real(self, entity_id, new_state, attributes, force_update, context, state_info, c() if timestamp is None else timestamp)

    with patch("custom_components.hauflo.derived.now", c), patch.object(StateMachine, "async_set", stamped):
        yield c


@pytest.fixture
async def entry(hass: HomeAssistant, clock):
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    for eid, uid in (*OWNED.items(), (FOREIGN, "u-foreign")):
        reg.async_get_or_create(eid.split(".")[0], "knx", uid, suggested_object_id=eid.split(".")[1])
    reg.async_update_entity(GARAGE, device_class="garage")
    hass.states.async_set(OUT, "8.0", {"unit_of_measurement": "°C", "friendly_name": "Улица"})
    hass.states.async_set(SUPPLY, "on")
    for eid in (HEATER, HEATER2, FOREIGN):
        hass.states.async_set(eid, "off")
    hass.states.async_set(ROOM, "heat", {"temperature": 21.0, "current_temperature": 20.0, "hvac_modes": ["heat", "off"]})
    hass.states.async_set(FLOOR, "heat", {"temperature": 27.0, "hvac_modes": ["heat", "off"]})
    hass.states.async_set(VENT, "off", {"percentage": 0})
    hass.states.async_set(HALL, "off")
    hass.states.async_set(BLIND, "open", {"current_position": 100})
    hass.states.async_set(VALVE, "open")
    hass.states.async_set(LOCK, "locked")
    hass.states.async_set(GARAGE, "closed", {"device_class": "garage"})
    hass.states.async_set(LEAK, "off", {"device_class": "moisture"})
    hass.states.async_set("sensor.room_t", "22.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    with patch("custom_components.hauflo.Agent.run", lambda self: _none()):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.owned |= set(OWNED.values())
    clock.advance(1000)  # the devices have been as they are for a while (their minimum on / off times are over)
    yield entry


@pytest.fixture
def calls(hass: HomeAssistant, entry):
    made: list[tuple[str, str, str, dict]] = []

    async def handler(call: ServiceCall) -> None:
        eid = call.data["entity_id"]
        eid = eid[0] if isinstance(eid, list) else eid
        if eid.startswith("switch.hauflo_value_rule_"):  # the rule's own switch still reaches the agent
            rid = next(r for r, e in entry.runtime_data.rules.switches.items() if e.entity_id == eid)
            await entry.runtime_data.rules.set_enabled(rid, call.service == "turn_on")
            return
        made.append((call.domain, call.service, eid, {k: v for k, v in call.data.items() if k != "entity_id"}))
        s = hass.states.get(eid)
        attrs = dict(s.attributes) if s else {}
        state = s.state if s else "off"
        if call.service == "turn_on":
            state = "on" if call.domain != "climate" else "heat"
            if "brightness_pct" in call.data:
                attrs["brightness"] = round(call.data["brightness_pct"] * 255 / 100)
        elif call.service == "turn_off":
            state = "off"
        elif call.service == "set_temperature":
            attrs["temperature"] = call.data["temperature"]
        elif call.service == "set_hvac_mode":
            state = call.data["hvac_mode"]
        elif call.service == "set_percentage":
            state, attrs["percentage"] = "on", call.data["percentage"]
        elif call.service == "close_valve":
            state = "closed"
        elif call.service in ("set_cover_position", "open_cover", "close_cover"):
            pos = call.data.get("position", 100 if call.service == "open_cover" else 0)
            attrs["current_position"] = pos
            state = "open" if pos else "closed"
        hass.states.async_set(eid, state, attrs, context=call.context)

    for domain, services in {
        "switch": ("turn_on", "turn_off"), "light": ("turn_on", "turn_off"), "fan": ("turn_on", "turn_off", "set_percentage"),
        "climate": ("turn_on", "turn_off", "set_temperature", "set_hvac_mode"), "valve": ("close_valve", "open_valve"),
        "cover": ("open_cover", "close_cover", "set_cover_position"), "scene": ("turn_on",),
    }.items():
        for s in services:
            hass.services.async_register(domain, s, handler)
    return made


def heater(**kw):
    rule = {"id": "heater", "name": "Калорифер по улице", "purpose": "heater", "input": {"entity_id": OUT}, "when": "below", "x": 5, "y": 2, "for_min": 10, "back_min": 5, "actions": [{"do": "on", "entity_id": HEATER}]}
    return {**rule, **kw}


async def _post(client, body, status=200):
    res = await client.post(URL, json=body)
    got = await res.json()
    assert res.status == status, got
    return got


def _engine(entry) -> rules_mod.Rules:
    return entry.runtime_data.rules


async def _value(hass, v, eid=OUT, **attrs):
    hass.states.async_set(eid, v, {"unit_of_measurement": "°C", "friendly_name": "Улица", **attrs})
    await hass.async_block_till_done()


def _writes(calls, eid=HEATER):
    return [(s, d) for _dom, s, e, d in calls if e == eid]


async def _settle(hass, engine, clock, s):
    clock.advance(s)
    await engine._tick()
    await hass.async_block_till_done()


# --- the state machine ---------------------------------------------------------------------------------------------------


async def test_threshold_hysteresis_and_for_n_both_ways(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    client = await hass_client()
    await _value(hass, "8.0")
    got = await _post(client, {"op": "save", "rule": heater()})
    assert got["state"] == "normal" and got["entities"] == {"active": ACTIVE, "switch": SWITCH}
    assert "Hauflo" in got["hint_en"] and "защищает" in got["hint"]  # «помогает, а не защищает»
    engine = _engine(entry)
    assert hass.states.get(ACTIVE).state == "off" and hass.states.get(SWITCH).state == "on"
    await _value(hass, "4.0")  # below X: pending for 10 min, nothing yet
    assert engine.status("heater") == "pending" and not _writes(calls)
    assert "10 min" in engine.explain(engine.rules["heater"], False)
    await _settle(hass, engine, clock, 300)
    await _value(hass, "6.0")  # back above X before N: the for-N starts over
    assert engine.status("heater") == "normal"
    await _value(hass, "4.5")
    await _settle(hass, engine, clock, 599)
    assert not _writes(calls)
    await _settle(hass, engine, clock, 2)
    assert _writes(calls) == [("turn_on", {})] and hass.states.get(ACTIVE).state == "on"
    assert hass.states.get(ACTIVE).attributes["state"] == "active"
    # Inside the band (X … X + Y): stays ACTIVE however long.
    await _value(hass, "6.5")
    await _settle(hass, engine, clock, 3600)
    assert engine.st("heater")["active"] and engine.st("heater")["pend"] is None
    assert "back when 7 °C or more" in engine.explain(engine.rules["heater"], False)  # the band said, not «below 5»
    assert "7 °C и выше" in engine.explain(engine.rules["heater"], True)
    # Past X + Y: back to NORMAL only after M (5 min) — the heater's min on time (5 min) is long over.
    await _value(hass, "7.5")
    await _settle(hass, engine, clock, 200)
    assert engine.st("heater")["active"]
    await _settle(hass, engine, clock, 101)
    assert not engine.st("heater")["active"] and _writes(calls)[-1] == ("turn_off", {})
    log = (await (await client.get(f"{URL}?log=heater")).json())["log"]
    assert [e["event"] for e in log if e["event"] in ("active", "normal", "write")] == ["active", "write", "normal", "write"]


async def test_level_based_at_start_and_restart_keeps_state(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    """Already past X when saved: no crossing needed. A reload (HA restart) keeps ACTIVE and doesn't write again."""
    await _value(hass, "-8.0")
    client = await hass_client()
    await _post(client, {"op": "save", "rule": heater(for_min=0)})
    assert _writes(calls) == [("turn_on", {})]
    engine = _engine(entry)
    assert engine.st("heater")["active"]
    await hass.async_block_till_done()
    with patch("custom_components.hauflo.Agent.run", lambda self: _none()):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.owned |= set(OWNED.values())  # the fixture's «made by Hauflo», not in the agent's store
    engine = _engine(entry)
    await engine.async_evaluate_all("start")
    await hass.async_block_till_done()
    assert engine.st("heater")["active"] and hass.states.get(ACTIVE).state == "on"
    assert _writes(calls) == [("turn_on", {})]  # nothing re-sent
    # Pending times are wall-clock: a pending NORMAL survives the restart too.
    await _value(hass, "9.0")
    pend = engine.st("heater")["pend"]
    assert pend is not None
    await hass.async_block_till_done()
    with patch("custom_components.hauflo.Agent.run", lambda self: _none()):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.owned |= set(OWNED.values())  # the fixture's «made by Hauflo», not in the agent's store
    engine = _engine(entry)
    assert engine.st("heater")["pend"] == pend
    await _settle(hass, engine, clock, 301)
    assert not engine.st("heater")["active"] and _writes(calls)[-1] == ("turn_off", {})


async def test_unavailable_input_safe_state_and_repairs(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    await _value(hass, "-3.0")
    await _post(await hass_client(), {"op": "save", "rule": heater(for_min=0)})
    engine = _engine(entry)
    assert hass.states.get(HEATER).state == "on"
    clock.advance(10)  # well inside the heater's 5 min minimum on time — a safe «off» isn't held by it
    hass.states.async_set(OUT, "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get(HEATER).state == "off" and engine.status("heater") == "fault"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "rule_fault_heater")
    assert issue is not None and issue.translation_key == "rule_fault" and issue.translation_placeholders["entity"] == OUT
    assert "safe state" in engine.explain(engine.rules["heater"], False)
    # Unknown / not a number / another unit: the same, never an action on an old value.
    for bad, attrs in (("unknown", {}), ("abc", {}), ("40", {"unit_of_measurement": "%"})):
        hass.states.async_set(OUT, bad, attrs)
        await hass.async_block_till_done()
        assert engine.status("heater") == "fault", bad
    await _value(hass, "-3.0")
    assert ir.async_get(hass).async_get_issue(DOMAIN, "rule_fault_heater") is None
    assert engine.status("heater") == "pending" or engine.st("heater")["active"]
    # A frost rule says it instead (a push via the notify engine), once per fault.
    told = []

    async def tell(title, text, user):
        told.append(text)

    engine.tell = tell
    await _post(await hass_client(), {"op": "save", "rule": heater(id="frost", name="Мороз", purpose="frost", actions=[{"do": "on", "entity_id": HEATER2}], interlock=None)})
    hass.states.async_set(OUT, "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set(OUT, "unknown")
    await hass.async_block_till_done()
    assert len([t for t in told if "no data" in t]) == 1


async def test_not_heard_yet_waits_then_safe_state_and_unknown_target_is_written(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    """After a restart a KNX sensor is «unknown» until the bus answers: no fault (no Repairs, no forced off) inside the
    canon's grace; a write-only actuator not heard yet («unknown») is still switched."""
    grace = rules_mod.C("engine", "unknown_grace_s")
    hass.states.async_set(OUT, "unknown", {"unit_of_measurement": "°C"})  # HA keeps the unit while «unknown»
    hass.states.async_set(HEATER, "unknown")
    await hass.async_block_till_done()
    await _post(await hass_client(), {"op": "save", "rule": heater(for_min=0)})
    engine = _engine(entry)
    assert engine.status("heater") != "fault" and ir.async_get(hass).async_get_issue(DOMAIN, "rule_fault_heater") is None
    await _value(hass, "-3.0")  # heard: the rule acts on the «unknown» heater
    assert _writes(calls)[-1][0] == "turn_on" and hass.states.get(HEATER).state == "on"
    hass.states.async_set(OUT, "unknown", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    await _settle(hass, engine, clock, grace / 2)
    assert engine.status("heater") != "fault" and hass.states.get(HEATER).state == "on"
    await _settle(hass, engine, clock, grace)
    assert engine.status("heater") == "fault" and hass.states.get(HEATER).state == "off"
    assert ir.async_get(hass).async_get_issue(DOMAIN, "rule_fault_heater") is not None


async def test_manual_hold_then_pause(hass: HomeAssistant, hass_client, hass_admin_user, entry, calls, clock) -> None:
    await _value(hass, "-3.0")
    await _post(await hass_client(), {"op": "save", "rule": heater(for_min=0)})
    engine = _engine(entry)
    # The bus echoing our own «on» (no context of ours, same value, within the echo time): not a person.
    hass.states.async_set(HEATER, "on", {"x": 1})
    await hass.async_block_till_done()
    assert engine.status("heater") == "active"
    # A person switches it off in HA: the rule leaves it for 60 min or until NORMAL.
    clock.advance(400)
    hass.states.async_set(HEATER, "off", context=Context(user_id=hass_admin_user.id))
    await hass.async_block_till_done()
    st = engine.st("heater")
    assert engine.status("heater") == "hold" and st["hold_why"] == "manual"
    why = engine.explain(engine.rules["heater"], True)
    assert why.startswith("Вручную — вернётся в ") and engine._hhmm(st["hold_until"]) in why
    await _settle(hass, engine, clock, 60)
    assert hass.states.get(HEATER).state == "off"  # not argued with
    # The hold is over: the rule (still ACTIVE) puts the heater back on.
    await _settle(hass, engine, clock, 3600)
    assert hass.states.get(HEATER).state == "on" and engine.status("heater") == "active"
    # Until NORMAL: a hold ends with the episode, its end isn't undone on the person.
    hass.states.async_set(HEATER, "off", context=Context(user_id=hass_admin_user.id))
    await hass.async_block_till_done()
    n = len(_writes(calls))
    await _value(hass, "9.0")
    await _settle(hass, engine, clock, 301)
    assert engine.status("heater") == "normal" and len(_writes(calls)) == n
    # Three holds in 6 h: paused + a Repairs warning; switched on again — clean.
    await _value(hass, "-3.0")
    await _settle(hass, engine, clock, 601)
    hass.states.async_set(HEATER, "off", context=Context(user_id=hass_admin_user.id))
    await hass.async_block_till_done()
    assert engine.st("heater")["paused"] and engine.status("heater") == "paused"
    assert ir.async_get(hass).async_get_issue(DOMAIN, "rule_paused_heater") is not None
    await hass.services.async_call("switch", "turn_off", {"entity_id": SWITCH}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": SWITCH}, blocking=True)
    await hass.async_block_till_done()
    assert not engine.st("heater")["paused"] and ir.async_get(hass).async_get_issue(DOMAIN, "rule_paused_heater") is None


async def test_min_on_off_times_by_purpose(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    await _value(hass, "-3.0")
    await _post(await hass_client(), {"op": "save", "rule": heater(for_min=0, back_min=0)})
    engine = _engine(entry)
    assert hass.states.get(HEATER).state == "on"
    await _value(hass, "10.0")  # NORMAL at once (M = 0) — but the heater ran < 5 min: its «off» waits
    assert not engine.st("heater")["active"] and hass.states.get(HEATER).state == "on"
    assert engine.todo and engine.todo[0]["why"] == "min_time"
    await _settle(hass, engine, clock, 200)
    assert hass.states.get(HEATER).state == "on"
    await _settle(hass, engine, clock, 101)
    assert hass.states.get(HEATER).state == "off" and not engine.todo
    # Off < 5 min: an «on» waits the minimum off time too; a deferred write no longer wanted is dropped.
    await _value(hass, "-3.0")
    assert engine.todo and hass.states.get(HEATER).state == "off"
    assert "pause between switches" in engine.explain(engine.rules["heater"], False)  # not «working» while it waits
    await _value(hass, "10.0")
    await _settle(hass, engine, clock, 400)
    assert hass.states.get(HEATER).state == "off"


async def test_min_times_count_from_the_real_switch_over_a_restart(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    """HA's last_changed restarts with HA: the minimum on time counts from the heater's real switch, kept in the store."""
    await _value(hass, "-3.0")
    await _post(await hass_client(), {"op": "save", "rule": heater(for_min=0, back_min=0)})
    assert hass.states.get(HEATER).state == "on"
    t_on = clock()
    clock.advance(200)
    with patch("custom_components.hauflo.Agent.run", lambda self: _none()):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.owned |= set(OWNED.values())
    hass.states.async_remove(HEATER)
    hass.states.async_set(HEATER, "on")  # restored by HA: last_changed = now
    await hass.async_block_till_done()
    engine = _engine(entry)
    assert engine.switched[HEATER] == pytest.approx(t_on)
    await _value(hass, "10.0")  # NORMAL: the «off» waits only the rest of the 5 min since the real switch
    assert engine.todo and engine.todo[0]["due"] == pytest.approx(t_on + 300)
    await _settle(hass, engine, clock, 101)
    assert hass.states.get(HEATER).state == "off"


async def test_rate_cap_holds_and_says_it(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    await _post(await hass_client(), {"op": "save", "rule": heater(for_min=0, back_min=0, purpose="other")})
    engine = _engine(entry)
    for i in range(7):
        await _value(hass, "-3.0" if i % 2 == 0 else "10.0")
        clock.advance(60)
    assert len(_writes(calls)) == 6
    st = engine.st("heater")
    assert st["hold_why"] == "rate" and ir.async_get(hass).async_get_issue(DOMAIN, "rule_rate_heater") is not None
    assert "Too many switches" in engine.explain(engine.rules["heater"], False)
    # The hour is over while NORMAL: the episode's end is done then (no heater left on).
    await _value(hass, "10.0")
    await _settle(hass, engine, clock, 3600)
    assert st["hold_until"] is None and ir.async_get(hass).async_get_issue(DOMAIN, "rule_rate_heater") is None
    assert hass.states.get(HEATER).state == "off"


async def test_climate_gap_and_reverse_restores_the_setpoint(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    rule = heater(id="win", name="Окно", purpose="comfort", input={"entity_id": LEAK}, when="on", y=0, for_min=0, back_min=0, actions=[{"do": "setpoint", "entity_id": ROOM, "value": 16}])
    rule["input"] = {"entity_id": "binary_sensor.window"}
    hass.states.async_set("binary_sensor.window", "off")
    await _post(await hass_client(), {"op": "save", "rule": rule})
    engine = _engine(entry)
    hass.states.async_set("binary_sensor.window", "on")
    await hass.async_block_till_done()
    assert _writes(calls, ROOM) == [("set_temperature", {"temperature": 16.0})]
    hass.states.async_set("binary_sensor.window", "off")
    await hass.async_block_till_done()
    # A climate is written at most once in 5 min: the restore (21°, the value before) waits.
    assert len(_writes(calls, ROOM)) == 1 and engine.todo[0]["why"] == "climate_gap"
    await _settle(hass, engine, clock, 301)
    assert _writes(calls, ROOM)[-1] == ("set_temperature", {"temperature": 21.0})


async def test_bus_cap(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    with patch.dict(CANON["helpers"], {"bus_cap": 1}):
        await _value(hass, "-3.0")
        await _post(await hass_client(), {"op": "save", "rule": heater(for_min=0, purpose="other", actions=[{"do": "on", "entity_id": HEATER}, {"do": "on", "entity_id": HEATER2}])})
        engine = _engine(entry)
        assert hass.states.get(HEATER).state == "on" and hass.states.get(HEATER2).state == "off"
        assert engine.todo[0]["why"] == "bus"
        await _settle(hass, engine, clock, 61)
        assert hass.states.get(HEATER2).state == "on"


async def test_interlock_supply_fan(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    told = []

    async def tell(title, text, user):
        told.append(text)

    _engine(entry).tell = tell
    hass.states.async_set(SUPPLY, "off")
    await _value(hass, "-3.0")
    await _post(await hass_client(), {"op": "save", "rule": heater(for_min=0, interlock=SUPPLY)})
    engine = _engine(entry)
    assert hass.states.get(HEATER).state == "off" and told and "hint" in told[0]  # «подсказка Hauflo, не защита»
    hass.states.async_set(SUPPLY, "on")
    await hass.async_block_till_done()
    assert hass.states.get(HEATER).state == "on"  # the fan is back and it's still cold: the held «on» is done (level-based)
    assert [e["event"] for e in engine.log_of("heater")][-2:] == ["interlock_back", "write"]
    # The supply fan stops: the heater goes off at once (not held by its min on time), said.
    hass.states.async_set(SUPPLY, "off")
    await hass.async_block_till_done()
    assert hass.states.get(HEATER).state == "off"
    # Somebody switches it on by hand meanwhile: off again.
    clock.advance(400)
    hass.states.async_set(HEATER, "on", context=Context())
    await hass.async_block_till_done()
    assert hass.states.get(HEATER).state == "off"
    # The fan is back within the heater's min off time: the «on» waits; the fan stops again before it's due — held back,
    # said; the fan back once more — on.
    hass.states.async_set(SUPPLY, "on")
    await hass.async_block_till_done()
    assert hass.states.get(HEATER).state == "off" and engine.todo
    hass.states.async_set(SUPPLY, "off")
    await hass.async_block_till_done()
    await _settle(hass, engine, clock, 301)
    assert hass.states.get(HEATER).state == "off" and not engine.todo and engine.log_of("heater")[-1]["result"] == "interlock"
    assert "supply fan is off" in engine.explain(engine.rules["heater"], False)  # not «working» while held back
    hass.states.async_set(SUPPLY, "on")
    await hass.async_block_till_done()
    assert hass.states.get(HEATER).state == "on"
    with pytest.raises(rules_mod.Invalid):
        rules_mod.clean_rule(heater(purpose="fan", interlock=SUPPLY), engine._env(False))


async def test_conditions_and_only_if_ending(hass: HomeAssistant, hass_client, entry, calls, clock, freezer) -> None:
    await hass.config.async_update(time_zone="UTC")
    engine = _engine(entry)
    client = await hass_client()
    hass.states.async_set("person.anna", "not_home")
    await _value(hass, "-3.0")
    cond = [{"type": "season", "mode": "winter"}, {"type": "presence", "home": False}, {"type": "value", "entity_id": "sensor.room_t", "when": "below", "x": 25}, {"type": "state", "entity_id": SUPPLY, "on": True}]
    await _post(client, {"op": "save", "rule": heater(for_min=0, only_if=cond)})
    assert engine.st("heater")["active"]
    hass.states.async_set("person.anna", "home")  # «только если» ends: NORMAL at once (back_min not waited)
    await engine.evaluate("heater")
    assert engine.status("heater") == "waiting" and not engine.st("heater")["active"]
    assert "only if" in engine.explain(engine.rules["heater"], False)
    hass.states.async_set("person.anna", "not_home")
    rule = engine.rules["heater"]
    freezer.move_to("2026-10-28 10:00:00+00:00")  # a Wednesday (no HTTP after this: the token would be from the future)
    for c, ok in (
        ({"type": "time", "after": "09:00", "before": "11:00", "days": ["wed"]}, True),
        ({"type": "time", "after": "09:00", "before": "11:00", "days": ["thu"]}, False),
        ({"type": "time", "after": "22:00", "before": "02:00"}, False),
        ({"type": "season", "mode": "summer"}, False),
        ({"type": "value", "entity_id": "sensor.room_t", "when": "above", "x": 25}, False),
        ({"type": "value", "entity_id": "sensor.none", "when": "above", "x": 25}, False),
        ({"type": "state", "entity_id": SUPPLY, "on": False}, False),
    ):
        assert engine.conditions({**rule, "only_if": [rules_mod.clean_condition(c)]})[0] is ok, c
    freezer.move_to("2026-10-29 00:30:00+00:00")  # Thursday 00:30 — the night window started on Wednesday
    night = rules_mod.clean_condition({"type": "time", "after": "22:00", "before": "02:00", "days": ["wed"]})
    assert engine.conditions({**rule, "only_if": [night]})[0] is True
    for bad in ({"type": "time", "after": "25:00", "before": "02:00"}, {"type": "time", "after": "01:00", "before": "02:00", "days": ["xx"]}, {"type": "season", "mode": "spring"}, {"type": "value", "entity_id": "sensor.x", "when": "near", "x": 1}, {"type": "nope"}, "x"):
        with pytest.raises(rules_mod.Invalid):
            rules_mod.clean_condition(bad)


# --- what a save refuses ------------------------------------------------------------------------------------------------


async def test_loops_conflicts_targets_and_limits_refused(hass: HomeAssistant, hass_client, entry, calls) -> None:
    client = await hass_client()
    engine = _engine(entry)

    async def refused(rule, code, key=None):
        got = await _post(client, {"op": "save", "rule": rule}, 400)
        assert got["code"] == code, got
        if key:
            assert got["key"] == key, got
        assert got["message"] and got["message_ru"] and got["message"] != got["key"]
        return got

    # A target never decides its own rule — directly, through a condition, through another rule.
    await refused(heater(input={"entity_id": ROOM}, actions=[{"do": "setpoint", "entity_id": ROOM, "value": 20}], x=19, y=1), "loop", "rules_loop")
    await refused(heater(only_if=[{"type": "state", "entity_id": HEATER}]), "loop")
    hass.states.async_set("sensor.heater_power", "0", {"unit_of_measurement": "W"})
    await _post(client, {"op": "save", "rule": heater(id="a", name="A", input={"entity_id": HEATER}, when="on", y=0, actions=[{"do": "on", "entity_id": VENT}])})
    hass.states.async_set("fan.vent", "off")
    got = await refused(heater(id="b", name="B", input={"entity_id": "fan.vent"}, when="on", x=None, y=0, actions=[{"do": "on", "entity_id": "switch.heater"}]), "loop")
    assert got["key"] in ("rules_loop_chain", "rules_loop")
    # One rule per target attribute of one priority; safety and comfort may share — safety wins at run time.
    await _post(client, {"op": "save", "rule": heater()})
    got = await refused(heater(id="dup", name="Dup"), "conflict", "rules_conflict")
    assert "Калорифер по улице" in got["message"]
    await _post(client, {"op": "save", "rule": heater(id="frost", name="Мороз", purpose="frost", x=-10)})
    # Targets: Hauflo's own only; never a lock / garage; a valve only closes.
    for bad, code in (
        ({"do": "on", "entity_id": FOREIGN}, "target_not_allowed"),
        ({"do": "on", "entity_id": "switch.nowhere"}, "target_not_allowed"),
        ({"do": "position", "entity_id": GARAGE, "value": 100}, "target_not_allowed"),
        ({"do": "on", "entity_id": LOCK}, "bad_action"),
        ({"do": "open", "entity_id": VALVE}, "bad_action"),
        ({"do": "on", "entity_id": VALVE}, "bad_action"),
        ({"do": "setpoint", "entity_id": ROOM, "value": 99}, "bad_action"),
        ({"do": "mode", "entity_id": ROOM, "value": "boost"}, "bad_action"),
        ({"do": "notify", "text": ""}, "bad_action"),
    ):
        await refused(heater(id="t", name="T", purpose="other", actions=[bad]), code)
    # Limits (canon): actions, conditions, for N, Y within the span, a contact without a margin.
    lim = CANON["limits"]
    await refused(heater(id="t", name="T", actions=[{"do": "on", "entity_id": HEATER2}] * (lim["actions"] + 1)), "limit")
    await refused(heater(id="t", name="T", only_if=[{"type": "presence"}] * (lim["conditions"] + 1)), "limit")
    await refused(heater(id="t", name="T", for_min=lim["for_min"] + 1), "bad_time")
    await refused(heater(id="t", name="T", y=-1), "bad_hysteresis")
    await refused(heater(id="t", name="T", x=5, y=10), "bad_hysteresis")
    await refused(heater(id="t", name="T", when="outside", lo=18, hi=22, y=2), "bad_hysteresis")
    await refused(heater(id="t", name="T", when="between", lo=22, hi=18), "bad_range")
    await refused(heater(id="t", name="T", input={"entity_id": LEAK}, when="on", y=1), "bad_hysteresis")
    await refused(heater(id="t", name="T", input={"entity_id": LEAK}, when="below"), "bad_when")
    await refused(heater(id="t", name="T", when="near"), "bad_when")
    await refused(heater(id="t", name="T", input={"entity_id": "sensor.nope"}), "no_entity")
    await refused(heater(id="t", name="T", input={"entity_id": "lock.door"}), "bad_input")
    await refused(heater(id="t", name="T", input="nothing"), "bad_input")
    await refused(heater(id="T!", name="T"), "bad_id")
    await refused(heater(id="t", name=""), "bad_name")
    await refused(heater(id="t", name="T", purpose="rocket"), "bad_purpose")
    await refused(heater(id="t", name="T", priority="max"), "bad_priority")
    await refused(heater(id="t", name="T", back="sometimes"), "bad_back")
    await refused(heater(id="t", name="T", x="warm"), "bad_number")
    await refused(heater(id="t", name="T", client={"lo": 5, "hi": 1}), "bad_range")
    await refused(heater(id="t", name="T", input={"entity_id": OUT, "attribute": "Bad Attr"}), "bad_input")
    with patch.dict(CANON["limits"], {"rules": len(engine.rules)}):
        await refused(heater(id="more", name="More", actions=[{"do": "on", "entity_id": HEATER2}]), "limit", "rules_limit_rules")
    assert set(engine.rules) == {"a", "heater", "frost"}
    assert (await _post(client, {"op": "save", "rule": "x"}, 400))["code"] == "bad"


async def test_safety_beats_comfort_at_run_time_and_leak_never_reopens(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    client = await hass_client()
    engine = _engine(entry)
    await _post(client, {"op": "save", "rule": heater(for_min=0, back_min=0, purpose="other")})
    await _post(client, {"op": "save", "rule": heater(id="frost", name="Мороз", purpose="frost", for_min=0, back_min=0, x=-10, actions=[{"do": "on", "entity_id": HEATER}])})
    await _value(hass, "-15.0")
    assert engine.st("frost")["active"] and engine.st("heater")["active"]
    await _value(hass, "-9.0")  # inside frost's band (−10 … −8): both still ACTIVE
    await engine._write(engine.rules["heater"], {"do": "off", "entity_id": HEATER}, why="test")
    assert engine.st("heater")["log"][-1]["result"] == "priority:frost"
    # Leak: the valve closes, a push; back to normal — the valve stays shut (back «nothing», whatever was asked).
    told = []

    async def tell(title, text, user):
        told.append((title, text, user))

    engine.tell = tell
    await _post(client, {"op": "save", "rule": {"id": "leak", "name": "Протечка", "purpose": "leak", "input": {"entity_id": LEAK}, "when": "on", "actions": [{"do": "close", "entity_id": VALVE}, {"do": "notify", "text": "Протечка! Кран закрыт."}], "back": "reverse"}})
    assert engine.rules["leak"]["back"] == "nothing" and engine.rules["leak"]["priority"] == "safety"
    hass.states.async_set(LEAK, "on")
    await hass.async_block_till_done()
    assert hass.states.get(VALVE).state == "closed" and told == [("Протечка", "Протечка! Кран закрыт.", None)]
    hass.states.async_set(LEAK, "off")
    await hass.async_block_till_done()
    assert hass.states.get(VALVE).state == "closed" and not [c for c in calls if c[1] == "open_valve"]


async def test_targets_rechecked_at_run_time(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    """A target that stopped being Hauflo's (taken over by the integrator) is never written, whatever was saved."""
    await _post(await hass_client(), {"op": "save", "rule": heater(for_min=0)})
    entry.runtime_data.owned.discard("u-heater")
    await _value(hass, "-3.0")
    engine = _engine(entry)
    assert not _writes(calls) and engine.st("heater")["log"][-1]["result"] == "not_allowed"
    assert not await engine._write(engine.rules["heater"], {"do": "open", "entity_id": VALVE}, why="x")


# --- who may do what --------------------------------------------------------------------------------------------------------


async def test_client_rights_and_restricted_profile(hass: HomeAssistant, hass_client, hass_read_only_user, hass_read_only_access_token, entry, calls) -> None:
    admin = await hass_client()
    kid = await hass_client(hass_read_only_access_token)
    await _post(admin, {"op": "save", "rule": heater(client={"toggle": True, "lo": 0, "hi": 8})})
    await _post(admin, {"op": "save", "rule": heater(id="hidden", name="Скрытое", x=-20, actions=[{"do": "on", "entity_id": HEATER2}], client={"visible": False, "toggle": False})})
    seen = await (await kid.get(URL)).json()
    assert [r["id"] for r in seen["rules"]] == ["heater"] and "rule" not in seen["rules"][0] and seen["derived"] == [] and seen["recipes"] == []
    assert (await kid.get(f"{URL}?log=hidden")).status == 404
    # Tunes X inside the integrator's range; outside it — refused; toggles if allowed.
    got = await _post(kid, {"op": "tune", "id": "heater", "x": 7})
    assert got["x"] == 7
    assert (await _post(kid, {"op": "tune", "id": "heater", "x": 9}, 400))["code"] == "out_of_range"
    assert (await _post(kid, {"op": "enable", "id": "heater", "enabled": False}))["enabled"] is False
    assert (await _post(kid, {"op": "enable", "id": "hidden", "enabled": False}, 403))["code"] == "not_allowed"
    assert (await _post(kid, {"op": "delete", "id": "heater"}, 403))["code"] == "not_allowed"
    assert (await _post(kid, {"op": "save", "rule": heater()}, 403))["code"] == "not_allowed"
    assert (await kid.post(URL, json={"op": "derived_save", "derived": {}})).status == 403
    # His own rules: client actions on Hauflo's sensors only.
    assert (await _post(kid, {"op": "save", "rule": heater(id="mine", name="Мой", purpose="other")}, 403))["code"] == "not_allowed"
    assert (await _post(kid, {"op": "save", "rule": heater(id="mine", name="Мой", purpose="light", actions=[{"do": "on", "entity_id": HALL}])}, 403))["key"] == "rules_client_input"
    light = {"id": "mine", "name": "Свет", "purpose": "light", "input": {"entity_id": "sensor.room_t"}, "when": "above", "x": 25, "y": 1, "actions": [{"do": "brightness", "entity_id": HALL, "value": 40}, {"do": "notify", "text": "Жарко", "to": "all"}]}
    got = await _post(kid, {"op": "save", "rule": light})
    assert got["owner"] == "client" and got["rule"]["actions"][1]["to"] == "me" and got["rule"]["user_id"] == hass_read_only_user.id
    assert (await _post(kid, {"op": "tune", "id": "mine", "x": 30}))["x"] == 30
    assert (await _post(kid, {"op": "delete", "id": "mine"}))["deleted"] == "mine"
    # A restricted (child) profile only looks (and may dry-run).
    (await house(hass).load())["restricted"] = [hass_read_only_user.id]
    assert (await kid.post(URL, json={"op": "enable", "id": "heater", "enabled": True})).status == 403
    assert (await _post(kid, {"op": "preview", "rule": light}))["ok"] is True
    assert (await kid.get(URL)).status == 200


async def test_tunnel_reaches_it_and_the_owner_lock_refuses_writes() -> None:
    assert ("/api/hauflo/rules", "GET") in hauflo._HAUFLO_VIEWS and ("/api/hauflo/rules", "POST") in hauflo._HAUFLO_VIEWS
    assert hauflo._http_locked("POST", "/api/hauflo/rules") is True  # the owner closed the house: no rule changes
    assert hauflo._http_locked("GET", "/api/hauflo/rules") is False
    # A dry run only reads: through the lock; every other op — and a broken body — stays locked.
    assert hauflo._http_locked("POST", "/api/hauflo/rules", '{"op": "preview", "rule": {}}') is False
    for body in ('{"op": "save", "rule": {}}', '{"op": "tune", "id": "x"}', '{"op": "delete"}', "not json", '["preview"]', None, ""):
        assert hauflo._http_locked("POST", "/api/hauflo/rules", body) is True, body
    assert hauflo._http_locked("POST", "/api/hauflo/rules?x=1", '{"op": "preview"}') is False
    assert hauflo._http_locked("POST", "/api/hauflo/sidebar", '{"op": "preview"}') is True


async def test_rules_keep_running_while_the_house_is_locked(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    await _post(await hass_client(), {"op": "save", "rule": heater(for_min=0)})
    hass.config_entries.async_update_entry(entry, options={**entry.options, "allow_changes": False})
    await hass.async_block_till_done()
    assert entry.runtime_data.locked
    await _value(hass, "-3.0")
    assert _writes(calls) == [("turn_on", {})]


# --- API, dry run, entities ---------------------------------------------------------------------------------------------------


async def test_preview_is_a_dry_run(hass: HomeAssistant, hass_client, entry, calls) -> None:
    client = await hass_client()
    await _value(hass, "-3.0")
    got = await _post(client, {"op": "preview", "rule": heater()})
    assert got["ok"] and got["now"] == "wait" and got["value"] == -3.0 and "10 min" in got["why_en"] and "10 мин" in got["why"]
    assert got["actions"][0]["current"] == "off" and got["actions"][0]["already"] is False
    assert (await _post(client, {"op": "preview", "rule": heater(for_min=0)}))["now"] == "now"
    await _value(hass, "8.0")
    assert (await _post(client, {"op": "preview", "rule": heater()}))["now"] == "normal_for"
    assert (await _post(client, {"op": "preview", "rule": heater(for_min=0)}))["now"] == "normal"
    assert (await _post(client, {"op": "preview", "rule": heater(only_if=[{"type": "season", "mode": "summer"}])}))["now"] == "waiting"
    hass.states.async_set(OUT, "unavailable")
    assert (await _post(client, {"op": "preview", "rule": heater()}))["now"] == "fault"
    bad = await _post(client, {"op": "preview", "rule": heater(actions=[{"do": "on", "entity_id": LOCK}])})
    assert bad["ok"] is False and bad["code"] == "bad_action"
    assert not calls and not _engine(entry).rules


async def test_api_shape_entities_logbook_and_delete(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    client = await hass_client()
    book = []
    hass.bus.async_listen("logbook_entry", lambda e: book.append(e.data))
    hass.states.async_set("weather.home", "sunny", {"temperature": -4, "temperature_unit": "°C", "friendly_name": "Дом"})
    got = await (await client.get(URL)).json()
    assert set(got) == {"rules", "derived", "outdoor", "recipes", "links", "limits", "quiet"}
    assert got["outdoor"]["entity_id"] == "weather.home" and got["outdoor"]["forecast"] is True  # no street sensor of Hauflo's
    titles = {r["id"]: r for r in got["recipes"]}
    assert titles["heater_outdoor"]["title"] == "Калорифер по улице" and titles["heater_outdoor"]["hint_en"]
    assert {k: got["limits"][k] for k in ("rules", "actions", "conditions", "for_min")} == {k: CANON["limits"][k] for k in ("rules", "actions", "conditions", "for_min")}
    assert got["limits"]["window"]["n"] == CANON["window"]["n"] and got["limits"]["thresholds"] == CANON["thresholds"]
    # «outdoor» as the input: resolved to the forecast at save, said «по прогнозу».
    saved = await _post(client, {"op": "save", "rule": heater(input="outdoor", for_min=0)})
    assert saved["rule"]["input"]["entity_id"] == "weather.home" and saved["rule"]["input"]["forecast"]
    assert "(по прогнозу)" in saved["why"] and saved["active"]
    ent = er.async_get(hass).async_get(ACTIVE)
    assert ent is not None and ent.unique_id == f"{entry.entry_id}_rule_heater"
    assert hass.states.get(ACTIVE).attributes["friendly_name"].endswith("Калорифер по улице")
    assert any(b.get("entity_id") == ACTIVE and "since" in b["message"] for b in book)
    # Renamed: the entity's name follows; disabled from HA's switch: the rule goes NORMAL, its action undone.
    clock.advance(400)
    await _post(client, {"op": "save", "rule": heater(input="outdoor", for_min=0, name="Калорифер по улице")})
    await hass.services.async_call("switch", "turn_off", {"entity_id": SWITCH}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "off" and hass.states.get(HEATER).state == "off"
    assert _engine(entry).explain(_engine(entry).rules["heater"], False) == "Switched off."
    assert (await _post(client, {"op": "enable", "id": "heater", "enabled": "yes"}, 400))["code"] == "bad"
    assert (await _post(client, {"op": "tune", "id": "nope", "x": 1}, 400))["code"] == "no_rule"
    assert (await client.post(URL, json={"op": "launch"})).status == 400
    assert (await client.post(URL, data="{")).status == 400
    assert (await client.get(f"{URL}?log=nope")).status == 404
    await _post(client, {"op": "delete", "id": "heater"})
    await hass.async_block_till_done()
    assert hass.states.get(ACTIVE) is None and er.async_get(hass).async_get(SWITCH) is None
    assert (await _post(client, {"op": "delete", "id": "heater"}, 400))["code"] == "no_rule"


async def test_units_fahrenheit_and_actions_of_every_kind(hass: HomeAssistant, hass_client, entry, calls, clock) -> None:
    client = await hass_client()
    engine = _engine(entry)
    er.async_get(hass).async_get_or_create("scene", "knx", "u-cosy", suggested_object_id="cosy")
    hass.states.async_set("scene.cosy", "scening")
    entry.runtime_data.owned.add("u-cosy")
    await _post(client, {"op": "save", "rule": heater(for_min=0, purpose="other", actions=[{"do": "on", "entity_id": HEATER}, {"do": "speed", "entity_id": VENT, "value": 60}, {"do": "position", "entity_id": BLIND, "value": 30}, {"do": "brightness", "entity_id": HALL, "value": 50}, {"do": "mode", "entity_id": ROOM, "value": "off"}, {"do": "scene", "entity_id": "scene.cosy"}])})
    await _value(hass, "23.0", unit_of_measurement="°F")  # −5 °C: below 5 °C
    assert engine.st("heater")["value"] == pytest.approx(-5.0)
    done = {(e, s) for _d, s, e, _x in calls}
    assert {(HEATER, "turn_on"), (VENT, "set_percentage"), (BLIND, "set_cover_position"), (HALL, "turn_on"), (ROOM, "set_hvac_mode"), ("scene.cosy", "turn_on")} <= done
    calls.clear()
    clock.advance(400)
    await _value(hass, "60.0", unit_of_measurement="°F")  # 15.6 °C — NORMAL after 5 min
    await _settle(hass, engine, clock, 301)
    done = {(e, s, tuple(sorted(x.items()))) for _d, s, e, x in calls}
    # Reversed: on → off, the value-actions back to what they were (fan off, blind open, light off, heat).
    assert (HEATER, "turn_off", ()) in done and (VENT, "turn_off", ()) in done and (BLIND, "open_cover", ()) in done
    assert (HALL, "turn_off", ()) in done and (ROOM, "set_hvac_mode", (("hvac_mode", "heat"),)) in done
    assert not [c for c in calls if c[2] == "scene.cosy"]
    # Own back actions instead of the reverse.
    await _post(client, {"op": "save", "rule": heater(id="own", name="Своё", x=-30, purpose="other", actions=[{"do": "on", "entity_id": HEATER2}], back=[{"do": "off", "entity_id": HEATER2}])})
    assert engine.rules["own"]["back"] == [{"do": "off", "entity_id": HEATER2}]


async def test_failing_service_and_store_survive(hass: HomeAssistant, hass_client, entry, clock) -> None:
    async def boom(call):
        raise RuntimeError("bus down")

    hass.services.async_register("switch", "turn_on", boom)
    await _value(hass, "-3.0")
    await _post(await hass_client(), {"op": "save", "rule": heater(for_min=0)})
    engine = _engine(entry)
    assert engine.st("heater")["log"][-1]["event"] == "error" and "bus down" in engine.st("heater")["log"][-1]["result"]
    # A broken store item is skipped, a good one kept.
    await engine.store.async_save({"rules": ["junk", {"id": "x"}], "derived": [{"id": "BAD"}], "state": {"nope": {}}, "todo": ["x"]})
    fresh = rules_mod.Rules(hass, lambda e: True, lambda e: True)
    await fresh.async_load()
    assert fresh.rules == {} and fresh.derived_cfg == {} and fresh.todo == []


async def test_notify_engine_tell(hass: HomeAssistant, entry, hass_read_only_user) -> None:
    engine = entry.runtime_data.engine
    sent = []

    async def send(rule, title, message, **kw):
        sent.append((title, message, [p["service"] for p in kw["targets"]]))
        return []

    phones = [{"service": "mobile_app_a", "person": "person.anna", "os": "ios", "name": "A"}, {"service": "mobile_app_b", "person": "person.bob", "os": "android", "name": "B"}]
    hass.states.async_set("person.anna", "home", {"user_id": hass_read_only_user.id})
    with patch.object(engine, "phones", return_value=phones), patch.object(engine, "_send", send):
        await engine.async_tell("Правило", "Текст", hass_read_only_user.id)
        await engine.async_tell("Правило", "Всем")
    assert sent == [("Правило", "Текст", ["mobile_app_a"]), ("Правило", "Всем", ["mobile_app_a", "mobile_app_b"])]
    await hass.async_block_till_done()


def test_bands_outside_between_and_contacts() -> None:
    out = {"when": "outside", "lo": 18, "hi": 24, "y": 1}
    assert [rules_mod.past_on(out, v) for v in (17, 18, 21, 24, 25)] == [True, False, False, False, True]
    assert [rules_mod.past_off(out, v) for v in (18.5, 19, 23, 23.5)] == [False, True, True, False]
    btw = {"when": "between", "lo": 40, "hi": 60, "y": 5}
    assert [rules_mod.past_on(btw, v) for v in (39, 40, 60, 61)] == [False, True, True, False]
    assert [rules_mod.past_off(btw, v) for v in (36, 34, 64, 66)] == [False, True, False, True]
    on = {"when": "on", "y": 0}
    assert rules_mod.past_on(on, 1.0) and not rules_mod.past_on(on, 0.0) and rules_mod.past_off(on, 0.0)
    assert rules_mod.past_on({"when": "off", "y": 0}, 0.0)


async def test_clear_all_takes_the_rules_issues(hass: HomeAssistant, entry) -> None:
    from custom_components.hauflo import issues

    issues.raise_rule_issue(hass, "rule_fault", "x", name="X", entity="sensor.x", reason="—")
    assert ir.async_get(hass).async_get_issue(DOMAIN, "rule_fault_x") is not None
    issues.clear_all(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "rule_fault_x") is None
