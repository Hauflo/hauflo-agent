"""Other integrations' entities hidden / shown / named from Hauflo's list (п.63.5/7): only hidden_by ∈ {user, None} and
a sane name, only on a device of another integration — never Hauflo's or KNX's, a phone's, a helper without a device,
a lock / the alarm / a valve, never another field (class, labels, area, voice, id)."""
import types

import test_tunnel_allowlist as t

ns, ents_reg, devs_reg, dev_ents = t.ns, t.ents_reg, t.devs_reg, t.dev_ents


def _ent(platform, domain, device_id=None):
    e = t._Ent(platform, domain)
    e.device_id = device_id
    return e


ents_reg["sensor.gtc_floor"] = _ent("gtc", "sensor", "dgtc")
ents_reg["sensor.helper"] = _ent("template", "sensor", None)
ents_reg["sensor.phone_battery"] = _ent("mobile_app", "sensor", "dphone")
ents_reg["sensor.mixed"] = _ent("zwave_js", "sensor", "dmix")
ents_reg["sensor.sun_next"] = _ent("sun", "sensor", "dsvc")
ents_reg["lock.gtc_child"] = _ent("gtc", "lock", "dgtc")
devs_reg["dgtc"] = types.SimpleNamespace(config_entries=["z1"], area_id="hall", entry_type=None)
devs_reg["dphone"] = types.SimpleNamespace(config_entries=["p1"], area_id=None, entry_type=None)
dev_ents["dgtc"] = [ents_reg["sensor.gtc_floor"]]

U = lambda **kw: t.W({"type": "config/entity_registry/update", **kw})


def test_foreign_entity_hide_show_rename():
    assert U(entity_id="sensor.gtc_floor", hidden_by="user")
    assert U(entity_id="sensor.gtc_floor", hidden_by=None)
    assert U(entity_id="sensor.gtc_floor", name="Пол")
    assert U(entity_id="sensor.gtc_floor", name=None)  # back to the integration's own
    assert U(entity_id="sensor.gtc_floor", hidden_by="user", name="Пол")


def test_foreign_entity_nothing_else():
    for bad in [
        dict(entity_id="sensor.gtc_floor"),  # nothing to change
        dict(entity_id="sensor.gtc_floor", hidden_by="integration"),
        dict(entity_id="sensor.gtc_floor", disabled_by="user"),
        dict(entity_id="sensor.gtc_floor", hidden_by="user", area_id="x"),
        dict(entity_id="sensor.gtc_floor", labels=["alisa"]),
        dict(entity_id="sensor.gtc_floor", device_class="door"),
        dict(entity_id="sensor.gtc_floor", new_entity_id="sensor.x"),
        dict(entity_id="sensor.gtc_floor", options_domain="conversation", options={"should_expose": True}),
        dict(entity_id="sensor.gtc_floor", name=""),
        dict(entity_id="sensor.gtc_floor", name="   "),
        dict(entity_id="sensor.gtc_floor", name="x" * 101),
        dict(entity_id="sensor.gtc_floor", name=5),
    ]:
        assert not U(**bad), bad


def test_foreign_entity_never_these():
    for eid in ("sensor.helper", "sensor.phone_battery", "sensor.mixed", "sensor.sun_next", "lock.gtc_child", "sensor.missing", "switch.knx_by_hand"):
        assert not U(entity_id=eid, hidden_by="user"), eid
