"""«Диагностика по своей норме» (owner 28.10 + 04.11): each zone's / sensor's OWN normal behaviour, learned from what HA
sees, and a word to the integrator's staff when it is clearly off — before the client notices. Diagnosis only: nothing
here switches, sets or writes anything in the house.

The class (CLAUDE.md §10) — a deviation from one device's learned norm. Each kind is a DETECTOR over a small per-entity
norm kept in HA's storage (`hauflo.insights`); the engine (`Insights`) only feeds them state changes and a tick, saves
their norms and lists what they say. v1:
  * `ClimateEffect` — «нагрев / охлаждение без эффекта»: a climate wants heat (cool) and the room's temperature doesn't
    move, for far longer than its own learned rate (°C/h, median of its last episodes) says it should;
  * `StuckSensor` — «датчик застыл»: a temperature / humidity sensor that normally changes many times a day hasn't
    changed for a long time.
«Дом умнеет — B» (predict.py): trends toward a failure, forecast before the limit is crossed — a battery running down,
heating getting slower, a thermostat short-cycling, a radio link fading, a device dropping off more and more often, HA's
own disk filling and its backups getting old, water flowing at night. The same engine, more detectors.

No false alarm by design (research: Nest System Health Monitor, Loxone IRC — own norm, never a fixed threshold, several
observations before a word, the cause said): nothing while learning (too few samples), while a window / door of the
room is open, while the climate is unavailable or off, the season says the other way, the target changes (that
episode is dropped, never learned), and never right after an HA restart (an episode carried over needs a full
`flat_h` of fresh observation; an alert already open stays open, so a restart isn't a «снова в норме»).

The output (`items`) goes to Hauflo in the agent's status (`insights`); the cloud words it and tells the staff only.
Numbers: tuning.json «insight».
"""

from __future__ import annotations

import logging
import re
import statistics
import time
from datetime import timedelta
from typing import Any, Callable

from homeassistant.const import EVENT_STATE_CHANGED, STATE_ON
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .derived import NOT_A_STATE, SECONDS_PER_HOUR, num, outdoor
from .notify import labelled
from .storage import HaufloStore
from .tuning import TUNING

_LOGGER = logging.getLogger(__name__)
STORE_KEY = "hauflo.insights"
_T = TUNING["insight"]
TICK = _T["tick_s"]
SAVE_DELAY = _T["save_delay_s"]
LIST_MAX = _T["list_max"]
GONE_AFTER = _T["gone_after_s"]  # a remembered entity not in HA this long after start: removed from HA, forgotten
# Climate: «wants heat» from this far below the target, «reached» this close to it; only episodes from at least
# LEARN_GAP away teach the norm (a 0.3° wobble says nothing of the rate); the last SAMPLES rates are kept, LEARN_MIN
# of them before any word.
GAP = _T["gap_c"]
REACH = _T["reach_c"]
LEARN_GAP = _T["learn_gap_c"]
SAMPLES = _T["samples"]
LEARN_MIN = _T["learn_min"]
# Said when an episode lasts longer than both MIN_H and FACTOR × its expected time AND the temperature moved less than
# FLAT_C the right way in the last FLAT_H hours.
MIN_H = _T["min_h"]
FACTOR = _T["factor"]
FLAT_C = _T["flat_c"]
FLAT_H = _T["flat_h"]
MODES = {"heat": set(_T["heat_modes"]), "cool": set(_T["cool_modes"])}
ACTIONS = {"heat": set(_T["heat_actions"]), "cool": set(_T["cool_actions"])}
OPENING = set(_T["opening_classes"])
# Stuck sensor: these classes; said after STUCK_H without a change when its norm (median changes a day over the last
# STUCK_DAYS full days, at least LEARN_DAYS of them) is at least STUCK_NORM.
STUCK_CLASSES = set(_T["stuck_classes"])
STUCK_H = _T["stuck_h"]
STUCK_DAYS = _T["stuck_days"]
LEARN_DAYS = _T["learn_days"]
STUCK_NORM = _T["stuck_norm_changes"]
# An air handler's climate (owner 09.11, Горького 3 «Вентмашина Климат»): the supply air heats tens of °C an hour and its
# rate follows the airflow and the outdoor air — no zone's norm to slow down from, its own controller has the alarms.
# Known by its words (name, entity, area) or by a learned rate no room reaches.
AIR_HANDLER_WORDS = re.compile(_T["air_handler_words"], re.IGNORECASE)
AIR_HANDLER_RATE = _T["air_handler_rate_c_h"]

HEAT, COOL = "heat", "cool"
KIND = {HEAT: "no_heat", COOL: "no_cool"}
SIGN = {HEAT: 1, COOL: -1}
# What an episode carries over a restart (its samples don't: fresh observation is needed after it).
_KEEP = ("dir", "start", "gap", "target", "mode", "last_on", "alerted")


def _iso(ts: float) -> str:
    return dt_util.utc_from_timestamp(ts).isoformat()


def _round(v: float) -> float:
    return round(v, 2)


def _floats(v: Any) -> list[float]:
    return [float(x) for x in v if num(x) is not None and float(x) > 0] if isinstance(v, list) else []


class Detector:
    """One kind of «off its own norm». The engine calls `on_state` for its domains, `tick` every TICK seconds; `items`
    are the open findings now. `load` / `dump` are its persisted norms (a bad saved shape is read as empty)."""

    name = ""
    domains: tuple[str, ...] | None = ()  # None: every domain (a cheap first check in on_event, please)

    def __init__(self, ctx: Insights) -> None:
        self.ctx = ctx

    def load(self, data: dict[str, Any]) -> None:  # pragma: no cover — every detector has its own
        raise NotImplementedError

    def dump(self) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def on_state(self, new: State, now: float) -> None:  # pragma: no cover
        raise NotImplementedError

    def on_event(self, old: State | None, new: State, now: float) -> None:
        """A state change with the state before it (a detector that counts transitions); by default — the new state."""
        self.on_state(new, now)

    def on_learned(self, eid: str, direction: str, rate: float, now: float) -> None:
        """ClimateEffect learned a clean episode's rate (°C/h) — a detector that follows the rates' trend listens."""

    def tick(self, now: float) -> None:  # pragma: no cover
        raise NotImplementedError

    def items(self, now: float) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError


class ClimateEffect(Detector):
    """Heating / cooling without effect, against the zone's own rate (°C per hour from wanting to reaching)."""

    name = "climate"
    domains = ("climate",)

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        self.norms: dict[str, dict[str, list[float]]] = {}  # entity → direction → its last rates, °C/h
        self.eps: dict[str, dict[str, Any]] = {}  # entity → the running episode

    def load(self, data: dict[str, Any]) -> None:
        for eid, by in (data.get("norms") or {}).items():
            if isinstance(by, dict):
                got = {d: _floats(by.get(d))[-SAMPLES:] for d in (HEAT, COOL)}
                self.norms[str(eid)] = {d: v for d, v in got.items() if v}
        for eid, ep in (data.get("episodes") or {}).items():
            if not isinstance(ep, dict) or ep.get("dir") not in (HEAT, COOL) or num(ep.get("start")) is None or num(ep.get("gap")) is None:
                continue
            last_on = num(ep.get("last_on"))
            alerted = num(ep.get("alerted"))
            # Carried over a restart: never learned (HA was down part of it), and its samples start now — a word needs
            # FLAT_H of fresh observation (an alert already open stays open: a restart is no «снова в норме»).
            self.eps[str(eid)] = {
                "dir": ep["dir"],
                "start": float(ep["start"]),
                "gap": float(ep["gap"]),
                "target": num(ep.get("target")),
                "mode": str(ep.get("mode") or ""),
                "last_on": last_on if last_on is not None else float(ep["start"]),
                "alerted": alerted,
                "resumed": True,
                "samples": [],
            }

    def dump(self) -> dict[str, Any]:
        return {"norms": self.norms, "episodes": {eid: {k: ep.get(k) for k in _KEEP} for eid, ep in self.eps.items()}}

    # --- the episode -------------------------------------------------------------------------------------------------

    def _season_ok(self, d: str) -> bool:
        # The season says the other way (a floor «heat» in summer with the boiler off, fan coils «cool» in winter with the
        # chiller off): no effect is expected — no episode.
        season = self.ctx.season_now()
        return season not in (HEAT, COOL) or season == d

    def _want(self, st: State, cur: float, tgt: float) -> str | None:
        action = st.attributes.get("hvac_action")
        for d in (HEAT, COOL):
            if st.state not in MODES[d] or not self._season_ok(d):
                continue
            if (tgt - cur) * SIGN[d] < GAP:
                continue
            # A thermostat that says what it does: it must be heating (cooling) — idle below the target is its own
            # decision (a floor limit, a lock), not «no effect».
            if action is not None and action not in ACTIONS[d]:
                continue
            return d
        return None

    def on_state(self, new: State, now: float) -> None:
        self._eval(new, now)

    def tick(self, now: float) -> None:
        for st in self.ctx.hass.states.async_all("climate"):
            self._eval(st, now)
        if now - self.ctx.started > GONE_AFTER:
            for eid in [e for e in self.eps if self.ctx.hass.states.get(e) is None]:
                self._drop(eid)

    def _eval(self, st: State, now: float) -> None:
        eid = st.entity_id
        if st.state in NOT_A_STATE:
            return  # frozen: no word and no end (HA starting, the bus away) — «недоступен» is its own alarm
        cur, tgt = num(st.attributes.get("current_temperature")), num(st.attributes.get("temperature"))
        if cur is None or tgt is None:
            self._drop(eid)
            return
        ep = self.eps.get(eid)
        if ep is not None:
            d = ep["dir"]
            if ep["target"] != tgt or ep["mode"] != st.state or not self._season_ok(d):
                self._drop(eid)  # the target / mode / season changed: a new question, the old episode isn't learned
            elif (cur - tgt) * SIGN[d] >= -REACH:
                self._finish(eid, now)
            elif self.ctx.opening_open(eid):
                self._drop(eid)  # a window open: the episode says nothing — a fresh one after it closes
            else:
                self._observe(st, ep, cur, now)
                return
        d = self._want(st, cur, tgt)
        if d is not None and not self.ctx.opening_open(eid):
            self.eps[eid] = {"dir": d, "start": now, "gap": abs(tgt - cur), "target": tgt, "mode": st.state, "last_on": now, "alerted": None, "resumed": False, "samples": [(now, cur)]}
            self.ctx.dirty()

    def _observe(self, st: State, ep: dict[str, Any], cur: float, now: float) -> None:
        d = ep["dir"]
        action = st.attributes.get("hvac_action")
        if action is None or action in ACTIONS[d]:
            ep["last_on"] = now
        samples: list[tuple[float, float]] = ep["samples"]
        if not samples or samples[-1][1] != cur:
            samples.append((now, cur))
        # One anchor at or before FLAT_H ago is enough: the rest of the older ones go.
        edge = now - FLAT_H * SECONDS_PER_HOUR
        while len(samples) > 1 and samples[1][0] <= edge:
            samples.pop(0)
        if ep["alerted"] is not None:
            return
        rate = self.norm(st.entity_id, d)
        if rate is None:
            return  # still learning
        expected = ep["gap"] / rate
        hours = (now - ep["start"]) / SECONDS_PER_HOUR
        if hours <= max(MIN_H, FACTOR * expected):
            return
        change = self._change(ep, cur, now)
        if change is None or change * SIGN[d] >= FLAT_C:
            return  # not observed long enough since start / a restart, or it is moving after all
        if now - ep["last_on"] > FLAT_H * SECONDS_PER_HOUR:
            return  # the thermostat stopped asking: not this detector's question
        ep["alerted"] = now
        self.ctx.dirty()

    def _change(self, ep: dict[str, Any], cur: float, now: float) -> float | None:
        """The temperature's change over the last FLAT_H hours; None — not observed that long."""
        samples = ep["samples"]
        edge = now - FLAT_H * SECONDS_PER_HOUR
        if not samples or samples[0][0] > edge:
            return None
        anchor = [v for t, v in samples if t <= edge][-1]
        return cur - anchor

    def _finish(self, eid: str, now: float) -> None:
        ep = self.eps.pop(eid)
        hours = (now - ep["start"]) / SECONDS_PER_HOUR
        # Learned: a clean episode only — not carried over a restart, not one that was said (a failure isn't the norm),
        # from far enough away.
        if not ep["resumed"] and ep["alerted"] is None and ep["gap"] >= LEARN_GAP and hours > 0:
            rates = self.norms.setdefault(eid, {}).setdefault(ep["dir"], [])
            rates.append(round(ep["gap"] / hours, 4))
            del rates[:-SAMPLES]
            self.ctx.learned(eid, ep["dir"], rates[-1], now)
        self.ctx.dirty()

    def _drop(self, eid: str) -> None:
        if self.eps.pop(eid, None) is not None:
            self.ctx.dirty()

    def norm(self, eid: str, d: str) -> float | None:
        rates = self.norms.get(eid, {}).get(d) or []
        if len(rates) < LEARN_MIN:
            return None
        m = statistics.median(rates)
        return m if m > 0 else None

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        outdoor_now = self.ctx.outdoor_c()
        for eid, ep in self.eps.items():
            if ep["alerted"] is None:
                continue
            st = self.ctx.hass.states.get(eid)
            rate = self.norm(eid, ep["dir"])
            cur = num(st.attributes.get("current_temperature")) if st is not None else None
            change = self._change(ep, cur, now) if cur is not None else None
            out.append(
                {
                    "key": f"{KIND[ep['dir']]}|{eid}",
                    "kind": KIND[ep["dir"]],
                    "entityId": eid,
                    **self.ctx.who(eid),
                    "since": _iso(ep["start"]),
                    "hours": _round((now - ep["start"]) / SECONDS_PER_HOUR),
                    **({"expectedH": _round(ep["gap"] / rate)} if rate else {}),
                    **({"change": _round(change)} if change is not None else {}),
                    "flatH": FLAT_H,
                    # The street now: heating without effect in a frost is critical (the cloud's alertSeverity.json frost).
                    **({"outdoor": _round(outdoor_now)} if outdoor_now is not None else {}),
                }
            )
        return out


class StuckSensor(Detector):
    """A temperature / humidity sensor that normally changes many times a day and hasn't for STUCK_H hours."""

    name = "stuck"
    domains = ("sensor",)

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        # entity → {value, last (its last change), day (local date counted), count, partial (the day not seen whole),
        # days (the full days' change counts, oldest first)}
        self.recs: dict[str, dict[str, Any]] = {}

    def load(self, data: dict[str, Any]) -> None:
        for eid, r in (data.get("sensors") or {}).items():
            if not isinstance(r, dict) or num(r.get("last")) is None:
                continue
            # The day HA was down in is not a whole day: its count isn't learned.
            self.recs[str(eid)] = {"value": str(r.get("value")), "last": float(r["last"]), "day": str(r.get("day") or ""), "count": int(num(r.get("count")) or 0), "partial": True, "days": [int(x) for x in r.get("days") or [] if num(x) is not None][-STUCK_DAYS:]}

    def dump(self) -> dict[str, Any]:
        return {"sensors": self.recs}

    def _watched(self, st: State) -> bool:
        return st.attributes.get("device_class") in STUCK_CLASSES and (self.ctx.owns(st.entity_id) or labelled(self.ctx.hass, st.entity_id))

    def on_state(self, new: State, now: float) -> None:
        if new.state in NOT_A_STATE or num(new.state) is None or not self._watched(new):
            return
        rec = self.recs.get(new.entity_id)
        if rec is None:
            self._add(new, now)
        elif new.state != rec["value"]:
            rec["value"], rec["last"] = new.state, now
            rec["count"] += 1
            self.ctx.dirty()

    @staticmethod
    def _day(now: float) -> str:
        return dt_util.as_local(dt_util.utc_from_timestamp(now)).date().isoformat()

    def _add(self, st: State, now: float) -> None:
        # The first day is seen from now on, not whole: counted from the next one.
        self.recs[st.entity_id] = {"value": st.state, "last": min(st.last_changed.timestamp(), now), "day": self._day(now), "count": 0, "partial": True, "days": []}
        self.ctx.dirty()

    def tick(self, now: float) -> None:
        for st in self.ctx.hass.states.async_all("sensor"):
            if st.entity_id not in self.recs and st.state not in NOT_A_STATE and num(st.state) is not None and self._watched(st):
                self._add(st, now)
        today = self._day(now)
        for rec in self.recs.values():
            if rec["day"] != today:
                if not rec["partial"]:
                    rec["days"] = [*rec["days"], rec["count"]][-STUCK_DAYS:]
                rec.update(day=today, count=0, partial=False)
                self.ctx.dirty()
        if now - self.ctx.started > GONE_AFTER:
            for eid in [e for e in self.recs if self.ctx.hass.states.get(e) is None]:
                del self.recs[eid]
                self.ctx.dirty()

    def norm(self, eid: str) -> float | None:
        days = (self.recs.get(eid) or {}).get("days") or []
        return statistics.median(days) if len(days) >= LEARN_DAYS else None

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for eid, rec in self.recs.items():
            st = self.ctx.hass.states.get(eid)
            if st is None or st.state in NOT_A_STATE:
                continue  # no data is «нет данных» (notify), not «застыл»
            per_day = self.norm(eid)
            hours = (now - rec["last"]) / SECONDS_PER_HOUR
            if per_day is None or per_day < STUCK_NORM or hours < STUCK_H:
                continue
            out.append({"key": f"stuck|{eid}", "kind": "stuck", "entityId": eid, **self.ctx.who(eid), "since": _iso(rec["last"]), "hours": _round(hours), "perDay": _round(per_day)})
        return out


# The trends (predict.py) subclass Detector from here — imported once it exists.
from .noeffect import NOEFFECT  # noqa: E402
from .predict import TRENDS  # noqa: E402

DETECTORS: tuple[type[Detector], ...] = (ClimateEffect, StuckSensor, *TRENDS, *NOEFFECT)


class Insights:
    """The engine: feeds the detectors, keeps their norms (debounced save), lists their findings, says when the list
    changed (the agent sends its status then)."""

    def __init__(self, hass: HomeAssistant, owns: Callable[[str], bool], season_now: Callable[[], str | None]) -> None:
        self.hass = hass
        self.owns = owns
        # What the alarm rules watch (their sources and picks — the integrator's own choice, 399-18): a foreign entity there
        # (or its device's) is monitored like a labelled one. Set by the agent from its notify engine.
        self.watched: Callable[[], set[str]] = set
        self.season_now = season_now
        self.store: HaufloStore = HaufloStore(hass, STORE_KEY)
        self.clock: Callable[[], float] = time.time
        self.detectors: list[Detector] = [cls(self) for cls in DETECTORS]
        self.on_change: Callable[[], None] | None = None
        self.started = 0.0
        self.not_windows: set[str] = set()  # «opening» sensors that are no windows (valve feedback): ONE list, filled by PairEffect, read by every detector
        self._listed: frozenset[str] = frozenset()
        self._unsubs: list[Callable[[], None]] = []

    async def async_start(self) -> None:
        data = await self.store.async_load() or {}
        for det in self.detectors:
            try:
                saved = data.get(det.name) if isinstance(data, dict) else None
                det.load(saved if isinstance(saved, dict) else {})
            except Exception:  # noqa: BLE001 — a bad saved shape: learned anew, never a crash
                _LOGGER.warning("Hauflo insights: %s norms unreadable — learning anew", det.name, exc_info=True)
        self.started = self.clock()
        self._listed = frozenset(i["key"] for i in self.items())
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_event))
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=TICK)))

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    def data(self) -> dict[str, Any]:
        return {det.name: det.dump() for det in self.detectors}

    @callback
    def dirty(self) -> None:
        self.store.async_delay_save(self.data, SAVE_DELAY)

    @callback
    def _on_event(self, event: Event) -> None:
        new: State | None = event.data.get("new_state")
        if new is None:
            return
        old: State | None = event.data.get("old_state")
        handled = False
        for det in self.detectors:
            if det.domains is None or new.domain in det.domains:
                handled = handled or det.domains is not None  # an every-domain detector only notes, its tick decides
                self._run(det.on_event, old, new, self.clock())
        if handled:
            self._after()

    @callback
    def learned(self, eid: str, direction: str, rate: float, now: float) -> None:
        for det in self.detectors:
            self._run(det.on_learned, eid, direction, rate, now)

    @callback
    def _tick(self, _now: Any = None) -> None:
        now = self.clock()
        for det in self.detectors:
            self._run(det.tick, now)
        self._after()

    @staticmethod
    def _run(fn: Callable[..., None], *args: Any) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 — a diagnosis never breaks HA
            _LOGGER.exception("Hauflo insights: %s failed", getattr(fn, "__qualname__", fn))

    @callback
    def _after(self) -> None:
        keys = frozenset(i["key"] for i in self.items())
        if keys != self._listed:
            self._listed = keys
            if self.on_change:
                self.on_change()

    def items(self) -> list[dict[str, Any]]:
        now = self.clock()
        out: list[dict[str, Any]] = []
        for det in self.detectors:
            try:
                out.extend(det.items(now))
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Hauflo insights: %s items failed", det.name)
        return sorted(out, key=lambda i: (i["since"], i["key"]))[:LIST_MAX]

    # --- the house, read only ------------------------------------------------------------------------------------------

    def entry(self, eid: str) -> er.RegistryEntry | None:
        return er.async_get(self.hass).async_get(eid)

    def visible(self, eid: str, diagnostic: bool = False) -> bool:
        """Shown in HA: not disabled, not hidden; a diagnostic / config entity only for a detector that needs one (a
        battery, a link quality — diagnostic by nature). An entity HA's registry doesn't know (YAML) is shown."""
        entry = self.entry(eid)
        if entry is None:
            return True
        if entry.disabled_by is not None or entry.hidden_by is not None:
            return False
        return diagnostic or entry.entity_category is None

    def outdoor_c(self) -> float | None:
        """The street's temperature now, from the source the rules use (derived.outdoor); None — none known."""
        src = outdoor(self.hass, self.owns)
        st = self.hass.states.get(src["entity_id"]) if src else None
        if st is None or src is None:
            return None
        return num(st.attributes.get(src["attribute"])) if src["attribute"] else num(st.state)

    def _area_id(self, eid: str) -> str | None:
        entry = er.async_get(self.hass).async_get(eid)
        if entry is None:
            return None
        if entry.area_id:
            return entry.area_id
        device = dr.async_get(self.hass).async_get(entry.device_id) if entry.device_id else None
        return device.area_id if device else None

    def air_handler(self, eid: str) -> bool:
        """A climate of an air handler (AIR_HANDLER_WORDS / AIR_HANDLER_RATE): no «slower», no short cycling for it."""
        if not eid.startswith("climate."):
            return False
        w = self.who(eid)
        if AIR_HANDLER_WORDS.search(f"{eid} {w['name']} {w['area']}"):
            return True
        ce = next((det for det in self.detectors if isinstance(det, ClimateEffect)), None)
        rates = [r for r in (ce.norm(eid, d) if ce else None for d in (HEAT, COOL)) if r is not None]
        return any(r >= AIR_HANDLER_RATE for r in rates)

    def who(self, eid: str) -> dict[str, str]:
        st = self.hass.states.get(eid)
        area_id = self._area_id(eid)
        area = ar.async_get(self.hass).async_get_area(area_id) if area_id else None
        return {"name": (st.attributes.get("friendly_name") if st else None) or eid, "area": area.name if area else ""}

    def opening_open(self, eid: str, ignore: set[str] | frozenset[str] = frozenset()) -> bool:
        """A window / door of the same room is open now (a room unknown: none known open). `ignore`: entities that are
        not windows although HA calls them «opening» (a valve's feedback sensor: Горького 3's «Клапан 2.1»)."""
        area_id = self._area_id(eid)
        if not area_id:
            return False
        return any(s.state == STATE_ON and s.entity_id not in ignore and s.entity_id not in self.not_windows and s.attributes.get("device_class") in OPENING and self._area_id(s.entity_id) == area_id for s in self.hass.states.async_all("binary_sensor"))
