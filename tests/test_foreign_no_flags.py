"""foreign.py without the agent's flags (not set up) and the flags' own small methods: nothing is let through, nothing breaks."""

from homeassistant.core import HomeAssistant

from custom_components.hauflo import foreign


async def test_without_flags_nothing_is_allowed_or_reverted(hass: HomeAssistant):
    assert foreign.flags(hass) is None
    assert foreign.wizard_refusal(None, {"type": foreign.DEVICE_UPDATE}) == "not_allowed"
    assert foreign.wizard_refusal(hass, {"type": "config/other"}) == "not_allowed"
    assert foreign.revert(hass, {"d1"}, {"e1"}) == {"devices": [], "entities": [], "kept": {}, "unknown": []}
    assert foreign._journal_list(hass) == {"devices": [], "entities": []}


async def test_flags_set_notify_and_remove(hass: HomeAssistant):
    f = await foreign.async_setup(hass)
    f.set_notify({"light.a": True})
    assert f.notify == {"light.a": True}
    await f.async_remove()
