"""Р55: the cloud wizard's «Домофон» (GET / POST /api/hauflo/intercom) — the presets RU+EN and honestly «not checked»,
the doors with their passwords never sent back and kept when not retyped, the same checks as the options' step; the
TURN / STUN servers handed to HA's WebRTC (coturn's time-limited credentials); a doorbell's push with its picture,
its tap to the door's view and its urgency, taken off every phone once answered (ours and a SIP integration's)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import types
from datetime import timedelta

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.hauflo import FEATURES, foreign, notify
from custom_components.hauflo.const import DOMAIN
from custom_components.hauflo.intercom import EVENT, setup
from custom_components.hauflo.intercom.door import PRESETS
from custom_components.hauflo.tuning import TUNING

from .intercom_fixtures import fast, setup_doors  # noqa: F401 — fixtures
from .sip_panel import HOST, Media, Panel

URL = "/api/hauflo/intercom"
DOOR = {"name": "Калитка", "preset": "generic", "host": HOST, "dtmf": "#", "rtsp": "rtsp://{host}/live", "password": "pw", "area": ""}


async def test_presets_and_config(hass: HomeAssistant, setup_doors, hass_client, hass_read_only_access_token) -> None:
    await setup_doors([DOOR], ice_servers=[{"urls": ["turn:turn.example:3478"], "secret": "s3"}])
    assert setup.FEATURE in FEATURES
    client = await hass_client()
    got = await (await client.get(URL)).json()
    assert {p["id"] for p in got["presets"]} == set(PRESETS)
    for p in got["presets"]:
        assert p["label"]["ru"] and p["label"]["en"] and p["checked"] is False and p["mode"] in got["modes"]
        assert "open_http" not in p and "rtsp" not in p  # no panel API path leaves the agent
    cfg = got["config"]
    assert cfg["doors"][0]["name"] == "Калитка" and "password" not in cfg["doors"][0] and cfg["doors"][0]["password_set"] is True
    assert cfg["doors"][0]["sip_password_set"] is False
    assert cfg["ice_servers"] == [{"urls": ["turn:turn.example:3478"], "credential_set": True}] and "s3" not in str(got)
    assert got["listen"] == f"{HOST}:{hass.config_entries.async_entries(DOMAIN)[0].runtime_data.intercom.endpoint.port}"
    assert got["doors"]["kalitka"]["entity_id"] == "sensor.kalitka_intercom" and got["doors"]["kalitka"]["problem"] == "no_calls_yet"
    ro = await hass_client(hass_read_only_access_token)
    assert (await ro.get(URL)).status == 403 and (await ro.post(URL, json={})).status == 403


async def test_save(hass: HomeAssistant, setup_doors, hass_client) -> None:
    entry = await setup_doors([DOOR])
    client = await hass_client()
    for body, want in (
        ({"doors": [{"name": "Gate", "preset": "generic"}]}, {"code": "missing_host", "field": "doors", "door": "Gate"}),
        ({"doors": [{"name": "Gate", "preset": "nope", "host": HOST}]}, {"code": "bad_preset", "field": "doors", "door": "Gate"}),
        ({"doors": [{"name": "Gate", "mode": "nope", "host": HOST}]}, {"code": "bad_mode", "field": "doors", "door": "Gate"}),
        ({"doors": [{"name": "Gate", "open": "nope", "host": HOST}]}, {"code": "bad_open", "field": "doors", "door": "Gate"}),
        ({"bind": "eth0", "doors": []}, {"code": "bad_address", "field": "bind", "door": ""}),
        ({"public": "router", "doors": []}, {"code": "bad_address", "field": "public", "door": ""}),
        ({"port": 70000, "doors": []}, {"code": "bad_port", "field": "port", "door": ""}),
        ({"port": "x", "doors": []}, {"code": "bad_port", "field": "port", "door": ""}),
        ({"ice_servers": [{"urls": "http://x"}]}, {"code": "bad_ice", "field": "ice_servers", "door": ""}),
        ({"ice_servers": "turn:x"}, {"code": "bad_ice", "field": "ice_servers", "door": ""}),
        ({"ice_servers": ["turn:x"]}, {"code": "bad_ice", "field": "ice_servers", "door": ""}),
    ):
        resp = await client.post(URL, json=body)
        assert resp.status == 400 and await resp.json() == want, body
    assert (await client.post(URL, data="{")).status == 400
    assert (await client.post(URL, json={"lock": 1})).status == 400
    assert entry.options["intercom"]["doors"] == [DOOR]  # nothing refused was saved
    # The password not retyped: kept (by the door's name); the ICE secret too; a cleared one goes.
    door = {k: v for k, v in DOOR.items() if k != "password"} | {"area": "hall"}
    resp = await client.post(URL, json={"bind": HOST, "port": 0, "doors": [door], "ice_servers": [{"urls": "turn:t.example:3478", "secret": "k", "ttl_s": 600}]})
    assert resp.status == 200 and (await resp.json())["config"]["doors"][0]["password_set"] is True
    await hass.async_block_till_done()
    saved = entry.options["intercom"]
    assert saved["doors"][0]["password"] == "pw" and saved["doors"][0]["area"] == "hall"
    assert saved["ice_servers"] == [{"urls": ["turn:t.example:3478"], "secret": "k", "ttl_s": 600}]
    assert entry.runtime_data.intercom.options == saved  # the entry reloaded with them
    await client.post(URL, json={"bind": HOST, "port": 0, "doors": [door | {"password_clear": True}], "ice_servers": [{"urls": ["turn:t.example:3478"]}]})
    await hass.async_block_till_done()
    saved = entry.options["intercom"]
    assert "password" not in saved["doors"][0] and saved["ice_servers"] == [{"urls": ["turn:t.example:3478"], "secret": "k"}]
    await client.post(URL, json={"bind": HOST, "port": 0, "doors": [door], "ice_servers": []})
    await hass.async_block_till_done()
    assert "ice_servers" not in entry.options["intercom"]


async def test_not_set_up(hass: HomeAssistant, setup_doors, hass_client) -> None:
    entry = await setup_doors([DOOR])
    await hass.config_entries.async_unload(entry.entry_id)
    client = await hass_client()
    assert (await client.get(URL)).status == 404 and (await client.post(URL, json={})).status == 404


def test_turn_credentials() -> None:
    block = {"ice_servers": [{"urls": ["turn:t:3478"], "secret": "k", "ttl_s": 600}, {"urls": "stun:s:3478"}, {"urls": "turn:u", "username": "a", "credential": "b"}, {"urls": ""}, "x"]}
    got = setup.ice_servers(block, now=1000)
    user = f"1600:{TUNING['intercom']['turn_user']}"
    cred = base64.b64encode(hmac.new(b"k", user.encode(), hashlib.sha1).digest()).decode()
    assert got == [{"urls": ["turn:t:3478"], "username": user, "credential": cred}, {"urls": "stun:s:3478"}, {"urls": "turn:u", "username": "a", "credential": "b"}]
    assert setup.ice_servers({"ice_servers": [{"urls": "turn:t", "secret": "k"}]}, now=0)[0]["username"].startswith(f"{TUNING['intercom']['turn_ttl_s']}:")
    assert setup.register_ice(None, {}) is None  # type: ignore[arg-type]


async def test_ice_servers_reach_webrtc(hass: HomeAssistant, setup_doors) -> None:
    from homeassistant.components.web_rtc import async_get_ice_servers

    ice = [{"urls": ["turn:t.example:3478"], "username": "u", "credential": "c"}]
    entry = await setup_doors([DOOR], ice_servers=ice)
    mgr = entry.runtime_data.intercom
    if mgr._ice_off is None:  # no WebRTC set up by the test HA: set it up and hand them again
        assert await async_setup_component(hass, "web_rtc", {})
        mgr._ice_off = setup.register_ice(hass, mgr.options)
    assert any(s.urls == ["turn:t.example:3478"] and s.username == "u" for s in async_get_ice_servers(hass))
    await hass.config_entries.async_unload(entry.entry_id)
    assert not any(s.urls == ["turn:t.example:3478"] for s in async_get_ice_servers(hass))


async def test_register_ice_without_webrtc(hass: HomeAssistant, monkeypatch) -> None:
    from homeassistant.components import web_rtc

    monkeypatch.setattr(web_rtc, "async_register_ice_servers", lambda *_: (_ for _ in ()).throw(KeyError("ice")))
    assert setup.register_ice(hass, {"ice_servers": [{"urls": "stun:s"}]}) is None


# --- the push ------------------------------------------------------------------------------------------------------


class _Dash:
    def __init__(self, config) -> None:
        self.config, self._c = {"id": "x"}, config

    async def async_load(self, force: bool):
        if isinstance(self._c, Exception):
            raise self._c
        return self._c


@pytest.fixture
def lovelace(hass: HomeAssistant):
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    was = hass.data.get(LOVELACE_DATA)
    card = {"type": "custom:hauflo-camera", "intercom": "sensor.kalitka_intercom", "entity": "camera.kalitka"}
    hass.data[LOVELACE_DATA] = types.SimpleNamespace(dashboards={
        "mine": _Dash({"views": [{"cards": [card]}]}),  # the family's own: never a link
        "hauflo-broken": _Dash(RuntimeError("gone")),
        "hauflo-home": _Dash({"views": [{"path": "main", "cards": [card]}, {"path": "other", "cards": []}, {"cards": [card]}]}),
    })
    yield
    if was is None:
        hass.data.pop(LOVELACE_DATA, None)
    else:
        hass.data[LOVELACE_DATA] = was


async def test_ring_push_picture_link_and_cleared(hass: HomeAssistant, setup_doors, lovelace, hass_admin_user) -> None:
    entry = await setup_doors([DOOR])
    mgr = entry.runtime_data.intercom
    eng = entry.runtime_data.engine
    eng.phones = lambda: [{"service": "mobile_app_a", "os": "android", "person": "x"}, {"service": "mobile_app_i", "os": "ios", "person": "y"}]
    eng.scope = lambda phone, config=None: "all"
    eng.config = {**eng.config, "lang": "ru"}
    a, i = async_mock_service(hass, "notify", "mobile_app_a"), async_mock_service(hass, "notify", "mobile_app_i")
    panel, media = await Panel().start(), await Media().start()
    try:
        inv = panel.invite((HOST, mgr.endpoint.port), rtp_port=media.port)
        await panel.status(180)
        await hass.async_block_till_done()
        assert len(a) == 1 and len(i) == 1
        da, di = a[0].data["data"], i[0].data["data"]
        assert da["image"] == di["image"] == "/api/camera_proxy/camera.kalitka" and di["entity_id"] == "camera.kalitka"
        assert da["clickAction"] == di["url"] == "/hauflo-home/2"  # the room's view (no path: its index), not «Главная»
        assert di["push"] == TUNING["notify"]["ring_push"]["ios"] and da["priority"] == "high" and da["ttl"] == 0
        assert "camera.kalitka_talk" not in str(da)
        # Answered here: the ring's push off both phones.
        await hass.services.async_call(DOMAIN, "intercom_answer", {"entity_id": "sensor.kalitka_intercom"}, blocking=True, context=Context(user_id=hass_admin_user.id))
        ok = await panel.status(200, "INVITE")
        panel.in_dialog(inv, "ACK", (HOST, mgr.endpoint.port), ok=ok)
        await hass.async_block_till_done()
        assert a[-1].data["message"] == i[-1].data["message"] == "clear_notification" and a[-1].data["data"]["tag"] == da["tag"]
        # Another moment of the call (or a stranger's bus event): nothing more.
        hass.bus.async_fire(EVENT, {"state": "talking", "call": "event.kalitka_call"})
        hass.bus.async_fire(EVENT, {"state": "idle", "call": "event.kalitka_call"})
        await hass.async_block_till_done()
        assert len(a) == 2
    finally:
        panel.close()
        media.close()


async def test_quiet_hours_ring_keeps_picture_not_urgency(hass: HomeAssistant, setup_doors, lovelace) -> None:
    entry = await setup_doors([DOOR])
    eng = entry.runtime_data.engine
    eng.phones = lambda: [{"service": "mobile_app_i", "os": "ios", "person": "y"}]
    eng.scope = lambda phone, config=None: "all"
    eng.quiet_now = lambda: True
    i = async_mock_service(hass, "notify", "mobile_app_i")
    hass.states.async_set("event.kalitka_call", dt_util.utcnow().isoformat(), {"device_class": "doorbell", "event_type": "ring", "friendly_name": "Калитка"})
    await hass.async_block_till_done()
    d = i[0].data["data"]
    assert d["image"] == "/api/camera_proxy/camera.kalitka" and d["push"] == {"interruption-level": "passive"}


async def test_foreign_sip_integration(hass: HomeAssistant, setup_doors) -> None:
    """hass-sip (§4 of the contract): «incoming» rings, «connected» takes the push off, «ended» pushes nothing; with
    no camera / dashboard the push is the plain ring, urgent."""
    entry = await setup_doors([])
    eng = entry.runtime_data.engine
    eng.phones = lambda: [{"service": "mobile_app_a", "os": "android", "person": "x"}]
    eng.scope = lambda phone, config=None: "all"
    a = async_mock_service(hass, "notify", "mobile_app_a")
    eid = "event.porch_sip_call"
    er.async_get(hass).async_get_or_create("event", "sip", "porch", suggested_object_id="porch_sip_call")
    foreign.flags(hass).notify[eid] = True
    hass.states.async_set(eid, "unknown", {"friendly_name": "Porch"})

    def said(kind: str, ago: float = 0) -> None:
        hass.states.async_set(eid, (dt_util.utcnow() - timedelta(seconds=ago)).isoformat(), {"event_type": kind, "friendly_name": "Porch"})

    said("ended")  # before any ring: nothing
    await hass.async_block_till_done()
    assert a == []
    said("incoming", ago=-1)
    await hass.async_block_till_done()
    assert len(a) == 1 and a[0].data["data"]["priority"] == "high" and "image" not in a[0].data["data"] and "clickAction" not in a[0].data["data"]
    said("connected", ago=-2)
    await hass.async_block_till_done()
    assert len(a) == 2 and a[1].data["message"] == "clear_notification"
    said("ended", ago=-3)
    said("connected", ago=-4)  # already taken off: nothing
    await hass.async_block_till_done()
    assert len(a) == 2
    assert notify.RING_KINDS == {"doorbell"}


def test_wizard_reads_no_file_on_the_loop() -> None:
    """0.33 fix3 п.21: the wizard's GET (presets) runs on the event loop — the presets' names are read once at import,
    never on a call (HA: «Detected blocking call to open»; the stock-card table is warmed at setup, test_setup.py)."""
    from pathlib import Path
    from unittest.mock import patch

    from custom_components.hauflo.intercom import setup as setup_mod

    with patch.object(Path, "read_text", side_effect=AssertionError("blocking read on the loop")), patch("builtins.open", side_effect=AssertionError("blocking open on the loop")):
        got = setup_mod.presets()
        assert got and all(set(p["label"]) == {"ru", "en"} for p in got)
