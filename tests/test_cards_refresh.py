"""The cards reload a page left from before an agent update (owner 29.10: the HA app kept the old cards until «Сбросить
кэш фронтенда»): the agent's version is on its link entity, and /hauflo_static never lets a version-less address go stale."""

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import theme
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN, VERSION


async def test_link_entity_says_the_agent_version(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)

    async def idle(_self):
        await asyncio.Event().wait()

    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        eid = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_link")
        attrs = hass.states.get(eid).attributes
        assert attrs["version"] == VERSION and attrs["server"] == "app.hauflo.ru"
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.fixture
async def client(hass: HomeAssistant, hass_client_no_auth, tmp_path):
    (tmp_path / "fonts").mkdir()
    (tmp_path / "hauflo-cards.js").write_text("// cards")
    (tmp_path / "fonts" / "a.woff2").write_bytes(b"font")
    (tmp_path.parent / "secret.txt").write_text("no")
    assert await async_setup_component(hass, "http", {})
    hass.http.register_view(theme.StaticView(tmp_path))
    return await hass_client_no_auth()


async def test_versioned_address_is_kept_plain_one_is_revalidated(client) -> None:
    res = await client.get(f"/hauflo_static/hauflo-cards.js?v={VERSION}")
    assert res.status == 200 and await res.text() == "// cards"
    assert res.headers["Cache-Control"] == theme.CACHE_VERSIONED
    head = await client.head(f"/hauflo_static/hauflo-cards.js?v={VERSION}")
    assert head.status == 200 and head.headers["Cache-Control"] == theme.CACHE_VERSIONED
    for url in ("/hauflo_static/hauflo-cards.js", "/hauflo_static/fonts/a.woff2"):
        res = await client.get(url)
        assert res.status == 200 and res.headers["Cache-Control"] == "no-cache", url
        # Revalidation is cheap: the same file answers 304.
        again = await client.get(url, headers={"If-None-Match": res.headers["ETag"]})
        assert again.status == 304, url


async def test_only_files_inside_the_folder(client, tmp_path) -> None:
    view = theme.StaticView(tmp_path)
    assert view._file("../secret.txt") is None and view._file("fonts/../hauflo-cards.js") is not None
    for url in ("/hauflo_static/../secret.txt", "/hauflo_static/%2e%2e/secret.txt", "/hauflo_static/fonts", "/hauflo_static/nope.js"):
        res = await client.get(url)
        assert res.status == 404, url


async def test_cards_urls_carry_the_version() -> None:
    assert theme.CARDS_URL.endswith(f"?v={VERSION}") and theme.ICONS_URL.endswith(f"?v={VERSION}")
