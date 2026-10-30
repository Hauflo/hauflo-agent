"""pytest-homeassistant-custom-component: the integration under custom_components/ is loaded for each test."""

import pytest
from pytest_homeassistant_custom_component.common import mock_component

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations, hass):
    # The frontend package (hass_frontend) is not in the test requirements; the flow never serves panels.
    mock_component(hass, "frontend")
    yield


@pytest.fixture(autouse=True)
def _no_quiet_hours(monkeypatch):
    """Quiet hours are on by default in the canon; the other tests run at any time of day — they switch it off here,
    test_quiet.py turns it on through the API."""
    from custom_components.hauflo import quiet

    real = quiet.default
    monkeypatch.setattr(quiet, "default", lambda: {**real(), "on": False})
