"""The tunnel allowlist (security review 25.10): exactly what hauflo's deploy uses, nothing by prefix.
Runs without Home Assistant installed: python3 agent/tests/test_tunnel_allowlist.py (or pytest)."""
import ast, contextlib, glob, ipaddress, json, pathlib, re, sys, types
src = open(pathlib.Path(__file__).parents[1] / "custom_components/hauflo/__init__.py").read()
tree = ast.parse(src)
HELPERS = ("_ws_allowed", "_ga_key", "_knx_listens", "_entry_domain", "_flow_handler", "_http_allowed", "_own_entity", "_own_device", "_owned_knx", "_forbidden", "_lan_host", "_flow_input_ok", "_unused_label", "_device_entities_owned", "_protected", "_own_dashboard", "_alice_labels", "_homekit_step_ok", "_hauflo_entity", "_knx_writes", "_foreign_knx_writes", "_knx_config_ok", "_flow_step", "_platform", "_lovelace_ok", "_area_lights_owned", "_image_ok", "_owned_knx_writes", "_label_ok", "_scrub_states", "_scrub_devices", "_scrub_entities", "_phone_ids", "_scrub_monitor", "_scrub_notes", "_ws_changes", "_http_changes", "_owns", "_area_floor_empty", "_foreign_holders", "_scrub_diagnostics", "_overlap_kind", "knx_data", "_knx_store", "_route", "_owned_entry", "_template_entities", "_rule_reads_ok", "_template_step_ok", "_switch_as_x_step_ok")
keep = [n for n in tree.body if (isinstance(n, ast.Assign) and any(getattr(t, "id", "").startswith("_") for t in n.targets)) or (isinstance(n, ast.FunctionDef) and n.name in HELPERS)]
import logging
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "custom_components/hauflo"))
from tunnel import TUNNEL  # noqa: E402 — plain Python, no HA
from tuning import TUNING  # noqa: E402
ns = {"TUNNEL": TUNNEL, "TUNING": TUNING, "json": json, "re": re, "contextlib": contextlib, "ipaddress": ipaddress, "Any": object, "HomeAssistant": object, "logging": logging, "__name__": "agent", "DOMAIN": "hauflo", "degrade": lambda feature, err: None}
# Compiled as the module is (`from __future__ import annotations`): annotations stay strings, so a name used only in
# a signature (Callable, ConfigEntry…) needs no stub here — Python 3.13 evaluated them and the whole file failed to load.
import __future__
exec(compile(ast.Module(body=keep, type_ignores=[]), "agent", "exec", flags=__future__.annotations.compiler_flag, dont_inherit=True), ns)
# Every helper the list names must be found: one renamed in __init__.py would otherwise be skipped without a word.
_missing = [n for n in HELPERS if n not in ns]
assert not _missing, f"helpers not found in __init__.py: {_missing}"
class E:  # entry
    def __init__(s, d): s.domain = d
class M:
    def __init__(s, flows): s.flows = flows
    def async_get(s, fid):
        if fid not in s.flows: raise KeyError(fid)
        return {"handler": s.flows[fid], "step_id": STEP.get(fid, "user")}
# HA's current step of a flow (the HomeKit options are checked by it).
STEP = {"o1": "init", "o1n": "include", "o1f": "include", "o1k": "include", "o1x": "include", "o1g": "include", "o1d": "bridged_device_triggers", "fhp": "pairing"}
entries = {"g1": E("group"), "k1": E("knx"), "y1": E("yandex_smart_home"), "h1": E("homekit"), "z1": E("zwave_js"), "m1": E("mqtt"), "p1": E("mobile_app"), "h2": E("homekit")}
hass = types.SimpleNamespace(config_entries=types.SimpleNamespace(async_get_entry=entries.get, async_entries=lambda d: [e for e in entries.values() if e.domain == d], flow=M({"f1": "group", "f2": "mqtt", "fk": "knx", "fh": "homekit", "fhp": "homekit"}), options=M({"o1": "h1", "o1n": "h1", "o1x": "h1", "o1g": "h1", "o1d": "h1", "o1f": "h1", "o1k": "h1", "o2": "z1", "oy": "y1", "ok": "k1"})))
# Registries: knx entity/device are Hauflo's; zwave's and a lock aren't.
class _Ent:
    def __init__(s, platform, domain, uid=None, entry=None): s.platform, s.domain, s.unique_id, s.config_entry_id = platform, domain, uid, entry
class _Dev:
    def __init__(s, entries): s.config_entries = entries
ents_reg = {"light.knx_1": _Ent("knx", "light", "u-hauflo"), "switch.knx_by_hand": _Ent("knx", "switch", "u-hand"), "binary_sensor.smoke_z": _Ent("zwave_js", "binary_sensor"), "scene.hauflo_r1_evening": _Ent("homeassistant", "scene", "hauflo_r1_evening"), "scene.my_evening": _Ent("homeassistant", "scene", "1690000000000"), "cover.knx_blind": _Ent("knx", "cover", "u-blind"), "switch.rele_vorot": _Ent("template", "switch")}
devs_reg = {"dk": _Dev(["k1"]), "dz": _Dev(["z1"])}
dev_ents = {"dk": [_Ent("knx", "light", "u-hauflo")], "dk2": [_Ent("knx", "light", "u-hauflo"), _Ent("knx", "switch", "u-hand")]}
devs_reg["dk2"] = _Dev(["k1"])
ns["er"] = types.SimpleNamespace(async_get=lambda h: types.SimpleNamespace(async_get=ents_reg.get), async_entries_for_device=lambda reg, d, include_disabled_entities=False: dev_ents.get(d, []))
_labels = {"alisa": ["light.knx_1", "cover.knx_blind"], "integrator": ["light.knx_1", "lock.front"], "byhand": ["switch.knx_by_hand"]}
_dev_labels = {"ondevice": ["dk"]}
ns["er"].async_entries_for_label = lambda reg, lid: [types.SimpleNamespace(entity_id=e) for e in _labels.get(lid, [])]
ns["dr"] = types.SimpleNamespace(async_get=lambda h: types.SimpleNamespace(async_get=devs_reg.get), async_entries_for_label=lambda reg, lid: _dev_labels.get(lid, []))
# Rooms: the kitchen's only light is Hauflo's; the hall also has the family's own lamp (on a device placed there).
_area_ents = {"kitchen": [types.SimpleNamespace(entity_id="light.knx_1", domain="light", area_id="kitchen")], "hall": [types.SimpleNamespace(entity_id="light.knx_1", domain="light", area_id="hall")]}
_area_devs = {"hall": [types.SimpleNamespace(id="dhue")]}
dev_ents["dhue"] = [types.SimpleNamespace(entity_id="light.hue_lamp", domain="light", area_id=None, platform="hue")]
ents_reg["light.hue_lamp"] = _Ent("hue", "light")
ents_reg["cover.garage_door"] = _Ent("template", "cover")
ents_reg["valve.front_garden"] = _Ent("template", "valve")
ns["er"].async_entries_for_area = lambda reg, a: _area_ents.get(a, [])
ns["dr"].async_entries_for_area = lambda reg, a: _area_devs.get(a, [])
ns["DOMAIN"] = "hauflo"
# KNX UI store: Hauflo's light and the integrator's gate relay by hand.
class _GA:  # xknx's GroupAddress: hashable, its number as .raw
    def __init__(s, raw): s.raw = raw
    def __hash__(s): return s.raw
# A YAML entity of the integrator's (configuration.yaml): only in the module's address map, not in the UI store.
_yaml_gas = {_GA((7 << 11) | (6 << 8) | 1): [types.SimpleNamespace(unique_id="yaml-gate", ui=False)], _GA((1 << 11) | (1 << 8) | 1): [types.SimpleNamespace(unique_id="u-hauflo", ui=True)]}
hass.data = {"knx": types.SimpleNamespace(group_address_entities=_yaml_gas, config_store=types.SimpleNamespace(data={"entities": {"light": {"u-hauflo": {"knx": {"ga_switch": {"write": "1/1/1", "state": "1/1/2", "passive": []}}}}, "switch": {"u-hand": {"knx": {"ga_switch": {"write": "7/7/7", "state": "7/7/6", "passive": ["7/7/9"]}}}}}}))}
# What xknx itself runs: an exposure of the integrator's to 5/5/5 and the time server to 5/5/6 (no entity for either),
# and the device of Hauflo's own light (1/1/1, 1/1/2).
class _XDev:
    def __init__(s, gas): s._g = gas
    def group_addresses(s): return {_GA(g) for g in s._g}
hass.data["knx"].config_store.data["entities"]["scene"] = {"u-iscene": {"knx": {"ga_scene": {"write": "3/3/3"}, "scene_number": 1}}}
hass.data["knx"].xknx = types.SimpleNamespace(devices=[_XDev([(3 << 11) | (3 << 8) | 3]), _XDev([(5 << 11) | (5 << 8) | 5]), _XDev([(5 << 11) | (5 << 8) | 6]), _XDev([(1 << 11) | (1 << 8) | 1, (1 << 11) | (1 << 8) | 2])])
ns["_LOGGER"] = logging.getLogger("t")
OWNED_ENTRIES = {"h1", "g1"}  # a bridge and a group Hauflo set up through the tunnel
MEMO = {"o1n": ["light", "cover"], "o1x": ["light", "cover"], "o1g": ["cover"], "o1f": ["switch"], "o1k": ["switch"]}  # the domains each options flow named in its first step
H = lambda m, p, b=None: ns["_http_allowed"](hass, m, p, json.dumps(b) if b is not None else None, OWNED_ENTRIES, MEMO, {"u-hauflo", "u-blind"})
W = lambda msg: ns["_ws_allowed"](msg, hass, {"u-hauflo"}, OWNED_ENTRIES)
LV = lambda cfg: W({"type": "lovelace/config/save", "url_path": "hauflo-test", "config": cfg})
cases = [
 (H("POST", "/api/config/config_entries/flow", {"handler": "group"}), True),
 (H("POST", "/api/config/config_entries/flow", {"handler": "mqtt"}), False),
 (H("POST", "/api/config/config_entries/flow/f1", {}), True),
 (H("POST", "/api/config/config_entries/flow/f2", {}), False),
 (H("POST", "/api/config/config_entries/options/flow", {"handler": "h1"}), True),
 (H("POST", "/api/config/config_entries/options/flow", {"handler": "z1"}), False),
 (H("POST", "/api/config/config_entries/options/flow", {"handler": "k1"}), False),  # the integrator's KNX link (panel round 6)
 (H("POST", "/api/config/config_entries/options/flow/o1", {}), False),
 (H("POST", "/api/config/config_entries/options/flow/o2", {}), False),
 (H("DELETE", "/api/config/config_entries/entry/g1"), True),
 (H("DELETE", "/api/config/config_entries/entry/k1"), False),
 (H("POST", "/api/config/scene/config/hauflo_r1_evening", {}), True),
 (H("POST", "/api/config/scene/config/my_scene", {}), False),
 (H("POST", "/api/config/automation/config/x", {}), False),
 (H("POST", "/api/config/script/config/x", {}), False),
 (H("GET", "/api/diagnostics/config_entry/y1"), True),
 (H("GET", "/api/diagnostics/config_entry/m1"), False),
 # Narrow round 25.10 (C3): another's HomeKit bridge's diagnostics gave a phone's coordinates — only Hauflo's own,
 # and only the filter / Alice's label settings leave the house.
 (H("GET", "/api/diagnostics/config_entry/h1"), True),
 (H("GET", "/api/diagnostics/config_entry/h2"), False),
 (H("GET", "/api/diagnostics/config_entry/k1"), False),
 (H("GET", "/api/diagnostics/config_entry/g1"), False),
 (json.loads(ns["_scrub_diagnostics"](json.dumps({"data": {"config-entry": {"options": {"filter": {"include_domains": ["person"]}, "secret": 1}}, "accessories": [{"entity_state": {"attributes": {"latitude": 59.9}}}], "bridge": {"name": "iPhone Ивана"}}}))), {"data": {"config-entry": {"options": {"filter": {"include_domains": ["person"]}}}}}),
 (json.loads(ns["_scrub_diagnostics"](json.dumps({"data": {"entry": {"options": {"filter_source": "label", "label": "alisa", "token": "x"}}, "devices": [1]}}))), {"data": {"entry": {"options": {"filter_source": "label", "label": "alisa"}}}}),
 (H("GET", "/api/homekit/pairingqr?h1-abc"), True),
 (H("POST", "/api/hauflo/sidebar", {}), True),
 (H("GET", "/api/hauflo/alarms?x=1"), True),
 (H("GET", "/api/config/core/check_config"), False),
 (H("GET", "/api/config/auth/list"), False),
 (H("GET", "/api/hassio/x"), False),
 (H("GET", "/api/hauflo/../config/auth"), False),
 (H("GET", "/api/hauflo/%2e%2e/x"), False),
 (W({"type": "lovelace/resources/create"}), False),
 (W({"type": "config/auth/create"}), False),
 (W({"type": "config_entries/flow"}), False),
 (W({"type": "call_service", "domain": "lock", "service": "unlock"}), False),
 (W({"type": "call_service", "domain": "frontend", "service": "set_theme"}), True),
 (W({"type": "knx/create_entity"}), True),
 (W({"type": "config/entity_registry/update"}), False),
 (W({"type": "render_template"}), False),
 (W({"type": "config/entity_registry/update", "entity_id": "light.knx_1", "area_id": "x"}), True),
 (W({"type": "config/entity_registry/update", "entity_id": "binary_sensor.smoke_z", "disabled_by": "user"}), False),
 (W({"type": "config/entity_registry/update", "entity_id": "scene.hauflo_r1_evening", "area_id": "x"}), True),
 (W({"type": "config/device_registry/update", "device_id": "dk", "area_id": "x"}), True),
 (W({"type": "config/device_registry/update", "device_id": "dz", "area_id": "x"}), False),
 (W({"type": "homeassistant/expose_entity", "assistants": ["conversation"], "entity_ids": ["light.knx_1"], "should_expose": True}), True),
 (W({"type": "homeassistant/expose_entity", "assistants": ["conversation"], "entity_ids": ["light.knx_1", "lock.front"], "should_expose": True}), False),
 # Integrator run 20: never the integrator's own scene / KNX entity handed to a voice assistant; taken away is fine.
 (W({"type": "homeassistant/expose_entity", "assistants": ["conversation"], "entity_ids": ["scene.my_evening"], "should_expose": True}), False),
 (W({"type": "homeassistant/expose_entity", "assistants": ["conversation"], "entity_ids": ["switch.knx_by_hand"], "should_expose": True}), False),
 (W({"type": "homeassistant/expose_entity", "assistants": ["conversation"], "entity_ids": ["scene.hauflo_r1_evening"], "should_expose": True}), True),
 # Security expert 25.10: taking one away from voice is the integrator's setup too — only Hauflo's own.
 (W({"type": "homeassistant/expose_entity", "assistants": ["conversation"], "entity_ids": ["scene.my_evening"], "should_expose": False}), False),
 (W({"type": "knx/project_file_process"}), False),
 (H("POST", "/api/config/scene/config/hauflo_r1_evening", {"entities": {"light.knx_1": {"state": "on"}, "cover.knx_blind": {"state": "open"}}}), True),
 # Panel round 6: a Hauflo scene or group holds only Hauflo's own — not the integrator's gate relay, not a KNX one by hand.
 (H("POST", "/api/config/scene/config/hauflo_r1_evening", {"entities": {"light.knx_1": {"state": "on"}, "switch.rele_vorot": {"state": "on"}}}), False),
 (H("POST", "/api/config/scene/config/hauflo_r1_evening", {"entities": {"switch.knx_by_hand": {"state": "on"}}}), False),
 (H("POST", "/api/config/config_entries/flow/f1", {"name": "All covers", "entities": ["cover.knx_blind"]}), True),
 (H("POST", "/api/config/config_entries/flow/f1", {"name": "All covers", "entities": ["cover.knx_blind", "switch.rele_vorot"]}), False),
 (H("POST", "/api/config/config_entries/flow/f1", {"name": "All", "entities": "cover.knx_blind"}), False),
 (H("POST", "/api/config/scene/config/hauflo_r1_evening", {"entities": {"lock.front": {"state": "unlocked"}}}), False),
 (W({"type": "execute_script"}), False),
 # Security re-review round 3: KNX entities by hand stay the integrator's; flow inputs are checked.
 (W({"type": "knx/delete_entity", "entity_id": "light.knx_1"}), True),
 # Panel round 6: the integration list only for the domains Hauflo sets up.
 (W({"type": "config_entries/get", "domain": "homekit"}), True),
 (W({"type": "config_entries/get", "domain": "hauflo"}), True),  # the dashboard checks the agent's cards are there
 (W({"type": "config_entries/get", "domain": "mobile_app"}), False),
 (W({"type": "config_entries/get"}), False),
 # Panel round 6: never sending to the integrator's own entity's address (its gate relay), on create or update.
 (W({"type": "knx/create_entity", "platform": "light", "data": {"knx": {"ga_switch": {"write": "1/2/3", "state": "7/7/7", "passive": ["7/7/7"]}}}}), True),
 (W({"type": "knx/create_entity", "platform": "light", "data": {"knx": {"ga_switch": {"write": "7/7/7", "state": None, "passive": []}}}}), False),
 # Integrator run 22: the same address spelled otherwise — two-level, free, a number, leading zeros — is the same.
 *[(W({"type": "knx/create_entity", "platform": "switch", "data": {"knx": {"ga_switch": {"write": ga, "state": None, "passive": []}}}}), False) for ga in ("7/1799", "16135", 16135, "07/07/007", " 7/7/7 ")],
 (W({"type": "knx/create_entity", "platform": "switch", "data": {"knx": {"ga_switch": {"write": "7/x/7", "state": None, "passive": []}}}}), False),
 (W({"type": "knx/create_entity", "platform": "switch", "data": {"knx": {"ga_switch": {"write": ["7/7/7"], "state": None, "passive": []}}}}), False),
 (W({"type": "knx/create_entity", "platform": "switch", "data": {"knx": {"ga_switch": {"write": "7/7/8", "state": None, "passive": []}}}}), True),
 # Integrator run 23: the hand-made switch's state / passive addresses are its too (the actuator obeys a passive one).
 *[(W({"type": "knx/create_entity", "platform": "switch", "data": {"knx": {"ga_switch": {"write": ga, "state": None, "passive": []}}}}), False) for ga in ("7/7/6", "7/1798", "7/7/9", "7/1801")],
 (W({"type": "knx/update_entity", "entity_id": "light.knx_1", "platform": "light", "data": {"knx": {"ga_switch": {"write": "7/7/9", "state": "1/1/2", "passive": []}}}}), False),
 # Integrator run 23b: the integrator's YAML entity's address (7/6/1) is his too; Hauflo's own 1/1/1 stays Hauflo's.
 *[(W({"type": "knx/create_entity", "platform": "switch", "data": {"knx": {"ga_switch": {"write": ga, "state": None, "passive": []}}}}), False) for ga in ("7/6/1", "15873", "7/1537")],
 (W({"type": "knx/update_entity", "entity_id": "light.knx_1", "platform": "light", "data": {"knx": {"ga_switch": {"write": "1/1/1", "state": "7/6/1", "passive": []}}}}), True),
 (W({"type": "knx/update_entity", "entity_id": "light.knx_1", "platform": "light", "data": {"knx": {"ga_switch": {"write": "7/1799", "state": "1/1/2", "passive": []}}}}), False),
 (W({"type": "knx/update_entity", "entity_id": "light.knx_1", "platform": "light", "data": {"knx": {"ga_switch": {"write": "1/1/1", "state": "1/1/2", "passive": []}}}}), True),
 (W({"type": "knx/update_entity", "entity_id": "light.knx_1", "platform": "light", "data": {"knx": {"ga_switch": {"write": "7/7/7", "state": "1/1/2", "passive": []}}}}), False),
 (W({"type": "knx/create_entity", "platform": "cover", "data": {"knx": {"ga_up_down": {"write": "1/4/1"}, "ga_stop": {"write": "7/7/7"}}}}), False),
 (W({"type": "knx/delete_entity", "entity_id": "switch.knx_by_hand"}), False),
 (W({"type": "knx/delete_entity", "entity_id": "light.nowhere"}), False),
 (ns["_ws_allowed"]({"type": "knx/delete_entity", "entity_id": "light.knx_1"}, hass, None), False),
 (H("POST", "/api/config/config_entries/options/flow/o1", {"mode": "bridge", "include_exclude_mode": "include", "domains": ["light", "cover"]}), True),
 (H("POST", "/api/config/config_entries/options/flow/o1", {"mode": "bridge", "include_exclude_mode": "include", "domains": ["light", "lock"]}), False),
 (H("POST", "/api/config/config_entries/options/flow/o1", {"entities": ["light.a", "alarm_control_panel.home"]}), False),
 (H("POST", "/api/config/config_entries/options/flow/o1", {"mode": "bridge", "include_exclude_mode": "exclude", "domains": ["light"]}), False),
 (H("POST", "/api/config/config_entries/options/flow/o1", {"mode": "accessory", "domains": ["light"]}), False),
 (H("POST", "/api/config/config_entries/flow/fh", {"include_domains": ["valve"]}), False),
 (H("POST", "/api/config/config_entries/flow/fh", {"include_domains": ["person"]}), True),
 # Integrator run 20: a bridge only of entities chosen — an options «include» step without one of each named domain
 # left «cover» whole (the garage door on Apple Home); setup only on «person» (a whole «switch» could be a gate relay).
 (H("POST", "/api/config/config_entries/options/flow/o1n", {}), False),
 (H("POST", "/api/config/config_entries/options/flow/o1x", {"entities": "light.a"}), False),
 (H("POST", "/api/config/config_entries/options/flow/o1x", {"entities": ["light.a"]}), False),
 (H("POST", "/api/config/config_entries/options/flow/o1n", {"entities": ["light.knx_1", "cover.knx_blind"]}), True),
 (H("POST", "/api/config/config_entries/options/flow/o1n", {"entities": ["light.knx_1", "cover.knx_blind"]}), False),
 # Integrator run 21: only Hauflo's own on its bridge — not the integrator's gate relay, not a KNX entity by hand.
 (H("POST", "/api/config/config_entries/options/flow/o1f", {"entities": ["switch.rele_vorot"]}), False),
 (H("POST", "/api/config/config_entries/options/flow/o1k", {"entities": ["switch.knx_by_hand"]}), False),
 (H("POST", "/api/config/config_entries/options/flow/o1d", {"devices": []}), True),
 (H("POST", "/api/config/config_entries/options/flow/o1d", {"devices": [], "filter": {"include_domains": ["cover"]}}), False),
 (H("POST", "/api/config/config_entries/flow/fh", {"include_domains": ["switch"]}), False),
 (H("POST", "/api/config/config_entries/flow/fh", {"include_domains": []}), False),
 (H("POST", "/api/config/config_entries/flow/fh", {"include_domains": ["person", "script"]}), False),
 (H("POST", "/api/config/config_entries/flow/fhp", {}), True),
 (H("POST", "/api/config/config_entries/options/flow/o1", {"mode": "bridge", "domains": ["light"]}), False),
 (H("POST", "/api/config/config_entries/options/flow/oy", {"filter_source": "label", "label": "hauflo"}), False),
 (H("POST", "/api/config/config_entries/options/flow/oy", {"filter_source": "config_entry"}), False),
 (H("POST", "/api/config/config_entries/flow", {"handler": "yandex_smart_home"}), False),
 (H("POST", "/api/config/config_entries/flow/f1", {"name": "Locks", "entities": ["lock.a", "lock.b"]}), False),
 (H("POST", "/api/config/config_entries/flow/fk", {"connection_type": "tunneling"}), True),
 (H("POST", "/api/config/config_entries/flow/fk", {"host": "192.168.1.20", "port": 3671, "route_back": False}), True),
 (H("POST", "/api/config/config_entries/flow/fk", {"host": "5.188.10.9", "port": 3671}), False),
 (H("POST", "/api/config/config_entries/flow/fk", {"host": "knx.attacker.example"}), False),
 (H("POST", "/api/config/config_entries/flow/fk", {"gateway": "10.0.0.5:3671"}), True),
 (H("POST", "/api/config/config_entries/flow/fk", {"multicast_group": "224.0.23.12", "local_ip": "", "individual_address": "1.2.250"}), True),
 (H("POST", "/api/config/config_entries/flow/fk", {"multicast_group": "8.8.8.8"}), False),
 (H("POST", "/api/config/config_entries/options/flow/ok", {"telegram_store_section": {"telegram_db_backend": "postgres"}}), False),
 (H("POST", "/api/config/config_entries/options/flow/ok", {"telegram_db_postgres_dsn": "postgres://x@evil/db"}), False),
 # Security expert 25.10: a dashboard through the tunnel — only generated cards, Hauflo's own entities, fixed actions.
 *[(LV(c), False) for c in (
   {"views": [{"cards": [{"type": "tile", "entity": "switch.rele_vorot"}]}]},
   {"views": [{"cards": [{"type": "button", "name": "Свет кухня", "tap_action": {"action": "perform-action", "perform_action": "switch.turn_on", "target": {"entity_id": "switch.knx_by_hand"}}}]}]},
   {"views": [{"cards": [{"type": "button", "tap_action": {"action": "perform-action", "perform_action": "lock.unlock", "target": {"entity_id": "light.knx_1"}}}]}]},
   {"views": [{"cards": [{"type": "custom:hauflo-alert", "alarms": [], "action": {"title": "x", "service": "script.turn_on", "entity_id": "light.knx_1"}}]}]},
   {"views": [{"cards": [{"type": "tile", "entity": "light.knx_1", "tap_action": {"action": "url", "url_path": "https://evil.example"}}]}]},
   {"views": [{"cards": [{"type": "tile", "entity": "light.knx_1", "hold_action": {"action": "navigate", "navigation_path": "/config/automation/edit/new"}}]}]},
   {"views": [{"cards": [{"type": "iframe", "url": "https://evil.example"}]}]},
   {"views": [{"cards": [{"type": "custom:hauflo-security", "entity": "alarm_control_panel.home"}]}]},
   {"views": [{"cards": [{"type": "tile", "entity": "lock.front"}]}]},
   {"views": [{"cards": [{"type": "tile", "entity": "scene.hauflo_r1_evening", "hold_action": {"action": "perform-action", "perform_action": "knx.send", "data": {"address": "7/7/7", "payload": [128]}}}]}]},
   {"views": [{"cards": [{"type": "button", "tap_action": {"action": "perform-action", "perform_action": "switch.turn_on", "target": {"area_id": "kitchen"}}}]}]},
   {"views": [{"cards": [{"type": "custom:hauflo-room", "path": "/lovelace/0", "lights": []}]}]},
   "views: []",
 )],
 *[(LV(c), True) for c in (
   {"views": [{"cards": [{"type": "tile", "entity": "light.knx_1", "icon_tap_action": {"action": "toggle"}}]}]},
   {"views": [{"cards": [{"type": "tile", "entity": "scene.hauflo_r1_evening", "hold_action": {"action": "perform-action", "perform_action": "knx.send", "data": {"address": "1/1/1", "payload": [128]}}}]}]},
   {"views": [{"cards": [{"type": "button", "tap_action": {"action": "perform-action", "perform_action": "light.turn_off", "target": {"area_id": "kitchen"}}}]}]},
   {"views": [{"cards": [{"type": "custom:hauflo-alert", "alarms": ["binary_sensor.smoke_z"], "service": {"phone": "+7 900", "name": "ИП"}, "action": {"title": "Перекрыть воду", "service": "switch.turn_off", "entity_id": "light.knx_1"}}]}]},
   {"views": [{"cards": [{"type": "custom:hauflo-hero", "weather": "weather.home", "alarms": ["binary_sensor.smoke_z"]}]}]},
 )],
 (W({"type": "lovelace/config/save", "url_path": "lovelace", "config": {"views": []}}), False),
 # Security expert 25.10: Alice's label by what it carries.
 *[(ns["_flow_input_ok"](hass, "yandex_smart_home", {"filter_source": "label", "label": lab}, None, "fy", "choose_label", {"u-hauflo", "u-blind"}, OWNED_ENTRIES), want) for lab, want in (("alisa", True), ("integrator", False), ("byhand", False), ("ondevice", False), ("", False))],
 # Security expert 25.10: reads leave the house edited — another's HomeKit QR / PIN, the family's dashboards, phones' names, bus values.
 (H("GET", "/api/homekit/pairingqr?h2-abc"), False),
 (H("GET", "/api/homekit/pairingqr"), False),
 (W({"type": "lovelace/config", "url_path": "lovelace"}), False),
 (W({"type": "lovelace/config"}), False),
 (W({"type": "lovelace/config", "url_path": "hauflo-home"}), True),
 (ns["_scrub_states"]([{"entity_id": "person.ivan", "state": "home", "attributes": {"latitude": 1}, "last_changed": "x", "context": {"user_id": "u"}}], None)[0], {"entity_id": "person.ivan", "state": "hidden", "attributes": {}}),
 (ns["_scrub_states"]([{"entity_id": "lock.front", "state": "unlocked", "attributes": {}}], None)[0]["state"], "hidden"),
 (ns["_scrub_states"]([{"entity_id": "alarm_control_panel.home", "state": "disarmed", "attributes": {}}], None)[0]["state"], "hidden"),
 (ns["_scrub_devices"]([{"id": "d1", "config_entries": ["p1"], "name": "iPhone Ivan", "model": "iPhone15"}, {"id": "d2", "config_entries": ["k1"], "name": "Актуатор"}], hass), [{"id": "d1", "config_entries": ["p1"], "name": None, "model": None}, {"id": "d2", "config_entries": ["k1"], "name": "Актуатор"}]),
 (ns["_scrub_monitor"]({"project_loaded": True, "recent_telegrams": [{"destination": "1/1/1", "direction": "Incoming", "telegramtype": "GroupValueWrite", "timestamp": "t", "value": 21.5, "source": "1.1.5", "payload": [1]}]}), {"project_loaded": True, "recent_telegrams": [{"destination": "1/1/1", "direction": "Incoming", "telegramtype": "GroupValueWrite", "timestamp": "t"}]}),
 (ns["_scrub_notes"]({"notifications": {"h1": {"m": "PIN 1"}, "h2": {"m": "PIN 2"}, "x": {"m": "login failed"}}}, hass, OWNED_ENTRIES), {"notifications": {"h1": {"m": "PIN 1"}}}),
 # Security expert 25.10: a change through the tunnel needs a fresh backup — what reads never does.
 *[(ns["_ws_changes"](m), want) for m, want in (({"type": "knx/create_entity"}, True), ({"type": "lovelace/config/save"}, True), ({"type": "config/area_registry/update"}, True), ({"type": "homeassistant/expose_entity"}, True), ({"type": "get_states"}, False), ({"type": "lovelace/config"}, False), ({"type": "call_service", "domain": "homeassistant", "service": "update_entity"}, False), ({"type": "call_service", "domain": "frontend", "service": "set_theme"}, True), ({"type": "auth"}, False))],
 *[(ns["_http_changes"](m, p), want) for m, p, want in (("POST", "/api/config/config_entries/flow", True), ("DELETE", "/api/config/scene/config/hauflo_x", True), ("GET", "/api/config/config_entries/flow/x", False), ("POST", "/api/hauflo/backup", False), ("POST", "/api/hauflo/notify", False), ("POST", "/api/hauflo/sidebar", True))],
 (ns["_WS_READS"] <= ns["_WS_EXACT"], True),
 # Security expert 25.10: one ownership — Hauflo changes only what it made.
 (W({"type": "config/device_registry/update", "device_id": "dk", "disabled_by": "user"}), False),
 (W({"type": "config/device_registry/update", "device_id": "dk", "area_id": "kitchen"}), True),
 (W({"type": "config/device_registry/update", "device_id": "dk2", "area_id": "kitchen"}), False),
 (W({"type": "config/device_registry/update", "device_id": "dz", "area_id": "kitchen"}), False),
 (ns["_ws_allowed"]({"type": "config/area_registry/update", "area_id": "a1", "name": "Кухня", "aliases": ["кухня"]}, hass, {"u-hauflo"}, {"area:a1"}), True),
 (W({"type": "config/area_registry/update", "area_id": "kitchen", "name": "Кухня-PWNED", "aliases": ["открой ворота"]}), False),
 (W({"type": "config/area_registry/update", "area_id": "kitchen", "aliases": ["открой ворота"]}), False),
 (ns["_ws_allowed"]({"type": "config/floor_registry/update", "floor_id": "f1", "name": "1 этаж"}, hass, {"u-hauflo"}, {"floor:f1"}), True),
 (W({"type": "config/floor_registry/update", "floor_id": "theirs", "name": "x"}), False),
 (W({"type": "config/label_registry/delete", "label_id": "integrator"}), False),
 (W({"type": "homeassistant/expose_entity", "assistants": ["conversation"], "entity_ids": ["switch.knx_by_hand"], "should_expose": False}), False),
 (W({"type": "homeassistant/expose_entity", "assistants": ["conversation"], "entity_ids": ["light.knx_1"], "should_expose": False}), True),
 # HA expert 25.10: an exposure / the time server sends to the bus too — no Hauflo write there.
 *[(W({"type": "knx/create_entity", "platform": "switch", "data": {"knx": {"ga_switch": {"write": ga, "state": None, "passive": []}}}}), False) for ga in ("5/5/5", "5/5/6", "11525")],
 (W({"type": "knx/update_entity", "entity_id": "light.knx_1", "platform": "light", "data": {"knx": {"ga_switch": {"write": "1/1/1", "state": "1/1/2", "passive": []}}}}), True),
 # KNX expert 25.10: a scene on the room's scene address the integrator's scene uses — another number only, scenes only.
 (W({"type": "knx/create_entity", "platform": "scene", "data": {"knx": {"ga_scene": {"write": "3/3/3"}, "scene_number": 2}}}), True),
 (W({"type": "knx/create_entity", "platform": "scene", "data": {"knx": {"ga_scene": {"write": "3/3/3"}, "scene_number": 1}}}), False),
 (W({"type": "knx/create_entity", "platform": "switch", "data": {"knx": {"ga_switch": {"write": "3/3/3", "state": None, "passive": []}}}}), False),
 (W({"type": "knx/create_entity", "platform": "scene", "data": {"knx": {"ga_scene": {"write": "5/5/5"}, "scene_number": 2}}}), False),
 (W({"type": "knx/create_entity", "platform": "scene", "data": {"knx": {"ga_scene": {"write": "7/7/7"}, "scene_number": 2}}}), False),
 (W({"type": "knx/get_expose_groups"}), True),
 (W({"type": "knx/get_time_server_config"}), True),
 # KNX expert 25.10: reconfigure only Hauflo's own KNX link; no second setup; secure keys, Postgres and unbounded rate refused.
 (H("POST", "/api/config/config_entries/flow", {"handler": "knx", "entry_id": "k1"}), False),
 (H("POST", "/api/config/config_entries/flow", {"handler": "knx"}), False),
 (H("POST", "/api/config/config_entries/flow", {"handler": "group", "entry_id": "g1"}), False),
 (ns["_http_allowed"](hass, "POST", "/api/config/config_entries/flow", json.dumps({"handler": "knx", "entry_id": "k1"}), {"k1"}, MEMO, set()), True),
 (H("POST", "/api/config/config_entries/flow/fk", {"gateway": "1.1.250 - IP Interface @ 192.168.1.10:3671"}), True),
 (H("POST", "/api/config/config_entries/flow/fk", {"gateway": "1.1.250 - Evil @ 5.188.10.9:3671"}), False),
 *[(H("POST", "/api/config/config_entries/flow/fk", {"host": h}), False) for h in ("127.0.0.1", "0.0.0.0", "198.18.0.5", "240.0.0.1", "fd00::5", "[fe80::1]")],
 (H("POST", "/api/config/config_entries/flow/fk", {"user_id": 2, "user_password": "x"}), False),
 (H("POST", "/api/config/config_entries/flow/fk", {"backbone_key": "00"}), False),
 (H("POST", "/api/config/config_entries/flow/fk", {"knxkeys_filename": "a.knxkeys"}), False),
 (ns["_http_allowed"](hass, "POST", "/api/config/config_entries/options/flow/ok", json.dumps({"rate_limit": 20, "state_updater": True, "telegram_store_section": {"telegram_db_backend": "sqlite"}}), {"k1"}, MEMO, set()), True),
 *[(ns["_http_allowed"](hass, "POST", "/api/config/config_entries/options/flow/ok", json.dumps({"rate_limit": r}), {"k1"}, MEMO, set()), False) for r in (0, 61, "20", True)],
 (ns["_http_allowed"](hass, "POST", "/api/config/config_entries/options/flow/ok", json.dumps({"state_updater": "yes"}), {"k1"}, MEMO, set()), False),
 (ns["_http_allowed"](hass, "POST", "/api/config/config_entries/options/flow/ok", json.dumps({"telegram_store_section": {"telegram_db_backend": "postgres"}}), {"k1"}, MEMO, set()), False),
 (H("POST", "/api/config/config_entries/options/flow", {"handler": "k1"}), False),
 # Security re-review round 4: update and device removal only for Hauflo's own.
 (W({"type": "knx/update_entity", "entity_id": "light.knx_1", "platform": "light", "data": {}}), True),
 (W({"type": "knx/update_entity", "entity_id": "switch.knx_by_hand", "platform": "switch", "data": {}}), False),
 (W({"type": "config/device_registry/remove_config_entry", "device_id": "dk", "config_entry_id": "k1"}), True),
 (W({"type": "config/device_registry/remove_config_entry", "device_id": "dk2", "config_entry_id": "k1"}), False),
 (W({"type": "config/device_registry/remove", "device_id": "dz"}), False),
 # Security re-review round 4: Alice options, registry options, dashboards, views, bare host names.
 (H("POST", "/api/config/config_entries/options/flow", {"handler": "y1"}), False),
 (H("POST", "/api/config/config_entries/options/flow/oy", {"filter_source": "label"}), False),
 (H("POST", "/api/config/config_entries/flow", {"handler": "yandex_smart_home"}), False),
 (W({"type": "config/entity_registry/update", "entity_id": "light.knx_1", "options_domain": "conversation", "options": {"should_expose": True}}), False),
 (W({"type": "config/entity_registry/update", "entity_id": "switch.knx_by_hand", "labels": ["alice"]}), False),
 (W({"type": "lovelace/config/save", "url_path": "hauflo-1b8b40cd", "config": {}}), True),
 # A copy is the agent's alone (security re-review 28.10): never read, made, written or shown through the tunnel.
 (W({"type": "lovelace/config/save", "url_path": "hauflo-1b8b40cd-copy-20261026", "config": {}}), False),
 (W({"type": "lovelace/config/save", "url_path": "hauflo-1b8b40cd-copy-20261028035959-a1b2c3", "config": {}}), False),
 (W({"type": "lovelace/config", "url_path": "hauflo-1b8b40cd-copy-202610260000"}), False),
 (W({"type": "lovelace/dashboards/create", "url_path": "hauflo-1b8b40cd-copy-209912312359", "title": "x", "mode": "storage"}), False),
 (W({"type": "lovelace/dashboards/create", "url_path": "hauflo-1b8b40cd", "title": "x", "mode": "storage"}), True),
 (W({"type": "lovelace/config", "url_path": "hauflo-1b8b40cd"}), True),
 (W({"type": "lovelace/config/save", "url_path": None, "config": {}}), False),
 (W({"type": "lovelace/config/save", "url_path": "dashboard-home", "config": {}}), False),
 (H("DELETE", "/api/hauflo/notify"), False),
 (H("GET", "/api/hauflo/whatever"), False),
 # «Зима / Лето» (0.17): Hauflo's settings read and written, nothing else on that path; «Комфортный пол» is gone, and
 # the family's own switch is never the tunnel's.
 (H("GET", "/api/hauflo/season/config"), True),
 (H("POST", "/api/hauflo/season/config", {"exclude": []}), True),
 (H("DELETE", "/api/hauflo/season/config"), False),
 (H("POST", "/api/hauflo/season/config/x", {}), False),
 (H("POST", "/api/hauflo/season", {"mode": "heat"}), False),
 (H("GET", "/api/hauflo/comfort"), False),
 (H("POST", "/api/hauflo/comfort", {"rooms": []}), False),
 (H("POST", "/api/config/config_entries/flow/fk", {"host": "knxrouter"}), False),
 (H("POST", "/api/config/config_entries/flow/fk", {"gateway": "manual"}), True),
 # Security re-review round 5: the integrator's own groups, bridges and scenes are out of reach.
 (ns["_http_allowed"](hass, "DELETE", "/api/config/config_entries/entry/g1", None, set()), False),
 (ns["_http_allowed"](hass, "POST", "/api/config/config_entries/options/flow", json.dumps({"handler": "h1"}), set()), False),
 (W({"type": "config/entity_registry/update", "entity_id": "scene.my_evening", "new_entity_id": "scene.x"}), False),
 (H("POST", "/api/config/config_entries/flow/f1", "not json"), False),
 (ns["_http_allowed"](hass, "POST", "/api/config/config_entries/flow/f1", "[1, 2]", OWNED_ENTRIES), False),
 (W({"type": "call_service", "domain": "homeassistant", "service": "reload_config_entry", "service_data": {"entry_id": "z1"}}), False),
 (W({"type": "lovelace/dashboards/update", "dashboard_id": "x", "require_admin": False}), False),
 (W({"type": "homeassistant/expose_entity", "assistants": ["cloud.alexa"], "entity_ids": ["valve.water"], "should_expose": True}), False),
 # Integrator run 18: Siri off removes Hauflo's bridges — only those made through the agent.
 (ns["_http_allowed"](hass, "DELETE", "/api/config/config_entries/entry/h1", None, {"h1"}), True),
 (ns["_http_allowed"](hass, "DELETE", "/api/config/config_entries/entry/h1", None, {"other"}), False),
 (ns["_http_allowed"](hass, "DELETE", "/api/config/config_entries/entry/h1", None, None), False),
 (ns["_http_allowed"](hass, "DELETE", "/api/config/config_entries/entry/y1", None, {"y1"}), False),
]


def test_allowlist():
    bad = [i for i, (got, want) in enumerate(cases) if got != want]
    assert not bad, f"cases {bad} disagree"


def _btn(action: dict) -> dict:
    """A «Kitchen light» button on Hauflo's own light whose tap runs `action`."""
    return {"views": [{"cards": [{"type": "tile", "entity": "light.knx_1", "name": "Свет кухня", "tap_action": action}]}]}


_PA = lambda svc, **kw: {"action": "perform-action", "perform_action": svc, **kw}
# Security review 28.10 (agent 0.14.4, live): each of these was ACCEPTED and a non-admin's tap opened the garage.
BLOCKER_REFUSED = {
    "target entity_id all": _btn(_PA("cover.open_cover", target={"entity_id": "all"})),
    "target entity_id ALL": _btn(_PA("cover.open_cover", target={"entity_id": "ALL"})),
    "data entity_id all": _btn(_PA("cover.open_cover", data={"entity_id": "all"})),
    "data area_id": _btn(_PA("cover.open_cover", data={"area_id": "garage"})),
    "data device_id": _btn(_PA("cover.open_cover", data={"device_id": "dz"})),
    "data label_id": _btn(_PA("cover.open_cover", data={"label_id": "integrator"})),
    "data floor_id": _btn(_PA("cover.open_cover", data={"floor_id": "ground"})),
    "service_data area_id": _btn(_PA("cover.open_cover", service_data={"area_id": "garage"})),
    "target device_id": _btn(_PA("cover.open_cover", target={"device_id": "dz"})),
    "target label_id": _btn(_PA("homeassistant.toggle", target={"label_id": "integrator"})),
    "target area_id not light.turn_off": _btn(_PA("homeassistant.turn_off", target={"area_id": "kitchen"})),
    "upper-case entity": _btn(_PA("cover.open_cover", target={"entity_id": "COVER.GARAGE_DOOR"})),
    "upper-case in data list": _btn(_PA("homeassistant.toggle", data={"entity_id": ["VALVE.FRONT_GARDEN"]})),
    "leading space": _btn(_PA("cover.open_cover", target={"entity_id": " cover.garage_door"})),
    "trailing newline": _btn(_PA("cover.open_cover", target={"entity_id": "cover.garage_door\n"})),
    "comma list in one string": _btn(_PA("homeassistant.toggle", target={"entity_id": "light.knx_1,cover.garage_door"})),
    "own light + foreign in data": _btn(_PA("homeassistant.toggle", target={"entity_id": "light.knx_1"}, data={"entity_id": "cover.garage_door"})),
    "entity_id as dict": _btn(_PA("homeassistant.toggle", target={"entity_id": {"x": "cover.garage_door"}})),
    "entity_id as number": _btn(_PA("homeassistant.toggle", target={"entity_id": 5})),
    "empty list": _btn(_PA("homeassistant.toggle", target={"entity_id": []})),
    "no target at all": _btn(_PA("homeassistant.toggle")),
    "target not a dict": _btn(_PA("homeassistant.toggle", target="cover.garage_door")),
    "Entity_ID key spelled otherwise": _btn(_PA("homeassistant.toggle", target={"Entity_ID": "cover.garage_door"})),
    "unknown target key": _btn(_PA("homeassistant.toggle", target={"entity_id": "light.knx_1", "match": "all"})),
    "service vs perform_action disagree": _btn({"action": "call-service", "service": "cover.open_cover", "perform_action": "light.turn_on", "target": {"entity_id": "light.knx_1"}}),
    "call-service legacy with all": _btn({"action": "call-service", "service": "cover.open_cover", "service_data": {"entity_id": "all"}}),
    "toggle action carrying a target": _btn({"action": "toggle", "target": {"entity_id": "cover.garage_door"}}),
    "more-info carrying a service": _btn({"action": "more-info", "perform_action": "cover.open_cover", "target": {"entity_id": "light.knx_1"}}),
    "unknown action key carrying a service": {"views": [{"cards": [{"type": "tile", "entity": "light.knx_1", "features": [{"type": "light-brightness", "press": {"perform_action": "cover.open_cover", "target": {"entity_id": "all"}}}]}]}]},
    "nested under a card feature": {"views": [{"cards": [{"type": "tile", "entity": "light.knx_1", "features": [{"type": "light-brightness", "tap_action": _PA("cover.open_cover", data={"area_id": "garage"})}]}]}]},
    "alert card action entity all": {"views": [{"cards": [{"type": "custom:hauflo-alert", "alarms": [], "action": {"title": "x", "service": "switch.turn_off", "entity_id": "all"}}]}]},
    "alert card action area": {"views": [{"cards": [{"type": "custom:hauflo-alert", "alarms": [], "action": {"title": "x", "service": "switch.turn_off", "entity_id": "light.knx_1", "area_id": "garage"}}]}]},
    "room card lights all": {"views": [{"cards": [{"type": "custom:hauflo-room", "name": "x", "lights": "all"}]}]},
    "room card lights upper-case": {"views": [{"cards": [{"type": "custom:hauflo-room", "name": "x", "lights": ["SWITCH.RELE_VOROT"]}]}]},
    "tile entity with a space": {"views": [{"cards": [{"type": "tile", "entity": " cover.garage_door"}]}]},
    "light.turn_off area with the family's lamp": _btn(_PA("light.turn_off", target={"area_id": "hall"})),
    "light.turn_off area all": _btn(_PA("light.turn_off", target={"area_id": "all"})),
    "light.turn_off area in data": _btn(_PA("light.turn_off", data={"area_id": "kitchen"})),
    "knx.send with a target": {"views": [{"cards": [{"type": "tile", "entity": "scene.hauflo_r1_evening", "hold_action": _PA("knx.send", data={"address": "1/1/1", "payload": [128]}, target={"entity_id": "all"})}]}]},
    # LOW 7: a picture from outside the house.
    "external image": {"views": [{"cards": [{"type": "picture-glance", "image": "https://evil.example/p.png", "entities": []}]}]},
    "protocol-relative image": {"views": [{"cards": [{"type": "custom:hauflo-room", "name": "x", "picture": "//evil.example/p.png"}]}]},
    "image escaping /local": {"views": [{"cards": [{"type": "custom:hauflo-room", "name": "x", "picture": "/local/../../api/x"}]}]},
    "image with css break-out": {"views": [{"cards": [{"type": "custom:hauflo-room", "name": "x", "picture": "/local/a.jpg') url('https://evil.example"}]}]},
    "state_image external": {"views": [{"cards": [{"type": "picture-glance", "image": "/local/a.jpg", "state_image": {"on": "https://evil.example/on.png"}, "entities": []}]}]},
    "data: non-image": {"views": [{"cards": [{"type": "picture-glance", "image": "data:text/html;base64,PHNjcmlwdD4=", "entities": []}]}]},
}
BLOCKER_ACCEPTED = {
    "own light by entity_id": _btn(_PA("light.turn_off", target={"entity_id": "light.knx_1"})),
    "own light in a list": _btn(_PA("homeassistant.toggle", target={"entity_id": ["light.knx_1"]})),
    "own light in data (legacy)": _btn({"action": "call-service", "service": "light.turn_on", "service_data": {"entity_id": "light.knx_1", "brightness_pct": 50}}),
    "light.turn_off area all Hauflo's": _btn(_PA("light.turn_off", target={"area_id": "kitchen"})),
    "own scene": {"views": [{"cards": [{"type": "tile", "entity": "scene.hauflo_r1_evening", "tap_action": _PA("scene.turn_on", target={"entity_id": "scene.hauflo_r1_evening"})}]}]},
    "alert card own action": {"views": [{"cards": [{"type": "custom:hauflo-alert", "alarms": ["binary_sensor.smoke_z"], "action": {"title": "x", "service": "switch.turn_off", "entity_id": "light.knx_1"}}]}]},
    "local picture": {"views": [{"cards": [{"type": "custom:hauflo-room", "name": "x", "picture": "/local/rooms/kukhnia.jpg"}]}]},
    "area picture": {"views": [{"cards": [{"type": "picture-glance", "image": "/api/image/serve/0a1b2c/512x512", "entities": []}]}]},
    "inline svg": {"views": [{"cards": [{"type": "picture-glance", "image": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=", "entities": []}]}]},
}


def test_lovelace_service_targets_security_2810():
    wrong = [n for n, c in BLOCKER_REFUSED.items() if LV(c)] + [n for n, c in BLOCKER_ACCEPTED.items() if not LV(c)]
    assert not wrong, f"disagree: {wrong}"


if __name__ == "__main__":
    test_allowlist()
    test_lovelace_service_targets_security_2810()
    print(f"{len(cases)} cases ok")

# Dashboards Hauflo generated and deployed pass the check whole (their entities taken as Hauflo's own).
_real = ns["_hauflo_entity"]
ns["_hauflo_entity"] = lambda h, e, o, oe: not e.startswith(("lock.", "alarm_control_panel.", "valve."))
for f in sorted(glob.glob(str(pathlib.Path(__file__).parent / "fixtures/dashboards/hauflo_*.json"))):
    assert ns["_lovelace_ok"](None, json.load(open(f)), set(), set()), f"generated dashboard refused: {f}"
ns["_hauflo_entity"] = _real
print("generated dashboards ok")
