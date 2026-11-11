"""Guest access — refusals and failure paths: an unknown link is never revoked by guess, a read-only person never hands
control on, a broken HA internal fails closed, the dashboards unreadable offer nothing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.hauflo import guest as G

from .test_guest import _issue, entry, person  # noqa: F401 — the module's fixtures


async def test_revoking_an_unknown_link_is_refused_and_touches_nothing(hass: HomeAssistant, entry, person) -> None:  # noqa: F811
    engine = entry.runtime_data.guest
    token, res = await _issue(engine, person, [entry.living])
    with pytest.raises(G.Invalid):
        await engine.revoke(person, "nosuchlink0")
    assert (await engine.handle({"token": token, "device": "d" * 20, "op": "state"}))[0] == 200  # the real one still works


async def test_pages_not_readable_offer_nothing(hass: HomeAssistant, entry) -> None:  # noqa: F811
    engine = entry.runtime_data.guest
    engine.dashboards = None
    assert await engine.available() == []
    engine.dashboards = SimpleNamespace(page_list=AsyncMock(side_effect=RuntimeError("storage gone")))
    assert await engine.available() == []
    engine.dashboards = SimpleNamespace(page_list=AsyncMock(return_value=[{"dashboard": "d", "view": "v"}]))
    assert await engine.available() == [{"dashboard": "d", "view": "v"}]


def test_issuer_and_control_fail_closed():
    assert not G._issuer_ok(None)
    assert not G._issuer_ok(SimpleNamespace(system_generated=True, is_active=True))
    assert not G._issuer_ok(SimpleNamespace(system_generated=False, is_active=False))
    broken = SimpleNamespace(system_generated=False, is_active=True, is_admin=False, permissions=SimpleNamespace(access_all_entities=MagicMock(side_effect=RuntimeError("changed"))))
    assert not G._issuer_ok(broken)
    assert not G._may_control(SimpleNamespace(is_admin=False, permissions=None), "light.a")
    assert G._may_control(SimpleNamespace(is_admin=True), "light.a")
    ok = SimpleNamespace(is_admin=False, permissions=SimpleNamespace(check_entity=lambda e, p: e == "light.a"))
    assert G._may_control(ok, "light.a") and not G._may_control(ok, "light.b")


async def test_area_of_an_entity_follows_its_device(hass: HomeAssistant, entry) -> None:  # noqa: F811
    from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    engine = entry.runtime_data.guest
    assert engine._area("light.ghost") is None
    area = ar.async_get(hass).async_create("Кухня")
    cfg = MockConfigEntry(domain="test")
    cfg.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=cfg.entry_id, identifiers={("test", "1")})
    dr.async_get(hass).async_update_device(dev.id, area_id=area.id)
    reg = er.async_get(hass)
    reg.async_get_or_create("light", "test", "x1", suggested_object_id="x1", device_id=dev.id, config_entry=cfg)
    assert engine._area("light.x1") == area.id
    reg.async_get_or_create("light", "test", "x2", suggested_object_id="x2")
    assert engine._area("light.x2") is None
