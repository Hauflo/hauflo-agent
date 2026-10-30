"""Values worked out from other sensors («Правила по значениям», docs/automations.md): dew point, absolute humidity,
the difference of two sensors, min / max / average of several, how fast one rises. Each is a real HA sensor
(`sensor.hauflo_…`): seen in HA's history and dashboards, usable by HA's own automations, and the input of a value rule.

Never stale (design 29.10: «never act on stale derived»): unavailable while a source is unavailable / unknown / not a
number or hasn't reported for `max_age` minutes — a rule on it then goes to its safe state, it never acts on an old value.

«Улица» (outdoor): a KNX weather station or a Hauflo sensor whose name says so (canon: derived.outdoor_words), else HA's
weather.* (its temperature, said «по прогнозу» — a forecast is not the street).

Defaults are in rules_canon.json (canon.py); the texts of refusals in translations («exceptions», rules_…).
"""

from __future__ import annotations

from collections import deque
import math
import re
import time
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import PERCENTAGE, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_conversion import TemperatureConverter

from .canon import CANON

# Physics (named, not settings): Magnus over water (Sonntag 1990 / WMO), −45…60 °C; the ideal gas law for water vapour.
MAGNUS_B, MAGNUS_C = 17.62, 243.12
MAGNUS_E0_HPA = 6.112
VAPOUR_G_K_PER_HPA = 2.1674  # g·K / (m³·hPa): 100 / R_v (461.5 J/(kg·K)) × 1000
KELVIN_ZERO = 273.15
PERCENT = 100
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
ROUND_DIGITS = 3
ABS_HUMIDITY_UNIT = "g/m³"
RATE_SUFFIX = "/h"
SETPOINT_GAP = "setpoint_gap"  # a climate's «how far from its setpoint»: setpoint − room temperature (rules.py reads it)

KINDS = ("dew_point", "abs_humidity", "delta", "min", "max", "avg", "rise")
TWO = {"dew_point", "abs_humidity", "delta"}  # [temperature, humidity] / [a, b] — exactly two sources
PAIR = 2
TEMP_UNITS = {UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.KELVIN}
NOT_A_STATE = {STATE_UNAVAILABLE, STATE_UNKNOWN, None, ""}
WAITING = ("starting", "unknown", "collecting")  # a derived value that has no number yet, but nothing is lost
NOT_SOURCES = ("sensor.hauflo_", "binary_sensor.", "switch.", "light.")
_SRC_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+(#[a-z_]+)?$")


def now() -> float:
    """Wall clock (tests move it)."""
    return time.time()


def _cfg() -> dict[str, Any]:
    return CANON["derived"]


def id_re() -> re.Pattern[str]:
    return re.compile(rf"^[a-z0-9_]{{1,{int(CANON['limits']['id_len'])}}}$")


class Invalid(ValueError):
    """A setting refused: `code` for the app, `key` its text in translations («exceptions»), `ph` the placeholders."""

    def __init__(self, code: str, key: str, **ph: Any) -> None:
        super().__init__(key)
        self.code, self.key, self.ph = code, key, ph


def num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# --- formulas -------------------------------------------------------------------------------------------------------


def dew_point(t: float, rh: float) -> float | None:
    """°C from °C and % (Magnus). 20 °C / 50 % → 9.26 °C."""
    if not 0 < rh <= PERCENT or t <= -MAGNUS_C:
        return None
    g = math.log(rh / PERCENT) + MAGNUS_B * t / (MAGNUS_C + t)
    return MAGNUS_C * g / (MAGNUS_B - g)


def abs_humidity(t: float, rh: float) -> float | None:
    """g/m³ from °C and %: the saturation pressure by the same Magnus, the ideal gas law. 20 °C / 50 % → 8.6 g/m³."""
    if not 0 <= rh <= PERCENT or t <= -MAGNUS_C:
        return None
    es = MAGNUS_E0_HPA * math.exp(MAGNUS_B * t / (MAGNUS_C + t))
    return es * rh * VAPOUR_G_K_PER_HPA / (KELVIN_ZERO + t)


def slope_per_hour(samples: list[tuple[float, float]]) -> float | None:
    """Least squares over (time s, value): units per hour."""
    if len(samples) <= 1:
        return None
    n = len(samples)
    mt = sum(t for t, _ in samples) / n
    mv = sum(v for _, v in samples) / n
    den = sum((t - mt) ** 2 for t, _ in samples)
    if den <= 0:
        return None
    return sum((t - mt) * (v - mv) for t, v in samples) / den * SECONDS_PER_HOUR


# --- reading HA values ------------------------------------------------------------------------------------------------


def split_source(src: str) -> tuple[str, str | None]:
    eid, _, attr = src.partition("#")
    return eid, attr or None


def default_attribute(eid: str) -> str | None:
    """A climate's value is its room temperature, a weather's its temperature."""
    if eid.startswith("climate."):
        return "current_temperature"
    if eid.startswith("weather."):
        return "temperature"
    return None


def unit_of(st: State, attr: str | None) -> str | None:
    a = st.attributes
    if attr is None:
        return a.get("unit_of_measurement")
    if st.entity_id.startswith("weather."):
        if attr in ("temperature", "dew_point", "apparent_temperature"):
            return a.get("temperature_unit")
        if attr == "humidity":
            return PERCENTAGE
        if attr == "pressure":
            return a.get("pressure_unit")
        if attr in ("wind_speed", "wind_gust_speed"):
            return a.get("wind_speed_unit")
        return None
    if st.entity_id.startswith("climate.") and attr in ("current_temperature", "temperature", SETPOINT_GAP):
        return a.get("temperature_unit") or UnitOfTemperature.CELSIUS
    if attr in ("current_humidity", "humidity"):
        return PERCENTAGE
    return None


def read(hass: HomeAssistant, src: str, max_age: float = 0) -> tuple[float | None, str | None, str | None]:
    """(value, unit, why not): a number now, or the reason there is none (no_entity / unavailable / not_a_number /
    stale)."""
    eid, attr = split_source(src)
    attr = attr or default_attribute(eid)
    st = hass.states.get(eid)
    if st is None:
        return None, None, "no_entity"
    if st.state == STATE_UNKNOWN:
        return None, None, "unknown"  # not heard yet (a KNX sensor right after a restart) — the rules wait for it
    if st.state in NOT_A_STATE:
        return None, None, "unavailable"
    if max_age and (now() - st.last_reported.timestamp()) > max_age * SECONDS_PER_MINUTE:
        return None, None, "stale"
    v = num(st.attributes.get(attr) if attr else st.state)
    if v is None:
        return None, None, "not_a_number"
    return v, unit_of(st, attr), None


def to_celsius(v: float, unit: str | None) -> float | None:
    if unit in (None, "", UnitOfTemperature.CELSIUS):
        return v
    if unit in TEMP_UNITS:
        return TemperatureConverter.convert(v, unit, UnitOfTemperature.CELSIUS)
    return None


def convert(v: float, unit: str | None, to: str | None) -> float | None:
    """`v` in `unit` said in `to`: the same unit, or two temperature units (°F houses — HA's own converter)."""
    if (unit or "") == (to or ""):
        return v
    if unit in TEMP_UNITS and to in TEMP_UNITS:
        return TemperatureConverter.convert(v, unit, to)
    return None


def outdoor(hass: HomeAssistant, owns: Any = None) -> dict[str, Any] | None:
    """The street's temperature: a KNX / Hauflo sensor named so, else a weather entity («по прогнозу»)."""
    words = re.compile("|".join(re.escape(w) for w in _cfg()["outdoor_words"]), re.IGNORECASE)
    reg = er.async_get(hass)
    best: list[tuple[bool, str]] = []
    for st in hass.states.async_all("sensor"):
        if st.attributes.get("device_class") != SensorDeviceClass.TEMPERATURE or st.attributes.get("unit_of_measurement") not in TEMP_UNITS:
            continue
        if not words.search(f"{st.attributes.get('friendly_name') or ''} {st.entity_id}"):
            continue
        ent = reg.async_get(st.entity_id)
        platform = ent.platform if ent else None
        if platform == "knx" or (owns is not None and owns(st.entity_id)):
            best.append((platform != "knx", st.entity_id))
    if best:
        eid = sorted(best)[0][1]
        st = hass.states.get(eid)
        return {"entity_id": eid, "attribute": None, "forecast": False, "name": st.attributes.get("friendly_name") or eid}
    weathers = sorted(s.entity_id for s in hass.states.async_all("weather") if num(s.attributes.get("temperature")) is not None)
    if weathers:
        st = hass.states.get(weathers[0])
        return {"entity_id": weathers[0], "attribute": "temperature", "forecast": True, "name": st.attributes.get("friendly_name") or weathers[0]}
    return None


# --- settings -----------------------------------------------------------------------------------------------------------


def clean(raw: Any, hass: HomeAssistant | None = None) -> dict[str, Any]:
    """One derived value, checked; raises Invalid."""
    c, lim = _cfg(), CANON["limits"]
    if not isinstance(raw, dict):
        raise Invalid("bad", "rules_bad_object")
    rid = raw.get("id")
    if not isinstance(rid, str) or not id_re().match(rid):
        raise Invalid("bad_id", "rules_bad_id", n=lim["id_len"])
    kind = raw.get("kind")
    if kind not in KINDS:
        raise Invalid("bad_kind", "rules_bad_kind", kind=str(kind)[: lim["id_len"]])
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > lim["name_len"] or not name.isprintable():
        raise Invalid("bad_name", "rules_bad_name", n=lim["name_len"])
    sources = raw.get("sources")
    lo, hi = (PAIR, PAIR) if kind in TWO else (1, 1) if kind == "rise" else (PAIR, int(c["max_sources"]))
    ok = isinstance(sources, list) and all(isinstance(s, str) and _SRC_RE.match(s) and not s.startswith(NOT_SOURCES) for s in sources)
    if not ok or not lo <= len(sources) <= hi or len(set(sources)) != len(sources):
        raise Invalid("bad_sources", "rules_bad_sources", lo=lo, hi=hi)
    if hass is not None:
        for s in sources:
            if hass.states.get(split_source(s)[0]) is None:
                raise Invalid("no_entity", "rules_no_entity", entity=s)
    out: dict[str, Any] = {"id": rid, "kind": kind, "name": name.strip(), "sources": sources}
    age = num(raw.get("max_age"))
    out["max_age"] = c["max_age_min"] if age is None else max(0, min(c["max_age_hi_min"], age))
    if kind == "rise":
        w = num(raw.get("window"))
        wlo, whi = c["rise_window_min"]
        out["window"] = c["rise_window_default_min"] if w is None else max(wlo, min(whi, w))
    return out


# --- the value now ----------------------------------------------------------------------------------------------------


class Derived:
    """One derived value: worked out on every source change and every minute."""

    def __init__(self, hass: HomeAssistant, cfg: dict[str, Any]) -> None:
        self.hass = hass
        self.cfg = cfg
        self.value: float | None = None
        self.unit: str | None = None
        self.why: str | None = "starting"
        self.samples: deque[tuple[float, float]] = deque(maxlen=int(_cfg()["samples"]))

    @property
    def device_class(self) -> str | None:
        kind = self.cfg["kind"]
        if kind == "dew_point":
            return SensorDeviceClass.TEMPERATURE
        if kind == "abs_humidity":
            return SensorDeviceClass.ABSOLUTE_HUMIDITY
        if kind in ("min", "max", "avg"):
            eid, attr = split_source(self.cfg["sources"][0])
            st = self.hass.states.get(eid)
            dc = st.attributes.get("device_class") if st is not None and attr is None else None
            return dc or (SensorDeviceClass.TEMPERATURE if self.unit in TEMP_UNITS else None)
        return None

    def update(self) -> None:
        kind, age = self.cfg["kind"], self.cfg["max_age"]
        vals: list[tuple[float, str | None]] = []
        for src in self.cfg["sources"]:
            v, unit, why = read(self.hass, src, age)
            if why:
                self.value, self.why = None, f"{why}:{src}"
                if kind == "rise":
                    self.samples.clear()
                return
            vals.append((v, unit))  # type: ignore[arg-type]
        self.why = None
        if kind in ("dew_point", "abs_humidity"):
            t = to_celsius(*vals[0])
            if t is None or vals[1][1] not in (None, PERCENTAGE):
                self.value, self.why = None, "units"
                return
            self.unit = UnitOfTemperature.CELSIUS if kind == "dew_point" else ABS_HUMIDITY_UNIT
            v = dew_point(t, vals[1][0]) if kind == "dew_point" else abs_humidity(t, vals[1][0])
            self.value = None if v is None else round(v, ROUND_DIGITS)
            self.why = "range" if v is None else None
            return
        if kind == "rise":
            self._rise(*vals[0])
            return
        unit0 = vals[0][1]
        same: list[float] = []
        for v, unit in vals:
            conv = convert(v, unit, unit0)
            if conv is None:
                self.value, self.why = None, "units"
                return
            same.append(conv)
        self.unit = unit0
        if kind == "delta":
            self.value = round(same[0] - same[1], ROUND_DIGITS)
        elif kind == "min":
            self.value = min(same)
        elif kind == "max":
            self.value = max(same)
        else:
            self.value = round(sum(same) / len(same), ROUND_DIGITS)

    def _rise(self, v: float, unit: str | None) -> None:
        c = _cfg()
        t = now()
        self.samples.append((t, v))
        window = self.cfg["window"] * SECONDS_PER_MINUTE
        while self.samples and self.samples[0][0] < t - window:
            self.samples.popleft()
        self.unit = f"{unit or ''}{RATE_SUFFIX}"
        span = self.samples[-1][0] - self.samples[0][0]
        # A part of the window seen first (canon rise_min_share, ≥ rise_min_span_s): two close readings are noise.
        if span < max(c["rise_min_span_s"], window * c["rise_min_share"]):
            self.value, self.why = None, "collecting"
            return
        s = slope_per_hour(list(self.samples))
        self.value = None if s is None else round(s, ROUND_DIGITS)
