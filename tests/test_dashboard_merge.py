"""The integrator's own cards survive a Hauflo deploy (owner 28.10), merged by the agent (dashmerge.py) — and the cloud
can neither write nor read them: it sends only Hauflo's part (the tunnel's dashboard check), it can't name a local
card, a lock or a missing backup stops the save, the plan carries no foreign entity id."""

import copy
import glob
import json
import pathlib
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hauflo as agent_mod
from custom_components.hauflo import dashmerge
from custom_components.hauflo.const import CONF_ALLOW_CHANGES, CONF_RANGES, CONF_SECRET, CONF_SERVER, DOMAIN, LOCKED_CODE
from custom_components.hauflo.dashmerge import ID, plan_and_merge


def card(h, entity, name=None):
    return {"type": "tile", "entity": entity, ID: h, **({"name": name} if name else {})}


def section(h, *cards, heading=None):
    head = [{"type": "heading", "heading": heading, ID: f"{h}/head"}] if heading else []
    return {"type": "grid", ID: h, "cards": [*head, *cards]}


def view(h, path, title, *sections):
    return {"title": title, "path": path, "type": "sections", ID: h, "sections": list(sections)}


def hauflo(light_name="Свет"):
    """Hauflo's dashboard: «Гостиная» (lights, climate) and «Кухня»."""
    return {
        "views": [
            view("v/living", "living", "Гостиная",
                 section("v/living/s:light", card("v/living/s:light/c:l1", "light.living_main", light_name), card("v/living/s:light/c:l2", "light.living_wall"), heading="Освещение"),
                 section("v/living/s:climate", card("v/living/s:climate/c:t", "climate.living"), heading="Климат")),
            view("v/kitchen", "kitchen", "Кухня",
                 section("v/kitchen/s:light", card("v/kitchen/s:light/c:l", "light.kitchen"), heading="Освещение")),
        ]
    }


MY_CARD = {"type": "entities", "title": "Мой поливочный сценарий", "entities": ["switch.garden_valve", "sensor.soil"]}


def ids(cfg):
    out = []

    def walk(v):
        if isinstance(v, dict):
            if ID in v:
                out.append(v[ID])
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(cfg)
    return out


def cards_of(cfg, view_i, section_i):
    return cfg["views"][view_i]["sections"][section_i]["cards"]


# --- the merge itself ------------------------------------------------------------------------------------------------


def test_unchanged_dashboard_gets_hauflos_new_version():
    base = hauflo()
    new = hauflo("Люстра")
    merged, plan = plan_and_merge(new, copy.deepcopy(base), base)
    assert merged == new
    assert plan["edited"] is False and plan["local"] == [] and plan["conflicts"] == []
    assert plan["updates"] == {"added": 0, "changed": 1, "removed": 0}


def test_own_card_in_a_hauflo_section_stays_after_its_anchor():
    base = hauflo()
    cur = copy.deepcopy(base)
    cards_of(cur, 0, 0).insert(2, copy.deepcopy(MY_CARD))  # after «Свет» (l1), before l2
    merged, plan = plan_and_merge(hauflo("Люстра"), cur, base)
    got = cards_of(merged, 0, 0)
    assert [c.get(ID) for c in got] == ["v/living/s:light/head", "v/living/s:light/c:l1", None, "v/living/s:light/c:l2"]
    assert got[2] == MY_CARD
    assert got[1]["name"] == "Люстра"  # Hauflo's update applied around it
    assert plan["local"] == [{"kind": "card", "type": "entities", "title": "Мой поливочный сценарий", "where": "Гостиная"}]
    assert plan["changes"] is True


def test_own_card_first_in_a_section_stays_first():
    base = hauflo()
    cur = copy.deepcopy(base)
    cards_of(cur, 1, 0).insert(0, copy.deepcopy(MY_CARD))
    merged, _ = plan_and_merge(hauflo(), cur, base)
    assert cards_of(merged, 1, 0)[0] == MY_CARD


def test_own_view_and_own_section_stay_in_place():
    base = hauflo()
    cur = copy.deepcopy(base)
    mine = {"title": "Сад", "path": "garden", "type": "sections", "sections": [{"type": "grid", "cards": [MY_CARD]}]}
    cur["views"].insert(1, mine)
    cur["views"][0]["sections"].append({"type": "grid", "cards": [{"type": "markdown", "content": "Привет"}]})
    new = hauflo()
    new["views"].append(view("v/bath", "bath", "Ванная", section("v/bath/s:l", card("v/bath/s:l/c", "light.bath"))))
    merged, plan = plan_and_merge(new, cur, base)
    assert [v.get("path") for v in merged["views"]] == ["living", "garden", "kitchen", "bath"]
    assert merged["views"][1] == mine
    assert merged["views"][0]["sections"][-1] == {"type": "grid", "cards": [{"type": "markdown", "content": "Привет"}]}
    kinds = sorted((x["kind"], x.get("cards")) for x in plan["local"])
    assert kinds == [("section", 1), ("view", 1)]
    assert plan["updates"]["added"] == 1


def test_edited_hauflo_card_is_a_conflict_kept_by_default_or_taken():
    base = hauflo()
    cur = copy.deepcopy(base)
    cards_of(cur, 0, 1)[1]["features"] = [{"type": "climate-hvac-modes"}]
    new = hauflo()
    cards_of(new, 0, 1)[1]["name"] = "Кондиционер"
    merged, plan = plan_and_merge(new, cur, base)
    assert cards_of(merged, 0, 1)[1] == cards_of(cur, 0, 1)[1]
    assert plan["conflicts"] == [{"id": "v/living/s:climate/c:t", "kind": "card", "type": "tile", "where": "Гостиная", "kept": "yours", "updated": True}]
    merged, plan = plan_and_merge(new, cur, base, take=["v/living/s:climate/c:t"])
    assert cards_of(merged, 0, 1)[1] == cards_of(new, 0, 1)[1]
    assert plan["taken"] == 1
    merged, _ = plan_and_merge(new, cur, base, take=True)
    assert merged == new


def test_removed_hauflo_card_stays_removed_unless_restored():
    base = hauflo()
    cur = copy.deepcopy(base)
    del cards_of(cur, 0, 0)[2]  # l2
    del cur["views"][1]  # the whole kitchen
    merged, plan = plan_and_merge(hauflo(), cur, base)
    assert "v/living/s:light/c:l2" not in ids(merged) and "v/kitchen" not in ids(merged)
    assert sorted(x["id"] for x in plan["removed"]) == ["v/kitchen", "v/living/s:light/c:l2"]  # the topmost only
    merged, plan = plan_and_merge(hauflo(), cur, base, restore=["v/living/s:light/c:l2"])
    assert "v/living/s:light/c:l2" in ids(merged) and "v/kitchen" not in ids(merged)
    merged, _ = plan_and_merge(hauflo(), cur, base, restore=True)
    assert merged == hauflo()


def test_moved_hauflo_card_stays_where_the_integrator_put_it_and_is_updated():
    base = hauflo()
    cur = copy.deepcopy(base)
    moved = cards_of(cur, 0, 0).pop(1)  # l1 → the kitchen
    cards_of(cur, 1, 0).append(moved)
    merged, plan = plan_and_merge(hauflo("Люстра"), cur, base)
    assert [c.get(ID) for c in cards_of(merged, 0, 0)] == ["v/living/s:light/head", "v/living/s:light/c:l2"]
    assert cards_of(merged, 1, 0)[-1][ID] == "v/living/s:light/c:l1" and cards_of(merged, 1, 0)[-1]["name"] == "Люстра"
    assert [x["id"] for x in plan["moved"]] == ["v/living/s:light/c:l1"]
    assert ids(merged).count("v/living/s:light/c:l1") == 1


def test_anchor_gone_goes_to_the_end_of_the_same_parent():
    base = hauflo()
    cur = copy.deepcopy(base)
    cards_of(cur, 0, 0).insert(2, copy.deepcopy(MY_CARD))  # after l1
    cards_of(cur, 0, 1).append({"type": "markdown", "content": "под климатом"})
    new = hauflo()
    del cards_of(new, 0, 0)[1]  # Hauflo drops l1 (the anchor)
    new["views"][0]["sections"].pop(1)  # and the whole climate section
    merged, plan = plan_and_merge(new, cur, base)
    assert cards_of(merged, 0, 0)[-1] == MY_CARD
    # The climate section's own card: a section of its own at the end of the view.
    assert merged["views"][0]["sections"][-1] == {"type": "grid", "cards": [{"type": "markdown", "content": "под климатом"}]}
    assert len(plan["local"]) == 2


def test_hauflo_view_dropped_with_own_cards_keeps_them_in_a_view():
    base = hauflo()
    cur = copy.deepcopy(base)
    cards_of(cur, 1, 0).append(copy.deepcopy(MY_CARD))
    new = hauflo()
    new["views"].pop(1)
    merged, _ = plan_and_merge(new, cur, base)
    kept = merged["views"][-1]
    assert ID not in kept and kept["title"] == "Кухня" and kept["sections"] == [{"type": "grid", "cards": [MY_CARD]}]


def test_a_copied_hauflo_card_is_the_integrators():
    base = hauflo()
    cur = copy.deepcopy(base)
    dup = copy.deepcopy(cards_of(cur, 0, 0)[1])
    dup["name"] = "Копия"
    cards_of(cur, 1, 0).append(dup)
    merged, plan = plan_and_merge(hauflo(), cur, base)
    assert ids(merged).count("v/living/s:light/c:l1") == 1
    assert cards_of(merged, 1, 0)[-1] == {k: v for k, v in dup.items() if k != ID}
    assert plan["local"][0]["title"] == "Копия"


def test_the_cloud_cant_name_a_local_card():
    """Local cards have no id the base knows: whatever the cloud sends — ids, «take all», «restore all», an empty
    dashboard — they stay, word for word."""
    base = hauflo()
    cur = copy.deepcopy(base)
    planted = {**MY_CARD, ID: "v/living/s:light/c:evil"}  # an id the base never had: the integrator's
    cards_of(cur, 0, 0).append(copy.deepcopy(MY_CARD))
    cards_of(cur, 1, 0).append(planted)
    cur["views"].append({"title": "Сад", "path": "garden", "cards": [MY_CARD]})
    for new in ({"views": []}, {"views": [view("v/x", "x", "X", section("v/living/s:light/c:evil", card("v/living/s:light/c:evil", "light.kitchen")))]}):
        merged, _ = plan_and_merge(new, cur, base, take=True, restore=True)
        text = json.dumps(merged, ensure_ascii=False)
        assert text.count("Мой поливочный сценарий") == 3, new
        assert "garden" in text


def test_plan_carries_no_foreign_entity_id():
    base = hauflo()
    cur = copy.deepcopy(base)
    cards_of(cur, 0, 0).append(copy.deepcopy(MY_CARD))
    cards_of(cur, 0, 0).append({"type": "tile", "entity": "lock.front_door", "name": "lock.front_door"})
    cur["views"].append({"title": "switch.boiler", "path": "boiler", "cards": [{"type": "button", "entity": "switch.boiler", "tap_action": {"action": "toggle"}}]})
    cards_of(cur, 0, 1)[1]["entity"] = "climate.other_house"  # an edited Hauflo card pointing elsewhere
    _, plan = plan_and_merge(hauflo(), cur, base)
    text = json.dumps(plan, ensure_ascii=False)
    for foreign in ("switch.garden_valve", "sensor.soil", "lock.front_door", "switch.boiler", "climate.other_house"):
        assert foreign not in text, foreign
    assert len(plan["local"]) == 3 and len(plan["conflicts"]) == 1


def test_no_base_is_hauflos_config_as_is():
    merged, plan = plan_and_merge(hauflo(), {"views": [MY_CARD]}, None)
    assert merged == hauflo() and plan == {"base": False}


def test_ensure_ids_marks_whats_unmarked_uniquely():
    cfg = {"views": [{"path": "a", "sections": [{"type": "grid", "cards": [{"type": "tile", "entity": "light.a"}, {"type": "tile", ID: "x"}, {"type": "tile", ID: "x"}]}]}]}
    dashmerge.ensure_ids(cfg)
    got = ids(cfg)
    assert len(got) == len(set(got)) == 5


GENERATED = sorted(glob.glob(str(pathlib.Path(__file__).parent / "fixtures/dashboards/generated/*_0.14.8.json")))


@pytest.mark.parametrize("path", GENERATED, ids=lambda p: pathlib.Path(p).stem)
def test_real_generated_dashboards_merge(path):
    """The dashboards the backend builds for this agent (dashboardTunnel.test.ts): every view / section / card has its
    own id already (the agent adds none), a no-op merge is a no-op, and the integrator's card, section and view added
    anywhere survive a deploy of the same config."""
    cfg = json.load(open(path))
    assert dashmerge.ensure_ids(copy.deepcopy(cfg)) == cfg
    assert plan_and_merge(cfg, copy.deepcopy(cfg), cfg)[0] == cfg
    cur = copy.deepcopy(cfg)
    for v in cur["views"]:
        if isinstance(v.get("sections"), list) and v["sections"]:
            v["sections"][-1].setdefault("cards", []).append(copy.deepcopy(MY_CARD))
            v["sections"].insert(0, {"type": "grid", "cards": [{"type": "markdown", "content": "моя секция"}]})
        elif isinstance(v.get("cards"), list):
            v["cards"].insert(0, {"type": "markdown", "content": "моя карточка"})
    cur["views"].insert(1, {"title": "Сад", "path": "garden", "cards": [MY_CARD]})
    merged, plan = plan_and_merge(cfg, cur, cfg)
    assert merged == cur and plan["conflicts"] == [] and plan["removed"] == [] and plan["changes"] is False


# --- through the tunnel ----------------------------------------------------------------------------------------------


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


class _Dash:
    mode = "storage"

    def __init__(self, config):
        self.config = config
        self.saves = 0

    async def async_load(self, force):
        if self.config is None:
            from homeassistant.components.lovelace.const import ConfigNotFound

            raise ConfigNotFound
        return copy.deepcopy(self.config)

    async def async_save(self, config):
        self.config = copy.deepcopy(config)
        self.saves += 1


class _Local:
    def __init__(self):
        self.sent = []

    async def send_str(self, data):
        self.sent.append(data)


class _Tunnel:
    """The agent with HA's dashboard, HA's own websocket (copies) and the cloud's side faked."""

    def __init__(self, agent, dash):
        self.agent, self.dash, self.out, self.ws_cmds = agent, dash, [], []
        self.dashboards = [{"id": "d0", "url_path": "hauflo-home"}]
        agent.channels[1] = _Local()

    async def local_ws(self, cmds):
        self.ws_cmds.extend(c["type"] for c in cmds)
        res = []
        for c in cmds:
            if c["type"] == "lovelace/dashboards/list":
                res.append(copy.deepcopy(self.dashboards))
            elif c["type"] == "lovelace/dashboards/create":
                self.dashboards.append({"id": f"d{len(self.dashboards)}", "url_path": c["url_path"], "title": c.get("title")})
                res.append(None)
            elif c["type"] == "lovelace/dashboards/delete":
                self.dashboards = [d for d in self.dashboards if d["id"] != c["dashboard_id"]]
                res.append(None)
            else:
                res.append(None)
        return res

    async def send(self, msg, fresh=True, owned_ok=True):
        async def out(frame):
            self.out.append(frame)

        async def fake_ws(session, url, token, cmds):
            return await self.local_ws(cmds)

        with patch.object(self.agent, "send", out), patch.object(dashmerge.DashboardMerge, "_dashboard", lambda s, u: self.dash if u == "hauflo-home" else None), patch.object(dashmerge, "local_ws", fake_ws), patch.object(agent_mod.backup, "fresh", return_value=fresh), patch.object(agent_mod, "_hauflo_entity", return_value=owned_ok) as owned:
            if not owned_ok:
                owned.side_effect = lambda h, eid, o, oe: eid.startswith("light.") or eid.startswith("climate.")
            await self.agent._channel_send(1, json.dumps(msg))
        frame = json.loads(self.out[-1]["data"]) if self.out else None
        return frame


async def test_save_merges_keeps_a_copy_and_only_three(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    dash = _Dash(None)
    t = _Tunnel(agent, dash)
    r = await t.send({"id": 1, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": hauflo(), "title": "Дом"})
    assert r["success"], r
    assert dash.config == hauflo() and agent.dashboards.manages("hauflo-home")
    assert "lovelace/dashboards/create" not in t.ws_cmds  # nothing of the integrator's: no copy
    # The integrator adds his card; four deploys later his card is there and three copies are left.
    cards_of(dash.config, 0, 0).append(copy.deepcopy(MY_CARD))
    for i in range(4):
        t.dashboards.append({"id": f"old{i}", "url_path": f"hauflo-home-copy-20261001000{i}"})
    r = await t.send({"id": 2, "type": dashmerge.PLAN, "url_path": "hauflo-home", "config": hauflo("Люстра")})
    assert r["success"] and r["result"]["local"][0]["title"] == "Мой поливочный сценарий" and r["result"]["changes"] is True
    r = await t.send({"id": 3, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": hauflo("Люстра"), "title": "Дом"})
    assert r["success"] and r["result"]["copy"].startswith("hauflo-home-copy-")
    assert MY_CARD in cards_of(dash.config, 0, 0) and cards_of(dash.config, 0, 0)[1]["name"] == "Люстра"
    copies = [d["url_path"] for d in t.dashboards if "-copy-" in d["url_path"]]
    assert len(copies) == 3 and r["result"]["copy"] in copies and "hauflo-home-copy-202610010000" not in copies


async def test_first_upgrade_copy_is_a_precaution_named_in_the_dashboards_language(hass: HomeAssistant, entry) -> None:
    from datetime import datetime, timedelta, timezone  # noqa: PLC0415

    agent = entry.runtime_data
    local = datetime(2026, 10, 28, 2, 59, tzinfo=timezone(timedelta(hours=3)))  # HA's own time zone, not UTC
    for lang, title, want in (("ru", "Дом", "Дом (копия 28.10 02:59)"), ("en", "Home", "Home (copy 28 Oct 02:59)")):
        agent.dashboards.bases.clear()
        dash = _Dash(hauflo())  # on the HA already, the agent has no Hauflo version of it yet
        t = _Tunnel(agent, dash)
        with patch.object(dashmerge.dt_util, "now", return_value=local):
            r = await t.send({"id": 1, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": hauflo("Люстра"), "title": title, "lang": lang, "replace": True})
        assert r["success"], r
        assert r["result"]["copy_reason"] == "upgrade" and r["result"]["copy"] == "hauflo-home-copy-202610280259"
        assert t.dashboards[-1]["title"] == want
    # Edited in HA after Hauflo's save: the reason is that.
    cards_of(dash.config, 0, 0).append(copy.deepcopy(MY_CARD))
    r = await t.send({"id": 2, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": hauflo("Свет"), "title": "Home", "lang": "en"})
    assert r["success"] and r["result"]["copy_reason"] == "edited"


async def test_a_plain_save_of_a_managed_dashboard_is_merged_too(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    dash = _Dash(None)
    t = _Tunnel(agent, dash)
    await t.send({"id": 1, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": hauflo()})
    cards_of(dash.config, 0, 0).append(copy.deepcopy(MY_CARD))
    r = await t.send({"id": 2, "type": "lovelace/config/save", "url_path": "hauflo-home", "config": {"views": []}})
    assert r["success"]
    # Hauflo's views gone, the integrator's card kept in a view of its own («Гостиная»).
    assert dash.config["views"] == [{"title": "Гостиная", "path": "living-kept", "type": "sections", "sections": [{"type": "grid", "cards": [MY_CARD]}]}]
    assert not agent.channels[1].sent  # never relayed to HA as it was


async def test_foreign_card_from_the_cloud_is_refused(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    dash = _Dash(hauflo())
    t = _Tunnel(agent, dash)
    evil = hauflo()
    cards_of(evil, 0, 0).append({"type": "tile", "entity": "lock.front_door", ID: "v/x"})
    for typ in (dashmerge.SAVE, dashmerge.PLAN):
        r = await t.send({"id": 1, "type": typ, "url_path": "hauflo-home", "config": evil, "replace": True}, owned_ok=False)
        assert not r["success"] and r["error"]["code"] == "not_allowed", typ
    assert dash.config == hauflo() and dash.saves == 0
    # Not a Hauflo dashboard, a copy, a bad id: refused too.
    for url, cfg in (("lovelace", hauflo()), ("hauflo-home-copy-202610280400", hauflo()), ("hauflo-home", {"views": [{"title": "x", ID: "a\nb"}]})):
        r = await t.send({"id": 1, "type": dashmerge.SAVE, "url_path": url, "config": cfg, "replace": True})
        assert not r["success"] and r["error"]["code"] == "not_allowed", url


async def test_locked_or_no_backup_refuses_the_save_plan_still_reads(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    dash = _Dash(hauflo())
    t = _Tunnel(agent, dash)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ALLOW_CHANGES: False})
    r = await t.send({"id": 1, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": hauflo("x"), "replace": True})
    assert r["error"]["code"] == LOCKED_CODE
    r = await t.send({"id": 2, "type": dashmerge.PLAN, "url_path": "hauflo-home", "config": hauflo("x")})
    assert r["success"] and r["result"] == {"base": False, "exists": True}
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ALLOW_CHANGES: True})
    r = await t.send({"id": 3, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": hauflo("x"), "replace": True}, fresh=False)
    assert r["error"]["code"] == "backup_required"
    assert dash.saves == 0


async def test_no_base_needs_the_clouds_replace(hass: HomeAssistant, entry) -> None:
    """A dashboard the agent never saved (an older agent did): nothing tells the integrator's cards — the cloud's
    «replace» (its own hash check, as before this agent) or nothing; the pre-save config is copied either way."""
    agent = entry.runtime_data
    cur = hauflo()
    cards_of(cur, 0, 0).append(copy.deepcopy(MY_CARD))
    dash = _Dash(cur)
    t = _Tunnel(agent, dash)
    r = await t.send({"id": 1, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": hauflo()})
    assert r["error"]["code"] == "no_base" and dash.saves == 0
    r = await t.send({"id": 2, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": hauflo(), "replace": True})
    assert r["success"] and r["result"]["copy"] and dash.config == hauflo()
    # From now on it merges: «replace» no longer wipes what the integrator adds.
    cards_of(dash.config, 1, 0).append(copy.deepcopy(MY_CARD))
    r = await t.send({"id": 3, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": hauflo(), "replace": True})
    assert r["success"] and MY_CARD in cards_of(dash.config, 1, 0)


def test_the_tunnel_check_knows_the_id():
    ok = agent_mod._lovelace_ok
    with patch.object(agent_mod, "_hauflo_entity", return_value=True):
        assert ok(None, {"views": [{"title": "x", ID: "v/living", "cards": [{"type": "tile", "entity": "light.a", ID: "v/living/c:light.a"}]}]}, set(), set())
        assert not ok(None, {"views": [{"title": "x", ID: {"type": "x"}}]}, set(), set())
        assert not ok(None, {"views": [{"title": "x", ID: "x" * 300}]}, set(), set())
