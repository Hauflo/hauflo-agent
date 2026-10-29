"""The service passport's facts in the status (service review r2 29.10, D5): how KNX is connected, the HA address and its
administrators' names — an allowlist; KNX Secure keys, passwords, logins never leave the house."""

import json
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hauflo as hauflo
from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):  # the link loop: no Hauflo server in tests
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "top-secret"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


async def test_monitoring_accept_names_the_hauflo_person_only_for_the_agent_user(hass: HomeAssistant, hass_client, entry) -> None:
    """D8: «Принять» in Hauflo's monitoring goes through the tunnel as the agent's system user, with the person's name;
    any other user's «by» is ignored (their own name stays). The status lists the accept key."""
    eng = entry.runtime_data.engine
    eng.owns = lambda eid: True

    async def fake_send(rule, title, message, **kw):
        return []

    with patch.object(eng, "_send", fake_send):
        for eid in ("binary_sensor.leak", "binary_sensor.leak2"):
            hass.states.async_set(eid, "off", {"friendly_name": "Протечка", "device_class": "moisture"})
        await eng.async_set({"rules": [{"id": "r", "kind": "leak", "title": "Протечка", "sources": ["binary_sensor.leak", "binary_sensor.leak2"], "priority": "critical"}], "groups": []})
        for eid in ("binary_sensor.leak", "binary_sensor.leak2"):
            hass.states.async_set(eid, "on", {"friendly_name": "Протечка", "device_class": "moisture"})
        await hass.async_block_till_done()
        keys = {a["entityId"]: a["key"] for a in eng.active_list()}
        assert keys["binary_sensor.leak"] == "r|binary_sensor.leak"
        agent_user = await hass.auth.async_create_system_user("Hauflo agent")
        token = hass.auth.async_create_access_token(await hass.auth.async_create_refresh_token(agent_user))
        tunnel = await hass_client(token)
        assert (await tunnel.post("/api/hauflo/alarms", json={"ack": keys["binary_sensor.leak"], "by": "Игорь Сервисов\n"})).status == 200
        admin = await hass_client()
        assert (await admin.post("/api/hauflo/alarms", json={"ack": keys["binary_sensor.leak2"], "by": "Подделка"})).status == 200
        acked = {a["entityId"]: a["ackedBy"] for a in eng.active_list()}
        assert acked["binary_sensor.leak"] == "Игорь Сервисов (Hauflo)"
        assert "Подделка" not in str(acked["binary_sensor.leak2"])


def test_tunnel_accepts_alarms_even_in_a_locked_house() -> None:
    assert ("/api/hauflo/alarms", "POST") in hauflo._HAUFLO_VIEWS
    assert hauflo._http_locked("POST", "/api/hauflo/alarms") is False

SECRETS = {"user_password": "pw-1", "device_authentication": "dev-2", "backbone_key": "bb-3", "knxkeys_password": "kk-4", "knxkeys_filename": "knx/keys.knxkeys", "user_id": 3, "sync_latency_tolerance": 1000}


def test_knx_conn_allowlist_only():
    data = {"connection_type": "tunneling_tcp_secure", "host": "192.168.1.10", "port": 3671, "local_ip": "192.168.1.5", "route_back": False, "individual_address": "15.15.250", "tunnel_endpoint_ia": "1.1.240", "rate_limit": 0, **SECRETS}
    out = hauflo.knx_conn(data)
    assert out == {"type": "tunneling_tcp_secure", "host": "192.168.1.10", "port": 3671, "localIp": "192.168.1.5", "routeBack": False, "individualAddress": "15.15.250", "tunnelEndpoint": "1.1.240", "secure": True}
    text = json.dumps(out)
    for v in ("pw-1", "dev-2", "bb-3", "kk-4", "keys.knxkeys"):
        assert v not in text


def test_knx_conn_routing_and_garbage():
    assert hauflo.knx_conn({"connection_type": "routing", "multicast_group": "224.0.23.12", "multicast_port": 3671, "individual_address": "1.1.255"}) == {"type": "routing", "multicastGroup": "224.0.23.12", "multicastPort": 3671, "individualAddress": "1.1.255", "secure": False}
    assert hauflo.knx_conn(None) is None
    assert hauflo.knx_conn({"host": {"nested": 1}, "port": -5})["secure"] is False


def test_ha_url_without_login_query_or_fragment():
    assert hauflo.ha_url("https://user:pw@home.example.org:8443/ha/?token=x#y") == "https://home.example.org:8443/ha"
    assert hauflo.ha_url("http://192.168.1.20:8123") == "http://192.168.1.20:8123"
    assert hauflo.ha_url("ftp://x") is None
    assert hauflo.ha_url("") is None
    assert hauflo.ha_url(None) is None


def test_tunnel_reads_hand_edits_but_never_writes_them():
    assert ("/api/hauflo/ownership", "GET") in hauflo._HAUFLO_VIEWS
