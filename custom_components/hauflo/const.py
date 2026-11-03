"""Hauflo agent constants."""

import hashlib
from collections.abc import Callable
from pathlib import Path

from homeassistant.util.hass_dict import HassKey

DOMAIN = "hauflo"
VERSION = "0.18.0"


def _www_hash() -> str:
    h = hashlib.sha256()
    for p in sorted((Path(__file__).parent / "www").glob("*.js")):
        h.update(p.name.encode() + p.read_bytes())
    return h.hexdigest()[:8]


# The cards' address tag: the version (the cards compare `v` with the agent's) + the files' content — a file rebuilt
# under the same version (a hotfix, the test stand) is a new address too, never the browser's year-old copy (30.10).
ASSET_TAG = f"{VERSION}&h={_www_hash()}"
DEFAULT_SERVER = "https://app.hauflo.com"
# A pairing code starts with its server's region: «RU-…» → the Russian instance (data of Russian clients stays in
# Russia), «EU-…» and any other → the international one (owner 26.10).
REGION_SERVERS = {"RU": "https://app.hauflo.ru", "EU": "https://app.hauflo.com", "AE": "https://app.hauflo.com"}
# Plan B: a code without a region the international server doesn't know (or can't be reached — blocked) is tried here.
FALLBACK_SERVER = "https://app.hauflo.ru"
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
# «Вид карточек»: the current cards file, or the new one (cards v5); the house is switched by the owner in the options.
CONF_CARDS_VIEW = "cards_view"
CARDS_CURRENT = "current"
CARDS_NEW = "new"
CARDS_FILES = {CARDS_CURRENT: "hauflo-cards.js", CARDS_NEW: "hauflo-cards-v5.js"}
CONF_ACCEPT_PENDING = "accept_pending"
# «Сделать дом независимым» (independent.py): a tick in the options that leads to its own confirm step.
CONF_INDEPENDENT = "independent"
# The tunnel's answer to a write while the owner keeps the house closed (Hauflo says where to switch it back).
LOCKED_CODE = "locked_by_owner"


def cards_file(hass) -> str:
    """The cards file this house loads: its option «Вид карточек» (the current one unless the owner chose the new one)."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.options.get(CONF_CARDS_VIEW) == CARDS_NEW:
            return CARDS_FILES[CARDS_NEW]
    return CARDS_FILES[CARDS_CURRENT]
