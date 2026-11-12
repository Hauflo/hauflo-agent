"""п.12 TURN for the intercom: the region's time-limited credentials from Hauflo (backend ha/turn.ts) handed to HA's
WebRTC — asked when the doors start and on every ring, never handed out past their TTL, never logged; the
integrator's own ice_servers win (Hauflo not asked at all); the agent asks by its own secret."""

from __future__ import annotations

import asyncio
import logging

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.hauflo import Agent
from custom_components.hauflo.intercom import cloud_ice, setup
from custom_components.hauflo.intercom.door import RINGING
from custom_components.hauflo.tuning import TUNING

from .intercom_fixtures import fast, setup_doors  # noqa: F401 — fixtures
from .sip_panel import HOST

T = TUNING["intercom"]
URL = "/api/hauflo/intercom"
DOOR = {"name": "Калитка", "preset": "generic", "host": HOST, "dtmf": "#", "area": ""}
CRED = "WDXfmVNpa0XX4/G2F+CBPCi2mNk="
ANSWER = {
    "ice_servers": [
        {"urls": ["stun:app.hauflo.ru:3478"]},
        {"urls": ["turn:app.hauflo.ru:3478?transport=udp", "turn:app.hauflo.ru:3478?transport=tcp"], "username": "1700003600:p1", "credential": CRED},
    ],
    "ttl": 3600,
    "turn": True,
}


def test_parse() -> None:
    servers, ttl = cloud_ice.parse(ANSWER)
    assert ttl == 3600 and servers == ANSWER["ice_servers"]
    low, high = T["cloud_ice_ttl_s"]
    assert cloud_ice.parse({**ANSWER, "ttl": 86400})[1] == high and cloud_ice.parse({**ANSWER, "ttl": 1})[1] == low
    # a username without its credential (or the other way) is not a pair: dropped, the urls kept
    assert cloud_ice.parse({"ice_servers": [{"urls": "turn:t:3478", "username": "u"}], "ttl": 60})[0] == [{"urls": ["turn:t:3478"]}]
    assert cloud_ice.parse({"ice_servers": [], "ttl": 60}) == ([], 60)  # Hauflo without a host: nothing
    for bad in (None, [], {"ttl": 60}, {"ice_servers": "x", "ttl": 60}, {"ice_servers": ["x"], "ttl": 60}, {"ice_servers": [{"urls": "http://x"}], "ttl": 60}, {"ice_servers": [{"urls": []}], "ttl": 60}, {"ice_servers": [], "ttl": "soon"}, {"ice_servers": []}):
        with pytest.raises(ValueError):
            cloud_ice.parse(bad)


async def test_lifetime(hass: HomeAssistant, caplog) -> None:
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return ANSWER

    ice = cloud_ice.CloudIce(hass, fetch)
    assert ice.current() == [] and ice.state()["turn"] is False  # nothing before Hauflo answered
    await asyncio.gather(ice.refresh(), ice.refresh(), ice.refresh())
    assert calls == 1  # asked once for all who wanted it at the same time
    assert ice.current() == ANSWER["ice_servers"]
    st = ice.state()
    assert st["source"] == "cloud" and st["turn"] is True and 3500 < st["expires_in"] <= 3600 and CRED not in str(st)
    expires = ice._expires
    assert ice.current(now=expires - T["cloud_ice_min_left_s"] - 1) and ice.current(now=expires - T["cloud_ice_min_left_s"]) == []
    assert ice.current(now=expires + 1) == []  # never past their TTL
    assert ice._unsub is not None  # the next ask is scheduled (before they run out)
    ice.stop()
    assert ice.current() == [] and ice._unsub is None
    await ice.refresh()
    assert calls == 1  # stopped: not asked again
    assert CRED not in caplog.text


async def test_short_ttl_keeps_a_margin(hass: HomeAssistant) -> None:
    async def fetch():
        return {**ANSWER, "ttl": 60}

    ice = cloud_ice.CloudIce(hass, fetch)
    await ice.refresh()
    assert ice.current() and ice.current(now=ice._expires - 14) == []  # a quarter of 60 s, not 300 s that kills all
    ice.stop()


async def test_no_answer(hass: HomeAssistant, caplog) -> None:
    caplog.set_level(logging.DEBUG)

    async def fetch():
        raise ValueError(f"status 401 {CRED}")

    ice = cloud_ice.CloudIce(hass, fetch)
    await ice.refresh()
    assert ice.current() == [] and ice._unsub is not None  # asked again later
    assert CRED not in caplog.text  # the error's text is never logged, only its kind
    ice.stop()


async def test_cloud_turn_reaches_webrtc_and_ring_refreshes(hass: HomeAssistant, setup_doors, monkeypatch, hass_client) -> None:
    from homeassistant.components.web_rtc import async_get_ice_servers

    assert await async_setup_component(hass, "web_rtc", {})
    asked: list[str] = []

    async def fetch(self):
        asked.append(self.secret)
        return ANSWER

    monkeypatch.setattr(Agent, "cloud_ice", fetch)
    entry = await setup_doors([DOOR])
    mgr = entry.runtime_data.intercom
    await hass.async_block_till_done()
    assert asked == ["s"] and mgr.cloud_ice is not None and mgr._ice_off is not None
    turn = [s for s in async_get_ice_servers(hass) if any(u.startswith("turn:") for u in s.urls)]
    assert len(turn) == 1 and turn[0].username == "1700003600:p1" and turn[0].credential == CRED
    mgr.fire(next(iter(mgr.doors.values())), RINGING)  # a ring: fresh credentials for whoever answers
    await hass.async_block_till_done()
    assert len(asked) == 2
    got = await (await (await hass_client()).get(URL)).json()
    assert got["ice"]["source"] == "cloud" and got["ice"]["turn"] is True and CRED not in str(got)
    await hass.config_entries.async_unload(entry.entry_id)
    assert not any(s.username == "1700003600:p1" for s in async_get_ice_servers(hass))
    assert mgr.cloud_ice is None


async def test_own_servers_win(hass: HomeAssistant, setup_doors, monkeypatch, hass_client) -> None:
    from homeassistant.components.web_rtc import async_get_ice_servers

    assert await async_setup_component(hass, "web_rtc", {})
    asked = 0

    async def fetch(self):
        nonlocal asked
        asked += 1
        return ANSWER

    monkeypatch.setattr(Agent, "cloud_ice", fetch)
    entry = await setup_doors([DOOR], ice_servers=[{"urls": ["turn:own.example:3478"], "username": "u", "credential": "c"}])
    await hass.async_block_till_done()
    mgr = entry.runtime_data.intercom
    mgr.fire(next(iter(mgr.doors.values())), RINGING)
    await hass.async_block_till_done()
    assert asked == 0 and mgr.cloud_ice is None
    urls = [u for s in async_get_ice_servers(hass) for u in s.urls]
    assert "turn:own.example:3478" in urls and not any("hauflo" in u for u in urls)
    got = await (await (await hass_client()).get(URL)).json()
    assert got["ice"] == {"source": "manual", "turn": True, "expires_in": 0}


async def test_offline_cloud_leaves_the_lan_talk(hass: HomeAssistant, setup_doors) -> None:
    entry = await setup_doors([DOOR])  # the fixture's Hauflo is unreachable
    await hass.async_block_till_done()
    mgr = entry.runtime_data.intercom
    assert mgr.cloud_ice is not None and mgr.cloud_ice.current() == [] and mgr.ice_state()["turn"] is False
    assert setup.register_ice(hass, {}, None) is None


async def test_agent_asks_by_its_secret(hass: HomeAssistant, aioclient_mock) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "agent-secret"})
    agent = Agent(hass, entry)
    aioclient_mock.get(f"https://app.hauflo.ru{T['cloud_ice_path']}", json=ANSWER)
    assert await agent.cloud_ice() == ANSWER
    assert aioclient_mock.mock_calls[-1][3]["Authorization"] == "Bearer agent-secret"
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"https://app.hauflo.ru{T['cloud_ice_path']}", status=401, json={"code": "agent_unknown"})
    with pytest.raises(ValueError):
        await agent.cloud_ice()
