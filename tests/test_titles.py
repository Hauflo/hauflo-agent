"""The client's names of the dashboard's blocks (titles.py, owner 28.10): for the whole house, any signed-in person, kept
over deploys; text only — known kinds, one line, 40 characters; never through the tunnel."""

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo import _http_allowed
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN
from custom_components.hauflo.titles import clean


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    yield entry


async def test_any_person_renames_for_the_house(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry) -> None:
    # The client isn't an admin: allowed.
    client = await hass_client(hass_read_only_access_token)
    got = await (await client.get("/api/hauflo/titles")).json()
    assert got["titles"] == {} and "climate" in got["kinds"] and got["max"] == 40

    res = await client.post("/api/hauflo/titles", json={"titles": {"climate": "  Климат\n дома ", "lights": "", "nope": "x", "covers": "Ш" * 60}})
    assert res.status == 200
    assert (await res.json())["titles"] == {"climate": "Климат дома", "covers": "Ш" * 40}
    # Kept for everyone (another person's session reads the same).
    other = await hass_client()
    assert (await (await other.get("/api/hauflo/titles")).json())["titles"] == {"climate": "Климат дома", "covers": "Ш" * 40}

    # «Как настроил интегратор»: all back.
    assert (await (await client.post("/api/hauflo/titles", json={"titles": {}})).json())["titles"] == {}


async def test_only_text(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    for body in ({"titles": {"climate": 5}}, {"titles": ["Климат"]}, {"titles": "Климат"}, {}, []):
        assert (await client.post("/api/hauflo/titles", json=body)).status == 400
    assert (await client.post("/api/hauflo/titles", data="{")).status == 400
    # Markup is kept as text (the cards escape it), control characters go.
    assert clean({"climate": "<b>x</b>\u2028\x00y"}) == {"climate": "<b>x</b> y"}


def test_bidi_and_format_characters_go() -> None:
    # Unicode Cf (security re-review 28.10): RLO / LRI / zero-width / BOM can't flip or hide a name.
    assert clean({"lights": "\u202eТРЕВОГА\u2066x\u200by\ufeff", "climate": "\u200b\u200d"}) == {"lights": "ТРЕВОГАxy"}


def test_invisible_letters_go_and_a_blank_name_is_the_integrators() -> None:
    # Verifier 28.10: Hangul fillers, the blank braille cell, CGJ draw nothing — a name of them only looks empty.
    blank = "\u3164\u2800\u034f\u115f\u1160\uffa0\u17b4\u17b5"
    assert clean({"lights": blank, "climate": f" {blank} ", "covers": f"Шт\u3164оры\u2800"}) == {"covers": "Шторы"}
    assert clean({"lights": "\u2028Свет\u0085дома\u2029"}) == {"lights": "Свет дома"}


async def test_needs_a_signed_in_person(hass: HomeAssistant, hass_client_no_auth, entry) -> None:
    client = await hass_client_no_auth()
    assert (await client.get("/api/hauflo/titles")).status == 401
    assert (await client.post("/api/hauflo/titles", json={"titles": {"climate": "x"}})).status == 401


async def test_tunnel_cannot_rename(hass: HomeAssistant) -> None:
    assert not _http_allowed(hass, "POST", "/api/hauflo/titles", {"titles": {"climate": "x"}})
    assert not _http_allowed(hass, "GET", "/api/hauflo/titles", "")


async def test_kept_after_a_restart_of_the_integration(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    await client.post("/api/hauflo/titles", json={"titles": {"floor_heating": "Полы"}})
    async def idle(self):
        return None

    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert (await (await client.get("/api/hauflo/titles")).json())["titles"] == {"floor_heating": "Полы"}


def test_marks_only_name_is_the_integrators() -> None:
    """Re-verifier 28.10: combining marks / variation selectors alone survived and looked blank."""
    marks = {"lights": "\u0301\u0301", "climate": " \ufe0f\ufe0f ", "covers": "\u20dd\U000e0100"}
    assert clean(marks) == {}
    assert clean({"lights": "Свет\u0301", "scenes": "\u2764\ufe0f", "sockets": "1", "covers": "!"}) == {
        "lights": "Свет\u0301",
        "scenes": "\u2764\ufe0f",
        "sockets": "1",
        "covers": "!",
    }
