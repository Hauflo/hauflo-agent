"""The cloud wizard's «Домофон» (Р55): the presets of presets.json (their names RU+EN, honestly «not checked»), the
doors as set (passwords never sent back — only whether one is set), the listen address and the network cards to
choose from; a POST saves the same block the options' «Домофон» step saves (one check for both) and the entry reloads.
The TURN / STUN servers a phone outside the LAN needs for the talk (`ice_servers`) are passed to HA's WebRTC here."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import time
from pathlib import Path
from typing import Any

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant

from ..const import CONF_INTERCOM, DOMAIN
from ..tuning import TUNING
from .door import FIELDS, MODES, OPENERS, PRESETS, configs

_LOGGER = logging.getLogger(__name__)
T = TUNING["intercom"]
FEATURE = "intercom_setup"
SECRETS = ("password", "sip_password")
LANGS = ("ru", "en")
ICE_FIELDS = ("urls", "username", "credential", "secret", "ttl_s")


class SetupError(ValueError):
    """What the wizard / the options form shows: its code (intercom_<code>) and the door it is about."""

    def __init__(self, code: str, field: str, door: str = "") -> None:
        super().__init__(code)
        self.code, self.field, self.door = code, field, door


def _ipv4(text: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(text), ipaddress.IPv4Address)
    except ValueError:
        return False


def _read_labels() -> dict[str, dict[str, str]]:
    """Each preset's name in each language — the agent's own translations (selector.intercom_preset)."""
    out: dict[str, dict[str, str]] = {}
    for lang in LANGS:
        data = json.loads(Path(__file__).parent.parent.joinpath("translations", f"{lang}.json").read_text(encoding="utf-8"))
        for key, text in data["selector"]["intercom_preset"]["options"].items():
            out.setdefault(key, {})[lang] = text
    return out


# Read once at import (HA imports the integration off the event loop): the wizard's GET runs on the loop, where a file
# read is blocking I/O (0.33 fix3 п.21, HA's «Detected blocking call to open»).
LABELS = _read_labels()


def _labels() -> dict[str, dict[str, str]]:
    return LABELS


def presets() -> list[dict[str, Any]]:
    """What the wizard lists: no path, body or code of a panel's API — only what a choice needs."""
    labels = _labels()
    return [
        {
            "id": pid,
            "label": labels.get(pid) or {lang: str(p.get("label") or pid) for lang in LANGS},
            "maker": str(p.get("maker") or ""),
            "mode": str(p.get("mode") or "direct"),
            "open": str(p.get("open") or ""),
            "dtmf": str(p.get("dtmf") or ""),
            "relay": p.get("relay"),
            "sip_server": str(p.get("sip_server") or ""),
            "sip_user": str(p.get("sip_user") or ""),
            "codec": str(p.get("codec") or ""),
            "checked": bool(p.get("checked")),
        }
        for pid, p in PRESETS.items()
    ]


def masked(block: dict[str, Any]) -> dict[str, Any]:
    """The block as set, its passwords replaced by whether one is set (a TURN secret too)."""
    doors = []
    for raw in block.get("doors") or []:
        if isinstance(raw, dict):
            door = {k: v for k, v in raw.items() if k in FIELDS and k not in SECRETS}
            door.update({f"{k}_set": bool(raw.get(k)) for k in SECRETS})
            doors.append(door)
    ice = [{k: v for k, v in s.items() if k in ICE_FIELDS and k not in ("credential", "secret")} | {"credential_set": bool(s.get("credential") or s.get("secret"))} for s in block.get("ice_servers") or [] if isinstance(s, dict)]
    return {"bind": str(block.get("bind") or "auto"), "port": int(block.get("port") or T["sip_port"]), "public": str(block.get("public") or ""), "doors": doors, "ice_servers": ice}


def _ice(raw: Any, old: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if raw is None:
        return old
    if not isinstance(raw, list):
        raise SetupError("bad_ice", "ice_servers")
    out = []
    for s in raw:
        if not isinstance(s, dict):
            raise SetupError("bad_ice", "ice_servers")
        urls = s.get("urls")
        urls = [urls] if isinstance(urls, str) else urls
        if not urls or not all(isinstance(u, str) and u.split(":", 1)[0] in T["ice_schemes"] for u in urls):
            raise SetupError("bad_ice", "ice_servers")
        item: dict[str, Any] = {k: str(s[k]) for k in ("username", "credential", "secret") if s.get(k)}
        item["urls"] = urls
        if s.get("ttl_s") not in (None, ""):
            item["ttl_s"] = int(s["ttl_s"])
        prev = next((o for o in old if o.get("urls") == urls), {})
        for k in ("credential", "secret"):  # not sent back: kept as it was
            if not item.get(k) and prev.get(k) and not s.get(f"{k}_clear"):
                item[k] = prev[k]
        out.append(item)
    return out


def normalize(raw: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    """The block the options keep: {bind, port, public, doors, ice_servers}. A door's password not sent keeps the one
    it had (by the door's name). SetupError — what is wrong and with which door."""
    old = dict(old or {})
    olds = {str(d.get("name") or "").strip(): d for d in old.get("doors") or [] if isinstance(d, dict)}
    doors_in = raw.get("doors") or []
    doors_in = [d for d in (doors_in if isinstance(doors_in, list) else [doors_in]) if isinstance(d, dict)]
    doors = []
    for d in doors_in:
        door = {k: v for k, v in d.items() if k in FIELDS and v is not None}
        prev = olds.get(str(door.get("name") or "").strip(), {})
        for k in SECRETS:
            if not door.get(k) and prev.get(k) and not d.get(f"{k}_clear"):
                door[k] = prev[k]
        if door.get("preset") not in (None, "") and door["preset"] not in PRESETS:
            raise SetupError("bad_preset", "doors", str(door.get("name") or ""))
        if door.get("mode") not in (None, "") and door["mode"] not in MODES:
            raise SetupError("bad_mode", "doors", str(door.get("name") or ""))
        if door.get("open") not in (None, "") and door["open"] not in OPENERS:
            raise SetupError("bad_open", "doors", str(door.get("name") or ""))
        doors.append(door)
    try:
        port = int(raw.get("port") or T["sip_port"])
    except (TypeError, ValueError) as err:
        raise SetupError("bad_port", "port") from err
    value: dict[str, Any] = {"bind": str(raw.get("bind") or "auto").strip(), "port": port, "public": str(raw.get("public") or "").strip(), "doors": doors}
    ice = _ice(raw.get("ice_servers"), list(old.get("ice_servers") or []))
    if ice:
        value["ice_servers"] = ice
    if not 1 <= port <= 65535:
        raise SetupError("bad_port", "port")
    if value["bind"] != "auto" and not _ipv4(value["bind"]):
        raise SetupError("bad_address", "bind")
    if value["public"] and not _ipv4(value["public"].split(":", 1)[0]):
        raise SetupError("bad_address", "public")
    if (bad := next((c for c in configs(value) if c.error is not None), None)) is not None:
        raise SetupError(f"missing_{bad.error}", "doors", bad.name)
    return value


# --- WebRTC: the TURN / STUN servers -----------------------------------------------------------------------------------


def ice_servers(block: dict[str, Any], now: float | None = None) -> list[dict[str, Any]]:
    """The servers HA's WebRTC hands the card. A coturn `use-auth-secret` server gets fresh time-limited credentials
    each time (TURN REST: username «<expiry>:<name>», credential base64 HMAC-SHA1 of it by the shared secret)."""
    out = []
    for s in block.get("ice_servers") or []:
        if not isinstance(s, dict) or not s.get("urls"):
            continue
        item: dict[str, Any] = {"urls": s["urls"]}
        if s.get("secret"):
            user = f"{int((time.time() if now is None else now) + int(s.get('ttl_s') or T['turn_ttl_s']))}:{T['turn_user']}"
            item["username"] = user
            item["credential"] = base64.b64encode(hmac.new(str(s["secret"]).encode(), user.encode(), hashlib.sha1).digest()).decode()
        elif s.get("username"):
            item["username"], item["credential"] = s["username"], str(s.get("credential") or "")
        out.append(item)
    return out


def register_ice(hass: HomeAssistant, block: dict[str, Any]) -> Any:
    """Hands the servers to HA's WebRTC (camera/webrtc/get_client_config gives them to the card); the remover, or None."""
    if not block.get("ice_servers"):
        return None
    try:
        from homeassistant.components.web_rtc import async_register_ice_servers  # noqa: PLC0415
        from webrtc_models import RTCIceServer  # noqa: PLC0415

        return async_register_ice_servers(hass, lambda: [RTCIceServer(**s) for s in ice_servers(block)])
    except (ImportError, KeyError) as err:  # no WebRTC in this HA: the talk stays on the LAN
        _LOGGER.debug("Hauflo intercom: no WebRTC to hand the ICE servers to (%s)", err)
        return None


# --- the wizard's endpoint -------------------------------------------------------------------------------------------


async def adapters(hass: HomeAssistant) -> list[str]:
    try:
        from homeassistant.components import network  # noqa: PLC0415

        return [str(a["address"]) for ad in await network.async_get_adapters(hass) for a in ad["ipv4"]]
    except Exception:  # noqa: BLE001 — no list: «auto» or an address typed by hand
        return []


class IntercomView(HomeAssistantView):
    """GET (admin) → {features, presets, config (passwords masked), listen, adapters, doors: {id: state}};
    POST (admin) {bind?, port?, public?, doors: [...], ice_servers?} → saved into the options (the entry reloads);
    400 {code, field, door} when something is wrong."""

    url = "/api/hauflo/intercom"
    name = "api:hauflo:intercom"
    requires_auth = True

    def __init__(self, find: Any) -> None:
        self._find = find  # → the running Intercom or None

    def _entry(self, hass: HomeAssistant) -> Any:
        return next(iter(hass.config_entries.async_loaded_entries(DOMAIN)), None)

    async def get(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        hass = request.app[KEY_HASS]
        entry = self._entry(hass)
        if entry is None:
            return self.json_message("not set up", 404)
        mgr = self._find()
        doors = {}
        if mgr is not None:
            for door_id in mgr.doors:
                eid = mgr.entity_of(door_id, "state")
                st = hass.states.get(eid) if eid else None
                doors[door_id] = {"entity_id": eid, "state": st.state if st else None, "problem": st.attributes.get("problem") if st else None}
        return self.json(
            {
                "features": [FEATURE],
                "presets": presets(),
                "modes": list(MODES),
                "openers": list(OPENERS),
                "config": masked(dict(entry.options.get(CONF_INTERCOM) or {})),
                "listen": mgr.listen_address() if mgr is not None else None,
                "adapters": await adapters(hass),
                "doors": doors,
            }
        )

    async def post(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        hass = request.app[KEY_HASS]
        entry = self._entry(hass)
        if entry is None:
            return self.json_message("not set up", 404)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict) or set(body) - {"bind", "port", "public", "doors", "ice_servers"}:
            return self.json_message("bind / port / public / doors / ice_servers expected", 400)
        try:
            value = normalize(body, dict(entry.options.get(CONF_INTERCOM) or {}))
        except SetupError as err:
            return self.json({"code": err.code, "field": err.field, "door": err.door}, status_code=400)
        hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_INTERCOM: value})
        return self.json({"ok": True, "config": masked(value)})
