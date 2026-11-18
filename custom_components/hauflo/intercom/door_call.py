"""A door's SIP call: invite, answer, dialog and its end."""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Any
from ..guard import contained
from . import rtp, sip
from .door_config import ELSEWHERE, IDLE, RINGING, TALKING, Call, IntercomError, t_cfg
_LOGGER = logging.getLogger(__name__)

class DoorCallMixin:
    """Handles a door's SIP call."""

    def on_invite(self, req: sip.Message, src: tuple[str, int]) -> None:
        ep = self.mgr.endpoint
        if self.call is not None and req.call_id == self.call.invite.call_id:
            self._reinvite(req, src)
            return
        if self.call is not None:
            ep.reply(req, src, 486, 'Busy Here', to_tag=sip.new_tag())
            return
        media = sip.parse_sdp(req.body) if req.body else None
        chosen = media.choose(self.cfg.codec) if media is not None else None
        caller = sip.user_of(sip.uri_of(req.get('from') or ''))
        if media is None or chosen is None:
            ep.reply(req, src, 488, 'Not Acceptable Here', to_tag=sip.new_tag())
            self._log_call({'from': caller, 'source': f'{src[0]}:{src[1]}', 'result': 'codec_mismatch', 'offered': [media.names.get(p, str(p)) for p in media.pts] if media else []})
            self.changed()
            return
        call = Call(invite=req, source=src, media=media, pt=chosen[0], codec=chosen[1], caller=caller)
        self.call = call
        ep.reply(req, src, 100, 'Trying')
        ep.reply(req, src, 180, 'Ringing', to_tag=call.to_tag, headers=[self.mgr.contact(self)])
        self.state = RINGING
        self._cancel_timers()
        loop = asyncio.get_running_loop()
        self._ring_timer = loop.call_later(t_cfg()['ring_timeout_s'], self._ring_timeout)
        self.mgr.fire(self, RINGING)
        for cb in list(self.rings):
            cb({'from': caller})
        self.changed()

    def _reinvite(self, req: sip.Message, src: tuple[str, int]) -> None:
        call = self.call
        assert call is not None
        media = sip.parse_sdp(req.body) if req.body else None
        if media is not None and call.rtp is not None and media.port:
            call.rtp.remote = (media.addr or src[0], media.port)
            call.rtp.allowed.add(call.rtp.remote[0])
        if call.ok is None:
            self.mgr.endpoint.reply(req, src, 180, 'Ringing', to_tag=call.to_tag, headers=[self.mgr.contact(self)])
            return
        self.mgr.endpoint.reply(req, src, 200, 'OK', to_tag=call.to_tag, headers=[self.mgr.contact(self), ('Content-Type', 'application/sdp')], body=self._sdp(call))

    def _sdp(self, call: Call) -> bytes:
        assert call.rtp is not None
        return sip.build_sdp(self.mgr.advertised(self)[0], call.rtp.port, call.pt, call.codec, call.media.dtmf_pt, call.session_id)

    async def answer(self, user_id: str | None) -> None:
        """«Ответить»: this person talks; anyone else is told it was answered on another device."""
        call = self.call
        if self.state == TALKING:
            if self.talker == user_id:
                return
            raise IntercomError('answered_elsewhere')
        if self.state != RINGING or call is None:
            raise IntercomError('no_call')
        session = rtp.RtpSession(call.codec, call.pt, call.media.dtmf_pt, self._from_panel)
        session.remote = (call.media.addr or call.source[0], call.media.port)
        session.allowed = {call.source[0], *self.sources()}
        session.any_source = self.cfg.mode == 'cloud'
        try:
            await session.open(self.mgr.bind_host, tuple(t_cfg()['rtp_ports']))
        except OSError as err:
            _LOGGER.warning('Hauflo intercom: no port for the sound (%s)', err)
            raise IntercomError('no_audio_port') from err
        if self.call is not call or self.state != RINGING:
            session.close()
            raise IntercomError('no_call')
        call.rtp = session
        call.ok = self.mgr.endpoint.reply(call.invite, call.source, 200, 'OK', to_tag=call.to_tag, headers=[self.mgr.contact(self), ('Content-Type', 'application/sdp'), ('Allow', 'INVITE, ACK, CANCEL, BYE, OPTIONS, INFO')], body=self._sdp(call))
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
            self._end('no_ack')
            await self._bye(call)

    async def _refresh(self, call: Call) -> None:
        while self.call is call and self.state == TALKING:
            await asyncio.sleep(t_cfg()['talk_refresh_s'])
            self.changed()

    def on_ack(self, req: sip.Message) -> None:
        call = self.call
        if call is not None and call.acked is not None and (req.call_id == call.invite.call_id) and (not call.acked.done()):
            call.acked.set_result(True)

    def on_cancel(self, req: sip.Message, src: tuple[str, int]) -> None:
        call = self.call
        ep = self.mgr.endpoint
        if call is None or req.call_id != call.invite.call_id:
            ep.reply(req, src, 481, 'Call/Transaction Does Not Exist')
            return
        ep.reply(req, src, 200, 'OK')
        if self.state != RINGING:
            return
        ep.reply(call.invite, call.source, 487, 'Request Terminated', to_tag=call.to_tag)
        reason = req.get('reason') or ''
        self._end('answered_elsewhere' if 'cause=200' in reason.replace(' ', '') else 'missed')

    def on_bye(self, req: sip.Message, src: tuple[str, int]) -> None:
        call = self.call
        if call is None or req.call_id != call.invite.call_id:
            self.mgr.endpoint.reply(req, src, 481, 'Call/Transaction Does Not Exist')
            return
        self.mgr.endpoint.reply(req, src, 200, 'OK')
        self._end('ended_by_panel')

    def on_info(self, req: sip.Message, src: tuple[str, int]) -> None:
        ok = self.call is not None and req.call_id == self.call.invite.call_id
        self.mgr.endpoint.reply(req, src, 200 if ok else 481, 'OK' if ok else 'Call/Transaction Does Not Exist')

    async def hangup(self, user_id: str | None, admin: bool) -> None:
        call = self.call
        if call is None or self.state not in (RINGING, TALKING):
            raise IntercomError('no_call')
        if self.state == TALKING and self.talker not in (None, user_id) and (not admin):
            raise IntercomError('not_yours')
        if self.state == RINGING:
            self.mgr.endpoint.reply(call.invite, call.source, 603, 'Decline', to_tag=call.to_tag)
            self._end('declined')
            return
        self._end('hung_up')
        await self._bye(call)

    def _ring_timeout(self) -> None:
        call = self.call
        if call is not None and self.state == RINGING:
            self.mgr.endpoint.reply(call.invite, call.source, 480, 'Temporarily Unavailable', to_tag=call.to_tag)
            self._end('missed')

    def _in_dialog(self, call: Call, method: str) -> sip.Message:
        call.cseq += 1
        inv = call.invite
        target = sip.uri_of((inv.get_all('contact') or [inv.get('from') or ''])[0])
        msg = sip.Message(method=method, uri=target)
        host, port = self.mgr.advertised(self)
        msg.headers = [self.mgr.endpoint.via(host, port), ('Max-Forwards', '70')]
        msg.headers += [('Route', r) for r in inv.get_all('record-route')]
        msg.headers += [('From', f"{inv.get('to')};tag={call.to_tag}" if sip.param(inv.get('to') or '', 'tag') is None else inv.get('to') or ''), ('To', inv.get('from') or ''), ('Call-ID', inv.call_id), ('CSeq', f'{call.cseq} {method}')]
        return msg

    async def _bye(self, call: Call) -> None:
        with contained(_LOGGER, 'Hauflo intercom: BYE to the door not sent'):
            await self.mgr.endpoint.request(self._in_dialog(call, 'BYE'), call.source)

    def _end(self, result: str) -> None:
        call = self.call
        if call is None:
            return
        info: dict[str, Any] = {'from': call.caller, 'source': f'{call.source[0]}:{call.source[1]}', 'result': result, 'answered': call.answered_at is not None, 'codec': call.codec, 'duration_s': round(time.time() - call.answered_at) if call.answered_at else 0}
        if call.rtp is not None:
            stats = call.rtp.stats()
            info.update({k: stats[k] for k in ('rx_packets', 'tx_packets', 'rx_bytes', 'tx_bytes', 'sdp_remote', 'rtp_from')})
            info['from_card_packets'] = self.hub.back_packets if self.hub is not None else 0
            info['sdp_private'] = sip.is_private(call.media.addr or call.source[0]) and (not sip.is_private(call.source[0]))
            call.rtp.close()
        if call.acked is not None and (not call.acked.done()):
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
        if result == 'answered_elsewhere':
            self.state = ELSEWHERE
            self._elsewhere_timer = asyncio.get_running_loop().call_later(t_cfg()['answered_elsewhere_s'], self._idle)
        else:
            self.state = IDLE
        self.mgr.fire(self, self.state, None, result)
        self.changed()

    def _idle(self) -> None:
        if self.state == ELSEWHERE:
            self.state = IDLE
            self.changed()

    def _log_call(self, info: dict[str, Any]) -> None:
        self.last_call = {'at': time.time(), **info}

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
