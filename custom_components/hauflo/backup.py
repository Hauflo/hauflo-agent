from __future__ import annotations
import asyncio
import logging
import re
import time
from typing import Any
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.exceptions import HomeAssistantError
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from .const import DOMAIN
from .tuning import TUNING
_LOGGER = logging.getLogger(__name__)
LAST: dict[str, Any] = {}
_OURS = re.compile('^Hauflo \\d{4}-\\d\\d-\\d\\d \\d\\d[:.]\\d\\d([:.]\\d\\d)?$')
NAME_FORMAT = 'Hauflo %Y-%m-%d %H.%M.%S'
_T = TUNING['backup']
_WAIT = _T['wait_s']
_KEEP = _T['keep']
_KEEP_DAY = _T['keep_day_s']
_KEEP_WEEK = _T['keep_week_s']
_REUSE = _T['reuse_s']
FRESH = _T['fresh_s']
NO_BACKUP_NOTE = 'hauflo_no_backup'

def fresh() -> bool:
    return LAST.get('state') in ('done', 'skipped') and time.time() - float(LAST.get('at') or 0) <= FRESH

async def _create(hass: HomeAssistant, name: str) -> None:
    from homeassistant.components.backup import async_get_manager
    manager = async_get_manager(hass)
    settings = getattr(getattr(manager, 'config', None), 'data', None)
    password = getattr(getattr(settings, 'create_backup', None), 'password', None)
    agent_id = next(iter(getattr(manager, 'local_backup_agents', None) or {}), None) or next((a for a in getattr(manager, 'backup_agents', {}) if a.endswith('.local')), None)
    if agent_id is None:
        raise HomeAssistantError('no local backup location in this Home Assistant', translation_domain=DOMAIN, translation_key='no_backup_location')
    await manager.async_create_backup(agent_ids=[agent_id], include_addons=None, include_all_addons=False, include_database=False, include_folders=None, include_homeassistant=True, name=name, password=password)
    try:
        backups, _ = await manager.async_get_backups()
        ours = sorted((b for b in backups.values() if _OURS.match(b.name or '')), key=lambda b: b.date, reverse=True)
        now = dt_util.utcnow()
        age = {b.backup_id: (now - dt_util.parse_datetime(b.date)).total_seconds() if dt_util.parse_datetime(b.date) else 0 for b in ours}
        keep = {b.backup_id for b in ours[:_KEEP]} | {b.backup_id for b in ours if age[b.backup_id] < _KEEP_DAY}
        week_old = next((b for b in ours if age[b.backup_id] >= _KEEP_WEEK), None)
        if week_old is not None:
            keep.add(week_old.backup_id)
        for old in ours:
            if old.backup_id not in keep:
                await manager.async_delete_backup(old.backup_id)
    except Exception:
        _LOGGER.warning('Hauflo: older Hauflo backups not cleaned up', exc_info=True)

class BackupView(HomeAssistantView):
    url = '/api/hauflo/backup'
    name = 'api:hauflo:backup'
    requires_auth = True

    async def get(self, request):
        return self.json(LAST or {'state': 'none'})

    async def post(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        hass: HomeAssistant = request.app[KEY_HASS]
        if LAST.get('state') == 'running':
            return self.json(LAST, 202)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if isinstance(body, dict) and body.get('skip') is True:
            LAST.clear()
            LAST.update(state='skipped', name=None, at=time.time())
            from homeassistant.components import persistent_notification
            persistent_notification.async_create(hass, f"Hauflo изменил настройку без резервной копии — по выбору интегратора ({dt_util.now().strftime('%Y-%m-%d %H:%M')}). / Hauflo changed the setup without a backup, as the integrator chose.", 'Hauflo', NO_BACKUP_NOTE)
            return self.json(LAST)
        used = body.get('used') if isinstance(body, dict) else None
        if LAST.get('state') == 'done' and time.time() - float(LAST.get('at') or 0) < _REUSE and (not used or used != LAST.get('name')):
            return self.json({**LAST, 'reused': True})
        if 'backup' not in hass.config.components:
            return self.json({'state': 'failed', 'error': 'no_backup_integration'}, 501)
        name = dt_util.now().strftime(NAME_FORMAT)
        LAST.clear()
        LAST.update(state='running', name=name, at=time.time())

        async def run() -> None:
            try:
                await _create(hass, name)
                LAST.update(state='done')
            except Exception as err:
                LAST.update(state='failed', error=str(err)[:300])
        task = hass.async_create_background_task(run(), 'hauflo backup')
        try:
            await asyncio.wait_for(asyncio.shield(task), _WAIT)
        except TimeoutError:
            return self.json(LAST, 202)
        return self.json(LAST, 200 if LAST.get('state') == 'done' else 500)
