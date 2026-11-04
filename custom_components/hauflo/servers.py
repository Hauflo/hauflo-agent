"""Hauflo's servers and the one way the agent reaches them (owner 04.11: «если с одного сервера не скачивается —
автоматом с другого»). The hosts are data — servers.json; this module keeps the mechanics:

- ``same_region(server)``: the hosts the agent's link may use — only those of the server's OWN region (a Russian
  house's tunnel and data never move to the EU server or back), the last one that answered first. A server that
  isn't Hauflo's (a test stand typed under «Дополнительно») is only itself.
- ``first_answer(hosts, attempt)``: tries the hosts in order; a host that can't be reached (refused, no DNS, TLS,
  timeout) gives way to the next one, any answer — a 404 too — is the answer. The host that answered is remembered.

With one host in a region (today) the link only retries that host with its back-off: the list is ready for a second
one. Plain Python with aiohttp only (no HA import): tests and scripts/check-contracts.py read the same file."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
import json
from pathlib import Path
from typing import Any, TypeVar

import aiohttp

FILE = Path(__file__).with_name("servers.json")
T = TypeVar("T")
# A host that can't be reached: the next host may be. Anything else (a status, a body) is that host's answer.
UNREACHABLE = (aiohttp.ClientConnectionError, TimeoutError, OSError)


def _hosts(where: str, value: Any, prefix: str = "https://") -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x.startswith(prefix) and not x.endswith("/") for x in value):
        raise ValueError(f"servers.json {where}: a non-empty list of {prefix}… addresses without a trailing slash")
    return list(value)


def load(path: Path = FILE) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError("servers.json: version 1 expected")
    regions = data.get("regions")
    if not isinstance(regions, dict) or not regions:
        raise ValueError("servers.json regions: a non-empty object")
    for name, hosts in regions.items():
        _hosts(f"regions.{name}", hosts)
    for key in ("default_region", "pair_fallback_region"):
        if data.get(key) not in regions:
            raise ValueError(f"servers.json {key}: one of the regions")
    _hosts("mirrors", data.get("mirrors"))
    _hosts("github_zip", [data.get("github_zip")])
    return data


SERVERS = load()
REGIONS: dict[str, tuple[str, ...]] = {k: tuple(v) for k, v in SERVERS["regions"].items()}
DEFAULT_REGION: str = SERVERS["default_region"]
PAIR_FALLBACK_REGION: str = SERVERS["pair_fallback_region"]
DEFAULT_SERVER = REGIONS[DEFAULT_REGION][0]
PAIR_FALLBACK_SERVER = REGIONS[PAIR_FALLBACK_REGION][0]

# The host of each region's list that answered last (this run of HA): tried first next time.
_last_good: dict[tuple[str, ...], str] = {}


def norm(server: str) -> str:
    return str(server or "").strip().rstrip("/")


def ours(server: str) -> bool:
    s = norm(server)
    return any(s in hosts for hosts in REGIONS.values())


def _region_list(server: str) -> list[str]:
    """The hosts of ``server``'s region in their own order — only hosts every region that has ``server`` lists too (EU
    and AE share a server today: neither may pull the other to a host of its own). Not Hauflo's: only itself."""
    s = norm(server)
    lists = [hosts for hosts in REGIONS.values() if s in hosts]
    return [h for h in lists[0] if all(h in other for other in lists)] if lists else [s]


def same_region(server: str) -> list[str]:
    """The hosts the link to ``server`` may use, in the order to try: the last that answered, ``server``, the rest."""
    s = norm(server)
    allowed = _region_list(s)
    good = _last_good.get(tuple(allowed))
    return list(dict.fromkeys([*([good] if good in allowed else []), s, *allowed]))


def remember(host: str) -> None:
    """``host`` answered: its region's list starts with it next time (a host not Hauflo's isn't kept)."""
    h = norm(host)
    if ours(h):
        _last_good[tuple(_region_list(h))] = h


def forget() -> None:
    _last_good.clear()


async def first_answer(hosts: Iterable[str], attempt: Callable[[str], Awaitable[T]]) -> tuple[str, T]:
    """``attempt(host)`` on each host in turn until one answers; raises the last «unreachable» when none does."""
    last: BaseException | None = None
    for host in hosts:
        try:
            result = await attempt(host)
        except UNREACHABLE as err:
            last = err
            continue
        remember(host)
        return host, result
    raise last if last is not None else aiohttp.ClientConnectionError("no host")


def ws_url(host: str) -> str:
    return norm(host).replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/api/agent/ws"
