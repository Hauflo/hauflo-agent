"""The doors of one house: one SIP socket on the chosen address (never every interface), the loopback RTSP of their
sound for HA's go2rtc, the registrations (the agent on a panel's / a cloud's server; the panel on the agent), and who
a request is for — a call is taken only from an address the door's settings name. Services «Ответить» / «Завершить»
(the door's opening is its button only — notify.RUN_SERVICES never names it, a push can't open a door)."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import socket
import time
from typing import Any

import aiohttp

from homeassistant.core import Context, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from ..const import DOMAIN
from ..tuning import TUNING
from . import sip
from .door import TALKING, Door, DoorConfig, IntercomError, configs, render
from .rtsp import AudioHub, RtspServer
from .ua import Endpoint, Registrar, Registration, Timers

_LOGGER = logging.getLogger(__name__)
T = TUNING["intercom"]
EVENT = f"{DOMAIN}_intercom"
KINDS = ("camera", "talk", "call", "state", "open")
SERVICE_ANSWER = "intercom_answer"
SERVICE_HANGUP = "intercom_hangup"
_SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_id})


def unique_id(entry_id: str, door: str, kind: str) -> str:
    return f"{entry_id}_intercom_{door}_{kind}"


class Intercom:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any]) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.options = dict(options or {})
        self.locks: Any = None  # Р56: the house's locks (locks.py) — a door with no own way to open opens by its linked lock
        self.doors: dict[str, Door] = {cfg.id: Door(cfg, self) for cfg in configs(self.options)}
        self.bind_host = ""
        self.listen_error: str | None = None
        self.public = _address(str(self.options.get("public") or ""))
        timers = Timers(T["t1_s"], T["t2_s"], T["transaction_s"], T["answers_kept"])
        self.endpoint = Endpoint(self._on_request, timers, T["user_agent"])
        self.registrar = Registrar(T["realm"], T["nonce_ttl_s"], T["registrar_max_expires_s"])
        self.rtsp: RtspServer | None = None
        self.hub_device_id: str | None = None  # the «Hauflo» service device: each door's device is under it
        self.entities: dict[tuple[str, str], str] = {}  # (door, kind) → entity_id, as each entity is added
        self._tasks: set[asyncio.Task[Any]] = set()
        self._ice_off: Any = None  # HA WebRTC's remover of this house's TURN / STUN servers (setup.register_ice)

    # --- life ----------------------------------------------------------------------------------------------------

    async def start(self) -> None:
        """No door — no socket. The SIP socket on the chosen address only; the sound's RTSP on loopback only."""
        if not self.doors:
            return
        from .setup import register_ice  # noqa: PLC0415 — setup imports the view's HA modules

        self._ice_off = register_ice(self.hass, self.options)
        self.bind_host = await self._bind_host()
        if not self.bind_host:
            self.listen_error = "no_address"
        else:
            try:
                port = self.options.get("port")
                await self.endpoint.start(self.bind_host, int(T["sip_port"] if port in (None, "") else port))
            except OSError as err:
                _LOGGER.warning("Hauflo intercom: the call port is taken (%s)", err)
                self.listen_error = "sip_port_busy"
        self.rtsp = RtspServer("127.0.0.1", T["rtsp_port"])
        try:
            await self.rtsp.start()
        except OSError:
            self.rtsp = RtspServer("127.0.0.1", 0)  # its port taken: any free one (the camera asks the URL each time)
            await self.rtsp.start()
        for door in self.doors.values():
            door.hub = AudioHub(door.cfg.codec, T["frame_ms"], door.from_card, T["rtsp_max_clients"])
            self.rtsp.hubs[door.cfg.id] = door.hub
            if self.endpoint.transport is not None and door.cfg.error is None and door.cfg.mode in ("register", "cloud"):
                self._spawn(self._register(door))
        for door in self.doors.values():
            door.changed()

    async def stop(self) -> None:
        if self._ice_off is not None:
            self._ice_off()
            self._ice_off = None
        for door in self.doors.values():
            door.close()
            if door.registration is not None:
                await door.registration.stop()
                door.registration = None
        for t in list(self._tasks):
            t.cancel()
        self.endpoint.stop()
        if self.rtsp is not None:
            await self.rtsp.stop()
            self.rtsp = None

    async def _bind_host(self) -> str:
        chosen = str(self.options.get("bind") or "auto")
        if chosen != "auto":
            return chosen
        # HA's own LAN address (the one it reaches the internet from) — what a panel on the same network calls.
        try:
            from homeassistant.components import network

            return str(await network.async_get_source_ip(self.hass) or "")
        except Exception as err:  # noqa: BLE001 — no address: the plate says so
            _LOGGER.debug("Hauflo intercom: no source address (%s)", err)
            return ""

    async def _register(self, door: Door) -> None:
        cfg = door.cfg
        cloud = cfg.mode == "cloud"
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(cfg.sip_server, cfg.sip_port, family=socket.AF_INET, type=socket.SOCK_DGRAM)
        except OSError:
            door.registration = None
            door.dns_error = True
            door.changed()
            return
        door.dns_error = False
        door.server_ips = {str(i[4][0]) for i in infos}
        server = (str(infos[0][4][0]), cfg.sip_port)
        extra = cfg.register_extra

        def _extra(msg: sip.Message) -> None:
            assert extra is not None
            msg.headers.append(("Content-Type", str(extra.get("content_type") or "application/xml")))
            msg.body = render(str(extra.get("body") or ""), cfg).encode()

        reg = Registration(
            self.endpoint,
            server,
            cfg.sip_server,
            cfg.sip_user,
            cfg.sip_password,
            self.public or (self.bind_host, self.endpoint.port),
            expires=T["cloud_register_expires_s"] if cloud else T["register_expires_s"],
            retry=T["register_retry_s"],
            keepalive=T["nat_keepalive_s"],
            nat=cloud,
            on_change=door.changed,
            extra=_extra if extra else None,
        )
        door.registration = reg
        reg.start()

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # --- addresses -----------------------------------------------------------------------------------------------

    def listen_address(self) -> str | None:
        return f"{self.endpoint.host}:{self.endpoint.port}" if self.endpoint.transport is not None else None

    def advertised(self, door: Door) -> tuple[str, int]:
        """The address the panel answers to (Contact, SDP): the one a cloud saw us at, the one set by hand, or ours."""
        reg = door.registration
        if reg is not None and reg.public is not None:
            return reg.public
        return self.public or (self.bind_host, self.endpoint.port)

    def contact(self, door: Door) -> tuple[str, str]:
        host, port = self.advertised(door)
        return ("Contact", f"<{sip.sip_uri(door.cfg.sip_user or door.cfg.id, host, port)}>")

    def entity_of(self, door: str, kind: str) -> str | None:
        return self.entities.get((door, kind))

    def door_of(self, entity_id: str) -> Door | None:
        for (door, _kind), eid in self.entities.items():
            if eid == entity_id:
                return self.doors.get(door)
        return None

    def watched(self) -> set[str]:
        """The doorbell events: a ring pushes to the phones (notify.py) like any doorbell the canon watches."""
        return {eid for (_d, kind), eid in self.entities.items() if kind == "call"}

    # --- SIP -----------------------------------------------------------------------------------------------------

    def _door_for(self, msg: sip.Message, src: tuple[str, int]) -> Door | None:
        """The door a request is for: its source must be one the door's settings name (the panel, its server, the
        registered panel); with several such doors the dialled number decides."""
        doors = [d for d in self.doors.values() if d.cfg.error is None and src[0] in d.sources()]
        if len(doors) > 1:
            user = sip.user_of(msg.uri or "")
            doors = [d for d in doors if user in (d.cfg.sip_user, d.cfg.id)] or doors[:1]
        return doors[0] if doors else None

    def _door_of_call(self, call_id: str) -> Door | None:
        return next((d for d in self.doors.values() if d.call is not None and d.call.invite.call_id == call_id), None)

    def _on_request(self, msg: sip.Message, src: tuple[str, int]) -> None:
        method = msg.method or ""
        ep = self.endpoint
        if method == "REGISTER":
            self._on_register(msg, src)
            return
        if method in ("ACK", "BYE", "CANCEL", "INFO"):
            door = self._door_of_call(msg.call_id)
            if door is None or src[0] != door.call.source[0]:  # type: ignore[union-attr]
                if method != "ACK":
                    ep.reply(msg, src, 481, "Call/Transaction Does Not Exist")
                return
            {"ACK": lambda: door.on_ack(msg), "BYE": lambda: door.on_bye(msg, src), "CANCEL": lambda: door.on_cancel(msg, src), "INFO": lambda: door.on_info(msg, src)}[method]()
            return
        door = self._door_for(msg, src)
        if method == "INVITE":
            if door is None:
                self._rejected(msg, src)
                ep.reply(msg, src, 403, "Forbidden", to_tag=sip.new_tag())
                return
            door.last_invite_at = time.time()
            door.on_invite(msg, src)
            return
        if method in ("OPTIONS", "NOTIFY"):
            if door is None:
                ep.reply(msg, src, 403, "Forbidden")
                return
            ep.reply(msg, src, 200, "OK", headers=[("Allow", "INVITE, ACK, CANCEL, BYE, OPTIONS, INFO"), ("Accept", "application/sdp")])
            return
        ep.reply(msg, src, 501, "Not Implemented")

    def _rejected(self, msg: sip.Message, src: tuple[str, int]) -> None:
        """A call from an address no door names: refused, and the plate tells the integrator which address it was."""
        info = {"at": time.time(), "source": f"{src[0]}:{src[1]}", "from": sip.user_of(sip.uri_of(msg.get("from") or "")), "to": sip.user_of(msg.uri or "")}
        for door in self.doors.values():
            door.last_rejected = info
            door.changed()

    def _on_register(self, msg: sip.Message, src: tuple[str, int]) -> None:
        user = sip.user_of(sip.uri_of(msg.get("to") or ""))
        door = next((d for d in self.doors.values() if d.cfg.mode == "registrar" and d.cfg.error is None and d.cfg.sip_user == user), None)
        if door is None or (door.cfg.host and src[0] != door.cfg.host):
            self.endpoint.reply(msg, src, 403 if door is not None else 404, "Forbidden" if door is not None else "Not Found", to_tag=sip.new_tag())
            return
        status, reason, headers, binding = self.registrar.check(msg, src, door.cfg.sip_user, door.cfg.sip_password)
        if status == 200:
            door.binding = binding
            door.last_register_at = time.time()
            door.register_error = None
        elif status == 403:
            door.register_error = "wrong_password"
        self.endpoint.reply(msg, src, status, reason, to_tag=sip.new_tag(), headers=headers)
        door.changed()

    # --- the card's events -----------------------------------------------------------------------------------------

    def fire(self, door: Door, state: str, user_id: str | None = None, result: str | None = None) -> None:
        """`hauflo_intercom` on the bus: the card's / an automation's view of the call (the push is the event entity)."""
        self.hass.bus.async_fire(EVENT, {"door": door.cfg.id, "state": state, "user_id": user_id, "result": result, "entity_id": self.entity_of(door.cfg.id, "state"), "call": self.entity_of(door.cfg.id, "call")})

    # --- the door ------------------------------------------------------------------------------------------------

    async def http(self, cfg: DoorConfig, spec: dict[str, Any], body_ok: bool = False) -> bytes:
        """A request to the panel's own web API (ISAPI, CGI…) as its preset describes it; IntercomError on a refusal."""
        if not cfg.host or not spec.get("path"):
            raise IntercomError("open_not_set")
        scheme = str(spec.get("scheme") or "http")
        port = f":{int(spec['port'])}" if str(spec.get("port") or "").isdigit() else ""  # a panel's API off 80 / 443
        url = f"{scheme}://{cfg.host}{port}{render(str(spec['path']), cfg)}"
        kw: dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=T["http_timeout_s"])}
        headers: dict[str, str] = {}
        auth = str(spec.get("auth") or "none")
        if auth == "digest" and cfg.user:
            kw["middlewares"] = (aiohttp.DigestAuthMiddleware(cfg.user, cfg.password),)
        elif auth == "basic" and cfg.user:  # by hand: aiohttp.BasicAuth is going away in aiohttp 4
            headers["Authorization"] = "Basic " + base64.b64encode(f"{cfg.user}:{cfg.password}".encode()).decode()
        if spec.get("body"):
            kw["data"] = render(str(spec["body"]), cfg).encode()
            headers["Content-Type"] = str(spec.get("content_type") or "text/plain")
        if headers:
            kw["headers"] = headers
        # A panel's own self-signed certificate is the norm on a LAN; a public address is verified.
        session = async_get_clientsession(self.hass, verify_ssl=not sip.is_private(cfg.host))
        try:
            async with session.request(str(spec.get("method") or "GET"), url, **kw) as resp:
                data = await resp.read()
                if resp.status >= 300:
                    _LOGGER.warning("Hauflo intercom: the panel said %s", resp.status)
                    raise IntercomError("open_refused" if resp.status in (401, 403) else "open_failed")
                return data
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("Hauflo intercom: the panel did not answer (%s)", err)
            raise IntercomError("open_failed") from err

    async def press_entity(self, entity_id: str) -> None:
        """A relay of another integration (KNX, Zigbee…) that opens the door: its own «press / on / unlock»."""
        domain = entity_id.split(".", 1)[0]
        service = T["open_services"].get(domain)
        if not entity_id or service is None:
            raise IntercomError("open_not_set")
        try:
            await self.hass.services.async_call(domain, service, {"entity_id": entity_id}, blocking=True)
        except HomeAssistantError as err:
            raise IntercomError("open_failed") from err

    def linked_lock(self, door_id: str) -> str | None:
        return self.locks.linked(door_id) if self.locks is not None else None

    async def open_lock(self, entity_id: str, user_id: str | None) -> None:
        """A door opened by its linked smart lock (Р56): «открыть» when the lock can, else «отпереть»; only by the people
        the lock's settings list (locks.py) — else «Нет прав»."""
        if self.locks is None:
            raise IntercomError("open_not_set")
        if not self.locks.may_unlock(entity_id, user_id):
            raise IntercomError("open_no_rights")
        try:
            await self.locks.act(entity_id, "open" if self.locks.can_open(entity_id) else "unlock", Context(user_id=user_id))
        except HomeAssistantError as err:
            raise IntercomError("open_failed") from err

    async def snapshot(self, door: Door) -> bytes | None:
        spec = door.cfg.snapshot
        if not spec or not door.cfg.host:
            return None
        with contextlib.suppress(IntercomError):
            return await self.http(door.cfg, spec)
        return None

    # --- services ------------------------------------------------------------------------------------------------

    async def answer(self, entity_id: str, user_id: str | None) -> None:
        door = self._door_or_raise(entity_id)
        try:
            await door.answer(user_id)
        except IntercomError as err:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key=f"intercom_{err.key}") from err

    async def hangup(self, entity_id: str, user_id: str | None, admin: bool) -> None:
        door = self._door_or_raise(entity_id)
        try:
            await door.hangup(user_id, admin)
        except IntercomError as err:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key=f"intercom_{err.key}") from err

    async def open(self, door: Door, user_id: str | None) -> None:
        try:
            await door.open(user_id)
        except IntercomError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key=f"intercom_{err.key}") from err

    def _door_or_raise(self, entity_id: str) -> Door:
        door = self.door_of(entity_id)
        if door is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="intercom_unknown_door")
        return door

    def talking(self) -> list[Door]:
        return [d for d in self.doors.values() if d.state == TALKING]


def _address(text: str) -> tuple[str, int] | None:
    """«203.0.113.7» / «203.0.113.7:5062» — the address a router forwards to the agent (set by hand)."""
    text = text.strip()
    if not text:
        return None
    host, _, port = text.partition(":")
    return host, int(port) if port.isdigit() else int(T["sip_port"])


@callback
def async_setup_services(hass: HomeAssistant, find: Any) -> None:
    """`hauflo.intercom_answer` / `hauflo.intercom_hangup` (entity_id: any entity of the door). `find()` → the
    running Intercom or None."""
    if hass.services.has_service(DOMAIN, SERVICE_ANSWER):
        return

    def _mgr() -> Intercom:
        mgr = find()
        if mgr is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="intercom_unknown_door")
        return mgr

    async def _answer(call: ServiceCall) -> None:
        await _mgr().answer(call.data["entity_id"], call.context.user_id)

    async def _hangup(call: ServiceCall) -> None:
        admin = False
        if call.context.user_id is not None and (user := await hass.auth.async_get_user(call.context.user_id)) is not None:
            admin = user.is_admin
        elif call.context.user_id is None:
            admin = True  # an automation / the system
        await _mgr().hangup(call.data["entity_id"], call.context.user_id, admin)

    hass.services.async_register(DOMAIN, SERVICE_ANSWER, _answer, schema=_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_HANGUP, _hangup, schema=_SCHEMA)
