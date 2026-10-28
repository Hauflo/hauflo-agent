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
scene may hold: Hauflo's own lights and blinds only (owns() leaves out locks, the alarm, valves, garage doors, gates).

scenes.yaml: like automations.yaml, written only while it is plain (what HA's own scene editor writes), with a copy
scenes.yaml.hauflo-bak-<time> before each write (the last 3), one read-modify-write at a time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import re
import secrets
import time
import unicodedata
from typing import Any

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

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


class Invalid(ValueError):
    pass


class NotPlain(RuntimeError):
    RU = "Hauflo не меняет scenes.yaml: в нём комментарии, !include или ручное оформление. Сцену можно сохранить в редакторе сцен Home Assistant."
    EN = "Hauflo doesn't change scenes.yaml: it has comments, !include or hand formatting. Save the scene in Home Assistant's scene editor."


def clean_name(value: Any) -> str:
    if not isinstance(value, str):
        raise Invalid("name")
    raw = "".join(ch for ch in value if unicodedata.category(ch) != "Cf")
    text = " ".join(_CONTROL.sub(" ", raw).split())[:MAX_NAME].strip()
    if not any(unicodedata.category(ch)[0] in "LN" for ch in text):
        raise Invalid("name")
    return text


def _plain(text: str) -> list[Any]:
    import yaml  # noqa: PLC0415
    from homeassistant.util.yaml import dump  # noqa: PLC0415

    body = text.strip()
    if body in ("", "[]"):
        return []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise NotPlain(f"not YAML: {err}") from err
    if not isinstance(data, list):
        raise NotPlain("not a list")
    if dump(data).strip() != body:
        raise NotPlain("hand formatting")
    return data


class FamilyScenes:
    def __init__(self, hass: HomeAssistant, owns: Callable[[str], bool]) -> None:
        self.hass = hass
        self.owns = owns
        self.store = Store(hass, 1, STORE_KEY)

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
        import os  # noqa: PLC0415

        path = self.hass.config.path(SCENES)

        def read() -> str | None:
            if not os.path.isfile(path):
                return None
            with open(path, encoding="utf-8") as f:
                return f.read()

        text = await self.hass.async_add_executor_job(read)
        return ([], None) if text is None else (_plain(text), text)

    async def _write(self, data: list[dict[str, Any]], before: str | None) -> None:
        import os  # noqa: PLC0415

        from homeassistant.util.file import write_utf8_file_atomic  # noqa: PLC0415
        from homeassistant.util.yaml import dump  # noqa: PLC0415

        path = self.hass.config.path(SCENES)
        contents = dump(data)
        _plain(contents)

        def write() -> None:
            if before is not None:
                stamp = dt_util.utcnow().strftime("%Y%m%d-%H%M%S-%f")
                write_utf8_file_atomic(self.hass.config.path(BACKUP + stamp), before)
                folder = self.hass.config.path()
                for name in sorted(n for n in os.listdir(folder) if n.startswith(BACKUP))[:-KEEP_BACKUPS]:
                    os.remove(os.path.join(folder, name))
            write_utf8_file_atomic(path, contents)

        await self.hass.async_add_executor_job(write)

    async def _edit(self, scene_id: str, change: Callable[[dict[str, Any] | None], dict[str, Any] | None]) -> dict[str, Any] | None:
        """One read-modify-write of one of Hauflo's scenes; `change` gets the current config (None — none yet) and
        returns the new one (None — remove)."""
        if not (_FAMILY.fullmatch(scene_id) or _HAUFLO.fullmatch(scene_id)):
            raise Invalid("id: not Hauflo's")
        async with _FILE_LOCK:
            data, before = await self._read()
            now = next((s for s in data if isinstance(s, dict) and s.get("id") == scene_id), None)
            new = change(dict(now) if now is not None else None)
            kept = [s for s in data if not (isinstance(s, dict) and s.get("id") == scene_id)]
            if new is not None:
                # In its place (the scene editor's list order), a new one at the end.
                at = data.index(now) if now is not None else len(kept)
                kept.insert(min(at, len(kept)), new)
            await self._write(kept, before)
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
        return {"hidden": [x for x in data.get("hidden", []) if isinstance(x, str)], "max_name": MAX_NAME}

    async def add(self, room: Any, kind: Any, name: Any, entities: Any, user: str | None) -> dict[str, Any]:
        if not isinstance(room, str) or not _ROOM.fullmatch(room) or kind not in ("l", "c"):
            raise Invalid("room / kind")
        title = clean_name(name)
        ents = self.capture(entities, kind)
        reg = er.async_get(self.hass)
        mine = [e for e in reg.entities.values() if e.domain == "scene" and e.platform == "homeassistant" and str(e.unique_id).startswith("hauflo_fam_")]
        if len(mine) >= MAX_FAMILY:
            raise Invalid("too many scenes")
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
        from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

        reg = er.async_get(self.hass)
        areas = set()
        for eid in entities:
            ent = reg.async_get(eid)
            area = ent.area_id if ent else None
            if ent and not area and ent.device_id and (dev := dr.async_get(self.hass).async_get(ent.device_id)):
                area = dev.area_id
            areas.add(area)
        return next(iter(areas)) if len(areas) == 1 else None

    def _scene_id_of(self, entity_id: Any) -> str:
        if not isinstance(entity_id, str) or not entity_id.startswith("scene."):
            raise Invalid("entity_id")
        ent = er.async_get(self.hass).async_get(entity_id)
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
        ru = str(engine.hass.config.language or "").startswith("ru")
        op = body.get("op")
        try:
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
        except Invalid as err:
            return self.json_message(str(err), 400)
        except NotPlain as err:
            _LOGGER.warning("Hauflo scenes: scenes.yaml left as it is (%s)", err)
            return self.json({"message": NotPlain.RU if ru else NotPlain.EN, "code": "not_plain"}, 409)
        except PermissionError as err:
            return self.json_message(str(err), 403)
        except KeyError:
            return self.json_message("no such scene", 404)
        except LookupError as err:
            msg = "Нечего запомнить: свет и шторы комнаты не сообщают своё состояние." if ru else str(err)
            return self.json({"message": msg, "code": "nothing"}, 409)
        except Exception as err:  # noqa: BLE001 — HA refused the file / reload: said, not a 500 page
            _LOGGER.warning("Hauflo scenes: %s failed: %s", op, err)
            return self.json_message(f"Home Assistant: {err}", 500)
        return self.json_message("op: add, rename, recapture, delete or unhide", 400)
