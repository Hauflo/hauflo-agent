"""Presence simulation: the pure helpers' edge inputs (what a recorded state asks for, bursts, on-intervals)."""

import datetime as dt

from homeassistant.core import State
from homeassistant.util import dt as dt_util

from custom_components.hauflo import presence as pr


# --- what a recorded state asks for: the edge inputs ----------------------------------------------------------------------


def _st(eid, state, attrs=None, t=None):
    return State(eid, state, attrs or {}, last_updated=t or dt_util.utcnow(), last_changed=t or dt_util.utcnow())


def test_action_of_edges():
    assert pr.action_of("light", _st("light.a", "off"), True, False) == ("off", None)
    assert pr.action_of("light", _st("light.a", "unavailable"), True, False) is None
    assert pr.action_of("light", _st("light.a", "on", {"brightness": 255}), True, False) == ("on", 100)
    assert pr.action_of("light", _st("light.a", "on", {"brightness": 255}), False, False) == ("on", None)  # a light that can't dim
    assert pr.action_of("light", _st("light.a", "on", {"brightness": True}), True, False) == ("on", None)
    assert pr.action_of("light", _st("light.a", "on", {"brightness": 0}), True, False) == ("on", None)
    assert pr.action_of("cover", _st("cover.a", "opening"), False, True) is None  # on its way
    assert pr.action_of("cover", _st("cover.a", "open", {"current_position": 0}), False, True) == ("close", None)
    assert pr.action_of("cover", _st("cover.a", "open", {"current_position": 100}), False, True) == ("open", None)
    assert pr.action_of("cover", _st("cover.a", "open", {"current_position": 40}), False, True) == ("position", 40)
    assert pr.action_of("cover", _st("cover.a", "closed", {"current_position": 40}), False, False) == ("close", None)
    assert pr.action_of("switch", _st("switch.a", "on"), False, False) is None


def test_changes_coalesces_bursts_and_skips_repeats():
    start = dt_util.utcnow()
    s = lambda state, sec, attrs=None: _st("light.a", state, attrs, start + dt.timedelta(seconds=sec))  # noqa: E731
    before = _st("light.a", "on", None, start - dt.timedelta(hours=1))
    states = [s("on", 100), s("unavailable", 110), s("off", 4000), s("off", 4100), s("on", 8000), s("off", 8001)]
    got = pr.changes([before, *states], "light", False, False, start)
    # «on» again is not a change from the state before start; the off/on/off burst within the coalesce window is one «off»
    assert [(a, b) for _, a, b in got] == [("off", None)]
    assert pr.changes([], "light", False, False, start) == []


def test_on_intervals_keeps_the_first_brightness_and_an_open_tail():
    t0 = dt_util.utcnow()
    s = lambda state, m, attrs=None: _st("light.a", state, attrs, t0 + dt.timedelta(minutes=m))  # noqa: E731
    got = pr.on_intervals([s("on", 0), s("on", 5, {"brightness": 128}), s("off", 10), s("on", 20, {"brightness": 255})], t0 + dt.timedelta(minutes=30))
    assert [(round((b - a).total_seconds() / 60), p) for a, b, p in got] == [(10, 50), (10, 100)]
    assert pr.on_intervals([s("on", 40)], t0 + dt.timedelta(minutes=30)) == []  # on only after the end: no interval
