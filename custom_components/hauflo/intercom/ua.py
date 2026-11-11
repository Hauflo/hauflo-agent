"""The SIP user agent's transport (UDP, one socket on the chosen address) with its transactions — a retransmitted
request gets the same answer, our requests are retransmitted until answered (RFC 3261 §17, timers T1 / T2 / F) — plus
the two registration roles: the agent registering on a panel's / a cloud's SIP server (`Registration`, digest), and the
agent as the registrar of one account a panel registers to (`Registrar`, digest required)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import sip

_LOGGER = logging.getLogger(__name__)


@dataclass
class Timers:
    t1: float
    t2: float
    timeout: float
    cache: int


class Endpoint(asyncio.DatagramProtocol):
    """One UDP socket for every door. Requests go to `on_request(msg, source)`; a retransmission of one already
    answered is answered again from the cache and not passed on."""

    def __init__(self, on_request: Callable[[sip.Message, tuple[str, int]], None], timers: Timers, user_agent: str) -> None:
        self.on_request = on_request
        self.timers = timers
        self.user_agent = user_agent
        self.transport: asyncio.DatagramTransport | None = None
        self.host = ""
        self.port = 0
        self._answers: OrderedDict[tuple[str, str, str], tuple[bytes, tuple[str, int]]] = OrderedDict()
        self._waiting: dict[str, asyncio.Future[sip.Message]] = {}
        self.rx = self.tx = 0

    async def start(self, host: str, port: int) -> None:
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=(host, port))
        self.host, self.port = self.transport.get_extra_info("sockname")[:2]

    def stop(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        for fut in self._waiting.values():
            if not fut.done():
                fut.cancel()
        self._waiting.clear()

    def datagram_received(self, data: bytes, addr: tuple[str, Any]) -> None:
        src = (addr[0], addr[1])
        if not data.strip():
            return  # a keep-alive CRLF (RFC 5626)
        try:
            msg = sip.parse(data)
        except ValueError:
            return
        self.rx += 1
        if msg.status is not None:
            fut = self._waiting.get(msg.branch)
            if fut is not None and not fut.done() and msg.status >= 200:
                fut.set_result(msg)
            elif fut is not None and not fut.done() and msg.status < 200:
                pass  # provisional: keep waiting (timer F still runs)
            return
        key = self._key(msg)
        if msg.method != "ACK" and key in self._answers:
            raw, _ = self._answers[key]
            self._raw(raw, src)  # a retransmission: the same answer, nothing new happens
            return
        try:
            self.on_request(msg, src)
        except Exception:  # noqa: BLE001 — one bad packet never stops the socket
            _LOGGER.exception("Hauflo intercom: request not handled")

    @staticmethod
    def _key(msg: sip.Message) -> tuple[str, str, str]:
        return (msg.branch, msg.call_id, msg.cseq[1] or (msg.method or ""))

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("Hauflo intercom: socket error %s", exc)

    def _raw(self, raw: bytes, to: tuple[str, int]) -> None:
        if self.transport is not None:
            self.transport.sendto(raw, to)
            self.tx += 1

    def send(self, msg: sip.Message, to: tuple[str, int]) -> None:
        if msg.get("user-agent") is None and msg.get("server") is None:
            msg.headers.append(("User-Agent" if msg.method else "Server", self.user_agent))
        self._raw(msg.to_bytes(), to)

    def reply(self, req: sip.Message, to: tuple[str, int], status: int, reason: str, **kw: Any) -> sip.Message:
        """Answers `req` and remembers the answer for its retransmissions (the last one wins: 180 then 200 / 487)."""
        resp = sip.response(req, status, reason, received=to, **kw)
        self.send(resp, to)
        key = self._key(req)
        self._answers[key] = (resp.to_bytes(), to)
        self._answers.move_to_end(key)
        while len(self._answers) > self.timers.cache:
            self._answers.popitem(last=False)
        return resp

    async def request(self, msg: sip.Message, to: tuple[str, int]) -> sip.Message | None:
        """Sends a request until a final answer (T1 doubling up to T2) or timer F: None — nobody answered."""
        branch = msg.branch
        fut: asyncio.Future[sip.Message] = asyncio.get_running_loop().create_future()
        self._waiting[branch] = fut
        try:
            return await self.retransmit(msg, to, fut)
        finally:
            self._waiting.pop(branch, None)

    async def retransmit(self, msg: sip.Message, to: tuple[str, int], done: asyncio.Future[Any]) -> Any:
        """Sends `msg` again and again (T1, 2·T1 … T2) until `done` resolves or timer F: then None."""
        wait, end = self.timers.t1, time.monotonic() + self.timers.timeout
        while True:
            self.send(msg, to)
            left = end - time.monotonic()
            if left <= 0:
                return None
            with contextlib.suppress(TimeoutError):
                return await asyncio.wait_for(asyncio.shield(done), min(wait, left))
            wait = min(wait * 2, self.timers.t2)

    def via(self, host: str, port: int) -> tuple[str, str]:
        return ("Via", f"SIP/2.0/UDP {host}:{port};branch={sip.new_branch()};rport")


class Registration:
    """The agent's account on a SIP server (a Dahua panel's own, a cloud's): REGISTER with digest, refreshed before it
    runs out, retried after a failure. Behind a NAT (`nat`): the address the server saw (Via received / rport) becomes
    the Contact, and a CRLF keeps the router's way open between refreshes."""

    def __init__(self, ep: Endpoint, server: tuple[str, int], domain: str, user: str, password: str, contact: tuple[str, int], *, expires: int, retry: float, keepalive: float, nat: bool, on_change: Callable[[], None], extra: Callable[[sip.Message], None] | None = None) -> None:
        self.ep, self.server, self.domain = ep, server, domain
        self.extra = extra  # adds what a server wants beyond RFC 3261 (a Hikvision monitor's regXML body)
        self.user, self.password = user, password
        self.contact = contact
        self.expires, self.retry, self.keepalive, self.nat = expires, retry, keepalive, nat
        self.on_change = on_change
        self.registered = False
        self.at: float | None = None
        self.error: str | None = None
        self.public: tuple[str, int] | None = None
        self.call_id = sip.new_call_id(contact[0])
        self.tag = sip.new_tag()
        self.cseq = 0
        self.granted = 0
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self.registered and self.ep.transport is not None:
            self.registered = False
            with contextlib.suppress(Exception):
                async with asyncio.timeout(2):
                    await self.register(0)

    async def _run(self) -> None:
        while True:
            ok = False
            try:
                ok = await self.register(self.expires)
            except Exception as err:  # noqa: BLE001 — the loop keeps trying
                self.error = f"error: {err}"
            self.on_change()
            if not ok:
                await asyncio.sleep(self.retry)
                continue
            # Refresh at ~80% of what the server granted; behind a NAT a CRLF every `keepalive` keeps the way open.
            until = time.monotonic() + max(self.granted * 0.8, 1)
            while (left := until - time.monotonic()) > 0:
                await asyncio.sleep(min(left, self.keepalive) if self.nat else left)
                if self.nat and self.ep.transport is not None and until - time.monotonic() > 0:
                    self.ep.transport.sendto(b"\r\n\r\n", self.server)

    def _build(self, expires: int) -> sip.Message:
        self.cseq += 1
        host, port = self.public if (self.nat and self.public) else self.contact
        msg = sip.Message(method="REGISTER", uri=f"sip:{self.domain}")
        msg.headers = [
            self.ep.via(*self.contact),
            ("Max-Forwards", "70"),
            ("From", f"<{sip.sip_uri(self.user, self.domain)}>;tag={self.tag}"),
            ("To", f"<{sip.sip_uri(self.user, self.domain)}>"),
            ("Call-ID", self.call_id),
            ("CSeq", f"{self.cseq} REGISTER"),
            ("Contact", f"<{sip.sip_uri(self.user, host, port)}>"),
            ("Expires", str(expires)),
            ("Allow", "INVITE, ACK, CANCEL, BYE, OPTIONS, INFO, NOTIFY"),
        ]
        if self.extra is not None:
            self.extra(msg)
        return msg

    async def register(self, expires: int) -> bool:
        msg = self._build(expires)
        resp = await self.ep.request(msg, self.server)
        for _ in range(2):  # a challenge, then maybe a stale nonce
            if resp is None or resp.status not in (401, 407):
                break
            header = "WWW-Authenticate" if resp.status == 401 else "Proxy-Authenticate"
            chal = sip.parse_auth(resp.get(header) or "")
            msg = self._build(expires)
            msg.headers.append(("Authorization" if resp.status == 401 else "Proxy-Authorization", sip.authorization("REGISTER", msg.uri or "", self.user, self.password, chal)))
            resp = await self.ep.request(msg, self.server)
        if resp is None:
            self.registered, self.error = False, "no_answer"
            return False
        if resp.status != 200:
            self.registered, self.error = False, f"{resp.status} {resp.reason}".strip()
            return False
        if expires == 0:
            return True
        self.granted = _granted(resp, expires)
        self.registered, self.error, self.at = True, None, time.time()
        seen = _seen(resp)
        if self.nat and seen is not None and seen != (self.public or self.contact):
            self.public = seen  # the server saw us at another address (a NAT): register that one as the Contact
            return await self.register(expires)
        return True


def _granted(resp: sip.Message, asked: int) -> int:
    for c in resp.get_all("contact"):
        if (e := sip.param(c, "expires")) and e.isdigit():
            return int(e)
    e = resp.get("expires")
    return int(e) if e and e.strip().isdigit() else asked


def _seen(resp: sip.Message) -> tuple[str, int] | None:
    via = resp.get("via") or ""
    host = sip.param(via, "received")
    port = sip.param(via, "rport")
    if not host and not (port and port.isdigit()):
        return None
    sent = sip.host_port(via.split(";", 1)[0].split()[-1] if via.split() else "")
    return host or sent[0], int(port) if port and port.isdigit() else sent[1]


@dataclass
class Binding:
    contact: str
    source: tuple[str, int]
    until: float
    at: float


class Registrar:
    """The registrar of one account: digest required (no REGISTER is ever taken without the password), nonces of ours
    only and for a while."""

    def __init__(self, realm: str, nonce_ttl: float, max_expires: int) -> None:
        self.realm = realm
        self.nonce_ttl = nonce_ttl
        self.max_expires = max_expires
        self._nonces: dict[str, float] = {}
        self.failures = 0
        self.last_failure: str | None = None

    def nonce(self) -> str:
        now = time.monotonic()
        self._nonces = {n: t for n, t in self._nonces.items() if now - t < self.nonce_ttl}
        n = secrets.token_hex(16)
        self._nonces[n] = now
        return n

    def check(self, req: sip.Message, src: tuple[str, int], user: str, password: str) -> tuple[int, str, list[tuple[str, str]], Binding | None]:
        """(status, reason, headers, the binding to keep — None: unregister / refused)."""
        auth_hdr = req.get("authorization")
        if not auth_hdr:
            return 401, "Unauthorized", [("WWW-Authenticate", sip.challenge(self.realm, self.nonce()))], None
        auth = sip.parse_auth(auth_hdr)
        fresh = auth.get("nonce") in self._nonces and time.monotonic() - self._nonces[auth.get("nonce", "")] < self.nonce_ttl
        if not fresh:
            return 401, "Unauthorized", [("WWW-Authenticate", sip.challenge(self.realm, self.nonce()) + ", stale=true")], None
        if auth.get("username") != user or auth.get("realm") != self.realm or not sip.verify("REGISTER", auth, password):
            self.failures += 1
            self.last_failure = auth.get("username") or "?"
            return 403, "Forbidden", [], None
        contacts = req.get_all("contact")
        expires_hdr = req.get("expires")
        asked = int(expires_hdr) if expires_hdr and expires_hdr.strip().isdigit() else self.max_expires
        contact = contacts[0] if contacts else ""
        if contact and (e := sip.param(contact, "expires")) and e.isdigit():
            asked = int(e)
        if contact.strip() == "*" or asked == 0 or not contact:
            return 200, "OK", [], None
        granted = min(asked, self.max_expires)
        now = time.time()
        binding = Binding(sip.uri_of(contact), src, now + granted, now)
        return 200, "OK", [("Contact", f"<{binding.contact}>;expires={granted}"), ("Expires", str(granted)), ("Date", time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime()))], binding
