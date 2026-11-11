"""Р55: the registration roles against the emulator — the panel registering on the agent (Hikvision: the agent is its
SIP server, digest required), the agent on a panel's own SIP server (Dahua VTO), the agent on a maker's cloud behind a
NAT (BAS-IP / Dahua cloud: the address the server saw, a CRLF keep-alive, the call's audio from any relay) — and the
plate's words when it doesn't work (wrong password, no answer, no such server)."""

from __future__ import annotations

import asyncio
import socket

import pytest
from homeassistant.core import Context, HomeAssistant

from custom_components.hauflo.const import DOMAIN
from custom_components.hauflo.intercom import door as door_mod, sip

from .intercom_fixtures import fast, setup_doors  # noqa: F401 — fixtures
from .sip_panel import HOST, Go2rtc, Media, Panel


async def _until(pred, timeout: float = 3) -> None:
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.01)


def _attrs(hass: HomeAssistant, door: str) -> dict:
    return dict(hass.states.get(f"sensor.{door}_intercom").attributes)


async def test_panel_registers_on_the_agent(hass: HomeAssistant, setup_doors) -> None:
    entry = await setup_doors([
        {"name": "Front", "preset": "hikvision", "host": HOST, "user": "admin", "password": "x", "sip_user": "101", "sip_password": "pw"},
        {"name": "Back", "preset": "hikvision", "host": "10.9.9.9", "sip_user": "102", "sip_password": "pw"},
        {"name": "Side", "preset": "hikvision"},  # no account: never registered, the plate says what is missing
    ])
    mgr = entry.runtime_data.intercom
    agent = (HOST, mgr.endpoint.port)
    assert _attrs(hass, "front")["problem"] == "panel_not_registered" and _attrs(hass, "front")["registered"] is False
    assert _attrs(hass, "side")["problem"] == "missing_sip_account"
    # The panel's video is its own RTSP (the preset's path with its login), the agent's sound a separate camera.
    cam = hass.data["camera"].get_entity("camera.front")
    assert await cam.stream_source() == f"rtsp://admin:x@{HOST}:554/Streaming/Channels/101"
    panel = await Panel().start()
    # No password: a challenge, never a binding.
    panel.register(agent, "101")
    chal = await panel.status(401, "REGISTER")
    challenge = sip.parse_auth(chal.get("www-authenticate") or "")
    assert challenge["realm"] == "hauflo" and challenge["nonce"]
    # The right password: 200, the binding, the plate.
    panel.register(agent, "101", auth=sip.authorization("REGISTER", f"sip:{HOST}", "101", "pw", challenge), expires=7200)
    ok = await panel.status(200, "REGISTER")
    assert sip.param(ok.get("contact") or "", "expires") == "3600"  # capped at the registrar's maximum
    await hass.async_block_till_done()
    a = _attrs(hass, "front")
    assert a["registered"] is True and a["problem"] == "no_calls_yet" and a["panel_contact"] == f"{HOST}:{panel.port}" and a["last_register_at"]
    # A wrong password: 403 and the plate says it; a nonce we never gave: 401 stale.
    panel.register(agent, "101", auth=sip.authorization("REGISTER", f"sip:{HOST}", "101", "nope", challenge))
    await panel.status(403, "REGISTER")
    await hass.async_block_till_done()
    assert _attrs(hass, "front")["error"] == "wrong_password"
    panel.register(agent, "101", auth=sip.authorization("REGISTER", f"sip:{HOST}", "101", "pw", {**challenge, "nonce": "old"}))
    stale = await panel.status(401, "REGISTER")
    assert "stale=true" in (stale.get("www-authenticate") or "")
    # An account nobody set: 404; the account of a door at another address: 403.
    panel.register(agent, "999")
    await panel.status(404, "REGISTER")
    panel.register(agent, "102")
    await panel.status(403, "REGISTER")
    # The registered panel calls: taken.
    media = await Media().start()
    inv = panel.invite(agent, dialled="101", rtp_port=media.port)
    await panel.status(180)
    assert mgr.doors["front"].state == "ringing"
    panel.in_dialog(inv, "CANCEL", agent, same_branch=True)
    await panel.status(487, "INVITE")
    # Unregistered (Expires 0): the plate says the panel isn't connected.
    panel.register(agent, "101", auth=sip.authorization("REGISTER", f"sip:{HOST}", "101", "pw", challenge), expires=0)
    await panel.status(200, "REGISTER")
    await hass.async_block_till_done()
    assert _attrs(hass, "front")["registered"] is False and _attrs(hass, "front")["problem"] == "panel_not_registered"
    panel.close()
    media.close()


async def test_panel_unregisters(hass: HomeAssistant, setup_doors) -> None:
    entry = await setup_doors([{"name": "Front", "preset": "hikvision", "host": HOST, "sip_user": "101", "sip_password": "pw"}])
    mgr = entry.runtime_data.intercom
    agent = (HOST, mgr.endpoint.port)
    panel = await Panel().start()
    panel.register(agent, "101")
    challenge = sip.parse_auth((await panel.status(401, "REGISTER")).get("www-authenticate") or "")
    auth = sip.authorization("REGISTER", f"sip:{HOST}", "101", "pw", challenge)
    panel.register(agent, "101", auth=auth)
    await panel.status(200, "REGISTER")
    assert mgr.doors["front"].registered() is True
    panel.register(agent, "101", auth=auth, contact="*", expires=0)
    await panel.status(200, "REGISTER")
    await hass.async_block_till_done()
    assert mgr.doors["front"].registered() is False and _attrs(hass, "front")["problem"] == "panel_not_registered"
    panel.close()


async def test_agent_registers_on_the_panel(hass: HomeAssistant, setup_doors, hass_admin_user) -> None:
    server = await Panel().start()
    server.server_user, server.server_password = "9901#1", "pw"
    entry = await setup_doors([{"name": "VTO", "preset": "dahua", "host": HOST, "sip_server": HOST, "sip_port": server.port, "sip_user": "9901#1", "sip_password": "pw"}])
    mgr = entry.runtime_data.intercom
    await _until(lambda: mgr.doors["vto"].registered())
    first, second = server.registers[:2]
    assert first.get("authorization") is None and second.get("authorization") and "sip:9901%231@" in (second.get("from") or "")
    assert first.get("expires") == "300" and sip.host_port(sip.uri_of(second.get("contact") or ""))[1] == mgr.endpoint.port
    await hass.async_block_till_done()
    a = _attrs(hass, "vto")
    assert a["registered"] is True and a["error"] is None and a["nat_mode"] == "lan" and a["public_address"] is None and a["last_register_at"]
    # The panel's server calls the agent's extension: taken (its address is the server's).
    media = await Media().start()
    agent = (HOST, mgr.endpoint.port)
    inv = server.invite(agent, dialled="9901#1", rtp_port=media.port)
    await server.status(180)
    await hass.services.async_call(DOMAIN, "intercom_answer", {"entity_id": "sensor.vto_intercom"}, blocking=True, context=Context(user_id=hass_admin_user.id))
    ok = await server.status(200, "INVITE")
    assert "9901%231" in (ok.get("contact") or "")
    server.in_dialog(inv, "ACK", agent, ok=ok)
    # Unloaded: the agent unregisters (Expires 0) — no call goes into the void.
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert any(m.get("expires") == "0" for m in server.registers)
    server.close()
    media.close()


async def test_cloud_behind_a_nat(hass: HomeAssistant, setup_doors, hass_admin_user) -> None:
    cloud = await Panel().start()
    cloud.server_user, cloud.server_password = "555", "pw"
    cloud.seen_at = ("203.0.113.9", 41000)  # the router's address the cloud sees
    entry = await setup_doors([{"name": "Gate", "preset": "basip_cloud", "sip_server": HOST, "sip_port": cloud.port, "sip_user": "555", "sip_password": "pw"}])
    mgr = entry.runtime_data.intercom
    d = mgr.doors["gate"]
    await _until(lambda: d.registration is not None and d.registration.public is not None and d.registered())
    # Registered again with the address the cloud saw as the Contact; every 60 s (the cloud's NAT forgets fast).
    assert any("203.0.113.9:41000" in (m.get("contact") or "") and m.get("authorization") for m in cloud.registers)
    assert all(m.get("expires") == "60" for m in cloud.registers)
    # A CRLF keeps the router's way open between the refreshes.
    await _until(lambda: b"\r\n\r\n" in cloud.raw)
    await hass.async_block_till_done()
    a = _attrs(hass, "gate")
    assert a["nat_mode"] == "observed" and a["public_address"] == "203.0.113.9:41000" and a["problem"] == "no_calls_yet" and a["mode"] == "cloud"
    # The cloud calls; the answer's SDP names the outside address; the audio comes from a relay nobody named.
    media = await Media().start()
    agent = (HOST, mgr.endpoint.port)
    # The cloud's INVITE comes back through the very socket the REGISTERs left from (the router's hole is that one).
    assert {src for m, src in cloud.got if m.method == "REGISTER"} == {agent}
    talk = hass.data["camera"].get_entity("camera.gate_talk")
    rtsp = await Go2rtc(await talk.stream_source()).connect()
    await rtsp.play()
    inv = cloud.invite(agent, dialled="555", rtp_port=media.port, sdp_addr="192.168.1.50", offer="8 0 101")  # the panel's LAN address in its SDP
    await cloud.status(180)
    await hass.services.async_call(DOMAIN, "intercom_answer", {"entity_id": "sensor.gate_intercom"}, blocking=True, context=Context(user_id=hass_admin_user.id))
    ok = await cloud.status(200, "INVITE")
    m = sip.parse_sdp(ok.body)
    # BAS-IP's preset: PCMU first even when the offer leads with PCMA; DTMF by RFC 2833 named explicitly.
    assert m is not None and m.addr == "203.0.113.9" and m.pts[0] == 0 and m.dtmf_pt == 101
    cloud.in_dialog(inv, "ACK", agent, ok=ok)
    media.send((HOST, d.call.rtp.port), b"\x01" * 160, pt=0)
    await _until(lambda: d.call.rtp.rx == 1)
    # Symmetric RTP: the agent sends where the audio came from, not where the SDP said (a LAN address behind the cloud).
    assert d.call.rtp.latched == (HOST, media.port)
    rtsp.talk(b"\x02" * 160, 0)
    await _until(lambda: any(p[0] == 0 and p[3] == b"\x02" * 160 for p in media.packets))
    await hass.services.async_call("button", "press", {"entity_id": "button.gate_open"}, blocking=True, context=Context(user_id=hass_admin_user.id))
    dtmf = [p for p in media.packets if p[0] == 101]
    assert dtmf and all(p[3][0] == 11 for p in dtmf)  # «#»
    await asyncio.sleep(0.1)
    assert _attrs(hass, "gate")["call_audio"] == "bidirectional"
    await rtsp.close()
    # Where the SDP said and where the audio really came from: both on the plate (the «0 bytes» diagnosis).
    await hass.services.async_call(DOMAIN, "intercom_hangup", {"entity_id": "sensor.gate_intercom"}, blocking=True, context=Context(user_id=hass_admin_user.id))
    assert d.last_call["sdp_remote"] == f"192.168.1.50:{media.port}" and d.last_call["rtp_from"] == f"{HOST}:{media.port}"
    await cloud.request("BYE")
    media.close()
    cloud.close()


async def test_registration_problems(hass: HomeAssistant, setup_doors, monkeypatch) -> None:
    wrong = await Panel().start()
    wrong.server_user, wrong.server_password = "1", "other"
    silent = await Panel().start()
    silent.answers.clear()  # a socket that never answers REGISTER (no server_password: ignored)
    real = asyncio.get_running_loop().getaddrinfo

    async def lookup(host, *args, **kw):
        if host == "cloud.invalid":
            raise socket.gaierror("no such name")
        return await real(host, *args, **kw)

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    entry = await setup_doors([
        {"name": "Wrong", "preset": "dahua", "host": HOST, "sip_server": HOST, "sip_port": wrong.port, "sip_user": "1", "sip_password": "pw"},
        {"name": "Silent", "preset": "dahua", "host": HOST, "sip_server": HOST, "sip_port": silent.port, "sip_user": "2", "sip_password": "pw"},
        {"name": "Nowhere", "preset": "dahua_cloud", "sip_server": "cloud.invalid", "sip_user": "3", "sip_password": "pw"},
        {"name": "Nosrv", "preset": "dahua_cloud", "sip_user": "4", "sip_password": "pw"},
    ])
    mgr = entry.runtime_data.intercom
    await _until(lambda: mgr.doors["wrong"].registration is not None and mgr.doors["wrong"].registration.error is not None)
    await _until(lambda: mgr.doors["silent"].registration is not None and mgr.doors["silent"].registration.error == "no_answer", 5)
    await hass.async_block_till_done()
    assert _attrs(hass, "wrong")["problem"] == "not_registered" and _attrs(hass, "wrong")["error"].startswith("403")
    assert _attrs(hass, "silent")["problem"] == "not_registered" and _attrs(hass, "silent")["error"] == "no_answer"
    assert _attrs(hass, "nowhere")["problem"] == "server_not_found"
    assert _attrs(hass, "nosrv")["problem"] == "missing_sip_server"
    wrong.close()
    silent.close()


async def test_register_extra_and_the_dial_plan(hass: HomeAssistant, setup_doors, monkeypatch) -> None:
    """The extension point of presets.json (a Hikvision monitor's regXML): its body goes in every REGISTER."""
    server = await Panel().start()
    server.server_user, server.server_password = "7", "pw"
    monkeypatch.setitem(door_mod.PRESETS, "monitor", {**door_mod.PRESETS["dahua"], "label": "Monitor", "register_extra": {"content_type": "application/xml", "body": "<reg user=\"{user}\"/>"}})
    entry = await setup_doors([{"name": "Mon", "preset": "monitor", "host": HOST, "user": "a b", "sip_server": HOST, "sip_port": server.port, "sip_user": "7", "sip_password": "pw"}])
    mgr = entry.runtime_data.intercom
    await _until(lambda: mgr.doors["mon"].registered())
    assert server.registers[0].body == b'<reg user="a%20b"/>' and server.registers[0].get("content-type") == "application/xml"
    server.close()
