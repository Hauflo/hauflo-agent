"""«Заменить на Hauflo» (stage 5): a stock Home Assistant card of any integration → the Hauflo card for it, by ONE table
(www/stock.json, the cards read the same file). Only on the integrator's action: the admin's «Настройки» sheet of the
cards (GET/POST /api/hauflo/stock, admins only, never through the tunnel) or his ticks in the deploy plan (dashmerge.py
`plan` → `stock`, `save` with `stock` ids). Never Hauflo's own items (a `hauflo_id`: Hauflo decides their look) and never
silently: an offer is only an offer.

Each replaced card keeps what it was (`hauflo_was`): «Вернуть штатную» puts it back as it was. A device's cards in one
place (≥ 2 of one device_id) can become one «Устройство целиком» card (hauflo-area with `device`). Guarded entities
(locks, alarm panels, valves, garage / gate / door covers — protect.py / massSafety) never go into a console with an «all
at once» action, and an offer with one is never pre-ticked.
"""

from __future__ import annotations

import copy
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant

WAS = "hauflo_was"
HID = "hauflo_id"
Info = Callable[[str], "dict[str, Any] | None"]


@lru_cache(maxsize=1)
def table() -> dict[str, Any]:
    return json.loads((Path(__file__).parent / "www" / "stock.json").read_text(encoding="utf-8"))


def _ents(card: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if isinstance(card.get("entity"), str):
        out.append(card["entity"])
    for x in card.get("entities") or [] if isinstance(card.get("entities"), list) else []:
        e = x if isinstance(x, str) else x.get("entity") if isinstance(x, dict) else None
        if isinstance(e, str) and e not in out:
            out.append(e)
    return out


def _dom(eid: str) -> str:
    return eid.split(".", 1)[0]


def guarded(i: dict[str, Any]) -> bool:
    g = table()["guarded"]
    return i["domain"] in g["domains"] or (i["domain"] == "cover" and i.get("device_class") in g["cover_classes"])


def _speeds(i: dict[str, Any]) -> int:
    """A fan's speeds (owner 03.11, GTC: 1…10): HA's speed_count, else 100 / percentage_step; 0 = stepless."""
    n = i.get("speed_count")
    if not isinstance(n, (int, float)) or n < 1:
        step = i.get("percentage_step")
        n = round(100 / step) if isinstance(step, (int, float)) and step > 0 else 0
    n = int(n)
    return n if 1 < n <= table()["fan_speeds_max"] else 0


def _row(eid: str, i: dict[str, Any]) -> Any:
    n = _speeds(i) if i["domain"] == "fan" else 0
    return {"entity": eid, "scale": "speeds", "speeds": n} if n else eid


def _numeric(i: dict[str, Any]) -> bool:
    try:
        float(i.get("state"))
    except (TypeError, ValueError):
        return False
    return True


def _make(to: str, key: str, ents: list[str], infos: dict[str, dict[str, Any]], src: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"type": f"custom:{to}"}
    if key == "entity":
        out["entity"] = ents[0]
    elif key == "area":
        out["area"] = src["area"]
    else:
        out[key] = [_row(e, infos[e]) if key == "entities" else e for e in ents]
    name_key = table()["names"].get(to)
    for k in ("title", "name"):
        if name_key and isinstance(src.get(k), str) and src[k].strip():
            out[name_key] = src[k].strip()[:80]
            break
    return out


def offer_of(card: Any, info: Info) -> dict[str, Any] | None:
    """What one stock card becomes: {card, from, to, entities, guarded} or None (ours, Hauflo's, unknown, unsure)."""
    if not isinstance(card, dict) or HID in card or WAS in card:
        return None
    t = card.get("type")
    if not isinstance(t, str) or t.startswith("custom:"):
        return None
    tb = table()
    if t == "area":
        return {"card": _make("hauflo-area", "area", [], {}, card), "from": t, "to": "hauflo-area", "entities": 0, "guarded": False} if isinstance(card.get("area"), str) else None
    ents = _ents(card)
    if not ents or len(ents) > tb["max_entities"]:
        return None
    infos = {e: info(e) for e in ents}
    if any(v is None for v in infos.values()):
        return None  # an entity HA doesn't have: nothing to show it by
    infos_ok: dict[str, dict[str, Any]] = {k: v for k, v in infos.items() if v is not None}
    guard = any(guarded(i) for i in infos_ok.values())
    to: str | None = None
    key = "entities"
    if t in tb["types"]:
        spec = tb["types"][t]
        if spec.get("domains") and any(i["domain"] not in spec["domains"] for i in infos_ok.values()):
            return None
        to, key = spec["card"], spec["key"]
        if to == "hauflo-graph" and not all(_numeric(i) for i in infos_ok.values()):
            return None
        if key == "entity":
            ents = ents[:1]
    elif t in tb["generic"]:
        if len(ents) == 1:
            m = tb["one"].get(infos_ok[ents[0]]["domain"]) or tb["one"]["*"]
            to, key = m["card"], m["key"]
        else:
            doms = {i["domain"] for i in infos_ok.values()}
            for k, m in tb["many"].items():
                if not guard and doms <= set(tb["groups"].get(k, [k])) and not (m["card"] == "hauflo-graph" and not all(_numeric(i) for i in infos_ok.values())):
                    to, key = m["card"], m["key"]
                    break
            if to is None:
                devs = {i.get("device_id") for i in infos_ok.values()}
                if len(devs) == 1 and None not in devs:
                    return {"card": {"type": f"custom:{tb['device']}", "device": devs.pop(), **({"title": card["title"].strip()[:80]} if isinstance(card.get("title"), str) and card["title"].strip() else {})}, "from": t, "to": tb["device"], "entities": len(ents), "guarded": guard, "device": True}
                to, key = tb["mixed"], "entities"
    if to is None:
        return None
    return {"card": _make(to, key, ents, infos_ok, card), "from": t, "to": to, "entities": len(ents), "guarded": guard}


def _cid(path: str, card: Any) -> str:
    return hashlib.sha1(f"{path}\x00{json.dumps(card, sort_keys=True, ensure_ascii=False, default=str)}".encode()).hexdigest()[:16]


def _title(node: dict[str, Any]) -> str | None:
    for k in ("title", "heading", "name"):
        if isinstance(node.get(k), str) and node[k].strip():
            return node[k].strip()[:80]
    return None


def _label(card: Any, info: Info | None = None) -> str | None:
    """What the integrator calls this card: its title / name, else its first entity's name (a label, never content)."""
    if isinstance(card, list):
        card = card[0] if card else None
    if not isinstance(card, dict):
        return None
    own = _title(card) or (card.get("label").strip()[:80] if isinstance(card.get("label"), str) and card["label"].strip() else None)
    if own or info is None:
        return own
    ents = _ents(card)
    i = info(ents[0]) if ents else None
    return str(i["name"])[:80] if i and i.get("name") else None


def _where(view: str | None, cards: list[Any]) -> str | None:
    """The view's title, and a section's heading when it has one («Дом · Гостиная»)."""
    head = cards[0].get("heading") if cards and isinstance(cards[0], dict) and cards[0].get("type") == "heading" else None
    head = head.strip()[:60] if isinstance(head, str) and head.strip() else None
    return " · ".join(x for x in (view, head) if x) or None


def _holders(cfg: Any) -> list[tuple[str, list[Any], str | None]]:
    """Every list of cards in the tree (views → sections → cards, or views → cards): (path, the list, where it is)."""
    out: list[tuple[str, list[Any], str | None]] = []
    views = cfg.get("views") if isinstance(cfg, dict) else None
    for vi, v in enumerate(views if isinstance(views, list) else []):
        if not isinstance(v, dict):
            continue
        vt = _title(v)
        if isinstance(v.get("cards"), list):
            out.append((f"v{vi}", v["cards"], vt))
        for si, s in enumerate(v.get("sections") if isinstance(v.get("sections"), list) else []):
            if isinstance(s, dict) and isinstance(s.get("cards"), list):
                out.append((f"v{vi}/s{si}", s["cards"], _where(vt, s["cards"])))
    return out


def _plan(cfg: Any, info: Info) -> list[dict[str, Any]]:
    """All offers of a config, each with where it is (`at`: the holder, the card indexes it replaces)."""
    out: list[dict[str, Any]] = []
    for path, cards, vt in _holders(cfg):
        # «Устройство целиком»: ≥ 2 single cards of one device side by side → one card of the whole device (in their
        # place; «Вернуть штатную» puts them back exactly so).
        runs: list[tuple[str, list[int]]] = []
        for ci, c in enumerate(cards):
            o = offer_of(c, info)
            ents = _ents(c) if o is not None else []
            d = (info(ents[0]) or {}).get("device_id") if len(ents) == 1 and c.get("type") in table()["generic"] else None
            if d and runs and runs[-1][0] == d and runs[-1][1][-1] == ci - 1:
                runs[-1][1].append(ci)
            elif d:
                runs.append((d, [ci]))
            if o is not None:
                out.append({**o, "id": _cid(f"{path}/c{ci}", c), "at": (path, [ci]), "where": vt, "title": _label(c, info)})
        for dev, idx in runs:
            if len(idx) < 2:
                continue
            guard = any(guarded(info(e) or {"domain": ""}) for i in idx for e in _ents(cards[i]))
            out.append({"card": {"type": f"custom:{table()['device']}", "device": dev}, "from": cards[idx[0]].get("type"), "to": table()["device"], "entities": len(idx), "guarded": guard, "device": True, "id": _cid(f"{path}/d:{dev}", [cards[i] for i in idx]), "at": (path, idx), "where": vt, "title": info(_ents(cards[idx[0]])[0]).get("device_name") or _label(cards[idx[0]], info)})
    return out


def offers(cfg: Any, info: Info) -> list[dict[str, Any]]:
    """What the integrator is offered: id, from → to, how many entities, where; `pick` — pre-ticked (never a guarded one)."""
    return [{"id": o["id"], "from": o["from"], "to": o["to"], "entities": o["entities"], "device": bool(o.get("device")), "guarded": o["guarded"], "pick": not o["guarded"], **({"where": o["where"]} if o["where"] else {}), **({"title": o["title"]} if o.get("title") else {})} for o in _plan(cfg, info)]


def replaced(cfg: Any, info: Info | None = None) -> list[dict[str, Any]]:
    """Cards replaced earlier (they keep `hauflo_was`): «Вернуть штатную»."""
    out: list[dict[str, Any]] = []
    for path, cards, vt in _holders(cfg):
        for ci, c in enumerate(cards):
            if isinstance(c, dict) and WAS in c and isinstance(c.get("type"), str):
                was = c[WAS]
                title = _label(c) or _label(was, info)
                out.append({"id": _cid(f"{path}/c{ci}", c), "to": c["type"].removeprefix("custom:"), "from": (was[0] if isinstance(was, list) and was else was).get("type") if isinstance(was, (dict, list)) else None, **({"where": vt} if vt else {}), **({"title": title} if title else {})})
    return out


def apply(cfg: Any, ids: Any, info: Info) -> tuple[Any, int]:
    """The config with the chosen offers made (a device's offer first: its cards then are taken), and how many."""
    want = {x for x in ids if isinstance(x, str)} if isinstance(ids, list) else set()
    if not want:
        return cfg, 0
    plan = [o for o in _plan(cfg, info) if o["id"] in want]
    plan.sort(key=lambda o: not o.get("device"))
    new = copy.deepcopy(cfg)
    holders = {p: cards for p, cards, _ in _holders(new)}
    taken: dict[str, set[int]] = {}
    drop: dict[str, set[int]] = {}
    n = 0
    for o in plan:
        path, idx = o["at"]
        used = taken.setdefault(path, set())
        if used & set(idx):
            continue
        cards = holders[path]
        was = [cards[i] for i in idx]
        cards[idx[0]] = {**o["card"], WAS: was if len(was) > 1 else was[0]}
        drop.setdefault(path, set()).update(idx[1:])
        used.update(idx)
        n += 1
    for path, gone in drop.items():
        holders[path][:] = [c for i, c in enumerate(holders[path]) if i not in gone]
    return new, n


def undo(cfg: Any, ids: Any) -> tuple[Any, int]:
    """The chosen replaced cards back as they were (a whole device's card: all its cards, in its place)."""
    want = {x for x in ids if isinstance(x, str)} if isinstance(ids, list) else set()
    if not want:
        return cfg, 0
    new = copy.deepcopy(cfg)
    n = 0
    for path, cards, _ in _holders(new):
        out: list[Any] = []
        for ci, c in enumerate(cards):
            if isinstance(c, dict) and WAS in c and _cid(f"{path}/c{ci}", c) in want:
                was = c[WAS]
                back = [x for x in (was if isinstance(was, list) else [was]) if isinstance(x, dict)]
                if back:
                    out.extend(back)
                    n += 1
                    continue
            out.append(c)
        cards[:] = out
    return new, n


def hass_info(hass: HomeAssistant) -> Info:
    from homeassistant.helpers import device_registry as dr, entity_registry as er  # noqa: PLC0415

    reg = er.async_get(hass)
    devs = dr.async_get(hass)

    def info(eid: str) -> dict[str, Any] | None:
        s = hass.states.get(eid)
        if s is None:
            return None
        e = reg.async_get(eid)
        a = s.attributes
        d = devs.async_get(e.device_id) if e and e.device_id else None
        return {"domain": _dom(eid), "device_class": a.get("device_class"), "device_id": e.device_id if e else None, "state": s.state, "percentage_step": a.get("percentage_step"), "speed_count": a.get("speed_count"), "name": a.get("friendly_name"), "device_name": (d.name_by_user or d.name) if d else None}

    return info


def _dashboard(hass: HomeAssistant, url_path: Any) -> Any:
    from homeassistant.components.lovelace.const import LOVELACE_DATA  # noqa: PLC0415

    key = None if url_path in (None, "", "lovelace") else url_path
    if key is not None and not isinstance(key, str):
        return None
    dash = hass.data[LOVELACE_DATA].dashboards.get(key)
    return dash if dash is not None and getattr(dash, "mode", "storage") == "storage" else None


class StockView(HomeAssistantView):
    """GET ?url_path=… — the offers and the replaced cards of a dashboard; POST {url_path, replace: [ids]} or
    {url_path, undo: [ids]} — made, the new lists back. Admins only (the integrator), never through the tunnel."""

    url = "/api/hauflo/stock"
    name = "api:hauflo:stock"
    requires_auth = True

    async def _load(self, request: Any, url_path: Any) -> tuple[Any, Any]:
        from homeassistant.components.lovelace.const import ConfigNotFound  # noqa: PLC0415

        dash = _dashboard(request.app[KEY_HASS], url_path)
        if dash is None:
            return None, None
        try:
            return dash, await dash.async_load(False)
        except ConfigNotFound:
            return dash, None

    def _answer(self, hass: HomeAssistant, cfg: Any, **extra: Any) -> Any:
        info = hass_info(hass)
        return self.json({"offers": offers(cfg, info) if cfg else [], "replaced": replaced(cfg, info) if cfg else [], **extra})

    async def get(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admins only", 403)
        dash, cfg = await self._load(request, request.query.get("url_path"))
        if dash is None:
            return self.json_message("no such storage dashboard", 404)
        return self._answer(request.app[KEY_HASS], cfg)

    async def post(self, request):
        if not request["hass_user"].is_admin:
            return self.json_message("admins only", 403)
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("invalid JSON", 400)
        if not isinstance(body, dict):
            return self.json_message("an object expected", 400)
        hass = request.app[KEY_HASS]
        dash, cfg = await self._load(request, body.get("url_path"))
        if dash is None or cfg is None:
            return self.json_message("no such storage dashboard", 404)
        if isinstance(body.get("undo"), list):
            new, n = undo(cfg, body["undo"][:200])
        else:
            new, n = apply(cfg, (body.get("replace") or [])[:200] if isinstance(body.get("replace"), list) else [], hass_info(hass))
        if n:
            await dash.async_save(new)
        return self._answer(hass, new, done=n)
