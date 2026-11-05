"""Every block once (owner 05.11 п.49) and a deploy that changes exactly what its plan says (п.54): Hauflo's page layout
card holds a page's blocks, the old layout (each block up to three times, a section per screen width) and the old ids
(on a section and on words) are moved over by the agent without losing anything of the integrator's — what he removed
stays removed, what he edited stays his, his cards stay next to the block they followed."""

import copy
import glob
import json
import pathlib
from collections import Counter

import pytest
from homeassistant.core import HomeAssistant

from custom_components.hauflo import dashmerge
from custom_components.hauflo.dashmerge import ID, LAYOUT, plan_and_merge

from test_dashboard_merge import _Dash, _Tunnel, entry  # noqa: F401 — the fixture

PHONE = [{"condition": "screen", "media_query": "(max-width: 699.98px)"}]
WIDE = [{"condition": "screen", "media_query": "(min-width: 700px)"}]
LIGHTS = {"type": "custom:hauflo-lights", "block": "room_lights", "title": "Свет", "entities": ["light.a", "light.b"]}
COVERS = {"type": "custom:hauflo-covers", "block": "room_covers", "title": "Шторы", "entities": ["cover.a"]}
ALERT = {"type": "custom:hauflo-alert", "alarms": ["binary_sensor.leak"]}
MINE = {"type": "markdown", "content": "Моя заметка у света"}
GEN = pathlib.Path(__file__).parent / "fixtures/dashboards/generated"


def old_room(lights=LIGHTS, covers=COVERS):
    """0.20: the room page — the alert once, the lights and the covers as the phone's section and the wide ones."""
    s1, s2, s3 = "v/room/s:lights:Свет", "v/room/s:lights:Свет#2", "v/room/s:covers:Шторы"
    return {"views": [{
        "title": "Гостиная", "path": "room", "type": "sections", ID: "v/room",
        "sections": [
            {"type": "grid", ID: "v/room/s:alert:", "cards": [{**ALERT, ID: "v/room/s:alert:/alert:"}]},
            {"type": "grid", ID: s1, "visibility": PHONE, "cards": [
                {**lights, "grid_options": {"columns": 12}, ID: f"{s1}/lights:Свет"},
                {**covers, "grid_options": {"columns": 12}, ID: f"{s1}/covers:Шторы"},
            ]},
            {"type": "grid", ID: s2, "visibility": WIDE, "column_span": 2, "cards": [{**lights, "grid_options": {"columns": 24}, ID: f"{s2}/lights:Свет"}]},
            {"type": "grid", ID: s3, "visibility": WIDE, "cards": [{**covers, "grid_options": {"columns": 12}, ID: f"{s3}/covers:Шторы"}]},
        ],
    }]}


def new_room(lights=LIGHTS, covers=COVERS, title="Гостиная"):
    """0.21: each block once, in Hauflo's layout card; ids from what a block shows."""
    lid, cid = "v/room/lights:light.a", "v/room/covers:cover.a"
    return {"views": [{
        "title": title, "path": "room", "type": "sections", ID: "v/room",
        "sections": [
            {"type": "grid", ID: "v/room/s:alert:binary_sensor.leak", "cards": [{**ALERT, ID: "v/room/alert:binary_sensor.leak"}]},
            {"type": "grid", "column_span": 3, ID: "v/room/s:layout:lights-light.a", "cards": [{
                "type": LAYOUT, ID: "v/room/layout:lights-light.a", "grid_options": {"columns": "full"},
                "layouts": [{"media": "(max-width: 699.98px)", "columns": [[lid, cid]]}, {"media": "(min-width: 700px)", "columns": [[lid], [cid]]}],
                "cards": [{**lights, ID: lid}, {**covers, ID: cid}],
            }]},
        ],
    }]}


def blocks(cfg):
    """Every card of a config (a layout's blocks too), in order, without ids."""
    out = []

    def walk(node, level):
        for c in dashmerge._kids(node, level):
            if isinstance(c, dict):
                cl = dashmerge._child_level(level, dashmerge._kids_key(node, level))
                if cl == 3 and not dashmerge._holds(c, 3):
                    out.append({k: v for k, v in c.items() if k != ID})
                if dashmerge._holds(c, cl):
                    walk(c, cl)

    walk(cfg, 0)
    return out


def layout_of(cfg):
    return cfg["views"][0]["sections"][1]["cards"][0]


def section(cfg, sid):
    return next(s for s in cfg["views"][0]["sections"] if s.get(ID) == sid)


def assert_safe(report):
    assert report["lost"] == 0 and report["untouched"] == 0


# --- moving the old layout over (п.49) ---------------------------------------------------------------------------------


def test_untouched_old_page_becomes_the_new_one_nothing_to_say():
    old = old_room()
    merged, report = plan_and_merge(new_room(), copy.deepcopy(old), old)
    assert merged == new_room()
    assert_safe(report)
    assert report["removed"] == [] and report["conflicts"] == [] and report["local"] == [] and report["pages"] == []
    assert report["layout"] == {"blocks": 3, "copies": 2, "moved": 0}
    assert report["updates"] == {"added": 0, "changed": 0, "removed": 0}


@pytest.mark.parametrize("gone", ["phone", "wide"])
def test_a_block_stays_while_any_copy_of_it_is_there(gone):
    """Горького 2: «удалил лишние копии — пропало всё» — removing copies is not removing the block."""
    old = old_room()
    cur = copy.deepcopy(old)
    secs = cur["views"][0]["sections"]
    if "phone" in gone:
        secs[1]["cards"] = []
    if "wide" in gone:
        secs[2]["cards"], secs[3]["cards"] = [], []
    merged, report = plan_and_merge(new_room(), cur, old)
    assert_safe(report)
    assert blocks(merged) == blocks(new_room())
    lay = layout_of(merged)
    assert [c[ID] for c in lay["cards"]] == ["v/room/lights:light.a", "v/room/covers:cover.a"]
    assert report["removed"] == []


def test_a_block_with_every_copy_removed_stays_removed_and_comes_back_only_asked():
    old = old_room()
    cur = copy.deepcopy(old)
    secs = cur["views"][0]["sections"]
    secs[1]["cards"] = [c for c in secs[1]["cards"] if c["type"] != COVERS["type"]]
    secs[3]["cards"] = []
    merged, report = plan_and_merge(new_room(), cur, old)
    assert_safe(report)
    assert [c["type"] for c in blocks(merged)] == [ALERT["type"], LIGHTS["type"]]
    assert [r["id"] for r in report["removed"]] == ["v/room/covers:cover.a"]
    back, report = plan_and_merge(new_room(), cur, old, restore=["v/room/covers:cover.a"])
    assert blocks(back) == blocks(new_room()) and report["restored"] == 1


def test_whole_copy_sections_removed_the_block_stays_in_the_one_left():
    old = old_room()
    cur = copy.deepcopy(old)
    cur["views"][0]["sections"] = [s for s in cur["views"][0]["sections"] if s.get("visibility") != PHONE]
    merged, report = plan_and_merge(new_room(), cur, old)
    assert_safe(report)
    assert blocks(merged) == blocks(new_room())


def test_his_edit_of_one_copy_is_the_block_kept_his():
    old = old_room()
    cur = copy.deepcopy(old)
    cur["views"][0]["sections"][2]["cards"][0]["title"] = "Свет у дивана"  # the tablet's copy
    merged, report = plan_and_merge(new_room(), cur, old)
    assert_safe(report)
    got = [c for c in blocks(merged) if c["type"] == LIGHTS["type"]]
    assert len(got) == 1 and got[0]["title"] == "Свет у дивана"
    assert [(c["id"], c["kept"]) for c in report["conflicts"]] == [("v/room/lights:light.a", "yours")]
    assert report["local"] == []
    # Taken: Hauflo's version, once.
    taken, report = plan_and_merge(new_room(), cur, old, take=["v/room/lights:light.a"])
    assert blocks(taken) == blocks(new_room()) and report["taken"] == 1


def test_his_edits_of_two_copies_one_is_the_block_the_other_his_card():
    old = old_room()
    cur = copy.deepcopy(old)
    cur["views"][0]["sections"][1]["cards"][0]["title"] = "Свет телефон"
    cur["views"][0]["sections"][2]["cards"][0]["title"] = "Свет планшет"
    merged, report = plan_and_merge(new_room(), cur, old)
    assert_safe(report)
    titles = sorted(c["title"] for c in blocks(merged) if c["type"] == LIGHTS["type"])
    assert titles == ["Свет планшет", "Свет телефон"]
    assert len(report["conflicts"]) == 1 and [x.get("dropped") for x in report["local"]] == [True]


def test_the_same_edit_in_every_copy_is_one_block():
    old = old_room()
    cur = copy.deepcopy(old)
    cur["views"][0]["sections"][1]["cards"][0]["title"] = "Люстра"
    cur["views"][0]["sections"][2]["cards"][0]["title"] = "Люстра"
    merged, report = plan_and_merge(new_room(), cur, old)
    assert_safe(report)
    assert [c["title"] for c in blocks(merged) if c["type"] == LIGHTS["type"]] == ["Люстра"]
    assert report["local"] == []


def test_a_resized_copy_is_not_an_edit():
    old = old_room()
    cur = copy.deepcopy(old)
    cur["views"][0]["sections"][2]["cards"][0]["grid_options"] = {"columns": 18, "rows": 4}
    merged, report = plan_and_merge(new_room(), cur, old)
    assert_safe(report)
    assert merged == new_room() and report["conflicts"] == []


def test_his_card_in_the_phone_section_stays_after_its_block_inside_the_layout():
    old = old_room()
    cur = copy.deepcopy(old)
    cur["views"][0]["sections"][1]["cards"].insert(1, copy.deepcopy(MINE))
    merged, report = plan_and_merge(new_room(), cur, old)
    assert_safe(report)
    assert [c.get(ID, "mine") for c in layout_of(merged)["cards"]] == ["v/room/lights:light.a", "mine", "v/room/covers:cover.a"]
    assert len(report["local"]) == 1


def test_his_card_in_a_wide_copy_moves_next_to_its_block_and_once_when_in_every_copy():
    old = old_room()
    cur = copy.deepcopy(old)
    cur["views"][0]["sections"][2]["cards"].append(copy.deepcopy(MINE))  # the tablet's copy only
    merged, report = plan_and_merge(new_room(), cur, old)
    assert_safe(report)
    assert [c.get(ID, "mine") for c in layout_of(merged)["cards"]] == ["v/room/lights:light.a", "mine", "v/room/covers:cover.a"]
    assert report["layout"]["moved"] == 1
    # The same card in the phone's section and the tablet's: one card after the upgrade, nothing lost.
    cur["views"][0]["sections"][1]["cards"].insert(1, copy.deepcopy(MINE))
    merged, report = plan_and_merge(new_room(), cur, old)
    assert_safe(report)
    assert sum(1 for c in blocks(merged) if c == MINE) == 1
    # Two different cards of his: both.
    other = {"type": "markdown", "content": "другое"}
    cur["views"][0]["sections"][3]["cards"].append(other)
    merged, report = plan_and_merge(new_room(), cur, old)
    assert_safe(report)
    assert MINE in blocks(merged) and other in blocks(merged)


def test_his_section_and_his_page_stay():
    old = old_room()
    cur = copy.deepcopy(old)
    mine_sec = {"type": "grid", "cards": [{"type": "tile", "entity": "lock.door"}]}
    cur["views"][0]["sections"].insert(2, mine_sec)
    cur["views"].append({"title": "Сад", "path": "garden", "cards": [MINE]})
    merged, report = plan_and_merge(new_room(), cur, old)
    assert_safe(report)
    assert mine_sec in merged["views"][0]["sections"] and merged["views"][1] == cur["views"][1]


def test_his_edited_copy_section_stays_his():
    old = old_room()
    cur = copy.deepcopy(old)
    sec = cur["views"][0]["sections"][3]
    sec["visibility"] = []  # he made the covers' wide copy show everywhere
    sec["cards"].append(copy.deepcopy(MINE))
    merged, report = plan_and_merge(new_room(), cur, old)
    assert_safe(report)
    assert MINE in blocks(merged)
    # The block itself once, in the layout; his section kept with his card.
    assert sum(1 for c in blocks(merged) if c["type"] == COVERS["type"]) == 1


def test_his_moved_copy_stays_where_he_put_it():
    old = old_room()
    cur = copy.deepcopy(old)
    covers = cur["views"][0]["sections"][1]["cards"].pop(1)
    cur["views"][0]["sections"][3]["cards"] = []
    cur["views"][0]["sections"][0]["cards"].append(covers)  # next to the alert
    merged, report = plan_and_merge(new_room(), cur, old)
    assert_safe(report)
    alert_sec = merged["views"][0]["sections"][0]["cards"]
    assert [c[ID] for c in alert_sec] == ["v/room/alert:binary_sensor.leak", "v/room/covers:cover.a"]
    assert sum(1 for c in blocks(merged) if c["type"] == COVERS["type"]) == 1


def test_titles_in_another_language_still_find_the_block():
    """п.54 (tester 05.11: EN → RU — 44 ids gone, 48 new, what he removed came back): a block he removed stays removed
    when Hauflo's new version has other words."""
    old = old_room()
    cur = copy.deepcopy(old)
    secs = cur["views"][0]["sections"]
    secs[1]["cards"] = secs[1]["cards"][:1]
    secs[3]["cards"] = []
    new = new_room({**LIGHTS, "title": "Light"}, {**COVERS, "title": "Covers"}, title="Living room")
    merged, report = plan_and_merge(new, cur, old)
    assert_safe(report)
    assert [c["type"] for c in blocks(merged)] == [ALERT["type"], LIGHTS["type"]]
    assert [r["id"] for r in report["removed"]] == ["v/room/covers:cover.a"]
    # Both blocks have Hauflo's new words; in HA only the lights change (the covers he removed stay removed).
    assert report["updates"] == {"added": 0, "changed": 2, "removed": 0}
    assert report["pages"] == [{"id": "v/room", "title": "Living room", "added": 0, "changed": 1, "removed": 0, "before": 3, "after": 2}]


def test_a_block_hauflo_dropped_goes_his_edited_one_stays_his():
    old = old_room()
    cur = copy.deepcopy(old)
    new = new_room()
    lay = layout_of(new)
    lay["cards"] = lay["cards"][:1]
    merged, report = plan_and_merge(new, copy.deepcopy(cur), old)
    assert_safe(report)
    assert all(c["type"] != COVERS["type"] for c in blocks(merged))
    assert report["updates"]["removed"] == 1  # one block, not its three copies
    cur["views"][0]["sections"][3]["cards"][0]["title"] = "Мои шторы"
    merged, report = plan_and_merge(new, cur, old)
    assert_safe(report)
    assert [c["title"] for c in blocks(merged) if c["type"] == COVERS["type"]] == ["Мои шторы"]


@pytest.mark.parametrize("house", sorted({pathlib.Path(p).name.rsplit("_", 1)[0] for p in glob.glob(str(GEN / "*_0.21.0.json"))}))
def test_real_dashboards_move_over_whole(house):
    """The backend's real dashboards (dashboardTunnel.test.ts) 0.20 → 0.21: untouched — the new one, nothing to say;
    his card in every section, a block's copies removed — nothing of his lost, nothing outside the plan changed."""
    old, new = (json.loads((GEN / f"{house}_{v}.json").read_text()) for v in ("0.20.0", "0.21.0"))
    merged, report = plan_and_merge(new, copy.deepcopy(old), old)
    assert merged == new
    assert_safe(report)
    assert report["pages"] == [] and report["removed"] == [] and report["updates"] == {"added": 0, "changed": 0, "removed": 0}
    cur = copy.deepcopy(old)
    n = 0
    for v in cur["views"]:
        for s in v.get("sections") or []:
            if s.get("cards"):
                s["cards"].insert(1, {"type": "markdown", "content": f"моё {n}"})
                n += 1
        if v.get("sections") and len(v["sections"]) > 1 and v["sections"][-1].get("visibility"):
            v["sections"][-1]["cards"] = [c for c in v["sections"][-1]["cards"] if ID not in c]  # a wide copy's blocks gone
    merged, report = plan_and_merge(new, cur, old)
    assert_safe(report)
    mine = Counter(c["content"] for c in blocks(merged) if c.get("type") == "markdown")
    assert set(mine) == {f"моё {i}" for i in range(n)} and max(mine.values()) == 1
    # Every Hauflo block of the new version is there once (a wide copy gone is not the block gone).
    want = Counter(json.dumps(c, sort_keys=True) for c in blocks(new))
    got = Counter(json.dumps(c, sort_keys=True) for c in blocks(merged) if c.get("type") != "markdown")
    assert got == want


def test_an_old_agent_dashboard_with_old_ids_on_both_sides_is_not_moved():
    old = old_room()
    cur = copy.deepcopy(old)
    merged, report = plan_and_merge(copy.deepcopy(old), cur, old)
    assert merged == old and "layout" not in report


# --- the layout card itself, once moved over (п.49, п.54в) --------------------------------------------------------------


def test_layout_blocks_merge_like_cards():
    base = new_room()
    cur = copy.deepcopy(base)
    lay = layout_of(cur)
    lay["cards"].insert(1, copy.deepcopy(MINE))  # his card inside the layout
    lay["cards"].pop()  # he removed the covers
    lay2 = layout_of(base)
    new = new_room({**LIGHTS, "title": "Свет зала"})
    merged, report = plan_and_merge(new, cur, base)
    assert_safe(report)
    got = layout_of(merged)
    assert [c.get(ID, "mine") for c in got["cards"]] == ["v/room/lights:light.a", "mine"]
    assert got["cards"][0]["title"] == "Свет зала" and got["layouts"] == lay2["layouts"]
    assert [r["id"] for r in report["removed"]] == ["v/room/covers:cover.a"]
    assert report["pages"] == [{"id": "v/room", "title": "Гостиная", "added": 0, "changed": 1, "removed": 0, "before": 3, "after": 3}]


def test_deleting_the_layout_removes_its_blocks_his_cards_in_it_stay():
    base = new_room()
    cur = copy.deepcopy(base)
    cur["views"][0]["sections"][1]["cards"] = []
    merged, report = plan_and_merge(base, cur, base)
    assert_safe(report)
    assert all(c["type"] not in (LIGHTS["type"], COVERS["type"]) for c in blocks(merged))
    cur = copy.deepcopy(base)
    lay = layout_of(cur)
    lay["cards"].append(copy.deepcopy(MINE))
    new = copy.deepcopy(base)
    new["views"][0]["sections"].pop(1)  # Hauflo dropped the whole layout
    merged, report = plan_and_merge(new, cur, base)
    assert_safe(report)
    assert MINE in blocks(merged)


def test_a_hauflo_block_moved_out_of_the_layout_stays_out():
    base = new_room()
    cur = copy.deepcopy(base)
    covers = layout_of(cur)["cards"].pop()
    cur["views"][0]["sections"][0]["cards"].append(covers)
    merged, report = plan_and_merge(base, cur, base)
    assert_safe(report)
    assert [c[ID] for c in merged["views"][0]["sections"][0]["cards"]][-1] == "v/room/covers:cover.a"
    assert sum(1 for c in blocks(merged) if c["type"] == COVERS["type"]) == 1


def test_pages_say_what_changes_where_and_nothing_else():
    base = new_room()
    cur = copy.deepcopy(base)
    layout_of(cur)["cards"][1]["title"] = "Мои шторы"  # his edit: not Hauflo's change
    new = new_room({**LIGHTS, "entities": ["light.a", "light.b", "light.c"]}, {**COVERS, "entities": ["cover.a", "cover.b"]})
    new["views"].append({"title": "Кухня", "path": "kitchen", "type": "sections", ID: "v/kitchen", "sections": [
        {"type": "grid", ID: "v/kitchen/s:light", "cards": [{"type": "tile", "entity": "light.k", ID: "v/kitchen/tile:light.k"}]}]})
    merged, report = plan_and_merge(new, cur, base)
    assert_safe(report)
    assert report["pages"] == [
        {"id": "v/room", "title": "Гостиная", "added": 0, "changed": 1, "removed": 0, "before": 3, "after": 3},
        {"id": "v/kitchen", "title": "Кухня", "added": 1, "changed": 0, "removed": 0, "before": 0, "after": 1},
    ]
    assert layout_of(merged)["cards"][1]["title"] == "Мои шторы"


def test_every_item_says_what_it_is():
    """Tester 05.11: «по одной» listed «блок», «блок», «блок». A block / page says what it holds (card types, count), a
    Hauflo card without a title its entity (the server names it from the project); his cards only types and titles."""
    base = new_room()
    cur = copy.deepcopy(base)
    secs = cur["views"][0]["sections"]
    secs.pop(0)  # he removed the alert block
    secs.append({"type": "grid", "cards": [{"type": "tile", "entity": "light.his"}, {"type": "custom:mushroom-light-card", "entity": "light.his2"}]})
    _, report = plan_and_merge(dashmerge.ensure_ids(copy.deepcopy(base)), cur, base)
    assert report["removed"] == [{"id": "v/room/s:alert:binary_sensor.leak", "kind": "section", "cards": 1, "inner": ["custom:hauflo-alert"], "where": "Гостиная"}]
    assert report["local"] == [{"kind": "section", "cards": 2, "inner": ["tile", "custom:mushroom-light-card"], "where": "Гостиная"}]
    assert "light.his" not in json.dumps(report)
    # A card of Hauflo's without a title: its own entity.
    base = new_room({k: v for k, v in LIGHTS.items() if k != "title"})
    cur = copy.deepcopy(base)
    layout_of(cur)["cards"].pop(0)
    _, report = plan_and_merge(dashmerge.ensure_ids(copy.deepcopy(base)), cur, base)
    assert [(x["kind"], x["type"], x.get("entity"), x.get("title")) for x in report["removed"]] == [("card", "custom:hauflo-lights", "light.a", None)]


# --- the agent's own check before a save, and after it (п.54а, п.54д) ---------------------------------------------------


def test_untouched_guard_catches_a_change_outside_the_plan():
    base = new_room()
    cur = copy.deepcopy(base)
    m = dashmerge.Merge(dashmerge.ensure_ids(copy.deepcopy(base)), cur, base)
    assert m.untouched() == []
    # A merge bug: a block the plan doesn't change comes out changed, another twice.
    m.result["views"][0]["sections"][0]["cards"][0]["alarms"] = ["binary_sensor.other"]
    layout_of(m.result)["cards"].append(copy.deepcopy(layout_of(m.result)["cards"][0]))
    assert sorted(m.untouched()) == ["v/room/alert:binary_sensor.leak", "v/room/lights:light.a"]


def test_untouched_guard_knows_his_dropped_edit_must_stay():
    base = new_room()
    cur = copy.deepcopy(base)
    layout_of(cur)["cards"][1]["title"] = "Мои шторы"
    new = copy.deepcopy(base)
    layout_of(new)["cards"].pop()
    m = dashmerge.Merge(dashmerge.ensure_ids(new), cur, base)
    assert m.untouched() == []
    layout_of(m.result)["cards"] = [c for c in layout_of(m.result)["cards"] if c.get("title") != "Мои шторы"]
    assert m.untouched() == ["v/room/covers:cover.a"]


async def test_save_refuses_a_change_outside_the_plan_and_verifies_after(hass: HomeAssistant, entry) -> None:  # noqa: F811
    from unittest.mock import patch

    agent = entry.runtime_data
    base = new_room()
    dash = _Dash(copy.deepcopy(base))
    t = _Tunnel(agent, dash)
    agent.dashboards.bases["hauflo-home"] = copy.deepcopy(base)
    new = new_room({**LIGHTS, "title": "Свет зала"})
    msg = {"id": 7, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": new}
    with patch.object(dashmerge.Merge, "untouched", lambda self: ["v/room/alert:binary_sensor.leak"]):
        frame = await t.send(msg)
    assert frame["success"] is False and frame["error"]["code"] == "merge_untouched_changed" and dash.saves == 0
    frame = await t.send(msg)
    assert frame["success"] is True and frame["result"]["verify"] == "ok" and dash.saves == 1
    assert frame["result"]["pages"] == [{"id": "v/room", "title": "Гостиная", "added": 0, "changed": 1, "removed": 0, "before": 3, "after": 3}]

    class _Lossy(_Dash):
        async def async_save(self, config):
            config = copy.deepcopy(config)
            config["views"][0]["sections"].pop()
            await super().async_save(config)

    lossy = _Lossy(copy.deepcopy(dash.config))
    t2 = _Tunnel(agent, lossy)
    frame = await t2.send({**msg, "config": new_room({**LIGHTS, "title": "Свет"})})
    assert frame["success"] is True and frame["result"]["verify"] == "mismatch"


async def test_save_moves_an_old_dashboard_over_through_the_tunnel(hass: HomeAssistant, entry) -> None:  # noqa: F811
    agent = entry.runtime_data
    old = old_room()
    cur = copy.deepcopy(old)
    cur["views"][0]["sections"][2]["cards"].append(copy.deepcopy(MINE))
    cur["views"][0]["sections"][1]["cards"] = cur["views"][0]["sections"][1]["cards"][:1]
    cur["views"][0]["sections"][3]["cards"] = []
    dash = _Dash(cur)
    t = _Tunnel(agent, dash)
    agent.dashboards.bases["hauflo-home"] = copy.deepcopy(old)
    frame = await t.send({"id": 8, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": new_room()})
    assert frame["success"] is True
    res = frame["result"]
    assert res["lost"] == 0 and res["untouched"] == 0 and res["verify"] == "ok" and res["layout"]["copies"] == 2
    assert [r["id"] for r in res["removed"]] == ["v/room/covers:cover.a"] and res.get("copy")
    assert MINE in blocks(dash.config) and all(c["type"] != COVERS["type"] for c in blocks(dash.config))
    # The next deploy (new ids on both sides): nothing comes back, nothing multiplies.
    frame = await t.send({"id": 9, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": new_room()})
    assert frame["result"]["saved"] is False and "layout" not in frame["result"]
    assert sum(1 for c in blocks(dash.config) if c == MINE) == 1


# --- his empty page twins: no empty section of his left (tester 05.11) ------------------------------------------------


def test_drop_twins_removes_his_section_only_when_we_emptied_it():
    new = {"views": [
        {"title": "Сервис", "path": "servis", "type": "sections", ID: "v/servis", "sections": [{"type": "grid", ID: "v/servis/s", "cards": [
            {"type": LAYOUT, ID: "v/servis/layout:x", "cards": [{"type": "custom:hauflo-room", "path": "servis_p", ID: "v/servis/room:p"}]}]}]},
        {"title": "Протечки", "path": "servis_p", "type": "sections", ID: "v/servis_p", "sections": []},
    ]}
    tile = {"type": "button", "name": "Протечки", "tap_action": {"action": "navigate", "navigation_path": "/hauflo-x/p"}}
    cur = copy.deepcopy(new)
    secs = cur["views"][0]["sections"]
    secs.append({"type": "grid", "cards": [copy.deepcopy(tile)]})  # his section: only the tile
    secs.append({"type": "grid", "cards": [copy.deepcopy(tile), MINE]})  # his section with more
    secs.append({"type": "grid", "cards": []})  # his empty section from before: not ours to remove
    secs.append({"type": "grid", "cards": [{"type": "vertical-stack", "cards": [copy.deepcopy(tile)]}]})  # in his stack
    secs[0]["cards"][0]["cards"].append(copy.deepcopy(tile))  # in Hauflo's layout
    cur["views"].append({"title": "протечки", "path": "p", "type": "sections", "sections": []})
    assert dashmerge.twins(cur, new) == [{"id": "p", "title": "протечки", "tiles": 4}]
    out, n = dashmerge.drop_twins(cur, new, ["p"])
    assert n == 1
    secs = out["views"][0]["sections"]
    assert secs[0] == new["views"][0]["sections"][0]  # Hauflo's section and layout: only his tile gone
    assert secs[1:] == [{"type": "grid", "cards": [MINE]}, {"type": "grid", "cards": []}]
    _, report = plan_and_merge(new, out, copy.deepcopy(new))
    assert report["lost"] == 0


@pytest.mark.parametrize("house", sorted({pathlib.Path(p).name.rsplit("_", 2)[0] for p in glob.glob(str(GEN / "*_0.21.0_en.json"))}))
def test_real_dashboard_moved_over_into_another_language(house):
    """The tester's 05.11 case: a Russian 0.20 dashboard, the deploy in English. Every block found (none «gone», none
    «new»), the ids the same as in Russian; a block he removed (every copy) stays removed."""
    old, ru, en = (json.loads((GEN / f"{house}_{v}.json").read_text()) for v in ("0.20.0", "0.21.0", "0.21.0_en"))
    ids = lambda cfg: set(dashmerge._Index(cfg).node)  # noqa: E731
    assert ids(en) == ids(ru)
    merged, report = plan_and_merge(en, copy.deepcopy(old), old)
    assert_safe(report)
    assert report["updates"]["added"] == 0 and report["updates"]["removed"] == 0 and report["removed"] == []
    assert ids(merged) == ids(en)
    # He removed the first block of every page (each of its copies): none comes back, in either language alike.
    cur = copy.deepcopy(old)
    gone = set()
    for v in cur["views"]:
        first = next((c for s in v.get("sections") or [] for c in s.get("cards", []) if c.get("hauflo_id") and c.get("type") not in ("heading", "custom:hauflo-alert")), None)
        if first is None:
            continue
        key = dashmerge._sizeless(first)
        for s in v.get("sections") or []:
            s["cards"] = [c for c in s.get("cards", []) if dashmerge._sizeless(c) != key]
        gone.add(key)
    in_ru, rep_ru = plan_and_merge(ru, copy.deepcopy(cur), old)
    in_en, rep_en = plan_and_merge(en, copy.deepcopy(cur), old)
    assert_safe(rep_ru)
    assert_safe(rep_en)
    assert gone and len(rep_en["removed"]) == len(rep_ru["removed"]) >= 1
    assert not (gone & {dashmerge._sizeless(c) for c in blocks(in_ru)})
    assert ids(in_en) == ids(in_ru)
