"""Alarm rules as Home Assistant's own objects (0.26, owner's decision 5B; 0.31 — small, as written by hand): every
enabled rule → «Принято» and «авария открыта» helpers and an automation in automations.yaml (3 triggers, 2 actions)
that pushes, repeats, takes «Принято» and the rule's button and says «back to normal» — without Hauflo; «no data» and a
chattering sensor are the agent's engine's. Broken on purpose: a flicker, a restart, a hand-edited automation, a
hand-formatted file, a closed house, the integration removed, an upgrade from 0.30."""

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.components.persistent_notification import _async_get_or_create_notifications as _notes
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import dump, load_yaml
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hauflo import independent, notify, ownership
from custom_components.hauflo.const import CONF_ALLOW_CHANGES, CONF_RANGES, CONF_SECRET, CONF_SERVER, DOMAIN, NATIVE_STORE
from custom_components.hauflo.storage import HaufloStore

THEIRS = {"id": "1700000000000", "alias": "Integrator: porch", "triggers": [{"trigger": "state", "entity_id": "binary_sensor.porch", "to": "on"}], "actions": [{"action": "light.turn_off", "target": {"entity_id": "light.porch"}}]}
PHONES = [{"service": "mobile_app_anna", "name": "Pixel Анны", "os": "android", "person": "person.anna"}, {"service": "mobile_app_ivan", "name": "iPhone Ивана", "os": "ios", "person": "person.ivan"}]
LEAK = "binary_sensor.leak_kitchen"
LEAK2 = "binary_sensor.leak_bath"
VALVE = "valve.water_main"
IDS = independent.ids("leak")
AID = IDS["automation"]
ACK = "input_boolean." + IDS["helper"]
LEAK_RULE = {"id": "leak", "kind": "leak", "title": "Протечка", "sources": [LEAK, LEAK2], "picked": [LEAK, LEAK2], "alarm_states": ["on"], "priority": "critical", "repeat_min": 0,
             "recipients": {"all": True}, "action": {"title": "Перекрыть воду", "service": "valve.close_valve", "entity_id": VALVE}}
# The leak rule's window (life safety: short, HA review N-3 07.11).
BACK = independent.back_after(LEAK_RULE)
OFF_RULE = {"id": "boiler", "kind": "fault", "title": "Котёл", "sources": ["binary_sensor.boiler"], "picked": ["binary_sensor.boiler"], "alarm_states": ["on"], "recipients": {"all": True}, "enabled": False}


async def _idle(self):
    return None


@pytest.fixture
async def house(hass: HomeAssistant, tmp_path: Path, freezer, hass_admin_user):
    hass.config.config_dir = str(tmp_path)
    hass.config.language = "ru"
    (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
    (tmp_path / "automations.yaml").write_text(dump([THEIRS]))
    assert await async_setup_component(hass, "automation", {"automation": [THEIRS]})
    assert await async_setup_component(hass, "persistent_notification", {})
    for e, n in ((LEAK, "Протечка на кухне"), (LEAK2, "Протечка в ванной")):
        hass.states.async_set(e, "off", {"device_class": "moisture", "friendly_name": n})
    hass.states.async_set(VALVE, "open", {"friendly_name": "Кран {вода}"})
    hass.states.async_set("person.anna", "home", {"friendly_name": "Анна", "user_id": hass_admin_user.id})
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — Вилла", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"}, options={CONF_RANGES: ["1/1"], CONF_ALLOW_CHANGES: True})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    engine = entry.runtime_data.engine
    engine.owns = lambda eid: eid == VALVE
    engine.config = notify._clean_rules({"lang": "ru", "rules": [LEAK_RULE, OFF_RULE]}, hass, engine.owns)
    engine._apply()
    engine.phones = lambda: PHONES  # type: ignore[method-assign]
    pushes: list[ServiceCall] = []
    closed: list[ServiceCall] = []
    for p in PHONES:
        hass.services.async_register("notify", p["service"], pushes.append)
    hass.services.async_register("valve", "close_valve", closed.append)
    yield entry, engine, tmp_path, pushes, closed, freezer
    # A sensor left in normal keeps its «back» timer (chatter window): the automations stop with the test.
    await hass.services.async_call("automation", "turn_off", {"entity_id": "all"}, blocking=True)


async def _later(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _admin(hass: HomeAssistant):
    return next(u for u in hass.auth._store._users.values() if u.is_admin and not u.system_generated)


def _by(pushes: list[ServiceCall]) -> dict[str, dict]:
    return {c.service: c.data for c in pushes}


def _ours(config: Path) -> list[dict]:
    return [a for a in load_yaml(str(config / "automations.yaml")) if str(a["id"]).startswith("hauflo_alarm_")]


async def test_rule_becomes_a_helper_and_an_automation(hass: HomeAssistant, house) -> None:
    entry, engine, config, _, _, _ = house
    res = await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    assert res["added"] == ["Протечка"] and not res["engine"] and not res["error"]
    data = load_yaml(str(config / "automations.yaml"))
    assert data[0] == THEIRS and [a["id"] for a in data[1:]] == [AID]  # theirs untouched, the disabled rule skipped
    assert data[1]["alias"] == "Авария: Протечка" and ACK in data[1]["description"] and "iPhone Ивана" in data[1]["description"]
    assert list(config.glob("automations.yaml.hauflo-bak-*"))
    assert hass.states.get(ACK).state == "off"
    eid = er.async_get(hass).async_get_entity_id("automation", "automation", AID)
    assert eid and hass.states.get(eid).state == "on"
    assert engine.native["leak"]["automation"] == AID and engine.native["leak"]["helper"] == ACK
    assert engine.status()["native"]["leak"]["on"] is True
    # Hauflo's own in the agent's ownership, so a deploy knows them.
    reg = er.async_get(hass)
    assert ownership.kind_of(reg.async_get(eid), set(), set()) == "alarm" and ownership.kind_of(reg.async_get(ACK), set(), set()) == "alarm"
    # Again: nothing rewritten.
    again = await independent.async_sync(hass, engine)
    assert again["unchanged"] == ["Протечка"] and not again["added"] and not again["updated"]


async def test_alarm_push_ack_back_without_the_engine(hass: HomeAssistant, house) -> None:
    entry, engine, config, pushes, closed, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    hass.states.async_set(LEAK, "on", {"device_class": "moisture", "friendly_name": "Протечка на кухне"})
    await _later(hass, freezer, 2)
    assert pushes == []  # held 5 s first
    await _later(hass, freezer, 4)
    by = _by(pushes)
    assert len(pushes) == 2 and set(by) == {"mobile_app_anna", "mobile_app_ivan"}  # once per phone: the engine is quiet
    assert by["mobile_app_anna"]["title"] == "🚨 Протечка" and by["mobile_app_anna"]["message"] == "Протечка на кухне: авария."
    assert by["mobile_app_anna"]["data"]["channel"] == "alarm_stream" and by["mobile_app_anna"]["data"]["tag"] == AID
    assert by["mobile_app_ivan"]["data"]["push"]["interruption-level"] == "critical"
    acts = by["mobile_app_ivan"]["data"]["actions"]
    assert [a["action"] for a in acts] == [IDS["run"], IDS["ack"]]
    assert acts[0]["title"] == "Закрыть: Кран {вода}" and acts[1]["title"] == "Принято"  # a name with braces said as it is
    assert set(_notes(hass)) == {AID}  # HA's bell: the automation's note, none from the engine
    # «Принято» from the phone: the helper on, a quiet word to everyone with the person's name; the engine's card follows.
    pushes.clear()
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": IDS["ack"]}, context=Context(user_id=_admin(hass).id))
    await hass.async_block_till_done()
    assert hass.states.get(ACK).state == "on"
    by = _by(pushes)
    assert by["mobile_app_anna"]["message"] == "Принято: Анна." and "critical" not in str(by["mobile_app_ivan"]["data"].get("push", {}))
    assert all(i.get("acked_by") for i in engine.active.values())
    # A second press says nothing again.
    pushes.clear()
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": IDS["ack"]})
    await hass.async_block_till_done()
    assert pushes == []
    # Back to normal for the whole chatter window: the same tag replaces the alarm, «Принято» resets.
    hass.states.async_set(LEAK, "off", {"device_class": "moisture", "friendly_name": "Протечка на кухне"})
    await _later(hass, freezer, BACK - 10)
    assert pushes == [] and hass.states.get(ACK).state == "on" and set(_notes(hass)) == {AID}
    await _later(hass, freezer, 11)
    by = _by(pushes)
    assert by["mobile_app_anna"]["title"] == "✅ Протечка" and by["mobile_app_anna"]["data"]["tag"] == AID
    assert "снова в норме" in by["mobile_app_anna"]["message"]
    assert hass.states.get(ACK).state == "off" and set(_notes(hass)) == set()


async def test_flicker_and_restart_say_nothing(hass: HomeAssistant, house) -> None:
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 2)
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, 10)
    assert pushes == []  # 2 s of «on»: neither an alarm nor «back»
    # Accepted alarm, then HA restarts (unavailable → on): not again.
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": IDS["ack"]})
    await hass.async_block_till_done()
    pushes.clear()
    hass.states.async_set(LEAK, "unavailable")
    await _later(hass, freezer, 1)
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    assert pushes == []
    # Not accepted: a radio blip (unavailable → on) is the same open alarm — nothing (security N-1 07.11) …
    await hass.services.async_call("input_boolean", "turn_off", {"entity_id": ACK}, blocking=True)
    hass.states.async_set(LEAK, "unavailable")
    await _later(hass, freezer, 1)
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    assert pushes == []
    # … a real restart (no state before) tells it again while nobody accepted it.
    hass.states.async_remove(LEAK)
    await _later(hass, freezer, 1)
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    assert len(pushes) == 2


async def test_button_runs_the_rule_action_and_accepts(hass: HomeAssistant, house) -> None:
    entry, engine, config, pushes, closed, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": IDS["run"]})
    await hass.async_block_till_done()
    assert closed == []  # no alarm on: the button does nothing
    hass.states.async_set(LEAK2, "on")
    await _later(hass, freezer, 6)
    pushes.clear()
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": IDS["run"]})
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in closed] == [[VALVE]]
    assert hass.states.get(ACK).state == "on"
    assert _by(pushes)["mobile_app_anna"]["message"] == "Закрыть: Кран {вода}: выполнено."


async def test_repeat_until_accepted(hass: HomeAssistant, house) -> None:
    entry, engine, config, pushes, _, freezer = house
    engine.config = notify._clean_rules({"lang": "ru", "rules": [{**LEAK_RULE, "repeat_min": 5}]}, hass, engine.owns)
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    assert len(pushes) == 2
    pushes.clear()
    for _ in range(5):
        await _later(hass, freezer, 60)
    assert len(pushes) == 2 and "никто не подтвердил" in pushes[0].data["message"]
    assert pushes[0].data["data"]["tag"] == AID
    pushes.clear()
    # Accepted in HA itself (the helper's toggle): said to the phones; no more repeats.
    await hass.services.async_call("input_boolean", "turn_on", {"entity_id": ACK}, blocking=True, context=Context(user_id=_admin(hass).id))
    await hass.async_block_till_done()
    assert len(pushes) == 2 and pushes[0].data["message"].startswith("Принято")
    pushes.clear()
    for _ in range(11):
        await _later(hass, freezer, 60)
    assert pushes == []
    # Accepted, still in alarm: the run waits only for the rule's button (closing the valve is still worth it).
    eid = er.async_get(hass).async_get_entity_id("automation", "automation", AID)
    assert hass.states.get(eid).attributes["current"] == 1


async def test_repeats_slow_down_after_repeat_fast(hass: HomeAssistant, house, monkeypatch) -> None:
    """As the engine (owner 27.10): REPEAT_FAST repeats at the rule's pace, then hourly until «Принято» — never every
    5 minutes all night (HA's daily push limit). Still 3 triggers / 2 actions; the pace is the wait's own timeout."""
    entry, engine, config, pushes, _, freezer = house
    monkeypatch.setattr(independent, "REPEAT_FAST", 2)
    engine.config = notify._clean_rules({"lang": "ru", "rules": [{**LEAK_RULE, "repeat_min": 5}]}, hass, engine.owns)
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    auto = next(a for a in _ours(config) if a["id"] == AID)
    assert _size(auto) == (3, 2)
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    pushes.clear()
    for _ in range(10):  # 2 × 5 min: two repeats, each to both phones
        await _later(hass, freezer, 60)
    assert len(pushes) == 4 and all("никто не подтвердил" in c.data["message"] for c in pushes)
    pushes.clear()
    for _ in range(59):  # no third at the rule's pace — the next one an hour after the second
        await _later(hass, freezer, 60)
    assert pushes == []
    await _later(hass, freezer, 60)
    assert len(pushes) == 2 and "никто не подтвердил" in pushes[0].data["message"]
    assert "repeat.index" not in str(auto) and "last_changed" in auto["actions"][1]["repeat"]["sequence"][0]["timeout"]["seconds"]
    # A rule repeating hourly or rarer keeps its own pace (nothing to slow down).
    engine.config = notify._clean_rules({"lang": "ru", "rules": [{**LEAK_RULE, "repeat_min": notify.REPEAT_SLOW}]}, hass, engine.owns)
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    auto = next(a for a in _ours(config) if a["id"] == AID)
    assert "if a < 0 " in auto["actions"][1]["repeat"]["sequence"][0]["timeout"]["seconds"] and _size(auto) == (3, 2)


def _shakes(pushes: list[ServiceCall]) -> list[ServiceCall]:
    """The engine's «дребезжит» (📳) — a chattering source of a rule HA runs is the engine's to tell (0.31)."""
    return [c for c in pushes if str(c.data.get("title", "")).startswith("📳")]


async def _flap(hass: HomeAssistant, freezer, entity: str, times: int) -> None:
    for _ in range(times):
        hass.states.async_set(entity, "on")
        await _later(hass, freezer, 6)
        hass.states.async_set(entity, "off")
        await _later(hass, freezer, 6)


async def test_chattering_sensor_is_bounded(hass: HomeAssistant, house) -> None:
    """F-3 (security review 07.11): hysteresis on the return (Nagios flap detection, Zabbix recovery, HA's `for:`). The
    alarm is told at once; «back» only after the window in normal; back in alarm within it — nothing, the window anew."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _flap(hass, freezer, LEAK, 20)  # 20 alarms within 4 minutes
    # Told at once, once (HA's automation); the engine says the sensor chatters (0.31: chatter is the engine's), once.
    assert [c.data["title"] for c in pushes if c.service == "mobile_app_anna"] == ["🚨 Протечка", "📳 Протечка"]
    assert all("critical" not in str(c.data["data"]) for c in _shakes(pushes)) and len(_shakes(pushes)) == 2
    shake = engine._tag(f"leak|{LEAK}") + "_chatter"
    assert set(_notes(hass)) == {AID, shake} and hass.states.get(ACK).state == "off"
    pushes.clear()
    # In alarm again just before the window ends: still nothing, and the window starts again.
    await _later(hass, freezer, BACK - 30)
    await _flap(hass, freezer, LEAK, 1)
    await _later(hass, freezer, BACK - 30)
    assert pushes == [] and AID in _notes(hass)
    await _later(hass, freezer, 30)
    assert [c.data["title"] for c in pushes] == ["✅ Протечка"] * 2 and AID not in _notes(hass)
    pushes.clear()
    await _later(hass, freezer, 60)
    assert pushes == []  # «back» once
    # Over: the next alarm of the same sensor is a new one, said at once.
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    assert len(pushes) == 2 and pushes[0].data["title"] == "🚨 Протечка" and pushes[0].data["data"]["tag"] == AID
    assert [a["action"] for a in _by(pushes)["mobile_app_ivan"]["data"]["actions"]] == [IDS["run"], IDS["ack"]]
    pushes.clear()
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, 6)
    await _flap(hass, freezer, LEAK, 3)
    assert pushes == []
    # Another sensor of the rule: a new alarm, said at once, though the first one just chattered.
    hass.states.async_set(LEAK2, "on")
    await _later(hass, freezer, 6)
    assert len(pushes) == 2 and "Leak bath" in pushes[0].data["message"]  # unnamed: its id in words (0.33 п.5)
    pushes.clear()
    hass.states.async_set(LEAK2, "off")
    await _later(hass, freezer, BACK + 60)
    assert [c.data["title"] for c in pushes] == ["✅ Протечка"] * 2  # one «back» for both


async def test_a_flicker_after_calm_says_nothing(hass: HomeAssistant, house) -> None:
    """Under HOLD long after the last alarm: neither an alarm nor, after the window, a «back»."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _later(hass, freezer, 60)
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 2)
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, BACK + 60)
    assert pushes == []


async def test_chatter_while_another_is_in_alarm(hass: HomeAssistant, house) -> None:
    """One sensor in alarm, another chattering: its first alarm is said (a new name), then quiet; no «back» while one
    is on, then one «back» after the window."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _later(hass, freezer, 60)
    hass.states.async_set(LEAK2, "on")
    await _later(hass, freezer, 6)
    pushes.clear()
    await _later(hass, freezer, 1)  # not the very instant of the other's alarm (frozen time: «the rule acted in this phase»)
    await _flap(hass, freezer, LEAK, 15)
    assert len(pushes) == 4 and "Leak kitchen" in pushes[0].data["message"] and "Leak bath" in pushes[0].data["message"]
    assert LEAK not in pushes[0].data["message"] and LEAK2 not in pushes[0].data["message"]  # never the raw id (0.33 п.5)
    assert [c.data["title"] for c in pushes if c.service == "mobile_app_anna"] == ["🚨 Протечка", "📳 Протечка"]  # and «дребезжит», the engine's
    pushes.clear()
    await _later(hass, freezer, BACK)
    assert pushes == []  # the other one still on
    hass.states.async_set(LEAK2, "off")
    await _later(hass, freezer, BACK + 60)
    assert [c.data["title"] for c in pushes] == ["✅ Протечка"] * 2


async def test_chatter_then_repeat_until_accepted(hass: HomeAssistant, house) -> None:
    """With repeats: one alarm for the chatter, the repeat while it stays in alarm, until «Принято»; «back» once."""
    entry, engine, config, pushes, _, freezer = house
    engine.config = notify._clean_rules({"lang": "ru", "rules": [{**LEAK_RULE, "repeat_min": 5}]}, hass, engine.owns)
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _flap(hass, freezer, LEAK, 5)
    # The first alarm only (and the engine's «дребезжит»), no «back» yet.
    assert [c.data["title"] for c in pushes if c.service == "mobile_app_anna"] == ["🚨 Протечка", "📳 Протечка"]
    pushes.clear()
    hass.states.async_set(LEAK, "on")
    for _ in range(4):
        await _later(hass, freezer, 60)
    assert pushes == []
    for _ in range(7):
        await _later(hass, freezer, 60)
    # At 5 and 10 min after it held (HA's run: the repeat waits from the last trigger, the chatter's last alarm).
    assert len(pushes) == 4 and all("никто не подтвердил" in c.data["message"] for c in pushes)
    pushes.clear()
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": IDS["ack"]}, context=Context(user_id=_admin(hass).id))
    await hass.async_block_till_done()
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": IDS["ack"]})
    await hass.async_block_till_done()
    assert len(pushes) == 2 and pushes[0].data["message"] == "Принято: Анна."
    pushes.clear()
    for _ in range(11):
        await _later(hass, freezer, 60)
    assert pushes == []
    # Accepted, chatters on, then calm for the window: «back» once («Принято» means it was told).
    await _flap(hass, freezer, LEAK, 3)
    assert pushes == [] and hass.states.get(ACK).state == "on"
    await _later(hass, freezer, BACK)
    assert len(pushes) == 2 and pushes[0].data["title"] == "✅ Протечка" and hass.states.get(ACK).state == "off"


async def test_a_hand_edited_automation_is_kept(hass: HomeAssistant, house) -> None:
    entry, engine, config, _, _, _ = house
    await independent.async_sync(hass, engine)
    data = load_yaml(str(config / "automations.yaml"))
    data[1]["mode"] = "single"  # the integrator's own change
    assert engine.native_status()["leak"]["edited"] is False  # Hauflo's own write
    (config / "automations.yaml").write_text(dump(data))
    await hass.services.async_call("automation", "reload", {}, blocking=True)  # HA's editor reloads after its save
    assert engine.native_status()["leak"]["edited"] is True  # Н1: «Переписать» leaves it out
    engine.config = notify._clean_rules({"lang": "ru", "rules": [{**LEAK_RULE, "title": "Вода"}]}, hass, engine.owns)
    res = await independent.async_sync(hass, engine)
    assert res["kept_edited"] == ["Вода"] and _ours(config)[0]["mode"] == "single" and engine.native_status()["leak"]["edited"] is True
    # Its rule gone from Hauflo: the edited automation stays, said.
    engine.config = notify._clean_rules({"lang": "ru", "rules": []}, hass, engine.owns)
    res = await independent.async_sync(hass, engine)
    assert _ours(config) and [w["code"] for w in res["warnings"]] == ["edited_left"]


async def test_removed_rule_takes_its_automation_and_helper(hass: HomeAssistant, house) -> None:
    entry, engine, config, _, _, _ = house
    await independent.async_sync(hass, engine)
    engine.config = notify._clean_rules({"lang": "ru", "rules": []}, hass, engine.owns)
    res = await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    assert res["removed"] == ["Авария: Протечка"] and _ours(config) == []
    assert hass.states.get(ACK) is None and engine.native == {}
    assert er.async_get(hass).async_get_entity_id("automation", "automation", AID) is None


async def test_dry_run_writes_nothing(hass: HomeAssistant, house) -> None:
    entry, engine, config, _, _, _ = house
    before = (config / "automations.yaml").read_text()
    res = await independent.async_sync(hass, engine, dry_run=True)
    assert res["added"] == ["Протечка"]
    assert (config / "automations.yaml").read_text() == before and hass.states.get(ACK) is None and engine.native == {}


async def test_hand_formatted_file_keeps_rules_with_the_engine(hass: HomeAssistant, house) -> None:
    entry, engine, config, pushes, _, freezer = house
    text = "# the integrator's notes\n" + dump([THEIRS])
    (config / "automations.yaml").write_text(text)
    res = await independent.async_sync(hass, engine)
    assert res["error"] and res["error_code"] == "not_plain" and [(e["title"], e["reason"]) for e in res["engine"]] == [("Протечка", "not_plain")]
    assert (config / "automations.yaml").read_text() == text and engine.native == {}
    # Fail-safe: the engine pushes the alarm itself.
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 30)
    assert {c.service for c in pushes} == {"mobile_app_anna", "mobile_app_ivan"}


async def test_closed_house_makes_no_new_automation(hass: HomeAssistant, house) -> None:
    entry, engine, config, _, _, _ = house
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ALLOW_CHANGES: False})
    res = await independent.async_sync(hass, engine)
    assert [e["reason"] for e in res["engine"]] == ["locked"] and _ours(config) == []


async def test_engine_hands_over_an_open_alarm(hass: HomeAssistant, house) -> None:
    """Migration: an alarm already open when the rule becomes HA's own — told once (by the engine, under the native
    tag), never repeated by the engine again; an accepted one turns the helper on."""
    entry, engine, config, pushes, _, freezer = house
    engine.config = notify._clean_rules({"lang": "ru", "rules": [{**LEAK_RULE, "repeat_min": 5}]}, hass, engine.owns)
    engine._apply()
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 30)
    assert len(pushes) == 2  # the engine's alarm
    await engine._accept(notify.ACK, next(iter(engine.active)), "Анна", None)
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    assert hass.states.get(ACK).state == "on"
    pushes.clear()
    for _ in range(12):
        await _later(hass, freezer, 60)
    assert pushes == []  # accepted: neither the engine nor the automation repeats


async def test_remove_alarms_service_admin_only(hass: HomeAssistant, house, hass_read_only_user) -> None:
    entry, engine, config, _, _, _ = house
    await independent.async_sync(hass, engine)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(DOMAIN, "remove_alarms", {}, blocking=True, context=Context(user_id=hass_read_only_user.id))
    with pytest.raises(Unauthorized):
        await hass.services.async_call(DOMAIN, "remove_alarms", {}, blocking=True)
    assert _ours(config)
    admin = _admin(hass)
    await hass.services.async_call(DOMAIN, "remove_alarms", {}, blocking=True, context=Context(user_id=admin.id))
    await hass.async_block_till_done()
    assert _ours(config) == [] and hass.states.get(ACK) is None and engine.native == {}
    assert load_yaml(str(config / "automations.yaml")) == [THEIRS]
    # A hand-formatted file: nothing removed, said.
    await independent.async_sync(hass, engine)
    text = "# notes\n" + (config / "automations.yaml").read_text()
    (config / "automations.yaml").write_text(text)
    with pytest.raises(HomeAssistantError, match="ничего не удалено"):
        await hass.services.async_call(DOMAIN, "remove_alarms", {}, blocking=True, context=Context(user_id=admin.id))


async def test_removing_the_integration_leaves_them(hass: HomeAssistant, house) -> None:
    entry, engine, config, _, _, _ = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert [a["id"] for a in _ours(config)] == [AID] and hass.states.get(ACK) is not None
    assert "Автоматизации" in _notes(hass)[independent.NOTE]["message"]
    assert await HaufloStore(hass, NATIVE_STORE).async_load()


async def test_card_accept_and_the_engine_takes_a_rule_back(hass: HomeAssistant, house) -> None:
    entry, engine, config, pushes, _, freezer = house
    engine.config = notify._clean_rules({"lang": "ru", "rules": [{**LEAK_RULE, "repeat_min": 5}]}, hass, engine.owns)
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    hass.states.async_set(LEAK, "on")
    hass.states.async_set(LEAK2, "on")
    await _later(hass, freezer, 6)
    keys = sorted(engine.active)
    assert len(keys) == 2 and all(engine.active[k].get("repeat") is None for k in keys)  # the engine doesn't repeat them
    pushes.clear()
    # One of two accepted on the dashboard card: the helper stays off (the other still unaccepted).
    assert await engine.async_ack(keys[0], "Анна")
    await hass.async_block_till_done()
    assert hass.states.get(ACK).state == "off"
    assert await engine.async_ack(keys[1], "Анна")
    await hass.async_block_till_done()
    assert hass.states.get(ACK).state == "on"
    # Said to the phones by the engine under the automation's tag; the automation itself stays quiet (agent's context).
    assert pushes and all(c.data["data"]["tag"] == AID for c in pushes)
    assert len(pushes) == 4
    # Rule taken back (its automation gone, e.g. removed by the admin) with an open unaccepted alarm: the engine repeats.
    await hass.services.async_call("input_boolean", "turn_off", {"entity_id": ACK}, blocking=True)
    engine.active[keys[0]]["acked_by"] = None
    await engine.async_set_native({})
    assert engine.active[keys[0]].get("repeat") is not None
    for e in (LEAK, LEAK2):  # the alarms over: no repeat left waiting
        hass.states.async_set(e, "off")
    await _later(hass, freezer, 6)


async def test_a_rule_that_cant_be_made_stays_with_the_engine(hass: HomeAssistant, house) -> None:
    entry, engine, config, _, _, _ = house
    with patch.object(independent, "_ensure_helper", side_effect=RuntimeError("no storage")):
        res = await independent.async_sync(hass, engine)
    assert [e["reason"] for e in res["engine"]] == ["helper"] and _ours(config) == []
    with patch("homeassistant.components.automation.config.async_validate_config_item", side_effect=ValueError("bad")):
        res = await independent.async_sync(hass, engine)
    assert [e["reason"] for e in res["engine"]] == ["invalid"] and _ours(config) == []
    with patch("custom_components.hauflo.automations.Automations._write_file", side_effect=OSError("read-only")):
        res = await independent.async_sync(hass, engine)
    # The raw reason stays in HA's log: the cloud gets a code and the words (security F-7).
    assert res["error_code"] == "write" and "read-only" not in res["error"] and "журнал" in res["error"]
    assert [e["reason"] for e in res["engine"]] == ["write"] and engine.native == {}
    # Made once: a later failure keeps the running automation, said as a warning.
    await independent.async_sync(hass, engine)
    engine.config = notify._clean_rules({"lang": "ru", "rules": [{**LEAK_RULE, "title": "Вода"}]}, hass, engine.owns)
    with patch("homeassistant.components.automation.config.async_validate_config_item", side_effect=ValueError("bad")):
        res = await independent.async_sync(hass, engine)
    assert [w["code"] for w in res["warnings"]] == ["invalid"] and "leak" in engine.native


async def test_view_dry_run_then_apply(hass: HomeAssistant, house, hass_client) -> None:
    entry, engine, config, _, _, _ = house
    client = await hass_client()
    body = {"lang": "ru", "rules": [LEAK_RULE], "dry_run": True}
    res = await client.post("/api/hauflo/notify", json=body)
    assert res.status == 200, await res.text()
    got = await res.json()
    assert got["native"]["added"] == ["Протечка"] and _ours(config) == [] and engine.native == {}
    body.pop("dry_run")
    got = await (await client.post("/api/hauflo/notify", json=body)).json()
    assert got["native"]["added"] == ["Протечка"] and [a["id"] for a in _ours(config)] == [AID]
    status = await (await client.get("/api/hauflo/notify")).json()
    assert status["native"]["leak"]["automation"].startswith("automation.") and status["native"]["leak"]["helper"] == ACK
    # The sync failing never loses the saved rules.
    with patch.object(independent, "async_sync", side_effect=RuntimeError("boom")):
        got = await (await client.post("/api/hauflo/notify", json=body)).json()
    assert got["native"] == {"error": "boom"} and got["config"]["rules"][0]["id"] == "leak"


async def test_language_change_rewrites_the_texts(hass: HomeAssistant, house) -> None:
    """5B e2e D-3: the project's language changed → the plan says «update», the sync writes the other words."""
    entry, engine, config, _, _, _ = house
    await independent.async_sync(hass, engine)
    en = notify._clean_rules({"lang": "en", "rules": [LEAK_RULE]}, hass, engine.owns)
    plan = await independent.async_sync(hass, engine, dry_run=True, config=en)
    assert len(plan["updated"]) == 1 and not plan["unchanged"]
    assert _ours(config)[0]["alias"] == "Авария: Протечка"  # a plan writes nothing
    engine.config = en
    res = await independent.async_sync(hass, engine)
    assert len(res["updated"]) == 1 and _ours(config)[0]["alias"].startswith("Alarm: ")
    assert engine.native["leak"]["lang"] == "en"


async def test_no_yaml_anchors_and_three_backups(hass: HomeAssistant, house) -> None:
    """5B e2e D-5: a value used twice is written out, never «&id001 / *id001» renumbered on each write (a diff and
    «changed» every time); HA's editor's own anchors still read as plain; only the last 3 backup copies stay."""
    entry, engine, config, _, _, freezer = house
    shared = ["binary_sensor.porch"]
    theirs = {**THEIRS, "triggers": [{"trigger": "state", "entity_id": shared, "to": "on"}, {"trigger": "state", "entity_id": shared, "to": "off"}]}
    with_anchors = dump([theirs])
    assert "&id001" in with_anchors  # what HA's own editor writes for it
    (config / "automations.yaml").write_text(with_anchors)
    await independent.async_sync(hass, engine)
    text = (config / "automations.yaml").read_text()
    assert "&id" not in text and "*id" not in text
    assert load_yaml(str(config / "automations.yaml"))[0] == theirs
    for repeat in (5, 10, 7, 15, 16):
        freezer.tick(timedelta(seconds=1))  # each copy its own time
        engine.config = notify._clean_rules({"lang": "ru", "rules": [{**LEAK_RULE, "repeat_min": repeat}]}, hass, engine.owns)
        assert (await independent.async_sync(hass, engine))["updated"] == ["Протечка"]
        assert "&id" not in (config / "automations.yaml").read_text()
    assert len(list(config.glob("automations.yaml.hauflo-bak-*"))) == 3
    # Nothing changed → nothing written: no new copy, the same text.
    before = (config / "automations.yaml").read_text()
    assert (await independent.async_sync(hass, engine))["unchanged"] == ["Протечка"]
    assert (config / "automations.yaml").read_text() == before


async def test_one_language_for_what_is_said_about_them(hass: HomeAssistant, house) -> None:
    """5B e2e D-4: HA in Russian, the project in English — the note left on removal speaks the automations' language
    (kept with their record), not HA's."""
    entry, engine, config, _, _, _ = house
    engine.config = notify._clean_rules({"lang": "en", "rules": [LEAK_RULE]}, hass, engine.owns)
    await independent.async_sync(hass, engine)
    assert _ours(config)[0]["alias"].startswith("Alarm: ")
    assert await independent.written_english(hass, engine) is True
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    note = _notes(hass)[independent.NOTE]
    en = await independent.texts(hass, True)
    assert note["message"] == en["kept"] and note["title"] == en["kept_title"]


async def test_language_falls_back_to_the_engine_then_ha(hass: HomeAssistant, house) -> None:
    entry, engine, config, _, _, _ = house
    assert engine.native == {}
    engine.config = notify._clean_rules({"lang": "en", "rules": []}, hass, engine.owns)
    assert await independent.written_english(hass, engine) is True  # nothing written yet: the project's
    assert await independent.written_english(hass, None) is False  # no records, no engine: HA's (ru)
    hass.config.language = "en-GB"
    assert await independent.written_english(hass, None) is True


async def _quick(hass: HomeAssistant, freezer, entity: str, times: int, step: float = 1.5) -> None:
    """Faster than HOLD both ways (the live probe on ha-fresh 07.11: a 1.5 s chatter)."""
    for _ in range(times):
        hass.states.async_set(entity, "off")
        await _later(hass, freezer, step)
        hass.states.async_set(entity, "on")
        await _later(hass, freezer, step)


async def test_fast_chatter_after_an_alarm_is_quiet(hass: HomeAssistant, house) -> None:
    """A told alarm, then a chatter faster than HOLD ending in alarm: nothing new (the alarm is still open)."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _later(hass, freezer, 60)
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 8)
    assert len(pushes) == 2
    pushes.clear()
    await _quick(hass, freezer, LEAK, 8)
    await _later(hass, freezer, 8)
    assert pushes == []
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, BACK + 10)
    assert [c.data["title"] for c in pushes] == ["✅ Протечка"] * 2


async def test_flicker_before_the_first_alarm_still_tells_it(hass: HomeAssistant, house) -> None:
    """Safety: a sensor that flickers (under HOLD) and then stays in alarm is told — the flicker chains nothing."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _later(hass, freezer, 60)
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 1.5)
    await _quick(hass, freezer, LEAK, 5)
    await _later(hass, freezer, 8)
    assert len(pushes) == 2 and pushes[0].data["title"] == "🚨 Протечка"


async def test_flicker_soon_after_back_says_nothing(hass: HomeAssistant, house) -> None:
    """«back» said; a flicker 10 s later (and a quick chatter): no second «back» after the window (live probe 07.11);
    a real alarm after it is told."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _later(hass, freezer, 60)
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 8)
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, BACK + 1)
    assert [c.data["title"] for c in pushes][-2:] == ["✅ Протечка"] * 2
    pushes.clear()
    await _later(hass, freezer, 10)
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 1)
    await _quick(hass, freezer, LEAK, 3)
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, BACK + 10)
    assert pushes == []
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 8)
    assert len(pushes) == 2 and pushes[0].data["title"] == "🚨 Протечка"


async def test_back_said_when_another_flickers_after_a_told_return(hass: HomeAssistant, house) -> None:
    """One sensor told and back in normal; another flickers within the window: one «back» after the last change."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _later(hass, freezer, 60)
    hass.states.async_set(LEAK2, "on")
    await _later(hass, freezer, 8)
    hass.states.async_set(LEAK2, "off")
    await _later(hass, freezer, 30)
    pushes.clear()
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 1)
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, BACK - 40)
    assert pushes == []  # LEAK2's window broken by the other's change: not yet
    await _later(hass, freezer, 60)
    assert [c.data["title"] for c in pushes] == ["✅ Протечка"] * 2


def _eid(hass: HomeAssistant) -> str:
    return er.async_get(hass).async_get_entity_id("automation", "automation", AID)


async def test_automation_off_in_ha_the_engine_tells_and_repeats(hass: HomeAssistant, house) -> None:
    """HA review H-1: the rule's automation switched off in HA — its record alone silenced the alarm (no push, no note,
    no repeat; «на телефоне» said anyway). Now the engine tells it; on again — HA's automation, the engine quiet."""
    entry, engine, config, pushes, _, freezer = house
    engine.config = notify._clean_rules({"lang": "ru", "rules": [{**LEAK_RULE, "repeat_min": 5}]}, hass, engine.owns)
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    assert engine.status()["native"]["leak"]["state"] == "ha"
    await hass.services.async_call("automation", "turn_off", {"entity_id": _eid(hass)}, blocking=True)
    await hass.async_block_till_done()
    assert engine.status()["native"]["leak"]["state"] == "off" and not engine._in_ha("leak")
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 30)
    assert len(pushes) == 2 and all(c.data["data"]["tag"] != AID for c in pushes)  # the engine's own push, once per phone
    assert any(n.get("notification_id", "").startswith("hauflo") for n in _notes(hass).values())
    pushes.clear()
    for _ in range(5):
        await _later(hass, freezer, 60)
    assert len(pushes) == 2  # and its repeat
    pushes.clear()
    # On again with that alarm still open: HA's automation repeats it (its trigger sees an alarm already on), the
    # engine's repeat stops — once per phone, never twice.
    await hass.services.async_call("automation", "turn_on", {"entity_id": _eid(hass)}, blocking=True)
    await hass.async_block_till_done()
    assert engine.status()["native"]["leak"]["state"] == "ha" and engine._in_ha("leak")
    assert all(inc.get("repeat") is None for inc in engine.active.values())
    for _ in range(5):
        await _later(hass, freezer, 60)
    assert len(pushes) == 2 and all(c.data["data"]["tag"] == AID for c in pushes)
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, 6)


async def test_automation_off_with_an_open_alarm_the_engine_repeats(hass: HomeAssistant, house) -> None:
    entry, engine, config, pushes, _, freezer = house
    engine.config = notify._clean_rules({"lang": "ru", "rules": [{**LEAK_RULE, "repeat_min": 5}]}, hass, engine.owns)
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    assert len(pushes) == 2 and all(c.data["data"]["tag"] == AID for c in pushes)  # HA's automation
    assert all(inc.get("repeat") is None for inc in engine.active.values())
    # The engine has it too (held as HA holds it, then its group window): off sooner, the engine tells it once more.
    await _later(hass, freezer, notify.GROUP_WINDOW)
    pushes.clear()
    await hass.services.async_call("automation", "turn_off", {"entity_id": _eid(hass)}, blocking=True)  # stops its repeat loop
    await hass.async_block_till_done()
    assert any(inc.get("repeat") for inc in engine.active.values()) or engine.groups.get("leak", {}).get("repeat")
    for _ in range(6):
        await _later(hass, freezer, 60)
    print("DBG", [(c.service, c.data.get("title"), c.data["message"][:60]) for c in pushes]); assert len(pushes) == 2 and "никто не подтвердил" in pushes[0].data["message"]


async def test_automation_deleted_or_unavailable_is_said_and_restored_as_a_change(hass: HomeAssistant, house) -> None:
    """Deleted in HA's editor: the engine tells the alarms; the next apply writes it back as a change of the plan
    («restored»), never silently «added»; unavailable — the engine too."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    hass.states.async_set(_eid(hass), "unavailable")
    assert engine.status()["native"]["leak"]["state"] == "unavailable" and not engine._in_ha("leak")
    (config / "automations.yaml").write_text(dump([THEIRS]))
    await hass.services.async_call("automation", "reload", {}, blocking=True)
    er.async_get(hass).async_remove(_eid(hass))
    await hass.async_block_till_done()
    assert engine.status()["native"]["leak"]["state"] == "missing"
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 30)
    assert len(pushes) == 2  # the engine's
    plan = await independent.async_sync(hass, engine, dry_run=True)
    assert plan["restored"] == ["Протечка"] and not plan["added"] and _ours(config) == []
    res = await independent.async_sync(hass, engine)
    assert res["restored"] == ["Протечка"] and [a["id"] for a in _ours(config)] == [AID]
    assert "Протечка" in independent.summary({**res, "cards": "added"}, True)


async def test_ha_editor_saving_meanwhile_is_never_lost(hass: HomeAssistant, house) -> None:
    """HA review M-2: HA's automation editor doesn't take Hauflo's lock. Saved while a sync runs (between its read and
    its write): their change stays, Hauflo's goes on top; the backup copy holds the text replaced (theirs)."""
    entry, engine, config, _, _, _ = house
    mine = {**THEIRS, "id": "1700000000001", "alias": "Integrator: hall"}
    real = independent._ensure_helper

    async def editor_saves(*args, **kwargs):
        (config / "automations.yaml").write_text(dump([{**THEIRS, "mode": "restart"}, mine]))
        return await real(*args, **kwargs)

    with patch.object(independent, "_ensure_helper", editor_saves):
        res = await independent.async_sync(hass, engine)
    assert res["added"] == ["Протечка"]
    data = load_yaml(str(config / "automations.yaml"))
    assert data[0]["mode"] == "restart" and data[1] == mine and data[2]["id"] == AID
    backup = sorted(config.glob("automations.yaml.hauflo-bak-*"))[-1].read_text()
    assert "restart" in backup and "Integrator: hall" in backup
    # They edited Hauflo's own automation meanwhile: kept as they made it, said.
    engine.config = notify._clean_rules({"lang": "ru", "rules": [{**LEAK_RULE, "repeat_min": 7}]}, hass, engine.owns)

    async def edits_ours(*args, **kwargs):
        rows = load_yaml(str(config / "automations.yaml"))
        rows[2]["mode"] = "single"
        (config / "automations.yaml").write_text(dump(rows))
        return await real(*args, **kwargs)

    with patch.object(independent, "_ensure_helper", edits_ours):
        res = await independent.async_sync(hass, engine)
    assert res["kept_edited"] == ["Протечка"] and not res["updated"] and _ours(config)[0]["mode"] == "single"
    assert "leak" in engine.native


async def test_ha_editor_meanwhile_on_put_add_and_remove(hass: HomeAssistant, house) -> None:
    """The same re-read for every writer of automations.yaml: async_add_new, a Hauflo rule's write, remove_alarms."""
    from custom_components.hauflo import automations, yamlfile

    entry, engine, config, _, _, _ = house
    await independent.async_sync(hass, engine)
    data, before = await automations.Automations(hass, lambda _e: False)._read_file()
    other = {**THEIRS, "id": "1700000000002", "alias": "Integrator: garage"}
    (config / "automations.yaml").write_text(dump([*data, other]))
    path, backup = str(config / "automations.yaml"), str(config / "automations.yaml.hauflo-bak-")
    with pytest.raises(automations.NotPlain):  # no redo: refused, never written over
        await yamlfile.async_write(hass, path, backup, 3, data, before, automations.NotPlain)
    assert other in load_yaml(path)
    added, _ = await automations.async_add_new(hass, [{**THEIRS, "id": "hauflo_auto_x1", "alias": "Hauflo: x"}])
    assert added == ["hauflo_auto_x1"] and other in load_yaml(path)
    assert await independent.async_remove_all(hass, engine) == ["Авария: Протечка"]
    assert other in load_yaml(path) and _ours(config) == []


def test_null_never_rewritten_inside_a_string() -> None:
    """HA review M-3: «: null» at a line's end inside a value was rewritten to «:» by a text replace on the dump."""
    from custom_components.hauflo import automations, yamlfile

    rows = [{"id": "a", "description": "a: null\nb", "value": None, "list": [None, "x: null"]}]
    text = yamlfile.dump(rows)
    assert "value:\n" in text and "- null" not in text  # HA's own «key:» for null
    assert automations.Automations._plain(text) == rows  # reads back the same, plain
    # HA's own dump has that replace too (its editor's file still reads as plain — plain() takes either).
    assert automations.Automations._plain(dump([{"id": "b", "value": None}])) == [{"id": "b", "value": None}]


def test_rebase_leaves_what_they_changed_or_removed() -> None:
    from custom_components.hauflo import yamlfile

    a, b, c = {"id": "a", "v": 1}, {"id": "b", "v": 1}, {"id": "c", "v": 1}
    base = {"a": a, "b": b, "c": c}
    kept: list[str] = []
    redo = yamlfile.rebase({"a": {"id": "a", "v": 2}, "b": None, "c": {"id": "c", "v": 2}, "d": {"id": "d", "v": 1}}, base, lambda x, y: x == y, kept)
    # a — as read: written; b — they changed it: left; c — they removed it: not brought back; d — new: added.
    assert redo([a, {"id": "b", "v": 9}, {"id": "e"}]) == [{"id": "a", "v": 2}, {"id": "b", "v": 9}, {"id": "e"}, {"id": "d", "v": 1}]
    assert sorted(kept) == ["b", "c"]
    assert redo([a, b]) == [{"id": "a", "v": 2}, {"id": "d", "v": 1}]  # b removed as planned


# --- No data is not a change (security N-1, HA M-5, HA N-3 — round 2 review 07.11) ---------------------------------


def _titles(pushes: list[ServiceCall]) -> list[str]:
    return [c.data.get("title") for c in pushes if c.service == "mobile_app_anna"]


async def test_flapping_through_unavailable_is_one_alarm(hass: HomeAssistant, house) -> None:
    """Security N-1: an unaccepted alarm, then 20 × unavailable (2 s) → on (6 s) — the reviewer's 42 pushes. One alarm."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _later(hass, freezer, 60)
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    for _ in range(20):
        hass.states.async_set(LEAK, "unavailable")
        await _later(hass, freezer, 2)
        hass.states.async_set(LEAK, "on")
        await _later(hass, freezer, 6)
    assert len(pushes) == 2 and _titles(pushes) == ["🚨 Протечка"]
    # The first alarm itself coming through no data (a sensor waking up wet) is told — once.
    pushes.clear()
    hass.states.async_set(LEAK2, "unavailable")
    await _later(hass, freezer, 2)
    hass.states.async_set(LEAK2, "on")
    await _later(hass, freezer, 6)
    await _later(hass, freezer, notify.GROUP_WINDOW)
    # HA's automation keeps no memory of the sources it told: the engine tells this one, with its tag.
    # The agent's own push names the unnamed sensor in words, not by its raw id (acceptance 0.33 п.5).
    assert len(pushes) == 2 and "Leak bath" in pushes[0].data["message"] and LEAK2 not in pushes[0].data["message"] and pushes[0].data["data"]["tag"] == AID
    for _ in range(20):
        hass.states.async_set(LEAK2, "unavailable")
        await _later(hass, freezer, 2)
        hass.states.async_set(LEAK2, "on")
        await _later(hass, freezer, 6)
    assert len(pushes) == 2


async def test_alarm_unavailable_normal_flapping_is_bounded(hass: HomeAssistant, house) -> None:
    """Security N-1, scenario B: 20 × on (6 s) → unavailable (2 s) → off (4 s): one alarm, one «back» after the window."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _later(hass, freezer, 60)
    for _ in range(20):
        hass.states.async_set(LEAK, "on")
        await _later(hass, freezer, 6)
        hass.states.async_set(LEAK, "unavailable")
        await _later(hass, freezer, 2)
        hass.states.async_set(LEAK, "off")
        await _later(hass, freezer, 4)
    assert len(pushes) == 4 and _titles(pushes) == ["🚨 Протечка", "📳 Протечка"]  # one alarm; the engine: «дребезжит»
    await _later(hass, freezer, BACK + 5)
    assert _titles(pushes) == ["🚨 Протечка", "📳 Протечка", "✅ Протечка"] and len(pushes) == 6 and AID not in _notes(hass)


async def test_alarm_unavailable_then_normal_says_back(hass: HomeAssistant, house) -> None:
    """HA M-5: alarm → 20 s unavailable → normal for the window: «снова в норме», HA's bell clears, «Принято» resets."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _later(hass, freezer, 60)
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": IDS["ack"]})
    await hass.async_block_till_done()
    pushes.clear()
    hass.states.async_set(LEAK, "unavailable")
    await _later(hass, freezer, 20)
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, BACK - 5)
    assert pushes == [] and set(_notes(hass)) == {AID}
    await _later(hass, freezer, 6)
    assert _titles(pushes) == ["✅ Протечка"] and "снова в норме" in pushes[0].data["message"]
    assert set(_notes(hass)) == set() and hass.states.get(ACK).state == "off"
    # Not accepted this time: the same.
    pushes.clear()
    await _later(hass, freezer, 60)
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    hass.states.async_set(LEAK, "unknown")
    await _later(hass, freezer, 20)
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, BACK + 1)
    assert _titles(pushes) == ["🚨 Протечка", "✅ Протечка"] and set(_notes(hass)) == set()


async def test_no_data_without_an_alarm_says_no_back(hass: HomeAssistant, house) -> None:
    """«back» from no data only for an open alarm: a blip or a long outage of a quiet sensor ends without «✅»."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _later(hass, freezer, 60)
    hass.states.async_set(LEAK, "unavailable")
    await _later(hass, freezer, 20)
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, BACK + 5)
    assert pushes == []
    hass.states.async_set(LEAK, "unavailable")
    await _later(hass, freezer, notify.NODATA_AFTER + 5)
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, BACK + 5)
    assert {c.data["message"] for c in pushes} - {"clear_notification"} and "✅ Протечка" not in _titles(pushes)


async def test_life_safety_second_alarm_within_the_old_window_is_told(hass: HomeAssistant, house) -> None:
    """HA N-3: a leak back in normal for its (short) window, a real second leak 5 min after the first: told. A fault
    rule keeps the common window."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _later(hass, freezer, 60)
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, 240)
    assert _titles(pushes) == ["🚨 Протечка", "✅ Протечка"]
    pushes.clear()
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    assert len(pushes) == 2 and _titles(pushes) == ["🚨 Протечка"]
    assert notify.chatter_window({"kind": "fault"}) == notify.CHATTER_WINDOW > notify.chatter_window({"kind": "smoke"}) == notify.CHATTER_WINDOW_LIFE
    assert all(notify.chatter_window({"kind": k}) == notify.CHATTER_WINDOW_LIFE for k in ("leak", "smoke", "gas", "carbon_monoxide"))


async def test_rule_words_are_plain(hass: HomeAssistant, house) -> None:
    """Security N-6: a template or markup in alarm_states / kind never reaches an automation."""
    entry, engine, *_ = house
    bad = {**LEAK_RULE, "kind": "{{ x }}", "alarm_states": ["{{ 'off' }}", "on", "Wet<b>"]}
    # A bad kind falls to «custom»; bad alarm_states reject the rule with a reason, never a silent `on` (review r3 R3-9).
    ok = {**bad, "alarm_states": ["on"]}
    got = notify._clean_rules({"lang": "ru", "rules": [ok]}, hass, engine.owns)["rules"][0]
    assert got["kind"] == "custom" and got["alarm_states"] == ["on"]
    out = notify._clean_rules({"lang": "ru", "rules": [bad]}, hass, engine.owns)
    assert out["rules"] == [] and out["rejected"][0]["reason"] == "bad_alarm_states"


async def test_alarm_then_a_long_outage_then_normal(hass: HomeAssistant, house) -> None:
    """0.27.0's known limit gone (0.31): an unaccepted alarm, no data longer than NODATA_AFTER («нет связи» from the
    engine), then normal for the window — «✅» and HA's bell cleared (the open alarm is the helper's, not a chain)."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _later(hass, freezer, 60)
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    hass.states.async_set(LEAK, "unavailable")
    await _later(hass, freezer, notify.NODATA_AFTER + 5)
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, BACK + 5)
    assert "✅ Протечка" in _titles(pushes) and AID not in set(_notes(hass)) and hass.states.get(OPEN).state == "off"


# --- 0.31: as an integrator writes it by hand (owner 10.11) — small; chatter and «нет связи» are the engine's ----------
OPEN = "input_boolean." + IDS["open"]
CHAT = "input_text." + IDS["chatter"]
BAT_A, BAT_B, GW = "sensor.bat_hall", "sensor.bat_bath", "binary_sensor.gateway"


def _size(a: dict) -> tuple[int, int]:
    return len(a["triggers"]), len(a["actions"])


async def test_every_kind_is_small_and_ha_checks_it(hass: HomeAssistant, house) -> None:
    """≤ 3 triggers / ≤ 3 actions for a signal, a link, levels with one threshold and with different ones beside a
    signal; HA's own validation (the write) and its check_config take the file."""
    from homeassistant.helpers.check_config import async_check_ha_config_file

    entry, engine, config, *_ = house
    for e in (BAT_A, BAT_B):
        hass.states.async_set(e, "80", {"device_class": "battery", "unit_of_measurement": "%"})
    hass.states.async_set(GW, "on", {"device_class": "connectivity"})
    rec = {"recipients": {"all": True}, "repeat_min": 5}
    rules = [
        {**LEAK_RULE, **rec},
        {**rec, "id": "gw", "kind": "connectivity", "title": "Шлюз", "sources": [GW], "picked": [GW], "alarm_states": ["off"]},
        {**rec, "id": "bat", "kind": "battery", "title": "Батарея", "sources": [BAT_A, BAT_B], "picked": [BAT_A, BAT_B], "alarm_states": ["on"],
         "thresholds": {BAT_A: {"below": 20}, BAT_B: {"below": 20}}},
        {**rec, "id": "mix", "kind": "battery", "title": "Смешанные", "sources": [BAT_A, BAT_B, LEAK], "picked": [BAT_A, BAT_B, LEAK], "alarm_states": ["on"],
         "thresholds": {BAT_A: {"below": 20}, BAT_B: {"below": 30}}},
    ]
    engine.config = notify._clean_rules({"lang": "ru", "rules": rules}, hass, engine.owns)
    assert len(engine.config["rules"]) == 4, engine.config.get("rejected")
    res = await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    assert sorted(res["added"]) == sorted(r["title"] for r in rules) and not res["engine"], res
    ours = {a["id"]: a for a in _ours(config)}
    assert {k: _size(a) for k, a in ours.items()} == dict.fromkeys(ours, (3, 2))
    assert all(a["mode"] == "restart" and "conditions" not in a for a in ours.values())
    gw, bat, mix = (ours[independent.automation_id(x)] for x in ("gw", "bat", "mix"))
    assert gw["triggers"][0]["not_from"] == independent.NO_DATA
    # One threshold: HA's below / above as written by hand (the back past the step); different ones: the margin.
    assert {k: bat["triggers"][0][k] for k in ("trigger", "below")} == {"trigger": "numeric_state", "below": 20.0} and "value_template" not in bat["triggers"][0]
    assert bat["triggers"][1]["above"] == 25.0 and bat["triggers"][1]["for"] == {"seconds": independent.HOLD}
    assert "value_template" in mix["triggers"][0] and mix["triggers"][0]["below"] == 0
    check = await async_check_ha_config_file(hass)
    assert not check.errors, [e.message for e in check.errors]


async def test_the_push_names_the_place(hass: HomeAssistant, house) -> None:
    """«Протечка на кухне · Кухня: авария.» — the sensor's name and its area (HA's area_name)."""
    from homeassistant.helpers import area_registry as ar

    entry, engine, config, pushes, _, freezer = house
    reg = er.async_get(hass)
    hass.states.async_remove(LEAK)
    ent = reg.async_get_or_create("binary_sensor", "test", "leak_k", suggested_object_id="leak_kitchen")
    assert ent.entity_id == LEAK
    hass.states.async_set(LEAK, "off", {"friendly_name": "Протечка на кухне"})
    reg.async_update_entity(LEAK, area_id=ar.async_get(hass).async_create("Кухня").id)
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    hass.states.async_set(LEAK, "on", {"friendly_name": "Протечка на кухне"})
    await _later(hass, freezer, 6)
    assert _by(pushes)["mobile_app_anna"]["message"] == "Протечка на кухне · Кухня: авария."
    pushes.clear()
    hass.states.async_set(LEAK, "off", {"friendly_name": "Протечка на кухне"})
    await _later(hass, freezer, BACK + 1)
    assert _by(pushes)["mobile_app_anna"]["message"] == "Протечка на кухне · Кухня: снова в норме." and _by(pushes)["mobile_app_anna"]["data"]["tag"] == AID


async def test_open_helper_follows_the_alarm(hass: HomeAssistant, house) -> None:
    """«авария открыта»: on with the alarm told, off with «снова в норме»; HA's own, removed with the rule."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    assert hass.states.get(OPEN).state == "off" and engine.native["leak"]["open"] == OPEN and "chatter" not in engine.native["leak"]
    assert ownership.kind_of(er.async_get(hass).async_get(OPEN), set(), set()) == "alarm"
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    assert hass.states.get(OPEN).state == "on"
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, BACK + 1)
    assert hass.states.get(OPEN).state == "off"
    await independent.async_remove_all(hass, engine)
    await hass.async_block_till_done()
    assert hass.states.get(OPEN) is None and hass.states.get(ACK) is None


async def test_accept_from_the_phone_stops_the_repeat(hass: HomeAssistant, house) -> None:
    entry, engine, config, pushes, closed, freezer = house
    engine.config = notify._clean_rules({"lang": "ru", "rules": [{**LEAK_RULE, "repeat_min": 5}]}, hass, engine.owns)
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    for _ in range(5):
        await _later(hass, freezer, 60)
    assert len(pushes) == 4 and "никто не подтвердил" in pushes[-1].data["message"]
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": IDS["ack"]})
    await hass.async_block_till_done()
    pushes.clear()
    for _ in range(16):
        await _later(hass, freezer, 60)
    assert pushes == []
    # The button still works after «Принято» (closing the valve is still worth it): the run waits for it.
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": IDS["run"]})
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in closed] == [[VALVE]]


async def _old_0_30(hass: HomeAssistant, engine, config: Path, digest_ok: bool) -> dict:
    """The rule as 0.30 left it: its big automation, «Принято» and the chatter memory (input_text), a record with it."""
    from custom_components.hauflo import automations

    assert await async_setup_component(hass, "input_text", {})
    await hass.async_block_till_done()
    coll = await independent._helpers(hass)
    ack = await independent._ensure_helper(hass, coll, IDS["helper"], "Hauflo · Принято: Протечка", independent.ICON)
    texts = independent._collection(hass, "input_text")
    await texts.async_create_item({"name": IDS["chatter"], "max": 255})
    old = {"id": AID, "alias": "Авария: Протечка", "mode": "queued", "max": 10,
           "triggers": [{"trigger": "state", "entity_id": [LEAK, LEAK2], "to": ["on"], "id": f"t{i}"} for i in range(15)],
           "conditions": [{"condition": "template", "value_template": "{{ true }}"}],
           "actions": [{"choose": [{"conditions": [{"condition": "trigger", "id": "t0"}], "sequence": [{"action": "input_text.set_value", "target": {"entity_id": CHAT}, "data": {"value": ""}}]}]}]}
    (config / "automations.yaml").write_text(dump([THEIRS, old]))
    await hass.services.async_call("automation", "reload", {}, blocking=True)
    rec = {"automation": AID, "helper": ack, "chatter": CHAT, "digest": automations._digest(old) if digest_ok else "edited", "ack": IDS["ack"], "run": IDS["run"], "lang": "ru"}
    await engine.async_set_native({"leak": rec})
    await hass.async_block_till_done()
    assert hass.states.get(CHAT) is not None and not engine._engine_says("leak")  # 0.30's automation says chatter itself
    return old


async def test_upgrade_from_0_30_rewrites_and_takes_the_chatter_memory(hass: HomeAssistant, house) -> None:
    entry, engine, config, pushes, _, freezer = house
    await _old_0_30(hass, engine, config, digest_ok=True)
    hass.states.async_set(LEAK, "on")  # an alarm open across the upgrade
    await _later(hass, freezer, 6)
    plan = await independent.async_sync(hass, engine, dry_run=True)
    assert plan["updated"] == ["Протечка"] and hass.states.get(CHAT) is not None
    res = await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    assert res["updated"] == ["Протечка"] and not res["kept_edited"]
    new = _ours(config)[0]
    assert _size(new) == (3, 2) and new["mode"] == "restart" and "input_text" not in str(new)
    assert hass.states.get(CHAT) is None and er.async_get(hass).async_get(CHAT) is None
    assert "chatter" not in engine.native["leak"] and engine.native["leak"]["open"] == OPEN and engine._engine_says("leak")
    assert hass.states.get(OPEN).state == "on"  # the open alarm moved over: its «back» is HA's to say
    pushes.clear()
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, BACK + 1)
    assert _titles(pushes) == ["✅ Протечка"] and hass.states.get(OPEN).state == "off"


async def test_upgrade_leaves_a_hand_edited_rule_with_its_memory(hass: HomeAssistant, house) -> None:
    entry, engine, config, *_ = house
    old = await _old_0_30(hass, engine, config, digest_ok=False)
    res = await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    assert res["kept_edited"] == ["Протечка"] and not res["updated"]
    assert _ours(config) == [old] and hass.states.get(CHAT) is not None and hass.states.get(OPEN) is None
    assert engine.native["leak"]["chatter"] == CHAT and not engine._engine_says("leak")
    # «Убрать аварии Hauflo»: every helper kind goes, the memory too.
    await independent.async_remove_all(hass, engine)
    await hass.async_block_till_done()
    assert hass.states.get(CHAT) is None and hass.states.get(ACK) is None and _ours(config) == []


async def test_upgrade_drops_a_chatter_memory_whose_automation_is_gone(hass: HomeAssistant, house) -> None:
    """Acceptance 10.11 (taste): 0.30's automation deleted in HA by hand, its memory left — nothing reads it: it goes on
    the next sync (whether the rule is still there or not); someone else's input_text is never touched."""
    entry, engine, config, *_ = house
    await _old_0_30(hass, engine, config, digest_ok=True)
    texts = independent._collection(hass, "input_text")
    await texts.async_create_item({"name": "hauflo_chatter_gone_000000", "max": 255})
    await texts.async_create_item({"name": "kitchen_note", "max": 255})
    (config / "automations.yaml").write_text(dump([THEIRS]))
    await hass.services.async_call("automation", "reload", {}, blocking=True)
    plan = await independent.async_sync(hass, engine, dry_run=True)
    assert hass.states.get("input_text.hauflo_chatter_gone_000000") is not None and not plan["removed"]
    res = await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    assert res["restored"] == ["Протечка"] and hass.states.get(CHAT) is None
    assert hass.states.get("input_text.hauflo_chatter_gone_000000") is None and hass.states.get("input_text.kitchen_note") is not None


async def _slow(hass: HomeAssistant, freezer, entity: str, times: int, on: int = 6, off: int = 70) -> None:
    for _ in range(times):
        hass.states.async_set(entity, "on")
        await _later(hass, freezer, on)
        hass.states.async_set(entity, "off")
        await _later(hass, freezer, off)


async def test_slow_chatter_the_engine_says_the_sensor_is_faulty(hass: HomeAssistant, house) -> None:
    """Accepted loss (owner 10.11, no memory in YAML): HA tells each alarm held after the back window — the engine adds
    one «дребезжит» per sensor, quiet (not critical), in the rule's words: «HA keeps telling»."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _later(hass, freezer, 60)
    await _slow(hass, freezer, LEAK, 5)
    chat = [c for c in pushes if c.data["title"].startswith("📳")]
    assert len(chat) == len(PHONES) and "неисправный датчик" in chat[0].data["message"] and "приглуш" not in chat[0].data["message"]
    assert "interruption-level" not in str(chat[0].data["data"])
    assert _titles(pushes).count("🚨 Протечка") == 5  # HA's own: each one held after the window


async def test_chatter_words_in_english(hass: HomeAssistant, house) -> None:
    """R3-7: «дребезжит» in the rule's language — HA's rule and the engine's own."""
    entry, engine, config, pushes, _, freezer = house
    engine.config = notify._clean_rules({"lang": "en", "rules": [LEAK_RULE]}, hass, engine.owns)
    engine._apply()
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    await _later(hass, freezer, 60)
    await _slow(hass, freezer, LEAK, 3)
    chat = [c for c in pushes if c.data["title"].startswith("📳")]
    assert len(chat) == 2 and "looks like a faulty sensor" in chat[0].data["message"] and "раз" not in chat[0].data["message"]
    sent: list[tuple[str, str]] = []

    async def fake_send(rule, title, message, **kw):
        sent.append((title, message))
        return []

    engine.native = {}
    with patch.object(engine, "_send", fake_send):
        await engine._say_chatter(engine.config["rules"][0], hass.states.get(LEAK))
    assert sent and sent[0][0].startswith("📳 ") and "looks like a faulty sensor" in sent[0][1] and "10 min" in sent[0][1]


def _nd(pushes: list[ServiceCall]) -> list[ServiceCall]:
    return [c for c in pushes if str(c.data["data"].get("tag", "")).endswith("_nd")]


async def test_no_data_is_the_engines_for_a_rule_ha_runs(hass: HomeAssistant, house) -> None:
    """HA M-4 in 0.31: «нет связи» — one per rule (a gateway down takes every source), from the agent's engine though
    HA runs the rule; cleared by the last one back. English for an English project."""
    entry, engine, config, pushes, _, freezer = house
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    hass.states.async_set(LEAK, "unavailable")
    hass.states.async_set(LEAK2, "unavailable")
    await _later(hass, freezer, notify.NODATA_AFTER + 1)
    await _later(hass, freezer, notify.GROUP_WINDOW + 1)
    nd = _nd(pushes)
    assert len(nd) == len(PHONES) and "нет связи" in nd[0].data["title"] and "interruption-level" not in str(nd[0].data["data"])
    assert any(k.endswith("_nd") for k in _notes(hass))
    pushes.clear()
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, 1)
    hass.states.async_set(LEAK2, "off")
    await _later(hass, freezer, notify.GROUP_WINDOW + 1)
    assert _nd(pushes) and not any(k.endswith("_nd") for k in _notes(hass))
    assert not [c for c in pushes if c.data.get("title", "").startswith("✅ Протечка") and "норм" in c.data["message"]]  # no data is not an alarm
    # English.
    pushes.clear()
    engine.config = notify._clean_rules({"lang": "en", "rules": [LEAK_RULE]}, hass, engine.owns)
    engine._apply()
    hass.states.async_set(LEAK, "unavailable")
    await _later(hass, freezer, notify.NODATA_AFTER + 1)
    await _later(hass, freezer, notify.GROUP_WINDOW + 1)
    nd = _nd(pushes)
    assert nd and nd[0].data["title"].endswith(": no data") and nd[0].data["message"].startswith(f"No data for {notify.NODATA_AFTER // 60} min: ")
    assert "Нет" not in nd[0].data["message"] and "связ" not in nd[0].data["message"]



async def _restart(hass: HomeAssistant, engine, freezer, first: str, delay: float = 0) -> None:
    """HA stops (the run dies with the automation, the engine saves) and starts: the sensor's first state is ``first``
    (none before it — HA's state triggers see no change from «on»), ``delay`` s after the engine is up."""
    await hass.services.async_call("automation", "turn_off", {"entity_id": "all"}, blocking=True)
    engine.async_stop()
    await hass.async_block_till_done()
    hass.states.async_remove(LEAK)
    await engine.async_start()
    await hass.services.async_call("automation", "turn_on", {"entity_id": "all"}, blocking=True)
    await hass.async_block_till_done()
    if delay:
        await _later(hass, freezer, delay)
    hass.states.async_set(LEAK, first, {"device_class": "moisture", "friendly_name": "Протечка на кухне"})
    await hass.async_block_till_done()


async def test_back_to_normal_while_ha_restarted_is_said_once(hass: HomeAssistant, house) -> None:
    """Acceptance 10.11 (medium): the alarm open, HA restarts, the sensor is dry again before the automation is back —
    its «back» waits for a change from «on» it never sees; «авария открыта» stayed on for good, nobody told. The engine
    says «снова в норме» once (the automation's tag) and closes it; HA's own «back» is never doubled."""
    entry, engine, config, pushes, _, freezer = house
    await engine.store.async_save(engine.config)
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    assert len(pushes) == 2 and hass.states.get(OPEN).state == "on"
    pushes.clear()
    # Dry 2 s after the start: looked at once it has held the automation's window.
    await _restart(hass, engine, freezer, "off", delay=2)
    assert hass.states.get(OPEN).state == "on"  # not switched off quietly
    await _later(hass, freezer, BACK + notify.GROUP_WINDOW - 1)
    assert pushes == []
    await _later(hass, freezer, 3)
    assert hass.states.get(OPEN).state == "off" and hass.states.get(ACK).state == "off" and not engine.active
    assert len(pushes) == 2 and _titles(pushes) == ["✅ Протечка"] and {c.data["data"]["tag"] for c in pushes} == {AID}
    assert _by(pushes)["mobile_app_anna"]["message"] == "Протечка на кухне: снова в норме." and AID not in set(_notes(hass))
    await _later(hass, freezer, 3600)
    assert len(pushes) == 2  # once
    # Open again, HA restarts, the sensor first «unavailable» (not read yet): nothing said, still open …
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    pushes.clear()
    await _restart(hass, engine, freezer, "unavailable")
    await _later(hass, freezer, BACK + notify.GROUP_WINDOW + 1)
    assert pushes == [] and hass.states.get(OPEN).state == "on"
    # … then dry: HA's automation sees it (from «unavailable») and says it — the engine not again.
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, 3 * (BACK + notify.GROUP_WINDOW))
    assert len(pushes) == 2 and _titles(pushes) == ["✅ Протечка"] and hass.states.get(OPEN).state == "off"


async def test_a_flickering_second_source_never_puts_the_repeat_off(hass: HomeAssistant, house) -> None:
    """Acceptance 10.11 (medium): the alarm open, a second sensor flickers faster than the repeat — every «back» of it
    restarts the run (mode: restart); the repeat is counted from the alarm's opening, so it still comes on time."""
    entry, engine, config, pushes, _, freezer = house
    engine.config = notify._clean_rules({"lang": "ru", "rules": [{**LEAK_RULE, "repeat_min": 5}]}, hass, engine.owns)
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, 6)
    pushes.clear()
    for _ in range(10):  # 10 min; the bathroom sensor wet for 10 s, dry for 50 s, every minute
        hass.states.async_set(LEAK2, "on")
        await _later(hass, freezer, 10)
        hass.states.async_set(LEAK2, "off")
        await _later(hass, freezer, 50)
    repeats = [c for c in pushes if "никто не подтвердил" in c.data["message"]]
    assert len(repeats) == 4  # at 5 and 10 minutes, both phones
