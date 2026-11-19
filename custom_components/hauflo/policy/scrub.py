"""What the cloud may read: states, entities, devices and diagnostics with private data removed."""
from __future__ import annotations
from collections.abc import Callable
import json
import logging
import re
from typing import Any
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from .. import derived as dv, sidebar
from ..const import DOMAIN
from ..foreign import common as foreign_common
from ..guard import contained
from .const import ALICE_NOTE, CONFIG_KEEP, CONTACT, KEEP_ON_PRIVATE, PHONE_PLATFORMS, PRESENCE_CLASSES, PRIVATE_PLATFORMS, PRIVATE_STATE, STATE_ATTRS, STATE_TIMES, WEATHER_READS
from .http import entry_domain
_LOGGER = logging.getLogger(__name__)

def scrub_config(cfg: Any) -> Any:
    if not isinstance(cfg, dict):
        return {}
    out = {k: v for k, v in cfg.items() if k in CONFIG_KEEP}
    url = out.get('external_url')
    if url is not None:
        m = re.match('^(https?://)(?:[^@/?#]*@)?([^/?#@\\s]+)(/[^?#@\\s]*)?', url) if isinstance(url, str) else None
        if m:
            out['external_url'] = m.group(1) + m.group(2) + (m.group(3) or '')
        else:
            out.pop('external_url')
    return out

def _phone_ids(hass: HomeAssistant | None) -> set[str]:
    if hass is None:
        return set()
    with contained(_LOGGER, 'Hauflo: phone entities not listed — states go unscrubbed of them'):
        return {e.entity_id for e in er.async_get(hass).entities.values() if e.platform in PHONE_PLATFORMS}
    return set()

def scrub_entities(result: Any, hass: HomeAssistant | None) -> Any:
    phones = _phone_ids(hass)

    def phone(e: Any) -> bool:
        return isinstance(e, dict) and (e.get('platform') in PHONE_PLATFORMS or e.get('entity_id') in phones)
    if isinstance(result, list):
        return [e for e in result if not phone(e)]
    if isinstance(result, dict) and 'entity_id' in result:
        return None if phone(result) else result
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if k not in phones and (not phone(v))}
    return result

def scrub_panels(panels: Any) -> Any:
    return {str(k): {'url_path': str(k)} for k in panels if k in sidebar.MENU_EXTRAS} if isinstance(panels, dict) else {}

def scrub_states(states: Any, hass: HomeAssistant | None=None, visible: Callable[[str], bool] | None=None, hidden: set[str] | None=None) -> Any:
    if not isinstance(states, list):
        return []
    phones = set()
    if hass is not None:
        with contained(_LOGGER, 'Hauflo: private platforms not listed'):
            phones = {e.entity_id for e in er.async_get(hass).entities.values() if e.platform in PRIVATE_PLATFORMS}
    gone = _phone_ids(hass)
    states[:] = [st for st in states if not (isinstance(st, dict) and st.get('entity_id') in gone)]
    for st in states:
        if not isinstance(st, dict):
            continue
        eid = str(st.get('entity_id', ''))
        attrs = st.get('attributes') if isinstance(st.get('attributes'), dict) else {}
        presence = eid.startswith('binary_sensor.') and attrs.get('device_class') in PRESENCE_CLASSES
        if presence and visible is not None:
            presence = True
            with contained(_LOGGER, 'Hauflo: visibility of %s not checkable — hidden', eid):
                presence = not visible(eid)
        if eid.split('.')[0] in PRIVATE_STATE or eid in phones or presence or (eid in (hidden or ())):
            st['attributes'] = {k: v for k, v in attrs.items() if k in KEEP_ON_PRIVATE}
            st['state'] = 'hidden'
            for k in STATE_TIMES:
                st.pop(k, None)
        else:
            st['attributes'] = {k: v for k, v in attrs.items() if k in STATE_ATTRS}
    return states

def cloud_reads(hass: HomeAssistant | None, eid: str, attr: str | None, visible: Callable[[str], bool]) -> bool:
    """What the tunnel may read through a rule (input, condition, window history, derived source): the same as get_states shows it — no private domain / phone / sun, a presence sensor only when visible, only attributes get_states keeps."""
    domain = eid.split('.')[0]
    if domain == 'weather':
        return attr in WEATHER_READS
    if domain in PRIVATE_STATE:
        return False
    if attr is not None and attr not in STATE_ATTRS and (attr != dv.SETPOINT_GAP):
        return False
    try:
        reg = er.async_get(hass).async_get(eid) if hass is not None else None
        if reg is not None and reg.platform in PRIVATE_PLATFORMS | PHONE_PLATFORMS:
            return False
        if domain == 'binary_sensor':
            st = hass.states.get(eid) if hass is not None else None
            classes = {st.attributes.get('device_class') if st else None} | ({reg.device_class, reg.original_device_class} if reg else set())
            if classes & PRESENCE_CLASSES and (not visible(eid)):
                return False
    except Exception:
        _LOGGER.warning('Hauflo: not knowable: hidden', exc_info=True)
        return False
    return True

def history_reads(hass: HomeAssistant, eid: str, attr: str | None) -> bool:
    loaded = hass.config_entries.async_loaded_entries(DOMAIN)
    return bool(loaded) and cloud_reads(hass, eid, attr, loaded[0].runtime_data.state_visible)

def scrub_cloud(status: Any) -> Any:
    """Nabu Casa's status without the account, the house's remote address, Google's PIN for locks or the cloudhooks (security re-review round 5) — Hauflo needs whether it's on and which assistants are."""
    if not isinstance(status, dict):
        return {}
    prefs = status.get('prefs') if isinstance(status.get('prefs'), dict) else {}
    return {'logged_in': status.get('logged_in'), 'cloud': status.get('cloud'), 'prefs': {k: prefs.get(k) for k in ('alexa_enabled', 'google_enabled') if k in prefs}}

def scrub_diagnostics(text: str) -> str:
    """A config entry's diagnostics as Hauflo reads them: a bridge's filter, Alice's label settings — nothing else (states, devices, coordinates, people stay in the house)."""
    try:
        d = json.loads(text)
    except ValueError:
        return '{}'
    data = d.get('data') if isinstance(d, dict) and isinstance(d.get('data'), dict) else {}
    out: dict[str, Any] = {}
    ce = data.get('config-entry') if isinstance(data.get('config-entry'), dict) else None
    if ce is not None:
        flt = (ce.get('options') or {}).get('filter') if isinstance(ce.get('options'), dict) else None
        out['config-entry'] = {'options': {'filter': flt if isinstance(flt, dict) else {}}}
    entry = data.get('entry') if isinstance(data.get('entry'), dict) else None
    if entry is not None:
        opts = entry.get('options') if isinstance(entry.get('options'), dict) else {}
        out['entry'] = {'options': {k: opts.get(k) for k in ('filter_source', 'label', 'entry_aliases') if k in opts}}
    return json.dumps({'data': out})

def scrub_devices(devices: Any, hass: HomeAssistant | None) -> Any:
    """The device list without the phones' names («iPhone Ivan»): Hauflo places devices, it needn't know whose."""
    if not isinstance(devices, list):
        return []
    for d in devices:
        if isinstance(d, dict) and any((entry_domain(hass, e) == 'mobile_app' for e in foreign_common.device_dict_entry_ids(d))):
            for k in ('name', 'name_by_user', 'model', 'manufacturer'):
                if k in d:
                    d[k] = None
    return devices

def _redact_title(title: Any) -> str | None:
    if not isinstance(title, str):
        return None
    r = CONTACT.sub('…', title).strip()[:200]
    return r if any((c.isalnum() for c in r)) else None

def scrub_entries(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        return []
    out = []
    for e in entries:
        if not isinstance(e, dict) or e.get('domain') in PHONE_PLATFORMS or (not isinstance(e.get('entry_id'), str)):
            continue
        out.append({'entry_id': e['entry_id'], 'domain': e.get('domain'), 'title': _redact_title(e.get('title'))})
    return out

def scrub_monitor(info: Any) -> Any:
    """The bus monitor: which addresses answered (the devices check), never the values (who's home, what's on)."""
    if not isinstance(info, dict):
        return {}
    tg = info.get('recent_telegrams') if isinstance(info.get('recent_telegrams'), list) else []
    return {'project_loaded': info.get('project_loaded'), 'recent_telegrams': [{k: x.get(k) for k in ('destination', 'direction', 'telegramtype', 'timestamp')} for x in tg if isinstance(x, dict)]}

def scrub_notes(event: Any, hass: HomeAssistant, owned_entries: set[str] | None=None) -> Any:
    if not isinstance(event, dict) or not isinstance(event.get('notifications'), dict):
        return event
    keep = {k: v for k, v in event['notifications'].items() if entry_domain(hass, str(k)) == 'homekit' and owned_entries is not None and (str(k) in owned_entries)}
    return {**event, 'notifications': keep}

def alice_creds_to_ha(hass: HomeAssistant, text: str) -> str:
    """Alice's link login stays in the house (panel round 6: with it, anyone's Yandex account takes the house): shown as an HA notification, Hauflo only learns that it's there."""
    try:
        res = json.loads(text)
    except ValueError:
        return text
    if not isinstance(res, dict) or res.get('handler') != 'yandex_smart_home' or res.get('type') != 'create_entry':
        return text
    p = res.get('description_placeholders') or {}
    if not isinstance(p, dict) or not p.get('id'):
        return text
    lines = [f"ID: `{p.get('id')}`", f"Пароль / password: `{p.get('password', '')}`"]
    if p.get('otp') and p.get('otp') != '-':
        lines.append(f"Одноразовый код / one-time code: `{p['otp']}`")
    persistent_notification.async_create(hass, 'Для приложения «Дом с Алисой» / for the Yandex app:\n\n' + '\n\n'.join(lines), 'Hauflo: Алиса', ALICE_NOTE)
    res['description_placeholders'] = {'hauflo_note': ALICE_NOTE}
    return json.dumps(res)
