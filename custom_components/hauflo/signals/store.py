"""The kept signal policy and the helpers that read it."""
from __future__ import annotations
from collections.abc import Callable
import logging
from typing import Any
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from ..const import DOMAIN
from ..guard import contained
from ..storage import HaufloStore
from .canon import DATA, MEANING_DEFAULT, MEANING_KEYS, POLICY_KEYS, STORE_KEY, entity_id_cfg, evaluate, hash, local_max, meanings, stamp_len, when
from .policy import iso, meaning_of, meaning_policy, newer, signals_in
_LOGGER = logging.getLogger(__name__)

def combo_entity(cid: str) -> str:
    """The sensor of a combination (combos.ComboActive; backend signalCombos.ts comboEntityId)."""
    return f'binary_sensor.{DOMAIN}_combo_{cid}'

def combo_entities(hass: HomeAssistant | None) -> set[str]:
    """The sensors of the site's combinations (its policy's `combos`)."""
    s = store(hass)
    return {combo_entity(cid) for cid in s.combos()} if s is not None else set()

class Signals:
    """The site's policy as Hauflo last sent it, with the hash Hauflo computed (the agent never recomputes it: the two languages serialize numbers differently — the cloud compares its own hash with the one kept here)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = HaufloStore(hass, STORE_KEY)
        self.cloud: dict[str, Any] = {'scopes': [], 'entities': {}, 'findings': {}}
        self.policy: dict[str, Any] = self.cloud
        self.hash: str | None = None
        self.at: str | None = None
        self.listeners: list[Callable[[], None]] = []
        self.local: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        data = await self.store.async_load()
        got = signals_in({k: v for k, v in data.items() if k in ('scopes', 'entities', 'findings', 'diag', 'watchdog', 'observe', 'combos')}) if isinstance(data, dict) else None
        if got is not None:
            self.cloud = got[0]
            h = data.get('hash')
            self.hash = h if isinstance(h, str) and hash().match(h) else None
            self.at = data.get('at') if isinstance(data.get('at'), str) else None
        if isinstance(data, dict) and isinstance(data.get('local'), dict):
            self.local = {e: c for e, c in data['local'].items() if isinstance(e, str) and entity_id_cfg().match(e) and (local_in(c) is not None)}
            self._trim()
        self._merge()

    def _merge(self) -> None:
        own = dict(self.cloud.get('entities') or {})
        for eid, c in self.local.items():
            was = own.get(eid) or {}
            if not newer(c['at'], was.get('at')):
                continue
            rest = {k: v for k, v in was.items() if k not in MEANING_KEYS and k != 'at'}
            p = {**rest, **meaning_policy(c['meaning'])}
            if any((k in p for k in POLICY_KEYS)):
                own[eid] = {**p, 'at': c['at']}
            else:
                own.pop(eid, None)
        self.policy = {**self.cloud, 'entities': own}

    def _trim(self) -> None:
        if len(self.local) > local_max():
            self.local = dict(sorted(self.local.items(), key=lambda kv: when(kv[1]['at']) or dt_util.utc_from_timestamp(0), reverse=True)[:local_max()])

    def _data(self) -> dict[str, Any]:
        return {**self.cloud, 'hash': self.hash, 'at': self.at, 'local': self.local}

    def _told(self) -> None:
        for fn in list(self.listeners):
            with contained(_LOGGER, 'Hauflo signals: a listener failed', level=logging.ERROR):
                fn()

    @callback
    def choose(self, eid: str, meaning: str, by: str) -> None:
        """«Авария когда» chosen on a card's row in HA by an admin: applied at once (the cards, the push engine, «Всё хорошо»), kept, and carried to Hauflo by the status (`choices`) — the newer of the two choices wins."""
        was = self.local.pop(eid, None)
        if meaning == MEANING_DEFAULT and was is None and (eid not in (self.cloud.get('entities') or {})):
            self._merge()
            self.store.async_delay_save(self._data, 0)
            self._told()
            return
        self.local[eid] = {'meaning': meaning, 'by': str(by or '')[:stamp_len()], 'at': dt_util.utcnow().isoformat()}
        self._trim()
        self._merge()
        self.store.async_delay_save(self._data, 0)
        self._told()

    def choices(self) -> dict[str, dict[str, Any]]:
        """HA's choices the site's policy doesn't hold yet (newer than its own for the signal), newest first — Hauflo folds them in with the same `at`; its push back then equals them and they stop going."""
        own = self.cloud.get('entities') or {}
        out: dict[str, dict[str, Any]] = {}
        for eid, c in sorted(self.local.items(), key=lambda kv: when(kv[1]['at']) or dt_util.utc_from_timestamp(0), reverse=True):
            if newer(c['at'], (own.get(eid) or {}).get('at')):
                out[eid] = dict(c)
        return out

    def own_of(self, eid: str) -> dict[str, Any]:
        """Who chose the signal's own policy now: {meaning, ha: chosen in HA (not held by the site yet), by}."""
        p = (self.policy.get('entities') or {}).get(eid)
        c = self.local.get(eid)
        ha = c is not None and c['meaning'] != MEANING_DEFAULT and newer(c['at'], ((self.cloud.get('entities') or {}).get(eid) or {}).get('at'))
        return {'meaning': meaning_of(p), 'ha': ha, **({'by': c['by']} if ha and c else {})}

    @callback
    def apply(self, policy: dict[str, Any], hash_: str) -> None:
        if 'diag' not in policy and 'diag' in self.cloud:
            policy = {**policy, 'diag': self.cloud['diag']}
        self.cloud, self.hash, self.at = (policy, hash_, dt_util.utcnow().isoformat())
        own = policy.get('entities') or {}
        self.local = {e: c for e, c in self.local.items() if newer(c['at'], (own.get(e) or {}).get('at')) and (not (c['meaning'] == MEANING_DEFAULT and e not in own))}
        self._merge()
        self.store.async_delay_save(self._data, 0)
        self._told()

    @callback
    def listen(self, fn: Callable[[], None]) -> Callable[[], None]:
        self.listeners.append(fn)

        def off() -> None:
            if fn in self.listeners:
                self.listeners.remove(fn)
        return off

    def counts(self) -> dict[str, int]:
        return {'scopes': len(self.cloud['scopes']), 'entities': len(self.cloud['entities']), 'findings': len(self.cloud['findings'])}

    def finding(self, key: str) -> dict[str, Any] | None:
        return self.policy['findings'].get(key)

    def watch_on(self) -> set[str]:
        return {e for e, p in self.policy['entities'].items() if p.get('watch') is True}

    def combos(self) -> dict[str, dict[str, Any]]:
        return self.cloud.get('combos') or {}

    def watch_off(self) -> set[str]:
        return {e for e, p in self.policy['entities'].items() if p.get('watch') is False}

def local_in(v: Any) -> dict[str, Any] | None:
    """One HA choice as kept: {meaning: a canon one | default, by, at (ISO)}."""
    if not isinstance(v, dict) or (v.get('meaning') not in meanings() and v.get('meaning') != MEANING_DEFAULT) or (not iso(v.get('at'))):
        return None
    return {'meaning': v['meaning'], 'by': str(v.get('by') or '')[:stamp_len()], 'at': v['at']}

def watchdog_off(hass: HomeAssistant | None) -> set[str]:
    s = store(hass)
    return set((s.cloud.get('watchdog') or {}).get('off') or ()) if s is not None else set()

def observing(hass: HomeAssistant | None) -> bool:
    s = store(hass)
    return not (s is not None and (s.cloud.get('observe') or {}).get('on') is False)

def store(hass: HomeAssistant | None) -> Signals | None:
    return hass.data.get(DATA) if hass is not None else None

async def async_setup(hass: HomeAssistant) -> Signals:
    if (s := store(hass)) is not None:
        return s
    s = Signals(hass)
    await s.async_load()
    hass.data[DATA] = s
    return s

def policy(hass: HomeAssistant | None) -> dict[str, Any] | None:
    s = store(hass)
    return s.policy if s is not None else None

def current_hash(hass: HomeAssistant | None) -> str | None:
    s = store(hass)
    return s.hash if s is not None else None

def platform_of(hass: HomeAssistant, eid: str) -> str | None:
    ent = er.async_get(hass).async_get(eid)
    return ent.platform if ent is not None else None

def evaluate_in(hass: HomeAssistant, st: State, platform: str | None=None) -> dict[str, Any]:
    """`evaluate` with this house's policy and the entity's integration from HA's registry."""
    return evaluate(st, platform=platform if platform is not None else platform_of(hass, st.entity_id), policy=policy(hass))
