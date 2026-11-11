"""0.33 С3: everything the house says by itself — the alarms' pushes, the value rules' and the season's pushes, a
guest's request to the admins — in ONE language: the project's (the alarm engine's `lang`, as Hauflo wrote it), else
Home Assistant's. The acceptance saw «🚨 Equipment alarm» over a Russian push: a canon title was turned only from
Russian to English, and the value rules spoke HA's language whatever the project's was."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.hauflo import guest, notify, rules as rules_mod, season
from custom_components.hauflo.const import site_ru

RULE = {"id": "fault", "kind": "equipment", "sources": ["binary_sensor.boiler_fault"], "alarm_states": ["on"], "priority": "critical", "recipients": {"all": True}}


def test_site_ru_the_project_first_then_ha() -> None:
    assert site_ru("en", {"lang": "ru"}) is True
    assert site_ru("ru", {"lang": "en"}) is False
    assert site_ru("en", {}) is False and site_ru("ru-RU", None) is True
    assert site_ru(None, None) is True  # nothing said: Russian, as the engine always did
    assert site_ru("en", {"lang": "  "}) is False  # an empty project language is not one


@pytest.mark.parametrize(
    ("lang", "given", "said"),
    [
        ("ru", "Equipment alarm", "Авария оборудования"),  # the acceptance's case: made on an English page
        ("ru", "Авария оборудования", "Авария оборудования"),
        ("en", "Авария оборудования", "Equipment alarm"),
        ("en", "Water leak", "Water leak"),
        ("ru", "Water leak", "Протечка"),
        ("ru", "Котёл в котельной", "Котёл в котельной"),  # typed by the integrator: as is
        ("en", "Boiler room", "Boiler room"),
    ],
)
def test_a_canon_title_in_the_house_language_both_ways(lang: str, given: str, said: str) -> None:
    out = notify._clean_rules({"lang": lang, "rules": [{**RULE, "title": given}]})
    assert out["rules"][0]["title"] == said


async def test_the_title_by_ha_language_when_the_project_has_none(hass: HomeAssistant) -> None:
    hass.config.language = "en"
    assert notify._clean_rules({"rules": [{**RULE, "title": "Авария оборудования"}]}, hass)["rules"][0]["title"] == "Equipment alarm"
    hass.config.language = "ru"
    assert notify._clean_rules({"rules": [{**RULE, "title": "Equipment alarm"}]}, hass)["rules"][0]["title"] == "Авария оборудования"


async def test_value_rules_and_season_speak_the_alarms_language(hass: HomeAssistant) -> None:
    """HA in English, the project in Russian: the value rules' pushes in Russian, as the alarms' on the same phone."""
    hass.config.language = "en"
    engine = SimpleNamespace(config={"lang": "ru"})
    r = rules_mod.Rules(hass, owns=lambda e: True, own_raw=lambda e: True, notify_of=lambda: engine)
    assert r.ru is True
    s = season.Season(hass, lambda: engine, lambda: r, lambda e: True)
    assert s._lang() == "ru"
    engine.config = {"lang": "en"}
    hass.config.language = "ru"
    assert r.ru is False and s._lang() == "en"
    # No alarm engine yet (the agent loading): HA's language, as before.
    bare = rules_mod.Rules(hass, owns=lambda e: True, own_raw=lambda e: True)
    assert bare.ru is True
    hass.config.language = "en"
    assert bare.ru is False


async def test_a_guest_request_to_the_admins_in_the_house_language(hass: HomeAssistant) -> None:
    hass.config.language = "en"
    user = SimpleNamespace(id="u1", name="Марина")
    with patch("homeassistant.components.persistent_notification.async_create") as note:
        await guest.ask_admins(hass, user, "", [], {"lang": "ru"})
        assert note.call_args.kwargs["title"] == "Гостевой доступ" and "просит гостевую ссылку" in note.call_args.args[1]
        await guest.ask_admins(hass, user, "", [])
        assert note.call_args.kwargs["title"] == "Guest access"
