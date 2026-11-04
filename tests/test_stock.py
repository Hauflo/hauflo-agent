"""«Заменить на Hauflo» (stock.py): the one table, never Hauflo's own cards, guarded entities, a whole device, undo."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.hauflo import stock

STATES = {
    "light.a": {"device_id": "d1"},
    "light.b": {"device_id": "d2"},
    "switch.s": {"device_id": "d2"},
    "cover.c": {"device_class": "curtain"},
    "cover.g": {"device_class": "garage"},
    "climate.t": {"device_id": "d3"},
    "fan.gtc": {"percentage_step": 10, "device_id": "d4"},
    "fan.cont": {"percentage_step": 1},
    "select.mode": {"device_id": "d4", "state": "auto"},
    "button.filter": {"device_id": "d4", "state": "unknown"},
    "sensor.t": {"state": "21.5"},
    "sensor.h": {"state": "40"},
    "sensor.word": {"state": "ok"},
    "lock.door": {},
    "camera.gate": {},
    "scene.eve": {},
}


def info(eid):
    if eid not in STATES:
        return None
    return {"domain": eid.split(".")[0], "device_class": None, "device_id": None, "state": "on", "percentage_step": None, "speed_count": None, **STATES[eid]}


def cfg(*cards, sections=False):
    v = {"title": "Дом", "sections": [{"type": "grid", "cards": list(cards)}]} if sections else {"title": "Дом", "cards": list(cards)}
    return {"views": [v]}


def made(c):
    return {k: v for k, v in c.items() if k != stock.WAS}


def test_table_is_the_cards_file():
    t = json.loads((Path(stock.__file__).parent / "www" / "stock.json").read_text())
    assert t is stock.table() or t == stock.table()
    # Every target is one of our cards; guarded classes as protect.py / massSafety.
    assert {"lock", "alarm_control_panel", "valve"} <= set(t["guarded"]["domains"])
    assert {"garage", "gate", "door"} <= set(t["guarded"]["cover_classes"])


def test_single_cards_by_domain():
    one = lambda c: stock.offer_of(c, info)  # noqa: E731
    assert one({"type": "tile", "entity": "light.a"})["card"] == {"type": "custom:hauflo-entity", "entity": "light.a"}
    assert one({"type": "tile", "entity": "climate.t", "name": "Зал"})["card"] == {"type": "custom:hauflo-climate", "entity": "climate.t", "label": "Зал"}
    assert one({"type": "thermostat", "entity": "climate.t"})["to"] == "hauflo-climate"
    assert one({"type": "picture-entity", "entity": "camera.gate"})["to"] == "hauflo-camera"
    assert one({"type": "picture-entity", "entity": "light.a"}) is None
    # Gaps get a place: a select, a button — our any-entity card.
    assert one({"type": "tile", "entity": "select.mode"})["to"] == "hauflo-entity"
    assert one({"type": "button", "entity": "button.filter"})["to"] == "hauflo-entity"


def test_fan_speeds_from_percentage_step():
    o = stock.offer_of({"type": "tile", "entity": "fan.gtc"}, info)
    assert o["card"] == {"type": "custom:hauflo-sliders", "entities": [{"entity": "fan.gtc", "scale": "speeds", "speeds": 10}]}
    assert stock.offer_of({"type": "tile", "entity": "fan.cont"}, info)["card"]["entities"] == ["fan.cont"]
    STATES["fan.sc"] = {"speed_count": 3, "percentage_step": 33.33}
    try:
        assert stock.offer_of({"type": "tile", "entity": "fan.sc"}, info)["card"]["entities"][0]["speeds"] == 3
    finally:
        del STATES["fan.sc"]


def test_lists():
    one = lambda c: stock.offer_of(c, info)  # noqa: E731
    o = one({"type": "entities", "title": "Свет", "entities": ["light.a", {"entity": "switch.s"}]})
    assert o["card"] == {"type": "custom:hauflo-lights", "entities": ["light.a", "switch.s"], "title": "Свет"}
    assert one({"type": "glance", "entities": ["sensor.t", "sensor.h"]})["to"] == "hauflo-graph"
    # A word-valued sensor is no graph: the list goes to the devices card.
    assert one({"type": "entities", "entities": ["sensor.t", "sensor.word"]})["to"] == "hauflo-devices"
    assert one({"type": "history-graph", "entities": ["sensor.word"]}) is None
    assert one({"type": "entities", "entities": ["scene.eve"]})["to"] == "hauflo-entity"


def test_guarded_never_in_an_all_at_once_console():
    o = stock.offer_of({"type": "entities", "entities": ["cover.c", "cover.g"]}, info)
    assert o["to"] == "hauflo-devices" and o["guarded"] is True
    assert stock.offer_of({"type": "entities", "entities": ["cover.c"]}, info)["guarded"] is False
    lock = stock.offers(cfg({"type": "tile", "entity": "lock.door"}), info)
    assert lock[0]["guarded"] is True and lock[0]["pick"] is False


def test_never_hauflo_own_or_unknown():
    assert stock.offer_of({"type": "tile", "entity": "light.a", "hauflo_id": "x"}, info) is None
    assert stock.offer_of({"type": "custom:hauflo-entity", "entity": "light.a"}, info) is None
    assert stock.offer_of({"type": "custom:mushroom-light-card", "entity": "light.a"}, info) is None
    assert stock.offer_of({"type": "tile", "entity": "light.gone"}, info) is None
    assert stock.offer_of({"type": "markdown", "content": "hi"}, info) is None


def test_a_whole_device():
    # Not side by side: no whole device (its cards would not come back in their places).
    apart = cfg({"type": "tile", "entity": "fan.gtc"}, {"type": "markdown", "content": "x"}, {"type": "tile", "entity": "select.mode"})
    assert not [o for o in stock.offers(apart, info) if o["device"]]
    c = cfg({"type": "markdown", "content": "x"}, {"type": "tile", "entity": "fan.gtc"}, {"type": "tile", "entity": "select.mode"}, {"type": "tile", "entity": "button.filter"}, sections=True)
    off = stock.offers(c, info)
    dev = [o for o in off if o["device"]]
    assert len(dev) == 1 and dev[0]["entities"] == 3 and dev[0]["where"] == "Дом"
    new, n = stock.apply(c, [dev[0]["id"], off[0]["id"]], info)  # the single fan offer too: the device's wins
    assert n == 1
    cards = new["views"][0]["sections"][0]["cards"]
    assert [made(x) for x in cards] == [{"type": "markdown", "content": "x"}, {"type": "custom:hauflo-area", "device": "d4"}]
    assert len(cards[1][stock.WAS]) == 3
    # Back as it was, in its place.
    back = stock.replaced(new)
    assert back[0]["to"] == "hauflo-area" and back[0]["from"] == "tile"
    old, k = stock.undo(new, [back[0]["id"]])
    assert k == 1 and old == c


def test_apply_and_undo_one_card_and_the_list_of_one_device():
    c = cfg({"type": "entities", "title": "Вентиляция", "entities": ["fan.gtc", "select.mode", "button.filter"]}, {"type": "tile", "entity": "light.a"})
    off = stock.offers(c, info)
    assert off[0]["to"] == "hauflo-area" and off[0]["device"] is True
    new, n = stock.apply(c, [o["id"] for o in off], info)
    assert n == 2
    assert made(new["views"][0]["cards"][0]) == {"type": "custom:hauflo-area", "device": "d4", "title": "Вентиляция"}
    assert stock.offers(new, info) == []  # a replaced card is offered nothing more
    assert stock.undo(new, [r["id"] for r in stock.replaced(new)])[0] == c
    # Unknown ids: nothing changes.
    assert stock.apply(c, ["nope"], info) == (c, 0)
    assert stock.apply(c, "all", info) == (c, 0)


def test_labels_said_where_and_what():
    """The integrator reads which card: its title / name, else the entity's name; where: the view and the section's heading."""
    named = lambda eid: {**info(eid), "name": "Люстра"} if info(eid) else None  # noqa: E731
    c = cfg({"type": "heading", "heading": "Гостиная"}, {"type": "tile", "entity": "light.a"}, {"type": "entities", "title": "Свет", "entities": ["light.a", "light.b"]}, sections=True)
    off = stock.offers(c, named)
    assert [(o["where"], o["title"]) for o in off] == [("Дом · Гостиная", "Люстра"), ("Дом · Гостиная", "Свет")]
    new, _ = stock.apply(c, [off[0]["id"]], named)
    assert [(r["title"], r["where"]) for r in stock.replaced(new, named)] == [("Люстра", "Дом · Гостиная")]
