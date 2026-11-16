from __future__ import annotations
import asyncio
import secrets
import struct
import time
from collections.abc import Callable
from typing import Any
HEADER = struct.Struct('!BBHII')
VERSION = 2 << 6
SILENCE = {'PCMA': 213, 'PCMU': 255}
DTMF_EVENTS = {**{str(i): i for i in range(10)}, '*': 10, '#': 11, 'A': 12, 'B': 13, 'C': 14, 'D': 15}

def packet(pt: int, seq: int, ts: int, ssrc: int, payload: bytes, marker: bool=False) -> bytes:
    return HEADER.pack(VERSION, (128 if marker else 0) | pt & 127, seq & 65535, ts & 4294967295, ssrc) + payload

def parse(data: bytes) -> tuple[int, int, int, int, bool, bytes] | None:
    if len(data) < 12 or data[0] >> 6 != 2:
        return None
    b0, b1, seq, ts, ssrc = HEADER.unpack_from(data)
    off = 12 + 4 * (b0 & 15)
    if b0 & 16:
        if len(data) < off + 4:
            return None
        off += 4 + 4 * struct.unpack_from('!H', data, off + 2)[0]
    end = len(data)
    if b0 & 32 and end > off:
        end -= data[-1]
    if off > end:
        return None
    return (b1 & 127, seq, ts, ssrc, bool(b1 & 128), data[off:end])

def _ulaw_to_linear(u: int) -> int:
    u = ~u & 255
    t = ((u & 15) << 3) + 132
    t <<= (u & 112) >> 4
    return 132 - t if u & 128 else t - 132

def _alaw_to_linear(a: int) -> int:
    a ^= 85
    t = (a & 15) << 4
    seg = (a & 112) >> 4
    if seg == 0:
        t += 8
    elif seg == 1:
        t += 264
    else:
        t = t + 264 << seg - 1
    return t if a & 128 else -t

def _linear_to_ulaw(pcm: int) -> int:
    bias, clip = (132, 32635)
    sign = 128 if pcm < 0 else 0
    pcm = min(abs(pcm), clip) + bias
    exp = max(0, min(7, pcm.bit_length() - 8))
    mant = pcm >> exp + 3 & 15
    return ~(sign | exp << 4 | mant) & 255

def _linear_to_alaw(pcm: int) -> int:
    sign = 128 if pcm >= 0 else 0
    pcm = min(abs(pcm) if pcm >= 0 else -pcm - 1, 32767) >> 3
    if pcm < 32:
        out = pcm >> 1
    else:
        seg = pcm.bit_length() - 5
        out = seg << 4 | pcm >> seg & 15
    return (out | sign) ^ 85
A2U = bytes((_linear_to_ulaw(_alaw_to_linear(a)) for a in range(256)))
U2A = bytes((_linear_to_alaw(_ulaw_to_linear(u)) for u in range(256)))

def convert(payload: bytes, src: str, dst: str) -> bytes:
    if src == dst:
        return payload
    return payload.translate(A2U if src == 'PCMA' else U2A)

class RtpSession(asyncio.DatagramProtocol):

    def __init__(self, codec: str, pt: int, dtmf_pt: int | None, on_audio: Callable[[bytes], None]) -> None:
        self.codec, self.pt, self.dtmf_pt = (codec, pt, dtmf_pt)
        self.on_audio = on_audio
        self.transport: asyncio.DatagramTransport | None = None
        self.remote: tuple[str, int] | None = None
        self.latched: tuple[str, int] | None = None
        self.ssrc = secrets.randbits(32)
        self.seq = secrets.randbits(16)
        self.ts = secrets.randbits(32)
        self.rx = self.tx = 0
        self.rx_bytes = self.tx_bytes = 0
        self.last_rx = 0.0
        self.last_tx = 0.0
        self.port = 0
        self.foreign = 0
        self.allowed: set[str] = set()
        self.any_source = False

    async def open(self, host: str, ports: tuple[int, int]) -> int:
        loop = asyncio.get_running_loop()
        lo, hi = ports
        last: OSError | None = None
        for port in range(lo + lo % 2, hi + 1, 2) if lo else [0]:
            try:
                self.transport, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=(host, port))
            except OSError as err:
                last = err
                continue
            self.port = self.transport.get_extra_info('sockname')[1]
            return self.port
        raise last or OSError('no RTP port')

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
                self.foreign += 1
                return
        elif addr != self.latched and addr[0] != self.latched[0]:
            self.foreign += 1
            return
        got = parse(data)
        if got is None:
            return
        self.latched = addr
        pt, _seq, _ts, _ssrc, _marker, payload = got
        self.rx += 1
        self.rx_bytes += len(payload)
        self.last_rx = time.monotonic()
        if pt == self.pt and payload:
            self.on_audio(payload)

    def send(self, payload: bytes, marker: bool=False) -> None:
        if self.transport is None or (to := self.target) is None or (not payload):
            return
        self.seq = self.seq + 1 & 65535
        self.transport.sendto(packet(self.pt, self.seq, self.ts, self.ssrc, payload, marker), to)
        self.ts = self.ts + len(payload) & 4294967295
        self.tx += 1
        self.tx_bytes += len(payload)
        self.last_tx = time.monotonic()

    def punch(self) -> None:
        self.send(bytes([SILENCE[self.codec]]) * 160, marker=True)

    async def send_dtmf(self, digits: str, ms: int, frame_ms: int) -> None:
        if self.transport is None or (to := self.target) is None or self.dtmf_pt is None:
            raise OSError('no telephone-event')
        step = 8 * frame_ms
        for ch in digits:
            event = DTMF_EVENTS[ch.upper()]
            ts = self.ts
            frames = max(1, ms // frame_ms)
            for i in range(frames):
                self.seq = self.seq + 1 & 65535
                body = struct.pack('!BBH', event, 10, step * (i + 1))
                self.transport.sendto(packet(self.dtmf_pt, self.seq, ts, self.ssrc, body, marker=i == 0), to)
                await asyncio.sleep(frame_ms / 1000)
            for _ in range(3):
                self.seq = self.seq + 1 & 65535
                self.transport.sendto(packet(self.dtmf_pt, self.seq, ts, self.ssrc, struct.pack('!BBH', event, 128 | 10, step * frames)), to)
            self.ts = self.ts + step * frames & 4294967295
            self.tx += frames + 3
            await asyncio.sleep(frame_ms / 1000)

    def stats(self) -> dict[str, Any]:

        def fmt(a: tuple[str, int] | None) -> str | None:
            return f'{a[0]}:{a[1]}' if a else None
        return {'codec': self.codec, 'rx_packets': self.rx, 'tx_packets': self.tx, 'rx_bytes': self.rx_bytes, 'tx_bytes': self.tx_bytes, 'sdp_remote': fmt(self.remote), 'rtp_from': fmt(self.latched), 'local_port': self.port}
