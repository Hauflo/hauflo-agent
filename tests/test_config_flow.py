"""The Hauflo config flow: pairing by code, https only (except a local server), the region in the code picks the
server, and re-authentication of an unlinked agent (HA core review 25.10: tests before quality levels)."""

from unittest.mock import patch

import aiohttp

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN


@pytest.fixture(autouse=True)
def _no_setup():
    with patch("custom_components.hauflo.async_setup_entry", return_value=True):
        yield


async def _start(hass: HomeAssistant, advanced: bool = False):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def _code(hass: HomeAssistant, flow_id: str, data: dict):
    """The code, then the group address ranges as proposed (narrow round 25.10, acceptance B9)."""
    result = await hass.config_entries.flow.async_configure(flow_id, data)
    if result["type"] is FlowResultType.FORM and result.get("step_id") == "ranges":
        result = await hass.config_entries.flow.async_configure(flow_id, {})
    return result


async def test_pairs_with_a_code(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post("https://app.hauflo.ru/api/agent/pair", json={"secret": "s3cr3t", "project": "Вилла Юг"})
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert [str(k) for k in result["data_schema"].schema] == ["code", "advanced"]  # the server folded away
    result = await _code(hass, result["flow_id"], {"code": "ru-abc-def-ghk"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hauflo — Вилла Юг"
    assert result["data"] == {CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s3cr3t"}
    assert aioclient_mock.mock_calls[-1][2] == {"code": "RU-ABC-DEF-GHK"}


async def test_wrong_code(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post("https://app.hauflo.ru/api/agent/pair", status=404, text="<html>not found</html>")
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "RU-XXX-XXX-XXX"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"code": "invalid_code"}


async def test_plain_http_only_to_a_local_server(hass: HomeAssistant, aioclient_mock) -> None:
    result = await _start(hass, advanced=True)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "ABC-DEF-GHK", "advanced": {"server": "http://hauflo.example.com"}})
    assert result["errors"] == {"server": "https_required"}
    assert not aioclient_mock.mock_calls


async def test_the_region_in_the_code_picks_the_server(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post("https://app.hauflo.com/api/agent/pair", json={"secret": "s", "project": "Villa"})
    result = await _start(hass)
    result = await _code(hass, result["flow_id"], {"code": "AE-ABC-DEF-GHK"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SERVER] == "https://app.hauflo.com"


async def test_an_eu_code_goes_to_the_international_server(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post("https://app.hauflo.com/api/agent/pair", json={"secret": "s", "project": "Villa"})
    result = await _start(hass)
    result = await _code(hass, result["flow_id"], {"code": "eu 7kq 4mz 9ta"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SERVER] == "https://app.hauflo.com"
    assert [c[1].host for c in aioclient_mock.mock_calls] == ["app.hauflo.com"]


async def test_a_code_without_region_tries_russia_when_the_international_server_does_not_know_it(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post("https://app.hauflo.com/api/agent/pair", status=404)
    aioclient_mock.post("https://app.hauflo.ru/api/agent/pair", json={"secret": "s", "project": "Дом"})
    result = await _start(hass)
    result = await _code(hass, result["flow_id"], {"code": "7KQ-4MZ-9TA"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SERVER] == "https://app.hauflo.ru"


async def test_plan_b_when_the_international_server_is_blocked(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post("https://app.hauflo.com/api/agent/pair", exc=aiohttp.ClientConnectionError())
    aioclient_mock.post("https://app.hauflo.ru/api/agent/pair", json={"secret": "s", "project": "Дом"})
    result = await _start(hass)
    result = await _code(hass, result["flow_id"], {"code": "7KQ-4MZ-9TA"})
    assert result["data"][CONF_SERVER] == "https://app.hauflo.ru"


async def test_no_plan_b_for_a_wrong_code_anywhere(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post("https://app.hauflo.com/api/agent/pair", status=404)
    aioclient_mock.post("https://app.hauflo.ru/api/agent/pair", status=404)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "7KQ-4MZ-9TA"})
    assert result["errors"] == {"code": "invalid_code"}


async def test_a_regional_code_or_a_typed_server_never_goes_elsewhere(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post("https://app.hauflo.com/api/agent/pair", status=404)
    aioclient_mock.post("https://my.hauflo.example/api/agent/pair", status=404)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "EU-7KQ-4MZ-9TA"})
    assert result["errors"] == {"code": "invalid_code"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "7KQ-4MZ-9TA", "advanced": {"server": "https://my.hauflo.example"}})
    assert result["errors"] == {"code": "invalid_code"}
    assert {c[1].host for c in aioclient_mock.mock_calls} == {"app.hauflo.com", "my.hauflo.example"}


async def test_one_agent_per_home_assistant(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "a"}).add_to_hass(hass)
    result = await _start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reauth_relinks_the_same_entry(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — old", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "old", "user_id": "u1"})
    entry.add_to_hass(hass)
    aioclient_mock.post("https://app.hauflo.ru/api/agent/pair", json={"secret": "new", "project": "Вилла Юг"})
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "RU-ABC-DEF-GHK"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_SECRET] == "new"
    assert entry.data["user_id"] == "u1"  # the agent's own HA user is kept
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1



@pytest.mark.parametrize(
    ("reply", "error"),
    [({"status": 502, "text": "bad gateway"}, "cannot_connect"), ({"json": ["not", "an", "object"]}, "cannot_connect"), ({"json": {"project": "no secret"}}, "cannot_connect"), ({"exc": aiohttp.ClientError()}, "cannot_connect"), ({"text": "<html>"}, "cannot_connect")],
)
async def test_hauflo_unreachable_or_odd(hass: HomeAssistant, aioclient_mock, reply, error) -> None:
    aioclient_mock.post("https://app.hauflo.ru/api/agent/pair", **reply)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "RU-ABC-DEF-GHK"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_a_local_dev_server_by_ip_or_name(hass: HomeAssistant, aioclient_mock) -> None:
    for server in ("http://192.168.1.5:8765", "http://hauflo-dev:8765"):
        aioclient_mock.clear_requests()
        aioclient_mock.post(f"{server}/api/agent/pair", json={"secret": "s", "project": "Dev"})
        result = await _start(hass, advanced=True)
        result = await _code(hass, result["flow_id"], {"code": "ABC-DEF-GHK", "advanced": {"server": server}})
        assert result["type"] is FlowResultType.CREATE_ENTRY, server
        await hass.config_entries.async_remove(result["result"].entry_id)
    result = await _start(hass, advanced=True)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "ABC-DEF-GHK", "advanced": {"server": "ftp://x"}})
    assert result["errors"] == {"server": "https_required"}


async def test_reconfigure_links_another_project(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — A", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "old", "user_id": "u1"})
    entry.add_to_hass(hass)
    aioclient_mock.post("https://app.hauflo.ru/api/agent/pair", json={"secret": "new", "project": "B"})
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "RU-ABC-DEF-GHK"})
    assert result["reason"] == "reconfigure_successful"
    assert entry.title == "Hauflo — B" and entry.data[CONF_SECRET] == "new" and entry.data["user_id"] == "u1"
    # The old secret goes along to the same server: the old project lets this agent go (integrator run 19).
    assert aioclient_mock.mock_calls[-1][2] == {"code": "RU-ABC-DEF-GHK", "previous": "old"}


async def test_reauth_with_a_wrong_code_stays_on_the_form(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "old"})
    entry.add_to_hass(hass)
    aioclient_mock.post("https://app.hauflo.ru/api/agent/pair", status=404)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "RU-XXX"})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"code": "invalid_code"}
    assert entry.data[CONF_SECRET] == "old"


async def test_reconfigure_from_dubai_with_a_russian_code_goes_to_russia(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — Villa", data={CONF_SERVER: "https://app.hauflo.com", CONF_SECRET: "old"})
    entry.add_to_hass(hass)
    aioclient_mock.post("https://app.hauflo.ru/api/agent/pair", json={"secret": "new", "project": "Вилла Юг"})
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "RU-ABC-DEF-GHK"})
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_SERVER] == "https://app.hauflo.ru"
    # The old secret stays with the server that issued it.
    assert "previous" not in aioclient_mock.mock_calls[-1][2]


async def test_plain_http_never_to_a_public_ipv6(hass: HomeAssistant, aioclient_mock) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "ABC-DEF-GHK", "advanced": {"server": "http://[2001:4860::8888]:8765"}})
    assert result["errors"] == {"server": "https_required"}


async def test_pairing_asks_which_group_addresses_hauflo_may_send_to(hass: HomeAssistant, aioclient_mock) -> None:
    """Narrow round 25.10 (B9): an address no HA entity uses (a gate actuator) was writable — the integrator now
    allows the project's ranges in HA, and takes out what Hauflo must not control."""
    aioclient_mock.post("https://app.hauflo.ru/api/agent/pair", json={"secret": "s", "project": "Дом", "ranges": ["1/1", "1/2", "7/7"]})
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "RU-ABC-DEF-GHK"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "ranges"
    assert "7/7" in result["description_placeholders"]["ranges"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"ranges": "1/1, 1/2, 99/9, x"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {"ranges": ["1/1", "1/2"]}
