"""п.60 adversarial: holes suspected in daylight.py / scenes.py (tester, not the builder). A failing test = evidence."""

import asyncio
import json
import os

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util.yaml import dump
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.hauflo import daylight as dl
from custom_components.hauflo.house import house
from test_daylight import Clock, lux, sun
from test_scenes import LAMP, LIGHT, URL, _file, _sun, _two_light_scene, entry  # noqa: F401


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(dl, "_clock", c)
    return c


async def test_all_entities_skipped_is_answered_not_silent(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    eid = await _two_light_scene(hass, client)
    assert (await client.post(URL, json={"op": "daylight_skip", "entity_id": LIGHT, "on": True})).status == 200
    on = async_mock_service(hass, "light", "turn_on")
    tu = async_mock_service(hass, "scene", "turn_on")
    _sun(hass, 30)
    await entry.runtime_data.daylight.refresh(force=True)
    res = await client.post(URL, json={"op": "apply", "entity_id": eid})
    got = await res.json()
    assert res.status == 200 and sorted(got["skipped"]) == sorted([LIGHT, LAMP]) and got["applied"] == 0
    assert not on and not tu


async def test_attributes_pass_to_scene_apply_unchanged(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    eid = await _two_light_scene(hass, client)
    data = _file(hass)
    for s in data:
        if str(s["id"]).startswith("hauflo_fam_"):
            s["entities"][LIGHT] = {"state": "on", "brightness": 77, "color_temp": 300}
    with open(hass.config.path("scenes.yaml"), "w") as f:
        f.write(dump(data))
    calls = async_mock_service(hass, "scene", "apply")
    _sun(hass, 30)
    await entry.runtime_data.daylight.refresh(force=True)
    await client.post(URL, json={"op": "apply", "entity_id": eid})
    assert calls and calls[0].data["entities"] == {LIGHT: {"state": "on", "brightness": 77, "color_temp": 300}}


@pytest.mark.parametrize("bad", [["scene.x"], {"a": 1}, "SCENE.X", "scene.", "light.bed_lamp", "", True, 1.5])
async def test_apply_injection(hass: HomeAssistant, hass_client, entry, bad) -> None:
    client = await hass_client()
    res = await client.post(URL, json={"op": "apply", "entity_id": bad})
    assert res.status == 400, (bad, res.status)


async def test_stored_bad_lux_does_not_wipe_skip_list(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    await _two_light_scene(hass, client)
    data = await house(hass).load()
    data["daylight"] = {"skip": [LAMP], "lux": 50}  # 50 < 100 bound
    await house(hass).save()
    got = (await (await client.get(URL)).json())["daylight"]
    assert got["skip"] == [LAMP], got


async def test_sensor_without_unit_not_silently_accepted(hass: HomeAssistant, hass_client, entry) -> None:
    hass.states.async_set("sensor.nounit", "5000", {})
    client = await hass_client()
    res = await client.post(URL, json={"op": "daylight_config", "sensor": "sensor.nounit"})
    if res.status == 200:
        _sun(hass, -20)
        await entry.runtime_data.daylight.refresh(force=True)
        assert entry.runtime_data.daylight.source == "sensor", "accepted but silently ignored (falls to sun)"


async def test_nonexistent_sensor_accepted(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    res = await client.post(URL, json={"op": "daylight_config", "sensor": "sensor.does_not_exist"})
    assert res.status == 400, "a sensor that does not exist is accepted silently"


@pytest.mark.parametrize("val", ["-5", "unknown", "unavailable", "abc", "nan", "inf", ""])
async def test_junk_lux_values_fall_to_sun(hass: HomeAssistant, clock, val) -> None:
    d = dl.Daylight(hass)
    await d._save({"sensor": "sensor.outdoor_lux", "skip": []})
    hass.states.async_set("sensor.outdoor_lux", val, {"unit_of_measurement": "lx"})
    sun(hass, -20)
    await d.refresh(force=True)
    assert d.source == "sun" or (d.source == "sensor" and d.on is False), (val, d.source, d.value, d.on)


async def test_boundaries_and_hold(hass: HomeAssistant, clock) -> None:
    d = dl.Daylight(hass)
    await d._save({"sensor": "sensor.outdoor_lux", "skip": []})
    seq = {}
    for v in (999, 1000, 1001):
        lux(hass, v)
        clock.t += dl.HOLD + 1  # outside the hold, so only the threshold decides
        await d.refresh()
        seq[v] = d.on
    assert seq[1000] is True, f"exactly the threshold is not light (strict >): {seq}"
    for v in (801, 799):
        lux(hass, v)
        clock.t += dl.HOLD + 1
        await d.refresh()
        seq[v] = d.on
    assert seq[801] is True and seq[799] is False, seq
    lux(hass, 5000)
    clock.t += 1  # inside the hold: must not flip back
    await d.refresh()
    assert d.on is False
    d.async_stop()


async def test_hold_not_stuck_forever(hass: HomeAssistant, clock) -> None:
    d = dl.Daylight(hass)
    await d._save({"sensor": "sensor.outdoor_lux", "skip": []})
    lux(hass, 5000)
    await d.refresh()
    lux(hass, 10)
    clock.t += 5
    await d.refresh()
    assert d.on is True and d._later is not None
    d._later()  # cancel the timer
    d._later = None
    clock.t += dl.HOLD
    await d.refresh()
    assert d.on is False
    d.async_stop()


async def test_two_applies_at_once(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    eid = await _two_light_scene(hass, client)
    calls = async_mock_service(hass, "scene", "apply")
    _sun(hass, 30)
    await entry.runtime_data.daylight.refresh(force=True)
    rs = await asyncio.gather(*[client.post(URL, json={"op": "apply", "entity_id": eid}) for _ in range(2)])
    assert [r.status for r in rs] == [200, 200] and len(calls) == 2


async def test_daylight_config_bad_bodies_400(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    for body in ({"lux": 500, "skip": ["light.x"]}, {"lux": 500, "foo": 1}, {"lux": True}, {"lux": "500"}, {"sensor": ["sensor.a"]}):
        res = await client.post(URL, data=json.dumps({"op": "daylight_config", **body}), headers={"Content-Type": "application/json"})
        assert res.status == 400, (body, res.status)


async def test_skip_dedupe_and_bad_ids(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    await client.post(URL, json={"op": "daylight_skip", "entity_id": LAMP, "on": True})
    await client.post(URL, json={"op": "daylight_skip", "entity_id": LAMP, "on": True})
    assert (await (await client.get(URL)).json())["daylight"]["skip"] == [LAMP]
    for eid in ("climate.x", "light.", "light.UP", "switch.a b", ["light.a"]):
        assert (await client.post(URL, json={"op": "daylight_skip", "entity_id": eid, "on": True})).status == 400, eid


async def test_apply_when_scenes_yaml_missing_still_runs(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    eid = await _two_light_scene(hass, client)
    tu = async_mock_service(hass, "scene", "turn_on")
    _sun(hass, 30)
    await entry.runtime_data.daylight.refresh(force=True)
    os.remove(hass.config.path("scenes.yaml"))
    res = await client.post(URL, json={"op": "apply", "entity_id": eid})
    assert res.status == 200 and tu
