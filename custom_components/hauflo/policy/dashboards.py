"""Whole dashboards the cloud may save, and what is pruned from them."""
from __future__ import annotations
import copy
import logging
from typing import Any
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from .. import theme
from ..dashmerge import const as dashmerge_const
from ..foreign import refusals as foreign_refusals
from .const import ACTIONS, CARD_SERVICES, COPY_RE, EID_IN, ENTITY_RE, HAUFLO_ID_RE, IMAGE_KEYS, LOVELACE_TYPES, NAV_RE, NO_PRUNE_LISTS, READ_ONLY_DOMAINS, RUNS, TARGET_KEYS, TEXT_KEYS, prune_max, why_max
from .knx import ga_key
from .lovelace import area_lights_owned, image_ok, named_as, owned_scene_sends, rows_why_of, shown_protected, subs_why_of
from .own import hauflo_entity, protected
_LOGGER = logging.getLogger(__name__)

def lovelace_ok(hass: HomeAssistant | None, config: Any, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    return lovelace_refusal(hass, config, owned, owned_entries) is None

def lovelace_refusal(hass: HomeAssistant | None, config: Any, owned: set[str] | None, owned_entries: set[str] | None) -> str | None:
    """Why the agent refuses a Hauflo dashboard (None — it passes): the first item refused, where it is and why
    (`views[1].cards[0].type: custom:x — card type not allowed`) — for the agent's log, the cloud's deploy message and
    its
    journal (acceptance 0.33 K1: a bare «not allowed» left the integrator with nothing to fix)."""
    got = _lovelace_refusal_at(hass, config, owned, owned_entries)
    return got[0] if got is not None else None

def _lovelace_refusal_at(hass: HomeAssistant | None, config: Any, owned: set[str] | None, owned_entries: set[str] | None) -> tuple[str, list[Any]] | None:
    if not isinstance(config, dict):
        return ('config: not an object', [])
    scene_sends: list[dict[str, set[int]]] = []
    path: list[Any] = []
    why: list[tuple[str, list[Any]]] = []

    def no(reason: str) -> bool:
        if not why:
            where = ''.join((f'[{p}]' if isinstance(p, int) else f'.{p}' for p in path)).lstrip('.') or 'config'
            why.append((f'{where}: {reason}'[:why_max()], list(path)))
        return False

    def entity_why(eid: str) -> str:
        if protected(hass, eid):
            return f'{eid} — protected, not shown by Hauflo'
        return f"{eid} — not Hauflo's ({foreign_refusals.shown_refusal(hass, eid) or 'not_shown'})"

    def entity_ok(eid: str) -> bool:
        domain = eid.split('.', 1)[0]
        if domain in READ_ONLY_DOMAINS:
            return True
        return hauflo_entity(hass, eid, owned, owned_entries) or shown_protected(hass, eid, owned, owned_entries) or foreign_refusals.shown_ok(hass, eid) or no(entity_why(eid))

    def target_ids(value: Any) -> list[str] | None:
        """entity_id as HA reads it (cv.comp_entity_ids lower-cases, strips, splits on commas, takes «all»): only exact"""
        items = value if isinstance(value, list) else [value]
        if not items or not all((isinstance(i, str) and ENTITY_RE.fullmatch(i) for i in items)):
            return None
        return items

    def service_ok(v: dict[str, Any], card: str | None=None) -> bool:
        """A service call anywhere in a dashboard: an allowed service, on Hauflo's own entities named one by one; a
        protected one only by the card that shows it (`card`: the nearest «entity» around the call)."""
        names = [v[k] for k in ('perform_action', 'service') if k in v and (not (k == 'service' and isinstance(v[k], dict) and ('perform_action' not in v)))]
        if not names or not all((isinstance(n, str) and n in CARD_SERVICES for n in names)) or len(set(names)) != 1:
            return no(f"service {', '.join(map(str, names)) or '—'} not allowed")
        service = names[0]
        for k in ('target', 'data', 'service_data'):
            if k in v and (not isinstance(v[k], dict)):
                return no(f'{service}: {k} not an object')
        if service == 'knx.send':
            if set(v) & ({'target', 'service_data'} | TARGET_KEYS) or 'data' not in v:
                return no('knx.send: only data.address and data.payload')
            data = v['data']
            if not scene_sends:
                scene_sends.append(owned_scene_sends(hass, owned))
            payload = data.get('payload')
            allowed = scene_sends[0].get(ga_key(data.get('address')) or '?')
            if set(data) - {'address', 'payload'} or not allowed:
                return no(f"knx.send to {data.get('address')} — not a scene Hauflo made")
            return isinstance(payload, list) and len(payload) == 1 and (type(payload[0]) is int) and (payload[0] in allowed) or no(f"knx.send to {data.get('address')} — not a scene's store")
        ids: list[str] = []
        areas: list[Any] = []
        for part, is_target in ((v.get('target') or {}, True), (v.get('data') or {}, False), (v.get('service_data') or {}, False), (v, False)):
            for k, x in part.items():
                if not isinstance(k, str):
                    return no(f'{service}: a key that is not text')
                lk = k.strip().lower()
                if k == 'entity_id':
                    got = target_ids(x)
                    if got is None:
                        return no(f'{service}: entity_id not exact ids')
                    ids += got
                elif k == 'area_id' and is_target and (service == 'light.turn_off'):
                    areas += x if isinstance(x, list) else [x]
                elif lk in TARGET_KEYS or is_target:
                    return no(f'{service}: target {k} not allowed')
        if not ids and (not areas):
            return no(f'{service}: no entity_id named')
        if areas and (not all((area_lights_owned(hass, a, owned, owned_entries) for a in areas))):
            return no(f"{service}: an area with lights not Hauflo's")
        for i in ids:
            if not (hauflo_entity(hass, i, owned, owned_entries) or (i == card and shown_protected(hass, i, owned, owned_entries))):
                return no(f"{service} on {i} — runs Hauflo's own entities only")
        return True

    def walk(v: Any, key: str='', card: str | None=None) -> bool:
        if isinstance(v, dict):
            if isinstance(v.get('entity'), str):
                card = v['entity']
                if 'name' in v and shown_protected(hass, card, owned, owned_entries) and (not named_as(hass, card, v['name'])):
                    return no(f'{card} — a protected one under another name')
            kind = v.get('type')
            if kind is not None and (not isinstance(kind, str) or kind not in LOVELACE_TYPES):
                return no(f'card type {kind} not allowed')
            if set(v) & {'items', 'auto', 'order'} and str(kind).startswith('custom:hauflo-') and (rows_why := rows_why_of(v, hass)):
                return no(f'{kind}: {rows_why}')
            if set(v) & set(dashmerge_const.SUB_KEYS) and str(kind).startswith('custom:hauflo-') and (subs_why := subs_why_of(v)):
                return no(f'{kind}: {subs_why}')
            if 'hauflo_id' in v and (not (isinstance(v['hauflo_id'], str) and HAUFLO_ID_RE.match(v['hauflo_id']))):
                return no('hauflo_id not plain text')
            action = v.get('action')
            if key.endswith('_action') or isinstance(action, str):
                if action not in ACTIONS:
                    return no(f'action {action} not allowed')
                if action == 'navigate' and (not NAV_RE.match(str(v.get('navigation_path', '')))):
                    return no("navigate outside Hauflo's dashboards")
                if action in RUNS:
                    if not service_ok(v, card):
                        return False
                elif set(v) & ({'perform_action', 'service', 'target', 'data', 'service_data'} | TARGET_KEYS):
                    return no(f'{action}: a service or a target on an action that runs none')
            elif 'perform_action' in v or isinstance(v.get('service'), str):
                if not service_ok(v, card):
                    return False
            elif 'target' in v or 'service_data' in v:
                return no('a target with no service')
            if key == 'state_image' and (not all((image_ok(x) for x in v.values()))):
                return no('state_image: a picture from outside the house')
            for k, x in v.items():
                path.append(str(k))
                ok = walk(x, str(k), card)
                path.pop()
                if not ok:
                    return False
            return True
        if isinstance(v, list):
            for i, x in enumerate(v):
                path.append(i)
                ok = walk(x, key, card)
                path.pop()
                if not ok:
                    return False
            return True
        if isinstance(v, str):
            if key in ('perform_action', 'service', 'type', 'action', 'navigation_path', 'hauflo_id'):
                return True
            if key in IMAGE_KEYS:
                return image_ok(v) or no('a picture from outside the house')
            if v.startswith('/') and key == 'path':
                return bool(NAV_RE.match(v)) or no("a path outside Hauflo's dashboards")
            if key not in TEXT_KEYS:
                norm = v.strip().lower()
                if norm == 'all':
                    return no('«all» — every entity')
                pieces = [p.strip().lower() for p in v.split(',')]
                if any((ENTITY_RE.fullmatch(p) for p in pieces)) and (len(pieces) > 1 or norm != v):
                    return no('entity ids not in their exact form')
                if ENTITY_RE.fullmatch(v):
                    return entity_ok(v)
                if v.lower().startswith(('http://', 'https://', '//', 'javascript:', 'data:')):
                    return no('an address outside the house')
            return key not in ('url', 'url_path') or v.startswith('hauflo-') or no("url_path not Hauflo's")
        return True
    if walk(config):
        return None
    return why[0] if why else ('config: refused', [])

def _prune_at(path: list[Any]) -> int | None:
    for i in range(len(path) - 1, 1, -1):
        if isinstance(path[i], int) and isinstance(path[i - 1], str) and (path[i - 1] not in NO_PRUNE_LISTS):
            return i
    return None

def _first_entity(v: Any) -> str | None:
    if isinstance(v, str):
        return v if ENTITY_RE.fullmatch(v) else None
    if isinstance(v, dict):
        if isinstance(v.get('entity'), str) and ENTITY_RE.fullmatch(v['entity']):
            return v['entity']
        v = list(v.values())
    if isinstance(v, list):
        for x in v:
            if (e := _first_entity(x)) is not None:
                return e
    return None

def lovelace_prune(hass: HomeAssistant | None, config: Any, owned: set[str] | None, owned_entries: set[str] | None) -> tuple[Any, list[dict[str, Any]]]:
    """One refused card / row / scene never refuses the whole dashboard (accept3b №6: a scene judged protected — a
    gate's cover inside it, the mark sticky — and the family's dashboard wasn't saved at all): what the check refuses is
    taken out, item by item, the smallest one around it, and said — the entity, a short reason (`protected`,
    `not_hauflo`, `refused`) and the check's own words for the log. Only taking out: the rest is checked again in full,
    a refusal with nothing smaller than the dashboard to take out stays the dashboard's (the caller checks again)."""
    if not isinstance(config, dict):
        return (config, [])
    cfg = copy.deepcopy(config)
    dropped: list[dict[str, Any]] = []
    for _ in range(prune_max()):
        got = _lovelace_refusal_at(hass, cfg, owned, owned_entries)
        at = _prune_at(got[1]) if got is not None else None
        if got is None or at is None:
            break
        holder: Any = cfg
        for k in got[1][:at]:
            holder = holder[k]
        item = holder.pop(got[1][at])
        why = got[0]
        named = EID_IN.findall(why.split(': ', 1)[-1])
        eid = next((e for e in named if ENTITY_RE.fullmatch(e) and e not in CARD_SERVICES), None) or _first_entity(item)
        kind = 'protected' if 'protected' in why else 'not_hauflo' if "not Hauflo's" in why else 'refused'
        dropped.append({**({'entity': eid} if eid else {}), 'why': kind, 'detail': why[:why_max()]})
    return (cfg, dropped)

def own_dashboard(hass: HomeAssistant | None, dashboard_id: Any) -> bool:
    """A dashboard Hauflo made (hauflo-…), looked up by its id."""
    if hass is None or not isinstance(dashboard_id, str):
        return False
    try:
        for url_path, dash in hass.data[LOVELACE_DATA].dashboards.items():
            if (getattr(dash, 'config', None) or {}).get('id') == dashboard_id:
                return str(url_path).startswith('hauflo-') and (not COPY_RE.search(str(url_path)))
    except (KeyError, AttributeError):
        return False
    return False

def alice_labels(hass: HomeAssistant | None) -> set[str]:
    """The labels Alice exposes by (Yandex Smart Home «by label»)."""
    if hass is None:
        return set()
    return {str(e.options.get('label')) for e in hass.config_entries.async_entries('yandex_smart_home') if e.options.get('label')}

def theme_ok(msg: dict[str, Any]) -> bool:
    data = msg.get('service_data')
    if not isinstance(data, dict) or not data or (not set(data) <= {'name', 'name_dark'}) or set(msg) - {'id', 'type', 'domain', 'service', 'service_data'}:
        return False
    return all((isinstance(v, str) and v in theme.names() for v in data.values()))
