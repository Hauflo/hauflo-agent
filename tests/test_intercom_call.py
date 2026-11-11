"""Р55: a door call end to end against the panel emulator (tests/sip_panel.py) — ring → push → answer (one talker) →
sound both ways through the agent's RTSP (go2rtc's part) → the door opened by DTMF → hang up; and every way a call
ends otherwise (the panel gives up, answered on the indoor monitor, nobody answers, busy, no G.711, a stranger)."""

from __future__ import annotations

import asyncio
import struct

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.hauflo import notify
from custom_components.hauflo.const import DOMAIN
from custom_components.hauflo.intercom import EVENT, sip
from custom_components.hauflo.tuning import TUNING

from .intercom_fixtures import fast, setup_doors  # noqa: F401 — fixtures
from .sip_panel import HOST, Go2rtc, Media, Panel

DOOR = {"name": "Калитка", "preset": "generic", "host": HOST, "dtmf": "#"}
SENSOR, CALL, OPEN, TALK = "sensor.kalitka_intercom", "event.kalitka_call", "button.kalitka_open", "camera.kalitka_talk"
VOICE = bytes(range(1, 161))  # 20 ms of «speech»


@pytest.fixture
async def door(hass: HomeAssistant, setup_doors):
    entry = await setup_doors([DOOR])
    mgr = entry.runtime_data.intercom
    panel, media = await Panel().start(), await Media().start()
    yield entry, mgr, panel, media, (HOST, mgr.endpoint.port)
    panel.close()
    media.close()


def _attrs(hass: HomeAssistant) -> dict:
    return dict(hass.states.get(SENSOR).attributes)


async def _ring(hass: HomeAssistant, panel: Panel, agent, media: Media, **kw) -> sip.Message:
    inv = panel.invite(agent, rtp_port=media.port, **kw)
    await panel.status(180)
    await hass.async_block_till_done()
    return inv


async def _answer(hass: HomeAssistant, panel: Panel, agent, inv: sip.Message, user_id: str | None, ack: bool = True) -> tuple[sip.Message, tuple[str, int]]:
    await hass.services.async_call(DOMAIN, "intercom_answer", {"entity_id": SENSOR}, blocking=True, context=Context(user_id=user_id))
    ok = await panel.status(200, "INVITE")
    if ack:
        panel.in_dialog(inv, "ACK", agent, ok=ok)
    m = sip.parse_sdp(ok.body)
    assert m is not None
    await hass.async_block_till_done()
    return ok, (m.addr, m.port)


async def _until(pred, timeout: float = 2) -> None:
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.01)


async def test_the_whole_call(hass: HomeAssistant, door, hass_admin_user, hass_read_only_user) -> None:
    entry, mgr, panel, media, agent = door
    eng = entry.runtime_data.engine
    eng.phones = lambda: [{"service": "mobile_app_p", "os": "android", "person": "x"}]
    eng.scope = lambda phone, config=None: "all"
    eng.config = {**eng.config, "lang": "ru"}
    pushes = async_mock_service(hass, "notify", "mobile_app_p")
    bus: list[dict] = []
    hass.bus.async_listen(EVENT, lambda e: bus.append(dict(e.data)))
    for eid in (SENSOR, CALL, OPEN, TALK, "camera.kalitka"):
        assert (hass.states.get(eid) is not None) is (eid != "camera.kalitka"), eid  # the generic preset has no video
    a = _attrs(hass)
    assert a["problem"] == "no_calls_yet" and a["audio_path"] == "webrtc" and a["registered"] is None and a["listen"] == f"{HOST}:{agent[1]}"
    assert a["talk_camera"] == TALK and a["open_button"] == OPEN and a["camera"] is None and a["nat_mode"] == "lan"

    # go2rtc already plays the talk camera (the card opened): silence until the call, never nothing.
    cam = hass.data["camera"].get_entity(TALK)
    url = await cam.stream_source()
    assert url.startswith(f"rtsp://127.0.0.1:{mgr.rtsp.port}/door/kalitka/") and mgr.rtsp.token in url
    assert await cam.async_camera_image() is None
    rtsp = await Go2rtc(url).connect()
    await rtsp.play()
    ch, payload = await rtsp.frame()
    assert ch == 0 and payload == b"\xd5" * 160

    # The ring: 100 + 180, the doorbell event (the push), the bus event.
    inv = await _ring(hass, panel, agent, media)
    assert hass.states.get(SENSOR).state == "ringing"
    assert hass.states.get(CALL).attributes["event_type"] == "ring" and hass.states.get(CALL).attributes["from"] == "door"
    assert (await panel.status(100)).status == 100
    assert len(pushes) == 1 and pushes[0].data["title"].startswith("🔔")
    # The push never opens the door: its actions are only notify.RUN_SERVICES, none is a button / lock.
    assert all(s.split(".")[0] not in ("button", "lock") for s in notify.RUN_SERVICES)
    assert "button.kalitka_open" not in str(pushes[0].data)
    assert bus[-1]["state"] == "ringing" and bus[-1]["entity_id"] == SENSOR

    # Answer: 200 with our SDP (PCMA + telephone-event), the talker is this person.
    ok, rtp_to = await _answer(hass, panel, agent, inv, hass_admin_user.id)
    assert b"m=audio" in ok.body and b"telephone-event" in ok.body and rtp_to[0] == HOST
    assert sip.uri_of(ok.get("contact") or "").startswith(f"sip:kalitka@{HOST}")
    a = _attrs(hass)
    assert hass.states.get(SENSOR).state == "talking" and a["talker"] == hass_admin_user.id and bus[-1]["state"] == "talking"
    # The NAT punch: one silent frame first, with the marker.
    await _until(lambda: media.packets)
    assert media.packets[0][2] is True and media.packets[0][3] == b"\xd5" * 160
    # Answered by one: another person can't take it, nor hang it up (an admin can).
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "intercom_answer", {"entity_id": SENSOR}, blocking=True, context=Context(user_id=hass_read_only_user.id))
    assert err.value.translation_key == "intercom_answered_elsewhere"
    await hass.services.async_call(DOMAIN, "intercom_answer", {"entity_id": SENSOR}, blocking=True, context=Context(user_id=hass_admin_user.id))  # the same one: nothing
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "intercom_hangup", {"entity_id": CALL}, blocking=True, context=Context(user_id=hass_read_only_user.id))
    assert err.value.translation_key == "intercom_not_yours"

    # Sound: panel → agent → go2rtc (the card hears), card → go2rtc backchannel → agent → panel.
    media.send(rtp_to, VOICE)
    await _until(lambda: entry.runtime_data.intercom.doors["kalitka"].call.rtp.rx > 0)
    for _ in range(10):
        ch, payload = await rtsp.frame()
        if payload == VOICE:
            break
    assert payload == VOICE and ch == 0
    rtsp.talk(VOICE, 8)
    await _until(lambda: any(p[3] == VOICE for p in media.packets))
    await asyncio.sleep(0.1)  # the plate's counters refresh while talking
    a = _attrs(hass)
    assert a["call_audio"] == "bidirectional" and a["rtp_bytes_in"] == 160 and a["rtp_bytes_out"] >= 320 and a["audio"]["from_card_packets"] == 1
    assert a["problem"] is None

    # «Открыть» (DTMF #, RFC 4733): event 11, the end bit three times.
    await hass.services.async_call("button", "press", {"entity_id": OPEN}, blocking=True, context=Context(user_id=hass_admin_user.id))
    dtmf = [p for p in media.packets if p[0] == 101]
    assert dtmf and all(p[3][0] == 11 for p in dtmf) and sum(1 for p in dtmf if p[3][1] & 0x80) == 3
    a = _attrs(hass)
    assert a["last_open"]["ok"] is True and a["last_open"]["via"] == "dtmf" and a["last_open"]["by"] == hass_admin_user.id
    assert bus[-1]["state"] == "opened"

    # Hang up: BYE in the panel's dialog (From / To swapped, its Record-Route as a Route), idle, the call's record.
    await hass.services.async_call(DOMAIN, "intercom_hangup", {"entity_id": SENSOR}, blocking=True, context=Context(user_id=hass_admin_user.id))
    bye = await panel.request("BYE")
    assert bye.call_id == inv.call_id and bye.get("route") and "tag=" in (bye.get("from") or "") and bye.get("to") == inv.get("from")
    await hass.async_block_till_done()
    a = _attrs(hass)
    assert hass.states.get(SENSOR).state == "idle" and a["talker"] is None
    assert a["last_call"]["result"] == "hung_up" and a["last_call"]["answered"] is True and a["last_call"]["rx_packets"] == 1
    assert a["call_audio"] == "bidirectional" and a["problem"] is None and a["rtp_bytes_in"] == 160
    assert bus[-1] == {"door": "kalitka", "state": "idle", "user_id": None, "result": "hung_up", "entity_id": SENSOR, "call": "event.kalitka_call"}
    # After the call the backchannel goes nowhere (no call: nothing reaches the panel).
    before = len(media.packets)
    rtsp.talk(VOICE, 8)
    await asyncio.sleep(0.05)
    assert len(media.packets) == before
    await rtsp.close()


async def test_panel_ends_and_retransmissions(hass: HomeAssistant, door, hass_admin_user) -> None:
    _entry, mgr, panel, media, agent = door
    inv = await _ring(hass, panel, agent, media)
    # The panel didn't hear the 180: the same INVITE again gets the same answer, nothing new happens.
    panel.send(inv, agent)
    await panel.status(180)
    # A re-INVITE of the ringing call (a new branch, the same Call-ID): 180 again.
    re = sip.Message(method="INVITE", uri=inv.uri, headers=[h for h in inv.headers if h[0] != "Via"] + [("Via", f"SIP/2.0/UDP {HOST}:{panel.port};branch={sip.new_branch()}")], body=inv.body)
    re.set("CSeq", f"{inv.cseq[0] + 1} INVITE")
    panel.send(re, agent)
    await panel.status(180)
    # A second call while one rings: busy.
    other = Panel()
    await other.start()
    other.invite(agent)
    assert (await other.status(486)).status == 486
    other.close()
    ok, rtp_to = await _answer(hass, panel, agent, inv, hass_admin_user.id)
    # A re-INVITE in the call (the panel moves its audio): 200 with our SDP again, the new address taken.
    media2 = await Media().start()
    re2 = sip.Message(method="INVITE", uri=inv.uri, headers=list(re.headers), body=inv.body.replace(str(media.port).encode(), str(media2.port).encode()))
    re2.set("Via", f"SIP/2.0/UDP {HOST}:{panel.port};branch={sip.new_branch()}")
    re2.set("CSeq", f"{inv.cseq[0] + 2} INVITE")
    panel.send(re2, agent)
    ok2 = await panel.wait(lambda m: m.status == 200 and m.cseq == (inv.cseq[0] + 2, "INVITE"))
    assert b"m=audio" in ok2.body and mgr.doors["kalitka"].call.rtp.remote == (HOST, media2.port)
    # In-dialog INFO and OPTIONS: 200.
    panel.in_dialog(inv, "INFO", agent, cseq=inv.cseq[0] + 3)
    await panel.status(200, "INFO")
    panel.plain("OPTIONS", agent)
    opt = await panel.status(200, "OPTIONS")
    assert "INVITE" in (opt.get("allow") or "")
    # The panel hangs up.
    panel.in_dialog(inv, "BYE", agent, ok=ok, cseq=inv.cseq[0] + 4)
    await panel.status(200, "BYE")
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == "idle" and _attrs(hass)["last_call"]["result"] == "ended_by_panel"
    # Nothing came from the panel in that call: the plate says where to look.
    assert _attrs(hass)["problem"] == "no_audio_from_panel" and _attrs(hass)["call_audio"] == "none"
    media2.close()


async def test_cancel_elsewhere_missed_declined(hass: HomeAssistant, door, hass_admin_user) -> None:
    _entry, mgr, panel, media, agent = door
    # The indoor monitor picked up: CANCEL with «cause=200» → 200, 487, «ответили на другом устройстве», then idle.
    inv = await _ring(hass, panel, agent, media)
    panel.in_dialog(inv, "CANCEL", agent, same_branch=True, headers=[("Reason", 'SIP;cause=200;text="Call completed elsewhere"')])
    await panel.status(200, "CANCEL")
    await panel.status(487, "INVITE")
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == "answered_elsewhere"
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "intercom_answer", {"entity_id": SENSOR}, blocking=True, context=Context(user_id=hass_admin_user.id))
    assert err.value.translation_key == "intercom_no_call"
    await _until(lambda: hass.states.get(SENSOR).state == "idle")
    assert _attrs(hass)["last_call"]["result"] == "answered_elsewhere" and _attrs(hass)["call_audio"] is None
    # The visitor walked away: a plain CANCEL → missed.
    inv = await _ring(hass, panel, agent, media)
    panel.in_dialog(inv, "CANCEL", agent, same_branch=True)
    await panel.status(487, "INVITE")
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == "idle" and _attrs(hass)["last_call"]["result"] == "missed"
    # A CANCEL / BYE of no call: 481; an ACK of none: silence.
    panel.in_dialog(inv, "CANCEL", agent, same_branch=False)
    await panel.status(481, "CANCEL")
    panel.in_dialog(inv, "BYE", agent)
    await panel.status(481, "BYE")
    panel.in_dialog(inv, "ACK", agent)
    # Declined from the app while ringing: 603.
    inv = await _ring(hass, panel, agent, media)
    await hass.services.async_call(DOMAIN, "intercom_hangup", {"entity_id": OPEN}, blocking=True, context=Context(user_id=hass_admin_user.id))
    await panel.status(603, "INVITE")
    await hass.async_block_till_done()
    assert _attrs(hass)["last_call"]["result"] == "declined"
    # Nothing to hang up / answer now.
    for service in ("intercom_hangup", "intercom_answer"):
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(DOMAIN, service, {"entity_id": SENSOR}, blocking=True, context=Context(user_id=hass_admin_user.id))
        assert err.value.translation_key == "intercom_no_call"
    # An entity that isn't a door's.
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "intercom_answer", {"entity_id": "light.x"}, blocking=True)
    assert err.value.translation_key == "intercom_unknown_door"
    # A CANCEL after the answer only gets its 200 (the call goes on; the panel then sends BYE).
    inv = await _ring(hass, panel, agent, media)
    await _answer(hass, panel, agent, inv, hass_admin_user.id)
    panel.in_dialog(inv, "CANCEL", agent, same_branch=False)
    await panel.status(200, "CANCEL")
    assert hass.states.get(SENSOR).state == "talking"
    # An automation (no user) may hang up anybody's call.
    await hass.services.async_call(DOMAIN, "intercom_hangup", {"entity_id": SENSOR}, blocking=True)
    await panel.request("BYE")


async def test_nobody_answers(hass: HomeAssistant, door, monkeypatch) -> None:
    _entry, _mgr, panel, media, agent = door
    monkeypatch.setitem(TUNING["intercom"], "ring_timeout_s", 0.1)
    await _ring(hass, panel, agent, media)
    await panel.status(480, "INVITE")
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == "idle" and _attrs(hass)["last_call"]["result"] == "missed"


async def test_no_g711_and_no_sdp(hass: HomeAssistant, door) -> None:
    _entry, _mgr, panel, media, agent = door
    panel.invite(agent, offer="18", names={18: "G729"})
    await panel.status(488)
    await hass.async_block_till_done()
    a = _attrs(hass)
    assert a["problem"] == "codec_mismatch" and a["last_call"]["offered"] == ["G729"] and hass.states.get(SENSOR).state == "idle"
    panel.invite(agent, body=b"")
    await panel.status(488)
    # μ-law only: taken (the hub's A-law is converted).
    panel.invite(agent, offer="0", names={0: "PCMU"}, rtp_port=media.port)
    await panel.status(180)


async def test_no_ack_ends_the_call(hass: HomeAssistant, door, hass_admin_user) -> None:
    _entry, _mgr, panel, media, agent = door
    inv = await _ring(hass, panel, agent, media)
    await _answer(hass, panel, agent, inv, hass_admin_user.id, ack=False)
    # The 200 is retransmitted (T1 doubling) until timer F, then the call is ended with a BYE.
    await panel.request("BYE", timeout=3)
    assert sum(1 for m, _ in panel.got if m.status == 200 and m.cseq[1] == "INVITE") >= 3
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == "idle" and _attrs(hass)["problem"] == "no_ack"


async def test_silent_call_diagnosis(hass: HomeAssistant, door, hass_admin_user) -> None:
    _entry, mgr, panel, media, agent = door
    inv = await _ring(hass, panel, agent, media)
    _ok, rtp_to = await _answer(hass, panel, agent, inv, hass_admin_user.id)
    await asyncio.sleep(0.1)
    d = mgr.doors["kalitka"]
    assert d.problem() == "no_audio_from_panel" and d.call_audio() == "none"
    # Packets from an address that isn't the panel's are dropped and counted, never relayed.
    d.call.rtp.datagram_received(b"\x80\x08" + b"\x00" * 10 + VOICE, ("10.1.1.1", 5000))
    assert d.call.rtp.foreign == 1 and d.call.rtp.rx == 0
    media.send(rtp_to, VOICE)
    await _until(lambda: d.call.rtp.rx == 1)
    assert d.problem() == "no_audio_to_panel" and d.call_audio() == "no_tx"
    # Once latched, another port of another address is foreign too; garbage isn't RTP.
    d.call.rtp.datagram_received(b"junk", ("10.1.1.1", 5000))
    d.call.rtp.datagram_received(b"junk", d.call.rtp.latched)
    assert d.call.rtp.foreign == 2


async def test_dtmf_by_sip_info(hass: HomeAssistant, door, hass_admin_user) -> None:
    _entry, _mgr, panel, media, agent = door
    # Not in a call: a DTMF door can't open.
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("button", "press", {"entity_id": OPEN}, blocking=True)
    assert err.value.translation_key == "intercom_open_needs_call"
    assert _attrs(hass)["last_open"]["ok"] is False and _attrs(hass)["last_open"]["error"] == "open_needs_call"
    # A panel without telephone-event: SIP INFO «Signal=#».
    inv = await _ring(hass, panel, agent, media, offer="8", names={8: "PCMA"})
    await _answer(hass, panel, agent, inv, hass_admin_user.id)
    await hass.services.async_call("button", "press", {"entity_id": OPEN}, blocking=True)
    info = await panel.request("INFO")
    assert info.body.startswith(b"Signal=#") and info.get("content-type") == "application/dtmf-relay"
    # The panel refuses the INFO: «не открылась».
    panel.answers["INFO"] = 403
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("button", "press", {"entity_id": OPEN}, blocking=True)
    assert err.value.translation_key == "intercom_open_failed"


async def test_strangers_and_odd_requests(hass: HomeAssistant, setup_doors) -> None:
    entry = await setup_doors([{"name": "Ворота", "preset": "generic", "host": "10.9.9.9"}])
    mgr = entry.runtime_data.intercom
    agent = (HOST, mgr.endpoint.port)
    panel = await Panel(user="stranger").start()
    panel.invite(agent, dialled="vorota")
    await panel.status(403, "INVITE")
    await hass.async_block_till_done()
    a = dict(hass.states.get("sensor.vorota_intercom").attributes)
    assert a["problem"] == "call_from_unknown_address" and a["last_rejected"]["source"].startswith(HOST) and a["last_rejected"]["from"] == "stranger"
    assert hass.states.get("sensor.vorota_intercom").state == "idle"
    panel.plain("OPTIONS", agent)
    await panel.status(403, "OPTIONS")
    panel.plain("MESSAGE", agent)
    await panel.status(501, "MESSAGE")
    # Not SIP, keep-alives, an answer nobody waits for: ignored, the socket lives on.
    assert panel.transport is not None
    for junk in (b"\r\n\r\n", b"hello", b"SIP/2.0 abc", b"SIP/2.0 200 OK\r\nVia: SIP/2.0/UDP x;branch=z9hG4bKnone\r\n\r\n"):
        panel.transport.sendto(junk, agent)
    panel.plain("OPTIONS", agent)
    await panel.status(403, "OPTIONS")
    panel.close()


async def test_stop_ends_calls(hass: HomeAssistant, setup_doors, hass_admin_user) -> None:
    entry = await setup_doors([DOOR, {**DOOR, "name": "Калитка", "host": HOST}])  # the same name twice: two ids
    mgr = entry.runtime_data.intercom
    assert set(mgr.doors) == {"kalitka", "kalitka_2"}
    agent = (HOST, mgr.endpoint.port)
    a, b = await Panel().start(), await Panel().start()
    media = await Media().start()
    # Two doors on one address: the dialled number decides (the door's id).
    inv = a.invite(agent, dialled="kalitka_2", rtp_port=media.port)
    await a.status(180)
    assert mgr.doors["kalitka_2"].state == "ringing" and mgr.doors["kalitka"].state == "idle"
    b.invite(agent, dialled="kalitka", rtp_port=media.port)
    await b.status(180)
    await hass.services.async_call(DOMAIN, "intercom_answer", {"entity_id": "sensor.kalitka_2_intercom"}, blocking=True, context=Context(user_id=hass_admin_user.id))
    await a.status(200, "INVITE")
    assert [d.cfg.id for d in mgr.talking()] == ["kalitka_2"]
    # Unloading: the ringing one gets 480, the talking one a BYE; no socket stays.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await b.status(480, "INVITE")
    bye = await a.request("BYE")
    assert bye.call_id == inv.call_id and mgr.endpoint.transport is None and mgr.rtsp is None
    for p in (a, b, media):
        p.close()
