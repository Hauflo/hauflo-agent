"""Р83: an alarm rule in the flat form (alarm_form.py — as the integrator writes it by hand in HA's editor, the Г2 form).
Broken on purpose: an alarm and a second sensor, «Принято» from a phone, back to normal with a sensor without data, a
hand-edited automation, a rule removed, an old-form rule kept until the owner's «переписать», 20 sensors, mixed levels."""

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import category_registry as cr, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import dump, load_yaml
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hauflo import alarm_form, independent, notify
from custom_components.hauflo.const import CONF_ALLOW_CHANGES, CONF_RANGES, CONF_SECRET, CONF_SERVER, DOMAIN, DO_ACTION, OK_ACTION

PHONES = [{"service": "mobile_app_anna", "name": "Pixel Анны", "os": "android", "person": "person.anna"},
          {"service": "mobile_app_ivan", "name": "iPhone Ивана", "os": "ios", "person": "person.ivan"}]
LEAK, LEAK2, VALVE = "binary_sensor.leak_kitchen", "binary_sensor.leak_bath", "valve.water_main"
FLOOR = [f"binary_sensor.floor_fault_{i}" for i in range(1, 21)]
BATS = ["sensor.bat_hall", "sensor.bat_bed", "sensor.bat_door"]
LEAK_RULE = {"id": "leak", "kind": "leak", "title": "Протечка", "sources": [LEAK, LEAK2], "picked": [LEAK, LEAK2], "alarm_states": ["on"], "priority": "critical",
             "recipients": {"all": True}, "repeat_min": 5, "action": {"title": "Перекрыть воду", "service": "valve.close_valve", "entity_id": VALVE}}
FLOOR_RULE = {"id": "floor", "kind": "fault", "title": "Ошибка тёплого пола", "sources": FLOOR, "picked": FLOOR, "alarm_states": ["on"], "priority": "critical",
              "recipients": {"all": True}, "repeat_min": 5}
BAT_RULE = {"id": "bat", "kind": "battery", "title": "Батареи", "sources": BATS, "picked": BATS, "alarm_states": ["on"], "recipients": {"all": True}, "repeat_min": 0,
            "thresholds": {b: {"below": 20} for b in BATS}}


async def _idle(self):
    return None


@pytest.fixture
async def house(hass: HomeAssistant, tmp_path: Path, freezer):
    hass.config.config_dir = str(tmp_path)
    hass.config.language = "ru"
    (tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\nscript: !include scripts.yaml\n")
    (tmp_path / "automations.yaml").write_text("[]\n")
    (tmp_path / "scripts.yaml").write_text("")
    assert await async_setup_component(hass, "automation", {"automation": []})
    assert await async_setup_component(hass, "script", {})
    assert await async_setup_component(hass, "persistent_notification", {})
    for e, name in ((LEAK, "Протечка кухня"), (LEAK2, "Протечка ванная")):
        hass.states.async_set(e, "off", {"device_class": "moisture", "friendly_name": name})
    for i, e in enumerate(FLOOR, 1):
        hass.states.async_set(e, "off", {"device_class": "problem", "friendly_name": f"Пол {i}"})
    for e in BATS:
        hass.states.async_set(e, "80", {"device_class": "battery", "unit_of_measurement": "%", "friendly_name": e.split(".")[1]})
    hass.states.async_set("person.anna", "home", {"friendly_name": "Анна", "user_id": None})
    hass.states.async_set("person.ivan", "home", {"friendly_name": "Иван", "user_id": None})
    hass.states.async_set(VALVE, "open", {"friendly_name": "Кран"})
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"}, options={CONF_RANGES: ["1/1"], CONF_ALLOW_CHANGES: True})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    engine = entry.runtime_data.engine
    engine.owns = lambda eid: True
    engine.phones = lambda: PHONES  # type: ignore[method-assign]
    pushes: list[ServiceCall] = []
    closed: list[ServiceCall] = []
    for p in PHONES:
        hass.services.async_register("notify", p["service"], pushes.append)
    hass.services.async_register("valve", "close_valve", closed.append)
    yield engine, tmp_path, pushes, closed, freezer
    await hass.services.async_call("automation", "turn_off", {"entity_id": "all"}, blocking=True)


def _rules(hass: HomeAssistant, engine, *rules: dict) -> None:
    engine.config = notify._clean_rules({"lang": "ru", "rules": [dict(r, enabled=True) for r in rules]}, hass, engine.owns)
    engine._apply()


async def _later(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _autos(config: Path) -> dict:
    return {a["id"]: a for a in load_yaml(str(config / "automations.yaml"))}


def _scripts(config: Path) -> dict:
    return load_yaml(str(config / "scripts.yaml")) or {}


def _lines(*cfgs) -> int:
    return sum(len(dump(c).splitlines()) for c in cfgs if c)


async def test_leak_in_the_flat_form_works_end_to_end(hass: HomeAssistant, house) -> None:
    engine, config, pushes, closed, freezer = house
    _rules(hass, engine, LEAK_RULE)
    res = await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    assert res["added"] == ["Протечка"] and not res["engine"] and not res["error"], res
    aid = independent.automation_id("leak")
    slug = aid.removeprefix("hauflo_alarm_")
    autos, scripts = _autos(config), _scripts(config)
    assert set(autos) == {aid, aid + "_back", alarm_form.ACCEPT_ID, alarm_form.BUTTON_ID}
    assert set(scripts) == {"hauflo_push", "hauflo_run_" + slug}
    a = autos[aid]
    assert a["alias"] == "Авария: Протечка" and a["mode"] == "restart" and a["conditions"] == []
    assert a["triggers"] == [{"trigger": "state", "entity_id": [LEAK, LEAK2], "to": "on", "for": {"seconds": independent.HOLD}}]
    assert "Кому: Анна, Иван" in a["description"] and "сервис Hauflo" in a["description"] and "один повторный пуш" in a["description"]
    assert autos[aid + "_back"]["alias"] == "Авария: Протечка — снова в норме"
    # The group helper «<rule> · все датчики» (HA's own), the «Принято» helper, under «Hauflo · Аварии».
    reg = er.async_get(hass)
    group = "binary_sensor." + aid
    ent = reg.async_get(group)
    assert ent and ent.platform == "group" and ent.name == "Протечка · все датчики"
    assert hass.states.get(group).attributes["entity_id"] == [LEAK, LEAK2]
    cat = next(c for c in cr.async_get(hass).async_list_categories(scope="automation") if c.name == "Hauflo · Аварии")
    assert reg.async_get(reg.async_get_entity_id("automation", "automation", aid)).categories["automation"] == cat.category_id
    assert reg.async_get(group).categories.get("helpers")
    rec = engine.native["leak"]
    assert rec["form"] == 2 and rec["group"] == group and "open" not in rec
    assert not hass.states.get("input_boolean.hauflo_open_" + slug)
    # The alarm: both phones, critical per OS, the sensor named, the notice in HA.
    hass.states.async_set(LEAK, "on", {"device_class": "moisture", "friendly_name": "Протечка кухня"})
    await _later(hass, freezer, independent.HOLD + 1)
    by = {c.service: c.data for c in pushes}
    assert set(by) == {"mobile_app_anna", "mobile_app_ivan"}, [c.data for c in pushes]
    assert by["mobile_app_anna"]["title"] == "🚨 Протечка" and by["mobile_app_anna"]["message"] == "Протечка кухня: авария."
    assert by["mobile_app_anna"]["data"]["channel"] == "alarm_stream" and by["mobile_app_ivan"]["data"]["push"]["interruption-level"] == "critical"
    assert by["mobile_app_ivan"]["data"]["tag"] == aid
    buttons = by["mobile_app_ivan"]["data"]["actions"]
    assert [b["action"] for b in buttons] == [DO_ACTION + slug.upper(), OK_ACTION + slug.upper()] and buttons[0]["title"] == "Закрыть: Кран"
    notes = hass.data.get("persistent_notification") or {}
    assert aid in str(notes)
    # A second sensor: the alarm again, both named.
    pushes.clear()
    hass.states.async_set(LEAK2, "on", {"device_class": "moisture", "friendly_name": "Протечка ванная"})
    await _later(hass, freezer, independent.HOLD + 1)
    assert len(pushes) == 2 and pushes[0].data["message"] == "Протечка кухня, Протечка ванная: авария.", [c.data for c in pushes]
    # Nobody accepted it: repeated at the rule's pace.
    pushes.clear()
    await _later(hass, freezer, 5 * 60 + 1)
    assert len(pushes) == 2 and "никто не подтвердил" in pushes[0].data["message"]
    # «Принято» from a phone: quiet «Принято.», the notice gone, no more repeats.
    pushes.clear()
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": OK_ACTION + slug.upper()})
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.hauflo_ack_" + slug).state == "on"
    assert len(pushes) == 2 and pushes[0].data["message"] == "Принято." and pushes[0].data["data"].get("alert_once") is True
    pushes.clear()
    await _later(hass, freezer, 3600)
    assert pushes == []
    # Back to normal: one sensor without data — no «снова в норме».
    hass.states.async_set(LEAK, "off")
    hass.states.async_set(LEAK2, "unavailable")
    await _later(hass, freezer, independent.back_after(engine.config["rules"][0]) + 1)
    assert pushes == []
    hass.states.async_set(LEAK2, "off")
    await _later(hass, freezer, independent.back_after(engine.config["rules"][0]) + 1)
    # unavailable → off (acceptance С2): the group was off already — the sensor's own return says «снова в норме» once.
    titles = [c.data["title"] for c in pushes]
    assert titles.count("✅ Протечка") == 2 and pushes[-1].data["message"] == "Все датчики снова в норме.", [c.data for c in pushes]
    pushes.clear()
    await _later(hass, freezer, 3600)
    assert pushes == []  # said once
    # A fresh alarm, then a clean back: «снова в норме» to both phones.
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, independent.HOLD + 1)
    pushes.clear()
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, independent.back_after(engine.config["rules"][0]) + 1)
    titles = [c.data["title"] for c in pushes]
    assert titles.count("✅ Протечка") == 2 and pushes[-1].data["message"] == "Все датчики снова в норме.", [c.data for c in pushes]
    # The button from the push: the valve closed, then «Принято».
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, independent.HOLD + 1)
    pushes.clear()
    hass.bus.async_fire(notify.ACTION_EVENT, {"action": DO_ACTION + slug.upper()})
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in closed] == [[VALVE]]
    assert hass.states.get("input_boolean.hauflo_ack_" + slug).state == "on"
    assert any(c.data["message"] == "Закрыть: Кран: выполнено." for c in pushes), [c.data for c in pushes]
    # Again: nothing rewritten.
    again = await independent.async_sync(hass, engine)
    assert again["unchanged"] == ["Протечка"] and not again["added"] and not again["updated"]


async def test_floor_20_sensors_names_and_plus_n(hass: HomeAssistant, house) -> None:
    engine, config, pushes, _closed, freezer = house
    _rules(hass, engine, FLOOR_RULE)
    assert (await independent.async_sync(hass, engine))["added"] == ["Ошибка тёплого пола"]
    await hass.async_block_till_done()
    aid = independent.automation_id("floor")
    a = _autos(config)[aid]
    assert a["triggers"][0]["entity_id"] == FLOOR and len(a["triggers"]) == 1
    assert "+12" in a["description"]  # 8 named, then «+N»
    for e in FLOOR[:10]:
        hass.states.async_set(e, "on", {"device_class": "problem", "friendly_name": f"Пол {FLOOR.index(e) + 1}"})
    await _later(hass, freezer, independent.alarm_hold(engine.config["rules"][0]) + 1)
    msg = pushes[-1].data["message"]
    assert msg.startswith("Пол 1, Пол 2") and msg.endswith(" +2: авария."), msg


async def test_battery_levels_single_and_mixed(hass: HomeAssistant, house) -> None:
    engine, config, pushes, _closed, freezer = house
    _rules(hass, engine, BAT_RULE)
    assert (await independent.async_sync(hass, engine))["added"] == ["Батареи"]
    await hass.async_block_till_done()
    aid = independent.automation_id("bat")
    a = _autos(config)[aid]
    assert a["triggers"] == [{"trigger": "numeric_state", "entity_id": BATS, "below": 20.0, "for": {"seconds": independent.alarm_hold(engine.config["rules"][0])}}]
    ent = er.async_get(hass).async_get("binary_sensor." + aid)
    assert ent and ent.platform == "template"  # a level: HA's template helper
    assert aid + "_back" not in _autos(config)  # not critical: no «снова в норме»
    hass.states.async_set(BATS[0], "18", {"device_class": "battery", "unit_of_measurement": "%", "friendly_name": "bat_hall"})
    await _later(hass, freezer, independent.alarm_hold(engine.config["rules"][0]) + 1)
    assert pushes and pushes[0].data["message"] == "bat_hall (18 %): авария.", [c.data for c in pushes]
    assert hass.states.get("binary_sensor." + aid).state == "on"
    hass.states.async_set(BATS[0], "23", {"device_class": "battery", "unit_of_measurement": "%"})  # inside the step: still on
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor." + aid).state == "on"
    # Mixed thresholds: one numeric_state per threshold.
    mixed = {**BAT_RULE, "thresholds": {BATS[0]: {"below": 20}, BATS[1]: {"below": 20}, BATS[2]: {"below": 30}}}
    _rules(hass, engine, mixed)
    res = await independent.async_sync(hass, engine)
    assert res["updated"] == ["Батареи"], res
    trig = _autos(config)[aid]["triggers"]
    assert [(t["entity_id"], t["below"]) for t in trig] == [([BATS[0], BATS[1]], 20.0), ([BATS[2]], 30.0)]


async def test_lines_per_rule_before_and_after(hass: HomeAssistant, house) -> None:
    engine, config, _pushes, _closed, _freezer = house
    t = await independent.texts(hass, False)
    out = {}
    for rule in (LEAK_RULE, FLOOR_RULE, BAT_RULE):
        _rules(hass, engine, rule)
        r = engine.config["rules"][0]
        before = independent.automation_for(r, PHONES, t, "input_boolean.a", "input_boolean.o", names={VALVE: "Кран"})
        res = await independent.async_sync(hass, engine)
        assert res["added"] or res["updated"], res
        aid = independent.automation_id(r["id"])
        after = [_autos(config).get(aid), _autos(config).get(aid + "_back"), _scripts(config).get("hauflo_run_" + aid.removeprefix("hauflo_alarm_"))]
        out[r["title"]] = (_lines(before), _lines(*after))
    print("lines per rule (before, after):", out)
    shared = _lines(_scripts(config)["hauflo_push"], _autos(config).get(alarm_form.ACCEPT_ID))
    print("shared once:", shared)
    for title, (b, a) in out.items():
        assert a < b, (title, b, a)


async def test_old_form_kept_until_migrate_then_rewritten_edited_never(hass: HomeAssistant, house) -> None:
    engine, config, _pushes, _closed, _freezer = house
    _rules(hass, engine, LEAK_RULE, {**FLOOR_RULE, "repeat_min": 0})
    # Written in the form before (as 0.34 did: no scripts in HA then).
    hass.config.components.remove("script")
    try:
        res = await independent.async_sync(hass, engine)
    finally:
        hass.config.components.add("script")
    assert sorted(res["added"]) == ["Ошибка тёплого пола", "Протечка"]
    leak, floor = independent.automation_id("leak"), independent.automation_id("floor")
    assert "open" in engine.native["leak"] and "form" not in engine.native["leak"]
    # The floor automation edited by hand in HA.
    autos = load_yaml(str(config / "automations.yaml"))
    for a in autos:
        if a["id"] == floor:
            a["description"] = "мой текст"
            a["mode"] = "queued"
    (config / "automations.yaml").write_text(dump(autos))
    text = (config / "automations.yaml").read_text()
    # An agent update / a deploy: nothing rewritten, said what «переписать» would do.
    dry = await independent.async_sync(hass, engine, dry_run=True)
    assert dry["migrate"] == ["Протечка"] and dry["kept_edited"] == ["Ошибка тёплого пола"]
    plain = await independent.async_sync(hass, engine)
    assert plain["migrate"] == ["Протечка"] and (config / "automations.yaml").read_text() == text and not (config / "scripts.yaml").read_text().strip()
    # The owner's «переписать»: dry run first, then for real — a backup, the flat form, the old helper gone.
    dry = await independent.async_sync(hass, engine, dry_run=True, migrate=True)
    assert dry["updated"] == ["Протечка"] and dry["kept_edited"] == ["Ошибка тёплого пола"]
    done = await independent.async_sync(hass, engine, migrate=True)
    await hass.async_block_till_done()
    assert done["updated"] == ["Протечка"] and done["kept_edited"] == ["Ошибка тёплого пола"] and not done["migrate"]
    assert list(config.glob("automations.yaml.hauflo-bak-*"))
    autos = _autos(config)
    assert autos[floor]["description"] == "мой текст" and autos[floor]["mode"] == "queued"
    assert autos[leak]["mode"] == "restart" and leak + "_back" in autos and engine.native["leak"]["form"] == 2
    slug = leak.removeprefix("hauflo_alarm_")
    assert hass.states.get("input_boolean.hauflo_open_" + slug) is None and hass.states.get("input_boolean.hauflo_ack_" + slug)


async def test_flat_rule_edited_by_hand_stays_removed_rule_takes_its_objects(hass: HomeAssistant, house) -> None:
    engine, config, _pushes, _closed, _freezer = house
    _rules(hass, engine, LEAK_RULE, FLOOR_RULE)
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    leak, floor = independent.automation_id("leak"), independent.automation_id("floor")
    # The floor's «снова в норме» edited in HA: the rule kept whole.
    autos = load_yaml(str(config / "automations.yaml"))
    for a in autos:
        if a["id"] == floor + "_back":
            a["mode"] = "queued"
    (config / "automations.yaml").write_text(dump(autos))
    _rules(hass, engine, {**LEAK_RULE, "repeat_min": 10}, {**FLOOR_RULE, "repeat_min": 10})
    res = await independent.async_sync(hass, engine)
    assert res["updated"] == ["Протечка"] and res["kept_edited"] == ["Ошибка тёплого пола"], res
    # The leak rule removed: its automation, «снова в норме», button script, group and «Принято» go; the shared stay.
    _rules(hass, engine, {**FLOOR_RULE, "repeat_min": 10})
    slug = leak.removeprefix("hauflo_alarm_")
    res = await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    assert res["removed"] == ["Авария: Протечка"], res
    autos, scripts = _autos(config), _scripts(config)
    assert leak not in autos and leak + "_back" not in autos and "hauflo_run_" + slug not in scripts
    assert alarm_form.BUTTON_ID not in autos and alarm_form.ACCEPT_ID in autos and "hauflo_push" in scripts
    assert hass.states.get("binary_sensor." + leak) is None and hass.states.get("input_boolean.hauflo_ack_" + slug) is None
    assert er.async_get(hass).async_get_entity_id("binary_sensor", "group", "x") is None
    assert not [e for e in hass.config_entries.async_entries("group") if e.options.get("name") == leak]
    # «Убрать аварии Hauflo»: everything of the alarms goes, edited ones too.
    gone = await independent.async_remove_all(hass, engine)
    await hass.async_block_till_done()
    assert "Hauflo: пуш аварии" in gone
    assert not [a for a in _autos(config) if a.startswith("hauflo_alarm_")] and not _scripts(config)
    assert not alarm_form.our_entries(hass)


async def _restart(hass: HomeAssistant, engine, freezer, first: str, delay: float = 0) -> None:
    """HA stops (the alarm's run dies with the automation, the engine saves) and starts: the sensor's first state is
    ``first`` (none before it — HA's state triggers and the group see no change), ``delay`` s after the engine is up."""
    await hass.services.async_call("automation", "turn_off", {"entity_id": "all"}, blocking=True)
    engine.async_stop()
    await hass.async_block_till_done()
    hass.states.async_remove(LEAK)
    await engine.async_start()
    await hass.services.async_call("automation", "turn_on", {"entity_id": "all"}, blocking=True)
    await hass.async_block_till_done()
    if delay:
        await _later(hass, freezer, delay)
    hass.states.async_set(LEAK, first, {"device_class": "moisture", "friendly_name": "Протечка кухня"})
    await hass.async_block_till_done()


async def test_back_to_normal_while_ha_restarted_flat_form(hass: HomeAssistant, house) -> None:
    """The flat form over a restart of HA: the alarm open, HA restarts, the sensor is dry before the automations are
    back — the group starts off, HA's own «снова в норме» (on → off) never fires. The engine says it once with the
    automation's tag and takes the notice; HA's own «back» is never doubled. Still in alarm after the restart: one more
    push from the run (said in the automation's description), then HA's own «back» — the engine quiet."""
    engine, config, pushes, closed, freezer = house
    _rules(hass, engine, LEAK_RULE)
    await engine.store.async_save(engine.config)
    await independent.async_sync(hass, engine)
    await hass.async_block_till_done()
    aid = independent.automation_id("leak")
    back = independent.back_after(engine.config["rules"][0])
    hass.states.async_set(LEAK, "on", {"device_class": "moisture", "friendly_name": "Протечка кухня"})
    await _later(hass, freezer, independent.HOLD + 1)
    assert len(pushes) == 2 and aid in str(hass.data.get("persistent_notification") or {})
    pushes.clear()
    # Dry 2 s after the start: looked at once it has held the automation's back window.
    await _restart(hass, engine, freezer, "off", delay=2)
    await _later(hass, freezer, back + notify.GROUP_WINDOW - 1)
    assert pushes == []
    await _later(hass, freezer, 3)
    assert [c.data["title"] for c in pushes] == ["✅ Протечка"] * 2 and pushes[0].data["message"] == "Все датчики снова в норме.", [c.data for c in pushes]
    assert {c.data["data"]["tag"] for c in pushes} == {aid} and aid not in str(hass.data.get("persistent_notification") or {})
    await _later(hass, freezer, 3600)
    assert len(pushes) == 2  # once
    # Open again, HA restarts, the sensor first «unavailable» (not read yet): nothing said …
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, independent.HOLD + 1)
    pushes.clear()
    await _restart(hass, engine, freezer, "unavailable")
    await _later(hass, freezer, back + notify.GROUP_WINDOW + 1)
    assert pushes == []
    # … then dry: the group never went on — the sensor's own return (С2) is HA's «снова в норме», once; the engine
    # (its look a group window later) sees it said and is quiet.
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, back + 1)
    assert [c.data["title"] for c in pushes] == ["✅ Протечка"] * 2, [c.data for c in pushes]
    await _later(hass, freezer, 3 * (back + notify.GROUP_WINDOW))
    assert len(pushes) == 2, [c.data for c in pushes]
    # Still in alarm after the restart: the run again (one repeat), then the sensor dry — HA's own «back», once.
    hass.states.async_set(LEAK, "on")
    await _later(hass, freezer, independent.HOLD + 1)
    pushes.clear()
    await _restart(hass, engine, freezer, "on")
    await _later(hass, freezer, 2 * independent.HOLD + notify.NATIVE_HOLD + 2)
    assert [c.data["title"] for c in pushes] == ["🚨 Протечка"] * 2, [c.data for c in pushes]
    pushes.clear()
    hass.states.async_set(LEAK, "off")
    await _later(hass, freezer, 3 * (back + notify.GROUP_WINDOW))
    assert [c.data["title"] for c in pushes] == ["✅ Протечка"] * 2, [c.data for c in pushes]
