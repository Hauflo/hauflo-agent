"""A statistic of a value over time windows for the value rules (rules.py): «среднее / мин / макс / сумма за час · день ·
неделю», N periods in a row («5 дней подряд среднесуточная ниже +8 °C» — the season, owner 30.10).

Never our own history — HA's recorder holds it:
  * a sensor with a state_class: the recorder's long-term statistics (`statistics_during_period`, hour / day / week,
    local days and ISO weeks as HA reduces them); mean / min / max of a measurement, the period's change («сумма») of a
    total (an energy meter, a run-hours counter);
  * a weather entity (no statistics): its recorded states' attribute, mean / min / max per period from at least
    `history_min_samples` samples;
  * an on / off entity (a pump, a boiler): «сумма» = hours on in the period, from its recorded state changes — within the
    recorder's keep (`history_keep_days`).
A period without data (no row, too few samples, mostly unavailable) is None — «нет данных», never a guess and never a
trigger. Only complete periods count: the one running now never does. Read in the recorder's executor, when a period
closes (+ `compile_delay_s`: HA compiles the hour's statistics just after it), at start and on a save — never polled.

Numbers: rules_canon.json «window»; refusals: translations («exceptions», rules_window_…).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .canon import CANON
from .derived import NOT_A_STATE, SECONDS_PER_HOUR, Invalid, num

STATISTICS, HISTORY, ON_TIME = "statistics", "history", "on_time"
SUM, CHANGE = "sum", "change"  # «сумма» of a total = HA's per-period «change»
ONE_SECOND = timedelta(seconds=1)


def _w() -> dict[str, Any]:
    return CANON["window"]


# --- periods (local time, DST-safe: stepped in UTC, floored locally) -------------------------------------------------


def period_start(t: datetime, period: str) -> datetime:
    """The start of the local hour / day / ISO week (Monday) `t` is in."""
    local = dt_util.as_local(t)
    if period == "hour":
        return local.replace(minute=0, second=0, microsecond=0)
    day = dt_util.start_of_local_day(local)
    if period == "day":
        return day
    return dt_util.start_of_local_day((day - timedelta(days=day.weekday())).date())


def next_start(t: datetime, period: str) -> datetime:
    """When the period `t` is in closes."""
    s = period_start(t, period)
    step = timedelta(seconds=_w()["periods"][period])
    # A day / week across a clock change is 23 / 25 h: aim a third of a period past its nominal end, then floor.
    return period_start(dt_util.as_utc(s) + step + step / len(_w()["periods"]), period)


def starts(n: int, period: str, now: datetime | None = None) -> list[datetime]:
    """The last `n` complete periods' starts, oldest first (the running one is not over — never counted)."""
    cur = period_start(now or dt_util.now(), period)
    out: list[datetime] = []
    for _ in range(n):
        cur = period_start(dt_util.as_utc(cur) - ONE_SECOND, period)
        out.append(cur)
    return out[::-1]


# --- which source can say the statistic ----------------------------------------------------------------------------


def source_of(hass: HomeAssistant, entity_id: str, attribute: str | None, stat: str) -> str:
    """statistics / history / on_time for this input and statistic; raises Invalid with the reason (RU + EN texts)."""
    w = _w()
    domain = entity_id.split(".")[0]
    st = hass.states.get(entity_id)
    if st is None:
        raise Invalid("no_entity", "rules_no_entity", entity=entity_id)
    if domain in w["on_time_domains"] and attribute is None:
        if stat != SUM:
            raise Invalid("bad_window", "rules_window_on_time", entity=entity_id)
        return ON_TIME
    if domain in w["history_domains"]:
        if stat not in w["history_stats"]:
            raise Invalid("bad_window", "rules_window_stat_kind", entity=entity_id, kind=domain)
        return HISTORY
    sc = st.attributes.get("state_class")
    if domain != "sensor" or attribute is not None or not sc:
        raise Invalid("no_statistics", "rules_window_no_stats", entity=entity_id)
    if sc not in w["state_classes"][stat]:
        raise Invalid("bad_window", "rules_window_stat_kind", entity=entity_id, kind=sc)
    return STATISTICS


def history_span_ok(period: str, n: int) -> bool:
    """A source read from the recorder's states: the whole window inside its keep."""
    w = _w()
    return n * w["periods"][period] <= w["history_keep_days"] * w["periods"]["day"]


# --- the values ---------------------------------------------------------------------------------------------------


def _key(t: Any) -> float | None:
    when = dt_util.utc_from_timestamp(t) if isinstance(t, (int, float)) else t
    return when.timestamp() if isinstance(when, datetime) else None


async def values(hass: HomeAssistant, entity_id: str, attribute: str | None, win: dict[str, Any], now: datetime | None = None) -> tuple[list[float | None], list[float], str | None]:
    """(the last n complete periods' values oldest first — None = no data, their start timestamps, why none at all)."""
    period, stat = win["period"], win["stat"]
    n = max(int(win["n"]), int(win.get("n_back") or win["n"]))
    first = starts(n, period, now)
    keys = [s.timestamp() for s in first]
    empty: list[float | None] = [None] * n
    if "recorder" not in hass.config.components:
        return empty, keys, "no_recorder"
    from homeassistant.components.recorder import get_instance  # noqa: PLC0415 — the recorder is optional

    end = period_start(now or dt_util.now(), period)
    src = win.get("source") or source_of(hass, entity_id, attribute, stat)
    run = get_instance(hass).async_add_executor_job
    if src == STATISTICS:
        from homeassistant.components.recorder import statistics  # noqa: PLC0415

        kind = CHANGE if stat == SUM else stat
        got = await run(lambda: statistics.statistics_during_period(hass, first[0], end, {entity_id}, period, None, {kind}))
        by = {_key(r.get("start")): num(r.get(kind)) for r in got.get(entity_id, [])}
        vals = [by.get(k) for k in keys]
        return vals, keys, None if any(v is not None for v in vals) else "no_statistics"
    from homeassistant.components.recorder import history  # noqa: PLC0415

    if src == HISTORY:
        got = await run(lambda: history.get_significant_states(hass, first[0], end, [entity_id], significant_changes_only=False, include_start_time_state=False))
        per: dict[float, list[float]] = {}
        for s in got.get(entity_id, []):
            v = num(getattr(s, "attributes", {}).get(attribute)) if attribute else num(getattr(s, "state", None))
            if v is not None:
                per.setdefault(period_start(s.last_updated, period).timestamp(), []).append(v)
        vals = [_reduce(per.get(k, []), stat) for k in keys]
        return vals, keys, None if any(v is not None for v in vals) else "no_history"
    got = await run(lambda: history.state_changes_during_period(hass, first[0], end, entity_id, include_start_time_state=True))
    vals = _on_hours(got.get(entity_id, []), [*keys, end.timestamp()])
    return vals, keys, None if any(v is not None for v in vals) else "no_history"


def _reduce(samples: list[float], stat: str) -> float | None:
    if len(samples) < _w()["history_min_samples"]:
        return None
    if stat == "min":
        return min(samples)
    if stat == "max":
        return max(samples)
    return sum(samples) / len(samples)


def _on_hours(states: list[Any], edges: list[float]) -> list[float | None]:
    """Hours on in each [edges[i], edges[i+1]); a period mostly unknown (no state yet, unavailable) is None."""
    spans = [(s.last_changed.timestamp(), s.state) for s in states]
    out: list[float | None] = []
    for a, b in zip(edges, edges[1:]):
        on = gap = 0.0
        for i, (t0, state) in enumerate(spans):
            t1 = spans[i + 1][0] if i + 1 < len(spans) else b
            lo, hi = max(a, t0), min(b, t1)
            if hi <= lo:
                continue
            if state == STATE_ON:
                on += hi - lo
            elif state in NOT_A_STATE:
                gap += hi - lo
        known_from = spans[0][0] if spans else b
        gap += max(0.0, min(b, known_from) - a)
        out.append(None if gap > (b - a) * _w()["history_max_gap_share"] else on / SECONDS_PER_HOUR)
    return out
