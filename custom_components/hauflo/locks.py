"""Р56: smart door locks of any integration (Z2M / ZHA / Matter / a cloud one) — recognised by HA's metadata only: the
`lock` domain and the other entities of the same device (an event entity or a text sensor saying what happened, a
tamper sensor, a battery), never a brand or an integration's name.

What a lock does is read from three places, merged within `merge_s` (Z2M sends the lock's state, its `action` and
`action_source_name` / `action_user` as separate updates): the lock's own state (locked / unlocked / jammed), an event /
text sensor of its device («fingerprint_unlock», «lock_failure_invalid_pin_or_id», Matter's «LockOperation»), the
user / method sensors or attributes. The words are tuning.json `locks`, never code.

The push («🔓 Входная дверь» / «открыта отпечатком · Мария · 18:02») goes to the phones with «пуш о звонке» on, by the
alarms' own path (notify.NotifyEngine._send): an alarm (jammed, tamper, `wrong_count` wrong attempts within
`wrong_window_s`) by default, each unlock only once the integrator turned it on. Tamper is critical (through silent
mode and the quiet hours); anything else in the quiet hours comes without a sound.

`hauflo.lock` (entity_id, action lock / unlock / open): the panel's buttons. Locking is always allowed; unlocking /
opening the door only by the people the integrator listed (none listed — anyone), else «Нет прав». Honest limit: it
guards Hauflo's panel; HA's own `lock.unlock` stays HA's (its users' rights). A second tap while the first command is
under way is the same command (one call to the lock).

A cloud lock (its integration's iot_class cloud_*): HA reads it as rarely as the cloud allows; the integrator may ask
for a read every N minutes (`poll`), and the card says «обновляется раз в N мин»."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components.lock import LockEntityFeature
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, ServiceCall, State, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er, translation
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import foreign
from .const import DOMAIN
from .storage import HaufloStore
from .tuning import TUNING

_LOGGER = logging.getLogger(__name__)

FEATURE = foreign.LOCKS_FEATURE
SERVICE = "lock"
STORE_KEY = f"{DOMAIN}.locks"
T = TUNING["locks"]
IGNORE = re.compile(T["ignore"], re.I)
OPS: list[tuple[str, re.Pattern[str]]] = [(k, re.compile(v, re.I)) for k, v in T["ops"].items()]
METHODS: list[tuple[str, re.Pattern[str]]] = [(k, re.compile(v, re.I)) for k, v in T["methods"].items()]
STATE_OPS: dict[str, str] = T["state_ops"]
OP_ATTRS: list[str] = T["op_attrs"]
USER_ATTRS: list[str] = T["user_attrs"]
METHOD_ATTRS: list[str] = T["method_attrs"]
USER_SENSOR = re.compile(T["user_sensor_words"], re.I)
METHOD_SENSOR = re.compile(T["method_sensor_words"], re.I)
ICONS: dict[str, str] = T["icons"]
ALARM_OPS = frozenset(T["alarm_ops"])
CRITICAL_OPS = frozenset(T["critical_ops"])
# Which of two pieces within one merge says the event: an alarm over an unlock over a lock.
RANK = {op: i for i, op in enumerate(reversed(list(T["ops"])))}
WRONG_COUNT = int(T["wrong_count"])
WRONG_WINDOW = float(T["wrong_window_s"])
MERGE_S = float(T["merge_s"])
EVENT_FRESH = TUNING["link"]["event_recent_s"]
NOT_A_STATE = (STATE_UNAVAILABLE, STATE_UNKNOWN)
SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_id, vol.Required("action"): vol.In(["lock", "unlock", "open"])})


def op_of(text: str) -> str | None:
    """«fingerprint_unlock» → unlock, «lock_failure_invalid_pin_or_id» → wrong, «LockFactoryReset» → None."""
    if not text or IGNORE.search(text):
        return None
    return next((op for op, words in OPS if words.search(text)), None)


def method_of(text: str) -> str | None:
    if not text:
        return None
    return next((m for m, words in METHODS if words.search(text)), None)


def _attr(state: State | None, keys: list[str]) -> Any:
    if state is None:
        return None
    for k in keys:
        v = state.attributes.get(k)
        if v not in (None, "", [], {}) and not isinstance(v, (dict, list, bool)):
            return v
    return None


def _text_sensor(e: er.RegistryEntry) -> bool:
    """A sensor saying words, not a number: no unit, no state class, no class or «enum» (Z2M's `action`)."""
    return e.domain == "sensor" and not e.unit_of_measurement and not (e.capabilities or {}).get("state_class") and (e.device_class or e.original_device_class) in (None, "enum")


class Locks:
    """The house's locks: what each did last, the pushes, the panel's service, the cloud ones' reads."""

    def __init__(self, hass: HomeAssistant, engine: Callable[[], Any]) -> None:
        self.hass = hass
        self._engine = engine
        self.devices: dict[str, dict[str, Any]] = {}  # lock entity → {"device", "sources", "users", "methods", "tamper", "battery"}
        self.owner: dict[str, str] = {}  # a device's other entity → its lock
        self.cloud: dict[str, bool] = {}
        self.last: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._wrong: dict[str, list[float]] = {}
        self._busy: set[str] = set()
        self._unsubs: list[CALLBACK_TYPE] = []
        self._listen: CALLBACK_TYPE | None = None
        self._polls: list[CALLBACK_TYPE] = []
        self._start: CALLBACK_TYPE | None = None  # HA's «started» once-listener, until it fires
        self._store = HaufloStore(hass, STORE_KEY)

    # --- settings ------------------------------------------------------------------------------------------------

    def settings(self, eid: str) -> dict[str, Any]:
        f = foreign.flags(self.hass)
        return dict((f.locks if f is not None else {}).get(eid) or {})

    def may_unlock(self, eid: str, user_id: str | None) -> bool:
        """None (an automation, the system) or nobody listed — yes; else only the listed people."""
        users = self.settings(eid).get("users") or []
        return user_id is None or not users or user_id in users

    def linked(self, door_id: str) -> str | None:
        """The lock that opens this intercom door (the integrator's link) — even a lock gone since: «не открылась» then,
        never the panel's relay quietly «opening» a door it may not be wired to."""
        f = foreign.flags(self.hass)
        for eid, s in sorted((f.locks if f is not None else {}).items()):
            if s.get("door") == door_id:
                return eid
        return None

    # --- discovery -----------------------------------------------------------------------------------------------

    @callback
    def discover(self) -> None:
        reg = er.async_get(self.hass)
        by_device: dict[str, list[er.RegistryEntry]] = {}
        for e in reg.entities.values():
            if e.device_id and not e.disabled_by:
                by_device.setdefault(e.device_id, []).append(e)
        devices: dict[str, dict[str, Any]] = {}
        for e in sorted(reg.entities.values(), key=lambda x: x.entity_id):
            if e.domain != "lock" or e.disabled_by or e.platform == DOMAIN or len(devices) >= T["locks_max"]:
                continue
            d: dict[str, Any] = {"device": e.device_id, "sources": [], "users": [], "methods": [], "tamper": None, "battery": None}
            for x in by_device.get(e.device_id or "", []) if e.device_id else []:
                dc = x.device_class or x.original_device_class
                obj = x.entity_id.split(".", 1)[1]
                if x.domain == "event":
                    d["sources"].append(x.entity_id)
                elif x.domain == "binary_sensor" and dc == "tamper":
                    d["tamper"] = x.entity_id
                elif x.domain in ("sensor", "binary_sensor") and dc == "battery" and d["battery"] is None:
                    d["battery"] = x.entity_id
                elif _text_sensor(x):
                    d["users" if USER_SENSOR.search(obj) else "methods" if METHOD_SENSOR.search(obj) else "sources"].append(x.entity_id)
            devices[e.entity_id] = d
        self.devices = devices
        self.owner = {x: lock for lock, d in devices.items() for x in [*d["sources"], *d["users"], *d["methods"], *([d["tamper"]] if d["tamper"] else [])]}

    def owns_event(self, eid: str) -> bool:
        """An event entity of a lock's device: its press is the lock's, not a «кнопка» ring (notify.py)."""
        return eid in self.owner

    async def _cloud_of(self) -> None:
        reg = er.async_get(self.hass)
        out: dict[str, bool] = {}
        for eid in self.devices:
            e = reg.async_get(eid)
            try:
                integ = await async_get_integration(self.hass, e.platform) if e is not None else None
            except Exception:  # noqa: BLE001 — a custom integration gone: not known as cloud
                integ = None
            out[eid] = bool(integ is not None and str(integ.manifest.get("iot_class") or "").startswith("cloud"))
        self.cloud = out

    # --- life ----------------------------------------------------------------------------------------------------

    async def async_start(self) -> None:
        saved = await self._store.async_load()
        if isinstance(saved, dict):
            self.last = {k: v for k, v in saved.items() if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("op"), str)}
        await self.apply()
        self._unsubs.append(self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_changed))
        if not self.hass.is_running:
            self._start = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self._started)

    async def async_save(self) -> None:
        await self._store.async_save(self.last)

    async def _started(self, _event: Event) -> None:
        # Fired: HA drops a once-listener itself, removing it again at stop logged «Unable to remove unknown job listener»
        # (reaccept 3, п.21 — as daylight / habits / season keep theirs).
        self._start = None
        await self.apply()

    @callback
    def _registry_changed(self, event: Event) -> None:
        eid = event.data.get("entity_id") or ""
        if eid.startswith("lock.") or event.data.get("action") == "create" or eid in self.owner:
            self.hass.async_create_task(self.apply())

    async def apply(self) -> None:
        """(Re)read the locks and their devices, listen to them, (re)start the cloud ones' reads."""
        self.discover()
        await self._cloud_of()
        for u in self._polls:
            u()
        self._polls = []
        if self._listen is not None:
            self._listen()
            self._listen = None
        ids = sorted({*self.devices, *self.owner})
        if ids:
            self._listen = async_track_state_change_event(self.hass, ids, self._on_state)
        for eid in self.devices:
            minutes = int(self.settings(eid).get("poll") or 0)
            if minutes and self.cloud.get(eid):
                self._polls.append(async_track_time_interval(self.hass, self._poller(eid), timedelta(minutes=minutes)))

    def _poller(self, eid: str) -> Callable[[Any], Any]:
        async def _poll(_now: Any) -> None:
            try:
                await self.hass.services.async_call("homeassistant", "update_entity", {"entity_id": eid}, blocking=False)
            except HomeAssistantError as err:
                _LOGGER.debug("Hauflo: lock %s read failed: %s", eid, err)

        return _poll

    @callback
    def stop(self) -> None:
        for u in [*self._unsubs, *self._polls, *([self._start] if self._start else [])]:
            u()
        self._unsubs, self._polls, self._start = [], [], None
        if self._listen is not None:
            self._listen()
            self._listen = None
        for p in self._pending.values():
            if p.get("timer"):
                p["timer"]()
        self._pending.clear()

    # --- what happened -------------------------------------------------------------------------------------------

    @callback
    def _on_state(self, event: Event) -> None:
        new: State | None = event.data.get("new_state")
        old: State | None = event.data.get("old_state")
        if new is None:
            return
        eid = new.entity_id
        lock = eid if eid in self.devices else self.owner.get(eid)
        if lock is None:
            return
        d = self.devices[lock]
        if eid == lock:
            # A lock's state: only a real change (not a restart's unavailable → locked, not an attribute's).
            if old is None or old.state in NOT_A_STATE or old.state == new.state or new.state not in STATE_OPS:
                return
            self._piece(lock, STATE_OPS[new.state], self._method_attr(new), _attr(new, USER_ATTRS), new.context.user_id)
        elif new.domain == "event":
            at = dt_util.parse_datetime(new.state)
            if old is not None and old.state == new.state or at is None or at.tzinfo is None or abs((dt_util.utcnow() - at).total_seconds()) > EVENT_FRESH:
                return
            said = str(new.attributes.get("event_type") or "")
            op_said = _attr(new, OP_ATTRS)
            self._piece(lock, op_of(str(op_said)) if op_said is not None else op_of(said), self._method_attr(new) or method_of(said), _attr(new, USER_ATTRS), None)
        elif eid == d["tamper"]:
            if new.state == "on" and old is not None and old.state == "off":
                self._piece(lock, "tamper", None, None, None)
        elif new.state in NOT_A_STATE or (old is not None and old.state == new.state):
            return
        elif eid in d["users"]:
            self._piece(lock, None, None, new.state, None)
        elif eid in d["methods"]:
            self._piece(lock, None, method_of(new.state), None, None)
        else:
            self._piece(lock, op_of(new.state), self._method_attr(new) or method_of(new.state), _attr(new, USER_ATTRS), None)

    @staticmethod
    def _method_attr(state: State) -> str | None:
        v = _attr(state, METHOD_ATTRS)
        return method_of(str(v)) if v is not None else None

    @callback
    def _piece(self, lock: str, op: str | None, method: str | None, user: Any, user_id: str | None) -> None:
        """One piece of an event; pieces within `merge_s` are one event (Z2M: state, action, source, user apart)."""
        if isinstance(user, str) and not user.strip().isdigit() and (m := method_of(user)) is not None:
            # «changed_by: Keypad» (a lock saying how, not who): a method, never a person's name.
            method, user = method or m, None
        if op is None and method is None and user in (None, "") and user_id is None:
            return
        p = self._pending.get(lock)
        if p is None:
            p = self._pending[lock] = {"op": None, "method": None, "user": None, "user_id": None, "at": dt_util.utcnow(), "timer": None}
            p["timer"] = async_call_later(self.hass, MERGE_S, callback(lambda _now: self._finish(lock)))
        if op is not None and (p["op"] is None or RANK[op] > RANK[p["op"]]):
            p["op"] = op
        if method is not None and p["method"] is None:
            p["method"] = method
        if user not in (None, "") and p["user"] is None:
            p["user"] = user
        if user_id is not None and p["user_id"] is None:
            p["user_id"] = user_id
            p["method"] = p["method"] or "app"

    @callback
    def _finish(self, lock: str) -> None:
        p = self._pending.pop(lock, None)
        if p is None or p["op"] is None:
            return
        self._fill(lock, p)
        self.hass.async_create_task(self._told(lock, p))

    def _fill(self, lock: str, p: dict[str, Any]) -> None:
        """Who and how the lock just said again unchanged (the same person, the same finger: no state change, only
        a «reported» stamp) — read from the device's user / method sensors stamped within the event's window."""
        d = self.devices.get(lock) or {}
        for key, eids in (("user", d.get("users") or []), ("method", d.get("methods") or [])):
            if p[key] is not None:
                continue
            for eid in eids:
                st = self.hass.states.get(eid)
                if st is None or st.state in NOT_A_STATE or st.state == "":
                    continue
                if abs((st.last_reported - p["at"]).total_seconds()) > MERGE_S:
                    continue
                p[key] = st.state if key == "user" else method_of(st.state)
                if p[key] is not None:
                    break

    async def _told(self, lock: str, p: dict[str, Any]) -> None:
        op = p["op"]
        user = await self._who(lock, p)
        self.last[lock] = {"op": op, "method": p["method"], "user": user, "at": p["at"].isoformat()}
        self._store.async_delay_save(lambda: self.last, 2)
        s = self.settings(lock)
        n = 1
        if op == "wrong":
            now = time.monotonic()
            log = [t for t in self._wrong.get(lock, []) if now - t < WRONG_WINDOW] + [now]
            self._wrong[lock] = log
            if len(log) < WRONG_COUNT:
                return
            self._wrong[lock] = []
            n = len(log)
        if op in ALARM_OPS:
            if s.get("alarms") is False:
                return
        elif not (op == "unlock" and s.get("push") is True):
            return
        await self._push(lock, op, p["method"], user, p["at"], n)

    async def _who(self, lock: str, p: dict[str, Any]) -> str:
        """The person: the integrator's name for a lock's user number, a name the lock says, an HA user who pressed."""
        names = self.settings(lock).get("names") or {}
        u = p["user"]
        if u not in (None, ""):
            key = str(u).strip()
            if key in names:
                return str(names[key])
            if key.isdigit():
                return f"#{int(key)}"
            return key[: T["name_max"]]
        if p["user_id"] is not None and (user := await self.hass.auth.async_get_user(p["user_id"])) is not None:
            return user.name or ""
        return ""

    async def _words(self, english: bool) -> dict[str, str]:
        try:
            return await translation.async_get_translations(self.hass, "en" if english else "ru", "exceptions", [DOMAIN])
        except Exception:  # noqa: BLE001 — words not loaded: the keys below fall back
            return {}

    async def _push(self, lock: str, op: str, method: str | None, user: str, at: Any, n: int) -> None:
        engine = self._engine()
        if engine is None:
            return
        got = await self._words(engine._english())

        def word(key: str, fallback: str = "") -> str:
            return got.get(f"component.{DOMAIN}.exceptions.{key}.message") or fallback

        what = word(f"lock_op_{op}", op).format(n=n)
        if method and (by := word(f"lock_by_{method}")):
            what = f"{what} {by}"
        if user.startswith("#") and user[1:].isdigit():
            user = word("lock_user_index", user).format(n=user[1:])
        message = " · ".join(x for x in (what, user, dt_util.as_local(at).strftime("%H:%M")) if x)
        name = engine._name(lock)
        critical = op in CRITICAL_OPS
        rule = {"id": "lock", "title": name, "priority": "critical" if critical else "normal", "recipients": {"all": True, "people": [], "groups": [], "phones": []}}
        silent = not critical and bool(engine.quiet_now and engine.quiet_now())
        await engine._send(rule, f"{ICONS.get(op, '')} {name}".strip(), message, tag=engine._tag(f"lock|{lock}|{op}"), critical=critical, silent=silent, targets=engine.ring_targets())

    # --- the panel -----------------------------------------------------------------------------------------------

    async def act(self, eid: str, action: str, call_context: Any = None) -> None:
        """`hauflo.lock`: lock always; unlock / open — the listed people only; open — a lock that can (feature OPEN)."""
        st = self.hass.states.get(eid)
        if eid.split(".", 1)[0] != "lock" or st is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="lock_unknown")
        user_id = getattr(call_context, "user_id", None)
        if action != "lock" and not self.may_unlock(eid, user_id):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="lock_no_rights")
        if action == "open" and not int(st.attributes.get("supported_features") or 0) & LockEntityFeature.OPEN:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="lock_no_open")
        if eid in self._busy:
            return  # a second tap while the first is under way: the same command
        self._busy.add(eid)
        try:
            await self.hass.services.async_call("lock", action, {"entity_id": eid}, blocking=True, context=call_context)
        except HomeAssistantError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="lock_failed") from err
        finally:
            self._busy.discard(eid)

    def can_open(self, eid: str) -> bool:
        st = self.hass.states.get(eid)
        return st is not None and bool(int(st.attributes.get("supported_features") or 0) & LockEntityFeature.OPEN)

    async def wizard(self, doors: list[dict[str, str]]) -> dict[str, Any]:
        """The wizard's «Замок» (GET /api/hauflo/foreign `lock_info`): each lock found (cloud — the poll and the hint;
        open — «Открыть дверь»), the HA people one may give the right to unlock, the intercom's doors to link."""
        people = [{"id": u.id, "name": str(u.name or "")[:T["name_max"]]} for u in await self.hass.auth.async_get_users() if u.is_active and not u.system_generated]
        return {
            "seen": {eid: {"cloud": bool(self.cloud.get(eid)), "open": self.can_open(eid)} for eid in sorted(self.devices)},
            "people": sorted(people, key=lambda p: (p["name"].casefold(), p["id"]))[: T["users_max"]],
            "doors": doors,
        }

    def view(self, user_id: str | None) -> dict[str, dict[str, Any]]:
        """The cards' lock tile (house.py): the last event, the battery, the door, whether this person may unlock."""
        out: dict[str, dict[str, Any]] = {}
        for eid, d in self.devices.items():
            s = self.settings(eid)
            poll = int(s.get("poll") or 0) if self.cloud.get(eid) else 0
            out[eid] = {
                "last": self.last.get(eid),
                "battery": d["battery"],
                "door": s.get("door"),
                "cloud": bool(self.cloud.get(eid)),
                "poll": poll,
                "can_unlock": self.may_unlock(eid, user_id),
                "open": self.can_open(eid),
            }
        return out


@callback
def async_setup_services(hass: HomeAssistant, find: Callable[[], Locks | None]) -> None:
    if hass.services.has_service(DOMAIN, SERVICE):
        return

    async def _act(call: ServiceCall) -> None:
        locks = find()
        if locks is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="lock_unknown")
        await locks.act(call.data["entity_id"], call.data["action"], call.context)

    hass.services.async_register(DOMAIN, SERVICE, _act, schema=SCHEMA)
