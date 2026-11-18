"""A door's settings, presets and call record."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
import functools
import json
from pathlib import Path
import secrets
import time
from typing import Any
from urllib.parse import quote
from homeassistant.util import dt as dt_util, slugify
from ..tuning import tuning
from . import rtp, sip
t_cfg = functools.cache(lambda: tuning()['intercom'])

@functools.cache
def presets_cfg() -> dict[str, dict[str, Any]]:
    """presets.json, read on first use — off the event loop: async_setup_entry and the config flow preload it in the
    executor."""
    return {k: v for k, v in json.loads(Path(__file__).with_name('presets.json').read_text(encoding='utf-8')).items() if not k.startswith('_')}
MODES = ('direct', 'register', 'registrar', 'cloud')
OPENERS = ('dtmf', 'http', 'entity')
IDLE, RINGING, TALKING, ELSEWHERE = ('idle', 'ringing', 'talking', 'answered_elsewhere')
STATES = (IDLE, RINGING, TALKING, ELSEWHERE)
FIELDS = ('name', 'area', 'preset', 'mode', 'host', 'user', 'password', 'sip_server', 'sip_port', 'sip_user', 'sip_password', 'rtsp', 'open', 'dtmf', 'relay', 'open_entity', 'codec')

class IntercomError(Exception):
    """A refusal the card / the service shows: its translation key (exceptions.intercom_<key>)."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key

@dataclass
class DoorConfig:
    id: str
    name: str
    preset: str
    maker: str
    model: str
    mode: str
    host: str = ''
    user: str = ''
    password: str = ''
    sip_server: str = ''
    sip_port: int = 5060
    sip_user: str = ''
    sip_password: str = ''
    rtsp: str = ''
    snapshot: dict[str, Any] | None = None
    open: str = ''
    open_http: dict[str, Any] | None = None
    dtmf: str = ''
    relay: int = 1
    open_entity: str = ''
    codec: str = 'PCMA'
    register_extra: dict[str, Any] | None = None
    area: str = ''
    error: str | None = None

def _q(v: Any) -> str:
    return quote(str(v), safe='')

def render(template: str, cfg: DoorConfig) -> str:
    return template.format(host=cfg.host, user=_q(cfg.user), password=_q(cfg.password), relay=cfg.relay)

def configs(options: dict[str, Any]) -> list[DoorConfig]:
    """The doors of the options' «intercom» block, each with its preset filled in; ids unique (a slug of the name)."""
    out: list[DoorConfig] = []
    seen: set[str] = set()
    for raw in options.get('doors') or []:
        if not isinstance(raw, dict) or not str(raw.get('name') or '').strip():
            continue
        name = str(raw['name']).strip()
        preset_id = str(raw.get('preset') or 'generic')
        preset = presets_cfg().get(preset_id, presets_cfg()['generic'])
        merged = {**preset, **{k: v for k, v in raw.items() if k in FIELDS and v not in (None, '')}}
        base = slugify(name) or 'door'
        door_id, n = (base, 2)
        while door_id in seen:
            door_id, n = (f'{base}_{n}', n + 1)
        seen.add(door_id)
        mode = merged.get('mode') if merged.get('mode') in MODES else 'direct'
        cfg = DoorConfig(id=door_id, name=name, preset=preset_id if preset_id in presets_cfg() else 'generic', maker=str(preset.get('maker') or ''), model=str(preset.get('label') or ''), mode=str(mode), host=str(merged.get('host') or '').strip(), user=str(merged.get('user') or ''), password=str(merged.get('password') or ''), sip_server=str(merged.get('sip_server') or '').strip(), sip_port=_int(merged.get('sip_port'), t_cfg()['sip_port']), sip_user=str(merged.get('sip_user') or ''), sip_password=str(merged.get('sip_password') or ''), snapshot=merged.get('snapshot') if isinstance(merged.get('snapshot'), dict) else None, open=str(merged.get('open') or ''), open_http=merged.get('open_http') if isinstance(merged.get('open_http'), dict) else None, dtmf=str(merged.get('dtmf') or ''), relay=_int(merged.get('relay'), 1), open_entity=str(merged.get('open_entity') or ''), codec=str(merged.get('codec') or 'PCMA').upper() if str(merged.get('codec') or '').upper() in sip.CODECS else 'PCMA', area=str(raw.get('area') or '').strip())
        if isinstance(merged.get('register_extra'), dict):
            cfg.register_extra = merged['register_extra']
        rtsp_t = str(merged.get('rtsp') or '')
        if rtsp_t and cfg.host:
            try:
                cfg.rtsp = render(rtsp_t, cfg) if '{' in rtsp_t else rtsp_t
            except (KeyError, IndexError, ValueError):
                cfg.rtsp = ''
        if cfg.mode == 'register' and (not cfg.sip_server):
            cfg.sip_server = cfg.host
        cfg.error = _missing(cfg)
        out.append(cfg)
    return out

def iso(ts: float | None) -> str | None:
    return dt_util.utc_from_timestamp(ts).isoformat() if ts else None

def stamped(info: dict[str, Any] | None) -> dict[str, Any] | None:
    return {**info, 'at': iso(info.get('at'))} if info else None

def _int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default

def _missing(cfg: DoorConfig) -> str | None:
    if cfg.mode == 'direct' and (not cfg.host):
        return 'host'
    if cfg.mode in ('register', 'cloud') and (not cfg.sip_server):
        return 'sip_server'
    if cfg.mode in ('register', 'cloud', 'registrar') and (not (cfg.sip_user and cfg.sip_password)):
        return 'sip_account'
    return None

@dataclass
class Call:
    invite: sip.Message
    source: tuple[str, int]
    media: sip.Media
    pt: int
    codec: str
    to_tag: str = field(default_factory=sip.new_tag)
    session_id: int = field(default_factory=lambda: secrets.randbits(31))
    started: float = field(default_factory=time.time)
    answered_at: float | None = None
    rtp: rtp.RtpSession | None = None
    ok: sip.Message | None = None
    acked: asyncio.Future[bool] | None = None
    cseq: int = 0
    caller: str = ''
