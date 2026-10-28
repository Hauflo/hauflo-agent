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
the tunnel. The house's place (op location / detect) only an HA administrator: it moves HA's zone.home, which presence
and every sun rule of the house use (security review 28.10).

automations.yaml (security review 28.10: re-dumping it lost the integrator's comments and !include). HA reads automations
only from configuration.yaml's `automation:` keys — no file of Hauflo's own without editing configuration.yaml, which
is riskier still. So the agent writes automations.yaml only while it is plain — what HA's own automation editor writes:
it loads and dumps back to the same text (no comments, no !include / !secret / !input). Anything else is refused with
a message (the integrator keeps the file by hand; nothing is touched). Before every write a copy
automations.yaml.hauflo-bak-<time> (the last 3 kept); read-modify-write under one lock for the whole house.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import datetime as dt
import hashlib
import json
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
NO_LOCATION_RU = "Место дома не задано — рассвет и закат посчитаются неправильно. Укажите, где дом."
NO_LOCATION_EN = "The home's location isn't set — sunrise and sunset will be wrong. Set where the house is."
# What a rule may do, by domain: the service and what its value is.
_DO = {
    "cover": {"open": "cover.open_cover", "close": "cover.close_cover", "position": "cover.set_cover_position"},
    # «ramp»: a wake-up light, brightness up in steps over minutes (KNX lights in HA have no transition).
    "light": {"on": "light.turn_on", "off": "light.turn_off", "ramp": "light.turn_on"},
    "scene": {"on": "scene.turn_on"},
}


BACKUP = AUTOMATIONS + ".hauflo-bak-"
KEEP_BACKUPS = 3
# One read-modify-write of automations.yaml at a time, whichever config entry asks.
_FILE_LOCK = asyncio.Lock()


class Invalid(ValueError):
    """A request the agent refuses (400)."""


class NotPlain(RuntimeError):
    """automations.yaml carries what a re-dump would lose (409, said in both languages)."""

    RU = ("Hauflo не меняет automations.yaml: в нём комментарии, !include / !secret или ручное оформление, которые "
          "потерялись бы при перезаписи. Правила Hauflo в этом доме добавляет интегратор, или он приводит файл к виду, "
          "в каком его сохраняет редактор автоматизаций Home Assistant.")
    EN = ("Hauflo doesn't change automations.yaml: it has comments, !include / !secret or hand formatting a rewrite would "
          "lose. In this home the integrator adds Hauflo's rules, or saves the file the way Home Assistant's automation "
          "editor writes it.")


class Changed(Exception):
    """The automation was edited in HA's own editor: Hauflo never writes over it unasked (409)."""


def _digest(cfg: Any) -> str:
    """What an automation does — not its description or initial_state (written by older agents)."""
    if not isinstance(cfg, dict):
        return ""
    body = {k: v for k, v in cfg.items() if k not in ("description", "initial_state")}
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


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

    def _room_covers(self, area_id: str, position: bool = False) -> list[str]:
        """The Hauflo blinds of a room, one by one (not its group — that would move them twice); with `position`, only
        the ones that can stop at a position."""
        reg = er.async_get(self.hass)
        out = []
        for eid in self.hass.states.async_entity_ids("cover"):
            if not self._pickable(eid) or self._area(eid) != area_id or ((e := reg.async_get(eid)) is not None and e.platform == "group"):
                continue
            if position and not int(self.hass.states.get(eid).attributes.get("supported_features") or 0) & 4:
                continue
            out.append(eid)
        return sorted(out)

    def _area_target(self, domain: str, area_id: str, explicit: list[str]) -> dict[str, Any]:
        """The room itself when every one of its lights / blinds is Hauflo's (a lamp added later follows — HA expert
        28.10); else the list — never the integrator's own or a garage door by way of the room."""
        everything = [eid for eid in self.hass.states.async_entity_ids(domain) if self._area(eid) == area_id]
        if everything and all(self._pickable(eid) for eid in everything):
            return {"area_id": area_id}
        return {"entity_id": explicit}

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
                    item["group"] = (e := er.async_get(self.hass).async_get(eid)) is not None and e.platform == "group"
                if domain == "light":
                    modes = s.attributes.get("supported_color_modes") or []
                    item["dim"] = any(m != "onoff" for m in modes)
                out[domain].append(item)
        for area_id in {i["area"] for i in out["light"] if i["area"]}:
            if n := len(self._room_lights(area_id)):
                rooms[area_id] = n
        names = {a: (area.name if (area := areas.async_get_area(a)) else a) for a in {i["area"] for v in out.values() for i in v if i["area"]}}
        cover_rooms = []
        for area_id in {i["area"] for i in out["cover"] if i["area"]}:
            if n := len(self._room_covers(area_id)):
                cover_rooms.append({"area_id": area_id, "name": names.get(area_id, area_id), "covers": n, "position": bool(self._room_covers(area_id, True))})
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
            "cover_rooms": sorted(cover_rooms, key=lambda r: r["name"]),
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
            if do == "room_covers":
                # «Все шторы спальни» as one action (family review 28.10: twenty «Штора 1, Штора 2, Штора 1…»).
                area_id, cmd = a.get("area_id"), a.get("cover")
                if cmd not in _DO["cover"]:
                    raise Invalid("room_covers: cover open, close or position")
                if not isinstance(area_id, str) or not self._room_covers(area_id, cmd == "position"):
                    raise Invalid("room_covers: a room with Hauflo blinds")
                item = {"do": "room_covers", "area_id": area_id, "cover": cmd}
                if cmd == "position":
                    item["value"] = _int(a.get("value"), 0, 100, "position %")
                clean.append(item)
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
                    out.append({"action": "light.turn_off", "target": self._area_target("light", a["area_id"], lights)})
                continue
            if a["do"] == "room_covers":
                if covers := self._room_covers(a["area_id"], a["cover"] == "position"):
                    call = {"action": _DO["cover"][a["cover"]], "target": self._area_target("cover", a["area_id"], covers)}
                    if a["cover"] == "position":
                        call["data"] = {"position": a["value"]}
                    out.append(call)
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
            # Neutral: it keeps working after Hauflo is removed (HA expert 28.10), the page it named may be gone.
            "description": "Создано в Hauflo." if ru else "Made in Hauflo.",
            "triggers": [trigger],
            "conditions": [{"condition": "time", "weekday": list(rule["days"])}] if len(rule["days"]) < 7 else [],
            "actions": self._service_calls(rule),
            "mode": "single",
            # No initial_state: HA keeps the on / off a person set (in HA or here) over restarts (HA expert 28.10).
        }
        return cfg

    @staticmethod
    def _plain(text: str) -> list[Any]:
        """automations.yaml as data — only when a dump gives back the same text (HA's editor's own format); else
        NotPlain. An empty file / «[]» is plain."""
        import yaml  # noqa: PLC0415
        from homeassistant.util.yaml import dump  # noqa: PLC0415

        body = text.strip()
        if body in ("", "[]"):
            return []
        try:
            data = yaml.safe_load(text)  # no HA tags: one would fail here, not be followed
        except yaml.YAMLError as err:
            raise NotPlain(f"not YAML: {err}") from err
        if not isinstance(data, list):
            raise NotPlain("not a list")
        if dump(data).strip() != body:
            raise NotPlain("hand formatting")
        return data

    async def _read_file(self) -> tuple[list[dict[str, Any]], str | None]:
        import os  # noqa: PLC0415

        path = self.hass.config.path(AUTOMATIONS)

        def read() -> str | None:
            if not os.path.isfile(path):
                return None
            with open(path, encoding="utf-8") as f:
                return f.read()

        text = await self.hass.async_add_executor_job(read)
        return ([], None) if text is None else (self._plain(text), text)

    async def _write_file(self, data: list[dict[str, Any]], before: str | None) -> None:
        import os  # noqa: PLC0415

        from homeassistant.util.file import write_utf8_file_atomic  # noqa: PLC0415
        from homeassistant.util.yaml import dump  # noqa: PLC0415

        path = self.hass.config.path(AUTOMATIONS)
        contents = dump(data)  # before the file is opened: a dump error never truncates it
        self._plain(contents)  # what is written reads back the same (never a file the next write would refuse)

        def write() -> None:
            if before is not None:
                stamp = dt_util.utcnow().strftime("%Y%m%d-%H%M%S-%f")
                write_utf8_file_atomic(self.hass.config.path(BACKUP + stamp), before)
                folder = self.hass.config.path()
                old = sorted(n for n in os.listdir(folder) if n.startswith(BACKUP))
                for name in old[:-KEEP_BACKUPS]:
                    os.remove(os.path.join(folder, name))
            write_utf8_file_atomic(path, contents)

        await self.hass.async_add_executor_job(write)

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
        async with _FILE_LOCK:
            data, before = await self._read_file()
            kept = [a for a in data if not (isinstance(a, dict) and a.get("id") == rule_id)]
            if cfg is not None:
                kept.append(cfg)
            await self._write_file(kept, before)
        if cfg is not None:
            await self.hass.services.async_call("automation", "reload", {"id": rule_id}, blocking=True)
        elif eid := self.entity_of(rule_id):
            er.async_get(self.hass).async_remove(eid)

    async def _load(self) -> dict[str, Any]:
        data = await self.store.async_load()
        return data if isinstance(data, dict) and isinstance(data.get("rules"), dict) else {"rules": {}}

    async def changed(self, data: dict[str, Any]) -> set[str]:
        """Hauflo's rules someone edited in HA's own automation editor: what's in automations.yaml is not what Hauflo
        wrote (its digest; for a rule from before the digest — what Hauflo would write from the form)."""
        try:
            file, _ = await self._read_file()
        except Exception:  # noqa: BLE001 — an unreadable file: nothing said changed, writes refuse on their own
            return set()
        now = {a.get("id"): a for a in file if isinstance(a, dict)}
        out = set()
        for rule_id, rule in data["rules"].items():
            if (cur := now.get(rule_id)) is None:
                continue
            want = rule.get("hash") or _digest(self.automation(rule_id, rule))
            if _digest(cur) != want:
                out.add(rule_id)
        return out

    async def save(self, raw: Any, user: str | None, force: bool = False) -> dict[str, Any]:
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
            # Edited in HA's editor: only «Вернуть как в Hauflo», asked and confirmed, writes over it (HA expert 28.10).
            if rule_id is not None and not force and rule_id in await self.changed(data):
                raise Changed(rule_id)
            rule = self.validate(raw, data["rules"].get(rule_id) if rule_id else None)
            rule_id = rule_id or PREFIX + secrets.token_hex(6)
            rule["by"], rule["at"] = user, time.time()
            cfg = self.automation(rule_id, rule)
            await self._put(rule_id, cfg)
            rule["hash"] = _digest(cfg)
            data["rules"][rule_id] = rule
            await self.store.async_save(data)
        await self._apply_enabled(rule_id, rule["enabled"])
        out = {"id": rule_id, **{k: v for k, v in rule.items() if k != "hash"}}
        # A sunrise / sunset rule while HA doesn't know where the house is: saved, and said (HA expert 28.10).
        if rule["when"].get("event") in ("sunrise", "sunset") and not self.location()["set"]:
            out["warning"] = "no_location"
        return out

    async def _apply_enabled(self, rule_id: str, enabled: bool) -> None:
        # HA keeps the switch over restarts (no initial_state); a reload keeps an unchanged automation's state.
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
            # Only HA's own switch: the file isn't written (an edit made in HA's editor stays — HA expert 28.10).
            await self.store.async_save(data)
        await self._apply_enabled(rule_id, enabled)
        return {"id": rule_id, **{k: v for k, v in rule.items() if k != "hash"}}

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

    async def set_time_zone(self, body: dict[str, Any]) -> dict[str, Any]:
        """The house's time zone (family review 28.10: HA in UTC, a Moscow family saw sunset at 13:58). Only the zone."""
        from homeassistant.core_config import ConfigSource  # noqa: PLC0415

        if self.hass.config.config_source == ConfigSource.YAML:
            raise PermissionError("the time zone is set in configuration.yaml")
        tz = body.get("time_zone")
        if set(body) - {"op", "time_zone"} or not isinstance(tz, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){0,2}", tz) or dt_util.get_time_zone(tz) is None:
            raise Invalid("time_zone: an IANA zone (Europe/Moscow)")
        await self.hass.config.async_update(time_zone=tz)
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
        edited = await self.changed(data)
        rules = []
        for rule_id, rule in data["rules"].items():
            eid = self.entity_of(rule_id)
            s = self.hass.states.get(eid) if eid else None
            # On / off as HA has it (a person may switch it in HA too).
            rules.append({"id": rule_id, **{k: v for k, v in rule.items() if k not in ("by", "hash")}, "entity_id": eid, "state": s.state if s else "missing", "enabled": s.state == "on" if s else rule["enabled"], "changed": rule_id in edited})
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
        ru = str(engine.hass.config.language or "").startswith("ru")
        if op in ("location", "detect") and not (user is not None and user.is_admin):
            # HA's home location moves zone.home (presence, every sun rule): an administrator's (security review 28.10).
            msg = "Место дома задаёт администратор Home Assistant." if ru else "The home's location is set by the Home Assistant administrator."
            return self.json({"message": msg, "code": "admin_only"}, 403)
        try:
            if op == "save":
                saved = await engine.save(body.get("rule"), user.name if user else None, body.get("force") is True)
                if saved.get("warning") == "no_location":
                    saved["warning_text"] = NO_LOCATION_RU if ru else NO_LOCATION_EN
                return self.json(saved)
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
            if op == "timezone":
                # The whole HA's clock: only HA's owner (an admin).
                if not (user and user.is_admin):
                    return self.json_message("admin only", 403)
                return self.json(await engine.set_time_zone(body))
        except Invalid as err:
            return self.json_message(str(err), 400)
        except NotPlain as err:
            _LOGGER.warning("Hauflo automations: automations.yaml left as it is (%s)", err)
            return self.json({"message": NotPlain.RU if ru else NotPlain.EN, "code": "not_plain"}, 409)
        except KeyError:
            return self.json_message("no such rule", 404)
        except Changed:
            return self.json_message("changed in Home Assistant", 409)
        except LookupError as err:
            return self.json_message(str(err), 409)
        except PermissionError as err:
            return self.json_message(str(err), 409)
        except Exception as err:  # noqa: BLE001 — HA refused the automation or the file: said, not a 500
            _LOGGER.warning("Hauflo sun: %s failed: %s", op, err)
            return self.json_message(f"Home Assistant: {err}", 500)
        return self.json_message("op: save, enable, delete, run, detect, location or timezone", 400)
