"""Other integrations' entities as alarm sources (п.63.6): the integrator's «Уведомлять» switch in Hauflo's list, apart
from the panel's (a leak sensor hidden from the room card still alarms). The deploy sends the whole map; True works as
the «Hauflo мониторинг» label, False silences one the label would watch. Not in the map — the label decides, as before.
Hauflo's own entities are never silenced by it (notify._source_ok checks them first).

The wizard of other integrations (п.57, agent 0.23.0, feature «foreign_wizard»): the deploy first sends its plan — the
project's rooms and the very devices / entities it may touch — then writes, through the tunnel, a device's room / name /
role labels and an entity's room / name / hidden / role labels (also an entity with no device: a template, an MQTT one).
Never a KNX / Hauflo / phone one, never a service device, never one outside the plan, never a room outside it, never
a label not Hauflo's role one (hauflo_role_*) — and the family's own labels are always kept (merged, not replaced).
Before Hauflo's first write to an item its values as they were are kept here; «Вернуть как было» puts them back
(a field the integrator changed by hand since stays his).

Other integrations' lights and blinds in Hauflo's scenes and groups (399-6, agent 0.28.0, feature «foreign_scene_members»):
a house with no KNX (Matter, Zigbee) — the integrator picks a room's lamps and blinds on «Сцены»; the deploy sends that
very list ({"members": [...]}, the whole set each time, kept across restarts: a family scene «remember again» and a
group's options need it later too). Only a listed light / cover of a real integration, enabled, not hidden by the
family, never protected (a lock / gate / garage door, a group or a «Switch as X» / template helper over one, a cover
named as a gate) — member_refusal says why not. The registry of such an entity stays out of Hauflo's reach (_own_entity
is unchanged): a scene / group of Hauflo deleted never touches it, a group never hides it.

Smart locks (Р56, agent feature «foreign_locks», locks.py): the wizard's «Замок» sends {"locks": {lock entity: {push,
alarms, users, names, door, poll}}} — the whole map each time: a push on each unlock (off by default), the alarms' pushes
(on by default), the HA users who may unlock from the panel, the integrator's names for the lock's user numbers, the
intercom door it opens, a cloud lock's read every N minutes."""

from __future__ import annotations

import re
import time
from typing import Any

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, config_validation as cv, device_registry as dr, entity_registry as er, label_registry as lr

from . import backup
from .const import DOMAIN
from .storage import HaufloStore
from .tuning import TUNING

STORE_KEY = f"{DOMAIN}.foreign"
DATA = f"{DOMAIN}_foreign"
MAX_IDS = TUNING["ownership"]["foreign_max"]  # a house's other integrations' entities, never a flood
NAME_MAX = TUNING["ownership"]["foreign_name_max"]
# A deploy's plan lives while it writes: idle this long (no write of it) and it is gone. The deploy writes right after
# sending it and each write keeps it alive, so 5 minutes outlasts a tunnel reconnect, never a deploy that is over
# (red team L2: an hour-long plan let a write through long after its deploy).
PLAN_TTL = TUNING["ownership"]["foreign_plan_ttl_s"]
# Plans at once (red team L1): one per deploy / «Поставить» running, by its id — a second integrator's never replaces
# the first's. More at once is refused (plans_busy), never one silently dropped.
PLANS_MAX = TUNING["ownership"]["foreign_plans_max"]
PLAN_FEATURE = "foreign_plan_id"
# A wizard's write names its plan (red team N7): `hauflo_plan` on the registry update — only that plan may allow it (a
# write of deploy A is never let through by deploy B's plan holding the same item); a plan gone or another's — refused
# (`plan_gone` / `not_in_plan`). The field is the agent's, taken off before HA sees the update. None: any live plan
# holding the item, as before (a backend from before this feature).
WRITE_PLAN_FEATURE = "foreign_write_plan"
PLAN_KEY = "hauflo_plan"
PLAN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
LEGACY_PLAN = ""  # a plan sent with no id (a backend before plan ids): one such, replaced by the next
LABELS_MAX = TUNING["ownership"]["foreign_labels_max"]
FEATURE = "foreign_wizard"
MEMBERS_FEATURE = "foreign_scene_members"
MEMBER_DOMAINS = frozenset({"light", "cover"})
# What the family dashboard's own cards may name from another integration (0.33, Р50/Р51): a room's light, blind,
# thermostat, socket, player — never a lock, an alarm panel, a siren (no domain here), a gate (protected) or the bus.
SHOWN_DOMAINS = frozenset(TUNING["ownership"]["foreign_shown_domains"])
SHOWN_FEATURE = "foreign_shown"
ROLE_PREFIX = "hauflo_role_"
_ROLE_RE = re.compile(r"^hauflo_role_[a-z0-9_]{1,40}$")
# Only the canon's roles (the cards'): any other name would make a label of its own — the registry flooded (red team 3).
ROLES = frozenset(TUNING["ownership"]["foreign_roles"])
# An entity with no device: only of a domain the cards draw — never HA's machinery (a person, an automation, a zone).
DEVICELESS_DOMAINS = frozenset(TUNING["ownership"]["foreign_deviceless_domains"])
# What a name never carries into HA: C0 / C1 controls and the bidi overrides that flip how it reads (red team 3).
UNSAFE_NAME = re.compile("[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]")
# Never the wizard's, whatever the plan says: the bus (KNX — Hauflo's own deploy owns it), Hauflo's own, the phones.
NEVER_PLATFORMS = frozenset({"knx", DOMAIN, "mobile_app"})
DEVICE_KEYS = frozenset({"area_id", "name_by_user", "labels"})
ENTITY_KEYS = frozenset({"area_id", "name", "hidden_by", "labels"})
DEVICE_UPDATE = "config/device_registry/update"
ENTITY_UPDATE = "config/entity_registry/update"
LOCKS_FEATURE = "foreign_locks"
LOCK_KEYS = frozenset({"push", "alarms", "users", "names", "door", "poll"})
_LT = TUNING["locks"]
_USER_ID = re.compile(r"^[0-9a-f]{32}$")
_DOOR_ID = re.compile(r"^[a-z0-9_]{1,64}$")
_USER_KEY = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


def device_entry_ids(device: Any) -> set[str]:
    """The config entries a device belongs to, on every HA the agent supports. HA 2026.x: one `config_entry_id` (the
    `config_entries` set is a deprecated shim, logged on each read, gone in 2027.10); a restored composite device (an
    old id of a split one) still answers `config_entries` with the union, unlogged; older HA: only `config_entries`."""
    one = getattr(device, "config_entry_id", None)
    if isinstance(one, str) and one and getattr(device, "is_composite_device", False) is not True:
        return {one}
    return {x for x in getattr(device, "config_entries", None) or () if isinstance(x, str)}


def device_dict_entry_ids(d: dict[str, Any]) -> list[str]:
    """The same for a device as `config/device_registry/list` sends it: `config_entry_id` first (the `config_entries`
    list is kept there only until HA 2027.8)."""
    one = d.get("config_entry_id")
    if isinstance(one, str) and one:
        return [one]
    return [x for x in d.get("config_entries") or [] if isinstance(x, str)]


class Flags:
    def __init__(self, hass: HomeAssistant) -> None:
        self.notify: dict[str, bool] = {}
        # The deploys' plans by id (in memory: a restart asks the deploy for them again):
        # {plan id: {"areas", "devices", "entities": set, "at": last use}}. Each item is written once by its plan.
        self.plans: dict[str, dict[str, Any]] = {}
        # Before Hauflo's first write: "d:<device id>" / "e:<entity registry id>" → {"was": {...}, "set": {...}}.
        self.journal: dict[str, dict[str, dict[str, Any]]] = {}
        # Other integrations' lights / blinds the integrator put in Hauflo's scenes and groups (399-6): entity ids.
        self.members: set[str] = set()
        # Р56: the locks' settings (locks.py) and who hears their change (the locks' listeners, the intercom's button).
        self.locks: dict[str, dict[str, Any]] = {}
        self.on_locks: Any = None
        # The wizard's «Замок» facts (locks.Locks.wizard with the intercom's doors), set by the agent: None — no locks yet.
        self.lock_info: Any = None
        self._store = HaufloStore(hass, STORE_KEY)

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        raw = data.get("notify") if isinstance(data, dict) else None
        self.notify = {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, bool)} if isinstance(raw, dict) else {}
        j = data.get("journal") if isinstance(data, dict) else None
        self.journal = {k: v for k, v in j.items() if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("was"), dict) and isinstance(v.get("set"), dict)} if isinstance(j, dict) else {}
        self.members = members_ok(data.get("members") if isinstance(data, dict) else None) or set()
        self.locks = locks_ok(data.get("locks") if isinstance(data, dict) else None) or {}

    def _data(self) -> dict[str, Any]:
        return {"notify": self.notify, "journal": self.journal, "members": sorted(self.members), "locks": self.locks}

    def save(self) -> None:
        self._store.async_delay_save(self._data, 0)

    def set_notify(self, m: dict[str, bool]) -> None:
        self.notify = dict(m)
        self.save()

    def set_members(self, ids: set[str]) -> None:
        self.members = set(ids)
        self.save()

    def set_locks(self, m: dict[str, dict[str, Any]]) -> None:
        was = self.locks
        self.locks = m
        self.save()
        if callable(self.on_locks):
            self.on_locks(was, m)

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


def members_ok(v: Any) -> set[str] | None:
    """{"members": [...]}: entity ids of lights / covers only, at most MAX_IDS (anything else — the whole body refused)."""
    if not isinstance(v, list) or len(v) > MAX_IDS:
        return None
    if not all(isinstance(x, str) and len(x) <= 255 and cv.valid_entity_id(x) and x.split(".", 1)[0] in MEMBER_DOMAINS for x in v):
        return None
    return set(v)


def locks_ok(v: Any) -> dict[str, dict[str, Any]] | None:
    """{"locks": {lock entity: {push?, alarms?: bool, users?: [HA user ids], names?: {user no.: name}, door?: intercom
    door id | null, poll?: minutes}}} — anything else refuses the whole body (a flood or a garbage name never kept)."""
    if not isinstance(v, dict) or len(v) > _LT["locks_max"]:
        return None
    out: dict[str, dict[str, Any]] = {}
    for eid, s in v.items():
        if not (isinstance(eid, str) and cv.valid_entity_id(eid) and eid.startswith("lock.") and isinstance(s, dict) and not set(s) - LOCK_KEYS):
            return None
        c: dict[str, Any] = {}
        for k in ("push", "alarms"):
            if k in s:
                if not isinstance(s[k], bool):
                    return None
                c[k] = s[k]
        users = s.get("users", [])
        if not isinstance(users, list) or len(users) > _LT["users_max"] or not all(isinstance(u, str) and _USER_ID.match(u) for u in users):
            return None
        if users:
            c["users"] = sorted(set(users))
        names = s.get("names", {})
        if not isinstance(names, dict) or len(names) > _LT["names_max"]:
            return None
        for k, n in names.items():
            if not (isinstance(k, str) and _USER_KEY.match(k) and isinstance(n, str) and 0 < len(n.strip()) <= _LT["name_max"] and not UNSAFE_NAME.search(n)):
                return None
        if names:
            c["names"] = {k: n.strip() for k, n in names.items()}
        door = s.get("door")
        if door is not None and not (isinstance(door, str) and _DOOR_ID.match(door)):
            return None
        if door:
            c["door"] = door
        poll = s.get("poll", 0)
        if isinstance(poll, bool) or not isinstance(poll, int) or not 0 <= poll <= _LT["poll_max_min"]:
            return None
        if poll:
            c["poll"] = poll
        out[eid] = c
    return out


def locks_of(hass: HomeAssistant | None) -> dict[str, dict[str, Any]]:
    f = flags(hass)
    return f.locks if f is not None else {}


def member_refusal(hass: HomeAssistant | None, entity_id: Any) -> str | None:
    """Why another integration's entity may not be in a Hauflo scene / group (None — it may). Each check fails closed;
    a Hauflo entity is never judged here (_hauflo_entity is)."""
    if not isinstance(entity_id, str) or entity_id.split(".", 1)[0] not in MEMBER_DOMAINS:
        return "domain_not_allowed"
    f = flags(hass)
    if hass is None or f is None or entity_id not in f.members:
        return "not_in_plan"
    reg = er.async_get(hass).async_get(entity_id)
    if reg is None:
        return "unknown_entity"  # no registry entry: a YAML one nobody can read
    if reg.platform in NEVER_PLATFORMS:
        return "platform_not_allowed"
    if reg.disabled_by is not None:
        return "disabled"
    if reg.hidden_by == er.RegistryEntryHider.USER:
        return "hidden_by_user"
    if not reg.config_entry_id:
        return "no_config_entry"
    st = hass.states.get(entity_id)
    if st is None:
        return "no_state"  # not loaded: what a group holds can't be read
    if _is_protected(hass, entity_id):
        return "protected"
    from . import _GATE_NAME, _PROTECTED_COVERS, _helper_operates_known  # noqa: PLC0415 — the package imports this module

    if _helper_operates_known(hass, reg) is False:
        return "unreadable_helper"  # a template light / cover whose actions name nothing
    if entity_id.startswith("cover."):
        names = (st.attributes.get("friendly_name"), reg.name, reg.original_name, entity_id.split(".", 1)[1].replace("_", " "))
        if st.attributes.get("device_class") in _PROTECTED_COVERS or any(isinstance(n, str) and _GATE_NAME.search(n) for n in names):
            return "gate"  # a cover named / classed as a gate, whatever its integration says
    return None


def member_ok(hass: HomeAssistant | None, entity_id: Any) -> bool:
    return member_refusal(hass, entity_id) is None


def shown_refusal(hass: HomeAssistant | None, entity_id: Any) -> str | None:
    """Why another integration's entity may not be named on Hauflo's dashboard cards (None — it may). Shown only: the
    card operates it with the viewer's own HA rights, a dashboard action still names Hauflo's own only (service_ok).
    Each check fails closed, as member_refusal."""
    if not isinstance(entity_id, str) or not cv.valid_entity_id(entity_id) or entity_id.split(".", 1)[0] not in SHOWN_DOMAINS:
        return "domain_not_allowed"
    if hass is None:
        return "no_hass"
    reg = er.async_get(hass).async_get(entity_id)
    if reg is None:
        return "unknown_entity"
    if reg.platform in NEVER_PLATFORMS:
        return "platform_not_allowed"
    if reg.disabled_by is not None:
        return "disabled"
    if not reg.config_entry_id:
        return "no_config_entry"
    if _is_protected(hass, entity_id):
        return "protected"
    if entity_id.startswith("cover."):
        from . import _GATE_NAME, _PROTECTED_COVERS  # noqa: PLC0415 — the package imports this module

        st = hass.states.get(entity_id)
        names = ((st.attributes.get("friendly_name") if st else None), reg.name, reg.original_name, entity_id.split(".", 1)[1].replace("_", " "))
        if (st is not None and st.attributes.get("device_class") in _PROTECTED_COVERS) or reg.original_device_class in _PROTECTED_COVERS or reg.device_class in _PROTECTED_COVERS or any(isinstance(n, str) and _GATE_NAME.search(n) for n in names):
            return "gate"
    return None


def shown_ok(hass: HomeAssistant | None, entity_id: Any) -> bool:
    return shown_refusal(hass, entity_id) is None


# --- the wizard (п.57) ----------------------------------------------------------------------------------------------


def _ids(v: Any, check: Any = None) -> set[str] | None:
    if not isinstance(v, list) or len(v) > MAX_IDS or not all(isinstance(x, str) and x and len(x) <= 255 for x in v):
        return None
    if check is not None and not all(check(x) for x in v):
        return None
    return set(v)


def plan_ok(p: Any) -> dict[str, set[str]] | None:
    """{"areas": [area_id], "devices": [device_id], "entities": [entity_id]} — each list required (may be empty)."""
    if not isinstance(p, dict) or set(p) != {"areas", "devices", "entities"}:
        return None
    out = {"areas": _ids(p["areas"]), "devices": _ids(p["devices"]), "entities": _ids(p["entities"], cv.valid_entity_id)}
    return out if all(v is not None for v in out.values()) else None  # type: ignore[return-value]


def active_plans(hass: HomeAssistant | None) -> dict[str, dict[str, Any]]:
    """The live plans by id (one idle past PLAN_TTL is dropped here)."""
    f = flags(hass)
    if f is None:
        return {}
    now = time.time()
    for pid in [k for k, p in f.plans.items() if now - float(p.get("at") or 0) > PLAN_TTL]:
        f.plans.pop(pid, None)
    return f.plans


def _part(msg: dict[str, Any]) -> tuple[str, Any] | None:
    if msg.get("type") == DEVICE_UPDATE:
        return "devices", msg.get("device_id")
    if msg.get("type") == ENTITY_UPDATE:
        return "entities", msg.get("entity_id")
    return None


def _holding(hass: HomeAssistant | None, msg: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The live plans that hold this message's item, the oldest first — only the one it names, when it names one."""
    ref = _part(msg)
    if ref is None:
        return []
    named = msg.get(PLAN_KEY, None) if PLAN_KEY in msg else None
    held = [(k, p) for k, p in active_plans(hass).items() if ref[1] in p[ref[0]] and (PLAN_KEY not in msg or k == named)]
    return sorted(held, key=lambda kp: float(kp[1].get("at") or 0))


def planned(hass: HomeAssistant | None, msg: dict[str, Any]) -> bool:
    """The item of this registry update is in a deploy's live plan — or the update names a plan (N7: then the wizard's
    rules alone decide, a gone plan refused with its reason, never the click's placing): the wizard's rules decide."""
    return PLAN_KEY in msg or bool(_holding(hass, msg))


def drop_items(hass: HomeAssistant | None, devices: set[str], entities: set[str]) -> None:
    """These items leave every plan (put back by «Вернуть как было»: only a plan sent after writes them again)."""
    live = active_plans(hass)
    for pid, p in list(live.items()):
        p["devices"] = p["devices"] - devices
        p["entities"] = p["entities"] - entities
        if not p["devices"] and not p["entities"]:
            live.pop(pid, None)


def _consume(hass: HomeAssistant, pid: str, msg: dict[str, Any]) -> None:
    """A plan's item written: gone from that plan (the same write again is refused), the plan kept alive."""
    live = active_plans(hass)
    plan, ref = live.get(pid), _part(msg)
    if plan is None or ref is None:
        return
    plan[ref[0]] = plan[ref[0]] - {ref[1]}
    plan["at"] = time.time()
    if not plan["devices"] and not plan["entities"]:
        live.pop(pid, None)


def _labels_ok(hass: HomeAssistant, labels: Any) -> str | None:
    if not isinstance(labels, list) or len(labels) > LABELS_MAX or not all(isinstance(x, str) for x in labels):
        return "bad_labels"
    if not all(_ROLE_RE.match(x) and x[len(ROLE_PREFIX):] in ROLES for x in labels):
        return "label_not_hauflo_role"
    # Never one a voice assistant exposes by (Alice «by label»): a role label is the cards', not the voice's.
    alice = {str(e.options.get("label")) for e in hass.config_entries.async_entries("yandex_smart_home") if e.options.get("label")}
    if set(labels) & alice:
        return "label_not_hauflo_role"
    return None


def _name_ok(v: Any) -> bool:
    return v is None or (isinstance(v, str) and bool(v.strip()) and len(v) <= NAME_MAX and not UNSAFE_NAME.search(v))


def _device_refusal(hass: HomeAssistant, device_id: Any) -> str | None:
    """A device the wizard may touch: another integration's, a real one (not a service), nothing of the bus / Hauflo /
    a phone on it."""
    device = dr.async_get(hass).async_get(device_id) if isinstance(device_id, str) else None
    if device is None:
        return "unknown_device"
    if getattr(device, "entry_type", None) is not None:
        return "service_device"
    domains = {e.domain for eid in device_entry_ids(device) if (e := hass.config_entries.async_get_entry(eid))}
    ents = er.async_entries_for_device(er.async_get(hass), device.id, include_disabled_entities=True)
    if (domains | {e.platform for e in ents}) & NEVER_PLATFORMS:
        return "platform_not_allowed"
    return None


def _area_refusal(hass: HomeAssistant, plan: dict[str, Any], area_id: Any, was: Any, allow_none: bool) -> str | None:
    """Into a room of the plan that exists — or back to where it was before Hauflo (the journal's)."""
    if area_id is None:
        return None if allow_none or was is None else "area_not_in_plan"
    if not isinstance(area_id, str):
        return "bad_area"
    if area_id != was and area_id not in plan["areas"]:
        return "area_not_in_plan"
    return None if ar.async_get(hass).async_get_area(area_id) is not None else "unknown_area"


def _journal_key(hass: HomeAssistant, msg: dict[str, Any]) -> str | None:
    if msg.get("type") == DEVICE_UPDATE:
        return f"d:{msg.get('device_id')}"
    entry = er.async_get(hass).async_get(str(msg.get("entity_id")))
    return f"e:{entry.id}" if entry is not None else None


def _was(hass: HomeAssistant, msg: dict[str, Any], field: str) -> Any:
    """The value before Hauflo's first write (the journal's), or the current one."""
    f = flags(hass)
    key = _journal_key(hass, msg)
    rec = f.journal.get(key) if f is not None and key else None
    if rec is not None and field in rec["was"]:
        return rec["was"][field]
    return _current(hass, msg).get(field)


def _current(hass: HomeAssistant, msg: dict[str, Any]) -> dict[str, Any]:
    if msg.get("type") == DEVICE_UPDATE:
        d = dr.async_get(hass).async_get(str(msg.get("device_id")))
        return {} if d is None else {"area_id": d.area_id, "name_by_user": d.name_by_user, "labels": sorted(d.labels)}
    e = er.async_get(hass).async_get(str(msg.get("entity_id")))
    return {} if e is None else {"area_id": e.area_id, "name": e.name, "hidden_by": e.hidden_by.value if e.hidden_by is not None else None, "labels": sorted(e.labels)}


def _is_protected(hass: HomeAssistant, entity_id: str) -> bool:
    from . import _protected  # noqa: PLC0415 — the package imports this module

    return _protected(hass, entity_id)


def wizard_refusal(hass: HomeAssistant | None, msg: dict[str, Any]) -> str | None:
    """Why a device / entity registry update of another integration's item is refused (None — allowed). The reason
    goes back to Hauflo in the error message («Hauflo agent: not allowed (<reason>)»)."""
    return decide(hass, msg)[0]


def decide(hass: HomeAssistant | None, msg: dict[str, Any]) -> tuple[str | None, str | None]:
    """(why refused, None) or (None, the id of the plan that allows it): the oldest live plan holding the item whose
    rooms allow the write — a room is never taken from another deploy's plan."""
    live = active_plans(hass) if hass is not None else {}
    held = _holding(hass, msg)
    if not held:
        why = _refusal(hass, msg, {"areas": set(), "devices": set(), "entities": set()} if live else None)
        if PLAN_KEY in msg and why in ("not_in_plan", "no_plan"):
            # The plan it names: not a live one (gone, expired, never sent, a bad id) — or a live one without the item.
            named = msg.get(PLAN_KEY)
            why = "not_in_plan" if isinstance(named, str) and named in live else "plan_gone"
        return why, None
    why: str | None = None
    for pid, plan in held:
        if (r := _refusal(hass, msg, plan)) is None:
            return None, pid
        why = why or r
    return why, None


def _refusal(hass: HomeAssistant | None, msg: dict[str, Any], plan: dict[str, Any] | None) -> str | None:
    t = msg.get("type")
    if hass is None or t not in (DEVICE_UPDATE, ENTITY_UPDATE):
        return "not_allowed"
    device = t == DEVICE_UPDATE
    keys, ref = (DEVICE_KEYS, "device_id") if device else (ENTITY_KEYS, "entity_id")
    extra = set(msg) - {"id", "type", ref, PLAN_KEY} - keys
    if extra:
        return f"field_not_allowed:{sorted(extra)[0]}"
    if not set(msg) & keys:
        return "nothing_to_change"
    if PLAN_KEY in msg and not (isinstance(msg[PLAN_KEY], str) and PLAN_ID.match(msg[PLAN_KEY])):
        return "plan_gone"
    if plan is None:
        return "no_plan"
    if device:
        if (why := _device_refusal(hass, msg.get("device_id"))) is not None:
            return why
        if msg["device_id"] not in plan["devices"]:
            return "not_in_plan"
        if "name_by_user" in msg and not _name_ok(msg["name_by_user"]):
            return "bad_name"
    else:
        entry = er.async_get(hass).async_get(str(msg.get("entity_id")))
        if entry is None:
            return "unknown_entity"
        if entry.platform in NEVER_PLATFORMS:
            return "platform_not_allowed"
        if entry.device_id:
            if (why := _device_refusal(hass, entry.device_id)) is not None:
                return why
        elif not entry.unique_id:
            return "no_unique_id"
        elif entry.domain not in DEVICELESS_DOMAINS:
            return "system_entity"
        if entry.entity_id not in plan["entities"]:
            return "not_in_plan"
        # What opens the house (a gate / garage cover, a «Switch as X» over one, a group with a lock inside): never placed
        # in a room nor given a role by the wizard — the same rule as voice / scenes (_protected); name / hidden stay.
        if set(msg) & {"area_id", "labels"} and _is_protected(hass, entry.entity_id):
            return "protected_domain"
        if "hidden_by" in msg and msg["hidden_by"] not in ("user", None):
            return "bad_hidden_by"
        if "name" in msg and not _name_ok(msg["name"]):
            return "bad_name"
    # A device's null: out of the project's room it stands in («Без комнаты», accept 4A Р66) — wizard_prepare puts it back
    # where it stood before Hauflo; out of a family's area it never moves (that one is not Hauflo's to take).
    out_of_room = device and msg.get("area_id", "") is None and _current(hass, msg).get("area_id") in plan["areas"]
    if "area_id" in msg and (why := _area_refusal(hass, plan, msg["area_id"], _was(hass, msg, "area_id"), allow_none=not device or out_of_room)) is not None:
        return why
    if "labels" in msg and (why := _labels_ok(hass, msg["labels"])) is not None:
        return why
    return None


def wizard_prepare(hass: HomeAssistant, msg: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """Before an allowed write goes to HA: keeps the item's values as they were (first write only), merges the role
    labels with the family's own (HA replaces the whole list), creates a missing hauflo_role_* label. Returns the
    message to send (None — refused: a label of that id can't be made) and the label ids it created."""
    f = flags(hass)
    created: list[str] = []
    _why, pid = decide(hass, msg)
    out = {k: v for k, v in msg.items() if k != PLAN_KEY}  # the agent's own field: HA's schema takes no other
    if msg.get("type") == DEVICE_UPDATE and "area_id" in msg and msg["area_id"] is None:
        # «Без комнаты»: back to the family's area it stood in before Hauflo's first move (the journal's), if that area is
        # still there and not one of the project's rooms; else no area.
        was = _was(hass, msg, "area_id")
        rooms = (active_plans(hass).get(pid) or {}).get("areas") or () if pid is not None else ()
        if isinstance(was, str) and was not in rooms and ar.async_get(hass).async_get_area(was) is not None:
            out["area_id"] = was
    if "labels" in msg:
        reg = lr.async_get(hass)
        for lid in dict.fromkeys(msg["labels"]):
            if reg.async_get_label(lid) is not None:
                continue
            if reg.async_get_label_by_name(lid) is not None:
                return None, created  # a label of that name but another id: never guessed
            label = reg.async_create(lid, icon="mdi:tag-outline", description="Hauflo")
            if label.label_id != lid:
                reg.async_delete(label.label_id)
                return None, created
            created.append(lid)
        cur = _current(hass, msg).get("labels") or []
        out["labels"] = sorted({x for x in cur if not x.startswith(ROLE_PREFIX)} | set(msg["labels"]))
    key = _journal_key(hass, msg)
    if f is not None and key is not None:
        cur = _current(hass, msg)
        rec = f.journal.setdefault(key, {"was": {}, "set": {}})
        fields = (DEVICE_KEYS if msg.get("type") == DEVICE_UPDATE else ENTITY_KEYS) & set(out)
        for k in fields:
            rec["was"].setdefault(k, cur.get(k))
            rec["set"][k] = sorted(out[k]) if k == "labels" else out[k]
        f.save()
    if pid is not None:
        _consume(hass, pid, msg)  # one-shot (red team L2): the same write again is refused — the plan is not a pass
    return out, created


def revert(hass: HomeAssistant, devices: set[str], entities: set[str]) -> dict[str, Any]:
    """«Вернуть как было»: what Hauflo wrote to these items back to the values before it. A field the integrator
    changed in HA since (not Hauflo's value any more) stays as he set it; role labels go, the family's stay."""
    f = flags(hass)
    out: dict[str, Any] = {"devices": [], "entities": [], "kept": {}, "unknown": []}
    if f is None:
        return out
    dreg, ereg = dr.async_get(hass), er.async_get(hass)
    targets: list[tuple[str, str, Any]] = [("d", d, dreg.async_get(d)) for d in sorted(devices)]
    # A device goes back with its entities Hauflo wrote (the wizard's name template renamed them, the page sends the
    # device alone — acceptance B 06.11, D3); an entity named twice is one target.
    seen = set(entities)
    for d in sorted(devices):
        if dreg.async_get(d) is None:
            continue
        for e in er.async_entries_for_device(ereg, d, include_disabled_entities=True):
            if e.entity_id not in seen and f"e:{e.id}" in f.journal:
                seen.add(e.entity_id)
                entities = entities | {e.entity_id}
    for eid in sorted(entities):
        e = ereg.async_get(eid)
        targets.append(("e", eid, e))
    for kind, ref, item in targets:
        key = f"{kind}:{ref}" if kind == "d" else (f"e:{item.id}" if item is not None else None)
        rec = f.journal.get(key) if key else None
        if item is None or rec is None:
            out["unknown"].append(ref)
            continue
        msg = {"type": DEVICE_UPDATE, "device_id": ref} if kind == "d" else {"type": ENTITY_UPDATE, "entity_id": ref}
        cur = _current(hass, msg)
        changes: dict[str, Any] = {}
        kept: list[str] = []
        for k, was in rec["was"].items():
            if k == "labels":
                if cur.get("labels") != rec["set"].get("labels"):
                    kept.append(k)  # his own labels since: only Hauflo's role labels go
                ours = {x for x in cur.get("labels") or [] if x.startswith(ROLE_PREFIX) and x not in (was or [])}
                if ours:
                    changes["labels"] = {x for x in cur.get("labels") or [] if x not in ours}
            elif cur.get(k) == rec["set"].get(k):
                if cur.get(k) != was:
                    changes[k] = was
            else:
                kept.append(k)
        if "area_id" in changes and changes["area_id"] is not None and ar.async_get(hass).async_get_area(changes["area_id"]) is None:
            changes.pop("area_id")  # the room was deleted since: left where it is
            kept.append("area_id")
        if kind == "d":
            dreg.async_update_device(ref, **changes)
        else:
            if "hidden_by" in changes:
                changes["hidden_by"] = er.RegistryEntryHider(changes["hidden_by"]) if changes["hidden_by"] else None
            ereg.async_update_entity(ref, **changes)
        f.journal.pop(key, None)
        drop_items(hass, {ref} if kind == "d" else set(), {ref} if kind == "e" else set())
        out["devices" if kind == "d" else "entities"].append(ref)
        if kept:
            out["kept"][ref] = sorted(set(kept))
    f.save()
    return out


class ForeignView(HomeAssistantView):
    """GET → {"notify": {...}, "features": [...], "plan": bool, "journal": {"devices": [...], "entities": [...]}, "locks":
    {...}, "lock_info": {"seen", "people", "doors"} (Р56, only with a lock in the house)};
    POST (admin only), any of:
      {"notify": {entity_id: bool}} — replaces the alarm sources map;
      {"plan": {"areas": [...], "devices": [...], "entities": [...]} | null, "plan_id": "<id>"} — a deploy's plan (п.57),
        by its id (none — the one legacy plan): each item written once, gone when idle PLAN_TTL; null — dropped;
      {"revert": {"devices": [...], "entities": [...]}} — «Вернуть как было» (a fresh backup first, as any change);
      {"locks": {lock entity: {...}}} — the locks' settings (Р56, locks_ok), the whole map;
      {"members": [entity_id, ...]} — other integrations' lights / covers for Hauflo's scenes and groups (399-6), the
        whole set; the answer says each one refused and why ({"refused": {entity_id: reason}})."""

    url = "/api/hauflo/foreign"
    name = "api:hauflo:foreign"
    requires_auth = True

    async def get(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        hass = request.app[KEY_HASS]
        f = flags(hass)
        info = await f.lock_info() if f is not None and callable(f.lock_info) else None
        return self.json({"notify": f.notify if f is not None else {}, "features": [FEATURE, PLAN_FEATURE, WRITE_PLAN_FEATURE, MEMBERS_FEATURE, LOCKS_FEATURE], "plan": bool(active_plans(hass)), "journal": _journal_list(hass), "locks": f.locks if f is not None else {}, **({"lock_info": info} if info else {})})

    async def post(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict) or not body or set(body) - {"notify", "plan", "plan_id", "revert", "members", "locks"}:
            return self.json_message("notify / plan / revert / members / locks expected", 400)
        pid = body.get("plan_id", LEGACY_PLAN)
        if "plan_id" in body and ("plan" not in body or not isinstance(pid, str) or not PLAN_ID.match(pid)):
            return self.json_message("plan_id: [A-Za-z0-9_-]{1,64} with a plan expected", 400)
        if "notify" in body and not notify_ok(body["notify"]):
            return self.json_message("notify: {entity_id: bool} expected", 400)
        members = members_ok(body.get("members")) if "members" in body else None
        if "members" in body and members is None:
            return self.json_message("members: a list of light / cover entity ids expected", 400)
        locks = locks_ok(body.get("locks")) if "locks" in body else None
        if "locks" in body and locks is None:
            return self.json_message("locks: {lock entity: {push, alarms, users, names, door, poll}} expected", 400)
        plan = plan_ok(body["plan"]) if body.get("plan") is not None else None
        if "plan" in body and body["plan"] is not None and plan is None:
            return self.json_message("plan: {areas, devices, entities} lists of ids expected", 400)
        rv = body.get("revert")
        if "revert" in body and (not isinstance(rv, dict) or set(rv) - {"devices", "entities"} or _ids(rv.get("devices", [])) is None or _ids(rv.get("entities", [])) is None):
            return self.json_message("revert: {devices, entities} lists of ids expected", 400)
        hass = request.app[KEY_HASS]
        f = flags(hass)
        if f is None:
            return self.json_message("not ready", 409)
        if "revert" in body and not backup.fresh():
            return self.json({"code": "backup_required", "message": "Hauflo agent: a backup first"}, 428)
        live = active_plans(hass)
        if plan is not None and pid not in live and len(live) >= PLANS_MAX:
            # Never another deploy's plan dropped for this one: said, and tried again when one is over.
            return self.json({"code": "plans_busy", "message": "Hauflo agent: not allowed (plans_busy)"}, 409)
        out: dict[str, Any] = {}
        if "notify" in body:
            f.set_notify(body["notify"])
        if "plan" in body:
            if plan is None:
                live.pop(pid, None)
            else:
                live[pid] = {**plan, "at": time.time()}
        if members is not None:
            f.set_members(members)
            out["refused"] = {e: r for e in sorted(members) if (r := member_refusal(hass, e)) is not None}
        if locks is not None:
            f.set_locks(locks)
            out["locks"] = f.locks
        if "revert" in body:
            out["reverted"] = revert(hass, set(rv.get("devices", [])), set(rv.get("entities", [])))
        return self.json({"notify": f.notify, "plan": bool(active_plans(hass)), **out})


def _journal_list(hass: HomeAssistant) -> dict[str, list[str]]:
    """What Hauflo changed and can put back: device ids, entity ids (as they are now)."""
    f = flags(hass)
    if f is None:
        return {"devices": [], "entities": []}
    ereg = er.async_get(hass)
    ids = {e.id: e.entity_id for e in ereg.entities.values()}
    return {
        "devices": sorted(k[2:] for k in f.journal if k.startswith("d:")),
        "entities": sorted(ids[k[2:]] for k in f.journal if k.startswith("e:") and k[2:] in ids),
    }
