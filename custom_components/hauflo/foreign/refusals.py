"""Why a member or a shown entity of another integration is refused."""
from __future__ import annotations
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_registry as er
from ..policy.const import PROTECTED_COVERS, gate_name
from ..policy.own import helper_operates_known
from .common import MEMBER_DOMAINS, NEVER_PLATFORMS, shown_domains
from .flags import flags
from .wizard import is_protected

def member_refusal(hass: HomeAssistant | None, entity_id: Any) -> str | None:
    """Why another integration's entity may not be in a Hauflo scene / group (None — it may). Each check fails closed;
    a Hauflo entity is never judged here (_hauflo_entity is)."""
    if not isinstance(entity_id, str) or entity_id.split('.', 1)[0] not in MEMBER_DOMAINS:
        return 'domain_not_allowed'
    f = flags(hass)
    if hass is None or f is None or entity_id not in f.members:
        return 'not_in_plan'
    reg = er.async_get(hass).async_get(entity_id)
    if reg is None:
        return 'unknown_entity'
    if reg.platform in NEVER_PLATFORMS:
        return 'platform_not_allowed'
    if reg.disabled_by is not None:
        return 'disabled'
    if reg.hidden_by == er.RegistryEntryHider.USER:
        return 'hidden_by_user'
    if not reg.config_entry_id:
        return 'no_config_entry'
    st = hass.states.get(entity_id)
    if st is None:
        return 'no_state'
    if is_protected(hass, entity_id):
        return 'protected'
    if helper_operates_known(hass, reg) is False:
        return 'unreadable_helper'
    if entity_id.startswith('cover.'):
        names = (st.attributes.get('friendly_name'), reg.name, reg.original_name, entity_id.split('.', 1)[1].replace('_', ' '))
        if st.attributes.get('device_class') in PROTECTED_COVERS or any((isinstance(n, str) and gate_name().search(n) for n in names)):
            return 'gate'
    return None

def member_ok(hass: HomeAssistant | None, entity_id: Any) -> bool:
    return member_refusal(hass, entity_id) is None

def shown_refusal(hass: HomeAssistant | None, entity_id: Any) -> str | None:
    """Why another integration's entity may not be named on Hauflo's dashboard cards (None — it may). Shown only: the
    card operates it with the viewer's own HA rights, a dashboard action still names Hauflo's own only (service_ok).
    Each check fails closed, as member_refusal."""
    if not isinstance(entity_id, str) or not cv.valid_entity_id(entity_id) or entity_id.split('.', 1)[0] not in shown_domains():
        return 'domain_not_allowed'
    if hass is None:
        return 'no_hass'
    reg = er.async_get(hass).async_get(entity_id)
    if reg is None:
        return 'unknown_entity'
    if reg.platform in NEVER_PLATFORMS:
        return 'platform_not_allowed'
    if reg.disabled_by is not None:
        return 'disabled'
    if not reg.config_entry_id:
        return 'no_config_entry'
    if is_protected(hass, entity_id):
        return 'protected'
    if entity_id.startswith('cover.'):
        st = hass.states.get(entity_id)
        names = (st.attributes.get('friendly_name') if st else None, reg.name, reg.original_name, entity_id.split('.', 1)[1].replace('_', ' '))
        if st is not None and st.attributes.get('device_class') in PROTECTED_COVERS or reg.original_device_class in PROTECTED_COVERS or reg.device_class in PROTECTED_COVERS or any((isinstance(n, str) and gate_name().search(n) for n in names)):
            return 'gate'
    return None

def shown_ok(hass: HomeAssistant | None, entity_id: Any) -> bool:
    return shown_refusal(hass, entity_id) is None
