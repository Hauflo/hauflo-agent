"""Service, export and summary of the cloud-independent alarm automations."""
from __future__ import annotations
import logging
from typing import Any
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_register_admin_service
from .. import issues, keep
from ..alarm_form import common as alarm_form_common, groups as alarm_form_groups, scripts as alarm_form_scripts
from ..automations import common as automations_common, engine as automations_engine
from ..const import ALARM_PREFIX, DOMAIN, NATIVE_STORE, RUN_PREFIX, site_ru
from ..notify.engine import NotifyEngine
from ..storage import HaufloStore
from ..theme import asset_tag
from .common import HELPERS_FEATURE, KIND, NOTE, SERVICE, texts
from .helpers import collection, drop_helper, empty
from .sync import async_sync
_LOGGER = logging.getLogger(__name__)

async def async_remove_all(hass: HomeAssistant, engine: NotifyEngine | None) -> list[str]:
    """«Убрать аварии Hauflo» (hauflo.remove_alarms, HA's admin): every hauflo_alarm_* automation, hauflo_ack_* /
    hauflo_open_* helper (and a
    hauflo_chatter_* of before 0.31) goes — edited ones too (the admin asked for exactly that); the agent's engine takes
    the rules back. Returns the
    aliases removed. NotPlain when automations.yaml isn't plain (nothing removed)."""
    file = automations_engine.Automations(hass, lambda _eid: False)
    async with automations_common.FILE_LOCK:
        data, before = await file.read_file()
        ours = [a for a in data if isinstance(a, dict) and str(a.get('id') or '').startswith(ALARM_PREFIX)]
        if ours:

            def others(rows: list[Any]) -> list[Any]:
                return [a for a in rows if not (isinstance(a, dict) and str(a.get('id') or '').startswith(ALARM_PREFIX))]
            await file.write_file(others(data), before, others)
    if ours:
        await hass.services.async_call('automation', 'reload', {}, blocking=True)
        reg = er.async_get(hass)
        for a in ours:
            if (eid := reg.async_get_entity_id('automation', 'automation', str(a['id']))):
                reg.async_remove(eid)
    gone_scripts: list[str] = []
    if 'script' in hass.config.components:

        def mine(key: Any) -> bool:
            return key == alarm_form_common.SCRIPT or str(key).startswith(RUN_PREFIX)
        try:
            scripts, sbefore = await alarm_form_scripts.read_scripts(hass)
        except alarm_form_common.NotPlainScripts:
            _LOGGER.warning("Hauflo: scripts.yaml isn't plain — the alarm scripts stay")
            scripts, sbefore = ({}, None)
        if (gone := {k: v for k, v in scripts.items() if mine(k)}):
            await alarm_form_scripts.write_scripts(hass, {k: v for k, v in scripts.items() if not mine(k)}, sbefore, lambda fresh: {k: v for k, v in fresh.items() if not mine(k)})
            await hass.services.async_call('script', 'reload', {}, blocking=True)
            reg = er.async_get(hass)
            for key, cfg in gone.items():
                gone_scripts.append(str((cfg or {}).get('alias') or key))
                if (eid := reg.async_get_entity_id('script', 'script', key)):
                    reg.async_remove(eid)
    await alarm_form_groups.drop_all_groups(hass)
    await HaufloStore(hass, alarm_form_common.SHARED_STORE).async_save({})
    for domain, prefix in KIND:
        coll = await collection(hass, domain)
        if coll is None and domain in hass.config.components:
            issues.degrade(hass, HELPERS_FEATURE, LookupError(f'{domain} helper commands not found'), issues.HA)
        for object_id in [k for k in (coll.data if coll is not None else {}) if str(k).startswith(prefix)]:
            await drop_helper(hass, coll, object_id)
    if engine is not None:
        await engine.async_set_native({})
    return [str(a.get('alias') or a.get('id')) for a in ours] + gone_scripts

async def written_english(hass: HomeAssistant, engine: NotifyEngine | None) -> bool:
    """One language for everything said about the alarm automations: the one they were written in (the project's, kept
    with their records), else the engine's (the project's or HA's) — never HA's beside a project's other (5B e2e
    D-4)."""
    stored = engine.native if engine is not None else await HaufloStore(hass, NATIVE_STORE).async_load()
    lang = next((r['lang'] for r in (stored or {}).values() if isinstance(r, dict) and r.get('lang') in ('ru', 'en')), None)
    if lang:
        return lang == 'en'
    if engine is not None:
        return engine.english()
    return not site_ru(hass.config.language)

def async_setup_service(hass: HomeAssistant, engine_of: Any) -> None:
    """``hauflo.remove_alarms`` — HA's admin in the house only (never the agent's own tunnel user, an admin too).
    Registered once for HA's whole run (integration setup); engine_of() raises «not_loaded» without a loaded entry."""

    async def handle(call: ServiceCall) -> None:
        user = await hass.auth.async_get_user(call.context.user_id) if call.context.user_id else None
        if user is None or user.system_generated or (not user.is_admin):
            raise Unauthorized(context=call.context)
        engine = engine_of()
        try:
            await async_remove_all(hass, engine)
        except automations_common.NotPlain as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key='alarm_native_not_plain') from err
        except OSError as err:
            _LOGGER.error('Hauflo: alarms not removed: %s', err)
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key='alarm_native_write_failed') from err
        persistent_notification.async_dismiss(hass, NOTE)
    async_register_admin_service(hass, DOMAIN, SERVICE, handle)

async def async_removed(hass: HomeAssistant) -> None:
    """    them away. Their record stays: the agent added again knows them as its own."""
    if not await HaufloStore(hass, NATIVE_STORE).async_load():
        return
    t = await texts(hass, await written_english(hass, None))
    persistent_notification.async_create(hass, t['kept'], t['kept_title'], NOTE)

async def async_export(hass: HomeAssistant, engine: NotifyEngine | None) -> dict[str, Any]:
    """«Сделать дом независимым»: cards kept for good; the alarm rules as HA's own automations — the same sync as a"""
    out: dict[str, Any] = {'cards': None, **empty()}
    try:
        out['cards'] = await keep.async_make_independent(hass, asset_tag())
    except Exception as err:
        _LOGGER.exception('Hauflo: cards not kept')
        out['cards'] = f'error: {err}'
    if engine is None:
        return out
    try:
        out.update(await async_sync(hass, engine))
    except Exception as err:
        _LOGGER.exception('Hauflo: alarm rules not exported')
        out['error'] = str(err)
    out['skipped'] = [r['title'] for r in engine.config.get('rules') or [] if not (r.get('enabled') and r.get('sources'))]
    return out

def summary(result: dict[str, Any], ru: bool) -> str:
    lines: list[str] = []
    cards = result.get('cards')
    if cards == keep.ADDED:
        lines.append('✓ Карточки панели: /config/www/hauflo, подключены ресурсами панелей.' if ru else '✓ Dashboard cards: /config/www/hauflo, added as dashboard resources.')
    elif cards == keep.YAML:
        lines.append('! Карточки скопированы в /config/www/hauflo, но ресурсы панелей заданы в configuration.yaml — добавьте их (см. уведомление).' if ru else '! Cards copied to /config/www/hauflo, but dashboard resources live in configuration.yaml — add them (see the notification).')
    else:
        lines.append(f'! Карточки не сохранены: {cards}' if ru else f'! Cards not kept: {cards}')
    if result.get('error'):
        lines.append(f"! Автоматизации не созданы: {result['error']}" if ru else f"! Automations not made: {result['error']}")
    made = [*(result.get('added') or []), *(result.get('updated') or []), *(result.get('restored') or []), *(result.get('unchanged') or []), *(result.get('kept_edited') or [])]
    if made:
        lines.append(('✓ Аварии работают в Home Assistant сами (автоматизации «Авария: …», помощники «Hauflo · принято: …»): ' if ru else '✓ Alarms run in Home Assistant itself (automations “Alarm: …”, helpers “Hauflo · accepted: …”): ') + ', '.join(made))
    if (kept := result.get('kept_edited')):
        lines.append(('Изменены вручную — оставлены как есть: ' if ru else 'Edited by hand — left as they are: ') + ', '.join(kept))
    if (left := result.get('engine')):
        lines.append(('! Остались у агента Hauflo: ' if ru else '! Left with the Hauflo agent: ') + ', '.join((str(e['title']) for e in left)))
    if (skipped := result.get('skipped')):
        lines.append(('Выключенные / пустые правила пропущены: ' if ru else 'Disabled / empty rules skipped: ') + ', '.join((str(x) for x in skipped)))
    for w in result.get('warnings') or []:
        if w.get('code') == 'double':
            lines.append(f"! Старые автоматизации «независимого дома» ({w['title']}): авария придёт дважды — удалите их." if ru else f"! Old “independent house” automations ({w['title']}): an alarm comes twice — delete them.")
    if not made and (not result.get('error')) and (not result.get('engine')):
        lines.append('Правил аварий нет — автоматизации не нужны.' if ru else 'No alarm rules — no automations needed.')
    return '\n'.join(lines)
