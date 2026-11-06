"""Hauflo's page as HA's sections (п.70): each block its own card in Hauflo's section, the integrator drags them, adds
his own cards, removes ours — a redeploy keeps his order and his cards, puts Hauflo's new blocks after his order (HA's
rule for an added card), what he removed stays removed until asked back; the old layout card moves over whole."""

import copy
import glob
import json
import pathlib
from collections import Counter

import pytest

from custom_components.hauflo.dashmerge import ID, LAYOUT, _child_level, _holds, _kids, _kids_key, plan_and_merge

SECTION = "custom:hauflo-section"
SID = "v/room/s:layout:lights-light.a"
LIGHTS = {"type": "custom:hauflo-lights", "block": "room_lights", "title": "Свет", "entities": ["light.a"], ID: "v/room/lights:light.a"}
COVERS = {"type": "custom:hauflo-covers", "block": "room_covers", "title": "Шторы", "entities": ["cover.a"], ID: "v/room/covers:cover.a"}
SCENES = {"type": "custom:hauflo-scenes", "title": "Сцены", "entities": ["scene.a"], ID: "v/room/scenes:scene.a"}
CLIMATE = {"type": "custom:hauflo-climate", "entity": "climate.a", ID: "v/room/climate:climate.a"}
ALERT = {"type": "custom:hauflo-alert", "alarms": ["binary_sensor.leak"], ID: "v/room/alert:binary_sensor.leak"}
MINE = {"type": "markdown", "content": "Моя заметка"}
LAYOUTS = [{"media": "(min-width: 1100px)", "max": 3}, {"media": "(min-width: 700px)", "max": 2}]


def room(*cards, alert=True):
    secs = [{"type": "grid", ID: "v/room/s:alert:binary_sensor.leak", "cards": [copy.deepcopy(ALERT)]}] if alert else []
    secs.append({"type": SECTION, ID: SID, "column_span": 3, "layouts": LAYOUTS, "cards": [copy.deepcopy(c) for c in cards]})
    return {"views": [{"title": "Гостиная", "path": "room", "type": "sections", ID: "v/room", "sections": secs}]}


def ours(cfg):
    sec = next(s for s in cfg["views"][0]["sections"] if s.get(ID) == SID)
    return [c.get(ID, c.get("type")) for c in sec["cards"]]


def ids(*cards):
    return [c[ID] for c in cards]


def safe(report):
    assert report["lost"] == 0 and report["untouched"] == 0


def test_untouched_page_is_hauflos_new_one():
    base = room(LIGHTS, COVERS, SCENES)
    new = room(SCENES, LIGHTS, COVERS, CLIMATE)  # Hauflo's new order and a new block
    merged, report = plan_and_merge(copy.deepcopy(new), copy.deepcopy(base), base)
    safe(report)
    assert merged == new


def test_his_drag_stays_and_hauflos_update_lands_in_place():
    base = room(LIGHTS, COVERS, SCENES)
    cur = room(COVERS, SCENES, LIGHTS)  # dragged in HA's edit mode
    new = room({**LIGHTS, "title": "Свет зала"}, COVERS, SCENES)
    merged, report = plan_and_merge(new, cur, base)
    safe(report)
    assert ours(merged) == ids(COVERS, SCENES, LIGHTS)
    assert merged["views"][0]["sections"][1]["cards"][2]["title"] == "Свет зала"
    assert not report.get("conflicts")


def test_a_new_block_goes_after_his_order():
    base = room(LIGHTS, COVERS, SCENES)
    cur = room(SCENES, LIGHTS, COVERS)
    new = room(LIGHTS, CLIMATE, COVERS, SCENES)
    merged, report = plan_and_merge(new, cur, base)
    safe(report)
    assert ours(merged) == ids(SCENES, LIGHTS, COVERS, CLIMATE)


def test_his_cards_stay_where_he_put_them():
    base = room(LIGHTS, COVERS, SCENES)
    cur = room(COVERS, MINE, LIGHTS, SCENES, {"type": "tile", "entity": "light.his"})
    merged, report = plan_and_merge(copy.deepcopy(base), cur, base)
    safe(report)
    assert ours(merged) == [COVERS[ID], "markdown", LIGHTS[ID], SCENES[ID], "tile"]


def test_his_order_kept_over_hauflos_new_order():
    base = room(LIGHTS, COVERS, SCENES)
    cur = room(COVERS, LIGHTS, SCENES)
    merged, report = plan_and_merge(room(SCENES, COVERS, LIGHTS), cur, base)
    safe(report)
    assert ours(merged) == ids(COVERS, LIGHTS, SCENES)


def test_a_removed_block_stays_removed_and_comes_back_at_the_end_when_asked():
    base = room(LIGHTS, COVERS, SCENES)
    cur = room(SCENES, LIGHTS)  # dragged and removed the covers
    merged, report = plan_and_merge(copy.deepcopy(base), cur, base)
    safe(report)
    assert ours(merged) == ids(SCENES, LIGHTS)
    assert [r["id"] for r in report["removed"]] == [COVERS[ID]]
    merged, report = plan_and_merge(copy.deepcopy(base), cur, base, restore=[COVERS[ID]])
    assert ours(merged) == ids(SCENES, LIGHTS, COVERS)


def test_his_section_order_on_the_page_stays():
    base = room(LIGHTS, COVERS)
    cur = copy.deepcopy(base)
    secs = cur["views"][0]["sections"]
    secs.reverse()  # the alert section dragged below the blocks
    secs.insert(1, {"type": "grid", "cards": [copy.deepcopy(MINE)]})
    merged, report = plan_and_merge(copy.deepcopy(base), cur, base)
    safe(report)
    assert [s.get(ID, "his") for s in merged["views"][0]["sections"]] == [SID, "his", "v/room/s:alert:binary_sensor.leak"]


def test_a_block_moved_to_his_section_stays_there():
    base = room(LIGHTS, COVERS)
    cur = copy.deepcopy(base)
    secs = cur["views"][0]["sections"]
    covers = secs[1]["cards"].pop()
    secs.append({"type": "grid", "cards": [copy.deepcopy(MINE), covers]})
    merged, report = plan_and_merge(room({**LIGHTS, "title": "Свет 2"}, {**COVERS, "title": "Шторы 2"}), cur, base)
    safe(report)
    assert ours(merged) == ids(LIGHTS)
    his = merged["views"][0]["sections"][2]["cards"]
    assert [c.get(ID, "mine") for c in his] == ["mine", COVERS[ID]] and his[1]["title"] == "Шторы 2"


def test_the_layout_card_page_becomes_sections_his_cards_kept():
    """0.23 → 0.24: Hauflo's layout card goes, its blocks are the section's own cards (the same section id); his cards
    inside the layout and after it in the section stay, after Hauflo's blocks."""
    lay_id = "v/room/layout:lights-light.a"
    base = room(alert=True)
    sec = base["views"][0]["sections"][1]
    sec.update({"type": "grid", "layouts": None})
    del sec["layouts"]
    sec["cards"] = [{"type": LAYOUT, ID: lay_id, "grid_options": {"columns": "full"}, "layouts": [{"media": "(min-width: 700px)", "columns": [[LIGHTS[ID]], [COVERS[ID]]]}],
                     "cards": [copy.deepcopy(LIGHTS), copy.deepcopy(COVERS)]}]
    cur = copy.deepcopy(base)
    cur["views"][0]["sections"][1]["cards"][0]["cards"].insert(1, copy.deepcopy(MINE))
    cur["views"][0]["sections"][1]["cards"].append({"type": "tile", "entity": "light.his"})
    new = room(LIGHTS, COVERS, SCENES)
    merged, report = plan_and_merge(new, cur, base)
    safe(report)
    sec = merged["views"][0]["sections"][1]
    assert sec["type"] == SECTION and sec[ID] == SID and sec["layouts"] == LAYOUTS
    assert ours(merged) == [LIGHTS[ID], COVERS[ID], SCENES[ID], "markdown", "tile"]
    assert not any(c.get("type") == LAYOUT for c in sec["cards"])


GEN = pathlib.Path(__file__).parent / "fixtures/dashboards/generated"


def _cards(cfg):
    """Every plain card (a layout's blocks too), in order."""
    out = []

    def walk(node, level):
        for c in _kids(node, level):
            if isinstance(c, dict):
                cl = _child_level(level, _kids_key(node, level))
                if cl == 3 and not _holds(c, 3):
                    out.append(c)
                if _holds(c, cl):
                    walk(c, cl)

    walk(cfg, 0)
    return out


@pytest.mark.parametrize("house", sorted({pathlib.Path(p).name.rsplit("_", 1)[0] for p in glob.glob(str(GEN / "*_atelier_0.24.0.json"))}))
def test_real_dashboards_layout_to_sections(house):
    """The backend's real dashboards (dashboardTunnel.test.ts) 0.21 (layout card) → 0.24 (sections): untouched — the new
    one; his card in every section and every layout and Hauflo's blocks dragged — all his cards once, his order kept."""
    old, new = (json.loads((GEN / f"{house}_{v}.json").read_text()) for v in ("0.21.0", "0.24.0"))
    merged, report = plan_and_merge(copy.deepcopy(new), copy.deepcopy(old), old)
    safe(report)
    assert merged == new
    cur, n = copy.deepcopy(old), 0
    for v in cur["views"]:
        for s in v.get("sections") or []:
            for c in [s, *[x for x in s.get("cards") or [] if x.get("type") == LAYOUT]]:
                if c.get("cards"):
                    c["cards"].insert(1, {"type": "markdown", "content": f"моё {n}"})
                    n += 1
    merged, report = plan_and_merge(copy.deepcopy(new), cur, old)
    safe(report)
    mine = Counter(c["content"] for c in _cards(merged) if c.get("type") == "markdown")
    assert set(mine) == {f"моё {i}" for i in range(n)} and max(mine.values()) == 1
    assert Counter(c[ID] for c in _cards(merged) if ID in c) == Counter(c[ID] for c in _cards(new))
    # Then he drags the blocks of each of Hauflo's sections (reversed): the next deploy keeps it.
    base, cur = merged, copy.deepcopy(merged)
    for v in cur["views"]:
        for s in v.get("sections") or []:
            if s.get("type") == SECTION:
                s["cards"].reverse()
    again, report = plan_and_merge(copy.deepcopy(new), copy.deepcopy(cur), base)
    safe(report)
    assert again == cur


# --- W1-3: a Hauflo container the integrator dissolved does not come back empty ----------------------------------------
ALERT_S = "v/room/s:alert:binary_sensor.leak"
S1 = "v/room/s:lights:house"
LAYOUT_ID = "v/room/layout:stack"


def two_sections(*second, extra=()):
    secs = [{"type": "grid", ID: ALERT_S, "cards": [copy.deepcopy(ALERT)]}, {"type": SECTION, ID: S1, "cards": [copy.deepcopy(c) for c in second]}]
    return {"views": [{"title": "Дом", "path": "room", "type": "sections", ID: "v/room", "sections": secs + list(extra)}]}


def sec_cards(cfg, i):
    return [c[ID] for c in cfg["views"][0]["sections"][i]["cards"]]


def lights_moved_up():
    """The integrator's edit: lights moved to section 0, section 1 deleted."""
    cur = two_sections(LIGHTS)
    cur["views"][0]["sections"][0]["cards"].append(copy.deepcopy(LIGHTS))
    del cur["views"][0]["sections"][1]
    return cur


@pytest.mark.parametrize("restore", [(), True, [S1]])
def test_a_dissolved_section_is_not_brought_back_empty(restore):
    base, cur = two_sections(LIGHTS), lights_moved_up()
    merged, report = plan_and_merge(copy.deepcopy(base), copy.deepcopy(cur), base, restore=restore)
    safe(report)
    assert merged == cur and not report.get("removed")


def test_new_blocks_of_a_dissolved_section_go_to_the_end_of_where_its_children_went():
    base, cur = two_sections(LIGHTS), lights_moved_up()
    merged, report = plan_and_merge(two_sections(LIGHTS, COVERS), cur, base)
    safe(report)
    assert len(merged["views"][0]["sections"]) == 1 and sec_cards(merged, 0) == ids(ALERT, LIGHTS, COVERS)


def test_removed_blocks_of_a_dissolved_section_come_back_only_when_asked():
    base, cur = two_sections(LIGHTS, COVERS), lights_moved_up()  # covers went with the section
    merged, _ = plan_and_merge(copy.deepcopy(base), copy.deepcopy(cur), base, restore=[])
    assert sec_cards(merged, 0) == ids(ALERT, LIGHTS)
    merged, report = plan_and_merge(copy.deepcopy(base), copy.deepcopy(cur), base, restore=[S1])
    assert sec_cards(merged, 0) == ids(ALERT, LIGHTS, COVERS) and report["restored"] == 1


def test_children_moved_to_different_sections_go_where_most_of_them_went():
    third = {"type": "grid", ID: "v/room/s:x", "cards": [copy.deepcopy(SCENES)]}
    base = two_sections(LIGHTS, COVERS, CLIMATE, extra=[third])
    cur = copy.deepcopy(base)
    secs = cur["views"][0]["sections"]
    secs[2]["cards"] += [copy.deepcopy(LIGHTS), copy.deepcopy(COVERS)]
    secs[0]["cards"].append(copy.deepcopy(CLIMATE))
    del secs[1]
    new = two_sections(LIGHTS, COVERS, CLIMATE, extra=[third])
    new["views"][0]["sections"][1]["cards"].append({**MINE, ID: "v/room/new"})
    merged, report = plan_and_merge(new, cur, base)
    safe(report)
    assert [s[ID] for s in merged["views"][0]["sections"]] == [ALERT_S, "v/room/s:x"] and sec_cards(merged, 1)[-1] == "v/room/new"


def test_a_dissolved_section_whose_children_went_to_his_section_is_not_empty_either():
    base = two_sections(LIGHTS)
    cur = copy.deepcopy(base)
    cur["views"][0]["sections"][1] = {"type": "grid", "cards": [copy.deepcopy(MINE), copy.deepcopy(LIGHTS)]}  # his section now holds ours
    merged, report = plan_and_merge(copy.deepcopy(base), copy.deepcopy(cur), base, restore=True)
    safe(report)
    assert merged == cur


def test_a_layout_stack_dissolved_is_not_brought_back_empty():
    def page(*inside):
        stack = {"type": LAYOUT, ID: LAYOUT_ID, "cards": [copy.deepcopy(c) for c in inside]}
        return {"views": [{"title": "Дом", "path": "room", "type": "sections", ID: "v/room", "sections": [
            {"type": "grid", ID: "v/room/s:a", "cards": [copy.deepcopy(ALERT), stack]}, {"type": "grid", ID: "v/room/s:b", "cards": []}]}]}

    base = page(LIGHTS)
    cur = page()
    cur["views"][0]["sections"][0]["cards"] = [copy.deepcopy(ALERT)]  # the stack removed ...
    cur["views"][0]["sections"][1]["cards"] = [copy.deepcopy(LIGHTS)]  # ... lights put into the next section first
    merged, report = plan_and_merge(page(LIGHTS, COVERS), cur, base, restore=True)
    safe(report)
    flat = json.dumps(merged)
    assert LAYOUT_ID not in flat and flat.count(LIGHTS[ID]) == 1 and sec_cards(merged, 1) == ids(LIGHTS, COVERS)


def test_a_section_moved_whole_to_another_page_stays_there():
    base = two_sections(LIGHTS)
    base["views"].append({"title": "Ещё", "path": "more", "type": "sections", ID: "v/more", "sections": []})
    cur = copy.deepcopy(base)
    cur["views"][1]["sections"].append(cur["views"][0]["sections"].pop())
    merged, report = plan_and_merge(copy.deepcopy(base), cur, base, restore=True)
    safe(report)
    assert merged == cur


def test_a_fully_deleted_section_comes_back_when_asked_with_its_blocks():
    base = two_sections(LIGHTS, COVERS)
    cur = copy.deepcopy(base)
    del cur["views"][0]["sections"][1]  # no child alive elsewhere: nothing dissolved, it was removed
    merged, report = plan_and_merge(copy.deepcopy(base), cur, base)
    assert [s[ID] for s in merged["views"][0]["sections"]] == [ALERT_S] and report["removed"][0]["id"] == S1
    merged, report = plan_and_merge(copy.deepcopy(base), cur, base, restore=[S1])
    assert sec_cards(merged, 1) == ids(LIGHTS, COVERS)
