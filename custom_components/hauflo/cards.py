"""The Hauflo cards as a plugin of their own (п.13, owner 07.11): «Hauflo Cards» from Marketplace/HACS for any Home
Assistant, the agent's www/ the same source (cards/build.mjs bundles it). Both on one house → ONE copy on the page.

The agent decides at its start which copy serves (`pick`): the plugin only when it is at least as new as the agent's own
cards and this agent is at least the plugin's `agent_min`; otherwise the agent's own cards (HA's extra JS, loaded before
any dashboard resource — the plugin's file then defines nothing, hauflo-cards.js HF_FIRST) and HA's Repairs say what to
update. A tie is the agent's: the same code, and the house keeps what it had before the plugin came.

The plugin is found among the dashboard resources (HACS/Marketplace adds it as /hacsfiles/<repo>/hauflo-cards.js); its
version is the first line of its file — `// hauflo-cards {"version": …, "agent_min": …}` (cards/build.mjs).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from . import issues
from .const import CARDS_FILE, VERSION

_LOGGER = logging.getLogger(__name__)

# The agent's own cards: their version and the agent they need (the plugin's file says the same in its first line).
OWN: dict[str, str] = json.loads((Path(__file__).parent / "www" / "cards.json").read_text(encoding="utf-8"))
HEADER = re.compile(r"^// hauflo-cards (\{.*\})\s*$")
AGENT, PLUGIN = "agent", "plugin"
OUTDATED, NEED_AGENT = issues.CARDS_OUTDATED, issues.CARDS_NEED_AGENT
ISSUES = (OUTDATED, NEED_AGENT)
# The agent's own addresses: its copy, not the plugin's.
OURS = ("/hauflo_static/", "/local/hauflo/")
# Where HA serves a resource's file from: HACS/Marketplace's /hacsfiles/ → www/community, /local/ → www.
ROOTS = {"/hacsfiles/": ("www", "community"), "/local/": ("www",)}


def parse_version(text: object) -> tuple[int, ...] | None:
    """«1.2.3» (a «v» and a pre-release tail allowed: «v1.2.3b0» → 1.2.3) → (1, 2, 3); anything else → None."""
    m = re.match(r"^v?(\d+(?:\.\d+){0,3})", str(text or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def at_least(have: object, need: object) -> bool:
    h, n = parse_version(have), parse_version(need)
    if n is None:
        return True
    if h is None:
        return False
    width = max(len(h), len(n))
    return h + (0,) * (width - len(h)) >= n + (0,) * (width - len(n))


def pick(own: dict[str, Any], agent: str, plugin: dict[str, Any] | None) -> tuple[str, str | None]:
    """Which copy serves the cards and the Repairs issue to raise (None — none): see the module's docstring."""
    if plugin is None:
        return AGENT, None
    version = plugin.get("version")
    if parse_version(version) is None:
        return AGENT, OUTDATED  # a file we can't read the version of: the agent's cards, never a guess
    if not at_least(agent, plugin.get("agent_min")):
        return AGENT, NEED_AGENT
    if not at_least(version, own.get("version")):
        return AGENT, OUTDATED
    if at_least(own.get("version"), version):
        return AGENT, None  # the same version: the same code, the agent's copy stays
    return PLUGIN, None


def read_header(first_line: str) -> dict[str, Any] | None:
    m = HEADER.match(first_line or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def plugin_url(urls: list[str]) -> str | None:
    """The plugin's resource among the dashboard resources: a hauflo-cards.js that is not the agent's own copy."""
    for url in urls:
        path = url.split("?", 1)[0]
        if path.rsplit("/", 1)[-1] == CARDS_FILE and not path.startswith(OURS):
            return url
    return None


def plugin_file(config_dir: Path, url: str) -> Path | None:
    """The file HA serves for the resource (only inside its www folder)."""
    path = url.split("?", 1)[0]
    for prefix, parts in ROOTS.items():
        if path.startswith(prefix):
            root = config_dir.joinpath(*parts).resolve()
            f = (root / path[len(prefix):]).resolve()
            return f if f.is_relative_to(root) else None
    return None


def _read_first_line(f: Path | None) -> str:
    try:
        with f.open(encoding="utf-8") as fh:  # type: ignore[union-attr]
            return fh.readline()
    except (AttributeError, OSError, UnicodeDecodeError):
        return ""


async def async_find_plugin(hass: HomeAssistant) -> dict[str, Any] | None:
    """The installed plugin ({"url", "version", "agent_min"}; version "" when its file can't be read) or None."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    res = getattr(hass.data.get(LOVELACE_DATA), "resources", None)
    if res is None or not hasattr(res, "async_items"):
        return None
    if hasattr(res, "async_get_info"):
        await res.async_get_info()  # the storage collection loads on first use
    url = plugin_url([str(item.get("url", "")) for item in res.async_items()])
    if url is None:
        return None
    f = plugin_file(Path(hass.config.config_dir), url)
    head = read_header(await hass.async_add_executor_job(_read_first_line, f)) or {}
    return {"url": url, "version": str(head.get("version") or ""), "agent_min": str(head.get("agent_min") or "")}


def report(hass: HomeAssistant, issue: str | None, plugin: dict[str, Any] | None) -> None:
    """HA's Repairs: the one issue that applies, the other gone."""
    for other in ISSUES:
        if other != issue:
            issues.clear_issue(hass, other)
    if issue is None or plugin is None:
        return
    issues.raise_issue(
        hass,
        issue,
        plugin=plugin.get("version") or "?",
        own=OWN.get("version", ""),
        agent=VERSION,
        agent_min=plugin.get("agent_min") or "?",
    )


async def async_choose(hass: HomeAssistant) -> tuple[str, dict[str, Any] | None]:
    """At the agent's start: which copy serves, Repairs updated. Returns (AGENT | PLUGIN, the plugin or None)."""
    plugin = await async_find_plugin(hass)
    source, issue = pick(OWN, VERSION, plugin)
    report(hass, issue, plugin)
    if plugin is not None:
        _LOGGER.info("Hauflo: cards plugin %s at %s — the %s's cards serve", plugin.get("version") or "?", plugin["url"], source)
    return source, plugin
