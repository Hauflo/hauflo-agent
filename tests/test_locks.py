"""Р56: a smart lock of any integration (Z2M / ZHA / Matter / a cloud one) — recognised by HA's metadata (the lock domain,
the device's other entities), never by a brand. L2: the signal canon — jammed is the house's alarm, the lock's battery is
judged as a lock's (onDevice), a lost lock is a lost link."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.components.lock import LockEntityFeature
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.hauflo import locks as locks_mod, signals
from custom_components.hauflo.const import CONF_RANGES, CONF_SECRET, CONF_SERVER, DOMAIN

LOCK = "lock.front_door"
BAT = "sensor.front_door_battery"
BULB_BAT = "sensor.remote_battery"


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"}, options={CONF_RANGES: ["1/1"]})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    yield entry


def _device(hass: HomeAssistant, name: str, *entities: tuple[str, str | None]) -> str:
    """A device of a foreign integration (mqtt: a Z2M-like one) with its entities; → the device id."""
    owner = MockConfigEntry(domain="mqtt")
    owner.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=owner.entry_id, identifiers={("mqtt", name)}, name=name)
    reg = er.async_get(hass)
    for eid, dc in entities:
        reg.async_get_or_create(eid.split(".")[0], "mqtt", eid, suggested_object_id=eid.split(".")[1], device_id=dev.id, original_device_class=dc)
    return dev.id


async def test_lock_signals_by_canon(hass: HomeAssistant, entry) -> None:
    dev = _device(hass, "door", (LOCK, None), (BAT, "battery"))
    _device(hass, "remote", (BULB_BAT, "battery"))
    below = signals.CANON["onDevice"]["lock"]["sensor.battery"]["alarm"]["below"]
    assert signals.CANON["classes"]["sensor.battery"]["house"] == "none", "a remote's battery never moves the house"
    assert signals.device_kinds(hass) == {dev: "lock"} and signals.device_kind_of(hass, BAT) == "lock" and signals.device_kind_of(hass, BULB_BAT) is None
    hass.states.async_set(LOCK, "jammed", {})
    hass.states.async_set(BAT, str(below - 1), {"device_class": "battery"})
    hass.states.async_set(BULB_BAT, str(below - 1), {"device_class": "battery"})
    mon = {m["entityId"]: m for m in entry.runtime_data.status()["monitored"]}
    assert mon[LOCK]["problem"] is True and mon[LOCK]["house"] == "alarm"
    assert mon[BAT]["problem"] is True and mon[BAT]["deviceKind"] == "lock" and mon[BAT]["house"] == "fault"
    assert mon[BULB_BAT]["house"] == "none" and "deviceKind" not in mon[BULB_BAT], "a remote's battery: its own class"
    house = entry.runtime_data.house()
    assert house["_house"] == "alarm" and {f["entity"] for f in house["_faults"]} == {LOCK, BAT}
    assert house["_signals"][BAT]["alarm"]["below"] == below
    # Locked, charged: quiet. Lost: a lost link («замок без связи»), never «авария».
    hass.states.async_set(LOCK, "locked", {})
    hass.states.async_set(BAT, str(below + 1), {"device_class": "battery"})
    assert entry.runtime_data.house()["_house"] == "ok"
    hass.states.async_set(LOCK, "unavailable", {})
    h = entry.runtime_data.house()
    assert h["_house"] == "offline" and h["_faults"][0]["offline"] is True


# --- L3: the lock's events, pushes, the panel's service ------------------------------------------------------------

ACTION = "sensor.front_door_action"
ACTION_USER = "sensor.front_door_action_user"
ACTION_SOURCE = "sensor.front_door_action_source_name"
TAMPER = "binary_sensor.front_door_tamper"
EVENT = "event.front_door_lock"


@pytest.fixture
async def z2m(hass: HomeAssistant, entry):
    """A Z2M-like lock (mqtt): the lock, its `action` / `action_user` / `action_source_name` text sensors, a tamper
    sensor, an event entity, a battery; phones with «пуш о звонке» on, Russian."""
    _device(hass, "door", (LOCK, None), (ACTION, None), (ACTION_USER, None), (ACTION_SOURCE, "enum"), (TAMPER, "tamper"), (EVENT, None), (BAT, "battery"))
    for eid, state in ((LOCK, "locked"), (ACTION, ""), (ACTION_USER, ""), (ACTION_SOURCE, ""), (TAMPER, "off"), (EVENT, "unknown"), (BAT, "80")):
        hass.states.async_set(eid, state, {"friendly_name": "Входная дверь"} if eid == LOCK else {})
    eng = entry.runtime_data.engine
    eng.phones = lambda: [{"service": "mobile_app_p", "os": "android", "person": "x"}]
    eng.scope = lambda phone, config=None: "all"
    eng.config = {**eng.config, "lang": "ru"}
    calls = async_mock_service(hass, "notify", "mobile_app_p")
    locks = entry.runtime_data.locks
    await locks.apply()
    yield locks, calls


def _later(hass: HomeAssistant, s: float = locks_mod.MERGE_S + 1) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=s))


def _hm() -> str:
    return dt_util.as_local(dt_util.utcnow()).strftime("%H:%M")


async def _settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done()
    _later(hass)
    await hass.async_block_till_done()
    await hass.async_block_till_done()


async def test_z2m_lock_is_found_by_its_device(hass: HomeAssistant, z2m) -> None:
    locks, _ = z2m
    d = locks.devices[LOCK]
    assert d["sources"] == sorted([ACTION, EVENT]) or set(d["sources"]) == {ACTION, EVENT}
    assert d["users"] == [ACTION_USER] and d["methods"] == [ACTION_SOURCE] and d["tamper"] == TAMPER and d["battery"] == BAT
    assert locks.owns_event(EVENT) and not locks.owns_event("event.someone_else")
    assert locks.cloud[LOCK] is False, "mqtt is local_push"


async def test_unlock_is_remembered_not_pushed_by_default(hass: HomeAssistant, hass_client, z2m) -> None:
    locks, calls = z2m
    # Z2M: the action, its source and user, the lock's state — four updates, one event.
    hass.states.async_set(ACTION_SOURCE, "fingerprint")
    hass.states.async_set(ACTION_USER, "3")
    hass.states.async_set(ACTION, "fingerprint_unlock")
    hass.states.async_set(LOCK, "unlocked", {"friendly_name": "Входная дверь"})
    await _settle(hass)
    assert calls == [], "each unlock is off by default"
    last = locks.last[LOCK]
    assert (last["op"], last["method"], last["user"]) == ("unlock", "fingerprint", "#3")
    # The integrator turns «пуш о каждом открытии» on and names user 3.
    client = await hass_client()
    res = await client.post("/api/hauflo/foreign", json={"locks": {LOCK: {"push": True, "names": {"3": "Мария"}}}})
    assert res.status == 200, await res.text()
    hass.states.async_set(LOCK, "locked", {"friendly_name": "Входная дверь"})
    await _settle(hass)
    assert calls == [], "a lock's locking never pushes"
    # The same person, the same finger again: the sensors only re-stamped (Z2M republishes the same values).
    hass.states.async_set(ACTION_USER, "3")
    hass.states.async_set(ACTION_SOURCE, "fingerprint")
    hass.states.async_set(LOCK, "unlocked", {"friendly_name": "Входная дверь"})
    await _settle(hass)
    assert len(calls) == 1, calls
    assert calls[0].data["title"] == "🔓 Входная дверь"
    assert calls[0].data["message"] == f"открыта отпечатком · Мария · {_hm()}"


async def test_double_unlock_within_merge_is_one_push(hass: HomeAssistant, z2m) -> None:
    locks, calls = z2m
    from custom_components.hauflo import foreign

    foreign.flags(hass).locks = {LOCK: {"push": True}}
    hass.states.async_set(ACTION, "keypad_unlock")
    hass.states.async_set(ACTION, "unlock")
    hass.states.async_set(ACTION, "keypad_unlock")
    hass.states.async_set(LOCK, "unlocked", {"friendly_name": "Входная дверь"})
    await _settle(hass)
    assert len(calls) == 1 and calls[0].data["message"].startswith("открыта кодом ·")


async def test_alarms_push_by_default(hass: HomeAssistant, z2m) -> None:
    locks, calls = z2m
    # Jammed: «⚠️ Входная дверь · замок заклинило».
    hass.states.async_set(LOCK, "jammed", {"friendly_name": "Входная дверь"})
    await _settle(hass)
    assert len(calls) == 1 and calls[0].data["title"] == "⚠️ Входная дверь" and calls[0].data["message"].startswith("замок заклинило")
    # Wrong code: only the Nth within the window pushes.
    for i in range(locks_mod.WRONG_COUNT):
        hass.states.async_set(ACTION, f"lock_failure_invalid_pin_or_id" if i % 2 == 0 else "unlock_failure_invalid_pin_or_id")
        await _settle(hass)
        assert len(calls) == (1 if i < locks_mod.WRONG_COUNT - 1 else 2), i
    assert calls[1].data["title"] == "🚫 Входная дверь" and f"подряд: {locks_mod.WRONG_COUNT}" in calls[1].data["message"]
    # Tamper: critical.
    hass.states.async_set(TAMPER, "on")
    await _settle(hass)
    assert len(calls) == 3 and calls[2].data["title"] == "🚨 Входная дверь"
    assert calls[2].data["data"].get("priority") == "high" or calls[2].data["data"].get("ttl") == 0 or "critical" in str(calls[2].data["data"]).lower() or calls[2].data["data"].get("channel") == "alarm_stream"
    # The integrator turned the alarms off: silent.
    from custom_components.hauflo import foreign

    foreign.flags(hass).locks = {LOCK: {"alarms": False}}
    hass.states.async_set(TAMPER, "off")
    hass.states.async_set(LOCK, "locked", {"friendly_name": "Входная дверь"})
    await _settle(hass)
    hass.states.async_set(LOCK, "jammed", {"friendly_name": "Входная дверь"})
    await _settle(hass)
    assert len(calls) == 3
    assert locks.last[LOCK]["op"] == "jammed", "still remembered for the tile"


async def test_restart_and_noise_are_not_events(hass: HomeAssistant, z2m) -> None:
    locks, calls = z2m
    from custom_components.hauflo import foreign

    foreign.flags(hass).locks = {LOCK: {"push": True}}
    # unavailable → unlocked (a restart / reconnect): not an unlock.
    hass.states.async_set(LOCK, "unavailable")
    hass.states.async_set(LOCK, "unlocked", {"friendly_name": "Входная дверь"})
    # Noise words the canon ignores (a factory reset, a battery report), and an old restored event.
    hass.states.async_set(ACTION, "lock_factory_reset")
    hass.states.async_set(EVENT, (dt_util.utcnow() - timedelta(hours=1)).isoformat(), {"event_type": "unlock"})
    # Only a user / a method, no operation: nothing.
    hass.states.async_set(ACTION_USER, "7")
    await _settle(hass)
    assert calls == [] and LOCK not in locks.last


async def test_matter_like_event_entity(hass: HomeAssistant, z2m) -> None:
    """A Matter-like event: the operation in its attributes, the source as «Keypad», the user an index; «changed_by:
    Keypad» on the lock is how, never who."""
    locks, calls = z2m
    from custom_components.hauflo import foreign

    foreign.flags(hass).locks = {LOCK: {"push": True}}
    hass.states.async_set(EVENT, dt_util.utcnow().isoformat(), {"event_type": "lock_operation", "operation_type": "Unlock", "operation_source": "Keypad", "user_index": 2})
    await _settle(hass)
    assert len(calls) == 1 and calls[0].data["message"] == f"открыта кодом · пользователь №2 · {_hm()}"
    hass.states.async_set(LOCK, "locked", {"friendly_name": "Входная дверь"})
    await _settle(hass)
    hass.states.async_set(LOCK, "unlocked", {"friendly_name": "Входная дверь", "changed_by": "Keypad"})
    await _settle(hass)
    assert locks.last[LOCK]["method"] == "code" and locks.last[LOCK]["user"] == ""
    # The lock's event is the lock's: never a «🔔 Кнопка» ring of the doorbell engine, even watched.
    foreign.flags(hass).notify[EVENT] = True
    assert all(not c.data["title"].startswith("🔔") for c in calls)


async def test_english_push(hass: HomeAssistant, entry, z2m) -> None:
    locks, calls = z2m
    from custom_components.hauflo import foreign

    entry.runtime_data.engine.config["lang"] = "en"
    foreign.flags(hass).locks = {LOCK: {"push": True}}
    hass.states.async_set(ACTION_SOURCE, "rfid")
    hass.states.async_set(LOCK, "unlocked", {"friendly_name": "Front door"})
    await _settle(hass)
    assert calls[0].data["title"] == "🔓 Front door" and calls[0].data["message"] == f"unlocked by card · {_hm()}"


async def test_quiet_hours_without_sound(hass: HomeAssistant, entry, z2m) -> None:
    locks, calls = z2m
    from custom_components.hauflo import foreign

    foreign.flags(hass).locks = {LOCK: {"push": True}}
    entry.runtime_data.engine.quiet_now = lambda: True
    sent: list[dict] = []
    real = entry.runtime_data.engine._send

    async def spy(rule, title, message, **kw):
        sent.append({"title": title, **kw})
        return await real(rule, title, message, **kw)

    entry.runtime_data.engine._send = spy
    hass.states.async_set(LOCK, "unlocked", {"friendly_name": "Входная дверь"})
    await _settle(hass)
    hass.states.async_set(TAMPER, "on")
    await _settle(hass)
    assert [(s["silent"], s["critical"]) for s in sent] == [(True, False), (False, True)]


# --- the panel's service: rights, OPEN, a double tap ---------------------------------------------------------------


async def test_service_rights_open_and_double_tap(hass: HomeAssistant, z2m) -> None:
    locks, _ = z2m
    from custom_components.hauflo import foreign

    maria = await hass.auth.async_create_user("Мария")
    guest = await hass.auth.async_create_user("Сосед")
    foreign.flags(hass).locks = {LOCK: {"users": [maria.id]}}
    gate = asyncio.Event()
    got: list[ServiceCall] = []

    async def slow(call: ServiceCall) -> None:
        got.append(call)
        await gate.wait()

    for svc in ("lock", "unlock", "open"):
        hass.services.async_register("lock", svc, slow)
    # A foreign user: «Нет прав», the lock never asked.
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "lock", {"entity_id": LOCK, "action": "unlock"}, blocking=True, context=Context(user_id=guest.id))
    assert err.value.translation_key == "lock_no_rights" and got == []
    # Locking: anyone.
    gate.set()
    await hass.services.async_call(DOMAIN, "lock", {"entity_id": LOCK, "action": "lock"}, blocking=True, context=Context(user_id=guest.id))
    assert [c.service for c in got] == ["lock"]
    # «Открыть дверь» on a lock without OPEN: refused.
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "lock", {"entity_id": LOCK, "action": "open"}, blocking=True, context=Context(user_id=maria.id))
    assert err.value.translation_key == "lock_no_open"
    # A double tap: the second while the first is under way — one command to the lock.
    gate.clear()
    first = hass.async_create_task(hass.services.async_call(DOMAIN, "lock", {"entity_id": LOCK, "action": "unlock"}, blocking=True, context=Context(user_id=maria.id)))
    for _ in range(5):
        await asyncio.sleep(0)
    await hass.services.async_call(DOMAIN, "lock", {"entity_id": LOCK, "action": "unlock"}, blocking=True, context=Context(user_id=maria.id))
    gate.set()
    await first
    assert [c.service for c in got] == ["lock", "unlock"] and got[1].context.user_id == maria.id
    # OPEN supported: opens. A system call (no user) is allowed.
    hass.states.async_set(LOCK, "locked", {"friendly_name": "Входная дверь", "supported_features": int(LockEntityFeature.OPEN)})
    await hass.services.async_call(DOMAIN, "lock", {"entity_id": LOCK, "action": "open"}, blocking=True)
    assert got[-1].service == "open"
    # Not a lock / unknown.
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "lock", {"entity_id": "lock.nowhere", "action": "lock"}, blocking=True)
    # The lock failing: the client's words.
    async def broken(call: ServiceCall) -> None:
        raise HomeAssistantError("timeout")

    hass.services.async_register("lock", "lock", broken)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, "lock", {"entity_id": LOCK, "action": "lock"}, blocking=True)
    assert err.value.translation_key == "lock_failed"
    assert locks._busy == set()


async def test_house_view_says_the_tile(hass: HomeAssistant, hass_client, hass_admin_user, z2m) -> None:
    locks, _ = z2m
    client = await hass_client()
    other = await hass.auth.async_create_user("Сосед")
    assert (await client.post("/api/hauflo/foreign", json={"locks": {LOCK: {"users": [other.id], "door": "front", "poll": 10}}})).status == 200
    got = (await (await client.get("/api/hauflo/house")).json())["locks"]
    assert got[LOCK] == {"last": None, "battery": BAT, "door": "front", "cloud": False, "poll": 0, "can_unlock": False, "open": False}, "the admin not listed: no unlock; not a cloud lock: no reads"
    assert locks.view(other.id)[LOCK]["can_unlock"] is True and locks.view(None)[LOCK]["can_unlock"] is True


async def test_settings_are_checked_and_kept(hass: HomeAssistant, hass_client, entry, z2m) -> None:
    from custom_components.hauflo import foreign

    client = await hass_client()
    for bad in (
        {"switch.x": {}},
        {LOCK: {"push": "yes"}},
        {LOCK: {"users": ["not-an-id"]}},
        {LOCK: {"names": {"1": ""}}},
        {LOCK: {"names": {"1": "a‮b"}}},
        {LOCK: {"names": {"1": "x" * 41}}},
        {LOCK: {"door": "Front Door"}},
        {LOCK: {"poll": -1}},
        {LOCK: {"poll": True}},
        {LOCK: {"poll": 100000}},
        {LOCK: {"color": "red"}},
        {LOCK: []},
        [],
    ):
        res = await client.post("/api/hauflo/foreign", json={"locks": bad})
        assert res.status == 400, bad
    assert foreign.flags(hass).locks == {}
    good = {LOCK: {"push": True, "alarms": False, "users": ["a" * 32, "a" * 32], "names": {"1": " Мария "}, "door": "front_2", "poll": 5}}
    assert (await client.post("/api/hauflo/foreign", json={"locks": good})).status == 200
    want = {LOCK: {"push": True, "alarms": False, "users": ["a" * 32], "names": {"1": "Мария"}, "door": "front_2", "poll": 5}}
    assert foreign.flags(hass).locks == want
    got = await (await client.get("/api/hauflo/foreign")).json()
    assert got["locks"] == want and got["lock_info"]["seen"] == {LOCK: {"cloud": False, "open": False}}
    # No lock in the house: no «Замок» facts (the wizard draws no section).
    entry.runtime_data.locks.devices = {}
    assert "lock_info" not in await (await client.get("/api/hauflo/foreign")).json()
    await hass.async_block_till_done()
    # Kept over a restart.
    f = foreign.Flags(hass)
    await foreign.flags(hass)._store.async_save(foreign.flags(hass)._data())
    await f.async_load()
    assert f.locks == want
    # Not an admin: refused.
    assert foreign.locks_of(hass) == want and foreign.locks_of(None) == {}


async def test_cloud_lock_is_read_every_n_minutes(hass: HomeAssistant, entry) -> None:
    owner = MockConfigEntry(domain="verisure")
    owner.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=owner.entry_id, identifiers={("verisure", "door")}, name="door")
    er.async_get(hass).async_get_or_create("lock", "verisure", "cloud_door", suggested_object_id="cloud_door", device_id=dev.id)
    hass.states.async_set("lock.cloud_door", "locked")
    from custom_components.hauflo import foreign

    reads = async_mock_service(hass, "homeassistant", "update_entity")
    foreign.flags(hass).set_locks({"lock.cloud_door": {"poll": 5}})
    await hass.async_block_till_done()
    locks = entry.runtime_data.locks
    assert locks.cloud["lock.cloud_door"] is True
    assert locks.view(None)["lock.cloud_door"]["poll"] == 5 and locks.view(None)["lock.cloud_door"]["cloud"] is True
    info = await foreign.flags(hass).lock_info()
    assert info["seen"] == {"lock.cloud_door": {"cloud": True, "open": False}} and info["doors"] == [], "no intercom: no doors to link"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5, seconds=1))
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in reads] == ["lock.cloud_door"]
    # Poll off: no more reads.
    foreign.flags(hass).set_locks({})
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=11))
    await hass.async_block_till_done()
    assert len(reads) == 1


async def test_last_is_kept_over_a_restart(hass: HomeAssistant, entry, z2m) -> None:
    locks, _ = z2m
    hass.states.async_set(LOCK, "unlocked", {"friendly_name": "Входная дверь"})
    await _settle(hass)
    _later(hass, 10)
    await hass.async_block_till_done()
    fresh = locks_mod.Locks(hass, lambda: None)
    await fresh.async_start()
    assert fresh.last[LOCK]["op"] == "unlock"
    fresh.stop()
    # No engine (the link not up yet): remembered, no crash.
    fresh._pending.clear()
    await fresh._push(LOCK, "jammed", None, "", dt_util.utcnow(), 1)


async def test_old_user_is_not_glued_to_a_new_unlock(hass: HomeAssistant, z2m, freezer) -> None:
    """Мария opened an hour ago; now the door is opened by a key: never «· Мария»."""
    locks, _ = z2m
    hass.states.async_set(ACTION_USER, "3")
    hass.states.async_set(ACTION_SOURCE, "fingerprint")
    hass.states.async_set(LOCK, "unlocked", {"friendly_name": "Входная дверь"})
    await _settle(hass)
    assert locks.last[LOCK]["user"] == "#3"
    hass.states.async_set(LOCK, "locked", {"friendly_name": "Входная дверь"})
    await _settle(hass)
    freezer.tick(timedelta(hours=1))
    hass.states.async_set(LOCK, "unlocked", {"friendly_name": "Входная дверь"})
    await _settle(hass)
    assert locks.last[LOCK]["user"] == "" and locks.last[LOCK]["method"] is None


async def test_lock_event_is_not_a_ring_but_its_doorbell_is(hass: HomeAssistant, z2m) -> None:
    """A lock with a built-in bell (Aqara / Philips-like): its unlock event is the lock's, never «🔔 Кнопка»; the bell
    on the same device still rings."""
    locks, calls = z2m
    from custom_components.hauflo import foreign

    bell = "event.front_door_bell"
    dev = er.async_get(hass).async_get(LOCK).device_id
    er.async_get(hass).async_get_or_create("event", "mqtt", bell, suggested_object_id="front_door_bell", device_id=dev, original_device_class="doorbell")
    await locks.apply()
    assert locks.owns_event(bell)
    foreign.flags(hass).notify[EVENT] = True
    foreign.flags(hass).notify[bell] = True
    hass.states.async_set(EVENT, dt_util.utcnow().isoformat(), {"event_type": "unlock"})
    await _settle(hass)
    assert calls == [], "the lock's own event: no ring, unlocks not pushed by default"
    hass.states.async_set(bell, dt_util.utcnow().isoformat(), {"event_type": "ring", "device_class": "doorbell", "friendly_name": "Звонок"})
    await _settle(hass)
    assert len(calls) == 1 and calls[0].data["title"].startswith("🔔") and LOCK in locks.last and locks.last[LOCK]["op"] == "unlock"


async def test_who_from_the_app_and_a_name_the_lock_says(hass: HomeAssistant, z2m) -> None:
    locks, calls = z2m
    from custom_components.hauflo import foreign

    foreign.flags(hass).locks = {LOCK: {"push": True}}
    maria = await hass.auth.async_create_user("Мария")
    # HA's own «Отпереть» (the panel, the app): the state's context says who.
    hass.states.async_set(LOCK, "unlocked", {"friendly_name": "Входная дверь"}, context=Context(user_id=maria.id))
    await _settle(hass)
    assert calls[-1].data["message"] == f"открыта из приложения · Мария · {_hm()}"
    # A lock that says the name itself (a cloud one): as is, cut to the canon's length.
    hass.states.async_set(LOCK, "locked", {"friendly_name": "Входная дверь"})
    await _settle(hass)
    long = "Пётр Иванович " * 5
    hass.states.async_set(ACTION_USER, long)
    hass.states.async_set(LOCK, "unlocked", {"friendly_name": "Входная дверь"})
    await _settle(hass)
    assert locks.last[LOCK]["user"] == long[: locks_mod.T["name_max"]] and len(calls) == 2


async def test_stop_after_start_does_not_remove_a_fired_listener(hass: HomeAssistant, entry, caplog) -> None:
    """Reaccept 3 п.21: started while HA starts, stopped after HA was up (a restart's stop) — the «started» once-listener
    had fired and was gone; removing it again logged ERROR «Unable to remove unknown job listener». Stopped before HA is
    up, the listener is removed (nothing fires into a stopped Locks)."""
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState

    hass.set_state(CoreState.not_running)  # HA's `is_running` counts «starting» as up
    up = locks_mod.Locks(hass, lambda: None)
    await up.async_start()
    early = locks_mod.Locks(hass, lambda: None)
    await early.async_start()
    assert up._start is not None and early._start is not None
    early.stop()
    assert early._start is None
    with patch.object(up, "apply") as up_applied, patch.object(early, "apply") as early_applied:
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
    assert up_applied.call_count == 1 and early_applied.call_count == 0  # `early` had let go
    assert up._start is None
    up.stop()
    up.stop()  # twice (HA's stop, then the entry's unload): nothing left to remove
    assert "unknown job listener" not in caplog.text
