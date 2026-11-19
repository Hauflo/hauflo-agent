"""Says rules, their state and their reasons in words."""
from __future__ import annotations
from typing import Any
from homeassistant.const import PERCENTAGE, STATE_CLOSED, STATE_OFF, STATE_ON, STATE_OPEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import translation
from .. import derived as dv
from ..const import DOMAIN
from ..derived import SECONDS_PER_MINUTE, Invalid, num
from ..notify.common import label
from .answer import fmt
from .conditions import reads_of
from .const import ENTITY_RE, IS, IS_NUMBERS, LANGS
from .spec import C, option_domains, purpose, scheduled, spec_of

class Texts:
    """The agent's texts in both languages: translations («exceptions»), HA's option labels, devices by name."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.texts: dict[str, dict[str, str]] = {lang: {} for lang in LANGS}
        self.labels: dict[str, dict[str, str]] = {lang: {} for lang in LANGS}

    async def async_texts(self) -> None:
        prefix, suffix = (f'component.{DOMAIN}.exceptions.', '.message')
        for lang in LANGS:
            tr = await translation.async_get_translations(self.hass, lang, 'exceptions', [DOMAIN])
            self.texts[lang] = {k[len(prefix):-len(suffix)]: v for k, v in tr.items() if k.startswith(prefix) and k.endswith(suffix)}
            self.labels[lang] = await translation.async_get_translations(self.hass, lang, 'entity_component', option_domains())

    def say(self, key: str, ru: bool, **ph: Any) -> str:
        text = self.texts[LANGS[0] if ru else LANGS[1]].get(key) or self.texts[LANGS[1]].get(key) or key
        try:
            return text.format(**ph)
        except (KeyError, IndexError, ValueError):
            return text

    def who(self, eid: Any) -> Any:
        """A device in a text: what a person calls it (notify.label), never its entity id."""
        return label(self.hass, eid) if isinstance(eid, str) and ENTITY_RE.match(eid) else eid

    def known_label(self, ru: bool, domain: Any, field: Any, option: str) -> str | None:
        """An option value as HA names it in the language — its state attribute's labels, else the domain's states, else the same attribute of another domain (a fan's «boost» preset as a thermostat's), else Hauflo's own (KNX's presets HA has no label for: standby / economy / building_protection); None — nobody names it."""
        labels = self.labels[LANGS[0] if ru else LANGS[1]]
        root = f'component.{domain}.entity_component._.'
        got = labels.get(f'{root}state_attributes.{field}.state.{option}') or labels.get(f'{root}state.{option}')
        if not got and field:
            got = next((labels[k] for d in sorted(option_domains()) if (k := f'component.{d}.entity_component._.state_attributes.{field}.state.{option}') in labels), None)
        own = f'rules_opt_{option}'
        return got or (self.say(own, ru) if own in self.texts[LANGS[1]] else None)

    def option_label(self, ru: bool, domain: Any, field: Any, option: str) -> str:
        """An option value in words (known_label), else as is."""
        return self.known_label(ru, domain, field, option) or option

    def reads_out(self, st: Any) -> list[dict[str, Any]]:
        out = []
        for item in reads_of(st):
            field = C('actions_by_domain')[st.domain][item['do']].get('field')
            if item['kind'] == 'option':
                labels = {}
                for o in item['options']:
                    ru, en = (self.known_label(True, st.domain, field, o), self.known_label(False, st.domain, field, o))
                    if ru or en:
                        labels[o] = {LANGS[0]: ru or en, LANGS[1]: en or ru}
                item = {**item, 'labels': labels}
            elif item['kind'] in IS_NUMBERS:
                item = {**item, 'unit': PERCENTAGE if item['kind'] == 'percent' else dv.unit_of(st, item['attribute'])}
            out.append(item)
        return out

    def words(self, ru: bool, ph: dict[str, Any]) -> dict[str, Any]:
        """A refusal's placeholders in words: an action by its name (rules_do_…), a device by its name, options by HA's labels."""
        out = {k: self.who(v) if k in ('entity', 'other') else v for k, v in ph.items()}
        for k in ('do', 'what'):
            if isinstance((v := out.get(k)), str) and f'rules_do_{v}' in self.texts[LANGS[1]]:
                out[k] = self.say(f'rules_do_{v}', ru)
        if isinstance((opts := out.get('options')), (list, tuple)):
            out['options'] = ', '.join((self.option_label(ru, out.get('domain'), out.get('field'), str(o)) for o in opts)) or '—'
        return out

    def refusal(self, err: Invalid) -> dict[str, Any]:
        return {'code': err.code, 'key': err.key, 'message': self.say(err.key, False, **self.words(False, err.ph)), 'message_ru': self.say(err.key, True, **self.words(True, err.ph))}

async def said(hass: HomeAssistant, err: Invalid, ru: bool) -> dict[str, Any]:
    """A keyed refusal as a page shows it: its text in the request's language (never a key, an id), its code."""
    tx = Texts(hass)
    await tx.async_texts()
    return {'message': tx.say(err.key, ru, **tx.words(ru, err.ph)), 'code': err.code}

class RulesTextMixin:
    """Describes rules in the user's language."""

    def status(self, rid: str) -> str:
        rule, st = (self.rules[rid], self.st(rid))
        if not rule['enabled']:
            return 'off'
        for key in ('paused', 'fault'):
            if st[key]:
                return key
        if st['hold_until']:
            return 'hold'
        if scheduled(rule):
            return 'scheduled' if st.get('next_at') else 'no_next'
        if st['active']:
            return 'active'
        if st.get('cond'):
            return 'waiting'
        if rule.get('window') and st.get('no_data'):
            return 'no_data'
        return 'pending' if st['pend'] else 'normal'

    def threshold(self, rule: dict[str, Any], ru: bool) -> str:
        u, w = (rule['input'].get('unit'), rule['when'])
        if w in ('below', 'above'):
            return self.say(f'rules_thr_{w}', ru, x=fmt(rule['x'], u))
        if w in ('outside', 'between'):
            return self.say(f'rules_thr_{w}', ru, lo=fmt(rule['lo'], None), hi=fmt(rule['hi'], u))
        if w == IS:
            return self.say('rules_thr_is_not' if rule.get('not') else 'rules_thr_is', ru, values=self.is_words(rule['input'], rule[IS], ru))
        return self.say(f'rules_word_{w}', ru)

    def back_threshold(self, rule: dict[str, Any], ru: bool) -> str:
        """When an ACTIVE rule goes back — the threshold with the hysteresis (past_off), said."""
        u, w, y = (rule['input'].get('unit'), rule['when'], rule.get('y', 0))
        if w == 'below':
            return self.say('rules_thr_min', ru, x=fmt(rule['x'] + y, u))
        if w == 'above':
            return self.say('rules_thr_max', ru, x=fmt(rule['x'] - y, u))
        if w == 'outside':
            return self.say('rules_thr_between', ru, lo=fmt(rule['lo'] + y, None), hi=fmt(rule['hi'] - y, u))
        if w == 'between':
            return self.say('rules_thr_outside', ru, lo=fmt(rule['lo'] - y, None), hi=fmt(rule['hi'] + y, u))
        if w == IS:
            return self.say('rules_thr_is' if rule.get('not') else 'rules_thr_is_not', ru, values=self.is_words(rule['input'], rule[IS], ru))
        return self.say(f'rules_word_{(STATE_OFF if w == STATE_ON else STATE_ON)}', ru)

    def value_word(self, src: dict[str, Any], v: Any, ru: bool) -> str:
        """A value «равно» compares, in words: on / off, the device's option as HA names it, a number with its unit."""
        if v is None:
            return fmt(None, None)
        kind = src.get('kind')
        if kind == 'onoff' or (kind is None and v in (STATE_ON, STATE_OFF, STATE_OPEN, STATE_CLOSED)):
            return self.say(f'rules_word_{v}', ru)
        if kind in IS_NUMBERS or (kind is None and num(v) is not None):
            return fmt(num(v), src.get('unit'))
        spec = spec_of(src['entity_id'], src.get('do')) or {}
        return self.option_label(ru, src['entity_id'].split('.')[0], spec.get('field'), str(v))

    def is_words(self, src: dict[str, Any], values: list[Any], ru: bool) -> str:
        return f" {self.say('rules_word_or', ru)} ".join((self.value_word(src, v, ru) for v in values))

    def explain(self, rule: dict[str, Any], ru: bool) -> str:
        """The rule now, one sentence (RU / EN, translations rules_explain_…)."""
        st = self.st(rule['id'])
        state = self.status(rule['id'])
        if scheduled(rule):
            return self._explain_at(rule, state, ru)
        inp = rule['input']
        s = self.hass.states.get(inp['entity_id'])
        label = (s.attributes.get('friendly_name') if s else None) or inp['entity_id']
        if inp.get('forecast'):
            label = self.say('rules_forecast', ru, label=label)
        if rule['when'] in ('on', 'off'):
            value = self.say(f"rules_word_{(STATE_ON if st.get('value') else STATE_OFF)}", ru)
        elif rule['when'] == IS:
            value = self.value_word(inp, st.get('value'), ru)
        else:
            value = fmt(st.get('value'), inp.get('unit'))
        ph = {'label': label, 'value': value, 'threshold': self.threshold(rule, ru), 'back': self.back_threshold(rule, ru), 'time': self._hhmm(st['hold_until'] or st['since'])}
        if rule.get('window') and state in ('active', 'normal', 'no_data'):
            return self._explain_window(rule, state, ru, ph)
        if state == 'fault':
            key = 'rules_explain_fault_off' if purpose(rule['purpose']).get('safe') == 'off' else 'rules_explain_fault_wait'
            return self.say(key, ru, **ph, reason=self.say(f"rules_reason_{st['fault']}", ru))
        if state == 'hold':
            return self.say(f"rules_explain_hold_{st['hold_why']}", ru, **ph)
        if state == 'active' and self._interlock_off(rule):
            return self.say('rules_explain_interlocked', ru, **ph)
        if state == 'active' and (due := [x['due'] for x in self.todo if x['rule'] == rule['id'] and (not x['back'])]):
            return self.say('rules_explain_deferred', ru, **{**ph, 'time': self._hhmm(min(due))})
        if st['pend'] and state in ('pending', 'active'):
            need = (rule['back_min'] if st['active'] else rule['for_min']) * SECONDS_PER_MINUTE
            left = max(0, round((st['pend'] + need - dv.now()) / SECONDS_PER_MINUTE))
            return self.say('rules_explain_pending_off' if st['active'] else 'rules_explain_pending_on', ru, **ph, minutes=left)
        return self.say(f'rules_explain_{state}', ru, **ph)

    def _explain_at(self, rule: dict[str, Any], state: str, ru: bool) -> str:
        st = self.st(rule['id'])
        ph = {'time': self._hhmm(st['hold_until']), 'next': self._when_text(st.get('next_at'))}
        if state in ('off', 'paused'):
            head = self.say(f'rules_explain_{state}', ru, **ph)
        elif state == 'hold':
            head = self.say(f"rules_explain_hold_{st['hold_why']}", ru, **ph)
        else:
            head = self.say(f'rules_explain_{state}', ru, **ph)
        if st.get('last_result') in ('fired', 'skipped', 'missed'):
            head += ' ' + self.say(f"rules_explain_last_{st['last_result']}", ru, time=self._when_text(st['last_at']))
        return head

    def window_words(self, rule: dict[str, Any], ru: bool) -> str:
        """«среднее за день» / «daily mean»."""
        w = rule['window']
        return self.say(f"rules_window_{w['stat']}_{w['period']}", ru)

    def _explain_window(self, rule: dict[str, Any], state: str, ru: bool, ph: dict[str, Any]) -> str:
        st, w = (self.st(rule['id']), rule['window'])
        vals = (self.win.get(rule['id']) or {}).get('values') or []
        n = w['n_back'] if st['active'] else w['n']
        u, wh, y = (rule['input'].get('unit'), rule['when'], rule['y'])
        if wh in ('below', 'above'):
            back = self.say(f"rules_thr_{('above' if wh == 'below' else 'below')}", ru, x=fmt(rule['x'] + (y if wh == 'below' else -y), u))
        else:
            inner = wh == 'outside'
            back = self.say(f"rules_thr_{('between' if inner else 'outside')}", ru, lo=fmt(rule['lo'] + (y if inner else -y), None), hi=fmt(rule['hi'] - (y if inner else -y), u))
        ph = {**ph, 'back': back, 'stat': self.window_words(rule, ru), 'held': st.get('periods', 0), 'n': n, 'missing': sum((v is None for v in vals[-n:])) + max(0, n - len(vals))}
        return self.say(f'rules_explain_window_{state}', ru, **ph)

    def hint(self, pname: str) -> dict[str, str]:
        if not purpose(pname).get('hint'):
            return {}
        return {'hint': self.say(f'rules_hint_{pname}', True), 'hint_en': self.say(f'rules_hint_{pname}', False)}

    def display_name(self, rule: dict[str, Any], ru: bool) -> str:
        """The name in a language: a generated one from its recipe title + room (+ number), a typed one as typed."""
        gen = rule.get('name_gen')
        if not gen:
            return rule['name']
        parts = [self.say(f"rules_recipe_{gen['recipe']}", ru)]
        if gen.get('room'):
            parts.append(gen['room'])
        text = ' · '.join(parts)
        return f"{text} {gen['n']}"[:C('limits', 'name_len')] if gen.get('n', 1) > 1 else text[:C('limits', 'name_len')]

    def unique_name(self, rule: dict[str, Any]) -> None:
        """Two rules never share a name: a generated one gets the next number, a typed one «(2)»."""
        taken = {self.display_name(r, True).casefold() for k, r in self.rules.items() if k != rule['id']}
        gen = rule.get('name_gen')
        if gen:
            while self.display_name(rule, True).casefold() in taken and gen['n'] < C('limits', 'rules'):
                gen['n'] += 1
            rule['name'] = self.display_name(rule, self.ru)
            return
        base, k = (rule['name'], 1)
        while rule['name'].casefold() in taken:
            k += 1
            suffix = f' ({k})'
            rule['name'] = base[:C('limits', 'name_len') - len(suffix)] + suffix
