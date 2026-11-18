"""HTTP views of the installer and the restart it asks for."""
from __future__ import annotations
from typing import Any
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from .common import FEATURE, RUNNING, Refused, by_domain, item
from .installer import installer

async def read_body(request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}

def _check(body: dict[str, Any]) -> tuple[str | None, Any]:
    domain = body.get('domain')
    it = item(domain)
    if it is None:
        return (None, ('not_in_canon', 404))
    if 'repo' in body and body['repo'] != it['repo']:
        return (None, ('not_in_canon', 403))
    return (domain, None)

class InstallView(HomeAssistantView):
    url = '/api/hauflo/install'
    name = 'api:hauflo:install'
    requires_auth = True

    async def get(self, request):
        hass: HomeAssistant = request.app[KEY_HASS]
        return self.json({'feature': FEATURE, 'canon': sorted(by_domain()), 'installs': installer(hass).items()})

    async def post(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        hass: HomeAssistant = request.app[KEY_HASS]
        body = await read_body(request)
        domain, bad = _check(body)
        if bad:
            return self.json({'code': bad[0]}, bad[1])
        inst = installer(hass)
        try:
            if body.get('op') == 'configured':
                return self.json(await inst.configured(domain))
            if body.get('op') not in (None, 'install'):
                return self.json({'code': 'bad_op'}, 400)
            job = await inst.start(domain)
        except Refused as err:
            return self.json({'code': str(err)}, 409)
        return self.json(job, 202 if job['state'] in RUNNING else 200)

class RestartView(HomeAssistantView):
    url = '/api/hauflo/install/restart'
    name = 'api:hauflo:install:restart'
    requires_auth = True

    async def post(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        hass: HomeAssistant = request.app[KEY_HASS]
        body = await read_body(request)
        domain, bad = _check(body)
        if bad:
            return self.json({'code': bad[0]}, bad[1])
        if body.get('confirm') is not True:
            return self.json({'code': 'confirm_required'}, 400)
        try:
            return self.json(await installer(hass).restart(domain), 202)
        except Refused as err:
            return self.json({'code': str(err)}, 409)
