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
