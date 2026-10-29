"""The client's order of a block's items (owner 28.10: «если ты выставил как-то автоматически, а мне не нравится — надо
дать возможность перетаскивать, чтобы выстраивалось как надо»). For the whole house, like the block titles (titles.py):
kept here, so a deploy (which rewrites the dashboard) never loses it; the cards put it over the cloud's order — what
the cloud adds later goes at the end, what it removed is dropped.

A block is `<dashboard>|<view path>|<slot>`: the slot is the card's kind and its block / kind / title — the same in
the cards (hauflo-cards.js `orderSlot`), so the phone and tablet copies of a room's block share one order; the room
tiles of a page are one block (`room:tiles`, items = their paths). Only a block that is on that Hauflo dashboard now,
and only the items it holds (entity ids in its cards, an area card's entities, the tiles' paths) are kept.

GET/POST /api/hauflo/order — any signed-in person of this HA (the client isn't an admin), as the titles; never through
the tunnel (not in the tunnel's list of the agent's views), so the owner's lock on Hauflo doesn't concern it.
"""

from __future__ import annotations

import re
import time
from typing import Any

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant

from .house import refuse
from .storage import HaufloStore
STORE_KEY = "hauflo.order"
MAX_KEY = 300
MAX_ITEMS = 300
MAX_BLOCKS = 500
MAX_ID = 255
PREFIX = "custom:hauflo-"
_DASH = re.compile(r"^hauflo-[a-z0-9_-]{1,100}$")
_COPY = re.compile(r"-copy-\d")
_ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f  ]")


def slot(card: dict[str, Any]) -> str | None:
    """A Hauflo card's slot on its view (hauflo-cards.js `orderSlot`); None — not a Hauflo card."""
    kind = card.get("type")
    if not isinstance(kind, str) or not kind.startswith(PREFIX):
        return None
    t = kind[len(PREFIX):]
    if t == "room" and card.get("path"):
        return "room:tiles"
    zones = card.get("zones")
    title = card.get("title") if isinstance(card.get("title"), str) else ""
    # Untitled light and blind sliders of one room are two blocks: by the domain of their first item.
    ents = card.get("entities")
    first = ents[0] if not title and isinstance(ents, list) and ents else None
    first = first.get("entity") if isinstance(first, dict) else first
    domain = first.split(".")[0] if isinstance(first, str) else ""
    k = next((x for x in (card.get("block"), card.get("kind"), "zones" if isinstance(zones, list) and zones else "", card.get("area"), domain) if isinstance(x, str) and x), "")
    return f"{t}:{k}:{title}"


def parse_key(key: Any) -> tuple[str, str, str] | None:
    if not isinstance(key, str) or not 0 < len(key) <= MAX_KEY or _CONTROL.search(key):
        return None
    parts = key.split("|", 2)
    if len(parts) != 3 or not _DASH.match(parts[0]) or _COPY.search(parts[0]) or not parts[1] or not parts[2]:
        return None
    return parts[0], parts[1], parts[2]


def _walk(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, list):
        for x in node:
            _walk(x, out)
    elif isinstance(node, dict):
        if isinstance(node.get("type"), str):
            out.append(node)
        for x in node.values():
            if isinstance(x, (list, dict)):
                _walk(x, out)


def _ids(node: Any, out: set[str]) -> None:
    if isinstance(node, str):
        if len(node) <= MAX_ID and _ENTITY.match(node):
            out.add(node)
    elif isinstance(node, list):
        for x in node:
            _ids(x, out)
    elif isinstance(node, dict):
        for x in node.values():
            _ids(x, out)


def _area_members(hass: HomeAssistant, area: str) -> set[str]:
    from homeassistant.helpers import device_registry as dr, entity_registry as er  # noqa: PLC0415

    devs = dr.async_get(hass)
    out: set[str] = set()
    for e in er.async_get(hass).entities.values():
        if e.area_id == area or (not e.area_id and e.device_id and (d := devs.async_get(e.device_id)) is not None and d.area_id == area):
            out.add(e.entity_id)
    return out


def items_of(hass: HomeAssistant, view: dict[str, Any], want: str) -> set[str] | None:
    """What the block `want` of a view holds; None — no such block there."""
    cards: list[dict[str, Any]] = []
    _walk(view.get("sections") or [], cards)
    _walk(view.get("cards") or [], cards)
    found = False
    out: set[str] = set()
    for c in cards:
        if slot(c) != want:
            continue
        found = True
        _ids(c, out)
        if want == "room:tiles" and isinstance(c.get("path"), str) and len(c["path"]) <= MAX_ID:
            out.add(c["path"])
        if isinstance(c.get("area"), str):
            out |= _area_members(hass, c["area"])
        # The family's own scenes of the block's room (scenes.py `hauflo_fam_<room>_…`): drawn after the integrator's.
        rows = c.get("rows") if isinstance(c.get("rows"), list) else []
        for room in [c.get("scene_room"), *(r.get("scene_room") for r in rows if isinstance(r, dict))]:
            if isinstance(room, str) and room:
                prefix = f"hauflo_fam_{re.sub('[^a-z0-9]', '', room)}_"
                out |= {s.entity_id for s in hass.states.async_all("scene") if str(s.attributes.get("id", "")).startswith(prefix)}
    return out if found else None


async def _view(hass: HomeAssistant, dash: str, path: str) -> dict[str, Any] | None:
    from homeassistant.components.lovelace.const import LOVELACE_DATA, ConfigNotFound  # noqa: PLC0415

    data = hass.data.get(LOVELACE_DATA)
    board = getattr(data, "dashboards", {}).get(dash) if data is not None else None
    if board is None or getattr(board, "mode", "storage") != "storage":
        return None
    try:
        cfg = await board.async_load(False)
    except ConfigNotFound:
        return None
    views = cfg.get("views") if isinstance(cfg, dict) else None
    if not isinstance(views, list):
        return None
    for i, v in enumerate(views):
        if isinstance(v, dict) and (str(v.get("path") or "") == path or (not v.get("path") and path == str(i))):
            return v
    return None


async def clean_items(hass: HomeAssistant, key: Any, items: Any) -> list[str] | None:
    """The order as kept: the block's own items, once each, up to 300. None — no such block (or not a list)."""
    parsed = parse_key(key)
    if parsed is None or not isinstance(items, list):
        return None
    view = await _view(hass, parsed[0], parsed[1])
    allowed = items_of(hass, view, parsed[2]) if view is not None else None
    if allowed is None:
        return None
    out: list[str] = []
    for x in items:
        if isinstance(x, str) and x in allowed and x not in out:
            out.append(x)
            if len(out) >= MAX_ITEMS:
                break
    return out


def _store(hass: HomeAssistant) -> HaufloStore:
    return HaufloStore(hass, STORE_KEY)


async def async_load(hass: HomeAssistant) -> dict[str, list[str]]:
    data = await _store(hass).async_load() or {}
    raw = data.get("order")
    if not isinstance(raw, dict):
        return {}
    return {k: [x for x in v if isinstance(x, str)][:MAX_ITEMS] for k, v in raw.items() if parse_key(k) and isinstance(v, list)}


class OrderView(HomeAssistantView):
    url = "/api/hauflo/order"
    name = "api:hauflo:order"
    requires_auth = True

    async def get(self, request):
        return self.json({"order": await async_load(request.app[KEY_HASS]), "max": MAX_ITEMS})

    async def post(self, request):
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict):
            return self.json_message("an object expected", 400)
        if (no := await refuse(request)) is not None:
            return no  # «Кто может менять настройки дома» (house.py): admins and the people an admin allowed
        hass = request.app[KEY_HASS]
        order = await async_load(hass)
        if body.get("clear") is True:
            # «Как настроил интегратор»: every block in Hauflo's order again.
            order = {}
        else:
            key = body.get("key")
            items = await clean_items(hass, key, body.get("items"))
            if items is None:
                return self.json_message("key: a block of a Hauflo dashboard and items: a list of its items expected", 400)
            if items:
                if key not in order and len(order) >= MAX_BLOCKS:
                    return self.json_message("too many blocks", 400)
                order[key] = items
            else:
                order.pop(key, None)
        user = request["hass_user"]
        await _store(hass).async_save({"order": order, "at": time.time(), "by": user.name if user else None})
        return self.json({"order": order})
