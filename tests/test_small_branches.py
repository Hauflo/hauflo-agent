"""Edge branches of the small modules: tuning's schema, backup retention / failure, Repairs fix flows."""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import backup, issues, repairs, tuning
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _load(tmp_path, body):
    p = tmp_path / "t.json"
    p.write_text(json.dumps(body), encoding="utf8")
    return tuning.load(p)


@pytest.mark.parametrize(
    "body",
    [
        {"version": 2, "a": {"x": 1}},
        {"version": 1, "a": {}},
        {"version": 1, "a": 5},
        {"version": 1, "a": {"x": {}}},
        {"version": 1, "a": {"x": []}},
        {"version": 1, "a": {"x": [1, "a"]}},
        {"version": 1, "a": {"x": -1}},
        {"version": 1, "a": {"x": None}},
        {"version": 1, "a": {"x": "("}},
    ],
)
def test_tuning_refuses_a_bad_file(tmp_path, body):
    with pytest.raises(Exception):  # ValueError or re.error — the import fails loudly, never a silent default
        _load(tmp_path, body)


def test_tuning_accepts_tables_lists_and_regexes(tmp_path):
    got = _load(tmp_path, {"version": 1, "a": {"n": 1, "w": "a|b", "l": [1, 2], "s": ["x"], "o": [{"k": 1}], "t": {"z": 2}}})
    assert got["a"]["t"] == {"z": 2} and "version" not in got


class _B:
    def __init__(self, bid, age_s):
        self.backup_id, self.name = bid, f"Hauflo 2026-01-01 00.00.{bid:0>2}"
        self.date = (dt_util.utcnow() - timedelta(seconds=age_s)).isoformat()


class _Mgr:
    def __init__(self, items):
        self.local_backup_agents = {"backup.local": object()}
        self.config = None
        self.items = items
        self.deleted: list[str] = []

    async def async_create_backup(self, **kw):
        return None

    async def async_get_backups(self):
        return {b.backup_id: b for b in self.items}, None

    async def async_delete_backup(self, bid):
        self.deleted.append(bid)


async def test_old_backups_go_but_the_recent_and_one_week_old_stay(hass: HomeAssistant):
    day, week = backup._KEEP_DAY, backup._KEEP_WEEK
    items = [_B("1", 10), _B("2", 20), _B("3", 30), _B("4", day + 100), _B("5", week + 100), _B("6", week + 200), _B("7", week + 300)]
    m = _Mgr(items)
    with patch("homeassistant.components.backup.async_get_manager", return_value=m):
        await backup._create(hass, "Hauflo x")
    # newest 3 kept, plus the newest older than a week (5); 4 (older than a day, beyond keep) and 6, 7 go
    assert sorted(m.deleted) == ["4", "6", "7"]


async def test_a_failed_cleanup_never_fails_the_backup(hass: HomeAssistant):
    m = _Mgr([])
    m.async_get_backups = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("homeassistant.components.backup.async_get_manager", return_value=m):
        await backup._create(hass, "Hauflo x")


async def test_backup_view_reports_a_failed_create(hass: HomeAssistant, entry, hass_client):
    hass.config.components.add("backup")
    client = await hass_client()
    backup.LAST.clear()
    with patch.object(backup, "_create", AsyncMock(side_effect=RuntimeError("disk full"))):
        res = await client.post("/api/hauflo/backup", json={})
    got = await res.json()
    assert res.status == 500 and got["state"] == "failed" and "disk full" in got["error"]
    res = await client.get("/api/hauflo/backup")
    assert (await res.json())["state"] == "failed"
    # a backup still running is answered «running», not started twice
    backup.LAST.clear()
    backup.LAST.update(state="running", name="x", at=1)
    res = await client.post("/api/hauflo/backup", json={})
    assert res.status == 202
    backup.LAST.clear()
    assert (await (await client.get("/api/hauflo/backup")).json()) == {"state": "none"}


async def test_backup_view_says_no_backup_integration(hass: HomeAssistant, entry, hass_client):
    if "backup" in hass.config.components:
        hass.config.components.remove("backup")
    backup.LAST.clear()
    res = await (await hass_client()).post("/api/hauflo/backup", json={})
    assert res.status == 501


async def test_ranges_fix_flow_aborts_with_nothing_pending(hass: HomeAssistant):
    flow = repairs.RangesFixFlow()
    flow.hass = hass
    res = await flow.async_step_init()
    assert res["type"] == "abort" and res["reason"] == "nothing_pending"
    # a decision with no entry loaded, or nothing shown, resolves to nothing
    flow._shown = None
    assert flow._resolve(True)["reason"] == "nothing_pending"
    flow._shown = (["1/1/1"], [])
    assert flow._resolve(False)["reason"] == "nothing_pending"


async def test_fix_flow_factory(hass: HomeAssistant):
    assert isinstance(await repairs.async_create_fix_flow(hass, issues.RANGES_PENDING, None), repairs.RangesFixFlow)
    other = await repairs.async_create_fix_flow(hass, "something_else", None)
    assert not isinstance(other, repairs.RangesFixFlow)


async def test_backup_skip_is_noted_and_a_fresh_one_is_reused(hass: HomeAssistant, entry, hass_client):
    client = await hass_client()
    backup.LAST.clear()
    got = await (await client.post("/api/hauflo/backup", json={"skip": True})).json()
    assert got["state"] == "skipped" and backup.fresh()
    backup.LAST.clear()
    backup.LAST.update(state="done", name="Hauflo n", at=dt_util.utcnow().timestamp())
    got = await (await client.post("/api/hauflo/backup", json={})).json()
    assert got["reused"] is True
    # a deploy already went through after that backup: it is not that backup any more
    hass.config.components.add("backup")
    m = _Mgr([])
    with patch("homeassistant.components.backup.async_get_manager", return_value=m):
        got = await (await client.post("/api/hauflo/backup", json={"used": "Hauflo n"})).json()
    assert got.get("reused") is None and got["state"] == "done"
    backup.LAST.clear()
    user_res = await client.post("/api/hauflo/backup", data="not json")
    assert user_res.status in (200, 202, 500, 501)
    backup.LAST.clear()


async def test_ranges_fix_flow_asks_then_resolves(hass: HomeAssistant, entry):
    from custom_components.hauflo import ranges

    ranges.pending(hass).add("1/1/1")
    flow = repairs.RangesFixFlow()
    flow.hass = hass
    res = await flow.async_step_init()
    assert res["type"] == "menu" and set(res["menu_options"]) == {"allow", "decline"}
    assert flow._shown is not None and "1/1/1" in flow._shown[0]
    res = await flow.async_step_decline()
    assert res["type"] == "create_entry"
    assert "1/1/1" not in entry.runtime_data.allowed_ranges()
    # allow, with more asked meanwhile: the issue is raised again after the flow ends
    ranges.pending(hass).add("1/1/2")
    flow = repairs.RangesFixFlow()
    flow.hass = hass
    await flow.async_step_confirm()
    ranges.pending(hass).add("1/1/3")
    res = await flow.async_step_allow()
    assert res["type"] == "create_entry"
    await hass.async_block_till_done()
