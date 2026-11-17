import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any
from homeassistant.util.hass_dict import HassKey
from . import servers as _servers
DOMAIN = 'hauflo'
VERSION = '0.39.0'

def _www_hash() -> str:
    h = hashlib.sha256()
    www = Path(__file__).parent / 'www'
    for p in sorted((f for f in www.rglob('*') if f.is_file() and f.suffix in ('.js', '.json'))):
        h.update(p.relative_to(www).as_posix().encode() + p.read_bytes())
    return h.hexdigest()[:8]
ASSET_TAG = f'{VERSION}&h={_www_hash()}'
DEFAULT_SERVER = _servers.DEFAULT_SERVER
REGION_SERVERS = {region: hosts[0] for region, hosts in _servers.REGIONS.items()}
FALLBACK_SERVER = _servers.PAIR_FALLBACK_SERVER
CONF_SERVER = 'server'
CONF_SECRET = 'secret'
CONF_CODE = 'code'
CONF_CONFIRM_TAKEOVER = 'confirm_takeover'
CONF_USER_ID = 'user_id'
CONF_REFRESH_TOKEN_ID = 'refresh_token_id'
MONITOR_LABEL = 'Hauflo мониторинг'
MONITOR_LABEL_EN = 'Hauflo monitoring'
MONITOR_LABELS = {MONITOR_LABEL.casefold(), MONITOR_LABEL_EN.casefold()}
STATUS_EVERY = 60
OWNED_STORE = f'{DOMAIN}.knx_owned'
DATA_UNLINKED: HassKey[bool] = HassKey(f'{DOMAIN}_unlinked')
DATA_NOTIFY_VIEWS: HassKey[bool] = HassKey(f'{DOMAIN}_notify_views')
DATA_SIDEBAR_LISTENER: HassKey[Callable[[], None]] = HassKey(f'{DOMAIN}_sidebar_listener')
DATA_THEME_LISTENER: HassKey[Callable[[], None]] = HassKey(f'{DOMAIN}_theme_listener')
DATA_THEME_FIRING: HassKey[bool] = HassKey(f'{DOMAIN}_theme_firing')
DATA_THEME_DEFAULTS: HassKey[dict[str, str | None]] = HassKey(f'{DOMAIN}_theme_defaults')
DATA_WWW: HassKey[bool] = HassKey(f'{DOMAIN}_www_registered')
DATA_CARDS_FILE: HassKey[str] = HassKey(f'{DOMAIN}_cards_file')
DATA_CARDS_LOCAL: HassKey[bool] = HassKey(f'{DOMAIN}_cards_local')
CONF_RANGES = 'ranges'
DATA_PENDING_RANGES = 'hauflo_pending_ranges'
CONF_ALLOW_CHANGES = 'allow_changes'
CARDS_FILE = 'hauflo-cards.js'
OLD_CARDS_FILES = ('hauflo-cards-v3.js', 'hauflo-cards-v5.js')
CONF_CARDS_VIEW_OLD = 'cards_view'
CONF_ACCEPT_PENDING = 'accept_pending'
CONF_INDEPENDENT = 'independent'
CONF_INTERCOM = 'intercom'
ALARM_PREFIX = 'hauflo_alarm_'
ACK_PREFIX = 'hauflo_ack_'
OPEN_PREFIX = 'hauflo_open_'
CHATTER_PREFIX = 'hauflo_chatter_'
ACK_ACTION = 'HAUFLO_ACK_'
RUN_ACTION = 'HAUFLO_RUN_'
OK_ACTION = 'HAUFLO_OK_'
DO_ACTION = 'HAUFLO_DO_'
PUSH_SCRIPT = 'hauflo_push'
RUN_PREFIX = 'hauflo_run_'
NATIVE_STORE = f'{DOMAIN}.notify_native'
QUIET_PARENT = f'{DOMAIN}_quiet'
LOCKED_CODE = 'locked_by_owner'

def site_ru(hass_language: str | None, config: object=None) -> bool:
    lang = config.get('lang') if isinstance(config, dict) else None
    return str(lang if isinstance(lang, str) and lang.strip() else hass_language or 'ru').lower().startswith('ru')
SITE_LANGS = ('ru', 'en')

async def async_entity_names(hass: Any) -> dict[str, dict[str, str]]:
    from homeassistant.helpers import translation
    pre, suf = (f'component.{DOMAIN}.entity.', '.name')
    out: dict[str, dict[str, str]] = {}
    for lang in SITE_LANGS:
        got = await translation.async_get_translations(hass, lang, 'entity', [DOMAIN])
        out[lang] = {k[len(pre):-len(suf)]: v for k, v in got.items() if k.startswith(pre) and k.endswith(suf)}
    return out

def site_name(names: dict[str, dict[str, str]], ru: bool, key: str, **ph: str) -> str | None:
    text = names.get(SITE_LANGS[0] if ru else SITE_LANGS[1], {}).get(key)
    if not text:
        return None
    try:
        return text.format(**ph)
    except (KeyError, IndexError, ValueError):
        return None

def wants_ru(body: object, hass_language: str | None) -> bool:
    lang = body.get('lang') if isinstance(body, dict) else None
    return str(lang if isinstance(lang, str) and lang.strip() else hass_language or '').lower().startswith('ru')
