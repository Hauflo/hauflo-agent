"""Tunnel allowlist, flows / dashboards / REST / websocket / scrubbers (real module, real registries)."""

import json
from types import SimpleNamespace as NS

import pytest
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hauflo as m
from tests.test_allowlist_knx import GA, ga, knx


@pytest.fixture
def world(hass):
    """A KNX link, one Hauflo light, one hand-made switch, the gate relay (a hand-made cover), a lock."""
    k = MockConfigEntry(domain="knx")
    k.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create("light", "knx", "u-1", config_entry=k, suggested_object_id="mine")
    reg.async_get_or_create("switch", "knx", "u-hand", config_entry=k, suggested_object_id="hand")
    reg.async_get_or_create("cover", "knx", "u-cov", config_entry=k, suggested_object_id="blind")
    reg.async_get_or_create("cover", "template", "tg", suggested_object_id="garage", original_device_class="garage")
    reg.async_get_or_create("lock", "template", "lk", suggested_object_id="front")
    reg.async_get_or_create("light", "hue", "h1", suggested_object_id="hue")
    hass.data["knx"] = knx({"light": {"u-1": {"knx": {"ga": {"write": "1/1/1"}}}}, "cover": {"u-cov": {"knx": {"ga": {"write": "2/2/2"}}}}, "switch": {"u-hand": {"knx": {"ga": {"write": "7/7/1"}}}}}, {}, [])
    return NS(owned={"u-1", "u-cov"}, knx=k)


def lv(hass, w, cfg):
    return m._lovelace_ok(hass, cfg, w.owned, set())


def btn(**kw):
    return {"type": "button", **kw}


def test_lovelace_cards(hass, world):
    ok = lambda c: lv(hass, world, c)  # noqa: E731
    assert not m._lovelace_ok(hass, "x", set(), set())
    assert ok({"views": [{"type": "sections", "cards": [btn(entity="light.mine", name="Kitchen")]}]})
    assert ok({"views": [btn(entity="sensor.anything"), btn(entity="weather.home")]})  # read-only domains
    assert not ok({"views": [btn(entity="switch.hand")]})  # the integrator's own relay
    assert not ok({"views": [btn(entity="lock.front")]})
    assert not ok({"views": [btn(entity="cover.garage")]})
    assert not ok({"type": "custom:evil-card"})
    assert not ok({"type": 5})
    assert not ok({"type": "tile", "hauflo_id": "a\x00b"}) and ok({"type": "tile", "hauflo_id": "room-1"})
    # text keys carry anything; ids of the wrong shape or in a list ("light.mine,lock.front") don't pass
    assert ok({"type": "tile", "name": "light.whatever"})
    assert not ok({"type": "tile", "entity": "LIGHT.mine"}) and not ok({"type": "tile", "entity": "light.mine,lock.front"})
    assert not ok({"type": "tile", "entity": "all"}) and not ok({"type": "tile", "entity": " light.mine"})
    assert not ok({"type": "tile", "x": "https://evil.example/x.png"}) and not ok({"type": "tile", "x": "//evil"}) and not ok({"type": "tile", "x": "javascript:1"})
    assert ok({"type": "tile", "x": "plain text"})
    # url paths stay Hauflo's
    assert ok({"type": "tile", "url_path": "hauflo-x"}) and not ok({"type": "tile", "url_path": "lovelace"})
    # pictures stay in the house
    assert ok({"type": "picture-glance", "image": "/local/a.png"}) and ok({"type": "picture-glance", "image": "data:image/png;base64,AAAA"})
    assert not ok({"type": "picture-glance", "image": "https://x/y.png"}) and not ok({"type": "picture-glance", "image": "/local/../etc"})
    assert not ok({"type": "tile", "state_image": {"on": "http://x/y.png"}}) and ok({"type": "tile", "state_image": {"on": "/local/on.png"}})
    assert not m._image_ok(5) and not m._image_ok("/local//x")
    # navigation
    assert ok(btn(tap_action={"action": "navigate", "navigation_path": "/hauflo-main/room"}))
    assert not ok(btn(tap_action={"action": "navigate", "navigation_path": "/lovelace/0"}))
    assert ok({"type": "tile", "path": "/hauflo-main"}) and not ok({"type": "tile", "path": "/profile"})
    assert not ok(btn(tap_action={"action": "evil"}))
    assert ok(btn(tap_action={"action": "more-info"}))
    assert not ok(btn(tap_action={"action": "toggle", "target": {"entity_id": "light.mine"}}))  # a target on an action that runs none
    assert not ok({"type": "tile", "target": {"entity_id": "light.mine"}})  # a target with no service
    assert not ok({"type": "tile", "service_data": {}})


def test_lovelace_services(hass, world):
    def run(**kw):
        return lv(hass, world, btn(tap_action={"action": "perform-action", **kw}))

    assert run(perform_action="light.turn_on", target={"entity_id": "light.mine"})
    assert run(perform_action="light.turn_on", data={"entity_id": ["light.mine"]})
    assert not run(perform_action="light.turn_on", target={"entity_id": "switch.hand"})  # not Hauflo's
    assert not run(perform_action="light.turn_on", target={"entity_id": "LIGHT.mine"})
    assert not run(perform_action="light.turn_on", target={"entity_id": []})
    assert not run(perform_action="lock.unlock", target={"entity_id": "lock.front"})  # not in the card services
    assert not run(perform_action="light.turn_on")  # nothing named
    assert not run()
    assert not run(perform_action="light.turn_on", target="x")
    assert not run(perform_action="light.turn_on", target={"device_id": "d"})
    assert not run(perform_action="light.turn_on", target={"entity_id": "light.mine", "area_id": "kitchen"})
    assert not run(perform_action="light.turn_on", data={"floor_id": "f", "entity_id": "light.mine"})
    assert not run(perform_action="light.turn_on", service="light.turn_off", target={"entity_id": "light.mine"})  # two different services
    assert not run(perform_action="light.turn_on", target={"entity_id": "light.mine"}, data={1: 2})
    # a whole room off is allowed only when every light in it is Hauflo's
    assert run(perform_action="light.turn_off", target={"area_id": "nowhere"})  # an empty room: nothing there that is not Hauflo's
    er.async_get(hass).async_get_or_create("light", "knx", "u-room", suggested_object_id="inroom")
    # knx.send: only the store byte on an address Hauflo's own entity writes to
    send = lambda data, **kw: run(perform_action="knx.send", data=data, **kw)  # noqa: E731
    assert send({"address": "1/1/1", "payload": [129]})
    assert not send({"address": "7/7/1", "payload": [129]})  # the integrator's gate relay
    assert not send({"address": "1/1/1", "payload": [300]}) and not send({"address": "1/1/1", "payload": [True]}) and not send({"address": "1/1/1", "payload": "x"})
    assert not send({"address": "1/1/1", "payload": [1], "type": "string"})
    assert not run(perform_action="knx.send", target={"entity_id": "light.mine"}, data={"address": "1/1/1", "payload": [1]})
    assert not run(perform_action="knx.send")


async def test_area_lights(hass, world):
    areas = ar.async_get(hass)
    room = areas.async_create("Room")
    hall = areas.async_create("Hall")
    reg = er.async_get(hass)
    reg.async_update_entity("light.mine", area_id=room.id)
    reg.async_update_entity("light.hue", area_id=hall.id)
    f = lambda a: m._area_lights_owned(hass, a, world.owned, set())  # noqa: E731
    assert f(room.id) and not f(hall.id)
    for bad in (None, "all", "none", "Bad Id", "UP"):
        assert not f(bad)
    assert not m._area_lights_owned(None, room.id, world.owned, set())
    # a device placed in the room whose own light has no area counts as in it
    d = dr.async_get(hass).async_get_or_create(config_entry_id=world.knx.entry_id, identifiers={("x", "1")})
    dr.async_get(hass).async_update_device(d.id, area_id=room.id)
    reg.async_get_or_create("light", "hue", "h2", device_id=d.id, suggested_object_id="devlight")
    assert not f(room.id)
    # in a scene call
    run = lambda a: m._lovelace_ok(hass, {"type": "button", "tap_action": {"action": "perform-action", "perform_action": "light.turn_off", "target": {"area_id": a}}}, world.owned, set())  # noqa: E731
    assert run(hall.id) is False and run("all") is False


async def test_ws_rooms_labels_dashboards(hass, world):
    areas, floors, labels = ar.async_get(hass), None, lr.async_get(hass)
    own = areas.async_create("Mine")
    other = areas.async_create("Theirs")
    busy = areas.async_create("Busy")
    er.async_get(hass).async_update_entity("light.mine", area_id=busy.id)
    entries = {f"area:{own.id}", f"area:{busy.id}", "label:lab_mine", "label:lab_busy"}
    lab = labels.async_create("Mine")
    W = lambda msg: m._ws_allowed(msg, hass, world.owned, entries)  # noqa: E731
    assert W({"type": "config/area_registry/update", "area_id": own.id, "name": "x", "aliases": []})
    assert not W({"type": "config/area_registry/update", "area_id": own.id, "labels": ["x"]})
    assert not W({"type": "config/area_registry/update", "area_id": other.id, "name": "x"})  # someone else's name
    assert W({"type": "config/area_registry/update", "area_id": other.id, "floor_id": "f1"})  # an empty floor filled
    assert not W({"type": "config/area_registry/update", "area_id": other.id})
    assert not W({"type": "config/area_registry/update", "area_id": "ghost", "floor_id": "f1"})
    assert W({"type": "config/area_registry/delete", "area_id": own.id})
    assert not W({"type": "config/area_registry/delete", "area_id": busy.id})  # something is in it
    assert not W({"type": "config/area_registry/delete", "area_id": other.id})  # not Hauflo's
    assert not W({"type": "config/area_registry/delete", "area_id": own.id, "name": "x"})
    entries.add("area:ghost")
    assert not W({"type": "config/area_registry/delete", "area_id": "ghost"})
    entries.add(f"label:{lab.label_id}")
    assert W({"type": "config/label_registry/delete", "label_id": lab.label_id})
    assert not W({"type": "config/label_registry/delete", "label_id": "other"})
    er.async_get(hass).async_update_entity("light.mine", labels={lab.label_id})
    assert not W({"type": "config/label_registry/delete", "label_id": lab.label_id})  # carried
    # label gate for Alice: a label carrying a lock hands it over
    assert m._label_ok(hass, lab.label_id, world.owned, set())
    er.async_get(hass).async_update_entity("lock.front", labels={lab.label_id})
    assert not m._label_ok(hass, lab.label_id, world.owned, set())
    assert not m._label_ok(hass, "", world.owned, set()) and not m._label_ok(None, "x", set(), set())
    # dashboards
    assert W({"type": "lovelace/dashboards/create", "url_path": "hauflo-x"}) and not W({"type": "lovelace/dashboards/create", "url_path": "mine"})
    assert W({"type": "lovelace/config", "url_path": "hauflo-x"}) and not W({"type": "lovelace/config", "url_path": None})
    assert not W({"type": "lovelace/config", "url_path": "hauflo-x-copy-20260101"})  # copies are the agent's alone
    assert not W({"type": "lovelace/config/save", "url_path": "lovelace", "config": {}})
    assert W({"type": "lovelace/config/save", "url_path": "hauflo-x", "config": {"views": []}})
    assert not W({"type": "lovelace/config/save", "url_path": "hauflo-x", "config": {"type": "custom:evil"}})
    assert not W({"type": "lovelace/dashboards/update", "dashboard_id": "nope"})
    assert not m._own_dashboard(None, "x") and not m._own_dashboard(hass, 3)
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    hass.data[LOVELACE_DATA] = NS(dashboards={"hauflo-a": NS(config={"id": "dash_a"}), "mine": NS(config={"id": "dash_b"}), "hauflo-a-copy-1": NS(config={"id": "dash_c"}), None: NS(config=None)})
    assert W({"type": "lovelace/dashboards/update", "dashboard_id": "dash_a"})
    assert not W({"type": "lovelace/dashboards/update", "dashboard_id": "dash_b"}) and not W({"type": "lovelace/dashboards/update", "dashboard_id": "dash_c"})
    # simple allow rules
    assert not W({"type": "manifest/get", "integration": "cloud"}) and W({"type": "manifest/get", "integration": "knx"})
    assert not W({"type": "config_entries/get", "domain": "mobile_app"}) and W({"type": "config_entries/get", "domain": "knx"})
    assert W({"type": "call_service", "domain": "light", "service": "turn_on"}) is (("light", "turn_on") in m._SERVICES)
    assert not W({"type": "unknown/thing"})
    assert not W({"type": "call_service", "domain": "lock", "service": "unlock"})


async def test_ws_expose(hass, world):
    W = lambda ids, **kw: m._ws_allowed({"type": "homeassistant/expose_entity", "entity_ids": ids, **kw}, hass, world.owned, set())  # noqa: E731
    assert W(["light.mine"], should_expose=True)
    assert not W(["switch.hand"], should_expose=True)  # the gate relay never goes to Siri / Alice
    assert not W(["lock.front"], should_expose=False) and not W(["cover.garage"], should_expose=True)
    assert not W("light.mine") and not W([5])
    assert W(["light.mine"], should_expose=False)
    assert not W(["light.hue"], should_expose=False)  # taking one away only from Hauflo's own
    assert not W(["switch.hand"], should_expose=False)
    # a group holding the lock
    hass.states.async_set("group.sneaky", "on", {"entity_id": ["light.mine", "lock.front"]})
    er.async_get(hass).async_get_or_create("light", "group", "gg", suggested_object_id="sneaky")
    assert m._protected(hass, "group.sneaky") and m._protected(hass, 5) and not m._protected(hass, "light.mine")
    assert m._protected(hass, "cover.garage")
    assert not m._protected(None, "cover.x")


def test_forbidden_and_alice(hass, world):
    assert m._forbidden("lock") and m._forbidden("lock.front") and not m._forbidden("light.x")
    assert m._forbidden({"a": ["x", {"b": "alarm_control_panel.home"}]}) and not m._forbidden(5)
    assert m._alice_labels(None) == set()
    e = MockConfigEntry(domain="yandex_smart_home", options={"label": "alisa"})
    e.add_to_hass(hass)
    MockConfigEntry(domain="yandex_smart_home", options={}).add_to_hass(hass)
    assert m._alice_labels(hass) == {"alisa"}
    # a gate (a protected cover, even Hauflo's own KNX one) keeps its class and is never labelled for Alice
    er.async_get(hass).async_get_or_create("cover", "knx", "u-gate", config_entry=world.knx, suggested_object_id="gate", original_device_class="gate")

    def upd(**kw):
        return m._ws_allowed({"type": "config/entity_registry/update", "entity_id": "cover.gate", **kw}, hass, world.owned | {"u-gate"}, set())

    assert not upd(labels=["alisa"]) and upd(labels=["other"])


class Mgr:
    def __init__(self, flows):
        self.flows = flows

    def async_get(self, fid):
        if fid not in self.flows:
            raise KeyError(fid)
        return self.flows[fid]


def test_flow_input(hass, world):
    F = lambda dom, data, **kw: m._flow_input_ok(hass, dom, data, kw.pop("memo", {}), kw.pop("fid", "f"), kw.pop("step", None), world.owned, kw.pop("entries", set()))  # noqa: E731
    assert not F("knx", {"x": "lock.front"})
    assert F("mqtt", {"anything": 1})
    # KNX: secure keys, history to Postgres and a gateway outside the house stay refused
    assert F("knx", {"host": "192.168.1.5"}) and not F("knx", {"host": "evil.example.com"})
    assert not F("knx", {"host": "192.168.1.5", "user_password": "x"})
    assert not F("knx", {}, step="secure_tunnel") and not F("knx", {}, step="knxkeys_x") and not F("knx", {}, step="x_postgres")
    assert F("knx", {"telegram_store_section": {"telegram_db_backend": "sqlite"}})
    assert not F("knx", {"telegram_store_section": {"telegram_db_backend": "postgres"}})
    assert not F("knx", {"telegram_store_section": "x"}) and not F("knx", {"telegram_other": 1})
    assert F("knx", {"rate_limit": 20, "state_updater": True})
    for bad in ({"rate_limit": 0}, {"rate_limit": 61}, {"rate_limit": True}, {"state_updater": "yes"}):
        assert not F("knx", bad), bad
    assert F("knx", {"multicast_group": "224.0.23.12"}) and not F("knx", {"multicast_group": "10.0.0.1"}) and not F("knx", {"multicast_group": "zzz"})
    # a group of Hauflo's own only
    assert F("group", {"entities": ["light.mine"]}, entries=set())
    assert not F("group", {"entities": ["switch.hand"]}) and not F("group", {"entities": "light.mine"})
    # Alice: the cloud link by label, no skill credentials
    assert F("yandex_smart_home", {"filter_source": "label"}) and not F("yandex_smart_home", {"skill_id": "x"})
    assert not F("yandex_smart_home", {"connection_type": "direct"}) and not F("yandex_smart_home", {"filter_source": "config"})
    labels = lr.async_get(hass)
    lab = labels.async_create("Alice")
    assert F("yandex_smart_home", {"label": lab.label_id})
    er.async_get(hass).async_update_entity("lock.front", labels={lab.label_id})
    assert not F("yandex_smart_home", {"label": lab.label_id})


def test_homekit_steps(hass, world):
    memo = {}
    H = lambda data, step, fid="f": m._homekit_step_ok(hass, data, memo, fid, step, world.owned, set())  # noqa: E731
    assert H({"include_domains": ["person"]}, "user") and not H({"include_domains": ["light"]}, "user") and not H({"include_domains": ["person"], "x": 1}, "user")
    assert H({}, "pairing") and not H({"a": 1}, "pairing")
    assert not H({"mode": "accessory", "include_exclude_mode": "include", "domains": ["light"]}, "init")
    assert not H({"mode": "bridge", "include_exclude_mode": "exclude", "domains": ["light"]}, "init")
    assert not H({"mode": "bridge", "include_exclude_mode": "include", "domains": []}, "init")
    assert not H({"mode": "bridge", "include_exclude_mode": "include", "domains": [1]}, "init")
    assert not m._homekit_step_ok(hass, {"mode": "bridge", "include_exclude_mode": "include", "domains": ["light"]}, None, "f", "init")
    assert H({"mode": "bridge", "include_exclude_mode": "include", "domains": ["light"]}, "init", "f1")
    assert memo["f1"] == ["light"]
    assert not H({"entities": ["light.mine"]}, "include", "unknown-flow")  # the first step was skipped
    assert not H({"entities": "light.mine"}, "include", "f1")
    memo["f2"] = ["light", "cover"]
    assert not H({"entities": ["light.mine"]}, "include", "f2")  # a domain left unpicked: HomeKit would take it whole
    memo["f3"] = ["light"]
    assert not H({"entities": ["switch.hand"]}, "include", "f3") and "f3" not in memo
    memo["f4"] = ["light"]
    assert H({"entities": ["light.mine"]}, "include", "f4")
    memo["f5"] = ["light"]
    assert not H({"entities": ["light.mine"], "include_domains": ["x"]}, "include", "f5")
    assert H({}, "cameras") and not H({"include_domains": ["x"]}, "climate") and not H({"entities": []}, "bridged_device_triggers")
    assert not H({}, "mystery")


def test_http_allowed(hass, world):
    flows = Mgr({"fk": {"handler": "knx", "step_id": "user"}, "fm": {"handler": "mqtt", "step_id": "user"}, "fg": {"handler": "group", "step_id": "x"}})
    opts = Mgr({"ok": {"handler": "KX", "step_id": "init"}, "og": {"handler": "GR", "step_id": "init"}, "oy": {"handler": "AL", "step_id": "init"}, "oh": {"handler": "HK", "step_id": "init"}})
    hass.config_entries.flow = flows
    hass.config_entries.options = opts
    ents = {"KX": "knx", "GR": "group", "AL": "yandex_smart_home", "HK": "homekit", "MQ": "mqtt"}
    hass.config_entries.async_get_entry = lambda i: NS(domain=ents[i]) if i in ents else None
    entries_of = {"knx": [NS()], "yandex_smart_home": [], "mqtt": []}
    hass.config_entries.async_entries = lambda d=None, **kw: entries_of.get(d, [])
    own = {"KX", "GR", "HK"}
    H = lambda method, path, body=None: m._http_allowed(hass, method, path, json.dumps(body) if body is not None else None, own, {}, world.owned)  # noqa: E731
    # traversal / encoding / junk bodies
    assert not H("GET", "/api/config/../x") and not H("GET", "/api/%2e") and not H("GET", "/api\\x")
    assert not m._http_allowed(hass, "POST", "/api/config/config_entries/flow", "{not json", own, {}, world.owned)
    assert not m._http_allowed(hass, "POST", "/api/config/config_entries/flow", "[1]", own, {}, world.owned)
    assert m._http_allowed(hass, "GET", "/api/", None) and m._http_allowed(hass, "GET", "/api/config", {"a": 1})
    assert not H("POST", "/api/config")
    # the agent's own views, each with its method
    assert H("GET", "/api/hauflo/owned") and not H("DELETE", "/api/hauflo/owned") and not H("GET", "/api/hauflo/future")
    # HomeKit QR only for a bridge of Hauflo's
    assert H("GET", "/api/homekit/pairingqr?HK-secret") and not H("GET", "/api/homekit/pairingqr?OTHER-secret") and not H("GET", "/api/homekit/pairingqr")
    assert not m._http_allowed(hass, "GET", "/api/homekit/pairingqr?HK-s", None, None)
    # scenes: Hauflo's own, never the family's, only Hauflo's entities
    S = "/api/config/scene/config/"
    assert H("POST", S + "hauflo_evening", {"entities": {"light.mine": {}}})
    assert not H("POST", S + "evening", {"entities": {}}) and not H("POST", S + "hauflo_fam_x", {"entities": {}})
    assert not H("POST", S + "hauflo_evening", {"entities": {"switch.hand": {}}}) and not H("POST", S + "hauflo_evening", {"entities": {"lock.front": {}}})
    assert not H("POST", S + "hauflo_evening", {"entities": []}) and not H("POST", S + "hauflo_evening/x", {})
    assert H("GET", S + "hauflo_evening") and H("DELETE", S + "hauflo_evening")
    # flows
    assert H("GET", "/api/config/config_entries/flow_handlers")
    F = "/api/config/config_entries/flow"
    assert H("POST", F, {"handler": "group"}) and not H("POST", F, {"handler": "mqtt"})
    assert not H("POST", F, {"handler": "knx"})  # KNX is single-entry
    assert H("POST", F, {"handler": "knx", "entry_id": "KX"}) and not H("POST", F, {"handler": "knx", "entry_id": "OTHER"}) and not H("POST", F, {"handler": "group", "entry_id": "KX"})
    entries_of["yandex_smart_home"] = [NS()]
    assert not H("POST", F, {"handler": "yandex_smart_home"})  # a second Alice link
    assert H("POST", F + "/fk", {"host": "192.168.1.9"}) and not H("POST", F + "/fk", {"host": "8.8.8.8"})
    assert not H("POST", F + "/fm", {}) and not H("POST", F + "/ghost", {}) and H("GET", F + "/fk") and H("DELETE", F + "/fg")
    # options flows: only entries Hauflo set up
    O = "/api/config/config_entries/options/flow"
    assert H("POST", O, {"handler": "GR"}) and H("POST", O, {"handler": "KX"})
    assert not H("POST", O, {"handler": "AL"}) and not H("POST", O, {"handler": "MQ"}) and not H("POST", O, {"handler": "nope"})
    own.discard("GR")
    assert not H("POST", O, {"handler": "GR"})
    assert not m._http_allowed(hass, "POST", O, json.dumps({"handler": "GR"}), None, {}, None)
    own.add("GR")
    assert H("POST", O + "/ok", {"host": "192.168.1.9"}) and not H("POST", O + "/ok", {"host": "8.8.8.8"})
    assert not H("POST", O + "/oy", {}) and not H("POST", O + "/ghost", {})
    own.discard("GR")
    assert not H("POST", O + "/og", {})
    own.add("GR")
    assert H("GET", O + "/og")
    # deleting entries and reading diagnostics
    E = "/api/config/config_entries/entry/"
    assert H("DELETE", E + "GR") and H("DELETE", E + "HK") and not H("DELETE", E + "KX") and not H("DELETE", E + "MQ") and not H("DELETE", E + "ghost")
    assert not m._http_allowed(hass, "DELETE", E + "GR", None, None)
    D = "/api/diagnostics/config_entry/"
    assert H("GET", D + "HK") and not H("GET", D + "OTHERHK") and H("GET", D + "AL") and not H("GET", D + "KX") and not H("POST", D + "HK")
    own.discard("HK")
    assert not H("GET", D + "HK")
    assert not m._http_allowed(hass, "GET", D + "HK", None, None)
    assert not H("GET", "/api/states") and not H("POST", "/api/services/lock/unlock")
    assert m._flow_handler(flows, "nope") is None and m._flow_step(flows, "nope") is None and m._flow_step(flows, "fk") == "user"
    assert m._platform(hass, "light.nope") is None and m._platform(None, "light.mine") is None and m._platform(hass, 5) is None


def test_ws_locked_and_changes():
    assert m._http_locked("POST", "/api/config/x") and not m._http_locked("GET", "/api/states") and not m._http_locked("POST", "/api/hauflo/notify")
    assert m._http_changes("POST", "/api/hauflo/sidebar") and not m._http_changes("GET", "/api/config/x")
    assert m._ws_locked({"type": "config/entity_registry/update"}) and not m._ws_locked({"type": "get_states"})


def test_scrubbers(hass, world):
    assert m._scrub_cloud(5) == 5
    assert m._scrub_cloud({"logged_in": True, "cloud": "connected", "prefs": {"alexa_enabled": 1, "google_pin": "1234", "cloudhooks": {}}}) == {"logged_in": True, "cloud": "connected", "prefs": {"alexa_enabled": 1}}
    assert m._scrub_cloud({"prefs": "x"})["prefs"] == {}
    # diagnostics keep only a bridge's filter and Alice's label
    assert m._scrub_diagnostics("not json") == "{}"
    assert json.loads(m._scrub_diagnostics(json.dumps({"data": {"config-entry": {"options": {"filter": {"include_entities": ["light.a"]}, "secret": 1}, "data": {"pin": "1"}}, "entry": {"options": {"label": "alisa", "token": "t"}}, "states": [1]}}))) == {"data": {"config-entry": {"options": {"filter": {"include_entities": ["light.a"]}}}, "entry": {"options": {"label": "alisa"}}}}
    assert json.loads(m._scrub_diagnostics(json.dumps({"data": {"config-entry": {"options": "x"}, "entry": "y"}}))) == {"data": {"config-entry": {"options": {"filter": {}}}}}
    assert json.loads(m._scrub_diagnostics("[]")) == {"data": {}}
    # the bus monitor shows which addresses answered, never values
    assert m._scrub_monitor(3) == 3
    assert m._scrub_monitor({"project_loaded": True, "recent_telegrams": [{"destination": "1/1/1", "payload": [1], "timestamp": "t"}, "x"]}) == {"project_loaded": True, "recent_telegrams": [{"destination": "1/1/1", "direction": None, "telegramtype": None, "timestamp": "t"}]}
    assert m._scrub_monitor({"recent_telegrams": "x"})["recent_telegrams"] == []
    # devices: phones lose their names
    ph = MockConfigEntry(domain="mobile_app")
    ph.add_to_hass(hass)
    devs = [{"name": "Ivan's iPhone", "name_by_user": "x", "model": "m", "config_entries": [ph.entry_id]}, {"name": "Lamp", "config_entries": [world.knx.entry_id]}, "junk"]
    out = m._scrub_devices(devs, hass)
    assert out[0]["name"] is None and out[0]["manufacturer" if "manufacturer" in out[0] else "model"] is None and out[1]["name"] == "Lamp"
    assert m._scrub_devices("x", hass) == "x"
    # entities: phones never leave
    reg = er.async_get(hass)
    reg.async_get_or_create("sensor", "mobile_app", "p1", suggested_object_id="ivans_iphone_battery")
    assert m._phone_ids(None) == set() and m._phone_ids(hass) == {"sensor.ivans_iphone_battery"}
    lst = [{"entity_id": "sensor.ivans_iphone_battery"}, {"entity_id": "light.mine"}, {"entity_id": "x", "platform": "mobile_app"}]
    assert m._scrub_entities(lst, hass) == [{"entity_id": "light.mine"}]
    assert m._scrub_entities({"entity_id": "sensor.ivans_iphone_battery"}, hass) is None
    assert m._scrub_entities({"entity_id": "light.mine"}, hass) == {"entity_id": "light.mine"}
    assert m._scrub_entities({"sensor.ivans_iphone_battery": {}, "light.mine": {"a": 1}, "x": None}, hass) == {"light.mine": {"a": 1}, "x": None}
    assert m._scrub_entities("x", hass) == "x"
    # panels: only HA's own menu items
    assert m._scrub_panels(3) == {}
    # config: the address is cut to scheme://host[:port]/path
    c = m._scrub_config({"latitude": 1, "version": "x", "external_url": "https://u:p@home.example:8123/a?q#f"})
    assert c == {"version": "x", "external_url": "https://home.example:8123/a"}
    assert "external_url" not in m._scrub_config({"external_url": "javascript:1"}) and "external_url" not in m._scrub_config({"external_url": 5})
    assert m._scrub_config(5) == 5
    # states: a phone-platform sensor and presence classes
    reg.async_get_or_create("sensor", "sun", "s1", suggested_object_id="next_dawn")
    st = [{"entity_id": "sensor.next_dawn", "state": "t", "attributes": {"friendly_name": "d", "x": 1}}, "junk", {"entity_id": "binary_sensor.m", "state": "on", "attributes": {"device_class": "motion"}}]

    def boom(_e):
        raise RuntimeError

    out = m._scrub_states(st, hass, boom)
    assert out[0]["state"] == "hidden" and out[0]["attributes"] == {"friendly_name": "d"}
    assert out[2]["state"] == "hidden"
    assert m._scrub_states("x") == "x"


def test_scrub_notes_and_alice(hass, world, monkeypatch):
    hk = MockConfigEntry(domain="homekit")
    hk.add_to_hass(hass)
    other = MockConfigEntry(domain="homekit")
    other.add_to_hass(hass)
    ev = {"notifications": {hk.entry_id: {"m": 1}, other.entry_id: {"m": 2}, "x": 1}}
    assert m._scrub_notes(5, hass) == 5 and m._scrub_notes({"notifications": 3}, hass) == {"notifications": 3}
    assert m._scrub_notes(ev, hass, {hk.entry_id}) == {"notifications": {hk.entry_id: {"m": 1}}}
    assert m._scrub_notes(ev, hass, None) == {"notifications": {}}
    # Alice's login stays in the house
    made = []
    monkeypatch.setattr(m.persistent_notification, "async_create", lambda *a: made.append(a))
    res = {"handler": "yandex_smart_home", "type": "create_entry", "description_placeholders": {"id": "abc", "password": "pw", "otp": "123"}}
    out = json.loads(m._alice_creds_to_ha(hass, json.dumps(res)))
    assert out["description_placeholders"] == {"hauflo_note": m.ALICE_NOTE} and "pw" not in json.dumps(out) and "123" in made[0][1]
    for passthru in ("{bad", "[]", json.dumps({**res, "handler": "knx"}), json.dumps({**res, "type": "form"}), json.dumps({**res, "description_placeholders": {}}), json.dumps({**res, "description_placeholders": "x"})):
        assert m._alice_creds_to_ha(hass, passthru) == passthru
    assert len(made) == 1
    res["description_placeholders"]["otp"] = "-"
    m._alice_creds_to_ha(hass, json.dumps(res))
    assert "one-time" not in made[1][1]
