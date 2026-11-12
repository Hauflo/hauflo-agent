"""399-42 «События»: an incident's value graph — one numeric entity's values from HA's own recorder over a bounded
window, for Hauflo's page (the hour before the alarm → its end / now). Never our own history: the recorder holds it.

Read only, admin only, through the tunnel (tunnel.json). What may be read is what the tunnel may read anyway — the
agent's `reads` gate (__init__._cloud_reads: no person / zone / tracker / phone, no presence sensor Hauflo doesn't own,
only get_states' attributes) — and only a numeric value of the domains in tuning.json `history.domains` (a climate's
current temperature, a humidifier's humidity — `history.attrs`). The answer is thinned to `max_points` (each bucket's
min and max kept in time order: a spike survives), the window clipped to `max_window_h`."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .derived import num
from .signals import ENTITY_ID
from .tuning import TUNING

T = TUNING["history"]
MAX_POINTS = int(T["max_points"])
MAX_WINDOW = timedelta(hours=T["max_window_h"])
ATTRS: dict[str, str] = T["attrs"]
DOMAINS = frozenset(T["domains"])

Point = tuple[float, float]


def attribute_of(entity_id: str) -> str | None:
    """The value a graph shows: the state, or the domain's measured attribute (a climate's current temperature)."""
    return ATTRS.get(entity_id.split(".")[0])


def unit_of(hass: HomeAssistant, entity_id: str, attribute: str | None) -> str | None:
    """The state's own unit; a measured attribute's by its kind (HA's temperature unit, humidity in %)."""
    if attribute is None:
        st = hass.states.get(entity_id)
        return st.attributes.get("unit_of_measurement") if st else None
    return hass.config.units.temperature_unit if "temperature" in attribute else "%"


def window(start: str | None, end: str | None, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    """[start, end) as asked (ISO or epoch ms), end never in the future, the span never above MAX_WINDOW (its last part
    kept — the alarm's end side). None — unreadable or empty."""
    now = now or dt_util.utcnow()

    def at(v: str | None) -> datetime | None:
        if not v:
            return None
        if v.isdigit():
            return dt_util.utc_from_timestamp(int(v) / 1000)
        got = dt_util.parse_datetime(v)
        return dt_util.as_utc(got) if got else None

    a, b = at(start), at(end) or now
    if a is None or b is None:
        return None
    b = min(b, now)
    a = max(a, b - MAX_WINDOW)
    return (a, b) if a < b else None


def points_of(states: list[Any], attribute: str | None) -> list[Point]:
    """(epoch ms, value) of the numeric ones, in time order; `unavailable` / text dropped (a gap in the line)."""
    out: list[Point] = []
    for s in states:
        if isinstance(s, State):
            raw = s.attributes.get(attribute) if attribute else s.state
            when = s.last_updated if attribute else s.last_changed
        elif isinstance(s, dict):  # a minimal response
            raw = (s.get("attributes") or {}).get(attribute) if attribute else s.get("state")
            when = dt_util.parse_datetime(str(s.get("last_updated" if attribute else "last_changed") or ""))
        else:
            continue
        v = num(raw)
        if v is not None and when is not None:
            out.append((when.timestamp() * 1000, v))
    out.sort(key=lambda p: p[0])
    return out


def thin(points: list[Point], a_ms: float, b_ms: float, limit: int = MAX_POINTS) -> list[Point]:
    """At most `limit` points: the window cut in limit/2 buckets, each keeps its min and max in their time order."""
    if len(points) <= limit:
        return points
    n = max(1, limit // 2)
    size = (b_ms - a_ms) / n or 1
    buckets: dict[int, list[Point]] = {}
    for p in points:
        buckets.setdefault(min(n - 1, max(0, int((p[0] - a_ms) // size))), []).append(p)
    out: list[Point] = []
    for i in sorted(buckets):
        got = buckets[i]
        lo = min(got, key=lambda p: p[1])
        hi = max(got, key=lambda p: p[1])
        out.extend(sorted({lo, hi}, key=lambda p: p[0]))
    return out


async def read(hass: HomeAssistant, entity_id: str, a: datetime, b: datetime) -> dict[str, Any]:
    """The graph's data: {entity, attribute, unit, points: [[ms, value]], why} — `why` says why there is no line."""
    attribute = attribute_of(entity_id)
    unit = unit_of(hass, entity_id, attribute)
    base = {"entity": entity_id, "attribute": attribute, "unit": unit, "from": a.timestamp() * 1000, "to": b.timestamp() * 1000}
    if "recorder" not in hass.config.components:
        return {**base, "points": [], "why": "no_recorder"}
    from homeassistant.components.recorder import get_instance, history  # noqa: PLC0415 — the recorder is optional

    got = await get_instance(hass).async_add_executor_job(
        lambda: history.get_significant_states(hass, a, b, [entity_id], None, True, False, False, attribute is None)
    )
    pts = points_of(got.get(entity_id, []), attribute)
    if not pts:
        return {**base, "points": [], "why": "no_history"}
    # The value before the window (include_start_time_state) is drawn from the window's start.
    pts = [(max(t, base["from"]), v) for t, v in pts]
    return {**base, "points": [[round(t), v] for t, v in thin(pts, base["from"], base["to"])], "why": None}


class HistoryView(HomeAssistantView):
    """GET ?entity=<id>&start=<iso|ms>&end=<iso|ms> → one numeric entity's values (read()). Admins only (Hauflo through
    its tunnel); an entity the tunnel may not read, a domain without a number — 403 / 400, never an empty «no data»."""

    url = "/api/hauflo/history"
    name = "api:hauflo:history"
    requires_auth = True

    def __init__(self, reads: Callable[[str, str | None], bool]) -> None:
        self.reads = reads

    async def get(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        hass = request.app[KEY_HASS]
        q = request.query
        entity = q.get("entity", "")
        if not ENTITY_ID.match(entity) or entity.split(".")[0] not in DOMAINS:
            return self.json_message("entity: a numeric sensor / climate", 400)
        span = window(q.get("start"), q.get("end"))
        if span is None:
            return self.json_message("start / end", 400)
        if not self.reads(entity, attribute_of(entity)):
            return self.json_message("not readable", 403)
        return self.json(await read(hass, entity, *span))
