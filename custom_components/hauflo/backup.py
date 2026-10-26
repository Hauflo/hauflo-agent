"""A Home Assistant backup before Hauflo changes anything (CLAUDE.md §2, HA core review round 3): Hauflo asks
for one before every deploy and starts only when it's written. Settings only (no recorder database: seconds, not
minutes), to HA's own local backups — restored from Settings → System → Backups like any other."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# The last backup asked for: {"state": "running"|"done"|"failed", "name", "at", "error"?}
LAST: dict[str, Any] = {}
_OURS = re.compile(r"^Hauflo \d{4}-\d\d-\d\d \d\d:\d\d(:\d\d)?$")
# Hauflo's tunnel request lives 30 s: answer «running» well before, a longer backup is followed by GET (integrator
# run 18: a 40 s backup always failed the deploy with «timed out»).
_WAIT = 20
_KEEP = 3  # Hauflo's own backups kept — older ones go (integrator run 18: six in 35 minutes, gigabytes with media)
# Never cleaned away by asking for more (security expert 25.10: three backups in a row wiped every one from before an
# attack): a day's are all kept, and the newest older than a week stays as the last good one.
_KEEP_DAY = 24 * 3600
_KEEP_WEEK = 7 * 24 * 3600
_REUSE = 600  # one asked within 10 minutes of the last is that one
FRESH = 1800  # changes through the tunnel need a backup (or the integrator's «without») this recent
NO_BACKUP_NOTE = "hauflo_no_backup"


def fresh() -> bool:
    """A backup written (or skipped by the integrator's choice) within the last 30 minutes: changes may go on."""
    return LAST.get("state") in ("done", "skipped") and time.time() - float(LAST.get("at") or 0) <= FRESH


async def _create(hass: HomeAssistant, name: str) -> None:
    from homeassistant.components.backup import async_get_manager  # noqa: PLC0415 — public API (HA review round 4)

    manager = async_get_manager(hass)
    # Encrypted like HA's own automatic backups when a key is set (HA review round 4: secrets.yaml in the clear).
    settings = getattr(getattr(manager, "config", None), "data", None)
    password = getattr(getattr(settings, "create_backup", None), "password", None)
    agent_id = next(iter(manager.local_backup_agents))
    await manager.async_create_backup(
        agent_ids=[agent_id],
        include_addons=None,
        include_all_addons=False,
        include_database=False,
        include_folders=None,
        include_homeassistant=True,
        name=name,
        password=password,
    )
    # Only Hauflo's own («Hauflo YYYY-MM-DD HH:MM:SS»), never one the person or HA's schedule made.
    try:
        backups, _ = await manager.async_get_backups()
        ours = sorted((b for b in backups.values() if _OURS.match(b.name or "")), key=lambda b: b.date, reverse=True)
        now = dt_util.utcnow()
        age = {b.backup_id: (now - dt_util.parse_datetime(b.date)).total_seconds() if dt_util.parse_datetime(b.date) else 0 for b in ours}
        keep = {b.backup_id for b in ours[:_KEEP]} | {b.backup_id for b in ours if age[b.backup_id] < _KEEP_DAY}
        week_old = next((b for b in ours if age[b.backup_id] >= _KEEP_WEEK), None)
        if week_old is not None:
            keep.add(week_old.backup_id)
        for old in ours:
            if old.backup_id not in keep:
                await manager.async_delete_backup(old.backup_id)
    except Exception:  # noqa: BLE001 — the new backup is written; an old one left is no reason to fail
        _LOGGER.warning("Hauflo: older Hauflo backups not cleaned up", exc_info=True)


class BackupView(HomeAssistantView):
    url = "/api/hauflo/backup"
    name = "api:hauflo:backup"
    requires_auth = True

    async def get(self, request):
        return self.json(LAST or {"state": "none"})

    async def post(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        hass: HomeAssistant = request.app[KEY_HASS]
        if LAST.get("state") == "running":
            return self.json(LAST, 202)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if isinstance(body, dict) and body.get("skip") is True:
            # The integrator chose «deploy without a backup»: allowed, and said in the house where it can be seen.
            LAST.clear()
            LAST.update(state="skipped", name=None, at=time.time())
            from homeassistant.components import persistent_notification  # noqa: PLC0415

            persistent_notification.async_create(
                hass,
                f"Hauflo изменил настройку без бэкапа — по выбору интегратора ({dt_util.now().strftime('%Y-%m-%d %H:%M')}). / Hauflo changed the setup without a backup, as the integrator chose.",
                "Hauflo",
                NO_BACKUP_NOTE,
            )
            return self.json(LAST)
        if LAST.get("state") == "done" and time.time() - float(LAST.get("at") or 0) < _REUSE:
            return self.json(LAST)
        if "backup" not in hass.config.components:
            return self.json({"state": "failed", "error": "no_backup_integration"}, 501)
        name = f"Hauflo {dt_util.now().strftime('%Y-%m-%d %H:%M:%S')}"  # HA's own time zone
        LAST.clear()
        LAST.update(state="running", name=name, at=time.time())

        async def run() -> None:
            try:
                await _create(hass, name)
                LAST.update(state="done")
            except Exception as err:  # noqa: BLE001 — told to Hauflo, which then doesn't deploy
                LAST.update(state="failed", error=str(err)[:300])

        task = hass.async_create_background_task(run(), "hauflo backup")
        try:
            await asyncio.wait_for(asyncio.shield(task), _WAIT)
        except TimeoutError:
            return self.json(LAST, 202)
        return self.json(LAST, 200 if LAST.get("state") == "done" else 500)
