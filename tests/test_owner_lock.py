"""«Allow Hauflo to change this home's setup» (owner 28.10): a security boundary. With it off every write through the
tunnel is refused with «locked_by_owner» — each allowlisted websocket type and REST route is tried — reads, monitoring
and alarm delivery pass; the allowlist itself is pinned so it can't widen unnoticed. The switch lives in the entry's
options: it survives a restart and takes effect at once, with no reload."""

import json
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hauflo as agent_mod
from custom_components.hauflo.const import CONF_ALLOW_CHANGES, CONF_RANGES, CONF_SECRET, CONF_SERVER, DATA_PENDING_RANGES, DOMAIN, LOCKED_CODE

# The tunnel's websocket allowlist as it is today: a new type is a decision to make (and to classify here), not a slip.
ALLOWLIST = {
    "get_states", "get_config", "ping", "unsubscribe_events", "persistent_notification/subscribe", "cloud/status", "frontend/get_themes", "manifest/get", "get_panels",
    "config_entries/get",
    "config/area_registry/list", "config/area_registry/create", "config/area_registry/update",
    "config/floor_registry/list", "config/floor_registry/create", "config/floor_registry/update",
    "config/label_registry/list", "config/label_registry/create", "config/label_registry/delete",
    "config/device_registry/list", "config/device_registry/update", "config/device_registry/remove", "config/device_registry/remove_config_entry",
    "config/entity_registry/list", "config/entity_registry/get", "config/entity_registry/get_entries", "config/entity_registry/update",
    "knx/get_knx_project", "knx/get_entity_config", "knx/validate_entity", "knx/create_entity", "knx/update_entity",
    "knx/delete_entity", "knx/create_device", "knx/get_entities_by_group", "knx/group_monitor_info", "knx/get_expose_groups", "knx/get_time_server_config",
    "lovelace/config", "lovelace/config/save", "lovelace/dashboards/list", "lovelace/dashboards/create", "lovelace/dashboards/update",
    "homeassistant/expose_entity", "homeassistant/expose_entity/list",
}
# What changes the house — refused while locked. Listed by hand: moving one into «reads» must fail this test.
WRITES = {
    "config/area_registry/create", "config/area_registry/update", "config/floor_registry/create", "config/floor_registry/update",
    "config/label_registry/create", "config/label_registry/delete",
    "config/device_registry/update", "config/device_registry/remove", "config/device_registry/remove_config_entry", "config/entity_registry/update",
    "knx/create_entity", "knx/update_entity", "knx/delete_entity", "knx/create_device",
    "lovelace/config/save", "lovelace/dashboards/create", "lovelace/dashboards/update",
    "homeassistant/expose_entity",
}
SERVICES = {("homeassistant", "update_entity"): False, ("frontend", "set_theme"): True, ("scene", "reload"): False}
# Every REST route the tunnel lets through, with whether it changes the house.
HTTP = [
    ("GET", "/api/", False), ("GET", "/api/config", False),
    ("GET", "/api/hauflo/backup", False), ("POST", "/api/hauflo/backup", True),
    ("GET", "/api/hauflo/notify", False), ("POST", "/api/hauflo/notify", False), ("POST", "/api/hauflo/notify/test", False),
    ("GET", "/api/hauflo/alarms", False), ("POST", "/api/hauflo/sidebar", True), ("GET", "/api/hauflo/owned", False), ("GET", "/api/hauflo/ranges", False),
    ("GET", "/api/hauflo/comfort", False), ("POST", "/api/hauflo/comfort", True),
    ("GET", "/api/homekit/pairingqr?e1-x", False),
    ("GET", "/api/config/scene/config/hauflo_r1", False), ("POST", "/api/config/scene/config/hauflo_r1", True), ("DELETE", "/api/config/scene/config/hauflo_r1", True),
    ("GET", "/api/config/config_entries/flow_handlers", False), ("POST", "/api/config/config_entries/flow", True),
    ("GET", "/api/config/config_entries/flow/f1", False), ("POST", "/api/config/config_entries/flow/f1", True), ("DELETE", "/api/config/config_entries/flow/f1", True),
    ("POST", "/api/config/config_entries/options/flow", True),
    ("GET", "/api/config/config_entries/options/flow/o1", False), ("POST", "/api/config/config_entries/options/flow/o1", True), ("DELETE", "/api/config/config_entries/options/flow/o1", True),
    ("DELETE", "/api/config/config_entries/entry/g1", True),
    ("GET", "/api/diagnostics/config_entry/y1", False),
    # Not on the allowlist at all, still never through a locked house (fail closed: any method but GET).
    ("PUT", "/api/hauflo/notify", True), ("PATCH", "/api/config/core", True), ("POST", "/api/services/knx/send", True),
]


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):  # the link loop: no Hauflo server in tests
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo — Вилла Юг", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"}, options={CONF_RANGES: ["1/1"]})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


class _Local:
    def __init__(self):
        self.sent: list[str] = []

    async def send_str(self, data: str) -> None:
        self.sent.append(data)


def _msg(t: str, i: int) -> dict:
    return {"id": i, "type": t}


def test_the_allowlist_is_pinned_and_every_type_classified() -> None:
    assert agent_mod._WS_EXACT == ALLOWLIST
    assert agent_mod._SERVICES == set(SERVICES)
    assert agent_mod._WS_READS <= agent_mod._WS_EXACT
    assert agent_mod._WS_EXACT - agent_mod._WS_READS == WRITES
    for t in ALLOWLIST:
        assert agent_mod._ws_locked({"type": t}) is (t in WRITES), t
    for (domain, service), write in SERVICES.items():
        assert agent_mod._ws_locked({"type": "call_service", "domain": domain, "service": service}) is write
    # Fail closed: what no list knows counts as a write; signing in is not one.
    assert agent_mod._ws_locked({"type": "backup/generate"}) is True
    assert agent_mod._ws_locked({"type": "call_service", "domain": "knx", "service": "send"}) is True
    assert agent_mod._ws_locked({}) is True
    assert agent_mod._ws_locked({"type": "auth"}) is False


async def _ws_round(hass: HomeAssistant, entry, locked: bool) -> dict[str, str | None]:
    """Each allowlisted type (and service) through _channel_send with the other checks out of the way: what reached
    HA, and the refusal code of what didn't."""
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ALLOW_CHANGES: not locked})
    agent = entry.runtime_data
    local, out = _Local(), []

    async def send(frame):
        out.append(frame)

    agent.channels[1] = local
    msgs = [_msg(t, i) for i, t in enumerate(sorted(ALLOWLIST))]
    msgs += [{"id": 900 + i, "type": "call_service", "domain": d, "service": s} for i, (d, s) in enumerate(SERVICES)]
    result: dict[str, str | None] = {}
    with patch.object(agent, "send", send), patch.object(agent_mod, "_ws_allowed", return_value=True), patch.object(agent_mod.backup, "fresh", return_value=True), patch.object(agent, "_outside_ranges", return_value=set()):
        for m in msgs:
            before_local, before_out = len(local.sent), len(out)
            await agent._channel_send(1, json.dumps(m))
            key = m["type"] if m["type"] != "call_service" else f"{m['domain']}.{m['service']}"
            if len(local.sent) > before_local:
                result[key] = None
            else:
                assert len(out) == before_out + 1, key
                result[key] = json.loads(out[-1]["data"])["error"]["code"]
    return result


async def test_locked_refuses_every_ws_write_and_passes_every_read(hass: HomeAssistant, entry) -> None:
    result = await _ws_round(hass, entry, locked=True)
    for t in ALLOWLIST:
        assert result[t] == (LOCKED_CODE if t in WRITES else None), t
    assert result["frontend.set_theme"] == LOCKED_CODE
    assert result["homeassistant.update_entity"] is None and result["scene.reload"] is None


async def test_unlocked_lets_the_writes_through(hass: HomeAssistant, entry) -> None:
    result = await _ws_round(hass, entry, locked=False)
    assert all(code is None for code in result.values()), result


async def test_locked_refuses_before_the_allowlist_and_the_address_check(hass: HomeAssistant, entry) -> None:
    """The real checks, not patched: a KNX write outside the allowed addresses asks for nothing while locked."""
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ALLOW_CHANGES: False})
    agent = entry.runtime_data
    local, out = _Local(), []

    async def send(frame):
        out.append(frame)

    agent.channels[1] = local
    with patch.object(agent, "send", send):
        await agent._channel_send(1, json.dumps({"id": 1, "type": "knx/create_entity", "platform": "switch", "data": {"knx": {"ga_switch": {"write": "7/7/7"}}}}))
        await agent._channel_send(1, json.dumps({"id": 2, "type": "backup/generate"}))
        await agent._channel_send(1, json.dumps({"id": 3, "type": "auth"}))
        await agent._channel_send(1, json.dumps({"id": 4, "type": "get_states"}))
    assert [json.loads(f["data"])["error"]["code"] for f in out] == [LOCKED_CODE, LOCKED_CODE]
    assert not hass.data.get(DATA_PENDING_RANGES)
    assert [json.loads(s)["type"] for s in local.sent] == ["auth", "get_states"]


class _Res:
    status = 200

    async def text(self):
        return "{}"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Session:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def request(self, method, url, **kw):
        self.calls.append((method, url))
        return _Res()


@pytest.mark.parametrize("locked", [True, False])
async def test_http_writes_are_refused_while_locked(hass: HomeAssistant, entry, locked: bool) -> None:
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ALLOW_CHANGES: not locked})
    agent = entry.runtime_data
    out: list[dict] = []

    async def send(frame):
        out.append(frame)

    session = _Session()
    with patch.object(agent, "send", send), patch.object(agent, "local_session", session), patch.object(agent_mod, "_http_allowed", return_value=True), patch.object(agent_mod.backup, "fresh", return_value=True):
        for i, (method, path, write) in enumerate(HTTP):
            await agent._http({"id": i, "method": method, "path": path, "body": "{}"})
    by_id = {f["id"]: f for f in out}
    for i, (method, path, write) in enumerate(HTTP):
        if locked and write:
            assert by_id[i]["status"] == 423 and json.loads(by_id[i]["body"]) == {"code": LOCKED_CODE}, (method, path)
        else:
            assert by_id[i]["status"] == 200, (method, path)
    assert len(session.calls) == sum(1 for _, _, w in HTTP if not (locked and w))


async def test_the_lock_is_live_and_hauflo_hears_it(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    assert agent.locked is False and agent.status()["locked"] is False  # on by default
    with patch.object(agent, "status_now") as now:
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        flow = await hass.config_entries.options.async_configure(flow["flow_id"], {CONF_ALLOW_CHANGES: False, CONF_RANGES: "1/1"})
        await hass.async_block_till_done()
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    assert entry.runtime_data is agent  # no reload: the same agent, the link kept
    assert agent.locked is True and agent.status()["locked"] is True
    assert now.called  # status to Hauflo at once (the update listener)
    # A value that isn't a plain «on» locks (fail closed).
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ALLOW_CHANGES: "yes"})
    assert agent.locked is True


async def test_the_lock_survives_a_restart(hass: HomeAssistant, entry) -> None:
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ALLOW_CHANGES: False})

    async def idle(self):
        return None

    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.runtime_data.locked is True


async def test_the_dialog(hass: HomeAssistant, entry) -> None:
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.FORM and flow["step_id"] == "init"  # nothing pending: no «—»
    assert [str(k) for k in flow["data_schema"].schema] == [CONF_ALLOW_CHANGES, CONF_RANGES]
    defaults = {str(k): k.default() for k in flow["data_schema"].schema}
    assert defaults == {CONF_ALLOW_CHANGES: True, CONF_RANGES: "1/1"}
    assert flow["description_placeholders"] == {"pending": "", "project": "Вилла Юг", "server": "app.hauflo.ru", "url": "https://app.hauflo.ru"}
    # Commas and new lines both separate the addresses.
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {CONF_ALLOW_CHANGES: True, CONF_RANGES: "1/1\n2/2/5-9, 3/3/1\n\nx"})
    assert entry.options == {CONF_RANGES: ["1/1", "2/2/5-9", "3/3/1"], CONF_ALLOW_CHANGES: True}

    hass.data[DATA_PENDING_RANGES] = {"7/1/1"}
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["step_id"] == "init_pending"
    assert flow["description_placeholders"]["pending"] == "7/1/1"
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {CONF_ALLOW_CHANGES: True, CONF_RANGES: "1/1, 7/1/1"})
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_RANGES] == ["1/1", "7/1/1"]


def test_both_languages_say_the_same_steps() -> None:
    import pathlib

    base = pathlib.Path(agent_mod.__file__).parent
    texts = {n: json.loads((base / n).read_text()) for n in ("strings.json", "translations/en.json", "translations/ru.json")}
    for name, j in texts.items():
        steps = j["options"]["step"]
        assert set(steps) == {"init", "init_pending"}, name
        for step in steps.values():
            assert set(step["data"]) == {CONF_ALLOW_CHANGES, CONF_RANGES}
            assert "{project}" in step["description"] and "{server}" in step["description"] and "{url}" in step["description"]
        assert "{pending}" in steps["init_pending"]["description"] and "{pending}" not in steps["init"]["description"]
        assert "—" not in steps["init"]["description"].split("\n\n", 1)[1]
