"""Hauflo «Atelier» look for the client's Home Assistant (owner 2026-10-24): one theme with a light and a
dark mode (follows the phone), and Hauflo's own icon set `hauflo:*`.

The theme is added to HA's themes in memory — the client's configuration.yaml isn't touched. HA's
«Reload themes» drops it; the agent puts it back. Picked per user in the profile, or by Hauflo as the
default (frontend.set_theme) when the integrator asks.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from aiohttp import web
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import KEY_HASS, HomeAssistantView

from homeassistant.components import persistent_notification
from homeassistant.core import CoreState, Event, HomeAssistant, callback

from .const import ASSET_TAG, CARDS_FILE, DATA_CARDS_FILE, DATA_CARDS_LOCAL, DATA_THEME_DEFAULTS, DATA_THEME_LISTENER, DATA_THEME_FIRING, DATA_WWW

_LOGGER = logging.getLogger(__name__)
THEME = "Hauflo Atelier"
# Names before the rename (owner 25.10: nothing is live yet — no copies, the old ones are removed).
OLD_THEMES = ("Hauflo Aura", "Hauflo Atelier", "Hauflo Atelier · светлая", "Hauflo Atelier · тёмная")
# The agent's version in the URL: the HA app and browsers kept serving the old cards after an update (owner 29.10:
# «4 из 4 работают», «Нагрев» from before 0.16.4 still on his phone) — a new version is a new URL, loaded fresh.
ICONS_URL = f"/hauflo_static/hauflo-icons.js?v={ASSET_TAG}"


CARDS_URL = f"/hauflo_static/{CARDS_FILE}?v={ASSET_TAG}"

# The theme is data (www/themes/hauflo.json — the same file the cards read their tokens from): HA's variables, common +
# graphite (dark) + paper (light).
_DIR = Path(__file__).parent / "www" / "themes"
_DATA = json.loads((_DIR / "hauflo.json").read_text(encoding="utf-8"))
_COMMON: dict[str, str] = _DATA["ha"]["common"]
DARK: dict[str, str] = _DATA["ha"]["dark"]
LIGHT: dict[str, str] = _DATA["ha"]["light"]

AURA = {**_COMMON, "modes": {"light": LIGHT, "dark": DARK}}
# Fixed variants: a dashboard or a view set to one of them stays light / dark whatever the phone says
# (owner 2026-10-25: «итоговый набор тёмной и светлой темы»).
LIGHT_TAIL, DARK_TAIL = " · светлая", " · тёмная"
THEME_LIGHT = f"{THEME}{LIGHT_TAIL}"
THEME_DARK = f"{THEME}{DARK_TAIL}"
FIXED = {THEME_LIGHT: {**_COMMON, **LIGHT}, THEME_DARK: {**_COMMON, **DARK}}


# Names that are no longer ours. The current ones (THEME + FIXED) were in OLD_THEMES too, so «is an old
# name still there?» was always true and every themes_updated re-injected and fired again — an endless
# loop in HA (integrator review 28.10). Only names we don't ship any more are removed.
_STALE = tuple(t for t in OLD_THEMES if t != THEME and t not in FIXED)


def _valid(data: object) -> bool:
    """A theme file: a name and HA's variables (common, dark, light) as strings; its cards' tokens as strings."""
    if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not data["name"].strip():
        return False
    ha = data.get("ha")
    parts = [ha.get(k) for k in ("common", "dark", "light")] if isinstance(ha, dict) else [None]
    cards = data.get("cards", {})
    return all(isinstance(p, dict) and all(isinstance(v, str) for v in p.values()) for p in [*parts, cards])


def variants(data: dict) -> dict[str, dict]:
    """One theme file → HA's themes: the one that follows the phone and the fixed light / dark ones. Another file's card
    tokens go in as `hf-t-<name>` (the cards read `--hf-<name>: var(--hf-t-<name>, Hauflo's own)`): a new theme is one
    JSON file in www/themes, no code (stage 5, item 6)."""
    common, dark, light = data["ha"]["common"], data["ha"]["dark"], data["ha"]["light"]
    tokens = {f"hf-t-{k}": v for k, v in data.get("cards", {}).items()}
    base = {**common, **tokens}
    name = data["name"].strip()[:60]
    return {name: {**base, "modes": {"light": light, "dark": dark}}, f"{name}{LIGHT_TAIL}": {**base, **light}, f"{name}{DARK_TAIL}": {**base, **dark}}


def load_extra(folder: Path) -> dict[str, dict]:
    """Every other theme file in the folder (sorted by file name), a broken one skipped with a warning."""
    out: dict[str, dict] = {}
    for f in sorted(folder.glob("*.json")):
        if f.name == "hauflo.json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if not _valid(data) or data["name"].strip() in (THEME, *FIXED):
            _LOGGER.warning("Hauflo: theme file %s skipped (not a theme or Hauflo's own name)", f.name)
            continue
        out.update(variants(data))
    return out


EXTRA = load_extra(_DIR)


def _ours() -> dict:
    return {THEME: AURA, **FIXED, **EXTRA}


def names() -> set[str]:
    """The themes that are Hauflo's own — the only ones the cloud may make the house's default."""
    return set(_ours())


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
        if saved.get(key) in names() and hass.data.get(key) != saved[key]:
            hass.data[key] = saved[key]
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
        if before.get(k) in names() and hass.data.get(k) != before[k]:
            hass.data[k] = before[k]
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
        hass.http.register_view(StaticView(www))
        hass.data[DATA_CARDS_FILE] = CARDS_URL
        # An independent house loads them from /config/www/hauflo (keep.py) — not twice.
        if not hass.data.get(DATA_CARDS_LOCAL):
            add_extra_js_url(hass, ICONS_URL)
            add_extra_js_url(hass, hass.data[DATA_CARDS_FILE])
            # Set up while HA runs (just paired): a tab that had already opened a dashboard loads neither the extra JS nor
            # the resources again until a reload (integrator review r2, D2) — HA says so. Gone at the next HA start.
            if hass.state is CoreState.running:
                _say_reload(hass)


# A versioned address (`?v=` — the cards, the icons) never changes its content: kept for a year. Without one (the fonts,
# an address typed by hand) the browser asks again every time (a 304 when nothing changed) — HA's own static paths say
# nothing, and a browser then guesses how long to keep a file, so a version-less address could stay old for days.
CACHE_VERSIONED = "public, max-age=31536000, immutable"
CACHE_PLAIN = "no-cache"


class StaticView(HomeAssistantView):
    """The agent's www folder at /hauflo_static: HA's extra JS loads before anyone has logged in (no auth, like HA's
    own static paths), only files inside the folder."""

    url = "/hauflo_static/{path:.+}"
    name = "hauflo:static"
    requires_auth = False

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _file(self, path: str) -> Path | None:
        f = (self._root / path).resolve()
        return f if f.is_relative_to(self._root) and f.is_file() else None

    async def get(self, request: web.Request, path: str) -> web.StreamResponse:
        f = await request.app[KEY_HASS].async_add_executor_job(self._file, path)
        if f is None:
            raise web.HTTPNotFound
        return web.FileResponse(f, headers={"Cache-Control": CACHE_VERSIONED if request.query.get("v") else CACHE_PLAIN})

    head = get


RELOAD_NOTE = "hauflo_cards_reload"


def _say_reload(hass: HomeAssistant) -> None:
    ru = str(hass.config.language or "").startswith("ru")
    try:
        persistent_notification.async_create(
            hass,
            "Агент Hauflo подключён. В уже открытых вкладках Home Assistant обновите страницу, чтобы загрузились карточки "
            "Hauflo — иначе панель Hauflo покажет «Ошибка конфигурации»."
            if ru
            else "The Hauflo agent is connected. Reload Home Assistant tabs that are already open so the Hauflo cards load — "
            "otherwise the Hauflo dashboard shows «Configuration error».",
            "Hauflo",
            RELOAD_NOTE,
        )
    except Exception:  # noqa: BLE001 — a hint, never stops the agent
        _LOGGER.debug("Hauflo: reload hint not shown", exc_info=True)


@callback
def async_unload(hass: HomeAssistant) -> None:
    """Unloaded or removed: «Reload themes» no longer puts the Hauflo theme back (HA review round 4). The cards'
    JS stays registered — dashboards keep working until HA restarts (keep.py makes copies on removal)."""
    if callable(unsub := hass.data.pop(DATA_THEME_LISTENER, None)):
        unsub()
    hass.data.pop(DATA_THEME_FIRING, None)
    hass.data.pop(DATA_THEME_DEFAULTS, None)
