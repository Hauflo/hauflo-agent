"""«Исполнитель включён, эффекта нет» (п.68, owner 06.11): the executor of a pair has been ON for a whole window and the
quantity it is there to change did not move. One class (CLAUDE.md §10) — a PAIR «executor → measured effect» with a
direction, a window and a threshold, the same detector for:

  * `floor` — a floor loop's valve open, its floor temperature not rising (the case that started it: Горького 3, loop 1
    open for days at 23 °C against a 27 °C setpoint while loops 2–6 sat at theirs);
  * `air` — a thermostat heating, its room air not rising (only until ClimateEffect has learned that zone's own rate:
    the zone's own norm beats a standard);
  * `co2` / `humidity` — a ventilation fan on, the CO2 / the humidity of the air it serves not dropping.
Another pair (a pump on and the supply cold, a light on and the power zero, a cover moving and the position unchanged) is
one more block in tuning.json «noeffect_*» — the machine is the same.

Pairs are found from the mapping, never typed: by AREA, the entity's class (temperature / CO2 / humidity), its name's
words and the TRAILING NUMBER of the names («Клапан 2.3» ↔ «Температура пола 3»). Counts or numbers that don't match
(valves 8 and 10 against floors 1 and 2) are NOT guessed: the pair stays open for the integrator (`set_pair`).

The standard (tuning.json, with its source beside it): a window long enough for the medium to answer (EN 1264 screed lag
1–3 h; EN 15500-1 air 1–2 h; CO2 and humidity answer within the hour) with the executor active ≥ `min_share` of it,
a change below `min_change`, and ROOM LEFT for the effect (setpoint − value ≥ `margin`, or ≥ `peer_gap` under the
neighbours' median, or a CO2 / humidity above `needs`) — a loop at its setpoint is not faulty. `hold_windows` windows in
a row say it; the end needs `clear_change` in `clear_h` (a wider bar than the start: no flapping) or `clear_h` of calm.
No false alarm: unavailable / gateway restart / a setpoint change / a window open / the other season restart the window;
a daily valve exercise (10 min of the 6 h) stays inside `min_share`. Said once per episode; the same pair is not said
again within `repeat_h`. Diagnosis only: nothing is switched."""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from .derived import NOT_A_STATE, SECONDS_PER_HOUR, num
from .insight import ACTIONS, GONE_AFTER, MODES, Detector, _iso, _round
from .notify import labelled
from .tuning import TUNING

if TYPE_CHECKING:
    from .insight import Insights

_N = TUNING["noeffect"]
MIN_SHARE = _N["min_share"]
HOLD = _N["hold_windows"]
REPEAT_S = _N["repeat_h"] * SECONDS_PER_HOUR
SAMPLE_S = _N["sample_s"]
PAIRS_EVERY = _N["pairs_every_s"]
ACTIVE = set(_N["active_states"])
EXCLUDE = re.compile(_N["exclude_words"], re.I)
KINDS = {k: TUNING[f"noeffect_{k}"] for k in _N["kinds"]}
KIND = "no_effect"
# What a thermostat may regulate («воздух / пол» on «Проверка», backend effectKinds.json types.climate), of the kinds on.
EFFECT_KINDS = {"air", "floor"} & set(KINDS)
_TAIL = re.compile(r"(\d+)(?!.*\d)")


def _words(pattern: str) -> re.Pattern[str] | None:
    return re.compile(pattern, re.I) if pattern else None


def _text(st: State) -> str:
    return f"{st.attributes.get('friendly_name') or ''} {st.entity_id}"


def integration(hass: HomeAssistant, eid: str) -> dict[str, str]:
    """Whose entity it is (399-18): the integration's domain and the name its entry has in HA («Daikin», «Tado»)."""
    entry = er.async_get(hass).async_get(eid)
    if entry is None:
        return {"platform": "", "integration": ""}
    ce = hass.config_entries.async_get_entry(entry.config_entry_id) if entry.config_entry_id else None
    return {"platform": entry.platform, "integration": (ce.title if ce else "") or entry.platform}


def _tail(st: State) -> int | None:
    """The last number of the friendly name («Клапан 2.3» → 3, «Температура пола 3» → 3); none — not numbered."""
    m = _TAIL.search(str(st.attributes.get("friendly_name") or ""))
    return int(m.group(1)) if m else None


class PairEffect(Detector):
    name = "noeffect"
    domains = None  # every domain: on_state only notes a change of a paired entity, the tick decides
    KINDS = (KIND,)
    DEV = ("limit", "change")

    def __init__(self, ctx: Insights) -> None:
        super().__init__(ctx)
        self.manual: dict[str, dict[str, str]] = {}  # effect → {"kind", "exec"} ("exec" "" — not a pair, the integrator said)
        # climate → "air" / "floor": what a thermostat regulates, the integrator's word on «Проверка» (backend effectKinds.ts,
        # sent by the deploy). Over the words; the pairs are derived from it (a floor one is its room's loops' target).
        self.kinds: dict[str, str] = {}
        self.saved: dict[str, dict[str, Any]] = {}  # effect → what an open finding carries over a restart
        self.pairs: dict[str, dict[str, Any]] = {}  # effect → {"kind", "exec", "target"}
        self.unpaired: list[dict[str, str]] = []  # effects with executors the mapping could not tell apart: for the integrator
        self.why: dict[str, str] = {}  # effect → how the mapping paired it: "single" (one and one in the room) / "number" (the trailing number) / "house" (one executor in the house) / "own" (the thermostat itself)
        self.runs: dict[str, dict[str, Any]] = {}
        self._paired_at = -PAIRS_EVERY
        self._involved: set[str] = set()

    # --- persisted ----------------------------------------------------------------------------------------------------

    def load(self, data: dict[str, Any]) -> None:
        for eff, m in (data.get("manual") or {}).items():
            if isinstance(m, dict) and m.get("kind") in KINDS and isinstance(m.get("exec"), str):
                self.manual[str(eff)] = {"kind": m["kind"], "exec": m["exec"]}
        for eff, r in (data.get("open") or {}).items():
            if isinstance(r, dict) and r.get("kind") in KINDS and num(r.get("since")) is not None:
                # Carried over a restart: stays open (a restart is no «снова в норме»); its window starts afresh.
                self.saved[str(eff)] = {"kind": r["kind"], "exec": str(r.get("exec") or ""), "since": float(r["since"]), "said_at": num(r.get("said_at"))}
        for eff, said in (data.get("said") or {}).items():
            if num(said) is not None:
                self.runs.setdefault(str(eff), self._new())["said_at"] = float(said)
        self.kinds = {str(e): k for e, k in (data.get("kinds") or {}).items() if k in EFFECT_KINDS}

    def dump(self) -> dict[str, Any]:
        opened = {e: {"kind": self.pairs[e]["kind"], "exec": self.pairs[e]["exec"], "since": r["since"], "said_at": r["said_at"]} for e, r in self.runs.items() if r["alerted"] and e in self.pairs}
        return {"manual": self.manual, "open": {**self.saved, **opened}, "said": {e: r["said_at"] for e, r in self.runs.items() if r["said_at"] is not None}, "kinds": self.kinds}

    @staticmethod
    def _new() -> dict[str, Any]:
        return {"samples": [], "bad_since": None, "calm_since": None, "alerted": False, "since": None, "said_at": None, "last": None}

    # --- the pairs (the mapping's, then the integrator's) -------------------------------------------------------------

    def set_pair(self, effect: str, kind: str, executor: str) -> None:
        """The integrator's word: this executor is this effect's (`executor` "" — this effect has no executor to judge)."""
        if kind in KINDS:
            self.manual[effect] = {"kind": kind, "exec": executor}
            self._paired_at = -PAIRS_EVERY
            self.ctx.dirty()

    def set_kinds(self, kinds: dict[str, str]) -> None:
        """The deploy's map of thermostats' «воздух / пол» (the whole of it: one not in it is read by its words again)."""
        kinds = {e: k for e, k in kinds.items() if k in EFFECT_KINDS}
        if kinds != self.kinds:
            self.kinds = kinds
            self._paired_at = -PAIRS_EVERY
            self.ctx.dirty()

    def _floor(self, st: State, floor_re: re.Pattern[str] | None) -> bool:
        """Is this thermostat a floor's: the integrator's word, else its words."""
        k = self.kinds.get(st.entity_id)
        return k == "floor" if k else bool(floor_re and floor_re.search(_text(st)))

    def climate_kind(self, eid: str) -> str | None:
        """«floor» / «air» of a thermostat: the integrator's word, a floor pair's target, else its words (analytics.py loops)."""
        if eid in self.kinds:
            return self.kinds[eid]
        if any(p.get("kind") == "floor" and p.get("target") == eid for p in self.pairs.values()):
            return "floor"
        st = self.ctx.hass.states.get(eid)
        if st is None or st.domain != "climate":
            return None
        floor_re = _words(KINDS["floor"]["target_words"]) if "floor" in KINDS else None
        return "floor" if self._floor(st, floor_re) else "air"

    def clear_pair(self, effect: str) -> None:
        if self.manual.pop(effect, None) is not None:
            self._paired_at = -PAIRS_EVERY
            self.ctx.dirty()

    def pairs_view(self) -> dict[str, Any]:
        """What the integrator sees and may refine: the pairs found, the effects left without an unambiguous executor."""
        who = self.ctx.who
        here = lambda eid: self.ctx.hass.states.get(eid) is not None  # noqa: E731
        pairs = []
        # An entity HA no longer has (renamed / removed by a new deploy — integrator review 07.11 С-7) is not shown as a
        # pair «без комнаты» until the next refresh: only what HA has now.
        for e, p in self.pairs.items():
            if not here(e) or not here(p["exec"]):
                continue
            m = e in self.manual
            st = self.ctx.hass.states.get(e)
            pairs.append({"effect": e, "kind": p["kind"], "exec": p["exec"], "manual": m, **{"name": who(e)["name"], "area": who(e)["area"], "execName": who(p["exec"])["name"], "why": "manual" if m else self.why.get(e, "single"), "number": (_tail(st) if st else None)},
                          **integration(self.ctx.hass, e)})
        unpaired = [{**u, "name": who(u["effect"])["name"], "area": who(u["effect"])["area"], **integration(self.ctx.hass, u["effect"])} for u in self.unpaired if here(u["effect"])]
        cands = {k: [{"id": s.entity_id, **who(s.entity_id), **integration(self.ctx.hass, s.entity_id)} for s in self._executors(k)] for k in KINDS}
        ignored = [{"effect": e, "kind": m["kind"], "name": who(e)["name"], "area": who(e)["area"]} for e, m in self.manual.items() if m["exec"] == ""]
        return {"pairs": pairs, "unpaired": unpaired, "ignored": ignored, "candidates": cands, "offered": self._offered()}

    def _offered(self) -> list[dict[str, Any]]:
        """Another integration's thermostats (399-18: a Daikin / Tado climate.*) not paired yet: offered as their own pair
        («Следить» → set_pair(effect = exec = it)) — one not monitored is never judged before the integrator says so; a
        monitored one in no room (no air to judge by default) is paired the same way. A floor one by its words."""
        who, taken = self.ctx.who, {*self.pairs, *self.manual}
        floor_re = _words(KINDS["floor"]["target_words"]) if "floor" in KINDS else None
        out = []
        for s in self.ctx.hass.states.async_all("climate"):
            if s.entity_id in taken or not self.ctx.visible(s.entity_id) or self.ctx.owns(s.entity_id):
                continue
            kind = "floor" if self._floor(s, floor_re) else "air"
            if kind in KINDS:
                out.append({"effect": s.entity_id, "kind": kind, "name": who(s.entity_id)["name"], "area": who(s.entity_id)["area"], **integration(self.ctx.hass, s.entity_id)})
        return sorted(out, key=lambda o: (o["area"], o["name"]))

    def _executors(self, kind: str) -> list[State]:
        """Every entity the integrator may name as this kind's executor (the same filter the mapping uses), by name."""
        cfg = KINDS[kind]
        if kind == "air":
            found = self._states(["climate"])
        else:
            exe_re = _words(cfg["exec_words"])
            found = [s for s in self._states(cfg["exec_domains"]) if (exe_re is None or exe_re.search(_text(s))) and not EXCLUDE.search(_text(s)) and s.attributes.get("device_class") != "problem"]
        return sorted(found, key=lambda s: _text(s))

    def _states(self, domains: list[str]) -> list[State]:
        """The entities a pair is made of: shown in HA, Hauflo's own or marked for monitoring by the integrator (the same
        rule as the stuck-sensor detector) — never another integration's own (w6a2 D-B10: a demo «HeatPump» paired with
        itself, «без комнаты»)."""
        return [s for d in domains for s in self.ctx.hass.states.async_all(d) if self.ctx.visible(s.entity_id) and self._monitored(s.entity_id)]

    def _monitored(self, eid: str) -> bool:
        """Hauflo's own, marked «Hauflo мониторинг», or watched by an alarm rule (its entity or another of its device —
        a Daikin's error sensor in a rule makes its climate a pair too, 399-18)."""
        if self.ctx.owns(eid) or labelled(self.ctx.hass, eid):
            return True
        watched = self.ctx.watched()
        if not watched:
            return False
        if eid in watched:
            return True
        reg = er.async_get(self.ctx.hass)
        entry = reg.async_get(eid)
        return bool(entry and entry.device_id and any((w := reg.async_get(x)) is not None and w.device_id == entry.device_id for x in watched))

    def _find(self, kind: str) -> tuple[dict[str, str | None], list[dict[str, str]]]:
        """effect → executor (None: house-wide ambiguous / none) for a kind, from area + class + words + trailing numbers."""
        cfg = KINDS[kind]
        eff_re, exe_re, tgt_re = _words(cfg["effect_words"]), _words(cfg["exec_words"]), _words(cfg["target_words"])
        if kind == "air":
            # A thermostat is its room's: one in no room has no air to judge (w6a2 D-B10).
            own = [s for s in self._states(["climate"]) if self.ctx._area_id(s.entity_id) is not None]
            self.why.update({s.entity_id: "own" for s in own})
            return {s.entity_id: s.entity_id for s in own}, []
        effects = [s for s in self._states([cfg["effect_domain"]]) if (not cfg["effect_class"] or s.attributes.get("device_class") == cfg["effect_class"]) and (eff_re is None or eff_re.search(_text(s))) and not EXCLUDE.search(_text(s))]
        eff_ids = {s.entity_id for s in effects}
        execs = [s for s in self._states(cfg["exec_domains"]) if s.entity_id not in eff_ids and (exe_re is None or exe_re.search(_text(s))) and not EXCLUDE.search(_text(s)) and s.attributes.get("device_class") != "problem"]
        by_area: dict[str | None, dict[str, list[State]]] = defaultdict(lambda: {"e": [], "x": []})
        for s in effects:
            by_area[self.ctx._area_id(s.entity_id)]["e"].append(s)
        for s in execs:
            by_area[self.ctx._area_id(s.entity_id)]["x"].append(s)
        found: dict[str, str | None] = {}
        left: list[dict[str, str]] = []
        for area, grp in by_area.items():
            e, x = grp["e"], grp["x"]
            if not e or area is None:
                continue  # an effect in no room is paired by hand only: the house's one executor is not its by default
            tails_e, tails_x = [_tail(s) for s in e], [_tail(s) for s in x]
            if area is not None and len(e) == 1 and len(x) == 1:
                found[e[0].entity_id] = x[0].entity_id
                self.why[e[0].entity_id] = "single"
            elif area is not None and x and None not in tails_e + tails_x and len(set(tails_e)) == len(e) and set(tails_e) == set(tails_x) and len(set(tails_x)) == len(x):
                by_n = {_tail(s): s.entity_id for s in x}
                for s in e:
                    found[s.entity_id] = by_n[_tail(s)]
                    self.why[s.entity_id] = "number"
            elif cfg["house_wide"] and len(execs) == 1:
                for s in e:
                    found[s.entity_id] = execs[0].entity_id
                    self.why[s.entity_id] = "house"
            elif x:
                left.extend({"effect": s.entity_id, "kind": kind} for s in e)  # executors there, the mapping can't tell which is whose
        if kind == "floor":
            self._targets = {}
            for s in effects:
                area = self.ctx._area_id(s.entity_id)
                cl = [c for c in self._states(["climate"]) if area and self._floor(c, tgt_re) and self.ctx._area_id(c.entity_id) == area]
                if len(cl) == 1:
                    self._targets[s.entity_id] = cl[0].entity_id
        return found, left

    def _refresh(self, now: float) -> None:
        if now - self._paired_at < PAIRS_EVERY:
            return
        self._paired_at = now
        pairs: dict[str, dict[str, Any]] = {}
        self.unpaired = []
        self.why = {}
        self._targets: dict[str, str] = {}
        for kind in KINDS:
            found, left = self._find(kind)
            targets = dict(self._targets) if kind == "floor" else {}
            self.unpaired += [u for u in left if u["effect"] not in self.manual]
            for eff, exe in found.items():
                if exe:
                    pairs[eff] = {"kind": kind, "exec": exe, "target": exe if kind == "air" else targets.get(eff)}
        floor_thermostats = {p["target"] for p in pairs.values() if p["kind"] == "floor" and p["target"]}
        floor_re = _words(KINDS["floor"]["target_words"]) if "floor" in KINDS else None
        for eff in [e for e, p in pairs.items() if p["kind"] == "air"]:
            if eff in floor_thermostats:
                del pairs[eff]  # the floor's thermostat is the loops' setpoint (judged per loop), not a room's air
            elif (st := self.ctx.hass.states.get(eff)) is not None and self._floor(st, floor_re):
                # A floor thermostat with no loops of its own measures the floor, not the air: judged as a floor (its
                # slower bar), shown under «Пол» (integrator review 07.11 С-7: «Тёплый пол» under «Воздух»).
                pairs[eff]["kind"] = "floor"
        for eff, m in self.manual.items():
            if m["exec"] == "":
                pairs.pop(eff, None)
            else:
                pairs[eff] = {"kind": m["kind"], "exec": m["exec"], "target": pairs.get(eff, {}).get("target")}
        for eff in [e for e in self.runs if e not in pairs and not self.runs[e]["alerted"]]:
            if self.runs[eff]["said_at"] is None:
                del self.runs[eff]
        self.pairs = pairs
        self._involved = {i for e, p in pairs.items() for i in (e, p["exec"], p["target"]) if i}
        for eff, p in pairs.items():
            run = self.runs.setdefault(eff, self._new())
            sv = self.saved.pop(eff, None)
            if sv and sv["kind"] == p["kind"]:
                run.update(alerted=True, since=sv["since"], said_at=sv["said_at"] if sv["said_at"] is not None else run["said_at"])
        if now - self.ctx.started > GONE_AFTER:
            self.saved.clear()  # not found again after HA had time to load: the pair is gone, the finding with it

    # --- reading the pair ---------------------------------------------------------------------------------------------

    def _read(self, eff: str, p: dict[str, Any]) -> tuple[int, float, float | None] | None:
        """(executor active 0/1, the measured value, the setpoint or None) — None: something unavailable, no judgement."""
        es, xs = self.ctx.hass.states.get(eff), self.ctx.hass.states.get(p["exec"])
        if es is None or xs is None or es.state in NOT_A_STATE or xs.state in NOT_A_STATE:
            return None
        if es.domain == "climate":  # a thermostat is its own effect (air, or a floor without loops): its reading and setpoint
            val, tgt = num(es.attributes.get("current_temperature")), num(es.attributes.get("temperature"))
            action = es.attributes.get("hvac_action")
            if val is None or tgt is None or es.state not in MODES["heat"]:
                return None
            return (1 if action in ACTIONS["heat"] else 0), val, tgt
        val = num(es.state)
        if val is None:
            return None
        tgt = None
        ts = self.ctx.hass.states.get(p["target"]) if p.get("target") else None
        if ts is not None and ts.state not in NOT_A_STATE and ts.state != "off":
            tgt = num(ts.attributes.get("temperature"))
        return (1 if xs.state in ACTIVE else 0), val, tgt

    def on_state(self, new: State, now: float) -> None:
        if new.entity_id in self._involved:
            self._sample(now, only=new.entity_id)

    def _sample(self, now: float, only: str | None = None) -> None:
        for eff, p in self.pairs.items():
            if only is not None and only not in (eff, p["exec"], p.get("target")):
                continue
            run = self.runs.setdefault(eff, self._new())
            got = self._read(eff, p)
            if got is None:
                run["samples"], run["bad_since"], run["last"] = [], None, None  # a gap (unavailable, a restart): a window starts afresh
                continue
            s = run["samples"]
            run["last"] = got
            if s and s[-1][1:] == got and (only is not None or now - s[-1][0] < SAMPLE_S):
                continue  # nothing new (an event) / nothing new and sampled lately (a tick)
            s.append((now, got[0], got[1], got[2]))
            edge = now - KINDS[p["kind"]]["window_h"] * SECONDS_PER_HOUR
            while len(s) > 1 and s[1][0] <= edge:
                s.pop(0)

    # --- the judgement ------------------------------------------------------------------------------------------------

    def warm(self, now: float) -> None:
        """The pairs are the mapping's, never saved: found at once when switched on again (insight.Detector.warm)."""
        self._refresh(now)

    def tick(self, now: float) -> None:
        self._refresh(now)
        self._sample(now)
        execs = {p["exec"] for p in self.pairs.values()}
        self.ctx.not_windows = execs
        for eff, p in self.pairs.items():
            self._judge(eff, p, self.runs[eff], now, execs)
        if now - self.ctx.started > GONE_AFTER:
            for eff in [e for e in self.runs if self.ctx.hass.states.get(e) is None and not self.runs[e]["alerted"]]:
                self.runs.pop(eff, None)

    def _delta(self, samples: list, now: float, hours: float) -> tuple[float, float] | None:
        """(value change over the last `hours`, executor's active share) from samples covering them; None — not covered."""
        edge = now - hours * SECONDS_PER_HOUR
        if not samples or samples[0][0] > edge:
            return None
        anchor = max((s for s in samples if s[0] <= edge), key=lambda s: s[0])
        pts = [(edge, anchor[1])] + [(s[0], s[1]) for s in samples if s[0] > edge]
        active = sum((nxt[0] - cur[0]) * cur[1] for cur, nxt in zip(pts, pts[1:] + [(now, 0)]))
        return samples[-1][2] - anchor[2], active / (now - edge)

    def _room_left(self, eff: str, p: dict[str, Any], cfg: dict[str, Any], val: float, tgt: float | None) -> bool:
        if cfg["falls"]:
            return val >= cfg["needs"]
        if tgt is not None:
            return tgt - val >= cfg["margin"]
        if cfg["peer_gap"]:
            area = self.ctx._area_id(eff)
            peers = [r["last"][1] for e, r in self.runs.items() if e != eff and r["last"] and self.pairs.get(e, {}).get("kind") == p["kind"] and self.ctx._area_id(e) == area and area]
            return len(peers) >= 2 and statistics.median(peers) - val >= cfg["peer_gap"]
        return False

    def _judge(self, eff: str, p: dict[str, Any], run: dict[str, Any], now: float, execs: set[str]) -> None:
        cfg = KINDS[p["kind"]]
        s, last = run["samples"], run["last"]
        if last is None or not s:
            return
        season = self.ctx.season_now()
        wrong_season = not cfg["falls"] and season not in (None, "heat")
        if self._ahu(eff, p) or (eff.startswith("climate.") and (wrong_season or self._learned(eff))):
            run["bad_since"] = None  # off-season / the zone's own norm judges (ClimateEffect) — any thermostat, a floor's too;
            return  # an air handler's zone: its air is not the room's (0.32, owner 10.11)
        if cfg["windows"] and self.ctx.opening_open(eff):
            run["samples"], run["bad_since"] = [s[-1]], None  # a window open: nothing is expected; a fresh window after it closes
            return
        if wrong_season:
            run["bad_since"] = None
            return
        if any(x[3] != s[-1][3] for x in s):
            run["samples"], run["bad_since"] = [s[-1]], None  # the setpoint moved (a schedule, the night, absence): a new question
            return
        win = self._delta(s, now, cfg["window_h"])
        if win is None:
            return  # not observed a whole window yet
        change, share = win
        sign = -1 if cfg["falls"] else 1
        failing = share >= MIN_SHARE and change * sign < cfg["min_change"] and self._room_left(eff, p, cfg, last[1], last[2])
        if failing:
            run["calm_since"] = None
            run["bad_since"] = run["bad_since"] if run["bad_since"] is not None else now
            if not run["alerted"] and now - run["bad_since"] >= (HOLD - 1) * cfg["window_h"] * SECONDS_PER_HOUR and (run["said_at"] is None or now - run["said_at"] >= REPEAT_S):
                run.update(alerted=True, since=run["bad_since"] - cfg["window_h"] * SECONDS_PER_HOUR, said_at=now)
                self.ctx.dirty()
            return
        run["bad_since"] = None
        if not run["alerted"]:
            return
        recent = self._delta(s, now, cfg["clear_h"])
        if (recent is not None and recent[0] * sign >= cfg["clear_change"]) or (run["calm_since"] is not None and now - run["calm_since"] >= cfg["clear_h"] * SECONDS_PER_HOUR):
            run.update(alerted=False, since=None, calm_since=None)
            self.ctx.dirty()
        elif run["calm_since"] is None:
            run["calm_since"] = now

    def _ahu(self, eff: str, p: dict[str, Any] | None) -> bool:
        """The effect or its executor is an air handler's climate: never judged «no effect»."""
        return self.ctx.air_handler(eff) or bool(p and self.ctx.air_handler(p["exec"]))

    def _learned(self, eff: str) -> bool:
        from .insight import ClimateEffect

        return any(isinstance(d, ClimateEffect) and d.norm(eff, "heat") is not None for d in self.ctx.detectors)

    def inputs(self, kind: str, eid: str) -> list[dict[str, Any]]:
        """The effect (a room's temperature / CO₂ / humidity), the executor that should move it, the room's windows."""
        p = self.pairs.get(eid) or self.saved.get(eid) or {}
        cfg = KINDS.get(p.get("kind", "")) or {}
        return [
            {"role": "effect", "entityId": eid},
            *([{"role": "exec", "entityId": p["exec"]}] if p.get("exec") else []),
            *({"role": "window", "entityId": w} for w in (self.ctx.windows_of(eid) if cfg.get("windows") else [])),
            {"role": "season"},
        ]

    def keys(self) -> list[tuple[str, str]]:
        return [(KIND, eff) for eff in dict.fromkeys([*self.pairs, *self.saved])]

    def excluded(self, kind: str, eid: str) -> str | None:
        p = self.pairs.get(eid)
        if self._ahu(eid, p):
            return "air_handler"
        cfg = KINDS.get((p or self.saved.get(eid) or {}).get("kind", ""))
        if cfg and not cfg["falls"] and self.ctx.season_now() not in (None, "heat"):
            return "season"
        return super().excluded(kind, eid)

    def progress(self, kind: str, eid: str, now: float) -> dict[str, Any]:
        # A fixed bar (min_change over window_h), nothing to learn.
        cfg = KINDS.get((self.pairs.get(eid) or self.saved.get(eid) or {}).get("kind", ""))
        return {"ready": {"norm": cfg["min_change"] if cfg else None, "unit": cfg["unit"] if cfg else "", "n": len((self.runs.get(eid) or {}).get("samples") or [])}}

    def reset(self, eid: str, kind: str) -> None:
        self.saved.pop(eid, None)
        if eid in self.runs:
            self.runs[eid] = self._new()

    def items(self, now: float) -> list[dict[str, Any]]:
        out = []
        for eff, sv in self.saved.items():  # right after a start, before the pairs are found again: the finding stays listed
            if self._ahu(eff, sv):
                continue
            xs = self.ctx.hass.states.get(sv["exec"])
            out.append({"key": f"{KIND}|{eff}", "kind": KIND, "role": sv["kind"], "entityId": eff, **self.ctx.who(eff), "exec": (xs.attributes.get("friendly_name") if xs else None) or sv["exec"], "since": _iso(sv["since"]), "hours": _round((now - sv["since"]) / SECONDS_PER_HOUR), "flatH": KINDS[sv["kind"]]["window_h"], "limit": KINDS[sv["kind"]]["min_change"]})
        for eff, run in self.runs.items():
            p = self.pairs.get(eff)
            if not run["alerted"] or p is None or run["since"] is None or self._ahu(eff, p):
                continue
            cfg = KINDS[p["kind"]]
            xs = self.ctx.hass.states.get(p["exec"])
            win = self._delta(run["samples"], now, cfg["window_h"])
            last = run["last"]
            out.append(
                {
                    "key": f"{KIND}|{eff}",
                    "kind": KIND,
                    "role": p["kind"],
                    "entityId": eff,
                    **self.ctx.who(eff),
                    "exec": (xs.attributes.get("friendly_name") if xs else None) or p["exec"],
                    "since": _iso(run["since"]),
                    "hours": _round((now - run["since"]) / SECONDS_PER_HOUR),
                    "flatH": cfg["window_h"],
                    "limit": cfg["min_change"],
                    **({"change": _round(win[0])} if win else {}),
                    **({"value": _round(last[1])} if last else {}),
                }
            )
        return out


NOEFFECT: tuple[type[Detector], ...] = (PairEffect,)


class PairsView(HomeAssistantView):
    """The integrator's refinement of the pairs (п.68). GET → pairs_view() + `supply` (loops that want a supply sensor,
    analytics.py п.8); POST (admin only):
      {"set": {"effect": entity_id, "kind": "floor|air|co2|humidity", "exec": entity_id | ""}} — this executor is this effect's;
      {"clear": entity_id} — back to what the mapping finds;
      {"kinds": {climate entity_id: "air|floor"}} — the deploy's «воздух / пол» of thermostats, the whole map (п.19);
      {"supply": {"loop": climate entity_id, "sensor": entity_id | ""}} — «кто греет пол, где датчик подачи» (п.8)."""

    url = "/api/hauflo/pairs"
    name = "api:hauflo:pairs"
    requires_auth = True

    def __init__(self, det_of) -> None:
        self.det_of = det_of

    def _fresh(self) -> PairEffect | None:
        det = self.det_of()
        if det is not None:
            det._refresh(det.ctx.clock())
        return det

    async def get(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        det = self._fresh()
        if det is None:
            return self.json_message("not ready", 409)
        return self.json(self._view(det))

    @staticmethod
    def _loops(det: PairEffect):
        lt = det.ctx.owner.get("loop_slow")
        return lt if lt is not None and hasattr(lt, "supply_view") else None

    def _view(self, det: PairEffect) -> dict[str, Any]:
        lt = self._loops(det)
        return {**det.pairs_view(), "supply": lt.supply_view() if lt is not None else []}

    async def post(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        det = self.det_of()
        if det is None:
            return self.json_message("not ready", 409)
        ok_id = lambda v: isinstance(v, str) and 0 < len(v) <= 255 and "." in v  # noqa: E731
        if isinstance(body, dict) and set(body) == {"set"} and isinstance(body["set"], dict):
            b = body["set"]
            if set(b) != {"effect", "kind", "exec"} or not ok_id(b["effect"]) or b["kind"] not in KINDS or not (b["exec"] == "" or ok_id(b["exec"])):
                return self.json_message("set: {effect, kind, exec} expected", 400)
            # An entity HA doesn't have is no pair (review B4 D-B4-4: climate.nonexistent_x was saved and only hidden in the UI).
            for eid in (b["effect"], b["exec"]):
                if eid and det.ctx.hass.states.get(eid) is None:
                    return self.json_message(f"unknown_entity: {eid}", 400)
            det.set_pair(b["effect"], b["kind"], b["exec"])
        elif isinstance(body, dict) and set(body) == {"clear"} and ok_id(body["clear"]):
            det.clear_pair(body["clear"])
        elif isinstance(body, dict) and set(body) == {"kinds"} and isinstance(body["kinds"], dict):
            k = body["kinds"]
            if len(k) > 5000 or not all(ok_id(e) and e.startswith("climate.") and v in EFFECT_KINDS for e, v in k.items()):
                return self.json_message("kinds: {climate entity_id: air | floor} expected", 400)
            det.set_kinds(k)
        elif isinstance(body, dict) and set(body) == {"supply"} and isinstance(body["supply"], dict):
            b = body["supply"]
            lt = self._loops(det)
            if set(b) != {"loop", "sensor"} or not ok_id(b["loop"]) or not (b["sensor"] == "" or ok_id(b["sensor"])) or lt is None:
                return self.json_message("supply: {loop, sensor} expected", 400)
            if b["loop"] not in {x["loop"] for x in lt.supply_view()}:
                return self.json_message(f"not_a_loop: {b['loop']}", 400)
            if b["sensor"] and det.ctx.hass.states.get(b["sensor"]) is None:
                return self.json_message(f"unknown_entity: {b['sensor']}", 400)
            lt.set_supply(b["loop"], b["sensor"])
        else:
            return self.json_message("set / clear / kinds / supply expected", 400)
        return self.json(self._view(self._fresh()))
