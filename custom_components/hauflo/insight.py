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

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.const import EVENT_STATE_CHANGED, STATE_ON
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .derived import NOT_A_STATE, SECONDS_PER_HOUR, num, outdoor
from .notify import labelled
from .signals import ENTITY_ID, policy as policy_of
from .storage import HaufloStore
from .tuning import TUNING

_LOGGER = logging.getLogger(__name__)
STORE_KEY = "hauflo.insights"
META = "_meta"  # the engine's own part of the store (first seen, openings, resets, choices made in HA)
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
# The observation gate (0.32, owner 10.11: an air handler «learned its norm» in under a day and said «slower» 10 times in
# 72 h): no norm before OBSERVE_H hours since the detector first saw the entity AND OBS_EPS learned episodes; a rate is
# compared only with episodes that started as far from the target (±GAP_BAND °C — the approach slows near it); the last
# EPISODES_KEEP episodes are kept with why each was (not) learned; a finding opens at most MAX_PER_DAY times a day.
OBSERVE_H = _T["observe_min_h"]
OBS_EPS = _T["observe_min_eps"]
GAP_BAND = _T["gap_band_c"]
# Fewer learned episodes from as far away (±GAP_BAND) than this: that band is still learning — no norm, no finding (the
# nearest of other bands would mix a slow near-target approach into a far one; acceptance 10.11 L5).
GAP_LIKE_MIN = _T["gap_like_min"]
# GET /api/hauflo/insights `?offset=`: ASCII digits up to this (a longer one is refused, never a 500).
OFFSET_MAX = _T["states_offset_max"]
OFFSET_RE = re.compile(r"[0-9]{1,%d}" % len(str(OFFSET_MAX)))
# `?limit=`: a longer page, up to this (the HA card «Диагностика» reads the house in one call a tick, not page by page).
LIMIT_MAX = _T["states_limit_max"]
# Choices made in HA the site doesn't hold yet, at most this many per status (the newest first; the cloud keeps findingsMax).
CHOICES_MAX = _T["choices_max"]
# Choices made in HA kept here at most (the newest; acceptance 10.11 L3: 300 POSTs on made-up entities — 300 records).
LOCAL_MAX = _T["local_max"]
LIMIT_RE = re.compile(r"[0-9]{1,%d}" % len(str(LIMIT_MAX)))
EPISODES_KEEP = _T["episodes_keep"]
MAX_PER_DAY = _T["insight_max_per_day"]
# Who reads the per-entity diagnosis in HA (GET /api/hauflo/insights): "admins" — HA's admins; "all" — every HA user.
# The default until Hauflo pushes the integrator's choice («Диагностика: всем / администраторам», signals.py `diag`).
DIAG_VISIBLE = TUNING["signals"]["diag"]["default"]
DIAG_ALL = "all"
# The card groups its rows by these (kind → section; a kind not listed — the card's «Прочее»), in this order.
SECTIONS = _T["sections"]
SECTION_ORDER = _T["section_order"]
# The integrator's own «порог ×N» for a finding: within these bounds.
FACTOR_MIN, FACTOR_MAX = _T["factor_min"], _T["factor_max"]
FEATURE = "insights"
DAY_S = timedelta(days=1).total_seconds()
USED, DROPPED = "used", "dropped:"
ACTIONS_IN = ("normal", "follow", "reset")

HEAT, COOL = "heat", "cool"
KIND = {HEAT: "no_heat", COOL: "no_cool"}
DIR_OF = {v: k for k, v in KIND.items()}
SIGN = {HEAT: 1, COOL: -1}
# What an episode carries over a restart (its samples don't: fresh observation is needed after it).
_KEEP = ("dir", "start", "gap", "target", "mode", "last_on", "alerted")


def _iso(ts: float) -> str:
    return dt_util.utc_from_timestamp(ts).isoformat()


def _round(v: float) -> float:
    return round(v, 2)


def _rates(v: Any) -> list[list[float | None]]:
    """Learned rates [rate, start gap]: a plain rate (learned before 0.32) has no gap known."""
    out: list[list[float | None]] = []
    for x in v if isinstance(v, list) else []:
        r, g = (x[0], x[1]) if isinstance(x, list) and len(x) == 2 else (x, None)
        if num(r) is not None and float(r) > 0:
            out.append([float(r), float(g) if num(g) is not None else None])
    return out


def _median_rate(rates: list[list[float | None]], gap: float | None) -> float | None:
    """The median rate; with `gap` — only of the episodes from as far away (±GAP_BAND), fewer than GAP_LIKE_MIN of them:
    None (that band is still learning; an episode learned before 0.32 has no gap and counts for none)."""
    if gap is None:
        like = [r for r, _g in rates]
    else:
        like = [r for r, g in rates if g is not None and abs(g - gap) <= GAP_BAND]
        if len(like) < GAP_LIKE_MIN:
            return None
    if not like:
        return None
    m = statistics.median(like)
    return m if m > 0 else None


class Detector:
    """One kind of «off its own norm». The engine calls `on_state` for its domains, `tick` every TICK seconds; `items`
    are the open findings now. `load` / `dump` are its persisted norms (a bad saved shape is read as empty)."""

    name = ""
    domains: tuple[str, ...] | None = ()  # None: every domain (a cheap first check in on_event, please)
    KINDS: tuple[str, ...] = ()  # the finding kinds it says (the key's first part)
    UNIT = ""  # its norm's unit — a token the reader words (translations: selector.insight_unit)
    DEV: tuple[str, str] = ("", "")  # an open finding's fields: what the norm was, what it is now
    diagnostic = False  # watches diagnostic entities too (a battery, a link)

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

    def on_learned(self, eid: str, direction: str, rate: float, now: float, gap: float | None = None) -> None:
        """ClimateEffect learned a clean episode's rate (°C/h, from `gap` °C away) — a detector following the trend listens."""

    def tick(self, now: float) -> None:  # pragma: no cover
        raise NotImplementedError

    def items(self, now: float) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    # --- the integrator's view (0.32): one state per (kind, entity) -------------------------------------------------

    def keys(self) -> list[tuple[str, str]]:
        """(kind, entity) it knows now — learning, ready or off its norm."""
        return []

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        """learning{have, need, hours, needH} or ready{norm, unit, n} — a detector with a fixed bar is ready at once."""
        return {"ready": {"norm": None, "unit": self.UNIT, "n": 0}}

    def excluded(self, kind: str, eid: str) -> str | None:
        """Why it is not judged at all (air_handler | hidden | no_outdoor | coarse_battery | season), None — judged."""
        return None if self.ctx.visible(eid, self.diagnostic) else "hidden"

    def episodes(self, eid: str) -> list[dict[str, Any]]:
        return []

    def extra(self, kind: str, eid: str) -> dict[str, Any]:
        """More of the row for the integrator's view (analytics.py: its unit, the standard, a tip, «нормы нет — спросим»)."""
        return {}

    def reset(self, eid: str, kind: str) -> None:
        """«Сбросить обучение»: its norms of this entity forgotten (learned anew)."""

    def learning(self, eid: str, have: int, need: int, now: float) -> dict[str, Any]:
        return {"learning": {"have": have, "need": need, "hours": _round(self.ctx.hours_seen(self.name, eid, now)), "needH": OBSERVE_H}}

    def state(self, eid: str, now: float, kind: str | None = None, open_items: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """One of learning / ready / deviation{since, was, now} / normal{by, at} / excluded{reason}."""
        kind = kind or next((k for k, e in self.keys() if e == eid), self.KINDS[0] if self.KINDS else "")
        key = f"{kind}|{eid}"
        if (why := self.excluded(kind, eid)) is not None:
            return {"excluded": {"reason": why}}
        f = self.ctx.finding(key)
        if f and f.get("normal"):
            return {"normal": {"by": f.get("by"), "at": f.get("at")}}
        opened = open_items if open_items is not None else {i["key"]: i for i in self.items(now)}
        if (item := opened.get(key)) is not None:
            return {"deviation": {"since": item["since"], "was": item.get(self.DEV[0]), "now": item.get(self.DEV[1])}}
        return self.progress(kind, eid, now)


class ClimateEffect(Detector):
    """Heating / cooling without effect, against the zone's own rate (°C per hour from wanting to reaching)."""

    name = "climate"
    domains = ("climate",)
    KINDS = (KIND[HEAT], KIND[COOL])
    UNIT = "c_per_h"
    DEV = ("expectedH", "hours")

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        # entity → direction → its last learned episodes [rate °C/h, the start gap °C | None (learned before 0.32)]
        self.norms: dict[str, dict[str, list[list[float | None]]]] = {}
        self.eps: dict[str, dict[str, Any]] = {}  # entity → the running episode
        self.ring: dict[str, list[dict[str, Any]]] = {}  # entity → its last EPISODES_KEEP episodes, learned or not (why)

    def load(self, data: dict[str, Any]) -> None:
        for eid, by in (data.get("norms") or {}).items():
            if isinstance(by, dict):
                got = {d: _rates(by.get(d))[-SAMPLES:] for d in (HEAT, COOL)}
                self.norms[str(eid)] = {d: v for d, v in got.items() if v}
        for eid, ring in (data.get("ring") or {}).items():
            if isinstance(ring, list):
                self.ring[str(eid)] = [e for e in ring if isinstance(e, dict)][-EPISODES_KEEP:]
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
        return {"norms": self.norms, "episodes": {eid: {k: ep.get(k) for k in _KEEP} for eid, ep in self.eps.items()}, "ring": self.ring}

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
                self._drop(eid, now, "gone")
            for eid in [e for e in self.ring if e not in self.eps and self.ctx.hass.states.get(e) is None]:
                del self.ring[eid]

    def _eval(self, st: State, now: float) -> None:
        eid = st.entity_id
        if st.state in NOT_A_STATE:
            return  # frozen: no word and no end (HA starting, the bus away) — «недоступен» is its own alarm
        cur, tgt = num(st.attributes.get("current_temperature")), num(st.attributes.get("temperature"))
        if cur is None or tgt is None:
            self._drop(eid, now, "no_temp")
            return
        ep = self.eps.get(eid)
        if ep is not None:
            d = ep["dir"]
            if ep["target"] != tgt:
                self._drop(eid, now, "target")  # the target changed: a new question, the old episode isn't learned
            elif ep["mode"] != st.state:
                self._drop(eid, now, "mode")
            elif not self._season_ok(d):
                self._drop(eid, now, "season")
            elif (cur - tgt) * SIGN[d] >= -REACH:
                self._finish(eid, now)
            elif self.ctx.opening_open(eid):
                self._drop(eid, now, "window")  # a window open: the episode says nothing — a fresh one after it closes
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
        rate = self.norm(st.entity_id, d, ep["gap"])
        if rate is None:
            return  # still learning
        expected = ep["gap"] / rate
        hours = (now - ep["start"]) / SECONDS_PER_HOUR
        if hours <= max(MIN_H, self.ctx.factor(f"{KIND[d]}|{st.entity_id}", FACTOR) * expected):
            return
        change = self._change(ep, cur, now)
        if change is None or change * SIGN[d] >= FLAT_C:
            return  # not observed long enough since start / a restart, or it is moving after all
        if now - ep["last_on"] > FLAT_H * SECONDS_PER_HOUR:
            return  # the thermostat stopped asking: not this detector's question
        if self.ctx.air_handler(st.entity_id):
            return  # an air handler's supply air: no zone's norm to fall behind (owner 09.11, 0.32 sweep)
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

    def _log(self, eid: str, ep: dict[str, Any], now: float, result: str) -> None:
        """One line of the entity's episode ring (the integrator's «why not learned yet»)."""
        hours = (now - ep["start"]) / SECONDS_PER_HOUR
        outdoor_now = self.ctx.outdoor_c()
        line = {"start": _iso(ep["start"]), "dir": ep["dir"], "gap": _round(ep["gap"]), "hours": _round(hours), "result": result}
        if hours > 0:
            line["rate"] = _round(ep["gap"] / hours)
        if outdoor_now is not None:
            line["outdoor"] = _round(outdoor_now)
        self.ring[eid] = [*self.ring.get(eid, []), line][-EPISODES_KEEP:]

    def _finish(self, eid: str, now: float) -> None:
        ep = self.eps.pop(eid)
        hours = (now - ep["start"]) / SECONDS_PER_HOUR
        # Learned: a clean episode only — not carried over a restart, not one that was said (a failure isn't the norm),
        # from far enough away.
        why = "resumed" if ep["resumed"] else "said" if ep["alerted"] is not None else "small_gap" if ep["gap"] < LEARN_GAP or hours <= 0 else None
        self._log(eid, ep, now, USED if why is None else f"{DROPPED}{why}")
        if why is None:
            rates = self.norms.setdefault(eid, {}).setdefault(ep["dir"], [])
            rates.append([round(ep["gap"] / hours, 4), round(ep["gap"], 3)])
            del rates[:-SAMPLES]
            self.ctx.learned(eid, ep["dir"], rates[-1][0], now, ep["gap"])
        self.ctx.dirty()

    def _drop(self, eid: str, now: float | None = None, why: str = "") -> None:
        ep = self.eps.pop(eid, None)
        if ep is not None:
            if now is not None:
                self._log(eid, ep, now, f"{DROPPED}{why}")
            self.ctx.dirty()

    def norm(self, eid: str, d: str, gap: float | None = None) -> float | None:
        """The zone's own rate, °C/h — only after the observation gate (OBS_EPS learned episodes AND OBSERVE_H hours since
        first seen); with `gap` — of the episodes that started as far away (±GAP_BAND), fewer than GAP_LIKE_MIN: None."""
        rates = self.norms.get(eid, {}).get(d) or []
        if len(rates) < self.need() or not self.ctx.observed(self.name, eid):
            return None
        return _median_rate(rates, gap)

    def raw(self, eid: str, d: str) -> float | None:
        """Its rate before the gate (an air handler is known by a rate no room reaches — in its first day too)."""
        rates = self.norms.get(eid, {}).get(d) or []
        return _median_rate(rates, None) if len(rates) >= LEARN_MIN else None

    @staticmethod
    def need() -> int:
        return max(LEARN_MIN, OBS_EPS)

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        outdoor_now = self.ctx.outdoor_c()
        for eid, ep in self.eps.items():
            if ep["alerted"] is None or self.ctx.air_handler(eid):
                continue
            st = self.ctx.hass.states.get(eid)
            rate = self.norm(eid, ep["dir"], ep["gap"])
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

    # --- what the integrator sees (0.32) -------------------------------------------------------------------------------

    def keys(self) -> list[tuple[str, str]]:
        out = []
        for eid in dict.fromkeys([*self.norms, *self.eps, *self.ring]):
            dirs = [d for d in (HEAT, COOL) if d in self.norms.get(eid, {})]
            if eid in self.eps and self.eps[eid]["dir"] not in dirs:
                dirs.append(self.eps[eid]["dir"])
            out += [(KIND[d], eid) for d in dirs or [HEAT]]
        return out

    def excluded(self, kind: str, eid: str) -> str | None:
        if self.ctx.air_handler(eid):
            return "air_handler"
        if not self._season_ok(DIR_OF[kind]):
            return "season"
        return super().excluded(kind, eid)

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        d = DIR_OF[kind]
        rates = self.norms.get(eid, {}).get(d) or []
        m = self.norm(eid, d)
        if m is None:
            return self.learning(eid, len(rates), self.need(), now)
        return {"ready": {"norm": _round(m), "unit": self.UNIT, "n": len(rates)}}

    def episodes(self, eid: str) -> list[dict[str, Any]]:
        return list(self.ring.get(eid, []))

    def reset(self, eid: str, kind: str) -> None:
        d = DIR_OF[kind]
        self.norms.get(eid, {}).pop(d, None)
        if not self.norms.get(eid):
            self.norms.pop(eid, None)
        if (self.eps.get(eid) or {}).get("dir") == d:
            self.eps.pop(eid)
        self.ring[eid] = [e for e in self.ring.get(eid, []) if e.get("dir") != d]
        if not self.ring[eid]:
            del self.ring[eid]


class StuckSensor(Detector):
    """A temperature / humidity sensor that normally changes many times a day and hasn't for STUCK_H hours."""

    name = "stuck"
    domains = ("sensor",)
    KINDS = ("stuck",)
    UNIT = "per_day"
    DEV = ("perDay", "hours")

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

    def keys(self) -> list[tuple[str, str]]:
        return [("stuck", eid) for eid in self.recs]

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        days = (self.recs.get(eid) or {}).get("days") or []
        m = self.norm(eid)
        if m is None:
            return self.learning(eid, len(days), LEARN_DAYS, now)
        return {"ready": {"norm": _round(m), "unit": self.UNIT, "n": len(days)}}

    def reset(self, eid: str, kind: str) -> None:
        self.recs.pop(eid, None)


# The trends (predict.py) subclass Detector from here — imported once it exists.
from .analytics import ANALYTICS, is_note, section_of  # noqa: E402
from .noeffect import NOEFFECT  # noqa: E402
from .predict import TRENDS  # noqa: E402

DETECTORS: tuple[type[Detector], ...] = (ClimateEffect, StuckSensor, *TRENDS, *NOEFFECT, *ANALYTICS)


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
        # The site's choice per finding (signals.py `findings` {"kind|entity": {normal, factor, resetAt, by, at}}): set by
        # the agent. «Это норма» — not in the status, the detector learns on; resetAt — its norms forgotten once.
        self.finding_of: Callable[[str], dict[str, Any] | None] | None = None
        self.first: dict[str, float] = {}  # "detector|entity" → first seen (the OBSERVE_H gate)
        self.opens: dict[str, list[float]] = {}  # finding key → when it opened within the last day (MAX_PER_DAY)
        self.resets: dict[str, str] = {}  # finding key → the resetAt already applied
        self.local: dict[str, dict[str, Any]] = {}  # finding key → a choice made in HA (POST /api/hauflo/insights)
        self.owner: dict[str, Detector] = {k: det for det in self.detectors for k in det.KINDS}
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
        meta = data.get(META) if isinstance(data, dict) else None
        self._load_meta(meta if isinstance(meta, dict) else {})
        # Learned before 0.32 (no «first seen» kept): observed long enough already — the episodes' count still gates.
        for det in self.detectors:
            for _kind, eid in self._keys(det):
                self.first.setdefault(f"{det.name}|{eid}", self.started - OBSERVE_H * SECONDS_PER_HOUR)
        # Open before the restart: still open (no new opening counted, no «снова в норме»).
        self._listed = frozenset(i["key"] for i in self._raw(self.started))
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_event))
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=TICK)))

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    def _load_meta(self, meta: dict[str, Any]) -> None:
        self.first = {str(k): float(v) for k, v in (meta.get("first") or {}).items() if num(v) is not None}
        self.opens = {str(k): [float(t) for t in v if num(t) is not None][-MAX_PER_DAY:] for k, v in (meta.get("opens") or {}).items() if isinstance(v, list)}
        self.resets = {str(k): str(v) for k, v in (meta.get("resets") or {}).items() if isinstance(v, str)}
        self.local = {str(k): v for k, v in (meta.get("local") or {}).items() if isinstance(v, dict)}
        self._trim_local()

    def _trim_local(self) -> None:
        """At most LOCAL_MAX choices made in HA: the oldest go (the site holds what it folded in by then)."""
        if len(self.local) > LOCAL_MAX:
            keep = sorted(self.local.items(), key=lambda kv: str(kv[1].get("at") or ""), reverse=True)[:LOCAL_MAX]
            self.local = dict(keep)

    def data(self) -> dict[str, Any]:
        return {**{det.name: det.dump() for det in self.detectors}, META: {"first": self.first, "opens": self.opens, "resets": self.resets, "local": self.local}}

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
    def learned(self, eid: str, direction: str, rate: float, now: float, gap: float | None = None) -> None:
        for det in self.detectors:
            self._run(det.on_learned, eid, direction, rate, now, gap)

    @callback
    def _tick(self, _now: Any = None) -> None:
        now = self.clock()
        for det in self.detectors:
            self._run(det.tick, now)
        self._seen(now)
        self._after()

    @staticmethod
    def _keys(det: Detector) -> list[tuple[str, str]]:
        try:
            return det.keys()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Hauflo insights: %s keys failed", det.name)
            return []

    def _seen(self, now: float) -> None:
        """«First seen» of what each detector knows; forgotten with it (once HA had time to load everything)."""
        known = set()
        for det in self.detectors:
            for _kind, eid in self._keys(det):
                k = f"{det.name}|{eid}"
                known.add(k)
                if k not in self.first:
                    self.first[k] = now
                    self.dirty()
        if now - self.started > GONE_AFTER and set(self.first) - known:
            self.first = {k: v for k, v in self.first.items() if k in known}
            self.dirty()

    def hours_seen(self, name: str, eid: str, now: float) -> float:
        first = self.first.get(f"{name}|{eid}")
        return max(0.0, (now - first) / SECONDS_PER_HOUR) if first is not None else 0.0

    def mark(self, name: str, eid: str, now: float) -> None:
        """«First seen» right away — for what a detector learns between the ticks (a learned rate)."""
        if f"{name}|{eid}" not in self.first:
            self.first[f"{name}|{eid}"] = now
            self.dirty()

    def observed(self, name: str, eid: str) -> bool:
        return self.hours_seen(name, eid, self.clock()) >= OBSERVE_H

    def finding(self, key: str) -> dict[str, Any] | None:
        """The choice about this finding: the site's (Hauflo), or one made in HA since (the newer wins)."""
        cloud = self.finding_of(key) if self.finding_of else None
        loc = self.local.get(key)
        if loc is not None and (cloud is None or str(loc.get("at") or "") >= str(cloud.get("at") or "")):
            return loc
        return cloud

    def is_normal(self, key: str) -> bool:
        """The site said «это норма» for this finding: hidden, and a detector with a norm keeps learning through it."""
        return bool((self.finding(key) or {}).get("normal"))

    def factor(self, key: str, default: float) -> float:
        """The detector's «×N» bar — the integrator's own for this finding if set («Порог…»)."""
        f = self.finding(key)
        return float(f["factor"]) if f and num(f.get("factor")) is not None else default

    @staticmethod
    def _run(fn: Callable[..., None], *args: Any) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 — a diagnosis never breaks HA
            _LOGGER.exception("Hauflo insights: %s failed", getattr(fn, "__qualname__", fn))

    @callback
    def _after(self) -> None:
        now = self.clock()
        keys = frozenset(i["key"] for i in self._shown(now))
        for k in keys - self._listed:
            self.opens[k] = [*(t for t in self.opens.get(k, []) if now - t < DAY_S), now][-MAX_PER_DAY:]
            self.dirty()
        for k in [k for k, ts in self.opens.items() if k not in keys and all(now - t >= DAY_S for t in ts)]:
            del self.opens[k]
            self.dirty()
        if keys != self._listed:
            self._listed = keys
            if self.on_change:
                self.on_change()

    def _raw(self, now: float) -> list[dict[str, Any]]:
        """The detectors' open findings, without those the site called «это норма»."""
        out: list[dict[str, Any]] = []
        for det in self.detectors:
            try:
                out.extend(det.items(now))
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Hauflo insights: %s items failed", det.name)
        return [i for i in out if not (self.finding(i["key"]) or {}).get("normal")]

    def _shown(self, now: float) -> list[dict[str, Any]]:
        """What goes out: a finding new now (not listed) that already opened MAX_PER_DAY times this day waits."""
        out = []
        for i in self._raw(now):
            k = i["key"]
            if k in self._listed or sum(1 for t in self.opens.get(k, []) if now - t < DAY_S) < MAX_PER_DAY:
                out.append(i)
        # The analytics' hints (п.8) after the findings: many notes never push a «нагрев без эффекта» out of the list.
        return sorted(out, key=lambda i: (is_note(i["kind"]), i["since"], i["key"]))[:LIST_MAX]

    def items(self) -> list[dict[str, Any]]:
        return self._shown(self.clock())

    # --- the integrator's view and choices (0.32: GET / POST /api/hauflo/insights, the site's findings) ---------------

    def states(self, entity: str | None = None, offset: int = 0, limit: int = LIST_MAX) -> dict[str, Any]:
        """Every (kind, entity) a detector knows with its state, `limit` a page; with `entity` — its episodes too."""
        now = self.clock()
        rows: list[dict[str, Any]] = []
        for det in self.detectors:
            try:
                opened = {i["key"]: i for i in det.items(now)}
                for kind, eid in det.keys():
                    if entity is None or eid == entity:
                        rows.append({"key": f"{kind}|{eid}", "kind": kind, "detector": det.name, "entityId": eid, "section": section_of(kind), **det.extra(kind, eid), **det.state(eid, now, kind, opened)})
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Hauflo insights: %s states failed", det.name)
        rows.sort(key=lambda r: (r["entityId"], r["kind"]))
        page = rows[offset : offset + limit]
        for r in page:
            r.update(self.who(r["entityId"]))
        out: dict[str, Any] = {"total": len(rows), "offset": offset, "items": page}
        if entity is not None:
            out["episodes"] = [e for det in self.detectors for e in det.episodes(entity)]
        return out

    def reset(self, kind: str, eid: str, at: str) -> None:
        """«Сбросить обучение»: the detector's norms of this entity forgotten, observed anew from now."""
        det = self.owner.get(kind)
        if det is None:
            return
        self._run(det.reset, eid, kind)
        key = f"{kind}|{eid}"
        self.first[f"{det.name}|{eid}"] = self.clock()
        self.resets[key] = at
        self.opens.pop(key, None)
        self.dirty()

    @callback
    def findings_changed(self) -> None:
        """The site's findings changed (signals.py): a new resetAt resets once; «это норма» leaves the list at once."""
        for det in self.detectors:
            for kind, eid in self._keys(det):
                key = f"{kind}|{eid}"
                at = (self.finding(key) or {}).get("resetAt")
                if at and at != self.resets.get(key):
                    self.reset(kind, eid, at)
        self._after()

    @callback
    def choose(self, kind: str, eid: str, action: str, by: str, factor: float | None = None) -> None:
        """A choice made in HA (an admin): normal — «это норма»; follow — «снова следить» (with its own ×N); reset."""
        at = _iso(self.clock())
        key = f"{kind}|{eid}"
        if action == "reset":
            self.reset(kind, eid, at)
        else:
            self.local.pop(key, None)
            self.local[key] = {"by": by, "at": at, **({"normal": True} if action == "normal" else {}), **({"factor": factor} if factor is not None else {})}
            self._trim_local()
            self.dirty()
        self._after()

    def choices(self) -> dict[str, dict[str, Any]]:
        """Choices made in HA (an admin's «Это норма» / «Снова следить») the site's findings don't hold yet — the status
        carries them and Hauflo folds them in with the same `at`; the push back then equals them and they stop going."""
        out: dict[str, dict[str, Any]] = {}
        for key, loc in sorted(self.local.items(), key=lambda kv: str(kv[1].get("at") or ""), reverse=True):
            cloud = (self.finding_of(key) if self.finding_of else None) or {}
            # «Снова следить» with the canon's ×N over no finding there is the site's default already — nothing to say.
            if str(loc.get("at") or "") > str(cloud.get("at") or "") and (cloud or "normal" in loc or "factor" in loc):
                out[key] = {k: loc[k] for k in ("normal", "factor", "by", "at") if k in loc}
            if len(out) >= CHOICES_MAX:
                break
        return out

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
        rates = [r for r in (ce.raw(eid, d) if ce else None for d in (HEAT, COOL)) if r is not None]
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


def diag_visible(hass: HomeAssistant | None) -> str:
    """Who reads the diagnosis now: the integrator's choice Hauflo pushed with the site's signals, else the default."""
    pushed = ((policy_of(hass) or {}).get("diag") or {}).get("visible")
    return pushed if isinstance(pushed, str) else DIAG_VISIBLE


class InsightsView(HomeAssistantView):
    """The per-entity diagnosis (0.32). GET → Insights.states() (`?entity=` — with its episodes, `?offset=` — the next
    page, `?limit=` its length up to LIMIT_MAX; with the canon's sections and the engine's tick and whether the reader may answer): HA's admins, everyone when the diagnosis is
    shown to all (`diag_visible`). POST (admins only) {entity, kind, action: normal | follow |
    reset, factor?} — «это норма» / «снова следить» (its own ×N within FACTOR_MIN..FACTOR_MAX) / «сбросить обучение»."""

    url = "/api/hauflo/insights"
    name = "api:hauflo:insights"
    requires_auth = True

    def __init__(self, ins_of: Callable[[], Insights | None]) -> None:
        self.ins_of = ins_of

    async def get(self, request):
        admin = request["hass_user"].is_admin
        if not admin and diag_visible(request.app[KEY_HASS]) != DIAG_ALL:
            return self.json_message("admin only", 403)
        ins = self.ins_of()
        if ins is None:
            return self.json_message("not ready", 409)
        entity = request.query.get("entity")
        offset = request.query.get("offset", "0")
        limit = request.query.get("limit", str(LIST_MAX))
        if (entity is not None and not ENTITY_ID.match(entity)) or not OFFSET_RE.fullmatch(offset) or int(offset) > OFFSET_MAX:
            return self.json_message("entity / offset", 400)
        if not LIMIT_RE.fullmatch(limit) or not 1 <= int(limit) <= LIMIT_MAX:
            return self.json_message(f"limit: 1..{LIMIT_MAX}", 400)
        out = ins.states(entity, int(offset), int(limit))
        # The card's grouping and refresh: the canon's sections, the engine's tick; whether this reader may answer.
        return self.json({**out, "sections": SECTIONS, "sectionOrder": SECTION_ORDER, "tickS": TICK, "canEdit": admin})

    async def post(self, request):
        user = request["hass_user"]
        if not user.is_admin:
            return self.json_message("admin only", 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        ins = self.ins_of()
        if ins is None:
            return self.json_message("not ready", 409)
        if not isinstance(body, dict) or not {"entity", "kind", "action"} <= set(body) <= {"entity", "kind", "action", "factor"}:
            return self.json_message("{entity, kind, action, factor?} expected", 400)
        entity, kind, action, factor = body["entity"], body["kind"], body["action"], body.get("factor")
        # A list / an object for a word is a 400, never a 500 (acceptance 10.11 L1: `kind: []` — unhashable).
        if not isinstance(entity, str) or not ENTITY_ID.match(entity) or not isinstance(kind, str) or kind not in ins.owner or not isinstance(action, str) or action not in ACTIONS_IN:
            return self.json_message("entity / kind / action", 400)
        if factor is not None and (action != "follow" or isinstance(factor, bool) or not isinstance(factor, (int, float)) or not FACTOR_MIN <= float(factor) <= FACTOR_MAX):
            return self.json_message(f"factor: {FACTOR_MIN}..{FACTOR_MAX} with follow", 400)
        ins.choose(kind, entity, action, user.name or user.id, float(factor) if factor is not None else None)
        return self.json(ins.states(entity))
