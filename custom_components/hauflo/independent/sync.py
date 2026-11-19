"""Writes and removes the alarm automations to match the rules."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.components.automation.config import async_validate_config_item
from homeassistant.components.script.config import async_validate_config_item as script_valid
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from .. import yamlfile
from ..alarm_form import automations as alarm_form_automations, common as alarm_form_common, groups as alarm_form_groups, scripts as alarm_form_scripts
from ..automations import common as automations_common, engine as automations_engine
from ..const import ACK_PREFIX, ALARM_PREFIX, CHATTER_PREFIX, DO_ACTION, OK_ACTION, OPEN_PREFIX
from ..guard import contained
from ..notify.common import RUN_SERVICES
from ..notify.engine import NotifyEngine
from ..storage import HaufloStore
from .build import automation_for
from .common import ICON, OPEN_ICON, PREFIX, alarm_groups, alarm_hold, back_after, ids, lit, of, outputs, say, texts
from .helpers import ack_entity, collection, drop_ours, empty, ensure_helper, helpers
_LOGGER = logging.getLogger(__name__)

async def async_sync(hass: HomeAssistant, engine: NotifyEngine, *, dry_run: bool=False, config: dict[str, Any] | None=None, migrate: bool=False) -> dict[str, Any]:
    """The engine's rules as automations + helpers in HA (one write of automations.yaml, then one reload); the engine is told which rules HA now runs (``async_set_native`` — open alarms move over)."""
    english = engine.english(config)
    lang = 'en' if english else 'ru'
    out = empty()
    rules = [r for r in (config or engine.config).get('rules') or [] if r.get('enabled') and r.get('sources')]
    out['rules'] = len(rules)
    stored: dict[str, dict[str, Any]] = dict(engine.native)

    def to_engine(rule: dict[str, Any], reason: str) -> None:
        out['engine'].append({'id': rule['id'], 'title': rule['title'], 'reason': reason})
    for r in [r for r in rules if r.get('policy')]:
        to_engine(r, 'policy')
    rules = [r for r in rules if not r.get('policy')]
    t = await texts(hass, english)
    if 'automation' not in hass.config.components:
        for r in rules:
            to_engine(r, 'no_automation')
        if rules:
            out['error'], out['error_code'] = (t['no_automation'], 'no_automation')
        if not dry_run and stored:
            await engine.async_set_native({})
        return out
    locked = bool(engine.locked is not None and engine.locked())
    coll = None if dry_run else await helpers(hass)
    if not dry_run and coll is None:
        for r in rules:
            if r['id'] not in stored:
                to_engine(r, 'helper')
        return out
    colls = {'input_boolean': coll, 'input_text': None if dry_run or 'input_text' not in hass.config.components else await collection(hass, 'input_text')}
    names = {r['action']['entity_id']: engine.name_of(r['action']['entity_id']) for r in rules if r.get('action')}
    file = automations_engine.Automations(hass, lambda _eid: False)
    digest_of = automations_common.digest
    flat = 'script' in hass.config.components
    scripts: dict[str, Any] = {}
    sbefore: str | None = None
    if flat:
        try:
            scripts, sbefore = await alarm_form_scripts.read_scripts(hass)
        except alarm_form_common.NotPlainScripts:
            flat = False
            out['warnings'].append({'code': 'scripts_not_plain', 'title': alarm_form_common.SCRIPTS})
    shared_store = HaufloStore(hass, alarm_form_common.SHARED_STORE)
    shared_rec: dict[str, str] = dict(await shared_store.async_load() or {})
    async with automations_common.FILE_LOCK:
        try:
            data, before = await file.read_file()
        except automations_common.NotPlain:
            out['error'], out['error_code'] = (automations_common.NotPlain.EN if english else automations_common.NotPlain.RU, 'not_plain')
            for r in rules:
                if r['id'] not in stored:
                    to_engine(r, 'not_plain')
            return out
        current = {a.get('id'): a for a in data if isinstance(a, dict)}
        native: dict[str, dict[str, Any]] = {}
        writes: dict[str, dict[str, Any] | None] = {}
        script_writes: dict[str, dict[str, Any] | None] = {}
        drop: list[str] = []
        made: dict[str, list[str]] = {}
        made_groups: dict[str, str] = {}
        rule_of: dict[str, dict[str, Any]] = {}
        flat_now: list[str] = []

        def flat_objects(rec: dict[str, Any] | None) -> tuple[Any, Any, Any]:
            rec = rec or {}
            return (current.get(rec.get('automation')), current.get(rec.get('back')) if rec.get('back') else None, scripts.get(rec.get('run_script')) if rec.get('run_script') else None)
        for r in rules:
            the = ids(r['id'])
            aid = the['automation']
            slug = aid.removeprefix(ALARM_PREFIX)
            cur, rec = (current.get(aid), stored.get(r['id']))
            if cur is None and locked:
                to_engine(r, 'locked')
                continue
            rule_of[aid] = r
            targets = engine.targets(r, config)
            was_flat = bool(rec and rec.get('form') == alarm_form_common.form())
            old_form = cur is not None and (not was_flat)
            if was_flat and (not flat):
                out['kept_edited'].append(r['title'])
                native[r['id']] = dict(rec)
                continue
            if was_flat:
                if cur is not None and alarm_form_automations.digest(*flat_objects(rec)) != rec.get('digest'):
                    out['kept_edited'].append(r['title'])
                    native[r['id']] = dict(rec)
                    continue
            elif cur is not None and (not (rec and digest_of(cur) == rec.get('digest'))) and (digest_of(cur) != digest_of(automation_for(r, targets, t, ack_entity(hass, the['helper']), ack_entity(hass, the['open']), names=names))):
                out['kept_edited'].append(r['title'])
                native[r['id']] = dict(rec) if rec else {'automation': aid, 'helper': ack_entity(hass, the['helper']), 'digest': '', 'ack': the['ack'], 'run': the['run'], 'lang': lang}
                continue
            if old_form and flat and (not migrate):
                out['migrate'].append(r['title'])
            if flat and (not old_form or migrate):
                if await _flat_rule(hass, r, targets, t, the, slug, cur, rec, coll, names, engine, lang, out, native, writes, script_writes, made, made_groups, current, scripts, drop, to_engine, async_validate_config_item):
                    flat_now.append(r['id'])
                continue
            if coll is not None:
                made[r['id']] = [o for o in (the['helper'], the['open']) if o not in coll.data]
            try:
                if coll is not None:
                    ack = await ensure_helper(hass, coll, the['helper'], say(t, 'helper', title=r['title']), ICON)
                    opn = await ensure_helper(hass, coll, the['open'], say(t, 'open_helper', title=r['title']), OPEN_ICON)
                else:
                    ack, opn = (ack_entity(hass, the['helper']), ack_entity(hass, the['open']))
            except Exception:
                _LOGGER.exception('Hauflo: helpers for %s not made', r['id'])
                if cur is not None and rec:
                    native[r['id']] = rec
                    out['warnings'].append({'code': 'helper', 'title': r['title']})
                else:
                    to_engine(r, 'helper')
                    drop.extend(made.pop(r['id'], []))
                continue
            cfg = automation_for(r, targets, t, ack, opn, names=names)
            digest = digest_of(cfg)
            if cur is not None and digest_of(cur) == digest:
                state = 'unchanged'
            else:
                try:
                    await async_validate_config_item(hass, aid, cfg)
                except Exception as err:
                    _LOGGER.warning('Hauflo: alarm automation %s refused by HA: %s', aid, err)
                    if cur is not None and rec:
                        native[r['id']] = rec
                        out['warnings'].append({'code': 'invalid', 'title': r['title']})
                    else:
                        to_engine(r, 'invalid')
                        drop.extend(made.pop(r['id'], []))
                    continue
                state = 'updated' if cur is not None else 'restored' if rec else 'added'
                writes[aid] = cfg
            out[state].append(r['title'])
            native[r['id']] = {'automation': aid, 'helper': ack, 'open': opn, 'digest': digest, 'ack': the['ack'], 'run': the['run'], 'lang': lang}
            drop.append(the['chatter'])
        wanted = {ids(r['id'])['automation'] for r in rules}
        by_aid = {rec.get('automation'): rec for rec in stored.values()}
        not_rules = {rec['back'] for rec in [*stored.values(), *native.values()] if rec.get('back')} | set(alarm_form_common.SHARED_IDS)

        def helpers_of(aid: str) -> list[str]:
            return [str((by_aid.get(aid) or {}).get('helper') or '').partition('.')[2] or of(aid, ACK_PREFIX), of(aid, OPEN_PREFIX), of(aid, CHATTER_PREFIX)]
        gone_groups: list[str] = []
        for aid, cur in current.items():
            if not isinstance(aid, str) or not aid.startswith(ALARM_PREFIX) or aid in wanted or (aid in not_rules):
                continue
            rec = by_aid.get(aid)
            flat_rec = bool(rec and rec.get('form') == alarm_form_common.form())
            if rec is None or (alarm_form_automations.digest(*flat_objects(rec)) if flat_rec else digest_of(cur)) != rec.get('digest'):
                out['warnings'].append({'code': 'edited_left', 'title': str(cur.get('alias') or aid)})
                continue
            writes[aid] = None
            out['removed'].append(str(cur.get('alias') or aid))
            drop += helpers_of(aid)
            if flat_rec:
                if rec.get('back') in current:
                    writes[rec['back']] = None
                if rec.get('run_script') in scripts:
                    script_writes[rec['run_script']] = None
                gone_groups.append(aid)
        need = any((rec.get('form') == alarm_form_common.form() for rec in native.values()))
        need_button = any((rec.get('run_script') for rec in native.values() if rec.get('form') == alarm_form_common.form()))
        shared_new: dict[str, str] = {}
        if flat:
            wanted_shared: list[tuple[str, str, Any]] = [('script', alarm_form_common.SCRIPT, alarm_form_scripts.push_script(t) if need else None), ('automation', alarm_form_common.ACCEPT_ID, alarm_form_automations.accept_automation(t) if need else None), ('automation', alarm_form_common.BUTTON_ID, alarm_form_automations.button_automation(t) if need_button else None)]
            for kind, key, cfg in wanted_shared:
                cur = (scripts if kind == 'script' else current).get(key)
                if cur is not None and alarm_form_automations.digest(cur) not in (shared_rec.get(key), alarm_form_automations.digest(cfg) if cfg else None):
                    out['warnings'].append({'code': 'shared_edited', 'title': str(cur.get('alias') or key)})
                    continue
                if cfg is None:
                    if cur is not None:
                        (script_writes if kind == 'script' else writes)[key] = None
                    continue
                shared_new[key] = alarm_form_automations.digest(cfg)
                if cur is not None and alarm_form_automations.digest(cur) == shared_new[key]:
                    continue
                try:
                    await (script_valid if kind == 'script' else async_validate_config_item)(hass, key, cfg)
                except Exception as err:
                    _LOGGER.warning('Hauflo: shared alarm object %s refused by HA: %s', key, err)
                    out['warnings'].append({'code': 'invalid', 'title': key})
                    shared_new.pop(key, None)
                    continue
                (script_writes if kind == 'script' else writes)[key] = cfg
        if (memories := colls['input_text']) is not None:
            drop += [o for o in memories.data if str(o).startswith(CHATTER_PREFIX) and ALARM_PREFIX + str(o).removeprefix(CHATTER_PREFIX) not in current and (o not in drop)]
        script_failed = False
        if script_writes and (not dry_run):
            new_scripts = {k: v for k, v in {**scripts, **script_writes}.items() if v is not None}
            kept_scripts: list[str] = []
            try:
                await alarm_form_scripts.write_scripts(hass, new_scripts, sbefore, alarm_form_scripts.rebase_scripts(script_writes, scripts, kept_scripts))
                await hass.services.async_call('script', 'reload', {}, blocking=True)
            except Exception as err:
                _LOGGER.exception('Hauflo: alarm scripts not written: %s', err)
                script_failed = True
            if need and (not hass.services.has_service('script', alarm_form_common.SCRIPT)):
                _LOGGER.warning("Hauflo: script.%s not loaded — scripts.yaml isn't the scripts HA loads", alarm_form_common.SCRIPT)
                script_failed = True
            for key in kept_scripts:
                out['warnings'].append({'code': 'edited_left', 'title': key})
        if script_failed:
            out['error'], out['error_code'] = (t['script_failed'], 'script')
            for rule_id in flat_now:
                r = next((x for x in rules if x['id'] == rule_id))
                the = ids(rule_id)
                for key in ('added', 'updated', 'restored', 'unchanged'):
                    if r['title'] in out[key]:
                        out[key].remove(r['title'])
                writes.pop(the['automation'], None)
                writes.pop(alarm_form_common.back_id(the['automation']), None)
                drop = [o for o in drop if o not in (the['open'], the['chatter'])]
                if (rec := stored.get(rule_id)) is not None:
                    native[rule_id] = rec
                else:
                    native.pop(rule_id, None)
                    to_engine(r, 'script')
                    drop.extend(made.get(rule_id, []))
                    if rule_id in made_groups:
                        gone_groups.append(made_groups[rule_id])
            for key in alarm_form_common.SHARED_IDS:
                writes.pop(key, None)
            shared_new = dict(shared_rec)
        if writes and (not dry_run):
            kept = [writes[a.get('id')] if isinstance(a, dict) and a.get('id') in writes else a for a in data]
            new = [a for a in kept if a is not None] + [c for a, c in writes.items() if c is not None and a not in current]
            theirs: list[str] = []
            same = lambda a, b: digest_of(a) == digest_of(b)
            try:
                await file.write_file(new, before, yamlfile.rebase(writes, current, same, theirs))
            except Exception as err:
                _LOGGER.exception('Hauflo: alarm automations not written: %s', err)
                out['error'], out['error_code'] = (t['write_failed'], 'write')
                for r in rules:
                    if r['id'] not in stored:
                        to_engine(r, 'write')
                        native.pop(r['id'], None)
                        for object_id in made.get(r['id'], []):
                            await drop_ours(hass, colls, object_id)
                        if r['id'] in made_groups:
                            await alarm_form_groups.drop_group(hass, made_groups[r['id']])
                return out
            for aid in theirs:
                writes.pop(aid, None)
                if aid in not_rules or aid.endswith(alarm_form_common.BACK):
                    continue
                if (r := rule_of.get(aid)) is None:
                    gone = set(helpers_of(aid))
                    drop = [o for o in drop if o not in gone]
                    gone_groups = [g for g in gone_groups if g != aid]
                    out['removed'] = [x for x in out['removed'] if x != str((current.get(aid) or {}).get('alias') or aid)]
                    out['warnings'].append({'code': 'edited_left', 'title': str((current.get(aid) or {}).get('alias') or aid)})
                    continue
                drop = [o for o in drop if o not in (ids(r['id'])['chatter'], ids(r['id'])['open'])]
                for key in ('added', 'updated', 'restored'):
                    if r['title'] in out[key]:
                        out[key].remove(r['title'])
                if (rec := stored.get(r['id'])) is not None:
                    out['kept_edited'].append(r['title'])
                    native[r['id']] = rec
                else:
                    native.pop(r['id'], None)
                    to_engine(r, 'write')
                    drop.extend(made.get(r['id'], []))
                    if r['id'] in made_groups:
                        gone_groups.append(made_groups[r['id']])
    if not dry_run:
        reg = er.async_get(hass)
        if writes:
            await hass.services.async_call('automation', 'reload', {}, blocking=True)
            for aid, cfg in writes.items():
                if cfg is None and (eid := reg.async_get_entity_id('automation', 'automation', aid)):
                    reg.async_remove(eid)
        if script_writes and (not script_failed):
            for key, cfg in script_writes.items():
                if cfg is None and (eid := reg.async_get_entity_id('script', 'script', key)):
                    reg.async_remove(eid)
        for object_id in drop:
            await drop_ours(hass, colls, object_id)
        for object_id in gone_groups:
            await alarm_form_groups.drop_group(hass, object_id)
        if flat and shared_new != shared_rec:
            await shared_store.async_save(shared_new)
        if (flat_recs := [rec for rec in native.values() if rec.get('form') == alarm_form_common.form()]):
            with contained(_LOGGER, 'Hauflo: alarm category not set', level=logging.ERROR):
                alarm_form_groups.categorize(hass, t['category'], alarm_form_groups.category_members(hass, flat_recs))
        await engine.async_set_native(native)
    if (old := [str(a.get('alias') or a.get('id')) for a in data if isinstance(a, dict) and str(a.get('id') or '').startswith(PREFIX)]):
        out['warnings'].append({'code': 'double', 'title': ', '.join(old)})
    return out

async def _flat_rule(hass: HomeAssistant, r: dict[str, Any], targets: list[dict[str, Any]], t: dict[str, str], the: dict[str, str], slug: str, cur: Any, rec: dict[str, Any] | None, coll: Any, names: dict[str, str], engine: NotifyEngine, lang: str, out: dict[str, Any], native: dict[str, Any], writes: dict[str, Any], script_writes: dict[str, Any], made: dict[str, list[str]], made_groups: dict[str, str], current: dict[str, Any], scripts: dict[str, Any], drop: list[str], to_engine: Any, validate: Any) -> bool:
    aid = the['automation']
    back_key = alarm_form_common.back_id(aid)
    run_key = alarm_form_common.run_key(slug)
    was_flat = bool(rec and rec.get('form') == alarm_form_common.form())
    if coll is not None:
        made[r['id']] = [o for o in (the['helper'],) if o not in coll.data]
    try:
        if coll is not None:
            ack = await ensure_helper(hass, coll, the['helper'], say(t, 'helper', title=r['title']), ICON)
            if aid not in alarm_form_automations.our_entries(hass):
                made_groups[r['id']] = aid
            group = await alarm_form_groups.ensure_group(hass, aid, r, say(t, 'group', title=r['title']))
        else:
            ack, group = (ack_entity(hass, the['helper']), alarm_form_groups.group_entity(hass, aid))
    except Exception:
        _LOGGER.exception('Hauflo: helpers for %s not made', r['id'])
        if cur is not None and rec:
            native[r['id']] = rec
            out['warnings'].append({'code': 'helper', 'title': r['title']})
        else:
            to_engine(r, 'helper')
            drop.extend(made.pop(r['id'], []))
            if r['id'] in made_groups:
                await alarm_form_groups.drop_group(hass, made_groups.pop(r['id']))
        return False
    act = r.get('action') if (r.get('action') or {}).get('service') in RUN_SERVICES else None
    button_text = button = None
    if act:
        button_text = say(t, 'close' if act['service'] == 'valve.close_valve' else 'off', name=names.get(act['entity_id']) or act['entity_id'])[:60]
        button = lit(button_text)
    cfgs = alarm_form_automations.rule_configs(r, targets, t, ack, group, slug=slug, aid=aid, hold_groups=alarm_groups(r, list(r['sources'])), hold=alarm_hold(r), back_s=back_after(r), outputs_on=outputs(r, 'on_start'), outputs_off=outputs(r, 'on_end'), button=button, button_text=button_text, act=act, title=lit(r['title']), names={e: engine.name_of(e) for e in r['sources']}, who=', '.join(dict.fromkeys((engine.name_of(p['person']) if p.get('person') and hass.states.get(p['person']) else str(p.get('name') or p['service']) for p in targets))))
    digest = alarm_form_automations.digest(cfgs['automation'], cfgs['back'], cfgs['run'])
    if was_flat and cur is not None and (alarm_form_automations.digest(cur, current.get(back_key), scripts.get(run_key)) == digest):
        state = 'unchanged'
    else:
        try:
            await validate(hass, aid, cfgs['automation'])
            if cfgs['back']:
                await validate(hass, back_key, cfgs['back'])
            if cfgs['run']:
                await script_valid(hass, run_key, cfgs['run'])
        except Exception as err:
            _LOGGER.warning('Hauflo: alarm automation %s refused by HA: %s', aid, err)
            if cur is not None and rec:
                native[r['id']] = rec
                out['warnings'].append({'code': 'invalid', 'title': r['title']})
            else:
                to_engine(r, 'invalid')
                drop.extend(made.pop(r['id'], []))
                if r['id'] in made_groups:
                    await alarm_form_groups.drop_group(hass, made_groups.pop(r['id']))
            return False
        state = 'updated' if cur is not None else 'restored' if rec else 'added'
        writes[aid] = cfgs['automation']
        if cfgs['back'] is not None or back_key in current:
            writes[back_key] = cfgs['back']
        if cfgs['run'] is not None or run_key in scripts:
            script_writes[run_key] = cfgs['run']
    out[state].append(r['title'])
    native[r['id']] = {'automation': aid, 'helper': ack, 'group': group, 'back': back_key if cfgs['back'] else None, 'run_script': run_key if cfgs['run'] else None, 'digest': digest, 'ack': OK_ACTION + slug.upper(), 'run': DO_ACTION + slug.upper(), 'lang': lang, 'form': alarm_form_common.form()}
    drop += [the['open'], the['chatter']]
    return True
