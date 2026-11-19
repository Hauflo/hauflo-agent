"""What an automation may act on: rooms, groups and devices."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.components import script
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from ..natural import natural_key
from ..notify.common import label
from ..ownership import entity_room
from ..rules import actions as rules_actions, conditions as rules_conditions, const as rules_const, spec as rules_spec
from .common import COARSE, RAMP, Invalid, client_acts, client_domains, cover_classes, cover_never, group_domains, max_ramp_min, scene_platforms, script_never
_LOGGER = logging.getLogger(__name__)

class AutomationsTargetsMixin:
    """Lists and resolves the targets an automation may use."""

    def area(self, eid: str) -> str | None:
        return entity_room(self.hass, eid)

    def pickable(self, eid: Any, depth: int=0) -> bool:
        """A device of the canon's client domains, of any integration, that the person sees in HA, and safe: never what Hauflo protects (locks, the alarm, valves, garage doors and gates, sticky marks), a blind only by its class, a switch only a socket, a scene / a script only when what it sets is safe — and one that takes at least one of the canon's actions (HA would refuse the rest)."""
        if not isinstance(eid, str) or not (acts := client_acts((domain := eid.split('.')[0]))) or (st := self.hass.states.get(eid)) is None:
            return False
        ent = er.async_get(self.hass).async_get(eid)
        if ent is None or ent.disabled_by or ent.hidden_by or (ent.entity_category is not None) or self.protected(eid):
            return False
        classes = rules_conditions.classes_of(self.hass, eid)
        if domain == 'cover':
            if classes & cover_never() or not (classes & cover_classes() or self.owns(eid)):
                return False
        elif not rules_conditions.client_class_ok(self.hass, eid):
            return False
        if domain == 'scene':
            if self.owns(eid) or ent.platform in scene_platforms():
                return True
            members = st.attributes.get('entity_id')
            return ent.platform == 'homeassistant' and depth < 2 and isinstance(members, list) and bool(members) and all((self.pickable(m, depth + 1) for m in members))
        if domain == 'script' and (not self._safe_script(eid, depth)):
            return False
        return any((rules_conditions.supports(st, spec) for spec in acts.values()))

    def _safe_script(self, eid: str, depth: int) -> bool:
        try:
            if depth >= 2 or any((f(self.hass, eid) for f in (script.devices_in_script, script.areas_in_script, script.floors_in_script, script.labels_in_script))):
                return False
            members = script.entities_in_script(self.hass, eid)
        except Exception:
            _LOGGER.warning('Hauflo: scripts not loaded: nothing known, nothing picked', exc_info=True)
            return False
        for m in members:
            d = m.split('.')[0]
            if d in script_never() or self.protected(m) or (d == 'cover' and rules_conditions.classes_of(self.hass, m) & cover_never()) or (d == 'script' and (not self.pickable(m, depth + 1))):
                return False
        return True

    def _dims(self, eid: str) -> bool:
        st = self.hass.states.get(eid)
        return st is not None and any((m != 'onoff' for m in st.attributes.get('supported_color_modes') or []))

    def _fits(self, eid: str, do: str, value: Any=None) -> bool:
        if do == RAMP:
            return self._dims(eid)
        domain = eid.split('.')[0]
        if (spec := client_acts(domain).get(do)) is None:
            return False
        if domain == 'light':
            return True
        st = self.hass.states.get(eid)
        if not rules_conditions.supports(st, spec):
            return False
        if spec.get('value') == 'option' and value is not None and (st is not None):
            opts = st.attributes.get(spec['options'])
            return isinstance(opts, (list, tuple)) and str(value) in map(str, opts)
        return True

    def _modes(self, eid: str) -> list[str]:
        st = self.hass.states.get(eid)
        have = st.attributes.get('hvac_modes') or [] if st else []
        return [m for m in ('heat', 'cool', 'off') if m in have]

    def _alarm_sensor(self, eid: Any) -> bool:
        if not isinstance(eid, str) or not eid.startswith('sensor.'):
            return False
        ent = er.async_get(self.hass).async_get(eid)
        if ent is None or ent.platform != 'mobile_app' or ent.disabled_by:
            return False
        return str(ent.unique_id).endswith('next_alarm') or eid.endswith('_next_alarm')

    def _members(self, domain: str, area_id: Any, do: str | None=None, value: Any=None) -> list[str]:
        if not isinstance(area_id, str):
            return []
        reg = er.async_get(self.hass)
        found = [e for e in self.hass.states.async_entity_ids(domain) if self.pickable(e) and self.area(e) == area_id and (do is None or self._fits(e, do, value))]
        single = [e for e in found if (x := reg.async_get(e)) is None or x.platform != 'group']
        return sorted(single or found) if domain == 'light' else sorted(single)

    def room_lights(self, area_id: str) -> list[str]:
        return self._members('light', area_id)

    def room_covers(self, area_id: str, position: bool=False) -> list[str]:
        return self._members('cover', area_id, 'position' if position else None)

    def _area_target(self, domain: str, area_id: str, explicit: list[str], do: Any=None) -> dict[str, Any]:
        if domain != 'light':
            return {'entity_id': explicit}
        everything = [eid for eid in self.hass.states.async_entity_ids(domain) if self.area(eid) == area_id]
        if everything and all((self.pickable(eid) for eid in everything)) and (not (do == 'toggle' and self._grouped(everything))):
            return {'area_id': area_id}
        return {'entity_id': explicit}

    def _grouped(self, eids: list[str]) -> bool:
        reg = er.async_get(self.hass)
        return any(((e := reg.async_get(eid)) is not None and e.platform == 'group' for eid in eids))

    def _house_target(self, domain: str, do: Any) -> bool:
        everything = self.hass.states.async_entity_ids(domain)
        return domain == 'light' and bool(everything) and all((self.pickable(e) for e in everything)) and (not (do == 'toggle' and self._grouped(everything)))

    def _set_members(self, domain: str, area_id: str, position: bool=False) -> list[str]:
        return self._members(domain, area_id, 'position' if position else None)

    def _all_target(self, domain: str, do: Any=None, value: Any=None) -> tuple[list[str], list[str]]:
        if isinstance(do, bool):
            do = 'position' if do else None
        reg = er.async_get(self.hass)
        rooms, loose = (set(), [])
        for eid in self.hass.states.async_entity_ids(domain):
            if not self.pickable(eid):
                continue
            if (area := self.area(eid)):
                rooms.add(area)
            elif (e := reg.async_get(eid)) is not None and e.platform != 'group' and (do is None or self._fits(eid, do, value)):
                loose.append(eid)
        rooms = {r for r in rooms if self._members(domain, r, do, value)}
        return (sorted(rooms), sorted(loose))

    @staticmethod
    def _domain(t: dict[str, Any]) -> str:
        if t.get('domain'):
            return t['domain']
        e = t['entity_id']
        return (e if isinstance(e, str) else e[0]).split('.')[0]

    def _target(self, a: dict[str, Any]) -> dict[str, Any] | None:
        t, do, value = (a['target'], a['do'], a.get('value'))
        domain = self._domain(t)
        if isinstance(t.get('entity_id'), str):
            return {'entity_id': t['entity_id']}
        if isinstance(t.get('area_id'), str):
            members = self._members(domain, t['area_id'], do, value)
            return self._area_target(domain, t['area_id'], members, do) if members else None
        if t.get('all') and self._house_target(domain, do):
            return {'entity_id': 'all'}
        rooms, loose = self._all_target(domain, do, value) if t.get('all') else ([], [])
        rooms = sorted({*rooms, *t.get('area_id', [])})
        area_ids: list[str] = []
        eids: list[str] = []
        for room in rooms:
            if not (members := self._members(domain, room, do, value)):
                continue
            got = self._area_target(domain, room, members, do)
            if 'area_id' in got:
                area_ids.append(room)
            else:
                eids += got['entity_id']
        for eid in [*loose, *t.get('entity_id', [])]:
            if self.pickable(eid) and self._fits(eid, do, value) and (eid not in eids) and (self.area(eid) not in area_ids):
                eids.append(eid)
        target: dict[str, Any] = {}
        if area_ids:
            target['area_id'] = area_ids
        if eids:
            target['entity_id'] = eids
        return target or None

    def covers(self, a: dict[str, Any]) -> set[tuple[str, str]]:
        """(device, coarse act) pairs one action covers — habits.py compares them with a person's own hand actions."""
        try:
            a = a if 'target' in a else self._legacy(a)
            t = a['target']
            domain = self._domain(t)
        except (Invalid, KeyError, IndexError, TypeError, AttributeError):
            return set()
        if isinstance(t.get('entity_id'), str):
            eids = {t['entity_id']}
        elif isinstance(t.get('area_id'), str):
            eids = set(self._members(domain, t['area_id']))
        else:
            rooms, loose = self._all_target(domain) if t.get('all') else ([], [])
            eids = {*loose, *(t.get('entity_id') or [])}
            for room in {*rooms, *(t.get('area_id') or [])}:
                eids |= set(self._members(domain, room))
        act = a.get('value') if a['do'] == 'mode' else COARSE.get(a['do'], a['do'])
        return {(e, act) for e in eids}

    def _acts_of(self, eid: str, st: Any) -> dict[str, dict[str, Any]]:
        lim = rules_spec.C('limits')
        attrs = st.attributes
        out: dict[str, dict[str, Any]] = {}
        for do, spec in client_acts(eid.split('.')[0]).items():
            if not rules_conditions.supports(st, spec):
                continue
            kind = spec.get('value')
            item: dict[str, Any] = {'kind': kind} if kind else {}
            if kind == 'option':
                item['options'] = [str(o) for o in attrs.get(spec['options']) or []]
            elif kind in rules_const.NUMERIC:
                fallback = {'percent': lim['percent'], 'setpoint': lim['setpoint'], 'seconds': rules_spec.C('actions', 'pulse_s')}.get(kind, (-lim['number_abs'], lim['number_abs']))
                item['min'], item['max'] = rules_spec.bounds(spec, attrs, fallback)
                step = rules_actions.num(attrs.get(spec['step'])) if spec.get('step') else None
                item['step'] = step if step and step > 0 else lim['setpoint_step'] if kind == 'setpoint' else 1
                unit = {'percent': '%', 'seconds': 's', 'setpoint': self.hass.config.units.temperature_unit}.get(kind, attrs.get('unit_of_measurement'))
                if unit:
                    item['unit'] = unit
            out[do] = item
        if eid.startswith('light.') and self._dims(eid):
            out[RAMP] = {'kind': RAMP, 'min': 1, 'max': 100, 'step': 1, 'unit': '%', 'minutes': [1, max_ramp_min()]}
        return out

    def targets(self) -> dict[str, Any]:
        areas = ar.async_get(self.hass)
        out: dict[str, list[dict[str, Any]]] = {'cover': [], 'light': [], 'scene': [], 'fan': [], 'switch': [], 'climate': []}
        rooms: dict[str, int] = {}
        for domain in out:
            for eid in sorted(self.hass.states.async_entity_ids(domain)):
                if not self.pickable(eid):
                    continue
                s = self.hass.states.get(eid)
                area = self.area(eid)
                item: dict[str, Any] = {'entity_id': eid, 'name': label(self.hass, eid, s), 'area': area}
                if domain == 'cover':
                    item['position'] = bool(int(s.attributes.get('supported_features') or 0) & 4)
                    item['group'] = (e := er.async_get(self.hass).async_get(eid)) is not None and e.platform == 'group'
                if domain == 'light':
                    modes = s.attributes.get('supported_color_modes') or []
                    item['dim'] = any((m != 'onoff' for m in modes))
                if domain == 'climate':
                    item.update(self._climate_spec(eid))
                out[domain].append(item)
        for area_id in {i['area'] for i in out['light'] if i['area']}:
            if (n := len(self.room_lights(area_id))):
                rooms[area_id] = n
        names = {a: area.name if (area := areas.async_get_area(a)) else a for a in {i['area'] for v in out.values() for i in v if i['area']}}
        cover_rooms = []
        for area_id in {i['area'] for i in out['cover'] if i['area']}:
            if (n := len(self.room_covers(area_id))):
                cover_rooms.append({'area_id': area_id, 'name': names.get(area_id, area_id), 'covers': n, 'position': bool(self.room_covers(area_id, True))})
        alarms = []
        for ent in er.async_get(self.hass).entities.values():
            if ent.domain == 'sensor' and self._alarm_sensor(ent.entity_id):
                dev = dr.async_get(self.hass).async_get(ent.device_id) if ent.device_id else None
                s = self.hass.states.get(ent.entity_id)
                alarms.append({'entity_id': ent.entity_id, 'phone': dev.name_by_user or dev.name if dev else ent.entity_id, 'next': s.state if s else None})
        sensors = []
        for eid in sorted(self.hass.states.async_entity_ids('sensor')):
            if (kind := self._value_kind(eid)) is None:
                continue
            s = self.hass.states.get(eid)
            sensors.append({'entity_id': eid, 'name': label(self.hass, eid, s), 'area': self.area(eid), 'kind': kind, 'unit': s.attributes.get('unit_of_measurement'), 'value': s.state})
        binaries = []
        for domain in ('binary_sensor', 'media_player'):
            for eid in sorted(self.hass.states.async_entity_ids(domain)):
                if (kind := self._state_kind(eid)) is None:
                    continue
                s = self.hass.states.get(eid)
                binaries.append({'entity_id': eid, 'name': label(self.hass, eid, s), 'area': self.area(eid), 'kind': kind, 'state': s.state})
        for i in (*sensors, *binaries):
            if i['area'] and i['area'] not in names:
                names[i['area']] = area.name if (area := areas.async_get_area(i['area'])) else i['area']
        reg = er.async_get(self.hass)
        entities = []
        for domain in client_domains():
            for eid in sorted(self.hass.states.async_entity_ids(domain)):
                if not self.pickable(eid) or not (acts := self._acts_of(eid, (s := self.hass.states.get(eid)))):
                    continue
                item = {'entity_id': eid, 'name': label(self.hass, eid, s), 'area': self.area(eid), 'domain': domain, 'acts': acts, 'state': s.state}
                if (e := reg.async_get(eid)) is not None and e.platform == 'group':
                    item['group'] = True
                entities.append(item)
                if item['area'] and item['area'] not in names:
                    names[item['area']] = area.name if (area := areas.async_get_area(item['area'])) else item['area']
        people = sum((1 for eid in self.hass.states.async_entity_ids('person') if self.hass.states.get(eid).attributes.get('device_trackers') or []))
        return {'sensors': sensors, 'binaries': binaries, 'people': people, 'alarms': sorted(alarms, key=lambda a: str(a['phone'])), 'covers': out['cover'], 'lights': out['light'], 'scenes': out['scene'], 'fans': out['fan'], 'switches': out['switch'], 'climates': out['climate'], 'rooms': sorted(({'area_id': a, 'name': names.get(a, a), 'lights': n} for a, n in rooms.items()), key=lambda r: natural_key(r['name'])), 'cover_rooms': sorted(cover_rooms, key=lambda r: natural_key(r['name'])), 'areas': names, 'entities': entities, 'group_domains': list(group_domains())}
