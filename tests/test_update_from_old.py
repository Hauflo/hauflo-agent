"""Update 0.19 / 0.20 → this agent (owner 04.11 п.7 «2г»): a house keeps its settings, its link, the dashboard merge bases
and the family's dashboard, and the browser takes the new cards without «Сбросить кэш фронтенда» — the Lovelace resources
move to the new address (version + file hash), HA's extra JS carries it, the version-less path is revalidated."""

import copy
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import dashmerge, keep, theme
from custom_components.hauflo.const import ASSET_TAG, CONF_SECRET, CONF_SERVER, DATA_CARDS_LOCAL, DOMAIN, VERSION


async def _idle(self):  # the link loop: no Hauflo server in tests
    return None


def _store(data, key):
    return {"version": 1, "minor_version": 1, "key": key, "data": data}


BASE = {"views": [{"title": "Гостиная", "path": "living", "cards": [{"type": "tile", "entity": "light.a", dashmerge.ID: "v/living/c:a"}], dashmerge.ID: "v/living"}]}
KEY = "ab" * 32
# What agents of 0.19 / 0.20 had saved (shapes as their code dumped them).
OLD = {
    dashmerge.STORE_KEY: {"hauflo-home": BASE},
    dashmerge.META_KEY: {"key": KEY, "copies": {"hauflo-home": ["hauflo-home-copy-20261001-101010"]}, "taken": ["hauflo-home-copy-20261001-101010"]},
    "hauflo.season": {"config": {"include": ["climate.living"], "names": "season"}, "season": "heat", "at": 1759305600.0, "by": "Иван", "migrated": True, "suggest_applied": None, "last_resend": 1.0},
    "hauflo.rules": {"rules": []},
    "hauflo.notify": {"rules": []},
    "hauflo.guest": {},
    "hauflo.titles": {},
    "hauflo.order": {},
}


@pytest.fixture
async def lovelace(hass: HomeAssistant, tmp_path: Path):
    hass.config.config_dir = str(tmp_path)
    assert await async_setup_component(hass, "lovelace", {})
    await hass.async_block_till_done()
    return tmp_path


async def _old_house(hass: HomeAssistant, hass_storage, old_version: str):
    for key, data in OLD.items():
        hass_storage[key] = _store(copy.deepcopy(data), key)
    res = keep._resources(hass)
    await res.async_get_info()
    # What the old agent had put: its own addresses with its version (the old tag — a version, 0.20 also a hash).
    for name in keep.MODULES:
        await res.async_create_item({"res_type": "module", "url": f"/hauflo_static/{name}?v={old_version}"})
    await res.async_create_item({"res_type": "module", "url": "/local/theirs.js"})
    return MockConfigEntry(
        domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s3cret"},
    )


@pytest.mark.parametrize("old", ["0.19.0", "0.20.0&h=0123456789ab"])
async def test_update_keeps_everything_and_moves_the_cards(hass: HomeAssistant, lovelace: Path, hass_storage, old: str) -> None:
    entry = await _old_house(hass, hass_storage, old)
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    # The link and the settings are the entry's: untouched.
    assert entry.data[CONF_SERVER] == "https://app.hauflo.ru" and entry.data[CONF_SECRET] == "s3cret"
    # The merge bases and the read-placeholder key: loaded as they were, never regenerated.
    merge = entry.runtime_data.dashboards
    assert merge.bases == OLD[dashmerge.STORE_KEY] and merge.key == bytes.fromhex(KEY)
    assert merge.copies == OLD[dashmerge.META_KEY]["copies"] and merge.taken_copies == set(OLD[dashmerge.META_KEY]["taken"])
    # What the old agent saved is still there, and the season is as chosen.
    for key in (dashmerge.STORE_KEY, dashmerge.META_KEY):
        assert hass_storage[key]["data"] == OLD[key], key
    assert entry.runtime_data.season.season == "heat" and entry.runtime_data.season.config["include"] == ["climate.living"]
    # The browser: the agent's resources are moved to the new address (never doubled), the integrator's own stay.
    urls = sorted(i["url"] for i in keep._resources(hass).async_items())
    assert urls == sorted([*(f"/hauflo_static/{n}?v={ASSET_TAG}" for n in keep.MODULES), "/local/theirs.js"])
    assert not any(old in u for u in urls) and theme.CARDS_URL.endswith(ASSET_TAG) and ASSET_TAG.startswith(VERSION)
    # Saved at once: an HA restart right after the update keeps the new addresses.
    assert sorted(i["url"] for i in hass_storage["lovelace_resources"]["data"]["items"]) == urls
    assert hass.data[DATA_CARDS_LOCAL] is False
    # Updated again with nothing new (a restart): nothing changes.
    with patch("custom_components.hauflo.Agent.run", _idle):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert sorted(i["url"] for i in keep._resources(hass).async_items()) == urls


async def test_independent_house_update_refreshes_its_copy(hass: HomeAssistant, lovelace: Path, hass_storage) -> None:
    entry = await _old_house(hass, hass_storage, "0.19.0")
    hass_storage[keep.STORE_KEY] = _store({"cards": True}, keep.STORE_KEY)
    res = keep._resources(hass)
    for item in list(res.async_items()):
        if item["url"].startswith("/hauflo_static/"):
            await res.async_delete_item(item["id"])
    for name in keep.MODULES:
        await res.async_create_item({"res_type": "module", "url": f"/local/hauflo/{name}?v=0.19.0"})
    (lovelace / "www" / "hauflo").mkdir(parents=True)
    (lovelace / "www" / "hauflo" / "hauflo-cards.js").write_text("// 0.19 cards")
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert hass.data[DATA_CARDS_LOCAL] is True
    assert sorted(i["url"] for i in res.async_items()) == sorted([*(f"/local/hauflo/{n}?v={ASSET_TAG}" for n in keep.MODULES), "/local/theirs.js"])
    assert (lovelace / "www" / "hauflo" / "hauflo-cards.js").read_text() != "// 0.19 cards"  # the copy is the new file
