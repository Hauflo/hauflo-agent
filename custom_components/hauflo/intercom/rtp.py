"""RTP (RFC 3550) for a door call: G.711 packets, A-law ↔ μ-law (a panel offering only the other law than the one the
cards' stream carries), DTMF as telephone-event (RFC 4733), and the call's UDP session — symmetric (it answers where
the panel's packets really come from, a NAT in between) and counted for the diagnosis."""

from __future__ import annotations

import asyncio
import secrets
import struct
import time
from collections.abc import Callable
from typing import Any

HEADER = struct.Struct("!BBHII")
VERSION = 2 << 6
SILENCE = {"PCMA": 0xD5, "PCMU": 0xFF}
DTMF_EVENTS = {**{str(i): i for i in range(10)}, "*": 10, "#": 11, "A": 12, "B": 13, "C": 14, "D": 15}


def packet(pt: int, seq: int, ts: int, ssrc: int, payload: bytes, marker: bool = False) -> bytes:
    return HEADER.pack(VERSION, (0x80 if marker else 0) | (pt & 0x7F), seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc) + payload


def parse(data: bytes) -> tuple[int, int, int, int, bool, bytes] | None:
    """(payload type, seq, timestamp, ssrc, marker, payload) — None for anything that isn't RTP v2."""
    if len(data) < 12 or data[0] >> 6 != 2:
        return None
    b0, b1, seq, ts, ssrc = HEADER.unpack_from(data)
    off = 12 + 4 * (b0 & 0x0F)
    if b0 & 0x10:  # a header extension
        if len(data) < off + 4:
            return None
        off += 4 + 4 * struct.unpack_from("!H", data, off + 2)[0]
    end = len(data)
    if b0 & 0x20 and end > off:  # padding: its count is the last byte
        end -= data[-1]
    if off > end:
        return None
    return b1 & 0x7F, seq, ts, ssrc, bool(b1 & 0x80), data[off:end]


# --- G.711 (ITU-T G.711): one law into the other through 16-bit linear, tables made once ------------------------------


def _ulaw_to_linear(u: int) -> int:
    u = ~u & 0xFF
    t = ((u & 0x0F) << 3) + 0x84
    t <<= (u & 0x70) >> 4
    return (0x84 - t) if u & 0x80 else (t - 0x84)


def _alaw_to_linear(a: int) -> int:
    a ^= 0x55
    t = (a & 0x0F) << 4
    seg = (a & 0x70) >> 4
    if seg == 0:
        t += 8
    elif seg == 1:
        t += 0x108
    else:
        t = (t + 0x108) << (seg - 1)
    return t if a & 0x80 else -t


def _linear_to_ulaw(pcm: int) -> int:
    bias, clip = 0x84, 32635
    sign = 0x80 if pcm < 0 else 0
    pcm = min(abs(pcm), clip) + bias
    exp = max(0, min(7, pcm.bit_length() - 8))
    mant = (pcm >> (exp + 3)) & 0x0F
    return ~(sign | (exp << 4) | mant) & 0xFF


def _linear_to_alaw(pcm: int) -> int:
    sign = 0x80 if pcm >= 0 else 0
    pcm = min(abs(pcm) if pcm >= 0 else -pcm - 1, 32767) >> 3
    if pcm < 32:
        out = pcm >> 1
    else:
        seg = pcm.bit_length() - 5
        out = (seg << 4) | ((pcm >> seg) & 0x0F)
    return (out | sign) ^ 0x55


A2U = bytes(_linear_to_ulaw(_alaw_to_linear(a)) for a in range(256))
U2A = bytes(_linear_to_alaw(_ulaw_to_linear(u)) for u in range(256))


def convert(payload: bytes, src: str, dst: str) -> bytes:
    if src == dst:
        return payload
    return payload.translate(A2U if src == "PCMA" else U2A)


# --- the call's session -----------------------------------------------------------------------------------------------


class RtpSession(asyncio.DatagramProtocol):
    """The panel's audio leg of one call: what comes in goes to `on_audio` (payload, in the call's codec); `send` puts
    the card's voice on the wire. Symmetric: the first packets from the panel's address (any port) say where to answer."""

    def __init__(self, codec: str, pt: int, dtmf_pt: int | None, on_audio: Callable[[bytes], None]) -> None:
        self.codec, self.pt, self.dtmf_pt = codec, pt, dtmf_pt
        self.on_audio = on_audio
        self.transport: asyncio.DatagramTransport | None = None
        self.remote: tuple[str, int] | None = None  # where the SDP said
        self.latched: tuple[str, int] | None = None  # where the packets come from
        self.ssrc = secrets.randbits(32)
        self.seq = secrets.randbits(16)
        self.ts = secrets.randbits(32)
        self.rx = self.tx = 0
        self.rx_bytes = self.tx_bytes = 0
        self.last_rx = 0.0
        self.last_tx = 0.0
        self.port = 0
        self.foreign = 0  # packets from an address that isn't the panel's (dropped)
        # Whose packets are the panel's: its own address, its SIP server's, the SDP's. A cloud's media relay sends from
        # addresses nobody names in advance (`any_source`): the first one is taken, then only it.
        self.allowed: set[str] = set()
        self.any_source = False

    async def open(self, host: str, ports: tuple[int, int]) -> int:
        """Binds the first free even port of the range on the listening address; OSError — none free."""
        loop = asyncio.get_running_loop()
        lo, hi = ports
        last: OSError | None = None
        for port in range(lo + (lo % 2), hi + 1, 2) if lo else [0]:
            try:
                self.transport, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=(host, port))
            except OSError as err:
                last = err
                continue
            self.port = self.transport.get_extra_info("sockname")[1]
            return self.port
        raise last or OSError("no RTP port")

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None

    @property
    def target(self) -> tuple[str, int] | None:
        return self.latched or self.remote

    def datagram_received(self, data: bytes, addr: tuple[str, Any]) -> None:
        addr = (addr[0], addr[1])
        if self.latched is None:
            if not self.any_source and addr[0] not in self.allowed and (self.remote is None or addr[0] != self.remote[0]):
                self.foreign += 1  # not the panel's (nor its server's): dropped, never relayed
                return
        elif addr != self.latched and addr[0] != self.latched[0]:
            self.foreign += 1
            return
        got = parse(data)
        if got is None:
            return
        self.latched = addr  # symmetric RTP: answer where it comes from (a NAT in between)
        pt, _seq, _ts, _ssrc, _marker, payload = got
        self.rx += 1
        self.rx_bytes += len(payload)
        self.last_rx = time.monotonic()
        if pt == self.pt and payload:
            self.on_audio(payload)

    def send(self, payload: bytes, marker: bool = False) -> None:
        """The card's voice (the call's codec, 20 ms frames) to the panel."""
        if self.transport is None or (to := self.target) is None or not payload:
            return
        self.seq = (self.seq + 1) & 0xFFFF
        self.transport.sendto(packet(self.pt, self.seq, self.ts, self.ssrc, payload, marker), to)
        self.ts = (self.ts + len(payload)) & 0xFFFFFFFF
        self.tx += 1
        self.tx_bytes += len(payload)
        self.last_tx = time.monotonic()

    def punch(self) -> None:
        """One silent frame right after the answer: opens the NAT's way back for the panel's / the cloud's audio."""
        self.send(bytes([SILENCE[self.codec]]) * 160, marker=True)

    async def send_dtmf(self, digits: str, ms: int, frame_ms: int) -> None:
        """RFC 4733 telephone-events: one timestamp per digit, growing duration, the end packet three times."""
        if self.transport is None or (to := self.target) is None or self.dtmf_pt is None:
            raise OSError("no telephone-event")
        step = 8 * frame_ms
        for ch in digits:
            event = DTMF_EVENTS[ch.upper()]
            ts = self.ts
            frames = max(1, ms // frame_ms)
            for i in range(frames):
                self.seq = (self.seq + 1) & 0xFFFF
                body = struct.pack("!BBH", event, 10, step * (i + 1))
                self.transport.sendto(packet(self.dtmf_pt, self.seq, ts, self.ssrc, body, marker=i == 0), to)
                await asyncio.sleep(frame_ms / 1000)
            for _ in range(3):
                self.seq = (self.seq + 1) & 0xFFFF
                self.transport.sendto(packet(self.dtmf_pt, self.seq, ts, self.ssrc, struct.pack("!BBH", event, 0x80 | 10, step * frames)), to)
            self.ts = (self.ts + step * frames) & 0xFFFFFFFF
            self.tx += frames + 3
            await asyncio.sleep(frame_ms / 1000)

    def stats(self) -> dict[str, Any]:
        def fmt(a: tuple[str, int] | None) -> str | None:
            return f"{a[0]}:{a[1]}" if a else None

        return {
            "codec": self.codec,
            "rx_packets": self.rx,
            "tx_packets": self.tx,
            "rx_bytes": self.rx_bytes,
            "tx_bytes": self.tx_bytes,
            "sdp_remote": fmt(self.remote),
            "rtp_from": fmt(self.latched),
            "local_port": self.port,
        }
