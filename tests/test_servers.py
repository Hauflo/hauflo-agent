"""Owner 04.11 «запасной сервер»: the agent's link moves to the next host of its OWN region when one can't be reached —
proved with the first host really blocked (a refused port, a host that hangs past the timeout), never across regions."""

import asyncio
import json
import socket
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hauflo as hauflo
from custom_components.hauflo import servers
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DEFAULT_SERVER, DOMAIN, FALLBACK_SERVER, REGION_SERVERS

RUN = hauflo.Agent.run
EU, RU = "https://app.hauflo.com", "https://app.hauflo.ru"
EU2, RU2 = "https://eu-b.hauflo.test", "https://ru-b.hauflo.test"
TWO_EACH = {"EU": (EU, EU2), "RU": (RU, RU2), "AE": (EU,)}


@pytest.fixture(autouse=True)
def _fresh():
    servers.forget()
    yield
    servers.forget()


# --- the data -----------------------------------------------------------------------------------------------------------
def test_hosts_are_data_and_checked(tmp_path) -> None:
    assert DEFAULT_SERVER == servers.REGIONS["EU"][0] == EU
    assert FALLBACK_SERVER == servers.REGIONS["RU"][0] == RU
    assert REGION_SERVERS == {"EU": EU, "RU": RU, "AE": EU}
    good = json.loads(servers.FILE.read_text())
    for broken in (
        {**good, "regions": {**good["regions"], "RU": ["http://app.hauflo.ru"]}},  # never plain http
        {**good, "regions": {**good["regions"], "RU": ["https://app.hauflo.ru/"]}},
        {**good, "regions": {**good["regions"], "RU": []}},
        {**good, "default_region": "XX"},
        {**good, "mirrors": []},
        {**good, "version": 2},
    ):
        f = tmp_path / "s.json"
        f.write_text(json.dumps(broken))
        with pytest.raises(ValueError):
            servers.load(f)


def test_the_link_stays_in_its_region() -> None:
    assert servers.same_region(RU) == [RU]
    assert servers.same_region(EU + "/") == [EU]
    assert servers.same_region("http://hauflo-backend:8765") == ["http://hauflo-backend:8765"]  # a dev stand: itself
    with patch.dict(servers.REGIONS, TWO_EACH):
        assert servers.same_region(RU) == [RU, RU2]
        assert servers.same_region(RU2) == [RU2, RU]
        # AE shares the EU server: an AE/EU house only gets hosts both regions list — never EU's own second one.
        assert servers.same_region(EU) == [EU]
        servers.remember(RU2)
        assert servers.same_region(RU) == [RU2, RU]  # sticky: the one that answered last first
        assert servers.same_region(EU) == [EU]
        servers.remember("https://elsewhere.example")  # not ours: not kept
        assert servers.same_region(RU) == [RU2, RU]
        for region in ("RU", "EU"):
            for host in TWO_EACH[region]:
                other = {h for r, hs in TWO_EACH.items() if r != region and set(hs).isdisjoint(TWO_EACH[region]) for h in hs}
                assert not other & set(servers.same_region(host))


# --- first_answer against real sockets: refused, hanging, answering -----------------------------------------------------
def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens: connecting is refused
    return port


async def _serve(handler) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


async def test_first_host_refused_or_hanging_the_next_answers(socket_enabled) -> None:
    async def ok(_req):
        return web.json_response({"secret": "s"})

    async def hang(_req):
        await asyncio.sleep(5)
        return web.Response(text="late")

    async def missing(_req):
        return web.Response(status=404)

    good, good_url = await _serve(ok)
    slow, slow_url = await _serve(hang)
    nf, nf_url = await _serve(missing)
    refused = f"http://127.0.0.1:{_closed_port()}"
    try:
        async with aiohttp.ClientSession() as session:

            async def attempt(host: str):
                async with session.get(f"{host}/api/agent/pair", timeout=aiohttp.ClientTimeout(total=0.5, sock_connect=0.5)) as res:
                    return res.status

            assert await servers.first_answer([refused, slow_url, good_url], attempt) == (good_url, 200)
            # A 404 is an answer (a wrong code), not a reason to ask elsewhere.
            assert await servers.first_answer([nf_url, good_url], attempt) == (nf_url, 404)
            with pytest.raises((aiohttp.ClientConnectionError, OSError)):
                await servers.first_answer([refused], attempt)
            with pytest.raises(TimeoutError):
                await servers.first_answer([slow_url], attempt)
    finally:
        for r in (good, slow, nf):
            await r.cleanup()


# --- pairing ------------------------------------------------------------------------------------------------------------
@pytest.fixture
def _no_setup():
    with patch("custom_components.hauflo.async_setup_entry", return_value=True):
        yield


async def _pair(hass: HomeAssistant, code: str):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": code})
    if result["type"] is FlowResultType.FORM and result.get("step_id") == "ranges":
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    return result


async def test_pairing_moves_to_the_regions_second_host(hass: HomeAssistant, aioclient_mock, _no_setup) -> None:
    aioclient_mock.post(f"{RU}/api/agent/pair", exc=aiohttp.ClientConnectionError("refused"))
    aioclient_mock.post(f"{RU2}/api/agent/pair", json={"secret": "s", "project": "Дом"})
    with patch.dict(servers.REGIONS, TWO_EACH):
        result = await _pair(hass, "RU-ABC-DEF-GHK")
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_SERVER] == RU  # the region's address is kept; the tunnel finds the host itself
        assert [c[1].host for c in aioclient_mock.mock_calls] == ["app.hauflo.ru", "ru-b.hauflo.test"]
        assert servers.same_region(RU)[0] == RU2


async def test_pairing_never_leaves_the_region(hass: HomeAssistant, aioclient_mock, _no_setup) -> None:
    aioclient_mock.post(f"{RU}/api/agent/pair", exc=TimeoutError())
    aioclient_mock.post(f"{RU2}/api/agent/pair", exc=aiohttp.ClientConnectionError())
    aioclient_mock.post(f"{EU}/api/agent/pair", json={"secret": "s", "project": "x"})
    with patch.dict(servers.REGIONS, TWO_EACH):
        result = await _pair(hass, "RU-ABC-DEF-GHK")
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "cannot_connect"}
    assert {c[1].host for c in aioclient_mock.mock_calls} == {"app.hauflo.ru", "ru-b.hauflo.test"}


async def test_a_wrong_code_is_not_asked_elsewhere_in_the_region(hass: HomeAssistant, aioclient_mock, _no_setup) -> None:
    aioclient_mock.post(f"{RU}/api/agent/pair", status=404)
    aioclient_mock.post(f"{RU2}/api/agent/pair", json={"secret": "s", "project": "x"})
    with patch.dict(servers.REGIONS, TWO_EACH):
        result = await _pair(hass, "RU-ABC-DEF-GHK")
    assert result["errors"] == {"code": "invalid_code"}
    assert [c[1].host for c in aioclient_mock.mock_calls] == ["app.hauflo.ru"]


# --- the tunnel ---------------------------------------------------------------------------------------------------------
class _WS:
    close_code = 1000

    def __init__(self, frames):
        self._msgs = [SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(f)) for f in frames]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._msgs:
            raise StopAsyncIteration
        return self._msgs.pop(0)

    async def send_str(self, _data):
        return None

    async def close(self):
        return None

    @property
    def closed(self):
        return False


class _Dial:
    """ws_connect by host: «down» hosts refuse, «hang» hosts never answer, the rest welcome."""

    def __init__(self, down=(), hang=()):
        self.down, self.hang, self.urls = set(down), set(hang), []

    def ws_connect(self, url, **_k):
        self.urls.append(url)
        down, hang = any(h in url for h in self.down), any(h in url for h in self.hang)

        class Ctx:
            async def __aenter__(self):
                if down:
                    raise aiohttp.ClientConnectionError("refused")
                if hang:
                    await asyncio.sleep(60)
                return _WS([{"t": "welcome", "minVersion": "0.0.1"}])

            async def __aexit__(self, *exc):
                return False

        return Ctx()


@pytest.fixture
async def agent(hass: HomeAssistant):
    async def _idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: RU, CONF_SECRET: "top-secret"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry.runtime_data


async def _run(agent, waits_until: int = 1) -> list:
    waits = []

    def wait(delay, hinted, *_a):
        waits.append(delay)
        if len(waits) >= waits_until:
            agent.stopped = True
        return 0

    with patch("custom_components.hauflo._next_wait", wait), patch.object(hauflo, "CONNECT_TIMEOUT", 0.2):
        await RUN(agent)
    return waits


async def test_tunnel_blocked_first_host_goes_to_the_second_at_once(hass: HomeAssistant, agent) -> None:
    with patch.dict(servers.REGIONS, TWO_EACH):
        agent.session = dial = _Dial(down=["app.hauflo.ru"])
        waits = await _run(agent)
        # No wait between the blocked host and the next one; the link came up on the second (then closed → one wait).
        assert dial.urls == ["wss://app.hauflo.ru/api/agent/ws", "wss://ru-b.hauflo.test/api/agent/ws"]
        assert waits == [5]
        # Sticky: the next start goes to the host that answered.
        agent.stopped = False
        agent.session = dial = _Dial()
        await _run(agent)
        assert dial.urls == ["wss://ru-b.hauflo.test/api/agent/ws"]


async def test_tunnel_hanging_host_times_out_to_the_next(hass: HomeAssistant, agent) -> None:
    with patch.dict(servers.REGIONS, TWO_EACH):
        agent.session = dial = _Dial(hang=["app.hauflo.ru"])
        await _run(agent)
        assert dial.urls == ["wss://app.hauflo.ru/api/agent/ws", "wss://ru-b.hauflo.test/api/agent/ws"]


async def test_tunnel_never_leaves_the_region_and_backs_off_after_a_round(hass: HomeAssistant, agent) -> None:
    with patch.dict(servers.REGIONS, TWO_EACH):
        agent.session = dial = _Dial(down=["app.hauflo.ru", "ru-b.hauflo.test"])
        waits = await _run(agent, waits_until=2)
        assert waits == [5, 10]  # one back-off per round of the region's hosts, growing as before
        assert all("hauflo.ru" in u or "ru-b." in u for u in dial.urls) and len(dial.urls) == 4
        assert not any("hauflo.com" in u or "eu-b." in u for u in dial.urls)


async def test_one_host_region_only_retries_with_back_off(hass: HomeAssistant, agent) -> None:
    agent.session = dial = _Dial(down=["app.hauflo.ru"])
    waits = await _run(agent, waits_until=3)
    assert waits == [5, 10, 20]
    assert dial.urls == ["wss://app.hauflo.ru/api/agent/ws"] * 3
