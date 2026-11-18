"""Install limits, the canon of allowed integrations and its checks."""
from __future__ import annotations
import functools
import json
import logging
from pathlib import Path
import re
from typing import Any
from awesomeversion import AwesomeVersion, AwesomeVersionException
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.loader import IntegrationNotFound, async_get_integration
from ..guard import contained
from ..tuning import tuning
_LOGGER = logging.getLogger(__name__)
FILE = Path(__file__).parents[1] / 'integrations.json'
FEATURE = 'install_integration'
STORE_KEY = 'hauflo.install'
DATA = 'hauflo_installer'
_t = functools.cache(lambda: tuning()['install'])
wait = functools.cache(lambda: _t()['wait_s'])
timeout = functools.cache(lambda: _t()['download_timeout_s'])
max_download = functools.cache(lambda: _t()['max_download_bytes'])
max_unpacked = functools.cache(lambda: _t()['max_unpacked_bytes'])
max_members = functools.cache(lambda: _t()['max_members'])
max_redirects = functools.cache(lambda: _t()['max_redirects'])
keep = functools.cache(lambda: _t()['keep_backups'])
RUNNING = ('downloading', 'verifying')
SOURCES = ('marketplace', 'hacs', 'github')
DONE = ('installed', 'restart_required', 'restarting', 'restarted', 'configured')
WORK_DIR = ('.hauflo', 'install')
_DOMAIN = re.compile('^[a-z0-9_]+$')
_REPO = re.compile('^(?!\\.)[A-Za-z0-9_.-]+/(?!\\.)[A-Za-z0-9_.-]+$')
_ASSET = re.compile('^[A-Za-z0-9_.-]+\\.zip$')
SHA_RE = re.compile('^[0-9a-f]{64}$')
VERSION_RE = re.compile('^v?\\d+(\\.\\d+)*$')

class Refused(Exception):
    """Why an install stops — the reason is the status' «failed:<reason>»."""

def load(path: Path=FILE) -> dict[str, Any]:
    """The canon, checked when the agent loads: a bad file is a bug of the release and fails loudly."""
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('version') != 1:
        raise ValueError('integrations.json: version 1 expected')
    url = data.get('download')
    if not isinstance(url, str) or not url.startswith('https://github.com/') or (not all((k in url for k in ('{repo}', '{version}', '{asset}')))):
        raise ValueError('integrations.json download: an https://github.com/ address with {repo}, {version} and {asset}')
    hosts = data.get('hosts')
    if not isinstance(hosts, list) or 'github.com' not in hosts or (not all((isinstance(h, str) and h.endswith(('github.com', 'githubusercontent.com')) for h in hosts))):
        raise ValueError("integrations.json hosts: GitHub's own hosts only")
    seen: set[str] = set()
    for item in data.get('integrations') or []:
        where = f"integrations.json {item.get('domain')!r}"
        checks = ((_DOMAIN, item.get('domain')), (_REPO, item.get('repo')), (VERSION_RE, item.get('version')), (_ASSET, item.get('asset')), (SHA_RE, item.get('sha256')), (VERSION_RE, item.get('min_ha')))
        if not all((isinstance(v, str) and rx.match(v) for rx, v in checks)):
            raise ValueError(f'{where}: domain, repo, version, asset, sha256 and min_ha')
        zp = item.get('zip_path')
        if not isinstance(zp, str) or zp.startswith('/') or '..' in zp.split('/') or ('\\' in zp):
            raise ValueError(f'{where}: zip_path a folder inside the archive ("" = its root)')
        if not isinstance(item.get('store'), dict) or not isinstance(item['store'].get('category'), str):
            raise ValueError(f'{where}: store.category')
        if item['domain'] in seen:
            raise ValueError(f'{where}: twice')
        seen.add(item['domain'])
    if not seen:
        raise ValueError('integrations.json integrations: at least one')
    return data

@functools.cache
def integrations() -> dict[str, Any]:
    """integrations.json, read on first use — off the event loop: async_setup_entry and the config flow preload it in
    the executor."""
    return load()
by_domain = functools.cache(lambda: {i['domain']: i for i in integrations()['integrations']})

def item(domain: Any) -> dict[str, Any] | None:
    return by_domain().get(domain) if isinstance(domain, str) else None

def bare(version: Any) -> str:
    return str(version or '').removeprefix('v')

def ha_ok(min_ha: str) -> bool:
    with contained(_LOGGER, 'Hauflo: HA version %s not comparable with %s — not refused for it', HA_VERSION, min_ha, expect=AwesomeVersionException):
        return AwesomeVersion(HA_VERSION) >= AwesomeVersion(min_ha)
    return True

def folder_version(folder: Path) -> str | None:
    """The version in custom_components/<domain>/manifest.json; "" — the folder is there without a readable one."""
    if not folder.exists():
        return None
    try:
        return bare(json.loads((folder / 'manifest.json').read_text(encoding='utf-8')).get('version'))
    except (OSError, ValueError, AttributeError):
        return ''

async def loaded_version(hass: HomeAssistant, domain: str) -> str | None:
    """The version HA knows of this run: HA reads custom integrations once at its start — files put there later are
    known only after a restart (then this is the restart having happened)."""
    try:
        integration = await async_get_integration(hass, domain)
    except IntegrationNotFound:
        return None
    return bare(integration.version) if integration.version is not None else ''
