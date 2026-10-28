"""Hauflo «Atelier» look for the client's Home Assistant (owner 2026-10-24): one theme with a light and a
dark mode (follows the phone), and Hauflo's own icon set `hauflo:*`.

The theme is added to HA's themes in memory — the client's configuration.yaml isn't touched. HA's
«Reload themes» drops it; the agent puts it back. Picked per user in the profile, or by Hauflo as the
default (frontend.set_theme) when the integrator asks.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import Event, HomeAssistant, callback

from .const import DATA_THEME_DEFAULTS, DATA_THEME_LISTENER, DATA_THEME_FIRING, DATA_WWW

_LOGGER = logging.getLogger(__name__)
THEME = "Hauflo Atelier"
# Names before the rename (owner 25.10: nothing is live yet — no copies, the old ones are removed).
OLD_THEMES = ("Hauflo Aura", "Hauflo Atelier", "Hauflo Atelier · светлая", "Hauflo Atelier · тёмная")
ICONS_URL = "/hauflo_static/hauflo-icons.js"
CARDS_URL = "/hauflo_static/hauflo-cards.js"

_COMMON = {
    "ha-card-border-radius": "30px",
    "ha-card-border-width": "0px",
    "ha-badge-border-radius": "14px",
    "ha-dialog-border-radius": "28px",
    "ha-tile-border-radius": "24px",
    "eh-radius": "30px",
    "ha-font-family-body": "Onest, Inter, -apple-system, 'SF Pro Text', 'Segoe UI', Roboto, sans-serif",
    "ha-font-family-heading": "Onest, Inter, -apple-system, 'SF Pro Display', 'Segoe UI', Roboto, sans-serif",
    "ha-card-header-font-size": "18px",
    "ha-view-sections-column-gap": "12px",
    "ha-section-grid-row-gap": "12px",
    "ha-section-grid-column-gap": "12px",
    # The tabs: no grey track, the active tab gets Hauflo's own mark (hauflo-cards.js).
    "ha-tab-indicator-color": "transparent",
    "ha-tab-track-color": "transparent",
}

# Graphite (dark) and paper (light): glass with an edge highlight, deep soft shadows, light as colour.
DARK = {
    "primary-color": "#5CE0B4",
    "accent-color": "#5CE0B4",
    "primary-background-color": "#08090A",
    # The warm glow low in the corner, fixed: the header bar and the page are one surface (designer review 25.10).
    "lovelace-background": "radial-gradient(90% 55% at 100% 100%, #1a1712 0%, #08090A 60%) fixed",
    "secondary-background-color": "#141517",
    "card-background-color": "#141517",
    "ha-card-background": "linear-gradient(180deg, rgba(255,255,255,.075), rgba(255,255,255,.03))",
    "ha-card-box-shadow": "inset 0 1px 0 rgba(255,255,255,.10), inset 0 0 0 1px rgba(255,255,255,.05), 0 12px 24px rgba(0,0,0,.35), 0 40px 80px rgba(0,0,0,.45)",
    "primary-text-color": "#F4F2EE",
    "secondary-text-color": "rgba(244,242,238,.64)",
    "disabled-text-color": "rgba(244,242,238,.46)",
    "divider-color": "rgba(255,255,255,.08)",
    "app-header-background-color": "#08090A",
    "app-header-text-color": "#F4F2EE",
    "app-header-border-bottom": "none",
    "sidebar-background-color": "#08090A",
    "sidebar-text-color": "#F4F2EE",
    "sidebar-selected-icon-color": "#F4F2EE",
    "sidebar-selected-text-color": "#F4F2EE",
    "state-icon-color": "rgba(244,242,238,.64)",
    "state-active-color": "#FFB25C",
    "state-light-active-color": "#FFB25C",
    "state-switch-active-color": "#5CE0B4",
    "state-cover-active-color": "#8DC2FF",
    "state-climate-heat-color": "#FFB25C",
    "state-climate-cool-color": "#8DC2FF",
    "state-climate-auto-color": "#5CE0B4",
    "state-binary_sensor-active-color": "#FF5A5F",
    "error-color": "#FF5A5F",
    "warning-color": "#FFB25C",
    "success-color": "#5CE0B4",
    "info-color": "#8DC2FF",
    "switch-checked-color": "#5CE0B4",
    "switch-checked-button-color": "#08090A",
    "switch-checked-track-color": "#5CE0B4",
    "input-fill-color": "rgba(255,255,255,.06)",
    "eh-ink": "#F4F2EE",
    "eh-ink2": "rgba(244,242,238,.64)",
    "eh-ink3": "rgba(244,242,238,.46)",
    "eh-hair": "rgba(255,255,255,.08)",
    "eh-glass": "linear-gradient(180deg, rgba(255,255,255,.075), rgba(255,255,255,.03))",
    "eh-edge": "inset 0 1px 0 rgba(255,255,255,.10), inset 0 0 0 1px rgba(255,255,255,.05)",
    "eh-deep": "0 1px 1px rgba(0,0,0,.4), 0 12px 24px rgba(0,0,0,.35), 0 40px 80px rgba(0,0,0,.45)",
    "eh-amber": "#FFB25C",
    "eh-amber-glow": "rgba(255,160,70,.55)",
    "eh-ice": "#8DC2FF",
    "eh-ice-glow": "rgba(90,160,255,.45)",
    "eh-alert": "#FF5A5F",
    "eh-alert-glow": "rgba(255,70,80,.55)",
    "eh-mint": "#5CE0B4",
    "eh-track": "rgba(255,255,255,.08)",
}

LIGHT = {
    "primary-color": "#14946C",
    "accent-color": "#14946C",
    "primary-background-color": "#EFECE6",
    "lovelace-background": "radial-gradient(90% 55% at 100% 100%, #F7EBDD 0%, #EFECE6 60%) fixed",
    "secondary-background-color": "#E6E2DA",
    "card-background-color": "#FBFAF8",
    "ha-card-background": "linear-gradient(180deg, rgba(255,255,255,.92), rgba(255,255,255,.72))",
    "ha-card-box-shadow": "inset 0 1px 0 #fff, inset 0 0 0 1px rgba(18,18,18,.04), 0 10px 20px rgba(40,30,20,.06), 0 30px 60px rgba(40,30,20,.08)",
    "primary-text-color": "#121212",
    "secondary-text-color": "rgba(18,18,18,.64)",
    "disabled-text-color": "rgba(18,18,18,.46)",
    "divider-color": "rgba(18,18,18,.08)",
    "app-header-background-color": "#EFECE6",
    "app-header-text-color": "#121212",
    "app-header-border-bottom": "none",
    "sidebar-background-color": "#EFECE6",
    "sidebar-text-color": "#121212",
    "sidebar-selected-icon-color": "#121212",
    "sidebar-selected-text-color": "#121212",
    "state-icon-color": "rgba(18,18,18,.64)",
    "state-active-color": "#E8901F",
    "state-light-active-color": "#E8901F",
    "state-switch-active-color": "#14946C",
    "state-cover-active-color": "#3C82F0",
    "state-climate-heat-color": "#E8901F",
    "state-climate-cool-color": "#3C82F0",
    "state-climate-auto-color": "#14946C",
    "state-binary_sensor-active-color": "#E5383E",
    "error-color": "#E5383E",
    "warning-color": "#E8901F",
    "success-color": "#14946C",
    "info-color": "#3C82F0",
    "switch-checked-color": "#14946C",
    "switch-checked-button-color": "#FFFFFF",
    "switch-checked-track-color": "#14946C",
    "input-fill-color": "rgba(18,18,18,.05)",
    "eh-ink": "#121212",
    "eh-ink2": "rgba(18,18,18,.64)",
    "eh-ink3": "rgba(18,18,18,.46)",
    "eh-hair": "rgba(18,18,18,.08)",
    "eh-glass": "linear-gradient(180deg, rgba(255,255,255,.92), rgba(255,255,255,.72))",
    "eh-edge": "inset 0 1px 0 #fff, inset 0 0 0 1px rgba(18,18,18,.04)",
    "eh-deep": "0 1px 1px rgba(40,30,20,.05), 0 10px 20px rgba(40,30,20,.06), 0 30px 60px rgba(40,30,20,.08)",
    "eh-amber": "#E8901F",
    "eh-amber-glow": "rgba(255,170,80,.55)",
    "eh-ice": "#3C82F0",
    "eh-ice-glow": "rgba(90,150,255,.35)",
    "eh-alert": "#E5383E",
    "eh-alert-glow": "rgba(255,80,80,.45)",
    "eh-mint": "#14946C",
    "eh-track": "rgba(18,18,18,.07)",
}

AURA = {**_COMMON, "modes": {"light": LIGHT, "dark": DARK}}
# Fixed variants: a dashboard or a view set to one of them stays light / dark whatever the phone says
# (owner 2026-10-25: «итоговый набор тёмной и светлой темы»).
THEME_LIGHT = f"{THEME} · светлая"
THEME_DARK = f"{THEME} · тёмная"
FIXED = {THEME_LIGHT: {**_COMMON, **LIGHT}, THEME_DARK: {**_COMMON, **DARK}}


# Names that are no longer ours. The current ones (THEME + FIXED) were in OLD_THEMES too, so «is an old
# name still there?» was always true and every themes_updated re-injected and fired again — an endless
# loop in HA (integrator review 28.10). Only names we don't ship any more are removed.
_STALE = tuple(t for t in OLD_THEMES if t != THEME and t not in FIXED)


def _ours() -> dict:
    return {THEME: AURA, **FIXED}


@callback
def _inject(hass: HomeAssistant) -> bool:
    """Put the Hauflo themes into HA's list. True only when the list actually changed."""
    themes = hass.data.get("frontend_themes")
    if themes is None:
        return False
    want = _ours()
    if all(themes.get(k) == v for k, v in want.items()) and not any(t in themes for t in _STALE):
        return False
    for old in _STALE:
        themes.pop(old, None)
    themes.update(want)
    return True


async def _restore_default(hass: HomeAssistant) -> bool:
    """HA picks the saved default theme when its frontend starts — before the agent added Atelier — and
    falls back to its own. Put the saved choice back now that the theme is there."""
    from homeassistant.helpers.storage import Store

    saved = await Store(hass, 1, "frontend_theme").async_load() or {}
    changed = False
    for key in ("frontend_default_theme", "frontend_default_dark_theme"):
        if saved.get(key) == THEME and hass.data.get(key) != THEME:
            hass.data[key] = THEME
            changed = True
    return changed


_DEFAULT_KEYS = ("frontend_default_theme", "frontend_default_dark_theme")


def _remember_defaults(hass: HomeAssistant) -> None:
    """What HA's default themes are now — read at every themes_updated, so that at a reload we know what they were
    just before it (an admin picking another theme fires the event too: the pick is what is remembered)."""
    hass.data[DATA_THEME_DEFAULTS] = {k: hass.data.get(k) for k in _DEFAULT_KEYS}


def _reapply_defaults(hass: HomeAssistant) -> bool:
    """«Reload themes» resets the default to HA's own when the theme isn't in the YAML list. Put Hauflo's back only
    where it was the default before the reload (the admin's later pick is remembered as that, so it stays)."""
    before = hass.data.get(DATA_THEME_DEFAULTS) or {}
    changed = False
    for k in _DEFAULT_KEYS:
        if before.get(k) == THEME and hass.data.get(k) != THEME:
            hass.data[k] = THEME
            changed = True
    return changed


def _fire(hass: HomeAssistant) -> None:
    # Re-entrancy guard: outside a dispatch HA runs listeners right inside async_fire, so our own listener
    # sees this event while the flag is set and skips it. Inside a dispatch (fired from the listener) HA
    # queues it; that next pass finds the list unchanged and doesn't fire — the loop always ends.
    hass.data[DATA_THEME_FIRING] = True
    try:
        hass.bus.async_fire("themes_updated")
    finally:
        hass.data[DATA_THEME_FIRING] = False


async def async_setup(hass: HomeAssistant) -> None:
    injected = _inject(hass)
    if await _restore_default(hass) or injected:
        _fire(hass)
    _remember_defaults(hass)

    @callback
    def _after_reload(_event: Event) -> None:
        # HA's «Reload themes» rebuilt the list from YAML: put the Hauflo themes back — fire only when the
        # list really changed, never for our own event.
        if hass.data.get(DATA_THEME_FIRING):
            return
        # The default theme Hauflo set comes back too (HA's reload resets it to «default»). Same single-fire rule.
        # Only after a reload (our themes were gone): at any other themes_updated the default is the admin's pick.
        if _inject(hass):
            _reapply_defaults(hass)
            _fire(hass)
        _remember_defaults(hass)

    if not hass.data.get(DATA_THEME_LISTENER):
        hass.data[DATA_THEME_LISTENER] = hass.bus.async_listen("themes_updated", _after_reload)
    # Icons, cards and the Onest font from the agent's www folder (nothing from the internet).
    www = Path(__file__).parent / "www"
    if not hass.data.get(DATA_WWW) and await hass.async_add_executor_job(www.is_dir):
        hass.data[DATA_WWW] = True
        await hass.http.async_register_static_paths([StaticPathConfig("/hauflo_static", str(www), False)])
        add_extra_js_url(hass, ICONS_URL)
        add_extra_js_url(hass, CARDS_URL)


@callback
def async_unload(hass: HomeAssistant) -> None:
    """Unloaded or removed: «Reload themes» no longer puts the Hauflo theme back (HA review round 4). The cards'
    JS stays registered — dashboards keep working until HA restarts (keep.py makes copies on removal)."""
    if callable(unsub := hass.data.pop(DATA_THEME_LISTENER, None)):
        unsub()
    hass.data.pop(DATA_THEME_FIRING, None)
    hass.data.pop(DATA_THEME_DEFAULTS, None)
