"""Hauflo agent constants."""

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from homeassistant.util.hass_dict import HassKey

from . import servers as _servers

DOMAIN = "hauflo"
VERSION = "0.36.0"


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
CONF_INTERCOM = "intercom"  # Р55: the doors' intercom (intercom/): {bind, port, public, doors: [...]}
# Alarm rules as Home Assistant's own objects (wave 5B, owner 06.11): per rule an automation in automations.yaml and two
# helpers («Принято», «авария открыта») — they push, repeat and take «Принято» without the agent or the internet (independent.py).
ALARM_PREFIX = "hauflo_alarm_"  # the automation's id (not an entity id: HA names the entity from the alias)
ACK_PREFIX = "hauflo_ack_"  # the input_boolean's id → input_boolean.hauflo_ack_<rule>
OPEN_PREFIX = "hauflo_open_"  # the input_boolean's id → input_boolean.hauflo_open_<rule>: on = the rule's alarm is open (told, not back)
# Before 0.31: the input_text hauflo_chatter_<rule> (a chattering sensor's memory) — only removed now (independent.async_sync).
CHATTER_PREFIX = "hauflo_chatter_"
ACK_ACTION = "HAUFLO_ACK_"  # the push's «Принято» button (mobile_app_notification_action)
RUN_ACTION = "HAUFLO_RUN_"  # the push's rule button («Перекрыть воду»)
# The same buttons of a rule written in the flat form (Р83, alarm_form.py): one shared automation each takes them — never
# the old form's own triggers (a rule of both forms in one house is told once).
OK_ACTION = "HAUFLO_OK_"
DO_ACTION = "HAUFLO_DO_"
PUSH_SCRIPT = "hauflo_push"  # script.hauflo_push — the push of every rule of the flat form
RUN_PREFIX = "hauflo_run_"  # script.hauflo_run_<rule> — a rule's push button
NATIVE_STORE = f"{DOMAIN}.notify_native"
# The context parent of a helper the agent sets quietly (the move of an accepted alarm, «Принято» from a card the agent
# tells itself): the automation then says nothing twice.
QUIET_PARENT = f"{DOMAIN}_quiet"
# The tunnel's answer to a write while the owner keeps the house closed (Hauflo says where to switch it back).
LOCKED_CODE = "locked_by_owner"


def site_ru(hass_language: str | None, config: object = None) -> bool:
    """The ONE language of everything the house says by itself — every push, HA notice and logbook line (0.33 С3): the
    project's, as Hauflo wrote it into the alarm engine's config (`lang`), else Home Assistant's. A push in English over
    a Russian HA said «🚨 Equipment alarm» while the value rules' pushes to the same phone came in Russian. Names of rooms
    and devices are the integrator's and stay as written."""
    lang = config.get("lang") if isinstance(config, dict) else None
    return str(lang if isinstance(lang, str) and lang.strip() else hass_language or "ru").lower().startswith("ru")


SITE_LANGS = ("ru", "en")


async def async_entity_names(hass: Any) -> dict[str, dict[str, str]]:
    """The agent's entity names with a placeholder in both languages ({lang: {"<platform>.<key>": template}}, from the
    translations' «entity» section) — for the names a site gives its own sensors (combination, rule): HA words them in
    its own language («Hauflo Combination · Приток…» on an English HA of a Russian project, w5 acceptance), the house
    says everything in ONE language (site_ru)."""
    from homeassistant.helpers import translation  # noqa: PLC0415

    pre, suf = f"component.{DOMAIN}.entity.", ".name"
    out: dict[str, dict[str, str]] = {}
    for lang in SITE_LANGS:
        got = await translation.async_get_translations(hass, lang, "entity", [DOMAIN])
        out[lang] = {k[len(pre) : -len(suf)]: v for k, v in got.items() if k.startswith(pre) and k.endswith(suf)}
    return out


def site_name(names: dict[str, dict[str, str]], ru: bool, key: str, **ph: str) -> str | None:
    """`key` («binary_sensor.combo») worded in the house's language; None — no texts (HA's own translation then)."""
    text = names.get(SITE_LANGS[0] if ru else SITE_LANGS[1], {}).get(key)
    if not text:
        return None
    try:
        return text.format(**ph)
    except (KeyError, IndexError, ValueError):
        return None


def wants_ru(body: object, hass_language: str | None) -> bool:
    """The language of an answer to a card: the house's language the card sends (`lang`, from its dashboard), else HA's.
    The test house's HA was English under a Russian dashboard: errors came in English (03.11)."""
    lang = body.get("lang") if isinstance(body, dict) else None
    return str(lang if isinstance(lang, str) and lang.strip() else hass_language or "").lower().startswith("ru")
