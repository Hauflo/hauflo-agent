"""Р77б (owner 13.11): the page «Служебное» is for the HA users the integrator ticks, by name — the client is often an
administrator himself, so not «all admins». The cloud says a marker per name, the agent adds those people's ids in the
house (dashmerge.expand_users), a read for the cloud drops them again (collapse_users): no user id leaves the house."""

import copy
import json

from homeassistant.core import HomeAssistant

from custom_components.hauflo import dashmerge
from custom_components.hauflo.dashmerge import ID, plan_and_merge

M = dashmerge.USER_MARKER
PEOPLE = [("id-ivan", "Иван"), ("id-anna", "Анна"), ("id-ivan2", "Иван"), ("id-owner", "Владелец")]


def cfg(visible, users=None):
    tile = {"type": "tile", "entity": "button.reset", ID: "c:1", **({"visibility": [{"condition": "user", "users": users}]} if users is not None else {})}
    return {"views": [
        {"title": "Дом", "path": "home", ID: "v:home", "sections": [{"type": "grid", ID: "s:1", "cards": [{"type": "tile", "entity": "light.a", ID: "c:0"}]}]},
        {"title": "Служебное", "path": "service_devices", ID: "v:srv", "visible": visible, "sections": [{"type": "grid", ID: "s:2", "cards": [tile]}]},
    ]}


def test_names_become_ids_beside_the_markers_and_a_read_says_the_markers_only():
    marker = [{"user": f"{M}Иван"}, {"user": f"{M}Анна"}]
    src = cfg(copy.deepcopy(marker), [f"{M}Анна"])
    out = dashmerge.expand_users(copy.deepcopy(src), PEOPLE)
    # Every person of the name (two «Иван»), each once; the markers stay (a read can tell what to drop).
    assert out["views"][1]["visible"] == [*marker, {"user": "id-ivan"}, {"user": "id-anna"}, {"user": "id-ivan2"}]
    assert out["views"][1]["sections"][0]["cards"][0]["visibility"][0]["users"] == [f"{M}Анна", "id-anna"]
    # A page for everyone is not touched; «Владелец» (the client, an admin too) is in no list.
    assert "visible" not in out["views"][0]
    assert "id-owner" not in json.dumps(out)
    # The read of that very config: the markers, never an id.
    back = dashmerge.collapse_users(copy.deepcopy(out), PEOPLE)
    assert back == src
    assert "id-" not in json.dumps(back)
    # Expanded twice — nothing added twice.
    assert dashmerge.expand_users(copy.deepcopy(out), PEOPLE) == out


def test_a_name_nobody_has_shows_the_page_to_nobody():
    marker = [{"user": f"{M}Сервис Hauflo"}]
    out = dashmerge.expand_users(cfg(copy.deepcopy(marker)), PEOPLE)
    assert out["views"][1]["visible"] == marker  # no user's id → a list no user matches (HA: edit mode only)
    out = dashmerge.expand_users(cfg([{"user": f"{M}Иван"}, {"user": f"{M}Нет такого"}]), PEOPLE)
    assert [x["user"] for x in out["views"][1]["visible"]] == [f"{M}Иван", f"{M}Нет такого", "id-ivan", "id-ivan2"]
    # Nobody in the house known (auth not readable): the markers alone.
    assert dashmerge.expand_users(cfg(copy.deepcopy(marker)), [])["views"][1]["visible"] == marker


def test_lists_without_markers_are_never_touched():
    """The integrator's own choice of people, the admins' marker, guests' lists: as they are, both ways."""
    for v in ([{"user": "id-ivan"}], [{"user": "id-anna"}, {"user": "id-x"}], [{"user": dashmerge.ADMINS}], []):
        c = cfg(copy.deepcopy(v), [x["user"] for x in v])
        assert dashmerge.expand_users(copy.deepcopy(c), PEOPLE) == c
        assert dashmerge.collapse_users(copy.deepcopy(c), PEOPLE) == c


def test_with_the_admins_marker_and_the_merge_the_same_deploy_changes_nothing():
    """save(): admins expanded first, then the names; the base holds the expansion — a second deploy is no change, a
    renamed person's page follows the name at the next deploy."""
    src = cfg([{"user": f"{M}Анна"}], [dashmerge.ADMINS])
    out = dashmerge.expand_users(dashmerge.expand_admins(copy.deepcopy(src), ["id-owner", "id-anna"]), PEOPLE)
    assert out["views"][1]["visible"] == [{"user": f"{M}Анна"}, {"user": "id-anna"}]
    assert out["views"][1]["sections"][0]["cards"][0]["visibility"][0]["users"] == ["id-owner", "id-anna"]
    merged, _ = plan_and_merge(copy.deepcopy(out), None, None)
    again, report = plan_and_merge(dashmerge.expand_users(dashmerge.expand_admins(copy.deepcopy(src), ["id-owner", "id-anna"]), PEOPLE), merged, copy.deepcopy(out))
    assert again == merged and not report["changes"]
    read = dashmerge.collapse_users(dashmerge.collapse_admins(dashmerge.redact(copy.deepcopy(merged), copy.deepcopy(merged), b"k" * 32), ["id-owner", "id-anna"]), PEOPLE)
    assert read["views"][1]["visible"] == [{"user": f"{M}Анна"}]
    assert read["views"][1]["sections"][0]["cards"][0]["visibility"][0]["users"] == [dashmerge.ADMINS]
    # «Анна» renamed to «Аня» in HA: the next deploy's expansion no longer has her id — the merge takes Hauflo's change.
    renamed = [("id-anna", "Аня")]
    nxt, report = plan_and_merge(dashmerge.expand_users(dashmerge.expand_admins(copy.deepcopy(src), ["id-owner", "id-anna"]), renamed), merged, copy.deepcopy(out))
    assert nxt["views"][1]["visible"] == [{"user": f"{M}Анна"}] and report["changes"]


async def test_the_house_people_by_name_active_and_not_hass_own(hass: HomeAssistant):
    eng = dashmerge.DashboardMerge(hass)
    ivan = await hass.auth.async_create_user("Иван")
    off = await hass.auth.async_create_user("Иван")
    await hass.auth.async_deactivate_user(off)
    await hass.auth.async_create_system_user("Иван")
    people = await eng._users()
    assert [i for i, n in people if n == "Иван"] == [ivan.id]
    out = await eng._expand(cfg([{"user": f"{M}Иван"}]))
    assert out["views"][1]["visible"] == [{"user": f"{M}Иван"}, {"user": ivan.id}]
