"""pytest-homeassistant-custom-component: the integration under custom_components/ is loaded for each test."""

import pytest
from pytest_homeassistant_custom_component.common import mock_component

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations, hass):
    # The frontend package (hass_frontend) is not in the test requirements; the flow never serves panels.
    mock_component(hass, "frontend")
    yield
