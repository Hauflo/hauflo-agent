"""The integrator's own cards survive a Hauflo deploy (owner 28.10), merged by the agent (dashmerge.py) — and the cloud
can neither write nor read them: it sends only Hauflo's part (the tunnel's dashboard check), it can't name a local
card, a lock or a missing backup stops the save, the plan carries no foreign entity id."""

import copy
import glob
import re
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
    """The same key changed in HA and by Hauflo to different values: his item stays unless he takes Hauflo's."""
    base = hauflo()
    cur = copy.deepcopy(base)
    cards_of(cur, 0, 1)[1]["name"] = "Сплит"
    new = hauflo()
    cards_of(new, 0, 1)[1]["name"] = "Кондиционер"
    merged, plan = plan_and_merge(new, cur, base)
    assert cards_of(merged, 0, 1)[1] == cards_of(cur, 0, 1)[1]
    assert plan["conflicts"] == [{"id": "v/living/s:climate/c:t", "kind": "card", "type": "tile", "entity": "climate.living", "where": "Гостиная", "kept": "yours", "updated": True}]
    assert plan["untouched"] == 0 and plan["pages"] == []
    merged, plan = plan_and_merge(new, cur, base, take=["v/living/s:climate/c:t"])
    assert cards_of(merged, 0, 1)[1] == cards_of(new, 0, 1)[1]
    assert plan["taken"] == 1
    merged, _ = plan_and_merge(new, cur, base, take=True)
    assert merged == new


def test_edited_hauflo_item_merges_key_by_key_without_a_conflict():
    """п.16 (399 13.11): a picture picked in HA on a Hauflo tile, Hauflo's new version with the same picture and new
    keys (temps, alarms, picture_from) — no conflict, the save goes through with both. Likewise a key Hauflo left as it
    was (his `features`) next to Hauflo's rename; a key he removed stays removed; the same at section and view level."""
    base = hauflo()
    cur = copy.deepcopy(base)
    kitchen = cards_of(cur, 1, 0)[0]
    kitchen["picture"] = "/local/hauflo/kitchen.jpg"  # picked in HA
    cards_of(cur, 0, 1)[1]["features"] = [{"type": "climate-hvac-modes"}]  # his, Hauflo never sends it
    del cards_of(cur, 0, 0)[1]["entity"]  # removed in HA, Hauflo leaves the key as it was
    cur["views"][0]["title"] = "Зал"  # the view renamed in HA
    cur["views"][0]["sections"][0]["column_span"] = 2  # his section setting
    new = hauflo()
    cards_of(new, 1, 0)[0].update({"picture": "/local/hauflo/kitchen.jpg", "picture_from": "ha", "temps": ["sensor.kitchen_t"], "alarms": ["binary_sensor.kitchen_leak"]})
    cards_of(new, 0, 1)[1]["name"] = "Кондиционер"
    new["views"][0]["icon"] = "mdi:sofa"
    new["views"][0]["sections"][0]["cards"][0]["heading"] = "Свет"
    merged, plan = plan_and_merge(new, cur, base)
    assert plan["conflicts"] == [] and plan["untouched"] == 0 and plan["changes"] is True
    assert cards_of(merged, 1, 0)[0] == cards_of(new, 1, 0)[0]
    assert cards_of(merged, 0, 1)[1] == {**cards_of(new, 0, 1)[1], "features": [{"type": "climate-hvac-modes"}]}
    assert "entity" not in cards_of(merged, 0, 0)[1] and cards_of(merged, 0, 0)[1][ID] == "v/living/s:light/c:l1"
    assert merged["views"][0]["title"] == "Зал" and merged["views"][0]["icon"] == "mdi:sofa"
    assert merged["views"][0]["sections"][0]["column_span"] == 2 and merged["views"][0]["sections"][0]["cards"][0]["heading"] == "Свет"
    assert {r["id"]: r["changed"] for r in plan["pages"]} == {"v/living": 2, "v/kitchen": 1}  # the heading and the climate card; a view is no block
    # The key he removed, Hauflo now changes: a conflict as before; his version of the card stays.
    new2 = copy.deepcopy(new)
    cards_of(new2, 0, 0)[1]["entity"] = "light.living_wall_2"
    merged, plan = plan_and_merge(new2, cur, base)
    assert [c["id"] for c in plan["conflicts"]] == ["v/living/s:light/c:l1"]
    assert cards_of(merged, 0, 0)[1] == cards_of(cur, 0, 0)[1]
    assert plan["untouched"] == 0


def test_automations_tab_moved_into_service_keeps_the_family_cards_on_it():
    """Д17 (owner 28.10): «Автоматизации» is no tab of its own any more but a section of «Сервис». The old tab goes with
    Hauflo's card; whatever the family put on it stays (in a view of its own), and nothing of the family's is lost."""
    auto = {"type": "custom:hauflo-automations", "grid_options": {"columns": "full"}}
    status = lambda: section("v/service/s:status", card("v/service/s:status/c:a", "sensor.a"))
    old = {"views": [view("v/service", "service", "Сервис", status()), view("v/automations", "automations", "Автоматизации", {"type": "grid", ID: "v/automations/s:automations", "cards": [{**auto, ID: "v/automations/s:automations/automations:"}]})]}
    new = {"views": [view("v/service", "service", "Сервис", status(), {"type": "grid", ID: "v/service/s:automations", "cards": [{**auto, ID: "v/service/s:automations/automations:"}]})]}
    cur = copy.deepcopy(old)
    cur["views"][1]["sections"][0]["cards"].append({"type": "markdown", "content": "семейное правило"})
    merged, plan = plan_and_merge(new, cur, copy.deepcopy(old))
    assert plan["lost"] == 0
    assert "семейное правило" in json.dumps(merged, ensure_ascii=False)
    assert json.dumps(merged).count("hauflo-automations") == 1
    assert [v["path"] for v in merged["views"] if "hauflo-automations" in json.dumps(v)] == ["service"]
    # nothing of the family's there: the old tab just goes
    clean, plan = plan_and_merge(new, copy.deepcopy(old), copy.deepcopy(old))
    assert plan["lost"] == 0 and [v["path"] for v in clean["views"]] == ["service"]


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
    new = hauflo()
    cards_of(new, 0, 1)[1]["entity"] = "climate.living_2"  # … that Hauflo points elsewhere too: a conflict
    _, plan = plan_and_merge(new, cur, base)
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
    # «Copies» the agent didn't make (the cloud's from the future, security re-review 28.10) are never pruned for its own.
    fakes = [f"hauflo-home-copy-20991231235{i}" for i in range(3)]
    t.dashboards += [{"id": f"fake{i}", "url_path": u} for i, u in enumerate(fakes)]
    # The integrator adds his card; four deploys later his card is there and three of the agent's copies are left.
    cards_of(dash.config, 0, 0).append(copy.deepcopy(MY_CARD))
    r = await t.send({"id": 2, "type": dashmerge.PLAN, "url_path": "hauflo-home", "config": hauflo("Люстра")})
    assert r["success"] and r["result"]["local"][0]["title"] == "Мой поливочный сценарий" and r["result"]["changes"] is True
    made = []
    for i in range(4):
        r = await t.send({"id": 3 + i, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": hauflo(f"Люстра {i}"), "title": "Дом"})
        assert r["success"] and re.fullmatch(r"hauflo-home-copy-\d{14}-[0-9a-f]{6}", r["result"]["copy"]), r
        # The fourth copy prunes the first — and says so (the backend tells the integrator; acceptance A 06.11).
        assert r["result"].get("copies_removed") == (1 if i == 3 else None), r
        made.append(r["result"]["copy"])
        assert MY_CARD in cards_of(dash.config, 0, 0) and cards_of(dash.config, 0, 0)[1]["name"] == f"Люстра {i}"
        cards_of(dash.config, 0, 0)[0]["heading"] = f"правка {i}"  # he edits again: the next deploy copies again
    assert len(set(made)) == 4  # the same second: never one copy for two saves
    copies = [d["url_path"] for d in t.dashboards if "-copy-" in d["url_path"]]
    assert sorted(copies) == sorted(fakes + made[1:]) and agent.dashboards.copies["hauflo-home"] == made[1:]


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
        assert r["result"]["copy_reason"] == "upgrade" and re.fullmatch(r"hauflo-home-copy-20261028025900-[0-9a-f]{6}", r["result"]["copy"])
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
    # accept3b №6: the refused card is left off and said — never on HA's dashboard, never the whole dashboard refused
    for typ in (dashmerge.PLAN, dashmerge.SAVE):
        r = await t.send({"id": 1, "type": typ, "url_path": "hauflo-home", "config": evil, "replace": True}, owned_ok=False)
        assert r["success"] and r["result"]["dropped"] == [{"entity": "lock.front_door", "why": "protected"}], typ
    assert dash.config == hauflo() and "lock.front_door" not in json.dumps(dash.config)  # the rest is what HA has: nothing to write
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


# --- security re-review 28.10: ids of another kind, the «nothing of his lost» rule, copies, reads -----------------------


GARAGE = {"type": "entities", "title": "Мой гараж", "entities": ["lock.front_door", "cover.garage_door"]}
GARDEN = {"type": "grid", "cards": [{"type": "tile", "entity": "valve.front_garden", "name": "Полив сада"}]}


def _with_his_cards():
    """Hauflo's dashboard as HA holds it after the integrator added a card in Hauflo's section and a section of his own
    next to the kitchen's — the reviewer's dm1."""
    cur = hauflo()
    cards_of(cur, 0, 0).append(copy.deepcopy(GARAGE))
    cur["views"][1]["sections"].append(copy.deepcopy(GARDEN))
    return cur


def _his(cfg):
    txt = json.dumps(cfg, ensure_ascii=False)
    return [e for e in ("lock.front_door", "cover.garage_door", "valve.front_garden") if e in txt]


def _section_as_card_view_as_section():
    """The reviewer's dm3: the section holding his card sent as a card, the view next to his section sent as a section."""
    return {"views": [view("v/living", "living", "Гостиная", {"type": "grid", ID: "v/x", "cards": [
        card("v/living/s:light", "light.living_main"), card("v/living/s:light/c:l1", "light.living_main")]},
        {"type": "grid", ID: "v/kitchen", "cards": []})]}


def test_an_id_sent_at_another_level_is_a_new_item_his_cards_stay():
    base, cur = hauflo(), _with_his_cards()
    merged, plan = plan_and_merge(_section_as_card_view_as_section(), cur, base)
    assert _his(merged) == ["lock.front_door", "cover.garage_door", "valve.front_garden"]
    assert plan["lost"] == 0
    assert GARAGE in [c for v in merged["views"] for s in v.get("sections", []) for c in s.get("cards", [])]
    # The new items got ids of their own; the base's section / view went as dropped (their own cards kept, said so).
    assert "v/living/s:light~2" in ids(merged) and "v/kitchen~2" in ids(merged)
    assert {x.get("title") for x in plan["local"]} >= {"Мой гараж"}


def test_a_view_turned_masonry_is_another_kind():
    base, cur = hauflo(), _with_his_cards()
    new = hauflo()
    new["views"][1] = {"title": "Кухня", "path": "kitchen", ID: "v/kitchen", "cards": [card("v/kitchen/c", "light.kitchen")]}
    merged, plan = plan_and_merge(new, cur, base)
    assert plan["lost"] == 0 and "valve.front_garden" in _his(merged)
    assert "v/kitchen~2" in ids(merged)


def test_his_item_under_a_hauflo_id_of_another_kind_is_his():
    """HA holds a card under the id the base knows as a section (pasted in the raw editor): it is his, kept as it is."""
    base = hauflo()
    cur = hauflo()
    mine = {"type": "markdown", "content": "моё", ID: "v/kitchen/s:light"}
    cards_of(cur, 0, 0).append(mine)
    merged, plan = plan_and_merge(hauflo(), cur, base)
    assert {"type": "markdown", "content": "моё"} in cards_of(merged, 0, 0) and plan["lost"] == 0


def test_lost_refuses_whatever_the_merge_did():
    """The last line: with the kind check off (the old bug back), the merge loses his cards — and says so."""
    base, cur = hauflo(), _with_his_cards()
    with patch.object(dashmerge, "rekey", lambda cfg, base: cfg), patch.object(dashmerge, "_Index", _IndexNoKind):
        merged, plan = plan_and_merge(_section_as_card_view_as_section(), cur, base)
    assert plan["lost"] >= 1 and "lock.front_door" not in _his(merged)


class _IndexNoKind(dashmerge._Index):
    def __init__(self, cfg, expect=None):
        super().__init__(cfg, None)


def test_real_dashboards_lose_nothing_of_his():
    assert GENERATED
    for path in GENERATED:
        cfg = dashmerge.ensure_ids(json.loads(pathlib.Path(path).read_text()))
        cur = copy.deepcopy(cfg)
        for v in cur["views"]:
            (v.get("sections") or [{"cards": v.setdefault("cards", [])}])[0].setdefault("cards", []).append(copy.deepcopy(GARAGE))
        for new in (cfg, {"views": cfg["views"][1:]}, {"views": []}):
            merged, plan = plan_and_merge(new, cur, cfg)
            assert plan["lost"] == 0, path


async def test_level_attack_through_the_tunnel_keeps_his_cards(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    dash = _Dash(None)
    t = _Tunnel(agent, dash)
    assert (await t.send({"id": 1, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": hauflo()}))["success"]
    dash.config = _with_his_cards()
    for typ in ("lovelace/config/save", dashmerge.SAVE):
        dash.config = _with_his_cards()
        agent.dashboards.bases["hauflo-home"] = dashmerge.ensure_ids(hauflo())
        r = await t.send({"id": 2, "type": typ, "url_path": "hauflo-home", "config": _section_as_card_view_as_section()})
        assert r["success"], r
        assert _his(dash.config) == ["lock.front_door", "cover.garage_door", "valve.front_garden"], typ


async def test_merge_lost_local_refuses_and_writes_nothing(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    dash = _Dash(None)
    t = _Tunnel(agent, dash)
    assert (await t.send({"id": 1, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": hauflo()}))["success"]
    dash.config = _with_his_cards()
    saves, created = dash.saves, list(t.dashboards)
    with patch.object(dashmerge, "rekey", lambda cfg, base: cfg), patch.object(dashmerge, "_Index", _IndexNoKind):
        r = await t.send({"id": 2, "type": "lovelace/config/save", "url_path": "hauflo-home", "config": _section_as_card_view_as_section()})
    assert not r["success"] and r["error"]["code"] == "merge_lost_local", r
    assert dash.saves == saves and dash.config == _with_his_cards() and t.dashboards == created
    assert agent.dashboards.bases["hauflo-home"] == dashmerge.ensure_ids(hauflo())  # Hauflo's version unchanged too


async def test_plain_save_of_an_unmanaged_hauflo_dashboard_copies_first(hass: HomeAssistant, entry) -> None:
    """Deployed by an older agent (no Hauflo version stored): a plain save is no longer a silent overwrite."""
    agent = entry.runtime_data
    dash = _Dash(_with_his_cards())
    t = _Tunnel(agent, dash)
    before = copy.deepcopy(dash.config)
    r = await t.send({"id": 1, "type": "lovelace/config/save", "url_path": "hauflo-home", "config": {"views": [{"title": "x", "cards": [{"type": "heading", "heading": "пусто"}]}]}})
    assert r["success"], r
    assert t.ws_cmds[:3] == ["lovelace/dashboards/list", "lovelace/dashboards/create", "lovelace/config/save"]
    assert agent.dashboards.copies["hauflo-home"] and agent.dashboards.manages("hauflo-home")
    assert not agent.channels[1].sent and before != dash.config


async def test_copies_are_the_agents_alone(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    dash = _Dash(hauflo())
    t = _Tunnel(agent, dash)
    for msg in (
        {"type": "lovelace/config/save", "url_path": "hauflo-home-copy-20261028035959-abcdef", "config": {"views": []}},
        {"type": "lovelace/config/save", "url_path": "hauflo-home-copy-202610280359", "config": {"views": []}},
        {"type": "lovelace/config", "url_path": "hauflo-home-copy-202610280359"},
        {"type": "lovelace/dashboards/create", "url_path": "hauflo-home-copy-209912312359", "title": "x", "mode": "storage"},
        {"type": dashmerge.SAVE, "url_path": "hauflo-home-copy-202610280359", "config": hauflo(), "replace": True},
    ):
        r = await t.send({"id": 1, **msg})
        assert not r["success"] and r["error"]["code"] == "not_allowed", msg
    assert not agent.channels[1].sent
    # «Show the copy to everyone» (dashboards/update): only a Hauflo dashboard that isn't a copy.
    from homeassistant.components.lovelace.const import LOVELACE_DATA  # noqa: PLC0415

    import types  # noqa: PLC0415

    fake = types.SimpleNamespace(data={LOVELACE_DATA: types.SimpleNamespace(dashboards={
        "hauflo-home": types.SimpleNamespace(config={"id": "d1"}),
        "hauflo-home-copy-20261028035959-abcdef": types.SimpleNamespace(config={"id": "d2"}),
    })})
    assert agent_mod._own_dashboard(fake, "d1") and not agent_mod._own_dashboard(fake, "d2")


async def test_a_read_through_the_tunnel_shows_hauflos_part_only(hass: HomeAssistant, entry) -> None:
    agent = entry.runtime_data
    dash = _Dash(None)
    t = _Tunnel(agent, dash)
    assert (await t.send({"id": 1, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": hauflo()}))["success"]
    plain = await t.send({"id": 2, "type": "lovelace/config", "url_path": "hauflo-home"})
    assert plain["success"] and plain["result"] == dash.config  # nothing of his: Hauflo's config as it is
    dash.config = _with_his_cards()
    cards_of(dash.config, 0, 0)[1]["name"] = "lock.kitchen_door"  # he edits a Hauflo card too
    r = await t.send({"id": 3, "type": "lovelace/config", "url_path": "hauflo-home"})
    assert r["success"] and _his(r["result"]) == [] and "lock.kitchen_door" not in json.dumps(r["result"])
    got = cards_of(r["result"], 0, 0)
    assert got[1] == {ID: "v/living/s:light/c:l1", "type": dashmerge.LOCAL, "edited": got[1]["edited"]}
    assert got[-1]["type"] == dashmerge.LOCAL and set(got[-1]) == {"type", "hash"}
    assert r["result"]["views"][1]["sections"][-1]["type"] == dashmerge.LOCAL
    # The same config reads the same; a change of his changes the read (the cloud's «edited in HA» check still works).
    assert (await t.send({"id": 4, "type": "lovelace/config", "url_path": "hauflo-home"}))["result"] == r["result"]
    cards_of(dash.config, 0, 0)[-1]["title"] = "Гараж"
    assert (await t.send({"id": 5, "type": "lovelace/config", "url_path": "hauflo-home"}))["result"] != r["result"]
    assert not agent.channels[1].sent
    # A Hauflo dashboard the agent never saved: relayed to HA as before (nothing tells his items there).
    await t.send({"id": 6, "type": "lovelace/config", "url_path": "hauflo-other"})
    assert json.loads(agent.channels[1].sent[-1])["url_path"] == "hauflo-other"


async def test_a_copy_made_before_a_take_save_is_never_pruned(hass: HomeAssistant, entry) -> None:
    """Verifier 28.10: a take-save loses his card nested in Hauflo's vertical-stack (the plan says so, a copy keeps it),
    then three trivial saves in the same second used to prune that copy — the only place his card was left."""

    def stacked(name="Климат"):
        cfg = hauflo()
        cards_of(cfg, 0, 1)[1] = {"type": "vertical-stack", ID: "v/living/s:climate/c:t", "title": name, "cards": [{"type": "tile", "entity": "climate.living", "name": name}]}
        return cfg

    agent = entry.runtime_data
    dash = _Dash(None)
    t = _Tunnel(agent, dash)
    assert (await t.send({"id": 1, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": stacked()}))["success"]
    dash.config = _with_his_cards()
    cards_of(dash.config, 0, 1)[1] = copy.deepcopy(cards_of(stacked(), 0, 1)[1])
    cards_of(dash.config, 0, 1)[1]["cards"].append(copy.deepcopy(GARAGE))  # his card inside Hauflo's stack
    plan = await t.send({"id": 2, "type": dashmerge.PLAN, "url_path": "hauflo-home", "config": stacked("Тепло")})
    assert [c["id"] for c in plan["result"]["conflicts"]] == ["v/living/s:climate/c:t"], plan
    r = await t.send({"id": 3, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": stacked("Тепло"), "take": True})
    assert r["success"] and r["result"]["taken"] == 1, r
    kept = r["result"]["copy"]
    assert "Мой гараж" not in json.dumps(cards_of(dash.config, 0, 1), ensure_ascii=False)  # taken over: only the copy has it
    assert agent.dashboards.taken_copies == {kept}
    # Trivial saves in the same second (more than three): ordinary copies are pruned to three, the take copy stays.
    for i in range(5):
        cards_of(dash.config, 0, 0)[0]["heading"] = f"правка {i}"
        r = await t.send({"id": 4 + i, "type": dashmerge.SAVE, "url_path": "hauflo-home", "config": stacked(f"Тепло {i}"), "title": "Дом"})
        assert r["success"] and r["result"]["copy"], r
    urls = [d["url_path"] for d in t.dashboards]
    assert kept in urls and kept in agent.dashboards.copies["hauflo-home"]
    assert len([u for u in agent.dashboards.copies["hauflo-home"] if u != kept]) == dashmerge.MAX_COPIES
    assert len([u for u in urls if "-copy-" in u]) == dashmerge.MAX_COPIES + 1
    # Kept over a restart of the agent (its store).
    await agent.dashboards.async_load()
    assert agent.dashboards.taken_copies == {kept}


def test_automations_card_moved_out_of_service_is_not_left_duplicated():
    """Owner 28.10: the «Автоматизации» card was inside «Сервис», now it is a tab of its own. It is Hauflo's own card (it
    has an id): the old copy goes, the new one is added, the integrator's own card in «Сервис» stays."""
    auto = {"type": "custom:hauflo-automations", "grid_options": {"columns": "full"}}
    old = {"views": [
        view("v/living", "living", "Гостиная", section("v/living/s:l", card("v/living/s:l/c", "light.a"))),
        view("v/service", "service", "Сервис",
             {"type": "grid", ID: "v/service/s:automations:", "cards": [{**auto, ID: "v/service/s:automations:/automations:"}]},
             section("v/service/s:p", card("v/service/s:p/c", "sensor.p"))),
    ]}
    new = {"views": [
        copy.deepcopy(old["views"][0]),
        view("v/service", "service", "Сервис", copy.deepcopy(old["views"][1]["sections"][1])),
        view("v/automations", "automations", "Автоматизации",
             {"type": "grid", ID: "v/automations/s:automations:", "cards": [{**auto, ID: "v/automations/s:automations:/automations:"}]}),
    ]}
    cur = copy.deepcopy(old)
    cur["views"][1]["sections"].append({"type": "grid", "cards": [copy.deepcopy(MY_CARD)]})
    merged, _ = plan_and_merge(new, cur, old)
    assert json.dumps(merged).count("custom:hauflo-automations") == 1
    svc = next(v for v in merged["views"] if v["path"] == "service")
    assert "hauflo-automations" not in json.dumps(svc)
    assert MY_CARD in [c for s in svc["sections"] for c in s["cards"]]
    assert any(v["path"] == "automations" for v in merged["views"])


def test_automations_only_in_settings_the_card_tile_and_subview_go_and_nothing_else():
    """Owner 04.11: «Автоматизации» live only in «Настройки». A dashboard with the section / tile + subview refreshes to one
    without them: Hauflo's rules card, its tile and its page go; the readings, the rooms and the family's own card stay."""
    auto = {"type": "custom:hauflo-automations", "grid_options": {"columns": "full"}}
    tile = {"type": "custom:hauflo-room", "name": "Автоматизации", "icon": "mdi:robot-outline", "path": "/dash/automations"}
    status = lambda: section("v/service/s:status", card("v/service/s:status/c:a", "sensor.a"))
    room = lambda: view("v/living", "living", "Гостиная", section("v/living/s:l", card("v/living/s:l/c", "light.a")))
    new = {"views": [room(), view("v/service", "service", "Сервис", status())]}
    sect = {"type": "grid", ID: "v/service/s:automations:", "cards": [{**auto, ID: "v/service/s:automations:/automations:"}]}
    page = lambda: {**view("v/automations", "automations", "Автоматизации", {"type": "grid", ID: "v/automations/s:a:", "cards": [{**auto, ID: "v/automations/s:a:/a:"}]}), "subview": True, "back_path": "/dash/service"}
    olds = {
        "section": {"views": [room(), view("v/service", "service", "Сервис", status(), sect)]},
        "tile": {"views": [room(), view("v/service", "service", "Сервис", status(), {"type": "grid", ID: "v/service/s:t:", "cards": [{**tile, ID: "v/service/s:t:/t:"}]}), page()]},
    }
    for name, old in olds.items():
        cur = copy.deepcopy(old)
        cur["views"][1]["sections"].append({"type": "grid", "cards": [copy.deepcopy(MY_CARD)]})
        merged, plan = plan_and_merge(copy.deepcopy(new), cur, copy.deepcopy(old))
        text = json.dumps(merged, ensure_ascii=False)
        assert "hauflo-automations" not in text and "robot-outline" not in text, name
        assert [v["path"] for v in merged["views"]] == ["living", "service"], name
        svc = merged["views"][1]
        assert MY_CARD in [c for s in svc["sections"] for c in s["cards"]], name
        assert "sensor.a" in text and "light.a" in text, name


def test_stock_offer_only_on_the_integrators_cards_and_kept_his_after_the_merge():
    """«Заменить на Hauflo» at deploy: Hauflo's own tiles are never offered; his stock card, ticked, becomes Hauflo's
    card — still his (no Hauflo id), in its place, merged around as any own card; «Вернуть штатную» from it."""
    from custom_components.hauflo import stock

    def info(eid):
        return {"domain": eid.split(".")[0], "device_class": None, "device_id": None, "state": "on" if not eid.startswith("sensor.") else "12", "percentage_step": None, "speed_count": None}

    base = hauflo()
    cur = copy.deepcopy(base)
    mine = {"type": "entities", "title": "Свет сада", "entities": ["light.garden", "switch.path"]}
    cards_of(cur, 0, 0).insert(2, mine)
    offers = stock.offers(cur, info)
    assert [(o["from"], o["to"], o["pick"]) for o in offers] == [("entities", "hauflo-lights", True)]
    src, n = stock.apply(cur, [offers[0]["id"]], info)
    merged, plan = plan_and_merge(hauflo("Люстра"), src, base)
    got = cards_of(merged, 0, 0)
    assert n == 1 and plan["lost"] == 0
    assert {k: v for k, v in got[2].items() if k != stock.WAS} == {"type": "custom:hauflo-lights", "entities": ["light.garden", "switch.path"], "title": "Свет сада"}
    assert got[2][stock.WAS] == mine and ID not in got[2]
    assert got[1]["name"] == "Люстра"


def test_admins_view_marker_expanded_in_the_house_and_said_again_on_a_read():
    """Owner 04.11 п.21: the «Сервис» floor for HA's administrators — the cloud says a marker, the agent puts the
    admins' ids in, a read through the tunnel shows the marker again (no user id leaves the house)."""
    marker = [{"user": dashmerge.ADMINS}]
    tile = {"type": "custom:hauflo-room", ID: "c:srv", "visibility": [{"condition": "screen", "media_query": "(min-width: 700px)"}, {"condition": "user", "users": [dashmerge.ADMINS]}]}
    cfg = {"views": [{"title": "Дом", ID: "v:home", "sections": [{"type": "grid", ID: "s:home", "cards": [tile]}]}, {"title": "Сервис", ID: "v:srv", "visible": marker, "sections": []}]}
    out = dashmerge.expand_admins(copy.deepcopy(cfg), ["a1", "b2"])
    assert out["views"][1]["visible"] == [{"user": "a1"}, {"user": "b2"}]
    assert out["views"][0]["sections"][0]["cards"][0]["visibility"][1]["users"] == ["a1", "b2"]
    assert out["views"][0]["sections"][0]["cards"][0]["visibility"][0]["condition"] == "screen"
    assert "visible" not in out["views"][0]
    # No admin known: the marker stays — a list no user matches.
    assert dashmerge.expand_admins(copy.deepcopy(cfg), [])["views"][1]["visible"] == marker
    # Saved with ids; the read of that very config says the marker, never the ids.
    merged, _ = plan_and_merge(copy.deepcopy(out), None, None)
    read = dashmerge.collapse_admins(dashmerge.redact(copy.deepcopy(merged), copy.deepcopy(merged), b"k" * 32), ["b2", "a1"])
    assert read["views"][1]["visible"] == marker
    assert read["views"][0]["sections"][0]["cards"][0]["visibility"][1]["users"] == [dashmerge.ADMINS]
    assert "a1" not in json.dumps(read) and "b2" not in json.dumps(read)
    # The same deploy again: nothing changes (base and HA hold the ids, the cloud's marker expands to the same).
    again, report = plan_and_merge(dashmerge.expand_admins(copy.deepcopy(cfg), ["a1", "b2"]), merged, copy.deepcopy(out))
    assert again == merged and not report["changes"]


def test_collapse_admins_only_the_admins_list_says_the_marker():
    """п.9: a read says the marker only for a list equal to HA's administrators now — the integrator's own people,
    a part of the admins or a list after the admins changed stay as they are (never turned into «администраторы»)."""
    cfg = lambda v, u: {"views": [{"title": "A", "visible": v, "cards": [{"type": "tile", "entity": "light.a", "visibility": [{"condition": "user", "users": u}]}]}]}
    got = dashmerge.collapse_admins(cfg([{"user": "b2"}, {"user": "a1"}], ["a1", "b2"]), ["a1", "b2"])
    assert got["views"][0]["visible"] == [{"user": dashmerge.ADMINS}] and got["views"][0]["cards"][0]["visibility"][0]["users"] == [dashmerge.ADMINS]
    for v, u in (([{"user": "mom"}], ["mom"]), ([{"user": "a1"}], ["a1"]), ([{"user": "a1"}, {"user": "b2"}, {"user": "c3"}], ["a1", "b2", "c3"]), ([{"user": "a1"}, {"user": "a1"}], ["a1", "a1"])):
        got = dashmerge.collapse_admins(cfg(copy.deepcopy(v), list(u)), ["a1", "b2"])
        assert got["views"][0]["visible"] == v and got["views"][0]["cards"][0]["visibility"][0]["users"] == u
    # A list with something else than users, an empty list, no admins known: as is.
    odd = [{"user": "a1"}, {"user": "b2", "x": 1}]
    assert dashmerge.collapse_admins(cfg(copy.deepcopy(odd), []), ["a1", "b2"])["views"][0]["visible"] == odd
    assert dashmerge.collapse_admins(cfg([{"user": "a1"}], ["a1"]), [])["views"][0]["visible"] == [{"user": "a1"}]
    # Round trip: the marker expanded, read back — the marker again.
    marker = cfg([{"user": dashmerge.ADMINS}], [dashmerge.ADMINS])
    back = dashmerge.collapse_admins(dashmerge.expand_admins(copy.deepcopy(marker), ["a1", "b2"]), ["a1", "b2"])
    assert back == marker


def _diag_view(visible=True):
    v = {"title": "Диагностика", "path": "diagnostics", ID: "v:diag", "icon": "mdi:stethoscope", "type": "sections", "max_columns": 1,
         "sections": [{"type": "grid", ID: "s:diag", "cards": [{"type": "custom:hauflo-insights", ID: "c:diag"}]}]}
    if visible:
        v["visible"] = [{"user": dashmerge.ADMINS}]
    return v


def test_diagnostics_tab_survives_a_redeploy_and_follows_the_visibility_choice():
    """п.9: Hauflo's «Диагностика» tab — his edits elsewhere stay, the tab itself is Hauflo's: the integrator's
    «всем / администраторам» (its `visible`) applies on the next deploy; his own card on the tab stays too."""
    new = lambda vis: dashmerge.expand_admins({"views": [*copy.deepcopy(hauflo())["views"], _diag_view(vis)]}, ["a1"])
    base = new(True)
    cur = copy.deepcopy(base)
    mine = {"type": "markdown", "content": "моё"}
    cards_of(cur, 0, 0).append(mine)
    cur["views"][-1]["sections"][0]["cards"].append({"type": "markdown", "content": "заметка"})
    # Admins → all: `visible` goes, his cards stay (on his tab and on the diagnostics tab).
    merged, report = plan_and_merge(new(False), copy.deepcopy(cur), copy.deepcopy(base))
    diag = merged["views"][-1]
    assert report["lost"] == 0 and "visible" not in diag
    assert mine in cards_of(merged, 0, 0) and {"type": "markdown", "content": "заметка"} in diag["sections"][0]["cards"]
    assert [c["type"] for c in diag["sections"][0]["cards"]][0] == "custom:hauflo-insights"
    # And back: all → admins.
    again, report = plan_and_merge(new(True), copy.deepcopy(merged), new(False))
    assert report["lost"] == 0 and again["views"][-1]["visible"] == [{"user": "a1"}]
    # The same deploy once more: nothing to change.
    same, report = plan_and_merge(new(True), copy.deepcopy(again), new(True))
    assert same == again and not report["changes"]


def test_diagnostics_tab_on_his_own_diagnostics_path_gets_its_own():
    """Acceptance 10.11 L8: his own page on «diagnostics» + Hauflo's tab — two pages of one path (HA opens his for both).
    Hauflo's tab takes «diagnostics_2», the next deploy keeps it, his page and its card stay; a page of Hauflo's a tile
    opens (a room's) keeps its path."""
    new = {"views": [*copy.deepcopy(hauflo())["views"], _diag_view(False)]}
    his = {"title": "Мои графики", "path": "diagnostics", "type": "sections", "sections": [{"type": "grid", "cards": [{"type": "markdown", "content": "моё"}]}]}
    cur = copy.deepcopy(new)
    cur["views"].append(copy.deepcopy(his))
    merged, report = plan_and_merge(new, copy.deepcopy(cur), copy.deepcopy(new))
    paths = [v.get("path") for v in merged["views"]]
    assert report["lost"] == 0 and his in merged["views"]
    assert paths.count("diagnostics") == 1 and "diagnostics_2" in paths
    assert next(v for v in merged["views"] if v.get("path") == "diagnostics_2")["sections"][0]["cards"][0]["type"] == "custom:hauflo-insights"
    again, report = plan_and_merge(new, copy.deepcopy(merged), copy.deepcopy(new))
    assert [v.get("path") for v in again["views"]] == paths and report["lost"] == 0
    # A page a card opens keeps its path: nothing renamed.
    tile = {"type": "button", "tap_action": {"action": "navigate", "navigation_path": "/x/diagnostics"}}
    cfg = {"views": [{"title": "T", "path": "diagnostics", ID: "v:t", "cards": []}, {"title": "M", "path": "diagnostics", "cards": [tile]}]}
    dashmerge.unique_paths(cfg)
    assert [v["path"] for v in cfg["views"]] == ["diagnostics", "diagnostics"]


def test_twins_his_empty_page_named_as_hauflos_offered_and_dropped_only_ticked_with_his_tiles():
    """п.51 (Горького 3): his empty «Протечки» page + his tile to it next to Hauflo's «Протечки» — offered; only a
    ticked, still-empty one goes (with his tiles that open it); a page with cards is never offered."""
    hauflo_room = {"type": "custom:hauflo-room", "name": "Протечки", "path": "/hauflo-x/servis_protechki", ID: "v/servis/s/room:p"}
    new = {"views": [
        {"title": "Сервис", "path": "servis", "type": "sections", ID: "v/servis", "sections": [{"type": "grid", ID: "v/servis/s", "cards": [hauflo_room]}]},
        {"title": "Протечки", "path": "servis_protechki", "type": "sections", ID: "v/servis_protechki", "sections": []},
        {"title": "Котельная", "path": "servis_kotelnaia", "type": "sections", ID: "v/servis_kotelnaia", "sections": []},
    ]}
    mine_tile = {"type": "custom:hauflo-room", "name": "Протечки", "icon": "hauflo:leak", "path": "protechki"}
    other_tile = {"type": "button", "name": "Котельная", "tap_action": {"action": "navigate", "navigation_path": "/hauflo-x/kotelnaia"}}
    current = copy.deepcopy(new)
    current["views"][0]["sections"][0]["cards"] += [mine_tile, other_tile]
    current["views"] += [
        {"title": " протечки ", "path": "protechki", "subview": True, "type": "sections", "sections": []},
        {"title": "Котельная", "path": "kotelnaia", "type": "sections", "sections": [{"type": "grid", "cards": [{"type": "tile", "entity": "switch.zont"}]}]},
    ]
    offered = dashmerge.twins(current, new)
    assert offered == [{"id": "protechki", "title": "протечки", "tiles": 1}]  # «Котельная» of his holds a card: never
    # Not ticked: nothing goes.
    same, n = dashmerge.drop_twins(current, new, [])
    assert n == 0 and same is current
    # Ticked — and a name the cloud makes up (his full page) is ignored.
    out, n = dashmerge.drop_twins(current, new, ["protechki", "kotelnaia"])
    assert n == 1
    assert [v["path"] for v in out["views"]] == ["servis", "servis_protechki", "servis_kotelnaia", "kotelnaia"]
    assert mine_tile not in out["views"][0]["sections"][0]["cards"] and other_tile in out["views"][0]["sections"][0]["cards"]
    assert hauflo_room in out["views"][0]["sections"][0]["cards"]
    # The merge after it loses none of his other items.
    _, report = plan_and_merge(new, out, copy.deepcopy(new))
    assert report["lost"] == 0


STYLE_PAIRS = sorted({re.sub(r"_(atelier|classic)_", "_@_", pathlib.Path(p).name) for p in glob.glob(str(pathlib.Path(__file__).parent / "fixtures/dashboards/generated/*_0.21.0_en.json"))})


def _unsuffixed(node):
    """Ids without the `~N` a view of another kind gets (rekey: a panel page is a sections page in the other design)."""
    if isinstance(node, dict):
        return {k: (re.sub(r"~\d+", "", v) if k == ID and isinstance(v, str) else _unsuffixed(v)) for k, v in node.items()}
    return [_unsuffixed(x) for x in node] if isinstance(node, list) else node


def _agent_save(new, live, base):
    """What `DashboardMerge.save` does to the config the cloud sends: ids, rekey against the base, merge; the stored base is the keyed `new`."""
    new = dashmerge.rekey(dashmerge.ensure_ids(copy.deepcopy(new)), dashmerge.unpanel(base, new))
    merged, plan = plan_and_merge(new, live, base)
    return merged, new, plan


@pytest.mark.parametrize("pair", STYLE_PAIRS)
def test_design_round_trip_keeps_the_integrators_card(pair):
    """п.30 (04.11): Atelier → classic → Atelier (and the other way) through the agent's merge, the way the tunnel runs it.
    His own card in a Hauflo section and his own view survive every switch; nothing is lost or in conflict; no page is
    doubled; the way back is Hauflo's first design plus his items, ids the same but for the `~N` of a page that changed kind."""
    d = pathlib.Path(__file__).parent / "fixtures/dashboards/generated"
    at = json.load(open(d / pair.replace("@", "atelier")))
    cl = json.load(open(d / pair.replace("@", "classic")))
    mine = {"type": "entities", "title": "Мой поливочный сценарий", "entities": ["switch.garden_valve", "sensor.soil"]}
    for first, second in ((at, cl), (cl, at)):
        live = copy.deepcopy(first)
        for v in live["views"]:
            if isinstance(v.get("sections"), list) and v["sections"]:
                v["sections"][-1].setdefault("cards", []).append(copy.deepcopy(mine))
                break
        live["views"].append({"title": "Сад", "path": "garden", "cards": [copy.deepcopy(mine)]})
        base = first
        for new in (second, first, second, first):
            live, base, plan = _agent_save(new, live, base)
            assert plan["conflicts"] == [] and plan["lost"] == 0
            assert json.dumps(live, ensure_ascii=False).count("switch.garden_valve") == 2
            paths = [v.get("path") for v in live["views"]]
            assert len(paths) == len(set(paths))
        no_mine = copy.deepcopy(live)
        no_mine["views"] = [v for v in no_mine["views"] if v.get("path") != "garden"]
        for v in no_mine["views"]:
            for sec in v.get("sections") or []:
                sec["cards"] = [c for c in sec.get("cards", []) if c != mine]
            # His card whose Hauflo section the other design doesn't have waits in a section / view of its own (no id).
            if isinstance(v.get("sections"), list):
                v["sections"] = [sec for sec in v["sections"] if sec.get(ID) or sec.get("cards")]
        no_mine["views"] = [v for v in no_mine["views"] if v.get(ID) or v.get("sections") or v.get("cards")]
        assert _unsuffixed(no_mine) == _unsuffixed(dashmerge.ensure_ids(copy.deepcopy(first)))


def test_edited_where_names_the_places_never_the_content():
    """Р33 (owner 11.11): «edited» said nothing of where — the plan names the places (path, key, how), no values."""
    base = hauflo()
    cur = copy.deepcopy(base)
    cards_of(cur, 0, 0)[1]["name"] = "Секретное имя"
    _, plan = plan_and_merge(hauflo(), cur, base)
    assert plan["edited"] is True
    assert plan["edited_where"] == [{"path": "views/0/sections/0/cards/1", "op": "changed", "key": "name", "view": "Гостиная", "type": "tile"}]
    assert "Секретное имя" not in json.dumps(plan["edited_where"], ensure_ascii=False)
    _, same = plan_and_merge(hauflo(), copy.deepcopy(base), base)
    assert same["edited"] is False and "edited_where" not in same


def test_edited_where_is_capped():
    base = hauflo()
    cur = copy.deepcopy(base)
    for v in cur["views"]:
        v["icon"] = "mdi:home"
        v["subview"] = False
        v["badges"] = []
    where = dashmerge.edited_where(cur, base, limit=3)
    assert len(where) == 3 and all(x["op"] == "added" for x in where)


def test_r2_a_block_he_edited_that_hauflo_drops_stays_his_and_says_its_hauflo_id():
    """0.33 Р2 (Горького 3, «Котельная»): the integrator added the pump to Hauflo's «Отопление» block in its editor (his
    rows, `items`); the project no longer has that block. The merge keeps the block as his (ручное не удаляется) — and its
    plan line carries the block's Hauflo id, so the platform's loss guard says «сохранится как ваше», not «удалится»."""
    heat = {"type": "custom:hauflo-area", "block": "heating", "title": "Отопление", "area": "kotelnaia", ID: "v/boiler/s:main/heat"}
    old = {"views": [view("v/boiler", "boiler", "Котельная", {"type": "grid", ID: "v/boiler/s:main", "cards": [card("v/boiler/s:main/t", "sensor.boiler_t"), heat]})]}
    new = {"views": [view("v/boiler", "boiler", "Котельная", {"type": "grid", ID: "v/boiler/s:main", "cards": [card("v/boiler/s:main/t", "sensor.boiler_t")]})]}
    cur = copy.deepcopy(old)
    cur["views"][0]["sections"][0]["cards"][1]["items"] = [{"entity": "switch.nasos_tp", "name": "Насос тёплого пола"}]
    merged, plan = plan_and_merge(new, cur, copy.deepcopy(old))
    kept = merged["views"][0]["sections"][0]["cards"]
    assert any(c.get("title") == "Отопление" and c.get("items") for c in kept), kept
    assert plan["lost"] == 0
    assert [x for x in plan["local"] if x.get("dropped")] == [{"kind": "card", "type": "custom:hauflo-area", "title": "Отопление", "where": "Котельная", "dropped": True, "id": "v/boiler/s:main/heat"}]
    # Not edited — Hauflo dropping it removes it, no line of his.
    merged, plan = plan_and_merge(new, copy.deepcopy(old), copy.deepcopy(old))
    assert "Отопление" not in json.dumps(merged, ensure_ascii=False) and not plan["local"]
