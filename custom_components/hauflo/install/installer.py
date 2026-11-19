"""Runs an install and keeps its state."""
from __future__ import annotations
import asyncio
import contextlib
import hashlib
import logging
from pathlib import Path
import time
from typing import Any
from homeassistant.core import HomeAssistant
from ..storage import HaufloStore
from .common import DATA, RUNNING, SOURCES, STORE_KEY, WORK_DIR, Refused, bare, folder_version, ha_ok, item, loaded_version, wait
from .fetch import download, unpack, via_hacs, via_marketplace
_LOGGER = logging.getLogger(__name__)

class Installer:
    """The installs of this HA (one at a time per integration), kept across its restarts."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = HaufloStore(hass, STORE_KEY)
        self.jobs: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def _dest(self, domain: str) -> Path:
        return Path(self.hass.config.path('custom_components', domain))

    def _work(self) -> Path:
        return Path(self.hass.config.path(*WORK_DIR))

    async def async_load(self) -> None:
        """After a restart: the one asked for is known now (restarted) or it isn't (failed:not_loaded); an install the restart cut off is failed:interrupted."""
        data = await self.store.async_load()
        jobs = data.get('jobs') if isinstance(data, dict) else None
        self.jobs = {d: j for d, j in (jobs or {}).items() if item(d) and isinstance(j, dict)}
        changed = False
        for domain, job in self.jobs.items():
            state = str(job.get('state'))
            if state in RUNNING:
                self._step(job, 'failed:interrupted')
                changed = True
            elif state in ('restart_required', 'restarting'):
                version = await loaded_version(self.hass, domain)
                if version == bare(job.get('version')):
                    self._step(job, 'restarted')
                    changed = True
                elif state == 'restarting':
                    self._step(job, 'failed:not_loaded')
                    changed = True
        if changed:
            await self._save()

    async def _save(self) -> None:
        await self.store.async_save({'jobs': self.jobs})

    @staticmethod
    def _step(job: dict[str, Any], state: str) -> None:
        job['state'] = state
        job['at'] = time.time()
        job.setdefault('steps', []).append({'step': state, 'at': job['at']})
        del job['steps'][:-20]

    def items(self) -> list[dict[str, Any]]:
        return [{'domain': d, **{k: v for k, v in j.items() if k != 'steps'}, 'steps': [s['step'] for s in j.get('steps', [])]} for d, j in sorted(self.jobs.items())]

    def job(self, domain: str) -> dict[str, Any]:
        return next((x for x in self.items() if x['domain'] == domain), {'domain': domain, 'state': 'none', 'steps': []})

    async def start(self, domain: str) -> dict[str, Any]:
        """Start (or join) an install; answered within _WAIT seconds, the rest is read by GET."""
        it = item(domain)
        if it is None:
            raise Refused('not_in_canon')
        if not ha_ok(it['min_ha']):
            raise Refused('ha_too_old')
        task = self._tasks.get(domain)
        if task is None or task.done():
            job = self.jobs.get(domain) or {}
            if job.get('state') in ('restart_required', 'restarting', 'restarted', 'configured') and job.get('version') == it['version']:
                return self.job(domain)
            owned = bool(job.get('owned')) or job.get('source') in SOURCES
            self.jobs[domain] = {'version': it['version'], 'repo': it['repo'], 'source': None, 'owned': owned, 'steps': []}
            self._step(self.jobs[domain], 'downloading')
            task = self._tasks[domain] = self.hass.async_create_background_task(self._run(it), f'hauflo install {domain}')
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), wait())
        return self.job(domain)

    async def _run(self, it: dict[str, Any]) -> None:
        domain = it['domain']
        job = self.jobs[domain]
        try:
            dest = self._dest(domain)
            have = await self.hass.async_add_executor_job(folder_version, dest)
            ours = bool(job.get('owned'))
            if have == bare(it['version']):
                job['source'] = 'present'
            elif have is not None and (not ours):
                raise Refused('exists')
            elif await via_marketplace(self.hass, it):
                job['source'] = 'marketplace'
            elif await via_hacs(self.hass, it):
                job['source'] = 'hacs'
            else:
                data = await download(self.hass, it)
                self._step(job, 'verifying')
                if hashlib.sha256(data).hexdigest() != it['sha256']:
                    raise Refused('checksum')
                job['backup'] = await self.hass.async_add_executor_job(unpack, data, it, dest, self._work(), ours)
                job['source'] = 'github'
            if job['source'] in ('marketplace', 'hacs'):
                self._step(job, 'verifying')
                if await self.hass.async_add_executor_job(folder_version, dest) != bare(it['version']):
                    raise Refused('version')
            self._step(job, 'installed')
            self._step(job, 'restarted' if await loaded_version(self.hass, domain) == bare(it['version']) else 'restart_required')
            _LOGGER.info('Hauflo: %s %s installed (%s)', it['repo'], it['version'], job['source'])
        except Refused as err:
            self._step(job, f'failed:{err}')
            _LOGGER.warning('Hauflo: %s not installed — %s', it['repo'], err)
        except Exception as err:
            job['error'] = str(err)[:300]
            self._step(job, 'failed:error')
            _LOGGER.warning('Hauflo: %s not installed', it['repo'], exc_info=True)
        finally:
            job['owned'] = ours_now(job)
            await self._save()

    async def configured(self, domain: str) -> dict[str, Any]:
        job = self.jobs.get(domain)
        if job is None or job.get('state') not in ('restarted', 'configured'):
            raise Refused('not_restarted')
        if job['state'] != 'configured':
            self._step(job, 'configured')
            await self._save()
        return self.job(domain)

    async def restart(self, domain: str) -> dict[str, Any]:
        """HA's own restart (it checks the configuration first and refuses a broken one) — only for an install waiting for it, only on this explicit request."""
        job = self.jobs.get(domain)
        if job is None or job.get('state') not in ('restart_required', 'restarting'):
            raise Refused('not_needed')
        self._step(job, 'restarting')
        await self._save()
        self.hass.async_create_background_task(self.hass.services.async_call('homeassistant', 'restart'), 'hauflo install restart')
        return self.job(domain)

def ours_now(job: dict[str, Any]) -> bool:
    return bool(job.get('owned')) or job.get('source') in SOURCES

def installer(hass: HomeAssistant) -> Installer:
    inst = hass.data.get(DATA)
    if inst is None:
        inst = hass.data[DATA] = Installer(hass)
    return inst
