"""No hardcode in the value rules (owner 29.10): thresholds, timings, safety helpers, limits and recipes come from
rules_canon.json (checked by its schema when the agent loads); user texts from translations (RU + EN). This test fails on
a user-facing sentence in rules.py / derived.py or on a number there that isn't structural (0 / 1) or a named
physics / protocol constant."""

from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest
import voluptuous as vol

from custom_components.hauflo import canon

H = pathlib.Path(__file__).resolve().parents[1] / "custom_components/hauflo"
LOGIC = ("rules.py", "derived.py", "window.py")
# Named constants allowed to hold a number: physics, unit conversions, HA protocol scales — never a setting.
NAMED = {"MAGNUS_B", "MAGNUS_C", "MAGNUS_E0_HPA", "VAPOUR_G_K_PER_HPA", "KELVIN_ZERO", "PERCENT", "SECONDS_PER_MINUTE", "SECONDS_PER_HOUR", "ROUND_DIGITS", "PAIR", "BRIGHTNESS_MAX", "ID_BYTES"}
# Calls whose strings are for logs, task names or the app's API protocol — not the people's texts.
NOT_TEXT_CALLS = {"warning", "info", "debug", "error", "exception", "json_message", "async_create_task", "async_track_time_interval"}
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
SENTENCE = re.compile(r"[A-Za-z]{2,}[,;:!?]?\s+[A-Za-z]{2,}")


def _offenders(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                skip.add(id(first.value))  # a docstring
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in NOT_TEXT_CALLS:
                for sub in ast.walk(node):
                    skip.add(id(sub))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            skip.add(id(node.right))  # a square in a formula is math, not a setting
    named: set[int] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and all(isinstance(t, (ast.Name, ast.Tuple)) for t in node.targets):
            names = {n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)}
            if names and names <= NAMED:
                named |= {id(sub) for sub in ast.walk(node.value)}
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in skip:
            continue
        v = node.value
        if isinstance(v, str) and (CYRILLIC.search(v) or SENTENCE.search(v)):
            out.append(f"{path.name}:{node.lineno} text {v[:40]!r}")
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and v not in (0, 1) and id(node) not in named:
            out.append(f"{path.name}:{node.lineno} number {v!r}")
    return out


def test_no_hardcoded_texts_or_settings_in_the_rules_logic() -> None:
    found = [o for name in LOGIC for o in _offenders(H / name)]
    assert not found, "\n".join(found)


def test_the_checker_catches_offenders(tmp_path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text('"""Doc «ok» with words."""\nHOLD = 3600\nX = "Вручную"\n\ndef f():\n    return "too many switches" if 5 else 0\n', encoding="utf-8")
    found = _offenders(bad)
    assert sorted(f.split(" ", 1)[1].split(" ")[0] for f in found) == ["number", "number", "text", "text"]


def test_canon_schema_rejects_a_bad_canon(tmp_path) -> None:
    good = json.loads(canon.FILE.read_text(encoding="utf-8"))
    assert canon.load() == canon.CANON
    for broken in (
        {k: v for k, v in good.items() if k != "helpers"},
        {**good, "version": 2},
        {**good, "helpers": {**good["helpers"], "rate_max": -1}},
        {**good, "purposes": {**good["purposes"], "heater": {**good["purposes"]["heater"], "safe": "explode"}}},
        {**good, "recipes": [{**good["recipes"][0], "purpose": "rocket"}]},
        {**good, "client_purposes": ["rocket"]},
        {**good, "limits": {**good["limits"], "setpoint": [5]}},
    ):
        f = tmp_path / "canon.json"
        f.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(vol.Invalid):
            canon.load(f)


def _texts(lang: str) -> dict[str, str]:
    name = "strings.json" if lang == "strings" else f"translations/{lang}.json"
    data = json.loads((H / name).read_text(encoding="utf-8"))
    return {k: v["message"] for k, v in data["exceptions"].items() if k.startswith("rules_")}


def _keys_in_code() -> set[str]:
    keys: set[str] = set()
    for name in LOGIC:
        for node in ast.walk(ast.parse((H / name).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.fullmatch(r"rules_[a-z_]*[a-z]", node.value):
                keys.add(node.value)
    c = canon.CANON
    keys |= {f"rules_reason_{r}" for r in ("no_entity", "unavailable", "not_a_number", "unit", "stale")}
    keys |= {f"rules_thr_{w}" for w in ("below", "above", "outside", "between")} | {"rules_word_on", "rules_word_off"}
    keys |= {f"rules_explain_{s}" for s in ("off", "paused", "normal", "active", "waiting", "pending", "fault_off", "fault_wait", "hold_rate", "hold_manual", "pending_on", "pending_off")}
    keys |= {f"rules_hint_{p}" for p, v in c["purposes"].items() if v["hint"]}
    keys |= {f"rules_preview_{n}" for n in ("fault", "waiting", "now", "wait", "normal", "normal_for")}
    keys |= {f"rules_recipe_{r['id']}" for r in c["recipes"]}
    return keys


def test_every_text_in_both_languages_with_the_same_placeholders() -> None:
    en, ru, strings = _texts("en"), _texts("ru"), _texts("strings")
    assert en == strings
    needed = _keys_in_code()
    assert not needed - set(en), sorted(needed - set(en))
    assert set(en) == set(ru)
    for k in en:
        assert set(re.findall(r"\{(\w+)\}", en[k])) == set(re.findall(r"\{(\w+)\}", ru[k])), k
        assert CYRILLIC.search(ru[k]) or k in ("rules_thr_outside", "rules_explain_pending") or not SENTENCE.search(ru[k]), k
    # «помогаем / подсказываем», never «защищаем / гарантируем» (owner 29.10).
    for k in ru:
        assert not re.search(r"\b(гарантир|защищаем|защитит)", ru[k], re.IGNORECASE), k
        assert not re.search(r"\b(guarantee|we protect|protects you)", en[k], re.IGNORECASE), k


def test_value_rule_entities_never_collide_with_the_block_rules() -> None:
    """Device-block rules (backend blockRulesDeploy.ts) are `binary_sensor.hauflo_rule_<sha10>`; a value rule's entity ids
    come from the canon prefix — neither namespace may start with the other, whatever the slug."""
    ts = H.parents[2] / "backend/src/ha/blockRulesDeploy.ts"
    if not ts.exists():
        pytest.skip("backend not in this checkout")
    found = re.findall(r"binary_sensor\.(hauflo_[a-z_]+)\$\{", ts.read_text(encoding="utf-8"))
    assert found, "the block rules' entity id template moved — update this test"
    eng = canon.CANON["engine"]
    mine = [f"hauflo_{eng['rule_prefix']}_", f"hauflo_{eng['derived_prefix']}_"]
    for block in found:
        for m in mine:
            assert not m.startswith(block) and not block.startswith(m), (m, block)
