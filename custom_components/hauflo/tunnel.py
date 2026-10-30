"""What Hauflo may ask through the tunnel — tunnel.json, the one list the agent and the backend's tests share (a call the
backend makes that the agent would refuse fails the backend's own tests, not a house). Checked when the agent loads: a
bad file is a bug of the release and the import fails loudly — never an empty list that lets nothing through silently,
nor one that lets everything through.

Plain Python on purpose (no HA import): the backend's contract test and scripts read the same file."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

FILE = Path(__file__).with_name("tunnel.json")
_METHODS = {"GET", "POST", "DELETE"}


def _strings(where: str, value: Any) -> None:
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
        raise ValueError(f"tunnel.json {where}: a non-empty list of strings")


def _pairs(where: str, value: Any) -> None:
    if not isinstance(value, list) or not value or not all(isinstance(x, list) and len(x) == 2 and all(isinstance(y, str) for y in x) for x in value):
        raise ValueError(f"tunnel.json {where}: a non-empty list of [a, b] string pairs")


def load(path: Path = FILE) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError("tunnel.json: version 1 expected")
    ws, http, flows = data["ws"], data["http"], data["flows"]
    for k in ("commands", "reads", "agent_commands", "manifests", "config_entries", "config_entries_owned_only"):
        _strings(f"ws.{k}", ws[k])
    for k in ("services", "read_services"):
        _pairs(f"ws.{k}", ws[k])
    if not set(ws["reads"]) <= set(ws["commands"]):
        raise ValueError("tunnel.json ws.reads: only commands from ws.commands")
    if not {tuple(p) for p in ws["read_services"]} <= {tuple(p) for p in ws["services"]}:
        raise ValueError("tunnel.json ws.read_services: only services from ws.services")
    for r in http["routes"]:
        if not (isinstance(r, dict) and isinstance(r.get("id"), str) and isinstance(r.get("path"), str) and r["path"].startswith("/api/")):
            raise ValueError(f"tunnel.json http.routes: {r!r}")
        if not r.get("methods") or not set(r["methods"]) <= _METHODS:
            raise ValueError(f"tunnel.json http.routes {r['path']}: methods of {sorted(_METHODS)}")
    _pairs("http.locked_ok", http["locked_ok"])
    for k in ("setup", "options", "owned_only", "delete", "tracked", "diagnostics", "entities_by_entry"):
        _strings(f"flows.{k}", flows[k])
    sax = flows["switch_as_x"]
    for k in ("required", "target_domains", "source_domains"):
        _strings(f"flows.switch_as_x.{k}", sax[k])
    for k in ("keys", "options_keys"):
        if not isinstance(sax[k], dict) or not sax[k]:
            raise ValueError(f"tunnel.json flows.switch_as_x.{k}: step → keys")
        for step, keys in sax[k].items():
            _strings(f"flows.switch_as_x.{k}.{step}", keys)
    tpl = flows["template"]
    _strings("flows.template.menu", tpl["menu"])
    _strings("flows.template.device_classes", tpl["device_classes"])
    for step in tpl["menu"]:
        _strings(f"flows.template.keys.{step}", tpl["keys"].get(step))
        _strings(f"flows.template.options_keys.{step}", tpl["options_keys"].get(step))
    for k in ("max_name", "max_state"):
        if not isinstance(tpl[k], int) or tpl[k] <= 0:
            raise ValueError(f"tunnel.json flows.template.{k}: a positive integer")
    for t in tpl["tokens"]:
        rx = re.compile(t["re"])  # a bad pattern fails here, not on a deploy
        if "entity" in t and not (isinstance(t["entity"], int) and 0 < t["entity"] <= rx.groups):
            raise ValueError(f"tunnel.json flows.template.tokens {t['re']}: entity is a group number")
    _strings("direct_only", data["direct_only"])
    return data


TUNNEL: dict[str, Any] = load()
