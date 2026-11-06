"""timers.py (off_in), tunnel.load() refusals, StockView GET/POST."""

from __future__ import annotations

import copy
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from custom_components.hauflo import stock, timers, tunnel
from custom_components.hauflo.const import DOMAIN

pytestmark = pytest.mark.parametrize("expected_lingering_timers", [True])


async def test_off_in_switches_off_cancels_and_refuses_non_light(hass: HomeAssistant):
    off = async_mock_service(hass, "light", "turn_off")
    timers.async_setup(hass)
    timers.async_setup(hass)  # second call is a no-op
    await hass.services.async_call(DOMAIN, "off_in", {"entity_id": "light.a", "minutes": 5}, blocking=True)
    assert "light.a" in hass.data[timers.DATA_OFF_TIMERS]
    # a new timer replaces the old one
    await hass.services.async_call(DOMAIN, "off_in", {"entity_id": "light.a", "minutes": 10}, blocking=True)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done()
    assert off == []
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=11))
    await hass.async_block_till_done()
    assert len(off) == 1 and off[0].data["entity_id"] == "light.a"
    assert "light.a" not in hass.data[timers.DATA_OFF_TIMERS]
    # minutes 0 cancels
    await hass.services.async_call(DOMAIN, "off_in", {"entity_id": "light.b", "minutes": 5}, blocking=True)
    await hass.services.async_call(DOMAIN, "off_in", {"entity_id": "light.b", "minutes": 0}, blocking=True)
    assert "light.b" not in hass.data[timers.DATA_OFF_TIMERS]
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "off_in", {"entity_id": "switch.x", "minutes": 5}, blocking=True)


def _bad(tmp_path, mutate):
    data = json.loads(tunnel.FILE.read_text(encoding="utf-8"))
    mutate(data)
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_tunnel_load_ok_and_refusals(tmp_path):
    assert tunnel.load()["version"] == 1

    def setk(path, value):
        def m(d):
            o = d
            for k in path[:-1]:
                o = o[k]
            o[path[-1]] = value
        return m

    cases = [
        setk(["version"], 2),
        setk(["ws", "commands"], []),
        setk(["ws", "services"], [["a"]]),
        setk(["ws", "reads"], ["__not_a_command__"]),
        setk(["ws", "read_services"], [["x", "y"]]),
        setk(["http", "routes"], [{"id": "x", "path": "/nope", "methods": ["GET"]}]),
        setk(["http", "routes"], [{"id": "x", "path": "/api/x", "methods": ["PUT"]}]),
        setk(["http", "locked_never_keys"], [{"path": "/zzz", "method": "GET", "keys": ["a"]}]),
        setk(["flows", "setup"], []),
        setk(["flows", "switch_as_x", "keys"], {}),
        setk(["flows", "template", "max_name"], 0),
        setk(["flows", "template", "tokens"], [{"re": "(a)", "entity": 3}]),
        setk(["direct_only"], []),
    ]
    for m in cases:
        with pytest.raises(ValueError):
            tunnel.load(_bad(tmp_path, m))


class FakeDash:
    mode = "storage"

    def __init__(self, cfg):
        self.cfg, self.saved = cfg, None

    async def async_load(self, force):
        return self.cfg

    async def async_save(self, c):
        self.saved = c
        self.cfg = c


def _req(hass, user_admin=True, query=None, body=None, bad_json=False):
    async def js():
        if bad_json:
            raise ValueError("x")
        return body

    return SimpleNamespace(app={stock.KEY_HASS: hass}, query=query or {}, json=js, __getitem__=None) if False else _Req(hass, user_admin, query, js)


class _Req(dict):
    def __init__(self, hass, admin, query, js):
        super().__init__(hass_user=SimpleNamespace(is_admin=admin))
        self.app = {stock.KEY_HASS: hass}
        self.query = query or {}
        self.json = js


async def test_stock_view(hass: HomeAssistant, monkeypatch):
    card = {"type": "tile", "entity": "light.a"}
    dash = FakeDash({"views": [{"title": "Д", "cards": [card]}]})
    monkeypatch.setattr(stock, "_dashboard", lambda h, u: dash if u == "x" else None)
    hass.states.async_set("light.a", "on")
    view = stock.StockView()
    status = lambda r: r.status  # noqa: E731

    assert status(await view.get(_req(hass, False))) == 403
    assert status(await view.get(_req(hass, query={"url_path": "nope"}))) == 404
    r = await view.get(_req(hass, query={"url_path": "x"}))
    assert r.status == 200 and json.loads(r.body)["offers"]

    async def post(body, admin=True, bad=False):
        async def js():
            if bad:
                raise ValueError("x")
            return body

        return await view.post(_Req(hass, admin, {}, js))

    assert (await post({}, admin=False)).status == 403
    assert (await post(None, bad=True)).status == 400
    assert (await post([1])).status == 400
    assert (await post({"url_path": "nope"})).status == 404
    offers = json.loads(r.body)["offers"]
    ids = [o["id"] for o in offers]
    r2 = await post({"url_path": "x", "replace": ids})
    assert r2.status == 200 and json.loads(r2.body)["done"] >= 1 and dash.saved is not None
    r3 = await post({"url_path": "x", "undo": ids})
    assert r3.status == 200
    r4 = await post({"url_path": "x", "replace": "bad"})
    assert json.loads(r4.body)["done"] == 0


async def test_stock_dashboard_lookup_and_missing_config(hass: HomeAssistant):
    from homeassistant.components.lovelace.const import LOVELACE_DATA, ConfigNotFound

    class Empty(FakeDash):
        async def async_load(self, force):
            raise ConfigNotFound

    yaml_dash = SimpleNamespace(mode="yaml")
    hass.data[LOVELACE_DATA] = SimpleNamespace(dashboards={None: Empty(None), "dash": FakeDash({"views": []}), "y": yaml_dash})
    assert isinstance(stock._dashboard(hass, "lovelace"), Empty)
    assert stock._dashboard(hass, "dash") is hass.data[LOVELACE_DATA].dashboards["dash"]
    assert stock._dashboard(hass, "y") is None
    assert stock._dashboard(hass, "missing") is None
    assert stock._dashboard(hass, 5) is None

    view = stock.StockView()
    r = await view.get(_Req(hass, True, {}, None))
    assert r.status == 200 and json.loads(r.body) == {"offers": [], "replaced": []}
    # POST on a dashboard without a config is a 404
    async def js():
        return {"url_path": None}

    assert (await view.post(_Req(hass, True, {}, js))).status == 404


def test_stock_odd_cards_are_left_alone():
    info = lambda eid: None  # noqa: E731
    assert stock.offer_of({"type": "area", "area": "kitchen"}, info)["to"] == "hauflo-area"
    assert stock.offer_of({"type": "area", "area": 5}, info) is None
    assert stock.offer_of({"type": "tile", "entity": "light.ghost"}, info) is None
    assert stock.offer_of({"type": "markdown"}, info) is None
    assert stock.undo({"views": []}, [])[1] == 0
    assert stock.undo({"views": []}, [1, None])[1] == 0
