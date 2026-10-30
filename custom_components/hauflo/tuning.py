"""Timings, thresholds, limits and name-word lists of the agent's modules (owner 30.10: no hardcode) — one file,
tuning.json, checked against this schema when the agent loads. A bad file is a bug of the release: the import fails
loudly, never a silent default. (The value rules' own canon is canon.py / rules_canon.json; user texts are in
translations.) The modules keep NAMED constants (``NODATA_AFTER = T["nodata_after_s"]``) — the mechanics stay in code.

Plain Python on purpose (no HA / voluptuous import): the checker is also run by scripts/check-hardcode.mjs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

FILE = Path(__file__).with_name("tuning.json")


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def _check(section: str, key: str, value: Any) -> None:
    where = f"tuning.json {section}.{key}"
    if isinstance(value, str):
        re.compile(value)  # a word list is a regex: a bad one fails here, not in the middle of an alarm
    elif isinstance(value, list):
        if not value or not (all(isinstance(x, str) for x in value) or all(_num(x) for x in value)):
            raise ValueError(f"{where}: a non-empty list of strings or of non-negative numbers")
    elif not _num(value):
        raise ValueError(f"{where}: a non-negative number, a list or a word-list string")


def load(path: Path = FILE) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.pop("version", None) != 1:
        raise ValueError("tuning.json: version 1 expected")
    for section, body in data.items():
        if not isinstance(body, dict) or not body:
            raise ValueError(f"tuning.json {section}: a non-empty object")
        for key, value in body.items():
            _check(section, key, value)
    return data


TUNING: dict[str, dict[str, Any]] = load()
