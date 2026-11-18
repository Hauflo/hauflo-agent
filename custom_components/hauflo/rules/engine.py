"""The rules engine: store, start and stop."""
from __future__ import annotations
from collections import deque
from collections.abc import Callable
import contextlib
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from .. import derived as dv, quiet
from ..const import async_entity_names, site_ru
from ..derived import Invalid
from ..storage import HaufloStore
from .answer import blank
from .api import RulesApiMixin
from .bus import RulesBusMixin, clean_bus_reads
from .checks import Env
from .const import AT, IS, STORE_KEY
from .entities import RulesEntitiesMixin, derived_entity_id
from .evaluate import RulesEvaluateMixin
from .hold import RulesHoldMixin
from .preview import RulesPreviewMixin
from .run import RulesRunMixin
from .schedule import RulesScheduleMixin
from .spec import C
from .suggest import RulesSuggestMixin
from .text import RulesTextMixin, Texts
if TYPE_CHECKING:
    from .entities import DerivedSensor, RuleActive, RuleSwitch
_LOGGER = logging.getLogger(__name__)

class Rules(RulesScheduleMixin, RulesBusMixin, RulesEvaluateMixin, RulesSuggestMixin, RulesRunMixin, RulesHoldMixin, RulesTextMixin, RulesApiMixin, RulesPreviewMixin, RulesEntitiesMixin, Texts):
    """Every value rule and derived value of the house (one per loaded entry)."""

    def __init__(self, hass: HomeAssistant, owns: Callable[[str], bool], own_raw: Callable[[str], bool], season: Callable[[], str] | None=None, tell: Callable[..., Any] | None=None, *, season_now: Callable[[], str | None] | None=None, season_set: Callable[[str, str | None], Any] | None=None, ask: Callable[..., Any] | None=None, notify_of: Callable[[], Any] | None=None) -> None:
        self.hass = hass
        self.notify_of = notify_of or (lambda: None)
        self.owns, self.own_raw = (owns, own_raw)
        self.season = season or (lambda: 'winter')
        self.tell = tell
        self.season_now = season_now or (lambda: None)
        self.season_set = season_set
        self.ask = ask
        self.ask_text: dict[str, Callable[[dict[str, Any], bool, bool], tuple[str, str, str]]] = {}
        self.cloud_reads: Callable[[str, str | None], bool] = lambda eid, attr: False
        self.win: dict[str, dict[str, Any]] = {}
        self._win_timers: dict[str, CALLBACK_TYPE] = {}
        self._sug_sent: dict[str, list[float]] = {}
        self.store = HaufloStore(hass, STORE_KEY)
        self.rules: dict[str, dict[str, Any]] = {}
        self.quiet = quiet.default()
        self.derived_cfg: dict[str, dict[str, Any]] = {}
        self.derived: dict[str, dv.Derived] = {}
        self.state: dict[str, dict[str, Any]] = {}
        self.writes: dict[str, list[float]] = {}
        self.climate_at: dict[str, float] = {}
        self.switched: dict[str, float] = {}
        self.todo: list[dict[str, Any]] = []
        self.pulses: dict[str, dict[str, Any]] = {}
        self._pulse_timers: dict[str, CALLBACK_TYPE] = {}
        self._repeat_timers: dict[str, list[CALLBACK_TYPE]] = {}
        Texts.__init__(self, hass)
        self.names: dict[str, dict[str, str]] = {}
        self.entry_id: str | None = None
        self.add_binary: Callable[[list[Any]], None] | None = None
        self.add_switches: Callable[[list[Any]], None] | None = None
        self.add_sensors: Callable[[list[Any]], None] | None = None
        self.binaries: dict[str, RuleActive] = {}
        self.switches: dict[str, RuleSwitch] = {}
        self.sensors: dict[str, DerivedSensor] = {}
        self._ctx: deque[str] = deque(maxlen=int(C('engine', 'contexts')))
        self._sent: dict[str, tuple[float, Any]] = {}
        self._bus: deque[float] = deque()
        self._warned: dict[str, float] = {}
        self._timers: dict[str, CALLBACK_TYPE] = {}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._unsub_tick: CALLBACK_TYPE | None = None
        self._unsub_action: CALLBACK_TYPE | None = None
        self.bus_reads: list[dict[str, Any]] = []
        self._br_index: dict[int, list[str]] = {}
        self._br_timers: dict[str, tuple[CALLBACK_TYPE, float]] = {}
        self._br_log: deque[dict[str, Any]] = deque(maxlen=int(C('bus_reads', 'log_size')))
        self._br_sent: deque[tuple[float, int]] = deque()
        self._unsub_br: CALLBACK_TYPE | None = None

    @property
    def ru(self) -> bool:
        """The house's one language (const.site_ru): the project's, else HA's — as the alarms' pushes say it."""
        return site_ru(self.hass.config.language, getattr(self.notify_of(), 'config', None))

    async def async_texts(self) -> None:
        await super().async_texts()
        self.names = await async_entity_names(self.hass)

    async def async_load(self) -> None:
        data = await self.store.async_load() or {}
        for raw in data.get('derived') or []:
            try:
                d = dv.clean(raw)
            except Invalid:
                continue
            self.derived_cfg[d['id']] = d
        for raw in data.get('rules') or []:
            if isinstance(raw, dict) and isinstance(raw.get('id'), str) and (isinstance(raw.get('input'), dict) or (raw.get('when') == AT and isinstance(raw.get('at'), dict))):
                self.rules[raw['id']] = raw
        for rid, st in (data.get('state') or {}).items():
            if rid in self.rules and isinstance(st, dict):
                self.state[rid] = {**blank(), **st}
        if isinstance(data.get('quiet'), dict):
            with contextlib.suppress(Invalid):
                self.quiet = quiet.clean(data['quiet'], quiet.default())
        self.writes = {k: [float(t) for t in v] for k, v in (data.get('writes') or {}).items() if isinstance(v, list)}
        self.climate_at = {k: float(v) for k, v in (data.get('climate_at') or {}).items()}
        self.switched = {k: float(v) for k, v in (data.get('switched') or {}).items() if isinstance(v, (int, float))}
        self.todo = [t for t in data.get('todo') or [] if isinstance(t, dict) and t.get('rule') in self.rules]
        self.pulses = {k: v for k, v in (data.get('pulses') or {}).items() if isinstance(v, dict) and isinstance(v.get('until'), (int, float))}
        try:
            self._br_set(clean_bus_reads(data.get('bus_reads') or []))
        except Invalid:
            _LOGGER.warning('hauflo: stored bus reads dropped (out of the canon)')
        self._make_derived()

    def data(self) -> dict[str, Any]:
        return {'rules': list(self.rules.values()), 'derived': list(self.derived_cfg.values()), 'state': self.state, 'quiet': self.quiet, 'writes': self.writes, 'climate_at': self.climate_at, 'switched': self.switched, 'todo': self.todo, 'bus_reads': self.bus_reads, 'pulses': self.pulses}

    def _save_soon(self) -> None:
        self.store.async_delay_save(self.data, C('engine', 'save_delay_s'))

    async def _save(self) -> None:
        await self.store.async_save(self.data())

    def _env(self, client: bool) -> Env:
        return Env(self.hass, self.owns, self.own_raw, client)

    def reads(self, rule: dict[str, Any]) -> set[tuple[str, str | None]]:
        """(entity, attribute) a rule's decision reads, derived values unfolded to their sources."""
        out = {(rule['input']['entity_id'], rule['input'].get('attribute'))} if 'input' in rule else set()
        for c in rule['only_if']:
            if 'entity_id' in c:
                out.add((c['entity_id'], c.get('attribute') if IS in c else c.get('attribute') or ('hvac_action' if c['type'] == 'heating' else dv.default_attribute(c['entity_id']))))
        if rule.get('interlock'):
            out.add((rule['interlock'], None))
        for eid, _ in list(out):
            for src in self.derived_sources(eid):
                e, a = dv.split_source(src)
                out.add((e, a or dv.default_attribute(e)))
        return out

    def cloud_hidden(self, rule: dict[str, Any]) -> str | None:
        """The first thing a rule reads that the tunnel may not (or None). «Дома / никого» reads where the family is — a
        person's state, never the tunnel's (red-team 2, A3): such a rule is saved in HA only, and the tunnel sees it
        redacted like any rule over a private input."""
        if any((c['type'] == 'presence' for c in rule['only_if'])):
            return next(iter(sorted(self.hass.states.async_entity_ids('person'))), 'person')
        return next((eid for eid, attr in sorted(self.reads(rule), key=str) if not self.cloud_reads(eid, attr)), None)

    def cloud_hidden_entities(self) -> set[str]:
        """The rule / derived entities whose state would tell the tunnel what it may not read (scrubbed like
        private)."""
        out: set[str] = set()
        for rid, rule in self.rules.items():
            if self.cloud_hidden(rule) is not None and (b := self.binaries.get(rid)) is not None and b.entity_id:
                out.add(b.entity_id)
        for did, cfg in self.derived_cfg.items():
            if any((not self.cloud_reads(e, a or dv.default_attribute(e)) for e, a in map(dv.split_source, cfg['sources']))):
                ent = self.sensors.get(did)
                out.add(ent.entity_id if ent is not None and ent.entity_id else derived_entity_id(cfg))
        return out

    def _cloud_check(self, rule: dict[str, Any]) -> None:
        if (eid := self.cloud_hidden(rule)) is not None:
            raise Invalid('not_allowed', 'rules_private_input', entity=eid)

    def st(self, rid: str) -> dict[str, Any]:
        return self.state.setdefault(rid, blank())

    async def async_start(self) -> None:
        await self.async_texts()
        self.sync_entities()
        self._listen()
        self._bus_listen()
        self._unsub_tick = async_track_time_interval(self.hass, self._tick, dt.timedelta(seconds=C('engine', 'tick_s')), name='hauflo rules')
        self._unsub_action = self.hass.bus.async_listen('mobile_app_notification_action', self._on_action)

        @callback
        def _up(_hass: HomeAssistant) -> None:
            self.hass.async_create_task(self._start_all(), 'hauflo rules start')
        async_at_started(self.hass, _up)

    async def _start_all(self) -> None:
        await self._pulses_due(start=True)
        for rid in [r for r, x in self.rules.items() if x.get('window')]:
            await self.window_refresh(rid, 'start')
        await self.async_evaluate_all('start')

    @callback
    def async_stop(self) -> None:
        for u in [*self._unsubs, *self._timers.values(), *self._win_timers.values(), *self._pulse_timers.values(), *(u for us in self._repeat_timers.values() for u in us), *([self._unsub_tick] if self._unsub_tick else []), *([self._unsub_action] if self._unsub_action else [])]:
            u()
        self._unsubs.clear()
        self._timers.clear()
        self._win_timers.clear()
        self._pulse_timers.clear()
        self._repeat_timers.clear()
        self._unsub_tick = None
        self._unsub_action = None
        for u, _ in self._br_timers.values():
            u()
        self._br_timers.clear()
        if self._unsub_br is not None:
            self._unsub_br()
            self._unsub_br = None

    @property
    def own_contexts(self) -> deque[str]:
        """The contexts of this engine's own commands (the habits recorder tells them from a person's)."""
        return self._ctx

    def sent_at(self, entity_id: str) -> float | None:
        """When this engine last commanded the entity (the echo window of the habits recorder)."""
        sent = self._sent.get(entity_id)
        at = sent[0] if isinstance(sent, tuple) else sent
        return at if isinstance(at, (int, float)) else None
