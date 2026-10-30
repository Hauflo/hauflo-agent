"""Hauflo agent: one outbound WebSocket from this Home Assistant to Hauflo.

Over it Hauflo deploys (its frames are relayed to this HA's own WebSocket and REST API, signed in
as a system user the agent owns) and receives this object's status every minute: HA version, the
KNX link, unavailable KNX entities and the signals the integrator marked «Hauflo мониторинг».
Nothing listens on the network here; the secret only identifies this agent to Hauflo.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import contextlib
import ipaddress
import json
import logging
import random
import re
import time
from urllib.parse import urlparse
from typing import Any

import aiohttp

from homeassistant.auth.const import GROUP_ID_ADMIN
from homeassistant.components import persistent_notification
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, __version__ as HA_VERSION
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er, label_registry as lr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from . import automations, backup, dashmerge, guest, house, issues, keep, notify, ownership, presence, ranges, retired, rules, scenes, season, service, sidebar, theme, titles, voice
from . import order as block_order
from .issues import DEGRADED, degrade  # noqa: F401 — DEGRADED: diagnostics read it here
from .storage import HaufloStore
from .const import DATA_CARDS_LOCAL, CONF_REFRESH_TOKEN_ID, CONF_SECRET, CONF_SERVER, CONF_USER_ID, DATA_NOTIFY_VIEWS, DATA_UNLINKED, DOMAIN, MONITOR_LABEL, MONITOR_LABEL_EN, MONITOR_LABELS, OWNED_STORE, STATUS_EVERY, VERSION, CONF_RANGES, CONF_ALLOW_CHANGES, LOCKED_CODE
from .tuning import TUNING
from .tunnel import TUNNEL

_LOGGER = logging.getLogger(__name__)
PROBLEM_ON = {"problem", "safety", "smoke", "gas", "moisture", "battery", "heat", "cold", "carbon_monoxide", "tamper", "vibration"}


async def _local_token(hass: HomeAssistant, entry: ConfigEntry):
    """The refresh token of the agent's own system user (made once, kept with the entry). Access tokens made from
    it live 30 minutes — a fresh one for every request (HA core review 25.10: the tunnel lost auth after 30 min)."""
    user = None
    if uid := entry.data.get(CONF_USER_ID):
        user = await hass.auth.async_get_user(uid)
    if user is None:
        user = await hass.auth.async_create_system_user("Hauflo agent", group_ids=[GROUP_ID_ADMIN])
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_USER_ID: user.id, CONF_REFRESH_TOKEN_ID: None})
    refresh = None
    if rid := entry.data.get(CONF_REFRESH_TOKEN_ID):
        refresh = hass.auth.async_get_refresh_token(rid)
    if refresh is None:
        refresh = await hass.auth.async_create_refresh_token(user)
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_REFRESH_TOKEN_ID: refresh.id})
    return refresh


# What Hauflo may do in this HA through the tunnel — exactly what its deploy and status use, nothing by prefix
# (security review 25.10: «config/» let an automation with any service through, «lovelace/» a JS resource):
# registries, the KNX UI, dashboards, voice exposure, HomeKit / Alice / group / template flows, its own scenes, status
# reads. The lists are tunnel.json — the one source the backend's tests check their calls against (tester 30.10: the
# rules' «template» flow passed the backend's fake HA and was refused here, «403 Hauflo agent: not allowed»).
_TW = TUNNEL["ws"]
_TH = TUNNEL["http"]
_TF = TUNNEL["flows"]
_WS_EXACT = set(_TW["commands"])
_SERVICES = {(d, s) for d, s in _TW["services"]}
# What only reads (or refreshes) — every other allowed command changes the house and needs a fresh backup first
# (security expert 25.10: the backup was the cloud's promise, not the agent's).
_WS_READS = set(_TW["reads"])
_READ_SERVICES = {(d, s) for d, s in _TW["read_services"]}
_CREATES = {"config/area_registry/create": "area", "config/floor_registry/create": "floor", "config/label_registry/create": "label"}


def _ws_changes(msg: dict[str, Any]) -> bool:
    t = msg.get("type")
    if t == "call_service":
        return (str(msg.get("domain")), str(msg.get("service"))) not in _READ_SERVICES
    return t not in _WS_READS and t != "auth"


def _http_changes(method: str, path: str) -> bool:
    bare = path.split("?")[0]
    return method in ("POST", "DELETE") and (bare.startswith("/api/config/") or bare == "/api/hauflo/sidebar")


# The owner closed the house in HA (options: «Allow Hauflo to change this home's setup» off): every write is refused,
# fail closed — over the websocket all but reads (_ws_changes), over REST all but GET. Only the alarm delivery stays:
# its rules and a test push configure who is told, not the house (owner 28.10: monitoring and alarms keep working).
# Accepting an alarm («Принято» from Hauflo's monitoring, service review r2 D8) doesn't change the house either.
_LOCKED_HTTP_OK = {(p, m) for p, m in _TH["locked_ok"]}


def _ws_locked(msg: dict[str, Any]) -> bool:
    return _ws_changes(msg)


# A POST that only reads: a value rule's dry run (rules.py «preview») — the body the lock reads is the one sent on.
_LOCKED_READ_OPS = {(r["path"], r["method"]): set(r["ops"]) for r in _TH["locked_read_ops"]}


def _http_locked(method: str, path: str, body: Any = None) -> bool:
    bare = path.split("?")[0]
    if method == "GET" or (bare, method) in _LOCKED_HTTP_OK:
        return False
    if (ops := _LOCKED_READ_OPS.get((bare, method))) is not None:
        try:
            data = json.loads(body) if isinstance(body, (str, bytes)) and body else body
        except ValueError:
            return True
        return not (isinstance(data, dict) and data.get("op") in ops)
    return True
# Integrations Hauflo sets up (flows) or tunes (options) — the options and the delete only of an entry it set up itself
# (owned_only); the entries whose answer it tracks as its own; the ones whose diagnostics it reads.
_FLOW_DOMAINS = set(_TF["setup"])
_OPTIONS_DOMAINS = set(_TF["options"])
_OWNED_ONLY = set(_TF["owned_only"])
_DELETE_DOMAINS = set(_TF["delete"])
_TRACKED_DOMAINS = set(_TF["tracked"])
_DIAG_DOMAINS = set(_TF["diagnostics"])
# Platforms whose entities are Hauflo's only through the config entry it set up (its groups, its rules' sensors).
_ENTRY_PLATFORMS = set(_TF["entities_by_entry"])
# config_entries/get: only the integrations Hauflo sets up — not every title in the house («Анин iPhone»; panel round
# 6); of «template» only the entries it made (the integrator's own template helpers aren't the cloud's to see).
_ENTRY_DOMAINS = set(_TW["config_entries"])
_ENTRY_OWNED_ONLY = set(_TW["config_entries_owned_only"])
_MANIFESTS = set(_TW["manifests"])
# REST: a path by its route (a {placeholder} is one non-empty segment) and method; every route has its own checks below.
_ROUTES = [(r["id"], r["path"].split("/"), set(r["methods"])) for r in _TH["routes"]]
# The agent's own views, each with its methods — not whatever a future view may be (security round 4). Among them:
# «Принято» from Hauflo's «Мониторинг» (service review r2 D8); «Создано в этом доме» and the service passport's hand
# edits (ownership, r2 D5); /ranges POST only ASKS — the owner's «Allow / Decline» in Repairs grants; «Правила по
# значениям» check every target and limit themselves (rules.py); the season's settings only pick among the climates
# HA says can heat and cool (season.py).
_HAUFLO_VIEWS = {(r["path"], m) for r in _TH["routes"] if r["id"] == "hauflo" for m in r["methods"]}
_MAX_CHANNELS = 8


# What a registry edit may touch: Hauflo's own — KNX entities/devices, its light/cover groups and scenes
# (security re-review 25.10: the allowlist limited commands, not what they change).
_OWN_PLATFORMS = {"knx", "group", DOMAIN}
_OWN_DEVICE_DOMAINS = {"knx", "group"}
# Never handed to a voice assistant, never in a scene: what opens the house or stops protection.
_NEVER_EXPOSE = ("lock.", "alarm_control_panel.", "valve.")
_NEVER_DOMAINS = {"lock", "alarm_control_panel", "valve"}
_SCENE_DOMAINS = {"light", "cover", "switch", "climate", "fan", "media_player"}


def _own_entity(hass: HomeAssistant | None, entity_id: Any, owned_entries: set[str] | None = None) -> bool:
    """Hauflo's own: KNX / its agent's entities, a group or a rule sensor it set up through the tunnel, a scene it wrote (hauflo_…) —
    never the integrator's own group or template helper or scene (security re-review round 5)."""
    if hass is None or not isinstance(entity_id, str):
        return False
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return False
    if entry.platform in _ENTRY_PLATFORMS:
        return owned_entries is not None and entry.config_entry_id in owned_entries
    if entry.platform == "homeassistant" and entry.domain == "scene":
        return str(entry.unique_id).startswith("hauflo_")
    return entry.platform in _OWN_PLATFORMS


_PROTECTED_COVERS = {"garage", "gate", "door"}


def _protected(hass: HomeAssistant | None, entity_id: Any, depth: int = 0) -> bool:
    """What opens the house or stops its protection: a lock, the alarm, a valve, a garage door / gate / door cover —
    never in a voice assistant, a scene or a bridge, whatever path asks (security re-review round 5); nor a group with
    one of them inside (panel round 6)."""
    if not isinstance(entity_id, str):
        return True
    if entity_id.startswith(_NEVER_EXPOSE):
        return True
    st = hass.states.get(entity_id) if hass is not None and hasattr(hass, "states") else None
    members = st.attributes.get("entity_id") if st is not None else None
    if isinstance(members, list) and members and depth < 3 and any(_protected(hass, m, depth + 1) for m in members):
        return True
    if not entity_id.startswith("cover.") or hass is None:
        return False
    reg = er.async_get(hass).async_get(entity_id)
    classes = {st.attributes.get("device_class") if st is not None else None, getattr(reg, "device_class", None), getattr(reg, "original_device_class", None)}
    return bool(classes & _PROTECTED_COVERS)


def _own_device(hass: HomeAssistant | None, device_id: Any) -> bool:
    if hass is None or not isinstance(device_id, str):
        return False
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return False
    domains = {e.domain for eid in device.config_entries if (e := hass.config_entries.async_get_entry(eid))}
    return bool(domains) and domains <= _OWN_DEVICE_DOMAINS


# What never leaves this house through get_states (security re-review round 4: camera tokens, GPS, media): Hauflo
# needs ids, names, classes and modes — not where people are or a camera's access token.
_PRIVATE_DOMAINS = {"person", "device_tracker", "camera", "zone", "calendar", "media_player", "image", "image_processing", "todo"}
_PRIVATE_KEYS = {"access_token", "entity_picture", "latitude", "longitude", "gps_accuracy", "altitude", "course", "speed", "vertical_accuracy", "user_id", "device_trackers", "source", "token", "location", "message", "description"}
_KEEP_ON_PRIVATE = {"friendly_name", "device_class", "icon", "supported_features"}


# HA's config without where the house is (integrator run 19: latitude/longitude, location name, paths left).
_CONFIG_KEEP = {"version", "components", "time_zone", "language", "unit_system", "state", "recovery_mode", "safe_mode", "currency",
                "external_url"}  # the address the owner made public — the deploy result links the dashboard there (E6); the LAN one stays home


def _scrub_config(cfg: Any) -> Any:
    if not isinstance(cfg, dict):
        return cfg
    out = {k: v for k, v in cfg.items() if k in _CONFIG_KEEP}
    # Only scheme://host[:port][/path]: a login and password in it (HA accepts https://user:pass@…) stay home (round 3, C3).
    url = out.get("external_url")
    if url is not None:
        m = re.match(r"^(https?://)(?:[^@/?#]*@)?([^/?#@\s]+)(/[^?#@\s]*)?", url) if isinstance(url, str) else None
        if m:
            out["external_url"] = m.group(1) + m.group(2) + (m.group(3) or "")
        else:
            out.pop("external_url")
    return out


# Attributes Hauflo may see — an allowlist (security re-review round 5: a denylist let an iPhone's geocoded address,
# «Postal Code» and the sun's times through).
_STATE_ATTRS = {
    "friendly_name", "device_class", "state_class", "unit_of_measurement", "icon", "supported_features", "hvac_modes", "hvac_action",
    "min_temp", "max_temp", "target_temp_step", "temperature", "current_temperature", "target_temp_high", "target_temp_low",
    "preset_modes", "preset_mode", "fan_modes", "fan_mode", "swing_modes", "swing_mode", "current_position", "current_tilt_position",
    "brightness", "color_mode", "supported_color_modes", "color_temp_kelvin", "min_color_temp_kelvin", "max_color_temp_kelvin",
    "percentage", "percentage_step", "options", "min", "max", "step", "mode", "humidity", "current_humidity", "entity_id", "restored",
}
# Whose state itself is private: where people are, how many are home, what they watch, the house's place (sun times).
_PRIVATE_STATE = {"person", "device_tracker", "zone", "sun", "weather", "camera", "calendar", "media_player", "image", "image_processing", "todo", "notify", "tts", "stt", "conversation", "alarm_control_panel", "lock"}
# Whose sensors are private too: phones, and the sun's times (dawn / noon / dusk give the house's place — security
# expert 25.10).
_PRIVATE_PLATFORMS = {"mobile_app", "sun"}
_STATE_TIMES = ("context", "last_changed", "last_updated", "last_reported")
# Reads whose answer the agent edits before it leaves the house.
_SCRUBBED = (
    "get_states", "get_config", "cloud/status", "config/device_registry/list", "knx/group_monitor_info", "get_panels",
    "config/entity_registry/list", "config/entity_registry/get", "config/entity_registry/get_entries",
)
# The phones' entities never leave at all (HA reviewer r2: entity_registry/list carried «sensor.ivans_iphone_battery»
# and its name while the README said phone names are removed): their ids are the phones' names. Hauflo needs none of
# them — alarms reach phones through the agent's notify engine, in the house.
_PHONE_PLATFORMS = {"mobile_app"}


def _phone_ids(hass: HomeAssistant | None) -> set[str]:
    if hass is None:
        return set()
    with contextlib.suppress(Exception):
        return {e.entity_id for e in er.async_get(hass).entities.values() if e.platform in _PHONE_PLATFORMS}
    return set()


def _scrub_entities(result: Any, hass: HomeAssistant | None) -> Any:
    """config/entity_registry/list, /get, /get_entries without the phones' entities (as the device list without their
    names)."""
    phones = _phone_ids(hass)

    def phone(e: Any) -> bool:
        return isinstance(e, dict) and (e.get("platform") in _PHONE_PLATFORMS or e.get("entity_id") in phones)

    if isinstance(result, list):
        return [e for e in result if not phone(e)]
    if isinstance(result, dict) and "entity_id" in result:
        return None if phone(result) else result
    if isinstance(result, dict):  # get_entries: entity_id → entry (or null)
        return {k: v for k, v in result.items() if k not in phones and not phone(v)}
    return result


def _scrub_panels(panels: Any) -> Any:
    """Only HA's own menu items «Убрать лишнее из меню» may hide, by name (the deploy says which this HA has) — no
    other panel's name (an add-on, an iframe: what runs in the house) or config leaves it (security review 28.10)."""
    return {str(k): {"url_path": str(k)} for k in panels if k in sidebar.MENU_EXTRAS} if isinstance(panels, dict) else {}


# Binary sensors that tell who is home and where they move (security review 28.10: motion / doors with their times went
# to the cloud): their state is Hauflo's to see only on its own entities or ones marked «Hauflo мониторинг».
_PRESENCE_CLASSES = {"motion", "occupancy", "presence", "door", "window", "opening", "garage_door", "lock"}


def _scrub_states(states: Any, hass: HomeAssistant | None = None, visible: Callable[[str], bool] | None = None) -> Any:
    if not isinstance(states, list):
        return states
    phones = set()
    if hass is not None:
        with contextlib.suppress(Exception):
            phones = {e.entity_id for e in er.async_get(hass).entities.values() if e.platform in _PRIVATE_PLATFORMS}
    gone = _phone_ids(hass)
    states[:] = [st for st in states if not (isinstance(st, dict) and st.get("entity_id") in gone)]
    for st in states:
        if not isinstance(st, dict):
            continue
        eid = str(st.get("entity_id", ""))
        attrs = st.get("attributes") if isinstance(st.get("attributes"), dict) else {}
        presence = eid.startswith("binary_sensor.") and attrs.get("device_class") in _PRESENCE_CLASSES
        if presence and visible is not None:
            try:
                presence = not visible(eid)
            except Exception:  # noqa: BLE001 — not knowable: hidden
                presence = True
        if eid.split(".")[0] in _PRIVATE_STATE or eid in phones or presence:
            st["attributes"] = {k: v for k, v in attrs.items() if k in _KEEP_ON_PRIVATE}
            st["state"] = "hidden"
            for k in _STATE_TIMES:  # when someone came home, and who did what
                st.pop(k, None)
        else:
            st["attributes"] = {k: v for k, v in attrs.items() if k in _STATE_ATTRS}
    return states


def _scrub_cloud(status: Any) -> Any:
    """Nabu Casa's status without the account, the house's remote address, Google's PIN for locks or the cloudhooks
    (security re-review round 5) — Hauflo needs whether it's on and which assistants are."""
    if not isinstance(status, dict):
        return status
    prefs = status.get("prefs") if isinstance(status.get("prefs"), dict) else {}
    return {"logged_in": status.get("logged_in"), "cloud": status.get("cloud"), "prefs": {k: prefs.get(k) for k in ("alexa_enabled", "google_enabled") if k in prefs}}


def _owned_knx(hass: HomeAssistant | None, entity_id: Any, owned: set[str] | None) -> bool:
    """A KNX UI entity Hauflo created or took over (its unique_id is in the agent's store) — never one the
    integrator made by hand (security re-review round 3: knx/delete_entity reached any KNX entity)."""
    if hass is None or owned is None or not isinstance(entity_id, str):
        return False
    entry = er.async_get(hass).async_get(entity_id)
    return entry is not None and entry.platform == "knx" and entry.unique_id in owned


def _ga_key(value: Any) -> str | None:
    """One spelling per group address (integrator run 22: «7/1793», «16129», 16129 and «07/07/001» all reached the
    gate relay's 7/7/1). Three-level, two-level, free and numeric forms → the raw number; an internal address stays
    itself; anything else → None (refused)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if 0 <= value <= 0xFFFF else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("i-") or text.startswith("i_"):
        return text
    parts = text.split("/")
    try:
        nums = [int(x, 10) for x in parts]
    except ValueError:
        return None
    if any(n < 0 for n in nums):
        return None
    if len(nums) == 3 and nums[0] <= 31 and nums[1] <= 7 and nums[2] <= 255:
        return str((nums[0] << 11) | (nums[1] << 8) | nums[2])
    if len(nums) == 2 and nums[0] <= 31 and nums[1] <= 2047:
        return str((nums[0] << 11) | nums[1])
    if len(nums) == 1 and nums[0] <= 0xFFFF:
        return str(nums[0])
    return None


def _ga_range(key: str) -> str | None:
    """A group address key (its raw number) → «main/middle»."""
    try:
        raw = int(key)
    except (TypeError, ValueError):
        return None
    return f"{raw >> 11}/{(raw >> 8) & 7}"


def _ga_text(key: str) -> str | None:
    """A group address key (its raw number) → «main/middle/sub»."""
    try:
        raw = int(key)
    except (TypeError, ValueError):
        return None
    return f"{raw >> 11}/{(raw >> 8) & 7}/{raw & 0xFF}"


_ALLOWED_ITEM = re.compile(r"(\d{1,2})/(\d)(?:/(\d{1,3})(?:-(\d{1,3}))?)?")


def _range_allows(allowed: set[str], key: str) -> bool:
    """Is this group address (its raw number) in what the integrator allowed: a main/middle group «1/1», an address
    «1/1/5» or a span «1/1/1-21» (KNX review 27.10: whole middle groups let Hauflo write commands it never makes)."""
    try:
        raw = int(key)
    except (TypeError, ValueError):
        return False
    main, middle, sub = raw >> 11, (raw >> 8) & 7, raw & 0xFF
    for item in allowed:
        m = _ALLOWED_ITEM.fullmatch(str(item).strip())
        if not m or int(m.group(1)) != main or int(m.group(2)) != middle:
            continue
        if m.group(3) is None:
            return True
        lo = int(m.group(3))
        hi = int(m.group(4)) if m.group(4) is not None else lo
        if lo <= sub <= hi:
            return True
    return False


def _knx_writes(value: Any) -> set[str]:
    """Every group address a KNX UI config sends to («write»), however deep and however spelled; one that can't be
    read is «?» — matches nothing of the integrator's, so the caller refuses it separately."""
    out: set[str] = set()
    if isinstance(value, dict):
        if "write" in value and value["write"] not in (None, ""):
            out.add(_ga_key(value["write"]) or "?")
        for v in value.values():
            out |= _knx_writes(v)
    elif isinstance(value, list):
        for v in value:
            out |= _knx_writes(v)
    return out


def _knx_listens(value: Any) -> set[str]:
    """Every group address a KNX UI config sends to or listens on (write, state, passive), however deep."""
    out: set[str] = set()
    if isinstance(value, dict):
        for key in ("write", "state"):
            if value.get(key) not in (None, ""):
                out.add(_ga_key(value[key]) or "?")
        passive = value.get("passive")
        for ga in passive if isinstance(passive, list) else []:
            out.add(_ga_key(ga) or "?")
        for v in value.values():
            out |= _knx_listens(v)
    elif isinstance(value, list):
        for v in value:
            out |= _knx_listens(v)
    return out


# The KNX integration's runtime object is HA's private API (HA core + SRE review 29.10): read in one place — by
# KNX_MODULE_KEY, else the plain «knx» key of older HA — and a changed shape degrades the feature, never the agent.
_KNX_KEY = []  # type: list[Any] — KNX_MODULE_KEY once resolved, or «knx» when HA has none to import


def knx_data(hass: HomeAssistant | None) -> Any | None:
    """The running KNX integration (KNXModule), None without KNX."""
    data = getattr(hass, "data", None)
    if data is None:
        return None
    if not _KNX_KEY:
        try:
            from homeassistant.components.knx.const import KNX_MODULE_KEY  # noqa: PLC0415

            _KNX_KEY.append(KNX_MODULE_KEY)
        except Exception:  # noqa: BLE001 — no xknx installed / an older HA: the plain key
            _KNX_KEY.append("knx")
    try:
        return data.get(_KNX_KEY[0]) or data.get("knx")
    except Exception as err:  # noqa: BLE001
        degrade("knx integration data", err)
        return None


def _knx_store(hass: HomeAssistant | None) -> dict[str, Any] | None:
    """The KNX UI entity store ({platform: {unique id: config}}); None without KNX or when its shape changed."""
    knx = knx_data(hass)
    if knx is None:
        return None
    try:
        store = knx.config_store.data["entities"]
        if not isinstance(store, dict):
            raise TypeError(f"entities is {type(store).__name__}")
        return store
    except Exception as err:  # noqa: BLE001
        degrade("knx entity store", err)
        return None


def _foreign_knx_writes(hass: HomeAssistant | None, owned: set[str] | None, skip: str | None = None) -> set[str] | None:
    """The addresses the integrator's own KNX UI entities send to or listen on — Hauflo's never send there (panel
    round 6: a Siri-exposed «Kitchen light» rewired to the gate relay's address; integrator run 23: to its passive
    7/7/5, which the same actuator obeys). None when it can't be known."""
    store = _knx_store(hass)
    if store is None:  # no KNX yet: nothing of the integrator's; KNX there but unreadable: not known
        return set() if hass is not None and knx_data(hass) is None else None
    out: set[str] = set()
    for configs in store.values():
        for uid, config in configs.items():
            if uid != skip and (owned is None or uid not in owned):
                out |= _knx_listens(config.get("knx") if isinstance(config, dict) else None)
    # Every running KNX entity by its addresses, the integrator's YAML ones too (integrator run 23b: a «Калитка» in
    # configuration.yaml isn't in the UI store — a Hauflo switch rewired to its 7/6/1 opened it).
    for ga, ids in (getattr(knx_data(hass), "group_address_entities", None) or {}).items():
        if any(getattr(i, "unique_id", None) != skip and (owned is None or getattr(i, "unique_id", None) not in owned) for i in ids):
            raw = getattr(ga, "raw", None)
            key = str(raw) if isinstance(raw, int) else _ga_key(str(ga))
            if key:
                out.add(key)
    # Everything else the running KNX sends to or listens on — exposures (UI, YAML, service), the time server, any
    # device without a UI entity (HA expert 25.10: an expose sends to the bus but was in neither list): every
    # address xknx knows, less those only Hauflo's own entities use.
    try:
        mine: set[str] = set()
        for configs in store.values():
            for uid, config in configs.items():
                if owned is not None and uid in owned:
                    mine |= _knx_listens(config.get("knx") if isinstance(config, dict) else None)
        for device in list(knx_data(hass).xknx.devices):  # type: ignore[union-attr]
            for ga in device.group_addresses():
                raw = getattr(ga, "raw", None)
                key = str(raw) if isinstance(raw, int) else _ga_key(str(ga))
                if key and key not in mine:
                    out.add(key)
    except Exception:  # noqa: BLE001 — xknx not reachable: the lists above still hold
        _LOGGER.debug("Hauflo: KNX devices not read", exc_info=True)
    out.discard("?")
    return out


def _foreign_holders(hass: HomeAssistant | None, owned: set[str] | None, skip: str | None = None) -> dict[str, list[tuple[str | None, int | None]]] | None:
    """Per address, who else listens there: (platform, scene number if known). An address only xknx knows (an
    exposure, the time server) gets a holder of no platform — never shared."""
    store = _knx_store(hass)
    if store is None:
        return None
    gae = getattr(knx_data(hass), "group_address_entities", None) or {}
    out: dict[str, list[tuple[str | None, int | None]]] = {}
    mine: set[str] = set()
    for platform, configs in store.items():
        for uid, config in configs.items():
            knx = config.get("knx") if isinstance(config, dict) else None
            gas = _knx_listens(knx)
            if uid == skip or (owned is not None and uid in owned):
                mine |= gas
                continue
            num = knx.get("scene_number") if isinstance(knx, dict) else None
            for ga in gas - {"?"}:
                out.setdefault(ga, []).append((platform, num if isinstance(num, int) else None))
    for ga, ids in gae.items():
        raw = getattr(ga, "raw", None)
        key = str(raw) if isinstance(raw, int) else _ga_key(str(ga))
        for i in ids:
            if getattr(i, "ui", False) or getattr(i, "unique_id", None) == skip or (owned is not None and getattr(i, "unique_id", None) in owned):
                continue
            if key:
                out.setdefault(key, []).append((getattr(i, "platform", None), None))
    with contextlib.suppress(Exception):
        for device in list(knx_data(hass).xknx.devices):  # type: ignore[union-attr]
            for ga in device.group_addresses():
                raw = getattr(ga, "raw", None)
                key = str(raw) if isinstance(raw, int) else _ga_key(str(ga))
                if key and key not in mine and key not in out:
                    out[key] = [(None, None)]
    return out


_SCENE_EID = re.compile(r"scene\.[a-z0-9_]{1,128}")


def _knx_scenes_owned(hass: HomeAssistant | None, owned: set[str] | None) -> dict[str, tuple[str, str, int]]:
    """Hauflo's own KNX UI scenes: entity id → (unique id, scene address key, scene number 1–64), read from the KNX
    integration's own store — what a scene's «Запомнить как сейчас» on the panel may reach (Д19, 28.10). A scene the
    integrator made by hand, a YAML one, one with an unreadable address or number: not here."""
    if hass is None or not owned:
        return {}
    configs = (_knx_store(hass) or {}).get("scene") or {}
    if not configs or not isinstance(configs, dict):  # no KNX, no scenes: none
        return {}
    reg = er.async_get(hass)
    out: dict[str, tuple[str, str, int]] = {}
    for uid, config in configs.items():
        if uid not in owned or not isinstance(config, dict) or not isinstance(config.get("knx"), dict):
            continue
        knx = config["knx"]
        ga = knx.get("ga_scene")
        key = _ga_key(ga.get("write")) if isinstance(ga, dict) else None
        number = knx.get("scene_number")
        if key is None or not key.isdigit() or isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 64:
            continue
        eid = reg.async_get_entity_id("scene", "knx", uid)
        if eid and _SCENE_EID.fullmatch(eid):
            out[eid] = (uid, key, number)
    return out


def _knx_scene_target(hass: HomeAssistant | None, owned: set[str] | None, allowed: set[str], entity_id: Any) -> tuple[str, int]:
    """Where a KNX scene's learn telegram goes: (address «main/middle/sub», scene number). Only from the agent's own
    records — the browser names a scene entity, never an address or a value (security review 28.10, Д19). Refused
    (PermissionError): not a scene entity, not Hauflo's own KNX scene, its address outside what the owner allowed in
    HA, or anything but a scene listens there (an actuator's switch object on the same address would take the byte
    as a command)."""
    if not isinstance(entity_id, str) or not _SCENE_EID.fullmatch(entity_id):
        raise PermissionError("not a scene")
    found = _knx_scenes_owned(hass, owned).get(entity_id)
    if found is None:
        raise PermissionError("not a KNX scene of Hauflo's")
    uid, key, number = found
    if not _range_allows(allowed, key):
        raise PermissionError("its address isn't allowed in Home Assistant")
    holders = _foreign_holders(hass, None, uid)
    if holders is None or any(p != "scene" for p, _n in holders.get(key, [])):
        raise PermissionError("not only scenes listen on its address")
    text = _ga_text(key)
    if text is None:
        raise PermissionError("address")
    return text, number


def _knx_scenes_learnable(hass: HomeAssistant | None, owned: set[str] | None, allowed: set[str]) -> dict[str, int]:
    """The KNX scenes the panel offers «Запомнить как сейчас» / «Переименовать» on: entity id → scene number."""
    out: dict[str, int] = {}
    for eid in _knx_scenes_owned(hass, owned):
        with contextlib.suppress(PermissionError):
            out[eid] = _knx_scene_target(hass, owned, allowed, eid)[1]
    return out


def _knx_config_ok(hass: HomeAssistant | None, msg: dict[str, Any], owned: set[str] | None) -> bool:
    data = msg.get("data")
    writes = _knx_writes(data.get("knx") if isinstance(data, dict) else None)
    if not writes:
        return True
    uid = None
    if msg.get("type") == "knx/update_entity" and hass is not None:
        entry = er.async_get(hass).async_get(msg.get("entity_id"))
        uid = entry.unique_id if entry is not None else None
    if "?" in writes:
        return False
    foreign = _foreign_knx_writes(hass, owned, uid)
    if foreign is None:
        return False
    clash = writes & foreign
    if not clash:
        return True
    # By the one classifier the deploy plan uses too (K4): a KNX scene on a room's scene address only the integrator's
    # scenes of other numbers use is how KNX scenes work (KNX expert 25.10: the plan said «create», the agent refused);
    # anything else there — his entity, an exposure, the time server — is a write to his side: refused.
    knx = data.get("knx") if isinstance(data, dict) else None
    number = knx.get("scene_number") if isinstance(knx, dict) else None
    holders = _foreign_holders(hass, owned, uid)
    if holders is None:
        return False
    return all(_overlap_kind(msg.get("platform"), number, True, True, holders.get(ga) or [(None, None)]) != "foreign_write" for ga in clash)


def _overlap_kind(platform: Any, number: Any, write: bool, main: bool, holders: list) -> str | None:
    """How an address Hauflo's entity uses and others use too is classified — the same as the deploy plan's
    (backend/src/ha/overlap.ts); the contract cases are agent/tests/fixtures/overlap-cases.json. holders: (platform, scene
    number) of everyone else there, platform None for what only the bus side knows (an exposure, the time server)."""
    if not holders:
        return None
    no = number if isinstance(number, int) and not isinstance(number, bool) else None
    if write:
        return "scene_shared" if platform == "scene" and no is not None and all(p == "scene" and n != no for p, n in holders) else "foreign_write"
    if main and any(p == platform and (platform != "scene" or n == no) for p, n in holders):
        return "duplicate"
    return "shared_listen"


def _device_entities_owned(hass: HomeAssistant | None, device_id: Any, owned: set[str] | None) -> bool:
    if hass is None or owned is None or not isinstance(device_id, str):
        return False
    ents = er.async_entries_for_device(er.async_get(hass), device_id, include_disabled_entities=True)
    return all(e.platform != "knx" or e.unique_id in owned for e in ents)


def _owns(owned_entries: set[str] | None, kind: str, item_id: Any) -> bool:
    """A room / floor / label Hauflo created through the tunnel (kept with its config entries as «kind:id»)."""
    return owned_entries is not None and isinstance(item_id, str) and f"{kind}:{item_id}" in owned_entries


def _area_floor_empty(hass: HomeAssistant | None, area_id: Any) -> bool:
    if hass is None or not isinstance(area_id, str):
        return False
    try:
        from homeassistant.helpers import area_registry as ar  # noqa: PLC0415

        area = ar.async_get(hass).async_get_area(area_id)
    except Exception:  # noqa: BLE001 — not knowable: not allowed
        return False
    return area is not None and not area.floor_id


def _empty_area(hass: HomeAssistant | None, area_id: Any) -> bool:
    """No entity and no device in it — a room deleted there takes nothing with it."""
    if hass is None or not isinstance(area_id, str):
        return False
    from homeassistant.helpers import area_registry as ar  # noqa: PLC0415

    if ar.async_get(hass).async_get_area(area_id) is None:
        return False
    return not er.async_entries_for_area(er.async_get(hass), area_id) and not dr.async_entries_for_area(dr.async_get(hass), area_id)


def _unused_label(hass: HomeAssistant | None, label_id: Any) -> bool:
    if hass is None or not isinstance(label_id, str):
        return False
    if er.async_entries_for_label(er.async_get(hass), label_id):
        return False
    if dr.async_entries_for_label(dr.async_get(hass), label_id):
        return False
    from homeassistant.helpers import area_registry as ar  # noqa: PLC0415

    return not ar.async_entries_for_label(ar.async_get(hass), label_id)


def _forbidden(value: Any) -> bool:
    """A lock, the alarm or a valve anywhere in a flow's input — as an entity or a whole domain."""
    if isinstance(value, str):
        return value in _NEVER_DOMAINS or value.startswith(_NEVER_EXPOSE)
    if isinstance(value, dict):
        return any(_forbidden(k) or _forbidden(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_forbidden(v) for v in value)
    return False


_BENCH_NET = ipaddress.ip_network("198.18.0.0/15")
# KNX Secure (keys, passwords) stays the integrator's, set in HA by hand — never through the tunnel.
_KNX_SECURE_KEYS = ("user_id", "user_password", "device_authentication", "backbone_key", "sync_latency_tolerance", "knxkeys", "secure_")


# What of HA's KNX connection settings leaves for the service passport — nothing else (never user_password,
# device_authentication, backbone_key, knxkeys_*: _KNX_SECURE_KEYS).
_KNX_CONN_KEYS = {"connection_type": "type", "host": "host", "port": "port", "local_ip": "localIp", "route_back": "routeBack", "multicast_group": "multicastGroup", "multicast_port": "multicastPort", "individual_address": "individualAddress", "tunnel_endpoint_ia": "tunnelEndpoint"}


def knx_conn(data: Any) -> dict[str, Any] | None:
    """HA's KNX config entry data → the passport's connection facts (an allowlist; secure only as a yes/no).
    ConfigEntry.data is a read-only MappingProxyType, not a dict (coverage pass 29.10: the passport's KNX link was empty)."""
    if not isinstance(data, Mapping):
        return None
    out: dict[str, Any] = {}
    for key, name in _KNX_CONN_KEYS.items():
        v = data.get(key)
        if isinstance(v, bool) or (isinstance(v, int) and 0 <= v <= 65535):
            out[name] = v
        elif isinstance(v, str) and v:
            out[name] = v[:64]
    ctype = str(data.get("connection_type") or "")
    out["secure"] = ctype.endswith("_secure") or bool(data.get("knxkeys_filename"))
    return out


def ha_url(raw: Any) -> str | None:
    """An URL without its login, query or fragment («https://user:pw@x/a?b» → «https://x/a»)."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        u = urlparse(raw)
        port = f":{u.port}" if u.port else ""
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname:
        return None
    return f"{u.scheme}://{u.hostname}{port}{u.path.rstrip('/')}"[:200]


def _lan_host(value: Any) -> bool:
    """KNX talks to an interface in the house: a private / link-local address (or a gateway choice with one)."""
    if not isinstance(value, str) or not value:
        return value in (None, "")
    # A found gateway comes as «1.1.250 - Name @ 192.168.1.10:3671» (xknx GatewayDescriptor; KNX expert 25.10: the
    # wizard fell back to manual entry on every scanned gateway).
    host = value.rsplit("@", 1)[1].strip() if "@" in value else value
    host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if "." not in host and ":" not in host:
        return host in ("manual", "automatic", "auto")  # HA's choices; never a bare name the DNS search domain resolves
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    # IPv4 in the house only: not loopback / 0.0.0.0 / reserved / the benchmark net Python counts as private.
    if ip.version != 4 or ip.is_loopback or ip.is_unspecified or ip.is_reserved or ip.is_multicast or ip in _BENCH_NET:
        return False
    return ip.is_private or ip.is_link_local


_HK_FILTER_KEYS = {"include_domains", "include_entities", "exclude_domains", "exclude_entities", "domains", "entities", "mode", "include_exclude_mode", "filter"}


def _hauflo_entity(hass: HomeAssistant | None, eid: Any, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    """Hauflo's own and safe to hand on (a bridge, a voice assistant): a KNX one only if Hauflo created it, never
    a lock / the alarm / a valve / a garage door."""
    return not _protected(hass, eid) and _own_entity(hass, eid, owned_entries) and (_platform(hass, eid) != "knx" or _owned_knx(hass, eid, owned))


def _homekit_step_ok(hass: HomeAssistant | None, data: dict[str, Any], memo: dict[str, list[str]] | None, flow_id: str, step: str | None, owned: set[str] | None = None, owned_entries: set[str] | None = None) -> bool:
    """A HomeKit bridge only ever of the entities chosen, by HA's own current step (integrator run 20: an options step
    without «entities» left a domain unpicked and HomeKit took it whole — every cover, the garage door with them).
    Setup starts on «person» alone (a whole «switch» / «script» could be a gate's relay); the options name the domains,
    then an entity of each; the steps after them never touch the filter."""
    if step == "user":
        return data.get("include_domains") == ["person"] and set(data) <= {"include_domains"}
    if step == "pairing":
        return not data
    if step == "init":
        domains = data.get("domains")
        if data.get("mode") != "bridge" or data.get("include_exclude_mode") != "include" or not isinstance(domains, list) or not domains:
            return False
        if not all(isinstance(d, str) for d in domains) or memo is None:
            return False
        memo[flow_id] = list(domains)
        return True
    if step == "include":
        entities = data.get("entities")
        if memo is None or flow_id not in memo or not isinstance(entities, list) or not all(isinstance(e, str) for e in entities):
            return False
        picked = {e.split(".")[0] for e in entities}
        if any(d not in picked for d in memo.pop(flow_id)):
            return False
        # Integrator run 21: only Hauflo's own — the integrator's gate relay went onto a Hauflo bridge.
        if not all(_hauflo_entity(hass, e, owned, owned_entries) for e in entities):
            return False
        return set(data) <= {"entities"}
    if step in ("cameras", "climate", "bridged_device_triggers"):
        return not (set(data) & _HK_FILTER_KEYS)
    return False


def _flow_step(manager: Any, flow_id: str) -> str | None:
    try:
        return str(manager.async_get(flow_id).get("step_id"))
    except Exception:  # noqa: BLE001 — unknown flow: no step
        return None


# The sub-blocks' and the hints' rules (backend blockRulesDeploy.ts) are HA's own template binary sensors, made with the
# «template» helper's config flow. Security decision (30.10): a template is Jinja HA renders on every change of what
# it reads — through the tunnel it could read what the agent keeps from Hauflo (a person, a phone, a presence sensor:
# _scrub_states) and hand it back as an unscrubbed state, a «sensor» could carry the value whole, a «switch» / «button»
# helper runs actions (a gate's relay). So only what a rule is: a binary sensor (on = the warning is up), its name,
# its state template, the «problem» class — nothing else (no device, no actions, no availability). The template is
# read token by token against tunnel.json's grammar, the one ruleTemplate (backend deviceBlocks.ts) writes: states of
# entities, their last change / report, now(), comparisons and and / or / not — no filters, no {% %}, no loops, no
# expand() / area_entities() that would reach the rest of the house. Every entity it names is Hauflo's own (its KNX
# entities, groups, rule sensors) and not a private domain — what Hauflo already reads in get_states, so the sensor
# tells it nothing new; it never acts (HA's sandbox renders templates read-only). Options and the delete only of a
# template entry the agent saw made through the tunnel (_track_entry; _OWNED_ONLY), never the integrator's helpers;
# config_entries/get of «template» answers with those alone.
_TPL = _TF["template"]
_TPL_TOKENS = [(re.compile(t["re"]), t.get("entity")) for t in _TPL["tokens"]]
_TPL_MENU = set(_TPL["menu"])
_TPL_KEYS = {k: set(v) for k, v in _TPL["keys"].items()}
# HA's options form of a binary sensor helper has the template alone (its class is set once, at setup).
_TPL_OPTION_KEYS = {k: set(v) for k, v in _TPL["options_keys"].items()}
_TPL_CLASSES = set(_TPL["device_classes"])


def _template_entities(text: Any) -> list[str] | None:
    """The entities a rule's template reads; None — not a rule's template (any other Jinja, too long)."""
    if not isinstance(text, str) or not text.strip() or len(text) > _TPL["max_state"]:
        return None
    pos, ents = 0, []
    while pos < len(text):
        for rx, group in _TPL_TOKENS:
            m = rx.match(text, pos)
            if m is not None and m.end() > pos:
                if group:
                    ents.append(m.group(group))
                pos = m.end()
                break
        else:
            return None
    return ents


def _rule_reads_ok(hass: HomeAssistant | None, eid: str, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    """What a rule may read: Hauflo's own entity, not a private domain (where people are, a lock)."""
    return eid.split(".")[0] not in _PRIVATE_STATE and _own_entity(hass, eid, owned_entries) and (_platform(hass, eid) != "knx" or _owned_knx(hass, eid, owned))


def _template_step_ok(hass: HomeAssistant | None, data: dict[str, Any], step: str | None, owned: set[str] | None, owned_entries: set[str] | None, options: bool) -> bool:
    """A template helper's step, by HA's own current step: the menu picks a binary sensor; its form (setup or
    options) sets only the rule's name, template and class."""
    if step == "user" and not options:
        return set(data) == {"next_step_id"} and str(data.get("next_step_id")) in _TPL_MENU
    keys = (_TPL_OPTION_KEYS if options else _TPL_KEYS).get(str(step))
    if keys is None or not set(data) <= keys or any(k not in data for k in _TPL["required"]):
        return False
    if "device_class" in data and str(data["device_class"]) not in _TPL_CLASSES:
        return False
    if "name" in data and not (isinstance(data["name"], str) and 0 < len(data["name"]) <= _TPL["max_name"]):
        return False
    ents = _template_entities(data.get("state"))
    return ents is not None and all(_rule_reads_ok(hass, e, owned, owned_entries) for e in ents)


# A lamp outlet deployed as a switch, shown as a light (backend lampWrappers.ts, design B7): HA's «Switch as X» helper
# over it. Security decision (30.10): the helper operates whatever it wraps — over the integrator's gate relay or a
# lock it would hand them to every light group, scene and voice phrase as a «light». So only a light, only over a
# switch Hauflo made itself and may hand on (_hauflo_entity: its own KNX switch, never protected), with the keys HA's
# form has; options (invert) and the delete only of a helper the agent saw made through the tunnel (_OWNED_ONLY).
_SAX = _TF["switch_as_x"]
_SAX_KEYS = {k: set(v) for k, v in _SAX["keys"].items()}
_SAX_OPTION_KEYS = {k: set(v) for k, v in _SAX["options_keys"].items()}


def _switch_as_x_step_ok(hass: HomeAssistant | None, data: dict[str, Any], step: str | None, owned: set[str] | None, owned_entries: set[str] | None, options: bool) -> bool:
    """A «Switch as X» helper's step, by HA's own current step."""
    keys = (_SAX_OPTION_KEYS if options else _SAX_KEYS).get(str(step))
    if keys is None or not set(data) <= keys:
        return False
    if "invert" in data and not isinstance(data["invert"], bool):
        return False
    if options:
        return True
    if any(k not in data for k in _SAX["required"]) or str(data.get("target_domain")) not in _SAX["target_domains"]:
        return False
    eid = data.get("entity_id")
    return isinstance(eid, str) and eid.split(".", 1)[0] in _SAX["source_domains"] and _hauflo_entity(hass, eid, owned, owned_entries)


def _flow_input_ok(hass: HomeAssistant, domain: str | None, data: dict[str, Any], memo: dict[str, list[str]] | None = None, flow_id: str = "", step: str | None = None, owned: set[str] | None = None, owned_entries: set[str] | None = None, options: bool = False) -> bool:
    """What a flow step may set (security re-review round 3: only the handler was checked, not the input): never
    a lock / alarm / valve for a voice assistant or a group, HomeKit as a bridge of included entities, Alice by the
    label, KNX to an interface in the house and its telegram history kept in HA."""
    if _forbidden(data):
        return False
    if domain == "homekit":
        return _homekit_step_ok(hass, data, memo, flow_id, step, owned, owned_entries)
    if domain == "template":
        return _template_step_ok(hass, data, step, owned, owned_entries, options)
    if domain == "switch_as_x":
        return _switch_as_x_step_ok(hass, data, step, owned, owned_entries, options)
    if domain == "group" and "entities" in data:
        # A Hauflo group holds only Hauflo's own (panel round 6: the integrator's gate relay inside «All covers» went to
        # Siri with the group).
        ents = data.get("entities")
        return isinstance(ents, list) and all(_hauflo_entity(hass, e, owned, owned_entries) for e in ents)
    if domain == "yandex_smart_home":
        # The cloud link by a label, as Hauflo sets it up — no skill / cloud credentials of anyone's.
        if any(str(k).startswith(("skill", "cloud", "instance", "user_id", "token")) for k in data):
            return False
        if not (data.get("filter_source", "label") == "label" and data.get("connection_type", "cloud") == "cloud"):
            return False
        # The label by what it carries (security expert 25.10: any label — one the integrator's lock wears — handed the
        # lock to Alice): only Hauflo's own entities, nothing on a device or an area.
        return "label" not in data or _label_ok(hass, data.get("label"), owned, owned_entries)
    if domain == "knx":
        if step is not None and (step.startswith(("secure", "knxkeys")) or "postgres" in step):
            return False
        if any(str(k).startswith(_KNX_SECURE_KEYS) for k in data):
            return False
        # Telegram history stays in HA (sqlite) — never shipped to someone's Postgres (KNX expert 25.10).
        for k, v in data.items():
            if str(k) == "telegram_store_section":
                if not isinstance(v, dict) or v.get("telegram_db_backend", "sqlite") != "sqlite" or any("postgres" in str(x) for x in v):
                    return False
            elif str(k).startswith("telegram_"):
                return False
        # Options: a bounded send rate (0 = no limit) and a real on/off state updater.
        if "rate_limit" in data and not (isinstance(data["rate_limit"], int) and not isinstance(data["rate_limit"], bool) and 1 <= data["rate_limit"] <= 60):
            return False
        if "state_updater" in data and not isinstance(data["state_updater"], bool):
            return False
        if not all(_lan_host(data.get(k)) for k in ("host", "local_ip", "gateway")):
            return False
        mcast = data.get("multicast_group")
        if mcast not in (None, ""):
            try:
                return ipaddress.ip_address(str(mcast)).is_multicast
            except ValueError:
                return False
    return True


# A dashboard written through the tunnel (security expert 25.10: a saved «hauflo-…» dashboard held a «Kitchen light»
# button that opened the integrator's gate, made the start panel of everyone): only the cards Hauflo generates, only
# Hauflo's own entities to operate, only these actions and services.
_LOVELACE_TYPES = {
    "sections", "panel", "masonry", "grid", "tile", "heading", "horizontal-stack", "vertical-stack", "button",
    "picture-glance", "cover-open-close", "cover-position", "target-temperature", "light-brightness",
    "climate-preset-modes", "climate-hvac-modes", "climate-fan-modes", "numeric-input", "thermostat",
    "custom:hauflo-scenes", "custom:hauflo-alert", "custom:hauflo-room", "custom:hauflo-lights", "custom:hauflo-hero",
    "custom:hauflo-climate", "custom:hauflo-status", "custom:hauflo-devices", "custom:hauflo-sliders",
    "custom:hauflo-covers", "custom:hauflo-acs",
    # The «Автоматизации» tab (automations.py): no entity, no action — the card asks the agent itself.
    "custom:hauflo-automations",
    # An empty room's note (0.14.7): text only.
    "custom:hauflo-title",
    # What HA put in an area (0.14.7): only the area id comes from the cloud; the card reads HA and opens HA's own dialog.
    "custom:hauflo-area",
}
# What a card may only show: anyone's (weather on the floor, an alarm source of a monitoring rule).
_READ_ONLY_DOMAINS = {"sensor", "binary_sensor", "weather", "sun", "person", "zone", "device_tracker", "calendar", "image", "camera", "event"}
_ACTIONS = {"more-info", "toggle", "none", "navigate", "perform-action", "call-service"}
_CARD_SERVICES = {
    "homeassistant.toggle", "homeassistant.turn_on", "homeassistant.turn_off",
    "light.turn_on", "light.turn_off", "light.toggle", "switch.turn_on", "switch.turn_off", "switch.toggle",
    "cover.open_cover", "cover.close_cover", "cover.stop_cover", "cover.set_cover_position", "cover.toggle",
    "scene.turn_on", "fan.turn_on", "fan.turn_off", "fan.set_percentage",
    "climate.set_temperature", "climate.set_hvac_mode", "climate.set_preset_mode", "climate.set_fan_mode",
    "climate.set_swing_mode", "climate.turn_off", "knx.send",
}
_ENTITY_RE = re.compile(r"[a-z_]+\.[a-z0-9_]+")  # fullmatch only
# A view / section / card's Hauflo id (dashmerge.py): plain text, no control characters.
_HAUFLO_ID_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")
# A copy of a dashboard (dashmerge.py: hauflo-…-copy-<stamp>[-<random>]): never read or written through the tunnel.
_COPY_RE = re.compile(r"-copy-\d")
_NAV_RE = re.compile(r"^/(hauflo-[a-z0-9_-]+(/[a-z0-9_-]*)?|config/scene/edit/hauflo_[a-z0-9_]+)$")


def _owned_knx_writes(hass: HomeAssistant | None, owned: set[str] | None) -> set[str]:
    """The addresses Hauflo's own KNX entities send to (a KNX scene's, stored by «hold» on its tile)."""
    store = _knx_store(hass)
    if store is None:
        return set()
    out: set[str] = set()
    for configs in store.values():
        for uid, config in configs.items():
            if owned is not None and uid in owned:
                out |= _knx_writes(config.get("knx") if isinstance(config, dict) else None)
    out.discard("?")
    return out


# HA's service call targets (cv.ENTITY_SERVICE_FIELDS): «data» and «service_data» are merged into the target by the
# frontend and HA, so all three are checked alike (security review 28.10).
_TARGET_KEYS = {"entity_id", "device_id", "area_id", "floor_id", "label_id"}
_TEXT_KEYS = {"icon", "name", "title", "label", "text", "theme", "url_path", "path", "color"}
_RUNS = ("perform-action", "call-service")
# Pictures a card may show: HA's own (a room's photo, a camera through HA, /local), Hauflo's static files and inline
# images — never an address outside the house (a tracking pixel sees who opens the dashboard; security review 28.10).
_IMAGE_KEYS = {"image", "picture", "camera_image", "background", "icon_image", "entity_picture"}
_IMAGE_RE = re.compile(r"(?:/local/|/api/image/serve/|/api/image_proxy/|/api/camera_proxy/|/hauflo_static/)[A-Za-z0-9_./%?=&+-]*")
_DATA_IMAGE_RE = re.compile(r"data:image/(?:png|jpeg|gif|webp|svg\+xml)(?:;[a-z0-9=_-]+)*;base64,[A-Za-z0-9+/=]*")


def _image_ok(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if _DATA_IMAGE_RE.fullmatch(value):
        return True
    return bool(_IMAGE_RE.fullmatch(value)) and ".." not in value and "//" not in value


def _area_lights_owned(hass: HomeAssistant | None, area_id: Any, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    """«Всё выключить» by a room: every light HA would turn off there (its own area, or its device's when it has
    none — as HA's service helper resolves it) is Hauflo's."""
    if hass is None or not isinstance(area_id, str) or not re.fullmatch(r"[a-z0-9_]+", area_id) or area_id in ("all", "none"):
        return False
    try:
        ents = er.async_get(hass)
        lights = [e for e in er.async_entries_for_area(ents, area_id) if e.domain == "light"]
        for device in dr.async_entries_for_area(dr.async_get(hass), area_id):
            lights += [e for e in er.async_entries_for_device(ents, device.id) if e.domain == "light" and not e.area_id]
        return all(_hauflo_entity(hass, e.entity_id, owned, owned_entries) for e in lights)
    except Exception:  # noqa: BLE001 — not knowable: not allowed
        return False


def _lovelace_ok(hass: HomeAssistant | None, config: Any, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    if not isinstance(config, dict):
        return False
    owned_writes: list[set[str]] = []

    def entity_ok(eid: str) -> bool:
        domain = eid.split(".", 1)[0]
        if domain in _READ_ONLY_DOMAINS:
            return True
        return _hauflo_entity(hass, eid, owned, owned_entries)

    def target_ids(value: Any) -> list[str] | None:
        """entity_id as HA reads it (cv.comp_entity_ids lower-cases, strips, splits on commas, takes «all»): only exact
        lower-case ids pass — anything HA would reinterpret is refused (security review 28.10)."""
        items = value if isinstance(value, list) else [value]
        if not items or not all(isinstance(i, str) and _ENTITY_RE.fullmatch(i) for i in items):
            return None
        return items

    def service_ok(v: dict[str, Any]) -> bool:
        """A service call anywhere in a dashboard: an allowed service, on Hauflo's own entities named one by one."""
        names = [v[k] for k in ("perform_action", "service") if k in v and not (k == "service" and isinstance(v[k], dict) and "perform_action" not in v)]
        if not names or not all(isinstance(n, str) and n in _CARD_SERVICES for n in names) or len(set(names)) != 1:
            return False
        service = names[0]
        for k in ("target", "data", "service_data"):
            if k in v and not isinstance(v[k], dict):
                return False
        if service == "knx.send":
            # A KNX scene's «store» (+128) on an address one of Hauflo's own entities sends to — nothing else.
            if set(v) & ({"target", "service_data"} | _TARGET_KEYS) or "data" not in v:
                return False
            data = v["data"]
            if not owned_writes:
                owned_writes.append(_owned_knx_writes(hass, owned))
            payload = data.get("payload")
            if set(data) - {"address", "payload"} or _ga_key(data.get("address")) not in owned_writes[0]:
                return False
            return isinstance(payload, list) and all(isinstance(b, int) and not isinstance(b, bool) and 0 <= b <= 255 for b in payload)
        ids: list[str] = []
        areas: list[Any] = []
        # Our own alarm card's action names its entity at the top (hauflo-alert: {service, entity_id}).
        for part, is_target in ((v.get("target") or {}, True), (v.get("data") or {}, False), (v.get("service_data") or {}, False), (v, False)):
            for k, x in part.items():
                if not isinstance(k, str):
                    return False
                lk = k.strip().lower()
                if k == "entity_id":
                    got = target_ids(x)
                    if got is None:
                        return False
                    ids += got
                elif k == "area_id" and is_target and service == "light.turn_off":
                    areas += x if isinstance(x, list) else [x]
                elif lk in _TARGET_KEYS or is_target:
                    return False  # device / area / floor / label: whatever HA finds there isn't Hauflo's to say
        if not ids and not areas:
            return False  # nothing named: never «whatever the service reaches by default»
        if areas and not all(_area_lights_owned(hass, a, owned, owned_entries) for a in areas):
            return False
        return all(_hauflo_entity(hass, i, owned, owned_entries) for i in ids)

    def walk(v: Any, key: str = "") -> bool:
        if isinstance(v, dict):
            kind = v.get("type")
            if kind is not None and (not isinstance(kind, str) or kind not in _LOVELACE_TYPES):
                return False
            if "hauflo_id" in v and not (isinstance(v["hauflo_id"], str) and _HAUFLO_ID_RE.match(v["hauflo_id"])):
                return False
            action = v.get("action")
            if key.endswith("_action") or isinstance(action, str):  # tap_action, hold_action, icon_tap_action, double_tap_action…
                if action not in _ACTIONS:
                    return False
                if action == "navigate" and not _NAV_RE.match(str(v.get("navigation_path", ""))):
                    return False
                if action in _RUNS:
                    if not service_ok(v):
                        return False
                elif set(v) & ({"perform_action", "service", "target", "data", "service_data"} | _TARGET_KEYS):
                    return False  # fail closed: a service or a target on an action that runs none
            elif "perform_action" in v or isinstance(v.get("service"), str):
                if not service_ok(v):  # a service under any other key (our own cards' actions, card features)
                    return False
            elif "target" in v or "service_data" in v:
                return False  # a target with no service to check it by
            if key == "state_image" and not all(_image_ok(x) for x in v.values()):
                return False
            return all(walk(x, str(k)) for k, x in v.items())
        if isinstance(v, list):
            return all(walk(x, key) for x in v)
        if isinstance(v, str):
            if key in ("perform_action", "service", "type", "action", "navigation_path", "hauflo_id"):
                return True  # checked with their dict
            if key in _IMAGE_KEYS:
                return _image_ok(v)
            if v.startswith("/") and key == "path":
                return bool(_NAV_RE.match(v))
            if key not in _TEXT_KEYS:
                norm = v.strip().lower()
                if norm == "all":
                    return False  # HA's «every entity» (security review 28.10)
                # HA lower-cases, strips and splits on commas what it reads as entity ids: only the exact form passes.
                pieces = [p.strip().lower() for p in v.split(",")]
                if any(_ENTITY_RE.fullmatch(p) for p in pieces) and (len(pieces) > 1 or norm != v):
                    return False
                if _ENTITY_RE.fullmatch(v):
                    return entity_ok(v)
                if v.lower().startswith(("http://", "https://", "//", "javascript:", "data:")):
                    return False  # nothing in a Hauflo dashboard loads from outside the house
            return key not in ("url", "url_path") or v.startswith("hauflo-")
        return True

    return walk(config)


def _own_dashboard(hass: HomeAssistant | None, dashboard_id: Any) -> bool:
    """A dashboard Hauflo made (hauflo-…), looked up by its id."""
    if hass is None or not isinstance(dashboard_id, str):
        return False
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA  # noqa: PLC0415

        for url_path, dash in hass.data[LOVELACE_DATA].dashboards.items():
            if (getattr(dash, "config", None) or {}).get("id") == dashboard_id:
                # Never a copy: shown to everyone it would hand the integrator's old cards out (security re-review 28.10).
                return str(url_path).startswith("hauflo-") and not _COPY_RE.search(str(url_path))
    except Exception:  # noqa: BLE001 — not knowable: not allowed
        return False
    return False


def _label_ok(hass: HomeAssistant | None, label_id: Any, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    if hass is None or not isinstance(label_id, str) or not label_id:
        return False
    try:
        if dr.async_entries_for_label(dr.async_get(hass), label_id):
            return False
        return all(_hauflo_entity(hass, e.entity_id, owned, owned_entries) for e in er.async_entries_for_label(er.async_get(hass), label_id))
    except Exception:  # noqa: BLE001 — not knowable: not allowed
        return False


def _alice_labels(hass: HomeAssistant | None) -> set[str]:
    """The labels Alice exposes by (Yandex Smart Home «by label»)."""
    if hass is None:
        return set()
    return {str(e.options.get("label")) for e in hass.config_entries.async_entries("yandex_smart_home") if e.options.get("label")}


def _ws_allowed(msg: dict[str, Any], hass: HomeAssistant | None = None, owned: set[str] | None = None, owned_entries: set[str] | None = None) -> bool:
    t = str(msg.get("type", ""))
    if t == "call_service":
        return (str(msg.get("domain")), str(msg.get("service"))) in _SERVICES
    if t not in _WS_EXACT:
        return False
    if t == "manifest/get":
        # Only what the deploy reads (the agent's own version) and KNX's — not which integrations the house runs.
        return isinstance(msg.get("integration"), str) and msg["integration"] in _MANIFESTS
    if t == "config_entries/get":
        # The agent's own among them: the dashboard needs its cards.
        return isinstance(msg.get("domain"), str) and msg["domain"] in _ENTRY_DOMAINS
    if t == "config/entity_registry/update":
        # Hauflo's own only (a KNX one only if it created it), never a lock / the alarm / a valve, and never handed
        # to a voice assistant through the registry's options (security re-review round 4).
        eid = msg.get("entity_id")
        if not isinstance(eid, str) or eid.startswith(_NEVER_EXPOSE) or not _own_entity(hass, eid, owned_entries):
            return False
        # A garage door / gate keeps its class and never gets Alice's label (security re-review round 5).
        if _protected(hass, eid) and ("device_class" in msg or set(msg.get("labels") or []) & _alice_labels(hass)):
            return False
        if _forbidden(msg.get("new_entity_id")):
            return False
        reg_entry = er.async_get(hass).async_get(eid) if hass is not None else None
        if reg_entry is not None and reg_entry.platform == "knx" and not _owned_knx(hass, eid, owned):
            return False
        return "options_domain" not in msg  # Hauflo never sets an entity's options (voice exposure goes by expose_entity)
    if t in ("config/device_registry/remove", "config/device_registry/remove_config_entry"):
        # Removing a KNX device deletes every UI entity on it (HA's knx async_remove_config_entry_device): only when
        # each of them is Hauflo's (security re-review round 4).
        return _own_device(hass, msg.get("device_id")) and _device_entities_owned(hass, msg.get("device_id"), owned)
    if t == "config/device_registry/update":
        # Only placing a device of Hauflo's own in a room (security expert 25.10: «disabled_by» on the KNX interface
        # reloaded KNX — the house lost the bus).
        return set(msg) <= {"id", "type", "device_id", "area_id"} and _own_device(hass, msg.get("device_id")) and _device_entities_owned(hass, msg.get("device_id"), owned)
    if t in ("config/area_registry/update", "config/floor_registry/update"):
        kind = "area" if t.startswith("config/area") else "floor"
        key = f"{kind}_id"
        if _owns(owned_entries, kind, msg.get(key)):
            return set(msg) <= {"id", "type", key, "name", "aliases", "icon", "floor_id", "level"}
        # Someone else's room: only its empty floor filled in — never its name, aliases (a voice phrase), picture.
        if kind == "area" and set(msg) <= {"id", "type", "area_id", "floor_id"} and msg.get("floor_id"):
            return _area_floor_empty(hass, msg.get("area_id"))
        return False
    if t in ("knx/delete_entity", "knx/update_entity"):
        # Only what Hauflo created — rewriting a hand-made entity's addresses is as bad as deleting it, and an update
        # never makes an entity Hauflo's (security re-review round 4: update, then delete).
        return _owned_knx(hass, msg.get("entity_id"), owned) and (t == "knx/delete_entity" or _knx_config_ok(hass, msg, owned))
    if t == "knx/create_entity":
        return _knx_config_ok(hass, msg, owned)
    if t == "config/area_registry/delete":
        # Only a room Hauflo made (for this or an earlier project here) with nothing left in it — the plan's «лишние
        # комнаты», ticked by the integrator (service review 29.10: «Kitchen», «Room 201» beside the real rooms).
        return set(msg) <= {"id", "type", "area_id"} and _owns(owned_entries, "area", msg.get("area_id")) and _empty_area(hass, msg.get("area_id"))
    if t == "config/label_registry/delete":
        # Only a label Hauflo made and nothing carries (its category labels of the other language).
        return _owns(owned_entries, "label", msg.get("label_id")) and _unused_label(hass, msg.get("label_id"))
    if t.startswith("lovelace/") and _COPY_RE.search(str(msg.get("url_path") or "")):
        # A copy of a dashboard (the integrator's cards as they were) is the agent's alone: the cloud neither reads,
        # makes, nor writes one (security re-review 28.10: fake copies pruned the real ones, a copy overwritten).
        return False
    if t == "lovelace/dashboards/create":
        # Hauflo's own dashboards (hauflo-…), never the client's (security re-review round 4).
        return str(msg.get("url_path") or "").startswith("hauflo-")
    if t == "lovelace/config":
        # Reading: Hauflo's own dashboards only, not the family's (security expert 25.10).
        return str(msg.get("url_path") or "").startswith("hauflo-")
    if t == "lovelace/config/save":
        return str(msg.get("url_path") or "").startswith("hauflo-") and _lovelace_ok(hass, msg.get("config"), owned, owned_entries)
    if t == "lovelace/dashboards/update":
        return _own_dashboard(hass, msg.get("dashboard_id"))
    if t == "lovelace/dashboards/delete":
        # An old project's dashboard the integrator ticked for removal: only one Hauflo made (hauflo-…), never a copy
        # (the integrator's old cards) and never the client's own.
        return set(msg) <= {"id", "type", "dashboard_id"} and _own_dashboard(hass, msg.get("dashboard_id"))
    if t == "homeassistant/expose_entity":
        # Hauflo hands its own to a voice assistant, never the integrator's group or scene (integrator run 20); taking
        # one away from it is fine for any.
        ids = msg.get("entity_ids")
        if not isinstance(ids, list) or any(_protected(hass, i) for i in ids):
            return False
        if msg.get("should_expose") is False:
            # Taking one away too only from Hauflo's own (security expert 25.10: the integrator's voice setup).
            return all(_own_entity(hass, i, owned_entries) and (_platform(hass, i) != "knx" or _owned_knx(hass, i, owned)) for i in ids)
        return all(_hauflo_entity(hass, i, owned, owned_entries) for i in ids)
    return True


def _scrub_diagnostics(text: str) -> str:
    """A config entry's diagnostics as Hauflo reads them: a bridge's filter, Alice's label settings — nothing else
    (states, devices, coordinates, people stay in the house)."""
    try:
        d = json.loads(text)
    except ValueError:
        return "{}"
    data = d.get("data") if isinstance(d, dict) and isinstance(d.get("data"), dict) else {}
    out: dict[str, Any] = {}
    ce = data.get("config-entry") if isinstance(data.get("config-entry"), dict) else None
    if ce is not None:
        flt = (ce.get("options") or {}).get("filter") if isinstance(ce.get("options"), dict) else None
        out["config-entry"] = {"options": {"filter": flt if isinstance(flt, dict) else {}}}
    entry = data.get("entry") if isinstance(data.get("entry"), dict) else None
    if entry is not None:
        opts = entry.get("options") if isinstance(entry.get("options"), dict) else {}
        out["entry"] = {"options": {k: opts.get(k) for k in ("filter_source", "label", "entry_aliases") if k in opts}}
    return json.dumps({"data": out})


def _scrub_devices(devices: Any, hass: HomeAssistant | None) -> Any:
    """The device list without the phones' names («iPhone Ivan»): Hauflo places devices, it needn't know whose."""
    if not isinstance(devices, list):
        return devices
    for d in devices:
        if isinstance(d, dict) and any(_entry_domain(hass, str(e)) == "mobile_app" for e in d.get("config_entries") or []):
            for k in ("name", "name_by_user", "model", "manufacturer"):
                if k in d:
                    d[k] = None
    return devices


def _scrub_monitor(info: Any) -> Any:
    """The bus monitor: which addresses answered (the devices check), never the values (who's home, what's on)."""
    if not isinstance(info, dict):
        return info
    tg = info.get("recent_telegrams") if isinstance(info.get("recent_telegrams"), list) else []
    return {"project_loaded": info.get("project_loaded"), "recent_telegrams": [{k: x.get(k) for k in ("destination", "direction", "telegramtype", "timestamp")} for x in tg if isinstance(x, dict)]}


def _scrub_notes(event: Any, hass: HomeAssistant, owned_entries: set[str] | None = None) -> Any:
    """HA's notifications to Hauflo: only the HomeKit bridges' pairing codes it shows — not the logins that failed, not
    Alice's link, not what else HA tells its owner (panel round 6)."""
    if not isinstance(event, dict) or not isinstance(event.get("notifications"), dict):
        return event
    # Only the bridges Hauflo set up — not the PIN of the integrator's own (security expert 25.10).
    keep = {k: v for k, v in event["notifications"].items() if _entry_domain(hass, str(k)) == "homekit" and owned_entries is not None and str(k) in owned_entries}
    return {**event, "notifications": keep}


ALICE_NOTE = "hauflo_alisa"


def _alice_creds_to_ha(hass: HomeAssistant, text: str) -> str:
    """Alice's link login stays in the house (panel round 6: with it, anyone's Yandex account takes the house): shown
    as an HA notification, Hauflo only learns that it's there."""
    try:
        res = json.loads(text)
    except ValueError:
        return text
    if not isinstance(res, dict) or res.get("handler") != "yandex_smart_home" or res.get("type") != "create_entry":
        return text
    p = res.get("description_placeholders") or {}
    if not isinstance(p, dict) or not p.get("id"):
        return text
    lines = [f"ID: `{p.get('id')}`", f"Пароль / password: `{p.get('password', '')}`"]
    if p.get("otp") and p.get("otp") != "-":
        lines.append(f"Одноразовый код / one-time code: `{p['otp']}`")
    persistent_notification.async_create(hass, "Для приложения «Дом с Алисой» / for the Yandex app:\n\n" + "\n\n".join(lines), "Hauflo: Алиса", ALICE_NOTE)
    res["description_placeholders"] = {"hauflo_note": ALICE_NOTE}
    return json.dumps(res)


def _platform(hass: HomeAssistant | None, entity_id: Any) -> str | None:
    entry = er.async_get(hass).async_get(entity_id) if hass is not None and isinstance(entity_id, str) else None
    return entry.platform if entry else None


def _entry_domain(hass: HomeAssistant, entry_id: str) -> str | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    return entry.domain if entry else None


def _flow_handler(manager: Any, flow_id: str) -> str | None:
    try:
        return str(manager.async_get(flow_id).get("handler"))
    except Exception:  # noqa: BLE001 — unknown flow: not allowed
        return None


def _route(method: str, bare: str) -> str | None:
    """The tunnel.json route a REST call is on (its id), None — not one Hauflo may call."""
    segs = bare.split("/")
    for rid, pattern, methods in _ROUTES:
        if method in methods and len(pattern) == len(segs) and all(p == s or (p[:1] == "{" and p[-1:] == "}" and bool(s)) for p, s in zip(pattern, segs)):
            return rid
    return None


def _owned_entry(owned_entries: set[str] | None, entry_id: Any) -> bool:
    return owned_entries is not None and isinstance(entry_id, str) and entry_id in owned_entries


def _http_allowed(hass: HomeAssistant, method: str, path: str, body: Any, owned_entries: set[str] | None = None, memo: dict[str, list[str]] | None = None, owned: set[str] | None = None) -> bool:
    if ".." in path or "%" in path or "\\" in path:
        return False
    bare = path.split("?")[0]
    parts = bare.strip("/").split("/")
    try:
        data = json.loads(body) if isinstance(body, str) and body else body if isinstance(body, dict) else {}
    except ValueError:
        return False  # fail closed: a body no check could read never passes (security re-review round 5)
    if not isinstance(data, dict):
        return False
    route = _route(method, bare)
    if route in ("info", "hauflo", "flow_handlers"):
        return True
    if route == "homekit_qr":
        # «?<entry_id>-<secret>»: only a bridge Hauflo set up, never the integrator's QR (security expert 25.10).
        return _owned_entry(owned_entries, path.split("?", 1)[1].split("-", 1)[0] if "?" in path else "")
    if route == "scene":
        if not parts[4].startswith("hauflo_"):
            return False
        # A scene the family made on the dashboard (scenes.py) is theirs: the cloud never reads, rewrites or deletes it.
        if parts[4].startswith("hauflo_fam_"):
            return False
        # A scene only sets lights, blinds, relays, climate, fans, media — never a lock or the alarm.
        # Only Hauflo's own (panel round 6: the integrator's gate relay in «Evening»).
        ents = data.get("entities", {}) if method == "POST" else {}
        return isinstance(ents, dict) and all(isinstance(k, str) and k.split(".")[0] in _SCENE_DOMAINS and _hauflo_entity(hass, k, owned, owned_entries) for k in ents)
    if route == "flow":
        handler = str(data.get("handler"))
        # A second Alice link would be someone else's Yandex account on this house.
        if handler == "yandex_smart_home" and hass.config_entries.async_entries(handler):
            return False
        # A reconfigure (an entry_id in the body) rewrites a live connection and reloads it at once: only a KNX link
        # Hauflo set up itself — never the integrator's (KNX expert 25.10: the house would lose the bus).
        if data.get("entry_id") is not None:
            return handler == "knx" and _owned_entry(owned_entries, str(data.get("entry_id")))
        # KNX is single-entry: a new setup while one exists is never Hauflo's to start.
        if handler == "knx" and hass.config_entries.async_entries("knx"):
            return False
        return handler in _FLOW_DOMAINS
    if route == "flow_step":
        domain = _flow_handler(hass.config_entries.flow, parts[4])
        step = _flow_step(hass.config_entries.flow, parts[4])
        return domain in _FLOW_DOMAINS and (method != "POST" or _flow_input_ok(hass, domain, data, memo, parts[4], step, owned, owned_entries))
    if route == "options":
        # Alice's options are never Hauflo's to change (it only sets the integration up): another connection type or
        # skill would hand the house to someone else's Yandex account (security re-review round 4).
        # A group's, a bridge's, a rule sensor's options only for one Hauflo set up (security re-review round 5: the
        # integrator's own helpers and bridges were reachable); KNX too (panel round 6: the integrator's KNX link
        # could be pointed elsewhere and the house lost the bus).
        handler = str(data.get("handler"))
        domain = _entry_domain(hass, handler)
        if domain in _OWNED_ONLY and not _owned_entry(owned_entries, handler):
            return False
        return domain in _OPTIONS_DOMAINS
    if route == "options_step":
        handler = _flow_handler(hass.config_entries.options, parts[5])
        domain = _entry_domain(hass, handler) if handler is not None else None
        if domain in _OWNED_ONLY and not _owned_entry(owned_entries, handler):
            return False
        step = _flow_step(hass.config_entries.options, parts[5])
        return domain in _OPTIONS_DOMAINS and (method != "POST" or _flow_input_ok(hass, domain, data, memo, parts[5], step, owned, owned_entries, options=True))
    if route == "entry_delete":
        # Its groups and rule sensors; a HomeKit bridge only if it was made through this agent (Siri switched off —
        # integrator run 18).
        return _entry_domain(hass, parts[4]) in _DELETE_DOMAINS and _owned_entry(owned_entries, parts[4])
    if route == "diagnostics":
        # Only what Hauflo reads there: the filter of a HomeKit bridge it set up, Alice's label settings — never
        # another's bridge (narrow round 25.10: an integrator's bridge's diagnostics gave a phone's coordinates and
        # user ids). The answer is cut to those fields too (_scrub_diagnostics).
        domain = _entry_domain(hass, parts[3])
        return domain in _DIAG_DOMAINS and (domain not in _OWNED_ONLY or _owned_entry(owned_entries, parts[3]))
    return False


_T = TUNING["link"]
# Reconnects after a Hauflo restart or an outage (HA core + SRE review 29.10): every agent knocking at the same second
# is a storm — each wait is spread ±50 %, and after the server said «reconnect» (a planned restart) the first knock
# comes 1–30 s later at random.
RECONNECT_HINT_WAIT = tuple(_T["reconnect_hint_wait_s"])
LINK_LOST_ISSUE_AFTER = _T["lost_issue_after_s"]  # the link down this long: said in HA's Repairs
# A frame this big (a dashboard, a plan) is parsed off the event loop: json.loads of megabytes blocks HA's loop.
BIG_FRAME = _T["big_frame_bytes"]


def _next_wait(delay: float, hinted: bool, uniform: Callable[[float, float], float] = random.uniform) -> float:
    """Seconds before the next knock at Hauflo."""
    if hinted:
        return uniform(*RECONNECT_HINT_WAIT)
    return delay * uniform(0.5, 1.5)


class Agent:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.server = entry.data[CONF_SERVER]
        self.secret = entry.data[CONF_SECRET]
        self.session = async_get_clientsession(hass, verify_ssl=True)
        self.local_session = async_get_clientsession(hass, verify_ssl=False)
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.channels: dict[int, aiohttp.ClientWebSocketResponse] = {}
        self.tasks: set[asyncio.Task] = set()
        self.refresh = None
        self.stopped = False
        # KNX UI entities Hauflo created or took over (by unique_id — renames keep them): only these may be deleted.
        self.engine: notify.NotifyEngine | None = None  # alarms to the phones (set up right after the agent)
        self.automations: Any = None  # the dashboard's «Автоматизации» (automations.py), made on first use
        self.presence: presence.Presence | None = None  # «Нет дома — имитация присутствия» (presence.py)
        self.season: season.Season | None = None  # «Зима / Лето»: the switch, the resend, the suggestion (season.py)
        self.rules: rules.Rules | None = None  # «Правила по значениям» and the derived values (rules.py, derived.py)
        self.guest: guest.GuestAccess | None = None  # «Гостевой доступ» (guest.py), set up with the entry
        self.ownership: ownership.Ownership | None = None  # the «Hauflo» label and «Создано в этом доме» (ownership.py)
        self.owned: set[str] = set()
        self.owned_entries: set[str] = set()  # config entries (HomeKit bridges, groups) made through the tunnel
        self._flow_memo: dict[str, list[str]] = {}  # HomeKit options: the domains of a flow's first step
        self._owned_store: HaufloStore = HaufloStore(hass, OWNED_STORE)
        # The last dashboard Hauflo saved, per dashboard: the integrator's own cards are merged in against it (dashmerge.py).
        self.dashboards = dashmerge.DashboardMerge(hass)
        self._pending: dict[tuple[int, Any], tuple[str, Any]] = {}
        self._note_subs: set[tuple[int, Any]] = set()  # persistent_notification subscriptions: their events are scrubbed
        self._unsub_signals = None
        self._status_soon: Callable[[], None] | None = None
        # True after Hauflo's «welcome», until the link drops — the «Связь с Hauflo» entity shows it.
        self.linked = False
        self._lost_timer: Callable[[], None] | None = None  # «link lost for 6 h» → Repairs
        # A link to the KNX interface isn't a live bus (routing «connects» with nothing on the wire —
        # integrator run 24.10): the last telegram that came FROM the bus says it.
        self._knx_heard: float | None = None
        self._knx_since = time.monotonic()
        # Names of the house's HA administrators (the service passport, service review r2 D5) — read in the status loop.
        self._admins: list[str] = []
        self._unsub_knx = None
        try:
            from homeassistant.components.knx.const import SIGNAL_KNX_TELEGRAM  # noqa: PLC0415
            from homeassistant.helpers.dispatcher import async_dispatcher_connect  # noqa: PLC0415

            @callback
            def _heard(_telegram, telegram_dict) -> None:
                if str((telegram_dict or {}).get("direction", "")).lower().startswith("incoming"):
                    self._knx_heard = time.monotonic()

            self._unsub_knx = async_dispatcher_connect(hass, SIGNAL_KNX_TELEGRAM, _heard)
        except Exception as err:  # noqa: BLE001
            degrade("knx telegram signal", err)

    @property
    def local_base(self) -> str:
        api = self.hass.config.api
        scheme = "https" if api and api.use_ssl else "http"
        # HA bound to one address (http: server_host) doesn't answer on 127.0.0.1 (HA review round 4): that address.
        host = "127.0.0.1"
        if api and api.host and api.host not in ("0.0.0.0", "::"):
            host = f"[{api.host}]" if ":" in api.host else api.host
        return f"{scheme}://{host}:{api.port if api else 8123}"

    def _spawn(self, coro) -> None:
        task = self.hass.async_create_background_task(coro, "hauflo agent")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    @property
    def token(self) -> str:
        """A fresh access token of the agent's user for one request / one tunnel login."""
        return self.hass.auth.async_create_access_token(self.refresh) if self.refresh else ""

    async def send(self, frame: dict[str, Any]) -> None:
        if self.ws and not self.ws.closed:
            await self.ws.send_str(json.dumps(frame))

    # --- the link to Hauflo ---------------------------------------------------------------------

    async def run(self) -> None:
        self.refresh = await _local_token(self.hass, self.entry)
        await self._ensure_label()
        delay = 5
        url = self.server.replace("https://", "wss://").replace("http://", "ws://") + "/api/agent/ws"
        self._watch_link(False)
        while not self.stopped:
            hinted = False
            try:
                async with self.session.ws_connect(url, heartbeat=30, max_msg_size=16 * 1024 * 1024) as ws:
                    self.ws = ws
                    await self.send({"t": "hello", "token": self.secret, "version": VERSION, "haVersion": HA_VERSION, "instance": await self._instance()})
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            frame = await self._parse(msg.data)
                        except ValueError:
                            _LOGGER.warning("Hauflo: unreadable frame dropped")
                            continue
                        if not isinstance(frame, dict):
                            continue
                        if frame.get("t") == "reconnect":
                            hinted = True  # Hauflo restarts: it closes next, the knock back comes spread out
                            continue
                        if frame.get("t") == "welcome":
                            self._set_linked(True)
                            delay = 5
                            self.hass.data.pop(DATA_UNLINKED, None)
                            persistent_notification.async_dismiss(self.hass, "hauflo_unlinked")
                            issues.clear_issue(self.hass, issues.UNLINKED)
                            issues.sync_version(self.hass, VERSION, frame.get("minVersion"), frame.get("latest"))
                            self._spawn(self._status_loop(ws))
                            # Alarms opened / closed or the rules arrived → status now, not in a minute
                            # (integrator run 25.10: monitoring green for a minute after a re-add).
                            if engine := self.engine:
                                engine.on_change = self.status_now
                            continue
                        try:
                            await self._on_frame(frame)
                        except Exception:  # noqa: BLE001 — one bad frame mustn't end the link (HA core review 25.10)
                            _LOGGER.exception("Hauflo: frame %s failed", frame.get("t"))
                    if ws.close_code == 4003:
                        # Unlinked in Hauflo (or the project deleted): say so in HA, knock rarely. Adding the
                        # integration again with a new code updates this entry (integrator run 25.10: stuck at
                        # «второй не нужен»).
                        self.hass.data[DATA_UNLINKED] = True
                        issues.raise_issue(self.hass, issues.UNLINKED)
                        # HA's own «re-configure» card (HA core review 25.10): a new code updates this entry.
                        self.entry.async_start_reauth(self.hass)
                        delay = 600
                        await asyncio.sleep(delay)
                        continue
                    # 1012 «service restart», 1001 «going away»: Hauflo restarting — no hint frame needed.
                    hinted = hinted or ws.close_code in (1001, 1012)
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                _LOGGER.debug("Hauflo link down: %s", err)
            finally:
                self.ws = None
                self._set_linked(False)
                for ch in list(self.channels):
                    await self._close_channel(ch, tell=False)
            if not self.stopped:
                await asyncio.sleep(_next_wait(delay, hinted))
                delay = min(delay * 2, 60)

    async def _parse(self, data: str) -> Any:
        """A frame from Hauflo; a big one off the event loop (raises ValueError when unreadable)."""
        if len(data) > BIG_FRAME:
            return await self.hass.async_add_executor_job(json.loads, data)
        return json.loads(data)

    @callback
    def _watch_link(self, on: bool) -> None:
        """Back — the Repairs issue goes; lost — it comes after 6 hours without Hauflo."""
        if on:
            if self._lost_timer:
                self._lost_timer()
                self._lost_timer = None
            issues.clear_issue(self.hass, issues.LINK_LOST)
        elif self._lost_timer is None and not self.stopped:
            self._lost_timer = async_call_later(self.hass, LINK_LOST_ISSUE_AFTER, self._link_lost)

    @callback
    def _link_lost(self, _now: Any) -> None:
        self._lost_timer = None
        if not self.linked and not self.stopped and not self.hass.data.get(DATA_UNLINKED):
            issues.raise_issue(self.hass, issues.LINK_LOST, server=str(urlparse(self.server).hostname), hours=str(LINK_LOST_ISSUE_AFTER // 3600))

    async def async_load(self) -> None:
        data = await self._owned_store.async_load()
        self.owned = set(data.get("unique_ids", [])) if isinstance(data, dict) else set()
        self.owned_entries = set(data.get("entries", [])) if isinstance(data, dict) else set()
        await self.dashboards.async_load()

    def _knx_uid(self, entity_id: Any) -> str | None:
        entry = er.async_get(self.hass).async_get(entity_id) if isinstance(entity_id, str) else None
        return entry.unique_id if entry is not None and entry.platform == "knx" else None

    def _track_entry(self, text: str) -> None:
        """A HomeKit bridge, a group or the KNX link set up through the tunnel is Hauflo's: it may delete (bridges, groups)
        or tune (KNX options) it later, never another's."""
        try:
            res = json.loads(text)
        except ValueError:
            return
        entry_id = (res.get("result") or {}).get("entry_id") if isinstance(res, dict) and res.get("type") == "create_entry" else None
        if isinstance(entry_id, str) and _entry_domain(self.hass, entry_id) in _TRACKED_DOMAINS:
            self.owned_entries.add(entry_id)
            self._owned_store.async_delay_save(self._owned_data, 0)  # at once: a crash right after a create mustn't lose it (HA expert 25.10)
            self._owned_changed()

    def _owned_changed(self) -> None:
        """What Hauflo owns moved: the «Hauflo» label follows (after the deploy's burst of changes)."""
        if (own := self.ownership) is not None:
            own.schedule()

    def _owned_data(self) -> dict[str, Any]:
        return {"unique_ids": sorted(self.owned), "entries": sorted(self.owned_entries)}

    def _track(self, ch: int, data: str) -> str:
        """Answers to what Hauflo asked: a create / delete of a KNX entity moves the owned list; get_states leaves
        without private attributes. Returns what goes to Hauflo."""
        try:
            msg = json.loads(data)
        except ValueError:
            return data
        changed = scrubbed = False
        for r in msg if isinstance(msg, list) else [msg]:
            if isinstance(r, dict) and r.get("type") == "event" and (ch, r.get("id")) in self._note_subs:
                r["event"] = _scrub_notes(r.get("event"), self.hass, self.owned_entries)
                scrubbed = True
                continue
            if not isinstance(r, dict) or r.get("type") != "result" or (key := (ch, r.get("id"))) not in self._pending:
                continue
            kind, ref = self._pending.pop(key)
            res = r.get("result")
            if kind == "config_entries/get":
                # Of «template» only the rule sensors Hauflo made: the integrator's own helpers stay in the house.
                r["result"] = [e for e in res if isinstance(e, dict) and e.get("entry_id") in self.owned_entries] if isinstance(res, list) else []
                scrubbed = True
                continue
            if kind in _SCRUBBED:
                r["result"] = (
                    _scrub_states(res, self.hass, self._state_visible) if kind == "get_states" else _scrub_config(res) if kind == "get_config"
                    else _scrub_cloud(res) if kind == "cloud/status" else _scrub_devices(res, self.hass) if kind == "config/device_registry/list"
                    else _scrub_panels(res) if kind == "get_panels"
                    else _scrub_entities(res, self.hass) if kind.startswith("config/entity_registry/") else _scrub_monitor(res)
                )
                scrubbed = True
                continue
            if kind in _CREATES:
                # A room / floor / label Hauflo made: its own to rename, re-floor, delete later.
                k = _CREATES[kind]
                if r.get("success") and isinstance(res, dict) and isinstance(res.get(f"{k}_id"), str):
                    self.owned_entries.add(f"{k}:{res[f'{k}_id']}")
                    changed = True
                continue
            if kind == "config/entity_registry/update":
                reg_entry = res.get("entity_entry") if r.get("success") and isinstance(res, dict) else None
                if isinstance(reg_entry, dict) and (own := self.ownership) is not None:
                    old_id, named = ref
                    own.remember_name(reg_entry.get("entity_id"), reg_entry.get("name"), old_id, named)
                continue
            if not r.get("success") or (kind != "knx/delete_entity" and not (isinstance(res, dict) and res.get("success"))):
                continue
            if kind == "knx/update_entity":
                continue  # an update never makes an entity Hauflo's
            uid = ref if kind == "knx/delete_entity" else self._knx_uid(res.get("entity_id"))
            if uid:
                (self.owned.discard if kind == "knx/delete_entity" else self.owned.add)(uid)
                changed = True
        if changed:
            self._owned_store.async_delay_save(self._owned_data, 0)  # at once: a crash right after a create mustn't lose it (HA expert 25.10)
            self._owned_changed()
        return json.dumps(msg) if scrubbed else data

    async def _on_frame(self, f: dict[str, Any]) -> None:
        t = f.get("t")
        if t == "ws_open":
            self._spawn(self._open_channel(int(f["ch"])))
        elif t == "ws_send":
            await self._channel_send(int(f["ch"]), f["data"])
        elif t == "ws_close":
            await self._close_channel(int(f["ch"]), tell=False)
        elif t == "http":
            self._spawn(self._http(f))
        elif t == "guest":
            self._spawn(self._guest(f))
        elif t == "service":
            # Who services this home and its access history — kept for the owner to read in HA (service.py).
            await service.async_save(self.hass, f.get("info"))

    async def _guest(self, f: dict[str, Any]) -> None:
        """A guest's request relayed by Hauflo (guest.py, docs/guest-access.md): answered by the guest module alone —
        never HA's own WebSocket / REST, never the tunnel's allowlists; the token, the room and the action are checked
        there."""
        status, body = 503, {"code": "not_ready"}
        if (g := self.guest) is not None:
            try:
                await g.async_load()
                status, body = await g.handle(f.get("body"))
            except Exception:  # noqa: BLE001 — the guest gets «не получилось», never a stack
                _LOGGER.exception("Hauflo guest: request failed")
                status, body = 500, {"code": "failed"}
        await self.send({"t": "guest_res", "id": f.get("id"), "status": status, "body": json.dumps(body)})

    # --- the tunnel to this HA ----------------------------------------------------------------

    async def _open_channel(self, ch: int) -> None:
        # Hauflo uses one or two at a time (security re-review round 4); a number already open is refused, never a
        # second admin session behind it (round 5).
        if len(self.channels) >= _MAX_CHANNELS or ch in self.channels:
            await self.send({"t": "ws_closed", "ch": ch})
            return
        try:
            local = await self.local_session.ws_connect(f"{self.local_base}/api/websocket", max_msg_size=0)
        except aiohttp.ClientError:
            await self.send({"t": "ws_closed", "ch": ch})
            return
        self.channels[ch] = local
        try:
            async for msg in local:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = self._track(ch, msg.data) if self._pending or self._note_subs else msg.data
                    await self.send({"t": "ws_msg", "ch": ch, "data": data})
        finally:
            for key in [k for k in self._pending if k[0] == ch]:
                del self._pending[key]
            self._note_subs = {k for k in self._note_subs if k[0] != ch}
            if self.channels.pop(ch, None) is not None:
                await self.send({"t": "ws_closed", "ch": ch})

    async def _instance(self) -> str | None:
        """This HA's own id: Hauflo trusts another account's takeover only on the same instance (security expert 25.10)."""
        try:
            from homeassistant.helpers import instance_id  # noqa: PLC0415

            return await instance_id.async_get(self.hass)
        except Exception:  # noqa: BLE001
            return None

    def _state_visible(self, eid: str) -> bool:
        """A presence sensor's state goes to Hauflo only when it is Hauflo's own or marked «Hauflo мониторинг»."""
        own = _own_entity(self.hass, eid, self.owned_entries) and (_platform(self.hass, eid) != "knx" or _owned_knx(self.hass, eid, self.owned))
        return own or notify.labelled(self.hass, eid)

    @property
    def locked(self) -> bool:
        """The owner switched «Allow Hauflo to change this home's setup» off — read live, an options save needs no reload."""
        return self.entry.options.get(CONF_ALLOW_CHANGES, True) is not True

    def allowed_ranges(self) -> set[str]:
        """Main/middle groups Hauflo may send to: what the integrator allowed in HA, and — for sites paired before
        this existed — the groups of the entities Hauflo already made."""
        allowed = {str(r) for r in self.entry.options.get(CONF_RANGES) or []}
        if CONF_RANGES not in self.entry.options:
            try:
                for configs in (_knx_store(self.hass) or {}).values():
                    for uid, config in configs.items():
                        if uid in self.owned:
                            allowed |= {r for r in (_ga_range(k) for k in _knx_writes(config.get("knx") if isinstance(config, dict) else None)) if r}
            except Exception:  # noqa: BLE001 — no KNX yet: nothing made
                pass
        return allowed

    def _outside_ranges(self, msg: dict[str, Any]) -> set[str]:
        if msg.get("type") not in ("knx/create_entity", "knx/update_entity"):
            return set()
        data = msg.get("data")
        writes = _knx_writes(data.get("knx") if isinstance(data, dict) else None) - {"?"}
        allowed = self.allowed_ranges()
        # What's outside is asked for address by address — never a whole group around it.
        return {t for k in writes if (t := _ga_text(k)) and not _range_allows(allowed, k)}

    def _ask_ranges(self, items: set[str]) -> None:
        ranges.ask(self.hass, items)

    def _dashboard_ok(self, config: Any) -> bool:
        return _lovelace_ok(self.hass, config, self.owned, self.owned_entries)

    async def _channel_send(self, ch: int, data: str) -> None:
        local = self.channels.get(ch)
        if local is None:
            return
        try:
            msg = json.loads(data)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        # Hauflo doesn't hold a token for this HA: the agent signs in with its own.
        if msg.get("type") == "auth":
            data = json.dumps({"type": "auth", "access_token": self.token})
        elif msg.get("type") == dashmerge.PLAN or (msg.get("type") == "lovelace/config" and self.dashboards.manages(msg.get("url_path")) and not dashmerge.is_copy(msg.get("url_path"))):
            # Reads only: answered by the agent itself (no foreign card's content leaves the house).
            await dashmerge.handle(self, ch, msg, self._dashboard_ok, backup.fresh, LOCKED_CODE)
            return
        elif self.locked and _ws_locked(msg):
            # Closed by the owner in HA: no write gets through, whatever it is — before any other check asks for more.
            _LOGGER.info("Hauflo: «%s» refused — changes by Hauflo are switched off in HA", msg.get("type"))
            await self.send({"t": "ws_msg", "ch": ch, "data": json.dumps({"id": msg.get("id"), "type": "result", "success": False, "error": {"code": LOCKED_CODE, "message": "Hauflo agent: changes by Hauflo are switched off in Home Assistant (Hauflo → Configure)"}})})
            return
        elif (outside := self._outside_ranges(msg)):
            # Not a range the integrator allowed in HA (acceptance B9): refused, said in the house where to allow it.
            self._ask_ranges(outside)
            await self.send({"t": "ws_msg", "ch": ch, "data": json.dumps({"id": msg.get("id"), "type": "result", "success": False, "error": {"code": "address_not_allowed", "message": f"Hauflo agent: group addresses {', '.join(sorted(outside))} are not allowed in HA (Hauflo → Configure)"}})})
            return
        elif msg.get("type") == dashmerge.SAVE or (msg.get("type") == "lovelace/config/save" and str(msg.get("url_path") or "").startswith("hauflo-")):
            # Hauflo's part merged with what the integrator made in HA — by the agent: the cloud never writes his cards.
            # Every save of a Hauflo dashboard (security re-review 28.10: one the agent had no version of was
            # overwritten with no copy); a copy's is refused there.
            await dashmerge.handle(self, ch, msg, self._dashboard_ok, backup.fresh, LOCKED_CODE)
            return
        elif not _ws_allowed(msg, self.hass, self.owned, self.owned_entries):
            # Only what Hauflo deploys and reads — never backups, add-ons, arbitrary services (security review 25.10).
            _LOGGER.warning("Hauflo: «%s» isn't allowed through the tunnel", msg.get("type"))
            await self.send({"t": "ws_msg", "ch": ch, "data": json.dumps({"id": msg.get("id"), "type": "result", "success": False, "error": {"code": "not_allowed", "message": "Hauflo agent: not allowed"}})})
            return
        if _ws_changes(msg) and not backup.fresh():
            await self.send({"t": "ws_msg", "ch": ch, "data": json.dumps({"id": msg.get("id"), "type": "result", "success": False, "error": {"code": "backup_required", "message": "Hauflo agent: a backup first"}})})
            return
        if msg.get("type") in _SCRUBBED:
            self._pending[(ch, msg.get("id"))] = (str(msg["type"]), None)
        if msg.get("type") == "config_entries/get" and msg.get("domain") in _ENTRY_OWNED_ONLY:
            self._pending[(ch, msg.get("id"))] = ("config_entries/get", None)
        if msg.get("type") == "persistent_notification/subscribe":
            self._note_subs.add((ch, msg.get("id")))
        if msg.get("type") in _CREATES:
            self._pending[(ch, msg.get("id"))] = (str(msg["type"]), None)
        if (t := msg.get("type")) in ("knx/create_entity", "knx/update_entity", "knx/delete_entity"):
            ref = self._knx_uid(msg.get("entity_id")) if t == "knx/delete_entity" else msg.get("entity_id")
            self._pending[(ch, msg.get("id"))] = (t, ref)
        if msg.get("type") == "config/entity_registry/update":
            # The name Hauflo sets (its groups) isn't a hand rename in «Made in this home» (ownership.py).
            self._pending[(ch, msg.get("id"))] = ("config/entity_registry/update", (msg.get("entity_id"), "name" in msg))
        await local.send_str(data)

    async def _close_channel(self, ch: int, tell: bool) -> None:
        local = self.channels.pop(ch, None)
        if local is not None:
            await local.close()
        if tell:
            await self.send({"t": "ws_closed", "ch": ch})

    async def _http(self, f: dict[str, Any]) -> None:
        path = str(f.get("path", ""))
        if self.locked and _http_locked(str(f.get("method", "GET")).upper(), path, f.get("body")):
            _LOGGER.info("Hauflo: %s %s refused — changes by Hauflo are switched off in HA", f.get("method"), path.split("?")[0])
            await self.send({"t": "http_res", "id": f.get("id"), "status": 423, "body": json.dumps({"code": LOCKED_CODE})})
            return
        if not _http_allowed(self.hass, str(f.get("method", "GET")).upper(), path, f.get("body"), self.owned_entries, self._flow_memo, self.owned):
            _LOGGER.warning("Hauflo: %s %s isn't allowed through the tunnel", f.get("method"), path.split("?")[0])
            await self.send({"t": "http_res", "id": f.get("id"), "status": 403, "body": "Hauflo agent: not allowed"})
            return
        if _http_changes(str(f.get("method", "GET")).upper(), path) and not backup.fresh():
            await self.send({"t": "http_res", "id": f.get("id"), "status": 428, "body": json.dumps({"code": "backup_required"})})
            return
        try:
            async with self.local_session.request(
                f.get("method", "GET"),
                f"{self.local_base}{path}",
                data=f.get("body"),
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as res:
                text = await res.text()
                if res.status == 200 and path.startswith("/api/config/config_entries/flow/"):
                    self._track_entry(text)
                    text = _alice_creds_to_ha(self.hass, text)
                if res.status == 200 and path.split("?")[0].startswith("/api/diagnostics/"):
                    text = _scrub_diagnostics(text)
                if res.status == 200 and path.split("?")[0] == "/api/config":
                    with contextlib.suppress(ValueError):
                        text = json.dumps(_scrub_config(json.loads(text)))
                await self.send({"t": "http_res", "id": f["id"], "status": res.status, "body": text})
        except (aiohttp.ClientError, TimeoutError) as err:
            await self.send({"t": "http_res", "id": f["id"], "status": 502, "body": str(err)})

    # --- status -------------------------------------------------------------------------------

    async def _ensure_label(self) -> None:
        # In HA's own language (persona review round 4: «Hauflo мониторинг» in an English villa's HA); either name works.
        reg = lr.async_get(self.hass)
        if not any(label.name.casefold() in MONITOR_LABELS for label in reg.async_list_labels()):
            ru = str(self.hass.config.language or "").startswith("ru")
            reg.async_create(MONITOR_LABEL if ru else MONITOR_LABEL_EN, icon="mdi:bell-alert", color="red", description=ownership.MONITOR_TEXT["ru" if ru else "en"])

    def _monitored_ids(self) -> list[str]:
        label = next((l for l in lr.async_get(self.hass).async_list_labels() if l.name.casefold() in MONITOR_LABELS), None)
        if label is None:
            return []
        return [e.entity_id for e in er.async_entries_for_label(er.async_get(self.hass), label.label_id)]

    def _knx(self) -> dict[str, Any]:
        entries = self.hass.config_entries.async_entries("knx")
        loaded = any(e.state is ConfigEntryState.LOADED for e in entries)
        connected = None
        if loaded:
            try:
                connected = knx_data(self.hass).xknx.connection_manager.connected.is_set()  # type: ignore[union-attr]
            except Exception as err:  # noqa: BLE001
                degrade("knx connection state", err)
        # Minutes the bus has been silent (since the agent started when nothing came yet).
        silent = int((time.monotonic() - (self._knx_heard or self._knx_since)) // 60)
        return {"loaded": loaded, "connected": connected, "state": entries[0].state.value if entries else "missing", "silentMin": silent, "heard": self._knx_heard is not None}

    def _knx_conn(self) -> dict[str, Any] | None:
        """How HA talks to the bus, for the service passport (service review r2 D5): the connection type, the gateway's
        address and the tunnel / routing details — an allowlist; KNX Secure keys, passwords and the keyring never leave."""
        entries = self.hass.config_entries.async_entries("knx")
        if not entries:
            return None
        return knx_conn(entries[0].data)

    def _ha_url(self) -> str | None:
        """The house's Home Assistant address as configured (external, else internal), without a login or a query."""
        return ha_url(self.hass.config.external_url or self.hass.config.internal_url)

    async def _refresh_admins(self) -> None:
        with contextlib.suppress(Exception):
            users = await self.hass.auth.async_get_users()
            self._admins = sorted({str(u.name)[:80] for u in users if u.is_admin and u.is_active and not u.system_generated and u.name})[:20]

    def status(self) -> dict[str, Any]:
        issues.sync_degraded(self.hass)  # a feature HA's internals took away since the last minute: in Repairs
        reg = er.async_get(self.hass)
        knx_ids = [e.entity_id for e in reg.entities.values() if e.platform == "knx" and not e.disabled_by]
        unavailable = [i for i in knx_ids if (s := self.hass.states.get(i)) is not None and s.state == "unavailable"]
        monitored = []
        for eid in self._monitored_ids():
            s = self.hass.states.get(eid)
            if s is None:
                continue
            dc = s.attributes.get("device_class")
            problem = s.state == "unavailable" or (s.domain == "binary_sensor" and s.state == "on" and (dc in PROBLEM_ON or dc is None))
            if s.domain == "sensor" and dc == "battery":
                with contextlib.suppress(ValueError):
                    problem = problem or float(s.state) < 20
            monitored.append({"entityId": eid, "name": s.name, "state": s.state, "problem": problem})
        return {
            "haVersion": HA_VERSION,
            "agentVersion": VERSION,
            "knx": self._knx(),
            "unavailable": {"count": len(unavailable), "sample": unavailable[:10]},
            "monitored": monitored,
            # Alarms the agent's push engine has open now (notify.py) — monitoring shows them too.
            "alarms": engine.active_list() if (engine := self.engine) else [],
            # No rules yet (a re-added agent before Hauflo sends them): its open alarms aren't known yet.
            "alarmsReady": bool(engine and engine.config["rules"]),
            # The owner closed the house for changes in HA: Hauflo stops a deploy before it starts.
            "locked": self.locked,
            # The alarms' times are ISO with their offset (UTC); this is the house's clock to say them in — mails
            # then say what the push and the alarm card say (integrator review 29.10, defect 9).
            "timeZone": str(self.hass.config.time_zone or ""),
            # For the service passport in Hauflo's handover pack (service review r2 D5): how KNX is connected, the HA
            # address and who administers it (names only) — never keys, passwords or tokens.
            "knxConn": self._knx_conn(),
            "haUrl": self._ha_url(),
            "haAdmins": list(self._admins),
        }

    @callback
    def _set_linked(self, on: bool) -> None:
        if self.linked != on:
            self.linked = on
            # Once when it goes and once when it's back (HA quality scale: log-when-unavailable).
            _LOGGER.info("Hauflo: link to %s %s", urlparse(self.server).hostname, "restored" if on else "lost — reconnecting")
            async_dispatcher_send(self.hass, f"{DOMAIN}_link")
            self._watch_link(on)

    @callback
    def status_now(self) -> None:
        """Status to Hauflo right away (alarms or rules changed) — when linked; otherwise the next link sends it."""
        if self.ws is not None and not self.ws.closed:
            self._spawn(self.send({"t": "status", "status": self.status()}))

    async def _status_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while self.ws is ws and not ws.closed:
            await self._refresh_admins()
            await self.send({"t": "status", "status": self.status()})
            self._watch_signals()
            await asyncio.sleep(STATUS_EVERY)

    @callback
    def _watch_signals(self) -> None:
        """A monitored signal changes → status right away (an alarm shouldn't wait a minute)."""
        if self._unsub_signals:
            self._unsub_signals()
        ids = self._monitored_ids()
        self._unsub_signals = async_track_state_change_event(self.hass, ids, self._signal_changed) if ids else None

    @callback
    def _signal_changed(self, _event: Event) -> None:
        if self._status_soon:
            self._status_soon()
        # HA's own timer (HA core review 25.10), cancelled in stop(); a @callback, so it runs in the event loop
        # (a plain lambda is run in an executor thread — re-review 25.10).
        self._status_soon = async_call_later(self.hass, 2, self._status_tick)

    @callback
    def _status_tick(self, _now: Any) -> None:
        self._status_soon = None
        self.status_now()

    async def stop(self) -> None:
        if self._unsub_knx:
            self._unsub_knx()
            self._unsub_knx = None
        self.stopped = True
        if self._unsub_signals:
            self._unsub_signals()
            self._unsub_signals = None
        if self._status_soon:
            self._status_soon()
            self._status_soon = None
        if self._lost_timer:
            self._lost_timer()
            self._lost_timer = None
        if self.ws:
            await self.ws.close()
        for task in list(self.tasks):
            task.cancel()


PLATFORMS = ["binary_sensor", "sensor", "switch"]


type HaufloConfigEntry = ConfigEntry[Agent]


async def async_setup_entry(hass: HomeAssistant, entry: HaufloConfigEntry) -> bool:
    # «Кто может менять настройки дома» (house.py): first — round 2's allow-list becomes round 3's restricted profiles.
    try:
        await house.async_init(hass)
    except Exception as err:  # noqa: BLE001 — never stops the agent
        degrade("house settings", err)
    agent = Agent(hass, entry)
    await agent.async_load()
    entry.runtime_data = agent
    owns = lambda eid: _own_entity(hass, eid, agent.owned_entries) and (_platform(hass, eid) != "knx" or _owned_knx(hass, eid, agent.owned))  # noqa: E731
    # «Зима / Лето» from HA's storage (it keeps working without Hauflo online); «Комфортный пол» of an earlier agent is
    # taken away cleanly — its own entities and store only, the KNX thermostats untouched (retired.py).
    agent.season = season.Season(hass, lambda: agent.engine, lambda: agent.rules, owns)
    await agent.season.async_load()
    try:
        await retired.async_retire_comfort(hass, entry.entry_id, agent.season.async_seed)
    except Exception as err:  # noqa: BLE001 — never stops the agent
        degrade("comfort floor removal", err)

    async def _tell(title: str, message: str, user_id: str | None) -> None:
        if agent.engine is not None:
            await agent.engine.async_tell(title, message, user_id)

    async def _ask(title: str, message: str, actions: list, tag: str) -> None:
        # A rule's suggestion: to every phone that gets the house's messages, with «Сделать / Не сейчас».
        if agent.engine is not None:
            await agent.engine.async_tell(title, message, None, actions=actions, tag=tag)

    async def _season_set(mode: str, _why: str | None) -> None:
        await agent.season.apply(mode, None)

    # «Правила по значениям»: loaded before the platforms (their entities come from it), run after them.
    agent.rules = rules.Rules(
        hass,
        owns=lambda eid: _hauflo_entity(hass, eid, agent.owned, agent.owned_entries),
        own_raw=owns,  # a valve's «close» only — every other target goes through _hauflo_entity
        season=agent.season.rule_season,
        tell=_tell,
        season_now=agent.season.current,
        season_set=_season_set,
        ask=_ask,
    )
    agent.rules.entry_id = entry.entry_id
    try:
        await agent.rules.async_load()
    except Exception as err:  # noqa: BLE001 — never stops the agent
        degrade("value rules", err)
    # The «Hauflo» label on what Hauflo made, and «Создано в этом доме» (ownership.py) — before the sensor is added.
    agent.ownership = ownership.Ownership(hass, lambda: agent.owned, lambda: agent.owned_entries, lambda: agent.locked)
    try:
        await agent.ownership.async_start()
    except Exception as err:  # noqa: BLE001 — never stops the agent
        degrade("hauflo label", err)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    try:
        await agent.season.async_start()
    except Exception as err:  # noqa: BLE001 — never stops the agent
        degrade("season", err)
    try:
        await agent.rules.async_start()
    except Exception as err:  # noqa: BLE001 — never stops the agent
        degrade("value rules", err)
    # The cards: served by the agent, or — a house made independent — from /config/www/hauflo, refreshed to this
    # version; fallback copies an earlier removal left are taken off (keep.py). Before the theme adds its scripts, and
    # both before the link starts: a deploy right after it must find the cards served (integrator review r2, D2).
    try:
        hass.data[DATA_CARDS_LOCAL] = await keep.async_on_start(hass, VERSION)
    except Exception as err:  # noqa: BLE001 — never stops the agent
        degrade("lovelace resources", err)
    for feature, setup in (("voice sentences", voice.async_setup), ("theme", theme.async_setup), ("menu for new people", sidebar.async_setup)):
        try:
            await setup(hass)
        except Exception as err:  # noqa: BLE001 — a changed HA internal never stops the agent
            degrade(feature, err)
    entry.async_create_background_task(hass, agent.run(), "hauflo agent link")
    # Push alarms to the residents' phones (notify.py); its API is registered once per HA run.
    # «Нет дома — имитация присутствия»: on / until survive a restart (presence.py).
    agent.presence = presence.Presence(hass, lambda eid: _hauflo_entity(hass, eid, agent.owned, agent.owned_entries))
    try:
        await agent.presence.async_start()
    except Exception as err:  # noqa: BLE001 — never stops the agent
        degrade("presence simulation", err)
    # «Гостевой доступ»: links to this region's server; the grants and the log stay in HA (guest.py).
    agent.guest = guest.GuestAccess(
        hass,
        lambda eid: _hauflo_entity(hass, eid, agent.owned, agent.owned_entries),
        lambda: f"{agent.server.rstrip('/')}/g/{guest.route_of(agent.secret)}",
    )
    try:
        await agent.guest.async_load()
    except Exception as err:  # noqa: BLE001 — never stops the agent (loaded again on the first request)
        degrade("guest access", err)
    engine = notify.NotifyEngine(hass)
    # Set here too: the link may say «welcome» before the engine exists (re-added integration — monitoring
    # stayed empty until the minute status, integrator run 14).
    engine.on_change = agent.status_now
    engine.owns = owns
    engine.locked = lambda: agent.locked
    await engine.async_start()
    agent.engine = engine
    if not hass.data.get(DATA_NOTIFY_VIEWS):
        hass.data[DATA_NOTIFY_VIEWS] = True
        hass.http.register_view(notify.NotifyView(lambda: _engine(hass)))
        hass.http.register_view(notify.NotifyTestView(lambda: _engine(hass)))
        hass.http.register_view(backup.BackupView())
        hass.http.register_view(notify.AlarmsView(lambda: _engine(hass)))
        hass.http.register_view(OwnedView())
        hass.http.register_view(ownership.OwnershipView(_ownership))
        hass.http.register_view(RangesView())
        hass.http.register_view(rules.RulesView(lambda: _rules(hass)))
        hass.http.register_view(sidebar.SidebarView())
        hass.http.register_view(season.SeasonView(lambda: _season(hass)))
        hass.http.register_view(season.SeasonConfigView(lambda: _season(hass)))
        hass.http.register_view(titles.TitlesView())
        hass.http.register_view(block_order.OrderView())
        hass.http.register_view(automations.AutomationsView(lambda: _automations(hass)))
        hass.http.register_view(scenes.ScenesView(lambda: _scenes(hass)))
        hass.http.register_view(presence.PresenceView(lambda: _presence(hass)))
        hass.http.register_view(guest.GuestView(lambda: _guest(hass), lambda: _engine(hass)))
        hass.http.register_view(house.HouseView())

    async def _stop(_event: Event) -> None:
        if agent.presence is not None:
            agent.presence.async_stop()
        await agent.stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop))
    # The entry changed: options (the lock, the addresses) are read live — no reload, Hauflo hears the lock at once;
    # a new link (reauth / reconfigure: another secret or server) reloads here — HA's way since 2026.12, the flow only
    # updates the entry (HA reviewer r2: async_update_reload_and_abort with an update listener breaks in 2026.12).
    entry.async_on_unload(entry.add_update_listener(_entry_changed))
    issues.sync_degraded(hass)
    return True


def relinked(agent: Agent, data: Mapping[str, Any]) -> bool:
    """The entry's link differs from the one the running agent uses: a reload takes the new one."""
    return data.get(CONF_SECRET) != agent.secret or data.get(CONF_SERVER) != agent.server


async def _entry_changed(hass: HomeAssistant, entry: HaufloConfigEntry) -> None:
    if relinked(entry.runtime_data, entry.data):
        hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    entry.runtime_data.status_now()
    if (own := entry.runtime_data.ownership) is not None:
        own.schedule(1)  # the house opened again: the label catches up


class OwnedView(HomeAssistantView):
    """The KNX entities Hauflo made here (their unique ids): a plan shows the ones no project knows any more —
    Hauflo's own orphans (HA expert 25.10: they were nowhere to be seen). Admin only, read only."""

    url = "/api/hauflo/owned"
    name = "api:hauflo:owned"
    requires_auth = True

    async def get(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        hass = request.app[KEY_HASS]
        owned: set[str] = set()
        entries: set[str] = set()
        areas: set[str] = set()
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            owned |= entry.runtime_data.owned
            # The groups and bridges it set up (config entries) — a lost deploy record takes them back, not a second
            # set (round 6, B11). Floors and labels («kind:id») stay inside.
            entries |= {e for e in entry.runtime_data.owned_entries if ":" not in e}
            # Rooms it made: a plan offers to remove the empty ones no project here uses (service review 29.10).
            areas |= {e.split(":", 1)[1] for e in entry.runtime_data.owned_entries if e.startswith("area:")}
        return self.json({"unique_ids": sorted(owned), "entries": sorted(entries), "areas": sorted(areas)})


class RangesView(HomeAssistantView):
    """The group address ranges Hauflo may send to (allowed in HA) and the ones it asked for — the plan says which a
    deploy still needs allowed (acceptance B9). Read only; changed only in HA's own options flow."""

    url = "/api/hauflo/ranges"
    name = "api:hauflo:ranges"
    requires_auth = True

    async def get(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        hass = request.app[KEY_HASS]
        allowed: set[str] = set()
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            allowed |= entry.runtime_data.allowed_ranges()
        return self.json({"allowed": sorted(allowed), **ranges.state(hass)})

    async def post(self, request):
        """The plan asks for what the project writes ({"addresses": [...], "project": "…"}): what isn't allowed waits
        for the owner's one click in Repairs. Grants nothing (integrator review 29.10, defect 4)."""
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        hass = request.app[KEY_HASS]
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("bad body", 400)
        wanted = body.get("addresses") if isinstance(body, dict) else None
        if not isinstance(wanted, list) or len(wanted) > ranges.MAX_ITEMS or not all(isinstance(x, str) for x in wanted):
            return self.json_message("bad addresses", 400)
        loaded = hass.config_entries.async_loaded_entries(DOMAIN)
        if not loaded:
            return self.json_message("not linked", 409)
        allowed = loaded[0].runtime_data.allowed_ranges()
        project = str(body.get("project") or "") or (loaded[0].title or "").removeprefix("Hauflo — ")
        return self.json({"allowed": sorted(allowed), **ranges.request(hass, allowed, wanted, project)})


def _ownership(hass: HomeAssistant) -> ownership.Ownership | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.ownership
    return None


def _engine(hass: HomeAssistant) -> notify.NotifyEngine | None:
    """The loaded entry's alarm engine — for the views, registered once per HA run."""
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if entry.runtime_data.engine is not None:
            return entry.runtime_data.engine
    return None


def _season(hass: HomeAssistant) -> season.Season | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.season
    return None


def _automations(hass: HomeAssistant) -> automations.Automations | None:
    """One per loaded entry (its lock keeps two saves from writing automations.yaml at once)."""
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        agent = entry.runtime_data
        if getattr(agent, "automations", None) is None:
            agent.automations = automations.Automations(hass, lambda eid: _hauflo_entity(hass, eid, agent.owned, agent.owned_entries))
        return agent.automations
    return None


def _scenes(hass: HomeAssistant) -> scenes.FamilyScenes | None:
    """The family's own room scenes (scenes.py): Hauflo's lights and blinds only, as automations."""
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        agent = entry.runtime_data
        if getattr(agent, "family_scenes", None) is None:
            agent.family_scenes = scenes.FamilyScenes(
                hass,
                lambda eid: _hauflo_entity(hass, eid, agent.owned, agent.owned_entries),
                knx=scenes.KnxScenes(
                    numbers=lambda: _knx_scenes_learnable(hass, agent.owned, agent.allowed_ranges()),
                    target=lambda eid: _knx_scene_target(hass, agent.owned, agent.allowed_ranges(), eid),
                    locked=lambda: agent.locked,
                ),
            )
        return agent.family_scenes
    return None


def _presence(hass: HomeAssistant) -> presence.Presence | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.presence
    return None


def _guest(hass: HomeAssistant) -> guest.GuestAccess | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.guest
    return None


def _rules(hass: HomeAssistant) -> rules.Rules | None:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data.rules
    return None


async def async_unload_entry(hass: HomeAssistant, entry: HaufloConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    if (sn := entry.runtime_data.season) is not None:
        sn.async_stop()
        await sn.store.async_save(sn.data())  # the last resend kept (a delayed save would be lost)
    if (vr := entry.runtime_data.rules) is not None:
        vr.async_stop()
        await vr.store.async_save(vr.data())  # the state as it is now (a delayed save would be lost)
    if (sim := entry.runtime_data.presence) is not None:
        entry.runtime_data.presence = None
        sim.async_stop()
        # Unloaded or removed (not HA stopping — a restart picks them up again): the lights «Нет дома» lit go off, nobody
        # is left to switch them off (HA expert 28.10).
        if not hass.is_stopping:
            try:
                async with asyncio.timeout(15):
                    await sim.async_lights_out()
            except Exception:  # noqa: BLE001 — never blocks the unload
                _LOGGER.warning("Hauflo presence: lights not switched off on unload", exc_info=True)
    await entry.runtime_data.stop()
    # Guest access stops with the entry: its unsaved log written, no grant served by an unloaded agent.
    if (guests := entry.runtime_data.guest) is not None:
        entry.runtime_data.guest = None
        await guests.async_stop()
    if (own := entry.runtime_data.ownership) is not None:
        own.async_stop()
        await own.async_flush()
    voice.async_unload(hass)
    sidebar.async_unload(hass)
    theme.async_unload(hass)
    if engine := entry.runtime_data.engine:
        entry.runtime_data.engine = None
        engine.async_stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Removed in HA: its system user and voice sentences go too, and Hauflo is told (the site is no
    longer paired — no «agent gone» alarm)."""
    await voice.async_remove(hass)
    issues.clear_all(hass)  # nothing left to repair for an agent that is gone
    # What Hauflo made stays known as its own: the agent added again finds its entities, rooms and groups (HA expert
    # 25.10: re-adding lost every ownership and the next deploy made «_2» duplicates).
    # The client's dashboard keeps its cards without the agent (copies + Lovelace resources).
    try:
        await keep.async_keep_dashboard(hass, VERSION)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Hauflo: dashboard cards not kept")
    with contextlib.suppress(Exception):
        await notify.NotifyEngine(hass).async_remove()
    with contextlib.suppress(Exception):
        await HaufloStore(hass, retired.COMFORT_STORE).async_remove()
    with contextlib.suppress(Exception):
        await HaufloStore(hass, season.STORE_KEY).async_remove()
    with contextlib.suppress(Exception):
        await HaufloStore(hass, rules.STORE_KEY).async_remove()
    # The guest log names the house's people and when guests came (personal data, HA core review 29.10): it goes with
    # the agent, and so do the links — nobody serves them any more. The dashboards stay (deliberate, keep.py).
    try:
        await HaufloStore(hass, guest.STORE_KEY).async_remove()
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Hauflo: guest access log not removed")
    # Who serviced the home: Hauflo's to say, gone with the agent.
    with contextlib.suppress(Exception):
        await service.async_remove(hass)
    # The last plan Hauflo told («Создано в этом доме»): stale without the agent. The «Hauflo» label stays (README).
    with contextlib.suppress(Exception):
        await HaufloStore(hass, ownership.STORE_KEY).async_remove()
    # HA's own menu back for everyone: the panels «Убрать лишнее из меню» hid (HA expert 28.10).
    try:
        await sidebar.async_remove(hass)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Hauflo: sidebar panels not restored")
    with contextlib.suppress(Exception):
        session = async_get_clientsession(hass)
        await session.post(
            f"{entry.data[CONF_SERVER]}/api/agent/unpair",
            headers={"Authorization": f"Bearer {entry.data[CONF_SECRET]}"},
            json={},
            timeout=aiohttp.ClientTimeout(total=10),
        )
    if (uid := entry.data.get(CONF_USER_ID)) and (user := await hass.auth.async_get_user(uid)):
        await hass.auth.async_remove_user(user)
