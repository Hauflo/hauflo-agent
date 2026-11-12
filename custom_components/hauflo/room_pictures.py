"""Pictures for rooms by name (owner 12.11, Р54): a boiler room, a water node, a switchboard… (utility, `auto`) get one
of three pictures of ours (www/rooms, canon in room_pictures.json) until the room has a photo of its own; a living room,
a bedroom, a kitchen… have three of their type offered but show none until somebody chooses (owner 12.11: residential by
choice); a name no type matches is offered the general set. The client may change the photo right from the room's card.

WebSocket commands (phone card → HA; the cloud's editor goes the same way through the tunnel):
* `hauflo/room_picture_options {area_id}` → `[{url, label}]` — the three of the room's type (the general three when no type matches).
  Р82 (one picker for everything with a picture): `{name, zone: true}` instead of `area_id` — a zone's / floor's tile on
  «Главная» (no HA area; canon `zones`, its type by the name); `all: true` — then every other type of the canon too, each
  labelled «<type> · <variant>» (Р61: any picture may be chosen; the card's editor asks so, the tile's sheet keeps three).
* `hauflo/area_picture {area_id, picture}` — sets (an address `_image_ok` accepts) or clears (null) the area's picture;
  a restricted (child) profile may not (house.py), as it may not reorder blocks (order.py).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar

from .house import may_edit

FILE = Path(__file__).with_name("room_pictures.json")
CANON: dict[str, Any] = json.loads(FILE.read_text(encoding="utf-8"))
BASE: str = CANON["base"]
TYPES: list[dict[str, Any]] = CANON["types"]
_RES = [(t, [re.compile(p, re.IGNORECASE) for p in t["match"]]) for t in TYPES]
FALLBACK: dict[str, Any] = next(t for t in TYPES if t["id"] == CANON["fallback"])
ZONES: list[dict[str, Any]] = CANON["zones"]["types"]
_ZONE_RES = [(t, [re.compile(p, re.IGNORECASE) for p in t["match"]]) for t in ZONES]
ZONE_FALLBACK: dict[str, Any] = next(t for t in ZONES if t["id"] == CANON["zones"]["fallback"])
OPTIONS = "hauflo/room_picture_options"
SET = "hauflo/area_picture"


def kind_of(name: Any) -> dict[str, Any] | None:
    """The room type a room's name says (first of the canon that matches), else None."""
    if not isinstance(name, str):
        return None
    return next((t for t, res in _RES if any(r.search(name) for r in res)), None)


def zone_kind_of(name: Any) -> dict[str, Any]:
    """A zone's / floor's type by its name (canon `zones`, first that matches), else the canon's fallback (a floor)."""
    text = name if isinstance(name, str) else ""
    return next((t for t, res in _ZONE_RES if any(r.search(text) for r in res)), ZONE_FALLBACK)


def urls_of(kind: dict[str, Any]) -> list[str]:
    return [BASE + f for f in kind["files"]]


def default_picture(name: Any) -> str | None:
    """The picture a room without a photo shows by itself: the first of its type's (always the same one) — utility
    types only (`auto`); a living room shows none until chosen."""
    kind = kind_of(name)
    return urls_of(kind)[0] if kind and kind.get("auto") else None


def labels(kind: dict[str, Any], lang: str, group: bool = False) -> list[dict[str, str]]:
    """«Вариант n» of the kind's pictures; `group` — «<type> · Вариант n» (an option among every type's)."""
    ru = lang.startswith("ru")
    text = CANON["variant_labels"]["ru" if ru else "en"]
    head = f"{kind['label']['ru' if ru else 'en']} · " if group else ""
    return [{"url": u, "label": head + text.replace("{n}", str(i + 1))} for i, u in enumerate(urls_of(kind))]


def choices(own: dict[str, Any], lang: str, every: bool = False) -> list[dict[str, str]]:
    """The own type's three first; `every` — then every room type of the canon (a set already offered is not repeated)."""
    out = labels(own, lang)
    if every:
        seen = {o["url"] for o in out}
        for kind in TYPES:
            more = [o for o in labels(kind, lang, group=True) if o["url"] not in seen]
            seen.update(o["url"] for o in more)
            out += more
    return out


def options_for(hass: HomeAssistant, area_id: Any, every: bool = False) -> list[dict[str, str]] | None:
    """The room's choices; None — no such room."""
    area = ar.async_get(hass).async_get_area(area_id) if isinstance(area_id, str) else None
    if area is None:
        return None
    return choices(kind_of(area.name) or FALLBACK, str(hass.config.language or ""), every)


@websocket_api.websocket_command(
    {
        vol.Required("type"): OPTIONS,
        vol.Exclusive("area_id", "of"): str,
        vol.Exclusive("name", "of"): str,
        vol.Optional("zone", default=False): bool,
        vol.Optional("all", default=False): bool,
    }
)
@websocket_api.async_response
async def _options(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    lang = str(hass.config.language or "")
    if "name" in msg:
        # A zone's / floor's tile (no HA area) — or a room the editor knows only by name.
        own = zone_kind_of(msg["name"]) if msg["zone"] else kind_of(msg["name"]) or FALLBACK
        connection.send_result(msg["id"], choices(own, lang, msg["all"]))
        return
    if "area_id" not in msg:
        connection.send_error(msg["id"], "invalid_format", "area_id or name expected")
        return
    found = options_for(hass, msg["area_id"], msg["all"])
    if found is None:
        connection.send_error(msg["id"], "not_found", "no such room")
        return
    connection.send_result(msg["id"], found)


@websocket_api.websocket_command({vol.Required("type"): SET, vol.Required("area_id"): str, vol.Required("picture"): vol.Any(str, None)})
@websocket_api.async_response
async def _set(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    from . import _image_ok  # noqa: PLC0415 — the package imports this module

    if not await may_edit(hass, connection.user):
        connection.send_error(msg["id"], "not_allowed", "a restricted (child) profile can't change the house's settings")
        return
    picture = msg["picture"]
    if picture is not None and not _image_ok(picture):
        connection.send_error(msg["id"], "invalid_format", "an image address of this home expected")
        return
    areas = ar.async_get(hass)
    if areas.async_get_area(msg["area_id"]) is None:
        connection.send_error(msg["id"], "not_found", "no such room")
        return
    areas.async_update(msg["area_id"], picture=picture)
    connection.send_result(msg["id"], {"picture": picture})


def register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, _options)
    websocket_api.async_register_command(hass, _set)
