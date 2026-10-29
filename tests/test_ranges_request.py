"""Address ranges as a first-class, house-owned step (integrator review 29.10, defect 4): the cloud only ASKS, the
owner allows or declines in one click in Repairs, and a reconfigure asks for the new project's addresses instead of
keeping the old ones silently."""

from unittest.mock import patch

import pytest
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hauflo as hauflo
from custom_components.hauflo import issues, ranges
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN


async def _idle(self):
    return None


@pytest.fixture
async def entry(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — Старый", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"}, options={"ranges": ["0/0/1", "7/7/1"]})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


def _issue(hass: HomeAssistant):
    return ir.async_get(hass).async_get_issue(DOMAIN, issues.RANGES_PENDING)


def _notes(hass: HomeAssistant) -> dict:
    return persistent_notification._async_get_or_create_notifications(hass)


def test_expand_and_compress_round_trip() -> None:
    assert ranges.compress(ranges.expand(["1/1/1-3", "1/1/4", "1/2", "0/0/9"])) == ["0/0/9", "1/1/1-4", "1/2"]
    assert ranges.count(["1/0/0-255", "2/0/5"]) == 257
    assert ranges.compress(set()) == []


async def test_the_plan_asks_and_nothing_is_granted(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    res = await client.post("/api/hauflo/ranges", json={"project": "Новый", "addresses": ["0/0/1", "1/0/0-255", "1/1/5"]})
    assert res.status == 200
    body = await res.json()
    # What isn't allowed yet waits; «7/7/1» (no address of the new project there) is offered for removal with it.
    assert body["pending"] == ["1/0", "1/1/5"] and body["pendingCount"] == 257 and body["remove"] == ["7/7/1"]
    assert entry.options["ranges"] == ["0/0/1", "7/7/1"]
    assert not entry.runtime_data._outside_ranges({"type": "knx/create_entity", "data": {"knx": {"ga": {"write": "0/0/1"}}}})
    assert entry.runtime_data._outside_ranges({"type": "knx/create_entity", "data": {"knx": {"ga": {"write": "1/0/7"}}}}) == {"1/0/7"}
    issue = _issue(hass)
    assert issue is not None and issue.is_fixable
    assert issue.translation_placeholders == {"ranges": "1/0, 1/1/5", "count": "257", "project": "Новый"}
    assert "1/0, 1/1/5" in _notes(hass)[ranges.NOTIFICATION_ID]["message"]
    # GET says what waits — the plan re-checks it.
    got = await (await client.get("/api/hauflo/ranges")).json()
    assert got["pending"] == ["1/0", "1/1/5"] and got["pendingCount"] == 257
    # Everything already allowed: nothing to ask, the issue goes.
    res = await client.post("/api/hauflo/ranges", json={"addresses": ["0/0/1"]})
    assert (await res.json())["pending"] == [] and _issue(hass) is None


@pytest.mark.parametrize("body", [{"addresses": "1/1/1"}, {"addresses": [1]}, {"addresses": ["1/1/1"] * (ranges.MAX_ITEMS + 1)}, ["x"]])
async def test_a_bad_ask_is_refused(hass: HomeAssistant, hass_client, entry, body) -> None:
    res = await (await hass_client()).post("/api/hauflo/ranges", json=body)
    assert res.status == 400
    assert _issue(hass) is None


def test_the_tunnel_lets_the_ask_through_but_not_when_the_house_is_closed() -> None:
    assert ("/api/hauflo/ranges", "POST") in hauflo._HAUFLO_VIEWS
    assert hauflo._http_locked("POST", "/api/hauflo/ranges")


async def _fix(hass: HomeAssistant, choice: str):
    assert await async_setup_component(hass, "repairs", {})
    await hass.async_block_till_done()
    manager = hass.data["repairs"]["flow_manager"]
    result = await manager.async_init(DOMAIN, data={"issue_id": issues.RANGES_PENDING})
    assert result["type"] is FlowResultType.MENU and result["menu_options"] == ["allow", "decline"]
    assert result["description_placeholders"]["ranges"] == "1/0/1-2"
    assert result["description_placeholders"]["removed"] == "7/7/1"
    assert result["description_placeholders"]["project"] == "Новый"
    return await manager.async_configure(result["flow_id"], {"next_step_id": choice})


async def test_allow_in_one_click_adds_and_removes_exactly_what_was_shown(hass: HomeAssistant, entry) -> None:
    ranges.request(hass, entry.runtime_data.allowed_ranges(), ["0/0/1", "1/0/1", "1/0/2"], "Новый")
    result = await _fix(hass, "allow")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["ranges"] == ["0/0/1", "1/0/1-2"]
    assert _issue(hass) is None and ranges.NOTIFICATION_ID not in _notes(hass)
    assert ranges.state(hass)["pending"] == []


async def test_decline_keeps_everything_and_is_not_asked_again(hass: HomeAssistant, entry) -> None:
    ranges.request(hass, entry.runtime_data.allowed_ranges(), ["0/0/1", "1/0/1", "1/0/2"], "Новый")
    result = await _fix(hass, "decline")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["ranges"] == ["0/0/1", "7/7/1"]
    assert _issue(hass) is None
    st = ranges.request(hass, entry.runtime_data.allowed_ranges(), ["0/0/1", "1/0/1", "1/0/2"], "Новый")
    assert st["pending"] == [] and st["declined"] == ["1/0/1-2"] and _issue(hass) is None


async def test_a_refused_write_waits_in_repairs_too(hass: HomeAssistant, entry) -> None:
    entry.runtime_data._ask_ranges({"5/5/5"})
    issue = _issue(hass)
    assert issue is not None and issue.is_fixable and issue.translation_placeholders["ranges"] == "5/5/5"
    assert "/config/repairs" in _notes(hass)[ranges.NOTIFICATION_ID]["message"]


async def test_what_the_owner_allows_is_what_was_shown(hass: HomeAssistant, entry) -> None:
    """Security: the cloud changing its ask between the form and the click grants nothing new."""
    ranges.request(hass, entry.runtime_data.allowed_ranges(), ["0/0/1", "1/0/1", "1/0/2"], "Новый")
    assert await async_setup_component(hass, "repairs", {})
    manager = hass.data["repairs"]["flow_manager"]
    result = await manager.async_init(DOMAIN, data={"issue_id": issues.RANGES_PENDING})
    assert result["description_placeholders"]["ranges"] == "1/0/1-2"
    # Meanwhile: a gate's address slipped into the ask, and nothing is named for removal any more.
    ranges.request(hass, entry.runtime_data.allowed_ranges(), ["0/0/1", "1/0/1", "1/0/2", "7/7/1", "9/6/9"], "Новый")
    result = await manager.async_configure(result["flow_id"], {"next_step_id": "allow"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Exactly what the form showed: 1/0/1-2 added, 7/7/1 removed; 9/6/9 not granted.
    assert entry.options["ranges"] == ["0/0/1", "1/0/1-2"]
    await hass.async_block_till_done()
    # The rest still waits for its own click, the issue raised again after Repairs closed this one.
    assert ranges.state(hass)["pending"] == ["9/6/9"]
    assert _issue(hass) is not None
