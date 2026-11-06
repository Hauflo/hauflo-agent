"""W1-5 (acceptance 06.11 №22): a design switch (Atelier ↔ classic) renames Hauflo's room pages (`komnaty_spalnia` ↔
`etazh_1_spalnia`); the integrator's card on the old page goes to the end of the new page of the same room — no
«<path>-kept» tab for ever. No page of that room in the new design: the old way, a page of its own."""

import copy

from custom_components.hauflo.dashmerge import ID, plan_and_merge

MINE = {"type": "markdown", "content": "MY OWN CARD"}


def page(path, title, *cards, subview=True, back="/hauflo-home/komnaty", kind="sections"):
    v = {"title": title, "path": path, ID: f"v/{path}"}
    if subview:
        v.update(subview=True, back_path=back)
    if kind == "sections":
        v.update(type="sections", sections=[{"type": "grid", ID: f"v/{path}/s:{i}", "cards": [copy.deepcopy(c)]} for i, c in enumerate(cards)])
    else:
        v["cards"] = [copy.deepcopy(c) for c in cards]
    return v


def card(path, entity):
    return {"type": "tile", "entity": entity, ID: f"v/{path}/tile:{entity}"}


def atelier(*rooms):
    views = [{"title": "Комнаты", "path": "komnaty", "type": "sections", ID: "v/komnaty", "sections": []}]
    views += [page(f"komnaty_{slug}", title, card(f"komnaty_{slug}", e)) for slug, title, e in rooms]
    return {"views": views}


def classic(*rooms, floor="etazh_1"):
    views = [{"title": "Этаж 1", "path": floor, "type": "sections", ID: f"v/{floor}", "sections": []}]
    views += [page(f"{floor}_{slug}", title, card(f"{floor}_{slug}", e), back=f"/hauflo-home/{floor}") for slug, title, e in rooms]
    return {"views": views}


ROOMS = [("spalnia", "Спальня", "light.bed"), ("kukhnia", "Кухня", "light.kitchen")]


def with_mine(cfg, path):
    out = copy.deepcopy(cfg)
    v = next(v for v in out["views"] if v.get("path") == path)
    v["sections"][0]["cards"].append(copy.deepcopy(MINE))
    return out


def where_mine(cfg):
    return [v.get("path") for v in cfg["views"] if MINE in [c for s in v.get("sections", []) for c in s.get("cards", [])] + v.get("cards", [])]


def safe(report):
    assert report["lost"] == 0 and report["untouched"] == 0, report


def test_atelier_to_classic_his_card_moves_to_the_same_room():
    base = atelier(*ROOMS)
    cur = with_mine(base, "komnaty_spalnia")
    merged, report = plan_and_merge(classic(*ROOMS), cur, base)
    safe(report)
    assert where_mine(merged) == ["etazh_1_spalnia"]
    assert not [v for v in merged["views"] if str(v.get("path", "")).endswith("-kept")]
    room = next(v for v in merged["views"] if v["path"] == "etazh_1_spalnia")
    # At the end of the new page, after Hauflo's blocks (HA's rule for an added card), in a section of its own.
    assert room["sections"][-1] == {"type": "grid", "cards": [MINE]}
    assert room["sections"][0][ID] == "v/etazh_1_spalnia/s:0"
    assert [x.get("where") for x in report["local"]] == ["Спальня"]


def test_there_and_back_and_twice_no_kept_page_nothing_doubled():
    a, c = atelier(*ROOMS), classic(*ROOMS)
    base, cur = a, with_mine(a, "komnaty_spalnia")
    for new in (c, a, c, a):  # Atelier → classic → Atelier, twice
        merged, report = plan_and_merge(copy.deepcopy(new), cur, base)
        safe(report)
        assert where_mine(merged) == [next(v["path"] for v in new["views"] if v["title"] == "Спальня")]
        assert len(merged["views"]) == len(new["views"])
        assert sum(str(merged).count("MY OWN CARD") for _ in [0]) == 1
        base, cur = new, merged
    # Back on Atelier: Hauflo's page as Hauflo makes it plus his card at the end.
    room = next(v for v in cur["views"] if v["path"] == "komnaty_spalnia")
    assert room["sections"][:-1] == next(v for v in a["views"] if v["path"] == "komnaty_spalnia")["sections"]


def test_room_gone_from_the_new_design_keeps_the_old_way():
    base = atelier(*ROOMS)
    cur = with_mine(base, "komnaty_spalnia")
    merged, report = plan_and_merge(classic(ROOMS[1]), cur, base)
    safe(report)
    assert where_mine(merged) == ["komnaty_spalnia-kept"]
    kept = merged["views"][-1]
    assert ID not in kept and kept["title"] == "Спальня"


def test_same_path_another_kind_of_page_takes_his_card():
    # A sections page of Hauflo's sent as a page of cards under the same id: `rekey` gives it a new id (`v/room~2`).
    base = {"views": [page("room", "Гостиная", card("room", "light.a"), subview=False)]}
    cur = with_mine(base, "room")
    new = {"views": [page("room", "Гостиная", card("room", "light.a"), subview=False, kind="cards")]}
    merged, report = plan_and_merge(new, cur, base)
    safe(report)
    assert [v["path"] for v in merged["views"]] == ["room"]
    assert merged["views"][0][ID] == "v/room~2" and merged["views"][0]["cards"][-1] == MINE
    # A page of cards turned into a sections page: `unpanel` reads it that way first — his card stays on it too.
    base, cur, new = new, merged, {"views": [page("room", "Гостиная", card("room", "light.a"), subview=False)]}
    merged, report = plan_and_merge(new, cur, base)
    safe(report)
    assert where_mine(merged) == ["room"] and len(merged["views"]) == 1


def test_two_bedrooms_on_two_floors_told_apart_by_the_path():
    rooms = [("1_spalnia", "Спальня", "light.b1"), ("2_spalnia", "Спальня", "light.b2")]
    base = classic(*rooms, floor="dom")
    cur = with_mine(base, "dom_2_spalnia")
    merged, report = plan_and_merge(atelier(*rooms), cur, base)
    safe(report)
    assert where_mine(merged) == ["komnaty_2_spalnia"]


def test_ambiguous_title_keeps_the_old_way():
    rooms = [("a", "Спальня", "light.b1"), ("b", "Спальня", "light.b2")]
    base = {"views": [page("x", "Спальня", card("x", "light.b1"))]}
    cur = with_mine(base, "x")
    merged, report = plan_and_merge(atelier(*rooms), cur, base)
    safe(report)
    assert where_mine(merged) == ["x-kept"]


def test_his_section_on_a_page_of_cards_keeps_the_old_way():
    base = atelier(*ROOMS)
    cur = copy.deepcopy(base)
    v = next(v for v in cur["views"] if v["path"] == "komnaty_spalnia")
    v["sections"].append({"type": "grid", "cards": [{"type": "heading", "heading": "Моё"}, copy.deepcopy(MINE)]})
    new = {"views": [page("etazh_1_spalnia", "Спальня", card("etazh_1_spalnia", "light.bed"), kind="cards")]}
    merged, report = plan_and_merge(new, cur, base)
    safe(report)
    assert where_mine(merged) == ["komnaty_spalnia-kept"]
    assert len(report["local"]) == 1  # said once, not twice


def test_new_page_he_removed_takes_nothing():
    # Hauflo had both pages; he removed the one Hauflo keeps — it stays removed, his card isn't put on it.
    old, keep = page("komnaty_spalnia", "Спальня", card("komnaty_spalnia", "light.bed")), page("etazh_1_spalnia", "Спальня", card("etazh_1_spalnia", "light.bed"))
    base = {"views": [old, keep]}
    cur = with_mine({"views": [old]}, "komnaty_spalnia")
    merged, report = plan_and_merge({"views": [copy.deepcopy(keep)]}, cur, base)
    safe(report)
    assert [v.get("path") for v in merged["views"]] == ["komnaty_spalnia-kept"]


def test_moved_hauflo_card_on_the_old_page_goes_along():
    base = atelier(*ROOMS)
    cur = with_mine(base, "komnaty_spalnia")
    # He dragged the kitchen tile onto the bedroom page; the new design still has that tile on its own page.
    kit = next(v for v in cur["views"] if v["path"] == "komnaty_kukhnia")
    tile = kit["sections"][0]["cards"].pop(0)
    next(v for v in cur["views"] if v["path"] == "komnaty_spalnia")["sections"][0]["cards"].append(tile)
    new = classic(*ROOMS)
    new["views"].append(page("komnaty_kukhnia", "Кухня", card("komnaty_kukhnia", "light.kitchen"), subview=False))
    merged, report = plan_and_merge(new, cur, base)
    safe(report)
    room = next(v for v in merged["views"] if v["path"] == "etazh_1_spalnia")
    ids = [c.get(ID) for s in room["sections"] for c in s["cards"]]
    assert "v/komnaty_kukhnia/tile:light.kitchen" in ids and str(merged).count("v/komnaty_kukhnia/tile:light.kitchen") == 1
