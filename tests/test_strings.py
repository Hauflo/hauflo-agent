"""Every config-flow and options field says what it's for (HA quality scale Bronze «config-flow»: data_description),
in English and Russian; strings.json and translations/en.json are the same text."""

import json
import pathlib

H = pathlib.Path(__file__).resolve().parents[1] / "custom_components/hauflo"


def _load(name: str) -> dict:
    return json.loads((H / name).read_text(encoding="utf-8"))


def _missing(doc: dict) -> list[str]:
    out = []
    for flow in ("config", "options"):
        for step_id, step in doc[flow]["step"].items():
            parts = [(step_id, step)] + [(f"{step_id}.{name}", sec) for name, sec in (step.get("sections") or {}).items()]
            for where, part in parts:
                described = part.get("data_description") or {}
                out += [f"{flow}.{where}.{field}" for field in part.get("data") or {} if not described.get(field)]
    return out


def test_every_field_has_a_description_in_both_languages() -> None:
    assert _load("strings.json") == _load("translations/en.json")
    for name in ("strings.json", "translations/en.json", "translations/ru.json"):
        assert not _missing(_load(name)), name
