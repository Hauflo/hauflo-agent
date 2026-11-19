"""Websocket commands of the dashboard merge."""
from __future__ import annotations
import asyncio
from collections.abc import Callable
import json
import logging
from typing import TYPE_CHECKING, Any
from .const import PLAN, SAVE
from .store import MergeError, choice, is_copy
if TYPE_CHECKING:
    from .store import DashboardMerge
_LOGGER = logging.getLogger(__name__)

async def local_ws(session: Any, url: str, token: str, cmds: list[dict[str, Any]]) -> list[Any]:
    """Commands to this HA's own websocket as the agent's user (a copy of the dashboard, the old copies off) — the public API, one connection, in order; the first failure raises."""
    async with asyncio.timeout(60), session.ws_connect(url, max_msg_size=0) as ws:
        await ws.receive_json()
        await ws.send_json({'type': 'auth', 'access_token': token})
        if (await ws.receive_json()).get('type') != 'auth_ok':
            raise MergeError('local_auth', "this HA refused the agent's sign-in")
        out: list[Any] = []
        for i, c in enumerate(cmds, 1):
            await ws.send_json({**c, 'id': i})
            while True:
                r = await ws.receive_json()
                if isinstance(r, dict) and r.get('id') == i and (r.get('type') == 'result'):
                    break
            if not r.get('success'):
                raise MergeError('local_failed', f"{c.get('type')}: {(r.get('error') or {}).get('message')}")
            out.append(r.get('result'))
        return out

async def handle(agent: Any, ch: int, msg: dict[str, Any], validate: Callable[[Any], str | None], fresh_backup: Callable[[], bool], locked_code: str, prune: Callable[[Any], tuple[Any, list[dict[str, Any]]]] | None=None) -> None:
    """`hauflo/dashboard/plan` and `lovelace/config` of a dashboard the agent merges (reads), `hauflo/dashboard/save` and every plain `lovelace/config/save` of a `hauflo-…` dashboard (writes): answered here, never relayed to HA."""
    t, mid = (msg.get('type'), msg.get('id'))

    async def reply(result: Any=None, code: str | None=None, message: str='') -> None:
        frame: dict[str, Any] = {'id': mid, 'type': 'result', 'success': code is None}
        if code is None:
            frame['result'] = result
        else:
            frame['error'] = {'code': code, 'message': f'Hauflo agent: {message}'}
        await agent.send({'t': 'ws_msg', 'ch': ch, 'data': json.dumps(frame, ensure_ascii=False)})
    url, config = (msg.get('url_path'), msg.get('config'))
    merger: DashboardMerge = agent.dashboards
    if t == 'lovelace/config' and isinstance(url, str) and merger.manages(url) and (not is_copy(url)):
        try:
            await reply(await merger.read(url))
        except MergeError as err:
            await reply(code=err.code, message=str(err))
        return
    if t == 'lovelace/config' or not (isinstance(url, str) and url.startswith('hauflo-') and (not is_copy(url))) or (not isinstance(config, dict)):
        _LOGGER.warning("Hauflo: «%s» isn't allowed through the tunnel", t)
        await reply(code='not_allowed', message='not allowed')
        return
    why = validate(config)
    dropped: list[dict[str, Any]] = []
    if why is not None and prune is not None:
        config, dropped = prune(config)
        why = validate(config)
    if why is not None:
        _LOGGER.warning("Hauflo: «%s» of %s isn't allowed through the tunnel — %s", t, url, why)
        await reply(code='not_allowed', message=f'not allowed ({why})')
        return
    for d in dropped:
        _LOGGER.warning('Hauflo: %s of %s — left off the dashboard: %s', t, url, d.get('detail'))
    said = [{k: v for k, v in d.items() if k != 'detail'} for d in dropped][:100]
    try:
        if t == PLAN:
            planned = await merger.plan(url, config)
            await reply({**planned, 'dropped': said} if said and isinstance(planned, dict) else planned)
            return
        if agent.locked:
            await reply(code=locked_code, message='changes by Hauflo are switched off in Home Assistant (Hauflo → Configure)')
            return
        if not fresh_backup():
            await reply(code='backup_required', message='a backup first')
            return
        plain = t != SAVE
        title = msg.get('title') if isinstance(msg.get('title'), str) else None
        lang = msg.get('lang') if isinstance(msg.get('lang'), str) else None
        res = await merger.save(url, config, [] if plain else choice(msg.get('take')), [] if plain else choice(msg.get('restore')), not merger.manages(url) if plain else msg.get('replace') is True, title[:60] if title else None, lambda cmds: local_ws(agent.local_session, f'{agent.local_base}/api/websocket', agent.token, cmds), lang[:5] if lang else None, [] if plain else [x for x in (msg.get('stock') if isinstance(msg.get('stock'), list) else [])[:200] if isinstance(x, str) and len(x) <= 64], [] if plain else [x for x in (msg.get('twins') if isinstance(msg.get('twins'), list) else [])[:50] if isinstance(x, str) and len(x) <= 128])
        await reply(None if plain else {**res, 'dropped': said} if said and isinstance(res, dict) else res)
    except MergeError as err:
        await reply(code=err.code, message=str(err))
    except Exception as err:
        _LOGGER.exception('Hauflo: dashboard %s failed', t)
        await reply(code='failed', message=str(err)[:200])
