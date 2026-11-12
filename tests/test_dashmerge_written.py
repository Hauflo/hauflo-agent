"""Р33 (0.34 п.7): the panel the agent left in HA is no new edit of the integrator's — no copy of it per deploy, the
protection off once HA still shows exactly what was written; a real edit after it is one again."""

import copy
from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.hauflo import dashmerge
from custom_components.hauflo.dashmerge import ID


def _hauflo(n: int = 0):
    """Hauflo's panel; `n` — its version (a card of Hauflo's changed by the next deploy)."""
    return {"views": [{"title": "Главная", "path": "home", "type": "sections", ID: "v/home",
                       "sections": [{"type": "grid", ID: "v/home/s:a:", "cards": [{"type": "custom:hauflo-hero", "v": n, ID: "v/home/s:a:/hero:"}]}]}]}


HIS = {"type": "markdown", "content": "Котельная"}


async def _run(hass: HomeAssistant, m: dashmerge.DashboardMerge, ha: dict, n: int) -> dict:
    copies: list[str] = []

    class Dash:
        mode = "storage"
        config = {"title": "Дом"}

        async def async_load(self, force):
            return copy.deepcopy(ha["cfg"])

        async def async_save(self, cfg):
            ha["cfg"] = copy.deepcopy(cfg)

    async def local(cmds):
        if cmds[0]["type"] == "lovelace/dashboards/list":
            return [[]]
        copies.append(cmds[0]["type"])
        return [None]

    async def nothing(*a, **k):
        return None

    async def made(url_path, config, title, local_ws, lang=None, before_take=False):
        copies.append(url_path)
        return f"{url_path}-copy", 0

    with patch.object(m, "_dashboard", return_value=Dash()), patch.object(m.store, "async_save", nothing), patch.object(m.meta_store, "async_save", nothing), patch.object(m, "_copy", made):
        res = await m.save("hauflo-x", _hauflo(n), [], [], False, "Дом", local)
    return {**res, "copies": copies}


async def test_a_kept_edit_is_copied_once_not_per_deploy(hass: HomeAssistant) -> None:
    m = dashmerge.DashboardMerge(hass)
    m.bases["hauflo-x"] = _hauflo()
    cur = _hauflo()
    cur["views"][0]["sections"][0]["cards"].append(copy.deepcopy(HIS))
    # A change of Hauflo's of its own so the merge writes: his card kept beside it, a copy of his edit kept once.
    ha = {"cfg": cur}
    first = await _run(hass, m, ha, 1)
    assert first["copies"] == ["hauflo-x"] and first.get("copy_reason") == "edited"
    assert HIS in ha["cfg"]["views"][0]["sections"][0]["cards"]
    assert m.unchanged("hauflo-x", ha["cfg"])
    # The next deploy changes Hauflo's card again; HA still shows what the agent wrote — saved, no copy again, his card there.
    second = await _run(hass, m, ha, 2)
    assert second["saved"] is True and second["copies"] == [] and "copy" not in second
    assert HIS in ha["cfg"]["views"][0]["sections"][0]["cards"]


async def test_an_edit_after_the_save_is_an_edit_again(hass: HomeAssistant) -> None:
    m = dashmerge.DashboardMerge(hass)
    m.bases["hauflo-x"] = _hauflo()
    cur = _hauflo()
    cur["views"][0]["sections"][0]["cards"].append(copy.deepcopy(HIS))
    ha = {"cfg": cur}
    await _run(hass, m, ha, 1)
    ha["cfg"]["views"][0]["sections"][0]["cards"].append({"type": "markdown", "content": "Ещё"})
    assert not m.unchanged("hauflo-x", ha["cfg"])
    again = await _run(hass, m, ha, 2)
    assert again["copies"] == ["hauflo-x"]


async def test_nothing_written_still_remembers_what_ha_shows(hass: HomeAssistant) -> None:
    """A save that changes nothing in HA (an «ok» panel applied with no choice): HA as it is now is the agent's last word."""
    m = dashmerge.DashboardMerge(hass)
    m.bases["hauflo-x"] = _hauflo()
    ha = {"cfg": _hauflo()}
    res = await _run(hass, m, ha, 0)
    assert res["saved"] is False and m.unchanged("hauflo-x", ha["cfg"])


def test_the_digest_ignores_key_order():
    a = {"views": [{"a": 1, "b": 2}]}
    b = {"views": [{"b": 2, "a": 1}]}
    assert dashmerge._digest(a) == dashmerge._digest(b) and dashmerge._digest(a) != dashmerge._digest({"views": []})
