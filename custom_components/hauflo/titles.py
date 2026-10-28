"""The client's own names of the dashboard's blocks (owner 2026-10-28: «кому-то надо, чтобы было написано так, а кому-то
как мне»): «Кондиционеры» → «Климат», «Свет» → «Освещение». For the whole house, not per person; kept here, so a
deploy (which rewrites the dashboard) never loses them; the cards put them over the integrator's titles (`block` key of
a card — backend yaml/blockTitles.ts).

GET/POST /api/hauflo/titles — any signed-in person of this HA (the client isn't an admin); not through the tunnel
(not in the tunnel's list of the agent's views). Text only: known block kinds, one line, up to 40 characters; the cards
escape it when they draw it.
"""

from __future__ import annotations

import re
import time
import unicodedata
from typing import Any

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

STORE_KEY = "hauflo.titles"
MAX_LEN = 40
# The same kinds as the backend's BLOCK_KINDS (yaml/blockTitles.ts).
KINDS = (
    "climate",
    "floor_heating",
    "lights",
    "covers",
    "sockets",
    "scenes",
    "light_scenes",
    "cover_scenes",
    "mixed_scenes",
    "alarm_sensors",
    "house_lights",
    "house_covers",
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f  ]+")


def clean(body: Any) -> dict[str, str] | None:
    """The names as kept: known kinds, one trimmed line up to 40 characters; empty = the integrator's. None — not names."""
    if not isinstance(body, dict):
        return None
    out: dict[str, str] = {}
    for kind, raw in body.items():
        if not isinstance(raw, str) or not isinstance(kind, str):
            return None
        if kind not in KINDS:
            continue
        # Bidi / format characters (Unicode Cf: RLO, zero-width…) go too — a name can't flip or hide text.
        raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Cf")
        text = " ".join(_CONTROL.sub(" ", raw).split())[:MAX_LEN].strip()
        if text:
            out[kind] = text
    return out


def _store(hass: HomeAssistant) -> Store:
    return Store(hass, 1, STORE_KEY)


async def async_load(hass: HomeAssistant) -> dict[str, str]:
    data = await _store(hass).async_load() or {}
    return clean(data.get("titles") or {}) or {}


class TitlesView(HomeAssistantView):
    url = "/api/hauflo/titles"
    name = "api:hauflo:titles"
    requires_auth = True

    async def get(self, request):
        return self.json({"titles": await async_load(request.app[KEY_HASS]), "kinds": list(KINDS), "max": MAX_LEN})

    async def post(self, request):
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        titles = clean(body.get("titles") if isinstance(body, dict) else None)
        if titles is None:
            return self.json_message("titles: a map of block kind → text expected", 400)
        user = request["hass_user"]
        hass = request.app[KEY_HASS]
        await _store(hass).async_save({"titles": titles, "at": time.time(), "by": user.name if user else None})
        return self.json({"titles": titles})
