"""Voice intents (voice.py): what is said back and which climate service is called, per room, per kind of thermostat;
the sentences file is Hauflo's own and nothing a human wrote is touched."""

from unittest.mock import patch

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, intent
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.hauflo import voice

TURN_ON, TURN_OFF = ClimateEntityFeature.TURN_ON, ClimateEntityFeature.TURN_OFF
H = {h.intent_type: h for h in voice.HANDLERS}


def add(hass, eid, name, area=None, attrs=None, state="heat", device_area=None, exposed=True, disabled=False, aliases=()):
    reg = er.async_get(hass)
    kw = {}
    if device_area:
        entry = MockConfigEntry(domain="test")
        entry.add_to_hass(hass)
        dev = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("t", eid)})
        dr.async_get(hass).async_update_device(dev.id, area_id=device_area)
        kw["device_id"] = dev.id
    e = reg.async_get_or_create("climate", "test", eid, suggested_object_id=eid, original_name=name, **kw)
    reg.async_update_entity(e.entity_id, area_id=area, aliases=set(aliases), disabled_by=er.RegistryEntryDisabler.USER if disabled else None)
    hass.states.async_set(e.entity_id, state, {"friendly_name": name, **(attrs or {})})
    if not exposed:
        EXPOSED_OFF.add(e.entity_id)
    return e.entity_id


EXPOSED_OFF: set[str] = set()


@pytest.fixture(autouse=True)
def _expose():
    EXPOSED_OFF.clear()
    with patch.object(voice, "async_should_expose", lambda hass, assistant, eid: eid not in EXPOSED_OFF):
        yield


async def say(hass, kind, area=None, text="", value=None, said=None, **slots):
    sl = {}
    if area:
        sl["area"] = {"value": area, "text": said or area}
    if value is not None:
        sl["hauflo_temperature"] = {"value": value}
    sl.update(slots)
    res = await H[kind].async_handle(intent.Intent(hass, "conversation", kind, sl, text, Context(), "ru"))
    return res.speech["plain"]["speech"]


@pytest.fixture
def rooms(hass):
    areas = ar.async_get(hass)
    return areas.async_create("Спальня"), areas.async_create("Комната 2", aliases={"детская"})


async def test_nothing_found_and_which_room(hass: HomeAssistant, rooms) -> None:
    assert "не нашёл" in await say(hass, "HaufloThermostatOn", "спальня")
    assert "Тёплого пола" in await say(hass, "HaufloHeatingOn", "спальня")
    add(hass, "a", "Кондиционер")
    add(hass, "b", "Термостат кухни")
    # two thermostats and no room said: asks which, with an example fitting the action
    assert "в какой комнате" in await say(hass, "HaufloThermostatOn") and "включи термостат в спальне" in await say(hass, "HaufloThermostatOn")
    assert "выключи термостат в спальне" in await say(hass, "HaufloThermostatOff")
    assert "установи температуру в спальне на 22" in await say(hass, "HaufloSetTemperature")
    assert "какая температура в спальне" in await say(hass, "HaufloGetTemperature")


async def test_get_temperature_by_room_and_alias(hass: HomeAssistant, rooms) -> None:
    bed, kids = rooms
    add(hass, "k1", "Детский термостат", area=kids.id, attrs={"current_temperature": 21.0})
    add(hass, "k2", "Тёплый пол детской", area=kids.id, attrs={"current_temperature": 25.0})  # a floor: the room's own thermostat wins
    add(hass, "bed", "Спальня термостат", device_area=bed.id, attrs={"current_temperature": 20.5})
    add(hass, "off", "Выкл", area=bed.id, attrs={"current_temperature": 99}, exposed=False)  # hidden from Assist
    add(hass, "dis", "Отключён", area=bed.id, attrs={"current_temperature": 98}, disabled=True)
    # the room as said, with its preposition taken from what was heard
    assert await say(hass, "HaufloGetTemperature", "Комната 2", said="комнате 2", text="какая температура в комнате 2") == "В комнате 2 сейчас 21°."
    assert await say(hass, "HaufloGetTemperature", "детская") == "В детская сейчас 21°."  # by alias, with no phrase to take the preposition from
    assert (await say(hass, "HaufloGetTemperature", "спальня")).endswith("20.5°.")
    # no thermometer
    kitchen = ar.async_get(hass).async_create("Кухня")
    add(hass, "mute", "Нем", area=kitchen.id, attrs={})
    assert "термостат её не сообщает" in await say(hass, "HaufloGetTemperature", "кухня")
    assert "Термостата в гараж не нашёл" in await say(hass, "HaufloThermostatOn", "гараж")  # no such room: nothing


async def test_set_temperature_clamped_to_the_thermostat(hass: HomeAssistant, rooms) -> None:
    bed, _ = rooms
    calls = async_mock_service(hass, "climate", "set_temperature")
    add(hass, "t1", "Термостат", area=bed.id, attrs={"min_temp": 10, "max_temp": 30})
    assert await say(hass, "HaufloSetTemperature", "спальня", value=22) == "Температура в спальня: 22°."
    assert calls[-1].data == {"entity_id": "climate.t1", "temperature": 22.0}
    await say(hass, "HaufloSetTemperature", "спальня", value=50)
    assert calls[-1].data["temperature"] == 30  # never beyond what the device takes
    assert (await say(hass, "HaufloSetTemperature", "спальня", value=3)).endswith("10°.") and calls[-1].data["temperature"] == 10
    # two thermostats of different limits: each is clamped on its own and the answer says so
    add(hass, "t2", "Второй", area=bed.id, attrs={"min_temp": 5, "max_temp": 25})
    text = await say(hass, "HaufloSetTemperature", "спальня", value=3)
    assert "в пределах термостата" in text and sorted(c.data["temperature"] for c in calls[-2:]) == [5, 10]
    n = len(calls)
    assert "Не расслышал" in await say(hass, "HaufloSetTemperature", "спальня", value="abc")
    assert "Не расслышал" in await say(hass, "HaufloSetTemperature", "спальня")
    assert len(calls) == n


async def test_on_off_by_what_the_thermostat_can(hass: HomeAssistant, rooms) -> None:
    bed, _ = rooms
    on, off, hvac, preset = async_mock_service(hass, "climate", "turn_on"), async_mock_service(hass, "climate", "turn_off"), async_mock_service(hass, "climate", "set_hvac_mode"), async_mock_service(hass, "climate", "set_preset_mode")
    # one that can switch
    add(hass, "can", "Кондиционер", area=bed.id, attrs={"supported_features": TURN_ON | TURN_OFF})
    assert (await say(hass, "HaufloThermostatOn", "спальня")).endswith("включён.") and on[-1].data["entity_id"] == "climate.can"
    assert (await say(hass, "HaufloThermostatOff", "спальня")).endswith("выключен.") and off[-1].data["entity_id"] == "climate.can"
    for s in list(hass.states.async_all("climate")):
        hass.states.async_remove(s.entity_id)
    er.async_get(hass).async_remove("climate.can")
    # one with an off mode: through hvac mode
    add(hass, "hv", "Термостат", area=bed.id, attrs={"hvac_modes": [HVACMode.OFF, HVACMode.HEAT]})
    await say(hass, "HaufloThermostatOn", "спальня")
    assert hvac[-1].data["hvac_mode"] == HVACMode.HEAT
    await say(hass, "HaufloThermostatOff", "спальня")
    assert hvac[-1].data["hvac_mode"] == HVACMode.OFF
    er.async_get(hass).async_remove("climate.hv")
    hass.states.async_remove("climate.hv")
    # setpoint-only: on = the comfort preset when it has one; off = honest "doesn't switch off"
    add(hass, "sp", "Термостат", area=bed.id, attrs={"hvac_modes": [HVACMode.HEAT], "preset_modes": ["comfort", "building_protection"], "preset_mode": "eco", "temperature": 22.0})
    assert "в режиме комфорт" in await say(hass, "HaufloThermostatOn", "спальня") and preset[-1].data["preset_mode"] == "comfort"
    off_text = await say(hass, "HaufloThermostatOff", "спальня")
    assert "не выключается" in off_text and "уставка 22°" in off_text and "включи защиту" in off_text
    n = len(on) + len(off) + len(hvac) + len(preset)
    # already at comfort: nothing to do, says so
    hass.states.async_set("climate.sp", "heat", {"hvac_modes": [HVACMode.HEAT], "preset_modes": ["comfort"], "preset_mode": "comfort", "temperature": 22.0, "friendly_name": "Термостат"})
    assert "уже работает и держит 22°" in await say(hass, "HaufloThermostatOn", "спальня")
    assert len(on) + len(off) + len(hvac) + len(preset) == n
    hass.states.async_set("climate.sp", "heat", {"hvac_modes": [HVACMode.HEAT], "friendly_name": "Термостат"})
    assert "не выключается отдельно" in await say(hass, "HaufloThermostatOff", "спальня")
    assert "уже работает." in await say(hass, "HaufloThermostatOn", "спальня")


async def test_protection_and_floor_only_touch_what_they_name(hass: HomeAssistant, rooms) -> None:
    bed, _ = rooms
    preset = async_mock_service(hass, "climate", "set_preset_mode")
    on = async_mock_service(hass, "climate", "turn_on")
    add(hass, "ac", "Кондиционер", area=bed.id, attrs={"preset_modes": ["building_protection"]})
    add(hass, "fl", "Тёплый пол", area=bed.id, attrs={"supported_features": TURN_ON, "preset_modes": []})
    assert "в режиме защиты здания" in await say(hass, "HaufloProtection", "спальня")
    assert [c.data["entity_id"] for c in preset] == ["climate.ac"]  # not the floor
    assert "Тёплый пол" in await say(hass, "HaufloHeatingOn", "спальня") and [c.data["entity_id"] for c in on] == ["climate.fl"]
    er.async_get(hass).async_remove("climate.ac")
    hass.states.async_remove("climate.ac")
    add(hass, "plain", "Кондей", area=bed.id)
    assert "нет режима защиты" in await say(hass, "HaufloProtection", "спальня")
    assert FLOOR_RU("тёплый пол") and not FLOOR_RU("потолок") and FLOOR_RU("пол в ванной")


def FLOOR_RU(s):
    return bool(voice.FLOOR.search(voice._norm(s)))


def test_norm():
    assert voice._norm(None) == "" and voice._norm("  ЁлкА ") == "елка"


# --- the sentences file ----------------------------------------------------------------------------------------------
def test_write_remove_and_strays(tmp_path):
    path = str(tmp_path / "custom_sentences" / "ru" / "hauflo.yaml")
    assert voice._remove(path) is False  # nothing there
    assert voice._write(path) is True and open(path, encoding="utf-8").read() == voice.SENTENCES
    assert voice._write(path) is False  # unchanged
    # an older generated file under another name goes; a human's stays
    folder = tmp_path / "custom_sentences" / "ru"
    (folder / "old.yaml").write_text(voice.HEADER + "x", encoding="utf-8")
    (folder / "mine.yaml").write_text("# my own\n", encoding="utf-8")
    (folder / "notes.txt").write_text(voice.HEADER, encoding="utf-8")
    (folder / "bad.yaml").write_bytes(b"\xff\xfe\x00bad")
    assert voice._write(path) is True
    assert not (folder / "old.yaml").exists() and (folder / "mine.yaml").exists() and (folder / "notes.txt").exists() and (folder / "bad.yaml").exists()
    assert voice._remove_strays(str(tmp_path / "nope" / "x.yaml")) is False
    # a human-written file under our name is never overwritten nor removed
    with open(path, "w", encoding="utf-8") as f:
        f.write("# human\nlanguage: ru\n")
    assert voice._write(path) is False and voice._remove(path) is False
    assert open(path, encoding="utf-8").read().startswith("# human")
    with open(path, "w", encoding="utf-8") as f:
        f.write(voice.SENTENCES)
    assert voice._remove(path) is True and not (folder / "hauflo.yaml").exists()


async def test_setup_unload_remove_reload_conversation(hass: HomeAssistant, tmp_path) -> None:
    reloads = async_mock_service(hass, "conversation", "reload")
    hass.config.config_dir = str(tmp_path)
    await voice.async_setup(hass)
    assert len(reloads) == 1 and reloads[0].data == {"language": "ru"}
    assert {h.intent_type for h in voice.HANDLERS} <= {i.intent_type for i in intent.async_get(hass)}
    await voice.async_setup(hass)
    assert len(reloads) == 1  # unchanged: no reload
    voice.async_unload(hass)
    assert not ({h.intent_type for h in voice.HANDLERS} & {i.intent_type for i in intent.async_get(hass)})
    await voice.async_remove(hass)
    assert len(reloads) == 2
    await voice.async_remove(hass)  # already gone: quiet
    assert len(reloads) == 2
    with patch.object(voice, "_write", side_effect=OSError("read-only")):
        await voice.async_setup(hass)  # voice is an extra: never stops the agent
