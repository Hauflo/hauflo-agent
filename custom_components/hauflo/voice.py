"""Voice: «включи / выключи тёплый пол», «выключи термостат», «включи защиту» [в комнате].

A KNX floor heating or room thermostat usually has no on/off object — it keeps the climate all the
time, and HA's own «включи / выключи» fails on it with a general error. The agent adds these
sentences to HA (its own file in custom_sentences) and answers them: switches where the thermostat
can, otherwise says it works by its setpoint; a thermostat with the «building protection» mode is
offered that, never switched to it unasked (owner 2026-10-24).
"""

from __future__ import annotations

import contextlib
import logging
import os
import re

from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.core import HomeAssistant, State
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, intent

_LOGGER = logging.getLogger(__name__)

HEADER = "# Hauflo agent — generated, removed with the integration. Do not edit.\n"
_IN = "[(в|во|на) {area}]"
_FLOOR = "([тёплый|теплый|тёплые|теплые] (пол|полы)|подогрев пола)"
_THERMO = "(термостат|терморегулятор|кондиционер|кондей|климат|климат контроль)"
SENTENCES = HEADER + f"""language: ru
intents:
  HaufloHeatingOn:
    data:
      - sentences:
          - "(включи|включить|включай|вруби) {_FLOOR} {_IN}"
  HaufloHeatingOff:
    data:
      - sentences:
          - "(выключи|выключить|отключи|выключай|выруби) {_FLOOR} {_IN}"
  HaufloThermostatOn:
    data:
      - sentences:
          - "(включи|включить|включай|вруби) {_THERMO} {_IN}"
          - "(включи|верни) ([режим] комфорт|комфортный режим) {_IN}"
  HaufloThermostatOff:
    data:
      - sentences:
          - "(выключи|выключить|отключи|выключай|выруби) {_THERMO} {_IN}"
  HaufloSetTemperature:
    data:
      - sentences:
          - "(установи|поставь|сделай|выстави) температуру {_IN} на {{hauflo_temperature}} [градус|градуса|градусов]"
          - "(установи|поставь|сделай|выстави) {_IN} [температуру] {{hauflo_temperature}} [градус|градуса|градусов]"
  HaufloGetTemperature:
    data:
      - sentences:
          - "(какая|сколько) [сейчас] (температура|градусов) {_IN}"
          - "(какая|сколько) {_IN} [сейчас] (температура|градусов)"
  HaufloProtection:
    data:
      - sentences:
          - "(включи|переведи [в]) [режим] (защиту|защиты) [здания|от перегрева|от замерзания] {_IN}"
lists:
  hauflo_temperature:
    range:
      from: 5
      to: 40
"""
FLOOR = re.compile(r"(^|[^а-я])пол")
PROTECTION, COMFORT = "building_protection", "comfort"


def _norm(text: str | None) -> str:
    return (text or "").lower().replace("ё", "е").strip()


def _sentences_path(hass: HomeAssistant) -> str:
    return hass.config.path("custom_sentences", "ru", "hauflo.yaml")


def _remove_strays(path: str) -> bool:
    """An earlier agent's sentences under another file name (round 6: «Unknown intent» after an upgrade — its intents
    have no handler now): ours by the header, gone. Files a human wrote are left alone."""
    folder, gone = os.path.dirname(path), False
    with contextlib.suppress(FileNotFoundError):
        for name in os.listdir(folder):
            other = os.path.join(folder, name)
            if other == path or not name.endswith(".yaml"):
                continue
            with contextlib.suppress(OSError, UnicodeDecodeError):
                with open(other, encoding="utf-8") as f:
                    ours = f.readline() == HEADER
                if ours:
                    os.remove(other)
                    gone = True
    return gone


def _write(path: str) -> bool:
    """Our file only: one a human put there under the same name is left alone. True = the sentences changed."""
    changed = _remove_strays(path)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            current = f.read()
        if current == SENTENCES or not current.startswith(HEADER):
            return changed
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(SENTENCES)
    return True


def _remove(path: str) -> bool:
    with contextlib.suppress(FileNotFoundError):
        with open(path, encoding="utf-8") as f:
            if not f.read().startswith(HEADER):
                return False
        os.remove(path)
        return True
    return False


async def _reload(hass: HomeAssistant) -> None:
    if hass.services.has_service("conversation", "reload"):
        await hass.services.async_call("conversation", "reload", {"language": "ru"}, blocking=True)


def _attr(s: State, key: str) -> list:
    return list(s.attributes.get(key) or [])


class _Climate(intent.IntentHandler):
    """kind: "floor" — floor heating (named «… пол …»), "thermostat" — every other thermostat, "room" —
    the room's thermostat, its floor heating if it has none («установи температуру в комнате» with an
    AC and a floor heating there: HA's own answer is «несколько объектов»).
    action: "on", "off", "protect", "set" or "get"."""

    def __init__(self, intent_type: str, kind: str, action: str) -> None:
        self.intent_type = intent_type
        self.kind = kind
        self.action = action

    def _targets(self, intent_obj: intent.Intent) -> tuple[list[State] | None, str]:
        """(thermostats, «в комнате 2» as said); None — no room said and more than one in the house."""
        hass = intent_obj.hass
        slot = intent_obj.slots.get("area") or {}
        said = _norm(str(slot.get("text") or slot.get("value") or ""))
        # «в комнате 2» / «на кухне» as said: the preposition right before the room.
        m = re.search(rf"(?:^|\s)((?:в|во|на)\s+{re.escape(said)})", _norm(getattr(intent_obj, "text_input", None) or "")) if said else None
        where = f" {m.group(1)}" if m else (f" в {said}" if said else "")
        area_ids: set[str] | None = None
        if said:
            value = _norm(str(slot.get("value") or ""))
            area_ids = {
                a.id for a in ar.async_get(hass).async_list_areas()
                if {value, said} & {_norm(a.id), _norm(a.name), *(_norm(x) for x in a.aliases if isinstance(x, str))}
            }
        devs = dr.async_get(hass)
        found = []
        for e in er.async_get(hass).entities.values():
            if e.domain != "climate" or e.disabled_by or not (state := hass.states.get(e.entity_id)):
                continue
            # Only what's exposed to Assist — a thermostat the owner hid stays out of voice (HA core review 25.10).
            if not async_should_expose(hass, "conversation", e.entity_id):
                continue
            # Aliases may hold markers (ComputedNameType: «the entity's name»), not text.
            names = [n for n in (e.name, e.original_name, state.attributes.get("friendly_name"), *e.aliases) if isinstance(n, str)]
            floor = any(FLOOR.search(_norm(n)) for n in names)
            if self.kind != "room" and floor != (self.kind == "floor"):
                continue
            device = devs.async_get(e.device_id) if e.device_id else None
            area = e.area_id or (device.area_id if device else None)
            if area_ids is None or area in area_ids:
                found.append((state, floor))
        if self.kind == "room":
            found = [x for x in found if not x[1]] or found
        found = [x[0] for x in found]
        return (None if area_ids is None and len(found) > 1 else found), where

    async def _call(self, intent_obj: intent.Intent, calls: list[tuple[State, str, dict]]) -> None:
        for s, service, extra in calls:
            await intent_obj.hass.services.async_call(
                "climate", service, {"entity_id": s.entity_id, **extra}, blocking=True, context=intent_obj.context
            )

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        response = intent_obj.create_response()
        say = response.async_set_speech
        what = "Тёплый пол" if self.kind == "floor" else "Термостат"
        if self.kind == "room":
            what = "Температура"
        targets, where = self._targets(intent_obj)
        if targets is None:
            example = {"set": "установи температуру в спальне на 22", "get": "какая температура в спальне"}.get(
                self.action, f"{'выключи' if self.action == 'off' else 'включи'} {what.lower()} в спальне"
            )
            say(f"Скажите, в какой комнате: например, «{example}».")
            return response
        if not targets:
            say(f"{'Тёплого пола' if self.kind == 'floor' else 'Термостата'}{where} не нашёл.")
            return response

        if self.action == "get":
            temps = [t for s in targets if isinstance(t := s.attributes.get("current_temperature"), (int, float))]
            if not temps:
                say(f"Не знаю температуру{where}: термостат её не сообщает.")
            else:
                say(f"{where.strip().capitalize() + ' сейчас' if where else 'Сейчас'} {round(sum(temps) / len(temps), 1):g}°.")
            return response
        if self.action == "set":
            try:
                value = float((intent_obj.slots.get("hauflo_temperature") or {}).get("value"))
            except (TypeError, ValueError):
                say("Не расслышал температуру: например, «установи температуру в спальне на 22».")
                return response
            calls = []
            for s in targets:
                low, high = s.attributes.get("min_temp", value), s.attributes.get("max_temp", value)
                calls.append((s, "set_temperature", {"temperature": min(max(value, low), high)}))
            await self._call(intent_obj, calls)
            got = {c[2]["temperature"] for c in calls}
            text = f"{got.pop():g}°" if len(got) == 1 else f"{value:g}° (в пределах термостата)"
            say(f"{what}{where}: {text}.")
            return response
        if self.action == "protect":
            calls = [(s, "set_preset_mode", {"preset_mode": PROTECTION}) for s in targets if PROTECTION in _attr(s, "preset_modes")]
            await self._call(intent_obj, calls)
            say(f"{what}{where} в режиме защиты здания." if calls else f"У термостата{where} нет режима защиты.")
            return response

        on = self.action == "on"
        feature = ClimateEntityFeature.TURN_ON if on else ClimateEntityFeature.TURN_OFF
        calls = []
        for s in targets:
            modes = _attr(s, "hvac_modes")
            if s.attributes.get("supported_features", 0) & feature:
                calls.append((s, "turn_on" if on else "turn_off", {}))
            elif HVACMode.OFF in modes and len(modes) > 1:
                # Heat-only (no «off» mode) is a setpoint thermostat: «включи» would change nothing.
                calls.append((s, "set_hvac_mode", {"hvac_mode": next(m for m in modes if m != HVACMode.OFF) if on else HVACMode.OFF}))
            elif on and COMFORT in _attr(s, "preset_modes") and s.attributes.get("preset_mode") != COMFORT:
                calls.append((s, "set_preset_mode", {"preset_mode": COMFORT}))
        if calls:
            await self._call(intent_obj, calls)
            if on and all(c[1] == "set_preset_mode" for c in calls):
                say(f"{what}{where} в режиме комфорт.")
            else:
                say(f"{what}{where} {'включён' if on else 'выключен'}.")
            return response

        temps = {s.attributes.get("temperature") for s in targets}
        temp = temps.pop() if len(temps) == 1 else None
        holds = f" и держит {temp:g}°" if isinstance(temp, (int, float)) else ""
        if on:
            say(f"{what}{where} уже работает{holds}.")
        elif any(PROTECTION in _attr(s, "preset_modes") for s in targets):
            say(f"{what}{where} не выключается: он всегда держит климат{f' — уставка {temp:g}°' if holds else ''}. Могу перевести в защиту здания — скажите «включи защиту{where}».")
        else:
            say(f"{what}{where} не выключается отдельно: он работает по термостату{holds}. Скажите, например: «установи {what.lower()}{where} на 23».")
        return response


HANDLERS = (
    _Climate("HaufloHeatingOn", "floor", "on"),
    _Climate("HaufloHeatingOff", "floor", "off"),
    _Climate("HaufloThermostatOn", "thermostat", "on"),
    _Climate("HaufloThermostatOff", "thermostat", "off"),
    _Climate("HaufloProtection", "thermostat", "protect"),
    _Climate("HaufloSetTemperature", "room", "set"),
    _Climate("HaufloGetTemperature", "room", "get"),
)


async def async_setup(hass: HomeAssistant) -> None:
    for handler in HANDLERS:
        intent.async_register(hass, handler)
    try:
        if await hass.async_add_executor_job(_write, _sentences_path(hass)):
            await _reload(hass)
    except Exception:  # noqa: BLE001 — voice is an extra, the link must come up anyway
        _LOGGER.exception("Hauflo voice sentences not installed")


def async_unload(hass: HomeAssistant) -> None:
    remove = getattr(intent, "async_remove", None)
    for handler in HANDLERS:
        if remove:
            remove(hass, handler.intent_type)


async def async_remove(hass: HomeAssistant) -> None:
    with contextlib.suppress(Exception):
        if await hass.async_add_executor_job(_remove, _sentences_path(hass)):
            await _reload(hass)
