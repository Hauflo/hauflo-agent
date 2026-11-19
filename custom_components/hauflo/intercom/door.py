"""One door: its state, attributes and the lock it opens."""
from __future__ import annotations
import asyncio
from collections.abc import Callable
import logging
import time
from typing import TYPE_CHECKING, Any
from . import rtp
from .door_call import DoorCallMixin
from .door_config import IDLE, RINGING, TALKING, IntercomError, iso, stamped, t_cfg
if TYPE_CHECKING:
    from .door_config import Call, DoorConfig
    from .manager import Intercom
_LOGGER = logging.getLogger(__name__)

class Door(DoorCallMixin):
    """A door's call and diagnosis."""

    def __init__(self, cfg: DoorConfig, mgr: Intercom) -> None:
        self.cfg = cfg
        self.mgr = mgr
        self.state = IDLE
        self.call: Call | None = None
        self.talker: str | None = None
        self.listeners: set[Callable[[], None]] = set()
        self.rings: set[Callable[[dict[str, Any]], None]] = set()
        self.registration: Any = None
        self.binding: Any = None
        self.server_ips: set[str] = set()
        self.dns_error = False
        self.register_error: str | None = None
        self.last_register_at: float | None = None
        self.last_invite_at: float | None = None
        self.last_call: dict[str, Any] | None = None
        self.last_open: dict[str, Any] | None = None
        self.last_rejected: dict[str, Any] | None = None
        self.hub: Any = None
        self._ring_timer: asyncio.TimerHandle | None = None
        self._elsewhere_timer: asyncio.TimerHandle | None = None
        self._talk_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    def changed(self) -> None:
        for cb in list(self.listeners):
            cb()

    def sources(self) -> set[str]:
        """The addresses a call of this door may come from."""
        out = {self.cfg.host} if self.cfg.host else set()
        if self.cfg.mode in ('register', 'cloud'):
            out |= self.server_ips
        if self.cfg.mode == 'registrar' and self.binding is not None and (self.binding.until > time.time()):
            out.add(self.binding.source[0])
        return out

    def registered(self) -> bool | None:
        if self.cfg.mode in ('register', 'cloud'):
            return bool(self.registration is not None and self.registration.registered)
        if self.cfg.mode == 'registrar':
            return self.binding is not None and self.binding.until > time.time()
        return None

    def problem(self) -> str | None:
        """Why it doesn't work — the first that applies (the card's plate words: entity.sensor.intercom.state_attributes)."""
        if self.mgr.listen_error:
            return self.mgr.listen_error
        if self.cfg.error:
            return f'missing_{self.cfg.error}'
        if self.dns_error:
            return 'server_not_found'
        if self.cfg.mode in ('register', 'cloud') and (not self.registered()):
            return 'not_registered'
        if self.cfg.mode == 'registrar' and (not self.registered()):
            return 'panel_not_registered'
        if self.state == TALKING and self.call is not None and (self.call.rtp is not None) and (self.call.answered_at is not None):
            quiet = time.time() - self.call.answered_at > t_cfg()['no_audio_s']
            if quiet and self.call.rtp.rx == 0:
                return 'no_audio_from_panel'
            if quiet and (self.hub is None or self.hub.back_packets == 0):
                return 'no_audio_to_panel'
            return None
        if self.last_rejected is not None and (self.last_call is None or self.last_rejected['at'] > self.last_call['at']):
            return 'call_from_unknown_address'
        if self.last_call is None:
            return 'no_calls_yet'
        result = self.last_call.get('result')
        if result in ('codec_mismatch', 'no_ack'):
            return result
        if self.last_call.get('answered') and (not self.last_call.get('rx_packets')):
            return 'no_audio_from_panel'
        return None

    def call_audio(self) -> str | None:
        """Which way the sound went in the call now / the last one (hass-sip's words): none, no_rx (nothing from the panel), no_tx (nothing from the card), bidirectional."""
        if self.call is not None and self.call.rtp is not None:
            rx, tx = (self.call.rtp.rx, self.hub.back_packets if self.hub is not None else 0)
        elif self.last_call is not None and self.last_call.get('answered'):
            rx, tx = (self.last_call.get('rx_packets', 0), self.last_call.get('from_card_packets', 0))
        else:
            return None
        return 'bidirectional' if rx and tx else 'no_tx' if rx else 'no_rx' if tx else 'none'

    def nat_mode(self) -> str:
        """How the panel learns our address: «observed» (a cloud saw us behind a NAT), «manual» (set by hand), «lan»."""
        reg = self.registration
        if reg is not None and reg.public is not None:
            return 'observed'
        return 'manual' if self.mgr.public is not None else 'lan'

    def attributes(self) -> dict[str, Any]:
        reg = self.registration
        stats = self.call.rtp.stats() if self.call is not None and self.call.rtp is not None else self.last_call or {}
        error = reg.error if reg is not None else self.register_error
        out: dict[str, Any] = {'audio_path': 'webrtc' if self.hub is not None and self.mgr.rtsp is not None else 'none', 'call_audio': self.call_audio(), 'last_register_at': iso(reg.at if reg is not None else self.last_register_at), 'last_invite_at': iso(self.last_invite_at), 'rtp_bytes_in': stats.get('rx_bytes', 0), 'rtp_bytes_out': stats.get('tx_bytes', 0), 'nat_mode': self.nat_mode(), 'error': error, 'door': self.cfg.id, 'mode': self.cfg.mode, 'preset': self.cfg.preset, 'listen': self.mgr.listen_address(), 'registered': self.registered(), 'problem': self.problem(), 'talker': self.talker, 'camera': self.mgr.entity_of(self.cfg.id, 'camera'), 'talk_camera': self.mgr.entity_of(self.cfg.id, 'talk'), 'open_button': self.mgr.entity_of(self.cfg.id, 'open'), 'can_open': bool(self.cfg.open or self.mgr.linked_lock(self.cfg.id)), 'last_call': stamped(self.last_call), 'last_open': stamped(self.last_open)}
        if reg is not None:
            out['public_address'] = f'{reg.public[0]}:{reg.public[1]}' if reg.public else None
        if self.binding is not None:
            out['panel_contact'] = f'{self.binding.source[0]}:{self.binding.source[1]}'
        if self.last_rejected is not None:
            out['last_rejected'] = stamped(self.last_rejected)
        if self.call is not None and self.call.rtp is not None:
            out['audio'] = {**self.call.rtp.stats(), 'from_card_packets': self.hub.back_packets if self.hub else 0}
        return out

    def _from_panel(self, payload: bytes) -> None:
        if self.hub is not None and self.call is not None:
            self.hub.feed(rtp.convert(payload, self.call.codec, self.hub.codec))

    def from_card(self, payload: bytes) -> None:
        """The backchannel: only while the call is answered (one talker), never into a ringing / no call."""
        call = self.call
        if call is not None and self.state == TALKING and (call.rtp is not None) and (self.hub is not None):
            call.rtp.send(rtp.convert(payload, self.hub.codec, call.codec))

    async def open(self, user_id: str | None) -> None:
        """«Открыть»: only from the button / the service — never a push's action (notify.RUN_SERVICES)."""
        how = self.cfg.open
        lock = self.mgr.linked_lock(self.cfg.id)
        try:
            if lock:
                how = 'lock'
                await self.mgr.open_lock(lock, user_id)
            elif how == 'dtmf':
                await self._open_dtmf()
            elif how == 'http':
                await self.mgr.http(self.cfg, self.cfg.open_http or {})
            elif how == 'entity':
                await self.mgr.press_entity(self.cfg.open_entity)
            else:
                raise IntercomError('open_not_set')
        except IntercomError as err:
            self.last_open = {'at': time.time(), 'via': how, 'ok': False, 'error': err.key}
            self.changed()
            raise
        self.last_open = {'at': time.time(), 'via': how, 'ok': True, 'by': user_id}
        self.mgr.fire(self, 'opened', user_id)
        self.changed()

    async def _open_dtmf(self) -> None:
        call = self.call
        if call is None or self.state != TALKING or call.rtp is None or (not self.cfg.dtmf):
            raise IntercomError('open_needs_call')
        if call.media.dtmf_pt is not None:
            await call.rtp.send_dtmf(self.cfg.dtmf, t_cfg()['dtmf_ms'], t_cfg()['frame_ms'])
            return
        for ch in self.cfg.dtmf:
            msg = self._in_dialog(call, 'INFO')
            msg.headers.append(('Content-Type', 'application/dtmf-relay'))
            msg.body = f"Signal={ch}\r\nDuration={t_cfg()['dtmf_ms']}\r\n".encode()
            resp = await self.mgr.endpoint.request(msg, call.source)
            if resp is None or resp.status >= 300:
                raise IntercomError('open_failed')

    def close(self) -> None:
        call = self.call
        if call is not None:
            if self.state == RINGING:
                self.mgr.endpoint.reply(call.invite, call.source, 480, 'Temporarily Unavailable', to_tag=call.to_tag)
            elif self.state == TALKING and self.mgr.endpoint.transport is not None:
                self.mgr.endpoint.send(self._in_dialog(call, 'BYE'), call.source)
            if call.rtp is not None:
                call.rtp.close()
            self.call = None
        self._cancel_timers()
        for t in list(self._tasks):
            t.cancel()
