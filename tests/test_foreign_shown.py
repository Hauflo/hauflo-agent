"""0.33 wave G (Р50/Р51): another integration's lamp / blind / AC / socket on Hauflo's own cards — shown, operated with the
viewer's own HA rights; the tunnel's dashboard check (`_lovelace_ok` → entity_ok → foreign.shown_ok) takes it, and never a
lock, an alarm panel, a gate / garage, a helper over a lock, a disabled / YAML one or a KNX entity Hauflo didn't make.
The house is test_foreign_scene_members' (one fixture, the same refusals)."""
import custom_components.hauflo as m
from custom_components.hauflo import foreign
from tests.test_foreign_scene_members import house  # noqa: F401 — the fixture

SHOWN = ["light.matter_lamp", "cover.matter_blind", "switch.relay", "light.user_group"]
REFUSED = {
    "lock.front": "domain_not_allowed",
    "alarm_control_panel.home": "domain_not_allowed",
    "light.lamp_over_lock": "protected",
    "light.group_with_lock": "protected",
    "cover.garage": "protected",
    "cover.gate_by_name": "gate",
    "light.off_by_user": "disabled",
    "light.yaml_one": "no_config_entry",
    "light.knx_by_hand": "platform_not_allowed",
    "light.unknown": "unknown_entity",
    "not an id": "domain_not_allowed",
}


async def test_shown_refusal_each(hass, house):  # noqa: F811
    for eid in SHOWN:
        assert foreign.shown_refusal(hass, eid) is None, eid
    for eid, why in REFUSED.items():
        got = foreign.shown_refusal(hass, eid)
        # A gate cover may be judged protected first (its class) — refused either way.
        assert got == why or (why in ("gate", "protected") and got in ("gate", "protected")), (eid, got)
    assert foreign.shown_refusal(None, "light.matter_lamp") == "no_hass"


def _dash(*ids):
    return {"views": [{"cards": [{"type": "custom:hauflo-room", "name": "Гостиная", "lights": [i for i in ids if i.startswith("light.")], "covers": [i for i in ids if i.startswith("cover.")]}, *({"type": "custom:hauflo-sliders", "entities": [i]} for i in ids)]}]}


async def test_a_dashboard_of_them_passes_the_tunnel_and_one_lock_refuses_it(hass, house):  # noqa: F811
    ok = ["light.matter_lamp", "cover.matter_blind"]
    assert m._lovelace_ok(hass, _dash(*ok, "light.hauflo_knx"), house["owned"], set())
    for bad in ("light.group_with_lock", "cover.garage", "light.knx_by_hand", "light.lamp_over_lock"):
        assert not m._lovelace_ok(hass, _dash(*ok, bad), house["owned"], set()), bad
