"""What Hauflo made in this Home Assistant, seen in HA itself (service-company review 29.10: ownership lived only in
.storage/hauflo.knx_owned — an engineer in HA saw «KNX» like any other entity).

- One label «Hauflo» on every entity Hauflo created or took over: its KNX entities (the agent's owned list), the
  light / cover groups it set up, its scenes, the family's dashboard automations and the agent's own entities. Kept in
  sync by the agent: put on what becomes Hauflo's, taken off what stops being Hauflo's (or was labelled by hand).
  Only the label changes — never an entity_id, a name or an area. Not while the owner keeps the house closed.
- «Создано в этом доме» / «Made in this home»: a read-only sensor on the integration's page — how many entities are Hauflo's,
  which of them were changed by hand in HA (renamed, disabled; the ones the last plan keeps as edited) and what the next
  deploy would change, when Hauflo said it (POST /api/hauflo/ownership after each plan; tunnel only, info only).

Who services the house is said elsewhere (house transfer) — not here.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import re
from typing import Any

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, label_registry as lr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MONITOR_LABEL, MONITOR_LABEL_EN, MONITOR_LABELS
from .storage import HaufloStore

_LOGGER = logging.getLogger(__name__)
LABEL = "Hauflo"
LABEL_ICON = "mdi:home-import-outline"
LABEL_COLOR = "indigo"
LABEL_TEXT = {
    "ru": "Создано Hauflo. Метку ставит и снимает агент Hauflo — вручную её не ставьте.",
    "en": "Made by Hauflo. The Hauflo agent puts this label on and takes it off — don't set it by hand.",
}
MONITOR_TEXT = {
    "ru": "Hauflo следит за этим устройством и присылает аварии. Ставьте на устройства не из Hauflo (котёл, вентиляция, Zigbee) — своё Hauflo видит и без метки.",
    "en": "Hauflo watches this device and sends alarms. Put it on devices Hauflo didn't make (a boiler, ventilation, Zigbee) — Hauflo's own are watched without it.",
}
# Descriptions older agents wrote on the monitoring label: replaced by the clearer one, a hand-written one stays.
_OLD_MONITOR_TEXT = {"Hauflo: следить и присылать аварии в Telegram", "Hauflo: watch it and send alarms to Telegram"}
STORE_KEY = "hauflo.ownership"
SIGNAL = f"{DOMAIN}_ownership"
SYNC_DELAY = 20  # seconds after the last registry change (a deploy writes hundreds in a row)
MAX_LIST = 50
_ENTITY = re.compile(r"[a-z_]+\.[a-z0-9_]{1,200}")
_AUTOMATION_PREFIX = "hauflo_auto_"


def kind_of(entry: er.RegistryEntry, owned: set[str], owned_entries: set[str]) -> str | None:
    """Hauflo's own (and which kind) — the same rule the tunnel uses for what Hauflo may change."""
    if entry.platform == "knx":
        return "knx" if entry.unique_id in owned else None
    if entry.platform == "group":
        return "group" if entry.config_entry_id in owned_entries else None
    if entry.platform == "homeassistant" and entry.domain == "scene":
        return "scene" if str(entry.unique_id).startswith("hauflo_") else None
    if entry.platform == "automation":
        return "automation" if str(entry.unique_id).startswith(_AUTOMATION_PREFIX) else None
    if entry.platform == DOMAIN:
        return "agent"
    return None


def _ru(hass: HomeAssistant) -> bool:
    return str(hass.config.language or "").startswith("ru")


class Ownership:
    """One per config entry: the label kept in sync, the sensor's facts, the last plan Hauflo told."""

    def __init__(self, hass: HomeAssistant, owned: Callable[[], set[str]], owned_entries: Callable[[], set[str]], locked: Callable[[], bool]) -> None:
        self.hass = hass
        self._owned = owned
        self._owned_entries = owned_entries
        self._locked = locked
        self.store: HaufloStore = HaufloStore(hass, STORE_KEY)
        self.label_id: str | None = None
        self.plan: dict[str, Any] | None = None
        # The registry names Hauflo itself set through the tunnel (its light / cover groups: «Свет в комнате») — not a
        # hand rename (HA reviewer r2: a fresh deploy said «23 renamed»). entity_id → name.
        self.names: dict[str, str] = {}
        self._unsub: list[Callable[[], None]] = []
        self._timer: Callable[[], None] | None = None
        self.loaded = False
        self._dirty = False

    async def async_start(self) -> None:
        data = await self.store.async_load()
        if isinstance(data, dict) and isinstance(data.get("plan"), dict):
            self.plan = data["plan"]
        if isinstance(data, dict) and isinstance(data.get("names"), dict):
            self.names = {k: v for k, v in data["names"].items() if isinstance(k, str) and isinstance(v, str)}
        else:
            # An agent before this one kept no names: the ones Hauflo's groups carry now are taken as Hauflo's, once
            # (Hauflo names its groups in the registry; KNX entities and scenes it names in their own config).
            owned, entries = self._owned(), self._owned_entries()
            self.names = {e.entity_id: e.name for e in er.async_get(self.hass).entities.values() if e.name and kind_of(e, owned, entries) == "group"}
            self._save()
        self.loaded = True
        self._unsub.append(self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_changed))
        self.async_sync()

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        if self._timer:
            self._timer()
            self._timer = None

    def _data(self) -> dict[str, Any]:
        return {"plan": self.plan, "names": self.names}

    @callback
    def _save(self) -> None:
        # Delayed: a deploy renames dozens in a row. Written at once on unload (async_flush) and on HA's stop.
        self._dirty = True
        self.store.async_delay_save(self._data, 1)

    async def async_flush(self) -> None:
        if self._dirty:
            self._dirty = False
            await self.store.async_save(self._data())

    @callback
    def remember_name(self, entity_id: Any, name: Any = None, old_entity_id: Any = None, named: bool = True) -> None:
        """Hauflo changed an entity's registry entry through the tunnel (config/entity_registry/update): the name it
        set (named) is Hauflo's, a cleared one is forgotten; a new entity_id takes the remembered name along."""
        if not isinstance(entity_id, str):
            return
        before = dict(self.names)
        if isinstance(old_entity_id, str) and old_entity_id != entity_id and old_entity_id in self.names:
            self.names[entity_id] = self.names.pop(old_entity_id)
        if named:
            if isinstance(name, str) and name:
                self.names[entity_id] = name
            else:
                self.names.pop(entity_id, None)
        if self.names != before:
            self._save()
            async_dispatcher_send(self.hass, SIGNAL)

    # --- the label --------------------------------------------------------------------------------

    @callback
    def _registry_changed(self, _event: Event) -> None:
        self.schedule()

    @callback
    def schedule(self, delay: float = SYNC_DELAY) -> None:
        """A sync soon — once, after the last of a burst of changes."""
        if self._timer:
            self._timer()
        self._timer = async_call_later(self.hass, delay, self._run)

    @callback
    def _run(self, _now: Any) -> None:
        self._timer = None
        self.async_sync()

    @callback
    def _ensure_label(self) -> str:
        reg = lr.async_get(self.hass)
        if self.label_id and reg.async_get_label(self.label_id) is not None:
            return self.label_id
        found = next((label for label in reg.async_list_labels() if label.name.casefold() == LABEL.casefold()), None)
        if found is None:
            found = reg.async_create(LABEL, icon=LABEL_ICON, color=LABEL_COLOR, description=LABEL_TEXT["ru" if _ru(self.hass) else "en"])
        self.label_id = found.label_id
        return found.label_id

    @callback
    def _clarify_monitor_label(self) -> None:
        """«Hauflo monitoring» is for devices Hauflo didn't make (service review 29.10: an empty label read as broken)."""
        reg = lr.async_get(self.hass)
        for label in reg.async_list_labels():
            if label.name.casefold() in MONITOR_LABELS and (label.description or "") in _OLD_MONITOR_TEXT:
                ru = label.name == MONITOR_LABEL or (label.name != MONITOR_LABEL_EN and _ru(self.hass))
                reg.async_update(label.label_id, description=MONITOR_TEXT["ru" if ru else "en"])

    @callback
    def async_sync(self) -> tuple[int, int]:
        """The label on exactly Hauflo's entities. Returns (put on, taken off)."""
        if not self.loaded or self._locked():
            return 0, 0
        try:
            label_id = self._ensure_label()
            self._clarify_monitor_label()
        except Exception as err:  # noqa: BLE001 — a changed HA internal never stops the agent
            _LOGGER.warning("Hauflo: label not set up: %s", err)
            return 0, 0
        owned, entries = self._owned(), self._owned_entries()
        reg = er.async_get(self.hass)
        added = removed = 0
        for entry in list(reg.entities.values()):
            own = kind_of(entry, owned, entries) is not None
            has = label_id in entry.labels
            if own == has:
                continue
            labels = set(entry.labels) | {label_id} if own else set(entry.labels) - {label_id}
            try:
                reg.async_update_entity(entry.entity_id, labels=labels)
            except Exception as err:  # noqa: BLE001 — one entity HA refuses never stops the rest
                _LOGGER.debug("Hauflo: label on %s not changed: %s", entry.entity_id, err)
                continue
            if own:
                added += 1
            else:
                removed += 1
        if added or removed:
            _LOGGER.debug("Hauflo: label «%s» put on %d, taken off %d", LABEL, added, removed)
        async_dispatcher_send(self.hass, SIGNAL)  # renamed / disabled by hand: the sensor says it too
        return added, removed

    # --- what the sensor says ---------------------------------------------------------------------

    @callback
    def facts(self) -> dict[str, Any]:
        owned, entries = self._owned(), self._owned_entries()
        by_kind: dict[str, int] = {}
        renamed: list[str] = []
        disabled: list[str] = []
        for entry in er.async_get(self.hass).entities.values():
            kind = kind_of(entry, owned, entries)
            # The agent's own entities («Связь», this sensor) carry the label but aren't «made in this home».
            if kind is None or kind == "agent":
                continue
            by_kind[kind] = by_kind.get(kind, 0) + 1
            # A name given in HA's entity settings is the person's — unless it's the one Hauflo set (its groups).
            if entry.name and entry.name != self.names.get(entry.entity_id):
                renamed.append(entry.entity_id)
            if entry.disabled_by is er.RegistryEntryDisabler.USER:
                disabled.append(entry.entity_id)
        plan = self.plan or {}
        kept = [e for e in plan.get("kept", []) if isinstance(e, str)]
        return {
            "owned": sum(by_kind.values()),
            "by_kind": by_kind,
            "label": LABEL,
            "hand_edited": sorted(set(renamed) | set(disabled) | set(kept))[:MAX_LIST],
            "renamed": sorted(renamed)[:MAX_LIST],
            "disabled": sorted(disabled)[:MAX_LIST],
            "kept_by_plan": sorted(kept)[:MAX_LIST],
            "next_deploy": {k: v for k, v in plan.items() if k != "kept"} or None,
        }

    async def async_set_plan(self, raw: Any) -> dict[str, Any]:
        self.plan = clean_plan(raw)
        self._dirty = False
        await self.store.async_save(self._data())
        async_dispatcher_send(self.hass, SIGNAL)
        return self.plan


_COUNTS = ("create", "update", "leftovers", "leftover_rooms", "kept_count")


def clean_plan(raw: Any) -> dict[str, Any]:
    """What Hauflo says the next deploy would do — numbers, a time, entity ids; anything else from the wire is dropped."""
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    out: dict[str, Any] = {"at": dt_util.utcnow().isoformat()}
    for key in _COUNTS:
        v = raw.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 1_000_000:
            out[key] = v
    if isinstance(raw.get("dashboard"), bool):
        out["dashboard"] = raw["dashboard"]
    if isinstance(raw.get("project"), str):
        out["project"] = raw["project"][:120]
    kept = raw.get("kept")
    out["kept"] = sorted({k for k in kept if isinstance(k, str) and _ENTITY.fullmatch(k)})[:MAX_LIST] if isinstance(kept, list) else []
    return out


class OwnershipView(HomeAssistantView):
    """Hauflo tells what the next deploy would change (after each plan) — shown in HA, changes nothing. Admin only."""

    url = "/api/hauflo/ownership"
    name = "api:hauflo:ownership"
    requires_auth = True

    def __init__(self, of: Callable[[HomeAssistant], Ownership | None]) -> None:
        self._of = of

    async def get(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        own = self._of(request.app[KEY_HASS])
        return self.json(own.facts() if own else {})

    async def post(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        own = self._of(request.app[KEY_HASS])
        if own is None:
            return self.json_message("not ready", 503)
        try:
            body = await request.json()
            return self.json(await own.async_set_plan(body))
        except ValueError:
            return self.json_message("bad request", 400)
