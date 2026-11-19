from __future__ import annotations
import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import functools
import logging
from typing import Any
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntry, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.loader import IntegrationNotLoaded, async_get_loaded_integration
from homeassistant.util import dt as dt_util
from . import issues
from .const import DOMAIN
from .storage import HaufloStore
from .tuning import tuning
_LOGGER = logging.getLogger(__name__)
FEATURE = 'integration_watchdog'
STORE_KEY = f'{DOMAIN}.watchdog'
ISSUE_KIND = 'integration_down'
t_cfg = functools.cache(lambda: tuning()['watchdog'])
tick = functools.cache(lambda: timedelta(seconds=t_cfg()['tick_s']))
start_grace_s = functools.cache(lambda: t_cfg()['start_grace_s'])
wait_s = functools.cache(lambda: {'error': t_cfg()['error_wait_s'], 'retry': t_cfg()['retry_wait_s'], 'unavailable': t_cfg()['unavailable_after_s']})
first_s = functools.cache(lambda: t_cfg()['first_s'])
factor = functools.cache(lambda: t_cfg()['factor'])
max_s = functools.cache(lambda: t_cfg()['max_s'])
attempts_max = functools.cache(lambda: t_cfg()['attempts_max'])
window_s = functools.cache(lambda: t_cfg()['window_h'] * 3600)
down_after = functools.cache(lambda: t_cfg()['down_after_attempts'])
settle_s = functools.cache(lambda: t_cfg()['settle_s'])
spacing_s = functools.cache(lambda: t_cfg()['spacing_s'])
reload_timeout_s = functools.cache(lambda: t_cfg()['reload_timeout_s'])
keep_recovered_s = functools.cache(lambda: t_cfg()['keep_recovered_h'] * 3600)
error_len = functools.cache(lambda: t_cfg()['error_len'])
save_delay_s = functools.cache(lambda: t_cfg()['save_delay_s'])
never = functools.cache(lambda: frozenset(t_cfg()['never_domains']) | {DOMAIN})
skip_types = functools.cache(lambda: frozenset(t_cfg()['skip_types']))
OK, RESTARTING, DOWN = ('ok', 'restarting', 'down')
STATES = (OK, RESTARTING, DOWN)

def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts is not None else None

def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and (not isinstance(v, bool)) else None

def backoff(attempt: int) -> float:
    """The pause before reload number `attempt` (1-based): first_s, ×factor, at most max_s."""
    return min(first_s() * factor() ** max(attempt - 1, 0), max_s())

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
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, tick()))
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
        if entry.state is ConfigEntryState.LOADED and self._types.get(entry.domain, '') is None:
            del self._types[entry.domain]
        if entry.entry_id in self.recs and entry.state is ConfigEntryState.LOADED:
            self.evaluate()

    def _skipped(self, entry: ConfigEntry, off: set[str]) -> bool:
        if entry.domain in never() or entry.domain in off or entry.disabled_by is not None:
            return True
        if entry.domain not in self._types:
            try:
                self._types[entry.domain] = async_get_loaded_integration(self.hass, entry.domain).integration_type
            except IntegrationNotLoaded:
                self._types[entry.domain] = None
                _LOGGER.debug('Hauflo: %s not loaded: judged as any other integration', entry.domain)
                return False
        return self._types[entry.domain] in skip_types()

    def fault(self, entry: ConfigEntry) -> tuple[str, float | None] | None:
        """(kind, since — when known) of a fallen entry; None: works (or not ours to judge now)."""
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
        if self.started_at is None or now - self.started_at < start_grace_s():
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
        if r['confirmed'] or now - r['since'] < wait_s()[kind]:
            return False
        r.update(confirmed=True, state=RESTARTING, recoveredAt=None, title=entry.title)
        _LOGGER.warning('Hauflo watchdog: %s (%s) fell (%s) — restarting it', entry.title, entry.domain, kind)
        return True

    def _drop_old(self, now: float) -> bool:
        old = [eid for eid, r in self.recs.items() if r['state'] == OK and r['since'] is None and (now - (r['recoveredAt'] or 0) > keep_recovered_s())]
        for eid in old:
            del self.recs[eid]
        return bool(old)

    def _forget(self, eid: str) -> None:
        self.recs.pop(eid, None)
        self.settle_until.pop(eid, None)
        issues.clear_issue(self.hass, f'{ISSUE_KIND}_{eid}')

    def _next(self, now: float) -> None:
        if self.busy is not None or (self.last_reload is not None and now - self.last_reload < spacing_s()):
            return
        changed = False
        for eid, r in sorted(self.recs.items(), key=lambda x: x[1]['since'] or 0):
            if not r['confirmed'] or now < self.settle_until.get(eid, 0):
                continue
            r['tries'] = [t for t in r['tries'] if now - t < window_s()]
            if r['attempts'] >= down_after() and r['state'] != DOWN:
                self._down(eid, r)
                changed = True
            if len(r['tries']) >= attempts_max():
                if r['state'] != DOWN:
                    self._down(eid, r)
                    changed = True
                continue
            if now - (r['lastAt'] or r['since'] + wait_s()[r['kind']]) < backoff(r['attempts'] + 1):
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
            async with asyncio.timeout(reload_timeout_s()):
                await self.hass.config_entries.async_reload(eid)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.warning("Hauflo: a reload that raised is one that didn't help", exc_info=True)
            if (r := self.recs.get(eid)) is not None:
                r['lastError'] = (str(err) or type(err).__name__)[:error_len()]
        finally:
            self.busy = None
            self.settle_until[eid] = self._now() + settle_s()
            self.store.async_delay_save(self._data, save_delay_s())
        self.evaluate()

    @callback
    def _changed(self) -> None:
        self.store.async_delay_save(self._data, save_delay_s())
        if self.on_change is not None:
            self.on_change()

    def items(self) -> dict[str, dict[str, Any]]:
        """The status block (`integrations`): the entries the watchdog has a word about — fallen ones (restarting / down) and the ones back within keep_recovered_h; a short blip never confirmed isn't there."""
        return {f"{r['domain']}/{eid}": {'domain': r['domain'], 'title': r['title'], 'state': r['state'], 'since': _iso(r['since']), 'attempts': r['attempts'], 'lastError': r['lastError'], 'recoveredAt': _iso(r['recoveredAt'])} for eid, r in self.recs.items() if r['confirmed'] or r['recoveredAt'] is not None}

def _new(entry: ConfigEntry) -> dict[str, Any]:
    return {**_new_blank(), 'domain': entry.domain, 'title': entry.title}

def _error(entry: ConfigEntry, kind: str) -> str | None:
    if kind == 'unavailable':
        return STATE_UNAVAILABLE
    return str(entry.reason)[:error_len()] if entry.reason else None

def _rec_in(v: Any) -> dict[str, Any] | None:
    if not isinstance(v, dict) or not isinstance(v.get('domain'), str) or v.get('state') not in STATES:
        return None
    if v.get('kind') is not None and v['kind'] not in wait_s():
        return None
    r = _new_blank()
    r.update(domain=v['domain'], title=str(v.get('title') or v['domain']), state=v['state'], kind=v.get('kind'), since=_num(v.get('since')), confirmed=v.get('confirmed') is True, attempts=int(_num(v.get('attempts')) or 0), tries=[float(t) for t in v.get('tries') or [] if _num(t) is not None] if isinstance(v.get('tries'), list) else [], lastAt=_num(v.get('lastAt')), lastError=str(v['lastError'])[:error_len()] if v.get('lastError') else None, recoveredAt=_num(v.get('recoveredAt')))
    if r['confirmed'] and (r['since'] is None or r['kind'] is None):
        return None
    return r

def _new_blank() -> dict[str, Any]:
    return {'domain': '', 'title': '', 'state': OK, 'kind': None, 'since': None, 'confirmed': False, 'attempts': 0, 'tries': [], 'lastAt': None, 'lastError': None, 'recoveredAt': None}
