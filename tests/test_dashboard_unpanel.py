"""A panel room page becomes a sections page and keeps what the family put on it (owner live test 28.10, «Котельная»:
HA's «Добавить на панель» appended the ZONT boiler controller's cards to Hauflo's panel page — hidden under «В режиме
панели может отображаться только одна карточка»)."""

import copy
import json
from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.hauflo import dashmerge
from custom_components.hauflo.dashmerge import ID, plan_and_merge


def _building():
    return {"title": "Главная", "path": "building", "type": "sections", ID: "v/building",
            "sections": [{"type": "grid", ID: "v/building/s:hero:", "cards": [{"type": "custom:hauflo-hero", ID: "v/building/s:hero:/hero:"}]}]}


def _panel_room():
    """Hauflo's empty room page as agents 0.15.x saved it: a panel view with the area card only."""
    return {"views": [
        _building(),
        {"title": "Котельная", "path": "servis_kotelnaia", "subview": True, "back_path": "/hauflo-x/servis", "type": "panel", ID: "v/servis_kotelnaia",
         "cards": [{"type": "custom:hauflo-area", "area": "kotelnaia", "panel": True, ID: "v/servis_kotelnaia/area:"}]},
    ]}


def _sections_room():
    """The same page as the cloud sends it now: a sections view, one column, the area card in its one section."""
    return {"views": [
        _building(),
        {"title": "Котельная", "path": "servis_kotelnaia", "subview": True, "back_path": "/hauflo-x/servis", "type": "sections", "max_columns": 1, ID: "v/servis_kotelnaia",
         "sections": [{"type": "grid", ID: "v/servis_kotelnaia/s:area:", "cards": [{"type": "custom:hauflo-area", "area": "kotelnaia", ID: "v/servis_kotelnaia/s:area:/area:"}]}]},
    ]}


# What HA's «Добавить на панель» appended to the panel page (the owner's HA, ZONT boiler controller).
ZONT_GRID = {"type": "grid", "square": False, "columns": 1, "cards": [
    {"type": "entities", "entities": [{"entity": "switch.servis_kotelnaia_retsirkuliatsiia", "name": "Рециркуляция"}], "title": "Котельная"},
    {"type": "thermostat", "entity": "climate.servis_kotelnaia_dom_roditelei_mg28_gvs"}]}
ZONT_LIST = {"type": "entities", "entities": [{"entity": "sensor.servis_kotelnaia_dom_roditelei_mg28_g_s", "name": "Г.С."}], "title": "Котельная"}


def test_panel_room_page_becomes_sections_and_keeps_what_the_family_added():
    base = _panel_room()
    cur = copy.deepcopy(base)
    cur["views"][1]["cards"] += [copy.deepcopy(ZONT_GRID), copy.deepcopy(ZONT_LIST)]
    merged, plan = plan_and_merge(_sections_room(), cur, base)
    room = merged["views"][1]
    # The same page (path, id), a sections view now — no panel, no «-kept» page.
    assert [v["path"] for v in merged["views"]] == ["building", "servis_kotelnaia"]
    assert room["type"] == "sections" and room["max_columns"] == 1 and room[ID] == "v/servis_kotelnaia" and "cards" not in room
    assert len(room["sections"]) == 1
    got = room["sections"][0]["cards"]
    # Hauflo's card first (its new version, no «panel»), then what the family added, as it was.
    assert got[0] == {"type": "custom:hauflo-area", "area": "kotelnaia", ID: "v/servis_kotelnaia/s:area:/area:"}
    assert got[1:] == [ZONT_GRID, ZONT_LIST]
    assert plan["lost"] == 0 and len(plan["local"]) == 2 and plan["changes"]
    assert '"panel"' not in json.dumps(merged)


def test_panel_room_page_without_family_cards_is_just_the_new_page():
    base = _panel_room()
    merged, plan = plan_and_merge(_sections_room(), copy.deepcopy(base), base)
    assert merged == _sections_room()
    assert plan["local"] == [] and plan["conflicts"] == [] and plan["lost"] == 0 and not plan["edited"]


def test_panel_page_the_integrator_retitled_keeps_his_title_and_his_cards():
    base = _panel_room()
    cur = copy.deepcopy(base)
    cur["views"][1]["title"] = "Бойлерная"
    cur["views"][1]["cards"].append(copy.deepcopy(ZONT_LIST))
    merged, plan = plan_and_merge(_sections_room(), cur, base)
    room = merged["views"][1]
    assert room["title"] == "Бойлерная" and room["type"] == "sections"
    assert room["sections"][0]["cards"][-1] == ZONT_LIST and plan["lost"] == 0


def test_a_panel_page_the_cloud_still_sends_as_a_panel_is_left_alone():
    base = _panel_room()
    cur = copy.deepcopy(base)
    cur["views"][1]["cards"].append(copy.deepcopy(ZONT_LIST))
    merged, _ = plan_and_merge(copy.deepcopy(base), cur, base)
    assert merged["views"][1]["type"] == "panel" and merged["views"][1]["cards"][-1] == ZONT_LIST


def test_the_familys_own_panel_view_is_never_touched():
    base = _panel_room()
    cur = copy.deepcopy(base)
    his = {"title": "Камеры", "path": "cams", "type": "panel", "cards": [{"type": "picture-glance", "camera_image": "camera.gate"}]}
    cur["views"].append(copy.deepcopy(his))
    merged, plan = plan_and_merge(_sections_room(), cur, base)
    assert merged["views"][-1] == his and plan["lost"] == 0


async def test_save_migrates_a_panel_page_through_the_agent(hass: HomeAssistant) -> None:
    """The agent's own save (rekeyed against the base read as sections): one page, the family's cards on it, base = new."""
    m = dashmerge.DashboardMerge(hass)
    m.bases["hauflo-x"] = _panel_room()
    cur = _panel_room()
    cur["views"][1]["cards"].append(copy.deepcopy(ZONT_LIST))
    saved = {}

    class Dash:
        mode = "storage"
        config = {"title": "Дом"}

        async def async_load(self, force):
            return copy.deepcopy(cur)

        async def async_save(self, cfg):
            saved["cfg"] = cfg

    async def local(cmds):
        return [[]] if cmds[0]["type"] == "lovelace/dashboards/list" else [None]

    async def nothing(*a, **k):
        return None

    with patch.object(m, "_dashboard", return_value=Dash()), patch.object(m.store, "async_save", nothing), patch.object(m.meta_store, "async_save", nothing):
        res = await m.save("hauflo-x", _sections_room(), [], [], False, "Дом", local)
    room = saved["cfg"]["views"][1]
    assert room[ID] == "v/servis_kotelnaia" and room["type"] == "sections"
    assert room["sections"][0]["cards"][-1] == ZONT_LIST
    assert res["lost"] == 0 and m.bases["hauflo-x"]["views"][1][ID] == "v/servis_kotelnaia"
