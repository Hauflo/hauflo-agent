"""«На улице светло» (daylight.py, п.60): an outdoor lux sensor first (20 % hysteresis), the sun's elevation otherwise
(6° / 4°), a change held 10 minutes, a silent / foreign sensor gives way to the sun, the config refused when it is not
valid. Assume it flickers, ignores the sensor and keeps junk — and show it does not."""

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import daylight as dl
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN

LUX = "sensor.outdoor_lux"
HOLD = dl.HOLD


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(dl, "_clock", c)
    return c


def sun(hass: HomeAssistant, elevation) -> None:
    hass.states.async_set("sun.sun", "above_horizon" if (elevation or 0) > 0 else "below_horizon", {"elevation": elevation})


def lux(hass: HomeAssistant, value, unit="lx") -> None:
    hass.states.async_set(LUX, str(value), {"unit_of_measurement": unit, "device_class": "illuminance"})


async def _day(hass: HomeAssistant, cfg=None) -> dl.Daylight:
    d = dl.Daylight(hass)
    if cfg is not None:
        await d._save(cfg)
    return d


async def test_sensor_with_hysteresis(hass: HomeAssistant, clock) -> None:
    sun(hass, -20)  # night by the sun: only the sensor can say «light»
    lux(hass, 1001)
    d = await _day(hass, {"sensor": LUX, "skip": []})
    await d.refresh()
    assert (d.on, d.source, d.value, d.threshold) == (True, "sensor", 1001.0, 1000)
    clock.t += HOLD + 1
    lux(hass, 801)  # below the threshold, above 80 %: still light
    await d.refresh()
    assert d.on is True
    lux(hass, 799)
    await d.refresh()
    assert d.on is False
    clock.t += HOLD + 1
    lux(hass, 999.5)  # just under the threshold: still dark
    await d.refresh()
    assert d.on is False
    lux(hass, 1000)  # at the threshold: light (on from `lux` up, tester D4)
    await d.refresh()
    assert d.on is True


async def test_own_threshold(hass: HomeAssistant, clock) -> None:
    sun(hass, 40)  # the sun says light, but the sensor is the source
    lux(hass, 4000)
    d = await _day(hass, {"sensor": LUX, "lux": 5000, "skip": []})
    await d.refresh()
    assert (d.on, d.source, d.threshold) == (False, "sensor", 5000)


async def test_sun_fallback(hass: HomeAssistant, clock) -> None:
    sun(hass, 5.9)  # under 6°: dark
    d = await _day(hass)
    await d.refresh()
    assert (d.on, d.source) == (False, "sun")
    clock.t += HOLD + 1
    sun(hass, 6)  # at 6°: light (on from 6° up, tester D4)
    await d.refresh()
    assert d.on is True
    clock.t += HOLD + 1
    sun(hass, 4.5)  # between 4° and 6°: still light
    await d.refresh()
    assert d.on is True
    sun(hass, 3.9)
    await d.refresh()
    assert d.on is False


async def test_ten_minute_hold(hass: HomeAssistant, clock) -> None:
    sun(hass, 30)
    d = await _day(hass)
    await d.refresh()
    assert d.on is True
    clock.t += 60
    sun(hass, -10)  # a minute later dark: held
    await d.refresh()
    assert d.on is True
    clock.t += HOLD - 120
    await d.refresh()
    assert d.on is True  # 9 minutes after: still held
    clock.t += 61
    await d.refresh()
    assert d.on is False  # 10 minutes after the flip: through
    # And back again within the hold: held the other way too.
    clock.t += 30
    sun(hass, 30)
    await d.refresh()
    assert d.on is False
    d.async_stop()  # the timer the held flip set is cancelled


@pytest.mark.parametrize(("state", "attrs"), [(None, None), ("unavailable", {"unit_of_measurement": "lx"}), ("unknown", {"unit_of_measurement": "lx"}), ("bright", {"unit_of_measurement": "lx"}), ("5000", {"unit_of_measurement": "%"})])
async def test_silent_or_foreign_sensor_gives_way_to_the_sun(hass: HomeAssistant, clock, state, attrs) -> None:
    if state is not None:
        hass.states.async_set(LUX, state, attrs)
    sun(hass, 25)
    d = await _day(hass, {"sensor": LUX, "skip": []})
    await d.refresh()
    assert (d.on, d.source) == (True, "sun")


async def test_no_source_at_all_is_unknown(hass: HomeAssistant, clock) -> None:
    d = await _day(hass)
    await d.refresh()
    assert d.on is None and not d.is_light()
    sun(hass, None)  # a sun without an elevation
    await d.refresh()
    assert d.on is None


@pytest.mark.parametrize(
    "raw",
    [
        {"skip": [], "boom": 1},
        {"lux": 99},
        {"lux": 20001},
        {"lux": True},
        {"lux": "1000"},
        {"sensor": "light.outdoor"},
        {"sensor": "sensor.Outdoor Lux"},
        {"sensor": 5},
        {"skip": ["cover.blind"]},
        {"skip": "light.niche"},
        {"skip": [f"light.l{i}" for i in range(201)]},
        [],
        "x",
    ],
)
def test_invalid_config_refused(raw) -> None:
    with pytest.raises(dl.Invalid):
        dl.clean(raw)


def test_valid_config() -> None:
    assert dl.clean({"sensor": LUX, "lux": 100, "skip": ["light.a", "switch.b", "light.a"]}) == {"sensor": LUX, "lux": 100.0, "skip": ["light.a", "switch.b"]}
    assert dl.clean({}) == {"skip": []}
    assert dl.clean({"sensor": "", "lux": None, "skip": [f"light.l{i}" for i in range(200)]})["skip"][-1] == "light.l199"


async def test_stored_junk_is_ignored(hass: HomeAssistant) -> None:
    from custom_components.hauflo.house import house

    (await house(hass).load())["daylight"] = {"skip": ["cover.x"], "evil": True}
    assert await dl.Daylight(hass).config() == {"skip": []}


async def test_skip_list(hass: HomeAssistant) -> None:
    d = await _day(hass)
    for i in range(dl.MAX_SKIP):
        hass.states.async_set(f"light.l{i}", "off")
    hass.states.async_set("light.extra", "off")
    hass.states.async_set("cover.blind", "open")
    with patch.object(dl, "MAX_SKIP", 3):
        for i in range(3):
            await d.set_skip(f"light.l{i}", True)
        with pytest.raises(dl.Invalid):
            await d.set_skip("light.extra", True)
        assert await d.set_skip("light.l1", False) == ["light.l0", "light.l2"]
    assert await d.set_skip("light.l0", True) == ["light.l2", "light.l0"]  # twice: once
    for eid, on in (("cover.blind", True), ("light.nope", True), ("light.l5", "yes"), (None, True)):
        with pytest.raises(dl.Invalid):
            await d.set_skip(eid, on)


async def test_config_by_admin_api(hass: HomeAssistant, clock) -> None:
    sun(hass, 30)
    hass.states.async_set("sensor.temp", "5", {"unit_of_measurement": "°C"})
    d = await _day(hass)
    await d.refresh()
    with pytest.raises(dl.Invalid):
        await d.set_config({"sensor": "sensor.temp"})  # not lx
    with pytest.raises(dl.Invalid):
        await d.set_config({"skip": []})  # the family's list is not set here
    lux(hass, 300)
    assert await d.set_config({"sensor": LUX, "lux": 200}) == {"skip": [], "sensor": LUX, "lux": 200.0}
    assert (d.on, d.source) == (True, "sensor")  # a new source counts at once (no hold)
    assert await d.set_config({"sensor": ""}) == {"skip": [], "lux": 200.0}
    assert d.source == "sun"
    d.async_stop()


async def test_entity_on_the_hauflo_device(hass: HomeAssistant) -> None:
    """binary_sensor.hauflo_daylight follows the sun after setup; its attributes say why."""
    sun(hass, 30)

    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    s = hass.states.get("binary_sensor.hauflo_daylight")
    assert s is not None and s.state == "on", s
    assert s.attributes["source"] == "sun" and s.attributes["value"] == 30 and s.attributes["threshold"] == 6 and s.attributes["since"]
    assert s.attributes["device_class"] == "light"
    # The sun goes down: the entity follows (on the source's own change, the first answer was at start).
    entry.runtime_data.daylight.since -= HOLD + 1
    sun(hass, -3)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.hauflo_daylight").state == "off"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_stored_bad_field_drops_only_itself(hass: HomeAssistant) -> None:
    """Tester D1: one bad stored field used to wipe the whole config (the skip list too), and the next save kept the wipe."""
    from custom_components.hauflo.house import house

    data = await house(hass).load()
    data["daylight"] = {"skip": ["light.a", "cover.x", 5], "lux": 50, "sensor": "SENSOR.bad"}
    d = dl.Daylight(hass)
    assert await d.config() == {"skip": ["light.a"], "lux": float(dl.LUX_MIN)}  # clamped; the bad ids and sensor dropped
    data["daylight"] = {"skip": ["light.a"], "lux": "much", "sensor": LUX}
    assert await d.config() == {"skip": ["light.a"], "sensor": LUX}
    hass.states.async_set("light.b", "off")
    assert await d.set_skip("light.b", True) == ["light.a", "light.b"]  # a save keeps the good fields
    assert data["daylight"] == {"skip": ["light.a", "light.b"], "sensor": LUX}


@pytest.mark.parametrize(
    ("eid", "attrs", "why"),
    [("sensor.nowhere", None, "missing"), ("sensor.no_unit", {}, "unit"), ("sensor.temp", {"unit_of_measurement": "°C"}, "unit")],
)
async def test_sensor_refused_when_set(hass: HomeAssistant, eid, attrs, why) -> None:
    """Tester D2 / D3: set = used — no silent sun later. Said in Russian and English."""
    if attrs is not None:
        hass.states.async_set(eid, "5000", attrs)
    d = dl.Daylight(hass)
    with pytest.raises(dl.SensorRefused) as err:
        await d.set_config({"sensor": eid})
    ru, en = await err.value.text(hass, True), await err.value.text(hass, False)
    assert err.value.why == why and ru != en and ru != str(err.value) and en != str(err.value), (ru, en)  # both translated
    assert "sensor" not in await d.config()


async def test_nothing_decided_while_ha_starts(hass: HomeAssistant, clock) -> None:
    """Tester D5: the first answer came from the sun before it was the sun of now, and the hold kept it 10 minutes."""
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState

    sun(hass, 30)  # left from before the restart
    hass.set_state(CoreState.starting)
    d = dl.Daylight(hass)
    await d.async_start()
    await d.refresh()  # a tick while starting: nothing
    assert d.on is None
    sun(hass, 2.74)  # the sun's first real state
    await hass.async_block_till_done()
    assert (d.on, d.value) == (False, 2.74)
    clock.t += 5
    sun(hass, 30)  # the first answer is not held; a flip within 10 min is
    await hass.async_block_till_done()
    assert d.on is False
    d.async_stop()
    d2 = dl.Daylight(hass)
    await d2.async_start()
    assert d2.on is None
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert d2.on is True and d2._start is None
    d2.async_stop()


async def test_new_silent_sensor_resets_the_old_answer(hass: HomeAssistant, clock) -> None:
    """Retest 1: no sun, a light answer from one sensor, then a dead lx sensor set — unknown at once, not the old «light»."""
    lux(hass, 5000)
    d = await _day(hass, {"sensor": LUX, "skip": []})
    await d.refresh()
    assert d.on is True
    hass.states.async_set("sensor.dead_lux", "unavailable", {"unit_of_measurement": "lx"})
    clock.t += 5  # inside the hold: a forced change of source is not held
    await d.set_config({"sensor": "sensor.dead_lux"})
    assert (d.on, d.source) == (None, None)
    assert d.is_light() is False
    d.async_stop()


@pytest.mark.parametrize(
    ("state", "reg_unit", "own_unit", "ok"),
    [
        ("unavailable", "lx", None, True),  # silent, the registry says lx
        ("unknown", None, None, True),  # silent, nothing known: read once it speaks
        ("unavailable", "°C", None, False),  # silent, the registry says it is a thermometer
        ("unavailable", "lx", "°C", False),  # the person's own unit wins
        ("unavailable", "°C", "lx", True),
        (None, "lx", None, True),  # in the registry, no state yet (its integration not up)
        ("300", None, None, False),  # live, no unit: not lx (D3)
    ],
)
async def test_silent_sensor_judged_by_the_registry(hass: HomeAssistant, state, reg_unit, own_unit, ok) -> None:
    """Retest 2: an unavailable / unknown sensor has no unit in its attributes — not «not in lux» by that alone."""
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    e = reg.async_get_or_create("sensor", "test", "lux-1", suggested_object_id="garden_lux", unit_of_measurement=reg_unit)
    if own_unit:
        reg.async_update_entity_options(e.entity_id, "sensor", {"unit_of_measurement": own_unit})
    if state is not None:
        hass.states.async_set(e.entity_id, state, {})
    d = dl.Daylight(hass)
    if ok:
        assert (await d.set_config({"sensor": e.entity_id}))["sensor"] == e.entity_id
    else:
        with pytest.raises(dl.SensorRefused) as err:
            await d.set_config({"sensor": e.entity_id})
        assert err.value.why == "unit"
    d.async_stop()
