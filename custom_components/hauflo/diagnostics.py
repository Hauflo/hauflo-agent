"""Diagnostics for support (HA core review 25.10): the link, the status hauflo sees and the alarm rules —
secrets, user ids and the server's address path redacted."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_REFRESH_TOKEN_ID, CONF_SECRET, CONF_SERVER, CONF_USER_ID, DATA_UNLINKED, DOMAIN, VERSION

TO_REDACT = {CONF_SECRET, CONF_REFRESH_TOKEN_ID, CONF_USER_ID}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    agent = getattr(entry, "runtime_data", None)
    engine = agent.engine if agent else None
    status = agent.status() if agent else {}
    rules = (engine.config.get("rules") if engine else None) or []
    from . import DEGRADED  # noqa: PLC0415

    return {
        "agent_version": VERSION,
        "degraded": sorted(DEGRADED),
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "server_host": urlparse(entry.data.get(CONF_SERVER, "")).hostname,
        "linked": bool(agent and agent.linked),
        "unlinked_by_hauflo": bool(hass.data.get(DATA_UNLINKED)),
        "status": {
            "knx": status.get("knx"),
            "unavailable": status.get("unavailable", {}).get("count"),
            "monitored": len(status.get("monitored", [])),
            "alarms_ready": status.get("alarmsReady"),
            "open_alarms": len(status.get("alarms", [])),
        },
        "alarm_rules": [{"kind": r.get("kind"), "enabled": r.get("enabled"), "sources": len(r.get("sources", [])), "priority": r.get("priority"), "repeat_min": r.get("repeat_min"), "action": bool(r.get("action"))} for r in rules],
        "phones": len(engine.phones()) if engine else 0,
    }
