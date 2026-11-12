"""0.32 п.6: the integrator's rows of a Hauflo card (`items`, `auto`, `order` — the card editor's «Состав блока») are his,
field by field: a re-apply keeps them, they alone are no conflict and don't freeze the card (Hauflo's new `others`
still applies), both editing the same key of Hauflo's is a conflict as before; a read through the tunnel never shows
them (foreign entity ids); the tunnel's check takes them only in their form."""

import copy
import json
from unittest.mock import patch

import custom_components.hauflo as agent_mod
from custom_components.hauflo import dashmerge
from custom_components.hauflo.dashmerge import ID, plan_and_merge

AREA = "v/kitchen/s:other/c:area"
ROWS = {
    "items": [
        {"entity": "switch.shelly_kettle", "name": "Чайник"},
        {"entity": "sensor.shelly_power", "hidden": True},
        {"entity": "light.hall_strip", "name": "Лента из прихожей", "block": "control"},
    ],
    "order": ["switch.shelly_kettle", "lights"],
    "auto": True,
}


def area_card(others=True, block="control"):
    return {"type": "custom:hauflo-area", "area": "kitchen", "block": block, ID: AREA, **({"others": True} if others else {})}


def dash(card):
    return {"views": [{"title": "Кухня", "path": "kitchen", "type": "sections", ID: "v/kitchen", "sections": [
        {"type": "grid", ID: "v/kitchen/s:other", "cards": [{"type": "heading", "heading": "Прочее", ID: "v/kitchen/s:other/head"}, card]}]}]}


def area_of(cfg):
    return cfg["views"][0]["sections"][0]["cards"][1]


def with_rows(cfg):
    out = copy.deepcopy(cfg)
    area_of(out).update(copy.deepcopy(ROWS))
    return out


def test_rows_survive_a_re_apply_of_the_same_config():
    base = dash(area_card())
    cur = with_rows(base)
    merged, plan = plan_and_merge(copy.deepcopy(base), cur, base)
    assert area_of(merged) == area_of(cur)
    assert plan["conflicts"] == [] and plan["lost"] == 0 and plan["untouched"] == 0


def test_hauflos_change_of_its_own_keys_applies_under_his_rows():
    base = dash(area_card(others=True))
    cur = with_rows(base)
    new = dash(area_card(others=False, block="readings"))
    merged, plan = plan_and_merge(new, cur, base)
    got = area_of(merged)
    assert "others" not in got and got["block"] == "readings"
    assert {k: got[k] for k in ROWS} == ROWS
    assert plan["conflicts"] == [] and plan["untouched"] == 0 and plan["lost"] == 0
    assert plan["updates"]["changed"] == 1
    assert [p["changed"] for p in plan["pages"]] == [1]


def test_both_editing_the_same_key_of_hauflos_is_a_conflict_and_his_rows_stay_either_way():
    base = dash(area_card(block="control"))
    cur = with_rows(base)
    area_of(cur)["block"] = "readings"
    new = dash(area_card(block="diagnostics"))
    merged, plan = plan_and_merge(new, cur, base)
    assert area_of(merged)["block"] == "readings"
    assert [c["kept"] for c in plan["conflicts"]] == ["yours"]
    merged, plan = plan_and_merge(new, cur, base, take=[AREA])
    got = area_of(merged)
    assert got["block"] == "diagnostics" and {k: got[k] for k in ROWS} == ROWS
    assert plan["taken"] == 1 and plan["untouched"] == 0


def test_the_cloud_cant_write_his_rows_away():
    """Whatever the cloud sends under these keys, the card keeps the rows HA holds."""
    base = dash(area_card())
    cur = with_rows(base)
    new = dash({**area_card(), "items": [{"entity": "light.cloud_said"}], "order": ["covers"], "auto": False})
    merged, _ = plan_and_merge(new, cur, base)
    assert {k: area_of(merged)[k] for k in ROWS} == ROWS


def test_a_card_hauflo_drops_keeps_his_rows_as_his():
    base = dash(area_card())
    cur = with_rows(base)
    new = copy.deepcopy(base)
    new["views"][0]["sections"][0]["cards"].pop()
    merged, plan = plan_and_merge(new, cur, base)
    kept = [c for c in merged["views"][0]["sections"][0]["cards"] if c.get("type") == "custom:hauflo-area"]
    assert len(kept) == 1 and kept[0]["items"] == ROWS["items"] and ID not in kept[0]
    assert plan["lost"] == 0


def test_rows_never_leave_the_house_on_a_read():
    base = dash(area_card())
    cur = with_rows(base)
    read = dashmerge.redact(copy.deepcopy(cur), base, b"k" * 32)
    txt = json.dumps(read, ensure_ascii=False)
    assert area_of(read) == area_card()
    for secret in ("switch.shelly_kettle", "sensor.shelly_power", "light.hall_strip", "Чайник", "items", "order"):
        assert secret not in txt
    # His edit of Hauflo's own key reads as a placeholder — still nothing of his rows.
    area_of(cur)["block"] = "readings"
    read = dashmerge.redact(copy.deepcopy(cur), base, b"k" * 32)
    assert area_of(read)["type"] == dashmerge.LOCAL and "switch.shelly_kettle" not in json.dumps(read)


def test_not_a_hauflo_card_or_a_layout_keeps_the_old_whole_card_rule():
    tile = {"type": "tile", "entity": "light.kitchen", ID: AREA}
    base = dash(tile)
    cur = copy.deepcopy(base)
    area_of(cur)["order"] = ["x"]
    new = dash({**tile, "name": "Кухня"})
    merged, plan = plan_and_merge(new, cur, base)
    assert area_of(merged) == area_of(cur) and [c["kept"] for c in plan["conflicts"]] == ["yours"]


def test_the_tunnel_check_takes_rows_only_in_their_form():
    ok = agent_mod._lovelace_ok

    def cfg(**rows):
        return dash({**area_card(), **rows})

    with patch.object(agent_mod, "_hauflo_entity", return_value=True):
        assert ok(None, cfg(**ROWS), set(), set())
        assert ok(None, cfg(items=[], auto=False, order=[]), set(), set())
        bad = [
            {"items": None},
            {"items": "light.a"},
            {"items": [None]},
            {"items": [{"name": "без сущности"}]},
            {"items": [{"entity": "Light.A"}]},
            {"items": [{"entity": "light.a", "unknown": 1}]},
            {"items": [{"entity": "light.a", "name": "x" * 500}]},
            {"items": [{"entity": "light.a", "hidden": "yes"}]},
            {"items": [{"entity": "light.a", "block": "kitchen"}]},
            {"items": [{"entity": "light.a"}] * 1000},
            {"auto": "false"},
            {"order": "lights"},
            {"order": ["light.a, light.b"]},
            {"order": [1]},
        ]
        for rows in bad:
            assert not ok(None, cfg(**rows), set(), set()), rows
    # A foreign entity in a row: the walk refuses it like anywhere else in a dashboard the cloud sends.
    with patch.object(agent_mod, "_hauflo_entity", return_value=False), patch.object(agent_mod, "_shown_protected", return_value=False):
        assert not ok(None, cfg(items=[{"entity": "light.foreign"}]), set(), set())
        assert ok(None, cfg(items=[{"entity": "sensor.read_only"}]), set(), set())


# Р78: a numeric row's regulator (view / min / max / step) — the integrator's like any key of his rows: kept on a re-apply
# and under Hauflo's own change, taken by the tunnel only in its form (a view of the canon, finite numbers).
REG_ROWS = {"items": [{"entity": "fan.supply", "view": "stepper", "min": 10, "max": 30, "step": 5}, {"entity": "number.damper", "view": "slider", "min": 2.5, "max": 7}]}


def test_the_regulator_of_a_row_survives_a_re_apply_and_hauflos_change():
    base = dash(area_card())
    cur = copy.deepcopy(base)
    area_of(cur).update(copy.deepcopy(REG_ROWS))
    merged, plan = plan_and_merge(copy.deepcopy(base), cur, base)
    assert area_of(merged)["items"] == REG_ROWS["items"]
    assert plan["conflicts"] == [] and plan["lost"] == 0
    merged, _ = plan_and_merge(dash(area_card(others=False, block="readings")), cur, base)
    assert area_of(merged)["items"] == REG_ROWS["items"] and area_of(merged)["block"] == "readings"


def test_the_tunnel_takes_the_regulator_only_in_its_form():
    ok = agent_mod._lovelace_ok

    def cfg(row):
        return dash({**area_card(), "items": [{"entity": "fan.supply", **row}]})

    with patch.object(agent_mod, "_hauflo_entity", return_value=True):
        assert ok(None, dash({**area_card(), **REG_ROWS}), set(), set())
        for view in ("auto", "stepper", "slider", "buttons"):
            assert ok(None, cfg({"view": view}), set(), set()), view
        assert ok(None, cfg({"min": 0, "max": 100.0, "step": 0.5}), set(), set())
        for row in ({"view": "knob"}, {"view": 1}, {"min": "10"}, {"max": True}, {"step": float("nan")}, {"min": float("inf")}, {"max": 1e12}, {"step": None}):
            assert not ok(None, cfg(row), set(), set()), row


class _State:
    def __init__(self, attributes):
        self.attributes = attributes


class _Hass:
    """Only `states.get` — the regulator's device range."""

    def __init__(self, states):
        self.states = self
        self._s = states

    def get(self, entity_id):
        return self._s.get(entity_id)


def test_the_tunnel_refuses_a_regulator_out_of_sense_with_its_code():
    """Р78 accept 4A: min ≥ max, a step ≤ 0, a step over max − min, a bound outside the device's range — refused with the
    code the deploy shows the integrator (`<entity> regulator <code>`), the same rules as the card editor's regProblems."""
    why = agent_mod._lovelace_refusal

    def cfg(row, eid="fan.supply"):
        return dash({**area_card(), "items": [{"entity": eid, **row}]})

    hass = _Hass({
        "fan.supply": _State({"supported_features": 1, "percentage": 20}),
        "fan.onoff": _State({"supported_features": 0}),
        "light.l": _State({"supported_color_modes": ["brightness"]}),
        "number.d": _State({"min": 1, "max": 10, "step": 1}),
    })
    with patch.object(agent_mod, "_hauflo_entity", return_value=True):
        for row, code in (
            ({"min": 30, "max": 10}, "reg_min_max"),
            ({"min": 30, "max": 30}, "reg_min_max"),
            ({"min": 10, "max": 30, "step": 0}, "reg_step"),
            ({"min": 10, "max": 30, "step": -5}, "reg_step"),
            ({"min": 10, "max": 30, "step": 25}, "reg_step_range"),
            ({"min": 10, "max": 300}, "reg_out_of_range"),
            ({"min": -1}, "reg_out_of_range"),
            ({"step": 150}, "reg_step_range"),
        ):
            got = why(hass, cfg(row), set(), set())
            assert got and got.endswith(f"fan.supply regulator {code}"), (row, got)
        assert "light.l regulator reg_out_of_range" in why(hass, cfg({"min": 0}, "light.l"), set(), set())
        assert "number.d regulator reg_out_of_range" in why(hass, cfg({"max": 11}, "number.d"), set(), set())
        # In sense — taken; no device range known (no state, no regulator feature) — only min / max / step among themselves.
        for row, eid in (({"min": 10, "max": 30, "step": 5}, "fan.supply"), ({"min": 2, "max": 5}, "number.d"), ({"min": 500, "max": 900}, "fan.onoff"), ({"min": 500}, "fan.gone"), ({"view": "stepper"}, "fan.supply")):
            assert why(hass, cfg(row, eid), set(), set()) is None, (row, eid)
        assert "fan.gone regulator reg_min_max" in why(hass, cfg({"min": 9, "max": 1}, "fan.gone"), set(), set())
        assert "regulator reg_step" in why(None, cfg({"step": 0}), set(), set()), "no hass: the step still checked"
