"""Р55: the four ways a panel and the agent find each other are equal — the same call, sound both ways and «Открыть» in
each, on the emulator: direct (the panel calls the agent), register (the agent is an extension of the panel's server),
registrar (the panel is an extension of the agent), cloud (the agent is an extension of the maker's cloud, behind a NAT)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from homeassistant.core import Context, HomeAssistant

from custom_components.hauflo.const import DOMAIN
from custom_components.hauflo.intercom import door as door_mod, sip

from .intercom_fixtures import fast, setup_doors  # noqa: F401 — fixtures
from .sip_panel import HOST, Go2rtc, Media, Panel

ACCOUNT = {"sip_user": "201", "sip_password": "pw"}
OPEN = {"open": "dtmf", "dtmf": "#"}  # the same opening in every mode (each preset's own is tested elsewhere)


async def _until(pred, timeout: float = 3) -> None:
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.01)


async def _connect(mode: str, setup_doors) -> tuple:
    """The door of `mode`, the socket the calls come from, the agent's address."""
    panel = await Panel(user="201").start()
    if mode == "direct":
        entry = await setup_doors([{"name": "Door", "preset": "basip", "host": HOST, **OPEN}])
    elif mode == "registrar":
        entry = await setup_doors([{"name": "Door", "preset": "hikvision", "host": HOST, **ACCOUNT, **OPEN}])
        agent = (HOST, entry.runtime_data.intercom.endpoint.port)
        panel.register(agent, "201")
        challenge = sip.parse_auth((await panel.status(401, "REGISTER")).get("www-authenticate") or "")
        panel.register(agent, "201", auth=sip.authorization("REGISTER", f"sip:{HOST}", "201", "pw", challenge))
        await panel.status(200, "REGISTER")
    else:
        panel.server_user, panel.server_password = "201", "pw"
        if mode == "cloud":
            panel.seen_at = ("203.0.113.9", 41000)
        preset = "dahua" if mode == "register" else "basip_cloud"
        entry = await setup_doors([{"name": "Door", "preset": preset, "host": HOST, "sip_server": HOST, "sip_port": panel.port, **ACCOUNT, **OPEN}])
    mgr = entry.runtime_data.intercom
    await _until(lambda: mgr.doors["door"].registered() is not False)
    return entry, mgr, panel, (HOST, mgr.endpoint.port)


@pytest.mark.parametrize("mode", list(door_mod.MODES))
async def test_every_mode_the_same_call(hass: HomeAssistant, setup_doors, hass_admin_user, mode: str) -> None:
    entry, mgr, panel, agent = await _connect(mode, setup_doors)
    d = mgr.doors["door"]
    assert d.cfg.mode == mode
    await hass.async_block_till_done()
    a = dict(hass.states.get("sensor.door_intercom").attributes)
    assert a["mode"] == mode and a["problem"] == "no_calls_yet" and a["audio_path"] == "webrtc"
    assert a["registered"] is (None if mode == "direct" else True)
    rtsp = await Go2rtc(await hass.data["camera"].get_entity("camera.door_talk").stream_source()).connect()
    await rtsp.play()
    media = await Media().start()
    # The ring: the doorbell's event, the plate rings.
    inv = panel.invite(agent, dialled="201", rtp_port=media.port)
    await panel.status(180)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.door_intercom").state == "ringing"
    assert hass.states.get("event.door_call").attributes["event_type"] == "ring"
    # Answer → 200 with G.711 + telephone-event, ACK.
    await hass.services.async_call(DOMAIN, "intercom_answer", {"entity_id": "sensor.door_intercom"}, blocking=True, context=Context(user_id=hass_admin_user.id))
    ok = await panel.status(200, "INVITE")
    m = sip.parse_sdp(ok.body)
    assert m is not None and m.dtmf_pt == 101 and m.pts[0] == (0 if d.cfg.codec == "PCMU" else 8)
    assert m.addr == ("203.0.113.9" if mode == "cloud" else HOST)
    panel.in_dialog(inv, "ACK", agent, ok=ok)
    # Sound both ways: panel → agent → card; card (go2rtc backchannel) → agent → panel, where the panel's audio came from.
    voice = bytes(range(1, 161))
    media.send((HOST, d.call.rtp.port), voice, pt=m.pts[0])
    silence = (b"\xd5" * 160, b"\xff" * 160)  # G.711 A-law / µ-law
    for _ in range(20):
        _ch, frame = await rtsp.frame()
        if frame not in silence:
            break
    assert frame not in silence
    if mode == "cloud":  # the SDP named an address the audio didn't come from: sent back where it came from
        assert d.call.rtp.latched == (HOST, media.port)
    rtsp.talk(voice, m.pts[0])
    await _until(lambda: any(p[0] == m.pts[0] and p[3] == voice for p in media.packets))
    # «Открыть»: DTMF # (RFC 4733) into the call.
    await hass.services.async_call("button", "press", {"entity_id": "button.door_open"}, blocking=True, context=Context(user_id=hass_admin_user.id))
    assert any(p[0] == 101 and p[3][0] == 11 for p in media.packets)
    await asyncio.sleep(0.1)
    a = dict(hass.states.get("sensor.door_intercom").attributes)
    assert a["call_audio"] == "bidirectional" and a["rtp_bytes_in"] >= 160 and a["rtp_bytes_out"] >= 160 and a["problem"] is None
    # Hang up: BYE to the panel, the plate is idle again.
    await hass.services.async_call(DOMAIN, "intercom_hangup", {"entity_id": "sensor.door_intercom"}, blocking=True, context=Context(user_id=hass_admin_user.id))
    await _until(lambda: any(msg.method == "BYE" for msg, _src in panel.got))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.door_intercom").state == "idle"
    assert d.last_call["result"] == "hung_up" and d.last_call["answered"] is True
    await rtsp.close()
    media.close()
    panel.close()


def test_every_preset_named_in_both_languages() -> None:
    """Each preset is a choice in the door's form in RU + EN; one not tried on a real panel says it is by the maker's docs."""
    root = Path(door_mod.__file__).parents[1]
    en = json.loads((root / "translations/en.json").read_text(encoding="utf-8"))["selector"]["intercom_preset"]["options"]
    ru = json.loads((root / "translations/ru.json").read_text(encoding="utf-8"))["selector"]["intercom_preset"]["options"]
    assert set(en) == set(ru) == set(door_mod.PRESETS)
    for key, preset in door_mod.PRESETS.items():
        assert preset["mode"] in door_mod.MODES and preset["codec"] in sip.CODECS, key
        if key != "generic" and not preset.get("checked"):
            assert "documentation" in en[key] and "документации" in ru[key], key
    assert set(door_mod.MODES) <= {p["mode"] for p in door_mod.PRESETS.values()}  # every way has a preset
