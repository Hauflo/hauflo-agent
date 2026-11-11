"""Р55: the protocol pieces on their own — SIP parsing and digest, RTP framing and G.711, the loopback RTSP's refusals."""

from __future__ import annotations

import asyncio
import socket

import pytest

from custom_components.hauflo.intercom import rtp, sip
from custom_components.hauflo.intercom.rtsp import AudioHub, RtspServer

from .sip_panel import HOST, Go2rtc


def test_sip_parsing() -> None:
    folded = b"INVITE sip:1@h SIP/2.0\r\nSubject: one\r\n two\r\nCSeq: x INVITE\r\nContent-Length: 2\r\n\r\nabcdef"
    m = sip.parse(folded)
    assert m.get("subject") == "one two" and m.cseq == (0, "INVITE") and m.body == b"ab"
    lf = sip.parse(b"SIP/2.0 200 OK\nCall-ID: c\n\nbody")
    assert lf.status == 200 and lf.call_id == "c" and lf.body == b"body"
    for junk in (b"", b"\r\n\r\n", b"SIP/2.0 OK\r\n\r\n", b"HELLO world\r\n\r\n"):
        with pytest.raises(ValueError):
            sip.parse(junk)
    assert sip._split_commas('"a, b" <sip:x>, <sip:y,z>;q=1, w') == ['"a, b" <sip:x>', "<sip:y,z>;q=1", "w"]
    assert sip.uri_of("sip:1@h;tag=x") == "sip:1@h" and sip.param('<sip:a;lr>;expires="60"', "expires") == "60"
    assert sip.host_port("sip:1@10.0.0.5") == ("10.0.0.5", 5060) and sip.host_port("sip:1@10.0.0.5:5070;transport=udp") == ("10.0.0.5", 5070)
    assert sip.is_private("192.168.1.2") and sip.is_private("100.64.1.1") and not sip.is_private("8.8.8.8") and not sip.is_private("panel.local")


def test_sip_digest() -> None:
    plain = {"realm": "r", "nonce": "n"}  # an old server: no qop
    auth = sip.parse_auth(sip.authorization("REGISTER", "sip:h", "u", "pw", {**plain, "opaque": "o"}))
    assert auth["opaque"] == "o" and "qop" not in auth and sip.verify("REGISTER", auth, "pw") and not sip.verify("REGISTER", auth, "no")
    assert not sip.verify("REGISTER", {**auth, "algorithm": "SHA-256"}, "pw")  # only MD5 (panels know no other)


def test_rtp_framing() -> None:
    pkt = rtp.packet(8, 1, 160, 7, b"abcd", marker=True)
    assert rtp.parse(pkt) == (8, 1, 160, 7, True, b"abcd")
    ext = bytearray(rtp.packet(0, 2, 0, 7, b""))
    ext[0] |= 0x10
    assert rtp.parse(bytes(ext) + b"\x00\x00\x00\x01" + b"EXTN" + b"pay") == (0, 2, 0, 7, False, b"pay")
    assert rtp.parse(bytes(ext) + b"\x00") is None  # an extension cut short
    padded = bytearray(rtp.packet(0, 3, 0, 7, b"pay\x00\x00\x03"))
    padded[0] |= 0x20
    assert rtp.parse(bytes(padded))[5] == b"pay"
    bad = bytearray(rtp.packet(0, 4, 0, 7, b"\x09"))
    bad[0] |= 0x20
    assert rtp.parse(bytes(bad)) is None  # padding longer than the packet
    assert rtp.parse(b"\x80" * 4) is None and rtp.parse(b"\x00" * 12) is None
    assert rtp.convert(b"ab", "PCMA", "PCMA") == b"ab"
    assert rtp.convert(rtp.convert(b"\xd5\x55", "PCMA", "PCMU"), "PCMU", "PCMA") == b"\xd5\x55"  # A-law → µ-law → A-law: the same sound


async def test_rtp_session_edges(socket_enabled) -> None:
    s = rtp.RtpSession("PCMA", 8, None, lambda _p: None)
    with pytest.raises(OSError):
        await s.send_dtmf("#", 40, 20)  # nowhere to send, no telephone-event
    s.send(b"x")  # no socket: nothing
    busy = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    busy.bind((HOST, 0))
    port = busy.getsockname()[1]
    if port % 2:
        busy.close()
        busy = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        busy.bind((HOST, port - 1))
        port -= 1
    with pytest.raises(OSError):
        await s.open(HOST, (port, port))  # the only port of the range taken
    busy.close()
    await s.open(HOST, (0, 0))
    s.remote = (HOST, 9)
    s.datagram_received(b"\x00" * 12, (HOST, 9))  # not RTP v2: ignored, nothing latched
    s.datagram_received(rtp.packet(8, 1, 0, 1, b"a"), ("10.1.1.1", 9))  # a stranger's
    s.datagram_received(rtp.packet(8, 1, 0, 1, b"a"), (HOST, 9))
    s.datagram_received(rtp.packet(8, 1, 0, 1, b"a"), ("10.1.1.1", 9))  # latched: still a stranger
    assert s.rx == 1 and s.foreign == 2 and s.latched == (HOST, 9)
    s.close()


async def _rtsp(socket_enabled):
    server = RtspServer(HOST, 0)
    await server.start()
    back: list[bytes] = []
    server.hubs["a"] = AudioHub("PCMA", 20, back.append, 1)
    server.hubs["b"] = AudioHub("PCMU", 20, back.append, 1)
    return server, back


async def test_rtsp_refusals(socket_enabled) -> None:
    server, back = await _rtsp(socket_enabled)
    url = server.url("a")
    c = await Go2rtc(url).connect()
    assert (await c.ask("OPTIONS"))[0] == 200 and (await c.ask("GET_PARAMETER"))[0] == 200
    assert (await c.ask("DESCRIBE", url.replace(server.token, "wrong")))[0] == 404
    assert (await c.ask("DESCRIBE", headers={"Require": "www.onvif.org/ver20/backchannel, other"}))[0] == 551
    assert (await c.ask("DESCRIBE"))[0] == 200  # no backchannel asked: one track
    assert (await c.ask("SETUP", url + "/trackID=1", {"Transport": "RTP/AVP/TCP;unicast;interleaved=2-3"}))[0] == 404
    assert (await c.ask("SETUP", url + "/trackID=0", {"Transport": "RTP/AVP;unicast;client_port=5000-5001"}))[0] == 461
    assert (await c.ask("PLAY"))[0] == 454
    assert (await c.ask("ANNOUNCE", body=b"v=0\r\n"))[0] == 405
    assert (await c.ask("SETUP", url + "/trackID=0", {"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"}))[0] == 200
    assert (await c.ask("DESCRIBE", server.url("b")))[0] == 400  # one connection, one door
    assert (await c.ask("PLAY"))[0] == 200 and server.stats()["clients"] == 1
    # The hub has room for one: a second listener is refused.
    c2 = await Go2rtc(url).connect()
    await c2.ask("DESCRIBE")
    assert (await c2.ask("SETUP", url + "/trackID=0", {"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"}))[0] == 453
    c.talk(b"x" * 160, 8, channel=9)  # an unknown channel: dropped
    assert (await c.ask("TEARDOWN"))[0] == 200 and server.stats()["clients"] == 0
    # A head too big ends the connection.
    c3 = await Go2rtc(url).connect()
    c3.writer.write(b"OPTIONS " + b"x" * 70000 + b" RTSP/1.0\r\n\r\n")
    assert await c3.reader.read() == b""
    await c.close()
    await c2.close()
    await c3.close()
    await server.stop()
    await asyncio.sleep(0.05)
    assert back == []


async def test_rtsp_stop_with_a_listener(socket_enabled) -> None:
    server, _back = await _rtsp(socket_enabled)
    c = await Go2rtc(server.url("a")).connect()
    await c.play(backchannel=False)
    await asyncio.sleep(0.05)
    assert server.stats()["clients"] == 1
    await server.stop()
    await asyncio.sleep(0.05)
    await c.close()
