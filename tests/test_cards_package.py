"""«Hauflo Cards» beside the agent (п.13): one copy of the cards serves — the plugin only when it is newer than the
agent's own cards and this agent is at least its agent_min; otherwise the agent's, and Repairs says what to update."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.hauflo import cards, keep
from custom_components.hauflo.const import ASSET_TAG, DATA_CARDS_LOCAL, DOMAIN

from .test_exit import SERVED, _add, _urls, lovelace  # noqa: F401 — the fixture

OWN = {"version": "1.4.0", "agent_min": "0.28.0"}
URL = "/hacsfiles/hauflo-cards/hauflo-cards.js?hacstag=12345"


def test_versions() -> None:
    assert cards.parse_version("1.2.3") == (1, 2, 3)
    assert cards.parse_version("v1.2.3b0") == (1, 2, 3)
    assert cards.parse_version("") is None and cards.parse_version(None) is None and cards.parse_version("x1") is None
    assert cards.at_least("1.10.0", "1.9.9") and cards.at_least("1.2", "1.2.0") and not cards.at_least("1.2.0", "1.2.1")
    assert cards.at_least("0.1.0", "") and not cards.at_least("", "0.1.0")


@pytest.mark.parametrize(
    ("agent", "plugin", "want"),
    [
        ("0.28.0", None, (cards.AGENT, None)),
        ("0.28.0", {"version": "1.5.0", "agent_min": "0.28.0"}, (cards.PLUGIN, None)),
        ("0.28.0", {"version": "1.4.0", "agent_min": "0.28.0"}, (cards.AGENT, None)),  # a tie: the agent's
        ("0.28.0", {"version": "1.3.9", "agent_min": "0.27.0"}, (cards.AGENT, cards.OUTDATED)),
        ("0.28.0", {"version": "2.0.0", "agent_min": "0.30.0"}, (cards.AGENT, cards.NEED_AGENT)),
        ("0.28.0", {"version": "", "agent_min": ""}, (cards.AGENT, cards.OUTDATED)),  # unreadable: never a guess
        ("0.28.0", {"version": "1.5.0", "agent_min": ""}, (cards.PLUGIN, None)),
    ],
)
def test_pick(agent: str, plugin: dict | None, want: tuple) -> None:
    assert cards.pick(OWN, agent, plugin) == want


def test_header_and_file(tmp_path: Path) -> None:
    assert cards.read_header('// hauflo-cards {"version":"1.0.0","agent_min":"0.28.0"}\n') == {"version": "1.0.0", "agent_min": "0.28.0"}
    for bad in ("", "// hauflo-cards", "// hauflo-cards {nope}", "// hauflo-cards [1]", "const x = 1;"):
        assert cards.read_header(bad) is None, bad
    # The agent's own addresses are not the plugin.
    assert cards.plugin_url(["/hauflo_static/hauflo-cards.js?v=1", "/local/hauflo/hauflo-cards.js?v=1", "/local/x.js"]) is None
    assert cards.plugin_url(["/local/x.js", URL]) == URL
    assert cards.plugin_file(tmp_path, URL) == (tmp_path / "www/community/hauflo-cards/hauflo-cards.js").resolve()
    assert cards.plugin_file(tmp_path, "/local/cards/hauflo-cards.js") == (tmp_path / "www/cards/hauflo-cards.js").resolve()
    assert cards.plugin_file(tmp_path, "/hacsfiles/../../secrets/hauflo-cards.js") is None  # never outside www
    assert cards.plugin_file(tmp_path, "https://cdn.example/hauflo-cards.js") is None


def _plugin(root: Path, version: str, agent_min: str = "0.28.0") -> None:
    f = root / "www/community/hauflo-cards/hauflo-cards.js"
    f.parent.mkdir(parents=True, exist_ok=True)
    head = json.dumps({"version": version, "agent_min": agent_min}, separators=(",", ":"))
    f.write_text(f"// hauflo-cards {head}\nexport {{}};\n")


async def _resource(hass: HomeAssistant) -> None:
    res = keep._resources(hass)
    await res.async_get_info()
    await res.async_create_item({"res_type": "module", "url": URL})


def _issues(hass: HomeAssistant) -> set[str]:
    return {i for (d, i) in ir.async_get(hass).issues if d == DOMAIN and i in cards.ISSUES}


@pytest.fixture
def versions():
    with patch.object(cards, "OWN", OWN), patch.object(cards, "VERSION", "0.28.0"):
        yield


async def test_newer_plugin_serves_alone(hass: HomeAssistant, lovelace: Path, versions) -> None:  # noqa: F811
    await _resource(hass)
    res = keep._resources(hass)
    await res.async_create_item({"res_type": "module", "url": SERVED[0]})
    await res.async_create_item({"res_type": "module", "url": f"/local/hauflo/hauflo-cards.js?v={ASSET_TAG}"})
    _plugin(lovelace, "1.5.0")
    assert await keep.async_on_start(hass, ASSET_TAG) is True  # no extra JS from the agent
    assert _urls(hass) == [URL]
    assert _issues(hass) == set()


async def test_older_plugin_yields_and_repairs_say_so(hass: HomeAssistant, lovelace: Path, versions) -> None:  # noqa: F811
    await _resource(hass)
    _plugin(lovelace, "1.3.0")
    assert await keep.async_on_start(hass, ASSET_TAG) is False
    assert _urls(hass) == sorted([*SERVED, URL])
    assert _issues(hass) == {cards.OUTDATED}
    issue = ir.async_get(hass).async_get_issue(DOMAIN, cards.OUTDATED)
    assert issue.translation_placeholders["plugin"] == "1.3.0" and issue.translation_placeholders["own"] == "1.4.0"
    # The plugin updated: the next start hands over, the issue goes.
    _plugin(lovelace, "1.5.0")
    assert await keep.async_on_start(hass, ASSET_TAG) is True
    assert _urls(hass) == [URL]
    assert _issues(hass) == set()


async def test_plugin_needing_a_newer_agent(hass: HomeAssistant, lovelace: Path, versions) -> None:  # noqa: F811
    await _resource(hass)
    _plugin(lovelace, "2.0.0", agent_min="0.30.0")
    assert await keep.async_on_start(hass, ASSET_TAG) is False
    assert _issues(hass) == {cards.NEED_AGENT}
    assert ir.async_get(hass).async_get_issue(DOMAIN, cards.NEED_AGENT).translation_placeholders["agent_min"] == "0.30.0"
    # Removed: no plugin, no issue.
    res = keep._resources(hass)
    for item in list(res.async_items()):
        if item["url"] == URL:
            await res.async_delete_item(item["id"])
    assert await keep.async_on_start(hass, ASSET_TAG) is False
    assert _issues(hass) == set()


async def test_plugin_file_missing_is_the_agents(hass: HomeAssistant, lovelace: Path, versions) -> None:  # noqa: F811
    await _resource(hass)  # a resource left without its file
    assert await keep.async_on_start(hass, ASSET_TAG) is False
    assert SERVED[0] in _urls(hass)
    assert _issues(hass) == {cards.OUTDATED}


async def test_setup_and_removal_with_a_newer_plugin(hass: HomeAssistant, lovelace: Path, hass_storage, aioclient_mock, versions) -> None:  # noqa: F811
    aioclient_mock.post("https://app.hauflo.ru/api/agent/unpair", json={})
    await _resource(hass)
    _plugin(lovelace, "1.5.0")
    with patch("custom_components.hauflo.theme.add_extra_js_url") as extra:
        entry = await _add(hass)
    assert hass.data[DATA_CARDS_LOCAL] is True
    assert extra.call_count == 0  # the plugin's file alone on the page
    assert _urls(hass) == [URL]
    with patch("custom_components.hauflo.voice.async_remove"):
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
    # The plugin keeps the dashboard: no copy beside it.
    assert not (lovelace / "www" / "hauflo").exists()
    assert _urls(hass) == [URL]


def test_bundle_header_is_what_the_agent_reads() -> None:
    """www/cards.json is the cards' version; cards/build.mjs writes it as the bundle's first line in this format."""
    own = json.loads((Path(cards.__file__).parent / "www" / "cards.json").read_text())
    head = json.dumps({"version": own["version"], "agent_min": own["agent_min"]}, separators=(",", ":"))
    assert cards.read_header(f"// hauflo-cards {head}") == own
    assert cards.parse_version(own["version"]) and cards.parse_version(own["agent_min"])
