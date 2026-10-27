"""Every dashboard the backend builds passes the tunnel's dashboard check, `_lovelace_ok`, for the agent it is built for
(owner live test 28.10: a damper's «numeric-input» slider — new in the backend, unknown to the agent — made agent
0.13.0 refuse the whole dashboard). The fixtures are written by backend/src/ha/dashboardTunnel.test.ts from the
example projects, one per style and agent version. Ownership is the real check (`_hauflo_entity` → a KNX entity
Hauflo created), not stubbed. Runs without Home Assistant: python3 agent/tests/test_generated_dashboards.py."""
import ast, contextlib, glob, ipaddress, json, logging, pathlib, re, types

HERE = pathlib.Path(__file__).parent
src = open(HERE.parent / "custom_components/hauflo/__init__.py").read()
tree = ast.parse(src)
FUNCS = {"_lovelace_ok", "_hauflo_entity", "_own_entity", "_protected", "_platform", "_owned_knx", "_owned_knx_writes", "_knx_writes", "_ga_key"}
keep = [n for n in tree.body if (isinstance(n, ast.Assign) and any(getattr(t, "id", "").startswith("_") for t in n.targets)) or (isinstance(n, ast.FunctionDef) and n.name in FUNCS)]
ns = {"json": json, "re": re, "contextlib": contextlib, "ipaddress": ipaddress, "Any": object, "HomeAssistant": object, "logging": logging, "__name__": "agent", "DOMAIN": "hauflo"}
exec(compile(ast.Module(body=keep, type_ignores=[]), "agent", "exec"), ns)

# Card / feature types an agent learnt in a version (backend AGENT_FEATURE_MIN): older agents refuse them.
NEW_IN = {"numeric-input": "0.14.1", "thermostat": "0.14.1", "custom:hauflo-automations": "0.14.3"}
READ_ONLY = ns["_READ_ONLY_DOMAINS"]


def _v(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in s.split("."))


def _walk(v, out_ids: set, out_types: set, out_sends: set, key: str = ""):
    if isinstance(v, dict):
        if isinstance(v.get("type"), str):
            out_types.add(v["type"])
        if v.get("perform_action", v.get("service")) == "knx.send" and isinstance(v.get("data"), dict):
            out_sends.add(v["data"].get("address"))
        for k, x in v.items():
            _walk(x, out_ids, out_types, out_sends, str(k))
    elif isinstance(v, list):
        for x in v:
            _walk(x, out_ids, out_types, out_sends, key)
    elif isinstance(v, str) and ns["_ENTITY_RE"].match(v) and key not in ("icon", "name", "title", "label", "text", "theme", "url_path", "path", "color", "perform_action", "service", "type", "action"):
        out_ids.add(v)


class _Ent:
    def __init__(s, platform, domain, uid):
        s.platform, s.domain, s.unique_id, s.config_entry_id = platform, domain, uid, None


def check(path: str) -> None:
    cfg = json.load(open(path))
    ids, kinds, sends = set(), set(), set()
    _walk(cfg, ids, kinds, sends)
    # This HA: every entity the dashboard operates is a KNX entity Hauflo created (its uid in the agent's store).
    reg = {e: _Ent("knx", e.split(".")[0], f"u-{e}") for e in ids if e.split(".")[0] not in READ_ONLY}
    owned = {r.unique_id for r in reg.values()}
    hass = types.SimpleNamespace(states=types.SimpleNamespace(get=lambda e: None), data={})
    ns["er"] = types.SimpleNamespace(async_get=lambda h: types.SimpleNamespace(async_get=reg.get))
    # A KNX scene's address (its tile's «hold») is one a Hauflo entity sends to.
    ns["_owned_knx_writes"] = lambda h, o: {ns["_ga_key"](a) for a in sends}
    assert ns["_lovelace_ok"](hass, cfg, owned, set()), f"the agent would refuse {path}"
    agent = pathlib.Path(path).stem.rsplit("_", 1)[1]
    newer = {t for t in kinds if t in NEW_IN and _v(agent) < _v(NEW_IN[t])}
    assert not newer, f"{path}: {newer} only from agent {[NEW_IN[t] for t in newer]}"
    unknown = kinds - ns["_LOVELACE_TYPES"]
    assert not unknown, f"{path}: types the agent doesn't know: {unknown}"


FILES = sorted(glob.glob(str(HERE / "fixtures/dashboards/generated/*.json")))


def test_generated_dashboards_pass_the_tunnel():
    assert len(FILES) >= 8, "run backend vitest src/ha/dashboardTunnel.test.ts to write the fixtures"
    for f in FILES:
        check(f)


def test_new_types_are_in_the_agent():
    assert set(NEW_IN) <= ns["_LOVELACE_TYPES"]


def test_a_foreign_entity_is_refused():
    # The check is real: a switch Hauflo didn't create (another house's boiler relay) on a tile is refused.
    cfg = {"views": [{"cards": [{"type": "tile", "entity": "switch.boiler_relay"}]}]}
    reg = {"switch.boiler_relay": _Ent("template", "switch", "x")}
    ns["er"] = types.SimpleNamespace(async_get=lambda h: types.SimpleNamespace(async_get=reg.get))
    hass = types.SimpleNamespace(states=types.SimpleNamespace(get=lambda e: None), data={})
    assert not ns["_lovelace_ok"](hass, cfg, set(), set())
    # …and the slider row on a damper's tile passes (0.14.1).
    reg = {"number.damper": _Ent("knx", "number", "u1")}
    ns["er"] = types.SimpleNamespace(async_get=lambda h: types.SimpleNamespace(async_get=reg.get))
    cfg = {"views": [{"cards": [{"type": "tile", "entity": "number.damper", "features": [{"type": "numeric-input", "style": "slider"}], "features_position": "bottom"}]}]}
    assert ns["_lovelace_ok"](hass, cfg, {"u1"}, set())


if __name__ == "__main__":
    test_generated_dashboards_pass_the_tunnel()
    test_new_types_are_in_the_agent()
    test_a_foreign_entity_is_refused()
    print(f"{len(FILES)} generated dashboards ok")
