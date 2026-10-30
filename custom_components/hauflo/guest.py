"""Гостевой доступ по QR / ссылке (Н2, owner 28.10): a guest without an account controls the lights, blinds and
climate of the rooms a person of this house chose, for a few hours or days; never a lock, the alarm, a gate, a camera
or a setting; revoked in one tap. Security design: docs/guest-access.md.

Everything is decided here, in the agent. The cloud only relays the guest's request (a «guest» frame of the agent's
link) and serves a static page; it can't issue a link (the secret is born here and only its hash is kept), read the
list or the log (`/api/hauflo/guest` is not in the tunnel's allowlist), or reach anything but this module's fixed
table of actions. Every request is checked again: the grant alive, the entity in its snapshot, still in its room,
still a safe Hauflo light / cover / climate, the issuer still a person allowed to control it.

GET/POST /api/hauflo/guest — a signed-in person of this HA (issuing: one who may control the entities and isn't a
restricted profile; revoking: any; «ask»: a restricted profile asks the admins for a link — family review round 3).
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
import datetime as dt
import hashlib
import hmac
import logging
import re
import secrets
import time
from typing import Any

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from .house import may_edit, refuse
from .natural import natural_key
from .storage import HaufloStore
from . import qr
from .tuning import TUNING

_LOGGER = logging.getLogger(__name__)
STORE_KEY = "hauflo.guest"
DOMAINS = ("light", "cover", "climate")
_T = TUNING["guest"]
MAX_HOURS = _T["max_hours"]
MAX_ACTIVE = _T["max_active"]
MAX_DEVICES = _T["max_devices"]
DEFAULT_DEVICES = _T["default_devices"]
MAX_ROOMS = _T["max_rooms"]
KEEP_ENDED = dt.timedelta(days=7)  # an ended link stays in the list a week («истекла», «закрыта»)
LOG_DAYS = _T["log_days"]
LOG_MAX = _T["log_max"]
TEMP_BAND = tuple(_T["temp_band"])  # a guest's thermostat range, inside the entity's own
RATE_ACT = _T["rate_act"]  # actions a minute per link
RATE_STATE = _T["rate_state"]  # reads a minute per link (the page polls every 10 s)
RATE_BAD = _T["rate_bad"]  # wrong tokens a minute for the whole house → every wrong one gets 429
_TOKEN = re.compile(r"([A-Za-z0-9_-]{12})\.([A-Za-z0-9_-]{32})")
_DEVICE = re.compile(r"[A-Za-z0-9_-]{16,64}")
_ENTITY = re.compile(r"(light|cover|climate)\.[a-z0-9_]{1,128}")
_UNSAFE_TEXT = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]")
# The fixed table: action → (domain, service). The guest names an action, never a service.
ACTIONS: dict[str, tuple[str, str]] = {
    "on": ("light", "turn_on"),
    "off": ("light", "turn_off"),
    "open": ("cover", "open_cover"),
    "close": ("cover", "close_cover"),
    "stop": ("cover", "stop_cover"),
    "position": ("cover", "set_cover_position"),
    "temperature": ("climate", "set_temperature"),
}
_COVER_FEATURE = {"open": 1, "close": 2, "position": 4, "stop": 8}


class Invalid(ValueError):
    """A request the family's screen sent wrong (400)."""


def route_of(secret: str) -> str:
    """The guest pages' address part of this house: the cloud derives the same from the hash of the agent's secret
    (agentHub.ts guestRouteOf) — no project id in the link, and no other agent can take this house's route."""
    inner = hashlib.sha256(secret.encode()).hexdigest()
    return hashlib.sha256(f"hauflo-guest-route:{inner}".encode()).hexdigest()[:24]


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _clean(text: Any, limit: int) -> str:
    return _UNSAFE_TEXT.sub("", text if isinstance(text, str) else "").strip()[:limit]


def _iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


def _parse(value: Any) -> dt.datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


class _Window:
    """Events in the last minute."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.at: deque[float] = deque()

    def full(self) -> bool:
        now = time.monotonic()
        while self.at and now - self.at[0] > 60:
            self.at.popleft()
        return len(self.at) >= self.limit

    def add(self) -> None:
        self.at.append(time.monotonic())


class GuestAccess:
    def __init__(self, hass: HomeAssistant, owns: Callable[[str], bool], link_base: Callable[[], str]) -> None:
        self.hass = hass
        self.owns = owns  # Hauflo's own and safe (__init__._hauflo_entity: never a lock / alarm / valve / garage door)
        self.link_base = link_base  # «https://app.hauflo.ru/g/<route>»: the agent's own region server
        self.store: HaufloStore = HaufloStore(hass, STORE_KEY)
        self.grants: list[dict[str, Any]] = []
        self.log: list[dict[str, Any]] = []
        self.loaded = False
        self.lock = asyncio.Lock()
        self._load_lock = asyncio.Lock()
        self.bad = _Window(RATE_BAD)
        self.rates: dict[tuple[str, str], _Window] = {}
        self.refused: set[tuple[str, str]] = set()

    # --- storage -----------------------------------------------------------------------------------------------------
    async def async_load(self) -> None:
        # Once, under a lock: two guest requests at once mustn't both read the store and the later one put back what the
        # earlier already changed (a lost log entry, a device, a revoke — found by test_frame_round_trip).
        if self.loaded:
            return
        async with self._load_lock:
            if self.loaded:
                return
            data = await self.store.async_load()
            if isinstance(data, dict):
                self.grants = [g for g in data.get("grants", []) if isinstance(g, dict) and isinstance(g.get("id"), str)]
                self.log = [x for x in data.get("log", []) if isinstance(x, dict)]
            self.loaded = True

    async def async_stop(self) -> None:
        """The entry unloaded: the log noted in the last second is written now, not by a timer of a gone agent."""
        if self.loaded:
            async with self.lock:
                await self.store.async_save(self._data())
        self.rates.clear()
        self.refused.clear()

    def _data(self) -> dict[str, Any]:
        return {"grants": self.grants, "log": self.log}

    def _prune(self) -> None:
        now = dt_util.utcnow()
        self.grants = [g for g in self.grants if (end := self._ended_at(g)) is None or now - end < KEEP_ENDED]
        cut = now - dt.timedelta(days=LOG_DAYS)
        self.log = [x for x in self.log if (t := _parse(x.get("t"))) is not None and t >= cut][:LOG_MAX]

    def _note(self, entry: dict[str, Any]) -> None:
        self.log.insert(0, {"t": _iso(dt_util.utcnow()), **entry})
        del self.log[LOG_MAX:]
        self.store.async_delay_save(self._data, 1)

    # --- what a guest may get ----------------------------------------------------------------------------------------
    def _area(self, eid: str) -> str | None:
        ent = er.async_get(self.hass).async_get(eid)
        if ent is None:
            return None
        if ent.area_id:
            return ent.area_id
        if ent.device_id and (dev := dr.async_get(self.hass).async_get(ent.device_id)):
            return dev.area_id
        return None

    def _safe(self, eid: Any) -> bool:
        """A Hauflo light / cover / climate on the family's screen, never a lock / alarm / valve / garage, gate or door
        cover (owns()), not a group, not the agent's own (the comfort floor is the integrator's), not hidden / off."""
        if not isinstance(eid, str) or not _ENTITY.fullmatch(eid) or self.hass.states.get(eid) is None:
            return False
        ent = er.async_get(self.hass).async_get(eid)
        if ent is None or ent.disabled_by or ent.hidden_by or ent.platform in ("group", "hauflo"):
            return False
        return bool(self.owns(eid))

    def candidates(self, area_id: str) -> list[str]:
        return sorted(eid for d in DOMAINS for eid in self.hass.states.async_entity_ids(d) if self._safe(eid) and self._area(eid) == area_id)

    def rooms(self, user: Any = None) -> list[dict[str, Any]]:
        out = []
        for area in ar.async_get(self.hass).async_list_areas():
            ids = [e for e in self.candidates(area.id) if user is None or _may_control(user, e)]
            if ids:
                out.append({"id": area.id, "name": area.name, "count": len(ids), "kinds": sorted({e.split(".")[0] for e in ids})})
        return sorted(out, key=lambda r: natural_key(r["name"]))

    # --- the family's side -------------------------------------------------------------------------------------------
    def _ended_at(self, g: dict[str, Any]) -> dt.datetime | None:
        if g.get("revoked"):
            return _parse(g["revoked"]) or dt_util.utcnow()
        exp = _parse(g.get("expires"))
        return exp if exp is not None and exp <= dt_util.utcnow() else None

    def _status(self, g: dict[str, Any]) -> str:
        if g.get("revoked"):
            return "revoked"
        exp = _parse(g.get("expires"))
        return "expired" if exp is None or exp <= dt_util.utcnow() else "active"

    def answer(self, user: Any = None) -> dict[str, Any]:
        self._prune()
        names = {a.id: a.name for a in ar.async_get(self.hass).async_list_areas()}
        return {
            "grants": [
                {
                    "id": g["id"],
                    "label": g.get("label") or "",
                    "rooms": [{"id": r, "name": names.get(r, r)} for r in g.get("rooms", [])],
                    "count": len(g.get("entities", [])),
                    "by": g.get("by") or "",
                    "created": g.get("created"),
                    "expires": g.get("expires"),
                    "revoked": g.get("revoked"),
                    "status": self._status(g),
                    "devices": len(g.get("devices", [])),
                    "max_devices": g.get("max_devices", DEFAULT_DEVICES),
                }
                for g in self.grants
            ],
            "log": self.log[:100],
            "can_issue": user is not None and _issuer_ok(user),
        }

    async def issue(self, user: Any, body: dict[str, Any]) -> dict[str, Any]:
        if not _issuer_ok(user):
            raise PermissionError("only a person who controls the house can invite a guest")
        label = _clean(body.get("label"), 40)
        rooms = body.get("rooms")
        if not isinstance(rooms, list) or not 1 <= len(rooms) <= MAX_ROOMS or not all(isinstance(r, str) for r in rooms):
            raise Invalid("rooms: a list of rooms")
        rooms = list(dict.fromkeys(rooms))
        areas = ar.async_get(self.hass)
        if any(areas.async_get_area(r) is None for r in rooms):
            raise Invalid("no such room")
        hours = body.get("hours")
        if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= MAX_HOURS:
            raise Invalid(f"hours: 1…{MAX_HOURS}")
        devices = body.get("max_devices", DEFAULT_DEVICES)
        if isinstance(devices, bool) or not isinstance(devices, int) or not 1 <= devices <= MAX_DEVICES:
            raise Invalid(f"max_devices: 1…{MAX_DEVICES}")
        entities = [e for r in rooms for e in self.candidates(r) if _may_control(user, e)]
        if not entities:
            raise Invalid("nothing a guest may control in these rooms")
        async with self.lock:
            self._prune()
            if sum(1 for g in self.grants if self._status(g) == "active") >= MAX_ACTIVE:
                raise Invalid(f"at most {MAX_ACTIVE} guest links at once — close one first")
            gid, secret = secrets.token_urlsafe(9), secrets.token_urlsafe(24)
            now = dt_util.utcnow()
            grant = {
                "id": gid,
                "hash": _hash(secret),
                "label": label,
                "rooms": rooms,
                "entities": sorted(set(entities)),
                "by": user.name or "",
                "by_id": user.id,
                "created": _iso(now),
                "expires": _iso(now + dt.timedelta(hours=hours)),
                "revoked": None,
                "devices": [],
                "max_devices": devices,
            }
            self.grants.insert(0, grant)
            self.log.insert(0, {"t": _iso(now), "sys": "issued", "grant": gid, "label": label, "by": user.name or "", "hours": hours})
            await self.store.async_save(self._data())
        link = f"{self.link_base()}#{gid}.{secret}"
        # Shown once: only the hash stays here (a backup of HA doesn't hold a working link).
        return {"id": gid, "link": link, "qr": ["".join("1" if v else "0" for v in row) for row in qr.encode(link)], "expires": grant["expires"], "count": len(grant["entities"])}

    async def revoke(self, user: Any, grant_id: Any = None) -> int:
        """One link (`grant_id`) or every one still open (None). Any person of the house may — the safe direction."""
        n = 0
        async with self.lock:
            now = _iso(dt_util.utcnow())
            for g in self.grants:
                if (grant_id is None or g["id"] == grant_id) and self._status(g) == "active":
                    g["revoked"], g["revoked_by"] = now, getattr(user, "name", "") or ""
                    n += 1
            one = next((g for g in self.grants if g["id"] == grant_id), None)
            if grant_id is not None and one is None:
                raise Invalid("no such guest link")
            label = (one or {}).get("label") or ""
            self.log.insert(0, {"t": now, "sys": "revoked" if grant_id else "revoked_all", "grant": grant_id, "label": label, "by": getattr(user, "name", "") or "", "n": n})
            del self.log[LOG_MAX:]
            await self.store.async_save(self._data())
        return n

    # --- the guest's side (a «guest» frame from the cloud) --------------------------------------------------------------
    async def handle(self, body: Any) -> tuple[int, dict[str, Any]]:
        """What the guest's page asked: {token, device, op: state | act, entity_id?, action?, value?}. Never trusts it."""
        if not isinstance(body, dict) or set(body) - {"token", "device", "op", "entity_id", "action", "value"}:
            return 400, {"code": "bad_request"}
        token, device, op = body.get("token"), body.get("device"), body.get("op")
        if op not in ("state", "act") or not isinstance(device, str) or not _DEVICE.fullmatch(device):
            return 400, {"code": "bad_request"}
        m = _TOKEN.fullmatch(token) if isinstance(token, str) else None
        if self.bad.full():
            # Someone tries tokens: every wrong one waits (a real link is checked first — it still works).
            if m is None or self._grant(m.group(1), m.group(2)) is None:
                return 429, {"code": "slow_down"}
        grant = self._grant(m.group(1), m.group(2)) if m else None
        if grant is None:
            self.bad.add()
            return 404, {"code": "invalid"}
        if (status := self._status(grant)) != "active":
            return 410, {"code": status}
        issuer = await self.hass.auth.async_get_user(grant.get("by_id") or "")
        if issuer is None or not issuer.is_active or not _issuer_ok(issuer):
            return 410, {"code": "revoked"}
        rate = self.rates.setdefault((grant["id"], op), _Window(RATE_ACT if op == "act" else RATE_STATE))
        if rate.full():
            return 429, {"code": "slow_down"}
        rate.add()
        dev = _hash(device)[:16]
        if dev not in grant["devices"]:
            if len(grant["devices"]) >= grant.get("max_devices", DEFAULT_DEVICES):
                if (grant["id"], dev) not in self.refused:  # once per device: a forwarded link can't flood the log
                    self.refused.add((grant["id"], dev))
                    self._note({"grant": grant["id"], "label": grant.get("label") or "", "sys": "device_refused"})
                return 403, {"code": "device_limit"}
            grant["devices"].append(dev)
            self._note({"grant": grant["id"], "label": grant.get("label") or "", "sys": "opened", "n": len(grant["devices"])})
        allowed = self._allowed(grant, issuer)
        if op == "state":
            return 200, self._view(grant, allowed)
        return await self._act(grant, allowed, body.get("entity_id"), body.get("action"), body.get("value"))

    def _grant(self, gid: str, secret: str) -> dict[str, Any] | None:
        want = _hash(secret)
        found = None
        for g in self.grants:
            # Every grant compared in constant time: no timing tells which id exists.
            if hmac.compare_digest(str(g.get("id")), gid) & hmac.compare_digest(str(g.get("hash")), want):
                found = g
        return found

    def _allowed(self, grant: dict[str, Any], issuer: Any) -> list[str]:
        """The grant's snapshot as it stands now: still safe, still in one of its rooms, still the issuer's to control."""
        rooms = set(grant.get("rooms", []))
        return [e for e in grant.get("entities", []) if self._safe(e) and self._area(e) in rooms and _may_control(issuer, e)]

    def _item(self, eid: str) -> dict[str, Any]:
        st = self.hass.states.get(eid)
        a = st.attributes if st is not None else {}
        domain = eid.split(".")[0]
        out: dict[str, Any] = {"id": eid, "name": _clean(a.get("friendly_name") or eid, 60), "domain": domain, "state": st.state if st else "unavailable"}
        if domain == "light":
            modes = a.get("supported_color_modes") or []
            out["dim"] = any(mode != "onoff" for mode in modes)
            b = a.get("brightness")
            out["brightness"] = round(b / 255 * 100) if isinstance(b, (int, float)) and st and st.state == "on" else None
        elif domain == "cover":
            f = int(a.get("supported_features") or 0)
            out["can"] = [k for k, bit in _COVER_FEATURE.items() if f & bit]
            pos = a.get("current_position")
            out["position"] = pos if isinstance(pos, (int, float)) else None
        else:
            lo, hi = self._temp_range(a)
            out.update(
                {
                    "target": a.get("temperature") if isinstance(a.get("temperature"), (int, float)) else None,
                    "current": a.get("current_temperature") if isinstance(a.get("current_temperature"), (int, float)) else None,
                    "min": lo,
                    "max": hi,
                    "step": 0.5,
                    "settable": bool(int(a.get("supported_features") or 0) & 1) and lo < hi,
                }
            )
        return out

    @staticmethod
    def _temp_range(a: Any) -> tuple[float, float]:
        lo = a.get("min_temp") if isinstance(a.get("min_temp"), (int, float)) else TEMP_BAND[0]
        hi = a.get("max_temp") if isinstance(a.get("max_temp"), (int, float)) else TEMP_BAND[1]
        return max(float(lo), TEMP_BAND[0]), min(float(hi), TEMP_BAND[1])

    def _view(self, grant: dict[str, Any], allowed: list[str]) -> dict[str, Any]:
        names = {a.id: a.name for a in ar.async_get(self.hass).async_list_areas()}
        rooms = []
        for r in grant.get("rooms", []):
            # By name, naturally (Штора 2 before Штора 10) — not by entity id.
            items = sorted((self._item(e) for e in allowed if self._area(e) == r), key=lambda i: natural_key(i["name"]))
            if items:
                rooms.append({"name": _clean(names.get(r, ""), 60), "items": items})
        # No house name, no people, no other rooms: what the guest may touch and when it ends.
        return {"label": grant.get("label") or "", "expires": grant.get("expires"), "rooms": rooms}

    async def _act(self, grant: dict[str, Any], allowed: list[str], eid: Any, action: Any, value: Any) -> tuple[int, dict[str, Any]]:
        if not isinstance(eid, str) or eid not in allowed:
            return 403, {"code": "not_allowed"}
        if not isinstance(action, str) or action not in ACTIONS:
            return 400, {"code": "bad_action"}
        domain, service = ACTIONS[action]
        if eid.split(".")[0] != domain:
            return 403, {"code": "not_allowed"}
        st = self.hass.states.get(eid)
        a = st.attributes if st is not None else {}
        data: dict[str, Any] = {"entity_id": eid}
        num = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        if action == "on" and value is not None:
            if num is None or not 1 <= num <= 100 or not any(mode != "onoff" for mode in (a.get("supported_color_modes") or [])):
                return 400, {"code": "bad_value"}
            data["brightness_pct"] = round(num)
        elif action in _COVER_FEATURE:
            if not int(a.get("supported_features") or 0) & _COVER_FEATURE[action]:
                return 400, {"code": "bad_action"}
            if action == "position":
                if num is None or not 0 <= num <= 100:
                    return 400, {"code": "bad_value"}
                data["position"] = round(num)
            elif value is not None:
                return 400, {"code": "bad_value"}
        elif action == "temperature":
            lo, hi = self._temp_range(a)
            if num is None or not int(a.get("supported_features") or 0) & 1 or not lo <= num <= hi:
                return 400, {"code": "bad_value"}
            data["temperature"] = round(num * 2) / 2
        elif value is not None:
            return 400, {"code": "bad_value"}
        entry = {"grant": grant["id"], "label": grant.get("label") or "", "entity_id": eid, "name": self._item(eid)["name"], "do": action, "value": data.get("brightness_pct", data.get("position", data.get("temperature")))}
        try:
            async with asyncio.timeout(10):
                await self.hass.services.async_call(domain, service, data, blocking=True, context=Context())
        except Exception as err:  # noqa: BLE001 — the guest gets «не получилось», the family the reason in the log
            _LOGGER.warning("Hauflo guest: %s.%s on %s failed: %s", domain, service, eid, err)
            self._note({**entry, "ok": False})
            return 502, {"code": "failed"}
        self._note({**entry, "ok": True})
        return 200, {"ok": True, "item": self._item(eid)}


def _issuer_ok(user: Any) -> bool:
    """A person (not a system user) who may control the house in HA — a read-only one may not hand control on."""
    if user is None or getattr(user, "system_generated", True) or not getattr(user, "is_active", False):
        return False
    try:
        from homeassistant.auth.permissions.const import POLICY_CONTROL  # noqa: PLC0415

        return bool(user.is_admin or user.permissions.access_all_entities(POLICY_CONTROL))
    except Exception:  # noqa: BLE001 — a changed HA internal: fail closed
        return False


def _may_control(user: Any, eid: str) -> bool:
    try:
        from homeassistant.auth.permissions.const import POLICY_CONTROL  # noqa: PLC0415

        return bool(user.is_admin or user.permissions.check_entity(eid, POLICY_CONTROL))
    except Exception:  # noqa: BLE001 — a changed HA internal: fail closed
        return False


ASK_GAP = _T["ask_gap_s"]  # «Попросить администратора»: once in 10 minutes per person


async def ask_admins(hass: HomeAssistant, user: Any, note: str, phones: list[dict[str, Any]]) -> dict[str, Any]:
    """«Марина просит гостевую ссылку на вечер» to the admins: a notification in HA and a push to the admins' phones (the
    HA app, found as the alarms find them). The guest link itself is still made by an admin — nothing is issued here."""
    from homeassistant.components import persistent_notification  # noqa: PLC0415

    ru = str(hass.config.language or "ru").lower().startswith("ru")
    who = (user.name or "").strip() or ("Кто-то из семьи" if ru else "Someone in the family")
    what = f"{who} просит гостевую ссылку" if ru else f"{who} asks for a guest link"
    if note:
        what += f" {note}" if ru else f" ({note})"
    how = "Создать: Hauflo → ⚙ Настройки → Гостевой доступ." if ru else "To make one: Hauflo → ⚙ Settings → Guest access."
    title = "Гостевой доступ" if ru else "Guest access"
    persistent_notification.async_create(hass, f"{what}.\n\n{how}", title=title, notification_id=f"hauflo_guest_ask_{user.id}")
    admins = {u.id for u in await hass.auth.async_get_users() if u.is_admin and u.is_active and not u.system_generated}
    people = {s.entity_id for s in hass.states.async_all("person") if s.attributes.get("user_id") in admins}
    sent = 0
    for phone in phones:
        if phone.get("person") not in people:
            continue
        try:
            await hass.services.async_call("notify", phone["service"], {"title": title, "message": f"{what}. {how}", "data": {"tag": f"hauflo_guest_ask_{user.id}"}}, blocking=True)
            sent += 1
        except Exception as err:  # noqa: BLE001 — one phone that fails mustn't stop the others
            _LOGGER.warning("Hauflo guest request to %s failed: %s", phone.get("service"), err)
    return {"asked": True, "phones": sent}


class GuestView(HomeAssistantView):
    """The family's side, signed in to HA. Not reachable through Hauflo's tunnel (not in __init__._HAUFLO_VIEWS)."""

    url = "/api/hauflo/guest"
    name = "api:hauflo:guest"
    requires_auth = True

    def __init__(self, engine_of: Callable[[], GuestAccess | None], alarms_of: Callable[[], Any] | None = None) -> None:
        self.engine_of = engine_of
        self.alarms_of = alarms_of  # the alarm engine: its phones() are the admins' phones for «ask»
        self.asked: dict[str, float] = {}

    async def get(self, request):
        engine = self.engine_of()
        if engine is None:
            return self.json_message("Hauflo agent not set up", 503)
        await engine.async_load()
        user = request["hass_user"]
        out = engine.answer(user)
        # Guest links are the house's settings too: a restricted (child) profile (house.py) doesn't issue — it asks.
        out["can_issue"] = bool(out["can_issue"]) and await may_edit(request.app[KEY_HASS], user)
        out["can_ask"] = not out["can_issue"] and user is not None and not user.system_generated
        if request.query.get("rooms"):
            out["rooms"] = engine.rooms(user) if out["can_issue"] else []
        return self.json(out)

    async def post(self, request):
        engine = self.engine_of()
        if engine is None:
            return self.json_message("Hauflo agent not set up", 503)
        await engine.async_load()
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict):
            return self.json_message("an object expected", 400)
        op = body.get("op")
        # A new link is the house's settings (house.py: admins and the people an admin allowed); closing one is the safe
        # direction — any person of the house.
        if op == "issue" and (no := await refuse(request)) is not None:
            return no
        user = request["hass_user"]
        if op == "ask":
            note = body.get("note", "")
            if set(body) - {"op", "note"} or not isinstance(note, str) or len(note) > 60 or _UNSAFE_TEXT.search(note):
                return self.json_message("ask: an optional short note", 400)
            if user is None or user.system_generated:
                return self.json_message("a person of the house only", 403)
            last = self.asked.get(user.id, 0.0)
            if time.monotonic() - last < ASK_GAP and last:
                return self.json({"message": "already asked", "code": "too_soon", "wait": int(ASK_GAP - (time.monotonic() - last))}, 429)
            self.asked[user.id] = time.monotonic()
            alarms = self.alarms_of() if self.alarms_of else None
            try:
                phones = alarms.phones() if alarms is not None else []
            except Exception:  # noqa: BLE001 — no phones: the HA notification alone
                phones = []
            return self.json(await ask_admins(request.app[KEY_HASS], user, " ".join(note.split()), phones))
        try:
            if op == "issue":
                if set(body) - {"op", "label", "rooms", "hours", "max_devices"}:
                    return self.json_message("only label, rooms, hours, max_devices", 400)
                return self.json(await engine.issue(user, body))
            if op == "revoke":
                if not isinstance(body.get("id"), str):
                    return self.json_message("id expected", 400)
                return self.json({"revoked": await engine.revoke(user, body["id"]), **engine.answer(user)})
            if op == "revoke_all":
                return self.json({"revoked": await engine.revoke(user, None), **engine.answer(user)})
        except PermissionError as err:
            return self.json_message(str(err), 403)
        except Invalid as err:
            return self.json_message(str(err), 400)
        return self.json_message("op: issue | revoke | revoke_all | ask", 400)
