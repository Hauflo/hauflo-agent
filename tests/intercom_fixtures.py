"""Р55 tests: an entry with doors, the agent's SIP on 127.0.0.1 (any free port), timers short."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo.const import CONF_RANGES, CONF_SECRET, CONF_SERVER, DOMAIN
from custom_components.hauflo.tuning import TUNING

from .sip_panel import HOST

FAST = {"t1_s": 0.05, "t2_s": 0.2, "transaction_s": 0.6, "answered_elsewhere_s": 0.1, "rtsp_port": 0, "talk_refresh_s": 0.05, "dtmf_ms": 40, "frame_ms": 20, "register_retry_s": 0.1, "nat_keepalive_s": 0.05, "no_audio_s": 0.05, "http_timeout_s": 2}


@pytest.fixture
def fast(monkeypatch) -> None:
    for k, v in FAST.items():
        monkeypatch.setitem(TUNING["intercom"], k, v)


@pytest.fixture
async def setup_doors(hass: HomeAssistant, socket_enabled, fast, monkeypatch):
    """setup_doors([door, …], **block) → the entry; unloaded at the end (no socket, timer or task left)."""
    entries: list[MockConfigEntry] = []

    async def idle(self):
        return None

    monkeypatch.setattr("custom_components.hauflo.Agent.run", idle)  # for the whole test: a reload must not reach the server either

    async def make(doors: list[dict[str, Any]], **block: Any) -> MockConfigEntry:
        options = {CONF_RANGES: ["1/1"], "intercom": {"bind": HOST, "port": 0, **block, "doors": doors}}
        entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"}, options=options)
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entries.append(entry)
        return entry

    yield make
    for entry in entries:
        if entry.state.recoverable or entry.runtime_data is not None:
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
