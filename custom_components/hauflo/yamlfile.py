"""automations.yaml / scenes.yaml as Home Assistant's own editor writes them, but never with YAML anchors: one list
used twice (an alarm's sources in its triggers and its message) became «&id001 … *id001», renumbered on every write —
a diff and «changed» each time (5B e2e D-5). A file with anchors written by HA's editor still reads as plain.

Reading, parsing, dumping and writing all run in the executor (HA review M-1: 100 rules ≈ 1 MB blocked the loop
for seconds). A write re-reads the file first: changed since it was read (HA's own editor saved meanwhile — it doesn't
take our lock), the change is made again on what is there now, never over it (M-2)."""

from __future__ import annotations

from collections.abc import Callable
import os
from typing import Any

import yaml
from annotatedyaml.dumper import FastestAvailableSafeDumper
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import dump as ha_dump


class _NoAliases(FastestAvailableSafeDumper):  # type: ignore[misc,valid-type]
    def ignore_aliases(self, data: Any) -> bool:
        return True


# «key:» for null, as HA's dumper writes it — by the representer, never by replacing text in the dump (HA review M-3:
# «: null» at a line's end inside a string was rewritten too).
_NoAliases.add_representer(type(None), lambda dumper, _value: dumper.represent_scalar("tag:yaml.org,2002:null", ""))


def dump(data: Any) -> str:
    """HA's dump (same options, «key:» for null), every repeated value written out in full."""
    return yaml.dump(data, Dumper=_NoAliases, default_flow_style=False, allow_unicode=True, sort_keys=False)


def plain(data: Any, body: str) -> bool:
    """``body`` (stripped) is exactly what we or HA's editor would write for ``data``."""
    return body in (dump(data).strip(), ha_dump(data).strip())


def parse(text: str, error: type[Exception], shape: type = list) -> Any:
    """The file as data — only when a dump gives back the same text (HA's editor's own format); else ``error``. An
    empty file / «[]» is plain. ``shape`` — a list (automations.yaml, scenes.yaml) or a dict (scripts.yaml: script id →
    its config, «{}» empty)."""
    body = text.strip()
    if body in ("", "[]", "{}"):
        return shape()
    try:
        data = yaml.safe_load(text)  # no HA tags: one would fail here, not be followed
    except yaml.YAMLError as err:
        raise error(f"not YAML: {err}") from err
    if not isinstance(data, shape):
        raise error(f"not a {shape.__name__}")
    if not plain(data, body):
        raise error("hand formatting")
    return data


def _text(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


async def async_read(hass: HomeAssistant, path: str, error: type[Exception], shape: type = list) -> tuple[Any, str | None]:
    """(data, the text it was read from — None: no file)."""

    def job() -> tuple[Any, str | None]:
        text = _text(path)
        return (shape(), None) if text is None else (parse(text, error, shape), text)

    return await hass.async_add_executor_job(job)


def write(path: str, backup: str, keep: int, data: Any, before: str | None, error: type[Exception], redo: Callable[[Any], Any] | None = None, shape: type = list) -> Any:
    """Writes ``data`` (read as ``before``) to ``path`` — atomically, the text it replaces kept as ``backup``+stamp
    (the last ``keep``). The file changed since ``before``: ``redo`` makes the change again on what is there now (no
    ``redo`` — ``error``). Returns what was written. Blocking: the executor's."""
    from homeassistant.util.file import write_utf8_file_atomic  # noqa: PLC0415

    now = _text(path)
    if now != before:
        if redo is None:
            raise error("changed meanwhile")
        data = redo(shape() if now is None else parse(now, error, shape))
    contents = dump(data)  # before the file is opened: a dump error never truncates it
    parse(contents, error, shape)  # what is written reads back the same (never a file the next write would refuse)
    if now is not None:
        stamp = dt_util.utcnow().strftime("%Y%m%d-%H%M%S-%f")
        write_utf8_file_atomic(backup + stamp, now)
        folder, prefix = os.path.split(backup)
        for name in sorted(n for n in os.listdir(folder) if n.startswith(prefix))[:-keep]:
            os.remove(os.path.join(folder, name))
    write_utf8_file_atomic(path, contents)
    return data


async def async_write(hass: HomeAssistant, path: str, backup: str, keep: int, data: Any, before: str | None, error: type[Exception], redo: Callable[[Any], Any] | None = None, shape: type = list) -> Any:
    return await hass.async_add_executor_job(write, path, backup, keep, data, before, error, redo, shape)


def rebase(writes: dict[str, Any], base: dict[str, Any], same: Callable[[Any, Any], bool], kept: list[str] | None = None) -> Callable[[list[Any]], list[Any]]:
    """``redo`` for writes by id (``writes``: id → new item, None — remove) planned on ``base`` (id → item as read):
    on the file as it is now, an item someone changed or removed meanwhile is left as they made it (its id into
    ``kept``); all else — every other item, new ones — as now, the writes applied."""

    def redo(fresh: list[Any]) -> list[Any]:
        out: list[Any] = []
        seen: set[str] = set()
        for item in fresh:
            key = item.get("id") if isinstance(item, dict) else None
            if key not in writes:
                out.append(item)
                continue
            seen.add(key)
            if key in base and same(item, base[key]):
                if writes[key] is not None:
                    out.append(writes[key])
            else:
                out.append(item)
                if kept is not None:
                    kept.append(key)
        for key, item in writes.items():
            if key in seen or item is None:
                continue
            if key in base:  # removed meanwhile: not brought back
                if kept is not None:
                    kept.append(key)
                continue
            out.append(item)
        return out

    return redo
