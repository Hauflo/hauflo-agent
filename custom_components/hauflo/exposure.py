"""What the house gives each voice assistant and bridge, and «закрыть опасное» (0.34 п.7б).

GET  /api/hauflo/exposure → {"assistants": [{"id", "on", "mode", "count", "dangerous", "cut", "entities": [{"entity_id",
     "name", "danger"}]}], "dangerous": N}. Assist (conversation), Alexa / Google through Home Assistant Cloud (when on),
     Alice (Yandex Smart Home: its label; another filter — said, not listed) and each HomeKit bridge (its filter).
     Dangerous = the agent's own protection canon (__init__._protected: a lock, the alarm, a valve, a gate / garage door,
     a relay marked as one, a group or helper over one) plus tuning.json exposure.dangerous_domains (cameras).
POST /api/hauflo/exposure {"close": true} → the dangerous ones taken off every assistant that has them, nothing else:
     HA's expose settings (Assist / Alexa / Google), Alice's label off the entity, a HomeKit bridge's filter (out of its
     included entities, into its excluded ones). A fresh backup first (428 otherwise), as every change; admin only; the
     tunnel refuses it while the owner keeps the house closed. Answer: {"closed": [{"entity_id", "assistants"}], ...audit}.
Reads never change anything but what HA itself writes on asking (the default expose setting of a new entity).
"""

from __future__ import annotations

from collections.abc import Callable
import contextlib
import logging
from typing import Any

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entityfilter import (
    CONF_EXCLUDE_DOMAINS,
    CONF_EXCLUDE_ENTITIES,
    CONF_EXCLUDE_ENTITY_GLOBS,
    CONF_INCLUDE_DOMAINS,
    CONF_INCLUDE_ENTITIES,
    CONF_INCLUDE_ENTITY_GLOBS,
    convert_filter,
)

from . import backup
from .tuning import TUNING

# In the hello (FEATURES): the backend asks this agent for the audit only when it says so (an older one has no route).
FEATURE = "exposure"

_LOGGER = logging.getLogger(__name__)
_T = TUNING["exposure"]
ASSISTANTS: tuple[str, ...] = tuple(_T["assistants"])
DANGER_DOMAINS = set(_T["dangerous_domains"])
MAX_ROWS = int(_T["max_rows"])
HOMEKIT_DOMAINS = set(_T["homekit_domains"])
ALICE = "alice"
HOMEKIT = "homekit"
YANDEX_DOMAIN = "yandex_smart_home"
HOMEKIT_DOMAIN = "homekit"
_FILTER_KEYS = (CONF_INCLUDE_DOMAINS, CONF_INCLUDE_ENTITY_GLOBS, CONF_INCLUDE_ENTITIES, CONF_EXCLUDE_DOMAINS, CONF_EXCLUDE_ENTITY_GLOBS, CONF_EXCLUDE_ENTITIES)


def danger_of(entity_id: str, protected: set[str], never_domains: set[str]) -> str | None:
    """Why it is dangerous: its domain (a lock, the alarm, a valve, a camera) or «protected» (a gate, a relay marked as
    one, a group or helper over one); None — not dangerous."""
    domain = entity_id.split(".", 1)[0]
    if domain in never_domains or domain in DANGER_DOMAINS:
        return domain
    return "protected" if entity_id in protected else None


def _cloud_on(hass: HomeAssistant, assistant: str) -> bool:
    """Alexa / Google through Home Assistant Cloud: on only when the cloud is logged in and that assistant enabled."""
    cloud = hass.data.get("cloud")
    prefs = getattr(getattr(cloud, "client", None), "prefs", None)
    if prefs is None or not getattr(cloud, "is_logged_in", False):
        return False
    return bool(getattr(prefs, "alexa_enabled" if assistant.endswith("alexa") else "google_enabled", False))


def assistant_on(hass: HomeAssistant, assistant: str) -> bool:
    return assistant == ASSISTANTS[0] or _cloud_on(hass, assistant)


def _filter_of(options: Any) -> dict[str, list[str]]:
    flt = options.get("filter") if hasattr(options, "get") else None
    flt = flt if hasattr(flt, "get") else {}
    return {k: [str(x) for x in flt.get(k) or [] if isinstance(x, str)] for k in _FILTER_KEYS}


def _homekit_entities(hass: HomeAssistant, options: Any, states: list[str]) -> list[str]:
    """What a HomeKit bridge gives: its filter over the domains HomeKit bridges; an empty filter — all of them but the
    hidden and the diagnostic ones (HomeKit's own default); an accessory — its one entity."""
    flt = _filter_of(options)
    if options.get("mode") == "accessory":
        return [e for e in flt[CONF_INCLUDE_ENTITIES][:1] if e in states]
    test = convert_filter(flt)
    reg = er.async_get(hass)
    out = []
    for eid in states:
        if eid.split(".", 1)[0] not in HOMEKIT_DOMAINS:
            continue
        if test.empty_filter:
            entry = reg.async_get(eid)
            if entry is not None and (entry.entity_category is not None or entry.hidden_by is not None):
                continue
        elif not test(eid):
            continue
        out.append(eid)
    return out


def _alice_entries(hass: HomeAssistant) -> list[Any]:
    return list(hass.config_entries.async_entries(YANDEX_DOMAIN))


def _homekit_entries(hass: HomeAssistant) -> list[Any]:
    return list(hass.config_entries.async_entries(HOMEKIT_DOMAIN))


class Exposure:
    """The audit and the closing, with the agent's protection canon passed in."""

    def __init__(self, hass: HomeAssistant, protected: Callable[[], set[str]], never_domains: set[str]) -> None:
        self.hass = hass
        self._protected = protected
        self._never = never_domains

    def _should_expose(self, assistant: str, eid: str) -> bool:
        try:
            from homeassistant.components.homeassistant.exposed_entities import async_should_expose  # noqa: PLC0415
        except ImportError:
            return False
        try:
            return bool(async_should_expose(self.hass, assistant, eid))
        except Exception:  # noqa: BLE001 — an assistant this HA doesn't run
            return False

    def _groups(self) -> list[dict[str, Any]]:
        """Each assistant / bridge with what it is given (entity ids), before names and the cut."""
        hass = self.hass
        states = sorted(hass.states.async_entity_ids())
        out: list[dict[str, Any]] = []
        for a in ASSISTANTS:
            on = assistant_on(hass, a)
            out.append({"id": a, "on": on, "ids": [e for e in states if self._should_expose(a, e)] if on else []})
        reg = er.async_get(hass)
        for entry in _alice_entries(hass):
            label = entry.options.get("label") if hasattr(entry.options, "get") else None
            if label:
                ids = sorted(e.entity_id for e in er.async_entries_for_label(reg, str(label)))
                out.append({"id": ALICE, "entry": entry.entry_id, "title": entry.title, "on": True, "mode": "label", "ids": ids})
            else:
                out.append({"id": ALICE, "entry": entry.entry_id, "title": entry.title, "on": True, "mode": "other", "ids": []})
        for entry in _homekit_entries(hass):
            out.append({"id": HOMEKIT, "entry": entry.entry_id, "title": entry.title, "on": True, "mode": str(entry.options.get("mode") or "bridge"), "ids": _homekit_entities(hass, entry.options, states)})
        return out

    def audit(self) -> dict[str, Any]:
        protected = self._protected()
        total = 0
        rows: list[dict[str, Any]] = []
        for g in self._groups():
            ents = []
            for eid in g.pop("ids"):
                st = self.hass.states.get(eid)
                ents.append({"entity_id": eid, "name": (st.name if st is not None else eid)[:200], "danger": danger_of(eid, protected, self._never)})
            ents.sort(key=lambda r: (r["danger"] is None, r["entity_id"]))
            bad = sum(1 for r in ents if r["danger"])
            total += bad
            rows.append({**g, "count": len(ents), "dangerous": bad, "cut": max(0, len(ents) - MAX_ROWS), "entities": ents[:MAX_ROWS]})
        return {"assistants": rows, "dangerous": total}

    def close(self) -> list[dict[str, Any]]:
        """The dangerous ones off every assistant that has them; the rest untouched. Returns what was closed where."""
        hass = self.hass
        protected = self._protected()
        closed: dict[str, list[str]] = {}
        for g in self._groups():
            bad = [e for e in g["ids"] if danger_of(e, protected, self._never)]
            if not bad:
                continue
            if g["id"] in ASSISTANTS:
                from homeassistant.components.homeassistant.exposed_entities import async_expose_entity  # noqa: PLC0415

                for eid in bad:
                    with contextlib.suppress(Exception):
                        async_expose_entity(hass, g["id"], eid, False)
                        closed.setdefault(eid, []).append(g["id"])
            elif g["id"] == ALICE and g.get("mode") == "label":
                entry = hass.config_entries.async_get_entry(g["entry"])
                label = str(entry.options.get("label")) if entry is not None else None
                reg = er.async_get(hass)
                for eid in bad:
                    ent = reg.async_get(eid)
                    if ent is None or label not in ent.labels:
                        continue
                    try:
                        reg.async_update_entity(eid, labels=set(ent.labels) - {label})
                    except Exception as err:  # noqa: BLE001 — one HA refuses never stops the rest
                        _LOGGER.debug("Hauflo: Alice's label on %s not taken off: %s", eid, err)
                        continue
                    closed.setdefault(eid, []).append(ALICE)
            elif g["id"] == HOMEKIT:
                entry = hass.config_entries.async_get_entry(g["entry"])
                if entry is None or entry.options.get("mode") == "accessory":
                    continue  # one accessory = one device the owner paired by itself: said, never unpaired here
                flt = _filter_of(entry.options)
                gone = set(bad)
                flt[CONF_INCLUDE_ENTITIES] = [e for e in flt[CONF_INCLUDE_ENTITIES] if e not in gone]
                flt[CONF_EXCLUDE_ENTITIES] = sorted(set(flt[CONF_EXCLUDE_ENTITIES]) | gone)
                hass.config_entries.async_update_entry(entry, options={**entry.options, "filter": flt})
                for eid in bad:
                    closed.setdefault(eid, []).append(HOMEKIT)
        return [{"entity_id": e, "assistants": sorted(set(a))} for e, a in sorted(closed.items())]


class ExposureView(HomeAssistantView):
    """The audit (GET) and «закрыть опасное» (POST {"close": true}). Admin only."""

    url = "/api/hauflo/exposure"
    name = "api:hauflo:exposure"
    requires_auth = True

    def __init__(self, of: Callable[[HomeAssistant], Exposure | None]) -> None:
        self._of = of

    async def get(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        ex = self._of(request.app[KEY_HASS])
        if ex is None:
            return self.json_message("not ready", 409)
        return self.json(ex.audit())

    async def post(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict) or body != {"close": True}:
            return self.json_message('{"close": true} expected', 400)
        ex = self._of(request.app[KEY_HASS])
        if ex is None:
            return self.json_message("not ready", 409)
        if not backup.fresh():
            return self.json({"code": "backup_required", "message": "Hauflo agent: a backup first"}, 428)
        closed = ex.close()
        if closed:
            _LOGGER.info("Hauflo: dangerous entities taken off voice assistants / bridges: %s", ", ".join(c["entity_id"] for c in closed))
        return self.json({"closed": closed, **ex.audit()})
