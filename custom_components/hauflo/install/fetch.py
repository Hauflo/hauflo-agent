"""Downloads and unpacks an integration's release."""
from __future__ import annotations
import io
import json
from pathlib import Path
import shutil
import stat
import time
from typing import Any
from urllib.parse import urljoin, urlparse
import zipfile
import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .common import Refused, bare, integrations, keep, max_download, max_members, max_redirects, max_unpacked, timeout

async def download(hass: HomeAssistant, it: dict[str, Any]) -> bytes:
    """The pinned asset, every hop of its redirects on GitHub's own hosts (https only), capped in size."""
    return await fetch(hass, integrations()['download'].format(repo=it['repo'], version=it['version'], asset=it['asset']), set(integrations()['hosts']))

async def fetch(hass: HomeAssistant, url: str, hosts: set[str]) -> bytes:
    """A file, every hop of its redirects on these hosts (https only), capped in size — install's and self_update's."""
    session = async_get_clientsession(hass)
    for _ in range(max_redirects() + 1):
        u = urlparse(url)
        if u.scheme != 'https' or u.hostname not in hosts:
            raise Refused('host')
        try:
            async with session.get(url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=timeout())) as res:
                if res.status in (301, 302, 303, 307, 308) and res.headers.get('Location'):
                    url = urljoin(url, res.headers['Location'])
                    continue
                if res.status != 200:
                    raise Refused(f'download_{res.status}')
                if int(res.headers.get('Content-Length') or 0) > max_download():
                    raise Refused('too_big')
                buf = bytearray()
                async for chunk in res.content.iter_chunked(65536):
                    buf += chunk
                    if len(buf) > max_download():
                        raise Refused('too_big')
                return bytes(buf)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise Refused('download') from err
    raise Refused('redirects')

def _is_link(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)

def unpack(data: bytes, it: dict[str, Any], dest: Path, work: Path, replace: bool) -> str | None:
    """Into dest (custom_components/<domain>) only: every member checked first, unpacked beside it, swapped in with one rename, the folder it replaces kept in work/backups (the last _KEEP of this domain)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as err:
        raise Refused('not_zip') from err
    with zf:
        infos = zf.infolist()
        if len(infos) > max_members():
            raise Refused('too_many_files')
        prefix = it['zip_path'].strip('/')
        files: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
        total = 0
        for info in infos:
            name = info.filename
            if '\\' in name or name.startswith('/') or _is_link(info):
                raise Refused('unsafe_path')
            parts = tuple(name.split('/'))
            if prefix:
                head = tuple(prefix.split('/'))
                if parts[:len(head)] != head:
                    continue
                parts = parts[len(head):]
            if info.is_dir() or not parts or parts[-1] == '':
                continue
            if any((p in ('', '.', '..') or ':' in p for p in parts)):
                raise Refused('unsafe_path')
            total += info.file_size
            if total > max_unpacked():
                raise Refused('too_big')
            files.append((info, parts))
        manifest = next((i for i, p in files if p == ('manifest.json',)), None)
        try:
            m = json.loads(zf.read(manifest)) if manifest else {}
        except ValueError:
            m = {}
        if not isinstance(m, dict) or m.get('domain') != it['domain'] or bare(m.get('version')) != bare(it['version']):
            raise Refused('manifest')
        stage = work / f"{it['domain']}.new"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        try:
            root = stage.resolve()
            for info, parts in files:
                target = stage.joinpath(*parts)
                if not target.resolve().is_relative_to(root):
                    raise Refused('unsafe_path')
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with zf.open(info) as src, target.open('wb') as out:
                    while (chunk := src.read(65536)):
                        written += len(chunk)
                        if written > info.file_size:
                            raise Refused('too_big')
                        out.write(chunk)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    backups = work / 'backups'
    kept = None
    if dest.exists():
        if not replace:
            shutil.rmtree(stage, ignore_errors=True)
            raise Refused('exists')
        backups.mkdir(parents=True, exist_ok=True)
        kept = f"{it['domain']}-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1000000}"
        dest.rename(backups / kept)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        stage.rename(dest)
    except OSError:
        if kept:
            (backups / kept).rename(dest)
        shutil.rmtree(stage, ignore_errors=True)
        raise
    if backups.exists():
        old = sorted((p for p in backups.iterdir() if p.name.startswith(f"{it['domain']}-")), key=lambda p: p.name, reverse=True)
        for p in old[keep():]:
            shutil.rmtree(p, ignore_errors=True)
    return kept

async def via_marketplace(hass: HomeAssistant, it: dict[str, Any]) -> bool:
    if 'marketplace' not in hass.config.components:
        return False
    entries = [e for e in hass.config_entries.async_entries('marketplace') if getattr(e, 'runtime_data', None) is not None]
    repos = getattr(entries[0].runtime_data, 'repositories', None) if entries else None
    repo = repos.get_by_full_name(it['repo']) if repos is not None else None
    if repo is None:
        return False
    await repo.async_install_repository(ref=it['version'])
    return True

async def via_hacs(hass: HomeAssistant, it: dict[str, Any]) -> bool:
    """HACS: its download of the pinned tag (the repository added to it when it doesn't know it yet)."""
    if 'hacs' not in hass.config.components:
        return False
    hacs = hass.data.get('hacs')
    repos = getattr(hacs, 'repositories', None)
    if repos is None:
        return False
    repo = repos.get_by_full_name(it['repo'])
    if repo is None:
        await hacs.async_register_repository(it['repo'], it['store']['category'])
        repo = repos.get_by_full_name(it['repo'])
        if repo is None:
            return False
    await repo.async_download_repository(ref=it['version'])
    return True
