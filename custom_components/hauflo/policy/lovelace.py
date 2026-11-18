"""Dashboard cards the cloud may write: entities, images and rows."""
from __future__ import annotations
import logging
import math
import re
from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from .. import voice
from ..dashmerge import const as dashmerge_const
from ..guard import contained
from .const import DATA_IMAGE_RE, DATA_SVG_RE, ENCODED_PATH, ENTITY_RE, IMAGE_RE, NEVER_EXPOSE, ORDER_RE, PROTECT_DATA, REG_FEATURE, ROW_BLOCKS, ROW_KEYS, SUB_SHOW, SVG_LOCAL_URL, SVG_TAGS, WORD_RE, reg_range_attrs, reg_ranges, row_num_abs, row_text_max, row_views, rows_max, sub_modes_max
from .knx import ga_key, knx_store, knx_writes, owned_knx
from .own import class_protected, hauflo_entity, own_entity, platform, protected, sticky
_LOGGER = logging.getLogger(__name__)

def _svg_ok(b64: str) -> bool:
    import base64
    import xml.etree.ElementTree as ET
    try:
        text = base64.b64decode(b64, validate=True).decode('utf-8')
        if '<!' in text or '<?' in text:
            return False
        root = ET.fromstring(text)
    except (ValueError, ET.ParseError):
        return False
    for el in root.iter():
        if not isinstance(el.tag, str) or el.tag.rsplit('}', 1)[-1] not in SVG_TAGS:
            return False
        for key, val in el.attrib.items():
            name = key.rsplit('}', 1)[-1].lower()
            if name.startswith('on') or 'href' in name or name == 'style' or ('url(' in val and (not SVG_LOCAL_URL.fullmatch(val.strip()))):
                return False
    return True

def image_ok(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if DATA_IMAGE_RE.fullmatch(value):
        return True
    if (svg := DATA_SVG_RE.fullmatch(value)) is not None:
        return _svg_ok(svg.group(1))
    return bool(IMAGE_RE.fullmatch(value)) and '..' not in value and ('//' not in value) and (not ENCODED_PATH.search(value))

def area_lights_owned(hass: HomeAssistant | None, area_id: Any, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    """«Всё выключить» by a room: every light HA would turn off there (its own area, or its device's when it has
    none — as HA's service helper resolves it) is Hauflo's."""
    if hass is None or not isinstance(area_id, str) or (not re.fullmatch('[a-z0-9_]+', area_id)) or (area_id in ('all', 'none')):
        return False
    with contained(_LOGGER, 'Hauflo: lights of area %s not checkable — not allowed', area_id):
        ents = er.async_get(hass)
        lights = [e for e in er.async_entries_for_area(ents, area_id) if e.domain == 'light']
        for device in dr.async_entries_for_area(dr.async_get(hass), area_id):
            lights += [e for e in er.async_entries_for_device(ents, device.id) if e.domain == 'light' and (not e.area_id)]
        return all((hauflo_entity(hass, e.entity_id, owned, owned_entries) for e in lights))
    return False

def owned_scene_sends(hass: HomeAssistant | None, owned: set[str] | None) -> dict[str, set[int]]:
    """Address → the «store» bytes a dashboard may send there: each KNX scene Hauflo made, by its scene number (0x80 |
    number−1; any of the 64 when it has none) — minus every address that opens something protected (protect.py)."""
    store = knx_store(hass)
    if store is None or owned is None:
        return {}
    out: dict[str, set[int]] = {}
    for uid, config in (store.get('scene') or {}).items():
        knx = config.get('knx') if isinstance(config, dict) else None
        if uid not in owned or not isinstance(knx, dict):
            continue
        key = ga_key((knx.get('ga_scene') or {}).get('write') if isinstance(knx.get('ga_scene'), dict) else None)
        number = knx.get('scene_number')
        if key is None:
            continue
        out.setdefault(key, set()).update({128 | number - 1} if type(number) is int and 1 <= number <= 64 else set(range(128, 192)))
    marks = getattr(hass, 'data', {}).get(PROTECT_DATA)
    for key in set(getattr(marks, 'gas', None) or ()) | _protected_writes(hass):
        out.pop(key, None)
    return out

def _protected_writes(hass: HomeAssistant | None) -> set[str]:
    out: set[str] = set()
    store = knx_store(hass)
    if store is None:
        return out
    with contained(_LOGGER, 'Hauflo: protected KNX writes not read — the others still hold'):
        reg = er.async_get(hass)
        for domain, configs in store.items():
            for uid, config in (configs or {}).items():
                eid = reg.async_get_entity_id(domain, 'knx', uid)
                if eid and protected(hass, eid):
                    out |= knx_writes(config.get('knx') if isinstance(config, dict) else None)
    out.discard('?')
    return out

def shown_protected(hass: HomeAssistant | None, eid: Any, owned: set[str] | None, owned_entries: set[str] | None) -> bool:
    """A gate / garage door / electric lock Hauflo itself made (protected by its class or its mark, not by what it
    holds): the family's dashboard shows it on its own card."""
    if hass is None or not isinstance(eid, str) or eid.startswith(NEVER_EXPOSE):
        return False
    if not (sticky(hass, eid) or class_protected(hass, eid)):
        return False
    return own_entity(hass, eid, owned_entries) and (platform(hass, eid) != 'knx' or owned_knx(hass, eid, owned))

def _words(text: Any) -> set[str]:
    return set(WORD_RE.findall(voice.norm(str(text or ''))))

def named_as(hass: HomeAssistant, eid: str, name: Any) -> bool:
    """A card's name for a protected entity is its own: at least one word of HA's names for it (the deploy's short name
    drops the room — «Ворота» of «Двор Ворота»), the rest its words, room names (a shared device's «(для Двора и
    Гаража)») or short joining words — never «Свет кухня» on the gate (red-team 2, A1)."""
    if not isinstance(name, str):
        return False
    st = hass.states.get(eid) if hasattr(hass, 'states') else None
    reg = er.async_get(hass).async_get(eid)
    known = _words(st.attributes.get('friendly_name') if st is not None else None) | _words(getattr(reg, 'name', None)) | _words(getattr(reg, 'original_name', None))
    rooms: set[str] = set()
    with contained(_LOGGER, 'Hauflo: area names not read — rooms taken as none'):
        for area in ar.async_get(hass).async_list_areas():
            rooms |= _words(area.name)
    words = _words(name)
    rest = words - known
    return bool(words & known) and all((w in rooms or len(w) <= 3 or any((w.startswith(r[:-1]) for r in rooms if len(r) > 3)) for w in rest))

def subs_why_of(card: dict[str, Any]) -> str | None:
    """    names (the cards' cfgProblems); None — fine."""
    for k in dashmerge_const.SUB_KEYS:
        if k not in card:
            continue
        v = card[k]
        if k.startswith('show_') and v not in SUB_SHOW:
            return f'{k} not auto / show / hide'
        if k.endswith('_modes') and (not (isinstance(v, list) and len(v) <= sub_modes_max() and all((isinstance(m, (str, int)) and (not isinstance(m, bool)) and (len(str(m)) <= row_text_max()) for m in v)))):
            return f'{k} not a list of modes'
    return None

def rows_why_of(card: dict[str, Any], hass: HomeAssistant | None=None) -> str | None:
    """Why `items`, `auto` and `order` of a Hauflo card are not in their form (their entity ids are checked by the walk,
    like any); None — fine. A row's regulator out of its sense says its code (Р78, accept 4A): `<entity> regulator
    <code>`."""
    bad = 'rows not in their form'
    if 'auto' in card and (not isinstance(card['auto'], bool)):
        return bad
    order = card.get('order')
    if 'order' in card and (not (isinstance(order, list) and len(order) <= rows_max() and all((isinstance(x, str) and ORDER_RE.fullmatch(x) for x in order)))):
        return bad
    items = card.get('items')
    if 'items' not in card:
        return None
    if not isinstance(items, list) or len(items) > rows_max():
        return bad
    for row in items:
        if not isinstance(row, dict) or set(row) - ROW_KEYS or (not (isinstance(row.get('entity'), str) and ENTITY_RE.fullmatch(row['entity']))):
            return bad
        if any((k in row and (not (isinstance(row[k], str) and len(row[k]) <= row_text_max())) for k in ('name', 'secondary', 'icon', 'icon_type'))):
            return bad
        if 'hidden' in row and (not isinstance(row['hidden'], bool)):
            return bad
        if 'block' in row and row['block'] not in ROW_BLOCKS:
            return bad
        if 'view' in row and row['view'] not in row_views():
            return bad
        if any((k in row and (not _row_num(row[k])) for k in ('min', 'max', 'step'))):
            return bad
        code = _row_reg_why(row, _reg_range(hass, row['entity']))
        if code:
            return f"{row['entity']} regulator {code}"
    return None

def _reg_range(hass: HomeAssistant | None, entity_id: str) -> tuple[float, float] | None:
    state = hass.states.get(entity_id) if hass is not None else None
    if state is None:
        return None
    dom = entity_id.split('.', 1)[0]
    a = state.attributes or {}

    def n(x: Any) -> float | None:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None
    if dom in reg_range_attrs():
        lo, hi = (n(a.get('min')), n(a.get('max')))
        return (lo, hi) if lo is not None and hi is not None and (hi > lo) else None
    if dom not in reg_ranges():
        return None
    if dom == 'light':
        on = any((m != 'onoff' for m in a.get('supported_color_modes') or []))
    else:
        on = bool(int(n(a.get('supported_features')) or 0) & REG_FEATURE.get(dom, 0))
    return (float(reg_ranges()[dom][0]), float(reg_ranges()[dom][1])) if on else None

def _row_reg_why(row: dict[str, Any], rng: tuple[float, float] | None) -> str | None:
    mn, mx, st = (row.get('min'), row.get('max'), row.get('step'))
    lo = mn if mn is not None else rng[0] if rng else None
    hi = mx if mx is not None else rng[1] if rng else None
    if rng and any((x is not None and (x < rng[0] or x > rng[1]) for x in (mn, mx))):
        return 'reg_out_of_range'
    if lo is not None and hi is not None and (lo >= hi):
        return 'reg_min_max'
    if st is not None and st <= 0:
        return 'reg_step'
    if st is not None and lo is not None and (hi is not None) and (st > hi - lo + 1e-09):
        return 'reg_step_range'
    return None

def _row_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and (not isinstance(v, bool)) and math.isfinite(v) and (abs(v) <= row_num_abs())
