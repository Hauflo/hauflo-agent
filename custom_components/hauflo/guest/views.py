"""HTTP view and admin notices for guest access."""
from __future__ import annotations
from collections.abc import Callable
import logging
import time
from typing import TYPE_CHECKING, Any
from homeassistant.components import persistent_notification
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from ..const import site_ru, wants_ru
from ..guard import contained
from ..house import is_guest, may_edit, refuse
from ..rules.text import said
from .common import UNSAFE_TEXT, Invalid, Refused, ask_gap
if TYPE_CHECKING:
    from .access import GuestAccess
_LOGGER = logging.getLogger(__name__)

async def ask_admins(hass: HomeAssistant, user: Any, note: str, phones: list[dict[str, Any]], config: dict[str, Any] | None=None) -> dict[str, Any]:
    """«Марина просит гостевую ссылку на вечер» to the admins: a notification in HA and a push to the admins' phones
    (the
    HA app, found as the alarms find them). The guest link itself is still made by an admin — nothing is issued here."""
    ru = site_ru(hass.config.language, config)
    who = (user.name or '').strip() or ('Кто-то из семьи' if ru else 'Someone in the family')
    what = f'{who} просит гостевую ссылку' if ru else f'{who} asks for a guest link'
    if note:
        what += f' {note}' if ru else f' ({note})'
    how = 'Создать: Hauflo → ⚙ Настройки → Гостевой доступ.' if ru else 'To make one: Hauflo → ⚙ Settings → Guest access.'
    title = 'Гостевой доступ' if ru else 'Guest access'
    persistent_notification.async_create(hass, f'{what}.\n\n{how}', title=title, notification_id=f'hauflo_guest_ask_{user.id}')
    admins = {u.id for u in await hass.auth.async_get_users() if u.is_admin and u.is_active and (not u.system_generated)}
    people = {s.entity_id for s in hass.states.async_all('person') if s.attributes.get('user_id') in admins}
    sent = 0
    for phone in phones:
        if phone.get('person') not in people:
            continue
        with contained(_LOGGER, 'Hauflo guest request to %s failed: %s', phone.get('service'), err=True):
            await hass.services.async_call('notify', phone['service'], {'title': title, 'message': f'{what}. {how}', 'data': {'tag': f'hauflo_guest_ask_{user.id}'}}, blocking=True)
            sent += 1
    return {'asked': True, 'phones': sent}

class GuestView(HomeAssistantView):
    """The family's side, signed in to HA. Not reachable through Hauflo's tunnel (not in __init__._HAUFLO_VIEWS)."""
    url = '/api/hauflo/guest'
    name = 'api:hauflo:guest'
    requires_auth = True

    def __init__(self, engine_of: Callable[[], GuestAccess | None], alarms_of: Callable[[], Any] | None=None) -> None:
        self.engine_of = engine_of
        self.alarms_of = alarms_of
        self.asked: dict[str, float] = {}

    async def get(self, request):
        engine = self.engine_of()
        if engine is None:
            return self.json_message('Hauflo agent not set up', 503)
        await engine.async_load()
        user = request['hass_user']
        if is_guest(request.app[KEY_HASS], user):
            return self.json_message('a guest has no guest settings', 403)
        out = engine.answer(user)
        out['can_issue'] = bool(out['can_issue']) and await may_edit(request.app[KEY_HASS], user)
        out['can_ask'] = not out['can_issue'] and user is not None and (not user.system_generated)
        if request.query.get('rooms'):
            out['rooms'] = engine.rooms(user) if out['can_issue'] else []
        out['can_issue_pages'] = bool(out['can_issue']) and bool(user and user.is_admin)
        if out['can_issue_pages'] and request.query.get('pages'):
            out['pages_available'] = await engine.available()
        return self.json(out)

    async def post(self, request):
        engine = self.engine_of()
        if engine is None:
            return self.json_message('Hauflo agent not set up', 503)
        await engine.async_load()
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict):
            return self.json_message('an object expected', 400)
        op = body.get('op')
        if is_guest(request.app[KEY_HASS], request['hass_user']):
            return self.json_message('a guest has no guest settings', 403)
        if op in ('issue', 'issue_pages') and (no := (await refuse(request))) is not None:
            return no
        user = request['hass_user']
        if op == 'ask':
            note = body.get('note', '')
            if set(body) - {'op', 'note', 'lang'} or not isinstance(note, str) or len(note) > 60 or UNSAFE_TEXT.search(note):
                return self.json_message('ask: an optional short note', 400)
            if user is None or user.system_generated:
                return self.json_message('a person of the house only', 403)
            last = self.asked.get(user.id, 0.0)
            if time.monotonic() - last < ask_gap() and last:
                return self.json({'message': 'already asked', 'code': 'too_soon', 'wait': int(ask_gap() - (time.monotonic() - last))}, 429)
            self.asked[user.id] = time.monotonic()
            alarms = self.alarms_of() if self.alarms_of else None
            phones = []
            with contained(_LOGGER, 'Hauflo: no phones: the HA notification alone — ignored'):
                phones = alarms.phones() if alarms is not None else []
            return self.json(await ask_admins(request.app[KEY_HASS], user, ' '.join(note.split()), phones, getattr(alarms, 'config', None)))
        try:
            if op == 'issue':
                if set(body) - {'op', 'label', 'rooms', 'hours', 'max_devices', 'lang'}:
                    return self.json_message('only label, rooms, hours, max_devices', 400)
                return self.json(await engine.issue(user, body))
            if op == 'issue_pages':
                if set(body) - {'op', 'label', 'pages', 'hours', 'role', 'lang'}:
                    return self.json_message('only label, pages, hours, role', 400)
                return self.json(await engine.issue_pages(user, body))
            if op == 'revoke':
                if not isinstance(body.get('id'), str):
                    return self.json_message('id expected', 400)
                return self.json({'revoked': await engine.revoke(user, body['id']), **engine.answer(user)})
            if op == 'revoke_all':
                return self.json({'revoked': await engine.revoke(user, None), **engine.answer(user)})
        except Refused as err:
            return self.json(await said(engine.hass, err, wants_ru(body, engine.hass.config.language)), err.status)
        except PermissionError as err:
            return self.json_message(str(err), 403)
        except Invalid as err:
            return self.json_message(str(err), 400)
        return self.json_message('op: issue | issue_pages | revoke | revoke_all | ask', 400)
