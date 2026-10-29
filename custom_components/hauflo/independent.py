"""«Сделать дом независимым» / «Make the house independent» (service-company review 29.10: a clean, non-breaking exit).

Run by the HA owner from the integration's options (never through the tunnel). It:
- keeps the dashboard's cards in /config/www/hauflo as Lovelace resources for good (keep.async_make_independent);
- writes each enabled alarm rule as a plain HA automation in automations.yaml — the same sources and alarm states, a
  push to the same Home Assistant app phones (critical where the rule is), a note in HA, the rule's button («Перекрыть
  воду») as the push's button — through the same plain-file check, backup copy and lock as the dashboard automations;
- says what stops working without Hauflo.
It deletes nothing: an automation already exported (by its id) is left as it is, Hauflo's own rules keep running until
the agent is removed (both would push meanwhile — said in the result).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util, slugify

from . import automations, keep
from .const import VERSION
from .notify import ACTION_EVENT, NotifyEngine

_LOGGER = logging.getLogger(__name__)
PREFIX = "hauflo_independent_"
BUTTON = "HAUFLO_IND_"
# The sensor's name and its room in the message — rendered by HA when the automation runs.
MESSAGE = "{{ state_attr(trigger.entity_id, 'friendly_name') or trigger.entity_id }}{% set a = area_name(trigger.entity_id) %}{% if a %} · {{ a }}{% endif %}"


def automation_id(rule_id: str) -> str:
    slug = slugify(rule_id)[:40] or "rule"
    return f"{PREFIX}{slug}_{hashlib.sha1(rule_id.encode()).hexdigest()[:6]}"


def _push_data(phone: dict[str, Any], critical: bool, button: dict[str, str] | None) -> dict[str, Any]:
    data: dict[str, Any] = {"tag": "hauflo-{{ trigger.entity_id }}"}
    if critical:
        if phone.get("os") == "ios":
            data["push"] = {"sound": {"name": "default", "critical": 1, "volume": 1.0}, "interruption-level": "critical"}
        else:
            data.update({"priority": "high", "ttl": 0, "channel": "alarm_stream", "importance": "high"})
    if button:
        data["actions"] = [button]
    return data


def automation_for(rule: dict[str, Any], phones: list[dict[str, Any]], ru: bool, when: str) -> dict[str, Any]:
    """One rule as a plain HA automation (HA's own automation editor format)."""
    title = rule["title"]
    critical = rule.get("priority") == "critical"
    aid = automation_id(rule["id"])
    act = rule.get("action")
    button = {"action": BUTTON + aid[len(PREFIX):].upper(), "title": act["title"] or ("Выполнить" if ru else "Run")} if act else None
    names = ", ".join(p.get("name") or p["service"] for p in phones) or ("никому — только уведомление в HA" if ru else "nobody — only a notification in HA")
    description = (
        f"Из правила аварии Hauflo «{title}» ({when}, агент {VERSION}). Телефоны: {names}. Без повторов и «Принято» — "
        "это делал Hauflo. Меняйте как обычную автоматизацию."
        if ru else
        f"From the Hauflo alarm rule «{title}» ({when}, agent {VERSION}). Phones: {names}. No repeats or «Accept» — "
        "Hauflo did those. Edit it like any automation."
    )
    # The phones first (a phone that fails doesn't stop the others), then a note in HA itself.
    alarm_actions: list[dict[str, Any]] = [
        {"action": f"notify.{phone['service']}", "continue_on_error": True, "data": {"title": title, "message": MESSAGE, "data": _push_data(phone, critical, button)}}
        for phone in phones
    ]
    alarm_actions.append({"action": "persistent_notification.create", "data": {"title": title, "message": MESSAGE, "notification_id": "hauflo-{{ trigger.entity_id }}"}})
    triggers: list[dict[str, Any]] = [{"trigger": "state", "entity_id": list(rule["sources"]), "to": list(rule["alarm_states"]), "id": "alarm"}]
    if act and button:
        triggers.append({"trigger": "event", "event_type": ACTION_EVENT, "event_data": {"action": button["action"]}, "id": "button"})
        actions: list[dict[str, Any]] = [
            {
                "choose": [
                    {
                        "conditions": [{"condition": "trigger", "id": "button"}],
                        "sequence": [{"action": act["service"], "target": {"entity_id": act["entity_id"]}}],
                    }
                ],
                "default": alarm_actions,
            }
        ]
    else:
        actions = alarm_actions
    return {
        "id": aid,
        "alias": f"Авария: {title}" if ru else f"Alarm: {title}",
        "description": description,
        "mode": "parallel",
        "max": 25,
        "triggers": triggers,
        "conditions": [],
        "actions": actions,
    }


async def async_export(hass: HomeAssistant, engine: NotifyEngine | None) -> dict[str, Any]:
    """Cards kept for good, alarm rules as HA automations. Returns what was done (shown to the owner)."""
    ru = not engine._english() if engine is not None else str(hass.config.language or "").startswith("ru")
    out: dict[str, Any] = {"cards": None, "added": [], "already": [], "skipped": [], "error": None}
    try:
        out["cards"] = await keep.async_make_independent(hass, VERSION)
    except Exception as err:  # noqa: BLE001 — the automations are still worth making
        _LOGGER.exception("Hauflo: cards not kept")
        out["cards"] = f"error: {err}"
    rules = (engine.config.get("rules") if engine is not None else None) or []
    when = dt_util.now().strftime("%Y-%m-%d")
    configs: list[dict[str, Any]] = []
    for rule in rules:
        if not rule.get("enabled") or not rule.get("sources"):
            out["skipped"].append(rule.get("title") or rule.get("id"))
            continue
        configs.append(automation_for(rule, engine._targets(rule) if engine is not None else [], ru, when))
    if configs and "automation" not in hass.config.components:
        out["error"] = ("В configuration.yaml нет «automation: !include automations.yaml» — автоматизации не созданы." if ru
                        else "configuration.yaml has no «automation: !include automations.yaml» — no automations made.")
        return out
    if configs:
        try:
            added, already = await automations.async_add_new(hass, configs)
        except automations.NotPlain:
            out["error"] = automations.NotPlain.RU if ru else automations.NotPlain.EN
            return out
        except Exception as err:  # noqa: BLE001 — said to the owner, nothing half-written (one atomic write)
            _LOGGER.exception("Hauflo: alarm rules not exported")
            out["error"] = str(err)
            return out
        alias = {c["id"]: c["alias"] for c in configs}
        out["added"] = [alias[i] for i in added]
        out["already"] = [alias[i] for i in already]
    return out


def summary(result: dict[str, Any], ru: bool) -> str:
    """The result in the owner's words (the options flow's last step and a notification)."""
    lines: list[str] = []
    cards = result.get("cards")
    if cards == keep.ADDED:
        lines.append("✓ Карточки панели: /config/www/hauflo, подключены ресурсами панелей." if ru else "✓ Dashboard cards: /config/www/hauflo, added as dashboard resources.")
    elif cards == keep.YAML:
        lines.append("! Карточки скопированы в /config/www/hauflo, но ресурсы панелей заданы в configuration.yaml — добавьте их (см. уведомление)." if ru
                     else "! Cards copied to /config/www/hauflo, but dashboard resources live in configuration.yaml — add them (see the notification).")
    else:
        lines.append(f"! Карточки не сохранены: {cards}" if ru else f"! Cards not kept: {cards}")
    if result.get("error"):
        lines.append(f"! Автоматизации не созданы: {result['error']}" if ru else f"! Automations not made: {result['error']}")
    added, already, skipped = result.get("added") or [], result.get("already") or [], result.get("skipped") or []
    if added:
        lines.append(("✓ Новые автоматизации HA: " if ru else "✓ New HA automations: ") + ", ".join(added))
    if already:
        lines.append(("Уже были (не тронуты): " if ru else "Already there (left as they are): ") + ", ".join(already))
    if skipped:
        lines.append(("Выключенные / пустые правила пропущены: " if ru else "Disabled / empty rules skipped: ") + ", ".join(str(s) for s in skipped))
    if not added and not already and not result.get("error"):
        lines.append("Правил аварий нет — автоматизации не нужны." if ru else "No alarm rules — no automations needed.")
    if added:
        lines.append("Пока агент установлен, тревога придёт дважды (от Hauflo и от автоматизации) — отключите одно из двух." if ru
                     else "While the agent is installed an alarm comes twice (Hauflo and the automation) — turn one of them off.")
    return "\n".join(lines)
