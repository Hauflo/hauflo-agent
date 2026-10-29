"""Diagnostics for support (HA core review 25.10): the link, the status hauflo sees and the alarm rules —
secrets, user ids and the server's address path redacted; no personal data (HA reviewer r2): the service contact's
name, phone and e-mail, who the home moved from / to and the project's name (often the client's surname) are redacted;
phones and people only as counts."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import service
from .const import CONF_REFRESH_TOKEN_ID, CONF_SECRET, CONF_SERVER, CONF_USER_ID, DATA_UNLINKED, DOMAIN, VERSION

TO_REDACT = {CONF_SECRET, CONF_REFRESH_TOKEN_ID, CONF_USER_ID}
# service.py: the contact, the access history's companies; ownership.py: the plan's project name.
PERSONAL = {"name", "phone", "email", "from", "to", "project"}


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
        # What Hauflo made here, hand edits, the next deploy (ownership.py) — counts and entity ids, as the sensor shows.
        "ownership": async_redact_data(agent.ownership.facts(), PERSONAL) if agent and agent.ownership else None,
        # Who services the home and its access history (service.py) — what the owner sees in the options.
        "service": async_redact_data(info, PERSONAL) if (info := await service.async_load(hass)) else None,
    }
