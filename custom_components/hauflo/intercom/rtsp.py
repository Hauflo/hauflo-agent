"""The door's sound as an RTSP source for HA's own go2rtc (no add-on, no Docker): rtsp://127.0.0.1:<port>/door/<id>/<token>.
One audio track of what the panel says (G.711) and — when go2rtc asks for it with ONVIF's «Require:
www.onvif.org/ver20/backchannel» — a second, `sendonly` track: the card's microphone, which go2rtc sends back over the
same TCP connection (interleaved) and the agent relays to the panel. TCP only, loopback only, a path with a secret
token: nothing else on the host reaches a door call through it."""

from __future__ import annotations

import asyncio
import contextlib
import re
import secrets
import struct
import time
from collections.abc import Callable
from typing import Any

from ..tuning import TUNING
from . import rtp

BACKCHANNEL = "www.onvif.org/ver20/backchannel"
_TRACK = re.compile(r"trackID=(\d+)")
_PT = {"PCMA": 8, "PCMU": 0}


class AudioHub:
    """One door's stream: the panel's audio to every playing client (silence between calls — go2rtc drops a source that
    sends nothing), the clients' back audio to `on_back`."""

    def __init__(self, codec: str, frame_ms: int, on_back: Callable[[bytes], None], max_clients: int) -> None:
        self.codec = codec
        self.pt = _PT[codec]
        self.frame_ms = frame_ms
        self.on_back = on_back
        self.max_clients = max_clients
        self.clients: set[_Client] = set()
        self.ssrc = secrets.randbits(32)
        self.seq = secrets.randbits(16)
        self.ts = secrets.randbits(32)
        self.last_feed = 0.0
        self.back_packets = 0
        self.last_back = 0.0
        self._task: asyncio.Task[None] | None = None

    def feed(self, payload: bytes) -> None:
        """The panel's audio (in this hub's codec)."""
        self.last_feed = time.monotonic()
        self._send(payload)

    def _send(self, payload: bytes) -> None:
        self.seq = (self.seq + 1) & 0xFFFF
        pkt = rtp.packet(self.pt, self.seq, self.ts, self.ssrc, payload)
        self.ts = (self.ts + len(payload)) & 0xFFFFFFFF
        for c in list(self.clients):
            c.send_audio(pkt)

    def back(self, payload: bytes) -> None:
        self.back_packets += 1
        self.last_back = time.monotonic()
        self.on_back(payload)

    def attach(self, client: _Client) -> None:
        self.clients.add(client)
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._silence())

    def detach(self, client: _Client) -> None:
        self.clients.discard(client)
        if not self.clients and self._task is not None:
            self._task.cancel()
            self._task = None

    async def _silence(self) -> None:
        frame = bytes([rtp.SILENCE[self.codec]]) * (8 * self.frame_ms)
        step = self.frame_ms / 1000
        while self.clients:
            if time.monotonic() - self.last_feed > 2 * step:
                self._send(frame)
            await asyncio.sleep(step)

    def close(self) -> None:
        for c in list(self.clients):
            c.close()
        self.clients.clear()
        if self._task is not None:
            self._task.cancel()
            self._task = None


class _Client:
    def __init__(self, server: RtspServer, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.server, self.reader, self.writer = server, reader, writer
        self.session = secrets.token_hex(8)
        self.hub: AudioHub | None = None
        self.channels: dict[int, int] = {}  # track → its RTP interleaved channel
        self.backchannel = False
        self.playing = False

    def send_audio(self, pkt: bytes) -> None:
        ch = self.channels.get(0)
        if ch is None or not self.playing or self.writer.is_closing():
            return
        self.writer.write(b"$" + bytes([ch]) + struct.pack("!H", len(pkt)) + pkt)

    def close(self) -> None:
        if self.hub is not None:
            self.hub.detach(self)
        if not self.writer.is_closing():
            self.writer.close()

    async def run(self) -> None:
        try:
            while True:
                first = await self.reader.readexactly(1)
                if first == b"$":
                    ch, size = struct.unpack("!BH", await self.reader.readexactly(3))
                    data = await self.reader.readexactly(size)
                    self._interleaved(ch, data)
                    continue
                head = first + await self.reader.readuntil(b"\r\n\r\n")
                if len(head) > TUNING["intercom"]["rtsp_max_head"]:
                    break
                await self._request(head)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            pass
        finally:
            self.close()

    def _interleaved(self, ch: int, data: bytes) -> None:
        back = self.channels.get(1)
        if self.hub is None or back is None or ch != back:
            return  # RTCP or an unknown channel
        got = rtp.parse(data)
        if got is not None and got[0] == self.hub.pt and got[5]:
            self.hub.back(got[5])

    async def _request(self, head: bytes) -> None:
        lines = head.decode("utf-8", "replace").split("\r\n")
        method, url, *_ = (lines[0].split(" ") + ["", ""])[:3]
        headers: dict[str, str] = {}
        for ln in lines[1:]:
            k, colon, v = ln.partition(":")
            if colon:
                headers[k.strip().lower()] = v.strip()
        if (size := headers.get("content-length", "0")).isdigit() and int(size):
            await self.reader.readexactly(min(int(size), TUNING["intercom"]["rtsp_max_head"]))
        cseq = headers.get("cseq", "0")
        status, extra, body = self._handle(method.upper(), url, headers)
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed", 453: "Not Enough Bandwidth", 454: "Session Not Found", 461: "Unsupported Transport", 551: "Option not supported"}.get(status, "Error")
        out = [f"RTSP/1.0 {status} {reason}", f"CSeq: {cseq}", "Server: Hauflo"]
        out += [f"{k}: {v}" for k, v in extra]
        if body:
            out.append(f"Content-Length: {len(body)}")
        self.writer.write(("\r\n".join(out) + "\r\n\r\n").encode() + body)
        await self.writer.drain()

    def _handle(self, method: str, url: str, headers: dict[str, str]) -> tuple[int, list[tuple[str, str]], bytes]:
        if method == "OPTIONS":
            return 200, [("Public", "OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN, GET_PARAMETER, SET_PARAMETER")], b""
        if method in ("GET_PARAMETER", "SET_PARAMETER"):
            return 200, [("Session", self.session)], b""
        found = self.server.lookup(url)
        if found is None:
            return 404, [], b""
        base, hub = found
        if self.hub is not None and self.hub is not hub:
            return 400, [], b""
        if method == "DESCRIBE":
            wants = {r.strip() for r in headers.get("require", "").split(",") if r.strip()}
            if wants - {BACKCHANNEL}:
                return 551, [("Unsupported", ", ".join(sorted(wants - {BACKCHANNEL})))], b""
            self.backchannel = BACKCHANNEL in wants
            return 200, [("Content-Base", base + "/"), ("Content-Type", "application/sdp")], self._sdp(hub)
        if method == "SETUP":
            transport = headers.get("transport", "")
            if "interleaved=" not in transport:
                return 461, [], b""
            track = int(m.group(1)) if (m := _TRACK.search(url)) else 0
            if track > (1 if self.backchannel else 0):
                return 404, [], b""
            if self.hub is None:
                if len(hub.clients) >= hub.max_clients:
                    return 453, [], b""
                self.hub = hub
            ch = int(transport.split("interleaved=", 1)[1].split("-", 1)[0].split(";", 1)[0])
            self.channels[track] = ch
            return 200, [("Transport", f"RTP/AVP/TCP;unicast;interleaved={ch}-{ch + 1}"), ("Session", f"{self.session};timeout=60")], b""
        if method == "PLAY":
            if not self.channels or self.hub is None:
                return 454, [], b""
            self.playing = True
            hub.attach(self)
            return 200, [("Session", self.session), ("Range", "npt=0.000-")], b""
        if method == "TEARDOWN":
            self.playing = False
            if self.hub is not None:
                self.hub.detach(self)
            return 200, [("Session", self.session)], b""
        return 405, [], b""

    def _sdp(self, hub: AudioHub) -> bytes:
        tracks = [("trackID=0", "recvonly")] + ([("trackID=1", "sendonly")] if self.backchannel else [])
        lines = ["v=0", f"o=- {secrets.randbits(31)} 1 IN IP4 127.0.0.1", "s=Hauflo", "c=IN IP4 0.0.0.0", "t=0 0", "a=control:*"]
        for control, direction in tracks:
            # ONVIF Streaming §5.3: directions as the client sees them — it receives the door, it sends the backchannel.
            lines += [f"m=audio 0 RTP/AVP {hub.pt}", f"a=rtpmap:{hub.pt} {hub.codec}/8000", f"a=control:{control}", f"a={direction}"]
        return ("\r\n".join(lines) + "\r\n").encode()


class RtspServer:
    """Loopback RTSP for the doors' sound. Port 0 — any free one (the tests)."""

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port
        self.token = secrets.token_urlsafe(18)
        self.hubs: dict[str, AudioHub] = {}
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._accept, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for hub in self.hubs.values():
            hub.close()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        for t in list(self._clients):
            t.cancel()

    def path(self, door: str) -> str:
        return f"/door/{door}/{self.token}"

    def url(self, door: str) -> str:
        return f"rtsp://{self.host}:{self.port}{self.path(door)}"

    def lookup(self, url: str) -> tuple[str, AudioHub] | None:
        path = re.sub(r"^rtsp://[^/]*", "", url).split("?", 1)[0]
        for door, hub in self.hubs.items():
            p = self.path(door)
            if path == p or path.startswith(p + "/"):
                return self.url(door), hub
        return None

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        client = _Client(self, reader, writer)
        task = asyncio.current_task()
        if task is not None:
            self._clients.add(task)
        try:
            await client.run()
        finally:
            if task is not None:
                self._clients.discard(task)

    def stats(self) -> dict[str, Any]:
        return {"port": self.port, "clients": sum(len(h.clients) for h in self.hubs.values())}
