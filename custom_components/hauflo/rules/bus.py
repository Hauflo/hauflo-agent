"""KNX bus reads a rule may listen to."""
from __future__ import annotations
from functools import partial
import logging
from typing import Any
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_call_later
from .. import derived as dv
from ..derived import SECONDS_PER_MINUTE, Invalid, num
from .answer import fmt
from .const import GA_RE, KNX_WRITE
from .spec import C
_LOGGER = logging.getLogger(__name__)

def ga_raw(v: Any) -> int | None:
    """A KNX group address as its 16-bit number in any notation the canon's `ga_bits` knows (3-level «1/2/3», 2-level
    «1/234», free «2058»), None — not one. The bit layout is the KNX standard's, kept with the canon (no numbers
    here)."""
    if isinstance(v, bool) or not isinstance(v, (str, int)):
        return None
    t = str(v).strip()
    if not GA_RE.match(t):
        return None
    parts = [int(x) for x in t.split('/')]
    bits = C('bus_reads', 'ga_bits').get(str(len(parts)))
    if bits is None or any((p >= 1 << b for p, b in zip(parts, bits, strict=True))):
        return None
    out = 0
    for p, b in zip(parts, bits, strict=True):
        out = out << b | p
    return out

def _gas(v: Any, cap: int) -> list[str]:
    if not isinstance(v, list) or not v or len(v) > cap:
        raise Invalid('bad_bus_read', 'rules_bus_read_gas', n=cap)
    out: list[str] = []
    for g in v:
        if ga_raw(g) is None:
            raise Invalid('bad_bus_read', 'rules_bus_read_ga', ga=str(g)[:C('limits', 'id_len')])
        if (t := str(g).strip()) not in out:
            out.append(t)
    return out

def clean_bus_read(raw: Any) -> dict[str, Any]:
    """One «читать после команды» rule (Р65): a write to any of `triggers` → knx.read of `reads` after `delay_s`."""
    br = C('bus_reads')
    if not isinstance(raw, dict):
        raise Invalid('bad', 'rules_bad_object')
    rid = raw.get('id')
    if not isinstance(rid, str) or not dv.id_re().match(rid):
        raise Invalid('bad_id', 'rules_bad_id', n=C('limits', 'id_len'))
    lo, hi = br['delay_bounds_s']
    delay = raw.get('delay_s', br['delay_s'])
    if isinstance(delay, bool) or num(delay) is None or (not lo <= float(delay) <= hi):
        raise Invalid('bad_bus_read', 'rules_bus_read_delay', lo=fmt(lo, None), hi=fmt(hi, None))
    out = {'id': rid, 'triggers': _gas(raw.get('triggers'), int(br['triggers'])), 'reads': _gas(raw.get('reads'), int(br['reads'])), 'delay_s': float(delay), 'enabled': raw.get('enabled', True) is not False}
    if (name := raw.get('name')) not in (None, ''):
        if not isinstance(name, str) or len(name) > br['name_len'] or (not name.isprintable()):
            raise Invalid('bad_name', 'rules_bad_name', n=int(br['name_len']))
        out['name'] = name.strip()
    return out

def clean_bus_reads(raw: Any) -> list[dict[str, Any]]:
    """The house's whole list (the deploy sends it whole; ids unique)."""
    cap = int(C('bus_reads', 'rules'))
    if not isinstance(raw, list):
        raise Invalid('bad', 'rules_bad_object')
    if len(raw) > cap:
        raise Invalid('limit', 'rules_limit_bus_reads', n=cap)
    out = [clean_bus_read(r) for r in raw]
    if len({r['id'] for r in out}) != len(out):
        raise Invalid('bad_id', 'rules_bad_id', n=C('limits', 'id_len'))
    return out

class RulesBusMixin:
    """Listens to KNX bus reads."""

    def _br_set(self, rules: list[dict[str, Any]]) -> None:
        self.bus_reads = rules
        self._br_index = {}
        for r in rules:
            if r['enabled']:
                for g in r['triggers']:
                    self._br_index.setdefault(ga_raw(g) or 0, []).append(r['id'])
        for rid in [k for k in self._br_timers if k not in {r['id'] for r in rules}]:
            self._br_timers.pop(rid)[0]()

    @callback
    def _bus_listen(self) -> None:
        try:
            from homeassistant.components.knx.const import SIGNAL_KNX_TELEGRAM
        except ImportError as err:
            _LOGGER.debug('hauflo: no KNX telegram signal (%s)', err)
            return
        self._unsub_br = async_dispatcher_connect(self.hass, SIGNAL_KNX_TELEGRAM, self._bus_heard)

    @callback
    def _bus_heard(self, _telegram: Any, telegram_dict: Any=None) -> None:
        t = telegram_dict or {}
        if str(t.get('telegramtype', '')) != KNX_WRITE or (raw := ga_raw(t.get('destination'))) is None:
            return
        rids = self._br_index.get(raw)
        if not rids:
            return
        now = dv.now()
        hi = C('bus_reads', 'delay_bounds_s')[1]
        for rid in rids:
            rule = next((r for r in self.bus_reads if r['id'] == rid), None)
            if rule is None:
                continue
            first = now
            if (old := self._br_timers.pop(rid, None)) is not None:
                old[0]()
                first = old[1]
            delay = max(0.0, min(rule['delay_s'], first + hi - now))
            trigger = str(t.get('destination'))
            self._br_timers[rid] = (async_call_later(self.hass, delay, partial(self._bus_fire, rid, trigger)), first)

    async def _bus_fire(self, rid: str, trigger: str, _now: Any=None) -> None:
        self._br_timers.pop(rid, None)
        rule = next((r for r in self.bus_reads if r['id'] == rid), None)
        if rule is None or not rule['enabled']:
            return
        now = dv.now()
        while self._br_sent and now - self._br_sent[0][0] >= SECONDS_PER_MINUTE:
            self._br_sent.popleft()
        n = len(rule['reads'])
        item: dict[str, Any] = {'at': now, 'rule': rid, 'trigger': trigger, 'reads': rule['reads']}
        if sum((k for _, k in self._br_sent)) + n > C('bus_reads', 'reads_per_min'):
            self._br_log.append({**item, 'result': 'capped'})
            _LOGGER.warning('hauflo: bus read %s after %s skipped — more than %s reads a minute', rid, trigger, C('bus_reads', 'reads_per_min'))
            return
        self._br_sent.append((now, n))
        try:
            await self.hass.services.async_call('knx', 'read', {'address': rule['reads']}, blocking=True)
        except Exception as err:
            self._br_log.append({**item, 'result': 'error', 'error': str(err)[:int(C('engine', 'error_len'))]})
            _LOGGER.warning('hauflo: bus read %s after %s failed: %s', rid, trigger, err)
            return
        self._br_log.append({**item, 'result': 'read'})
        _LOGGER.info('hauflo: bus read %s after a write to %s: knx.read %s', rid, trigger, ', '.join(rule['reads']))

    async def set_bus_reads(self, raw: Any) -> dict[str, Any]:
        self._br_set(clean_bus_reads(raw))
        await self._save()
        return {'bus_reads': self.bus_reads_out()}

    def bus_reads_out(self) -> dict[str, Any]:
        return {'rules': self.bus_reads, 'log': list(self._br_log)[-int(C('bus_reads', 'log_out')):], 'listening': self._unsub_br is not None}
