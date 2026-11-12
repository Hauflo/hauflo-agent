"""0.34 п.8: the agent's own update on the integrator's wave, and the way back. Adversarial: a wrong hash, a mirror with
another version, a downgrade, a restart nobody confirmed, a non-admin, a rollback with nothing to undo, a backup of
another version, a restart that did not load the new code."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch
import zipfile

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.hauflo import install, self_update
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN, VERSION

NEW = ".".join([*VERSION.split(".")[:-1], str(int(VERSION.split(".")[-1]) + 1)])


def make_zip(version: str, domain: str = DOMAIN) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"domain": domain, "version": version}))
        zf.writestr("__init__.py", f"# {version}")
    return buf.getvalue()


GOOD = make_zip(NEW)
SHA = hashlib.sha256(GOOD).hexdigest()
GH, MIRROR_RU, MIRROR_COM = self_update.sources(NEW)


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
    hass.data.pop(self_update.DATA, None)
    # The agent's own folder as it is now (the version running).
    own = tmp_path / "custom_components" / DOMAIN
    own.mkdir(parents=True)
    (own / "manifest.json").write_text(json.dumps({"domain": DOMAIN, "version": VERSION}))
    return e


def own(hass: HomeAssistant) -> Path:
    return Path(hass.config.path("custom_components", DOMAIN))


def test_sources_and_hosts():
    assert GH == f"https://github.com/Hauflo/hauflo-agent/releases/download/v{NEW}/hauflo.zip"
    assert MIRROR_RU == "https://app.hauflo.ru/agent/hauflo.zip" and MIRROR_COM == "https://app.hauflo.com/agent/hauflo.zip"
    assert {"github.com", "app.hauflo.ru", "app.hauflo.com"} <= self_update.hosts()


def test_restore_checks_the_backup(tmp_path):
    dest, work = tmp_path / "cc" / DOMAIN, tmp_path / "w"
    dest.mkdir(parents=True)
    (dest / "manifest.json").write_text(json.dumps({"domain": DOMAIN, "version": NEW}))
    (work / "backups" / "hauflo-1").mkdir(parents=True)
    (work / "backups" / "hauflo-1" / "manifest.json").write_text(json.dumps({"domain": DOMAIN, "version": VERSION}))
    for bad in ("", "../x", ".hidden", "hauflo-missing"):
        with pytest.raises(install.Refused, match="no_backup"):
            self_update.restore(dest, work, bad, VERSION)
    with pytest.raises(install.Refused, match="backup_version"):
        self_update.restore(dest, work, "hauflo-1", "0.0.1")
    undone = self_update.restore(dest, work, "hauflo-1", VERSION)
    assert install._folder_version(dest) == install._bare(VERSION)
    assert install._folder_version(work / "backups" / undone) == NEW


async def test_update_restart_only_confirmed_then_rollback(hass: HomeAssistant, aioclient_mock, hass_client, entry):
    restarts = async_mock_service(hass, "homeassistant", "restart")
    aioclient_mock.get(GH, content=GOOD)
    client = await hass_client()
    got = await (await client.get("/api/hauflo/self_update")).json()
    assert got == {"feature": self_update.FEATURE, "version": install._bare(VERSION), "job": {"state": "none", "steps": []}}
    # A restart without its confirm: refused before anything is downloaded.
    assert (await client.post("/api/hauflo/self_update", json={"op": "update", "version": NEW, "sha256": SHA, "restart": True})).status == 400
    assert aioclient_mock.mock_calls == []
    res = await client.post("/api/hauflo/self_update", json={"op": "update", "version": NEW, "sha256": SHA})
    assert res.status == 200
    job = (await res.json())["job"]
    assert job["state"] == "restart_required" and job["from"] == install._bare(VERSION) and job["to"] == NEW
    assert job["steps"] == ["downloading", "verifying", "restart_required"]
    assert "backup" not in job
    assert install._folder_version(own(hass)) == NEW
    await hass.async_block_till_done()
    assert restarts == []
    # The way back before the restart: the replaced folder in place again.
    res = await client.post("/api/hauflo/self_update", json={"op": "rollback"})
    assert res.status == 200 and (await res.json())["job"]["state"] == "rollback_required"
    assert install._folder_version(own(hass)) == install._bare(VERSION)
    assert (await client.post("/api/hauflo/self_update", json={"op": "rollback"})).status == 409  # nothing more to undo
    assert (await client.post("/api/hauflo/self_update", json={"op": "restart"})).status == 400
    res = await client.post("/api/hauflo/self_update", json={"op": "restart", "confirm": True})
    assert (await res.json())["job"]["state"] == "rolling_back"
    await hass.async_block_till_done()
    assert len(restarts) == 1
    # The old code back after the restart says so.
    hass.data.pop(self_update.DATA)
    await self_update.updater(hass).async_load()
    assert self_update.updater(hass).view()["job"]["state"] == "rolled_back"


async def test_update_with_restart_then_the_new_code_says_updated(hass: HomeAssistant, aioclient_mock, hass_client, entry):
    restarts = async_mock_service(hass, "homeassistant", "restart")
    aioclient_mock.get(GH, content=GOOD)
    client = await hass_client()
    res = await client.post("/api/hauflo/self_update", json={"op": "update", "version": NEW, "sha256": SHA, "restart": True, "confirm": True})
    assert (await res.json())["job"]["state"] == "restarting"
    await hass.async_block_till_done()
    assert len(restarts) == 1
    hass.data.pop(self_update.DATA)
    with patch.object(self_update, "VERSION", NEW):
        await self_update.updater(hass).async_load()
    assert self_update.updater(hass).view()["job"]["state"] == "updated"
    # Health failed in the cloud: back, with a restart.
    res = await client.post("/api/hauflo/self_update", json={"op": "rollback", "restart": True, "confirm": True})
    assert (await res.json())["job"]["state"] == "rolling_back"
    await hass.async_block_till_done()
    assert len(restarts) == 2 and install._folder_version(own(hass)) == install._bare(VERSION)


async def test_restart_that_did_not_load_it(hass: HomeAssistant, aioclient_mock, hass_client, entry):
    async_mock_service(hass, "homeassistant", "restart")
    aioclient_mock.get(GH, content=GOOD)
    client = await hass_client()
    await client.post("/api/hauflo/self_update", json={"op": "update", "version": NEW, "sha256": SHA, "restart": True, "confirm": True})
    await hass.async_block_till_done()
    hass.data.pop(self_update.DATA)
    await self_update.updater(hass).async_load()  # still the old VERSION running
    assert self_update.updater(hass).view()["job"]["state"] == "failed:not_loaded"
    assert (await client.post("/api/hauflo/self_update", json={"op": "rollback"})).status == 200


async def test_mirror_when_github_fails_and_another_version_skipped(hass: HomeAssistant, aioclient_mock, hass_client, entry):
    aioclient_mock.get(GH, status=404)
    aioclient_mock.get(MIRROR_RU, content=make_zip("0.1.0"))  # the mirror's latest is another release
    aioclient_mock.get(MIRROR_COM, content=GOOD)
    client = await hass_client()
    job = (await (await client.post("/api/hauflo/self_update", json={"op": "update", "version": NEW, "sha256": SHA})).json())["job"]
    assert job["state"] == "restart_required"
    assert len(aioclient_mock.mock_calls) == 3


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"op": "update", "version": NEW, "sha256": "x"}, 409, "bad_request"),
        ({"op": "update", "version": "1.0; rm", "sha256": SHA}, 409, "bad_request"),
        ({"op": "update", "version": VERSION, "sha256": SHA}, 409, "not_newer"),
        ({"op": "update", "version": "0.1.0", "sha256": SHA}, 409, "not_newer"),
        ({"op": "rollback"}, 409, "nothing_to_undo"),
        ({"op": "restart", "confirm": True}, 409, "not_needed"),
        ({"op": "evil"}, 400, "bad_op"),
    ],
)
async def test_refusals(hass: HomeAssistant, hass_client, aioclient_mock, entry, body, status, code):
    client = await hass_client()
    res = await client.post("/api/hauflo/self_update", json=body)
    assert res.status == status and (await res.json())["code"] == code
    assert aioclient_mock.mock_calls == []
    assert install._folder_version(own(hass)) == install._bare(VERSION)


async def test_wrong_hash_everywhere(hass: HomeAssistant, aioclient_mock, hass_client, entry):
    for url in (GH, MIRROR_RU, MIRROR_COM):
        aioclient_mock.get(url, content=GOOD)
    client = await hass_client()
    job = (await (await client.post("/api/hauflo/self_update", json={"op": "update", "version": NEW, "sha256": "0" * 64})).json())["job"]
    assert job["state"] == "failed:checksum"
    assert install._folder_version(own(hass)) == install._bare(VERSION)


async def test_wrong_hash_then_mirrors_down_says_checksum(hass: HomeAssistant, aioclient_mock, hass_client, entry):
    """Live on the stand 11.11: GitHub gave a zip that wasn't the release, the mirrors were unreachable — the job said
    «download» (the last source), hiding the cause. The most telling reason wins."""
    aioclient_mock.get(GH, content=GOOD)
    aioclient_mock.get(MIRROR_RU, status=502)
    aioclient_mock.get(MIRROR_COM, exc=aiohttp.ClientError())
    client = await hass_client()
    job = (await (await client.post("/api/hauflo/self_update", json={"op": "update", "version": NEW, "sha256": "0" * 64})).json())["job"]
    assert job["state"] == "failed:checksum"
    assert self_update._telling(["download_502", "too_big", "download"]) == "too_big"
    assert self_update._telling(["download_404", "download"]) == "download"
    assert self_update._telling([]) == "download"


async def test_manifest_of_another_version_refused(hass: HomeAssistant, aioclient_mock, hass_client, entry):
    other = make_zip("9.9.9")
    aioclient_mock.get(GH, content=other)
    client = await hass_client()
    job = (await (await client.post("/api/hauflo/self_update", json={"op": "update", "version": NEW, "sha256": hashlib.sha256(other).hexdigest()})).json())["job"]
    assert job["state"] == "failed:manifest"


async def test_admin_only(hass: HomeAssistant, hass_client, hass_read_only_access_token, aioclient_mock, entry):
    user = await hass_client(hass_read_only_access_token)
    assert (await user.post("/api/hauflo/self_update", json={"op": "update", "version": NEW, "sha256": SHA})).status == 403


async def test_feature_and_backup_first(hass: HomeAssistant, entry):
    from custom_components import hauflo

    assert self_update.FEATURE in hauflo.FEATURES
    assert hauflo._http_changes("POST", "/api/hauflo/self_update") and not hauflo._http_changes("GET", "/api/hauflo/self_update")
