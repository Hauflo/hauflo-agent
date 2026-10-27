"""«Зима / лето» for the whole house from the client's dashboard settings (owner 27.10: «в зимний период одной кнопкой
кондиционеры на обогрев, летом — на охлаждение»).

Only what the owner asked — no season by date or outdoor temperature. Every climate that can both heat and cool:
  * working → `climate.set_hvac_mode` heat / cool;
  * switched off → never switched on by the season: a KNX one made in HA's KNX UI gets its heat/cool address written
    (DPT 1.100: 1 heat, 0 cool) — one telegram per address, a central switchover shared by many thermostats goes once;
    any other stays as it is and is said.
With «Комфортный пол» set up its own house mode does its rooms (comfort.py), the rest is done here.

GET/POST /api/hauflo/season — any signed-in person of this HA (the client isn't an admin); not through the tunnel.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
STORE_KEY = "hauflo.season"
HEAT, COOL = "heat", "cool"
_GONE = ("unavailable", "unknown")


def acs(hass: HomeAssistant) -> list[Any]:
    """The climates that can both heat and cool — floors and heat-only thermostats never."""
    reg = er.async_get(hass)
    out = []
    for s in hass.states.async_all("climate"):
        modes = s.attributes.get("hvac_modes") or []
        if HEAT not in modes or COOL not in modes or s.state in _GONE:
            continue
        ent = reg.async_get(s.entity_id)
        if ent and (ent.platform == DOMAIN or ent.disabled_by or ent.hidden_by):
            continue  # Hauflo's own room climates, ones taken off the client's screen
        out.append(s)
    return out


def heat_cool_address(hass: HomeAssistant, entity_id: str) -> str | None:
    """The heat/cool switchover address of a KNX UI climate (HA's KNX config store)."""
    try:
        cfg = hass.data["knx"].config_store.get_entity_config(entity_id)  # type: ignore[union-attr]
        ga = ((cfg.get("data") or {}).get("knx") or {}).get("ga_heat_cool") or {}
        w = ga.get("write")
        return w if isinstance(w, str) and w else None
    except Exception:  # noqa: BLE001 — not a KNX UI entity, or HA changed its internals
        return None


class Season:
    def __init__(self, hass: HomeAssistant, comfort_of: Callable[[], Any]) -> None:
        self.hass = hass
        self.comfort_of = comfort_of
        self.store = Store(hass, 1, STORE_KEY)

    async def answer(self) -> dict[str, Any]:
        saved = (await self.store.async_load()) or {}
        items = acs(self.hass)
        on = [s.state for s in items if s.state in (HEAT, COOL)]
        # What the working ACs do now (the most of them); with all off — the last one chosen here.
        season = (HEAT if on.count(HEAT) >= on.count(COOL) else COOL) if on else saved.get("season")
        return {
            "season": season,
            "chosen": saved.get("season"),
            "at": saved.get("at"),
            "acs": [{"entity_id": s.entity_id, "name": s.attributes.get("friendly_name") or s.entity_id, "state": s.state} for s in items],
        }

    async def apply(self, mode: str, user: str | None) -> dict[str, Any]:
        hass = self.hass
        changed: list[str] = []
        sent: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        comfort = self.comfort_of()
        done: set[str] = set()
        rooms = comfort.rooms() if comfort is not None else {}
        if rooms:
            from .comfort import SUMMER, WINTER  # noqa: PLC0415

            await comfort.set_mode(WINTER if mode == HEAT else SUMMER)
            for room in rooms.values():
                done.update(room.get("acs") or [])
            changed += sorted(done)
        addresses: set[str] = set()
        for s in acs(hass):
            if s.entity_id in done or s.state == mode:
                continue
            try:
                if s.state != "off":
                    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": s.entity_id, "hvac_mode": mode}, blocking=True)
                    changed.append(s.entity_id)
                elif (ga := heat_cool_address(hass, s.entity_id)) is not None:
                    if ga not in addresses:
                        addresses.add(ga)
                        await hass.services.async_call("knx", "send", {"address": ga, "payload": 1 if mode == HEAT else 0}, blocking=True)
                    sent.append(s.entity_id)
                else:
                    skipped.append(s.entity_id)
            except Exception as err:  # noqa: BLE001 — one AC failing never stops the others
                _LOGGER.warning("Hauflo season: %s %s failed: %s", mode, s.entity_id, err)
                failed.append(s.entity_id)
        await self.store.async_save({"season": mode, "at": time.time(), "by": user})
        return {"season": mode, "changed": changed, "sent": sent, "skipped": skipped, "failed": failed}


class SeasonView(HomeAssistantView):
    url = "/api/hauflo/season"
    name = "api:hauflo:season"
    requires_auth = True

    def __init__(self, season_of: Callable[[], Season | None]) -> None:
        self.season_of = season_of

    async def get(self, request):
        season = self.season_of()
        if season is None:
            return self.json_message("Hauflo agent not set up", 503)
        return self.json(await season.answer())

    async def post(self, request):
        season = self.season_of()
        if season is None:
            return self.json_message("Hauflo agent not set up", 503)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        mode = body.get("mode") if isinstance(body, dict) else None
        if mode not in (HEAT, COOL):
            return self.json_message("mode: heat or cool", 400)
        # A person's own action, as HA's own buttons are (the agent's KNX allowlist is for Hauflo's tunnel, which
        # can't reach this view).
        user = request["hass_user"]
        return self.json(await season.apply(mode, user.name if user else None))
