"""KNX scene learn from the panel (Д19, owner 28.10: «сцены KNX тоже должны пересохраняться»): a long press on a KNX
scene → «Запомнить как сейчас» sends ONE telegram, 0x80 | (number − 1) (DPT 18.001, learn bit), to that scene's address.
The browser names a scene entity only; the agent takes the address and the number from the KNX store. Never: another
address, another value, a scene that isn't Hauflo's, an address the owner didn't allow, an address something but scenes
listen on, a locked house, a burst. «Переименовать» — a name on the panel, kept by the agent."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import dump
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.hauflo import _http_allowed, scenes as scenes_mod
from custom_components.hauflo.const import CONF_ALLOW_CHANGES, CONF_RANGES, CONF_SECRET, CONF_SERVER, DOMAIN, LOCKED_CODE

URL = "/api/hauflo/scenes"
HAUFLO = {"id": "hauflo_bed_bright", "name": "Яркий", "entities": {"light.bed_ceiling": {"state": "on"}}}


def _ga(text: str) -> int:
    a, b, c = (int(x) for x in text.split("/"))
    return (a << 11) | (b << 8) | c


class _GA:  # xknx's GroupAddress: hashable, its number as .raw
    def __init__(self, raw: int) -> None:
        self.raw = raw

    def __hash__(self) -> int:
        return self.raw


def _scene(ga: str, number) -> dict:
    return {"entity": {"name": "x"}, "knx": {"ga_scene": {"write": ga}, "scene_number": number}}


# uid → (object id, KNX config, Hauflo's)
SCENES = {
    "u-sc3": ("bed_evening", _scene("1/5/0", 3), True),
    "u-sc1": ("bed_bright", _scene("1/5/0", 1), True),
    "u-hand-sc": ("bed_hand", _scene("1/5/0", 4), False),  # the integrator's own KNX scene, same address — a scene too
    "u-far": ("hall_far", _scene("2/1/0", 1), True),  # outside what the owner allowed
    "u-sw": ("kitchen_scene", _scene("1/5/5", 2), True),  # a switch object of the integrator's listens there too
    "u-yaml": ("bath_scene", _scene("1/5/6", 2), True),  # a YAML switch listens there
    "u-n65": ("odd_65", _scene("1/5/7", 65), True),
    "u-nbool": ("odd_bool", _scene("1/5/8", True), True),
    "u-nstr": ("odd_str", _scene("1/5/9", "3"), True),
    "u-badga": ("odd_ga", _scene("1/5/300", 1), True),
}


def eid(uid: str) -> str:
    return f"scene.{SCENES[uid][0]}"


@pytest.fixture
async def entry(hass: HomeAssistant, tmp_path: Path):
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text("scene: !include scenes.yaml\n")
    (tmp_path / "scenes.yaml").write_text(dump([HAUFLO]))
    reg = er.async_get(hass)
    for uid, (obj, _cfg, _mine) in SCENES.items():
        reg.async_get_or_create("scene", "knx", uid, suggested_object_id=obj)
        hass.states.async_set(f"scene.{obj}", "unknown")
    assert await async_setup_component(hass, "scene", {"scene": [HAUFLO]})
    await hass.async_block_till_done()

    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — test", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"}, options={CONF_RANGES: ["1/5/0-10"], CONF_ALLOW_CHANGES: True})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.owned |= {uid for uid, (_o, _c, mine) in SCENES.items() if mine}
    hass.data["knx"] = SimpleNamespace(
        config_store=SimpleNamespace(data={"entities": {
            "scene": {uid: cfg for uid, (_o, cfg, _m) in SCENES.items()},
            "switch": {"u-isw": {"knx": {"ga_switch": {"write": "7/7/7", "state": "1/5/5", "passive": []}}}},
        }}),
        group_address_entities={_GA(_ga("1/5/6")): [SimpleNamespace(unique_id="yaml-sw", ui=False, platform="switch")]},
        xknx=SimpleNamespace(devices=[]),
    )
    yield entry


@pytest.fixture
def sent(hass: HomeAssistant):
    return async_mock_service(hass, "knx", "send")


async def test_learn_sends_the_scene_byte_only(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry, sent) -> None:
    """A non-admin person; whatever else the body says (an address, a payload), one telegram to the scene's own address."""
    client = await hass_client(hass_read_only_access_token)
    res = await client.post(URL, json={"op": "knx_learn", "entity_id": eid("u-sc3"), "address": "7/7/1", "payload": [1], "scene_number": 64})
    assert res.status == 200, await res.text()
    assert (await res.json()) == {"sent": True, "entity_id": eid("u-sc3"), "scene_number": 3}
    assert len(sent) == 1
    assert dict(sent[0].data) == {"address": "1/5/0", "payload": [0x82]}


async def test_get_lists_learnable_only(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    body = await (await client.get(URL)).json()
    # The integrator's own, outside the ranges, a shared address, bad numbers / addresses: not offered.
    assert body["knx"] == {eid("u-sc3"): 3, eid("u-sc1"): 1}
    assert body["names"] == {}


@pytest.mark.parametrize(
    "target",
    [
        "u-hand-sc", "u-far", "u-sw", "u-yaml", "u-n65", "u-nbool", "u-nstr", "u-badga",
        "scene.hauflo_bed_bright", "light.bed_ceiling", "scene.nope", "scene.BED_EVENING", "scene.bed_evening ", None, ["scene.bed_evening"], {"entity_id": "scene.bed_evening"}, 7,
    ],
)
async def test_learn_refused(hass: HomeAssistant, hass_client, entry, sent, target) -> None:
    client = await hass_client()
    res = await client.post(URL, json={"op": "knx_learn", "entity_id": eid(target) if isinstance(target, str) and target in SCENES else target})
    assert res.status == 403, await res.text()
    # Each for its own reason (not one catch-all that would hide a hole in another check).
    why = {"u-far": "allowed", "u-sw": "only scenes", "u-yaml": "only scenes", "u-hand-sc": "of Hauflo's"}
    if isinstance(target, str) and target in why:
        assert why[target] in (await res.json())["message"]
    assert sent == []


async def test_learn_refused_without_ranges_option_for_foreign_address(hass: HomeAssistant, hass_client, entry, sent) -> None:
    """A site paired before the ranges option: only what Hauflo's own entities write — the scene's own address is."""
    hass.config_entries.async_update_entry(entry, options={CONF_ALLOW_CHANGES: True})
    client = await hass_client()
    assert (await client.post(URL, json={"op": "knx_learn", "entity_id": eid("u-sc1")})).status == 200
    assert dict(sent[0].data) == {"address": "1/5/0", "payload": [0x80]}
    # The owner narrowed what Hauflo may send to: the scene's address is no longer in it.
    hass.config_entries.async_update_entry(entry, options={CONF_RANGES: ["1/1"], CONF_ALLOW_CHANGES: True})
    assert (await client.post(URL, json={"op": "knx_learn", "entity_id": eid("u-sc3")})).status == 403
    assert len(sent) == 1


async def test_locked_house(hass: HomeAssistant, hass_client, entry, sent) -> None:
    hass.config_entries.async_update_entry(entry, options={CONF_RANGES: ["1/5/0-10"], CONF_ALLOW_CHANGES: False})
    client = await hass_client()
    res = await client.post(URL, json={"op": "knx_learn", "entity_id": eid("u-sc3")})
    assert res.status == 423
    assert (await res.json())["code"] == LOCKED_CODE
    assert sent == []


async def test_rate_limit(hass: HomeAssistant, hass_client, entry, sent) -> None:
    client = await hass_client()
    clock = [1000.0]
    with patch.object(scenes_mod, "_clock", lambda: clock[0]):
        assert (await client.post(URL, json={"op": "knx_learn", "entity_id": eid("u-sc3")})).status == 200
        res = await client.post(URL, json={"op": "knx_learn", "entity_id": eid("u-sc3")})
        body = await res.json()
        assert res.status == 429 and body["code"] == "too_soon" and "Wait a few seconds" in body["message"]
        # Another scene: its own clock.
        assert (await client.post(URL, json={"op": "knx_learn", "entity_id": eid("u-sc1")})).status == 200
        clock[0] += scenes_mod.LEARN_EVERY + 0.1
        assert (await client.post(URL, json={"op": "knx_learn", "entity_id": eid("u-sc3")})).status == 200
        # The whole house: a few a minute.
        for _ in range(scenes_mod.LEARN_PER_MINUTE):
            clock[0] += scenes_mod.LEARN_EVERY + 0.1
            await client.post(URL, json={"op": "knx_learn", "entity_id": eid("u-sc1")})
        assert len(sent) == scenes_mod.LEARN_PER_MINUTE
        # The house-wide limit says so — not «just sent» (tester 29.10).
        clock[0] += scenes_mod.LEARN_EVERY + 0.1
        res = await client.post(URL, json={"op": "knx_learn", "entity_id": eid("u-sc3")})
        body = await res.json()
        assert res.status == 429 and body["code"] == "too_soon"
        assert body["message"] == f"No more than {scenes_mod.LEARN_PER_MINUTE} scenes a minute — wait a minute."
        assert len(sent) == scenes_mod.LEARN_PER_MINUTE
        clock[0] += 61
        assert (await client.post(URL, json={"op": "knx_learn", "entity_id": eid("u-sc1")})).status == 200
    assert all(dict(c.data)["address"] == "1/5/0" and dict(c.data)["payload"] in ([0x80], [0x82]) for c in sent)


async def test_panel_name(hass: HomeAssistant, hass_client, hass_read_only_access_token, entry, sent) -> None:
    client = await hass_client(hass_read_only_access_token)
    res = await client.post(URL, json={"op": "knx_name", "entity_id": eid("u-sc3"), "name": "  Вечер‮ "})
    assert res.status == 200, await res.text()
    assert (await (await client.get(URL)).json())["names"] == {eid("u-sc3"): "Вечер"}
    # HA's own name is left as it is (voice, HA's lists).
    assert er.async_get(hass).async_get(eid("u-sc3")).name is None
    assert (await client.post(URL, json={"op": "knx_name", "entity_id": eid("u-sc3"), "name": ""})).status == 200
    assert (await (await client.get(URL)).json())["names"] == {}
    for target in (eid("u-hand-sc"), "scene.hauflo_bed_bright", "light.bed_ceiling", None):
        assert (await client.post(URL, json={"op": "knx_name", "entity_id": target, "name": "x"})).status == 403
    assert (await client.post(URL, json={"op": "knx_name", "entity_id": eid("u-sc3"), "name": 5})).status == 400
    assert sent == []


async def test_no_delete_of_knx_scene(hass: HomeAssistant, hass_client, entry) -> None:
    client = await hass_client()
    for op in ("delete", "rename", "recapture"):
        assert (await client.post(URL, json={"op": op, "entity_id": eid("u-sc3"), "name": "x", "entities": ["light.x"]})).status == 403, op
    assert er.async_get(hass).async_get(eid("u-sc3")) is not None


async def test_tunnel_cannot_learn(hass: HomeAssistant) -> None:
    body = {"op": "knx_learn", "entity_id": "scene.bed_evening"}
    assert not _http_allowed(hass, "POST", URL, body)
