"""«Диагностика — аналитика» (0.32 п.8): ONE engine for every series the house has, instead of a detector per case.

The class (CLAUDE.md §10) — «a measured quantity off what is normal for it»: a fridge plug drawing twice its usual power
at night, a water meter running at an hour it never does, CO2 in a bedroom far above its usual evening, a humidity that
stopped falling when the extract fan runs, a floor loop heating three times slower than its neighbours, an air handler's
stages chattering, a boiler short-cycling. Three detectors over one reference table (tuning.json «analytics»):

  * `SeriesNorm` — a series per entity, chosen by its device class / unit / state class / integration from
    `analytics.series[]` (a new kind of series is one more object there, never code); an hourly value (time-weighted
    mean, a meter's increase, a count of starts) kept per season × hour-of-week bucket (`analytics.buckets`); the norm is
    the bucket's median and its spread the MAD (robust: one wild hour moves neither), falling back to the hour of any day
    and then to the whole season while a bucket fills. A type's norm (`analytics.norms[]`, a published standard: CO2
    EN 16798-1, PM2.5 WHO 2021) judges a series before its own is learned; a series with neither — `excluded:no_norm`
    and a question for later (no guessing, no LLM);
  * `Correlation` — «X switched on → Y moves» (`analytics.correlations[]`): a relay on and its plug's power not rising,
    an extract fan on and the room's CO2 / humidity not falling. The expected change is learned per pair; a pair is bound
    by the mapping's pairs (noeffect.py), the same device or the same room (one candidate only — never guessed);
  * `LoopTuning` — a heating / cooling / humidity loop's tuning (`analytics.loops[]`: floor, radiator, air handler, AC,
    boiler, hot-water tank, humidifier, valve): rate at full demand, time to setpoint, overshoot, starts a day, duty —
    against its peers of the same type (median, the integrator's «×N») or the type's bar; the word says what to check.

Hints only, the staff's (status `insights`, «Диагностика»): nothing here switches, sets or writes anything in the house.
The shared observation gate (tuning.json «insight» observe_min_h / observe_min_eps), «Это норма», «×N» and «Сбросить
обучение» are the engine's (insight.py). Norms survive a restart (HaufloStore); an hour cut by a restart is dropped."""

from __future__ import annotations

import re
import statistics
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import State
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .derived import NOT_A_STATE, SECONDS_PER_HOUR, num
from .insight import GONE_AFTER, OBS_EPS, Detector, _iso, _round
from .tuning import TUNING

if TYPE_CHECKING:
    from .insight import Insights

_A = TUNING["analytics"]
PERIOD = _A["period_s"]
DISCOVER = _A["discover_s"]
MAX_SERIES = _A["max_series"]
BUDGET_MS = _A["tick_budget_ms"]
KEEP = _A["keep"]
Z, Z_END = _A["z"], _A["z_end"]
MAD_K = _A["mad_k"]
SPREAD_FLOOR = _A["spread_floor"]
HOLD = _A["hold"]
FACTOR = _A["factor"]
NOTE_PREFIXES = tuple(_A["note_prefixes"])
SEASONS = _A["seasons"]
MID = SEASONS[-1]  # neither the heating nor the cooling season
ANY = "any"  # a series not split by season
DAYS = _A["buckets"]["days"]  # weekday → its day group (workday / weekend)
HOUR_SPAN = _A["buckets"]["hour_span"]
LEVELS = _A["buckets"]["levels"]
# The levels a value is judged at, each with the own bucket's samples it needs beside: «the hour of any day» says «off»
# only when the bucket's own values (a weekend's hour, a workday's) agree — the whole season never judges, it only
# stands for the norm while the hours fill (a daily profile is not «off» at 03:00 because the day is at 500 W).
JUDGE: dict[str, int] = _A["buckets"]["judge"]
CORR_KEEP = _A["corr_keep"]
LOOP_KEEP = _A["loop_keep"]
LOOP_DAYS = _A["loop_days"]
LOOP_MIN_DAYS = _A["loop_min_days"]
LOOP_MAX_S = _A["loop_max_h"] * SECONDS_PER_HOUR
OVERSHOOT_S = _A["overshoot_h"] * SECONDS_PER_HOUR
PEERS_MIN = _A["peers_min"]
LOOP_UNITS = _A["loop_units"]
LOOP_METRICS = _A["loop_metrics"]
# An hour of a mean series counts when at least this share of it had a value.
COVER = _A["cover"]
# A period missed longer than this (HA stopped): its hours are not invented — the next one starts afresh.
MAX_CATCHUP = _A["max_catchup"]
SECTIONS: dict[str, str] = TUNING["insight"]["sections"]


def _rx(v: Any) -> Any:
    """A match table: its strings are regexes (tuning.py checked them), its numbers flags (1 — must be, 0 — must not)."""
    return {k: _rx(x) for k, x in v.items()} if isinstance(v, dict) else re.compile(v) if isinstance(v, str) else v


SPECS: list[dict[str, Any]] = _A["series"]
SPEC = {s["kind"]: s for s in SPECS}
MATCH = {s["kind"]: _rx(s["match"]) for s in SPECS}
TO = {s["kind"]: re.compile(s["to"]) for s in SPECS if "to" in s}
NORMS = [{**n, "match": _rx(n["match"])} for n in _A["norms"]]
CORRS = [{**c, "trigger": _rx(c["trigger"]), "to": re.compile(c["to"])} for c in _A["correlations"]]
READERS = {d: {k: (re.compile(v) if v else None) if k in ("active", "off") else v for k, v in r.items()} for d, r in _A["readers"].items()}
LOOPS = [{**lp, "match": _rx(lp["match"])} for lp in _A["loops"]]
LOOP_TYPES = {lp["type"]: lp for lp in LOOPS}


def is_note(kind: str) -> bool:
    """An analytics hint (a «заметка» of the digest), not one of the trend detectors' findings."""
    return kind.startswith(NOTE_PREFIXES)


def section_of(kind: str) -> str | None:
    return SECTIONS.get(kind)


def _local(ts: float):
    return dt_util.as_local(dt_util.utc_from_timestamp(ts))


def _day(ts: float) -> str:
    return _local(ts).date().isoformat()


def _matches(match: dict[str, Any], facts: dict[str, Any]) -> bool:
    for k, want in match.items():
        have = facts.get(k)
        if isinstance(want, re.Pattern):
            if not isinstance(have, str) or not want.search(have):
                return False
        elif callable(have):
            if bool(have()) != bool(want):
                return False
        elif have != want:
            return False
    return True


def robust(xs: list[float]) -> tuple[float, float]:
    """(median, spread): the spread is MAD × MAD_K (≈ σ of a normal series), floored by SPREAD_FLOOR of the median — a
    series that never moves is not «off» by a hair."""
    m = statistics.median(xs)
    mad = statistics.median(abs(x - m) for x in xs)
    return m, max(MAD_K * mad, SPREAD_FLOOR * abs(m), 1e-9)


def value_of(spec: dict[str, Any], st: State | None) -> float | None:
    """The state in the spec's unit (`units` factor, `minus` first: °F → °C); None — no number, or a unit it doesn't know."""
    if st is None or st.state in NOT_A_STATE:
        return None
    x = num(st.state)
    if x is None:
        return None
    units = spec.get("units")
    if units is None:
        return x
    unit = st.attributes.get("unit_of_measurement")
    if unit not in units:
        return None
    return (x - (spec.get("minus") or {}).get(unit, 0)) * units[unit]


class _Facts:
    """What a reference table matches an entity by: domain, device class, state class, unit, integration, model."""

    def __init__(self, ctx: Insights) -> None:
        self.ctx = ctx

    def of(self, st: State, **more: Any) -> dict[str, Any]:
        a = st.attributes
        entry = self.ctx.entry(st.entity_id)
        dev = dr.async_get(self.ctx.hass).async_get(entry.device_id) if entry and entry.device_id else None
        return {
            "domain": st.domain,
            "device_class": a.get("device_class") or (entry.original_device_class if entry else None),
            "state_class": a.get("state_class"),
            "unit": a.get("unit_of_measurement"),
            "platform": entry.platform if entry else None,
            "model": dev.model if dev else None,
            "manufacturer": dev.manufacturer if dev else None,
            **more,
        }


# --- 1. a series against its own norm ---------------------------------------------------------------------------------


class SeriesNorm(Detector):
    """A series per entity (analytics.series[]), an hourly value per season × hour-of-week bucket, judged against its
    bucket's median ± MAD (robust z ≥ Z, |Δ| ≥ the kind's min_delta, HOLD hours in a row) or the type's norm."""

    name = "series"
    domains = None  # every domain: on_event looks the entity up in its series first
    KINDS = tuple(f"norm_{s['kind']}" for s in SPECS)
    DEV = ("limit", "value")

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        # entity → {k: kind, b: {bucket: [hourly values]}, bad, bb (hours off own / type norm in a row), open, norm, n, last}
        self.series: dict[str, dict[str, Any]] = {}
        self.acc: dict[str, dict[str, Any]] = {}  # entity → the hour being measured (not kept over a restart: «resumed»)
        self.facts = _Facts(ctx)
        self._found_at: float | None = None
        self.took_ms = 0.0

    # --- persisted ----------------------------------------------------------------------------------------------------

    def load(self, data: dict[str, Any]) -> None:
        for eid, s in (data.get("series") or {}).items():
            if not isinstance(s, dict) or s.get("k") not in SPEC:
                continue
            b = {str(k): [float(x) for x in v if num(x) is not None][-KEEP:] for k, v in (s.get("b") or {}).items() if isinstance(v, list)}
            op = s.get("open")
            self.series[str(eid)] = {
                "k": s["k"],
                "b": {k: v for k, v in b.items() if v},
                "bad": int(num(s.get("bad")) or 0),
                "bb": int(num(s.get("bb")) or 0),
                "open": op if isinstance(op, dict) and num(op.get("since")) is not None else None,
                "norm": num(s.get("norm")),
                "n": int(num(s.get("n")) or 0),
                "tn": s.get("tn") if isinstance(s.get("tn"), int) and 0 <= s["tn"] < len(NORMS) else None,
                "last": num(s.get("last")),
            }

    def dump(self) -> dict[str, Any]:
        return {"series": self.series}

    # --- which entities ---------------------------------------------------------------------------------------------------

    def spec_of(self, st: State) -> tuple[str | None, int | None]:
        facts = self.facts.of(st)
        for spec in SPECS:
            if _matches(MATCH[spec["kind"]], facts) and ("units" not in spec or facts["unit"] in spec["units"]):
                tn = next((i for i, n in enumerate(NORMS) if _matches(n["match"], {**facts, "kind": spec["kind"]})), None)
                return spec["kind"], tn
        return None, None

    def _discover(self, now: float) -> None:
        hass = self.ctx.hass
        # Gone from HA (after HA had time to load everything): forgotten.
        if now - self.ctx.started > GONE_AFTER:
            for eid in [e for e in self.series if hass.states.get(e) is None]:
                del self.series[eid]
                self.acc.pop(eid, None)
                self.ctx.dirty()
        free = MAX_SERIES - len(self.series)
        if free <= 0:
            return
        found: list[tuple[int, str, str, int | None]] = []
        order = {s["kind"]: i for i, s in enumerate(SPECS)}
        for st in hass.states.async_all():
            if st.entity_id in self.series or not self.ctx.visible(st.entity_id):
                continue
            kind, tn = self.spec_of(st)
            if kind is not None:
                found.append((order[kind], st.entity_id, kind, tn))
        for _o, eid, kind, tn in sorted(found)[:free]:
            self.series[eid] = {"k": kind, "b": {}, "bad": 0, "bb": 0, "open": None, "norm": None, "n": 0, "tn": tn, "last": None}
            self.ctx.mark(self.name, eid, now)
            self.ctx.dirty()

    # --- the hour being measured ----------------------------------------------------------------------------------------

    def _new_acc(self, eid: str, spec: dict[str, Any], now: float) -> dict[str, Any]:
        st = self.ctx.hass.states.get(eid)
        v = value_of(spec, st) if spec["agg"] != "count" else None
        a = {"p": int(now // PERIOD), "partial": True, "t": now, "v": v, "v0": v is not None, "sw": 0.0, "w": 0.0, "inc": 0.0, "n": 0}
        self.acc[eid] = a
        return a

    @staticmethod
    def _integrate(a: dict[str, Any], t: float) -> None:
        if a["v"] is not None and t > a["t"]:
            a["sw"] += a["v"] * (t - a["t"])
            a["w"] += t - a["t"]
        a["t"] = max(a["t"], t)

    def on_state(self, new: State, now: float) -> None:  # pragma: no cover — on_event has the state before
        return

    def on_event(self, old: State | None, new: State, now: float) -> None:
        s = self.series.get(new.entity_id)
        if s is None:
            return
        spec = SPEC[s["k"]]
        if not spec.get("own", 1):
            return
        a = self.acc.get(new.entity_id) or self._new_acc(new.entity_id, spec, now)
        self._roll(new.entity_id, s, spec, a, now)
        agg = spec["agg"]
        if agg == "count":
            to = TO[s["k"]]
            if to.search(new.state or "") and old is not None and old.state not in NOT_A_STATE and not to.search(old.state or ""):
                a["n"] += 1
            return
        x = value_of(spec, new)
        if agg == "mean":
            self._integrate(a, now)
            a["v"] = x
            return
        # A meter: its increase; a fall is a reset (a new meter, a counter rolled over) — counted from zero.
        if x is None:
            return
        if a["v"] is not None:
            d = x - a["v"]
            a["inc"] += d if d >= 0 else x
        a["v"] = x

    def _value(self, spec: dict[str, Any], a: dict[str, Any]) -> float | None:
        if a["partial"]:
            return None
        if spec["agg"] == "mean":
            return a["sw"] / a["w"] if a["w"] >= COVER * PERIOD else None
        if spec["agg"] == "count":
            return a["n"] * SECONDS_PER_HOUR / PERIOD
        return a["inc"] * SECONDS_PER_HOUR / PERIOD if a["v0"] else None

    def _roll(self, eid: str, s: dict[str, Any], spec: dict[str, Any], a: dict[str, Any], now: float) -> None:
        """Close the hours that ended: each one's value judged against its bucket, then learned (when not off)."""
        p_now = int(now // PERIOD)
        if p_now - a["p"] > MAX_CATCHUP:
            self._new_acc(eid, spec, now)
            return
        while a["p"] < p_now:
            end = (a["p"] + 1) * PERIOD
            if spec["agg"] == "mean":
                self._integrate(a, end)
            x = self._value(spec, a)
            if x is not None:
                self._sample(eid, s, spec, x, a["p"] * PERIOD)
            a.update(p=a["p"] + 1, partial=False, t=end, sw=0.0, w=0.0, inc=0.0, n=0, v0=a["v"] is not None)

    # --- the norm ---------------------------------------------------------------------------------------------------------

    def bucket(self, spec: dict[str, Any], ts: float) -> str:
        t = _local(ts)
        season = (self.ctx.season_now() or MID) if spec.get("season") else ANY
        return f"{season}|{DAYS[t.weekday()]}|{t.hour // HOUR_SPAN}"

    @staticmethod
    def norm_at(s: dict[str, Any], key: str) -> tuple[float, float, int, str] | None:
        """(median, spread, n, level) of the bucket, else of that hour on any day, else of the season — the first with
        OBS_EPS values (the shared gate's count)."""
        season, _dg, hb = key.split("|")
        b = s["b"]
        for level in LEVELS:
            if level == "bucket":
                xs = b.get(key, [])
            elif level == "hour":
                xs = [x for k, v in b.items() if k.startswith(f"{season}|") and k.endswith(f"|{hb}") for x in v]
            else:
                xs = [x for k, v in b.items() if k.startswith(f"{season}|") for x in v]
            if len(xs) >= OBS_EPS:
                m, spread = robust(xs)
                return m, spread, len(xs), level
        return None

    @staticmethod
    def judge_at(s: dict[str, Any], key: str) -> tuple[list[float], float, int] | None:
        """The norm a value is judged against: (its medians — the level's and, below the bucket, the bucket's own —,
        spread, n), or None while learning (only the season has enough, or the bucket's own values are too few)."""
        norm = SeriesNorm.norm_at(s, key)
        if norm is None or norm[3] not in JUDGE:
            return None
        own = s["b"].get(key, [])
        if len(own) < JUDGE[norm[3]]:
            return None
        refs = [norm[0]] + ([statistics.median(own)] if norm[3] != "bucket" and own else [])
        return refs, norm[1], norm[2]

    def _sample(self, eid: str, s: dict[str, Any], spec: dict[str, Any], x: float, start: float) -> None:
        key = self.bucket(spec, start)
        kind = f"norm_{spec['kind']}"
        fkey = f"{kind}|{eid}"
        norm = self.judge_at(s, key) if self.ctx.observed(self.name, eid) else None
        if norm is not None:
            s["norm"], s["n"] = _round(norm[0][0]), norm[2]
        else:
            s["norm"] = None
        # Off only against every reference (the hour's and the bucket's own): z ≥ Z (×N) and |Δ| ≥ the kind's min_delta.
        f = self.ctx.factor(fkey, Z)
        dev_own = norm is not None and all(abs(x - r) / norm[1] >= f and abs(x - r) >= spec["min_delta"] for r in norm[0])
        tn = NORMS[s["tn"]] if s.get("tn") is not None else None
        bound = None
        if tn is not None:
            if "max" in tn and x > tn["max"]:
                bound = tn["max"]
            elif "min" in tn and x < tn["min"]:
                bound = tn["min"]
        s["bad"] = s["bad"] + 1 if dev_own else 0
        s["bb"] = s["bb"] + 1 if bound is not None else 0
        s["last"] = _round(x)
        op = s["open"]
        if op is None and (s["bad"] >= HOLD or (tn is not None and s["bb"] >= tn["hold"])):
            by_own = s["bad"] >= HOLD
            held = s["bad"] if by_own else s["bb"]
            s["open"] = {"since": start - (held - 1) * PERIOD, "was": _round(norm[0][0] if by_own else bound), "now": _round(x), "by": "own" if by_own else "type"}
        elif op is not None:
            calm = (norm is None or any(abs(x - r) / norm[1] < Z_END or abs(x - r) < spec["min_delta"] for r in norm[0])) and bound is None
            if calm:
                s["open"] = None
            else:
                op["now"] = _round(x)
        # Learned when not off its norm (a failure is not the norm) — and when the site said «это норма» (a new level:
        # the norm follows it, as every detector keeps learning after it); while learning — everything.
        if not dev_own or self.ctx.is_normal(fkey):
            ring = s["b"].setdefault(key, [])
            ring.append(_round(x))
            del ring[:-KEEP]
        self.ctx.dirty()

    # --- the engine's calls -----------------------------------------------------------------------------------------------

    def tick(self, now: float) -> None:
        t0 = time.perf_counter()
        if self._found_at is None or now - self._found_at >= DISCOVER:
            self._found_at = now
            self._discover(now)
        for eid, s in self.series.items():
            spec = SPEC[s["k"]]
            if not spec.get("own", 1):
                continue
            a = self.acc.get(eid) or self._new_acc(eid, spec, now)
            self._roll(eid, s, spec, a, now)
        self.took_ms = (time.perf_counter() - t0) * 1000

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for eid, s in self.series.items():
            op = s["open"]
            if op is None or not self.ctx.visible(eid):
                continue
            kind = f"norm_{s['k']}"
            was, cur = op.get("was"), op.get("now")
            out.append({
                "key": f"{kind}|{eid}", "kind": kind, "entityId": eid, **self.ctx.who(eid), "since": _iso(op["since"]),
                "role": s["k"], "value": cur, "limit": was, "hours": _round((now - op["since"]) / SECONDS_PER_HOUR),
                **({"change": _round((cur - was) / abs(was) * 100)} if num(was) and num(cur) is not None else {}),
            })
        return out

    def keys(self) -> list[tuple[str, str]]:
        return [(f"norm_{s['k']}", eid) for eid, s in self.series.items()]

    def excluded(self, kind: str, eid: str) -> str | None:
        if (why := super().excluded(kind, eid)) is not None:
            return why
        s = self.series.get(eid)
        if s is not None and not SPEC[s["k"]].get("own", 1) and s.get("tn") is None:
            return "no_norm"
        return None

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        s = self.series.get(eid)
        spec = SPEC.get(kind.removeprefix("norm_")) or SPECS[-1]
        unit = spec["unit"]
        if s is not None and self.ctx.observed(self.name, eid) and (norm := self.judge_at(s, self.bucket(spec, now))) is not None:
            return {"ready": {"norm": _round(norm[0][0]), "unit": unit, "n": norm[2]}}
        tn = NORMS[s["tn"]] if s is not None and s.get("tn") is not None else None
        if tn is not None:
            return {"ready": {"norm": tn.get("max", tn.get("min")), "unit": unit, "n": 0}}
        key = self.bucket(spec, now)
        have = len([x for k, v in (s or {}).get("b", {}).items() if k.split("|")[0] == key.split("|")[0] and k.endswith(f"|{key.split('|')[2]}") for x in v])
        return self.learning(eid, have, OBS_EPS, now)

    def extra(self, kind: str, eid: str) -> dict[str, Any]:
        s = self.series.get(eid)
        if s is None:
            return {}
        out: dict[str, Any] = {"unit": SPEC[s["k"]]["unit"]}
        if s["last"] is not None:
            out["last"] = s["last"]
        if s.get("tn") is not None:
            out["standard"] = NORMS[s["tn"]]["src"]
        elif not SPEC[s["k"]].get("own", 1):
            out["ask"] = True  # «Нормы нет — спросим позже»
        return out

    def reset(self, eid: str, kind: str) -> None:
        s = self.series.get(eid)
        if s is not None:
            s.update(b={}, bad=0, bb=0, open=None, norm=None, n=0)
            self.acc.pop(eid, None)
            self.ctx.dirty()

    def now_of(self, eid: str) -> float | None:
        """The series' value now (the correlation's before / after)."""
        s = self.series.get(eid)
        return value_of(SPEC[s["k"]], self.ctx.hass.states.get(eid)) if s is not None else None


# --- 2. «X on → Y moves» ------------------------------------------------------------------------------------------------


class Correlation(Detector):
    """A trigger's switching on and the change of its bound series `window_min` later (analytics.correlations[]): the
    expected change learned (median of the clean events); judged once it is at least `min_delta` the expected way —
    HOLD events in a row below max(min_delta, expected / ×N), or the other way, say it; the next good one ends it."""

    name = "correlation"
    domains = None
    KINDS = tuple(f"corr_{c['name']}" for c in CORRS)
    DEV = ("limit", "change")
    UNIT = "raw"

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        # trigger → {c: correlation name, s: series entity, d: [learned signed changes], a: [levels after them], bad, last, open}
        self.links: dict[str, dict[str, Any]] = {}
        self.pending: dict[str, dict[str, float]] = {}
        self._found_at: float | None = None

    def load(self, data: dict[str, Any]) -> None:
        names = {c["name"] for c in CORRS}
        for eid, r in (data.get("links") or {}).items():
            if isinstance(r, dict) and r.get("c") in names and isinstance(r.get("s"), str):
                op = r.get("open")
                self.links[str(eid)] = {"c": r["c"], "s": r["s"], "d": [float(x) for x in r.get("d") or [] if num(x) is not None][-CORR_KEEP:],
                                        "a": [float(x) for x in r.get("a") or [] if num(x) is not None][-CORR_KEEP:],
                                        "bad": int(num(r.get("bad")) or 0), "last": num(r.get("last")), "open": op if isinstance(op, dict) and num(op.get("since")) is not None else None}

    def dump(self) -> dict[str, Any]:
        return {"links": self.links}

    def _series(self) -> SeriesNorm | None:
        return next((d for d in self.ctx.detectors if isinstance(d, SeriesNorm)), None)

    def _bind(self, trig: str, cfg: dict[str, Any], sn: SeriesNorm) -> str | None:
        """The one series this trigger moves: the mapping's pair first, then the same device / room — one candidate only."""
        kind = cfg["series"]
        mine = [e for e, s in sn.series.items() if s["k"] == kind]
        pe = self.ctx.owner.get("no_effect")
        paired = [e for e, p in (getattr(pe, "pairs", {}) or {}).items() if p.get("exec") == trig and e in mine]
        if len(paired) == 1:
            return paired[0]
        if cfg["scope"] == "device":
            entry = self.ctx.entry(trig)
            dev = entry.device_id if entry else None
            cands = [e for e in mine if dev and (x := self.ctx.entry(e)) is not None and x.device_id == dev]
        else:
            area = self.ctx._area_id(trig)
            cands = [e for e in mine if area and self.ctx._area_id(e) == area]
        return cands[0] if len(cands) == 1 else None

    def _discover(self, now: float) -> None:
        sn = self._series()
        if sn is None:
            return
        hass = self.ctx.hass
        facts = _Facts(self.ctx)
        if now - self.ctx.started > GONE_AFTER:
            for eid in [e for e in self.links if hass.states.get(e) is None]:
                del self.links[eid]
                self.ctx.dirty()
        for st in hass.states.async_all():
            eid = st.entity_id
            if not self.ctx.visible(eid):
                continue
            # The first correlation of this trigger that binds (a fan with only a humidity sensor near: «вытяжка»).
            f = facts.of(st)
            cfg, target = next(((c, t) for c in CORRS if _matches(c["trigger"], f) and (t := self._bind(eid, c, sn)) is not None), (None, None))
            cur = self.links.get(eid)
            if target is None:
                if cur is not None and now - self.ctx.started > GONE_AFTER:
                    del self.links[eid]
                    self.ctx.dirty()
                continue
            if cur is None or cur["c"] != cfg["name"] or cur["s"] != target:
                self.links[eid] = {"c": cfg["name"], "s": target, "d": [], "a": [], "bad": 0, "last": None, "open": None}
                self.ctx.mark(self.name, eid, now)
                self.ctx.dirty()

    def on_state(self, new: State, now: float) -> None:  # pragma: no cover
        return

    def on_event(self, old: State | None, new: State, now: float) -> None:
        link = self.links.get(new.entity_id)
        if link is None:
            return
        cfg = next(c for c in CORRS if c["name"] == link["c"])
        on = bool(cfg["to"].search(new.state or ""))
        was = old is not None and old.state not in NOT_A_STATE and bool(cfg["to"].search(old.state or ""))
        if on and not was and old is not None and old.state not in NOT_A_STATE:
            sn = self._series()
            v0 = sn.now_of(link["s"]) if sn else None
            if v0 is not None:
                self.pending[new.entity_id] = {"t": now, "v": v0}
        elif not on:
            self.pending.pop(new.entity_id, None)  # off before the window ended: says nothing

    def tick(self, now: float) -> None:
        if self._found_at is None or now - self._found_at >= DISCOVER:
            self._found_at = now
            self._discover(now)
        sn = self._series()
        for trig in list(self.pending):
            link = self.links.get(trig)
            p = self.pending[trig]
            cfg = next((c for c in CORRS if link and c["name"] == link["c"]), None)
            if link is None or cfg is None:
                del self.pending[trig]
                continue
            if now - p["t"] < cfg["window_min"] * 60:
                continue
            del self.pending[trig]
            v1 = sn.now_of(link["s"]) if sn else None
            if v1 is not None:
                self._event(trig, link, cfg, p["v"], v1, p["t"])

    def expected(self, link: dict[str, Any]) -> float | None:
        return statistics.median(link["d"]) if len(link["d"]) >= OBS_EPS else None

    def level(self, link: dict[str, Any], cfg: dict[str, Any]) -> float | None:
        """Where the trigger brings the series: the learned level after the clean events, else the canon's `level` (an
        extract fan can't take a room's humidity / CO2 below it)."""
        a = link.setdefault("a", [])
        return statistics.median(a) if len(a) >= OBS_EPS else cfg.get("level")

    def _event(self, trig: str, link: dict[str, Any], cfg: dict[str, Any], v0: float, v1: float, t: float) -> None:
        sign = 1 if cfg["expect"] == "rise" else -1
        signed = sign * (v1 - v0)
        exp = self.expected(link) if self.ctx.observed(self.name, trig) else None
        key = f"corr_{cfg['name']}|{trig}"
        judged = exp is not None and exp >= cfg["min_delta"]
        # The mapping's own pair already says «нет эффекта» for this effect: one word, not two.
        pe = self.ctx.owner.get("no_effect")
        if judged and pe is not None and (getattr(pe, "runs", {}).get(link["s"]) or {}).get("alerted"):
            judged = False
        bar = max(cfg["min_delta"], exp / self.ctx.factor(key, FACTOR)) if judged else None
        # Nothing to move: the series was already at (or past) the level the trigger brings it to — a fan on a timer in
        # a dry bathroom, a relay switched on with its load already running. Not counted: neither a failure nor learned.
        lvl = self.level(link, cfg)
        if lvl is not None and sign * (lvl - v0) < max(cfg["min_delta"], bar or 0):
            return
        fail = judged and signed < bar
        link["last"] = _round(signed)
        normal = self.ctx.is_normal(key)
        if fail and normal:  # «это норма»: still learned, the expected change follows what it does now
            link["d"] = [*link["d"], _round(signed)][-CORR_KEEP:]
            link["a"] = [*link["a"], _round(v1)][-CORR_KEEP:]
        if fail:
            link["bad"] += 1
            if link["open"] is None and link["bad"] >= HOLD:
                link["open"] = {"since": t, "was": _round(exp), "now": _round(signed)}
            elif link["open"] is not None:
                link["open"]["now"] = _round(signed)
        else:
            link["bad"] = 0
            link["open"] = None
            link["d"] = [*link["d"], _round(signed)][-CORR_KEEP:]
            link["a"] = [*link["a"], _round(v1)][-CORR_KEEP:]
        self.ctx.dirty()

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for trig, link in self.links.items():
            op = link["open"]
            if op is None or not self.ctx.visible(trig):
                continue
            cfg = next((c for c in CORRS if c["name"] == link["c"]), None)
            if cfg is None:
                continue
            kind = f"corr_{cfg['name']}"
            out.append({"key": f"{kind}|{trig}", "kind": kind, "entityId": trig, **self.ctx.who(trig), "since": _iso(op["since"]),
                        "role": cfg["series"], "exec": self.ctx.who(link["s"])["name"], "limit": op["was"], "change": op["now"], "flatH": _round(cfg["window_min"] / 60)})
        return out

    def inputs(self, kind: str, eid: str) -> list[dict[str, Any]]:
        """What is switched on (the trigger) and the reading that should move after it."""
        link = self.links.get(eid)
        return [{"role": "trigger", "entityId": eid}, *([{"role": "effect", "entityId": link["s"]}] if link else [])]

    def keys(self) -> list[tuple[str, str]]:
        return [(f"corr_{link['c']}", trig) for trig, link in self.links.items()]

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        link = self.links.get(eid)
        cfg = next((c for c in CORRS if f"corr_{c['name']}" == kind), None)
        unit = SPEC[cfg["series"]]["unit"] if cfg and cfg["series"] in SPEC else self.UNIT
        exp = self.expected(link) if link is not None and self.ctx.observed(self.name, eid) else None
        if exp is not None:
            return {"ready": {"norm": _round(exp), "unit": unit, "n": len(link["d"])}}
        return self.learning(eid, len(link["d"]) if link else 0, OBS_EPS, now)

    def extra(self, kind: str, eid: str) -> dict[str, Any]:
        link = self.links.get(eid)
        return {"effect": link["s"]} if link else {}

    def reset(self, eid: str, kind: str) -> None:
        link = self.links.get(eid)
        if link is not None:
            link.update(d=[], a=[], bad=0, last=None, open=None)
            self.pending.pop(eid, None)
            self.ctx.dirty()


# --- 3. a loop's tuning against its peers --------------------------------------------------------------------------------


class LoopTuning(Detector):
    """Heating / cooling / humidity loops (analytics.loops[], first match wins): each episode «wants → reached» gives a
    rate and a time to setpoint, the OVERSHOOT_H after it an overshoot; each day — the starts and the share of time
    active. Judged once observed (the shared gate: OBS_EPS episodes / LOOP_MIN_DAYS days): against the median of its
    peers of the same type (PEERS_MIN of them, the integrator's «×N», default FACTOR), else against the type's bar."""

    name = "loops"
    domains = tuple(READERS)
    KINDS = tuple(dict.fromkeys(LOOP_METRICS.values()))
    DEV = ("limit", "value")

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        # entity → {t: type, ep: [[rate, warmup h per unit of the gap, overshoot]], days: {date: [active s, starts]}, open: {metric: {...}}, log: [...]}
        self.loops: dict[str, dict[str, Any]] = {}
        self.run: dict[str, dict[str, Any]] = {}  # entity → the episode now / the active state (not kept over a restart)
        self.supply: dict[str, str] = {}  # loop → its supply temperature sensor (the integrator's word, «Пары»)
        self.facts = _Facts(ctx)
        self._found_at: float | None = None

    # --- persisted ----------------------------------------------------------------------------------------------------

    def load(self, data: dict[str, Any]) -> None:
        for eid, r in (data.get("loops") or {}).items():
            if not isinstance(r, dict) or r.get("t") not in LOOP_TYPES:
                continue
            eps = [[float(v) for v in e] for e in r.get("ep") or [] if isinstance(e, list) and len(e) == 3 and all(num(v) is not None for v in e)]
            days = {str(d): [float(v[0]), int(v[1])] for d, v in (r.get("days") or {}).items() if isinstance(v, list) and len(v) == 2 and num(v[0]) is not None and num(v[1]) is not None}
            opened = {m: o for m, o in (r.get("open") or {}).items() if m in LOOP_METRICS and isinstance(o, dict) and num(o.get("since")) is not None}
            self.loops[str(eid)] = {"t": r["t"], "ep": eps[-LOOP_KEEP:], "days": dict(sorted(days.items())[-LOOP_DAYS:]), "open": opened,
                                    "log": [x for x in r.get("log") or [] if isinstance(x, dict)][-LOOP_KEEP:]}
        self.supply = {str(k): str(v) for k, v in (data.get("supply") or {}).items() if isinstance(v, str)}

    def dump(self) -> dict[str, Any]:
        return {"loops": self.loops, "supply": self.supply}

    # --- which loops --------------------------------------------------------------------------------------------------------

    def type_of(self, st: State) -> str | None:
        pe = self.ctx.owner.get("no_effect")
        facts = self.facts.of(
            st,
            air_handler=lambda: self.ctx.air_handler(st.entity_id),
            pair=(pe.climate_kind(st.entity_id) if pe is not None and hasattr(pe, "climate_kind") else None) or "",
        )
        for lp in LOOPS:
            m = dict(lp["match"])
            want_modes = m.pop("modes", None)
            if want_modes is not None and not any(want_modes.search(str(x)) for x in st.attributes.get("hvac_modes") or []):
                continue
            if _matches(m, facts):
                return lp["type"]
        return None

    def _discover(self, now: float) -> None:
        hass = self.ctx.hass
        for domain in READERS:
            for st in hass.states.async_all(domain):
                eid = st.entity_id
                if not self.ctx.visible(eid):
                    continue
                t = self.type_of(st)
                cur = self.loops.get(eid)
                if t is None:
                    continue
                if cur is None:
                    self.loops[eid] = {"t": t, "ep": [], "days": {}, "open": {}, "log": []}
                    self.ctx.mark(self.name, eid, now)
                    self.ctx.dirty()
                elif cur["t"] != t:  # the integrator said «пол» since: the type's norms differ — learned anew
                    self.loops[eid] = {"t": t, "ep": [], "days": {}, "open": {}, "log": []}
                    self.ctx.dirty()
        if now - self.ctx.started > GONE_AFTER:
            for eid in [e for e in self.loops if hass.states.get(e) is None]:
                del self.loops[eid]
                self.ctx.dirty()

    # --- reading a loop -----------------------------------------------------------------------------------------------------

    @staticmethod
    def read(st: State) -> dict[str, Any] | None:
        """{cur, tgt, active} by the domain's reader; None — off / unavailable."""
        r = READERS.get(st.domain)
        if r is None or (r["off"] is not None and r["off"].search(st.state or "")) or st.state in NOT_A_STATE:
            return None
        cur = num(st.attributes.get(r["current"])) if r["current"] else None
        tgt = num(st.attributes.get(r["target"])) if r["target"] else None
        action = st.attributes.get(r["action"]) if r["action"] else None
        if action is not None and r["active"] is not None:
            active: bool | None = bool(r["active"].search(str(action)))
        elif not r["action"] and r["active"] is not None:
            active = bool(r["active"].search(st.state or ""))
        else:
            active = None  # not said: the episode's gap stands for it
        return {"cur": cur, "tgt": tgt, "active": active}

    def on_state(self, new: State, now: float) -> None:  # pragma: no cover
        return

    def on_event(self, old: State | None, new: State, now: float) -> None:
        lp = self.loops.get(new.entity_id)
        if lp is None:
            return
        self._step(new.entity_id, lp, new, now)

    def _day_rec(self, lp: dict[str, Any], now: float) -> list[float]:
        d = _day(now)
        if d not in lp["days"]:
            lp["days"][d] = [0.0, 0]
            for old in sorted(lp["days"])[:-LOOP_DAYS]:
                del lp["days"][old]
        return lp["days"][d]

    def _step(self, eid: str, lp: dict[str, Any], st: State, now: float) -> None:
        cfg = LOOP_TYPES[lp["t"]]
        rd = self.read(st)
        run = self.run.setdefault(eid, {"ep": None, "active": None, "at": now})
        # Active time and starts (a day split at midnight is close enough for a daily share).
        if run["active"] is True:
            self._day_rec(lp, now)[0] += max(0.0, now - run["at"])
        act = rd["active"] if rd is not None else None
        if act is True and run["active"] is False:
            self._day_rec(lp, now)[1] += 1
            self.ctx.dirty()
        run["active"], run["at"] = act, now
        self._episode(eid, lp, cfg, run, rd, now)

    def _log(self, lp: dict[str, Any], ep: dict[str, Any], now: float, result: str, rate: float | None = None) -> None:
        line = {"start": _iso(ep["t0"]), "dir": "heat" if ep["sign"] > 0 else "cool", "gap": _round(ep["gap"]), "hours": _round((now - ep["t0"]) / SECONDS_PER_HOUR), "result": result}
        if rate is not None:
            line["rate"] = _round(rate)
        lp["log"] = [*lp["log"], line][-LOOP_KEEP:]
        self.ctx.dirty()

    def _episode(self, eid: str, lp: dict[str, Any], cfg: dict[str, Any], run: dict[str, Any], rd: dict[str, Any] | None, now: float) -> None:
        if not ({"rate", "warmup", "overshoot"} & set(cfg["metrics"])):
            return
        ep = run["ep"]
        if rd is None or rd["cur"] is None or rd["tgt"] is None:
            if ep is not None:
                self._log(lp, ep, now, "gone")
                run["ep"] = None
            return
        cur, tgt = rd["cur"], rd["tgt"]
        if ep is None:
            gap = tgt - cur
            if abs(gap) >= max(cfg["gap"], 1e-9) and rd["active"] is not False:
                run["ep"] = {"t0": now, "c0": cur, "tgt": tgt, "sign": 1 if gap > 0 else -1, "gap": abs(gap), "reach": None, "peak": 0.0}
            return
        if abs(tgt - ep["tgt"]) > cfg["reach"]:
            self._log(lp, ep, now, "target")
            run["ep"] = None
            return
        left = ep["sign"] * (tgt - cur)
        if ep["reach"] is None:
            if left <= cfg["reach"]:
                ep["reach"] = now
                # The time to setpoint per unit of the gap: a 6 °C gap takes longer than a 1 °C one, not «worse».
                hours = (now - ep["t0"]) / SECONDS_PER_HOUR
                ep["warm"] = hours / max(ep["gap"], 1e-6)
                ep["rate"] = abs(cur - ep["c0"]) / max(hours, 1e-6)
            elif now - ep["t0"] > LOOP_MAX_S:
                self._log(lp, ep, now, "long")
                run["ep"] = None
            return
        ep["peak"] = max(ep["peak"], -left)

    def _close_episodes(self, now: float) -> None:
        for eid, run in self.run.items():
            ep = run["ep"]
            lp = self.loops.get(eid)
            if ep is None or lp is None or ep["reach"] is None or now - ep["reach"] < OVERSHOOT_S:
                continue
            lp["ep"] = [*lp["ep"], [_round(ep["rate"]), _round(ep["warm"]), _round(ep["peak"])]][-LOOP_KEEP:]
            self._log(lp, ep, now, "used", ep["rate"])
            run["ep"] = None
            self._judge(eid, lp, now)

    # --- judging ------------------------------------------------------------------------------------------------------------

    def metric(self, lp: dict[str, Any], metric: str, now: float) -> tuple[float, int] | None:
        """(own median, n) — None while learning."""
        if metric in ("rate", "warmup", "overshoot"):
            i = ("rate", "warmup", "overshoot").index(metric)
            xs = [e[i] for e in lp["ep"]]
            return (statistics.median(xs), len(xs)) if len(xs) >= OBS_EPS else None
        today = _day(now)
        days = [v for d, v in lp["days"].items() if d != today]
        if len(days) < LOOP_MIN_DAYS:
            return None
        xs = [v[1] for v in days] if metric == "cycles" else [v[0] / 86400 for v in days]
        return statistics.median(xs), len(xs)

    def bar(self, eid: str, lp: dict[str, Any], metric: str, now: float) -> tuple[float, str] | None:
        """The bar it is judged against and where it is from: "peers" (median of the same type ×/÷ N) or "type"."""
        cfg = LOOP_TYPES[lp["t"]]
        key = f"{LOOP_METRICS[metric]}|{eid}"
        f = self.ctx.factor(key, FACTOR)
        peers = [m[0] for e, o in self.loops.items() if e != eid and o["t"] == lp["t"] and self.ctx.observed(self.name, e) and (m := self.metric(o, metric, now)) is not None]
        if len(peers) >= PEERS_MIN and metric != "duty":
            med = statistics.median(peers)
            return (med / f if metric == "rate" else med * f), "peers"
        limit = {"rate": "rate_min", "warmup": "warmup_max_h_per", "overshoot": "overshoot_max", "cycles": "cycles_max", "duty": "duty_max"}[metric]
        v = cfg.get(limit, 0)
        return (v, "type") if v else None

    def _judge(self, eid: str, lp: dict[str, Any], now: float) -> None:
        cfg = LOOP_TYPES[lp["t"]]
        if not self.ctx.observed(self.name, eid):
            return
        cyc = self.ctx.owner.get("short_cycling")
        for metric in cfg["metrics"]:
            own = self.metric(lp, metric, now)
            bar = self.bar(eid, lp, metric, now) if own is not None else None
            off = False
            if own is not None and bar is not None:
                off = own[0] < bar[0] if metric == "rate" else own[0] > bar[0]
            # «Частые пуски» already said by ShortCycling for it: one word, not two.
            if metric == "cycles" and off and cyc is not None and (getattr(cyc, "recs", {}).get(eid) or {}).get("open") is not None:
                off = False
            # Per unit of the gap the time to setpoint is the rate's other side: a slow loop is said once (its rate).
            if metric == "warmup" and off and "rate" in lp["open"]:
                off = False
            op = lp["open"].get(metric)
            if off and op is None:
                lp["open"][metric] = {"since": now, "was": _round(bar[0]), "now": _round(own[0]), "by": bar[1]}
                self.ctx.dirty()
            elif off:
                op.update(was=_round(bar[0]), now=_round(own[0]))
            elif op is not None:
                del lp["open"][metric]
                self.ctx.dirty()

    # --- the engine's calls -----------------------------------------------------------------------------------------------

    def tick(self, now: float) -> None:
        if self._found_at is None or now - self._found_at >= DISCOVER:
            self._found_at = now
            self._discover(now)
            for eid, lp in self.loops.items():
                self._judge(eid, lp, now)
        # Judged hourly (above, with the discovery) and on each finished episode; here — the active time and the episodes.
        for eid, lp in self.loops.items():
            st = self.ctx.hass.states.get(eid)
            if st is None:
                continue
            run = self.run.setdefault(eid, {"ep": None, "active": None, "at": now})
            if run["active"] is True:
                self._day_rec(lp, now)[0] += max(0.0, now - run["at"])
                run["at"] = now
            self._episode(eid, lp, LOOP_TYPES[lp["t"]], run, self.read(st), now)
        self._close_episodes(now)

    def _fields(self, metric: str, op: dict[str, Any], eid: str) -> dict[str, Any]:
        was, cur = op["was"], op["now"]
        if metric == "rate":
            out = {"wasRate": was, "nowRate": cur, "limit": was, "value": cur}
            if (sensor := self.supply.get(eid)) and (st := self.ctx.hass.states.get(sensor)) is not None and num(st.state) is not None:
                out["supply"] = num(st.state)
            return out
        if metric == "cycles":
            return {"normPerDay": was, "perDay": cur, "limit": was, "value": cur}
        if metric == "duty":
            return {"limit": _round(was * 100), "value": _round(cur * 100)}
        return {"limit": was, "value": cur}

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for eid, lp in self.loops.items():
            if not lp["open"] or not self.ctx.visible(eid):
                continue
            for metric, op in lp["open"].items():
                kind = LOOP_METRICS[metric]
                out.append({"key": f"{kind}|{eid}", "kind": kind, "entityId": eid, **self.ctx.who(eid), "since": _iso(op["since"]), "role": lp["t"], **self._fields(metric, op, eid)})
        return out

    def inputs(self, kind: str, eid: str) -> list[dict[str, Any]]:
        """The loop (its temperature, setpoint, starts) and its supply sensor when the integrator named one; its peers of
        the same type are the norm."""
        return [{"role": "self", "entityId": eid}, *([{"role": "supply", "entityId": self.supply[eid]}] if eid in self.supply else []), {"role": "peers"}]

    def keys(self) -> list[tuple[str, str]]:
        return [(LOOP_METRICS[m], eid) for eid, lp in self.loops.items() for m in LOOP_TYPES[lp["t"]]["metrics"]]

    def _metric_of(self, kind: str) -> str:
        return next(m for m, k in LOOP_METRICS.items() if k == kind)

    def unit(self, eid: str, metric: str) -> str:
        base = LOOP_UNITS.get(eid.split(".")[0], "raw")
        return {"rate": f"{base}_per_h", "warmup": f"h_per_{base}", "overshoot": base, "cycles": "per_day", "duty": "pct"}[metric]

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        lp = self.loops.get(eid)
        metric = self._metric_of(kind)
        if lp is None:
            return self.learning(eid, 0, OBS_EPS, now)
        own = self.metric(lp, metric, now)
        if own is not None and self.ctx.observed(self.name, eid):
            v = own[0] * 100 if metric == "duty" else own[0]
            return {"ready": {"norm": _round(v), "unit": self.unit(eid, metric), "n": own[1]}}
        if metric in ("cycles", "duty"):
            return self.learning(eid, len([d for d in lp["days"] if d != _day(now)]), LOOP_MIN_DAYS, now)
        return self.learning(eid, len(lp["ep"]), OBS_EPS, now)

    def extra(self, kind: str, eid: str) -> dict[str, Any]:
        lp = self.loops.get(eid)
        if lp is None:
            return {}
        cfg = LOOP_TYPES[lp["t"]]
        out: dict[str, Any] = {"role": lp["t"], "tip": f"{lp['t']}_{self._metric_of(kind)}"}
        if cfg.get("supply"):
            if eid in self.supply:
                out["supply"] = self.supply[eid]
            else:
                out["ask"] = "supply"  # «где датчик подачи» — the integrator's word in «Пары»
        op = lp["open"].get(self._metric_of(kind))
        if op is not None:
            out["by"] = op.get("by")
        return out

    def episodes(self, eid: str) -> list[dict[str, Any]]:
        # A thermostat's episodes are ClimateEffect's already (the same «wants → reached»): listed once.
        return [] if eid.startswith("climate.") else list((self.loops.get(eid) or {}).get("log", []))

    def reset(self, eid: str, kind: str) -> None:
        lp = self.loops.get(eid)
        if lp is None:
            return
        metric = self._metric_of(kind)
        if metric in ("rate", "warmup", "overshoot"):
            lp["ep"], lp["log"] = [], []
            self.run.pop(eid, None)
        else:
            lp["days"] = {}
        lp["open"].pop(metric, None)
        self.ctx.dirty()

    def set_supply(self, loop: str, sensor: str) -> None:
        """«Кто греет пол, где датчик подачи»: the integrator's word ("" — forgotten)."""
        if sensor:
            self.supply[loop] = sensor
        else:
            self.supply.pop(loop, None)
        self.ctx.dirty()

    def supply_view(self) -> list[dict[str, Any]]:
        """Loops that want a supply sensor, with the one said (the «Пары» slot)."""
        out = []
        for eid, lp in self.loops.items():
            if LOOP_TYPES[lp["t"]].get("supply") and self.ctx.hass.states.get(eid) is not None:
                s = self.supply.get(eid)
                out.append({"loop": eid, "type": lp["t"], **self.ctx.who(eid), "sensor": s, "sensorName": self.ctx.who(s)["name"] if s else None})
        return out


ANALYTICS = (SeriesNorm, Correlation, LoopTuning)
