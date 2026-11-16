from __future__ import annotations
import re
import time
from typing import Any
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from .house import refuse
from .ownership import room_area
from .storage import HaufloStore
STORE_KEY = 'hauflo.order'
MAX_KEY = 300
MAX_ITEMS = 300
MAX_BLOCKS = 500
MAX_ID = 255
PREFIX = 'custom:hauflo-'
_DASH = re.compile('^hauflo-[a-z0-9_-]{1,100}$')
_COPY = re.compile('-copy-\\d')
_ENTITY = re.compile('^[a-z_]+\\.[a-z0-9_]+$')
_CONTROL = re.compile('[\x00-\x1f\x7f-\x9f\u2028\u2029]')
AREA_CLASSES = ('lights', 'covers', 'climate', 'sockets', 'media', 'devices', 'security', 'camera', 'settings', 'buttons', 'other')
CLS = 'cls:'
HOUSE_VIEW = '*'
HOUSE_SLOT = 'cls'

def slot(card: dict[str, Any]) -> str | None:
    kind = card.get('type')
    if not isinstance(kind, str) or not kind.startswith(PREFIX):
        return None
    t = kind[len(PREFIX):]
    if t == 'room' and card.get('path'):
        return 'room:tiles'
    zones = card.get('zones')
    title = card.get('title') if isinstance(card.get('title'), str) else ''
    ents = card.get('entities')
    first = ents[0] if not title and isinstance(ents, list) and ents else None
    first = first.get('entity') if isinstance(first, dict) else first
    domain = first.split('.')[0] if isinstance(first, str) else ''
    k = next((x for x in (card.get('block'), card.get('kind'), 'zones' if isinstance(zones, list) and zones else '', card.get('area'), domain) if isinstance(x, str) and x), '')
    return f'{t}:{k}:{title}'

def parse_key(key: Any) -> tuple[str, str, str] | None:
    if not isinstance(key, str) or not 0 < len(key) <= MAX_KEY or _CONTROL.search(key):
        return None
    parts = key.split('|', 2)
    if len(parts) != 3 or not _DASH.match(parts[0]) or _COPY.search(parts[0]) or (not parts[1]) or (not parts[2]):
        return None
    return (parts[0], parts[1], parts[2])

def _walk(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, list):
        for x in node:
            _walk(x, out)
    elif isinstance(node, dict):
        if isinstance(node.get('type'), str):
            out.append(node)
        for x in node.values():
            if isinstance(x, (list, dict)):
                _walk(x, out)

def _ids(node: Any, out: set[str]) -> None:
    if isinstance(node, str):
        if len(node) <= MAX_ID and _ENTITY.match(node):
            out.add(node)
    elif isinstance(node, list):
        for x in node:
            _ids(x, out)
    elif isinstance(node, dict):
        for x in node.values():
            _ids(x, out)

def _area_members(hass: HomeAssistant, area: str) -> set[str]:
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    devs = dr.async_get(hass)
    out: set[str] = set()
    for e in er.async_get(hass).entities.values():
        own = e.area_id or ((d := devs.async_get(e.device_id)) is not None and d.area_id if e.device_id else None) or None
        if own and area in (own, room_area(hass, own)):
            out.add(e.entity_id)
    return out

def items_of(hass: HomeAssistant, view: dict[str, Any], want: str) -> set[str] | None:
    cards: list[dict[str, Any]] = []
    _walk(view.get('sections') or [], cards)
    _walk(view.get('cards') or [], cards)
    found = False
    out: set[str] = set()
    for c in cards:
        if slot(c) != want:
            continue
        found = True
        _ids(c, out)
        if want == 'room:tiles' and isinstance(c.get('path'), str) and (len(c['path']) <= MAX_ID):
            out.add(c['path'])
        if isinstance(c.get('area'), str):
            out |= _area_members(hass, c['area'])
        if c.get('type') == f'{PREFIX}area':
            out |= {f'{CLS}{k}' for k in AREA_CLASSES}
        rows = c.get('rows') if isinstance(c.get('rows'), list) else []
        for room in [c.get('scene_room'), *(r.get('scene_room') for r in rows if isinstance(r, dict))]:
            if isinstance(room, str) and room:
                prefix = f"hauflo_fam_{re.sub('[^a-z0-9]', '', room)}_"
                out |= {s.entity_id for s in hass.states.async_all('scene') if str(s.attributes.get('id', '')).startswith(prefix)}
    return out if found else None

async def _view(hass: HomeAssistant, dash: str, path: str) -> dict[str, Any] | None:
    from homeassistant.components.lovelace.const import LOVELACE_DATA, ConfigNotFound
    data = hass.data.get(LOVELACE_DATA)
    board = getattr(data, 'dashboards', {}).get(dash) if data is not None else None
    if board is None or getattr(board, 'mode', 'storage') != 'storage':
        return None
    try:
        cfg = await board.async_load(False)
    except ConfigNotFound:
        return None
    views = cfg.get('views') if isinstance(cfg, dict) else None
    if not isinstance(views, list):
        return None
    for i, v in enumerate(views):
        if isinstance(v, dict) and (str(v.get('path') or '') == path or (not v.get('path') and path == str(i))):
            return v
    return None

async def clean_items(hass: HomeAssistant, key: Any, items: Any) -> list[str] | None:
    parsed = parse_key(key)
    if parsed is None or not isinstance(items, list):
        return None
    if parsed[1] == HOUSE_VIEW:
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        data = hass.data.get(LOVELACE_DATA)
        if parsed[2] != HOUSE_SLOT or parsed[0] not in getattr(data, 'dashboards', {}):
            return None
        allowed = {f'{CLS}{k}' for k in AREA_CLASSES}
    else:
        view = await _view(hass, parsed[0], parsed[1])
        allowed = items_of(hass, view, parsed[2]) if view is not None else None
    if allowed is None:
        return None
    out: list[str] = []
    for x in items:
        if isinstance(x, str) and x in allowed and (x not in out):
            out.append(x)
            if len(out) >= MAX_ITEMS:
                break
    return out

def view_order(cfg: Any, order: dict[str, list[str]], dash: str, base: Any=None) -> list[dict[str, Any]] | None:
    views = cfg.get('views') if isinstance(cfg, dict) else None
    if not isinstance(views, list) or not isinstance(order, dict):
        return None
    paths = [str(v.get('path')) if isinstance(v, dict) and isinstance(v.get('path'), str) and v.get('path') else None for v in views]
    at = {p: i for i, p in enumerate(paths) if p is not None}

    def view_path(item: str) -> str | None:
        parts = [x for x in item.strip().split('/') if x]
        if not parts or (len(parts) > 1 and parts[-2] != dash):
            return None
        return parts[-1] if parts[-1] in at else None
    blocks: list[tuple[int, list[str]]] = []
    for key, items in order.items():
        parsed = parse_key(key)
        if parsed is None or parsed[0] != dash or parsed[2] != 'room:tiles' or (not isinstance(items, list)):
            continue
        want = [p for p in (view_path(x) for x in items if isinstance(x, str)) if p is not None]
        want = list(dict.fromkeys(want))
        if len(want) > 1:
            blocks.append((at.get(parsed[1], len(views)), want))
    if not blocks and isinstance(base, dict) and isinstance(base.get('views'), list):
        want = [str(v['path']) for v in base['views'] if isinstance(v, dict) and isinstance(v.get('path'), str) and (v.get('path') in at)]
        if len(want) > 1:
            blocks.append((0, list(dict.fromkeys(want))))
    out = list(views)
    for _, want in sorted(blocks, key=lambda b: b[0]):
        slots = sorted((at[p] for p in want))
        for i, p in zip(slots, want, strict=True):
            out[i] = views[at[p]]
    return out if out != views else None

def _store(hass: HomeAssistant) -> HaufloStore:
    return HaufloStore(hass, STORE_KEY)

async def async_load(hass: HomeAssistant) -> dict[str, list[str]]:
    data = await _store(hass).async_load() or {}
    raw = data.get('order')
    if not isinstance(raw, dict):
        return {}
    return {k: [x for x in v if isinstance(x, str)][:MAX_ITEMS] for k, v in raw.items() if parse_key(k) and isinstance(v, list)}

class OrderView(HomeAssistantView):
    url = '/api/hauflo/order'
    name = 'api:hauflo:order'
    requires_auth = True

    async def get(self, request):
        return self.json({'order': await async_load(request.app[KEY_HASS]), 'max': MAX_ITEMS})

    async def post(self, request):
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict):
            return self.json_message('an object expected', 400)
        if (no := (await refuse(request))) is not None:
            return no
        hass = request.app[KEY_HASS]
        order = await async_load(hass)
        if body.get('clear') is True:
            order = {}
        else:
            key = body.get('key')
            items = await clean_items(hass, key, body.get('items'))
            if items is None:
                return self.json_message('key: a block of a Hauflo dashboard and items: a list of its items expected', 400)
            if items:
                if key not in order and len(order) >= MAX_BLOCKS:
                    return self.json_message('too many blocks', 400)
                order[key] = items
            else:
                order.pop(key, None)
        user = request['hass_user']
        await _store(hass).async_save({'order': order, 'at': time.time(), 'by': user.name if user else None})
        parsed = parse_key(body.get('key')) if body.get('clear') is not True else None
        if body.get('clear') is True or (parsed is not None and parsed[2] == 'room:tiles'):
            for dash in [parsed[0]] if parsed is not None else _managed(hass):
                await reorder_views(hass, dash, order)
        return self.json({'order': order})

def _merger(hass: HomeAssistant) -> Any:
    from .const import DOMAIN
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if (boards := getattr(entry.runtime_data, 'dashboards', None)) is not None:
            return boards
    return None

def _managed(hass: HomeAssistant) -> list[str]:
    boards = _merger(hass)
    return sorted(getattr(boards, 'bases', {}) or {}) if boards is not None else []

async def reorder_views(hass: HomeAssistant, dash: str, order: dict[str, list[str]]) -> bool:
    boards = _merger(hass)
    return await boards.reorder_views(dash, order) if boards is not None else False
