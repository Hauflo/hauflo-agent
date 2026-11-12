"""Р59: an integration from outside HA's core (Yandex Smart Home for Alice first) installed on the integrator's click,
as Hauflo already sets up KNX — only the ones in integrations.json, each pinned to a release and its sha256.

The way, the first one this HA has: HA's own Marketplace (2026.11+), then HACS, else the release straight from GitHub —
checked against the pinned sha256, unpacked only into custom_components/<domain> (no path out of it, size and file count
capped, the old folder kept as a backup, swapped in one rename). Nothing is overwritten that Hauflo didn't install
(another version there is the integrator's own: «exists»). HA loads a new integration only after a restart, and the
restart is never automatic: a separate POST (/install/restart, {"confirm": true}) the integrator's explicit «Перезапустить»
sends. After it the cloud sets the integration up (alisa.ts) and says so here («configured»).

What each one went through is kept (HaufloStore) and told in the agent's status («installs»): downloading → verifying →
installed → restart_required → restarting → restarted → configured, or failed:<reason>."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import shutil
import stat
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .storage import HaufloStore
from .tuning import TUNING

_LOGGER = logging.getLogger(__name__)

FILE = Path(__file__).with_name("integrations.json")
FEATURE = "install_integration"
STORE_KEY = "hauflo.install"
DATA = "hauflo_installer"
_T = TUNING["install"]
_WAIT = _T["wait_s"]  # the tunnel's request lives 30 s: «downloading» is answered before, the rest is read by GET
_TIMEOUT = _T["download_timeout_s"]
_MAX_DOWNLOAD = _T["max_download_bytes"]
_MAX_UNPACKED = _T["max_unpacked_bytes"]
_MAX_MEMBERS = _T["max_members"]
_MAX_REDIRECTS = _T["max_redirects"]
_KEEP = _T["keep_backups"]

RUNNING = ("downloading", "verifying")
SOURCES = ("marketplace", "hacs", "github")  # what Hauflo itself put there (not «present»: found so)
DONE = ("installed", "restart_required", "restarting", "restarted", "configured")
# Where Hauflo keeps its unpacking and the folders it replaced — next to custom_components (one filesystem: the swap
# is a rename), never inside it (HA would take a copy there for an integration).
WORK_DIR = (".hauflo", "install")

_DOMAIN = re.compile(r"^[a-z0-9_]+$")
_REPO = re.compile(r"^(?!\.)[A-Za-z0-9_.-]+/(?!\.)[A-Za-z0-9_.-]+$")
_ASSET = re.compile(r"^[A-Za-z0-9_.-]+\.zip$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^v?\d+(\.\d+)*$")


class Refused(Exception):
    """Why an install stops — the reason is the status' «failed:<reason>»."""


def load(path: Path = FILE) -> dict[str, Any]:
    """The canon, checked when the agent loads: a bad file is a bug of the release and fails loudly."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError("integrations.json: version 1 expected")
    url = data.get("download")
    if not isinstance(url, str) or not url.startswith("https://github.com/") or not all(k in url for k in ("{repo}", "{version}", "{asset}")):
        raise ValueError("integrations.json download: an https://github.com/ address with {repo}, {version} and {asset}")
    hosts = data.get("hosts")
    if not isinstance(hosts, list) or "github.com" not in hosts or not all(isinstance(h, str) and h.endswith(("github.com", "githubusercontent.com")) for h in hosts):
        raise ValueError("integrations.json hosts: GitHub's own hosts only")
    seen: set[str] = set()
    for item in data.get("integrations") or []:
        where = f"integrations.json {item.get('domain')!r}"
        checks = (
            (_DOMAIN, item.get("domain")),
            (_REPO, item.get("repo")),
            (_VERSION, item.get("version")),
            (_ASSET, item.get("asset")),
            (_SHA, item.get("sha256")),
            (_VERSION, item.get("min_ha")),
        )
        if not all(isinstance(v, str) and rx.match(v) for rx, v in checks):
            raise ValueError(f"{where}: domain, repo, version, asset, sha256 and min_ha")
        zp = item.get("zip_path")
        if not isinstance(zp, str) or zp.startswith("/") or ".." in zp.split("/") or "\\" in zp:
            raise ValueError(f"{where}: zip_path a folder inside the archive (\"\" = its root)")
        if not isinstance(item.get("store"), dict) or not isinstance(item["store"].get("category"), str):
            raise ValueError(f"{where}: store.category")
        if item["domain"] in seen:
            raise ValueError(f"{where}: twice")
        seen.add(item["domain"])
    if not seen:
        raise ValueError("integrations.json integrations: at least one")
    return data


CANON = load()
_BY_DOMAIN = {i["domain"]: i for i in CANON["integrations"]}


def item(domain: Any) -> dict[str, Any] | None:
    return _BY_DOMAIN.get(domain) if isinstance(domain, str) else None


def _bare(version: Any) -> str:
    return str(version or "").removeprefix("v")


def _ha_ok(min_ha: str) -> bool:
    from awesomeversion import AwesomeVersion  # noqa: PLC0415 — HA's own dependency

    try:
        return AwesomeVersion(HA_VERSION) >= AwesomeVersion(min_ha)
    except Exception:  # noqa: BLE001 — a version HA itself can't compare: not refused for it
        return True


def _folder_version(folder: Path) -> str | None:
    """The version in custom_components/<domain>/manifest.json; "" — the folder is there without a readable one."""
    if not folder.exists():
        return None
    try:
        return _bare(json.loads((folder / "manifest.json").read_text(encoding="utf-8")).get("version"))
    except (OSError, ValueError, AttributeError):
        return ""


async def _loaded_version(hass: HomeAssistant, domain: str) -> str | None:
    """The version HA knows of this run: HA reads custom integrations once at its start — files put there later are
    known only after a restart (then this is the restart having happened)."""
    from homeassistant.loader import IntegrationNotFound, async_get_integration  # noqa: PLC0415

    try:
        integration = await async_get_integration(hass, domain)
    except IntegrationNotFound:
        return None
    return _bare(integration.version) if integration.version is not None else ""


# --- the release straight from GitHub ----------------------------------------------------------------------------


async def _download(hass: HomeAssistant, it: dict[str, Any]) -> bytes:
    """The pinned asset, every hop of its redirects on GitHub's own hosts (https only), capped in size."""
    return await fetch(hass, CANON["download"].format(repo=it["repo"], version=it["version"], asset=it["asset"]), set(CANON["hosts"]))


async def fetch(hass: HomeAssistant, url: str, hosts: set[str]) -> bytes:
    """A file, every hop of its redirects on these hosts (https only), capped in size — install's and self_update's."""
    session = async_get_clientsession(hass)
    for _ in range(_MAX_REDIRECTS + 1):
        u = urlparse(url)
        if u.scheme != "https" or u.hostname not in hosts:
            raise Refused("host")
        try:
            async with session.get(url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=_TIMEOUT)) as res:
                if res.status in (301, 302, 303, 307, 308) and res.headers.get("Location"):
                    url = urljoin(url, res.headers["Location"])
                    continue
                if res.status != 200:
                    raise Refused(f"download_{res.status}")
                if int(res.headers.get("Content-Length") or 0) > _MAX_DOWNLOAD:
                    raise Refused("too_big")
                buf = bytearray()
                async for chunk in res.content.iter_chunked(65536):
                    buf += chunk
                    if len(buf) > _MAX_DOWNLOAD:
                        raise Refused("too_big")
                return bytes(buf)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise Refused("download") from err
    raise Refused("redirects")


def _is_link(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def unpack(data: bytes, it: dict[str, Any], dest: Path, work: Path, replace: bool) -> str | None:
    """Into dest (custom_components/<domain>) only: every member checked first, unpacked beside it, swapped in with one
    rename, the folder it replaces kept in work/backups (the last _KEEP of this domain). → that backup's name."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as err:
        raise Refused("not_zip") from err
    with zf:
        infos = zf.infolist()
        if len(infos) > _MAX_MEMBERS:
            raise Refused("too_many_files")
        prefix = it["zip_path"].strip("/")
        files: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
        total = 0
        for info in infos:
            name = info.filename
            if "\\" in name or name.startswith("/") or _is_link(info):
                raise Refused("unsafe_path")
            parts = tuple(name.split("/"))
            if prefix:
                head = tuple(prefix.split("/"))
                if parts[: len(head)] != head:
                    continue
                parts = parts[len(head) :]
            if info.is_dir() or not parts or parts[-1] == "":
                continue
            if any(p in ("", ".", "..") or ":" in p for p in parts):
                raise Refused("unsafe_path")
            total += info.file_size
            if total > _MAX_UNPACKED:
                raise Refused("too_big")
            files.append((info, parts))
        manifest = next((i for i, p in files if p == ("manifest.json",)), None)
        try:
            m = json.loads(zf.read(manifest)) if manifest else {}
        except ValueError:
            m = {}
        if not isinstance(m, dict) or m.get("domain") != it["domain"] or _bare(m.get("version")) != _bare(it["version"]):
            raise Refused("manifest")
        stage = work / f"{it['domain']}.new"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        try:
            root = stage.resolve()
            for info, parts in files:
                target = stage.joinpath(*parts)
                if not target.resolve().is_relative_to(root):  # pragma: no cover — the checks above already; a last guard
                    raise Refused("unsafe_path")
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with zf.open(info) as src, target.open("wb") as out:
                    while chunk := src.read(65536):
                        written += len(chunk)
                        if written > info.file_size:  # the header lied about its size
                            raise Refused("too_big")
                        out.write(chunk)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    backups = work / "backups"
    kept = None
    if dest.exists():
        if not replace:
            shutil.rmtree(stage, ignore_errors=True)
            raise Refused("exists")
        backups.mkdir(parents=True, exist_ok=True)
        kept = f"{it['domain']}-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1000000}"
        dest.rename(backups / kept)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        stage.rename(dest)
    except OSError:
        if kept:
            (backups / kept).rename(dest)  # the one that was there goes back
        shutil.rmtree(stage, ignore_errors=True)
        raise
    if backups.exists():
        old = sorted((p for p in backups.iterdir() if p.name.startswith(f"{it['domain']}-")), key=lambda p: p.name, reverse=True)
        for p in old[_KEEP:]:
            shutil.rmtree(p, ignore_errors=True)
    return kept


# --- the stores HA may have ------------------------------------------------------------------------------------------


async def _via_marketplace(hass: HomeAssistant, it: dict[str, Any]) -> bool:
    """HA's own Marketplace (2026.11+): its install of the pinned tag. False — not here, or the repository not in it."""
    if "marketplace" not in hass.config.components:
        return False
    entries = [e for e in hass.config_entries.async_entries("marketplace") if getattr(e, "runtime_data", None) is not None]
    repos = getattr(entries[0].runtime_data, "repositories", None) if entries else None
    repo = repos.get_by_full_name(it["repo"]) if repos is not None else None
    if repo is None:
        return False
    await repo.async_install_repository(ref=it["version"])
    return True


async def _via_hacs(hass: HomeAssistant, it: dict[str, Any]) -> bool:
    """HACS: its download of the pinned tag (the repository added to it when it doesn't know it yet)."""
    if "hacs" not in hass.config.components:
        return False
    hacs = hass.data.get("hacs")
    repos = getattr(hacs, "repositories", None)
    if repos is None:
        return False
    repo = repos.get_by_full_name(it["repo"])
    if repo is None:
        await hacs.async_register_repository(it["repo"], it["store"]["category"])
        repo = repos.get_by_full_name(it["repo"])
        if repo is None:
            return False
    await repo.async_download_repository(ref=it["version"])
    return True


class Installer:
    """The installs of this HA (one at a time per integration), kept across its restarts."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = HaufloStore(hass, STORE_KEY)
        self.jobs: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def _dest(self, domain: str) -> Path:
        return Path(self.hass.config.path("custom_components", domain))

    def _work(self) -> Path:
        return Path(self.hass.config.path(*WORK_DIR))

    async def async_load(self) -> None:
        """After a restart: the one asked for is known now (restarted) or it isn't (failed:not_loaded); an install the
        restart cut off is failed:interrupted."""
        data = await self.store.async_load()
        jobs = data.get("jobs") if isinstance(data, dict) else None
        self.jobs = {d: j for d, j in (jobs or {}).items() if item(d) and isinstance(j, dict)}
        changed = False
        for domain, job in self.jobs.items():
            state = str(job.get("state"))
            if state in RUNNING:
                self._step(job, "failed:interrupted")
                changed = True
            elif state in ("restart_required", "restarting"):
                version = await _loaded_version(self.hass, domain)
                if version == _bare(job.get("version")):
                    self._step(job, "restarted")
                    changed = True
                elif state == "restarting":
                    self._step(job, "failed:not_loaded")
                    changed = True
        if changed:
            await self._save()

    async def _save(self) -> None:
        await self.store.async_save({"jobs": self.jobs})

    @staticmethod
    def _step(job: dict[str, Any], state: str) -> None:
        job["state"] = state
        job["at"] = time.time()
        job.setdefault("steps", []).append({"step": state, "at": job["at"]})
        del job["steps"][:-20]

    def items(self) -> list[dict[str, Any]]:
        return [{"domain": d, **{k: v for k, v in j.items() if k != "steps"}, "steps": [s["step"] for s in j.get("steps", [])]} for d, j in sorted(self.jobs.items())]

    def job(self, domain: str) -> dict[str, Any]:
        return next((x for x in self.items() if x["domain"] == domain), {"domain": domain, "state": "none", "steps": []})

    async def start(self, domain: str) -> dict[str, Any]:
        """Start (or join) an install; answered within _WAIT seconds, the rest is read by GET."""
        it = item(domain)
        if it is None:
            raise Refused("not_in_canon")
        if not _ha_ok(it["min_ha"]):
            raise Refused("ha_too_old")
        task = self._tasks.get(domain)
        if task is None or task.done():
            job = self.jobs.get(domain) or {}
            if job.get("state") in ("restart_required", "restarting", "restarted", "configured") and job.get("version") == it["version"]:
                return self.job(domain)  # done already: a second click asks for nothing again
            # Hauflo's own folder (it installed it before): a newer pin may replace it; the integrator's own never.
            owned = bool(job.get("owned")) or job.get("source") in SOURCES
            self.jobs[domain] = {"version": it["version"], "repo": it["repo"], "source": None, "owned": owned, "steps": []}
            self._step(self.jobs[domain], "downloading")
            task = self._tasks[domain] = self.hass.async_create_background_task(self._run(it), f"hauflo install {domain}")
        try:
            await asyncio.wait_for(asyncio.shield(task), _WAIT)
        except TimeoutError:
            pass
        return self.job(domain)

    async def _run(self, it: dict[str, Any]) -> None:
        domain = it["domain"]
        job = self.jobs[domain]
        try:
            dest = self._dest(domain)
            have = await self.hass.async_add_executor_job(_folder_version, dest)
            ours = bool(job.get("owned"))
            if have == _bare(it["version"]):
                job["source"] = "present"  # the same version there already (the integrator's or an earlier click's)
            elif have is not None and not ours:
                raise Refused("exists")  # another version is the integrator's own: never overwritten
            elif await _via_marketplace(self.hass, it):
                job["source"] = "marketplace"
            elif await _via_hacs(self.hass, it):
                job["source"] = "hacs"
            else:
                data = await _download(self.hass, it)
                self._step(job, "verifying")
                if hashlib.sha256(data).hexdigest() != it["sha256"]:
                    raise Refused("checksum")
                job["backup"] = await self.hass.async_add_executor_job(unpack, data, it, dest, self._work(), ours)
                job["source"] = "github"
            if job["source"] in ("marketplace", "hacs"):
                self._step(job, "verifying")
                if await self.hass.async_add_executor_job(_folder_version, dest) != _bare(it["version"]):
                    raise Refused("version")
            self._step(job, "installed")
            # Known in this run already (it was there at HA's start): nothing to restart for.
            self._step(job, "restarted" if await _loaded_version(self.hass, domain) == _bare(it["version"]) else "restart_required")
            _LOGGER.info("Hauflo: %s %s installed (%s)", it["repo"], it["version"], job["source"])
        except Refused as err:
            self._step(job, f"failed:{err}")
            _LOGGER.warning("Hauflo: %s not installed — %s", it["repo"], err)
        except Exception as err:  # noqa: BLE001 — a store's own error: said, never a crash of the agent
            job["error"] = str(err)[:300]
            self._step(job, "failed:error")
            _LOGGER.warning("Hauflo: %s not installed", it["repo"], exc_info=True)
        finally:
            job["owned"] = ours_now(job)
            await self._save()

    async def configured(self, domain: str) -> dict[str, Any]:
        job = self.jobs.get(domain)
        if job is None or job.get("state") not in ("restarted", "configured"):
            raise Refused("not_restarted")
        if job["state"] != "configured":
            self._step(job, "configured")
            await self._save()
        return self.job(domain)

    async def restart(self, domain: str) -> dict[str, Any]:
        """HA's own restart (it checks the configuration first and refuses a broken one) — only for an install waiting
        for it, only on this explicit request."""
        job = self.jobs.get(domain)
        if job is None or job.get("state") not in ("restart_required", "restarting"):
            raise Refused("not_needed")
        self._step(job, "restarting")
        await self._save()  # written before HA stops: the next start reads it
        self.hass.async_create_background_task(self.hass.services.async_call("homeassistant", "restart"), "hauflo install restart")
        return self.job(domain)


def ours_now(job: dict[str, Any]) -> bool:
    return bool(job.get("owned")) or job.get("source") in SOURCES


def installer(hass: HomeAssistant) -> Installer:
    inst = hass.data.get(DATA)
    if inst is None:
        inst = hass.data[DATA] = Installer(hass)
    return inst


async def _body(request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _check(body: dict[str, Any]) -> tuple[str | None, Any]:
    """The domain asked for → (domain, None) or (None, a refusal). A repo other than the canon's is refused."""
    domain = body.get("domain")
    it = item(domain)
    if it is None:
        return None, ("not_in_canon", 404)
    if "repo" in body and body["repo"] != it["repo"]:
        return None, ("not_in_canon", 403)
    return domain, None


class InstallView(HomeAssistantView):
    url = "/api/hauflo/install"
    name = "api:hauflo:install"
    requires_auth = True

    async def get(self, request):
        hass: HomeAssistant = request.app[KEY_HASS]
        return self.json({"feature": FEATURE, "canon": sorted(_BY_DOMAIN), "installs": installer(hass).items()})

    async def post(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        hass: HomeAssistant = request.app[KEY_HASS]
        body = await _body(request)
        domain, bad = _check(body)
        if bad:
            return self.json({"code": bad[0]}, bad[1])
        inst = installer(hass)
        try:
            if body.get("op") == "configured":
                return self.json(await inst.configured(domain))
            if body.get("op") not in (None, "install"):
                return self.json({"code": "bad_op"}, 400)
            job = await inst.start(domain)
        except Refused as err:
            return self.json({"code": str(err)}, 409)
        return self.json(job, 202 if job["state"] in RUNNING else 200)


class RestartView(HomeAssistantView):
    url = "/api/hauflo/install/restart"
    name = "api:hauflo:install:restart"
    requires_auth = True

    async def post(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admin only", 403)
        hass: HomeAssistant = request.app[KEY_HASS]
        body = await _body(request)
        domain, bad = _check(body)
        if bad:
            return self.json({"code": bad[0]}, bad[1])
        if body.get("confirm") is not True:  # the integrator's own «Перезапустить», never a default
            return self.json({"code": "confirm_required"}, 400)
        try:
            return self.json(await installer(hass).restart(domain), 202)
        except Refused as err:
            return self.json({"code": str(err)}, 409)
