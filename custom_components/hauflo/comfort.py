"""«Комфортный пол» and the house's winter / summer mode (owner 25.10, TASKS-TODO 1.13; docs/comfort-floor.md).

The client sets the ROOM («в комнате 22°»), never a floor setpoint of 28–30°. Per room the agent keeps each floor loop
between a comfort minimum (the floor never stays cold) and an upper limit, stepping the loop's own setpoint by the
room's error — slowly (one write per interval) so the KNX bus isn't spammed. The house mode: in winter only the floor
heats (the room's ACs to «heat» with a low setpoint — silent), in summer the ACs cool to the room's target and the
floor rests at its minimum or off.

Runs here, in HA: it keeps working without Hauflo or the internet. Hauflo sends the integrator's settings through the
tunnel (GET/POST /api/hauflo/comfort, admin only); the agent checks them itself — it writes only to climates Hauflo
made, only inside the room's limits and the thermostat's own.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable
from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.select import SelectEntity
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
STORE_KEY = f"{DOMAIN}.comfort"
WINTER, SUMMER = "winter", "summer"
MODES = [WINTER, SUMMER]
# Hard bounds whatever the settings say: a floor loop's setpoint, the client's room target.
FLOOR_LO, FLOOR_HI = 5.0, 35.0
ROOM_LO, ROOM_HI = 16.0, 28.0
DEFAULT_MIN, DEFAULT_MAX, DEFAULT_TARGET = 24.0, 29.0, 22.0
DEFAULT_INTERVAL, INTERVAL_LO, INTERVAL_HI = 600, 60, 3600
DEFAULT_WINTER_AC = 16.0
HYSTERESIS = 0.3  # °C of room error the floor is left alone
STEP_LO, STEP_HI = 0.5, 2.0  # one change of a loop's setpoint: proportional to the room error, within these
AFTER_TARGET = 60  # s: the client's new target is acted on this soon after the previous write (not a whole interval)
TICK = 60
MAX_ROOMS, MAX_LOOPS = 64, 8
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_NOT_A_STATE = {STATE_UNAVAILABLE, STATE_UNKNOWN, None}


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def clean_config(raw: Any, owns: Callable[[str], bool] | None = None) -> tuple[dict[str, Any], list[str]]:
    """Only what the regulator understands, each value in its bounds; what is dropped is said (`dropped`).
    `owns`: may the agent write to this climate (Hauflo's own) — None when re-reading its own storage."""
    out: dict[str, Any] = {"rooms": [], "interval": DEFAULT_INTERVAL, "winter_ac": DEFAULT_WINTER_AC}
    dropped: list[str] = []
    if not isinstance(raw, dict):
        return out, dropped
    if (iv := _num(raw.get("interval"))) is not None:
        out["interval"] = int(_clamp(iv, INTERVAL_LO, INTERVAL_HI))
    if (ac := _num(raw.get("winter_ac"))) is not None:
        out["winter_ac"] = _clamp(ac, FLOOR_LO, 30.0)
    seen: set[str] = set()
    writes: set[str] = set()
    for r in (raw.get("rooms") if isinstance(raw.get("rooms"), list) else [])[: MAX_ROOMS * 2]:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not _ID_RE.match(r["id"]) or r["id"] in seen:
            dropped.append(f"room {str(r.get('id') if isinstance(r, dict) else r)[:64]!r}: bad id")
            continue
        rid = r["id"]

        def climates(key: str, rid: str = rid) -> list[str]:
            ok: list[str] = []
            for eid in r.get(key) if isinstance(r.get(key), list) else []:
                if not isinstance(eid, str) or not eid.startswith("climate.") or not _ENTITY_RE.match(eid) or eid.startswith(f"climate.{DOMAIN}_"):
                    dropped.append(f"{rid}: {key} {str(eid)[:80]!r} is not a climate")
                elif eid in writes:
                    dropped.append(f"{rid}: {eid} is driven by another room")
                elif owns is not None and not owns(eid):
                    dropped.append(f"{rid}: {eid} is not Hauflo's")
                elif len(ok) < MAX_LOOPS:
                    ok.append(eid)
                    writes.add(eid)
            return ok

        floors = climates("floors")
        acs = climates("acs")
        if not floors:
            dropped.append(f"{rid}: no floor climate")
            continue
        lo, hi = _num(r.get("min")), _num(r.get("max"))
        lo = _clamp(lo if lo is not None else DEFAULT_MIN, FLOOR_LO, FLOOR_HI)
        hi = _clamp(hi if hi is not None else DEFAULT_MAX, FLOOR_LO, FLOOR_HI)
        if hi - lo < 1:
            dropped.append(f"{rid}: limits {lo}–{hi} need 1° between them")
            continue
        sensor = r.get("sensor")
        if sensor is not None and not (isinstance(sensor, str) and _ENTITY_RE.match(sensor) and sensor.split(".")[0] in ("sensor", "climate") and not sensor.startswith(f"climate.{DOMAIN}_")):
            dropped.append(f"{rid}: sensor {str(sensor)[:80]!r} is not a sensor or a climate")
            sensor = None
        seen.add(rid)
        out["rooms"].append(
            {
                "id": rid,
                "name": str(r.get("name") or rid)[:60],
                "enabled": r.get("enabled", True) is not False,
                "floors": floors,
                "acs": acs,
                "sensor": sensor,
                "min": lo,
                "max": hi,
                "summer": "off" if r.get("summer") == "off" else "min",
            }
        )
        if len(out["rooms"]) >= MAX_ROOMS:
            break
    return out, dropped


class ComfortEngine:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: Store[dict[str, Any]] = Store(hass, 1, STORE_KEY)
        self.config: dict[str, Any] = clean_config(None)[0]
        self.mode = WINTER
        self.targets: dict[str, float] = {}
        self.off: set[str] = set()  # rooms the client switched off: the floor rests at its comfort minimum
        self.owns: Callable[[str], bool] | None = None
        self.add_climates: Callable[[list[Any]], None] | None = None
        self.add_selects: Callable[[list[Any]], None] | None = None
        self.entities: dict[str, ComfortClimate] = {}
        self.select: HouseMode | None = None
        self._started = time.monotonic()
        self._last_write: dict[str, float] = {}
        self._target_at: dict[str, float] = {}
        self._soon: dict[str, Callable[[], None]] = {}
        self._unsub_tick: Callable[[], None] | None = None
        self._unsub_states: Callable[[], None] | None = None
        self.log: list[dict[str, Any]] = []  # the last writes (GET shows them: what the agent did and why)

    # --- settings ---------------------------------------------------------------------------------

    async def async_load(self) -> None:
        data = await self.store.async_load() or {}
        self.config = clean_config(data.get("config"))[0]
        self.mode = SUMMER if data.get("mode") == SUMMER else WINTER
        self.targets = {str(k): _clamp(v, ROOM_LO, ROOM_HI) for k, x in (data.get("targets") or {}).items() if (v := _num(x)) is not None}
        self.off = {str(x) for x in data.get("off") or []}

    def _data(self) -> dict[str, Any]:
        return {"config": self.config, "mode": self.mode, "targets": self.targets, "off": sorted(self.off)}

    async def _save(self) -> None:
        # At once, not delayed: a client's change is rare, and a reload right after it must find it.
        await self.store.async_save(self._data())

    async def async_set(self, raw: Any) -> dict[str, Any]:
        config, dropped = clean_config(raw, self.owns)
        self.config = config
        await self.store.async_save(self._data())
        self.sync_entities()
        self._listen()
        return {**self.answer(), "dropped": dropped}

    async def async_remove(self) -> None:
        await self.store.async_remove()

    def rooms(self) -> dict[str, dict[str, Any]]:
        return {r["id"]: r for r in self.config["rooms"] if r["enabled"]}

    def answer(self) -> dict[str, Any]:
        rooms = self.rooms()
        return {
            "config": self.config,
            "mode": self.mode,
            "entities": {rid: e.entity_id for rid, e in self.entities.items() if rid in rooms and e.entity_id},
            "house_mode": self.select.entity_id if self.select else None,
            "rooms": [self.status(r) for r in rooms.values()],
            "log": self.log[-20:],
        }

    def status(self, room: dict[str, Any]) -> dict[str, Any]:
        def loop(eid: str) -> dict[str, Any]:
            s = self.hass.states.get(eid)
            a = s.attributes if s else {}
            return {"entity_id": eid, "state": s.state if s else None, "target": _num(a.get("temperature")), "current": _num(a.get("current_temperature")), "action": a.get("hvac_action")}

        last = self._last_write.get(room["id"])
        return {
            "id": room["id"],
            "target": self.target(room["id"]),
            "on": room["id"] not in self.off,
            "room": self.room_temp(room),
            "floors": [loop(e) for e in room["floors"]],
            "acs": [loop(e) for e in room["acs"]],
            "last_write_s": round(time.monotonic() - last) if last else None,
        }

    def target(self, rid: str) -> float:
        return self.targets.get(rid, DEFAULT_TARGET)

    def room_temp(self, room: dict[str, Any]) -> float | None:
        """The room's temperature: a sensor's value, or a climate's (an AC's, a room thermostat's) current one."""
        eid = room.get("sensor")
        s = self.hass.states.get(eid) if eid else None
        if s is None or s.state in _NOT_A_STATE:
            return None
        return _num(s.attributes.get("current_temperature")) if eid.startswith("climate.") else _num(s.state)

    # --- entities -------------------------------------------------------------------------------

    @callback
    def sync_entities(self) -> None:
        """A climate per enabled room, the house mode with the first one; a room taken off — its climate goes."""
        rooms = self.rooms()
        reg = er.async_get(self.hass)
        for rid in [r for r in self.entities if r not in rooms]:
            ent = self.entities.pop(rid)
            if ent.entity_id and reg.async_get(ent.entity_id):
                reg.async_remove(ent.entity_id)
        for rid, room in rooms.items():
            if rid in self.entities:
                self.entities[rid].room = room
                self.entities[rid].refresh()
            elif self.add_climates is not None:
                self.entities[rid] = ent = ComfortClimate(self, room)
                self.add_climates([ent])
        if rooms and self.select is None and self.add_selects is not None:
            self.select = HouseMode(self)
            self.add_selects([self.select])
        elif not rooms and self.select is not None:
            if self.select.entity_id and reg.async_get(self.select.entity_id):
                reg.async_remove(self.select.entity_id)
            self.select = None

    @callback
    def _refresh(self) -> None:
        for ent in self.entities.values():
            ent.refresh()
        if self.select:
            self.select.refresh()

    # --- running --------------------------------------------------------------------------------

    @callback
    def async_start(self) -> None:
        self._started = time.monotonic()
        self.sync_entities()
        self._listen()
        self._unsub_tick = async_track_time_interval(self.hass, self._tick, _seconds(TICK), name="hauflo comfort")

    @callback
    def async_stop(self) -> None:
        for unsub in [self._unsub_tick, self._unsub_states, *self._soon.values()]:
            if unsub:
                unsub()
        self._unsub_tick = self._unsub_states = None
        self._soon.clear()

    @callback
    def _listen(self) -> None:
        if self._unsub_states:
            self._unsub_states()
            self._unsub_states = None
        ids = sorted({e for r in self.rooms().values() for e in [*r["floors"], *r["acs"], *([r["sensor"]] if r["sensor"] else [])]})
        if ids:
            self._unsub_states = async_track_state_change_event(self.hass, ids, self._changed)

    @callback
    def _changed(self, _event: Event) -> None:
        self._refresh()

    async def _tick(self, _now: Any) -> None:
        for room in list(self.rooms().values()):
            await self.step(room)

    @callback
    def _later(self, rid: str, delay: float = 5) -> None:
        """Acted on a few seconds after the last press — five taps on «+» are one write."""
        if cancel := self._soon.pop(rid, None):
            cancel()

        async def _go(_now: Any) -> None:
            self._soon.pop(rid, None)
            if room := self.rooms().get(rid):
                await self.step(room)

        self._soon[rid] = async_call_later(self.hass, delay, _go)

    async def set_target(self, rid: str, value: float) -> None:
        self.targets[rid] = _clamp(round(value * 2) / 2, ROOM_LO, ROOM_HI)
        self._target_at[rid] = time.monotonic()
        await self._save()
        self._refresh()
        room = self.rooms().get(rid)
        if room and self.mode == SUMMER and rid not in self.off:
            # Summer: the room's target is the ACs' setpoint (one main thermostat per room — owner 27.10).
            for ac in room["acs"]:
                await self._set_temp(room, ac, self.targets[rid], "summer target")
        else:
            self._later(rid)

    async def set_on(self, rid: str, on: bool) -> None:
        (self.off.discard if on else self.off.add)(rid)
        self._target_at[rid] = time.monotonic()
        await self._save()
        self._refresh()
        self._later(rid)

    async def set_mode(self, mode: str) -> None:
        """The house's season: once, at the switch, the ACs and the floors are put to it; the regulator then
        follows it. Never repeated on every step — the client may still change an AC by hand."""
        if mode not in MODES:
            return
        self.mode = mode
        await self._save()
        self._refresh()
        for room in list(self.rooms().values()):
            for ac in room["acs"]:
                if mode == WINTER:
                    await self._set_hvac(room, ac, HVACMode.HEAT, "winter: AC heats, low — silent")
                    await self._set_temp(room, ac, self.config["winter_ac"], "winter: AC low")
                else:
                    await self._set_hvac(room, ac, HVACMode.COOL, "summer: AC cools")
                    await self._set_temp(room, ac, self.target(room["id"]), "summer: AC to the room target")
            if mode == WINTER:
                for fl in room["floors"]:
                    s = self.hass.states.get(fl)
                    if s and s.state == HVACMode.OFF:
                        await self._set_hvac(room, fl, HVACMode.HEAT, "winter: floor on")
            await self.step(room, force=True)

    def plan(self, room: dict[str, Any]) -> list[tuple[str, str, Any, str]]:
        """What the room's floor loops should be set to now: (entity, «temp»|«hvac», value, why)."""
        out: list[tuple[str, str, Any, str]] = []
        rid = room["id"]
        room_t = self.room_temp(room)
        target = self.target(rid)
        for eid in room["floors"]:
            s = self.hass.states.get(eid)
            if s is None or s.state in _NOT_A_STATE:
                continue
            a = s.attributes
            t_lo, t_hi = _num(a.get("min_temp")), _num(a.get("max_temp"))
            lo = max(room["min"], t_lo if t_lo is not None else FLOOR_LO)
            hi = min(room["max"], t_hi if t_hi is not None else FLOOR_HI)
            if lo > hi:
                continue  # the thermostat can't be kept in the room's limits at all — never forced
            sp = _num(a.get("temperature"))
            if self.mode == SUMMER:
                if room["summer"] == "off":
                    if HVACMode.OFF in (a.get("hvac_modes") or []):
                        if s.state != HVACMode.OFF:
                            out.append((eid, "hvac", HVACMode.OFF, "summer: floor off"))
                        continue
                    # No «off» on this thermostat: its own lowest setpoint, below the comfort minimum on purpose.
                    lo = hi = t_lo if t_lo is not None else FLOOR_LO
                    want, why = lo, "summer: floor at its lowest"
                else:
                    want, why = lo, "summer: floor at comfort minimum"
            elif rid in self.off:
                want, why = lo, "comfort off: floor at comfort minimum"
            elif sp is None:
                want, why = lo, "floor setpoint unknown: comfort minimum"
            elif room_t is None:
                want, why = _clamp(sp, lo, hi), "no room temperature: kept within limits"
            else:
                err = target - room_t
                if abs(err) <= HYSTERESIS:
                    want, why = _clamp(sp, lo, hi), f"room {room_t:.1f}° at target {target:.1f}°"
                else:
                    step = math.copysign(_clamp(round(abs(err) * 2) / 2, STEP_LO, STEP_HI), err)
                    want, why = _clamp(sp + step, lo, hi), f"room {room_t:.1f}°, target {target:.1f}°: floor {step:+.1f}°"
            stp = max(_num(a.get("target_temp_step")) or 0.5, 0.5)
            want = _clamp(round(want / stp) * stp, lo, hi)
            if sp is None or abs(want - sp) >= 0.05:
                out.append((eid, "temp", round(want, 2), why))
        return out

    async def step(self, room: dict[str, Any], *, force: bool = False) -> None:
        rid = room["id"]
        todo = self.plan(room)
        if not todo:
            return
        now = time.monotonic()
        last = self._last_write.get(rid, self._started)
        # Not more often than the interval; the client's own change — within a minute (after the previous write).
        need = 0 if force else AFTER_TARGET if self._target_at.get(rid, -1e18) > last - 1 else self.config["interval"]
        if now - last < need:
            return
        for eid, kind, value, why in todo:
            if kind == "hvac":
                await self._set_hvac(room, eid, value, why)
            else:
                await self._set_temp(room, eid, value, why)

    async def _set_temp(self, room: dict[str, Any], eid: str, value: float, why: str) -> None:
        s = self.hass.states.get(eid)
        if s is None or s.state in _NOT_A_STATE:
            return
        a = s.attributes
        t_lo, t_hi = _num(a.get("min_temp")), _num(a.get("max_temp"))
        value = _clamp(value, t_lo if t_lo is not None else FLOOR_LO, t_hi if t_hi is not None else FLOOR_HI)
        if eid in room["floors"] and not (self.mode == SUMMER and room["summer"] == "off"):
            value = _clamp(value, room["min"], room["max"])  # never outside the room's limits, whatever asked
        if (cur := _num(a.get("temperature"))) is not None and abs(cur - value) < 0.05:
            return
        await self._call(room, eid, "set_temperature", {ATTR_TEMPERATURE: value}, why)

    async def _set_hvac(self, room: dict[str, Any], eid: str, mode: HVACMode, why: str) -> None:
        s = self.hass.states.get(eid)
        if s is None or s.state in _NOT_A_STATE or s.state == mode or mode not in (s.attributes.get("hvac_modes") or []):
            return
        await self._call(room, eid, "set_hvac_mode", {"hvac_mode": mode}, why)

    async def _call(self, room: dict[str, Any], eid: str, service: str, data: dict[str, Any], why: str) -> None:
        self._last_write[room["id"]] = time.monotonic()
        entry = {"at": time.time(), "room": room["id"], "entity_id": eid, service: next(iter(data.values())), "why": why}
        try:
            await self.hass.services.async_call("climate", service, {"entity_id": eid, **data}, blocking=True)
        except Exception as err:  # noqa: BLE001 — one thermostat failing never stops the others
            entry["error"] = str(err)[:200]
            _LOGGER.warning("Hauflo comfort: %s %s failed: %s", service, eid, err)
        self.log = [*self.log[-49:], entry]


def _seconds(s: int):
    from datetime import timedelta  # noqa: PLC0415

    return timedelta(seconds=s)


def _ru(hass: HomeAssistant) -> bool:
    return str(hass.config.language or "").lower().startswith("ru")


class ComfortClimate(ClimateEntity):
    """«Комфорт · <комната>»: the room's target; the floor loops follow it (docs/comfort-floor.md)."""

    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = ROOM_LO
    _attr_max_temp = ROOM_HI
    _attr_target_temperature_step = 0.5
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
    _attr_icon = "mdi:home-thermometer"

    def __init__(self, engine: ComfortEngine, room: dict[str, Any]) -> None:
        self.engine = engine
        self.room = room
        self._attr_unique_id = f"comfort_{room['id']}"
        # By the room's name («climate.hauflo_comfort_komnata_2»); HA makes it unique, Hauflo is told the real one.
        self.entity_id = f"climate.{DOMAIN}_comfort_{slugify(room['name']) or slugify(room['id'])}"

    @property
    def name(self) -> str:
        return f"{'Комфорт' if _ru(self.hass) else 'Comfort'} · {self.room['name']}" if self.hass else self.room["name"]

    @property
    def hvac_modes(self) -> list[HVACMode]:
        return [HVACMode.COOL if self.engine.mode == SUMMER else HVACMode.HEAT, HVACMode.OFF]

    @property
    def hvac_mode(self) -> HVACMode:
        if self.room["id"] in self.engine.off:
            return HVACMode.OFF
        return HVACMode.COOL if self.engine.mode == SUMMER else HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction | None:
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        ids = self.room["acs"] if self.engine.mode == SUMMER else self.room["floors"]
        acts = [s.attributes.get("hvac_action") for e in ids if (s := self.hass.states.get(e))]
        doing = HVACAction.COOLING if self.engine.mode == SUMMER else HVACAction.HEATING
        return doing if doing in acts else HVACAction.IDLE if acts else None

    @property
    def current_temperature(self) -> float | None:
        return self.engine.room_temp(self.room)

    @property
    def target_temperature(self) -> float:
        return self.engine.target(self.room["id"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        floors = [_num(s.attributes.get("temperature")) for e in self.room["floors"] if (s := self.hass.states.get(e))]
        return {
            "house_mode": self.engine.mode,
            "floor_min": self.room["min"],
            "floor_max": self.room["max"],
            "floor_targets": floors,
            "floors": self.room["floors"],
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (t := _num(kwargs.get(ATTR_TEMPERATURE))) is not None:
            await self.engine.set_target(self.room["id"], t)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.engine.set_on(self.room["id"], hvac_mode != HVACMode.OFF)

    async def async_turn_on(self) -> None:
        await self.engine.set_on(self.room["id"], True)

    async def async_turn_off(self) -> None:
        await self.engine.set_on(self.room["id"], False)

    async def async_added_to_hass(self) -> None:
        # In the room of its floor (the floor climate's area or its device's) — once, a move by hand stays.
        reg = er.async_get(self.hass)
        mine = reg.async_get(self.entity_id)
        if mine is not None and mine.area_id is None:
            for fl in self.room["floors"]:
                ent = reg.async_get(fl)
                area = ent.area_id if ent else None
                if ent and not area and ent.device_id and (dev := dr.async_get(self.hass).async_get(ent.device_id)):
                    area = dev.area_id
                if area:
                    reg.async_update_entity(self.entity_id, area_id=area)
                    break

    @callback
    def refresh(self) -> None:
        if self.hass is not None and self.entity_id and self.platform is not None:
            self.async_write_ha_state()


class HouseMode(SelectEntity):
    """«Режим дома»: зима — греет только пол, кондиционеры молчат; лето — кондиционеры охлаждают, пол отдыхает."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "house_mode"
    _attr_options = MODES
    _attr_icon = "mdi:sun-snowflake-variant"

    def __init__(self, engine: ComfortEngine) -> None:
        self.engine = engine
        self._attr_unique_id = "house_mode"
        self.entity_id = f"select.{DOMAIN}_house_mode"

    @property
    def device_info(self) -> DeviceInfo | None:
        entry = self.platform.config_entry if self.platform else None
        return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Hauflo", manufacturer="Hauflo", entry_type=DeviceEntryType.SERVICE) if entry else None

    @property
    def current_option(self) -> str:
        return self.engine.mode

    async def async_select_option(self, option: str) -> None:
        await self.engine.set_mode(option)

    @callback
    def refresh(self) -> None:
        if self.hass is not None and self.entity_id and self.platform is not None:
            self.async_write_ha_state()


class ComfortView(HomeAssistantView):
    """Hauflo's settings of «Комфортный пол» (through the tunnel): GET what the agent holds and does, POST new ones."""

    url = "/api/hauflo/comfort"
    name = "api:hauflo:comfort"
    requires_auth = True

    def __init__(self, engine_of: Callable[[], ComfortEngine | None]) -> None:
        self.engine_of = engine_of

    async def get(self, request):
        engine = self.engine_of()
        if not engine:
            return self.json_message("Hauflo agent not set up", 503)
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        return self.json(engine.answer())

    async def post(self, request):
        engine = self.engine_of()
        if not engine:
            return self.json_message("Hauflo agent not set up", 503)
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict):
            return self.json_message("an object expected", 400)
        return self.json(await engine.async_set(body))
