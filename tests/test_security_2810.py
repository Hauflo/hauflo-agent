"""Security review 28.10 (agent 0.14.4, live): what the tunnel's reads gave away — presence by motion / door sensors with
their times, every panel of the house, any integration's manifest."""

import custom_components.hauflo as agent_mod

T = {"last_changed": "2026-10-28T01:02:03+00:00", "last_updated": "x", "last_reported": "x", "context": {"user_id": "u"}}


def _st(eid: str, dc: str | None, state: str = "on") -> dict:
    return {"entity_id": eid, "state": state, "attributes": {"device_class": dc, "friendly_name": eid}, **T}


def test_presence_states_are_hidden_unless_hauflos_or_marked() -> None:
    visible = {"binary_sensor.knx_door", "binary_sensor.marked_motion"}.__contains__
    states = [
        _st("binary_sensor.hall_motion", "motion"),
        _st("binary_sensor.bedroom_occupancy", "occupancy"),
        _st("binary_sensor.office_presence", "presence"),
        _st("binary_sensor.front_door", "door"),
        _st("binary_sensor.kitchen_window", "window"),
        _st("binary_sensor.gate", "opening"),
        _st("binary_sensor.garage", "garage_door"),
        _st("binary_sensor.front_lock", "lock"),
        _st("binary_sensor.knx_door", "door"),  # Hauflo's own
        _st("binary_sensor.marked_motion", "motion"),  # marked «Hauflo мониторинг»
        _st("binary_sensor.leak", "moisture"),  # not presence
    ]
    out = {s["entity_id"]: s for s in agent_mod._scrub_states(states, None, visible)}
    for eid in ("binary_sensor.hall_motion", "binary_sensor.bedroom_occupancy", "binary_sensor.office_presence", "binary_sensor.front_door", "binary_sensor.kitchen_window", "binary_sensor.gate", "binary_sensor.garage", "binary_sensor.front_lock"):
        s = out[eid]
        assert s["state"] == "hidden", eid
        assert not set(s) & {"last_changed", "last_updated", "last_reported", "context"}, eid
        assert s["attributes"] == {"device_class": s["attributes"]["device_class"], "friendly_name": eid}  # what it is, not what it does
    for eid in ("binary_sensor.knx_door", "binary_sensor.marked_motion", "binary_sensor.leak"):
        assert out[eid]["state"] == "on" and out[eid]["last_changed"], eid
    # No owner check given (fail closed): hidden.
    assert agent_mod._scrub_states([_st("binary_sensor.knx_door", "door")], None)[0]["state"] == "hidden"
    # A visibility check that breaks: hidden.
    assert agent_mod._scrub_states([_st("binary_sensor.knx_door", "door")], None, lambda e: 1 / 0)[0]["state"] == "hidden"


def test_manifest_only_hauflo_and_knx() -> None:
    w = lambda integ: agent_mod._ws_allowed({"type": "manifest/get", "integration": integ})  # noqa: E731
    assert w("hauflo") and w("knx")
    assert not w("mobile_app") and not w("alarmo") and not w(None) and not w(["hauflo"])


def test_panels_only_menu_extras() -> None:
    panels = {k: {"url_path": k, "config": {"x": 1}} for k in ("lovelace", "map", "energy", "hassio", "frigate", "my-iframe", "config", "todo")}
    assert agent_mod._scrub_panels(panels) == {"map": {"url_path": "map"}, "energy": {"url_path": "energy"}, "todo": {"url_path": "todo"}}
