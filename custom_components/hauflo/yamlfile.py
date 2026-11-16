from __future__ import annotations
from collections.abc import Callable
import os
from typing import Any
import yaml
from annotatedyaml.dumper import FastestAvailableSafeDumper
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml import dump as ha_dump

class _NoAliases(FastestAvailableSafeDumper):

    def ignore_aliases(self, data: Any) -> bool:
        return True
_NoAliases.add_representer(type(None), lambda dumper, _value: dumper.represent_scalar('tag:yaml.org,2002:null', ''))

def dump(data: Any) -> str:
    return yaml.dump(data, Dumper=_NoAliases, default_flow_style=False, allow_unicode=True, sort_keys=False)

def plain(data: Any, body: str) -> bool:
    return body in (dump(data).strip(), ha_dump(data).strip())

def parse(text: str, error: type[Exception], shape: type=list) -> Any:
    body = text.strip()
    if body in ('', '[]', '{}'):
        return shape()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise error(f'not YAML: {err}') from err
    if not isinstance(data, shape):
        raise error(f'not a {shape.__name__}')
    if not plain(data, body):
        raise error('hand formatting')
    return data

def _text(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as f:
        return f.read()

async def async_read(hass: HomeAssistant, path: str, error: type[Exception], shape: type=list) -> tuple[Any, str | None]:

    def job() -> tuple[Any, str | None]:
        text = _text(path)
        return (shape(), None) if text is None else (parse(text, error, shape), text)
    return await hass.async_add_executor_job(job)

def write(path: str, backup: str, keep: int, data: Any, before: str | None, error: type[Exception], redo: Callable[[Any], Any] | None=None, shape: type=list) -> Any:
    from homeassistant.util.file import write_utf8_file_atomic
    now = _text(path)
    if now != before:
        if redo is None:
            raise error('changed meanwhile')
        data = redo(shape() if now is None else parse(now, error, shape))
    contents = dump(data)
    parse(contents, error, shape)
    if now is not None:
        stamp = dt_util.utcnow().strftime('%Y%m%d-%H%M%S-%f')
        write_utf8_file_atomic(backup + stamp, now)
        folder, prefix = os.path.split(backup)
        for name in sorted((n for n in os.listdir(folder) if n.startswith(prefix)))[:-keep]:
            os.remove(os.path.join(folder, name))
    write_utf8_file_atomic(path, contents)
    return data

async def async_write(hass: HomeAssistant, path: str, backup: str, keep: int, data: Any, before: str | None, error: type[Exception], redo: Callable[[Any], Any] | None=None, shape: type=list) -> Any:
    return await hass.async_add_executor_job(write, path, backup, keep, data, before, error, redo, shape)

def rebase(writes: dict[str, Any], base: dict[str, Any], same: Callable[[Any, Any], bool], kept: list[str] | None=None) -> Callable[[list[Any]], list[Any]]:

    def redo(fresh: list[Any]) -> list[Any]:
        out: list[Any] = []
        seen: set[str] = set()
        for item in fresh:
            key = item.get('id') if isinstance(item, dict) else None
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
            if key in base:
                if kept is not None:
                    kept.append(key)
                continue
            out.append(item)
        return out
    return redo
