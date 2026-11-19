from __future__ import annotations
import functools
import hashlib
import json
import logging
from pathlib import Path
from typing import Any
from aiohttp import web
from homeassistant.components import persistent_notification
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import CoreState, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from .const import CARDS_FILE, DATA_CARDS_FILE, DATA_CARDS_LOCAL, DATA_THEME_DEFAULTS, DATA_THEME_FIRING, DATA_THEME_LISTENER, DATA_WWW, VERSION
from .guard import contained
from .tuning import tuning
_LOGGER = logging.getLogger(__name__)
THEME = 'Hauflo Atelier'

def _www_hash() -> str:
    h = hashlib.sha256()
    www = Path(__file__).parent / 'www'
    for p in sorted((f for f in www.rglob('*') if f.is_file() and f.suffix in ('.js', '.json'))):
        h.update(p.relative_to(www).as_posix().encode() + p.read_bytes())
    return h.hexdigest()[:8]
asset_tag = functools.cache(lambda: f'{VERSION}&h={_www_hash()}')
OLD_THEMES = ('Hauflo Aura', 'Hauflo Atelier', 'Hauflo Atelier · светлая', 'Hauflo Atelier · тёмная')
icons_url = functools.cache(lambda: f'/hauflo_static/hauflo-icons.js?v={asset_tag()}')
cards_url = functools.cache(lambda: f'/hauflo_static/{CARDS_FILE}?v={asset_tag()}')
_DIR = Path(__file__).parent / 'www' / 'themes'

@functools.cache
def _theme_data() -> dict[str, Any]:
    return json.loads((_DIR / 'hauflo.json').read_text(encoding='utf-8'))
_common = functools.cache(lambda: _theme_data()['ha']['common'])
dark_cfg = functools.cache(lambda: _theme_data()['ha']['dark'])
light_cfg = functools.cache(lambda: _theme_data()['ha']['light'])
aura = functools.cache(lambda: {**_common(), 'modes': {'light': light_cfg(), 'dark': dark_cfg()}})
LIGHT_TAIL, DARK_TAIL = (' · светлая', ' · тёмная')
THEME_LIGHT = f'{THEME}{LIGHT_TAIL}'
THEME_DARK = f'{THEME}{DARK_TAIL}'
fixed = functools.cache(lambda: {THEME_LIGHT: {**_common(), **light_cfg()}, THEME_DARK: {**_common(), **dark_cfg()}})
_stale = functools.cache(lambda: tuple((t for t in OLD_THEMES if t != THEME and t not in fixed())))

def _valid(data: object) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get('name'), str) or (not data['name'].strip()):
        return False
    ha = data.get('ha')
    parts = [ha.get(k) for k in ('common', 'dark', 'light')] if isinstance(ha, dict) else [None]
    cards = data.get('cards', {})
    return all((isinstance(p, dict) and all((isinstance(v, str) for v in p.values())) for p in [*parts, cards]))

def variants(data: dict) -> dict[str, dict]:
    """One theme file → HA's themes: the one that follows the phone and the fixed light / dark ones."""
    common, dark, light = (data['ha']['common'], data['ha']['dark'], data['ha']['light'])
    tokens = {f'hf-t-{k}': v for k, v in data.get('cards', {}).items()}
    base = {**common, **tokens}
    name = data['name'].strip()[:60]
    return {name: {**base, 'modes': {'light': light, 'dark': dark}}, f'{name}{LIGHT_TAIL}': {**base, **light}, f'{name}{DARK_TAIL}': {**base, **dark}}

def load_extra(folder: Path) -> dict[str, dict]:
    """Every other theme file in the folder (sorted by file name), a broken one skipped with a warning."""
    out: dict[str, dict] = {}
    for f in sorted(folder.glob('*.json')):
        if f.name == 'hauflo.json':
            continue
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = None
        if not _valid(data) or data['name'].strip() in (THEME, *fixed()):
            _LOGGER.warning("Hauflo: theme file %s skipped (not a theme or Hauflo's own name)", f.name)
            continue
        out.update(variants(data))
    return out
extra = functools.cache(lambda: load_extra(_DIR))

def _ours() -> dict:
    return {THEME: aura(), **fixed(), **extra()}

def names() -> set[str]:
    """The themes that are Hauflo's own — the only ones the cloud may make the house's default."""
    return set(_ours())

@callback
def _inject(hass: HomeAssistant) -> bool:
    themes = hass.data.get('frontend_themes')
    if themes is None:
        return False
    want = _ours()
    if all((themes.get(k) == v for k, v in want.items())) and (not any((t in themes for t in _stale()))):
        return False
    for old in _stale():
        themes.pop(old, None)
    themes.update(want)
    return True

async def _restore_default(hass: HomeAssistant) -> bool:
    saved = await Store(hass, 1, 'frontend_theme').async_load() or {}
    changed = False
    for key in ('frontend_default_theme', 'frontend_default_dark_theme'):
        if saved.get(key) in names() and hass.data.get(key) != saved[key]:
            hass.data[key] = saved[key]
            changed = True
    return changed
_DEFAULT_KEYS = ('frontend_default_theme', 'frontend_default_dark_theme')

def _remember_defaults(hass: HomeAssistant) -> None:
    hass.data[DATA_THEME_DEFAULTS] = {k: hass.data.get(k) for k in _DEFAULT_KEYS}

def _reapply_defaults(hass: HomeAssistant) -> bool:
    before = hass.data.get(DATA_THEME_DEFAULTS) or {}
    changed = False
    for k in _DEFAULT_KEYS:
        if before.get(k) in names() and hass.data.get(k) != before[k]:
            hass.data[k] = before[k]
            changed = True
    return changed

def _fire(hass: HomeAssistant) -> None:
    hass.data[DATA_THEME_FIRING] = True
    try:
        hass.bus.async_fire('themes_updated')
    finally:
        hass.data[DATA_THEME_FIRING] = False

async def async_setup(hass: HomeAssistant) -> None:
    injected = _inject(hass)
    if await _restore_default(hass) or injected:
        _fire(hass)
    _remember_defaults(hass)

    @callback
    def _after_reload(_event: Event) -> None:
        if hass.data.get(DATA_THEME_FIRING):
            return
        if _inject(hass):
            _reapply_defaults(hass)
            _fire(hass)
        _remember_defaults(hass)
    if not hass.data.get(DATA_THEME_LISTENER):
        hass.data[DATA_THEME_LISTENER] = hass.bus.async_listen('themes_updated', _after_reload)
    www = Path(__file__).parent / 'www'
    if not hass.data.get(DATA_WWW) and await hass.async_add_executor_job(www.is_dir):
        hass.data[DATA_WWW] = True
        hass.http.register_view(StaticView(www))
        hass.data[DATA_CARDS_FILE] = cards_url()
        if not hass.data.get(DATA_CARDS_LOCAL):
            add_extra_js_url(hass, icons_url())
            add_extra_js_url(hass, hass.data[DATA_CARDS_FILE])
            if hass.state is CoreState.running:
                _say_reload(hass)
CACHE_VERSIONED = 'public, max-age=31536000, immutable'
CACHE_PLAIN = 'no-cache'

class StaticView(HomeAssistantView):
    """The agent's www folder at /hauflo_static: HA's extra JS loads before anyone has logged in (no auth, like HA's own static paths), only files inside the folder."""
    url = '/hauflo_static/{path:.+}'
    name = 'hauflo:static'
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
        return web.FileResponse(f, headers={'Cache-Control': CACHE_VERSIONED if request.query.get('v') else CACHE_PLAIN})
    head = get
RELOAD_NOTE = 'hauflo_cards_reload'
reload_note_s = functools.cache(lambda: tuning()['theme']['reload_note_s'])
DATA_RELOAD_TIMER = 'hauflo_reload_note_timer'

def _say_reload(hass: HomeAssistant) -> None:
    ru = str(hass.config.language or '').startswith('ru')
    with contained(_LOGGER, 'Hauflo: reload hint not shown'):
        persistent_notification.async_create(hass, 'Агент Hauflo подключён. Если панель Hauflo не загрузилась, обновите страницу в браузере.' if ru else "The Hauflo agent is connected. If the Hauflo dashboard doesn't load, refresh the page in your browser.", 'Hauflo', RELOAD_NOTE)
        if callable((old := hass.data.pop(DATA_RELOAD_TIMER, None))):
            old()

        @callback
        def _gone(_now: Any) -> None:
            hass.data.pop(DATA_RELOAD_TIMER, None)
            persistent_notification.async_dismiss(hass, RELOAD_NOTE)
        hass.data[DATA_RELOAD_TIMER] = async_call_later(hass, reload_note_s(), _gone)

@callback
def async_unload(hass: HomeAssistant) -> None:
    """Unloaded or removed: «Reload themes» no longer puts the Hauflo theme back (HA review round 4)."""
    if callable((unsub := hass.data.pop(DATA_THEME_LISTENER, None))):
        unsub()
    if callable((timer := hass.data.pop(DATA_RELOAD_TIMER, None))):
        timer()
    hass.data.pop(DATA_THEME_FIRING, None)
    hass.data.pop(DATA_THEME_DEFAULTS, None)
