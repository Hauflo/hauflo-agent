"""«Зима / Лето» («Обогрев / Охлаждение») for the whole house from the client's dashboard settings (owner 27.10: «в зимний
период одной кнопкой кондиционеры на обогрев, летом — на охлаждение»; 30.10: the integrator picks the thermostats, the
wording, the resend and the suggestion).

Which climates (owner 30.10: by default only Hauflo's own): one that can both heat and cool (HA says so) and is shown,
  * Hauflo's own (made by Hauflo — the agent's owned set, the same notion the tunnel's allow-list uses) is switched unless
    the integrator unticked it in Hauflo («Зима/Лето: какие термостаты переключать», sent by the deploy: `exclude`);
  * a foreign one (a Daikin, the HA demo, a KNX climate made by hand in HA's KNX UI) is switched only if the integrator
    ticked it (`include`).
A new Hauflo climate is switched by default, a foreign one isn't.
Migration: a saved config from before `include` existed would silently stop (or, with the new default, never have
started) switching foreign climates. So such a config runs the old rule (every foreign one not in `exclude`) until HA
is up, then those climates are written into `include` once (`async_migrate_include`) — nothing changes for an existing
house; a new install has no saved config and gets the new default.
  * working → `climate.set_hvac_mode` heat / cool;
  * switched off → never switched on by the season: a KNX one made in HA's KNX UI gets its heat/cool address written
    (DPT 1.100: 1 heat, 0 cool) — one telegram per address, a central switchover shared by many thermostats goes once;
    any other stays as it is and is said.

Resend: a KNX thermostat may lose its mode after a bus power loss or a reset — the chosen season is sent again every
`resend_s` (tuning.json, the integrator may change it or turn it off), after HA starts and when the KNX connection comes
back. Same safety: nothing is switched on, a KNX UI one gets its heat/cool address (once per address), another working
one is set only if it is in the other mode. Quietly: the log only, no message to the family.

Suggestion, never a switch: the value rule «season» (rules.py, recipe in rules_canon.json; made once from the earlier
`suggest` settings): the outdoor temperature's daily means (HA recorder's long-term statistics, window.py) below `below`
for `days` days in a row while the house cools → «пора на зиму / обогрев?»; strictly above `above` while it heats →
summer. No sensor or too little history → «нет данных», never a guess. «Не сейчас» snoozes it; sent again only after a
change or the snooze.

GET/POST /api/hauflo/season — any signed-in person of this HA (the client isn't an admin); not through the tunnel.
GET/POST /api/hauflo/season/config — Hauflo's settings through the tunnel (admin only).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.components.http import HomeAssistantView
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, CoreState, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, translation
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from . import derived as dv
from .const import DOMAIN
from .house import may_edit, refuse
from .storage import HaufloStore
from .tuning import TUNING

_LOGGER = logging.getLogger(__name__)
STORE_KEY = "hauflo.season"
HEAT, COOL = "heat", "cool"
MODES = (HEAT, COOL)
# The value rules' «season» condition (rules.py) speaks of winter / summer.
RULE_SEASON = {HEAT: "winter", COOL: "summer"}
# The wording the integrator picks: «Зима / Лето» (default — what existing houses had) or «Обогрев / Охлаждение».
LABELS = ("season", "mode")
LANGS = ("ru", "en")
_GONE = ("unavailable", "unknown")
_ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_T = TUNING["season"]
RESEND_S = _T["resend_s"]
RESEND_LO, RESEND_HI = _T["resend_bounds_s"]
TICK = _T["tick_s"]
START_DELAY = _T["start_delay_s"]
BELOW, ABOVE, DAYS, SNOOZE_DAYS = _T["suggest_below"], _T["suggest_above"], _T["suggest_days"], _T["snooze_days"]
DAYS_LO, DAYS_HI = _T["days_bounds"]
SNOOZE_LO, SNOOZE_HI = _T["snooze_bounds"]
T_LO, T_HI = _T["threshold_bounds"]
MAX_EXCLUDE = _T["max_exclude"]
MAX_INCLUDE = _T["max_include"]
LOG_MAX = _T["log_max"]
ACTION = "HAUFLO_SEASON"  # a phone's button: «HAUFLO_SEASON|heat», «HAUFLO_SEASON|later»
LATER = "later"
NOTIFY_TAG = "hauflo_season_suggest"
SEASON_RULE = "season"  # the suggestion's value rule (and its recipe) — its entity id, once made, never changes


def can_switch(state: Any) -> bool:
    modes = state.attributes.get("hvac_modes") or []
    return HEAT in modes and COOL in modes


def _shown(reg: er.EntityRegistry, eid: str) -> bool:
    ent = reg.async_get(eid)
    return not (ent and (ent.disabled_by or ent.hidden_by))  # ones taken off the client's screen are not switched


def is_chosen(state: Any, reg: er.EntityRegistry, ours: bool, exclude: Any, include: Any, legacy: bool = False) -> bool:
    """The one rule: capable and shown, and (Hauflo's own and not unticked) or (foreign and ticked). `legacy` = a config
    from before `include` (not migrated yet): a foreign one counts as ticked unless unticked."""
    if not (can_switch(state) and _shown(reg, state.entity_id)):
        return False
    if ours:
        return state.entity_id not in exclude
    return state.entity_id in include or (legacy and state.entity_id not in exclude)


def acs(hass: HomeAssistant, exclude: frozenset[str] | set[str] = frozenset(), include: frozenset[str] | set[str] = frozenset(), owns: Callable[[str], bool] | None = None, legacy: bool = False) -> list[Any]:
    """The climates the season switches (is_chosen) — floors and heat-only thermostats never. No `owns` = nothing is
    Hauflo's own."""
    reg = er.async_get(hass)
    return [s for s in hass.states.async_all("climate") if s.state not in _GONE and is_chosen(s, reg, bool(owns and owns(s.entity_id)), exclude, include, legacy)]


def climates(hass: HomeAssistant, exclude: frozenset[str] | set[str] = frozenset(), include: frozenset[str] | set[str] = frozenset(), owns: Callable[[str], bool] | None = None, legacy: bool = False) -> list[dict[str, Any]]:
    """Every climate of the house for Hauflo's setting: what HA says it can, whether it is switched now."""
    reg = er.async_get(hass)
    out = []
    for s in sorted(hass.states.async_all("climate"), key=lambda x: x.entity_id):
        ent = reg.async_get(s.entity_id)
        ours = bool(owns and owns(s.entity_id))
        out.append(
            {
                "entity_id": s.entity_id,
                "name": s.attributes.get("friendly_name") or s.entity_id,
                "platform": ent.platform if ent else None,
                "modes": list(s.attributes.get("hvac_modes") or []),
                "state": s.state,
                "capable": can_switch(s),
                "shown": _shown(reg, s.entity_id),
                "heat_cool_address": heat_cool_address(hass, s.entity_id) is not None,
                "ours": ours,
                "chosen": is_chosen(s, reg, ours, exclude, include, legacy),
            }
        )
    return out


def sensors(hass: HomeAssistant) -> list[dict[str, Any]]:
    """What the outdoor temperature may be read from: temperature sensors with statistics, weather entities."""
    out = []
    for s in sorted(hass.states.async_all("sensor"), key=lambda x: x.entity_id):
        if s.attributes.get("device_class") == "temperature" and s.attributes.get("state_class"):
            out.append({"entity_id": s.entity_id, "name": s.attributes.get("friendly_name") or s.entity_id, "kind": "sensor"})
    for s in sorted(hass.states.async_all("weather"), key=lambda x: x.entity_id):
        if dv.num(s.attributes.get("temperature")) is not None:
            out.append({"entity_id": s.entity_id, "name": s.attributes.get("friendly_name") or s.entity_id, "kind": "weather"})
    return out


def heat_cool_address(hass: HomeAssistant, entity_id: str) -> str | None:
    """The heat/cool switchover address of a KNX UI climate (HA's KNX config store)."""
    from . import knx_data  # noqa: PLC0415 — the one reader of HA's KNX internals

    try:
        cfg = knx_data(hass).config_store.get_entity_config(entity_id)  # type: ignore[union-attr]
        ga = ((cfg.get("data") or {}).get("knx") or {}).get("ga_heat_cool") or {}
        w = ga.get("write")
        return w if isinstance(w, str) and w else None
    except Exception:  # noqa: BLE001 — not a KNX UI entity, or HA changed its internals
        return None


def knx_connected(hass: HomeAssistant) -> bool | None:
    from . import knx_data  # noqa: PLC0415

    try:
        return bool(knx_data(hass).xknx.connection_manager.connected.is_set())  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 — no KNX, or HA changed its internals
        return None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def defaults() -> dict[str, Any]:
    return {
        "exclude": [],
        "include": [],
        "labels": LABELS[0],
        "resend_s": RESEND_S,
        "suggest": {"enabled": True, "sensor": None, "below": BELOW, "above": ABOVE, "days": DAYS, "snooze_days": SNOOZE_DAYS},
    }


def clean_config(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """Only what the season understands, each value in its bounds; what is dropped is said."""
    out = defaults()
    dropped: list[str] = []
    if not isinstance(raw, dict):
        return out, dropped
    ex = raw.get("exclude")
    for eid in (ex if isinstance(ex, list) else [])[:MAX_EXCLUDE]:
        if isinstance(eid, str) and eid.startswith("climate.") and _ENTITY_RE.match(eid):
            if eid not in out["exclude"]:
                out["exclude"].append(eid)
        else:
            dropped.append(f"exclude {str(eid)[:80]!r} is not a climate")
    inc = raw.get("include")
    for eid in (inc if isinstance(inc, list) else [])[:MAX_INCLUDE]:
        if isinstance(eid, str) and eid.startswith("climate.") and _ENTITY_RE.match(eid):
            if eid not in out["include"]:
                out["include"].append(eid)
        else:
            dropped.append(f"include {str(eid)[:80]!r} is not a climate")
    if raw.get("labels") in LABELS:
        out["labels"] = raw["labels"]
    if (r := dv.num(raw.get("resend_s"))) is not None:
        out["resend_s"] = 0 if r <= 0 else int(_clamp(r, RESEND_LO, RESEND_HI))
    sg = raw.get("suggest")
    if isinstance(sg, dict):
        s = out["suggest"]
        s["enabled"] = sg.get("enabled") is not False
        sensor = sg.get("sensor")
        if isinstance(sensor, str) and _ENTITY_RE.match(sensor) and sensor.split(".")[0] in ("sensor", "weather"):
            s["sensor"] = sensor
        elif sensor is not None:
            dropped.append(f"sensor {str(sensor)[:80]!r} is not a sensor or a weather entity")
        for key, lo, hi in (("below", T_LO, T_HI), ("above", T_LO, T_HI), ("days", DAYS_LO, DAYS_HI), ("snooze_days", SNOOZE_LO, SNOOZE_HI)):
            if (v := dv.num(sg.get(key))) is not None:
                s[key] = int(_clamp(v, lo, hi)) if key in ("days", "snooze_days") else _clamp(v, lo, hi)
    return out, dropped


def rule_body(s: dict[str, Any]) -> dict[str, Any]:
    """The suggestion's settings as a value rule (recipe «season», rules.py / window.py): the street's daily mean below
    `below` for `days` days in a row → «на зиму?»; strictly above `above` as long → «на лето?». A suggestion, never a
    switch (auto off). Thresholds the wrong way round (above < below) keep `below` both ways (Y can't be negative)."""
    return {
        "id": SEASON_RULE,
        "name": SEASON_RULE,
        "name_gen": {"recipe": SEASON_RULE},
        "purpose": SEASON_RULE,
        "enabled": s["enabled"],
        "input": {"entity_id": s["sensor"]} if s["sensor"] else "outdoor",
        "when": "below",
        "x": s["below"],
        "y": max(0.0, s["above"] - s["below"]),
        "window": {"stat": "mean", "period": "day", "n": int(s["days"])},
        "outputs": {"suggest": True, "suggest_back": True, "auto": False},
        "snooze_days": int(s["snooze_days"]),
        "actions": [{"do": "season", "value": HEAT}],
        "back": "reverse",
    }


class Season:
    """The house's season: the client's switch, the resend; the suggestion is the value rule «season» (rules.py). One per
    loaded entry."""

    def __init__(self, hass: HomeAssistant, notify_of: Callable[[], Any] | None = None, rules_of: Callable[[], Any] | None = None, owns: Callable[[str], bool] | None = None) -> None:
        self.hass = hass
        self.owns = owns  # Hauflo's own entity (the agent's owned set); None = nothing is
        self.legacy = False  # a saved config from before `include`, not migrated yet (see the module docstring)
        self.notify_of = notify_of or (lambda: None)
        self.rules_of = rules_of or (lambda: None)
        self.store = HaufloStore(hass, STORE_KEY)
        self.config: dict[str, Any] = defaults()
        self.season: str | None = None  # the last one chosen (the client's switch)
        self.at: float | None = None
        self.by: str | None = None
        # The suggestion became a value rule (owner 30.10): made once from `suggest`; Hauflo sending other settings later
        # changes it again (an older Hauflo still sends them), the same settings again never undo the integrator's edits.
        self.migrated = False
        self.suggest_applied: dict[str, Any] | None = None
        self.pending: str | None = None  # why the rule couldn't be made yet (no street sensor yet …) — tried again
        self.last_resend: float | None = None
        self.log: list[dict[str, Any]] = []
        self.texts: dict[str, dict[str, str]] = {lang: {} for lang in LANGS}
        self._knx_up: bool | None = None
        self._unsubs: list[CALLBACK_TYPE] = []

    # --- storage --------------------------------------------------------------------------------------------------

    async def async_load(self) -> None:
        data = await self.store.async_load() or {}
        raw = data.get("config")
        self.config = clean_config(raw)[0]
        self.legacy = isinstance(raw, dict) and "include" not in raw  # no saved config = a new install: the new default
        self.season = data.get("season") if data.get("season") in MODES else None
        self.at, self.by = data.get("at"), data.get("by")
        self.migrated = data.get("migrated") is True
        self.suggest_applied = data.get("suggest_applied") if isinstance(data.get("suggest_applied"), dict) else None
        self.last_resend = data.get("last_resend") if isinstance(data.get("last_resend"), (int, float)) else None

    def data(self) -> dict[str, Any]:
        return {"config": self.config, "season": self.season, "at": self.at, "by": self.by, "migrated": self.migrated, "suggest_applied": self.suggest_applied, "last_resend": self.last_resend}

    async def _save(self) -> None:
        await self.store.async_save(self.data())

    async def async_seed(self, mode: str) -> None:
        """A season known from elsewhere (the retired «Комфортный пол»'s house mode) — only if none was chosen here."""
        if mode in MODES and self.season is None:
            self.season = mode
            await self._save()

    # --- running --------------------------------------------------------------------------------------------------

    async def async_start(self) -> None:
        try:
            await self.async_texts()
        except Exception:  # noqa: BLE001 — the keys stand in for the texts
            _LOGGER.debug("Hauflo season: translations not loaded", exc_info=True)
        self._knx_up = knx_connected(self.hass)
        if (rules := self.rules_of()) is not None:
            rules.ask_text[SEASON_RULE] = self._ask_text
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=TICK), name="hauflo season"))
        self._unsubs.append(self.hass.bus.async_listen("mobile_app_notification_action", self._on_action))

        async def _later(_now: Any) -> None:
            await self.async_migrate_include()
            await self.async_sync_rule()
            await self.resend("start")

        if self.hass.state == CoreState.running:
            self._unsubs.append(async_call_later(self.hass, START_DELAY, _later))
        else:

            @callback
            def _started(_event: Event) -> None:
                self._unsubs.append(async_call_later(self.hass, START_DELAY, _later))

            self._unsubs.append(self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started))

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except ValueError:  # a one-shot listener already gone
                pass
        self._unsubs.clear()

    async def _tick(self, _now: Any) -> None:
        up = knx_connected(self.hass)
        back = up is True and self._knx_up is False
        self._knx_up = up
        resend = self.config["resend_s"]
        if back:
            await self.resend("knx back")
        elif resend and (self.last_resend is None or time.time() - self.last_resend >= resend):
            await self.resend("interval")
        if not self.migrated:
            await self.async_sync_rule()  # the street's sensor may have come up since

    # --- texts ----------------------------------------------------------------------------------------------------

    async def async_texts(self) -> None:
        prefix, suffix = f"component.{DOMAIN}.exceptions.", ".message"
        for lang in LANGS:
            tr = await translation.async_get_translations(self.hass, lang, "exceptions", [DOMAIN])
            self.texts[lang] = {k[len(prefix) : -len(suffix)]: v for k, v in tr.items() if k.startswith(prefix) and k.endswith(suffix)}

    def _lang(self) -> str:
        engine = self.notify_of()
        lang = str((getattr(engine, "config", None) or {}).get("lang") or self.hass.config.language or LANGS[0])
        return LANGS[0] if lang.lower().startswith(LANGS[0]) else LANGS[1]

    def say(self, key: str, lang: str, **ph: Any) -> str:
        text = self.texts.get(lang, {}).get(key) or self.texts[LANGS[1]].get(key) or key
        try:
            return text.format(**ph)
        except (KeyError, IndexError, ValueError):
            return text

    # --- what the house does now ----------------------------------------------------------------------------------

    def chosen(self) -> list[Any]:
        return acs(self.hass, set(self.config["exclude"]), set(self.config["include"]), self.owns, self.legacy)

    async def async_migrate_include(self) -> None:
        """An old config (no `include`): the foreign climates it switched by the old rule become `include`, once."""
        if not self.legacy:
            return
        reg = er.async_get(self.hass)
        ex = set(self.config["exclude"])
        found = [s.entity_id for s in sorted(self.hass.states.async_all("climate"), key=lambda x: x.entity_id) if not (self.owns and self.owns(s.entity_id)) and is_chosen(s, reg, False, ex, (), True)]
        self.config["include"] = sorted({*self.config["include"], *found})[:MAX_INCLUDE]
        self.legacy = False
        await self._save()

    def current(self) -> str | None:
        """What the working chosen ACs do now (the most of them); with all off — the last one chosen here."""
        on = [s.state for s in self.chosen() if s.state in MODES]
        return (HEAT if on.count(HEAT) >= on.count(COOL) else COOL) if on else self.season

    def rule_season(self) -> str:
        """For the value rules' «season» condition: winter unless summer was chosen."""
        return RULE_SEASON.get(self.season or HEAT, RULE_SEASON[HEAT])

    # --- the suggestion: the value rule «season» -------------------------------------------------------------------

    def _rule(self) -> tuple[Any, dict[str, Any] | None]:
        rules = self.rules_of()
        return rules, (rules.rules.get(SEASON_RULE) if rules is not None else None)

    def _to(self, rule: dict[str, Any], on: bool) -> tuple[str | None, int, float]:
        """(the season it suggests, days in a row, the threshold) for the rule's way `on` (cold) / not (warm)."""
        act = next((a for a in rule["actions"] if a["do"] == "season"), None)
        w = rule.get("window") or {}
        if act is None:
            return None, 0, 0.0
        to = act["value"] if on else MODES[1 - MODES.index(act["value"])]
        if on:
            return to, int(w.get("n", 0)), float(rule.get("x", 0.0))
        return to, int(w.get("n_back", w.get("n", 0))), float(rule.get("x", 0.0)) + float(rule.get("y", 0.0))

    def suggestion(self) -> dict[str, Any] | None:
        """The suggestion the family sees now (not snoozed, not done already), or None."""
        rules, rule = self._rule()
        sug = rules.suggestion(SEASON_RULE) if rule is not None else None
        if sug is None:
            return None
        to, days, threshold = self._to(rule, sug["on"])  # type: ignore[arg-type]
        return {"to": to, "days": days, "threshold": threshold} if to in MODES else None

    def _ask_text(self, rule: dict[str, Any], on: bool, _ru: bool) -> tuple[str, str, str]:
        """The season's own push wording (its labels, the project's language)."""
        lang = self._lang()
        mode, days, threshold = self._to(rule, on)
        to = self.say(f"season_to_{self.config['labels']}_{mode}", lang)
        why = self.say(f"season_suggest_why_{mode}", lang, days=days, threshold=f"{threshold:g}")
        return self.say("season_suggest_title", lang, to=to), self.say("season_suggest_message", lang, to=to, why=why), NOTIFY_TAG

    async def async_sync_rule(self, force: bool = False) -> None:
        """`suggest` → the value rule: once (the migration), and again when Hauflo sends other settings (`force`)."""
        rules, rule = self._rule()
        if rules is None or (self.migrated and not force):
            return
        s = self.config["suggest"]
        if rule is not None and not force:
            # Made already (by Hauflo's wizard): kept as it is.
            self.migrated, self.suggest_applied = True, dict(s)
            await self._save()
            return
        body = rule_body(s)
        if rule is not None:
            # Hauflo's new settings: they change what they say; the integrator's other choices (outputs …) are kept.
            body = {**rule, **{k: body[k] for k in ("enabled", "input", "x", "y", "window", "snooze_days")}, "name": rule["name"]}
        try:
            try:
                await rules.save(body, client=False, user_id=None)
            except dv.Invalid as err:
                if err.code != "bad_hysteresis":
                    raise
                await rules.save({**body, "y": 0.0}, client=False, user_id=None)
        except dv.Invalid as err:
            self.pending = err.code  # no street sensor (yet), no statistics: tried again on the next tick
            return
        self.migrated, self.suggest_applied, self.pending = True, dict(s), None
        await self._save()

    async def answer(self) -> dict[str, Any]:
        items = self.chosen()
        return {
            "season": self.current(),
            "chosen": self.season,
            "at": self.at,
            "labels": self.config["labels"],
            "suggestion": self.suggestion(),
            "acs": [{"entity_id": s.entity_id, "name": s.attributes.get("friendly_name") or s.entity_id, "state": s.state} for s in items],
        }

    def setup_answer(self) -> dict[str, Any]:
        """For Hauflo's setting: what the agent holds, the house's climates and sensors, the suggestion's rule."""
        found = dv.outdoor(self.hass)
        rules, rule = self._rule()
        sug = self.suggestion()
        st = rules.st(SEASON_RULE) if rule is not None else {}
        if rule is None:
            status = "no_rule" if self.migrated else "off" if not self.config["suggest"]["enabled"] else "no_sensor"
        else:
            status = "off" if not rule["enabled"] else "no_data" if st.get("no_data") else "suggest" if sug else "ok"
        snoozed = (st.get("sug") or {}).get("snooze_until") or None
        return {
            "config": self.config,
            "defaults": defaults(),
            "season": self.current(),
            "chosen": self.season,
            "climates": climates(self.hass, set(self.config["exclude"]), set(self.config["include"]), self.owns, self.legacy),
            "sensors": sensors(self.hass),
            "auto_sensor": found["entity_id"] if found else None,
            "suggest": {
                "status": status,
                "to": sug["to"] if sug else None,
                "sensor": rule["input"]["entity_id"] if rule else None,
                "means": ((rules.win.get(SEASON_RULE) or {}).get("values") or []) if rule else [],
                "snoozed_until": snoozed,
                "rule": rules.rule_out(SEASON_RULE, False) if rule else None,
                "pending": self.pending,
            },
            "last_resend": self.last_resend,
            "log": self.log[-LOG_MAX:],
        }

    async def async_set_config(self, raw: Any) -> dict[str, Any]:
        prev = self.config.get("suggest")
        prev_include = self.config["include"]
        self.config, dropped = clean_config(raw)
        if isinstance(raw, dict) and "include" in raw:
            self.legacy = False  # Hauflo knows the setting: its list is the answer
        else:
            self.config["include"] = prev_include  # an older Hauflo sends no `include`: what is held stays
        sent = isinstance(raw, dict) and isinstance(raw.get("suggest"), dict)
        if not sent and prev:
            # The app edits the street rule itself now (no «suggest» sent): the old settings stay until migrated.
            self.config["suggest"] = prev
        await self._save()
        if sent and self.config["suggest"] != self.suggest_applied:
            await self.async_sync_rule(force=self.migrated)
        return {**self.setup_answer(), "dropped": dropped}

    # --- the client's switch ---------------------------------------------------------------------------------------

    async def apply(self, mode: str, user: str | None) -> dict[str, Any]:
        hass = self.hass
        changed: list[str] = []
        sent: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        addresses: set[str] = set()
        for s in self.chosen():
            if s.state == mode:
                continue
            try:
                if s.state != "off":
                    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": s.entity_id, "hvac_mode": mode}, blocking=True)
                    changed.append(s.entity_id)
                elif (ga := heat_cool_address(hass, s.entity_id)) is not None:
                    if ga not in addresses:
                        addresses.add(ga)
                        await hass.services.async_call("knx", "send", {"address": ga, "payload": 1 if mode == HEAT else 0}, blocking=True)
                    sent.append(s.entity_id)
                else:
                    skipped.append(s.entity_id)
            except Exception as err:  # noqa: BLE001 — one AC failing never stops the others
                _LOGGER.warning("Hauflo season: %s %s failed: %s", mode, s.entity_id, err)
                failed.append(s.entity_id)
        self.season, self.at, self.by = mode, time.time(), user
        self.last_resend = time.time()  # just sent: the next resend a whole interval later
        await self._save()
        if self.suggestion() is None:
            self._dismiss()  # done (by the switch or the push): the note goes too
        return {"season": mode, "changed": changed, "sent": sent, "skipped": skipped, "failed": failed}

    # --- resend ---------------------------------------------------------------------------------------------------

    async def resend(self, why: str) -> dict[str, Any] | None:
        """The chosen season again — never one guessed, never a climate switched on. A KNX UI climate: its heat/cool
        address (once per address, whether it runs or is off — the address doesn't switch it on); another one: set only
        while it runs in the other mode."""
        mode = self.season
        if mode not in MODES or not self.config["resend_s"]:  # off: no resend at all (interval, start, KNX back)
            return None
        hass = self.hass
        addresses: set[str] = set()
        sent: list[str] = []
        changed: list[str] = []
        failed: list[str] = []
        for s in self.chosen():
            try:
                if (ga := heat_cool_address(hass, s.entity_id)) is not None:
                    if ga not in addresses:
                        addresses.add(ga)
                        await hass.services.async_call("knx", "send", {"address": ga, "payload": 1 if mode == HEAT else 0}, blocking=True)
                    sent.append(s.entity_id)
                elif s.state in MODES and s.state != mode:
                    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": s.entity_id, "hvac_mode": mode}, blocking=True)
                    changed.append(s.entity_id)
            except Exception as err:  # noqa: BLE001 — one failing never stops the others
                _LOGGER.debug("Hauflo season resend: %s failed: %s", s.entity_id, err)
                failed.append(s.entity_id)
        self.last_resend = time.time()
        self.store.async_delay_save(self.data, TICK)
        entry = {"at": self.last_resend, "why": why, "season": mode, "addresses": sorted(addresses), "changed": changed, "failed": failed}
        self.log = [*self.log[-(LOG_MAX - 1) :], entry]
        _LOGGER.debug("Hauflo season resend (%s): %s", why, entry)
        return entry

    # --- the phone's buttons (a push sent before the suggestion was a rule), the dashboard's «Не сейчас» ----------------

    @callback
    def _dismiss(self) -> None:
        from homeassistant.components import persistent_notification  # noqa: PLC0415

        persistent_notification.async_dismiss(self.hass, NOTIFY_TAG)

    async def snooze(self) -> None:
        rules, rule = self._rule()
        if rule is not None:
            await rules.answer_suggestion(SEASON_RULE, LATER)
        self._dismiss()

    async def _on_action(self, event: Event) -> None:
        action = str(event.data.get("action") or "")
        if not action.startswith(ACTION + "|"):
            return
        what = action.split("|", 1)[1]
        user = await self.hass.auth.async_get_user(event.context.user_id) if event.context.user_id else None
        if not await may_edit(self.hass, user):
            return  # a restricted (child) profile's phone, or nobody known: as the dashboard, refused
        if what == LATER:
            await self.snooze()
        elif what in MODES and (sug := self.suggestion()) and sug["to"] == what:
            rules, _rule = self._rule()
            await rules.answer_suggestion(SEASON_RULE, "on" if rules.st(SEASON_RULE)["sug"]["on"] else "off", user.name if user else None)



class SeasonView(HomeAssistantView):
    url = "/api/hauflo/season"
    name = "api:hauflo:season"
    requires_auth = True

    def __init__(self, season_of: Callable[[], Season | None]) -> None:
        self.season_of = season_of

    async def get(self, request):
        season = self.season_of()
        if season is None:
            return self.json_message("Hauflo agent not set up", 503)
        return self.json(await season.answer())

    async def post(self, request):
        season = self.season_of()
        if season is None:
            return self.json_message("Hauflo agent not set up", 503)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        body = body if isinstance(body, dict) else {}
        mode = body.get("mode")
        if mode not in MODES and body.get("snooze") is not True:
            return self.json_message("mode: heat or cool, or snooze", 400)
        if (no := await refuse(request)) is not None:
            return no  # «Кто может менять настройки дома» (house.py): admins and the people an admin allowed
        if mode not in MODES:
            await season.snooze()
            return self.json(await season.answer())
        # A person's own action, as HA's own buttons are (the agent's KNX allowlist is for Hauflo's tunnel, which
        # can't reach this view).
        user = request["hass_user"]
        return self.json(await season.apply(mode, user.name if user else None))


class SeasonConfigView(HomeAssistantView):
    """Hauflo's settings of the season (through the tunnel, admin only): GET what the agent holds, POST new ones."""

    url = "/api/hauflo/season/config"
    name = "api:hauflo:season:config"
    requires_auth = True

    def __init__(self, season_of: Callable[[], Season | None]) -> None:
        self.season_of = season_of

    async def get(self, request):
        season = self.season_of()
        if season is None:
            return self.json_message("Hauflo agent not set up", 503)
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        return self.json(season.setup_answer())

    async def post(self, request):
        season = self.season_of()
        if season is None:
            return self.json_message("Hauflo agent not set up", 503)
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict):
            return self.json_message("an object expected", 400)
        return self.json(await season.async_set_config(body))
