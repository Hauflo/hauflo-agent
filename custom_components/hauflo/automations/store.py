"""Reads and writes the automations file and its helpers."""
from __future__ import annotations
import asyncio
from collections.abc import Callable
import logging
import secrets
import time
from typing import Any
from homeassistant.components.automation.config import async_validate_config_item
from homeassistant.helpers import entity_registry as er, translation
from .. import yamlfile
from ..const import DOMAIN
from ..guard import contained
from ..rules import const as rules_const
from .common import AUTOMATIONS, BACKUP, FILE_LOCK, HELPER, ID, KEEP_BACKUPS, MAX_RULES, MOVED, PREFIX, RUN_VAR, Changed, Invalid, NotPlain, bad, digest
_LOGGER = logging.getLogger(__name__)

class AutomationsStoreMixin:
    """Keeps the automations in HA's automations file."""

    async def _helper(self, data: dict[str, Any], spec: dict[str, Any]) -> str:
        helpers = data.setdefault('helpers', {})
        sig = f"{spec['stat']}:{','.join(spec['sensors'])}"
        reg = er.async_get(self.hass)
        if (h := helpers.get(sig)) and self.hass.config_entries.async_get_entry(h['entry_id']) and (eid := reg.async_get_entity_id('sensor', HELPER, h['entry_id'])):
            return eid
        ru = str(self.hass.config.language or '').startswith('ru')
        flow = await self.hass.config_entries.flow.async_init(HELPER, context={'source': 'user'})
        res = await self.hass.config_entries.flow.async_configure(flow['flow_id'], {'name': f"hauflo_{spec['stat']}_{secrets.token_hex(4)}", 'entity_ids': list(spec['sensors']), 'type': spec['stat'], 'round_digits': 1 if spec['kind'] == 'temperature' else 0})
        entry = res.get('result')
        if entry is None:
            _LOGGER.warning('Hauflo automations: no %s helper (%s)', spec['stat'], res.get('errors') or res.get('reason'))
            raise Invalid('helper_failed')
        eid = None
        for _ in range(50):
            if (eid := reg.async_get_entity_id('sensor', HELPER, entry.entry_id)):
                break
            await asyncio.sleep(0.1)
        if eid is None:
            await self.hass.config_entries.async_remove(entry.entry_id)
            raise Invalid('helper_failed')
        title = await self._helper_title(spec, ru)
        self.hass.config_entries.async_update_entry(entry, title=title)
        reg.async_update_entity(eid, name=title)
        helpers[sig] = {'entry_id': entry.entry_id, 'entity_id': eid}
        return eid

    async def _helper_title(self, spec: dict[str, Any], ru: bool) -> str:
        pre = f'component.{DOMAIN}.exceptions.'
        tr: dict[str, str] = {}
        for lang in ('ru', 'en') if ru else ('en',):
            got = await translation.async_get_translations(self.hass, lang, 'exceptions', [DOMAIN])
            tr = {**{k: v for k, v in got.items() if k.startswith(pre)}, **tr}
        say = lambda key: tr.get(f'{pre}{key}.message', key)
        try:
            return say('automations_helper').format(stat=say(f"automations_stat_{spec['stat']}"), n=len(spec['sensors']))
        except (KeyError, IndexError, ValueError):
            return f"Hauflo · {spec['stat']}"

    async def _drop_unused_helpers(self, data: dict[str, Any]) -> None:
        used = {spec.get('entity') for rule in data['rules'].values() for spec in self._readings(rule)}
        for sig, h in list(data.get('helpers', {}).items()):
            if h.get('entity_id') in used:
                continue
            if self.hass.config_entries.async_get_entry(h['entry_id']):
                await self.hass.config_entries.async_remove(h['entry_id'])
            del data['helpers'][sig]

    @staticmethod
    def _plain(text: str) -> list[Any]:
        return yamlfile.parse(text, NotPlain)

    async def read_file(self) -> tuple[list[dict[str, Any]], str | None]:
        return await yamlfile.async_read(self.hass, self.hass.config.path(AUTOMATIONS), NotPlain)

    async def write_file(self, data: list[dict[str, Any]], before: str | None, redo: Callable[[list[Any]], list[Any]] | None=None) -> list[Any]:
        """One atomic write, the replaced text kept as a backup copy; the file changed since ``before`` (HA's editor
        saved meanwhile) — ``redo`` makes the change again on it (none — NotPlain, nothing written)."""
        return await yamlfile.async_write(self.hass, self.hass.config.path(AUTOMATIONS), self.hass.config.path(BACKUP), KEEP_BACKUPS, data, before, NotPlain, redo)

    def entity_of(self, rule_id: str) -> str | None:
        return er.async_get(self.hass).async_get_entity_id('automation', 'automation', rule_id)

    async def _put(self, rule_id: str, cfg: dict[str, Any] | None) -> None:
        if not ID.fullmatch(rule_id):
            raise bad('id')
        if cfg is not None:
            await async_validate_config_item(self.hass, rule_id, cfg)
        async with FILE_LOCK:
            data, before = await self.read_file()

            def put(rows: list[Any]) -> list[Any]:
                out: list[Any] = []
                done = cfg is None
                for a in rows:
                    if not (isinstance(a, dict) and a.get('id') == rule_id):
                        out.append(a)
                    elif not done:
                        out.append(cfg)
                        done = True
                return out if done else [*out, cfg]
            await self.write_file(put(data), before, put)
        if cfg is not None:
            await self.hass.services.async_call('automation', 'reload', {'id': rule_id}, blocking=True)
        elif (eid := self.entity_of(rule_id)):
            er.async_get(self.hass).async_remove(eid)

    async def load(self) -> dict[str, Any]:
        await self.texts()
        data = await self.store.async_load()
        data = data if isinstance(data, dict) and isinstance(data.get('rules'), dict) else {'rules': {}}
        if (moved := [rid for rid, rule in data['rules'].items() if isinstance(rule, dict) and self._upgrade(rid, rule)]):
            data[MOVED] = moved
        return data

    async def _save(self, data: dict[str, Any]) -> None:
        data.pop(MOVED, None)
        await self.store.async_save(data)

    def _upgrade(self, rule_id: str, rule: dict[str, Any]) -> bool:
        w = rule.get('when') if isinstance(rule.get('when'), dict) else {}
        acts = rule.get('actions') if isinstance(rule.get('actions'), list) else []
        if not ('at' in w or any((isinstance(a, dict) and 'target' not in a for a in acts))):
            return False
        try:
            if not rule.get('hash'):
                rule['hash'] = digest(self.automation(rule_id, rule))
            new = [a if 'target' in a else self._legacy(a) for a in acts]
        except Exception:
            _LOGGER.warning("Hauflo: a stored rule HA's states no longer explain: kept as it was", exc_info=True)
            return False
        if 'at' in w:
            w['times'] = [w.pop('at')]
        rule['actions'] = new
        return True

    async def _migrate(self, data: dict[str, Any]) -> None:
        moved = data.get(MOVED) or []
        file = []
        with contained(_LOGGER, 'Hauflo automations: automations.yaml not migrated — left alone', expect=(NotPlain, OSError)):
            file, _ = await self.read_file()
        there = {a.get('id') for a in file if isinstance(a, dict)}
        edited = await self.changed(data)
        for rule_id in moved:
            rule = data['rules'][rule_id]
            if rule_id not in there or rule_id in edited:
                continue
            with contained(_LOGGER, 'Hauflo automations: %s kept as it was (%s)', rule_id, err=True):
                cfg = self.automation(rule_id, rule)
                if digest(cfg) != rule.get('hash'):
                    await self._put(rule_id, cfg)
                    rule['hash'] = digest(cfg)
        await self._save(data)

    async def changed(self, data: dict[str, Any]) -> set[str]:
        """Hauflo's rules someone edited in HA's own automation editor: what's in automations.yaml is not what Hauflo
        wrote (its digest; for a rule from before the digest — what Hauflo would write from the form)."""
        try:
            file, _ = await self.read_file()
        except Exception:
            _LOGGER.warning('Hauflo: an unreadable file: nothing said changed, writes refuse on their own', exc_info=True)
            return set()
        now = {a.get('id'): a for a in file if isinstance(a, dict)}
        out = set()
        for rule_id, rule in data['rules'].items():
            if (cur := now.get(rule_id)) is None:
                continue
            want = rule.get('hash') or digest(self.automation(rule_id, rule))
            if digest(cur) != want:
                out.add(rule_id)
        return out

    async def save(self, raw: Any, user: str | None, force: bool=False, lang: str | None=None) -> dict[str, Any]:
        async with self.lock:
            data = await self.load()
            rule_id = raw.get('id') if isinstance(raw, dict) else None
            if rule_id is not None:
                if not isinstance(rule_id, str) or not ID.fullmatch(rule_id):
                    raise bad('id')
                if rule_id not in data['rules']:
                    raise KeyError(rule_id)
            elif len(data['rules']) >= MAX_RULES:
                raise Invalid('bad_rules_many', n=MAX_RULES)
            if rule_id is not None and (not force) and (rule_id in await self.changed(data)):
                raise Changed(rule_id)
            rule = self.validate(raw, data['rules'].get(rule_id) if rule_id else None)
            rule_id = rule_id or PREFIX + secrets.token_hex(6)
            rule['by'], rule['at'] = (user, time.time())
            rule['lang'] = lang if lang in rules_const.LANGS else rules_const.LANGS[0] if self._ru({}) else rules_const.LANGS[1]
            rule['alias'] = await self._unique_alias(rule_id, rule, data)
            await self._resolve(rule, data)
            cfg = self.automation(rule_id, rule)
            try:
                await self._put(rule_id, cfg)
            except Exception:
                await self._drop_unused_helpers(data)
                await self._save(data)
                raise
            rule['hash'] = digest(cfg)
            data['rules'][rule_id] = rule
            await self._drop_unused_helpers(data)
            await self._save(data)
        await self._apply_enabled(rule_id, rule['enabled'])
        out = {'id': rule_id, **{k: v for k, v in rule.items() if k != 'hash'}}
        if rule['when'].get('event') in ('sunrise', 'sunset') and (not self.location()['set']):
            out['warning'] = 'no_location'
        return out

    async def _apply_enabled(self, rule_id: str, enabled: bool) -> None:
        for _ in range(20):
            if (eid := self.entity_of(rule_id)):
                await self.hass.services.async_call('automation', 'turn_on' if enabled else 'turn_off', {'entity_id': eid}, blocking=True)
                return
            await asyncio.sleep(0.1)

    async def set_enabled(self, rule_id: Any, enabled: Any) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise bad('enabled')
        async with self.lock:
            data = await self.load()
            rule = self._own(data, rule_id)
            rule['enabled'] = enabled
            await self._save(data)
        await self._apply_enabled(rule_id, enabled)
        return {'id': rule_id, **{k: v for k, v in rule.items() if k != 'hash'}}

    @staticmethod
    def _own(data: dict[str, Any], rule_id: Any) -> dict[str, Any]:
        if not isinstance(rule_id, str) or not ID.fullmatch(rule_id):
            raise bad('id')
        if rule_id not in data['rules']:
            raise KeyError(rule_id)
        return data['rules'][rule_id]

    async def delete(self, rule_id: Any) -> None:
        async with self.lock:
            data = await self.load()
            self._own(data, rule_id)
            await self._put(rule_id, None)
            del data['rules'][rule_id]
            await self._drop_unused_helpers(data)
            await self._save(data)

    async def run(self, rule_id: Any) -> dict[str, Any]:
        """«Проверить сейчас»: HA runs the rule's own automation once, without the day check."""
        data = await self.load()
        self._own(data, rule_id)
        eid = self.entity_of(rule_id)
        if eid is None or self.hass.states.get(eid) is None:
            raise LookupError('automations_not_in_ha')
        await self.hass.services.async_call('automation', 'trigger', {'entity_id': eid, 'skip_condition': True, 'variables': {RUN_VAR: True}}, blocking=False)
        return {'id': rule_id, 'entity_id': eid}
