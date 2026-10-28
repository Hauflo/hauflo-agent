"""The Hauflo theme listener must never feed itself (integrator review 28.10: «Event themes_updated not fired:
more than 10000 events were queued … endless loop» on the first deploy)."""

from homeassistant.core import HomeAssistant, callback

from custom_components.hauflo import theme
from custom_components.hauflo.const import DATA_THEME_LISTENER, DATA_WWW


async def _setup(hass: HomeAssistant, themes: dict) -> list:
    hass.data["frontend_themes"] = themes
    hass.data[DATA_WWW] = True  # no http server in the tests: skip the static paths
    fired: list = []
    hass.bus.async_listen("themes_updated", callback(lambda e: fired.append(e)))
    await theme.async_setup(hass)
    await hass.async_block_till_done()
    return fired


async def test_repeated_themes_updated_is_bounded(hass: HomeAssistant) -> None:
    themes: dict = {"Mine": {"primary-color": "red"}}
    fired = await _setup(hass, themes)
    assert themes[theme.THEME] == theme.AURA and theme.THEME_LIGHT in themes and theme.THEME_DARK in themes
    assert themes["Mine"] == {"primary-color": "red"}
    fired.clear()
    # Anyone else firing themes_updated (the frontend, another integration) — the list is already right.
    for _ in range(20):
        hass.bus.async_fire("themes_updated")
    await hass.async_block_till_done()
    assert len(fired) == 20  # only those 20, nothing of ours
    theme.async_unload(hass)
    assert not hass.data.get(DATA_THEME_LISTENER)


async def test_reload_themes_puts_hauflo_back_once(hass: HomeAssistant) -> None:
    themes: dict = {}
    fired = await _setup(hass, themes)
    fired.clear()

    # HA's «Reload themes»: the list is rebuilt from YAML (Hauflo's themes gone) and themes_updated fired —
    # once from outside a dispatch and once from inside another listener (nested → queued by HA).
    themes.clear()
    themes["Mine"] = {}
    hass.bus.async_fire("themes_updated")
    await hass.async_block_till_done()
    assert theme.THEME in themes and len(fired) == 2  # HA's + one of ours, then quiet

    fired.clear()

    @callback
    def _reload(_e) -> None:
        themes.clear()
        hass.bus.async_fire("themes_updated")

    hass.bus.async_listen("test_reload", _reload)
    hass.bus.async_fire("test_reload")
    await hass.async_block_till_done()
    assert theme.THEME in themes and len(fired) == 2
    theme.async_unload(hass)


async def test_stale_names_removed_current_kept(hass: HomeAssistant) -> None:
    themes: dict = {"Hauflo Aura": {}, theme.THEME: {"old": "1"}}
    await _setup(hass, themes)
    assert "Hauflo Aura" not in themes and themes[theme.THEME] == theme.AURA
    assert theme._inject(hass) is False  # second pass is a no-op
    theme.async_unload(hass)


async def test_reload_themes_keeps_hauflo_as_default(hass: HomeAssistant) -> None:
    themes: dict = {}
    hass.data["frontend_default_theme"] = theme.THEME
    hass.data["frontend_default_dark_theme"] = None
    fired = await _setup(hass, themes)
    fired.clear()

    @callback
    def _reload() -> None:  # frontend.reload_themes: YAML list, default reset when its theme isn't in it
        themes.clear()
        hass.data["frontend_default_theme"] = "default"
        hass.bus.async_fire("themes_updated")

    for _ in range(5):
        _reload()
        await hass.async_block_till_done()
        assert theme.THEME in themes and hass.data["frontend_default_theme"] == theme.THEME
        assert hass.data["frontend_default_dark_theme"] is None
    assert len(fired) == 10  # HA's 5 + one of ours each, no loop

    # The admin picks another theme (event fired by set_theme) — a later reload leaves it alone.
    themes["Mine"] = {}
    hass.data["frontend_default_theme"] = "Mine"
    hass.bus.async_fire("themes_updated")
    await hass.async_block_till_done()
    themes.clear()
    themes["Mine"] = {}
    hass.bus.async_fire("themes_updated")
    await hass.async_block_till_done()
    assert hass.data["frontend_default_theme"] == "Mine" and theme.THEME in themes
    theme.async_unload(hass)


async def test_reload_does_not_make_hauflo_default_when_it_never_was(hass: HomeAssistant) -> None:
    themes: dict = {}
    hass.data["frontend_default_theme"] = "default"
    await _setup(hass, themes)
    themes.clear()
    hass.bus.async_fire("themes_updated")
    await hass.async_block_till_done()
    assert hass.data["frontend_default_theme"] == "default"
    theme.async_unload(hass)
