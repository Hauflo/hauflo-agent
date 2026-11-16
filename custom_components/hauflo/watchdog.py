from __future__ import annotations
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntry, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.loader import async_get_loaded_integration
from homeassistant.util import dt as dt_util
from . import issues
from .const import DOMAIN
from .storage import HaufloStore
from .tuning import TUNING
_LOGGER = logging.getLogger(__name__)
FEATURE = 'integration_watchdog'
STORE_KEY = f'{DOMAIN}.watchdog'
ISSUE_KIND = 'integration_down'
T = TUNING['watchdog']
TICK = timedelta(seconds=T['tick_s'])
START_GRACE_S = T['start_grace_s']
WAIT_S = {'error': T['error_wait_s'], 'retry': T['retry_wait_s'], 'unavailable': T['unavailable_after_s']}
FIRST_S, FACTOR, MAX_S = (T['first_s'], T['factor'], T['max_s'])
ATTEMPTS_MAX = T['attempts_max']
WINDOW_S = T['window_h'] * 3600
DOWN_AFTER = T['down_after_attempts']
SETTLE_S = T['settle_s']
SPACING_S = T['spacing_s']
RELOAD_TIMEOUT_S = T['reload_timeout_s']
KEEP_RECOVERED_S = T['keep_recovered_h'] * 3600
ERROR_LEN = T['error_len']
SAVE_DELAY_S = T['save_delay_s']
NEVER = frozenset(T['never_domains']) | {DOMAIN}
SKIP_TYPES = frozenset(T['skip_types'])
OK, RESTARTING, DOWN = ('ok', 'restarting', 'down')
STATES = (OK, RESTARTING, DOWN)

def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts is not None else None

def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and (not isinstance(v, bool)) else None

def backoff(attempt: int) -> float:
    return min(FIRST_S * FACTOR ** max(attempt - 1, 0), MAX_S)

class Watchdog:

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = HaufloStore(hass, STORE_KEY)
        self.recs: dict[str, dict[str, Any]] = {}
        self.on_change: Callable[[], None] | None = None
        self.off: Callable[[], set[str]] = set
        self.started_at: float | None = None
        self.busy: str | None = None
        self.last_reload: float | None = None
        self.settle_until: dict[str, float] = {}
        self._types: dict[str, str | None] = {}
        self._unsubs: list[Callable[[], None]] = []
        self._task: asyncio.Task | None = None

    @staticmethod
    def _now() -> float:
        return dt_util.utcnow().timestamp()

    async def async_start(self) -> None:
        data = await self.store.async_load()
        if isinstance(data, dict) and isinstance(data.get('recs'), dict):
            self.recs = {k: r for k, v in data['recs'].items() if isinstance(k, str) and (r := _rec_in(v)) is not None}

        @callback
        def started(_hass: HomeAssistant) -> None:
            self.started_at = self._now()
        self._unsubs.append(async_at_started(self.hass, started))
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, TICK))
        self._unsubs.append(async_dispatcher_connect(self.hass, SIGNAL_CONFIG_ENTRY_CHANGED, self._entry_changed))

    @callback
    def async_stop(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        if self._task is not None and (not self._task.done()):
            self._task.cancel()

    async def async_flush(self) -> None:
        await self.store.async_save(self._data())

    def _data(self) -> dict[str, Any]:
        return {'recs': self.recs}

    @callback
    def _tick(self, _now: Any=None) -> None:
        self.evaluate()

    @callback
    def _entry_changed(self, _change: Any, entry: ConfigEntry) -> None:
        if entry.entry_id in self.recs and entry.state is ConfigEntryState.LOADED:
            self.evaluate()

    def _skipped(self, entry: ConfigEntry, off: set[str]) -> bool:
        if entry.domain in NEVER or entry.domain in off or entry.disabled_by is not None:
            return True
        if entry.domain not in self._types:
            try:
                self._types[entry.domain] = async_get_loaded_integration(self.hass, entry.domain).integration_type
            except Exception:
                return False
        return self._types[entry.domain] in SKIP_TYPES

    def fault(self, entry: ConfigEntry) -> tuple[str, float | None] | None:
        if entry.state is ConfigEntryState.SETUP_ERROR:
            return ('error', None)
        if entry.state is ConfigEntryState.SETUP_RETRY:
            return ('retry', None)
        if entry.state is not ConfigEntryState.LOADED:
            return None
        states = [s for e in er.async_entries_for_config_entry(er.async_get(self.hass), entry.entry_id) if not e.disabled and (s := self.hass.states.get(e.entity_id)) is not None]
        if not states or any((s.state != STATE_UNAVAILABLE for s in states)):
            return None
        return ('unavailable', max((s.last_changed.timestamp() for s in states)))

    @callback
    def evaluate(self) -> None:
        now = self._now()
        if self.started_at is None or now - self.started_at < START_GRACE_S:
            return
        changed = False
        off = self.off()
        entries = {e.entry_id: e for e in self.hass.config_entries.async_entries()}
        for eid in [eid for eid, r in self.recs.items() if eid not in entries or self._skipped(entries[eid], off)]:
            self._forget(eid)
            changed = True
        for eid, entry in entries.items():
            if eid == self.busy or self._skipped(entry, off):
                continue
            if entry.state in (ConfigEntryState.SETUP_IN_PROGRESS, ConfigEntryState.UNLOAD_IN_PROGRESS):
                continue
            changed |= self._judge(entry, now)
        changed |= self._drop_old(now)
        if changed:
            self._changed()
        self._next(now)

    def _judge(self, entry: ConfigEntry, now: float) -> bool:
        eid = entry.entry_id
        f = self.fault(entry)
        r = self.recs.get(eid)
        if f is None:
            if r is None or (r['state'] == OK and r['since'] is None):
                return False
            if not r['confirmed']:
                del self.recs[eid]
                return False
            r.update(state=OK, since=None, confirmed=False, attempts=0, kind=None, recoveredAt=now, title=entry.title)
            issues.clear_issue(self.hass, f'{ISSUE_KIND}_{eid}')
            _LOGGER.info('Hauflo watchdog: %s (%s) works again', entry.title, entry.domain)
            return True
        kind, since = f
        if r is None:
            r = self.recs[eid] = _new(entry)
        if r['since'] is None:
            r.update(since=since if since is not None else now, kind=kind)
        r['lastError'] = _error(entry, kind) or r['lastError']
        if r['confirmed'] or now - r['since'] < WAIT_S[kind]:
            return False
        r.update(confirmed=True, state=RESTARTING, recoveredAt=None, title=entry.title)
        _LOGGER.warning('Hauflo watchdog: %s (%s) fell (%s) — restarting it', entry.title, entry.domain, kind)
        return True

    def _drop_old(self, now: float) -> bool:
        old = [eid for eid, r in self.recs.items() if r['state'] == OK and r['since'] is None and (now - (r['recoveredAt'] or 0) > KEEP_RECOVERED_S)]
        for eid in old:
            del self.recs[eid]
        return bool(old)

    def _forget(self, eid: str) -> None:
        self.recs.pop(eid, None)
        self.settle_until.pop(eid, None)
        issues.clear_issue(self.hass, f'{ISSUE_KIND}_{eid}')

    def _next(self, now: float) -> None:
        if self.busy is not None or (self.last_reload is not None and now - self.last_reload < SPACING_S):
            return
        changed = False
        for eid, r in sorted(self.recs.items(), key=lambda x: x[1]['since'] or 0):
            if not r['confirmed'] or now < self.settle_until.get(eid, 0):
                continue
            r['tries'] = [t for t in r['tries'] if now - t < WINDOW_S]
            if r['attempts'] >= DOWN_AFTER and r['state'] != DOWN:
                self._down(eid, r)
                changed = True
            if len(r['tries']) >= ATTEMPTS_MAX:
                if r['state'] != DOWN:
                    self._down(eid, r)
                    changed = True
                continue
            if now - (r['lastAt'] or r['since'] + WAIT_S[r['kind']]) < backoff(r['attempts'] + 1):
                continue
            r['attempts'] += 1
            r['tries'].append(now)
            r['lastAt'] = now
            self.busy, self.last_reload = (eid, now)
            self._task = self.hass.async_create_background_task(self._reload(eid), f'{DOMAIN} watchdog reload')
            changed = True
            break
        if changed:
            self._changed()

    def _down(self, eid: str, r: dict[str, Any]) -> None:
        r['state'] = DOWN
        issues.raise_rule_issue(self.hass, ISSUE_KIND, eid, name=r['title'], domain=r['domain'], attempts=r['attempts'])
        _LOGGER.warning("Hauflo watchdog: %s (%s) didn't come back after %s restarts", r['title'], r['domain'], r['attempts'])

    async def _reload(self, eid: str) -> None:
        try:
            async with asyncio.timeout(RELOAD_TIMEOUT_S):
                await self.hass.config_entries.async_reload(eid)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            if (r := self.recs.get(eid)) is not None:
                r['lastError'] = (str(err) or type(err).__name__)[:ERROR_LEN]
        finally:
            self.busy = None
            self.settle_until[eid] = self._now() + SETTLE_S
            self.store.async_delay_save(self._data, SAVE_DELAY_S)
        self.evaluate()

    @callback
    def _changed(self) -> None:
        self.store.async_delay_save(self._data, SAVE_DELAY_S)
        if self.on_change is not None:
            self.on_change()

    def items(self) -> dict[str, dict[str, Any]]:
        return {f"{r['domain']}/{eid}": {'domain': r['domain'], 'title': r['title'], 'state': r['state'], 'since': _iso(r['since']), 'attempts': r['attempts'], 'lastError': r['lastError'], 'recoveredAt': _iso(r['recoveredAt'])} for eid, r in self.recs.items() if r['confirmed'] or r['recoveredAt'] is not None}

def _new(entry: ConfigEntry) -> dict[str, Any]:
    return {**_new_blank(), 'domain': entry.domain, 'title': entry.title}

def _error(entry: ConfigEntry, kind: str) -> str | None:
    if kind == 'unavailable':
        return STATE_UNAVAILABLE
    return str(entry.reason)[:ERROR_LEN] if entry.reason else None

def _rec_in(v: Any) -> dict[str, Any] | None:
    if not isinstance(v, dict) or not isinstance(v.get('domain'), str) or v.get('state') not in STATES:
        return None
    if v.get('kind') is not None and v['kind'] not in WAIT_S:
        return None
    r = _new_blank()
    r.update(domain=v['domain'], title=str(v.get('title') or v['domain']), state=v['state'], kind=v.get('kind'), since=_num(v.get('since')), confirmed=v.get('confirmed') is True, attempts=int(_num(v.get('attempts')) or 0), tries=[float(t) for t in v.get('tries') or [] if _num(t) is not None] if isinstance(v.get('tries'), list) else [], lastAt=_num(v.get('lastAt')), lastError=str(v['lastError'])[:ERROR_LEN] if v.get('lastError') else None, recoveredAt=_num(v.get('recoveredAt')))
    if r['confirmed'] and (r['since'] is None or r['kind'] is None):
        return None
    return r

def _new_blank() -> dict[str, Any]:
    return {'domain': '', 'title': '', 'state': OK, 'kind': None, 'since': None, 'confirmed': False, 'attempts': 0, 'tries': [], 'lastAt': None, 'lastError': None, 'recoveredAt': None}
