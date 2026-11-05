"""Other integrations' entities as alarm sources (п.63.6): the integrator's «Уведомлять» switch in Hauflo's list, apart
from the panel's (a leak sensor hidden from the room card still alarms). The deploy sends the whole map; True works as
the «Hauflo мониторинг» label, False silences one the label would watch. Not in the map — the label decides, as before.
Hauflo's own entities are never silenced by it (notify._source_ok checks them first)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .storage import HaufloStore
from .tuning import TUNING

STORE_KEY = f"{DOMAIN}.foreign"
DATA = f"{DOMAIN}_foreign"
MAX_IDS = TUNING["ownership"]["foreign_max"]  # a house's other integrations' entities, never a flood


class Flags:
    def __init__(self, hass: HomeAssistant) -> None:
        self.notify: dict[str, bool] = {}
        self._store = HaufloStore(hass, STORE_KEY)

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        raw = data.get("notify") if isinstance(data, dict) else None
        self.notify = {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, bool)} if isinstance(raw, dict) else {}

    def set_notify(self, m: dict[str, bool]) -> None:
        self.notify = dict(m)
        self._store.async_delay_save(lambda: {"notify": self.notify}, 0)

    async def async_remove(self) -> None:
        await self._store.async_remove()


def flags(hass: HomeAssistant | None) -> Flags | None:
    data = getattr(hass, "data", None)
    return data.get(DATA) if isinstance(data, dict) else None


def notify_flag(hass: HomeAssistant | None, eid: str) -> bool | None:
    """The integrator's say for this entity: True / False, None — not said (the label decides)."""
    f = flags(hass)
    return f.notify.get(eid) if f is not None else None


def notify_on(hass: HomeAssistant | None) -> set[str]:
    f = flags(hass)
    return {k for k, v in f.notify.items() if v} if f is not None else set()


def notify_off(hass: HomeAssistant | None) -> set[str]:
    f = flags(hass)
    return {k for k, v in f.notify.items() if not v} if f is not None else set()


async def async_setup(hass: HomeAssistant) -> Flags:
    if (f := flags(hass)) is not None:
        return f
    f = Flags(hass)
    await f.async_load()
    hass.data[DATA] = f
    return f


def notify_ok(m: Any) -> bool:
    return isinstance(m, dict) and len(m) <= MAX_IDS and all(isinstance(k, str) and cv.valid_entity_id(k) and isinstance(v, bool) for k, v in m.items())


class ForeignView(HomeAssistantView):
    """GET → {"notify": {...}}; POST {"notify": {entity_id: bool}} replaces the map (admin only)."""

    url = "/api/hauflo/foreign"
    name = "api:hauflo:foreign"
    requires_auth = True

    async def get(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        f = flags(request.app[KEY_HASS])
        return self.json({"notify": f.notify if f is not None else {}})

    async def post(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        m = body.get("notify") if isinstance(body, dict) else None
        if not isinstance(body, dict) or set(body) - {"notify"} or not notify_ok(m):
            return self.json_message("notify: {entity_id: bool} expected", 400)
        f = flags(request.app[KEY_HASS])
        if f is None:
            return self.json_message("not ready", 409)
        f.set_notify(m)
        return self.json({"notify": f.notify})
