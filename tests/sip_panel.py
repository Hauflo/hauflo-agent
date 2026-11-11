"""Р55 tests: a door panel emulator speaking plain SIP / RTP / RTSP over 127.0.0.1 (no baresip, no linphone, no
external binary): it calls the agent (INVITE / CANCEL / BYE / ACK), registers to it, acts as a panel's / a cloud's SIP
server the agent registers on (digest challenge), sends and records RTP, and plays go2rtc's part on the agent's RTSP
(DESCRIBE with the ONVIF backchannel, interleaved TCP)."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Callable
from typing import Any

from custom_components.hauflo.intercom import rtp, sip

HOST = "127.0.0.1"


class Panel(asyncio.DatagramProtocol):
    """One UDP SIP socket. Every message is kept in `got`; `wait()` takes the next one that matches. As a SIP server
    (`server_password`), REGISTER is challenged and then accepted; `answers` gives the reply per method of a request."""

    def __init__(self, user: str = "door") -> None:
        self.user = user
        self.transport: asyncio.DatagramTransport | None = None
        self.port = 0
        self.got: list[tuple[sip.Message, tuple[str, int]]] = []
        self.used: set[int] = set()
        self.raw: list[bytes] = []
        self._new = asyncio.Event()
        self.answers: dict[str, int | None] = {"BYE": 200, "INFO": 200, "OPTIONS": 200}
        self.server_password: str | None = None
        self.server_user = ""
        self.seen_at: tuple[str, int] | None = None  # reply with this as the «received / rport» (a NAT)
        self.registers: list[sip.Message] = []
        self.nonce = "abc123"
        self.cseq = 0

    async def start(self) -> Panel:
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=(HOST, 0))
        self.port = self.transport.get_extra_info("sockname")[1]
        return self

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()

    @property
    def addr(self) -> tuple[str, int]:
        return (HOST, self.port)

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self.raw.append(data)
        if not data.strip():
            return
        msg = sip.parse(data)
        src = (addr[0], addr[1])
        self.got.append((msg, src))
        self._new.set()
        if msg.method == "REGISTER" and self.server_password is not None:
            self._register(msg, src)
        elif msg.method in self.answers and self.answers[msg.method] is not None:
            self.reply(msg, src, int(self.answers[msg.method] or 200))

    def _register(self, msg: sip.Message, src: tuple[str, int]) -> None:
        self.registers.append(msg)
        auth = msg.get("authorization")
        if not auth:
            self.reply(msg, src, 401, headers=[("WWW-Authenticate", sip.challenge("panel", self.nonce))])
            return
        parsed = sip.parse_auth(auth)
        if parsed.get("username") != self.server_user or not sip.verify("REGISTER", parsed, self.server_password or ""):
            self.reply(msg, src, 403)
            return
        exp = msg.get("expires") or "60"
        self.reply(msg, src, 200, headers=[("Contact", f"{msg.get('contact')};expires={exp}")])

    def reply(self, req: sip.Message, to: tuple[str, int], status: int, headers: list[tuple[str, str]] | None = None) -> None:
        seen = self.seen_at or to
        resp = sip.response(req, status, "X", to_tag=sip.new_tag(), headers=headers, received=seen)
        self.send(resp, to)

    def send(self, msg: sip.Message, to: tuple[str, int]) -> None:
        assert self.transport is not None
        self.transport.sendto(msg.to_bytes(), to)

    async def wait(self, pred: Callable[[sip.Message], bool], timeout: float = 3) -> sip.Message:
        async with asyncio.timeout(timeout):
            while True:
                for i, (msg, _src) in enumerate(self.got):
                    if i not in self.used and pred(msg):
                        self.used.add(i)
                        return msg
                self._new.clear()
                await self._new.wait()

    async def status(self, code: int, method: str | None = None, timeout: float = 3) -> sip.Message:
        return await self.wait(lambda m: m.status == code and (method is None or m.cseq[1] == method), timeout)

    async def request(self, method: str, timeout: float = 3) -> sip.Message:
        return await self.wait(lambda m: m.method == method, timeout)

    # --- requests the panel makes -----------------------------------------------------------------------------------

    def invite(self, to: tuple[str, int], *, dialled: str = "hauflo", rtp_port: int = 40000, sdp_addr: str = HOST, offer: str = "8 0 101", names: dict[int, str] | None = None, body: bytes | None = None, call_id: str | None = None, branch: str | None = None) -> sip.Message:
        msg = sip.Message(method="INVITE", uri=sip.sip_uri(dialled, *to))
        self.cseq += 1
        msg.headers = [
            ("Via", f"SIP/2.0/UDP {HOST}:{self.port};branch={branch or sip.new_branch()};rport"),
            ("Max-Forwards", "70"),
            ("From", f'"Door" <sip:{self.user}@{HOST}:{self.port}>;tag={sip.new_tag()}'),
            ("To", f"<{sip.sip_uri(dialled, *to)}>"),
            ("Call-ID", call_id or sip.new_call_id(HOST)),
            ("CSeq", f"{self.cseq} INVITE"),
            ("Contact", f"<sip:{self.user}@{HOST}:{self.port}>"),
            ("Record-Route", f"<sip:{HOST}:{self.port};lr>"),
        ]
        if body is None:
            names = names if names is not None else {8: "PCMA", 0: "PCMU", 101: "telephone-event"}
            lines = ["v=0", f"o=- 1 1 IN IP4 {sdp_addr}", "s=Door", f"c=IN IP4 {sdp_addr}", "t=0 0", f"m=audio {rtp_port} RTP/AVP {offer}"]
            lines += [f"a=rtpmap:{pt} {n}/8000" for pt, n in names.items() if str(pt) in offer.split()]
            body = ("\r\n".join([*lines, "a=sendrecv"]) + "\r\n").encode()
        if body:
            msg.headers.append(("Content-Type", "application/sdp"))
        msg.body = body
        self.send(msg, to)
        return msg

    def in_dialog(self, invite: sip.Message, method: str, to: tuple[str, int], *, ok: sip.Message | None = None, same_branch: bool = False, headers: list[tuple[str, str]] | None = None, cseq: int | None = None) -> sip.Message:
        """ACK / BYE / CANCEL of the panel's call (CANCEL: the INVITE's own branch and CSeq number)."""
        via = invite.get("via") if same_branch else f"SIP/2.0/UDP {HOST}:{self.port};branch={sip.new_branch()};rport"
        num = invite.cseq[0] if method in ("ACK", "CANCEL") else (cseq or invite.cseq[0] + 1)
        msg = sip.Message(method=method, uri=invite.uri)
        msg.headers = [("Via", via or ""), ("Max-Forwards", "70"), ("From", invite.get("from") or ""), ("To", (ok.get("to") if ok is not None else invite.get("to")) or ""), ("Call-ID", invite.call_id), ("CSeq", f"{num} {method}")]
        msg.headers += headers or []
        self.send(msg, to)
        return msg

    def register(self, to: tuple[str, int], user: str, *, auth: str | None = None, expires: int = 120, contact: str | None = None, call_id: str = "reg-1") -> sip.Message:
        self.cseq += 1
        msg = sip.Message(method="REGISTER", uri=f"sip:{HOST}")
        msg.headers = [
            ("Via", f"SIP/2.0/UDP {HOST}:{self.port};branch={sip.new_branch()};rport"),
            ("From", f"<sip:{user}@{HOST}>;tag={sip.new_tag()}"),
            ("To", f"<sip:{user}@{HOST}>"),
            ("Call-ID", call_id),
            ("CSeq", f"{self.cseq} REGISTER"),
            ("Contact", contact if contact is not None else f"<sip:{user}@{HOST}:{self.port}>"),
            ("Expires", str(expires)),
        ]
        if auth:
            msg.headers.append(("Authorization", auth))
        self.send(msg, to)
        return msg

    def plain(self, method: str, to: tuple[str, int], dialled: str = "hauflo") -> sip.Message:
        msg = sip.Message(method=method, uri=sip.sip_uri(dialled, *to))
        msg.headers = [("Via", f"SIP/2.0/UDP {HOST}:{self.port};branch={sip.new_branch()}"), ("From", f"<sip:{self.user}@{HOST}>;tag=x"), ("To", f"<sip:{dialled}@{HOST}>"), ("Call-ID", sip.new_call_id(HOST)), ("CSeq", f"1 {method}")]
        self.send(msg, to)
        return msg


class Media(asyncio.DatagramProtocol):
    """The panel's RTP socket: records what comes in, sends G.711 frames."""

    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self.port = 0
        self.packets: list[tuple[int, int, bool, bytes]] = []  # (pt, ts, marker, payload)
        self.seq = 0

    async def start(self) -> Media:
        self.transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(lambda: self, local_addr=(HOST, 0))
        self.port = self.transport.get_extra_info("sockname")[1]
        return self

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()

    def datagram_received(self, data: bytes, addr: Any) -> None:
        got = rtp.parse(data)
        if got is not None:
            self.packets.append((got[0], got[2], got[4], got[5]))

    def send(self, to: tuple[str, int], payload: bytes, pt: int = 8) -> None:
        assert self.transport is not None
        self.seq += 1
        self.transport.sendto(rtp.packet(pt, self.seq, self.seq * 160, 1234, payload), to)


class Go2rtc:
    """go2rtc's side of the agent's RTSP: DESCRIBE (with the backchannel), SETUP both tracks interleaved, PLAY."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.cseq = 0
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def connect(self) -> Go2rtc:
        port = int(self.url.split("://", 1)[1].split("/", 1)[0].split(":")[1])
        self.reader, self.writer = await asyncio.open_connection(HOST, port)
        return self

    async def ask(self, method: str, url: str | None = None, headers: dict[str, str] | None = None, body: bytes = b"") -> tuple[int, dict[str, str], bytes]:
        assert self.reader is not None and self.writer is not None
        self.cseq += 1
        lines = [f"{method} {url or self.url} RTSP/1.0", f"CSeq: {self.cseq}", *(f"{k}: {v}" for k, v in (headers or {}).items())]
        if body:
            lines.append(f"Content-Length: {len(body)}")
        self.writer.write(("\r\n".join(lines) + "\r\n\r\n").encode() + body)
        await self.writer.drain()
        while True:  # interleaved audio may come before the answer
            first = await self.reader.readexactly(1)
            if first == b"$":
                _ch, size = struct.unpack("!BH", await self.reader.readexactly(3))
                await self.reader.readexactly(size)
                continue
            head = (first + await self.reader.readuntil(b"\r\n\r\n")).decode()
            break
        status = int(head.split(" ", 2)[1])
        hdrs = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in head.split("\r\n")[1:]) if k}
        size = int(hdrs.get("content-length", "0"))
        return status, hdrs, await self.reader.readexactly(size) if size else b""

    async def play(self, backchannel: bool = True) -> None:
        req = {"Require": "www.onvif.org/ver20/backchannel"} if backchannel else {}
        status, _, sdp = await self.ask("DESCRIBE", headers={"Accept": "application/sdp", **req})
        assert status == 200 and b"a=rtpmap" in sdp
        assert (await self.ask("SETUP", self.url + "/trackID=0", {"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"}))[0] == 200
        if backchannel:
            assert (await self.ask("SETUP", self.url + "/trackID=1", {"Transport": "RTP/AVP/TCP;unicast;interleaved=2-3"}))[0] == 200
        assert (await self.ask("PLAY", headers={"Range": "npt=0.000-"}))[0] == 200

    async def frame(self, timeout: float = 2) -> tuple[int, bytes]:
        """The next interleaved RTP frame: (channel, payload)."""
        assert self.reader is not None
        async with asyncio.timeout(timeout):
            while True:
                first = await self.reader.readexactly(1)
                if first != b"$":
                    continue
                ch, size = struct.unpack("!BH", await self.reader.readexactly(3))
                data = await self.reader.readexactly(size)
                got = rtp.parse(data)
                if got is not None:
                    return ch, got[5]

    def talk(self, payload: bytes, pt: int, channel: int = 2) -> None:
        assert self.writer is not None
        pkt = rtp.packet(pt, 1, 160, 99, payload)
        self.writer.write(b"$" + bytes([channel]) + struct.pack("!H", len(pkt)) + pkt)

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
