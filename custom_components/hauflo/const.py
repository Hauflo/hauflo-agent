"""Hauflo agent constants."""

import hashlib
from collections.abc import Callable
from pathlib import Path

from homeassistant.util.hass_dict import HassKey

from . import servers as _servers

DOMAIN = "hauflo"
VERSION = "0.20.0"


def _www_hash() -> str:
    """Every served file, the data ones in subfolders too (themes/, locales/, domains.json): a changed token or word is
    a new address as well."""
    h = hashlib.sha256()
    www = Path(__file__).parent / "www"
    for p in sorted(f for f in www.rglob("*") if f.is_file() and f.suffix in (".js", ".json")):
        h.update(p.relative_to(www).as_posix().encode() + p.read_bytes())
    return h.hexdigest()[:8]


# The cards' address tag: the version (the cards compare `v` with the agent's) + the files' content — a file rebuilt
# under the same version (a hotfix, the test stand) is a new address too, never the browser's year-old copy (30.10).
ASSET_TAG = f"{VERSION}&h={_www_hash()}"
# Hauflo's servers are data (servers.json, servers.py). A pairing code starts with its server's region: «RU-…» → the
# Russian instance (data of Russian clients stays in Russia), «EU-…» and any other → the international one (owner 26.10).
DEFAULT_SERVER = _servers.DEFAULT_SERVER
REGION_SERVERS = {region: hosts[0] for region, hosts in _servers.REGIONS.items()}
# Plan B: a code without a region the international server doesn't know (or can't be reached — blocked) is tried here.
FALLBACK_SERVER = _servers.PAIR_FALLBACK_SERVER
CONF_SERVER = "server"
CONF_SECRET = "secret"
CONF_CODE = "code"
CONF_CONFIRM_TAKEOVER = "confirm_takeover"
CONF_USER_ID = "user_id"
CONF_REFRESH_TOKEN_ID = "refresh_token_id"
MONITOR_LABEL = "Hauflo мониторинг"
MONITOR_LABEL_EN = "Hauflo monitoring"
MONITOR_LABELS = {MONITOR_LABEL.casefold(), MONITOR_LABEL_EN.casefold()}
STATUS_EVERY = 60

# The KNX UI entities Hauflo created or took over (unique_ids): only these may be deleted through the tunnel.
OWNED_STORE = f"{DOMAIN}.knx_owned"

# hass.data keys, typed (HA review round 4).
DATA_UNLINKED: HassKey[bool] = HassKey(f"{DOMAIN}_unlinked")
DATA_NOTIFY_VIEWS: HassKey[bool] = HassKey(f"{DOMAIN}_notify_views")
DATA_SIDEBAR_LISTENER: HassKey[Callable[[], None]] = HassKey(f"{DOMAIN}_sidebar_listener")
DATA_THEME_LISTENER: HassKey[Callable[[], None]] = HassKey(f"{DOMAIN}_theme_listener")
DATA_THEME_FIRING: HassKey[bool] = HassKey(f"{DOMAIN}_theme_firing")
DATA_THEME_DEFAULTS: HassKey[dict[str, str | None]] = HassKey(f"{DOMAIN}_theme_defaults")
DATA_WWW: HassKey[bool] = HassKey(f"{DOMAIN}_www_registered")
# «Сделать дом независимым»: the cards come from /config/www/hauflo (Lovelace resources), the agent adds no copy of its own.
DATA_CARDS_FILE: HassKey[str] = HassKey(f"{DOMAIN}_cards_file")
DATA_CARDS_LOCAL: HassKey[bool] = HassKey(f"{DOMAIN}_cards_local")
# The group addresses (main/middle) Hauflo may send to — confirmed in HA by the integrator.
CONF_RANGES = "ranges"
# Ones Hauflo asked for that aren't confirmed yet (shown in the options flow).
DATA_PENDING_RANGES = "hauflo_pending_ranges"
# «Allow Hauflo to change this home's setup» (options, on by default): off — the tunnel refuses every config write;
# reads, monitoring and alarms keep working. Read live from the entry's options: no restart needed.
CONF_ALLOW_CHANGES = "allow_changes"
# The cards file. The trial «Вид карточек» files (0.17–0.18) are gone: a resource left from them is dropped (keep.py),
# and the option an owner saved then is ignored (config_flow drops it at the next save).
CARDS_FILE = "hauflo-cards.js"
OLD_CARDS_FILES = ("hauflo-cards-v3.js", "hauflo-cards-v5.js")
CONF_CARDS_VIEW_OLD = "cards_view"
CONF_ACCEPT_PENDING = "accept_pending"
# «Сделать дом независимым» (independent.py): a tick in the options that leads to its own confirm step.
CONF_INDEPENDENT = "independent"
# The tunnel's answer to a write while the owner keeps the house closed (Hauflo says where to switch it back).
LOCKED_CODE = "locked_by_owner"


def wants_ru(body: object, hass_language: str | None) -> bool:
    """The language of an answer to a card: the house's language the card sends (`lang`, from its dashboard), else HA's.
    The test house's HA was English under a Russian dashboard: errors came in English (03.11)."""
    lang = body.get("lang") if isinstance(body, dict) else None
    return str(lang if isinstance(lang, str) and lang.strip() else hass_language or "").lower().startswith("ru")
