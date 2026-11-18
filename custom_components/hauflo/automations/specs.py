"""What a trigger or condition may watch: states and values."""
from __future__ import annotations
import math
from typing import Any
from homeassistant.helpers import entity_registry as er
from .common import HELPER, IFS, MEDIA, OPS, STATS, TIME, TOS, VALUE_KINDS, Invalid, bad, bounded_float, bounded_int, max_for_min, max_only_if, max_sensors, state_classes

class AutomationsSpecsMixin:
    """Describes the states and values a trigger may watch."""

    def _value_kind(self, eid: Any) -> str | None:
        if not isinstance(eid, str) or not eid.startswith('sensor.') or (s := self.hass.states.get(eid)) is None:
            return None
        kind = s.attributes.get('device_class')
        if kind not in VALUE_KINDS:
            return None
        ent = er.async_get(self.hass).async_get(eid)
        if ent is not None and (ent.disabled_by or ent.hidden_by or (ent.platform == HELPER and str(ent.original_name or '').startswith('hauflo_'))):
            return None
        return str(kind)

    def _state_kind(self, eid: Any) -> str | None:
        if not isinstance(eid, str) or (domain := eid.split('.')[0]) not in ('binary_sensor', 'media_player') or (s := self.hass.states.get(eid)) is None:
            return None
        ent = er.async_get(self.hass).async_get(eid)
        if ent is not None and (ent.disabled_by or ent.hidden_by or ent.entity_category is not None):
            return None
        if domain == 'media_player':
            return MEDIA
        classes = [s.attributes.get('device_class'), *((ent.device_class, ent.original_device_class) if ent else ())]
        return next((state_classes()[c] for c in classes if c in state_classes()), None)

    def _state_spec(self, raw: dict[str, Any]) -> dict[str, Any]:
        sensors = raw.get('sensors')
        if not isinstance(sensors, list) or not 1 <= len(sensors) <= max_sensors() or len(set(map(str, sensors))) != len(sensors):
            raise Invalid('bad_sensors', n=max_sensors())
        kinds = {self._state_kind(e) for e in sensors}
        if None in kinds:
            raise Invalid('bad_state_sensor', entity=next((e for e in sensors if self._state_kind(e) is None)))
        if len(kinds) != 1:
            raise Invalid('bad_sensor_mix')
        kind = kinds.pop()
        if raw.get('kind') not in (None, kind):
            raise bad('when.kind')
        if raw.get('to') not in TOS:
            raise bad('when.to')
        if kind == MEDIA and raw['to'] == 'off' and (len(sensors) > 1):
            raise Invalid('bad_media_off')
        return {'kind': kind, 'sensors': sorted(sensors), 'to': raw['to'], 'for_min': bounded_int(raw.get('for_min', 0), 0, max_for_min(), 'for_min')}

    def _value_spec(self, raw: Any, what: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise bad(what)
        sensors = raw.get('sensors')
        if not isinstance(sensors, list) or not 1 <= len(sensors) <= max_sensors() or len(set(map(str, sensors))) != len(sensors):
            raise Invalid('bad_sensors', n=max_sensors())
        kinds = {self._value_kind(e) for e in sensors}
        if None in kinds:
            raise Invalid('bad_value_sensor', entity=next((e for e in sensors if self._value_kind(e) is None)))
        if len(kinds) != 1 or len({self.hass.states.get(e).attributes.get('unit_of_measurement') for e in sensors}) != 1:
            raise Invalid('bad_value_mix')
        if raw.get('kind') not in (None, *kinds):
            raise bad(f'{what}.kind')
        op = raw.get('op')
        if op not in OPS:
            raise bad(f'{what}.op')
        x = bounded_float(raw.get('x'), -100, 200000, 'x')
        out: dict[str, Any] = {'sensors': sorted(sensors), 'kind': kinds.pop(), 'op': op, 'x': int(x) if x.is_integer() else round(x, 2)}
        if len(sensors) > 1:
            if raw.get('stat', 'mean') not in STATS:
                raise bad(f'{what}.stat')
            out['stat'] = raw.get('stat', 'mean')
        return out

    @staticmethod
    def _never(when: dict[str, Any], only_if: list[dict[str, Any]]) -> None:
        ifs = {c['if'] for c in only_if}
        if when['event'] == 'presence' and ('home' if when['to'] == 'away' else 'away') in ifs:
            raise Invalid('never_presence')
        if when['event'] == 'time':
            for c in only_if:
                if c['if'] == 'between':
                    a, b = (c['after'], c['before'])
                    for at in when.get('times') or [when['at']]:
                        if not (a <= at < b if a < b else at >= a or at < b):
                            raise Invalid('never_between', a=a, b=b, at=at)
        spans: dict[tuple[Any, ...], list[float]] = {}
        for spec in (x for x in [when, *only_if] if x.get('event') == 'value' or x.get('if') == 'value'):
            span = spans.setdefault((tuple(spec['sensors']), spec.get('stat')), [-math.inf, math.inf])
            if spec['op'] == 'above':
                span[0] = max(span[0], spec['x'])
            else:
                span[1] = min(span[1], spec['x'])
            if span[0] >= span[1]:
                raise Invalid('never_value')

    def _only_if(self, raw: Any) -> list[dict[str, Any]]:
        if raw is None:
            return []
        if not isinstance(raw, list) or len(raw) > max_only_if():
            raise Invalid('bad_only_if_count', n=max_only_if())
        out: list[dict[str, Any]] = []
        for c in raw:
            kind = c.get('if') if isinstance(c, dict) else None
            if kind not in IFS:
                raise bad('only_if.if')
            if kind == 'between':
                after, before = (c.get('after'), c.get('before'))
                if not all((isinstance(t, str) and TIME.fullmatch(t) for t in (after, before))) or after == before:
                    raise Invalid('bad_between')
                out.append({'if': 'between', 'after': after, 'before': before})
            elif kind == 'value':
                out.append({'if': 'value', **self._value_spec(c, 'only_if.value')})
            else:
                out.append({'if': kind})
        if {'dark', 'light'} <= {c['if'] for c in out} or {'home', 'away'} <= {c['if'] for c in out}:
            raise Invalid('never_only_if')
        if len([c for c in out if c['if'] == 'between']) > 1:
            raise Invalid('bad_between_one')
        return out
