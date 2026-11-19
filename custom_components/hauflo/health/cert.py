"""The certificate of the house's external https address (`external_url`) running out — read from a TLS handshake with the address itself (the date only: trust is the browser's business, so a self-signed one is read too)."""
from __future__ import annotations
import asyncio
import contextlib
from http.client import HTTPS_PORT
import ssl
from typing import Any
from urllib.parse import urlsplit
from cryptography import x509
from homeassistant.core import HomeAssistant
from homeassistant.util.ssl import get_default_no_verify_context
from .common import CRIT, DAY_S, KEEP, OK, WARN, Result, cfg, iso
NAME = 'cert'

def target(hass: HomeAssistant) -> tuple[str, int] | None:
    url = hass.config.external_url
    if not url:
        return None
    try:
        p = urlsplit(url)
        port = p.port or HTTPS_PORT
    except ValueError:
        return None
    return (p.hostname, port) if p.scheme == 'https' and p.hostname else None

async def not_after(host: str, port: int) -> float:
    """The certificate's end (UTC timestamp) as the address serves it now."""
    async with asyncio.timeout(cfg()['cert_timeout_s']):
        _reader, writer = await asyncio.open_connection(host, port, ssl=get_default_no_verify_context(), server_hostname=host)
    try:
        der = writer.get_extra_info('ssl_object').getpeercert(binary_form=True)
    finally:
        writer.close()
        with contextlib.suppress(OSError, ssl.SSLError, TimeoutError):
            await writer.wait_closed()
    return x509.load_der_x509_certificate(der).not_valid_after_utc.timestamp()

async def check(hass: HomeAssistant, prev: dict[str, Any] | None, now: float) -> Result:
    where = target(hass)
    if where is None:
        return None
    try:
        end = await not_after(*where)
    except (OSError, ssl.SSLError, TimeoutError, ValueError, TypeError):
        return KEEP if prev and prev.get('_host') == where[0] else None
    return judge(where[0], end, now)

def judge(host: str, end: float, now: float) -> dict[str, Any]:
    days = (end - now) / DAY_S
    level = CRIT if days < cfg()['cert_crit_days'] else WARN if days < cfg()['cert_warn_days'] else OK
    return {'level': level, '_host': host, 'until': iso(end), 'days': int(days)}
