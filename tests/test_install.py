"""Р59: installing an integration from outside HA's core — only the canon's, only the pinned sha256, only into its own
folder; the restart only by its own explicit request. Adversarial: a wrong hash, a repo not in the canon, a path out of
the folder, a link, a bomb, a redirect to another host, the integrator's own folder, a restart nobody asked for."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.hauflo import install
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

ROOT = Path(__file__).parents[2]
IT = install.item("yandex_smart_home")
DL = install.CANON["download"].format(repo=IT["repo"], version=IT["version"], asset=IT["asset"])
BARE = IT["version"].removeprefix("v")


def make_zip(files: dict[str, bytes | str], *, link: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        if link:
            info = zipfile.ZipInfo(link)
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, "/etc/passwd")
    return buf.getvalue()


def manifest(domain: str = "yandex_smart_home", version: str = BARE) -> str:
    return json.dumps({"domain": domain, "version": version})


GOOD = make_zip({"manifest.json": manifest(), "__init__.py": "", "sub/x.py": "x = 1"})
GOOD_IT = {**IT, "sha256": hashlib.sha256(GOOD).hexdigest()}


@pytest.fixture
async def entry(hass: HomeAssistant, tmp_path):
    async def idle(self):
        return None

    e = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    e.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(e.entry_id)
        await hass.async_block_till_done()
    hass.config.config_dir = str(tmp_path)
    hass.data.pop(install.DATA, None)
    return e


@pytest.fixture
def canon_sha(monkeypatch):
    """The fixture zip stands for the release: its hash is the canon's for these tests."""
    monkeypatch.setitem(install._BY_DOMAIN, "yandex_smart_home", GOOD_IT)


@pytest.fixture
def not_loaded(monkeypatch):
    async def none(hass, domain):
        return None

    monkeypatch.setattr(install, "_loaded_version", none)


def dest(hass: HomeAssistant) -> Path:
    return Path(hass.config.path("custom_components", "yandex_smart_home"))


# --- the canon ---------------------------------------------------------------------------------------------------


def test_canon_is_the_backends_byte_for_byte():
    assert (ROOT / "agent/custom_components/hauflo/integrations.json").read_bytes() == (ROOT / "backend/src/ha/integrations.json").read_bytes()


def test_canon_pins_github_only():
    assert install.CANON["download"].startswith("https://github.com/")
    for it in install.CANON["integrations"]:
        assert len(it["sha256"]) == 64 and it["version"].startswith("v")


@pytest.mark.parametrize(
    "change",
    [
        {"version": 2},
        {"download": "https://evil.example/{repo}/{version}/{asset}"},
        {"download": "https://github.com/{repo}"},
        {"hosts": ["evil.example", "github.com"]},
        {"hosts": "github.com"},
        {"integrations": []},
        {"integrations": [{**IT, "sha256": "abc"}]},
        {"integrations": [{**IT, "repo": "../x"}]},
        {"integrations": [{**IT, "asset": "x.tar.gz"}]},
        {"integrations": [{**IT, "zip_path": "../x"}]},
        {"integrations": [{**IT, "zip_path": "/abs"}]},
        {"integrations": [{**IT, "store": {}}]},
        {"integrations": [IT, IT]},
    ],
)
def test_bad_canon_fails_loudly(tmp_path, change):
    data = {**json.loads(install.FILE.read_text(encoding="utf-8")), **change}
    f = tmp_path / "integrations.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        install.load(f)


def test_versions():
    assert install._ha_ok("2000.1.0") and not install._ha_ok("2999.1.0")
    with patch("awesomeversion.AwesomeVersion.__ge__", side_effect=Exception("x")):
        assert install._ha_ok("2000.1.0")


# --- unpacking: only into its own folder ------------------------------------------------------------------------


def test_unpack_root_and_backup(tmp_path):
    d, w = tmp_path / "cc" / "yandex_smart_home", tmp_path / "work"
    assert install.unpack(GOOD, IT, d, w, False) is None
    assert (d / "sub/x.py").read_text() == "x = 1" and install._folder_version(d) == BARE
    # Hauflo's own folder replaced: the old one kept, only the last few.
    (d / "old.txt").write_text("old")
    names = []
    for _ in range(install._KEEP + 2):
        names.append(install.unpack(GOOD, IT, d, w, True))
    kept = sorted(p.name for p in (w / "backups").iterdir())
    assert len(kept) == install._KEEP and not (d / "old.txt").exists()
    assert set(kept) <= set(names)


def test_unpack_zip_path(tmp_path):
    z = make_zip({"custom_components/yandex_smart_home/manifest.json": manifest(), "custom_components/yandex_smart_home/a.py": "", "README.md": "skip"})
    d = tmp_path / "cc" / "yandex_smart_home"
    install.unpack(z, {**IT, "zip_path": "custom_components/yandex_smart_home"}, d, tmp_path / "w", False)
    assert sorted(p.name for p in d.iterdir()) == ["a.py", "manifest.json"]


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (make_zip({"manifest.json": manifest(), "../evil.py": ""}), "unsafe_path"),
        (make_zip({"manifest.json": manifest(), "a/../../evil.py": ""}), "unsafe_path"),
        (make_zip({"manifest.json": manifest(), "/etc/evil": ""}), "unsafe_path"),
        (make_zip({"manifest.json": manifest(), "a\\..\\evil": ""}), "unsafe_path"),
        (make_zip({"manifest.json": manifest(), "C:/evil": ""}), "unsafe_path"),
        (make_zip({"manifest.json": manifest()}, link="link.py"), "unsafe_path"),
        (make_zip({"manifest.json": manifest("other")}), "manifest"),
        (make_zip({"manifest.json": manifest(version="0.0.1")}), "manifest"),
        (make_zip({"manifest.json": "{not json"}), "manifest"),
        (make_zip({"a.py": ""}), "manifest"),
        (b"not a zip at all", "not_zip"),
    ],
)
def test_unpack_refuses(tmp_path, data, reason):
    d = tmp_path / "cc" / "yandex_smart_home"
    with pytest.raises(install.Refused, match=f"^{reason}$"):
        install.unpack(data, IT, d, tmp_path / "w", False)
    assert not d.exists() and not (tmp_path / "evil.py").exists()
    assert not (tmp_path / "w" / "yandex_smart_home.new").exists()


def test_unpack_caps(tmp_path, monkeypatch):
    d = tmp_path / "cc" / "yandex_smart_home"
    monkeypatch.setattr(install, "_MAX_MEMBERS", 2)
    with pytest.raises(install.Refused, match="too_many_files"):
        install.unpack(GOOD, IT, d, tmp_path / "w", False)
    monkeypatch.setattr(install, "_MAX_MEMBERS", 100)
    monkeypatch.setattr(install, "_MAX_UNPACKED", 10)
    with pytest.raises(install.Refused, match="too_big"):
        install.unpack(make_zip({"manifest.json": manifest(), "big": "x" * 1000}), IT, d, tmp_path / "w", False)
    assert not d.exists()


def test_unpack_never_over_a_folder_not_its_own(tmp_path):
    d = tmp_path / "cc" / "yandex_smart_home"
    d.mkdir(parents=True)
    (d / "mine.py").write_text("integrator's")
    with pytest.raises(install.Refused, match="exists"):
        install.unpack(GOOD, IT, d, tmp_path / "w", False)
    assert (d / "mine.py").read_text() == "integrator's"


def test_unpack_swap_failure_puts_the_old_one_back(tmp_path):
    d, w = tmp_path / "cc" / "yandex_smart_home", tmp_path / "w"
    install.unpack(GOOD, IT, d, w, False)
    (d / "marker").write_text("1")
    real = Path.rename
    calls = []

    def rename(self, target):
        calls.append(self.name)
        if self.name.endswith(".new"):
            raise OSError("disk")
        return real(self, target)

    with patch.object(Path, "rename", rename), pytest.raises(OSError):
        install.unpack(GOOD, IT, d, w, True)
    assert (d / "marker").read_text() == "1" and not (w / "yandex_smart_home.new").exists()


def test_unpack_stale_stage_and_written_size(tmp_path):
    d, w = tmp_path / "cc" / "yandex_smart_home", tmp_path / "w"
    (w / "yandex_smart_home.new").mkdir(parents=True)
    (w / "yandex_smart_home.new" / "junk").write_text("x")
    install.unpack(GOOD, IT, d, w, False)
    assert not (d / "junk").exists()
    # A member whose header says less than it holds is stopped at what it said.
    real = zipfile.ZipFile.open

    def opened(self, info, *a, **k):
        f = real(self, info, *a, **k)
        if getattr(info, "filename", "") == "sub/x.py":
            info.file_size = 1
        return f

    with patch.object(zipfile.ZipFile, "open", opened), pytest.raises(install.Refused, match="too_big"):
        install.unpack(GOOD, IT, tmp_path / "cc2" / "yandex_smart_home", w, False)


def test_folder_version(tmp_path):
    assert install._folder_version(tmp_path / "none") is None
    (tmp_path / "f").mkdir()
    assert install._folder_version(tmp_path / "f") == ""
    (tmp_path / "f" / "manifest.json").write_text("[]")
    assert install._folder_version(tmp_path / "f") == ""


# --- the download --------------------------------------------------------------------------------------------------


async def test_download_follows_github_only(hass: HomeAssistant, aioclient_mock):
    hop = "https://release-assets.githubusercontent.com/x/asset.zip"
    aioclient_mock.get(DL, status=302, headers={"Location": hop})
    aioclient_mock.get(hop, content=GOOD)
    assert await install._download(hass, IT) == GOOD


@pytest.mark.parametrize(
    ("hop", "reason"),
    [("https://evil.example/asset.zip", "host"), ("http://objects.githubusercontent.com/a.zip", "host")],
)
async def test_download_refuses_other_hosts(hass: HomeAssistant, aioclient_mock, hop, reason):
    aioclient_mock.get(DL, status=302, headers={"Location": hop})
    with pytest.raises(install.Refused, match=reason):
        await install._download(hass, IT)


async def test_download_errors(hass: HomeAssistant, aioclient_mock, monkeypatch):
    aioclient_mock.get(DL, status=404)
    with pytest.raises(install.Refused, match="download_404"):
        await install._download(hass, IT)
    aioclient_mock.clear_requests()
    aioclient_mock.get(DL, status=302, headers={"Location": DL})
    with pytest.raises(install.Refused, match="redirects"):
        await install._download(hass, IT)
    aioclient_mock.clear_requests()
    aioclient_mock.get(DL, exc=TimeoutError())
    with pytest.raises(install.Refused, match="^download$"):
        await install._download(hass, IT)
    aioclient_mock.clear_requests()
    monkeypatch.setattr(install, "_MAX_DOWNLOAD", 10)
    aioclient_mock.get(DL, content=GOOD)
    with pytest.raises(install.Refused, match="too_big"):
        await install._download(hass, IT)
    aioclient_mock.clear_requests()
    aioclient_mock.get(DL, content=GOOD, headers={"Content-Length": "999999"})
    with pytest.raises(install.Refused, match="too_big"):
        await install._download(hass, IT)


# --- the view, end to end ------------------------------------------------------------------------------------------


async def test_github_install_then_restart_only_on_request(hass: HomeAssistant, aioclient_mock, hass_client, entry, canon_sha, not_loaded):
    restarts = async_mock_service(hass, "homeassistant", "restart")
    aioclient_mock.get(DL, content=GOOD)
    client = await hass_client()
    res = await client.post("/api/hauflo/install", json={"domain": "yandex_smart_home"})
    assert res.status == 200
    job = await res.json()
    assert job["state"] == "restart_required" and job["source"] == "github"
    assert job["steps"] == ["downloading", "verifying", "installed", "restart_required"]
    assert install._folder_version(dest(hass)) == BARE
    await hass.async_block_till_done()
    assert restarts == []  # installed, never restarted by itself
    # A second click asks for nothing again.
    assert (await (await client.post("/api/hauflo/install", json={"domain": "yandex_smart_home"})).json())["state"] == "restart_required"
    assert len(aioclient_mock.mock_calls) == 1
    # The restart wants its own explicit confirm.
    assert (await client.post("/api/hauflo/install/restart", json={"domain": "yandex_smart_home"})).status == 400
    assert (await client.post("/api/hauflo/install/restart", json={"domain": "yandex_smart_home", "confirm": "yes"})).status == 400
    await hass.async_block_till_done()
    assert restarts == []
    res = await client.post("/api/hauflo/install/restart", json={"domain": "yandex_smart_home", "confirm": True})
    assert res.status == 202 and (await res.json())["state"] == "restarting"
    await hass.async_block_till_done()
    assert len(restarts) == 1
    got = await (await client.get("/api/hauflo/install")).json()
    assert got["feature"] == install.FEATURE and got["installs"][0]["state"] == "restarting"
    # HA back: the new run knows it.
    async def loaded(hass, domain):
        return BARE

    hass.data.pop(install.DATA)
    with patch.object(install, "_loaded_version", loaded):
        await install.installer(hass).async_load()
    assert install.installer(hass).job("yandex_smart_home")["state"] == "restarted"
    res = await client.post("/api/hauflo/install", json={"domain": "yandex_smart_home", "op": "configured"})
    assert (await res.json())["steps"][-1] == "configured"
    assert (await (await client.post("/api/hauflo/install", json={"domain": "yandex_smart_home", "op": "configured"})).json())["state"] == "configured"
    # Nothing to restart for any more.
    assert (await client.post("/api/hauflo/install/restart", json={"domain": "yandex_smart_home", "confirm": True})).status == 409


async def test_wrong_hash_is_refused(hass: HomeAssistant, aioclient_mock, hass_client, entry, not_loaded):
    aioclient_mock.get(DL, content=GOOD)  # the canon's real hash isn't this zip's
    client = await hass_client()
    job = await (await client.post("/api/hauflo/install", json={"domain": "yandex_smart_home"})).json()
    assert job["state"] == "failed:checksum" and not dest(hass).exists()
    assert (await client.post("/api/hauflo/install/restart", json={"domain": "yandex_smart_home", "confirm": True})).status == 409


async def test_not_in_canon_and_rights(hass: HomeAssistant, hass_client, aioclient_mock, hass_read_only_access_token, entry):
    client = await hass_client()
    for body in ({"domain": "hacs"}, {"domain": "../x"}, {}, {"domain": 5}):
        assert (await client.post("/api/hauflo/install", json=body)).status == 404
        assert (await client.post("/api/hauflo/install/restart", json={**body, "confirm": True})).status == 404
    res = await client.post("/api/hauflo/install", json={"domain": "yandex_smart_home", "repo": "evil/yandex_smart_home"})
    assert res.status == 403 and (await res.json())["code"] == "not_in_canon"
    assert (await client.post("/api/hauflo/install/restart", json={"domain": "yandex_smart_home", "repo": "evil/x", "confirm": True})).status == 403
    assert (await client.post("/api/hauflo/install", json={"domain": "yandex_smart_home", "op": "rm"})).status == 400
    assert (await client.post("/api/hauflo/install", data="not json")).status == 404
    assert (await client.post("/api/hauflo/install", json={"domain": "yandex_smart_home", "op": "configured"})).status == 409
    user = await hass_client(hass_read_only_access_token)
    assert (await user.post("/api/hauflo/install", json={"domain": "yandex_smart_home"})).status == 403
    assert (await user.post("/api/hauflo/install/restart", json={"domain": "yandex_smart_home", "confirm": True})).status == 403
    assert aioclient_mock.mock_calls == []


async def test_ha_too_old(hass: HomeAssistant, hass_client, entry, monkeypatch):
    monkeypatch.setitem(install._BY_DOMAIN, "yandex_smart_home", {**IT, "min_ha": "2999.1.0"})
    res = await (await hass_client()).post("/api/hauflo/install", json={"domain": "yandex_smart_home"})
    assert res.status == 409 and (await res.json())["code"] == "ha_too_old"


async def test_integrators_own_folder_is_never_touched(hass: HomeAssistant, aioclient_mock, hass_client, entry, canon_sha, not_loaded):
    d = dest(hass)
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(manifest(version="0.9.0"))
    job = await (await (await hass_client()).post("/api/hauflo/install", json={"domain": "yandex_smart_home"})).json()
    assert job["state"] == "failed:exists" and aioclient_mock.mock_calls == []
    assert json.loads((d / "manifest.json").read_text())["version"] == "0.9.0"


async def test_same_version_present_and_loaded(hass: HomeAssistant, aioclient_mock, entry):
    d = dest(hass)
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(manifest())

    async def loaded(hass, domain):
        return BARE

    with patch.object(install, "_loaded_version", loaded):
        job = await install.installer(hass).start("yandex_smart_home")
    assert job["state"] == "restarted" and job["source"] == "present" and aioclient_mock.mock_calls == []


async def test_hauflos_own_older_install_is_upgraded(hass: HomeAssistant, aioclient_mock, entry, canon_sha, not_loaded):
    inst = install.installer(hass)
    install.unpack(make_zip({"manifest.json": manifest(version="1.0.0")}), {**IT, "version": "v1.0.0"}, dest(hass), inst._work(), False)
    inst.jobs["yandex_smart_home"] = {"version": "v1.0.0", "source": "github", "state": "restarted", "steps": []}
    aioclient_mock.get(DL, content=GOOD)
    job = await inst.start("yandex_smart_home")
    assert job["state"] == "restart_required" and job["backup"] and job["owned"]
    assert install._folder_version(dest(hass)) == BARE


async def test_failed_store_error_and_interrupted(hass: HomeAssistant, entry, not_loaded):
    inst = install.installer(hass)
    with patch.object(install, "_download", side_effect=RuntimeError("boom")):
        job = await inst.start("yandex_smart_home")
    assert job["state"] == "failed:error" and job["error"] == "boom"
    # Saved; a restart in the middle of an install is said as such.
    await inst.store.async_save({"jobs": {"yandex_smart_home": {"state": "verifying", "steps": []}, "not_canon": {"state": "x"}}})
    hass.data.pop(install.DATA)
    await install.installer(hass).async_load()
    assert [j["domain"] for j in install.installer(hass).items()] == ["yandex_smart_home"]
    assert install.installer(hass).job("yandex_smart_home")["state"] == "failed:interrupted"
    # A restart after which HA doesn't know it.
    await inst.store.async_save({"jobs": {"yandex_smart_home": {"state": "restarting", "version": IT["version"], "steps": []}}})
    hass.data.pop(install.DATA)
    await install.installer(hass).async_load()
    assert install.installer(hass).job("yandex_smart_home")["state"] == "failed:not_loaded"
    # Waiting for its restart and still not restarted: unchanged.
    await inst.store.async_save({"jobs": {"yandex_smart_home": {"state": "restart_required", "version": IT["version"], "steps": []}}})
    hass.data.pop(install.DATA)
    await install.installer(hass).async_load()
    assert install.installer(hass).job("yandex_smart_home")["state"] == "restart_required"
    assert install.installer(hass).job("other") == {"domain": "other", "state": "none", "steps": []}


async def test_slow_install_answers_running(hass: HomeAssistant, entry, monkeypatch, not_loaded):
    import asyncio

    gate = asyncio.Event()

    async def slow(hass, it):
        await gate.wait()
        raise install.Refused("download")

    monkeypatch.setattr(install, "_WAIT", 0)
    monkeypatch.setattr(install, "_download", slow)
    inst = install.installer(hass)
    assert (await inst.start("yandex_smart_home"))["state"] == "downloading"
    assert (await inst.start("yandex_smart_home"))["state"] == "downloading"  # joined, not a second one
    gate.set()
    await hass.async_block_till_done()
    assert inst.job("yandex_smart_home")["state"] == "failed:download"


# --- the stores ----------------------------------------------------------------------------------------------------


class _Repo:
    def __init__(self, hass, version=BARE):
        self.hass, self.version, self.calls = hass, version, []

    def _write(self):
        d = dest(self.hass)
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(manifest(version=self.version))

    async def async_install_repository(self, *, ref=None, **_):
        self.calls.append(ref)
        await self.hass.async_add_executor_job(self._write)

    async def async_download_repository(self, *, ref=None, **_):
        self.calls.append(ref)
        await self.hass.async_add_executor_job(self._write)


class _Repos:
    def __init__(self, repo):
        self.repo = repo

    def get_by_full_name(self, name):
        return self.repo if name == IT["repo"] else None


async def test_marketplace_first(hass: HomeAssistant, aioclient_mock, entry, not_loaded):
    repo = _Repo(hass)
    m = MockConfigEntry(domain="marketplace")
    m.add_to_hass(hass)
    m.runtime_data = SimpleNamespace(repositories=_Repos(repo))
    hass.config.components.add("marketplace")
    hass.config.components.add("hacs")
    hass.data["hacs"] = SimpleNamespace(repositories=_Repos(_Repo(hass)))
    job = await install.installer(hass).start("yandex_smart_home")
    assert job["source"] == "marketplace" and repo.calls == [IT["version"]] and job["state"] == "restart_required"
    assert aioclient_mock.mock_calls == []


async def test_marketplace_without_it_then_hacs_registers(hass: HomeAssistant, aioclient_mock, entry, not_loaded):
    m = MockConfigEntry(domain="marketplace")
    m.add_to_hass(hass)
    m.runtime_data = SimpleNamespace(repositories=_Repos(None))
    hass.config.components.add("marketplace")
    repo = _Repo(hass)
    known = _Repos(None)
    registered = []

    async def register(name, category):
        registered.append((name, category))
        known.repo = repo

    hass.config.components.add("hacs")
    hass.data["hacs"] = SimpleNamespace(repositories=known, async_register_repository=register)
    job = await install.installer(hass).start("yandex_smart_home")
    assert job["source"] == "hacs" and registered == [(IT["repo"], "integration")] and repo.calls == [IT["version"]]
    assert aioclient_mock.mock_calls == []


async def test_store_wrote_another_version(hass: HomeAssistant, entry, not_loaded):
    hass.config.components.add("hacs")
    hass.data["hacs"] = SimpleNamespace(repositories=_Repos(_Repo(hass, version="0.0.1")))
    job = await install.installer(hass).start("yandex_smart_home")
    assert job["state"] == "failed:version"


async def test_hacs_cannot_register_falls_to_github(hass: HomeAssistant, aioclient_mock, entry, canon_sha, not_loaded):
    async def register(name, category):
        return None

    hass.config.components.add("hacs")
    hass.data["hacs"] = SimpleNamespace(repositories=_Repos(None), async_register_repository=register)
    hass.config.components.add("marketplace")  # no entry of it loaded
    aioclient_mock.get(DL, content=GOOD)
    job = await install.installer(hass).start("yandex_smart_home")
    assert job["source"] == "github"
    hass.data["hacs"] = None
    assert await install._via_hacs(hass, IT) is False


async def test_loaded_version_reads_the_loader(hass: HomeAssistant):
    assert await install._loaded_version(hass, "no_such_integration_p59") is None
    assert await install._loaded_version(hass, DOMAIN) not in (None, "")
    with patch("homeassistant.loader.async_get_integration", return_value=SimpleNamespace(version=None)):
        assert await install._loaded_version(hass, "x") == ""


async def test_status_tells_installs_and_feature(hass: HomeAssistant, entry):
    from custom_components import hauflo

    assert install.FEATURE in hauflo.FEATURES
    assert hauflo._http_changes("POST", "/api/hauflo/install") and hauflo._http_changes("POST", "/api/hauflo/install/restart")
    assert not hauflo._http_changes("GET", "/api/hauflo/install")


def test_unpack_skips_folders_and_other_paths(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("pkg/", "")
        zf.writestr("pkg/manifest.json", manifest())
        zf.writestr("pkg/sub/", "")
        zf.writestr("other/evil.py", "")
    d = tmp_path / "cc" / "yandex_smart_home"
    install.unpack(buf.getvalue(), {**IT, "zip_path": "pkg"}, d, tmp_path / "w", False)
    assert sorted(p.name for p in d.iterdir()) == ["manifest.json"]


async def test_start_refuses_outside_the_canon(hass: HomeAssistant):
    with pytest.raises(install.Refused, match="not_in_canon"):
        await install.installer(hass).start("hacs")
