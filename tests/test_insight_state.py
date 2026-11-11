"""What the integrator sees per entity (0.32 п.3 B1): the observation gate (OBSERVE_H hours AND OBS_EPS episodes), like
gaps only, the air handler excluded everywhere (no_heat / noeffect), Detector.state() of every detector, the episode ring
(over a restart: «resumed»), «это норма» / reset / the integrator's own ×N, GET/POST /api/hauflo/insights."""

import json
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hauflo.const import CONF_SECRET, CONF_SERVER, DOMAIN
from custom_components.hauflo.insight import FACTOR_MAX, GAP_LIKE_MIN, LIST_MAX, LOCAL_MAX, MIN_H, OBS_EPS, OBSERVE_H, OFFSET_MAX, STORE_KEY, Insights
from custom_components.hauflo.noeffect import HOLD, KINDS as PAIR_KINDS, PairEffect

H = 3600
T0 = 1_800_000_000.0
FLOOR = "climate.floor"
AHU = "climate.ventmashina"
SHAPES = {
    "learning": {"have", "need", "hours", "needH"},
    "ready": {"norm", "unit", "n"},
    "deviation": {"since", "was", "now"},
    "normal": {"by", "at"},
    "excluded": {"reason"},
}
REASONS = {"air_handler", "hidden", "no_outdoor", "coarse_battery", "season", "no_norm"}


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
async def ins(hass: HomeAssistant):
    reg = er.async_get(hass)
    area = ar.async_get(hass).async_create("Гостиная")
    for domain, uid in (("climate", "floor"), ("climate", "ventmashina")):
        e = reg.async_get_or_create(domain, "test", uid, suggested_object_id=uid)
        reg.async_update_entity(e.entity_id, area_id=area.id)
    engine = Insights(hass, lambda eid: True, lambda: None)
    engine.clock = Clock()
    await engine.async_start()
    yield engine
    engine.async_stop()


def clim(ins: Insights):
    return ins.detectors[0]


async def at(hass, ins, hours, cur, eid=FLOOR, tgt=22.0, name="Тёплый пол"):
    ins.clock.t = T0 + hours * H
    hass.states.async_set(eid, "heat", {"current_temperature": cur, "temperature": tgt, "friendly_name": name})
    await hass.async_block_till_done()
    ins._tick()


async def episodes(hass, ins, n, every_h, rise_h, start=0.0, gap=2.0, **kw):
    t = start
    for _ in range(n):
        await at(hass, ins, t, 22.0 - gap, **kw)
        await at(hass, ins, t + rise_h, 22.0, **kw)
        t += every_h
    return t


def shape(state: dict) -> str:
    [(k, v)] = state.items()
    assert set(v) == SHAPES[k], state
    return k


# --- the gate ----------------------------------------------------------------------------------------------------------


async def test_twelve_episodes_in_two_hours_are_still_learning(hass, ins):
    """Owner 10.11: an air handler «learned» in under a day — 12 clean episodes within 2 h are no norm yet."""
    t = await episodes(hass, ins, 12, 1 / 6, 0.15, gap=1.2)
    st = clim(ins).state(FLOOR, ins.clock(), "no_heat")
    assert shape(st) == "learning"
    assert st["learning"]["have"] == 12 and st["learning"]["need"] == max(OBS_EPS, 5) and st["learning"]["hours"] < 3 and st["learning"]["needH"] == OBSERVE_H
    assert clim(ins).norm(FLOOR, "heat") is None
    # Flat for hours right after: nothing said — no norm to be «off».
    await at(hass, ins, t, 20.0)
    await at(hass, ins, t + MIN_H + 1, 20.0)
    assert ins.items() == []
    # The hours passed (and an episode more): ready.
    await at(hass, ins, OBSERVE_H, 22.0)
    await episodes(hass, ins, 1, 2, 1, start=OBSERVE_H + 1)
    st = clim(ins).state(FLOOR, ins.clock(), "no_heat")
    assert shape(st) == "ready" and st["ready"]["unit"] == "c_per_h" and st["ready"]["n"] >= 13


async def test_like_gap_only(hass, ins):
    """A 3° gap is crossed faster than a 1° one: the norm for a start is from the starts as far away."""
    det = clim(ins)
    det.norms[FLOOR] = {"heat": [[4.0, 3.0]] * 6 + [[1.0, 1.0]] * 6}
    ins.first[f"climate|{FLOOR}"] = T0 - OBSERVE_H * H
    assert det.norm(FLOOR, "heat", 3.0) == 4.0 and det.norm(FLOOR, "heat", 1.2) == 1.0
    assert det.norm(FLOOR, "heat") == pytest.approx(2.5)


async def test_a_band_with_too_few_like_gaps_is_still_learning(hass, ins):
    """Acceptance 10.11 L5: two episodes from 3.5° and ten from 1°: the 3.5° start has no norm (never the 1° ones' 1.0);
    episodes learned before 0.32 (no gap) count for no band; GAP_LIKE_MIN of them — that band's own median."""
    det = clim(ins)
    ins.first[f"climate|{FLOOR}"] = T0 - OBSERVE_H * H
    det.norms[FLOOR] = {"heat": [[5.0, 3.5]] * 2 + [[1.0, 1.0]] * 10}
    assert det.norm(FLOOR, "heat", 3.5) is None and det.norm(FLOOR, "heat", 1.0) == 1.0
    det.norms[FLOOR] = {"heat": [[5.0, None]] * 12}
    assert det.norm(FLOOR, "heat", 3.5) is None and det.norm(FLOOR, "heat") == 5.0
    det.norms[FLOOR] = {"heat": [[5.0, 3.5]] * GAP_LIKE_MIN + [[1.0, 1.0]] * 10}
    assert det.norm(FLOOR, "heat", 3.5) == 5.0
    # A running 3.5° episode with no norm for its band: no «no_heat», however long it runs.
    det.norms[FLOOR] = {"heat": [[5.0, 3.5]] * 2 + [[1.0, 1.0]] * 10}
    await at(hass, ins, 0, 18.5)
    await at(hass, ins, 30, 18.5)
    assert not [i for i in det.items(ins.clock()) if i["entityId"] == FLOOR]
    # The control: with its band learned, the same episode is said.
    det.norms[FLOOR] = {"heat": [[5.0, 3.5]] * GAP_LIKE_MIN + [[1.0, 1.0]] * 10}
    await at(hass, ins, 31, 18.5)
    assert [i["kind"] for i in det.items(ins.clock()) if i["entityId"] == FLOOR] == ["no_heat"]


# --- the air handler -----------------------------------------------------------------------------------------------------


async def test_air_handler_is_excluded_not_said(hass, ins):
    t = await episodes(hass, ins, OBS_EPS, 9, 1, eid=AHU, name="Вентмашина приточная")
    await at(hass, ins, t, 20.0, eid=AHU, name="Вентмашина приточная")
    await at(hass, ins, t + MIN_H + 1, 20.0, eid=AHU, name="Вентмашина приточная")
    assert ins.items() == []
    assert clim(ins).state(AHU, ins.clock(), "no_heat") == {"excluded": {"reason": "air_handler"}}
    slow = next(d for d in ins.detectors if d.name == "slowing")
    assert slow.state(AHU, ins.clock(), "heat_slowing") == {"excluded": {"reason": "air_handler"}}


@pytest.fixture
async def pairs(hass: HomeAssistant):
    area = ar.async_get(hass).async_create("Кухня")
    r = er.async_get(hass)
    for uid in ("kitchen_air", "kitchen_ventmashina"):
        e = r.async_get_or_create("climate", "test", uid, suggested_object_id=uid)
        r.async_update_entity(e.entity_id, area_id=area.id)
    engine = Insights(hass, lambda eid: True, lambda: None)
    engine.clock = Clock()
    await engine.async_start()
    yield engine
    engine.async_stop()


@pytest.mark.parametrize(("eid", "name", "said"), [("climate.kitchen_air", "Климат", True), ("climate.kitchen_ventmashina", "Вентмашина", False)])
async def test_noeffect_skips_an_air_handler(hass, pairs, eid, name, said):
    """The control (a room's thermostat, no norm yet) is said by the fixed bar; the air handler never."""
    hass.states.async_set(eid, "heat", {"friendly_name": name, "temperature": 25.0, "current_temperature": 20.0, "hvac_action": "heating"})
    win = PAIR_KINDS["air"]["window_h"]
    for i in range(1, int((HOLD + 1) * win * 4) + 1):
        pairs.clock.t = T0 + i * 0.25 * H
        await hass.async_block_till_done()
        pairs._tick()
    det = next(d for d in pairs.detectors if isinstance(d, PairEffect))
    assert ([i["entityId"] for i in pairs.items() if i["kind"] == "no_effect"] == [eid]) is said
    st = det.state(eid, pairs.clock(), "no_effect")
    assert (shape(st) == "deviation") if said else st == {"excluded": {"reason": "air_handler"}}


# --- state() of every detector ---------------------------------------------------------------------------------------------


async def test_every_detector_says_one_shape(hass, ins):
    now = ins.clock()
    kinds = 0
    for det in ins.detectors:
        assert det.KINDS, det.name
        for kind in det.KINDS:
            kinds += 1
            assert ins.owner[kind] is det
            st = det.state("sensor.nothing_known", now, kind)
            assert shape(st) in ("learning", "ready", "excluded"), (det.name, st)
            if "excluded" in st:
                assert st["excluded"]["reason"] in REASONS
    assert len(ins.detectors) >= 10 and kinds >= 11
    # A known one: listed by states() with its kind and detector.
    await episodes(hass, ins, 2, 3, 1)
    got = ins.states()
    rows = {r["key"]: (r["detector"], shape({k: r[k] for k in SHAPES if k in r}), r["name"]) for r in got["items"]}
    assert rows[f"no_heat|{FLOOR}"] == ("climate", "learning", "Тёплый пол") and rows[f"heat_slowing|{FLOOR}"][:2] == ("slowing", "learning")
    assert got["total"] == len(rows) and {k.split("|")[1] for k in rows} == {FLOOR}


async def test_deviation_normal_and_reset(hass, ins):
    t = await episodes(hass, ins, OBS_EPS, 9, 1)
    await at(hass, ins, t, 20.0)
    await at(hass, ins, t + MIN_H + 1, 20.0)
    key = f"no_heat|{FLOOR}"
    assert [i["key"] for i in ins.items()] == [key]
    st = clim(ins).state(FLOOR, ins.clock(), "no_heat")
    assert shape(st) == "deviation" and st["deviation"]["was"] == pytest.approx(1.0) and st["deviation"]["now"] == pytest.approx(MIN_H + 1)
    # «Это норма» (the site's finding): gone from the list, the detector keeps learning.
    found = {key: {"normal": True, "by": "Иван", "at": "2026-11-10T00:00:00+00:00"}}
    ins.finding_of = found.get
    ins.findings_changed()
    assert ins.items() == [] and clim(ins).state(FLOOR, ins.clock(), "no_heat") == {"normal": {"by": "Иван", "at": "2026-11-10T00:00:00+00:00"}}
    n = len(clim(ins).norms[FLOOR]["heat"])
    await at(hass, ins, t + MIN_H + 20, 22.0)
    await episodes(hass, ins, 1, 2, 1, start=t + MIN_H + 30)
    assert len(clim(ins).norms[FLOOR]["heat"]) == n + 1
    # resetAt: the norms forgotten once, observed anew (and the second time with the same resetAt — nothing).
    found[key] = {"resetAt": "2026-11-10T01:00:00+00:00", "by": "Иван", "at": "2026-11-10T01:00:00+00:00"}
    ins.findings_changed()
    assert FLOOR not in clim(ins).norms and shape(clim(ins).state(FLOOR, ins.clock(), "no_heat")) == "learning"
    assert ins.hours_seen("climate", FLOOR, ins.clock()) == 0
    await episodes(hass, ins, 1, 2, 1, start=t + MIN_H + 40)
    ins.findings_changed()
    assert len(clim(ins).norms[FLOOR]["heat"]) == 1


async def test_own_factor_from_a_finding(hass, ins):
    t = await episodes(hass, ins, OBS_EPS, 9, 1)
    ins.finding_of = {f"no_heat|{FLOOR}": {"factor": FACTOR_MAX, "by": "t", "at": "2026-11-10T00:00:00+00:00"}}.get
    await at(hass, ins, t, 20.0)
    await at(hass, ins, t + MIN_H + 1, 20.0)
    assert ins.items() == []  # 3 h flat: within ×FACTOR_MAX of its 1 h


async def test_choices_made_in_ha_go_to_the_site_until_it_holds_them(hass, ins):
    """п.9: «Это норма» / «Снова следить» on the card reach Hauflo by the status; once the site's finding carries the same
    `at` (the cloud folds them in) they stop going — no loop; a newer site choice wins over an older HA one."""
    key = f"no_heat|{FLOOR}"
    found: dict = {}
    ins.finding_of = found.get
    assert ins.choices() == {}
    ins.choose("no_heat", FLOOR, "normal", "Пётр")
    got = ins.choices()
    assert list(got) == [key] and got[key]["normal"] is True and got[key]["by"] == "Пётр" and got[key]["at"]
    found[key] = dict(got[key])  # the cloud folded it in, the same `at`
    assert ins.choices() == {} and ins.is_normal(key)
    ins.clock.t += 60
    ins.choose("no_heat", FLOOR, "follow", "Пётр", 2.5)
    got = ins.choices()
    assert got[key] == {"factor": 2.5, "by": "Пётр", "at": got[key]["at"]} and not ins.is_normal(key)
    # The site said «это норма» later (Hauflo, newer `at`): its word stands, nothing goes back.
    found[key] = {"normal": True, "by": "Иван", "at": "2099-01-01T00:00:00+00:00"}
    assert ins.choices() == {} and ins.is_normal(key)
    # «Снова следить» (the canon's ×N) over a finding the site doesn't hold: its default already, nothing goes.
    ins.clock.t += 60
    ins.choose("stuck", FLOOR, "follow", "Пётр")
    assert f"stuck|{FLOOR}" not in ins.choices()
    # A reset is HA's own (the norms forgotten here), never a choice to carry.
    ins.choose("stuck", FLOOR, "reset", "Пётр")
    assert f"stuck|{FLOOR}" not in ins.choices()


# --- the ring over a restart ---------------------------------------------------------------------------------------------


async def test_ring_survives_a_restart_and_says_resumed(hass, ins, hass_storage):
    t = await episodes(hass, ins, 3, 9, 1)
    await at(hass, ins, t, 20.0)  # an episode open when HA stops
    saved = json.loads(json.dumps(ins.data()))
    assert [e["result"] for e in saved["climate"]["ring"][FLOOR]] == ["used"] * 3
    ins.async_stop()
    hass_storage[STORE_KEY] = {"version": 1, "minor_version": 1, "key": STORE_KEY, "data": saved}
    again = Insights(hass, lambda eid: True, lambda: None)
    again.clock = Clock()
    again.clock.t = T0 + (t + 0.5) * H
    await again.async_start()
    await at(hass, again, t + 1, 22.0)
    ring = again.states(FLOOR)["episodes"]
    assert [e["result"] for e in ring] == ["used"] * 3 + ["dropped:resumed"]
    assert set(ring[0]) >= {"start", "dir", "gap", "hours", "rate", "result"}
    assert again.hours_seen("climate", FLOOR, again.clock()) == pytest.approx(t + 1)  # first seen kept too
    again.async_stop()


async def test_many_entities_one_page(hass, ins):
    for k in range(300):
        clim(ins).norms[f"climate.zone_{k:03}"] = {"heat": [[2.0, 2.0]]}
    got = ins.states()
    assert got["total"] == 300 and len(got["items"]) == LIST_MAX and got["offset"] == 0
    last = ins.states(offset=300 - 5)
    assert len(last["items"]) == 5 and last["items"][-1]["entityId"] == "climate.zone_299"


# --- the view ------------------------------------------------------------------------------------------------------------


@pytest.fixture
async def entry(hass: HomeAssistant):
    async def idle(self):
        return None

    entry = MockConfigEntry(domain=DOMAIN, title="Hauflo", data={CONF_SERVER: "https://app.hauflo.ru", CONF_SECRET: "s"})
    entry.add_to_hass(hass)
    with patch("custom_components.hauflo.Agent.run", idle):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_view_rights_bodies_and_choices(hass: HomeAssistant, entry, hass_client, hass_read_only_access_token):
    engine = entry.runtime_data.insights
    assert engine is not None
    engine.detectors[0].norms[FLOOR] = {"heat": [[2.0, 2.0]]}
    client = await hass_client()
    got = await (await client.get("/api/hauflo/insights")).json()
    assert got["total"] == 1 and got["items"][0]["key"] == f"no_heat|{FLOOR}"
    one = await (await client.get(f"/api/hauflo/insights?entity={FLOOR}")).json()
    assert "episodes" in one and one["items"][0]["entityId"] == FLOOR
    assert (await client.get("/api/hauflo/insights?entity=Bad Id")).status == 400
    assert (await client.get("/api/hauflo/insights?offset=-1")).status == 400
    # Acceptance 10.11 L7: a huge / non-ASCII offset is refused, never a 500; the canon's max still answers.
    for bad in ("1" + "0" * 200, "9" * 5000, "١٢", "²", str(OFFSET_MAX + 1), "", " 1"):
        assert (await client.get("/api/hauflo/insights", params={"offset": bad})).status == 400, bad[:20]
    edge = await client.get("/api/hauflo/insights", params={"offset": str(OFFSET_MAX)})
    assert edge.status == 200 and (await edge.json())["items"] == []
    for body in (
        {"entity": FLOOR, "kind": "no_heat", "action": "follow", "factor": FACTOR_MAX + 1},
        {"entity": FLOOR, "kind": "no_heat", "action": "follow", "factor": 1},
        {"entity": FLOOR, "kind": "no_heat", "action": "follow", "factor": True},
        {"entity": FLOOR, "kind": "no_heat", "action": "follow", "factor": "3"},
        {"entity": FLOOR, "kind": "no_heat", "action": "normal", "factor": 3},
        {"entity": FLOOR, "kind": "no_such", "action": "normal"},
        {"entity": FLOOR, "kind": "no_heat", "action": "mute"},
        {"entity": "Bad", "kind": "no_heat", "action": "normal"},
        {"entity": FLOOR, "kind": "no_heat"},
        {"entity": FLOOR, "kind": "no_heat", "action": "normal", "x": 1},
    ):
        assert (await client.post("/api/hauflo/insights", json=body)).status == 400, str(body)
    res = await client.post("/api/hauflo/insights", json={"entity": FLOOR, "kind": "no_heat", "action": "follow", "factor": 2.5})
    assert res.status == 200 and engine.factor(f"no_heat|{FLOOR}", 3) == 2.5
    res = await (await client.post("/api/hauflo/insights", json={"entity": FLOOR, "kind": "no_heat", "action": "normal"})).json()
    assert "normal" in res["items"][0]
    await client.post("/api/hauflo/insights", json={"entity": FLOOR, "kind": "no_heat", "action": "reset"})
    assert FLOOR not in engine.detectors[0].norms
    user = await hass_client(hass_read_only_access_token)
    assert (await user.get("/api/hauflo/insights")).status == 403
    assert (await user.post("/api/hauflo/insights", json={"entity": FLOOR, "kind": "no_heat", "action": "normal"})).status == 403
    with patch("custom_components.hauflo.insight.DIAG_VISIBLE", "all"):
        assert (await user.get("/api/hauflo/insights")).status == 200
        assert (await user.post("/api/hauflo/insights", json={"entity": FLOOR, "kind": "no_heat", "action": "normal"})).status == 403


async def test_who_reads_follows_the_integrators_choice_pushed_with_the_signals(hass: HomeAssistant, entry, hass_client, hass_read_only_access_token):
    """п.9: «Диагностика: всем / администраторам» (cloud model.diag.visible) comes with the site's signals; GET for a
    non-admin only with «всем», POST never; the answer carries the canon's sections and whether the reader may answer."""
    entry.runtime_data.insights.detectors[0].norms[FLOOR] = {"heat": [[2.0, 2.0]]}
    admin, user = await hass_client(), await hass_client(hass_read_only_access_token)
    push = lambda diag, h: admin.post("/api/hauflo/signals", json={"signals": {"scopes": [], "entities": {}, "findings": {}, **({"diag": diag} if diag is not None else {})}, "hash": h})
    got = await (await admin.get("/api/hauflo/insights")).json()
    assert got["canEdit"] is True and got["sections"]["no_heat"] == got["sectionOrder"][0] == "climate" and got["tickS"] > 0
    assert (await admin.get("/api/hauflo/insights", params={"limit": "500"})).status == 200
    for bad in ("0", "501", "-1", "1e3", "", "٣", "9" * 50):
        assert (await admin.get("/api/hauflo/insights", params={"limit": bad})).status == 400, bad
    assert (await user.get("/api/hauflo/insights")).status == 403
    assert (await push({"visible": "all"}, "a" * 16)).status == 200
    res = await user.get("/api/hauflo/insights")
    assert res.status == 200 and (await res.json())["canEdit"] is False
    assert (await user.post("/api/hauflo/insights", json={"entity": FLOOR, "kind": "no_heat", "action": "normal"})).status == 403
    await push({"visible": "admins"}, "b" * 16)
    assert (await user.get("/api/hauflo/insights")).status == 403
    await push({"visible": "all"}, "c" * 16)
    # Acceptance 10.11 L2: a push without the choice (an older cloud) or with a bad one (dropped) keeps the last choice —
    # «всем» never goes silently; and «администраторам» never turns into «всем» by a bad value either.
    await push(None, "d" * 16)
    assert (await user.get("/api/hauflo/insights")).status == 200
    for i, bad in enumerate(({"visible": "everyone"}, {"visible": "all", "x": 1}, "all", {"visible": None}, ["all"], 5)):
        body = await (await push(bad, f"{i:x}" * 16)).json()
        assert body["dropped"] == 1 and (await user.get("/api/hauflo/insights")).status == 200, bad
    await push({"visible": "admins"}, "a1" * 8)
    for i, bad in enumerate((None, {"visible": "everyone"}, "all")):
        await push(bad, f"b{i}" * 8)
        assert (await user.get("/api/hauflo/insights")).status == 403, bad


async def test_first_push_without_the_choice_is_the_canons_default(hass: HomeAssistant, entry, hass_client, hass_read_only_access_token):
    """L2: never pushed — the canon's default (admins), whatever a push without it or with a bad one says."""
    entry.runtime_data.insights.detectors[0].norms[FLOOR] = {"heat": [[2.0, 2.0]]}
    admin, user = await hass_client(), await hass_client(hass_read_only_access_token)
    for i, diag in enumerate((None, {"visible": "everyone"})):
        await admin.post("/api/hauflo/signals", json={"signals": {"scopes": [], "entities": {}, "findings": {}, **({"diag": diag} if diag else {})}, "hash": f"{i}" * 16})
        assert (await user.get("/api/hauflo/insights")).status == 403


async def test_post_with_a_list_or_an_object_for_a_word_is_a_400(hass: HomeAssistant, entry, hass_client):
    """Acceptance 10.11 L1: `kind` / `action` a list or an object — 400, never a 500 (unhashable)."""
    client = await hass_client()
    for body in (
        {"entity": FLOOR, "kind": ["no_heat"], "action": "normal"},
        {"entity": FLOOR, "kind": {"a": 1}, "action": "normal"},
        {"entity": FLOOR, "kind": "no_heat", "action": ["normal"]},
        {"entity": FLOOR, "kind": "no_heat", "action": {"x": 1}},
        {"entity": [FLOOR], "kind": "no_heat", "action": "normal"},
        {"entity": FLOOR, "kind": None, "action": "normal"},
    ):
        assert (await client.post("/api/hauflo/insights", json=body)).status == 400, str(body)


async def test_choices_made_in_ha_are_capped_by_the_canon(hass, ins):
    """Acceptance 10.11 L3: POSTs on any number of entities keep at most LOCAL_MAX choices — the newest; a repeat of one
    key is one record; a stored list over the cap is trimmed on load."""
    for i in range(LOCAL_MAX + 30):
        ins.clock.t = T0 + i
        ins.choose("no_heat", f"climate.z{i}", "normal", "t")
    assert len(ins.local) == LOCAL_MAX
    assert f"no_heat|climate.z{LOCAL_MAX + 29}" in ins.local and "no_heat|climate.z0" not in ins.local
    ins.clock.t += 10
    ins.choose("no_heat", f"climate.z{LOCAL_MAX + 29}", "follow", "t", 2.0)
    assert len(ins.local) == LOCAL_MAX
    ins._load_meta({"local": {f"k{i}": {"at": f"2026-11-10T00:00:{i % 60:02d}+00:00"} for i in range(LOCAL_MAX + 5)}})
    assert len(ins.local) == LOCAL_MAX



# --- every token worded, RU+EN -------------------------------------------------------------------------------------------


def test_every_token_is_worded():
    import re
    from pathlib import Path

    base = Path(__file__).parent.parent / "custom_components" / "hauflo"
    src = "".join((base / f).read_text("utf8") for f in ("insight.py", "predict.py", "noeffect.py"))
    tuning = json.loads((base / "tuning.json").read_text("utf8"))
    units = set(re.findall(r'UNIT = "(\w+)"', src)) | {u for m in re.findall(r"units = (\{[^}]*\})", src) for u in json.loads(m).values()}
    units |= {v["unit"] for v in tuning.values() if isinstance(v, dict) and "unit" in v}
    want = {
        "insight_stage": set(SHAPES),
        "insight_reason": REASONS,
        "insight_unit": units,
        "insight_result": {"used", "resumed", "said", "small_gap"} | set(re.findall(r'_drop\(eid, now, "(\w+)"\)', src)),
    }
    # п.8 analytics.py: units of every series and loop (rate units «<base>_per_h»), log results, sections, tips by type
    an = tuning["analytics"]
    units |= {s["unit"] for s in an["series"]} | set(an["loop_units"].values()) | {"h", "pct", "per_day"}
    units |= {f"{u}_per_h" for t, u in an["loop_units"].items() if any("rate" in lp["metrics"] and re.search(lp["match"]["domain"], t) for lp in an["loops"])}
    units |= {f"h_per_{u}" for t, u in an["loop_units"].items() if any("warmup" in lp["metrics"] and re.search(lp["match"]["domain"], t) for lp in an["loops"])}
    asrc = (base / "analytics.py").read_text("utf8")
    want["insight_result"] |= set(re.findall(r'self\._log\(lp, ep, now, "(\w+)"', asrc))
    want["insight_section"] = set(tuning["insight"]["section_order"]) | set(tuning["insight"]["sections"].values())
    want["insight_tip"] = {f'{lp["type"]}_{m}' for lp in an["loops"] for m in lp["metrics"]}
    # п.9 the card hauflo-insights names each detector by HA's own translations (no copy in the bundle)
    want["insight_kind"] = set(tuning["insight"]["sections"])
    assert len(units) >= 10
    for name in ("strings.json", "translations/en.json", "translations/ru.json"):
        sel = json.loads((base / name).read_text("utf8"))["selector"]
        for key, tokens in want.items():
            assert tokens <= set(sel[key]["options"]), (name, key, tokens - set(sel[key]["options"]))


async def test_analysis_is_its_own_sidebar_panel_who_sees_it_follows_the_choice(hass: HomeAssistant, entry, hass_client):
    """0.33 Р26/Р8: «Анализ» — an item of HA's sidebar (a `custom` panel with the cards' element), not a dashboard tab;
    admins only by default, everyone when the integrator shows the diagnosis to all; gone with the integration."""
    from custom_components.hauflo import analysis

    panels = hass.data.get("frontend_panels") or {}
    panel = panels.get(analysis.URL_PATH)
    assert panel is not None and panel.require_admin is True and panel.component_name == "custom"
    assert panel.config["_panel_custom"]["name"] == analysis.ELEMENT and panel.config["_panel_custom"]["embed_iframe"] is False
    assert panel.sidebar_title in (analysis.TITLES["ru"], analysis.TITLES["en"]) and panel.sidebar_icon == analysis.ICON
    admin = await hass_client()
    push = lambda diag, h: admin.post("/api/hauflo/signals", json={"signals": {"scopes": [], "entities": {}, "findings": {}, "diag": diag}, "hash": h})
    await push({"visible": "all"}, "e" * 16)
    await hass.async_block_till_done()
    assert hass.data["frontend_panels"][analysis.URL_PATH].require_admin is False
    await push({"visible": "admins"}, "f" * 16)
    await hass.async_block_till_done()
    assert hass.data["frontend_panels"][analysis.URL_PATH].require_admin is True
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert analysis.URL_PATH not in (hass.data.get("frontend_panels") or {})


async def test_analysis_title_speaks_the_house_language_not_has(hass: HomeAssistant, entry):
    """fix3 п.3: the sidebar's «Анализ» in the project's language (the alarm engine's `lang`, const.site_ru), re-registered
    when it changes — «Analysis» stood beside a Russian dashboard on an English HA; HA's own language only without one."""
    from custom_components.hauflo import analysis, notify

    engine = entry.runtime_data.engine
    hass.config.language = "en"
    engine.config = notify._clean_rules({"lang": "ru", "rules": []}, hass, engine.owns)
    entry.runtime_data.status_now()
    assert hass.data["frontend_panels"][analysis.URL_PATH].sidebar_title == analysis.TITLES["ru"]
    engine.config = notify._clean_rules({"lang": "en", "rules": []}, hass, engine.owns)
    entry.runtime_data.status_now()
    assert hass.data["frontend_panels"][analysis.URL_PATH].sidebar_title == analysis.TITLES["en"]
    # The same again: not re-registered.
    assert analysis.async_register(hass, hass.data[analysis.DATA_ADMIN], config=engine.config) is False
    # No project language: HA's.
    assert analysis.title_of("ru", None) == analysis.TITLES["ru"] and analysis.title_of("en", {}) == analysis.TITLES["en"]


async def test_each_row_says_what_is_analysed_and_how(hass: HomeAssistant, entry, hass_client):
    """Р8: a row carries its inputs (role, entity, its name/room, KNX addresses when known) and the detector («how»)."""
    engine = entry.runtime_data.insights
    engine.detectors[0].norms[FLOOR] = {"heat": [[2.0, 2.0]]}
    engine.addresses = lambda: {FLOOR: ["1/2/3", "1/2/4"]}
    got = await (await (await hass_client()).get("/api/hauflo/insights")).json()
    row = next(r for r in got["items"] if r["entityId"] == FLOOR)
    assert row["how"] == engine.detectors[0].name
    me = next(u for u in row["uses"] if u.get("role") == "self")
    assert me["entityId"] == FLOOR and me["knx"] == ["1/2/3", "1/2/4"]
    assert any(u.get("role") == "season" for u in row["uses"])


async def test_analysis_title_follows_the_language_the_cloud_sends(hass: HomeAssistant, entry):
    """fix4 п.4: the cloud sends the house's language alone (no rules: its config back with `lang`, on the link and when
    the language changes in Hauflo) — the engine takes it and the sidebar's title follows at once, through the same
    path as a rules' push (async_set → on_change → status_now)."""
    from custom_components.hauflo import analysis

    engine = entry.runtime_data.engine
    hass.config.language = "en"
    await engine.async_set({"rules": [], "groups": [], "rings": {}, "lang": "ru"})
    assert hass.data["frontend_panels"][analysis.URL_PATH].sidebar_title == analysis.TITLES["ru"]
    await engine.async_set({**engine.config, "lang": "en"})
    assert hass.data["frontend_panels"][analysis.URL_PATH].sidebar_title == analysis.TITLES["en"]
    assert engine.config["rules"] == [] and engine.config["lang"] == "en"
