"""The agent's tunnel end to end against fakes: link loop, per-channel relay (what is refused, what is scrubbed on the way
back), REST relay, status, stop; the views' guards and the entry's lifecycle."""

import json
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, label_registry as lr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

import custom_components.hauflo as hauflo
from custom_components.hauflo.const import CONF_ALLOW_CHANGES, CONF_SECRET, CONF_SERVER, DATA_UNLINKED, DOMAIN, LOCKED_CODE
from tests.test_allowlist_knx import knx

RUN = hauflo.Agent.run


async def _idle(self):
    return None


@pytest.fixture
async def entry(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "top-secret"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", _idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


class Local:
    """The HA-side websocket of one channel."""

    def __init__(self, msgs=()):
        self.sent, self.closed_called, self.msgs = [], False, list(msgs)

    async def send_str(self, data):
        self.sent.append(json.loads(data))

    async def close(self):
        self.closed_called = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.msgs:
            raise StopAsyncIteration
        return NS(type=aiohttp.WSMsgType.TEXT, data=self.msgs.pop(0)) if isinstance(self.msgs[0], str) else self.msgs.pop(0)


class Out:
    """Collects what the agent sends to Hauflo."""

    def __init__(self, agent):
        self.frames = []

        async def send(frame):
            self.frames.append(frame)

        self.patch = patch.object(agent, "send", send)

    def __enter__(self):
        self.patch.start()
        return self

    def __exit__(self, *a):
        self.patch.stop()

    def codes(self):
        return [json.loads(f["data"])["error"]["code"] for f in self.frames if f.get("t") == "ws_msg"]


class WS:
    def __init__(self, msgs, close_code=1000):
        self.msgs, self.close_code, self.closed = list(msgs), close_code, True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.msgs:
            raise StopAsyncIteration
        return self.msgs.pop(0)

    async def send_str(self, _d):
        return None

    async def close(self):
        return None


def text(f):
    return NS(type=aiohttp.WSMsgType.TEXT, data=json.dumps(f) if not isinstance(f, str) else f)


class Session:
    def __init__(self, ws=None, err=None):
        self.ws, self.err = ws, err

    def ws_connect(self, *_a, **_k):
        ws, err = self.ws, self.err

        class Ctx:
            async def __aenter__(self):
                if err:
                    raise err
                return ws

            async def __aexit__(self, *exc):
                return False

        return Ctx()


# --- the link loop -------------------------------------------------------------------------------------------------------
async def test_link_loop_survives_junk_and_a_failing_frame(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    agent.channels[7] = Local()
    msgs = [NS(type=aiohttp.WSMsgType.BINARY, data=b"x"), text("{not json"), text([1, 2]), text({"t": "http", "id": 1}), text({"t": "welcome", "minVersion": "0.0.1"})]
    agent.session = Session(WS(msgs))
    agent.engine = NS(on_change=None, config={"rules": []}, active_list=lambda: [])
    boom = AsyncMock(side_effect=RuntimeError("frame"))
    waits = []

    def wait(delay, hinted, *_a):
        waits.append(delay)
        agent.stopped = True
        return 0

    with patch.object(agent, "_on_frame", boom), patch("custom_components.hauflo._next_wait", wait):
        await RUN(agent)
    assert boom.await_count == 1  # the one bad frame didn't end the link
    assert agent.engine.on_change == agent.status_now
    assert agent.channels == {} and agent.ws is None and not agent.linked  # the channel is closed with the link
    assert waits == [5]
    # a connection error is just a retry, not a crash
    agent.stopped = False
    agent.session = Session(err=aiohttp.ClientConnectionError("down"))
    with patch("custom_components.hauflo._next_wait", wait):
        await RUN(agent)
    # unlinked in Hauflo: knock rarely and ask for the reauth
    agent.stopped = False
    agent.session = Session(WS([], close_code=4003))

    async def nap(sec):
        assert sec == 600
        agent.stopped = True

    with patch("custom_components.hauflo.asyncio.sleep", nap), patch.object(agent.entry, "async_start_reauth") as reauth:
        await RUN(agent)
    assert hass.data[DATA_UNLINKED] is True and reauth.called


async def test_big_frames_are_parsed_off_the_loop(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    with patch.object(hauflo, "BIG_FRAME", 10):
        assert await agent._parse('{"a": "' + "x" * 20 + '"}') == {"a": "x" * 20}
        with pytest.raises(ValueError):
            await agent._parse("{" + "x" * 20)
    assert await agent._parse('{"a": 1}') == {"a": 1}


async def test_send_only_on_an_open_link(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    sent = []

    class Ws:
        closed = False

        async def send_str(self, d):
            sent.append(d)

    await agent.send({"t": "x"})  # no link: nothing, no error
    agent.ws = Ws()
    await agent.send({"t": "x"})
    assert sent == ['{"t": "x"}']
    agent.ws.closed = True
    await agent.send({"t": "y"})
    assert len(sent) == 1
    agent.ws = None


async def test_frames_dispatch(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    calls = []
    with patch.object(agent, "_spawn", lambda coro: (calls.append(coro.cr_code.co_name), coro.close())), patch.object(agent, "_channel_send", AsyncMock()) as cs, patch.object(agent, "_close_channel", AsyncMock()) as cc, patch.object(hauflo.service, "async_save", AsyncMock()) as sv:
        await agent._on_frame({"t": "ws_open", "ch": "3"})
        await agent._on_frame({"t": "ws_send", "ch": 3, "data": "{}"})
        await agent._on_frame({"t": "ws_close", "ch": 3})
        await agent._on_frame({"t": "http", "id": 1})
        await agent._on_frame({"t": "guest", "id": 1})
        await agent._on_frame({"t": "service", "info": {"a": 1}})
        await agent._on_frame({"t": "mystery"})
    assert calls == ["_open_channel", "_http", "_guest"]
    cs.assert_awaited_once_with(3, "{}")
    cc.assert_awaited_once_with(3, tell=False)
    sv.assert_awaited_once_with(hass, {"a": 1})


async def test_guest_relay_answers_even_when_not_ready_or_failing(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    real = agent.guest
    with Out(agent) as out:
        agent.guest = None
        await agent._guest({"id": 1})
        agent.guest = NS(async_load=AsyncMock(), handle=AsyncMock(return_value=(200, {"ok": 1})))
        await agent._guest({"id": 2, "body": "b"})
        agent.guest = NS(async_load=AsyncMock(), handle=AsyncMock(side_effect=RuntimeError("secret detail")))
        await agent._guest({"id": 3})
    agent.guest = real
    assert [(f["status"], f["id"]) for f in out.frames] == [(503, 1), (200, 2), (500, 3)]
    assert "secret detail" not in out.frames[2]["body"]  # a guest never sees a stack


# --- the relay to HA ----------------------------------------------------------------------------------------------------
async def test_open_channel_limits_and_failure(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    with Out(agent) as out:
        agent.channels.update({i: Local() for i in range(8)})
        await agent._open_channel(20)  # too many
        await agent._open_channel(3)  # a number already open: never a second admin session behind it
        agent.channels.clear()
        agent.channels[3] = Local()
        await agent._open_channel(3)
        agent.channels.clear()
        agent.local_session = NS(ws_connect=AsyncMock(side_effect=aiohttp.ClientConnectionError("no")))
        await agent._open_channel(4)
    assert out.frames == [{"t": "ws_closed", "ch": 20}, {"t": "ws_closed", "ch": 3}, {"t": "ws_closed", "ch": 3}, {"t": "ws_closed", "ch": 4}]
    assert agent.channels == {}


async def test_open_channel_relays_and_scrubs_replies(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    agent._pending[(5, 1)] = ("get_config", None)
    agent._pending[(6, 1)] = ("get_config", None)  # another channel's: stays
    agent._note_subs = {(5, 9), (6, 9)}
    local = Local([json.dumps({"id": 1, "type": "result", "success": True, "result": {"latitude": 1.0, "version": "x"}}), NS(type=aiohttp.WSMsgType.BINARY, data=b"x")])
    agent.local_session = NS(ws_connect=AsyncMock(return_value=local))
    with Out(agent) as out:
        await agent._open_channel(5)
    msgs = [f for f in out.frames if f["t"] == "ws_msg"]
    assert json.loads(msgs[0]["data"])["result"] == {"version": "x"}  # where the house is stays home
    assert out.frames[-1] == {"t": "ws_closed", "ch": 5}
    assert (6, 1) in agent._pending and (5, 1) not in agent._pending and agent._note_subs == {(6, 9)}


async def test_track_scrubs_and_records_what_hauflo_made(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    assert agent._track(1, "{bad") == "{bad"
    k = MockConfigEntry(domain="knx")
    k.add_to_hass(hass)
    er.async_get(hass).async_get_or_create("light", "knx", "u-new", config_entry=k, suggested_object_id="new")
    agent._pending[(1, 1)] = ("knx/create_entity", "light.new")
    agent._pending[(1, 2)] = ("knx/delete_entity", "u-old")
    agent._pending[(1, 3)] = ("knx/update_entity", "light.new")
    agent._pending[(1, 4)] = ("knx/create_entity", None)
    agent._pending[(1, 5)] = ("config/area_registry/create", None)
    agent._pending[(1, 6)] = ("config/label_registry/create", None)
    agent._pending[(1, 7)] = ("config/area_registry/create", None)
    agent.owned.add("u-old")
    batch = [
        {"id": 1, "type": "result", "success": True, "result": {"success": True, "entity_id": "light.new"}},
        {"id": 2, "type": "result", "success": True, "result": None},
        {"id": 3, "type": "result", "success": True, "result": {"success": True, "entity_id": "light.new"}},  # an update never makes it Hauflo's
        {"id": 4, "type": "result", "success": False, "result": None},  # a failed create
        {"id": 5, "type": "result", "success": True, "result": {"area_id": "kitchen"}},
        {"id": 6, "type": "result", "success": True, "result": {"label_id": "lab"}},
        {"id": 7, "type": "result", "success": False, "error": {}},
        {"id": 8, "type": "result", "success": True, "result": 1},  # not asked for
        "junk",
    ]
    out = json.loads(agent._track(1, json.dumps(batch)))
    assert len(out) == 9
    assert agent.owned == {"u-new"} and agent.owned_entries == {"area:kitchen", "label:lab"}
    # a bridge made through a flow is tracked; another type of entry is not
    hk = MockConfigEntry(domain="homekit")
    hk.add_to_hass(hass)
    mq = MockConfigEntry(domain="mqtt")
    mq.add_to_hass(hass)
    agent._track_entry("not json")
    agent._track_entry(json.dumps({"type": "form"}))
    agent._track_entry(json.dumps({"type": "create_entry", "result": {"entry_id": mq.entry_id}}))
    assert mq.entry_id not in agent.owned_entries
    agent._track_entry(json.dumps({"type": "create_entry", "result": {"entry_id": hk.entry_id}}))
    assert hk.entry_id in agent.owned_entries
    assert agent._owned_data()["entries"] == sorted(agent.owned_entries)
    # notifications are cut to Hauflo's own bridges; a rename remembers the name (not for a failed one)
    agent._note_subs.add((1, 20))
    agent._pending[(1, 21)] = ("persistent_notification/x", None)
    note = {"id": 20, "type": "event", "event": {"notifications": {hk.entry_id: {"m": 1}, "other": {"m": 2}}}}
    got = json.loads(agent._track(1, json.dumps(note)))
    assert list(got["event"]["notifications"]) == [hk.entry_id]
    remembered = []
    agent._pending[(1, 30)] = ("config/entity_registry/update", ("light.old", True))
    agent._pending[(1, 31)] = ("config/entity_registry/update", ("light.old", True))
    with patch.object(agent.ownership, "remember_name", lambda *a: remembered.append(a)):
        agent._track(1, json.dumps([{"id": 30, "type": "result", "success": True, "result": {"entity_entry": {"entity_id": "light.new", "name": "N"}}}, {"id": 31, "type": "result", "success": False, "result": None}]))
    assert remembered == [("light.new", "N", "light.old", True)]
    # a scrubbed list of states hides the phones' and presence
    agent._pending[(1, 40)] = ("get_states", None)
    states = [{"entity_id": "person.ivan", "state": "home", "attributes": {"latitude": 1, "friendly_name": "Ivan"}}]
    got = json.loads(agent._track(1, json.dumps({"id": 40, "type": "result", "success": True, "result": states})))
    assert got["result"][0]["state"] == "hidden" and "latitude" not in got["result"][0]["attributes"]


async def test_channel_send_refusals_and_bookkeeping(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    local = Local()
    agent.channels[1] = local
    k = MockConfigEntry(domain="knx")
    k.add_to_hass(hass)
    er.async_get(hass).async_get_or_create("light", "knx", "u-mine", config_entry=k, suggested_object_id="mine")
    agent.owned.add("u-mine")
    hass.data["knx"] = knx({}, {}, [])
    send = lambda m: agent._channel_send(1, json.dumps(m) if not isinstance(m, str) else m)  # noqa: E731
    agent.refresh = object()
    with Out(agent) as out, patch.object(hauflo.backup, "fresh", return_value=False), patch.object(hass.auth, "async_create_access_token", return_value="tok"):
        await agent._channel_send(99, "{}")  # no such channel
        await send("{bad")
        await send([1])
        await send({"id": 1, "type": "auth"})
        assert local.sent[-1] == {"type": "auth", "access_token": "tok"}  # the agent signs in with its own token
        await send({"id": 2, "type": "backup/generate"})  # not on the list
        await send({"id": 3, "type": "config/area_registry/create", "name": "A"})  # a change without a fresh backup
        assert out.codes() == ["not_allowed", "backup_required"]
        # an address outside what the integrator allowed in HA is asked for, not sent
        with patch.object(agent, "_ask_ranges") as ask:
            await send({"id": 4, "type": "knx/create_entity", "platform": "light", "data": {"knx": {"ga": {"write": "9/1/1"}}}})
        assert out.codes()[-1] == "address_not_allowed" and ask.call_args[0][0] == {"9/1/1"}
    n = len(local.sent)
    with Out(agent), patch.object(hauflo.backup, "fresh", return_value=True):
        await send({"id": 10, "type": "get_states"})
        await send({"id": 11, "type": "persistent_notification/subscribe"})
        await send({"id": 12, "type": "config/area_registry/create", "name": "A"})
        await send({"id": 13, "type": "knx/delete_entity", "entity_id": "light.mine"})
        await send({"id": 14, "type": "config/entity_registry/update", "entity_id": "light.mine", "name": "X"})
    assert len(local.sent) == n + 5
    assert agent._pending[(1, 10)] == ("get_states", None) and (1, 11) in agent._note_subs
    assert agent._pending[(1, 12)][0] == "config/area_registry/create"
    assert agent._pending[(1, 13)] == ("knx/delete_entity", "u-mine")
    assert agent._pending[(1, 14)] == ("config/entity_registry/update", ("light.mine", True))
    # a saved Hauflo dashboard and the plan go to the merge, never straight to HA
    with patch.object(hauflo.dashmerge, "handle", AsyncMock()) as dm:
        await send({"id": 20, "type": "lovelace/config/save", "url_path": "hauflo-main", "config": {}})
        await send({"id": 21, "type": hauflo.dashmerge.PLAN})
    assert dm.await_count == 2 and len(local.sent) == n + 5
    # close
    await agent._close_channel(1, tell=True)
    await agent._close_channel(1, tell=False)
    assert local.closed_called and 1 not in agent.channels


async def test_locked_http_and_channel_say_why(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    agent.channels[1] = Local()
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ALLOW_CHANGES: False})
    with Out(agent) as out:
        await agent._channel_send(1, json.dumps({"id": 1, "type": "config/area_registry/create"}))
        await agent._http({"id": 5, "method": "POST", "path": "/api/config/x"})
    assert out.codes() == [LOCKED_CODE] and out.frames[-1]["status"] == 423


async def test_http_relay(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    calls = []

    class Res:
        def __init__(self, status, body):
            self.status, self.body = status, body

        async def text(self):
            return self.body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    reply = {}

    def request(method, url, **kw):
        calls.append((method, url, kw["headers"]["Authorization"].startswith("Bearer ")))
        if isinstance(reply["r"], Exception):
            raise reply["r"]
        return reply["r"]

    agent.local_session = NS(request=request)
    with Out(agent) as out:
        await agent._http({"id": 1, "method": "GET", "path": "/api/states"})  # not on the list
        assert out.frames[-1]["status"] == 403 and not calls
        with patch.object(hauflo.backup, "fresh", return_value=False):
            await agent._http({"id": 2, "method": "POST", "path": "/api/hauflo/sidebar", "body": "{}"})
        assert out.frames[-1]["status"] == 428 and not calls
        reply["r"] = Res(200, json.dumps({"latitude": 5, "version": "v", "external_url": "https://u:p@h.example/x"}))
        await agent._http({"id": 3, "method": "GET", "path": "/api/config"})
        assert json.loads(out.frames[-1]["body"]) == {"version": "v", "external_url": "https://h.example/x"}
        reply["r"] = Res(200, "not json")
        await agent._http({"id": 4, "method": "GET", "path": "/api/config"})
        assert out.frames[-1]["body"] == "not json"
        hk = MockConfigEntry(domain="homekit")
        hk.add_to_hass(hass)
        agent.owned_entries.add(hk.entry_id)
        reply["r"] = Res(200, json.dumps({"data": {"states": [1], "config-entry": {"options": {"filter": {"a": 1}, "pin": "9"}}}}))
        await agent._http({"id": 5, "method": "GET", "path": f"/api/diagnostics/config_entry/{hk.entry_id}"})
        assert json.loads(out.frames[-1]["body"]) == {"data": {"config-entry": {"options": {"filter": {"a": 1}}}}}
        reply["r"] = aiohttp.ClientConnectionError("gone")
        await agent._http({"id": 6, "method": "GET", "path": "/api/"})
        assert out.frames[-1]["status"] == 502
        # a created bridge is Hauflo's from now on
        hk2 = MockConfigEntry(domain="homekit")
        hk2.add_to_hass(hass)
        agent.hass.config_entries.flow = NS(async_get=lambda fid: {"handler": "homekit", "step_id": "pairing"})
        reply["r"] = Res(200, json.dumps({"type": "create_entry", "result": {"entry_id": hk2.entry_id}}))
        with patch.object(hauflo.backup, "fresh", return_value=True):
            await agent._http({"id": 7, "method": "POST", "path": "/api/config/config_entries/flow/f1", "body": "{}"})
        assert hk2.entry_id in agent.owned_entries


# --- status ------------------------------------------------------------------------------------------------------------
async def test_status_reports_knx_monitoring_and_alarms(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    k = MockConfigEntry(domain="knx")
    k.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create("light", "knx", "u-1", config_entry=k, suggested_object_id="gone")
    hass.states.async_set("light.gone", "unavailable")
    assert agent._knx() == {"loaded": False, "connected": None, "state": "not_loaded", "silentMin": 0, "heard": False}
    k.mock_state(hass, ConfigEntryState.LOADED)
    hass.data["knx"] = NS(xknx=NS(connection_manager=NS(connected=NS(is_set=lambda: True))))
    assert agent._knx()["connected"] is True
    hass.data["knx"] = NS()  # a changed internal: unknown, not a crash
    assert agent._knx()["connected"] is None
    hass.config_entries.async_update_entry(k, data={"connection_type": "tunneling", "host": "10.0.0.2"})
    assert agent._knx_conn() == {"type": "tunneling", "host": "10.0.0.2", "secure": False}
    # monitored ones: alarms for a problem, a low battery, an unavailable
    labels = lr.async_get(hass)
    await agent._ensure_label()
    await agent._ensure_label()  # once
    label = next(l for l in labels.async_list_labels() if l.name.casefold() in hauflo.MONITOR_LABELS)
    for eid, state, attrs in (("binary_sensor.leak", "on", {"device_class": "moisture"}), ("binary_sensor.door", "on", {"device_class": "door"}), ("sensor.bat", "12", {"device_class": "battery"}), ("sensor.bat2", "unknown", {"device_class": "battery"}), ("sensor.t", "unavailable", {})):
        reg.async_get_or_create(eid.split(".")[0], "test", eid, suggested_object_id=eid.split(".")[1])
        reg.async_update_entity(eid, labels={label.label_id})
        hass.states.async_set(eid, state, attrs)
    reg.async_get_or_create("sensor", "test", "nostate", suggested_object_id="nostate")
    reg.async_update_entity("sensor.nostate", labels={label.label_id})
    agent.engine = NS(active_list=lambda: [{"id": 1}], config={"rules": [1]})
    hass.config.external_url = "https://home.example"
    agent._admins = ["Ivan"]
    st = agent.status()
    problems = {m["entityId"]: m["problem"] for m in st["monitored"]}
    assert problems == {"binary_sensor.leak": True, "binary_sensor.door": False, "sensor.bat": True, "sensor.bat2": False, "sensor.t": True}
    assert st["unavailable"] == {"count": 1, "sample": ["light.gone"]} and st["alarms"] == [{"id": 1}] and st["alarmsReady"] is True
    assert st["haUrl"] == "https://home.example" and st["haAdmins"] == ["Ivan"] and st["locked"] is False
    # removing the label list entry: nothing monitored
    labels.async_delete(label.label_id)
    assert agent._monitored_ids() == []
    agent.engine = None
    k.mock_state(hass, ConfigEntryState.NOT_LOADED)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5))  # delayed registry saves go out
    await hass.async_block_till_done()


async def test_knx_conn_reads_the_real_entry_data(hass: HomeAssistant, entry) -> None:
    k = MockConfigEntry(domain="knx", data={"connection_type": "tunneling", "host": "10.0.0.2"})
    k.add_to_hass(hass)
    assert entry.runtime_data._knx_conn() == {"type": "tunneling", "host": "10.0.0.2", "secure": False}


async def test_status_loop_signals_and_stop(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    sent = []

    class Ws:
        closed = False

        async def send_str(self, d):
            sent.append(json.loads(d))

        async def close(self):
            sent.append("closed")

    ws = agent.ws = Ws()
    users = [NS(name="Ivan", is_admin=True, is_active=True, system_generated=False), NS(name="Kid", is_admin=False, is_active=True, system_generated=False), NS(name="Sys", is_admin=True, is_active=True, system_generated=True), NS(name="", is_admin=True, is_active=True, system_generated=False)]
    with patch.object(hass.auth, "async_get_users", AsyncMock(return_value=users)):
        await agent._refresh_admins()
        assert agent._admins == ["Ivan"]

        async def stop_after(_sec):
            agent.ws = None

        with patch("custom_components.hauflo.asyncio.sleep", stop_after):
            await agent._status_loop(ws)
    with patch.object(hass.auth, "async_get_users", AsyncMock(side_effect=RuntimeError)):
        await agent._refresh_admins()  # keeps the last list
    assert agent._admins == ["Ivan"]
    assert sent[0]["t"] == "status" and sent[0]["status"]["haAdmins"] == ["Ivan"]
    # a monitored change asks for a status within seconds; two changes are one tick
    agent.ws = ws
    er.async_get(hass).async_get_or_create("sensor", "test", "sig", suggested_object_id="sig")
    hass.states.async_set("sensor.sig", "1")
    hass.config.language = "ru"
    await agent._ensure_label()
    label = next(l for l in lr.async_get(hass).async_list_labels() if l.name.casefold() in hauflo.MONITOR_LABELS)
    er.async_get(hass).async_update_entity("sensor.sig", labels={label.label_id})
    agent._watch_signals()
    agent._watch_signals()  # re-watching drops the earlier subscription
    assert agent._unsub_signals is not None
    agent._signal_changed(None)
    first = agent._status_soon
    agent._signal_changed(None)
    assert agent._status_soon is not first
    agent._status_soon()  # the real timer; the tick itself is called by hand
    agent._status_tick(None)
    assert agent._status_soon is None
    await hass.async_block_till_done()
    assert any(isinstance(s, dict) and s.get("t") == "status" for s in sent[1:])
    # stop: everything unsubscribed, the link closed, tasks cancelled
    agent._signal_changed(None)
    agent._lost_timer = lambda: sent.append("timer")
    await agent.stop()
    assert agent.stopped and agent._unsub_signals is None and agent._status_soon is None and agent._lost_timer is None and "closed" in sent and "timer" in sent
    agent.ws = None
    await hass.async_block_till_done()
    assert agent._status_soon is None


async def test_allowed_ranges_and_lost_link(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    # before the owner chose ranges: those of what Hauflo already made
    agent.owned.add("u-1")
    hass.data["knx"] = knx({"light": {"u-1": {"knx": {"ga": {"write": "1/2/3"}}}, "u-x": {"knx": {"ga": {"write": "9/9/9"}}}, "u-bad": "x"}})
    assert agent.allowed_ranges() == {"1/2"}
    hass.data["knx"] = NS()
    assert agent.allowed_ranges() == set()
    hass.config_entries.async_update_entry(entry, options={**entry.options, "ranges": ["5/5", 6]})
    assert agent.allowed_ranges() == {"5/5", "6"}
    assert agent._outside_ranges({"type": "get_states"}) == set()
    assert agent._outside_ranges({"type": "knx/create_entity", "data": {"knx": {"ga": {"write": "5/5/1"}, "ga2": {"write": "7/7/7"}, "ga3": {"write": "bad"}}}}) == {"7/7/7"}
    # lost for hours: Repairs, once; back: it goes
    from homeassistant.helpers import issue_registry as ir

    agent._lost_timer = None
    agent.linked = False
    agent._link_lost(None)
    assert ir.async_get(hass).async_get_issue(DOMAIN, hauflo.issues.LINK_LOST) is not None
    agent._watch_link(True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, hauflo.issues.LINK_LOST) is None
    agent._lost_timer = lambda: None
    agent._watch_link(True)
    assert agent._lost_timer is None
    hass.data[DATA_UNLINKED] = True
    agent._link_lost(None)  # unlinked on purpose: no "lost" noise
    assert ir.async_get(hass).async_get_issue(DOMAIN, hauflo.issues.LINK_LOST) is None
    hass.data.pop(DATA_UNLINKED)


async def test_instance_state_visibility_and_local_base(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    assert isinstance(await agent._instance(), str)
    with patch("homeassistant.helpers.instance_id.async_get", AsyncMock(side_effect=RuntimeError)):
        assert await agent._instance() is None
    assert agent._state_visible("binary_sensor.nowhere") is False
    hass.config.api = NS(use_ssl=True, host="::1", port=9000)
    assert agent.local_base == "https://[::1]:9000"
    hass.config.api = NS(use_ssl=False, host="192.168.1.2", port=8123)
    assert agent.local_base == "http://192.168.1.2:8123"
    hass.config.api = NS(use_ssl=False, host="0.0.0.0", port=8123)
    assert agent.local_base == "http://127.0.0.1:8123"
    hass.config.api = None
    assert agent.local_base == "http://127.0.0.1:8123"
    agent.refresh = None
    assert agent.token == ""


async def test_knx_heard_only_from_the_bus(hass: HomeAssistant, entry, monkeypatch) -> None:
    import sys

    from homeassistant.helpers.dispatcher import async_dispatcher_send

    # xknx isn't installed here: a stand-in for HA's KNX constants (what the agent subscribes to).
    monkeypatch.setitem(sys.modules, "homeassistant.components.knx", NS(__path__=[]))
    monkeypatch.setitem(sys.modules, "homeassistant.components.knx.const", NS(SIGNAL_KNX_TELEGRAM="knx_telegram_test", KNX_MODULE_KEY="knx"))
    agent = hauflo.Agent(hass, entry)
    assert agent._knx_heard is None and agent._unsub_knx is not None
    async_dispatcher_send(hass, "knx_telegram_test", None, {"direction": "Outgoing"})
    await hass.async_block_till_done()
    assert agent._knx_heard is None  # our own telegrams aren't the bus talking
    async_dispatcher_send(hass, "knx_telegram_test", None, None)
    async_dispatcher_send(hass, "knx_telegram_test", None, {"direction": "Incoming"})
    await hass.async_block_till_done()
    assert agent._knx_heard is not None and agent._knx()["heard"] is True
    await agent.stop()
    assert agent._unsub_knx is None


# --- views and the entry's lifecycle -------------------------------------------------------------------------------------
class Req(dict):
    def __init__(self, hass, admin=True, body=None):
        super().__init__(hass_user=NS(is_admin=admin, name="Test", system_generated=False))
        self.app = {hauflo.KEY_HASS: hass}
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


async def test_views_are_admin_only_and_validate(hass: HomeAssistant, entry) -> None:
    owned, rng = hauflo.OwnedView(), hauflo.RangesView()
    for coro in (owned.get(Req(hass, admin=False)), rng.get(Req(hass, admin=False)), rng.post(Req(hass, admin=False))):
        assert (await coro).status == 403
    assert (await rng.post(Req(hass, body=ValueError()))).status == 400
    for bad in ([], {"addresses": "x"}, {"addresses": [1]}, {"addresses": ["1/1/1"] * 100000}):
        assert (await rng.post(Req(hass, body=bad))).status == 400
    entry.runtime_data.owned.add("u-1")
    entry.runtime_data.owned_entries |= {"area:a1", "g1", "label:l"}
    got = json.loads((await owned.get(Req(hass))).body)
    assert got == {"unique_ids": ["u-1"], "entries": ["g1"], "areas": ["a1"]}
    got = json.loads((await rng.get(Req(hass))).body)
    assert got["allowed"] == []
    ok = json.loads((await rng.post(Req(hass, body={"addresses": ["9/9/9"], "project": "P"}))).body)
    assert ok["allowed"] == []
    # nothing loaded -> not linked
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert (await rng.post(Req(hass, body={"addresses": []}))).status == 409
    for fn in (hauflo._ownership, hauflo._engine, hauflo._season, hauflo._automations, hauflo._scenes, hauflo._presence, hauflo._guest, hauflo._comfort):
        assert fn(hass) is None


async def test_getters_with_a_loaded_entry(hass: HomeAssistant, entry) -> None:
    assert hauflo._automations(hass) is hauflo._automations(hass) is not None
    assert hauflo._scenes(hass) is hauflo._scenes(hass) is not None
    assert hauflo._season(hass) is not None and hauflo._presence(hass) is not None and hauflo._guest(hass) is not None
    assert hauflo._comfort(hass) is not None and hauflo._ownership(hass) is not None


async def test_ha_stop_stops_the_agent_and_a_new_link_reloads(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    with patch.object(agent, "stop", AsyncMock()) as stop, patch.object(agent.presence, "async_stop") as pstop:
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()
    assert stop.await_count == 1 and pstop.called
    # options saved: no reload, Hauflo is told; another secret: reload
    with patch.object(agent, "status_now") as now, patch.object(hass.config_entries, "async_schedule_reload") as reload_:
        await hauflo._entry_changed(hass, entry)
        assert now.called and not reload_.called
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_SECRET: "new"})
        await hauflo._entry_changed(hass, entry)
        assert reload_.called
    assert hauflo.relinked(agent, {**entry.data, CONF_SERVER: "https://x"}) and not hauflo.relinked(agent, {CONF_SECRET: agent.secret, CONF_SERVER: agent.server})


async def test_unload_survives_a_stuck_presence_and_remove_survives_failures(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    agent.presence.async_lights_out = AsyncMock(side_effect=RuntimeError("stuck"))
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED and agent.guest is None and agent.engine is None
    with patch.object(hauflo.keep, "async_keep_dashboard", AsyncMock(side_effect=RuntimeError)), patch.object(hauflo.sidebar, "async_remove", AsyncMock(side_effect=RuntimeError)), patch.object(hauflo, "HaufloStore") as store, patch.object(hauflo.notify.NotifyEngine, "async_remove", AsyncMock(side_effect=RuntimeError)), patch.object(hauflo.service, "async_remove", AsyncMock(side_effect=RuntimeError)), patch.object(hauflo, "async_get_clientsession") as sess:
        store.return_value.async_remove = AsyncMock(side_effect=RuntimeError)
        sess.return_value.post = AsyncMock(side_effect=aiohttp.ClientConnectionError)
        await hauflo.async_remove_entry(hass, entry)  # nothing here may stop the removal
        assert sess.return_value.post.await_count == 1
    uid_entry = MockConfigEntry(domain=DOMAIN, data={CONF_SERVER: "https://x", CONF_SECRET: "s", hauflo.CONF_USER_ID: "u1"})
    user = NS(id="u1")
    with patch.object(hass.auth, "async_get_user", AsyncMock(return_value=user)), patch.object(hass.auth, "async_remove_user", AsyncMock()) as rm, patch.object(hauflo, "async_get_clientsession") as sess:
        sess.return_value.post = AsyncMock()
        await hauflo.async_remove_entry(hass, uid_entry)
    rm.assert_awaited_once_with(user)
