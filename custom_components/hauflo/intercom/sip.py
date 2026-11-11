"""SIP messages (RFC 3261), digest authentication (RFC 2617 / 8760 MD5 — what door panels speak) and SDP offers /
answers (RFC 4566 / 3264) with G.711 and telephone-event (RFC 4733). Pure functions, no I/O: ua.py sends them."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import secrets
from dataclasses import dataclass, field
from urllib.parse import quote, unquote

CRLF = "\r\n"
# The compact header forms a panel may send (RFC 3261 §7.3.3).
_COMPACT = {"v": "via", "f": "from", "t": "to", "i": "call-id", "m": "contact", "l": "content-length", "c": "content-type", "k": "supported"}
# Static RTP payload types of G.711 (RFC 3551): offered by number alone, without an rtpmap line.
STATIC = {0: "PCMU", 8: "PCMA"}
CODECS = ("PCMA", "PCMU")
MAGIC = "z9hG4bK"  # a branch of RFC 3261 starts with it


@dataclass
class Message:
    """One SIP request (`method`, `uri`) or response (`status`, `reason`); headers keep their order and repeats."""

    method: str | None = None
    uri: str | None = None
    status: int | None = None
    reason: str = ""
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""

    def get(self, name: str) -> str | None:
        name = _COMPACT.get(name.lower(), name.lower())
        for k, v in self.headers:
            if _COMPACT.get(k.lower(), k.lower()) == name:
                return v
        return None

    def get_all(self, name: str) -> list[str]:
        name = _COMPACT.get(name.lower(), name.lower())
        out: list[str] = []
        for k, v in self.headers:
            if _COMPACT.get(k.lower(), k.lower()) == name:
                out.extend(_split_commas(v) if name in ("via", "contact") else [v])
        return out

    def set(self, name: str, value: str) -> None:
        low = _COMPACT.get(name.lower(), name.lower())
        self.headers = [(k, v) for k, v in self.headers if _COMPACT.get(k.lower(), k.lower()) != low]
        self.headers.append((name, value))

    @property
    def call_id(self) -> str:
        return self.get("call-id") or ""

    @property
    def cseq(self) -> tuple[int, str]:
        num, _, method = (self.get("cseq") or "0 ").strip().partition(" ")
        try:
            return int(num), method.strip().upper()
        except ValueError:
            return 0, method.strip().upper()

    @property
    def branch(self) -> str:
        return param(self.get("via") or "", "branch") or ""

    def to_bytes(self) -> bytes:
        first = f"{self.method} {self.uri} SIP/2.0" if self.method else f"SIP/2.0 {self.status} {self.reason}"
        lines = [first, *(f"{k}: {v}" for k, v in self.headers if k.lower() not in ("content-length", "l"))]
        lines.append(f"Content-Length: {len(self.body)}")
        return (CRLF.join(lines) + CRLF + CRLF).encode() + self.body


def _split_commas(value: str) -> list[str]:
    """«a, <sip:b,c>, d» → three values: commas inside <> or quotes don't split."""
    out, cur, depth, quoted = [], "", 0, False
    for ch in value:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == "<":
            depth += 1
        elif not quoted and ch == ">":
            depth -= 1
        if ch == "," and not depth and not quoted:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def parse(data: bytes) -> Message:
    """A datagram → Message. ValueError for anything that isn't SIP (a stray packet, a keep-alive CRLF)."""
    head, sep, body = data.partition(b"\r\n\r\n")
    if not sep:
        head, sep, body = data.partition(b"\n\n")
    text = head.decode("utf-8", "replace")
    lines = [ln for ln in re.split(r"\r?\n", text)]
    if not lines or not lines[0].strip():
        raise ValueError("empty")
    first = lines[0].split(" ", 2)
    msg = Message()
    if first[0] == "SIP/2.0":
        if len(first) < 2 or not first[1].isdigit():
            raise ValueError("bad status line")
        msg.status, msg.reason = int(first[1]), first[2] if len(first) > 2 else ""
    elif len(first) == 3 and first[2].strip() == "SIP/2.0":
        msg.method, msg.uri = first[0].upper(), first[1]
    else:
        raise ValueError("not SIP")
    for ln in lines[1:]:
        if ln[:1] in (" ", "\t") and msg.headers:  # a folded line continues the header before it
            k, v = msg.headers[-1]
            msg.headers[-1] = (k, f"{v} {ln.strip()}")
            continue
        k, colon, v = ln.partition(":")
        if colon:
            msg.headers.append((k.strip(), v.strip()))
    length = msg.get("content-length")
    msg.body = body[: int(length)] if length and length.strip().isdigit() else body
    return msg


def param(value: str, name: str) -> str | None:
    """A «;name=value» parameter of a header (outside its <uri>)."""
    tail = value.rsplit(">", 1)[-1] if ">" in value else value
    for part in tail.split(";")[1:]:
        k, _, v = part.partition("=")
        if k.strip().lower() == name.lower():
            return v.strip().strip('"')
    return None


def uri_of(value: str) -> str:
    """«"Door" <sip:100@10.0.0.5:5060>;tag=x» → «sip:100@10.0.0.5:5060»."""
    if "<" in value:
        return value.split("<", 1)[1].split(">", 1)[0].strip()
    return value.split(";", 1)[0].strip()


def user_of(uri: str) -> str:
    rest = uri.split(":", 1)[1] if ":" in uri else uri
    return unquote(rest.split("@", 1)[0]) if "@" in rest else ""


def host_port(uri: str, default: int = 5060) -> tuple[str, int]:
    rest = uri.split(":", 1)[1] if uri.lower().startswith(("sip:", "sips:")) else uri
    rest = rest.split("@", 1)[-1].split(";", 1)[0].split("?", 1)[0]
    host, _, port = rest.partition(":")
    return host, int(port) if port.isdigit() else default


def sip_uri(user: str, host: str, port: int | None = None) -> str:
    """A SIP URI with the user escaped (Dahua's «9901#1» → «9901%231», RFC 3261 §25.1 user-unreserved kept)."""
    who = f"{quote(user, safe="-_.!~*'()&=+$,;?/")}@" if user else ""
    return f"sip:{who}{host}{f':{port}' if port else ''}"


def new_tag() -> str:
    return secrets.token_hex(6)


def new_branch() -> str:
    return MAGIC + secrets.token_hex(8)


def new_call_id(host: str) -> str:
    return f"{secrets.token_hex(10)}@{host}"


def response(req: Message, status: int, reason: str, *, to_tag: str | None = None, headers: list[tuple[str, str]] | None = None, body: bytes = b"", received: tuple[str, int] | None = None) -> Message:
    """A response to `req`: its Via (the first one marked with where it really came from — rport, RFC 3581), From,
    To (with our tag), Call-ID, CSeq copied."""
    out = Message(status=status, reason=reason)
    vias = req.get_all("via")
    for i, via in enumerate(vias):
        if i == 0 and received is not None:
            via = _mark_received(via, received)
        out.headers.append(("Via", via))
    to = req.get("to") or ""
    if to_tag and param(to, "tag") is None:
        to = f"{to};tag={to_tag}"
    out.headers += [("From", req.get("from") or ""), ("To", to), ("Call-ID", req.call_id), ("CSeq", req.get("cseq") or "")]
    out.headers += headers or []
    out.body = body
    return out


def _mark_received(via: str, src: tuple[str, int]) -> str:
    parts = via.split(";")
    sent_host = host_port(parts[0].split()[-1] if parts[0].split() else "")[0]
    keep = [parts[0]]
    has_rport = False
    for p in parts[1:]:
        k = p.split("=", 1)[0].strip().lower()
        if k == "rport":
            has_rport = True
            keep.append(f"rport={src[1]}")
        elif k != "received":
            keep.append(p)
    if sent_host != src[0] or has_rport:
        keep.append(f"received={src[0]}")
    return ";".join(keep)


# --- digest (RFC 2617; MD5 is what door panels and SIP servers support) -------------------------------------------


def parse_auth(value: str) -> dict[str, str]:
    """«Digest realm="x", nonce="y", qop="auth"» → {"realm": "x", …}; keys lower-case."""
    _, _, rest = value.strip().partition(" ")
    out: dict[str, str] = {}
    for m in re.finditer(r'([A-Za-z0-9_-]+)\s*=\s*("([^"]*)"|[^,\s]+)', rest):
        out[m.group(1).lower()] = m.group(3) if m.group(3) is not None else m.group(2)
    return out


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()  # noqa: S324 — SIP digest is MD5 by protocol (panels know no other)


def digest(method: str, uri: str, user: str, password: str, realm: str, nonce: str, *, qop: str | None = None, nc: str = "00000001", cnonce: str = "") -> str:
    ha1 = _md5(f"{user}:{realm}:{password}")
    ha2 = _md5(f"{method}:{uri}")
    if qop:
        return _md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return _md5(f"{ha1}:{nonce}:{ha2}")


def authorization(method: str, uri: str, user: str, password: str, challenge: dict[str, str], nc: int = 1) -> str:
    """The Authorization (or Proxy-Authorization) value answering a 401 / 407 challenge."""
    realm, nonce = challenge.get("realm", ""), challenge.get("nonce", "")
    qops = [q.strip() for q in challenge.get("qop", "").split(",") if q.strip()]
    qop = "auth" if "auth" in qops else None
    cnonce = secrets.token_hex(8)
    ncs = f"{nc:08x}"
    resp = digest(method, uri, user, password, realm, nonce, qop=qop, nc=ncs, cnonce=cnonce)
    parts = [f'username="{user}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"', f'response="{resp}"', "algorithm=MD5"]
    if qop:
        parts += [f"qop={qop}", f"nc={ncs}", f'cnonce="{cnonce}"']
    if "opaque" in challenge:
        parts.append(f'opaque="{challenge["opaque"]}"')
    return "Digest " + ", ".join(parts)


def challenge(realm: str, nonce: str) -> str:
    return f'Digest realm="{realm}", nonce="{nonce}", algorithm=MD5, qop="auth"'


def verify(method: str, auth: dict[str, str], password: str) -> bool:
    """The client's digest is right for this password (the realm / nonce are checked by the caller)."""
    if auth.get("algorithm", "MD5").upper() != "MD5":
        return False
    want = digest(method, auth.get("uri", ""), auth.get("username", ""), password, auth.get("realm", ""), auth.get("nonce", ""), qop=auth.get("qop"), nc=auth.get("nc", ""), cnonce=auth.get("cnonce", ""))
    return secrets.compare_digest(want, auth.get("response", ""))


# --- SDP -------------------------------------------------------------------------------------------------------------


@dataclass
class Media:
    """The audio of an SDP: where to send, which payload types in order, their names, DTMF's type."""

    addr: str = ""
    port: int = 0
    pts: list[int] = field(default_factory=list)
    names: dict[int, str] = field(default_factory=dict)
    dtmf_pt: int | None = None
    direction: str = "sendrecv"

    def choose(self, prefer: str) -> tuple[int, str] | None:
        """The first G.711 the offer has, ours first: (payload type, «PCMA» / «PCMU»)."""
        offered = [(pt, self.names.get(pt, STATIC.get(pt, "")).upper()) for pt in self.pts]
        g711 = [(pt, n) for pt, n in offered if n in CODECS]
        g711.sort(key=lambda x: x[1] != prefer)
        return g711[0] if g711 else None


def parse_sdp(body: bytes) -> Media | None:
    """The first audio m-line of an SDP; None — no audio in it."""
    media: Media | None = None
    session_addr = ""
    for raw in body.decode("utf-8", "replace").splitlines():
        line = raw.strip()
        if line.startswith("c=") and line.split():
            addr = line.split()[-1].split("/", 1)[0]
            if media is None:
                session_addr = addr
            else:
                media.addr = addr
        elif line.startswith("m="):
            if media is not None:
                break  # only the first audio
            kind, _, rest = line[2:].partition(" ")
            if kind != "audio":
                continue
            bits = rest.split()
            media = Media(addr=session_addr, port=int(bits[0].split("/", 1)[0]) if bits and bits[0].split("/", 1)[0].isdigit() else 0)
            media.pts = [int(p) for p in bits[2:] if p.isdigit()]
        elif media is not None and line.startswith("a=rtpmap:"):
            pt, _, name = line[9:].partition(" ")
            if pt.isdigit():
                codec = name.split("/", 1)[0].upper()
                media.names[int(pt)] = codec
                if codec == "TELEPHONE-EVENT" and (media.dtmf_pt is None or name.endswith("/8000")):
                    media.dtmf_pt = int(pt)
        elif media is not None and line[2:] in ("sendrecv", "sendonly", "recvonly", "inactive"):
            media.direction = line[2:]
    return media


def build_sdp(addr: str, port: int, pt: int, codec: str, dtmf_pt: int | None, session_id: int, version: int = 1, direction: str = "sendrecv", name: str = "Hauflo") -> bytes:
    pts = f"{pt}{f' {dtmf_pt}' if dtmf_pt is not None else ''}"
    lines = [
        "v=0",
        f"o=- {session_id} {version} IN IP4 {addr}",
        f"s={name}",
        f"c=IN IP4 {addr}",
        "t=0 0",
        f"m=audio {port} RTP/AVP {pts}",
        f"a=rtpmap:{pt} {codec}/8000",
    ]
    if dtmf_pt is not None:
        lines += [f"a=rtpmap:{dtmf_pt} telephone-event/8000", f"a=fmtp:{dtmf_pt} 0-16"]
    lines += ["a=ptime:20", f"a={direction}"]
    return (CRLF.join(lines) + CRLF).encode()


def is_private(addr: str) -> bool:
    """A LAN / carrier-NAT address: an SDP of a cloud call naming it can't be reached from the internet."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10")
