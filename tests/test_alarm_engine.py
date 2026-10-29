"""The alarm engine end to end (notify.py): single alarms, a summary for many devices, repeats, acceptance (push button
and dashboard), a return to normal, no-data and chattering sensors, an HA restart in the middle of an alarm, the views."""

import json
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, label_registry as lr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hauflo import MONITOR_LABELS, notify
from custom_components.hauflo.const import LOCKED_CODE

PHONES = [{"service": "mobile_app_ivan", "name": "iPhone", "os": "ios", "person": "person.ivan"}, {"service": "mobile_app_anna", "name": "Pixel", "os": "android", "person": None}]


class Rig:
    def __init__(self, hass):
        self.hass = hass
        hass.config.language = "ru"
        self.pushes: list[dict] = []
        self.eng = notify.NotifyEngine(hass)
        self.eng.phones = lambda: PHONES  # type: ignore[method-assign]
        self.eng.owns = lambda eid: True

        async def push(call):
            self.pushes.append({"service": call.service, **call.data})

        for p in PHONES:
            hass.services.async_register("notify", p["service"], push)

    def titles(self):
        return [p.get("title") for p in self.pushes if p["service"] == "mobile_app_ivan"]

    def messages(self):
        return [p["message"] for p in self.pushes if p["service"] == "mobile_app_ivan"]

    def last(self):
        return [p for p in self.pushes if p["service"] == "mobile_app_ivan"][-1]

    def clear(self):
        self.pushes.clear()

    async def rules(self, *rules, **extra):
        out = []
        for r in rules:
            out.append({"id": "r1", "kind": "leak", "title": "Протечка", "alarm_states": ["on"], "recipients": {"all": True}, "repeat_min": 0, **r, "picked": r["sources"]})
        await self.eng.async_set({"rules": out, **extra})

    async def tick(self, seconds):
        async_fire_time_changed(self.hass, dt_util.utcnow() + timedelta(seconds=seconds))
        await self.hass.async_block_till_done()


@pytest.fixture
async def rig(hass: HomeAssistant):
    r = Rig(hass)
    await r.eng.async_start()
    yield r
    r.eng.async_stop()
    await r.tick(10)


def sensors(hass, *names, state="off", **attrs):
    for n in names:
        hass.states.async_set(f"binary_sensor.{n}", state, {"friendly_name": n.upper(), "device_class": "moisture", **attrs})


async def test_single_alarm_repeat_accept_and_clear(hass, rig) -> None:
    sensors(hass, "kitchen")
    await rig.rules({"sources": ["binary_sensor.kitchen"], "repeat_min": 5, "priority": "critical"})
    hass.states.async_set("binary_sensor.kitchen", "on", {"friendly_name": "KITCHEN", "device_class": "moisture"})
    await rig.tick(4)
    assert rig.titles() == ["🚨 Протечка"] and "KITCHEN: авария в " in rig.messages()[0]
    first = rig.last()
    assert first["data"]["push"]["interruption-level"] == "critical" and [a["action"] for a in first["data"]["actions"]] == [f"{notify.ACK}|r1|binary_sensor.kitchen"]
    assert rig.eng.active["r1|binary_sensor.kitchen"]["pushed"] is True
    # nobody accepts: it repeats
    rig.clear()
    await rig.tick(6 * 60)
    assert "Повтор 2" in rig.messages()[0]
    # accepted from the dashboard by a person: the repeats stop and the others hear who
    rig.clear()
    assert await rig.eng.async_ack("r1|binary_sensor.kitchen", "Ivan") is True
    assert rig.messages() == ["KITCHEN: Принято: Ivan."]
    rig.clear()
    await rig.tick(7 * 60)
    assert rig.pushes == []
    # unknown key: nothing
    assert await rig.eng.async_ack("r1|binary_sensor.nope", "Ivan") is False
    # back to normal: a critical one accepted leaves nothing to look at, and the phones are told
    hass.states.async_set("binary_sensor.kitchen", "off", {"friendly_name": "KITCHEN", "device_class": "moisture"})
    await rig.tick(4)
    assert rig.titles() == ["✅ Протечка"] and "снова в норме" in rig.messages()[0]
    assert rig.eng.active == {} and rig.eng.unacked_rtn == {}


async def test_critical_back_to_normal_unaccepted_stays_until_accepted(hass, rig) -> None:
    sensors(hass, "boiler")
    await rig.rules({"sources": ["binary_sensor.boiler"], "priority": "critical"})
    hass.states.async_set("binary_sensor.boiler", "on", {"device_class": "moisture"})
    await rig.tick(4)
    hass.states.async_set("binary_sensor.boiler", "off", {"device_class": "moisture"})
    await rig.tick(4)
    key = "r1|binary_sensor.boiler"
    assert key in rig.eng.unacked_rtn  # ISA-18.2: returned, unacknowledged
    # in alarm again: the open incident is what is shown
    hass.states.async_set("binary_sensor.boiler", "on", {"device_class": "moisture"})
    await rig.tick(4)
    assert key not in rig.eng.unacked_rtn and key in rig.eng.active
    hass.states.async_set("binary_sensor.boiler", "off", {"device_class": "moisture"})
    await rig.tick(4)
    assert key in rig.eng.unacked_rtn
    assert await rig.eng.async_ack(key, "Anna") is True and rig.eng.unacked_rtn == {}


async def test_many_devices_are_one_summary_and_one_accept(hass, rig) -> None:
    names = ["a1", "a2", "a3"]
    area = ar.async_get(hass).async_create("Холл")
    reg = er.async_get(hass)
    for n in names:
        reg.async_get_or_create("binary_sensor", "test", n, suggested_object_id=n)
    reg.async_update_entity("binary_sensor.a1", area_id=area.id)
    sensors(hass, *names)
    await rig.rules({"sources": [f"binary_sensor.{n}" for n in names], "repeat_min": 5})
    for n in names:
        hass.states.async_set(f"binary_sensor.{n}", "on", {"friendly_name": n.upper(), "device_class": "moisture"})
    await rig.tick(4)
    assert rig.titles() == ["🚨 Протечка — 3 устройства"] and rig.last()["data"]["tag"] == "hauflo_r1_all"
    assert "Холл: A1" in rig.messages()[0] and rig.eng.groups["r1"]["count"] == 1
    assert all(i["pushed"] for i in rig.eng.active.values())
    # repeat of the summary
    rig.clear()
    await rig.tick(6 * 60)
    assert "Никто не подтвердил. Повтор 2" in rig.messages()[0]
    # one accept for all of them
    rig.clear()
    await rig.eng.async_ack("r1|*", "Ivan")
    assert rig.messages() == ["3 устройства: Принято: Ivan."] and all(i["acked_by"] == "Ivan" for i in rig.eng.active.values())
    # one back to normal: the summary is updated quietly; all back: replaced by "all fine"
    rig.clear()
    hass.states.async_set("binary_sensor.a1", "off", {"friendly_name": "A1", "device_class": "moisture"})
    await rig.tick(4)
    assert rig.pushes == [] or rig.titles() == []  # the accepted ones aren't news again
    for n in ("a2", "a3"):
        hass.states.async_set(f"binary_sensor.{n}", "off", {"friendly_name": n.upper(), "device_class": "moisture"})
    await rig.tick(4)
    assert "✅ Протечка — 2 устройства" in rig.titles() and "r1" not in rig.eng.groups


async def test_group_gets_a_late_member_and_returns_are_quiet_updates(hass, rig) -> None:
    names = ["b1", "b2", "b3", "b4", "b5"]
    sensors(hass, *names)
    await rig.rules({"sources": [f"binary_sensor.{n}" for n in names]})
    on = lambda n: hass.states.async_set(f"binary_sensor.{n}", "on", {"friendly_name": n.upper(), "device_class": "moisture"})  # noqa: E731
    off = lambda n: hass.states.async_set(f"binary_sensor.{n}", "off", {"friendly_name": n.upper(), "device_class": "moisture"})  # noqa: E731
    on("b1")
    await rig.tick(4)
    assert rig.titles() == ["🚨 Протечка"] and rig.eng.active["r1|binary_sensor.b1"]["own"] is True
    on("b2")
    on("b3")
    on("b4")
    await rig.tick(4)  # three at once: one summary, with the not yet accepted single in it; its own message leaves the phones
    assert "🚨 Протечка — 4 устройства" in rig.titles()
    assert any(p["message"] == "clear_notification" for p in rig.pushes)
    assert all(i["group"] for i in rig.eng.active.values())
    # a new one while the summary is open and not accepted joins it
    rig.clear()
    on("b5")
    await rig.tick(4)
    assert rig.titles() == ["🚨 Протечка — 5 устройств"]
    # one returns: the summary is re-said quietly ("passive" on iOS) with the return in it
    rig.clear()
    off("b1")
    await rig.tick(4)
    assert rig.last()["data"]["push"] == {"interruption-level": "passive"} and "Снова в норме" in rig.messages()[0]
    # a single returns to normal while the rest of the group stays open: it is part of the summary


async def test_singles_returning_together_are_one_message(hass, rig) -> None:
    names = ["c1", "c2", "c3"]
    sensors(hass, *names)
    await rig.rules({"sources": [f"binary_sensor.{n}" for n in names]})
    for n in names:
        hass.states.async_set(f"binary_sensor.{n}", "on", {"friendly_name": n.upper(), "device_class": "moisture"})
    await rig.tick(4)
    for n in names[:2]:
        hass.states.async_set(f"binary_sensor.{n}", "off", {"friendly_name": n.upper(), "device_class": "moisture"})
    await rig.tick(4)
    assert rig.eng.active.keys() == {"r1|binary_sensor.c3"}
    # flicker inside the window: never told, nothing to take back
    rig.clear()
    sensors(hass, "d1")
    await rig.rules({"sources": ["binary_sensor.d1"]})
    hass.states.async_set("binary_sensor.d1", "on", {"device_class": "moisture"})
    hass.states.async_set("binary_sensor.d1", "off", {"device_class": "moisture"})
    await rig.tick(4)
    assert rig.pushes == []


async def test_push_button_runs_only_the_rules_action_and_only_while_open(hass, rig) -> None:
    sensors(hass, "leak")
    hass.states.async_set("valve.main", "open", {"friendly_name": "Main valve"})
    calls = []

    async def close(call):
        calls.append(call.data["entity_id"])

    hass.services.async_register("valve", "close_valve", close)
    await rig.rules({"sources": ["binary_sensor.leak"], "action": {"title": "x", "service": "valve.close_valve", "entity_id": "valve.main"}})
    hass.states.async_set("binary_sensor.leak", "on", {"friendly_name": "LEAK", "device_class": "moisture"})
    await rig.tick(4)
    acts = [a["action"] for a in rig.last()["data"]["actions"]]
    assert acts == [f"{notify.RUN}|r1|binary_sensor.leak", f"{notify.ACK}|r1|binary_sensor.leak"] and rig.last()["data"]["actions"][0]["destructive"] is True
    user = NS(name="Ivan")
    hass.auth.async_get_user = AsyncMock(return_value=user)
    rig.clear()
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": f"{notify.RUN}|r1|binary_sensor.leak"}, context=Context(user_id="u1"))
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": "other|x"})
    await rig.tick(1)
    assert calls == ["valve.main"] and rig.messages() == ["LEAK: Закрыть: Main valve: выполнено (Ivan)."]
    # an old button on an alarm that is over runs nothing
    hass.states.async_set("binary_sensor.leak", "off", {"device_class": "moisture"})
    await rig.tick(4)
    rig.clear()
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": f"{notify.RUN}|r1|binary_sensor.leak"})
    await rig.tick(1)
    assert calls == ["valve.main"] and rig.pushes == []
    # a failing action is said, not swallowed
    async def broken(call):
        raise RuntimeError("stuck valve")

    hass.services.async_remove("valve", "close_valve")
    hass.services.async_register("valve", "close_valve", broken)
    hass.states.async_set("binary_sensor.leak", "on", {"friendly_name": "LEAK", "device_class": "moisture"})
    await rig.tick(4)
    rig.clear()
    hass.auth.async_get_user = AsyncMock(return_value=None)
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": f"{notify.RUN}|r1|binary_sensor.leak"})
    await rig.tick(1)
    assert "не получилось — stuck valve" in rig.messages()[0]


async def test_restart_in_the_middle_of_an_alarm(hass, rig) -> None:
    names = ["e1", "e2", "e3"]
    sensors(hass, *names)
    await rig.rules({"sources": [f"binary_sensor.{n}" for n in names], "repeat_min": 5}, groups=[])
    for n in names:
        hass.states.async_set(f"binary_sensor.{n}", "on", {"friendly_name": n.upper(), "device_class": "moisture"})
    await rig.tick(4)
    assert "r1" in rig.eng.groups
    snapshot = rig.eng._snapshot()
    rig.eng.async_stop()
    # HA starts again: the saved alarms are taken back without telling anyone; one sensor was fine again while down
    hass.states.async_set("binary_sensor.e3", "off", {"friendly_name": "E3", "device_class": "moisture"})
    eng2 = notify.NotifyEngine(hass)
    eng2.phones = lambda: PHONES  # type: ignore[method-assign]
    eng2.owns = lambda eid: True
    eng2.active_store.async_load = AsyncMock(return_value=json.loads(json.dumps(snapshot)))
    eng2.store.async_load = AsyncMock(return_value={"rules": [{"id": "r1", "kind": "leak", "title": "Протечка", "sources": [f"binary_sensor.{n}" for n in names], "picked": [f"binary_sensor.{n}" for n in names], "alarm_states": ["on"], "recipients": {"all": True}, "repeat_min": 5}]})
    rig.clear()
    await eng2.async_start()
    await rig.tick(4)
    assert set(eng2.active) == {"r1|binary_sensor.e1", "r1|binary_sensor.e2"} and "r1" in eng2.groups
    assert eng2.groups["r1"]["repeat"] is not None  # the summary's repeat goes on
    assert [a["key"] for a in eng2.active_list()] == ["r1|binary_sensor.e1", "r1|binary_sensor.e2"]
    assert not any("🚨" in (t or "") and "устройства" in (t or "") and "Повтор" in p["message"] for p in rig.pushes for t in [p.get("title")])
    eng2.async_stop()
    # rules not here yet (a re-added agent starts empty): the open alarms wait for them
    eng3 = notify.NotifyEngine(hass)
    eng3.active_store.async_load = AsyncMock(return_value=json.loads(json.dumps(snapshot)))
    eng3.store.async_load = AsyncMock(return_value=None)
    await eng3.async_start()
    assert len(eng3._restored) == 3 and eng3.active_list() == []
    eng3.async_stop()
    await rig.tick(10)


async def test_a_restored_single_alarm_is_taken_back_and_ended_when_over(hass, rig) -> None:
    sensors(hass, "f1", "f2")
    saved = {"active": {"r1|binary_sensor.f1": {"since": "2026-10-29T01:00:00+00:00", "acked_by": None, "count": 1, "last": dt_util.utcnow().isoformat()}, "r1|binary_sensor.f2": {"since": "2026-10-29T01:00:00+00:00", "acked_by": "Ivan"}}, "seen_ok": ["binary_sensor.f1"], "rtn": {"x": {"since": "s"}, "y": 5}}
    eng = notify.NotifyEngine(hass)
    eng.phones = lambda: PHONES  # type: ignore[method-assign]
    eng.owns = lambda eid: True
    rule = {"id": "r1", "kind": "leak", "title": "Протечка", "sources": ["binary_sensor.f1", "binary_sensor.f2"], "picked": ["binary_sensor.f1", "binary_sensor.f2"], "alarm_states": ["on"], "recipients": {"all": True}, "repeat_min": 5}
    eng.store.async_load = AsyncMock(return_value={"rules": [rule]})
    eng.active_store.async_load = AsyncMock(return_value=saved)
    hass.states.async_set("binary_sensor.f1", "on", {"friendly_name": "F1", "device_class": "moisture"})
    # f2 is fine again: ended while HA was down
    await eng.async_start()
    assert "r1|binary_sensor.f1" in eng.active and eng.active["r1|binary_sensor.f1"]["repeat"] is not None
    assert "r1|binary_sensor.f2" not in eng.active
    assert eng.unacked_rtn == {"x": {"since": "s"}}
    # the sensor reports again after the start (KNX reads the bus later): a restored one is adopted on its first state
    eng.async_stop()
    eng = notify.NotifyEngine(hass)
    eng.owns = lambda eid: True
    eng.phones = lambda: PHONES  # type: ignore[method-assign]
    eng.store.async_load = AsyncMock(return_value={"rules": [rule]})
    eng.active_store.async_load = AsyncMock(return_value={"active": {"r1|binary_sensor.g1": {"since": "2026-10-29T01:00:00+00:00"}, "r1|binary_sensor.g2": {"since": "2026-10-29T01:00:00+00:00"}}})
    eng.config = {"rules": [], "groups": []}
    rule2 = {**rule, "sources": ["binary_sensor.g1", "binary_sensor.g2"], "picked": ["binary_sensor.g1", "binary_sensor.g2"]}
    eng.store.async_load = AsyncMock(return_value={"rules": [rule2]})
    await eng.async_start()
    assert set(eng._restored) == {"r1|binary_sensor.g1", "r1|binary_sensor.g2"} and len(eng.active_list()) == 2
    hass.states.async_set("binary_sensor.g1", "on", {"device_class": "moisture"})
    hass.states.async_set("binary_sensor.g2", "off", {"device_class": "moisture"})
    await rig.tick(4)
    assert "r1|binary_sensor.g1" in eng.active and "r1|binary_sensor.g2" not in eng.active and not eng._restored
    eng.async_stop()
    await rig.tick(10)


async def test_no_data_chatter_and_link_rules(hass, rig) -> None:
    sensors(hass, "h1", "h2")
    await rig.rules({"sources": ["binary_sensor.h1", "binary_sensor.h2"]})
    # a sensor that reported fine, then goes silent for 15 minutes: said once as "no connection", not as an alarm
    hass.states.async_set("binary_sensor.h1", "unavailable")
    await rig.tick(15 * 60 + 5)
    await rig.tick(15 * 60 + 10)  # the flush window after it
    key = "r1|binary_sensor.h1"
    assert rig.eng.active[key]["nodata"] is True and "нет связи" in rig.titles()[-1]
    assert rig.eng.active_list()[0]["noData"] is True and rig.eng.active_list()[0]["critical"] is False
    rig.clear()
    hass.states.async_set("binary_sensor.h1", "off", {"friendly_name": "H1", "device_class": "moisture"})
    await rig.tick(4)
    assert key not in rig.eng.active and "связь есть" in rig.titles()[-1]
    # a sensor that has never reported is not "lost" (commissioning)
    sensors(hass, "h3", state="unavailable")
    await rig.rules({"sources": ["binary_sensor.h3"]})
    await rig.tick(16 * 60)
    assert rig.eng.active == {}
    # chattering: three starts within the window are told as one "faulty sensor", then quiet
    sensors(hass, "ch")
    await rig.rules({"sources": ["binary_sensor.ch"]})
    rig.clear()
    for _ in range(3):
        hass.states.async_set("binary_sensor.ch", "on", {"friendly_name": "CH", "device_class": "moisture"})
        await rig.tick(4)
        hass.states.async_set("binary_sensor.ch", "off", {"friendly_name": "CH", "device_class": "moisture"})
        await rig.tick(4)
    assert any("срабатывает и сбрасывается" in m for m in rig.messages())
    n = len(rig.pushes)
    hass.states.async_set("binary_sensor.ch", "on", {"friendly_name": "CH", "device_class": "moisture"})
    await rig.tick(4)
    assert len(rig.pushes) == n and rig.eng.active["r1|binary_sensor.ch"].get("quiet") is True
    # settled in alarm for the window: now it is told like any other
    await rig.tick(11 * 60)
    await rig.tick(11 * 60 + 5)  # and the flush window after it
    assert len(rig.pushes) > n


async def test_link_rule_off_means_alarm_only_after_it_was_seen_on(hass, rig) -> None:
    hass.states.async_set("binary_sensor.panel", "off", {"friendly_name": "Panel link", "device_class": "connectivity"})
    await rig.rules({"sources": ["binary_sensor.panel"], "kind": "connection", "alarm_states": ["off"], "title": "Связь со щитом"})
    assert rig.eng.active == {}  # never seen healthy: commissioning, not a lost link
    hass.states.async_set("binary_sensor.panel", "on", {"device_class": "connectivity"})
    await rig.tick(1)
    hass.states.async_set("binary_sensor.panel", "off", {"device_class": "connectivity"})
    await rig.tick(4)
    assert "r1|binary_sensor.panel" in rig.eng.active


async def test_dropping_a_rule_ends_its_alarms_quietly_and_stop_snapshots(hass, rig) -> None:
    sensors(hass, "i1", "i2", "i3")
    await rig.rules({"sources": ["binary_sensor.i1", "binary_sensor.i2", "binary_sensor.i3"], "repeat_min": 5})
    for n in ("i1", "i2", "i3"):
        hass.states.async_set(f"binary_sensor.{n}", "on", {"device_class": "moisture"})
    await rig.tick(4)
    assert "r1" in rig.eng.groups
    rig.clear()
    await rig.rules({"sources": ["binary_sensor.i1"], "enabled": True})
    assert set(rig.eng.active) == {"r1|binary_sensor.i1"}
    await rig.rules({"sources": ["binary_sensor.i1"], "enabled": False})
    assert rig.eng.active == {} and rig.eng.groups == {}
    await rig.rules()
    assert rig.eng.config["rules"] == []


async def test_locked_house_only_narrows_rules(hass, rig) -> None:
    sensors(hass, "j1", "j2")
    await rig.rules({"sources": ["binary_sensor.j1", "binary_sensor.j2"], "repeat_min": 10})
    rig.eng.locked = lambda: True
    with pytest.raises(notify.LockedChange, match="new source"):
        await rig.eng.async_set({"rules": [{"id": "r1", "kind": "leak", "title": "Протечка", "alarm_states": ["on"], "sources": ["binary_sensor.j1", "binary_sensor.j2", "binary_sensor.j3"], "picked": ["binary_sensor.j3"], "recipients": {"all": True}, "repeat_min": 10}]})
    await rig.eng.async_set({"rules": [{"id": "r1", "kind": "leak", "title": "Протечка", "alarm_states": ["on"], "sources": ["binary_sensor.j1"], "picked": ["binary_sensor.j1"], "recipients": {"all": True}, "repeat_min": 10}]})
    assert rig.eng.config["rules"][0]["sources"] == ["binary_sensor.j1"]
    rig.eng.locked = None
    # the pure judge, one widening at a time
    base = notify._clean_rules({"rules": [{"id": "r", "sources": ["sensor.a"], "picked": ["sensor.a"], "recipients": {"people": ["person.a"]}, "repeat_min": 10, "enabled": False}], "groups": [{"id": "g", "people": ["person.a"]}], "audience": {"*": "off"}}, None, None)
    wide = lambda **kw: notify.narrowing(notify._clean_rules({"rules": [{"id": "r", "sources": ["sensor.a"], "picked": ["sensor.a"], "recipients": {"people": ["person.a"]}, "repeat_min": 10, "enabled": False, **kw}], "groups": [{"id": "g", "people": ["person.a"]}], "audience": {"*": "off"}}, None, None), base)  # noqa: E731
    assert wide() is None
    assert "new rule" in notify.narrowing({"rules": [{"id": "z", "kind": "x", "title": "t", "alarm_states": ["on"], "sources": [], "recipients": {"all": False, "people": [], "groups": [], "phones": []}, "priority": "normal", "repeat_min": 0, "enabled": True}], "groups": []}, base)
    assert "changed text" in wide(title="Other") and "new recipient" in wide(recipients={"all": True}) and "new recipient" in wide(recipients={"people": ["person.a", "person.b"]})
    assert "repeats more often" in wide(repeat_min=5) and "switched on" in wide(enabled=True)
    assert "new people" in notify.narrowing({"rules": [], "groups": [{"id": "g", "people": ["person.a", "person.b"]}]}, base)
    assert "audience: a setting removed" == notify.narrowing({"rules": [], "groups": [], "audience": {}}, base)
    assert "audience *: more" in (notify.narrowing({"rules": [], "groups": [{"id": "g", "people": ["person.a"]}], "audience": {"*": "all"}}, base) or "")
    assert "audience person.x: more" in (notify.narrowing({"rules": [], "groups": [{"id": "g", "people": ["person.a"]}], "audience": {"*": "off", "person.x": "all"}}, base) or "")


async def test_phones_people_status_and_labels(hass, rig) -> None:
    eng = notify.NotifyEngine(hass)
    mobile = MockConfigEntry(domain="mobile_app")
    mobile.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=mobile.entry_id, identifiers={("mobile_app", "1")}, name="Ivan's iPhone", manufacturer="Apple")
    dev2 = dr.async_get(hass).async_get_or_create(config_entry_id=mobile.entry_id, identifiers={("mobile_app", "2")}, name="Pixel 7", manufacturer="Google")
    dev3 = dr.async_get(hass).async_get_or_create(config_entry_id=mobile.entry_id, identifiers={("mobile_app", "3")}, name="No service")
    reg = er.async_get(hass)
    for d, uid in ((dev, "t1"), (dev2, "t2"), (dev3, "t3")):
        reg.async_get_or_create("device_tracker", "mobile_app", uid, device_id=d.id, suggested_object_id=f"trk_{uid}")
    reg.async_get_or_create("device_tracker", "mobile_app", "t4", suggested_object_id="no_device")  # no device: skipped
    reg.async_get_or_create("sensor", "mobile_app", "t5", device_id=dev.id, suggested_object_id="phone_battery")
    async def noop(call):
        return None

    hass.services.async_register("notify", "mobile_app_ivan_s_iphone", noop)
    hass.services.async_register("notify", "mobile_app_pixel_7", noop)
    hass.states.async_set("person.ivan", "home", {"friendly_name": "Ivan", "device_trackers": ["device_tracker.trk_t1"]})
    phones = eng.phones()
    assert {p["service"]: (p["os"], p["person"]) for p in phones} == {"mobile_app_ivan_s_iphone": ("ios", "person.ivan"), "mobile_app_pixel_7": ("android", None)}
    people = eng.people()
    assert [(p["id"], p["name"], [ph["service"] for ph in p["phones"]]) for p in people] == [("person.ivan", "Ivan", ["mobile_app_ivan_s_iphone"])]
    # the setup page: a candidate for each alarm class, with its room and whether Hauflo marked it
    reg.async_get_or_create("binary_sensor", "test", "smoke1", suggested_object_id="smoke1")
    hass.states.async_set("binary_sensor.smoke1", "off", {"device_class": "smoke", "friendly_name": "Smoke"})
    hass.states.async_set("binary_sensor.hb", "on", {"friendly_name": "Panel heartbeat"})
    hass.states.async_set("binary_sensor.plain", "on", {"friendly_name": "Plain"})
    hass.states.async_set("valve.v", "open", {"friendly_name": "V"})
    hass.states.async_set("switch.s", "on", {"friendly_name": "S"})
    eng.owns = lambda eid: eid == "switch.s"
    st = eng.status()
    ids = {c["entity_id"] for c in st["candidates"]}
    assert "binary_sensor.smoke1" in ids and "binary_sensor.hb" in ids and "binary_sensor.plain" not in ids
    assert {a["entity_id"] for a in st["actuators"]} == {"valve.v", "switch.s"}
    # labelled() through the entity and through its device; failing registries never mark
    labels = lr.async_get(hass)
    lab = labels.async_create(next(iter(MONITOR_LABELS)).capitalize())
    assert notify.labelled(None, "x.y") is False and notify.labelled(hass, "binary_sensor.nope") is False and notify.labelled(hass, "binary_sensor.smoke1") is False
    reg.async_update_entity("binary_sensor.smoke1", labels={lab.label_id})
    assert notify.labelled(hass, "binary_sensor.smoke1") is True
    reg.async_get_or_create("sensor", "test", "devsens", device_id=dev3.id, suggested_object_id="devsens")
    dr.async_get(hass).async_update_device(dev3.id, labels={lab.label_id})
    assert notify.labelled(hass, "sensor.devsens") is True
    # a place from the device's room
    room = ar.async_get(hass).async_create("Кухня")
    dr.async_get(hass).async_update_device(dev3.id, area_id=room.id)
    hass.states.async_set("sensor.devsens", "1")
    assert eng._where(hass.states.get("sensor.devsens")) == "Кухня"
    assert eng._where(NS(entity_id="sensor.nope")) == ""
    # plural forms and numbers from the wire
    assert [notify._devices(n) for n in (1, 2, 5, 11, 12, 21, 22, 111)] == ["устройство", "устройства", "устройств", "устройств", "устройств", "устройство", "устройства", "устройств"]
    assert notify._int("abc") == 0 and notify._int(None) == 0 and notify._int("7.9") == 7
    # each language says the same rules
    assert notify._en("С 12:00: A. Никто не подтвердил. Повтор 2. 3 устройства и ещё 4") == "Since 12:00: A. Nobody accepted it. Repeat 2. 3 devices and 4 more"
    labels.async_delete(lab.label_id)
    await rig.tick(10)


async def test_alarm_views(hass, rig) -> None:
    from tests.test_agent_tunnel import Req

    sensors(hass, "k1")
    await rig.rules({"sources": ["binary_sensor.k1"], "priority": "critical"})
    hass.states.async_set("binary_sensor.k1", "on", {"friendly_name": "K1", "device_class": "moisture"})
    await rig.tick(4)
    eng = rig.eng
    none_view = notify.NotifyView(lambda: None)
    assert (await none_view.get(Req(hass))).status == 503 and (await none_view.post(Req(hass))).status == 503
    assert (await notify.NotifyTestView(lambda: None).post(Req(hass))).status == 503
    view, test = notify.NotifyView(lambda: eng), notify.NotifyTestView(lambda: eng)
    assert (await view.get(Req(hass, admin=False))).status == 403 and (await view.post(Req(hass, admin=False))).status == 403 and (await test.post(Req(hass, admin=False))).status == 403
    assert (await view.get(Req(hass))).status == 200
    assert (await view.post(Req(hass, body=ValueError()))).status == 400 and (await view.post(Req(hass, body=[1]))).status == 400
    assert (await test.post(Req(hass, body=ValueError()))).status == 400 and (await test.post(Req(hass, body=[1]))).status == 400
    assert (await view.post(Req(hass, body={"rules": []}))).status == 200
    eng.locked = lambda: True
    sensors(hass, "k2")
    res = await view.post(Req(hass, body={"rules": [{"id": "new", "sources": ["binary_sensor.k2"], "picked": ["binary_sensor.k2"]}]}))
    assert res.status == 423 and json.loads(res.body)["code"] == LOCKED_CODE
    eng.locked = None
    # a test: once, then asked to wait
    ok = await test.post(Req(hass, body={"all": True}))
    assert ok.status == 200 and json.loads(ok.body)["targets"] == 2
    waited = await test.post(Req(hass, body={"all": True}))
    assert waited.status == 429 and json.loads(waited.body)["code"] == "test_wait"
    # the alarm list for the dashboard
    await rig.rules({"sources": ["binary_sensor.k1"], "priority": "critical"})
    await rig.tick(4)
    alarms = notify.AlarmsView(lambda: eng)
    got = json.loads((await alarms.get(Req(hass))).body)
    assert "binary_sensor.k1" in got and got["_pushed"] == ["binary_sensor.k1"] and got["_list"][0]["critical"] is True
    eng.unacked_rtn["r9|binary_sensor.gone"] = {"since": "s", "cleared": "c", "title": "T"}
    assert any(i.get("returned") == "c" for i in json.loads((await alarms.get(Req(hass))).body)["_list"])
    assert json.loads((await notify.AlarmsView(lambda: None).get(Req(hass))).body)["_list"] == []
    # accepting: one, many, by key list, malformed
    post = lambda body, user=None: alarms.post(Req(hass, body=body))  # noqa: E731
    assert (await post(ValueError())).status == 400
    assert (await post({"ack": "r1|binary_sensor.nope"})).status == 404 and (await post({"ack": 5})).status == 400 and (await post({"ack": "nobar"})).status == 400 and (await post([1])).status == 400
    assert (await post({"ack_keys": "x"})).status == 400 and (await post({"ack_keys": ["no-bar"]})).status == 400 and (await post({"ack_keys": ["a|b"] * 201})).status == 400
    assert json.loads((await post({"ack_keys": ["r1|binary_sensor.k1", "r1|binary_sensor.k1", "r1|binary_sensor.zz"]})).body)["acked"] == 1
    assert eng.active["r1|binary_sensor.k1"]["acked_by"] == "Test"
    eng.active["r1|binary_sensor.k1"]["acked_by"] = None
    assert json.loads((await post({"ack_all": True})).body)["acked"] == 2  # the open one and the returned unaccepted one
    assert (await alarms.post(Req(hass, body={"ack": "r1|binary_sensor.k1"}))).status == 200
    # through the tunnel the person who pressed "accept" in Hauflo's monitoring is named by Hauflo, from the agent's own user only
    class Tunnel(Req):
        def __init__(self, *a, user, **k):
            super().__init__(*a, **k)
            self["hass_user"] = user

    eng.active["r1|binary_sensor.k1"]["acked_by"] = None
    await alarms.post(Tunnel(hass, body={"ack": "r1|binary_sensor.k1", "by": "Pe\x00te\nr " + "x" * 200}, user=NS(name="Hauflo agent", system_generated=True)))
    assert eng.active["r1|binary_sensor.k1"]["acked_by"].startswith("Pete") and eng.active["r1|binary_sensor.k1"]["acked_by"].endswith("(Hauflo)") and "\x00" not in eng.active["r1|binary_sensor.k1"]["acked_by"]
    eng.active["r1|binary_sensor.k1"]["acked_by"] = None
    await alarms.post(Tunnel(hass, body={"ack": "r1|binary_sensor.k1", "by": "Mallory"}, user=NS(name="Mallory", system_generated=False)))
    assert eng.active["r1|binary_sensor.k1"]["acked_by"] == "Mallory"  # a "by" from anyone else is not trusted: their own name
    await rig.tick(10)
