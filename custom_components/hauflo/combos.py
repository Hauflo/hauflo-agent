"""«Сочетание состояний» (0.34 п.10): two or more entities in their states at once for longer than T → a problem.

The combinations come with the site's signal policy (signals.py `combos`, feature COMBOS_FEATURE) and are read by the same
calculator as every signal (signals.cond_holds → alarm_of). Each is one Hauflo binary sensor (device class problem) —
on while every condition has held for `holdS`; Hauflo watches it by the site's own policy for it (watch, severity, «Всё
хорошо»), so it rides every alarm channel as any signal. Only a signal: nothing is switched — the service only warns,
the control logic stays the integrator's (owner rule).

«Since when» is the newest `last_changed` of the sources when the combination is first seen holding (a restart or a new
combination never waits T again for what has held long already), then kept while it holds.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from . import signals
from .const import async_entity_names, site_name
from .device import device_info

FEATURE = signals.COMBOS_FEATURE


class Combos:
    def __init__(self, hass: HomeAssistant, entry_id: str, ru: Callable[[], bool] | None = None) -> None:
        self.hass = hass
        self.entry_id = entry_id
        # The house's one language (const.site_ru) for its sensors' names; the names' texts — async_names.
        self.ru = ru or (lambda: str(hass.config.language or "").startswith("ru"))
        self.names: dict[str, dict[str, str]] = {}
        self.add_binary: Any = None
        self.entities: dict[str, ComboActive] = {}
        self.since: dict[str, float] = {}
        self.on: dict[str, bool] = {}
        self._timers: dict[str, CALLBACK_TYPE] = {}
        self._unsub_states: CALLBACK_TYPE | None = None
        self._unsub_policy: CALLBACK_TYPE | None = None
        self._ids: tuple[str, ...] = ()

    async def async_names(self) -> None:
        self.names = await async_entity_names(self.hass)

    def title(self, name: str) -> str | None:
        """«Сочетание · <name>» in the house's language; None — HA's translation (texts not loaded)."""
        return site_name(self.names, self.ru(), "binary_sensor.combo", name=name) if self.names else None

    def defs(self) -> dict[str, dict[str, Any]]:
        s = signals.store(self.hass)
        return s.combos() if s is not None else {}

    @callback
    def start(self) -> None:
        if (s := signals.store(self.hass)) is not None and self._unsub_policy is None:
            self._unsub_policy = s.listen(self.reload)
        self.reload()

    @callback
    def stop(self) -> None:
        for fn in (self._unsub_states, self._unsub_policy):
            if fn is not None:
                fn()
        self._unsub_states = self._unsub_policy = None
        for t in self._timers.values():
            t()
        self._timers.clear()

    @callback
    def reload(self) -> None:
        """The policy changed (or the platform came up): sources tracked, entities synced, every combination worked out."""
        defs = self.defs()
        for cid in [c for c in (*self.since, *self.on, *self._timers) if c not in defs]:
            self._forget(cid)
        ids = tuple(sorted({c["entityId"] for d in defs.values() for c in d["all"]}))
        if ids != self._ids:
            if self._unsub_states is not None:
                self._unsub_states()
            self._unsub_states = async_track_state_change_event(self.hass, list(ids), self._changed) if ids else None
            self._ids = ids
        for cid, d in defs.items():
            # Another definition: its «since» starts again from what the sources say now.
            if (ent := self.entities.get(cid)) is not None and ent.combo != d:
                self.since.pop(cid, None)
            self.evaluate(cid, d)
        self.sync_entities(defs)

    def _forget(self, cid: str) -> None:
        self.since.pop(cid, None)
        self.on.pop(cid, None)
        if (t := self._timers.pop(cid, None)) is not None:
            t()

    @callback
    def _changed(self, event: Event) -> None:
        eid = event.data.get("entity_id")
        for cid, d in self.defs().items():
            if any(c["entityId"] == eid for c in d["all"]):
                self.evaluate(cid, d)

    @callback
    def evaluate(self, cid: str, d: dict[str, Any]) -> bool:
        if (t := self._timers.pop(cid, None)) is not None:
            t()
        states = [self.hass.states.get(c["entityId"]) for c in d["all"]]
        now = dt_util.utcnow().timestamp()
        if signals.combo_holds(states, d):
            if cid not in self.since:
                self.since[cid] = max(st.last_changed.timestamp() for st in states if st is not None)
            left = self.since[cid] + d["holdS"] - now
            on = left <= 0
            if not on:
                self._timers[cid] = async_call_later(self.hass, left, partial(self._tick, cid))
        else:
            self.since.pop(cid, None)
            on = False
        self.on[cid] = on
        # Written on every evaluation: which conditions hold and since when are in its attributes (unrecorded).
        if (ent := self.entities.get(cid)) is not None:
            ent.refresh(d)
        return on

    @callback
    def _tick(self, cid: str, _now: Any = None) -> None:
        self._timers.pop(cid, None)
        if (d := self.defs().get(cid)) is not None:
            self.evaluate(cid, d)

    @callback
    def sync_entities(self, defs: dict[str, dict[str, Any]] | None = None) -> None:
        defs = self.defs() if defs is None else defs
        reg = er.async_get(self.hass)
        for cid in [k for k in self.entities if k not in defs]:
            ent = self.entities.pop(cid)
            if ent.entity_id and reg.async_get(ent.entity_id):
                reg.async_remove(ent.entity_id)
        for cid, d in defs.items():
            if cid in self.entities:
                self.entities[cid].refresh(d)
            elif self.add_binary is not None:
                self.entities[cid] = e = ComboActive(self, cid, d)
                self.add_binary([e])

    def attrs(self, cid: str, d: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"hold_s": d["holdS"], "conditions": [{"entity_id": c["entityId"], "holds": signals.cond_holds(self.hass.states.get(c["entityId"]), c["alarm"])} for c in d["all"]]}
        if (s := self.since.get(cid)) is not None:
            out["since"] = dt_util.utc_from_timestamp(s).isoformat()
        return out


class ComboActive(BinarySensorEntity):
    """On while every condition of the combination has held for its time; which hold now — in its attributes."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "combo"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _unrecorded_attributes = frozenset({"conditions", "since", "hold_s"})

    def __init__(self, engine: Combos, cid: str, combo: dict[str, Any]) -> None:
        self.engine = engine
        self.cid = cid
        self.combo = combo
        self._attr_unique_id = f"{engine.entry_id}_combo_{cid}"
        # The id the site's policy watches (backend signalCombos.ts comboEntityId).
        self.entity_id = signals.combo_entity(cid)
        if engine.entry_id:
            self._attr_device_info = device_info(engine.entry_id)
        self._attr_translation_placeholders = {"name": combo["name"]}
        self._name_in_site_lang(combo)

    def _name_in_site_lang(self, combo: dict[str, Any]) -> bool:
        """The name in the house's language over HA's (const.site_name); True — it changed."""
        title = self.engine.title(combo["name"])
        if title is None or getattr(self, "_attr_name", None) == title:
            return False
        self._attr_name = title
        return True

    @callback
    def refresh(self, combo: dict[str, Any]) -> None:
        if self._name_in_site_lang(combo) or self._attr_translation_placeholders != {"name": combo["name"]}:
            self._attr_translation_placeholders = {"name": combo["name"]}
            self.__dict__.pop("name", None)  # HA caches it (as rules.py _RuleEntity.refresh)
        self.combo = combo
        if self.hass is not None and self.entity_id and self.platform is not None:
            self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return bool(self.engine.on.get(self.cid))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.engine.attrs(self.cid, self.combo)
