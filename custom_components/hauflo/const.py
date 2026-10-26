"""Hauflo agent constants."""

from collections.abc import Callable

from homeassistant.util.hass_dict import HassKey

DOMAIN = "hauflo"
VERSION = "0.10.1"
DEFAULT_SERVER = "https://app.hauflo.ru"
# A pairing code starts with its server's region: «RU-…» → the Russian instance, «AE-…» → the international one.
REGION_SERVERS = {"RU": "https://app.hauflo.ru", "AE": "https://app.hauflo.com"}
CONF_SERVER = "server"
CONF_SECRET = "secret"
CONF_CODE = "code"
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
DATA_WWW: HassKey[bool] = HassKey(f"{DOMAIN}_www_registered")
# The group addresses (main/middle) Hauflo may send to — confirmed in HA by the integrator.
CONF_RANGES = "ranges"
# Ones Hauflo asked for that aren't confirmed yet (shown in the options flow).
DATA_PENDING_RANGES = "hauflo_pending_ranges"
