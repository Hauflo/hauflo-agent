"""HTTP views of the agent's own records."""
from __future__ import annotations
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.helpers import area_registry as ar
from .. import issues, ranges
from ..const import DOMAIN
from ..issues import degrading
from ..policy.areas import load_stock_area_names, stock_area

class OwnedView(HomeAssistantView):
    """The KNX entities Hauflo made here (their unique ids): a plan shows the ones no project knows any more —"""
    url = '/api/hauflo/owned'
    name = 'api:hauflo:owned'
    requires_auth = True

    async def get(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        hass = request.app[KEY_HASS]
        owned: set[str] = set()
        entries: set[str] = set()
        areas: set[str] = set()
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            owned |= entry.runtime_data.owned
            entries |= {e for e in entry.runtime_data.owned_entries if ':' not in e}
            areas |= {e.split(':', 1)[1] for e in entry.runtime_data.owned_entries if e.startswith('area:')}
        stock: list[str] = []
        with degrading(hass, 'stock rooms', issues.HA):
            await load_stock_area_names(hass)
            stock = sorted((a.id for a in ar.async_get(hass).async_list_areas() if a.id not in areas and stock_area(hass, a.id)))
            issues.recover(hass, 'stock rooms')
        return self.json({'unique_ids': sorted(owned), 'entries': sorted(entries), 'areas': sorted(areas), 'stock_areas': stock})

class RangesView(HomeAssistantView):
    """The group address ranges Hauflo may send to (allowed in HA) and the ones it asked for — the plan says which a
    deploy still needs allowed (acceptance B9). Read only; changed only in HA's own options flow."""
    url = '/api/hauflo/ranges'
    name = 'api:hauflo:ranges'
    requires_auth = True

    async def get(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        hass = request.app[KEY_HASS]
        allowed: set[str] = set()
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            allowed |= entry.runtime_data.allowed_ranges()
        return self.json({'allowed': sorted(allowed), **ranges.state(hass)})

    async def post(self, request):
        """The plan asks for what the project writes ({"addresses": [...], "project": "…"}): what isn't allowed waits"""
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        hass = request.app[KEY_HASS]
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('bad body', 400)
        wanted = body.get('addresses') if isinstance(body, dict) else None
        if not isinstance(wanted, list) or len(wanted) > ranges.MAX_ITEMS or (not all((isinstance(x, str) for x in wanted))):
            return self.json_message('bad addresses', 400)
        loaded = hass.config_entries.async_loaded_entries(DOMAIN)
        if not loaded:
            return self.json_message('not linked', 409)
        allowed = loaded[0].runtime_data.allowed_ranges()
        project = str(body.get('project') or '') or (loaded[0].title or '').removeprefix('Hauflo — ')
        return self.json({'allowed': sorted(allowed), **ranges.request(hass, allowed, wanted, project)})
