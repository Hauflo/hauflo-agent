"""0.34 п.2 — the one counter of open alarms (alarms_now.py): the cards, Hauflo's monitoring, the object page and the bot
count and list only it. Pure cases first, then the agent's GET /api/hauflo/alarms `_summary` and the status `alarmsNow`."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.hauflo import alarms_now


def _rule(eid, *, key=None, critical=True, acked=None, nodata=False, returned=False, since="2026-11-11T08:00:00+00:00"):
    return {"key": key or f"r|{eid}", "entityId": eid, "name": eid, "title": "Протечка", "since": since, "ackedBy": acked, "critical": critical, **({"noData": True} if nodata else {}), **({"returned": True} if returned else {})}


def test_empty_is_zero():
    s = alarms_now.summary([], [])
    assert s == {"critical": 0, "normal": 0, "acked": 0, "total": 0, "returned": 0, "lost": 0, "items": []}


def test_one_entity_counted_once_rule_and_fault():
    """Two rules on one sensor and its problem by the policy: ONE alarm (the bot said 3, the card 2)."""
    s = alarms_now.summary([_rule("binary_sensor.w", key="a|binary_sensor.w"), _rule("binary_sensor.w", key="b|binary_sensor.w")], [{"entity": "binary_sensor.w", "name": "W", "house": "alarm", "offline": False}])
    assert (s["total"], s["critical"], s["normal"], s["acked"]) == (1, 1, 0, 0)
    assert s["items"][0]["keys"] == ["a|binary_sensor.w", "b|binary_sensor.w"] and s["items"][0]["source"] == "rule"


def test_acked_only_when_every_rule_alarm_is():
    one = alarms_now.summary([_rule("x.a", key="a|x.a", acked="Мама"), _rule("x.a", key="b|x.a")], [])
    assert (one["critical"], one["acked"]) == (1, 0)
    both = alarms_now.summary([_rule("x.a", key="a|x.a", acked="Мама"), _rule("x.a", key="b|x.a", acked="Папа")], [])
    assert (both["critical"], both["acked"], both["items"][0]["ackedBy"]) == (0, 1, "Мама")


def test_house_problem_without_rule_counts_and_has_its_own_key():
    s = alarms_now.summary([], [{"entity": "binary_sensor.zont", "name": "Zont", "house": "fault", "offline": False}, {"entity": "binary_sensor.smoke", "name": "Дым", "house": "alarm", "offline": False}])
    assert (s["total"], s["critical"], s["normal"], s["acked"]) == (2, 1, 1, 0)
    assert s["items"][0]["entity"] == "binary_sensor.smoke" and all(i["source"] == "house" and not i["acked"] for i in s["items"])
    # 0.34 fix п.2: «Принять» on a house problem — its own key, so «Принять все» counts it too
    assert [i["keys"] for i in s["items"]] == [["_house|binary_sensor.smoke"], ["_house|binary_sensor.zont"]]


def test_house_problem_accepted_moves_the_counter_open_stays():
    faults = [{"entity": "binary_sensor.zont", "name": "Zont", "house": "fault", "offline": False}, {"entity": "binary_sensor.smoke", "name": "Дым", "house": "alarm", "offline": False}]
    s = alarms_now.summary([], faults, None, {"binary_sensor.zont": "Мама"})
    assert (s["total"], s["critical"], s["normal"], s["acked"]) == (2, 1, 0, 1)
    z = next(i for i in s["items"] if i["entity"] == "binary_sensor.zont")
    assert z["acked"] and z["ackedBy"] == "Мама" and z["state"] == "open"
    # a lost (offline) house signal has no key and is never counted as accepted
    lost = alarms_now.summary([], [{"entity": "binary_sensor.x", "name": "X", "house": "fault", "offline": True}], None, {"binary_sensor.x": "Мама"})
    assert lost["items"][0]["keys"] == [] and lost["acked"] == 0 and lost["lost"] == 1
    # a rule alarm of it keeps the rule's keys and acceptance: the house key is not added
    both = alarms_now.summary([_rule("binary_sensor.zont", key="a|binary_sensor.zont")], faults[:1], None, {"binary_sensor.zont": "Мама"})
    assert both["items"][0]["keys"] == ["a|binary_sensor.zont"] and both["acked"] == 0


def test_lost_and_returned_told_apart_never_open():
    s = alarms_now.summary([_rule("x.nd", nodata=True), _rule("x.back", returned=True)], [{"entity": "x.off", "name": "Off", "house": "alarm", "offline": True}])
    assert (s["total"], s["lost"], s["returned"]) == (0, 2, 1)
    assert [i["state"] for i in s["items"]] == ["returned", "lost", "lost"]
    # A lost sensor's keys are no accept keys (nothing to accept); a returned one's are (the alert card's «Принять»).
    assert next(i for i in s["items"] if i["entity"] == "x.nd")["keys"] == [] and next(i for i in s["items"] if i["entity"] == "x.back")["keys"] == ["r|x.back"]


def test_order_critical_then_open_then_oldest():
    s = alarms_now.summary([_rule("x.n", critical=False, since="2026-11-11T07:00:00+00:00"), _rule("x.c2", since="2026-11-11T09:00:00+00:00"), _rule("x.c1", since="2026-11-11T08:00:00+00:00"), _rule("x.ack", acked="Мама", since="2026-11-11T06:00:00+00:00")], [])
    assert [i["entity"] for i in s["items"]] == ["x.c1", "x.c2", "x.n", "x.ack"]
    assert (s["critical"], s["normal"], s["acked"], s["total"]) == (2, 1, 1, 4)


def test_cap_keeps_counts_whole():
    s = alarms_now.summary([_rule(f"x.{i}") for i in range(7)], [], cap=3)
    assert s["total"] == 7 and len(s["items"]) == 3 and s["more"] == 4


def test_garbage_ignored():
    s = alarms_now.summary([{"entityId": None}, {"key": "x"}], [{"entity": 3}, {}])
    assert s["total"] == 0 and s["items"] == []
