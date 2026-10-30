"""The tunnel contract, the agent's side (tester 30.10: the rules' «template» flow was refused live — «403 Hauflo agent:
not allowed» — while the backend's tests passed). Every call the backend makes, found in its source and dumped by
backend/src/ha/tunnelContract.test.ts (fixtures/tunnel-calls.json), goes through the agent's real checks here — as
Hauflo makes it: on what Hauflo set up itself. Plus the list is the agent's one source (tunnel.json)."""

import json
import pathlib
from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hauflo as m
from custom_components.hauflo.tunnel import TUNNEL, load
from tests.test_allowlist_knx import knx

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures/tunnel-calls.json").read_text(encoding="utf-8"))
CALLS = FIX["calls"]
WS = [c for c in CALLS if c["kind"] == "ws" and c["call"] not in TUNNEL["direct_only"]]
REST = [c for c in CALLS if c["kind"] == "rest"]


def test_the_dump_is_there_and_the_list_is_the_agents_one():
    assert len(CALLS) > 80
    assert any(c["params"].get("handler") == "template" for c in CALLS)
    # The module's sets are tunnel.json's — no second copy in the code.
    assert m._WS_EXACT == set(TUNNEL["ws"]["commands"]) and m._FLOW_DOMAINS == set(TUNNEL["flows"]["setup"])
    assert m._HAUFLO_VIEWS == {(r["path"], x) for r in TUNNEL["http"]["routes"] if r["id"] == "hauflo" for x in r["methods"]}
    assert load() == TUNNEL


def test_a_bad_list_fails_loudly(tmp_path):
    bad = json.loads(json.dumps(TUNNEL))
    bad["flows"]["setup"] = []
    (tmp_path / "t.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        load(tmp_path / "t.json")
    bad = json.loads(json.dumps(TUNNEL))
    bad["flows"]["template"]["tokens"].append({"re": "("})
    (tmp_path / "t.json").write_text(json.dumps(bad))
    with pytest.raises(Exception):  # noqa: B017 — re.error
        load(tmp_path / "t.json")


@pytest.mark.parametrize("c", WS, ids=lambda c: f"{c['file']}:{c['call']}:{c['params']}")
def test_every_ws_command_the_backend_sends(hass, c):
    if c["call"] in TUNNEL["ws"]["agent_commands"]:
        return  # answered by the agent itself (dashmerge.handle), before the allowlist
    msg = {"id": 1, "type": c["call"], **c["params"]}
    if c["call"] == "call_service" or c["call"] in ("config_entries/get", "manifest/get"):
        assert m._ws_allowed(msg, hass), c
    else:
        assert c["call"] in m._WS_EXACT, c


@pytest.mark.parametrize("c", REST, ids=lambda c: f"{c['file']}:{c['call']}")
async def test_every_rest_call_the_backend_makes(hass, c):
    method, path = c["call"].split(" ")
    route = m._route(method, path.replace("{x}", "x1"))
    assert route is not None, c
    if route == "scene":
        assert m._http_allowed(hass, method, path.replace("{x}", "hauflo_x1"), json.dumps({"entities": {}}) if method == "POST" else None), c
        return
    if route == "homekit_qr":
        e = MockConfigEntry(domain="homekit")
        e.add_to_hass(hass)
        assert m._http_allowed(hass, method, f"{path}?{e.entry_id}-secret", None, {e.entry_id}), c
        return
    if route == "flow":
        assert m._http_allowed(hass, method, path, json.dumps({"handler": c["params"]["handler"]})), c
        return
    if route in ("info", "hauflo", "flow_handlers"):
        assert m._http_allowed(hass, method, path, None), c
        return
    assert c.get("domains"), c  # the integrations the call is of (the backend's dump says)
    for domain in c["domains"]:
        e = MockConfigEntry(domain=domain)
        e.add_to_hass(hass)
        mine = {e.entry_id}
        if route == "flow_step":
            assert domain in m._FLOW_DOMAINS, c
        elif route == "options":
            assert m._http_allowed(hass, method, path, json.dumps({"handler": e.entry_id}), mine), c
        elif route == "options_step":
            # The form's own input is checked by its step (test_tunnel_template.py, test_allowlist_flows.py).
            with patch.object(m, "_flow_handler", return_value=e.entry_id):
                assert m._http_allowed(hass, "GET", path.replace("{x}", "o1"), None, mine), c
        else:
            assert m._http_allowed(hass, method, path.replace("{x}", e.entry_id), None, mine), c
            # …and never on what the integrator set up himself.
            if domain in m._OWNED_ONLY:
                assert not m._http_allowed(hass, method, path.replace("{x}", e.entry_id), None, set()), c


def test_every_rule_template_the_backend_writes(hass):
    k = MockConfigEntry(domain="knx")
    k.add_to_hass(hass)
    reg = er.async_get(hass)
    owned = set()
    for i, eid in enumerate(FIX["entities"]):
        domain, obj = eid.split(".")
        reg.async_get_or_create(domain, "knx", f"u-{i}", config_entry=k, suggested_object_id=obj)
        owned.add(f"u-{i}")
    hass.data["knx"] = knx({})
    for tpl in FIX["templates"]:
        assert set(m._template_entities(tpl)) <= set(FIX["entities"]), tpl
        assert m._template_step_ok(hass, {"name": "Включите вытяжку", "state": tpl, "device_class": "problem"}, "binary_sensor", owned, set(), False), tpl
        # Not Hauflo's entities (made by hand): the same template is refused.
        assert not m._template_step_ok(hass, {"name": "x", "state": tpl}, "binary_sensor", set(), set(), False), tpl
