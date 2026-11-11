"""Р55: the door's opening — the panel's own web API with digest (Hikvision ISAPI PUT) or basic auth, a relay of another
integration, refusals in the client's words — the panel's snapshot for the camera, and the options' «Домофон» step."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.hauflo.const import CONF_ALLOW_CHANGES, CONF_RANGES
from custom_components.hauflo.intercom import door as door_mod, sip

from .intercom_fixtures import fast, setup_doors  # noqa: F401 — fixtures
from .sip_panel import HOST


@pytest.fixture
async def web_panel(socket_enabled):
    """The panel's web API: digest (as Hikvision / Dahua), the requests it got; `mode` → "ok" / "refuse" / "fail"."""
    state = SimpleNamespace(got=[], mode="ok", port=0)

    async def handle(request: web.Request) -> web.Response:
        body = await request.read()
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            state.got.append((request.method, request.path_qs, body, "basic"))
            return web.Response(text="ok")
        if not auth.startswith("Digest ") or state.mode == "refuse":
            return web.Response(status=401, headers={"WWW-Authenticate": 'Digest realm="panel", nonce="n1", qop="auth", algorithm=MD5'})
        parsed = sip.parse_auth(auth)
        if parsed.get("username") != "admin" or not sip.verify(request.method, parsed, "secret"):
            return web.Response(status=401, headers={"WWW-Authenticate": 'Digest realm="panel", nonce="n1", qop="auth"'})
        state.got.append((request.method, request.path_qs, body, "digest"))
        if state.mode == "fail":
            return web.Response(status=500)
        return web.Response(body=b"\xff\xd8jpeg" if "picture" in request.path else b"<ok/>")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, 0)
    await site.start()
    state.port = site._server.sockets[0].getsockname()[1]
    yield state
    await runner.cleanup()


@pytest.fixture
def hik(monkeypatch, web_panel):
    """The Hikvision preset pointed at the emulator's port (the real one is 80)."""
    p = door_mod.PRESETS["hikvision"]
    monkeypatch.setitem(door_mod.PRESETS, "hikvision", {**p, "open_http": {**p["open_http"], "port": web_panel.port}, "snapshot": {**p["snapshot"], "port": web_panel.port}})
    monkeypatch.setitem(door_mod.PRESETS, "beward", {**door_mod.PRESETS["beward"], "open_http": {**door_mod.PRESETS["beward"]["open_http"], "port": web_panel.port}})
    return web_panel


DOOR = {"name": "Front", "preset": "hikvision", "host": HOST, "user": "admin", "password": "secret", "sip_user": "101", "sip_password": "pw", "relay": 2}


async def test_open_by_isapi_with_digest(hass: HomeAssistant, setup_doors, hik) -> None:
    await setup_doors([DOOR, {"name": "Beward", "preset": "beward", "host": HOST, "user": "admin", "password": "secret", "sip_user": "5", "sip_password": "pw"}])
    # Hikvision opens without a call (ISAPI): PUT …/door/<relay>, digest.
    await hass.services.async_call("button", "press", {"entity_id": "button.front_open"}, blocking=True)
    assert hik.got[-1] == ("PUT", "/ISAPI/AccessControl/RemoteControl/door/2", b"<RemoteControlDoor><cmd>open</cmd></RemoteControlDoor>", "digest")
    a = hass.states.get("sensor.front_intercom").attributes
    assert a["last_open"]["ok"] is True and a["last_open"]["via"] == "http" and a["can_open"] is True
    await hass.services.async_call("button", "press", {"entity_id": "button.beward_open"}, blocking=True)
    assert hik.got[-1][1] == "/cgi-bin/intercom_cgi?action=maindoor" and hik.got[-1][3] == "basic"
    # The snapshot for the card's still picture.
    cam = hass.data["camera"].get_entity("camera.front")
    assert await cam.async_camera_image() == b"\xff\xd8jpeg"
    # The panel refuses the login / fails: the client's words, the plate's record.
    hik.mode = "refuse"
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("button", "press", {"entity_id": "button.front_open"}, blocking=True)
    assert err.value.translation_key == "intercom_open_refused"
    hik.mode = "fail"
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("button", "press", {"entity_id": "button.front_open"}, blocking=True)
    assert err.value.translation_key == "intercom_open_failed"
    assert hass.states.get("sensor.front_intercom").attributes["last_open"] == {**hass.states.get("sensor.front_intercom").attributes["last_open"], "ok": False, "error": "open_failed"}
    # No snapshot: a frame of the stream from HA's go2rtc, if it is there.
    assert await cam.async_camera_image() is None
    cam._webrtc_provider = SimpleNamespace(async_get_image=AsyncMock(return_value=b"frame"))
    assert await cam.async_camera_image() == b"frame"


async def test_open_when_the_panel_is_gone(hass: HomeAssistant, setup_doors, monkeypatch, socket_enabled) -> None:
    p = door_mod.PRESETS["hikvision"]
    monkeypatch.setitem(door_mod.PRESETS, "hikvision", {**p, "open_http": {**p["open_http"], "port": 9}})  # nothing listens
    entry = await setup_doors([DOOR])
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("button", "press", {"entity_id": "button.front_open"}, blocking=True)
    assert err.value.translation_key == "intercom_open_failed"
    mgr = entry.runtime_data.intercom
    # A door with no way to open set, an http opener without a path: «не задано».
    d = mgr.doors["front"]
    d.cfg.open = ""
    with pytest.raises(HomeAssistantError) as err:
        await mgr.open(d, None)
    assert err.value.translation_key == "intercom_open_not_set"
    d.cfg.open, d.cfg.open_http = "http", {}
    with pytest.raises(HomeAssistantError):
        await mgr.open(d, None)
    d.cfg.snapshot = None
    assert await mgr.snapshot(d) is None


async def test_open_by_another_integrations_relay(hass: HomeAssistant, setup_doors) -> None:
    entry = await setup_doors([
        {"name": "Gate", "preset": "generic", "host": HOST, "open": "entity", "open_entity": "switch.gate_relay"},
        {"name": "Wicket", "preset": "generic", "host": HOST, "open": "entity", "open_entity": "light.not_a_relay"},
        {"name": "Garage", "preset": "generic", "host": HOST, "open": "entity", "open_entity": "lock.garage"},
    ])
    relay = async_mock_service(hass, "switch", "turn_on")  # after the setup: the switch platform registers the real one
    await hass.services.async_call("button", "press", {"entity_id": "button.gate_open"}, blocking=True)
    assert relay[-1].data == {"entity_id": "switch.gate_relay"}
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("button", "press", {"entity_id": "button.wicket_open"}, blocking=True)
    assert err.value.translation_key == "intercom_open_not_set"

    async def broken(call: ServiceCall) -> None:
        raise HomeAssistantError("jammed")

    hass.services.async_register("lock", "unlock", broken)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("button", "press", {"entity_id": "button.garage_open"}, blocking=True)
    assert err.value.translation_key == "intercom_open_failed"
    # The door's devices sit under the «Hauflo» service device.
    from homeassistant.helpers import device_registry as dr

    reg = dr.async_get(hass)
    hub = reg.async_get_device_by_identifier(("hauflo", entry.entry_id), entry.entry_id)
    gate = reg.async_get_device_by_identifier(("hauflo", f"{entry.entry_id}_intercom_gate"), entry.entry_id)
    assert hub is not None and gate is not None and gate.via_device_id == hub.id and gate.name == "Gate"


async def test_options_step(hass: HomeAssistant, setup_doors) -> None:
    entry = await setup_doors([])
    assert entry.runtime_data.intercom.doors == {} and entry.runtime_data.intercom.endpoint.transport is None  # no door, no socket
    assert hass.states.get("event.front_call") is None
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {CONF_ALLOW_CHANGES: True, CONF_RANGES: "1/1", "intercom": True})
    assert flow["step_id"] == "intercom" and "Hikvision" in flow["description_placeholders"]["presets"]
    keys = [str(k) for k in flow["data_schema"].schema]
    assert keys == ["bind", "port", "public", "doors"]
    bad = [
        ({"bind": "eth0", "port": 5060}, {"bind": "intercom_bad_address"}),
        ({"bind": "auto", "port": 5060, "public": "router"}, {"public": "intercom_bad_address"}),
        ({"bind": "auto", "port": 5060, "public": "", "doors": [{"name": "Gate", "preset": "generic"}]}, {"doors": "intercom_missing_host"}),
        ({"bind": "auto", "port": 5060, "public": "", "doors": [{"name": "Cloud", "preset": "basip_cloud"}]}, {"doors": "intercom_missing_sip_account"}),
    ]
    for data, errors in bad:
        flow = await hass.config_entries.options.async_configure(flow["flow_id"], data)
        assert flow["type"] is FlowResultType.FORM and flow["errors"] == errors, data
    assert flow["description_placeholders"]["door"] == "Cloud"
    door = {"name": "Front", "preset": "hikvision", "host": HOST, "sip_user": "101", "sip_password": "pw"}
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"bind": HOST, "port": 5070, "public": "203.0.113.7:5070", "doors": [door]})
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["intercom"] == {"bind": HOST, "port": 5070, "public": "203.0.113.7:5070", "doors": [door]}
    assert entry.options[CONF_RANGES] == ["1/1"]
    await hass.async_block_till_done()
    # The doors changed: the entry reloaded with them (their entities, the socket on the chosen address).
    mgr = entry.runtime_data.intercom
    assert set(mgr.doors) == {"front"} and mgr.endpoint.host == HOST and mgr.endpoint.port == 5070 and mgr.public == ("203.0.113.7", 5070)
    assert hass.states.get("sensor.front_intercom").attributes["nat_mode"] == "manual"
    # Shown again: what was saved.
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {CONF_ALLOW_CHANGES: True, CONF_RANGES: "1/1", "intercom": True})
    defaults = {str(k): k.default() for k in flow["data_schema"].schema}
    assert defaults["bind"] == HOST and defaults["port"] == 5070 and defaults["doors"] == [door]
    # Another options change (the lock) without the doors: no reload of the doors.
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"bind": HOST, "port": 5070, "public": "203.0.113.7:5070", "doors": [door]})
    await hass.async_block_till_done()
    assert entry.runtime_data.intercom is mgr


async def test_door_opens_by_its_linked_lock(hass: HomeAssistant, setup_doors, hik) -> None:
    """Р56: the integrator linked a smart lock to the door — «Открыть» goes to the lock (its OPEN when it has one), only
    for the people the lock lists; without the link the panel's own relay."""
    from homeassistant.components.lock import LockEntityFeature
    from homeassistant.core import Context
    from homeassistant.helpers import entity_registry as er

    from custom_components.hauflo import foreign

    entry = await setup_doors([DOOR])
    er.async_get(hass).async_get_or_create("lock", "zha", "door_lock", suggested_object_id="door_lock")
    hass.states.async_set("lock.door_lock", "locked", {"supported_features": int(LockEntityFeature.OPEN)})
    got = async_mock_service(hass, "lock", "open")
    async_mock_service(hass, "lock", "unlock")
    maria = await hass.auth.async_create_user("Мария", group_ids=["system-users"])
    guest = await hass.auth.async_create_user("Сосед", group_ids=["system-users"])
    foreign.flags(hass).set_locks({"lock.door_lock": {"door": "front", "users": [maria.id]}})
    await hass.async_block_till_done()
    await entry.runtime_data.locks.apply()
    # The wizard's «Замок» facts: the lock (it has OPEN), the people to give the right to, the door to link.
    info = await foreign.flags(hass).lock_info()
    assert info["seen"] == {"lock.door_lock": {"cloud": False, "open": True}} and info["doors"] == [{"id": "front", "name": "Front"}]
    assert {"id": maria.id, "name": "Мария"} in info["people"] and {"id": guest.id, "name": "Сосед"} in info["people"]
    await hass.services.async_call("button", "press", {"entity_id": "button.front_open"}, blocking=True, context=Context(user_id=maria.id))
    assert [c.data["entity_id"] for c in got] == ["lock.door_lock"] and got[0].context.user_id == maria.id and hik.got == []
    a = hass.states.get("sensor.front_intercom").attributes
    assert a["last_open"]["via"] == "lock" and a["last_open"]["ok"] is True
    # A neighbour with the panel in his app: «Нет прав», the lock never asked.
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("button", "press", {"entity_id": "button.front_open"}, blocking=True, context=Context(user_id=guest.id))
    assert err.value.translation_key == "intercom_open_no_rights" and len(got) == 1
    # The lock gone (removed / renamed): the client's «не открылась», not a crash.
    foreign.flags(hass).set_locks({"lock.gone": {"door": "front"}})
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("button", "press", {"entity_id": "button.front_open"}, blocking=True)
    assert err.value.translation_key == "intercom_open_failed"
    # Unlinked: the panel's relay again, read live (no reload).
    foreign.flags(hass).set_locks({})
    await hass.services.async_call("button", "press", {"entity_id": "button.front_open"}, blocking=True)
    assert hik.got[-1][1] == "/ISAPI/AccessControl/RemoteControl/door/2"
