"""The family's own room scenes from the dashboard (owner 28.10: «сцены штор, как и сцены света, должны создаваться и
редактироваться мной и клиентом, даже если в KNX они не сделаны — тогда просто делать их в HA»).

On a room's light / blinds scene row: «+» → a name → «Запомнить как сейчас»: what the room's lights (or blinds) do right
now becomes a real HA scene in scenes.yaml (it works without Hauflo or the internet), id `hauflo_fam_<room>_<l|c>_<stamp>`.
A long press on a scene → rename / remember again / delete. A scene the integrator made in the app (Hauflo's
`hauflo_<room>_…`) may be renamed and remembered again too — the next deploy keeps that (it rewrites a scene only when the
integrator changed it in the app); «delete» only hides it on this dashboard (the integrator's), for the whole house.
Another scene (the integrator's own, HA's) is never touched.

GET/POST /api/hauflo/scenes — any signed-in person of this HA (the client isn't an admin), as titles and «Автоматизации»;
not through the tunnel (not in its list of the agent's views; the tunnel can't write a `hauflo_fam_` scene either). What a
scene may hold: Hauflo's own lights and blinds and the other integrations' ones the integrator listed for its scenes
(foreign.member_refusal, 399-6) — owns() leaves out locks, the alarm, valves, garage doors, gates.

scenes.yaml: like automations.yaml, written only while it is plain (what HA's own scene editor writes), with a copy
scenes.yaml.hauflo-bak-<time> before each write (the last 3), one read-modify-write at a time.

Adaptive scenes (п.60, owner 05.11): a tap on a Hauflo HA scene runs it through `apply` — while it is light outside
(daylight.py) the lights the family marked «Днём не включать в сценах» (`daylight_skip`) are left out where the scene
would switch them ON (a niche lit by day is not seen); what the scene switches off still goes off. Nothing left out —
HA's own scene.turn_on. Any other scene (KNX, made in HA by hand) — scene.turn_on as it is. Running a scene is open to
every signed-in person (a restricted profile too, as HA's scene.turn_on); marking a light is a setting (not a restricted
profile), the sensor and the threshold (`daylight_config`) are an HA admin's.

KNX scenes (Д19, owner 28.10: «сцены KNX тоже должны пересохраняться»): a long press on a KNX scene Hauflo made →
«Переименовать» (a name on the panel only, kept here — HA, voice and the switches keep theirs) and «Запомнить как
сейчас» = KNX scene learn: one telegram, DPT 18.001 with the learn bit, 0x80 | (number − 1), to that scene's address.
The browser names the scene entity only; the address and the number come from the KNX integration's own store via
the agent (`KnxScenes.target`: Hauflo's own KNX scene, its address allowed by the owner, only scenes listen there) —
no address or value from outside ever reaches the bus here. Refused while the owner switched «Allow Hauflo to change
this home's setup» off; one per scene per few seconds, a few per minute for the house. No delete: a KNX scene lives in
the actuators.
"""

from __future__ import annotations

from http import HTTPStatus

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
import re
import secrets
import time
import unicodedata
from typing import Any

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import derived
from .daylight import Daylight, Invalid as DayInvalid, SensorRefused
from .house import refuse
from .ownership import entity_room
from .rules import said
from .storage import HaufloStore
from .const import LOCKED_CODE, wants_ru
from .tuning import TUNING
from . import yamlfile

_LOGGER = logging.getLogger(__name__)
STORE_KEY = "hauflo.scenes"
SCENES = "scenes.yaml"
BACKUP = SCENES + ".hauflo-bak-"
KEEP_BACKUPS = 3
MAX_NAME = 40
MAX_FAMILY = 60
MAX_ENTITIES = 60
_ROOM = re.compile(r"[a-z0-9]{1,12}")
_FAMILY = re.compile(r"hauflo_fam_([a-z0-9]{1,12})_([lc])_[a-z0-9]{6,16}")
# The integrator's scenes a deploy wrote (roomLighting.ts): hauflo_<room>_<key> / hauflo_<room>_cv_<key>.
_HAUFLO = re.compile(r"(?:hauflo|easyha)_(?!fam_)[a-z0-9]{1,12}_(?:cv_)?[a-z0-9]{1,16}")
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f  ]+")
_FILE_LOCK = asyncio.Lock()
_T = TUNING["scenes"]
# KNX scene learn: one per scene per LEARN_EVERY seconds, LEARN_PER_MINUTE for the whole house.
LEARN_EVERY = _T["learn_every_s"]
LEARN_PER_MINUTE = _T["learn_per_minute"]
MAX_NAMES = 200
_clock = time.monotonic  # the tests move it


class Invalid(ValueError):
    pass


class Refused(Invalid, derived.Refused):
    """A refusal a person can meet (not a request sent wrong): said in the page's language — translations scenes_<code>."""

    def __init__(self, code: str, status: int = HTTPStatus.BAD_REQUEST, **ph: Any) -> None:
        derived.Refused.__init__(self, code, f"scenes_{code}", status, **ph)


class Locked(RuntimeError):
    RU = "Владелец выключил «Разрешить Hauflo менять настройки этого объекта» (Hauflo → Настроить) — сцену KNX сейчас не запомнить."
    EN = "The owner switched off “Allow Hauflo to change this site's setup” (Hauflo → Configure) — the KNX scene can't be saved now."


class TooSoon(RuntimeError):
    """The same scene again within seconds — or (`house`) the house-wide limit a minute (tester 29.10: that one said
    «только что отправлено» though nothing of this scene had been)."""

    RU = "Только что отправлено. Подождите несколько секунд."
    EN = "Just sent. Wait a few seconds."

    def __init__(self, house: bool = False) -> None:
        super().__init__("house" if house else "scene")
        self.house = house

    def text(self, ru: bool) -> str:
        if self.house:
            n = LEARN_PER_MINUTE
            return f"Не больше {n} сцен в минуту — подождите минуту." if ru else f"No more than {n} scenes a minute — wait a minute."
        return self.RU if ru else self.EN


@dataclass
class KnxScenes:
    """What the agent knows of Hauflo's own KNX scenes (__init__.py: from the KNX store, the owner's ranges, the lock)."""

    numbers: Callable[[], dict[str, int]]  # entity id → scene number, Hauflo's own KNX scenes
    target: Callable[[str], tuple[str, int]]  # entity id → (address, number); PermissionError if it may not be sent
    locked: Callable[[], bool]


class NotPlain(RuntimeError):
    RU = "Hauflo не меняет scenes.yaml: в нём комментарии, !include или ручное оформление. Сцену можно сохранить в редакторе сцен Home Assistant."
    EN = "Hauflo doesn't change scenes.yaml: it has comments, !include or hand formatting. Save the scene in Home Assistant's scene editor."


def clean_name(value: Any) -> str:
    if not isinstance(value, str):
        raise Refused("bad_name")
    raw = "".join(ch for ch in value if unicodedata.category(ch) != "Cf")
    text = " ".join(_CONTROL.sub(" ", raw).split())[:MAX_NAME].strip()
    if not any(unicodedata.category(ch)[0] in "LN" for ch in text):
        raise Refused("bad_name")
    return text


def _plain(text: str) -> list[Any]:
    return yamlfile.parse(text, NotPlain)


class FamilyScenes:
    def __init__(self, hass: HomeAssistant, owns: Callable[[str], bool], knx: KnxScenes | None = None, daylight: Callable[[], Daylight | None] | None = None) -> None:
        self.hass = hass
        self.owns = owns
        self.knx = knx
        self.daylight = daylight or (lambda: None)
        self.store = HaufloStore(hass, STORE_KEY)
        self._learned: dict[str, float] = {}
        self._learn_times: list[float] = []

    # --- what a scene may hold ----------------------------------------------------------------------------------------
    def _capturable(self, eid: Any, kind: str) -> bool:
        domain = "light" if kind == "l" else "cover"
        if not isinstance(eid, str) or eid.split(".")[0] != domain or self.hass.states.get(eid) is None:
            return False
        ent = er.async_get(self.hass).async_get(eid)
        if ent is None or ent.disabled_by:
            return False
        return bool(self.owns(eid))

    def capture(self, entities: Any, kind: str) -> dict[str, dict[str, Any]]:
        """What the room's lights / blinds do now, in HA's own scene format. A lamp / blind that says nothing (unknown,
        unavailable) is left out — a scene never «remembers» a guess."""
        if not isinstance(entities, list) or not entities or len(entities) > MAX_ENTITIES:
            raise Invalid("entities")
        out: dict[str, dict[str, Any]] = {}
        for eid in entities:
            if not self._capturable(eid, kind):
                continue
            s = self.hass.states.get(eid)
            if s.state in ("unknown", "unavailable"):
                continue
            a = s.attributes
            if kind == "l":
                e: dict[str, Any] = {"state": "on" if s.state == "on" else "off"}
                if s.state == "on" and isinstance(a.get("brightness"), (int, float)):
                    e["brightness"] = int(a["brightness"])
                if s.state == "on" and isinstance(a.get("color_temp_kelvin"), (int, float)) and a.get("color_mode") == "color_temp":
                    e["color_temp_kelvin"] = int(a["color_temp_kelvin"])
            else:
                if s.state in ("opening", "closing"):
                    continue
                features = int(a.get("supported_features") or 0)
                e = {"state": "closed" if s.state == "closed" else "open"}
                if features & 4 and isinstance(a.get("current_position"), (int, float)):
                    e["current_position"] = int(a["current_position"])
                if features & 128 and isinstance(a.get("current_tilt_position"), (int, float)):
                    e["current_tilt_position"] = int(a["current_tilt_position"])
            out[eid] = e
        if not out:
            raise LookupError("nothing to remember: no light / blind of this room says its state")
        return out

    # --- scenes.yaml --------------------------------------------------------------------------------------------------
    async def _read(self) -> tuple[list[dict[str, Any]], str | None]:
        return await yamlfile.async_read(self.hass, self.hass.config.path(SCENES), NotPlain)

    async def _write(self, data: list[dict[str, Any]], before: str | None, redo: Callable[[list[Any]], list[Any]] | None = None) -> list[Any]:
        """Atomic, the replaced text kept as a backup copy; changed since ``before`` (HA's scene editor saved
        meanwhile) — ``redo`` makes the change again on it (yamlfile.write)."""
        return await yamlfile.async_write(self.hass, self.hass.config.path(SCENES), self.hass.config.path(BACKUP), KEEP_BACKUPS, data, before, NotPlain, redo)

    async def _edit(self, scene_id: str, change: Callable[[dict[str, Any] | None], dict[str, Any] | None]) -> dict[str, Any] | None:
        """One read-modify-write of one of Hauflo's scenes; `change` gets the current config (None — none yet) and
        returns the new one (None — remove)."""
        if not (_FAMILY.fullmatch(scene_id) or _HAUFLO.fullmatch(scene_id)):
            raise Invalid("id: not Hauflo's")
        async with _FILE_LOCK:
            data, before = await self._read()
            now = next((s for s in data if isinstance(s, dict) and s.get("id") == scene_id), None)
            new = change(dict(now) if now is not None else None)

            def put(rows: list[Any]) -> list[Any]:
                there = next((s for s in rows if isinstance(s, dict) and s.get("id") == scene_id), None)
                kept = [s for s in rows if not (isinstance(s, dict) and s.get("id") == scene_id)]
                if new is not None:
                    # In its place (the scene editor's list order), a new one at the end.
                    at = rows.index(there) if there is not None else len(kept)
                    kept.insert(min(at, len(kept)), new)
                return kept

            await self._write(put(data), before, put)
        await self.hass.services.async_call("scene", "reload", {}, blocking=True)
        if new is None and (eid := self.entity_of(scene_id)):
            er.async_get(self.hass).async_remove(eid)
        return new

    def entity_of(self, scene_id: str) -> str | None:
        return er.async_get(self.hass).async_get_entity_id("scene", "homeassistant", scene_id)

    async def _data(self) -> dict[str, Any]:
        data = await self.store.async_load()
        return data if isinstance(data, dict) else {}

    # --- what the dashboard asks --------------------------------------------------------------------------------------
    async def answer(self) -> dict[str, Any]:
        data = await self._data()
        out: dict[str, Any] = {"hidden": [x for x in data.get("hidden", []) if isinstance(x, str)], "max_name": MAX_NAME}
        if self.knx is not None:
            # The cards: which scenes are KNX ones that can be learnt (and their numbers — «в сцене N»), the family's names.
            numbers = self.knx.numbers()
            out["knx"] = numbers
            names = data.get("names") if isinstance(data.get("names"), dict) else {}
            out["names"] = {k: v for k, v in names.items() if k in numbers and isinstance(v, str)}
        if (day := self.daylight()) is not None:
            out["daylight"] = await day.answer()
        return out

    # --- adaptive scenes (daylight.py) ---------------------------------------------------------------------------------
    async def _entities_in(self, scene_id: str) -> dict[str, Any] | None:
        """What a Hauflo scene holds, from scenes.yaml — read only, so a hand-formatted file is read too; None when it
        can't be read (the scene then runs as HA's own)."""
        import yaml  # noqa: PLC0415

        path = self.hass.config.path(SCENES)

        def read() -> Any:
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f)

        try:
            data = await self.hass.async_add_executor_job(read)
        except (OSError, yaml.YAMLError):
            return None
        mine = next((x for x in data if isinstance(x, dict) and x.get("id") == scene_id), None) if isinstance(data, list) else None
        ents = mine.get("entities") if isinstance(mine, dict) else None
        return ents if isinstance(ents, dict) else None

    async def apply(self, entity_id: Any) -> dict[str, Any]:
        """A tap on a scene: a Hauflo HA scene without the lights left off by day (only while it is light outside and
        only where the scene would switch them on); any other — HA's scene.turn_on."""
        if not isinstance(entity_id, str) or not entity_id.startswith("scene.") or self.hass.states.get(entity_id) is None:
            raise Invalid("entity_id")
        try:
            scene_id: str | None = self._scene_id_of(entity_id)
        except PermissionError:
            scene_id = None  # a KNX scene, one made in HA by hand: HA's own
        day = self.daylight()
        ents = await self._entities_in(scene_id) if scene_id and day is not None and day.is_light() else None
        skip = set((await day.config())["skip"]) if ents and day is not None else set()
        # «on» written by hand (no quotes) is YAML's true.
        dropped = sorted(e for e, v in (ents or {}).items() if e in skip and (v.get("state") if isinstance(v, dict) else v) in ("on", True))
        if not dropped:
            await self.hass.services.async_call("scene", "turn_on", {"entity_id": entity_id}, blocking=True)
            return {"applied": len(ents) if ents else None, "skipped": []}
        rest = {e: v for e, v in ents.items() if e not in dropped}
        if rest:
            await self.hass.services.async_call("scene", "apply", {"entities": rest}, blocking=True)
        _LOGGER.debug("Hauflo: scene %s by day, left off: %s", entity_id, dropped)
        return {"applied": len(rest), "skipped": dropped}

    async def daylight_skip(self, entity_id: Any, on: Any) -> dict[str, Any]:
        if (day := self.daylight()) is None:
            raise PermissionError("daylight: not set up")
        return {"skip": await day.set_skip(entity_id, on)}

    async def daylight_config(self, body: dict[str, Any]) -> dict[str, Any]:
        if (day := self.daylight()) is None:
            raise PermissionError("daylight: not set up")
        if set(body) - {"op", "lang", "sensor", "lux"}:
            raise DayInvalid("daylight_config: sensor and / or lux only")
        await day.set_config({k: body[k] for k in ("sensor", "lux") if k in body})
        return await day.answer()

    # --- KNX scenes ---------------------------------------------------------------------------------------------------
    def _knx_scene(self, entity_id: Any) -> str:
        if self.knx is None:
            raise PermissionError("KNX scenes: not available")
        if not isinstance(entity_id, str) or entity_id not in self.knx.numbers():
            raise PermissionError("not a KNX scene of Hauflo's")
        return entity_id

    async def name(self, entity_id: Any, name: Any) -> dict[str, Any]:
        """A KNX scene's name on the panel (the family's, for the whole house); empty — the integrator's again."""
        eid = self._knx_scene(entity_id)
        title = "" if isinstance(name, str) and not name.strip() else clean_name(name)
        data = await self._data()
        old = data.get("names") if isinstance(data.get("names"), dict) else {}
        names = {k: v for k, v in old.items() if isinstance(k, str) and isinstance(v, str) and k != eid}
        if title:
            names[eid] = title
        await self.store.async_save({**data, "names": dict(list(names.items())[-MAX_NAMES:])})
        return {"entity_id": eid, "name": title}

    async def learn(self, entity_id: Any, user: str | None) -> dict[str, Any]:
        """«Запомнить как сейчас» on a KNX scene: the actuators on its address store what they do now as that scene
        (if their ETS parameters allow it — nothing on the bus says whether they did)."""
        if self.knx is None:
            raise PermissionError("KNX scenes: not available")
        if self.knx.locked():
            raise Locked
        address, number = self.knx.target(entity_id)  # PermissionError: not this scene / not allowed
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 64:
            raise PermissionError("scene number")
        # No await between the check and the stamps: two presses at once can't both pass.
        now = _clock()
        self._learn_times = [t for t in self._learn_times if now - t < 60]
        if now - self._learned.get(entity_id, -1e9) < LEARN_EVERY:
            raise TooSoon
        if len(self._learn_times) >= LEARN_PER_MINUTE:
            raise TooSoon(house=True)
        self._learned[entity_id] = now
        self._learn_times.append(now)
        payload = 0x80 | (number - 1)  # DPT 18.001: bit 7 = learn, bits 0–5 = scene number − 1
        await self.hass.services.async_call("knx", "send", {"address": address, "payload": [payload]}, blocking=True)
        _LOGGER.info("Hauflo: KNX scene %s (%s, №%s) learnt by %s", entity_id, address, number, user or "?")
        return {"sent": True, "entity_id": entity_id, "scene_number": number}

    async def add(self, room: Any, kind: Any, name: Any, entities: Any, user: str | None) -> dict[str, Any]:
        if not isinstance(room, str) or not _ROOM.fullmatch(room) or kind not in ("l", "c"):
            raise Invalid("room / kind")
        title = clean_name(name)
        ents = self.capture(entities, kind)
        reg = er.async_get(self.hass)
        mine = [e for e in reg.entities.values() if e.domain == "scene" and e.platform == "homeassistant" and str(e.unique_id).startswith("hauflo_fam_")]
        if len(mine) >= MAX_FAMILY:
            raise Refused("too_many", n=MAX_FAMILY)
        # Time first: the cards list a room's scenes in the order they were made.
        scene_id = f"hauflo_fam_{room}_{kind}_{int(time.time()):x}{secrets.token_hex(2)}"
        icon = "mdi:lightbulb-on-outline" if kind == "l" else "mdi:blinds-horizontal"
        await self._edit(scene_id, lambda _now: {"id": scene_id, "name": title, "icon": icon, "entities": ents})
        eid = await self._wait_entity(scene_id)
        # In the room's area (as the lights it remembers): HA's own lists show it where it belongs.
        if eid:
            area = self._area_of(list(ents))
            if area:
                reg.async_update_entity(eid, area_id=area)
        _LOGGER.info("Hauflo: scene «%s» made by %s", title, user or "?")
        return {"id": scene_id, "entity_id": eid, "name": title}

    async def _wait_entity(self, scene_id: str) -> str | None:
        for _ in range(20):
            if eid := self.entity_of(scene_id):
                return eid
            await asyncio.sleep(0.1)
        return None

    def _area_of(self, entities: list[str]) -> str | None:
        areas = {entity_room(self.hass, eid) for eid in entities}  # Р57: zones of one room → the room
        return next(iter(areas)) if len(areas) == 1 else None

    def _scene_id_of(self, entity_id: Any) -> str:
        if not isinstance(entity_id, str) or not entity_id.startswith("scene."):
            raise Invalid("entity_id")
        ent = er.async_get(self.hass).async_get(entity_id)
        if ent is None and self.hass.states.get(entity_id) is None:
            raise Refused("no_scene", HTTPStatus.NOT_FOUND)  # deleted meanwhile (another phone): «refresh», not «not Hauflo's»
        uid = str(ent.unique_id) if ent and ent.platform == "homeassistant" else ""
        if not (_FAMILY.fullmatch(uid) or _HAUFLO.fullmatch(uid)):
            raise PermissionError("not a scene of Hauflo's")
        return uid

    async def rename(self, entity_id: Any, name: Any) -> dict[str, Any]:
        scene_id = self._scene_id_of(entity_id)
        title = clean_name(name)

        def change(now: dict[str, Any] | None) -> dict[str, Any]:
            if now is None:
                raise KeyError(scene_id)
            return {**now, "name": title}

        await self._edit(scene_id, change)
        return {"id": scene_id, "name": title}

    async def recapture(self, entity_id: Any, entities: Any) -> dict[str, Any]:
        scene_id = self._scene_id_of(entity_id)
        kind = "c" if "_cv_" in scene_id or (_FAMILY.fullmatch(scene_id) and _FAMILY.fullmatch(scene_id).group(2) == "c") else "l"
        ents = self.capture(entities, kind)

        def change(now: dict[str, Any] | None) -> dict[str, Any]:
            if now is None:
                raise KeyError(scene_id)
            return {**now, "entities": ents}

        await self._edit(scene_id, change)
        return {"id": scene_id, "entities": len(ents)}

    async def delete(self, entity_id: Any) -> dict[str, Any]:
        """The family's own: gone from scenes.yaml. The integrator's: hidden on this dashboard (a deploy would make it
        again — it is his, set in the app)."""
        scene_id = self._scene_id_of(entity_id)
        if _FAMILY.fullmatch(scene_id):
            await self._edit(scene_id, lambda _now: None)
            return {"deleted": scene_id}
        data = await self._data()
        hidden = [x for x in data.get("hidden", []) if isinstance(x, str) and x != entity_id] + [entity_id]
        await self.store.async_save({**data, "hidden": hidden[-500:]})
        return {"hidden": entity_id}

    async def unhide(self, entity_id: Any) -> dict[str, Any]:
        data = await self._data()
        await self.store.async_save({**data, "hidden": [x for x in data.get("hidden", []) if x != entity_id]})
        return {"shown": entity_id}


class ScenesView(HomeAssistantView):
    url = "/api/hauflo/scenes"
    name = "api:hauflo:scenes"
    requires_auth = True

    def __init__(self, scenes_of: Callable[[], FamilyScenes | None]) -> None:
        self.scenes_of = scenes_of

    async def get(self, request):
        engine = self.scenes_of()
        if engine is None:
            return self.json_message("Hauflo agent not set up", 503)
        return self.json(await engine.answer())

    async def post(self, request):
        engine = self.scenes_of()
        if engine is None:
            return self.json_message("Hauflo agent not set up", 503)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict):
            return self.json_message("invalid JSON", 400)
        user = request["hass_user"]
        ru = wants_ru(body, engine.hass.config.language)
        op = body.get("op")
        # Every op here changes the house's scenes (create, rename, re-learn on the KNX actuators, delete): not a restricted
        # (child) profile (house.py, family review round 3). Running a scene is HA's own scene.turn_on — open to everyone.
        if op in ("add", "rename", "recapture", "delete", "unhide", "knx_name", "knx_learn", "daylight_skip") and (no := await refuse(request)) is not None:
            return no
        if op == "daylight_config" and not (user and user.is_admin):
            return self.json_message("only an administrator sets the daylight sensor", 403)
        try:
            if op == "apply":
                return self.json(await engine.apply(body.get("entity_id")))
            if op == "daylight_skip":
                return self.json(await engine.daylight_skip(body.get("entity_id"), body.get("on")))
            if op == "daylight_config":
                return self.json(await engine.daylight_config(body))
            if op == "add":
                return self.json(await engine.add(body.get("room"), body.get("kind"), body.get("name"), body.get("entities"), user.name if user else None))
            if op == "rename":
                return self.json(await engine.rename(body.get("entity_id"), body.get("name")))
            if op == "recapture":
                return self.json(await engine.recapture(body.get("entity_id"), body.get("entities")))
            if op == "delete":
                return self.json(await engine.delete(body.get("entity_id")))
            if op == "unhide":
                return self.json(await engine.unhide(body.get("entity_id")))
            if op == "knx_name":
                return self.json(await engine.name(body.get("entity_id"), body.get("name")))
            if op == "knx_learn":
                # The scene entity only: anything else in the body (an address, a payload) is never read.
                return self.json(await engine.learn(body.get("entity_id"), user.name if user else None))
        except SensorRefused as err:
            return self.json({"message": await err.text(engine.hass, ru), "code": err.key}, 400)
        except derived.Refused as err:
            return self.json(await said(engine.hass, err, ru), err.status)
        except (Invalid, DayInvalid) as err:
            return self.json_message(str(err), 400)
        except NotPlain as err:
            _LOGGER.warning("Hauflo scenes: scenes.yaml left as it is (%s)", err)
            return self.json({"message": NotPlain.RU if ru else NotPlain.EN, "code": "not_plain"}, 409)
        except Locked:
            return self.json({"message": Locked.RU if ru else Locked.EN, "code": LOCKED_CODE}, 423)
        except TooSoon as err:
            return self.json({"message": err.text(ru), "code": "too_soon"}, 429)
        except PermissionError as err:
            return self.json_message(str(err), 403)
        except KeyError:
            return self.json(await said(engine.hass, Refused("no_scene", HTTPStatus.NOT_FOUND), ru), 404)
        except LookupError as err:
            msg = "Нечего запомнить: свет и шторы комнаты не сообщают своё состояние." if ru else "Nothing to remember: the room's lights and blinds don't report their state."
            return self.json({"message": msg, "code": "nothing"}, 409)
        except Exception as err:  # noqa: BLE001 — HA refused the file / reload: said, not a 500 page
            _LOGGER.warning("Hauflo scenes: %s failed: %s", op, err)
            return self.json_message(f"Home Assistant: {err}", 500)
        return self.json_message("op: apply, add, rename, recapture, delete, unhide, knx_name, knx_learn, daylight_skip or daylight_config", 400)
