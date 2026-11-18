"""HA entities of the rules and their derived sensors."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util, slugify
from .. import derived as dv
from ..const import DOMAIN, site_name
from ..derived import ROUND_DIGITS, Invalid
from ..device import device_info
from .actions import inputs_of
from .const import IS
from .spec import C, act_spec, scheduled
if TYPE_CHECKING:
    from .engine import Rules

def derived_entity_id(cfg: dict[str, Any]) -> str:
    return f"sensor.{DOMAIN}_{C('engine', 'derived_prefix')}_{slugify(cfg['name']) or cfg['id']}"

def service_for(a: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The HA service of an action, by the canon's row: 0 % → its «zero» service (off / close), 100 % → its «full»."""
    spec, v = (act_spec(a) or {}, a.get('value'))
    numeric = isinstance(v, (int, float)) and (not isinstance(v, bool))
    if numeric and spec.get('zero') and (v == 0):
        return (spec['zero'], {})
    if numeric and spec.get('full') and (v == C('limits', 'percent')[1]):
        return (spec['full'], {})
    if not spec.get('field'):
        return (spec['service'], {})
    if spec.get('scale') and numeric:
        v = round(v * spec['scale'], ROUND_DIGITS)
    return (spec['service'], {spec['field']: v})

class _RuleEntity:
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = 'rule'

    def _setup(self, engine: Rules, rule: dict[str, Any], domain: str, suffix: str) -> None:
        self.engine = engine
        self.rid = rule['id']
        self._attr_unique_id = f"{engine.entry_id}_rule_{rule['id']}{suffix}"
        self.entity_id = f"{domain}.{DOMAIN}_{C('engine', 'rule_prefix')}_{slugify(rule['name']) or rule['id']}"
        if engine.entry_id:
            self._attr_device_info = device_info(engine.entry_id)
        self._name_key = f'{domain}.{self._attr_translation_key}'
        self._attr_translation_placeholders = {'name': rule['name']}
        self._site_title: str | None = None
        self._name_in_site_lang(rule)

    def _name_in_site_lang(self, rule: dict[str, Any]) -> bool:
        names = getattr(self.engine, 'names', None)
        title = site_name(names, self.engine.ru, self._name_key, name=rule['name']) if names else None
        if title is None or self._site_title == title:
            return False
        self._site_title = self._attr_name = title
        return True

    @callback
    def refresh(self) -> None:
        rule = self.engine.rules.get(self.rid)
        if rule is not None and (self._name_in_site_lang(rule) or self._attr_translation_placeholders != {'name': rule['name']}):
            self._attr_translation_placeholders = {'name': rule['name']}
            self.__dict__.pop('name', None)
        if self.hass is not None and self.entity_id and (self.platform is not None):
            self.async_write_ha_state()

class RuleActive(_RuleEntity, BinarySensorEntity):
    """On while the rule is ACTIVE; why in its attributes (and the logbook)."""
    _unrecorded_attributes = frozenset({'why', 'x', 'lo', 'hi', 'y', 'stat', 'period', 'periods_needed'})

    def __init__(self, engine: Rules, rule: dict[str, Any]) -> None:
        self._setup(engine, rule, 'binary_sensor', '')

    @property
    def is_on(self) -> bool:
        return bool(self.engine.st(self.rid)['active'])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rule = self.engine.rules.get(self.rid)
        if rule is None:
            return {}
        st = self.engine.st(self.rid)
        out = {'state': self.engine.status(self.rid), 'value': st.get('value'), 'unit': rule['input'].get('unit') if 'input' in rule else None, 'why': self.engine.explain(rule, self.engine.ru)}
        if scheduled(rule):
            out.update(next_at=st.get('next_at'), last_at=st.get('last_at'), last_result=st.get('last_result'))
        out.update({k: rule[k] for k in ('x', 'lo', 'hi', 'y', IS, 'not') if k in rule})
        if (w := rule.get('window')) is not None:
            out.update(statistic=st.get('value'), stat=w['stat'], period=w['period'], periods_in_row=st.get('periods', 0), periods_needed=w['n'], no_data=bool(st.get('no_data')))
            if st['since']:
                out['since'] = dt_util.utc_from_timestamp(st['since']).isoformat()
        if (sug := self.engine.suggestion(self.rid)) is not None:
            out['suggestion'] = STATE_ON if sug['on'] else STATE_OFF
        if st['hold_until']:
            out['hold_until'] = dt_util.utc_from_timestamp(st['hold_until']).isoformat()
        return out

class RuleSwitch(_RuleEntity, SwitchEntity):
    """The rule on / off (as the app's switch)."""
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, engine: Rules, rule: dict[str, Any]) -> None:
        self._setup(engine, rule, 'switch', '_enabled')

    @property
    def is_on(self) -> bool:
        rule = self.engine.rules.get(self.rid)
        return bool(rule and rule['enabled'])

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.engine.set_enabled(self.rid, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.engine.set_enabled(self.rid, False)

class DerivedSensor(SensorEntity):
    """«Точка росы · Ванная» and the like: a derived value as a real sensor."""
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = 'derived'
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, engine: Rules, did: str) -> None:
        self.engine = engine
        self.did = did
        cfg = engine.derived_cfg[did]
        self._attr_unique_id = f'{engine.entry_id}_derived_{did}'
        self.entity_id = derived_entity_id(cfg)
        if engine.entry_id:
            self._attr_device_info = device_info(engine.entry_id)
        self._attr_translation_placeholders = {'name': cfg['name']}

    @property
    def _d(self) -> dv.Derived | None:
        return self.engine.derived.get(self.did)

    @property
    def available(self) -> bool:
        """«unknown» while the sources aren't heard yet (the rules on it wait), «unavailable» once a source is lost."""
        d = self._d
        return d is not None and (d.value is not None or (d.why or '').split(':')[0] in dv.WAITING)

    @property
    def native_value(self) -> float | None:
        return self._d.value if self._d else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._d.unit if self._d else None

    @property
    def device_class(self) -> Any:
        return self._d.device_class if self._d else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self._d
        return {'kind': d.cfg['kind'], 'sources': d.cfg['sources'], **({'why': d.why} if d.why else {})} if d else {}

    @callback
    def refresh(self) -> None:
        if self.hass is not None and self.entity_id and (self.platform is not None):
            self.async_write_ha_state()

class RulesEntitiesMixin:
    """Keeps the rule entities and derived sensors."""

    def derived_sources(self, eid: str) -> list[str]:
        for did, d in self.derived_cfg.items():
            ent = self.sensors.get(did)
            if eid == (ent.entity_id if ent is not None and ent.entity_id else derived_entity_id(d)):
                return d['sources']
        return []

    def _log(self, rid: str, event: str, **kw: Any) -> None:
        st = self.st(rid)
        item = {'at': dv.now(), 'event': event, **{k: v for k, v in kw.items() if v is not None}}
        st['log'] = [*st['log'], item][-int(C('engine', 'log_size')):]

    def _logbook(self, rule: dict[str, Any], message: str) -> None:
        ent = self.binaries.get(rule['id'])
        data: dict[str, Any] = {'name': rule['name'], 'message': message, 'domain': DOMAIN}
        if ent is not None and ent.entity_id:
            data['entity_id'] = ent.entity_id
        self.hass.bus.async_fire('logbook_entry', data)

    @callback
    def _refresh(self, rid: str) -> None:
        for e in (self.binaries.get(rid), self.switches.get(rid)):
            if e is not None:
                e.refresh()

    def _make_derived(self) -> None:
        for did in [d for d in self.derived if d not in self.derived_cfg]:
            self.derived.pop(did)
        for did, cfg in self.derived_cfg.items():
            if did in self.derived:
                self.derived[did].cfg = cfg
            else:
                self.derived[did] = dv.Derived(self.hass, cfg)
            self.derived[did].update()

    @callback
    def sync_entities(self) -> None:
        reg = er.async_get(self.hass)
        for ents, ids in ((self.binaries, self.rules), (self.switches, self.rules), (self.sensors, self.derived_cfg)):
            for key in [k for k in ents if k not in ids]:
                ent = ents.pop(key)
                if ent.entity_id and reg.async_get(ent.entity_id):
                    reg.async_remove(ent.entity_id)
        for rid, rule in self.rules.items():
            for ents, add, cls in ((self.binaries, self.add_binary, RuleActive), (self.switches, self.add_switches, RuleSwitch)):
                if rid in ents:
                    ents[rid].refresh()
                elif add is not None:
                    ents[rid] = e = cls(self, rule)
                    add([e])
        for did in self.derived_cfg:
            if did in self.sensors:
                self.sensors[did].refresh()
            elif self.add_sensors is not None:
                self.sensors[did] = d = DerivedSensor(self, did)
                self.add_sensors([d])

    async def save_derived(self, raw: Any, *, tunnel: bool=False) -> dict[str, Any]:
        d = dv.clean(raw, self.hass)
        for src in d['sources'] if tunnel else ():
            e, a = dv.split_source(src)
            if not self.cloud_reads(e, a or dv.default_attribute(e)):
                raise Invalid('not_allowed', 'rules_private_input', entity=e)
        if d['id'] not in self.derived_cfg and len(self.derived_cfg) >= C('derived', 'max'):
            raise Invalid('limit', 'rules_limit_derived', n=int(C('derived', 'max')))
        self.derived_cfg[d['id']] = d
        self._make_derived()
        await self._save()
        self.sync_entities()
        self._listen()
        s = self.sensors.get(d['id'])
        return {**d, 'entity_id': s.entity_id if s else derived_entity_id(d), 'value': self.derived[d['id']].value}

    async def delete_derived(self, did: Any) -> None:
        if not isinstance(did, str) or did not in self.derived_cfg:
            raise Invalid('no_value', 'rules_no_value')
        ent = self.sensors.get(did)
        eid = ent.entity_id if ent is not None and ent.entity_id else derived_entity_id(self.derived_cfg[did])
        users = [r['name'] for r in self.rules.values() if eid in inputs_of(r)]
        if users:
            raise Invalid('in_use', 'rules_in_use', rules=', '.join(users))
        self.derived_cfg.pop(did)
        self._make_derived()
        await self._save()
        self.sync_entities()
        self._listen()
