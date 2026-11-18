"""Says an automation in words."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from ..natural import natural_key
from ..notify.common import label
from ..rules import const as rules_const, spec as rules_spec
from .common import DAYS, RAMP
_LOGGER = logging.getLogger(__name__)

class AutomationsDescribeMixin:
    """Describes automations in the user's language."""

    def _ru(self, rule: dict[str, Any]) -> bool:
        lang = rule.get('lang')
        return lang == rules_const.LANGS[0] if lang in rules_const.LANGS else str(self.hass.config.language or '').startswith(rules_const.LANGS[0])

    def _count(self, key: str, ru: bool, n: int, **ph: Any) -> str:
        if ru:
            form = 'one' if n % 10 == 1 and n % 100 != 11 else 'few' if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else 'many'
        else:
            form = 'one' if n == 1 else 'many'
        return self.tx.say(f'{key}_{form}', ru, n=n, **ph)

    def _d(self, ru: bool, key: str, **ph: Any) -> str:
        return self.tx.say(f'automations_d_{key}', ru, **ph)

    @staticmethod
    def _lower(text: str) -> str:
        return text[:1].lower() + text[1:]

    def _room_name(self, area_id: str) -> str:
        area = ar.async_get(self.hass).async_get_area(area_id)
        return area.name if area else area_id

    def _and(self, ru: bool, items: list[str]) -> str:
        if len(items) < 2:
            return ''.join(items)
        return self._d(ru, 'and', a=', '.join(items[:-1]), b=items[-1])

    def _num(self, v: Any, ru: bool) -> str:
        text = f'{v:g}' if isinstance(v, float) else str(v)
        return text.replace('.', ',') if ru else text

    def _minutes(self, n: int, ru: bool) -> str:
        h, m = divmod(abs(int(n)), 60)
        if not h:
            return self._d(ru, 'min', n=m)
        return self._d(ru, 'hmin', h=h, m=m) if m else self._d(ru, 'h', n=h)

    def _seconds(self, n: Any, ru: bool) -> str:
        n = int(n)
        return self._d(ru, 'sec', n=n) if n < 60 or n % 60 else self._minutes(n // 60, ru)

    def _names(self, eids: Any) -> str:
        return ', '.join((label(self.hass, e) for e in ([eids] if isinstance(eids, str) else eids)))

    def _phone(self, eid: str) -> str:
        ent = er.async_get(self.hass).async_get(eid)
        dev = dr.async_get(self.hass).async_get(ent.device_id) if ent and ent.device_id else None
        return dev.name_by_user or dev.name if dev and (dev.name_by_user or dev.name) else label(self.hass, eid)

    def _days(self, days: list[str], ru: bool) -> str | None:
        if len(days) == len(DAYS):
            return None
        if days == list(DAYS[:5]):
            return self._d(ru, 'days_workdays')
        if days == list(DAYS[5:]):
            return self._d(ru, 'days_weekend')
        return self._d(ru, 'days_some', days=', '.join((self._d(ru, f'wd_{d}') for d in days)))

    def _reading(self, spec: dict[str, Any], ru: bool) -> tuple[str, str]:
        kind = self._d(ru, f"kind_{spec['kind']}")
        st = self.hass.states.get(spec['sensors'][0])
        unit = (st.attributes.get('unit_of_measurement') if st else None) or ''
        if len(spec['sensors']) == 1:
            what = self._d(ru, 'reading_one', kind=kind, sensor=self._names(spec['sensors']))
        else:
            what = self._d(ru, 'reading_many', kind=kind, stat=self.tx.say(f"automations_stat_{spec.get('stat', 'mean')}", ru), sensors=self._names(spec['sensors']))
        return (what, self._d(ru, f"op_{spec['op']}", x=f"{self._num(spec['x'], ru)} {unit}".strip()))

    def _when_text(self, when: dict[str, Any], ru: bool) -> str:
        ev = when['event']
        if ev == 'time':
            return self._d(ru, 'at', times=self._and(ru, list(when.get('times') or [when['at']])))
        if ev == 'alarm_iphone':
            return self._d(ru, 'alarm_iphone')
        if ev == 'value':
            what, how = self._reading(when, ru)
            text = self._d(ru, 'when_value', what=what, op=how)
            return self._d(ru, 'hold', text=text, d=self._minutes(when['for_min'], ru)) if when.get('for_min') else text
        if ev == 'state':
            names = self._names(when['sensors'])
            if len(when['sensors']) > 1:
                names = self._d(ru, 'all_of' if when['to'] == 'off' else 'any_of', names=names)
            text = self._d(ru, f"state_{when['kind']}_{when['to']}", sensors=names)
            return self._d(ru, 'hold', text=text, d=self._minutes(when['for_min'], ru)) if when.get('for_min') else text
        if ev == 'presence':
            return self._d(ru, f"presence_{when['to']}")
        if ev == 'alarm_android':
            phone = self._phone(when['sensor'])
            at, of = (self._d(ru, 'alarm', phone=phone), self._d(ru, 'of_alarm', phone=phone))
        else:
            at, of = (self._d(ru, ev), self._d(ru, f'of_{ev}'))
        if not (off := (when.get('offset') or 0)):
            return at
        return self._d(ru, 'before' if off < 0 else 'after', d=self._minutes(off, ru), of=of)

    def _if_text(self, c: dict[str, Any], ru: bool) -> str:
        if c['if'] == 'between':
            return self._d(ru, 'if_between', a=c['after'], b=c['before'])
        if c['if'] == 'value':
            what, how = self._reading(c, ru)
            return self._d(ru, 'if_value', what=what, op=how)
        return self._d(ru, f"if_{c['if']}")

    def _value_text(self, a: dict[str, Any], spec: dict[str, Any], domain: str, ru: bool) -> str:
        v, kind = (a.get('value'), spec.get('value'))
        if v is None or not kind:
            return ''
        if kind == 'percent':
            return f'{self._num(v, ru)} %'
        if kind == 'seconds':
            return self._seconds(v, ru)
        if kind == 'setpoint':
            return f'{self._num(v, ru)} {self.hass.config.units.temperature_unit}'
        if kind == 'option':
            return self._d(ru, 'option', option=self.tx.option_label(ru, domain, spec.get('field'), str(v)))
        eid = a['target'].get('entity_id')
        st = self.hass.states.get(eid if isinstance(eid, str) else eid[0]) if eid else None
        return f"{self._num(v, ru)} {(st.attributes.get('unit_of_measurement') if st else None) or ''}".strip()

    def _where(self, a: dict[str, Any], ru: bool) -> str:
        t = a['target']
        picked = [t['entity_id']] if isinstance(t.get('entity_id'), str) else list(t.get('entity_id') or [])
        parts: list[str] = []
        if t.get('area_id') or t.get('all'):
            rooms = [t['area_id']] if isinstance(t.get('area_id'), str) else list(t.get('area_id') or [])
            names = sorted({self._room_name(r) for r in rooms}, key=natural_key)
            places = self._d(ru, 'everywhere') if t.get('all') else self._and(ru, names)
            got = self._target(a) or {}
            eids, by_room = (got.get('entity_id'), got.get('area_id') or [])
            by_room = [by_room] if isinstance(by_room, str) else list(by_room)
            if eids != 'all' and (t.get('all') or set(rooms) - set(by_room)):
                took = {e for e in ([eids] if isinstance(eids, str) else eids or []) if e not in picked}
                took |= {e for r in by_room for e in self._members(self._domain(t), r, a['do'], a.get('value'))}
                places = self._d(ru, 'snapshot', places=places, count=self._count('automations_d_devices', ru, len(took)))
            parts.append(self._d(ru, 'where_kind', kind=self._d(ru, f'dom_{self._domain(t)}'), places=places))
        if picked:
            parts.append(self._names(picked))
        return ', '.join(parts)

    def _act_text(self, a: dict[str, Any], ru: bool) -> str:
        a = a if 'target' in a else self._legacy(a)
        where = self._where(a, ru)
        if a['do'] == RAMP:
            text = self._d(ru, 'ramp', value=a['value'], d=self._minutes(a['minutes'], ru), where=where)
        else:
            domain = self._domain(a['target'])
            spec = rules_spec.C('actions_by_domain')[domain][a['do']]
            value = self._value_text(a, spec, domain, ru)
            do = self._lower(self.tx.say(f"rules_do_{a['do']}", ru))
            text = self._d(ru, 'act_value' if value else 'act', do=do, value=value, where=where)
        if (rep := a.get('repeat')):
            text = self._d(ru, 'repeat', act=text, times=self._count('automations_d_times', ru, rep['count']), every=self._seconds(rep['every_s'], ru))
        return text

    def describe(self, rule: dict[str, Any], ru: bool) -> str:
        """«Каждый будний день в 07:00 и 07:30: включить — свет (Кухня, Прихожая). Только если темно. Создано в Hauflo.»
        —
        the rule in the language it was made in, from translations only; the devices and rooms by their names."""
        made = self._d(ru, 'made')
        try:
            when = rule['when']
            head = self._when_text(when, ru)
            if (days := self._days(list(rule['days']), ru)):
                timed = when['event'] in ('time', 'sunrise', 'sunset', 'alarm_android', 'alarm_iphone')
                head = self._d(ru, 'days_at' if timed else 'days_when', days=days, when=head)
            out = [self._d(ru, 'sentence', head=head[:1].upper() + head[1:], acts='; '.join((self._act_text(a, ru) for a in rule['actions'])))]
            if (only := rule.get('only_if')):
                out.append(self._d(ru, 'only_if', conds=self._and(ru, [self._if_text(c, ru) for c in only])))
        except Exception:
            _LOGGER.warning('Hauflo automations: no words for a rule', exc_info=True)
            return made
        return ' '.join([*out, made])

    @staticmethod
    def _readings(rule: dict[str, Any]) -> list[dict[str, Any]]:
        return [x for x in [rule['when'], *rule.get('only_if', [])] if x.get('event') == 'value' or x.get('if') == 'value']

    async def _resolve(self, rule: dict[str, Any], data: dict[str, Any]) -> None:
        for spec in self._readings(rule):
            spec['entity'] = spec['sensors'][0] if len(spec['sensors']) == 1 else await self._helper(data, spec)
