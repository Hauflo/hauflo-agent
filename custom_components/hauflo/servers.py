"""автоматом с другого»). The hosts are data — servers.json; this module keeps the mechanics:

- ``same_region(server)``: the hosts the agent's link may use — only those of the server's OWN region (a Russian
  house's tunnel and data never move to the EU server or back), the last one that answered first. A server that
  isn't Hauflo's (a test stand typed under «Дополнительно») is only itself.
- ``first_answer(hosts, attempt)``: tries the hosts in order; a host that can't be reached (refused, no DNS, TLS,
  timeout) gives way to the next one, any answer — a 404 too — is the answer. The host that answered is remembered.

With one host in a region (today) the link only retries that host with its back-off: the list is ready for a second
one. Plain Python with aiohttp only (no HA import): tests and scripts/check-contracts.py read the same file."""
from __future__ import annotations
from collections.abc import Awaitable, Callable, Iterable
import functools
import json
from pathlib import Path
from typing import Any, TypeVar
import aiohttp
FILE = Path(__file__).with_name('servers.json')
T = TypeVar('T')
UNREACHABLE = (aiohttp.ClientConnectionError, TimeoutError, OSError)

def _hosts(where: str, value: Any, prefix: str='https://') -> list[str]:
    if not isinstance(value, list) or not value or (not all((isinstance(x, str) and x.startswith(prefix) and (not x.endswith('/')) for x in value))):
        raise ValueError(f'servers.json {where}: a non-empty list of {prefix}… addresses without a trailing slash')
    return list(value)

def load(path: Path=FILE) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('version') != 1:
        raise ValueError('servers.json: version 1 expected')
    regions = data.get('regions')
    if not isinstance(regions, dict) or not regions:
        raise ValueError('servers.json regions: a non-empty object')
    for name, hosts in regions.items():
        _hosts(f'regions.{name}', hosts)
    for key in ('default_region', 'pair_fallback_region'):
        if data.get(key) not in regions:
            raise ValueError(f'servers.json {key}: one of the regions')
    _hosts('mirrors', data.get('mirrors'))
    _hosts('github_zip', [data.get('github_zip')])
    return data

@functools.cache
def servers_cfg() -> dict[str, Any]:
    """servers.json, read on first use — off the event loop: async_setup_entry and the config flow preload it in the
    executor."""
    return load()
regions_cfg = functools.cache(lambda: {k: tuple(v) for k, v in servers_cfg()['regions'].items()})
default_region = functools.cache(lambda: servers_cfg()['default_region'])
pair_fallback_region = functools.cache(lambda: servers_cfg()['pair_fallback_region'])
default_server = functools.cache(lambda: regions_cfg()[default_region()][0])
region_servers = functools.cache(lambda: {region: hosts[0] for region, hosts in regions_cfg().items()})
pair_fallback_server = functools.cache(lambda: regions_cfg()[pair_fallback_region()][0])

class GoodHosts:
    """The host of each region's list that answered last: tried first next time. One per HA run, kept in hass.data
    (link/common.good_hosts) — not module state (HA core review): two HA instances in one process never share it."""

    def __init__(self) -> None:
        self.by_region: dict[tuple[str, ...], str] = {}

    def remember(self, host: str) -> None:
        """``host`` answered: its region's list starts with it next time (a host not Hauflo's isn't kept)."""
        h = norm(host)
        if ours(h):
            self.by_region[tuple(_region_list(h))] = h

def norm(server: str) -> str:
    return str(server or '').strip().rstrip('/')

def ours(server: str) -> bool:
    s = norm(server)
    return any((s in hosts for hosts in regions_cfg().values()))

def _region_list(server: str) -> list[str]:
    s = norm(server)
    lists = [hosts for hosts in regions_cfg().values() if s in hosts]
    return [h for h in lists[0] if all((h in other for other in lists))] if lists else [s]

def same_region(server: str, good: GoodHosts | None=None) -> list[str]:
    """The hosts the link to ``server`` may use, in the order to try: the last that answered (``good``), ``server``,
    the rest."""
    s = norm(server)
    allowed = _region_list(s)
    last = good.by_region.get(tuple(allowed)) if good is not None else None
    return list(dict.fromkeys([*([last] if last in allowed else []), s, *allowed]))

async def first_answer[T](hosts: Iterable[str], attempt: Callable[[str], Awaitable[T]], good: GoodHosts | None=None) -> tuple[str, T]:
    """``attempt(host)`` on each host in turn until one answers (kept in ``good``); raises the last «unreachable»
    when none does."""
    last: BaseException | None = None
    for host in hosts:
        try:
            result = await attempt(host)
        except UNREACHABLE as err:
            last = err
            continue
        if good is not None:
            good.remember(host)
        return (host, result)
    raise last if last is not None else aiohttp.ClientConnectionError('no host')

def ws_url(host: str) -> str:
    return norm(host).replace('https://', 'wss://', 1).replace('http://', 'ws://', 1) + '/api/agent/ws'
