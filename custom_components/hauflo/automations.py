"""«Автоматизации» — the client's own simple rules from the dashboard (owner 28.10: «дал доступ к гео, подтвердил,
указал закат/рассвет, какие сдвиги по времени, указал, что должно когда произойти»): sunrise / sunset ± minutes, a time
of day, a phone's alarm clock (Android: the HA app's next-alarm sensor; iPhone: a Shortcuts automation fires an event).
Plan and limits: docs/automations.md.

Every rule is a real HA automation in automations.yaml (it runs without Hauflo or the internet), id `hauflo_auto_…`,
written only here — never through the tunnel (no generic automation-create there). What a rule may do is fixed:
open / close / set the position of a Hauflo cover, switch a Hauflo light on (brightness) or off, a Hauflo scene on —
never a lock, the alarm, a valve or a garage door / gate cover, never any other service or entity.

The house's place: HA's own home location (latitude / longitude / elevation only, nothing else of the core config) —
found by the house's internet address (HA's own onboarding helper; the HA box has no GPS) or the phone, saved only
after the person confirms it.

GET/POST /api/hauflo/automations — any signed-in person of this HA (the client isn't an admin), as «Зима / лето»; not through
the tunnel.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import datetime as dt
import logging
import math
import re
import secrets
import time
from typing import Any

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)
STORE_KEY = "hauflo.automations"
PREFIX = "hauflo_auto_"
ALARM_EVENT = "hauflo_alarm_"
AUTOMATIONS = "automations.yaml"
EVENTS = ("sunrise", "sunset", "time", "alarm_android", "alarm_iphone")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_OFFSET = 120
MAX_RULES = 50
MAX_ACTIONS = 20
MAX_RAMP_MIN = 60
RAMP_STEPS = 12
_FROM = re.compile(r"[a-z_]{1,32}")
_ID = re.compile(r"hauflo_auto_[0-9a-f]{12}")
_ALARM_CODE = re.compile(r"hauflo_alarm_[0-9a-f]{6}")
_TIME = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
# HA's place before anyone set it: 0/0 (a fresh install) or the old San Diego default.
_UNSET = ((0.0, 0.0), (32.87336, -117.22743))
# What a rule may do, by domain: the service and what its value is.
_DO = {
    "cover": {"open": "cover.open_cover", "close": "cover.close_cover", "position": "cover.set_cover_position"},
    # «ramp»: a wake-up light, brightness up in steps over minutes (KNX lights in HA have no transition).
    "light": {"on": "light.turn_on", "off": "light.turn_off", "ramp": "light.turn_on"},
    "scene": {"on": "scene.turn_on"},
}


class Invalid(ValueError):
    """A request the agent refuses (400)."""


def _int(value: Any, lo: int, hi: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise Invalid(f"{what}: an integer {lo}…{hi}")
    return value


def _float(value: Any, lo: float, hi: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise Invalid(f"{what}: a number {lo}…{hi}")
    return float(value)


def _clean_name(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise Invalid("name: text")
    return " ".join("".join(c for c in value if c.isprintable()).split())[:80]


def _offset_text(minutes: int) -> str:
    sign = "-" if minutes < 0 else ""
    m = abs(minutes)
    return f"{sign}{m // 60:02d}:{m % 60:02d}:00"


class Automations:
    def __init__(self, hass: HomeAssistant, owns: Callable[[str], bool]) -> None:
        self.hass = hass
        self.owns = owns
        self.store = Store(hass, 1, STORE_KEY)
        self.lock = asyncio.Lock()

    # --- what may be picked ------------------------------------------------------------------------------------------
    def _area(self, eid: str) -> str | None:
        ent = er.async_get(self.hass).async_get(eid)
        if ent is None:
            return None
        if ent.area_id:
            return ent.area_id
        if ent.device_id and (dev := dr.async_get(self.hass).async_get(ent.device_id)):
            return dev.area_id
        return None

    def _pickable(self, eid: Any) -> bool:
        """A Hauflo cover / light / scene, on the client's screen, safe (owns() leaves out locks, the alarm, valves,
        garage doors and gates)."""
        if not isinstance(eid, str) or eid.split(".")[0] not in _DO or self.hass.states.get(eid) is None:
            return False
        ent = er.async_get(self.hass).async_get(eid)
        if ent is None or ent.disabled_by or ent.hidden_by:
            return False
        return bool(self.owns(eid))

    def _alarm_sensor(self, eid: Any) -> bool:
        """A phone's «next alarm» sensor of the HA app (Android) — the only sensor a rule may follow."""
        if not isinstance(eid, str) or not eid.startswith("sensor."):
            return False
        ent = er.async_get(self.hass).async_get(eid)
        if ent is None or ent.platform != "mobile_app" or ent.disabled_by:
            return False
        return str(ent.unique_id).endswith("next_alarm") or eid.endswith("_next_alarm")

    def _room_lights(self, area_id: str) -> list[str]:
        """The Hauflo lights of a room — the single lamps; the room's group only when there are none."""
        reg = er.async_get(self.hass)
        found = [eid for eid in self.hass.states.async_entity_ids("light") if self._pickable(eid) and self._area(eid) == area_id]
        single = [eid for eid in found if (e := reg.async_get(eid)) is not None and e.platform != "group"]
        return sorted(single or found)

    def targets(self) -> dict[str, Any]:
        areas = ar.async_get(self.hass)
        out: dict[str, list[dict[str, Any]]] = {"cover": [], "light": [], "scene": []}
        rooms: dict[str, int] = {}
        for domain in out:
            for eid in sorted(self.hass.states.async_entity_ids(domain)):
                if not self._pickable(eid):
                    continue
                s = self.hass.states.get(eid)
                area = self._area(eid)
                item: dict[str, Any] = {"entity_id": eid, "name": s.attributes.get("friendly_name") or eid, "area": area}
                if domain == "cover":
                    item["position"] = bool(int(s.attributes.get("supported_features") or 0) & 4)
                if domain == "light":
                    modes = s.attributes.get("supported_color_modes") or []
                    item["dim"] = any(m != "onoff" for m in modes)
                out[domain].append(item)
        for area_id in {i["area"] for i in out["light"] if i["area"]}:
            if n := len(self._room_lights(area_id)):
                rooms[area_id] = n
        names = {a: (area.name if (area := areas.async_get_area(a)) else a) for a in {i["area"] for v in out.values() for i in v if i["area"]}}
        alarms = []
        for ent in er.async_get(self.hass).entities.values():
            if ent.domain == "sensor" and self._alarm_sensor(ent.entity_id):
                dev = dr.async_get(self.hass).async_get(ent.device_id) if ent.device_id else None
                s = self.hass.states.get(ent.entity_id)
                alarms.append({"entity_id": ent.entity_id, "phone": (dev.name_by_user or dev.name) if dev else ent.entity_id, "next": s.state if s else None})
        return {
            "alarms": sorted(alarms, key=lambda a: str(a["phone"])),
            "covers": out["cover"],
            "lights": out["light"],
            "scenes": out["scene"],
            "rooms": sorted(({"area_id": a, "name": names.get(a, a), "lights": n} for a, n in rooms.items()), key=lambda r: r["name"]),
            "areas": names,
        }

    # --- rules -------------------------------------------------------------------------------------------------------
    def validate(self, raw: Any, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        """A rule as the dashboard sends it → the stored rule; anything else is refused."""
        if not isinstance(raw, dict):
            raise Invalid("rule: an object")
        when = raw.get("when")
        if not isinstance(when, dict) or when.get("event") not in EVENTS:
            raise Invalid("when.event: sunrise, sunset or time")
        rule_when: dict[str, Any] = {"event": when["event"]}
        if when["event"] == "time":
            at = when.get("at")
            if not isinstance(at, str) or not _TIME.fullmatch(at):
                raise Invalid("when.at: HH:MM")
            rule_when["at"] = at
        elif when["event"] == "alarm_iphone":
            # The event's name is the agent's (a new one per rule, kept on edits): never a name the client sends.
            code = (previous or {}).get("when", {}).get("code")
            rule_when["code"] = code if isinstance(code, str) and _ALARM_CODE.fullmatch(code) else ALARM_EVENT + secrets.token_hex(3)
        else:
            rule_when["offset"] = _int(when.get("offset", 0), -MAX_OFFSET, MAX_OFFSET, "when.offset (minutes)")
            if when["event"] == "alarm_android":
                sensor = when.get("sensor")
                if not self._alarm_sensor(sensor):
                    raise Invalid("when.sensor: a phone's next-alarm sensor (HA app)")
                rule_when["sensor"] = sensor
        days = raw.get("days", list(DAYS))
        if not isinstance(days, list) or not days or any(d not in DAYS for d in days) or len(set(days)) != len(days):
            raise Invalid("days: some of mon…sun")
        actions = raw.get("actions")
        if not isinstance(actions, list) or not actions or len(actions) > MAX_ACTIONS:
            raise Invalid(f"actions: 1…{MAX_ACTIONS}")
        clean: list[dict[str, Any]] = []
        for a in actions:
            if not isinstance(a, dict):
                raise Invalid("action: an object")
            do = a.get("do")
            if do == "room_off":
                area_id = a.get("area_id")
                if not isinstance(area_id, str) or not self._room_lights(area_id):
                    raise Invalid("room_off: a room with Hauflo lights")
                clean.append({"do": "room_off", "area_id": area_id})
                continue
            eid = a.get("entity_id")
            if not self._pickable(eid):
                raise Invalid(f"{eid!r}: not a Hauflo cover, light or scene")
            domain = eid.split(".")[0]
            if do not in _DO[domain]:
                raise Invalid(f"{eid}: do one of {', '.join(_DO[domain])}")
            item: dict[str, Any] = {"entity_id": eid, "do": do}
            if do == "position":
                item["value"] = _int(a.get("value"), 0, 100, "position %")
            elif domain == "light" and do == "on" and a.get("value") is not None:
                item["value"] = _int(a.get("value"), 1, 100, "brightness %")
            elif do == "ramp":
                modes = self.hass.states.get(eid).attributes.get("supported_color_modes") or []
                if not any(m != "onoff" for m in modes):
                    raise Invalid(f"{eid}: not a dimmable light")
                item["value"] = _int(a.get("value"), 1, 100, "brightness %")
                item["minutes"] = _int(a.get("minutes"), 1, MAX_RAMP_MIN, "ramp minutes")
            clean.append(item)
        if sum(1 for a in clean if a["do"] == "ramp") > 4:
            raise Invalid("at most 4 wake-up lights")
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise Invalid("enabled: true or false")
        origin = raw.get("from")
        if origin is not None and (not isinstance(origin, str) or not _FROM.fullmatch(origin)):
            raise Invalid("from: a suggestion's key")
        out = {"when": rule_when, "days": [d for d in DAYS if d in days], "actions": clean, "enabled": enabled, "name": _clean_name(raw.get("name"))}
        if origin:
            out["from"] = origin
        return out

    @staticmethod
    def _ramp(a: dict[str, Any]) -> list[dict[str, Any]]:
        """Brightness 1 % → the target in even steps over the minutes: one telegram a step, no templates."""
        steps = min(RAMP_STEPS, a["minutes"] * 2, a["value"])
        pause = round(a["minutes"] * 60 / steps)
        out: list[dict[str, Any]] = []
        for i in range(1, steps + 1):
            if i > 1:
                out.append({"delay": {"seconds": pause}})
            out.append({"action": "light.turn_on", "target": {"entity_id": a["entity_id"]}, "data": {"brightness_pct": max(1, round(a["value"] * i / steps))}})
        return out

    def _service_calls(self, rule: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        ramps = [self._ramp(a) for a in rule["actions"] if a["do"] == "ramp"]
        for a in rule["actions"]:
            if a["do"] == "ramp":
                continue
            if a["do"] == "room_off":
                lights = self._room_lights(a["area_id"])
                if lights:
                    out.append({"action": "light.turn_off", "target": {"entity_id": lights}})
                continue
            domain = a["entity_id"].split(".")[0]
            call: dict[str, Any] = {"action": _DO[domain][a["do"]], "target": {"entity_id": a["entity_id"]}}
            if a["do"] == "position":
                call["data"] = {"position": a["value"]}
            elif domain == "light" and a.get("value") is not None:
                call["data"] = {"brightness_pct": a["value"]}
            out.append(call)
        # The wake-up lights last (they take minutes), side by side.
        if len(ramps) == 1:
            out += ramps[0]
        elif ramps:
            out.append({"parallel": [{"sequence": r} for r in ramps]})
        return out

    def automation(self, rule_id: str, rule: dict[str, Any]) -> dict[str, Any]:
        """The HA automation of a rule (the new syntax: triggers / conditions / actions)."""
        when = rule["when"]
        if when["event"] == "time":
            trigger: dict[str, Any] = {"trigger": "time", "at": when["at"] + ":00"}
        elif when["event"] == "alarm_iphone":
            trigger = {"trigger": "event", "event_type": when["code"]}
        elif when["event"] == "alarm_android":
            trigger = {"trigger": "time", "at": {"entity_id": when["sensor"], "offset": _offset_text(when["offset"])}}
        else:
            trigger = {"trigger": "sun", "event": when["event"]}
            if when["offset"]:
                trigger["offset"] = _offset_text(when["offset"])
        ru = str(self.hass.config.language or "").startswith("ru")
        name = rule.get("name") or ("Автоматизация" if ru else "Automation")
        cfg: dict[str, Any] = {
            "id": rule_id,
            "alias": f"Hauflo · {name}",
            "description": "Создано в Hauflo: дашборд → Автоматизации. Меняйте там."
            if ru
            else "Made in Hauflo: dashboard → Automations. Change it there.",
            "triggers": [trigger],
            "conditions": [{"condition": "time", "weekday": list(rule["days"])}] if len(rule["days"]) < 7 else [],
            "actions": self._service_calls(rule),
            "mode": "single",
            "initial_state": rule["enabled"],
        }
        return cfg

    async def _read_file(self) -> list[dict[str, Any]]:
        from homeassistant.util.yaml import load_yaml  # noqa: PLC0415
        import os  # noqa: PLC0415

        path = self.hass.config.path(AUTOMATIONS)

        def read() -> Any:
            return load_yaml(path) if os.path.isfile(path) else None

        data = await self.hass.async_add_executor_job(read)
        if data is None:
            return []
        if not isinstance(data, list):
            raise RuntimeError("automations.yaml is not a list")
        return data

    async def _write_file(self, data: list[dict[str, Any]]) -> None:
        from homeassistant.util.file import write_utf8_file_atomic  # noqa: PLC0415
        from homeassistant.util.yaml import dump  # noqa: PLC0415

        path = self.hass.config.path(AUTOMATIONS)
        contents = dump(data)  # before the file is opened: a dump error never truncates it
        await self.hass.async_add_executor_job(write_utf8_file_atomic, path, contents)

    def entity_of(self, rule_id: str) -> str | None:
        return er.async_get(self.hass).async_get_entity_id("automation", "automation", rule_id)

    async def _put(self, rule_id: str, cfg: dict[str, Any] | None) -> None:
        """Write (or remove, with None) one of Hauflo's automations; only ever an id with the prefix."""
        if not _ID.fullmatch(rule_id):
            raise Invalid("id: not Hauflo's")
        if cfg is not None:
            from homeassistant.components.automation.config import async_validate_config_item  # noqa: PLC0415

            # HA's own check, as its automation editor does (a changed HA schema fails here, not in the file).
            await async_validate_config_item(self.hass, rule_id, cfg)
        data = await self._read_file()
        kept = [a for a in data if not (isinstance(a, dict) and a.get("id") == rule_id)]
        if cfg is not None:
            kept.append(cfg)
        await self._write_file(kept)
        if cfg is not None:
            await self.hass.services.async_call("automation", "reload", {"id": rule_id}, blocking=True)
        elif eid := self.entity_of(rule_id):
            er.async_get(self.hass).async_remove(eid)

    async def _load(self) -> dict[str, Any]:
        data = await self.store.async_load()
        return data if isinstance(data, dict) and isinstance(data.get("rules"), dict) else {"rules": {}}

    async def save(self, raw: Any, user: str | None) -> dict[str, Any]:
        async with self.lock:
            data = await self._load()
            rule_id = raw.get("id") if isinstance(raw, dict) else None
            if rule_id is not None:
                if not isinstance(rule_id, str) or not _ID.fullmatch(rule_id):
                    raise Invalid("id: not Hauflo's")
                if rule_id not in data["rules"]:
                    raise KeyError(rule_id)
            elif len(data["rules"]) >= MAX_RULES:
                raise Invalid(f"at most {MAX_RULES} rules")
            rule = self.validate(raw, data["rules"].get(rule_id) if rule_id else None)
            rule_id = rule_id or PREFIX + secrets.token_hex(6)
            rule["by"], rule["at"] = user, time.time()
            await self._put(rule_id, self.automation(rule_id, rule))
            data["rules"][rule_id] = rule
            await self.store.async_save(data)
        await self._apply_enabled(rule_id, rule["enabled"])
        return {"id": rule_id, **rule}

    async def _apply_enabled(self, rule_id: str, enabled: bool) -> None:
        # initial_state holds it over restarts; the switch now (a reload keeps an unchanged automation's state).
        for _ in range(20):
            if eid := self.entity_of(rule_id):
                await self.hass.services.async_call("automation", "turn_on" if enabled else "turn_off", {"entity_id": eid}, blocking=True)
                return
            await asyncio.sleep(0.1)

    async def set_enabled(self, rule_id: Any, enabled: Any) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise Invalid("enabled: true or false")
        async with self.lock:
            data = await self._load()
            rule = self._own(data, rule_id)
            rule["enabled"] = enabled
            await self._put(rule_id, self.automation(rule_id, rule))
            await self.store.async_save(data)
        await self._apply_enabled(rule_id, enabled)
        return {"id": rule_id, **rule}

    @staticmethod
    def _own(data: dict[str, Any], rule_id: Any) -> dict[str, Any]:
        if not isinstance(rule_id, str) or not _ID.fullmatch(rule_id):
            raise Invalid("id: not Hauflo's")
        if rule_id not in data["rules"]:
            raise KeyError(rule_id)
        return data["rules"][rule_id]

    async def delete(self, rule_id: Any) -> None:
        async with self.lock:
            data = await self._load()
            self._own(data, rule_id)
            await self._put(rule_id, None)
            del data["rules"][rule_id]
            await self.store.async_save(data)

    async def run(self, rule_id: Any) -> dict[str, Any]:
        """«Проверить сейчас»: HA runs the rule's own automation once, without the day check."""
        data = await self._load()
        self._own(data, rule_id)
        eid = self.entity_of(rule_id)
        if eid is None or self.hass.states.get(eid) is None:
            raise LookupError("the automation isn't in HA — save the rule again")
        # Not awaited to the end: a wake-up light takes minutes.
        await self.hass.services.async_call("automation", "trigger", {"entity_id": eid, "skip_condition": True}, blocking=False)
        return {"id": rule_id, "entity_id": eid}

    # --- the house's place -------------------------------------------------------------------------------------------
    def location(self) -> dict[str, Any]:
        c = self.hass.config
        lat, lon = round(float(c.latitude), 5), round(float(c.longitude), 5)
        from homeassistant.core_config import ConfigSource  # noqa: PLC0415

        return {
            "latitude": c.latitude,
            "longitude": c.longitude,
            "elevation": c.elevation,
            "set": (lat, lon) not in _UNSET,
            "yaml": c.config_source == ConfigSource.YAML,
            "time_zone": c.time_zone,
        }

    async def set_location(self, body: dict[str, Any]) -> dict[str, Any]:
        from homeassistant.core_config import ConfigSource  # noqa: PLC0415

        if self.hass.config.config_source == ConfigSource.YAML:
            raise PermissionError("the location is set in configuration.yaml")
        if set(body) - {"op", "latitude", "longitude", "elevation"}:
            raise Invalid("only latitude, longitude, elevation")
        lat = _float(body.get("latitude"), -90, 90, "latitude")
        lon = _float(body.get("longitude"), -180, 180, "longitude")
        if (round(lat, 5), round(lon, 5)) in _UNSET:
            raise Invalid("not a place")
        kwargs: dict[str, Any] = {"latitude": round(lat, 6), "longitude": round(lon, 6)}
        if body.get("elevation") is not None:
            kwargs["elevation"] = int(round(_float(body["elevation"], -500, 9000, "elevation")))
        await self.hass.config.async_update(**kwargs)
        return self.location()

    async def detect(self) -> dict[str, Any]:
        """Where the house is by its internet address — HA's own onboarding helper (the HA box has no GPS). Nothing is
        saved: the person confirms first."""
        from homeassistant.helpers.aiohttp_client import async_get_clientsession  # noqa: PLC0415
        from homeassistant.util.location import async_detect_location_info  # noqa: PLC0415

        info = await async_detect_location_info(async_get_clientsession(self.hass))
        if info is None or info.latitude is None or info.longitude is None:
            raise LookupError("the location couldn't be found by the internet address")
        return {
            "latitude": info.latitude,
            "longitude": info.longitude,
            "city": info.city,
            "region": getattr(info, "region_name", None),
            "country": info.country_code,
            "time_zone": info.time_zone,
        }

    def days(self, sun: bool = True) -> list[dict[str, Any]]:
        """Today and the next 7 days in HA's time zone with their sunrise and sunset (none while the house's place isn't
        known) — the dashboard says when a rule runs."""
        import astral  # noqa: PLC0415
        import astral.sun  # noqa: PLC0415

        tz = dt_util.get_time_zone(self.hass.config.time_zone) or dt_util.DEFAULT_TIME_ZONE
        obs = astral.Observer(self.hass.config.latitude, self.hass.config.longitude, self.hass.config.elevation)
        today = dt_util.now(tz).date()
        out = []
        for n in range(8):
            day = today + dt.timedelta(days=n)
            row: dict[str, Any] = {"date": day.isoformat(), "wd": DAYS[day.weekday()], "midnight": dt.datetime.combine(day, dt.time(), tz).isoformat()}
            for event in ("sunrise", "sunset"):
                if not sun:
                    row[event] = None
                    continue
                try:
                    row[event] = getattr(astral.sun, event)(obs, date=day, tzinfo=tz).isoformat()
                except ValueError:  # polar day / night: no such event that day
                    row[event] = None
            out.append(row)
        return out

    async def answer(self) -> dict[str, Any]:
        data = await self._load()
        rules = []
        for rule_id, rule in data["rules"].items():
            eid = self.entity_of(rule_id)
            s = self.hass.states.get(eid) if eid else None
            rules.append({"id": rule_id, **{k: v for k, v in rule.items() if k != "by"}, "entity_id": eid, "state": s.state if s else "missing"})
        rules.sort(key=lambda r: r.get("at") or 0)
        return {"location": self.location(), "days": self.days(self.location()["set"]), "rules": rules, "targets": self.targets(), "max_offset": MAX_OFFSET}


class AutomationsView(HomeAssistantView):
    url = "/api/hauflo/automations"
    name = "api:hauflo:automations"
    requires_auth = True

    def __init__(self, auto_of: Callable[[], Automations | None]) -> None:
        self.auto_of = auto_of

    async def get(self, request):
        engine = self.auto_of()
        if engine is None:
            return self.json_message("Hauflo agent not set up", 503)
        return self.json(await engine.answer())

    async def post(self, request):
        engine = self.auto_of()
        if engine is None:
            return self.json_message("Hauflo agent not set up", 503)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict):
            return self.json_message("invalid JSON", 400)
        # A person's own action, as HA's own buttons are; the tunnel can't reach this view (not in its allowlist).
        user = request["hass_user"]
        op = body.get("op")
        try:
            if op == "save":
                return self.json(await engine.save(body.get("rule"), user.name if user else None))
            if op == "enable":
                return self.json(await engine.set_enabled(body.get("id"), body.get("enabled")))
            if op == "delete":
                await engine.delete(body.get("id"))
                return self.json({"deleted": body.get("id")})
            if op == "run":
                return self.json(await engine.run(body.get("id")))
            if op == "location":
                return self.json(await engine.set_location(body))
            if op == "detect":
                return self.json(await engine.detect())
        except Invalid as err:
            return self.json_message(str(err), 400)
        except KeyError:
            return self.json_message("no such rule", 404)
        except LookupError as err:
            return self.json_message(str(err), 409)
        except PermissionError as err:
            return self.json_message(str(err), 409)
        except Exception as err:  # noqa: BLE001 — HA refused the automation or the file: said, not a 500
            _LOGGER.warning("Hauflo sun: %s failed: %s", op, err)
            return self.json_message(f"Home Assistant: {err}", 500)
        return self.json_message("op: save, enable, delete, run, detect or location", 400)
