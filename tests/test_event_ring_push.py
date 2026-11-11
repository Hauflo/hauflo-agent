"""A doorbell press (event.*) is one push to the HA app by the alarms' own path: once per visit, not for a restored old
press, not for an event nobody watches, in the project's language. 0.33 п.9: watched by the status' own rule (a doorbell
by its class alone pushes — it rang the bell and Telegram but no phone), a SIP call / SOS button by one mechanism, quiet
hours without a sound, «пуш о звонке» per person (on by default)."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.hauflo import foreign, notify
from custom_components.hauflo.const import CONF_RANGES, CONF_SECRET, CONF_SERVER, DOMAIN

BELL = "event.door_bell"
BUTTON = "event.wall_button"


@pytest.fixture
async def engine(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"}, options={CONF_RANGES: ["1/1"]})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    reg = er.async_get(hass)
    for eid in (BELL, BUTTON):
        reg.async_get_or_create("event", "zha", eid, suggested_object_id=eid.split(".")[1])
    hass.states.async_set(BELL, "unknown", {"device_class": "doorbell", "friendly_name": "Домофон"})
    hass.states.async_set(BUTTON, "unknown", {"device_class": "button", "friendly_name": "Кнопка"})
    eng = entry.runtime_data.engine
    eng.phones = lambda: [{"service": "mobile_app_p", "os": "android", "person": "x"}]
    eng.scope = lambda phone, config=None: "all"
    eng.config = {**eng.config, "lang": "ru"}
    calls = async_mock_service(hass, "notify", "mobile_app_p")
    yield eng, calls


def _ring(hass: HomeAssistant, eid: str, ago: float = 0) -> None:
    hass.states.async_set(eid, (dt_util.utcnow() - timedelta(seconds=ago)).isoformat(), {"device_class": hass.states.get(eid).attributes["device_class"], "friendly_name": hass.states.get(eid).attributes["friendly_name"]})


async def test_one_push_per_visit(hass: HomeAssistant, engine) -> None:
    eng, calls = engine
    foreign.flags(hass).notify[BELL] = True  # the integrator's «Уведомлять» (the doorbell's default class)
    _ring(hass, BELL)
    await hass.async_block_till_done()
    assert len(calls) == 1 and calls[0].data["title"] == "🔔 Звонок в дверь" and calls[0].data["message"] == "Домофон"
    # The visitor presses again within the window: the same visit, no second push.
    _ring(hass, BELL, ago=-1)
    await hass.async_block_till_done()
    assert len(calls) == 1
    # After the window: a new visit.
    eng._rang[BELL] -= notify.RING_WINDOW + 1
    _ring(hass, BELL, ago=-2)
    await hass.async_block_till_done()
    assert len(calls) == 2


async def test_english(hass: HomeAssistant, engine) -> None:
    eng, calls = engine
    eng.config["lang"] = "en"
    foreign.flags(hass).notify[BELL] = True
    _ring(hass, BELL)
    await hass.async_block_till_done()
    assert len(calls) == 1 and calls[0].data["title"] == "🔔 Doorbell" and calls[0].data["message"] == "Домофон"


async def test_not_watched_old_or_unavailable(hass: HomeAssistant, engine) -> None:
    eng, calls = engine
    # A button nobody turned on: silent.
    _ring(hass, BUTTON)
    # A watched doorbell: an old restored press, then unavailable — silent.
    foreign.flags(hass).notify[BELL] = True
    _ring(hass, BELL, ago=3600)
    hass.states.async_set(BELL, "unavailable", {"device_class": "doorbell"})
    await hass.async_block_till_done()
    assert calls == []
    # The integrator turns the button on: it fires as a plain event line.
    foreign.flags(hass).notify[BUTTON] = True
    _ring(hass, BUTTON, ago=-1)
    await hass.async_block_till_done()
    assert len(calls) == 1 and calls[0].data["title"] == "🔔 Кнопка" and calls[0].data["message"] == "Сработало событие"


def _event(hass: HomeAssistant, eid: str, attrs: dict, *, platform: str = "zha", device_class: str | None = None) -> None:
    er.async_get(hass).async_get_or_create("event", platform, eid, suggested_object_id=eid.split(".")[1], original_device_class=device_class)
    hass.states.async_set(eid, "unknown", attrs)


def _press(hass: HomeAssistant, eid: str, ago: float = 0) -> None:
    hass.states.async_set(eid, (dt_util.utcnow() - timedelta(seconds=ago)).isoformat(), dict(hass.states.get(eid).attributes))


async def test_doorbell_by_class_pushes_like_the_bell(hass: HomeAssistant, engine) -> None:
    """п.9: a doorbell of any integration is watched by the canon's class (the cloud's bell and Telegram said it) — the
    push follows the same rule, with no label and no «Уведомлять»; its place in the title."""
    eng, calls = engine
    _event(hass, "event.porch", {"device_class": "doorbell", "friendly_name": "Вызывная панель"}, device_class="doorbell")
    assert eng.watches is not None and eng.watches("event.porch")
    assert "event.porch" in {m["entityId"] for m in hass.config_entries.async_entries("hauflo")[0].runtime_data.monitored()}
    from homeassistant.helpers import area_registry as ar
    area = ar.async_get(hass).async_create("Прихожая")
    er.async_get(hass).async_update_entity("event.porch", area_id=area.id)
    _press(hass, "event.porch")
    await hass.async_block_till_done()
    assert len(calls) == 1 and calls[0].data["title"] == "🔔 Звонок в дверь · Прихожая" and calls[0].data["message"] == "Вызывная панель"
    assert "push" not in calls[0].data["data"] and "channel" not in calls[0].data["data"]  # a normal push, with its sound
    # The site said «не следить»: no push either (one rule with the status).
    foreign.flags(hass).notify["event.porch"] = False
    eng._rang.clear()
    _press(hass, "event.porch", ago=-1)
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_sip_call_and_sos_one_mechanism(hass: HomeAssistant, engine) -> None:
    """An intercom's SIP call without the doorbell class is a doorbell by its event type; an SOS button is critical (through
    silent mode and the quiet hours); the cloud's line says the same kind (status `eventClass`)."""
    eng, calls = engine
    eng.quiet_now = lambda: True
    _event(hass, "event.intercom", {"event_type": "incoming_call", "event_types": ["incoming_call"], "friendly_name": "Домофон"}, platform="sip")
    _event(hass, "event.panic", {"device_class": "button", "friendly_name": "Кнопка SOS"})
    for eid in ("event.intercom", "event.panic"):
        foreign.flags(hass).notify[eid] = True
    _press(hass, "event.intercom")
    _press(hass, "event.panic")
    await hass.async_block_till_done()
    by = {c.data["message"]: c.data for c in calls}
    assert by["Домофон"]["title"] == "🔔 Звонок в дверь"
    # Quiet hours: the call comes without a sound (Android: the low channel), the SOS rings anyway.
    assert by["Домофон"]["data"]["channel"] == notify.QUIET_CHANNEL and by["Домофон"]["data"]["importance"] == "low"
    sos = by["Кнопка SOS: нажата кнопка SOS"]
    assert sos["title"] == "🆘 SOS" and sos["data"]["channel"] == "alarm_stream" and sos["data"]["priority"] == "high"
    mon = {m["entityId"]: m for m in hass.config_entries.async_entries("hauflo")[0].runtime_data.monitored()}
    assert mon["event.intercom"]["eventClass"] == "doorbell" and mon["event.panic"]["eventClass"] == "sos"


async def test_quiet_hours_ios_passive(hass: HomeAssistant, engine) -> None:
    eng, calls = engine
    eng.phones = lambda: [{"service": "mobile_app_p", "os": "ios", "person": "x"}]
    eng.quiet_now = lambda: True
    foreign.flags(hass).notify[BELL] = True
    _ring(hass, BELL)
    await hass.async_block_till_done()
    assert calls[0].data["data"]["push"] == {"interruption-level": "passive"}


async def test_ring_push_setting_per_person(hass: HomeAssistant, engine) -> None:
    """«Пуш о звонке»: on by default; off for a person — their phones get no push; a phone whose alarms are «off» gets none
    unless switched on; the setting survives the wire (cleaned) and a locked house refuses turning it on."""
    eng, calls = engine
    phones = [{"service": "mobile_app_p", "os": "android", "person": "person.ivan"}, {"service": "mobile_app_q", "os": "android", "person": "person.olga"}]
    eng.phones = lambda: phones
    del eng.scope  # the engine's own: by «audience»
    eng.config = notify._clean_rules({"rules": [], "groups": [], "audience": {"person.olga": "off"}, "rings": {"person.ivan": False, "bad key": True, "*": "yes"}})
    assert eng.config["rings"] == {"person.ivan": False}
    assert [p["service"] for p in eng.ring_targets()] == []  # Иван switched off, Ольга's alarms off
    eng.config["rings"] = {"person.olga": True}
    assert [p["service"] for p in eng.ring_targets()] == ["mobile_app_p", "mobile_app_q"]
    q = async_mock_service(hass, "notify", "mobile_app_q")
    foreign.flags(hass).notify[BELL] = True
    eng.config["rings"] = {"person.ivan": False}
    eng.config["audience"] = {}
    _ring(hass, BELL)
    await hass.async_block_till_done()
    assert calls == [] and len(q) == 1
    assert notify._clean_rules({"rules": [], "groups": []})["rings"] == {}
    assert notify.narrowing({"rules": [], "groups": [], "rings": {"person.ivan": True}}, {"rules": [], "groups": [], "rings": {}}) == "rings person.ivan: more"
    assert notify.narrowing({"rules": [], "groups": [], "rings": {}}, {"rules": [], "groups": [], "rings": {"person.ivan": False}}) == "rings person.ivan: more"


async def test_sos_and_sip_watched_by_default(hass: HomeAssistant, engine) -> None:
    """Acceptance 0.33 M4: a panic button and an intercom's SIP call (MQTT, no doorbell class, no label, no «Уведомлять»)
    push by default — the canon watches every press kind of `event_kinds`; SOS critical; any other press stays silent.
    The name HA says twice (device «X» + entity «X») is said once (п.9)."""
    eng, calls = engine
    _event(hass, "event.sip_call", {"event_type": "incoming_call", "friendly_name": "SIP домофон вызов SIP домофон вызов"}, platform="mqtt")
    _event(hass, "event.sos_bedroom", {"friendly_name": "SOS кнопка Спальня SOS кнопка Спальня"}, platform="mqtt")
    _event(hass, "event.wall_switch", {"friendly_name": "Выключатель"}, platform="mqtt")
    assert eng.watches("event.sip_call") and eng.watches("event.sos_bedroom") and not eng.watches("event.wall_switch")
    mon = {m["entityId"] for m in hass.config_entries.async_entries("hauflo")[0].runtime_data.monitored()}
    assert {"event.sip_call", "event.sos_bedroom"} <= mon and "event.wall_switch" not in mon
    for eid in ("event.sip_call", "event.sos_bedroom", "event.wall_switch"):
        _press(hass, eid)
    await hass.async_block_till_done()
    by = {c.data["message"]: c.data for c in calls}
    assert set(by) == {"SIP домофон вызов", "SOS кнопка Спальня: нажата кнопка SOS"}
    assert by["SOS кнопка Спальня: нажата кнопка SOS"]["data"]["channel"] == "alarm_stream"
    # The site said «не следить» for the SOS button: silent (one rule with the status).
    foreign.flags(hass).notify["event.sos_bedroom"] = False
    eng._rang.clear()
    _press(hass, "event.sos_bedroom", ago=-1)
    await hass.async_block_till_done()
    assert len(calls) == 2


def test_name_said_once() -> None:
    assert notify.once("Звонок в дверь Звонок в дверь") == "Звонок в дверь"
    assert notify.once("Свет Свет 2") == "Свет Свет 2" and notify.once("Ab") == "Ab" and notify.once("") == ""
