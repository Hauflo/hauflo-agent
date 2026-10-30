"""What the owner has to know or do, in HA's own «Repairs» (Settings → Repairs) — not only a notification that is
gone after one click (HA core + SRE review 29.10): the link to Hauflo lost for hours, the site unlinked in Hauflo,
KNX addresses waiting for the owner's allow, a feature HA's internals took away, an agent older than Hauflo needs.
Each issue is made when its cause appears and removed when it goes; the texts are in strings.json → «issues».
"""

from __future__ import annotations

import logging
import re

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

LINK_LOST = "link_lost"
UNLINKED = "unlinked"
RANGES_PENDING = "ranges_pending"
DEGRADED_FEATURE = "degraded"
OUTDATED = "outdated"
ALL = (LINK_LOST, UNLINKED, RANGES_PENDING, DEGRADED_FEATURE, OUTDATED)
_SEVERITY = {UNLINKED: ir.IssueSeverity.ERROR, OUTDATED: ir.IssueSeverity.ERROR}

# HA internals the agent leans on (theme store, Lovelace resources, the frontend user store, xknx): when one of
# them changes shape in an HA release, the feature is skipped, never the agent — diagnostics and Repairs name it.
DEGRADED: set[str] = set()


@callback
def degrade(feature: str, err: BaseException) -> None:
    if feature not in DEGRADED:
        DEGRADED.add(feature)
        _LOGGER.warning("Hauflo: %s unavailable on this Home Assistant (%s) — skipped", feature, err)


@callback
def raise_issue(hass: HomeAssistant, issue_id: str, fixable: bool = False, **placeholders: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=fixable,
        is_persistent=False,
        severity=_SEVERITY.get(issue_id, ir.IssueSeverity.WARNING),
        translation_key=issue_id,
        translation_placeholders=placeholders or None,
    )


@callback
def clear_issue(hass: HomeAssistant, issue_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, issue_id)


# A value rule's own issues (rules.py): «rule_fault_<id>» (its sensor gives no data), «rule_rate_<id>» (too many
# switches), «rule_paused_<id>» (changed by hand again and again) — one per rule, the text by its kind.
RULE_KINDS = ("rule_fault", "rule_rate", "rule_paused")


@callback
def raise_rule_issue(hass: HomeAssistant, kind: str, rule_id: str, **placeholders: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{kind}_{rule_id}",
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=kind,
        translation_placeholders={k: str(v) for k, v in placeholders.items()},
    )


@callback
def clear_all(hass: HomeAssistant) -> None:
    for issue_id in ALL:
        clear_issue(hass, issue_id)
    reg = ir.async_get(hass)
    for domain, issue_id in list(reg.issues):
        if domain == DOMAIN and issue_id.startswith(RULE_KINDS):
            clear_issue(hass, issue_id)


@callback
def sync_degraded(hass: HomeAssistant) -> None:
    if DEGRADED:
        raise_issue(hass, DEGRADED_FEATURE, features=", ".join(sorted(DEGRADED)))
    else:
        clear_issue(hass, DEGRADED_FEATURE)


@callback
def sync_ranges(hass: HomeAssistant, pending: set[str] | None, project: str = "") -> None:
    """Fixable: «Allow / Decline» in one click (repairs.py), the exact addresses in its form."""
    if pending:
        from .ranges import count, range_key  # noqa: PLC0415 — ranges imports this module

        raise_issue(hass, RANGES_PENDING, fixable=True, ranges=", ".join(sorted(pending, key=range_key)), count=str(count(pending)), project=project or "—")
    else:
        clear_issue(hass, RANGES_PENDING)


def _version(text: object) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(text or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def older(version: str, minimum: object) -> bool:
    """True when this agent's version is below what the server needs; an unreadable minimum never says so."""
    mine, least = _version(version), _version(minimum)
    return mine is not None and least is not None and mine < least


@callback
def sync_version(hass: HomeAssistant, version: str, minimum: object, latest: object) -> None:
    if older(version, minimum):
        raise_issue(hass, OUTDATED, version=version, minimum=str(minimum), latest=str(latest or minimum))
    else:
        clear_issue(hass, OUTDATED)
