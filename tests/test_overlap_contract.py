"""K4: the agent classifies an address others use too exactly as the deploy plan does (backend/src/ha/overlap.ts) —
the shared cases are tests/fixtures/overlap-cases.json here, in the agent (the backend's vitest reads the same file),
so the published agent repository runs it on its own. Runs without Home Assistant: python3 tests/test_overlap_contract.py"""
import ast
import json
import pathlib

AGENT = pathlib.Path(__file__).resolve().parents[1]
_src = (AGENT / "custom_components/hauflo/__init__.py").read_text(encoding="utf-8")
_fn = next(n for n in ast.parse(_src).body if isinstance(n, ast.FunctionDef) and n.name == "_overlap_kind")
ns: dict = {"Any": object}
exec(compile(ast.Module(body=[_fn], type_ignores=[]), "agent", "exec"), ns)
CASES = json.loads((AGENT / "tests/fixtures/overlap-cases.json").read_text(encoding="utf-8"))["cases"]


def test_overlap_contract():
    assert CASES
    wrong = []
    for c in CASES:
        got = ns["_overlap_kind"](c["platform"], c["scene_number"], c["write"], c["main"], [tuple(h) for h in c["holders"]])
        if got != c["expect"]:
            wrong.append(f"{c['name']}: {got} != {c['expect']}")
    assert not wrong, wrong


if __name__ == "__main__":
    test_overlap_contract()
    print(f"{len(CASES)} overlap cases ok")
