"""word.

Only Hauflo's own release: the exact version asked, from GitHub's release of that version first, then Hauflo's mirrors
(servers.json: they hold the latest one — the version inside the zip decides), every hop on those hosts only (https),
checked against the sha256 the cloud read from the published release AND against the release's signature
(hauflo.zip.sig beside the zip, at the same source): Ed25519 over {"domain","version","sha256"} by one of SIGNING_KEYS —
sha256 alone came from the very mirror it guards). The manifest's domain and version checked too;
unpacked as install.py unpacks (no path out of the folder, size and file count capped) into custom_components/hauflo,
the folder it replaces kept beside (install's backups, the last few). Never a downgrade by «update»: going back is
«rollback», to the very folder this update replaced.

HA loads the new code only after a restart, and the restart is never automatic: `restart: true` with `confirm: true` in
the same request — the wave the integrator started and confirmed («HA перезапустится на каждом объекте»). After the
restart the new code says what happened (async_load): updated / rolled_back, or failed:not_loaded. What cannot be
answered by code that does not load (an import error in the new version) the cloud sees as the agent not coming back:
that site is left for a manual rollback (the backup folder is there)."""
from __future__ import annotations
import asyncio
import base64
import binascii
import contextlib
import hashlib
import json
import logging
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlparse
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from . import issues, servers
from .const import DOMAIN, SIGNING_KEYS, UNSIGNED_BEFORE, VERSION
from .install import common as install_common, fetch as install_fetch, views as install_views
from .storage import HaufloStore
_LOGGER = logging.getLogger(__name__)
FEATURE = 'self_update'
STORE_KEY = 'hauflo.self_update'
DATA = 'hauflo_self_update'
RUNNING = ('downloading', 'verifying')
WAITING = ('restart_required', 'rollback_required')

def _ver(v: str) -> tuple[int, ...]:
    return tuple((int(x) for x in install_common.bare(v).split('.')))

def sources(version: str) -> list[str]:
    """Where the release of `version` is: GitHub's release of that very version, then each mirror's latest."""
    gh = servers.servers_cfg()['github_zip'].replace('/latest/download/', f'/download/v{install_common.bare(version)}/')
    return [gh, *(f'{m}/hauflo.zip' for m in servers.servers_cfg()['mirrors'])]

def hosts() -> set[str]:
    return set(install_common.integrations()['hosts']) | {urlparse(m).hostname or '' for m in servers.servers_cfg()['mirrors']}

def signed_message(version: str, sha256: str) -> bytes:
    """The exact bytes the release's signature covers — scripts/agent-sign.mjs signs the same ones."""
    return json.dumps({'domain': DOMAIN, 'version': install_common.bare(version), 'sha256': sha256}, separators=(',', ':')).encode()

def signature_ok(version: str, sha256: str, sig: bytes) -> bool:
    """hauflo.zip.sig (one line: the 64-byte Ed25519 signature in base64) made by one of SIGNING_KEYS for this version
    and this zip's sha256 — another version's signature, another zip's, another key's: False."""
    try:
        raw = base64.b64decode(sig.strip(), validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(raw) != 64:
        return False
    msg = signed_message(version, sha256)
    for key in SIGNING_KEYS:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(raw, msg)
        except (InvalidSignature, ValueError):
            continue
        return True
    return False

def unsigned_allowed(version: str) -> bool:
    """Versions before UNSIGNED_BEFORE (0.40.0) were published without a signature; from it on, only signed."""
    return _ver(version) < _ver(UNSIGNED_BEFORE)
SIGNATURE_WHYS = ('signature', 'signature_missing')

def _telling(whys: list[str]) -> str:
    for why in (*SIGNATURE_WHYS, 'checksum'):
        if why in whys:
            return why
    return next((w for w in whys if not w.startswith('download')), whys[-1] if whys else 'download')

def restore(dest: Path, work: Path, backup: str, want: str) -> str:
    """The folder `backup` (its manifest of version `want`) back in place of dest; dest kept as a backup → its name."""
    src = work / 'backups' / backup
    if not backup or '/' in backup or backup.startswith('.') or (not src.is_dir()):
        raise install_common.Refused('no_backup')
    if install_common.folder_version(src) != install_common.bare(want):
        raise install_common.Refused('backup_version')
    undone = f"{DOMAIN}-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1000000}-undone"
    dest.rename(work / 'backups' / undone)
    try:
        src.rename(dest)
    except OSError:
        (work / 'backups' / undone).rename(dest)
        raise
    return undone

class SelfUpdate:
    """This agent's own update (one at a time), kept across HA's restarts."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = HaufloStore(hass, STORE_KEY)
        self.job: dict[str, Any] = {}
        self._task: asyncio.Task | None = None

    def _dest(self) -> Path:
        return Path(self.hass.config.path('custom_components', DOMAIN))

    def _work(self) -> Path:
        return Path(self.hass.config.path(*install_common.WORK_DIR))

    async def async_load(self) -> None:
        """After a restart: the code running now says whether the update (or the way back) took."""
        data = await self.store.async_load()
        self.job = data.get('job') if isinstance(data, dict) and isinstance(data.get('job'), dict) else {}
        state = str(self.job.get('state', ''))
        now = install_common.bare(VERSION)
        step = None
        if state in RUNNING:
            step = 'failed:interrupted'
        elif state == 'restarting':
            step = 'updated' if now == install_common.bare(self.job.get('to', '')) else 'failed:not_loaded'
        elif state == 'rolling_back':
            step = 'rolled_back' if now == install_common.bare(self.job.get('from', '')) else 'failed:rollback_not_loaded'
        if step:
            self._step(step)
            await self._save()

    async def _save(self) -> None:
        await self.store.async_save({'job': self.job})

    def _step(self, state: str) -> None:
        self.job['state'] = state
        self.job['at'] = time.time()
        self.job.setdefault('steps', []).append({'step': state, 'at': self.job['at']})
        del self.job['steps'][:-20]

    def view(self) -> dict[str, Any]:
        j = {k: v for k, v in self.job.items() if k not in ('steps', 'backup')}
        return {'feature': FEATURE, 'version': install_common.bare(VERSION), 'job': {'state': 'none', **j, 'steps': [s['step'] for s in self.job.get('steps', [])]}}

    async def update(self, version: str, sha256: str, restart: bool) -> dict[str, Any]:
        if not install_common.VERSION_RE.match(str(version)) or not install_common.SHA_RE.match(str(sha256)):
            raise install_common.Refused('bad_request')
        if _ver(version) <= _ver(VERSION):
            raise install_common.Refused('not_newer')
        if self._task and (not self._task.done()):
            raise install_common.Refused('busy')
        self.job = {'from': install_common.bare(VERSION), 'to': install_common.bare(version), 'restart': restart, 'steps': []}
        self._step('downloading')
        await self._save()
        self._task = self.hass.async_create_background_task(self._run(version, sha256, restart), 'hauflo self update')
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(self._task), install_common.wait())
        return self.view()

    async def _run(self, version: str, sha256: str, restart: bool) -> None:
        it = {'domain': DOMAIN, 'version': install_common.bare(version), 'zip_path': ''}
        try:
            data, whys = (None, [])
            for url in sources(version):
                try:
                    got = await install_fetch.fetch(self.hass, url, hosts())
                except install_common.Refused as err:
                    whys.append(str(err))
                    continue
                if hashlib.sha256(got).hexdigest() != sha256:
                    whys.append('checksum')
                    continue
                why = await self._signed(url, version, sha256)
                if why:
                    whys.append(why)
                    continue
                data = got
                break
            if data is None:
                raise install_common.Refused(_telling(whys))
            self._step('verifying')
            self.job['backup'] = await self.hass.async_add_executor_job(install_fetch.unpack, data, it, self._dest(), self._work(), True)
            self._step('restart_required')
            issues.clear_issue(self.hass, issues.UPDATE_NOT_SIGNED)
            if restart:
                await self._restart('restarting')
            _LOGGER.info('Hauflo: agent %s → %s unpacked', self.job['from'], self.job['to'])
        except install_common.Refused as err:
            self._step(f'failed:{err}')
            if str(err) in SIGNATURE_WHYS:
                issues.raise_issue(self.hass, issues.UPDATE_NOT_SIGNED, version=install_common.bare(version))
                _LOGGER.error("Hauflo: agent update to %s refused — %s: not signed by Hauflo's key, nothing installed", version, err)
            else:
                _LOGGER.warning('Hauflo: agent not updated — %s', err)
        except Exception as err:
            self.job['error'] = str(err)[:300]
            self._step('failed:error')
            _LOGGER.warning('Hauflo: agent not updated', exc_info=True)
        finally:
            await self._save()

    async def _signed(self, url: str, version: str, sha256: str) -> str | None:
        try:
            sig = await install_fetch.fetch(self.hass, f'{url}.sig', hosts())
        except install_common.Refused as err:
            if str(err) != 'download_404':
                return str(err)
            if unsigned_allowed(version):
                self.job['unsigned'] = True
                _LOGGER.warning('Hauflo: agent %s has no signature at %s — accepted: versions before %s were published unsigned', version, url, UNSIGNED_BEFORE)
                return None
            return 'signature_missing'
        return None if signature_ok(version, sha256, sig) else 'signature'

    async def rollback(self, restart: bool) -> dict[str, Any]:
        """Back to the folder this update replaced — while its files are what is in place (before or after the
        restart)."""
        if self._task and (not self._task.done()):
            raise install_common.Refused('busy')
        if self.job.get('state') not in ('restart_required', 'restarting', 'updated', 'failed:not_loaded') or not self.job.get('backup'):
            raise install_common.Refused('nothing_to_undo')
        self.job['undone'] = await self.hass.async_add_executor_job(restore, self._dest(), self._work(), self.job['backup'], self.job['from'])
        self.job.pop('backup', None)
        self._step('rollback_required')
        if restart:
            await self._restart('rolling_back')
        await self._save()
        return self.view()

    async def restart(self) -> dict[str, Any]:
        state = self.job.get('state')
        if state not in WAITING:
            raise install_common.Refused('not_needed')
        await self._restart('restarting' if state == 'restart_required' else 'rolling_back')
        return self.view()

    async def _restart(self, state: str) -> None:
        self._step(state)
        await self._save()
        self.hass.async_create_background_task(self.hass.services.async_call('homeassistant', 'restart'), 'hauflo self update restart')

def updater(hass: HomeAssistant) -> SelfUpdate:
    u = hass.data.get(DATA)
    if u is None:
        u = hass.data[DATA] = SelfUpdate(hass)
    return u

class SelfUpdateView(HomeAssistantView):
    url = '/api/hauflo/self_update'
    name = 'api:hauflo:self_update'
    requires_auth = True

    async def get(self, request):
        return self.json(updater(request.app[KEY_HASS]).view())

    async def post(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        body = await install_views.read_body(request)
        u = updater(request.app[KEY_HASS])
        op = body.get('op')
        restart = body.get('restart') is True
        if (restart or op == 'restart') and body.get('confirm') is not True:
            return self.json({'code': 'confirm_required'}, 400)
        try:
            if op == 'update':
                out = await u.update(str(body.get('version', '')), str(body.get('sha256', '')), restart)
            elif op == 'rollback':
                out = await u.rollback(restart)
            elif op == 'restart':
                out = await u.restart()
            else:
                return self.json({'code': 'bad_op'}, 400)
        except install_common.Refused as err:
            return self.json({'code': str(err)}, 409)
        return self.json(out, 202 if out['job']['state'] in RUNNING else 200)
