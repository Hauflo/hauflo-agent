"""HTTP view of the wizard's foreign settings."""
from __future__ import annotations
import time
from typing import Any
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from .. import backup
from .common import FEATURE, LEGACY_PLAN, LOCKS_FEATURE, MEMBERS_FEATURE, PLAN_FEATURE, PLAN_ID, WRITE_PLAN_FEATURE, plans_max
from .flags import flags, locks_ok, members_ok, notify_ok
from .plans import active_plans, ids, plan_ok
from .refusals import member_refusal
from .revert import journal_list, revert

class ForeignView(HomeAssistantView):
    """GET → {"notify": {...}, "features": [...], "plan": bool, "journal": {"devices": [...], "entities": [...]},
    "locks":
    {...}, "lock_info": {"seen", "people", "doors"} (Р56, only with a lock in the house)};
    POST (admin only), any of:
      {"notify": {entity_id: bool}} — replaces the alarm sources map;
      {"plan": {"areas": [...], "devices": [...], "entities": [...]} | null, "plan_id": "<id>"} — a deploy's plan
        by its id (none — the one legacy plan): each item written once, gone when idle PLAN_TTL; null — dropped;
      {"revert": {"devices": [...], "entities": [...]}} — «Вернуть как было» (a fresh backup first, as any change);
      {"locks": {lock entity: {...}}} — the locks' settings (Р56, locks_ok), the whole map;
        whole set; the answer says each one refused and why ({"refused": {entity_id: reason}})."""
    url = '/api/hauflo/foreign'
    name = 'api:hauflo:foreign'
    requires_auth = True

    async def get(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        hass = request.app[KEY_HASS]
        f = flags(hass)
        info = await f.lock_info() if f is not None and callable(f.lock_info) else None
        return self.json({'notify': f.notify if f is not None else {}, 'features': [FEATURE, PLAN_FEATURE, WRITE_PLAN_FEATURE, MEMBERS_FEATURE, LOCKS_FEATURE], 'plan': bool(active_plans(hass)), 'journal': journal_list(hass), 'locks': f.locks if f is not None else {}, **({'lock_info': info} if info else {})})

    async def post(self, request):
        if not request['hass_user'].is_admin:
            return self.json_message('admin only', 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message('invalid JSON', 400)
        if not isinstance(body, dict) or not body or set(body) - {'notify', 'plan', 'plan_id', 'revert', 'members', 'locks'}:
            return self.json_message('notify / plan / revert / members / locks expected', 400)
        pid = body.get('plan_id', LEGACY_PLAN)
        if 'plan_id' in body and ('plan' not in body or not isinstance(pid, str) or (not PLAN_ID.match(pid))):
            return self.json_message('plan_id: [A-Za-z0-9_-]{1,64} with a plan expected', 400)
        if 'notify' in body and (not notify_ok(body['notify'])):
            return self.json_message('notify: {entity_id: bool} expected', 400)
        members = members_ok(body.get('members')) if 'members' in body else None
        if 'members' in body and members is None:
            return self.json_message('members: a list of light / cover entity ids expected', 400)
        locks = locks_ok(body.get('locks')) if 'locks' in body else None
        if 'locks' in body and locks is None:
            return self.json_message('locks: {lock entity: {push, alarms, users, names, door, poll}} expected', 400)
        plan = plan_ok(body['plan']) if body.get('plan') is not None else None
        if 'plan' in body and body['plan'] is not None and (plan is None):
            return self.json_message('plan: {areas, devices, entities} lists of ids expected', 400)
        rv = body.get('revert')
        if 'revert' in body and (not isinstance(rv, dict) or set(rv) - {'devices', 'entities'} or ids(rv.get('devices', [])) is None or (ids(rv.get('entities', [])) is None)):
            return self.json_message('revert: {devices, entities} lists of ids expected', 400)
        hass = request.app[KEY_HASS]
        f = flags(hass)
        if f is None:
            return self.json_message('not ready', 409)
        if 'revert' in body and (not backup.fresh(hass)):
            return self.json({'code': 'backup_required', 'message': 'Hauflo agent: a backup first'}, 428)
        live = active_plans(hass)
        if plan is not None and pid not in live and (len(live) >= plans_max()):
            return self.json({'code': 'plans_busy', 'message': 'Hauflo agent: not allowed (plans_busy)'}, 409)
        out: dict[str, Any] = {}
        if 'notify' in body:
            f.set_notify(body['notify'])
        if 'plan' in body:
            if plan is None:
                live.pop(pid, None)
            else:
                live[pid] = {**plan, 'at': time.time()}
        if members is not None:
            f.set_members(members)
            out['refused'] = {e: r for e in sorted(members) if (r := member_refusal(hass, e)) is not None}
        if locks is not None:
            f.set_locks(locks)
            out['locks'] = f.locks
        if 'revert' in body:
            out['reverted'] = revert(hass, set(rv.get('devices', [])), set(rv.get('entities', [])))
        return self.json({'notify': f.notify, 'plan': bool(active_plans(hass)), **out})
