"""One door: its settings (a preset of presets.json + what the integrator set), the call's life — ringing → talking →
idle, «ответили на другом устройстве» — who talks (one at a time), the door's opening, and the diagnosis the card's
plate shows (registered or not, the last call, whether sound went each way, and why not)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from homeassistant.util import dt as dt_util, slugify

from ..tuning import TUNING
from . import rtp, sip

if TYPE_CHECKING:
    from .manager import Intercom

_LOGGER = logging.getLogger(__name__)
T = TUNING["intercom"]
PRESETS: dict[str, dict[str, Any]] = {k: v for k, v in json.loads(Path(__file__).with_name("presets.json").read_text(encoding="utf-8")).items() if not k.startswith("_")}
MODES = ("direct", "register", "registrar", "cloud")
OPENERS = ("dtmf", "http", "entity")
IDLE, RINGING, TALKING, ELSEWHERE = "idle", "ringing", "talking", "answered_elsewhere"
STATES = (IDLE, RINGING, TALKING, ELSEWHERE)
# The door's fields an integrator may set (the options' door form); a preset gives the rest.
FIELDS = ("name", "area", "preset", "mode", "host", "user", "password", "sip_server", "sip_port", "sip_user", "sip_password", "rtsp", "open", "dtmf", "relay", "open_entity", "codec")


class IntercomError(Exception):
    """A refusal the card / the service shows: its translation key (exceptions.intercom_<key>)."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass
class DoorConfig:
    id: str
    name: str
    preset: str
    maker: str
    model: str
    mode: str
    host: str = ""
    user: str = ""
    password: str = ""
    sip_server: str = ""
    sip_port: int = 5060
    sip_user: str = ""
    sip_password: str = ""
    rtsp: str = ""
    snapshot: dict[str, Any] | None = None
    open: str = ""
    open_http: dict[str, Any] | None = None
    dtmf: str = ""
    relay: int = 1
    open_entity: str = ""
    codec: str = "PCMA"
    register_extra: dict[str, Any] | None = None  # presets.json: more than RFC 3261 in each REGISTER
    area: str = ""  # the room (HA area name) its device is suggested into — the wizard's «Комната»
    error: str | None = None  # what is missing for it to work (the plate says it)


def _q(v: Any) -> str:
    return quote(str(v), safe="")


def render(template: str, cfg: DoorConfig) -> str:
    return template.format(host=cfg.host, user=_q(cfg.user), password=_q(cfg.password), relay=cfg.relay)


def configs(options: dict[str, Any]) -> list[DoorConfig]:
    """The doors of the options' «intercom» block, each with its preset filled in; ids unique (a slug of the name)."""
    out: list[DoorConfig] = []
    seen: set[str] = set()
    for raw in options.get("doors") or []:
        if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
            continue
        name = str(raw["name"]).strip()
        preset_id = str(raw.get("preset") or "generic")
        preset = PRESETS.get(preset_id, PRESETS["generic"])
        merged = {**preset, **{k: v for k, v in raw.items() if k in FIELDS and v not in (None, "")}}
        base = slugify(name) or "door"
        door_id, n = base, 2
        while door_id in seen:
            door_id, n = f"{base}_{n}", n + 1
        seen.add(door_id)
        mode = merged.get("mode") if merged.get("mode") in MODES else "direct"
        cfg = DoorConfig(
            id=door_id,
            name=name,
            preset=preset_id if preset_id in PRESETS else "generic",
            maker=str(preset.get("maker") or ""),
            model=str(preset.get("label") or ""),
            mode=str(mode),
            host=str(merged.get("host") or "").strip(),
            user=str(merged.get("user") or ""),
            password=str(merged.get("password") or ""),
            sip_server=str(merged.get("sip_server") or "").strip(),
            sip_port=_int(merged.get("sip_port"), T["sip_port"]),
            sip_user=str(merged.get("sip_user") or ""),
            sip_password=str(merged.get("sip_password") or ""),
            snapshot=merged.get("snapshot") if isinstance(merged.get("snapshot"), dict) else None,
            open=str(merged.get("open") or ""),
            open_http=merged.get("open_http") if isinstance(merged.get("open_http"), dict) else None,
            dtmf=str(merged.get("dtmf") or ""),
            relay=_int(merged.get("relay"), 1),
            open_entity=str(merged.get("open_entity") or ""),
            codec=str(merged.get("codec") or "PCMA").upper() if str(merged.get("codec") or "").upper() in sip.CODECS else "PCMA",
            area=str(raw.get("area") or "").strip(),
        )
        if isinstance(merged.get("register_extra"), dict):
            cfg.register_extra = merged["register_extra"]
        rtsp_t = str(merged.get("rtsp") or "")
        if rtsp_t and cfg.host:
            try:
                cfg.rtsp = render(rtsp_t, cfg) if "{" in rtsp_t else rtsp_t
            except (KeyError, IndexError, ValueError):
                cfg.rtsp = ""
        if cfg.mode == "register" and not cfg.sip_server:
            cfg.sip_server = cfg.host
        cfg.error = _missing(cfg)
        out.append(cfg)
    return out


def _iso(ts: float | None) -> str | None:
    return dt_util.utc_from_timestamp(ts).isoformat() if ts else None


def _stamped(info: dict[str, Any] | None) -> dict[str, Any] | None:
    return {**info, "at": _iso(info.get("at"))} if info else None


def _int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _missing(cfg: DoorConfig) -> str | None:
    if cfg.mode == "direct" and not cfg.host:
        return "host"  # a call is taken only from the panel's own address
    if cfg.mode in ("register", "cloud") and not cfg.sip_server:
        return "sip_server"
    if cfg.mode in ("register", "cloud", "registrar") and not (cfg.sip_user and cfg.sip_password):
        return "sip_account"  # the registrar never takes a REGISTER without a password
    return None


@dataclass
class Call:
    invite: sip.Message
    source: tuple[str, int]
    media: sip.Media
    pt: int
    codec: str
    to_tag: str = field(default_factory=sip.new_tag)
    session_id: int = field(default_factory=lambda: secrets.randbits(31))
    started: float = field(default_factory=time.time)
    answered_at: float | None = None
    rtp: rtp.RtpSession | None = None
    ok: sip.Message | None = None
    acked: asyncio.Future[bool] | None = None
    cseq: int = 0
    caller: str = ""


class Door:
    """A door's call and diagnosis. Its entities listen through `listeners` (state, attributes) and `rings` (the event)."""

    def __init__(self, cfg: DoorConfig, mgr: Intercom) -> None:
        self.cfg = cfg
        self.mgr = mgr
        self.state = IDLE
        self.call: Call | None = None
        self.talker: str | None = None
        self.listeners: set[Callable[[], None]] = set()
        self.rings: set[Callable[[dict[str, Any]], None]] = set()
        self.registration: Any = None  # ua.Registration (register / cloud)
        self.binding: Any = None  # ua.Binding (registrar)
        self.server_ips: set[str] = set()
        self.dns_error = False
        self.register_error: str | None = None  # registrar: a panel tried a wrong password
        self.last_register_at: float | None = None
        self.last_invite_at: float | None = None
        self.last_call: dict[str, Any] | None = None
        self.last_open: dict[str, Any] | None = None
        self.last_rejected: dict[str, Any] | None = None
        self.hub: Any = None  # rtsp.AudioHub
        self._ring_timer: asyncio.TimerHandle | None = None
        self._elsewhere_timer: asyncio.TimerHandle | None = None
        self._talk_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    # --- what is shown ------------------------------------------------------------------------------------------

    def changed(self) -> None:
        for cb in list(self.listeners):
            cb()

    def sources(self) -> set[str]:
        """The addresses a call of this door may come from."""
        out = {self.cfg.host} if self.cfg.host else set()
        if self.cfg.mode in ("register", "cloud"):
            out |= self.server_ips
        if self.cfg.mode == "registrar" and self.binding is not None and self.binding.until > time.time():
            out.add(self.binding.source[0])
        return out

    def registered(self) -> bool | None:
        if self.cfg.mode in ("register", "cloud"):
            return bool(self.registration is not None and self.registration.registered)
        if self.cfg.mode == "registrar":
            return self.binding is not None and self.binding.until > time.time()
        return None

    def problem(self) -> str | None:
        """Why it doesn't work — the first that applies (the card's plate words: entity.sensor.intercom.state_attributes)."""
        if self.mgr.listen_error:
            return self.mgr.listen_error
        if self.cfg.error:
            return f"missing_{self.cfg.error}"
        if self.dns_error:
            return "server_not_found"
        if self.cfg.mode in ("register", "cloud") and not self.registered():
            return "not_registered"
        if self.cfg.mode == "registrar" and not self.registered():
            return "panel_not_registered"
        if self.state == TALKING and self.call is not None and self.call.rtp is not None and self.call.answered_at is not None:
            quiet = time.time() - self.call.answered_at > T["no_audio_s"]
            if quiet and self.call.rtp.rx == 0:
                return "no_audio_from_panel"
            if quiet and (self.hub is None or self.hub.back_packets == 0):
                return "no_audio_to_panel"
            return None
        if self.last_rejected is not None and (self.last_call is None or self.last_rejected["at"] > self.last_call["at"]):
            return "call_from_unknown_address"
        if self.last_call is None:
            return "no_calls_yet"
        result = self.last_call.get("result")
        if result in ("codec_mismatch", "no_ack"):
            return result
        if self.last_call.get("answered") and not self.last_call.get("rx_packets"):
            return "no_audio_from_panel"
        return None

    def call_audio(self) -> str | None:
        """Which way the sound went in the call now / the last one (hass-sip's words): none, no_rx (nothing from the
        panel), no_tx (nothing from the card), bidirectional. None — no answered call yet."""
        if self.call is not None and self.call.rtp is not None:
            rx, tx = self.call.rtp.rx, self.hub.back_packets if self.hub is not None else 0
        elif self.last_call is not None and self.last_call.get("answered"):
            rx, tx = self.last_call.get("rx_packets", 0), self.last_call.get("from_card_packets", 0)
        else:
            return None
        return "bidirectional" if rx and tx else "no_tx" if rx else "no_rx" if tx else "none"

    def nat_mode(self) -> str:
        """How the panel learns our address: «observed» (a cloud saw us behind a NAT), «manual» (set by hand), «lan»."""
        reg = self.registration
        if reg is not None and reg.public is not None:
            return "observed"
        return "manual" if self.mgr.public is not None else "lan"

    def attributes(self) -> dict[str, Any]:
        reg = self.registration
        stats = self.call.rtp.stats() if self.call is not None and self.call.rtp is not None else (self.last_call or {})
        error = reg.error if reg is not None else self.register_error
        out: dict[str, Any] = {
            # The card hides «Ответить» unless the sound reaches it: «webrtc» — through the talk camera (go2rtc).
            "audio_path": "webrtc" if self.hub is not None and self.mgr.rtsp is not None else "none",
            "call_audio": self.call_audio(),
            "last_register_at": _iso(reg.at if reg is not None else self.last_register_at),
            "last_invite_at": _iso(self.last_invite_at),
            "rtp_bytes_in": stats.get("rx_bytes", 0),
            "rtp_bytes_out": stats.get("tx_bytes", 0),
            "nat_mode": self.nat_mode(),
            "error": error,
            "door": self.cfg.id,
            "mode": self.cfg.mode,
            "preset": self.cfg.preset,
            "listen": self.mgr.listen_address(),
            "registered": self.registered(),
            "problem": self.problem(),
            "talker": self.talker,
            "camera": self.mgr.entity_of(self.cfg.id, "camera"),
            "talk_camera": self.mgr.entity_of(self.cfg.id, "talk"),
            "open_button": self.mgr.entity_of(self.cfg.id, "open"),
            "can_open": bool(self.cfg.open or self.mgr.linked_lock(self.cfg.id)),
            "last_call": _stamped(self.last_call),
            "last_open": _stamped(self.last_open),
        }
        if reg is not None:
            out["public_address"] = f"{reg.public[0]}:{reg.public[1]}" if reg.public else None
        if self.binding is not None:
            out["panel_contact"] = f"{self.binding.source[0]}:{self.binding.source[1]}"
        if self.last_rejected is not None:
            out["last_rejected"] = _stamped(self.last_rejected)
        if self.call is not None and self.call.rtp is not None:
            out["audio"] = {**self.call.rtp.stats(), "from_card_packets": self.hub.back_packets if self.hub else 0}
        return out

    # --- the call ------------------------------------------------------------------------------------------------

    def on_invite(self, req: sip.Message, src: tuple[str, int]) -> None:
        ep = self.mgr.endpoint
        if self.call is not None and req.call_id == self.call.invite.call_id:
            self._reinvite(req, src)
            return
        if self.call is not None:
            ep.reply(req, src, 486, "Busy Here", to_tag=sip.new_tag())
            return
        media = sip.parse_sdp(req.body) if req.body else None
        chosen = media.choose(self.cfg.codec) if media is not None else None
        caller = sip.user_of(sip.uri_of(req.get("from") or ""))
        if media is None or chosen is None:
            ep.reply(req, src, 488, "Not Acceptable Here", to_tag=sip.new_tag())
            self._log_call({"from": caller, "source": f"{src[0]}:{src[1]}", "result": "codec_mismatch", "offered": [media.names.get(p, str(p)) for p in media.pts] if media else []})
            self.changed()
            return
        call = Call(invite=req, source=src, media=media, pt=chosen[0], codec=chosen[1], caller=caller)
        self.call = call
        ep.reply(req, src, 100, "Trying")
        ep.reply(req, src, 180, "Ringing", to_tag=call.to_tag, headers=[self.mgr.contact(self)])
        self.state = RINGING
        self._cancel_timers()
        loop = asyncio.get_running_loop()
        self._ring_timer = loop.call_later(T["ring_timeout_s"], self._ring_timeout)
        self.mgr.fire(self, RINGING)
        for cb in list(self.rings):
            cb({"from": caller})
        self.changed()

    def _reinvite(self, req: sip.Message, src: tuple[str, int]) -> None:
        call = self.call
        assert call is not None
        media = sip.parse_sdp(req.body) if req.body else None
        if media is not None and call.rtp is not None and media.port:
            call.rtp.remote = (media.addr or src[0], media.port)
            call.rtp.allowed.add(call.rtp.remote[0])
        if call.ok is None:  # still ringing: the same 180
            self.mgr.endpoint.reply(req, src, 180, "Ringing", to_tag=call.to_tag, headers=[self.mgr.contact(self)])
            return
        self.mgr.endpoint.reply(req, src, 200, "OK", to_tag=call.to_tag, headers=[self.mgr.contact(self), ("Content-Type", "application/sdp")], body=self._sdp(call))

    def _sdp(self, call: Call) -> bytes:
        assert call.rtp is not None
        return sip.build_sdp(self.mgr.advertised(self)[0], call.rtp.port, call.pt, call.codec, call.media.dtmf_pt, call.session_id)

    async def answer(self, user_id: str | None) -> None:
        """«Ответить»: this person talks; anyone else is told it was answered on another device."""
        call = self.call
        if self.state == TALKING:
            if self.talker == user_id:
                return
            raise IntercomError("answered_elsewhere")
        if self.state != RINGING or call is None:
            raise IntercomError("no_call")
        session = rtp.RtpSession(call.codec, call.pt, call.media.dtmf_pt, self._from_panel)
        session.remote = (call.media.addr or call.source[0], call.media.port)
        session.allowed = {call.source[0], *self.sources()}
        session.any_source = self.cfg.mode == "cloud"
        try:
            await session.open(self.mgr.bind_host, tuple(T["rtp_ports"]))
        except OSError as err:
            _LOGGER.warning("Hauflo intercom: no port for the sound (%s)", err)
            raise IntercomError("no_audio_port") from err
        if self.call is not call or self.state != RINGING:  # the panel gave up meanwhile
            session.close()
            raise IntercomError("no_call")
        call.rtp = session
        call.ok = self.mgr.endpoint.reply(call.invite, call.source, 200, "OK", to_tag=call.to_tag, headers=[self.mgr.contact(self), ("Content-Type", "application/sdp"), ("Allow", "INVITE, ACK, CANCEL, BYE, OPTIONS, INFO")], body=self._sdp(call))
        call.acked = asyncio.get_running_loop().create_future()
        call.answered_at = time.time()
        self._cancel_timers()
        self.state = TALKING
        self.talker = user_id
        session.punch()
        self._spawn(self._await_ack(call))
        self._talk_task = self._spawn(self._refresh(call))
        self.mgr.fire(self, TALKING, user_id)
        self.changed()

    async def _await_ack(self, call: Call) -> None:
        assert call.ok is not None and call.acked is not None
        got = await self.mgr.endpoint.retransmit(call.ok, call.source, call.acked)
        if got is None and self.call is call:
            self._end("no_ack")
            await self._bye(call)

    async def _refresh(self, call: Call) -> None:
        """While talking: the counters on the plate, every few seconds."""
        while self.call is call and self.state == TALKING:
            await asyncio.sleep(T["talk_refresh_s"])
            self.changed()

    def on_ack(self, req: sip.Message) -> None:
        call = self.call
        if call is not None and call.acked is not None and req.call_id == call.invite.call_id and not call.acked.done():
            call.acked.set_result(True)

    def on_cancel(self, req: sip.Message, src: tuple[str, int]) -> None:
        call = self.call
        ep = self.mgr.endpoint
        if call is None or req.call_id != call.invite.call_id:
            ep.reply(req, src, 481, "Call/Transaction Does Not Exist")
            return
        ep.reply(req, src, 200, "OK")
        if self.state != RINGING:
            return
        ep.reply(call.invite, call.source, 487, "Request Terminated", to_tag=call.to_tag)
        reason = req.get("reason") or ""
        # RFC 3326: «SIP;cause=200;text="Call completed elsewhere"» — the indoor monitor picked up.
        self._end("answered_elsewhere" if "cause=200" in reason.replace(" ", "") else "missed")

    def on_bye(self, req: sip.Message, src: tuple[str, int]) -> None:
        call = self.call
        if call is None or req.call_id != call.invite.call_id:
            self.mgr.endpoint.reply(req, src, 481, "Call/Transaction Does Not Exist")
            return
        self.mgr.endpoint.reply(req, src, 200, "OK")
        self._end("ended_by_panel")

    def on_info(self, req: sip.Message, src: tuple[str, int]) -> None:
        ok = self.call is not None and req.call_id == self.call.invite.call_id
        self.mgr.endpoint.reply(req, src, 200 if ok else 481, "OK" if ok else "Call/Transaction Does Not Exist")

    async def hangup(self, user_id: str | None, admin: bool) -> None:
        call = self.call
        if call is None or self.state not in (RINGING, TALKING):
            raise IntercomError("no_call")
        if self.state == TALKING and self.talker not in (None, user_id) and not admin:
            raise IntercomError("not_yours")
        if self.state == RINGING:
            self.mgr.endpoint.reply(call.invite, call.source, 603, "Decline", to_tag=call.to_tag)
            self._end("declined")
            return
        self._end("hung_up")
        await self._bye(call)

    def _ring_timeout(self) -> None:
        call = self.call
        if call is not None and self.state == RINGING:
            self.mgr.endpoint.reply(call.invite, call.source, 480, "Temporarily Unavailable", to_tag=call.to_tag)
            self._end("missed")

    def _in_dialog(self, call: Call, method: str) -> sip.Message:
        """A request of ours inside the panel's call (BYE, INFO): we are its callee, so From / To swap."""
        call.cseq += 1
        inv = call.invite
        target = sip.uri_of((inv.get_all("contact") or [inv.get("from") or ""])[0])
        msg = sip.Message(method=method, uri=target)
        host, port = self.mgr.advertised(self)
        msg.headers = [self.mgr.endpoint.via(host, port), ("Max-Forwards", "70")]
        msg.headers += [("Route", r) for r in inv.get_all("record-route")]
        msg.headers += [
            ("From", f"{inv.get('to')};tag={call.to_tag}" if sip.param(inv.get("to") or "", "tag") is None else inv.get("to") or ""),
            ("To", inv.get("from") or ""),
            ("Call-ID", inv.call_id),
            ("CSeq", f"{call.cseq} {method}"),
        ]
        return msg

    async def _bye(self, call: Call) -> None:
        with contextlib.suppress(Exception):
            await self.mgr.endpoint.request(self._in_dialog(call, "BYE"), call.source)

    def _end(self, result: str) -> None:
        call = self.call
        if call is None:
            return
        info: dict[str, Any] = {
            "from": call.caller,
            "source": f"{call.source[0]}:{call.source[1]}",
            "result": result,
            "answered": call.answered_at is not None,
            "codec": call.codec,
            "duration_s": round(time.time() - call.answered_at) if call.answered_at else 0,
        }
        if call.rtp is not None:
            stats = call.rtp.stats()
            info.update({k: stats[k] for k in ("rx_packets", "tx_packets", "rx_bytes", "tx_bytes", "sdp_remote", "rtp_from")})
            info["from_card_packets"] = self.hub.back_packets if self.hub is not None else 0
            info["sdp_private"] = sip.is_private(call.media.addr or call.source[0]) and not sip.is_private(call.source[0])
            call.rtp.close()
        if call.acked is not None and not call.acked.done():
            call.acked.cancel()
        self._log_call(info)
        self.call = None
        self.talker = None
        self._cancel_timers()
        if self._talk_task is not None:
            self._talk_task.cancel()
            self._talk_task = None
        if self.hub is not None:
            self.hub.back_packets = 0
        if result == "answered_elsewhere":
            self.state = ELSEWHERE
            self._elsewhere_timer = asyncio.get_running_loop().call_later(T["answered_elsewhere_s"], self._idle)
        else:
            self.state = IDLE
        self.mgr.fire(self, self.state, None, result)
        self.changed()

    def _idle(self) -> None:
        if self.state == ELSEWHERE:
            self.state = IDLE
            self.changed()

    def _log_call(self, info: dict[str, Any]) -> None:
        self.last_call = {"at": time.time(), **info}

    def _cancel_timers(self) -> None:
        for t in (self._ring_timer, self._elsewhere_timer):
            if t is not None:
                t.cancel()
        self._ring_timer = self._elsewhere_timer = None

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # --- sound ---------------------------------------------------------------------------------------------------

    def _from_panel(self, payload: bytes) -> None:
        if self.hub is not None and self.call is not None:
            self.hub.feed(rtp.convert(payload, self.call.codec, self.hub.codec))

    def from_card(self, payload: bytes) -> None:
        """The backchannel: only while the call is answered (one talker), never into a ringing / no call."""
        call = self.call
        if call is not None and self.state == TALKING and call.rtp is not None and self.hub is not None:
            call.rtp.send(rtp.convert(payload, self.hub.codec, call.codec))

    # --- the door ------------------------------------------------------------------------------------------------

    async def open(self, user_id: str | None) -> None:
        """«Открыть»: only from the button / the service — never a push's action (notify.RUN_SERVICES)."""
        how = self.cfg.open
        lock = self.mgr.linked_lock(self.cfg.id)
        try:
            if lock:
                # Р56: the integrator linked a smart lock to this door — it is what «Открыть» opens (the panel's own relay
                # may be wired to nothing), with the lock's own «who may unlock».
                how = "lock"
                await self.mgr.open_lock(lock, user_id)
            elif how == "dtmf":
                await self._open_dtmf()
            elif how == "http":
                await self.mgr.http(self.cfg, self.cfg.open_http or {})
            elif how == "entity":
                await self.mgr.press_entity(self.cfg.open_entity)
            else:
                raise IntercomError("open_not_set")
        except IntercomError as err:
            self.last_open = {"at": time.time(), "via": how, "ok": False, "error": err.key}
            self.changed()
            raise
        self.last_open = {"at": time.time(), "via": how, "ok": True, "by": user_id}
        self.mgr.fire(self, "opened", user_id)
        self.changed()

    async def _open_dtmf(self) -> None:
        call = self.call
        if call is None or self.state != TALKING or call.rtp is None or not self.cfg.dtmf:
            raise IntercomError("open_needs_call")
        if call.media.dtmf_pt is not None:
            await call.rtp.send_dtmf(self.cfg.dtmf, T["dtmf_ms"], T["frame_ms"])
            return
        # The panel offered no telephone-event: SIP INFO (application/dtmf-relay), what such panels take instead.
        for ch in self.cfg.dtmf:
            msg = self._in_dialog(call, "INFO")
            msg.headers.append(("Content-Type", "application/dtmf-relay"))
            msg.body = f"Signal={ch}\r\nDuration={T['dtmf_ms']}\r\n".encode()
            resp = await self.mgr.endpoint.request(msg, call.source)
            if resp is None or resp.status >= 300:
                raise IntercomError("open_failed")

    def close(self) -> None:
        call = self.call
        if call is not None:
            if self.state == RINGING:
                self.mgr.endpoint.reply(call.invite, call.source, 480, "Temporarily Unavailable", to_tag=call.to_tag)
            elif self.state == TALKING and self.mgr.endpoint.transport is not None:
                self.mgr.endpoint.send(self._in_dialog(call, "BYE"), call.source)
            if call.rtp is not None:
                call.rtp.close()
            self.call = None
        self._cancel_timers()
        for t in list(self._tasks):
            t.cancel()
