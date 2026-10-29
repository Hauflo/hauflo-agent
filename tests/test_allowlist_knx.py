"""Tunnel allowlist, KNX side (real module, real registries): which KNX writes/entities the cloud may touch.
Negative cases first — a refused command is the contract."""

from types import SimpleNamespace as NS

import pytest
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hauflo as m


class GA:
    def __init__(self, raw: int) -> None:
        self.raw = raw

    def __hash__(self) -> int:
        return self.raw


def ga(a: int, b: int, c: int) -> int:
    return (a << 11) | (b << 8) | c


class Dev:
    def __init__(self, *raws: int) -> None:
        self.raws = raws

    def group_addresses(self):
        return {GA(r) for r in self.raws}


def knx(store=None, gae=None, devices=None):
    return NS(config_store=NS(data={"entities": store if store is not None else {}}), group_address_entities=gae or {}, xknx=NS(devices=devices or []))


def test_ga_key_forms():
    assert m._ga_key(True) is None
    assert m._ga_key(-1) is None and m._ga_key(0x10000) is None
    assert m._ga_key(5) == "5"
    assert m._ga_key(None) is None
    assert m._ga_key(" i-1 ") == "i-1" and m._ga_key("i_x") == "i_x"
    assert m._ga_key("a/b") is None
    assert m._ga_key("-1/1/1") is None
    assert m._ga_key("1/1/1") == str(ga(1, 1, 1))
    assert m._ga_key("32/1/1") is None and m._ga_key("1/8/1") is None and m._ga_key("1/1/256") is None
    # the same address in every spelling is one key (integrator run 22)
    assert m._ga_key("7/1793") == m._ga_key("07/07/001") == m._ga_key("16129") == m._ga_key(16129) == str(ga(7, 7, 1))
    assert m._ga_key("1/2047") == str(2047 + 2048)
    assert m._ga_key("1/2048") is None
    assert m._ga_key("70000") is None
    assert m._ga_key("1/2/3/4") is None


def test_ga_range_text_bad():
    assert m._ga_range("x") is None and m._ga_text(None) is None
    assert m._ga_range(str(ga(1, 2, 3))) == "1/2" and m._ga_text(str(ga(1, 2, 3))) == "1/2/3"


def test_range_allows():
    k = str(ga(1, 1, 5))
    assert not m._range_allows({"1/1"}, "bad")
    assert m._range_allows({"1/1"}, k)
    assert m._range_allows({"1/1/5"}, k) and m._range_allows({"1/1/1-21"}, k)
    assert not m._range_allows({"1/1/6-9"}, k)
    assert not m._range_allows({"1/2", "garbage"}, k)


def test_knx_data_variants(hass):
    assert m.knx_data(None) is None
    assert m.knx_data(NS()) is None
    hass.data["knx"] = "x"
    assert m.knx_data(hass) == "x"

    class Boom(dict):
        def get(self, *_a):
            raise RuntimeError("shape")

    assert m.knx_data(NS(data=Boom())) is None


def test_knx_store_shapes(hass):
    assert m._knx_store(hass) is None  # no knx
    hass.data["knx"] = NS(config_store=NS(data={"entities": []}))
    assert m._knx_store(hass) is None  # wrong shape
    hass.data["knx"] = NS()
    assert m._knx_store(hass) is None  # no config_store


def test_knx_writes_listens():
    cfg = {"ga_a": {"write": "1/1/1", "state": "1/1/2", "passive": ["1/1/3", "junk"]}, "l": [{"write": "bad"}], "x": {"write": ""}}
    assert m._knx_writes(cfg) == {str(ga(1, 1, 1)), "?"}
    assert m._knx_listens(cfg) == {str(ga(1, 1, i)) for i in (1, 2, 3)} | {"?"}


def test_foreign_writes_and_config_ok(hass):
    gate = str(ga(7, 6, 1))
    store = {
        "light": {"u-mine": {"knx": {"ga_switch": {"write": "1/1/1", "state": "1/1/2"}}}, "u-gate": {"knx": {"ga_switch": {"write": "7/6/1"}}}, "u-bad": "notadict"},
        "scene": {"u-sc": {"knx": {"ga_scene": {"write": "3/3/3"}, "scene_number": 2}}},
    }
    hass.data["knx"] = knx(store, {GA(ga(7, 7, 1)): [NS(unique_id="yaml", ui=False, platform="switch"), NS(unique_id="u-mine", ui=True)]}, [Dev(ga(5, 5, 5), ga(1, 1, 1))])
    owned = {"u-mine"}
    f = m._foreign_knx_writes(hass, owned)
    assert gate in f and str(ga(7, 7, 1)) in f and str(ga(5, 5, 5)) in f and str(ga(1, 1, 1)) not in f
    # no KNX at all: nothing foreign; no hass: unknown
    del hass.data["knx"]
    assert m._foreign_knx_writes(hass, owned) == set()
    assert m._foreign_knx_writes(None, owned) is None
    # KNX there but its store is unreadable: unknown, so refused
    hass.data["knx"] = NS()
    assert m._foreign_knx_writes(hass, owned) is None
    assert m._knx_config_ok(hass, {"type": "knx/create_entity", "data": {"knx": {"ga": {"write": "1/1/9"}}}}, owned) is False
    hass.data["knx"] = knx(store, {}, [Dev(ga(5, 5, 5))])

    def ok(msg):
        return m._knx_config_ok(hass, msg, owned)

    assert ok({"type": "knx/create_entity", "data": {"knx": {"ga": {"write": "1/1/9"}}}})
    assert ok({"type": "knx/create_entity", "data": "nodict"})  # nothing written
    assert not ok({"type": "knx/create_entity", "data": {"knx": {"ga": {"write": "7/6/1"}}}})  # the gate relay
    assert not ok({"type": "knx/create_entity", "data": {"knx": {"ga": {"write": "5/5/5"}}}})  # an exposure only xknx knows
    assert not ok({"type": "knx/create_entity", "data": {"knx": {"ga": {"write": "garbage"}}}})

    # a scene on the room's scene address with another scene number is how KNX scenes work; the same number is not
    def sc(n):
        return {"type": "knx/create_entity", "platform": "scene", "data": {"knx": {"ga_scene": {"write": "3/3/3"}, "scene_number": n}}}

    assert ok(sc(5)) and not ok(sc(2))
    # an update skips its own entity's addresses
    er.async_get(hass).async_get_or_create("light", "knx", "u-mine", suggested_object_id="mine")
    assert ok({"type": "knx/update_entity", "entity_id": "light.mine", "data": {"knx": {"ga": {"write": "1/1/1"}}}})
    # holders unknown while the clash is being judged: refused
    hass.data["knx"] = knx(store, {}, [])
    assert not ok({"type": "knx/create_entity", "data": {"knx": {"ga": {"write": "7/6/1"}}}, "platform": "scene"})


def test_foreign_holders_and_overlap(hass):
    assert m._foreign_holders(hass, None) is None
    store = {"scene": {"u-a": {"knx": {"ga_scene": {"write": "3/3/3"}, "scene_number": 1}}, "u-mine": {"knx": {"ga_scene": {"write": "3/3/4"}}}}, "switch": {"u-s": {"knx": {"ga": {"state": "4/4/4"}}}}}
    hass.data["knx"] = knx(store, {GA(ga(6, 0, 1)): [NS(unique_id="y", ui=False, platform="light"), NS(unique_id="u-s", ui=True), NS(unique_id="u-mine", ui=False)]}, [Dev(ga(5, 5, 5), ga(3, 3, 4), ga(3, 3, 3))])
    h = m._foreign_holders(hass, {"u-mine"})
    assert h[str(ga(3, 3, 3))] == [("scene", 1)]
    assert h[str(ga(6, 0, 1))] == [("light", None)]
    assert h[str(ga(5, 5, 5))] == [(None, None)]
    assert str(ga(3, 3, 4)) not in h  # only Hauflo's own use it
    assert m._overlap_kind("scene", 1, True, True, []) is None
    assert m._overlap_kind("scene", 2, True, True, [("scene", 1)]) == "scene_shared"
    assert m._overlap_kind("scene", 1, True, True, [("scene", 1)]) == "foreign_write"
    assert m._overlap_kind("light", None, True, True, [("scene", 1)]) == "foreign_write"
    assert m._overlap_kind("light", None, False, True, [("light", None)]) == "duplicate"
    assert m._overlap_kind("light", None, False, False, [("light", None)]) == "shared_listen"
    assert m._overlap_kind("scene", True, False, True, [("scene", None)]) == "duplicate"


def test_knx_scenes_target(hass):
    reg = er.async_get(hass)
    reg.async_get_or_create("scene", "knx", "u-sc", suggested_object_id="evening")
    reg.async_get_or_create("scene", "knx", "u-hand", suggested_object_id="hand")
    reg.async_get_or_create("scene", "knx", "u-bad", suggested_object_id="badnum")
    store = {"scene": {
        "u-sc": {"knx": {"ga_scene": {"write": "3/3/3"}, "scene_number": 7}},
        "u-hand": {"knx": {"ga_scene": {"write": "3/3/9"}, "scene_number": 1}},
        "u-bad": {"knx": {"ga_scene": {"write": "3/3/8"}, "scene_number": 99}},
        "u-nodict": "x",
    }}
    hass.data["knx"] = knx(store)
    owned = {"u-sc", "u-bad", "u-nodict"}
    allowed = {"3/3"}
    assert m._knx_scenes_owned(None, owned) == {} and m._knx_scenes_owned(hass, set()) == {}
    assert set(m._knx_scenes_owned(hass, owned)) == {"scene.evening"}
    assert m._knx_scene_target(hass, owned, allowed, "scene.evening") == ("3/3/3", 7)
    for bad in (None, "light.x", "scene.evening;x"):
        with pytest.raises(PermissionError, match="not a scene"):
            m._knx_scene_target(hass, owned, allowed, bad)
    with pytest.raises(PermissionError, match="Hauflo's"):
        m._knx_scene_target(hass, owned, allowed, "scene.hand")  # the integrator's by hand
    with pytest.raises(PermissionError, match="allowed"):
        m._knx_scene_target(hass, owned, {"9/9"}, "scene.evening")
    # a switch listening on the same address would take the byte as a command: refused
    store["switch"] = {"u-sw": {"knx": {"ga": {"write": "3/3/3"}}}}
    with pytest.raises(PermissionError, match="only scenes"):
        m._knx_scene_target(hass, owned, allowed, "scene.evening")
    assert m._knx_scenes_learnable(hass, owned, allowed) == {}
    del store["switch"]
    assert m._knx_scenes_learnable(hass, owned, allowed) == {"scene.evening": 7}
    # the page may store only to the address of Hauflo's own entities
    assert m._owned_knx_writes(None, owned) == set()
    assert m._owned_knx_writes(hass, owned) == {str(ga(3, 3, 3)), str(ga(3, 3, 8))}


async def test_device_and_entity_ownership(hass):
    reg = er.async_get(hass)
    k = MockConfigEntry(domain="knx")
    k.add_to_hass(hass)
    z = MockConfigEntry(domain="zwave_js")
    z.add_to_hass(hass)
    d = dr.async_get(hass)
    dk = d.async_get_or_create(config_entry_id=k.entry_id, identifiers={("knx", "a")})
    dz = d.async_get_or_create(config_entry_id=z.entry_id, identifiers={("z", "a")})
    assert m._own_device(hass, dk.id) and not m._own_device(hass, dz.id)
    assert not m._own_device(None, dk.id) and not m._own_device(hass, 5) and not m._own_device(hass, "nope")
    reg.async_get_or_create("light", "knx", "u-1", device_id=dk.id, config_entry=k, suggested_object_id="a")
    reg.async_get_or_create("switch", "knx", "u-hand", device_id=dk.id, config_entry=k, suggested_object_id="b")
    assert m._device_entities_owned(hass, dk.id, {"u-1", "u-hand"})
    assert not m._device_entities_owned(hass, dk.id, {"u-1"})
    assert not m._device_entities_owned(hass, dk.id, None) and not m._device_entities_owned(None, dk.id, set()) and not m._device_entities_owned(hass, 1, set())

    def W(msg, owned=None):
        return m._ws_allowed(msg, hass, owned if owned is not None else {"u-1"}, set())

    assert not W({"type": "config/device_registry/remove", "device_id": dk.id})  # a hand-made entity on it
    assert W({"type": "config/device_registry/remove", "device_id": dk.id}, {"u-1", "u-hand"})
    assert not W({"type": "config/device_registry/remove_config_entry", "device_id": dz.id})
    assert W({"type": "config/device_registry/update", "device_id": dk.id, "id": 1, "area_id": "x"}, {"u-1", "u-hand"})
    assert not W({"type": "config/device_registry/update", "device_id": dk.id, "disabled_by": "user"}, {"u-1", "u-hand"})
    # entity registry updates: only Hauflo's own, never a lock, never a hand-made KNX one
    reg.async_get_or_create("lock", "template", "lk", suggested_object_id="front")
    reg.async_get_or_create("cover", "knx", "u-gate", config_entry=k, suggested_object_id="gate", original_device_class="gate")
    reg.async_get_or_create("sensor", "zwave_js", "zz", suggested_object_id="zs")

    def U(**kw):
        return W({"type": "config/entity_registry/update", **kw}, {"u-1", "u-gate"})

    assert U(entity_id="light.a")
    assert not U(entity_id="switch.b")  # hand-made KNX
    assert not U(entity_id="lock.front") and not U(entity_id=5) and not U(entity_id="sensor.zs")
    assert not U(entity_id="light.a", options_domain="cloud.alexa")
    assert not U(entity_id="light.a", new_entity_id="lock.front")
    assert not U(entity_id="cover.gate", device_class="door")  # a gate keeps its class
    assert U(entity_id="cover.gate", name="Gate")


def test_misc_pure(hass):
    assert m._own_entity(None, "x.y") is False and m._own_entity(hass, 3) is False and m._own_entity(hass, "light.nope") is False
    reg = er.async_get(hass)
    reg.async_get_or_create("light", "group", "g1", suggested_object_id="grp")
    reg.async_get_or_create("scene", "homeassistant", "hauflo_x", suggested_object_id="hs")
    reg.async_get_or_create("scene", "homeassistant", "mine", suggested_object_id="ms")
    assert not m._own_entity(hass, "light.grp")  # no config entry owned
    assert m._own_entity(hass, "scene.hs") and not m._own_entity(hass, "scene.ms")
    assert m._owns({"area:a"}, "area", "a") and not m._owns(None, "area", "a") and not m._owns({"area:a"}, "area", 3)
    for v in (None, "", "192.168.1.10", "10.0.0.5:3671", "1.1.250 - Name @ 192.168.1.10:3671", "manual", "auto", "169.254.1.1"):
        assert m._lan_host(v), v
    for v in ("8.8.8.8", "127.0.0.1", "0.0.0.0", "198.18.0.5", "224.0.23.12", "myhost", "evil.example.com", "fe80::1", 5, "::1"):
        assert not m._lan_host(v), v
    assert m.ha_url("https://u:p@x.example:8123/a/?q#f") == "https://x.example:8123/a"
    assert m.ha_url("ftp://x") is None and m.ha_url("") is None and m.ha_url(3) is None and m.ha_url("http://x:99999") is None
    assert m.knx_conn("x") is None
    c = m.knx_conn({"connection_type": "tunneling_secure", "host": "10.0.0.1", "port": 3671, "route_back": True, "user_password": "s", "individual_address": 5})
    assert c["secure"] and c["host"] == "10.0.0.1" and "user_password" not in str(c)
    assert not m.knx_conn({"connection_type": "tunneling"})["secure"] and m.knx_conn({"knxkeys_filename": "f"})["secure"]
