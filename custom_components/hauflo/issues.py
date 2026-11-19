from __future__ import annotations
from collections.abc import Iterator
from contextlib import contextmanager
import logging
import re
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from .const import DOMAIN, HassKey
from .ranges import count, describe
_LOGGER = logging.getLogger(__name__)
LINK_LOST = 'link_lost'
UNLINKED = 'unlinked'
RANGES_PENDING = 'ranges_pending'
DEGRADED_FEATURE = 'degraded'
AGENT_FAULT = 'agent_fault'
OUTDATED = 'outdated'
CARDS_OUTDATED = 'cards_plugin_outdated'
CARDS_NEED_AGENT = 'cards_need_agent'
UPDATE_NOT_SIGNED = 'update_not_signed'
ALL = (LINK_LOST, UNLINKED, RANGES_PENDING, DEGRADED_FEATURE, AGENT_FAULT, OUTDATED, CARDS_OUTDATED, CARDS_NEED_AGENT, UPDATE_NOT_SIGNED)
_SEVERITY = {UNLINKED: ir.IssueSeverity.ERROR, OUTDATED: ir.IssueSeverity.ERROR, UPDATE_NOT_SIGNED: ir.IssueSeverity.ERROR}
HA, AGENT = ('ha', 'agent')
KINDS = {HA: DEGRADED_FEATURE, AGENT: AGENT_FAULT}
DATA_DEGRADED: HassKey[dict[str, str]] = HassKey(f'{DOMAIN}_degraded')

def _degraded(hass: object, make: bool=True) -> dict[str, str] | None:
    data = getattr(hass, 'data', None)
    if not isinstance(data, dict):
        return None
    return data.setdefault(DATA_DEGRADED, {}) if make else data.get(DATA_DEGRADED)

@callback
def degrade(hass: object, feature: str, err: BaseException, kind: str=HA) -> None:
    """The feature skipped: told once with its stack (a repeat only at debug, the stack of the first is enough)."""
    state = _degraded(hass)
    if state is not None and state.get(feature) == kind:
        _LOGGER.debug('Hauflo: %s still unavailable: %s', feature, err)
        return
    if state is not None:
        state[feature] = kind
    if kind == AGENT:
        _LOGGER.error('Hauflo: %s failed — skipped', feature, exc_info=err)
    else:
        _LOGGER.warning('Hauflo: %s unavailable on this Home Assistant (%s) — skipped', feature, err, exc_info=err)

@contextmanager
def degrading(hass: object, feature: str, kind: str=HA) -> Iterator[None]:
    """The block's feature skipped on any error in it: `degrade` tells it (the setup stage never stops the agent)."""
    try:
        yield
    except Exception as err:
        degrade(hass, feature, err, kind)

@callback
def recover(hass: object, feature: str) -> None:
    """The feature works again (a later call succeeded): no longer named; Repairs follow with the next status."""
    state = _degraded(hass, make=False)
    if state and state.pop(feature, None) is not None:
        _LOGGER.info('Hauflo: %s works again', feature)

def degraded(hass: object) -> dict[str, list[str]]:
    """{kind: [feature, ...]} — only the kinds that have one (the minute's status, diagnostics)."""
    state = _degraded(hass, make=False) or {}
    return {kind: sorted((f for f, k in state.items() if k == kind)) for kind in KINDS if kind in state.values()}

@callback
def raise_issue(hass: HomeAssistant, issue_id: str, fixable: bool=False, **placeholders: str) -> None:
    ir.async_create_issue(hass, DOMAIN, issue_id, is_fixable=fixable, is_persistent=False, severity=_SEVERITY.get(issue_id, ir.IssueSeverity.WARNING), translation_key=issue_id, translation_placeholders=placeholders or None)

@callback
def clear_issue(hass: HomeAssistant, issue_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, issue_id)
RULE_KINDS = ('rule_fault', 'rule_rate', 'rule_paused')
ENTRY_KINDS = ('integration_down',)

@callback
def raise_rule_issue(hass: HomeAssistant, kind: str, rule_id: str, **placeholders: str) -> None:
    ir.async_create_issue(hass, DOMAIN, f'{kind}_{rule_id}', is_fixable=False, is_persistent=False, severity=ir.IssueSeverity.WARNING, translation_key=kind, translation_placeholders={k: str(v) for k, v in placeholders.items()})

@callback
def clear_all(hass: HomeAssistant) -> None:
    for issue_id in ALL:
        clear_issue(hass, issue_id)
    reg = ir.async_get(hass)
    for domain, issue_id in list(reg.issues):
        if domain == DOMAIN and issue_id.startswith(RULE_KINDS + ENTRY_KINDS):
            clear_issue(hass, issue_id)

@callback
def sync_degraded(hass: HomeAssistant) -> None:
    now = degraded(hass)
    for kind, issue_id in KINDS.items():
        if now.get(kind):
            raise_issue(hass, issue_id, features=', '.join(now[kind]))
        else:
            clear_issue(hass, issue_id)

@callback
def clear_degraded(hass: HomeAssistant) -> None:
    """The entry unloaded: what was skipped is tried again by the next setup — nothing named until then."""
    hass.data.pop(DATA_DEGRADED, None)
    sync_degraded(hass)

@callback
def sync_ranges(hass: HomeAssistant, pending: set[str] | None, project: str='') -> None:
    """Fixable: «Allow / Decline» in one click (repairs.py), the exact addresses in its form."""
    if pending:
        raise_issue(hass, RANGES_PENDING, fixable=True, ranges=describe(pending), count=str(count(pending)), project=project or '—')
    else:
        clear_issue(hass, RANGES_PENDING)

def _version(text: object) -> tuple[int, ...] | None:
    m = re.fullmatch('v?(\\d+)\\.(\\d+)\\.(\\d+)', str(text or '').strip())
    return tuple((int(x) for x in m.groups())) if m else None

def older(version: str, minimum: object) -> bool:
    """True when this agent's version is below what the server needs; an unreadable minimum never says so."""
    mine, least = (_version(version), _version(minimum))
    return mine is not None and least is not None and (mine < least)

@callback
def sync_version(hass: HomeAssistant, version: str, minimum: object, latest: object) -> None:
    if older(version, minimum):
        raise_issue(hass, OUTDATED, version=version, minimum=str(minimum), latest=str(latest or minimum))
    else:
        clear_issue(hass, OUTDATED)
