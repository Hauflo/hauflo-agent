"""A gate's / an electric lock's relay deployed as a plain KNX switch (red-team 2, A1/A2): once the deploy marks it
(«/api/hauflo/protect»), every path that would operate it without the family's own tap refuses it — rules, scenes,
«Switch as X», guest access, a room's «all lights off», voice, raw telegrams — while its own dashboard card keeps it."""

from types import SimpleNamespace as NS

import pytest
from homeassistant.helpers import area_registry as ar, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hauflo as m
from custom_components.hauflo import guest, protect, rules as R
from tests.test_allowlist_knx import knx


@pytest.fixture
async def world(hass):
    k = MockConfigEntry(domain="knx")
    k.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create("light", "knx", "u-1", config_entry=k, suggested_object_id="mine", original_name="Свет")
    reg.async_get_or_create("switch", "knx", "u-gsw", config_entry=k, suggested_object_id="dvor_vorota", original_name="Двор Ворота")
    reg.async_get_or_create("switch", "knx", "u-pump", config_entry=k, suggested_object_id="pump")
    reg.async_get_or_create("scene", "knx", "u-sc", config_entry=k, suggested_object_id="evening")
    hass.data["knx"] = knx({
        "light": {"u-1": {"knx": {"ga_switch": {"write": "1/1/1"}}}},
        "switch": {"u-gsw": {"knx": {"ga_switch": {"write": "6/0/1", "state": "6/0/2", "passive": ["6/0/3"]}}}, "u-pump": {"knx": {"ga_switch": {"write": "6/1/1"}}}},
        "scene": {"u-sc": {"knx": {"ga_scene": {"write": "6/0/1"}, "scene_number": 1}}},
    }, {}, [])
    for eid in ("light.mine", "switch.dvor_vorota", "switch.pump"):
        hass.states.async_set(eid, "off", {"friendly_name": "Двор Ворота" if "vorota" in eid else eid})
    hass.data[protect.DATA] = protect.Marks(hass)
    # «Switch as X» over the relay: a light the gate hides behind.
    sx = MockConfigEntry(domain="switch_as_x", options={"entity_id": "switch.dvor_vorota", "target_domain": "light"})
    sx.add_to_hass(hass)
    reg.async_get_or_create("light", "switch_as_x", "sx-1", config_entry=sx, suggested_object_id="lamp")
    hass.states.async_set("light.lamp", "off")
    return NS(owned={"u-1", "u-gsw", "u-pump", "u-sc"}, entries={sx.entry_id})


def _ok(hass, w, eid):
    return m._hauflo_entity(hass, eid, w.owned, w.entries)


async def test_unmarked_relay_is_plain(hass, world):
    # Before the deploy says what it is for, a relay is a switch like any other (what red-team 2 found).
    assert _ok(hass, world, "switch.dvor_vorota") and not m._protected(hass, "switch.dvor_vorota")


async def test_marked_relay_refused_everywhere(hass, world):
    assert m._protect_mark(hass, ["switch.dvor_vorota", "light.nothing"]) == ["switch.dvor_vorota"]
    marks = protect.marks(hass)
    assert "u-gsw" in marks.uids and m._ga_key("6/0/1") in marks.gas and m._ga_key("6/0/3") in marks.gas and m._ga_key("6/0/2") not in marks.gas
    for eid in ("switch.dvor_vorota", "light.lamp"):  # the relay, and the «Switch as X» light over it
        assert m._protected(hass, eid), eid
        assert not _ok(hass, world, eid), eid
    assert _ok(hass, world, "switch.pump") and _ok(hass, world, "light.mine")
    # rules
    env = R.Env(hass, owns=lambda e: _ok(hass, world, e), own_raw=lambda e: True)
    with pytest.raises(R.Invalid):
        R.clean_action({"do": "on", "entity_id": "switch.dvor_vorota"}, env)
    with pytest.raises(R.Invalid):
        R.clean_action({"do": "on", "entity_id": "light.lamp"}, env)
    assert R.clean_action({"do": "on", "entity_id": "switch.pump"}, env)
    # «Switch as X» over it
    assert not m._switch_as_x_step_ok(hass, {"entity_id": "switch.dvor_vorota", "target_domain": "light"}, "user", world.owned, world.entries, False)
    assert m._switch_as_x_step_ok(hass, {"entity_id": "switch.pump", "target_domain": "light"}, "user", world.owned, world.entries, False)
    # guest access
    g = guest.GuestAccess(hass, lambda e: _ok(hass, world, e), lambda: "")
    assert not g._safe("light.lamp") and not g._safe("switch.dvor_vorota")
    # a room's «all lights off» with the wrapper light in it
    room = ar.async_get(hass).async_create("Двор")
    er.async_get(hass).async_update_entity("light.lamp", area_id=room.id)
    assert not m._area_lights_owned(hass, room.id, world.owned, world.entries)
    # voice: never exposed by the tunnel, always taken off
    ws = lambda ids, exp: m._ws_allowed({"type": "homeassistant/expose_entity", "assistants": ["conversation"], "entity_ids": ids, "should_expose": exp}, hass, world.owned, world.entries)  # noqa: E731
    assert not ws(["switch.dvor_vorota"], True) and not ws(["light.lamp"], True)
    assert ws(["switch.dvor_vorota"], False)
    # a scene of Hauflo's on the same address as the relay: no raw «store» telegram there
    send = {"views": [{"cards": [{"type": "tile", "entity": "scene.evening", "hold_action": {"action": "perform-action", "perform_action": "knx.send", "data": {"address": "6/0/1", "payload": [128]}}}]}]}
    assert not m._lovelace_ok(hass, send, world.owned, world.entries)


async def test_marks_stick_and_only_add(hass, world):
    m._protect_mark(hass, ["switch.dvor_vorota"])
    m._protect_mark(hass, [])
    assert m._protected(hass, "switch.dvor_vorota")
    # the deploy can still update the relay itself (no class on a switch)
    assert m._ws_allowed({"type": "knx/update_entity", "entity_id": "switch.dvor_vorota", "platform": "switch", "data": {"entity": {"name": "Ворота"}, "knx": {"ga_switch": {"write": "6/0/1"}}}}, hass, world.owned, world.entries)
    # deleted and remade under another uid, the same address keeps it protected
    reg = er.async_get(hass)
    reg.async_get_or_create("switch", "knx", "u-new", suggested_object_id="other")
    hass.data["knx"].config_store.data["entities"]["switch"]["u-new"] = {"knx": {"ga_switch": {"write": "6/0/1"}}}
    assert m._protected(hass, "switch.other")


async def test_dashboard_keeps_the_gate_on_its_own_card(hass, world):
    m._protect_mark(hass, ["switch.dvor_vorota"])
    lv = lambda cfg: m._lovelace_ok(hass, {"views": [{"cards": [cfg]}]}, world.owned, world.entries)  # noqa: E731
    pa = lambda eid: {"action": "perform-action", "perform_action": "switch.turn_on", "target": {"entity_id": eid}}  # noqa: E731
    assert lv({"type": "tile", "entity": "switch.dvor_vorota"})
    assert lv({"type": "tile", "entity": "switch.dvor_vorota", "name": "Ворота", "icon": "mdi:gate"})
    assert lv({"type": "tile", "entity": "switch.dvor_vorota", "icon_tap_action": pa("switch.dvor_vorota")})
    assert lv({"type": "custom:hauflo-devices", "entities": [{"entity": "switch.dvor_vorota", "icon": "mdi:gate"}, "switch.pump"]})
    ar.async_get(hass).async_create("Двор")
    ar.async_get(hass).async_create("Гараж")
    assert lv({"type": "custom:hauflo-devices", "entities": [{"entity": "switch.dvor_vorota", "name": "Ворота (для Двора и Гаража)"}]})
    # under another name, or operated from another card: refused
    assert not lv({"type": "tile", "entity": "switch.dvor_vorota", "name": "Свет кухня"})
    assert not lv({"type": "custom:hauflo-devices", "entities": [{"entity": "switch.dvor_vorota", "name": "Свет"}]})
    assert not lv({"type": "tile", "entity": "light.mine", "tap_action": pa("switch.dvor_vorota")})
    assert not lv({"type": "button", "name": "Свет", "tap_action": pa("switch.dvor_vorota")})
    # the wrapper light is no card of its own: refused anywhere
    assert not lv({"type": "tile", "entity": "light.lamp"})
    # a lock / alarm stays off every dashboard
    assert not lv({"type": "tile", "entity": "lock.front"})


# --- the deploy takes its own mark back (tester 04.11: a relay marked as a gate, made an outlet again) ---------------


def _expose(hass, w, eid):
    return m._ws_allowed({"type": "homeassistant/expose_entity", "assistants": ["conversation"], "entity_ids": [eid], "should_expose": True}, hass, w.owned, w.entries)


async def test_deploy_takes_back_its_own_mark(hass, world):
    m._protect_mark(hass, ["switch.dvor_vorota"])
    assert not _expose(hass, world, "switch.dvor_vorota")  # the tester's «not_allowed»
    assert m._protect_unmark(hass, ["switch.dvor_vorota", "switch.pump", "light.nothing"]) == ["switch.dvor_vorota"]
    marks = protect.marks(hass)
    assert "u-gsw" not in marks.uids and not marks.gas and not marks.deploy
    for eid in ("switch.dvor_vorota", "light.lamp"):  # the relay, and the «Switch as X» light over it
        assert not m._protected(hass, eid) and _ok(hass, world, eid), eid
    assert _expose(hass, world, "switch.dvor_vorota")
    # marked again by a later deploy: protected again
    m._protect_mark(hass, ["switch.dvor_vorota"])
    assert m._protected(hass, "switch.dvor_vorota")


async def test_unmark_never_frees_what_the_deploy_did_not_mark(hass, world):
    reg = er.async_get(hass)
    store = hass.data["knx"].config_store.data["entities"]
    # a gate cover (its class) opening by the same address as the relay
    store["cover"] = {"u-gate": {"knx": {"ga_up_down": {"write": "6/0/1"}}}}
    reg.async_get_or_create("cover", "knx", "u-gate", suggested_object_id="gate")
    reg.async_update_entity("cover.gate", device_class="gate")
    m._protect_refresh(hass)
    m._protect_mark(hass, ["switch.dvor_vorota", "cover.gate"])
    assert m._protect_unmark(hass, ["switch.dvor_vorota", "cover.gate"]) == ["cover.gate", "switch.dvor_vorota"]
    marks = protect.marks(hass)
    # the gate stays a gate, and its address keeps the relay on it protected
    assert m._protected(hass, "cover.gate") and "u-gate" in marks.uids and m._ga_key("6/0/1") in marks.gas
    assert m._protected(hass, "switch.dvor_vorota")
    # protected only by an address the deploy marked by another relay: this one is no mark of its own to take back
    store["cover"] = {}
    reg.async_remove("cover.gate")
    marks.clear({"u-gate"}, {m._ga_key("6/0/1")})
    reg.async_get_or_create("switch", "knx", "u-twin", suggested_object_id="twin")
    store["switch"]["u-twin"] = {"knx": {"ga_switch": {"write": "6/0/1"}}}
    m._protect_mark(hass, ["switch.dvor_vorota"])
    assert m._protect_unmark(hass, ["switch.twin"]) == [] and m._protected(hass, "switch.twin")
    # the relay removed and made again on its addresses (a light now, a new unique id): the deploy's mark goes with it
    store["switch"].pop("u-gsw")
    reg.async_remove("switch.dvor_vorota")
    store["light"]["u-again"] = {"knx": {"ga_switch": {"write": "6/0/1"}}}
    reg.async_get_or_create("light", "knx", "u-again", suggested_object_id="again")
    assert m._protected(hass, "light.again")
    store["switch"].pop("u-twin")
    reg.async_remove("switch.twin")
    assert m._protect_unmark(hass, ["light.again"]) == ["light.again"] and not m._protected(hass, "light.again") and not marks.deploy
    # the agent's own mark (not the deploy's) stays
    marks.add("u-pump", {m._ga_key("6/1/1")})
    assert m._protect_unmark(hass, ["switch.pump"]) == [] and m._protected(hass, "switch.pump")


async def test_marks_kept_before_owners_stay_with_the_admin(hass, hass_storage, world):
    # A store from before the deploy's marks had an owner: every mark is the agent's — fail closed.
    hass_storage[protect.STORE_KEY] = {"version": 1, "minor_version": 1, "key": protect.STORE_KEY, "data": {"uids": ["u-gsw"], "gas": [m._ga_key("6/0/1")]}}
    old = protect.Marks(hass)
    await old.async_load()
    hass.data[protect.DATA] = old
    assert m._protected(hass, "switch.dvor_vorota")
    m._protect_mark(hass, ["switch.dvor_vorota"])
    assert m._protect_unmark(hass, ["switch.dvor_vorota"]) == ["switch.dvor_vorota"] and m._protected(hass, "switch.dvor_vorota")
    # and the new store keeps who marked what across a restart; an older agent reads «uids» / «gas» as all of them
    m._protect_mark(hass, ["switch.pump"])
    await hass.async_block_till_done()
    from homeassistant.util import dt as dt_util
    from datetime import timedelta
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    data = hass_storage[protect.STORE_KEY]["data"]
    assert set(data["uids"]) == {"u-gsw", "u-pump"} and data["own"]["uids"] == ["u-gsw"] and set(data["deploy"]) == {"u-pump"}
    again = protect.Marks(hass)
    await again.async_load()
    assert again.deploy == {"u-pump": {m._ga_key("6/1/1")}} and again.uids == {"u-gsw", "u-pump"}


async def test_only_the_deploy_path_takes_a_mark_back(hass, hass_client, hass_read_only_access_token, world):
    """Not a person of the house, not a guest, not the tunnel's generic calls, not while the owner closed the house."""
    from homeassistant.setup import async_setup_component

    await async_setup_component(hass, "http", {})
    hass.http.register_view(protect.ProtectView(m._protect_mark, m._protect_unmark))
    m._protect_mark(hass, ["switch.dvor_vorota"])
    # a person of the house who isn't HA's admin (a family member's app)
    user = await hass_client(hass_read_only_access_token)
    res = await user.post("/api/hauflo/protect", json={"entity_ids": [], "unmark": ["switch.dvor_vorota"]})
    assert res.status == 403 and m._protected(hass, "switch.dvor_vorota")
    # the tunnel's generic calls: voice, the registry, the KNX panel — none takes the mark off
    W = lambda msg: m._ws_allowed(msg, hass, world.owned, world.entries)  # noqa: E731
    assert not _expose(hass, world, "switch.dvor_vorota")
    assert W({"type": "config/entity_registry/update", "entity_id": "switch.dvor_vorota", "name": "Розетка"})
    assert W({"type": "knx/update_entity", "entity_id": "switch.dvor_vorota", "platform": "switch", "data": {"entity": {"name": "Розетка"}, "knx": {"ga_switch": {"write": "6/0/1"}}}})
    assert m._protected(hass, "switch.dvor_vorota")  # renamed, it is still marked
    # the guest: its own fixed table, and a marked relay is never «safe» for it
    g = guest.GuestAccess(hass, lambda e: _ok(hass, world, e), lambda: "")
    assert not g._safe("switch.dvor_vorota")
    # the owner closed the house: the deploy may still mark, never take a mark back (and an unreadable body is refused)
    assert not m._http_locked("POST", "/api/hauflo/protect", '{"entity_ids": ["switch.pump"]}')
    assert not m._http_locked("POST", "/api/hauflo/protect", '{"entity_ids": ["switch.pump"], "unmark": []}')
    for body in ('{"entity_ids": [], "unmark": ["switch.dvor_vorota"]}', "{oops", "[]"):
        assert m._http_locked("POST", "/api/hauflo/protect", body), body
        assert m._http_locked("POST", "/api/hauflo/protect?x=1", body), body
    # bad bodies
    admin = await hass_client()
    for body in ({"entity_ids": [], "unmark": "switch.dvor_vorota"}, {"entity_ids": ["switch.dvor_vorota"], "unmark": ["switch.dvor_vorota"]}, {"entity_ids": [], "unmark": ["not an id"]}):
        assert (await admin.post("/api/hauflo/protect", json=body)).status == 400, body
    assert m._protected(hass, "switch.dvor_vorota")
    # the deploy (HA's admin token the tunnel forwards with): its own mark comes off
    res = await admin.post("/api/hauflo/protect", json={"entity_ids": ["switch.pump"], "unmark": ["switch.dvor_vorota"]})
    assert res.status == 200 and await res.json() == {"marked": ["switch.pump"], "unmarked": ["switch.dvor_vorota"]}
    assert not m._protected(hass, "switch.dvor_vorota") and m._protected(hass, "switch.pump")
    # an older backend's body still works
    res = await admin.post("/api/hauflo/protect", json={"entity_ids": ["switch.dvor_vorota"]})
    assert await res.json() == {"marked": ["switch.dvor_vorota"], "unmarked": []}
