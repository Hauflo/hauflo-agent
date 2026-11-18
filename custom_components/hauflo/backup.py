"""A Home Assistant backup before Hauflo changes anything (CLAUDE.md §2, HA core review round 3): Hauflo asks
for one before every deploy and starts only when it's written. Settings only (no recorder database: seconds, not
minutes), to HA's own local backups — restored from Settings → System → Backups like any other."""
from __future__ import annotations
import asyncio
import functools
import logging
import re
import time
from typing import Any
from homeassistant.components import persistent_notification
from homeassistant.components.backup import async_get_manager
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey
from .const import DOMAIN
from .guard import contained
from .tuning import tuning
_LOGGER = logging.getLogger(__name__)
DATA_LAST: HassKey[dict[str, Any]] = HassKey(f'{DOMAIN}_backup_last')
_OURS = re.compile('^Hauflo \\d{4}-\\d\\d-\\d\\d \\d\\d[:.]\\d\\d([:.]\\d\\d)?$')
NAME_FORMAT = 'Hauflo %Y-%m-%d %H.%M.%S'

def is_ours(name: str | None) -> bool:
    """One of Hauflo's own pre-deploy backups (settings only — not a backup of the house: health/backup.py)."""
    return bool(_OURS.match(name or ''))
_t = functools.cache(lambda: tuning()['backup'])
_wait = functools.cache(lambda: _t()['wait_s'])
_keep = functools.cache(lambda: _t()['keep'])
_keep_day = functools.cache(lambda: _t()['keep_day_s'])
_keep_week = functools.cache(lambda: _t()['keep_week_s'])
_reuse = functools.cache(lambda: _t()['reuse_s'])
fresh_cfg = functools.cache(lambda: _t()['fresh_s'])
NO_BACKUP_NOTE = 'hauflo_no_backup'

def last(hass: HomeAssistant) -> dict[str, Any]:
    """The last backup asked for (empty: none yet in this run of HA)."""
    return hass.data.setdefault(DATA_LAST, {})

def fresh(hass: HomeAssistant) -> bool:
    """A backup written (or skipped by the integrator's choice) within the last 30 minutes: changes may go on."""
    cur = last(hass)
    return cur.get('state') in ('done', 'skipped') and time.time() - float(cur.get('at') or 0) <= fresh_cfg()

async def _create(hass: HomeAssistant, name: str) -> None:
    manager = async_get_manager(hass)
    settings = getattr(getattr(manager, 'config', None), 'data', None)
    password = getattr(getattr(settings, 'create_backup', None), 'password', None)
    agent_id = next(iter(getattr(manager, 'local_backup_agents', None) or {}), None) or next((a for a in getattr(manager, 'backup_agents', {}) if a.endswith('.local')), None)
    if agent_id is None:
        raise HomeAssistantError('no local backup location in this Home Assistant', translation_domain=DOMAIN, translation_key='no_backup_location')
    await manager.async_create_backup(agent_ids=[agent_id], include_addons=None, include_all_addons=False, include_database=False, include_folders=None, include_homeassistant=True, name=name, password=password)
    with contained(_LOGGER, 'Hauflo: older Hauflo backups not cleaned up'):
        backups, _ = await manager.async_get_backups()
        ours = sorted((b for b in backups.values() if _OURS.match(b.name or '')), key=lambda b: b.date, reverse=True)
        now = dt_util.utcnow()
        age = {b.backup_id: (now - dt_util.parse_datetime(b.date)).total_seconds() if dt_util.parse_datetime(b.date) else 0 for b in ours}
        keep = {b.backup_id for b in ours[:_keep()]} | {b.backup_id for b in ours if age[b.backup_id] < _keep_day()}
        week_old = next((b for b in ours if age[b.backup_id] >= _keep_week()), None)
        if week_old is not None:
            keep.add(week_old.backup_id)
        for old in ours:
            if old.backup_id not in keep:
                await manager.async_delete_backup(old.backup_id)

class BackupView(HomeAssistantView):
    url = '/api/hauflo/backup'
    name = 'api:hauflo:backup'
    requires_auth = True

    async def get(self, request):
        return self.json(last(request.app[KEY_HASS]) or {'state': 'none'})

    async def post(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        hass: HomeAssistant = request.app[KEY_HASS]
        cur = last(hass)
        if cur.get('state') == 'running':
            return self.json(cur, 202)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if isinstance(body, dict) and body.get('skip') is True:
            cur.clear()
            cur.update(state='skipped', name=None, at=time.time())
            persistent_notification.async_create(hass, f"Hauflo изменил настройку без резервной копии — по выбору интегратора ({dt_util.now().strftime('%Y-%m-%d %H:%M')}). / Hauflo changed the setup without a backup, as the integrator chose.", 'Hauflo', NO_BACKUP_NOTE)
            return self.json(cur)
        used = body.get('used') if isinstance(body, dict) else None
        if cur.get('state') == 'done' and time.time() - float(cur.get('at') or 0) < _reuse() and (not used or used != cur.get('name')):
            return self.json({**cur, 'reused': True})
        if 'backup' not in hass.config.components:
            return self.json({'state': 'failed', 'error': 'no_backup_integration'}, 501)
        name = dt_util.now().strftime(NAME_FORMAT)
        cur.clear()
        cur.update(state='running', name=name, at=time.time())

        async def run() -> None:
            try:
                await _create(hass, name)
                cur.update(state='done')
            except Exception as err:
                _LOGGER.warning("Hauflo: told to Hauflo, which then doesn't deploy", exc_info=True)
                cur.update(state='failed', error=str(err)[:300])
        task = hass.async_create_background_task(run(), 'hauflo backup')
        try:
            await asyncio.wait_for(asyncio.shield(task), _wait())
        except TimeoutError:
            return self.json(cur, 202)
        return self.json(cur, 200 if cur.get('state') == 'done' else 500)
