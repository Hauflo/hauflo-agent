"""A clean exit (service-company review and HA review 29.10): removing the integration leaves the family's dashboard
working — the cards copied to /config/www/hauflo and added as Lovelace resources — and a house made independent keeps
them while the agent is installed too."""

from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import keep, theme
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DATA_CARDS_LOCAL, DOMAIN, VERSION


async def _idle(self):  # the link loop: no Hauflo server in tests
    return None


async def _add(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.fixture
async def lovelace(hass: HomeAssistant, tmp_path: Path):
    hass.config.config_dir = str(tmp_path)
    assert await async_setup_component(hass, "lovelace", {})
    await hass.async_block_till_done()
    return tmp_path


SERVED = sorted(f"/hauflo_static/{n}?v={VERSION}" for n in keep.MODULES)


def _urls(hass: HomeAssistant) -> list[str]:
    res = keep._resources(hass)
    return sorted(item["url"] for item in res.async_items())


async def test_removal_keeps_the_dashboard_cards(hass: HomeAssistant, lovelace: Path, hass_storage, aioclient_mock) -> None:
    aioclient_mock.post("https://app.hauflo.ru/api/agent/unpair", json={})
    entry = await _add(hass)
    # Installed: the agent serves them — its own addresses are resources too (a tab opened before pairing, D2).
    assert _urls(hass) == SERVED
    await entry.runtime_data.ownership.async_set_plan({"create": 1})
    assert "hauflo.ownership" in hass_storage
    with patch("custom_components.hauflo.voice.async_remove"):
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
    assert "hauflo.ownership" not in hass_storage  # the last plan goes with the agent
    for name in ("hauflo-cards.js", "hauflo-icons.js", "fonts/onest-latin.woff2"):
        assert (lovelace / "www" / "hauflo" / name).is_file(), name
    want = sorted(f"/local/hauflo/{n}?v={VERSION}" for n in keep.MODULES)
    assert _urls(hass) == want
    # Saved at once: an HA restart right after the removal keeps them.
    assert sorted(i["url"] for i in hass_storage["lovelace_resources"]["data"]["items"]) == want
    # Added again: the agent serves the cards, the fallback resources go (the files stay, harmless).
    await _add(hass)
    assert _urls(hass) == SERVED
    assert hass.data[DATA_CARDS_LOCAL] is False


async def test_served_resources_follow_the_version_and_stay_single(hass: HomeAssistant, lovelace: Path) -> None:
    """The agent's resources are its theme's extra JS addresses, moved to a new version, never doubled, theirs kept."""
    assert sorted([theme.CARDS_URL, theme.ICONS_URL]) == SERVED
    res = keep._resources(hass)
    await res.async_get_info()
    await res.async_create_item({"res_type": "module", "url": "/hauflo_static/hauflo-cards.js?v=0.1.0"})
    await res.async_create_item({"res_type": "module", "url": "/hauflo_static/hauflo-cards.js?v=0.2.0"})
    await res.async_create_item({"res_type": "module", "url": "/local/theirs.js"})
    assert await keep.async_on_start(hass, VERSION) is False
    assert _urls(hass) == sorted([*SERVED, "/local/theirs.js"])
    assert await keep.async_on_start(hass, VERSION) is False  # a second start changes nothing
    assert _urls(hass) == sorted([*SERVED, "/local/theirs.js"])
    # Independent: the agent's addresses go, the copies' come.
    assert await keep.async_make_independent(hass, VERSION) == keep.ADDED
    assert _urls(hass) == sorted([*(f"/local/hauflo/{n}?v={VERSION}" for n in keep.MODULES), "/local/theirs.js"])


async def test_a_resource_of_an_older_agent_is_moved_to_this_version(hass: HomeAssistant, lovelace: Path) -> None:
    await async_setup_component(hass, "lovelace", {})
    res = keep._resources(hass)
    await res.async_get_info()
    await res.async_create_item({"res_type": "module", "url": "/local/hauflo/hauflo-cards.js?v=0.1.0"})
    await res.async_create_item({"res_type": "module", "url": "/local/theirs.js"})
    assert await keep.async_keep_dashboard(hass, VERSION) == keep.ADDED
    assert _urls(hass) == sorted([f"/local/hauflo/hauflo-cards.js?v={VERSION}", f"/local/hauflo/hauflo-icons.js?v={VERSION}", "/local/theirs.js"])


async def test_yaml_resources_are_said_not_pretended(hass: HomeAssistant, tmp_path: Path) -> None:
    hass.config.config_dir = str(tmp_path)
    assert await async_setup_component(hass, "lovelace", {"lovelace": {"resource_mode": "yaml"}})
    with patch.object(keep.persistent_notification, "async_create") as note:
        assert await keep.async_keep_dashboard(hass, VERSION) == keep.YAML
    assert (tmp_path / "www" / "hauflo" / "hauflo-cards.js").is_file()
    # HA says what to add, in its language — never a silent «kept».
    text = note.call_args.args[1]
    assert note.call_args.args[3] == keep.NOTE_ID and "/local/hauflo/hauflo-cards.js" in text and "resources" in text
    # Nothing remembered as independent: the agent keeps serving its own cards.
    assert await keep.async_make_independent(hass, VERSION) == keep.YAML
    assert not await keep.async_independent(hass)


async def test_independent_house_keeps_its_cards_while_installed(hass: HomeAssistant, lovelace: Path) -> None:
    entry = await _add(hass)
    assert await keep.async_make_independent(hass, VERSION) == keep.ADDED
    with patch("custom_components.hauflo.Agent.run", _idle):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    # Not released at the next start, refreshed instead; the agent doesn't add a second copy of the scripts.
    assert _urls(hass) == sorted(f"/local/hauflo/{n}?v={VERSION}" for n in keep.MODULES)
    assert hass.data[DATA_CARDS_LOCAL] is True
    # An updated agent: the copy and the address follow its version.
    (lovelace / "www" / "hauflo" / "hauflo-cards.js").write_text("old")
    assert await keep.async_on_start(hass, "9.9.9") is True
    assert _urls(hass) == sorted(f"/local/hauflo/{n}?v=9.9.9" for n in keep.MODULES)
    assert (lovelace / "www" / "hauflo" / "hauflo-cards.js").read_text() != "old"


async def test_paired_while_running_says_reload_open_tabs(hass: HomeAssistant, lovelace: Path) -> None:
    """Set up while HA runs (paired in a browser tab): HA tells to reload tabs that already opened a dashboard (D2)."""
    assert await async_setup_component(hass, "http", {})
    with patch.object(theme.persistent_notification, "async_create") as note, patch.object(theme, "add_extra_js_url") as js:
        entry = await _add(hass)
    assert sorted(c.args[1] for c in js.call_args_list) == SERVED
    assert [c.args[3] for c in note.call_args_list] == [theme.RELOAD_NOTE]
    # A reload of the entry in the same HA run says nothing again.
    with patch.object(theme.persistent_notification, "async_create") as note, patch("custom_components.hauflo.Agent.run", _idle):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert note.call_count == 0
