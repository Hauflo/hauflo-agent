"""399-18: another integration's thermostat (a Daikin / Tado climate.*) is a pair of its own once the integrator monitors
it — labelled «Hauflo мониторинг», in an alarm rule (it or another entity of its device), or confirmed by hand from the
offer; never judged before that (w6a2 D-B10 stays: a foreign demo climate is not paired on its own). The view says whose
it is (the integration)."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo.insight import Insights

from .test_noeffect import STEP, Clock, det, run

ATTRS = {"friendly_name": "Daikin гостиная", "temperature": 22.0, "current_temperature": 21.0, "hvac_action": "heating"}


async def _house(hass: HomeAssistant):
    area = ar.async_get(hass).async_create("Гостиная")
    ce = MockConfigEntry(domain="daikin", title="Daikin BRP069")
    ce.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=ce.entry_id, identifiers={("daikin", "unit1")}, name="Daikin")
    r = er.async_get(hass)
    clim = r.async_get_or_create("climate", "daikin", "unit1", suggested_object_id="daikin_living", config_entry=ce, device_id=dev.id).entity_id
    err = r.async_get_or_create("binary_sensor", "daikin", "unit1_err", suggested_object_id="daikin_error", config_entry=ce, device_id=dev.id).entity_id
    other = r.async_get_or_create("climate", "tado", "z2", suggested_object_id="tado_bed").entity_id
    dr.async_get(hass).async_update_device(dev.id, area_id=area.id)
    r.async_update_entity(other, area_id=area.id)
    hass.states.async_set(clim, "heat", ATTRS)
    hass.states.async_set(other, "heat", {**ATTRS, "friendly_name": "Tado спальня"})
    hass.states.async_set(err, "off", {"device_class": "problem"})
    watched: set[str] = set()
    ins = Insights(hass, lambda eid: False, lambda: None)
    ins.watched = lambda: watched
    ins.clock = Clock()
    await ins.async_start()
    return ins, clim, err, other, watched


async def test_foreign_climate_is_offered_not_judged_until_monitored(hass: HomeAssistant) -> None:
    ins, clim, err, other, watched = await _house(hass)
    try:
        await run(hass, ins, STEP)
        d = det(ins)
        view = d.pairs_view()
        assert view["pairs"] == []
        offered = {o["effect"]: o for o in view["offered"]}
        assert offered[clim]["kind"] == "air" and offered[clim]["integration"] == "Daikin BRP069" and offered[clim]["platform"] == "daikin"
        assert offered[other]["integration"] == "tado"  # no config entry: the integration's domain
        # Its device's error sensor in an alarm rule: the climate is watched → its own pair, not offered any more.
        watched.add(err)
        d._paired_at = -10**9
        await run(hass, ins, STEP)
        view = d.pairs_view()
        pair = next(p for p in view["pairs"] if p["effect"] == clim)
        assert pair["exec"] == clim and pair["integration"] == "Daikin BRP069"
        assert clim not in {o["effect"] for o in view["offered"]} and other in {o["effect"] for o in view["offered"]}
        # «Следить» on the other one: the integrator's pair.
        d.set_pair(other, "air", other)
        await run(hass, ins, STEP)
        view = d.pairs_view()
        assert {p["effect"] for p in view["pairs"]} == {clim, other} and view["offered"] == []
        # The rule gone: the watched one goes back to an offer; the hand-made pair stays.
        watched.clear()
        d._paired_at = -10**9
        await run(hass, ins, STEP)
        view = d.pairs_view()
        assert {p["effect"] for p in view["pairs"]} == {other} and [o["effect"] for o in view["offered"]] == [clim]
    finally:
        ins.async_stop()


async def test_hidden_or_own_climate_is_never_offered(hass: HomeAssistant) -> None:
    ins, clim, err, other, watched = await _house(hass)
    try:
        er.async_get(hass).async_update_entity(other, hidden_by=er.RegistryEntryHider.USER)
        ins.owns = lambda eid: eid == clim
        await run(hass, ins, STEP)
        assert det(ins).pairs_view()["offered"] == []
    finally:
        ins.async_stop()
