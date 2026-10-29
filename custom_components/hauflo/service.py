"""Who services this home — for the house owner, read only (service review 29.10: «the client has no role or consent in
access to their house»). Hauflo sends it over the link (frame «service»): the company, its contact, the access history
(transfers between companies, Hauflo support let in). Kept in `.storage/hauflo.service`; shown in the integration's
options (Settings → Devices & services → Hauflo → Configure) and in diagnostics. Nothing here changes anything."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant

from .storage import HaufloStore

STORE_KEY = "hauflo.service"
_KINDS = ("company", "owner", "support")


def _s(v: Any, n: int) -> str | None:
    return v[:n] if isinstance(v, str) and v else None


def clean(info: Any) -> dict[str, Any] | None:
    """The frame's info kept to the known fields, types and sizes (a bad frame never lands in the store)."""
    if not isinstance(info, dict) or not _s(info.get("company"), 120):
        return None
    contact = info.get("contact") if isinstance(info.get("contact"), dict) else {}
    phone = _s(contact.get("phone"), 40)
    email = _s(contact.get("email"), 120)
    name = _s(contact.get("name"), 60)
    history = []
    for h in info.get("history") if isinstance(info.get("history"), list) else []:
        if not isinstance(h, dict) or h.get("kind") not in _KINDS or not _s(h.get("at"), 40):
            continue
        item = {"kind": h["kind"], "at": h["at"][:40]}
        for k, n in (("from", 120), ("to", 120), ("until", 40), ("ended", 40)):
            if v := _s(h.get(k), n):
                item[k] = v
        if h.get("mode") in ("view", "edit"):
            item["mode"] = h["mode"]
        history.append(item)
        if len(history) >= 20:
            break
    return {
        "company": info["company"][:120],
        "contact": {**({"phone": phone} if phone else {}), **({"email": email} if email else {}), **({"name": name} if name else {})} if phone or email else None,
        "since": _s(info.get("since"), 40),
        "history": history,
    }


async def async_save(hass: HomeAssistant, info: Any) -> bool:
    data = clean(info)
    if data is None:
        return False
    await HaufloStore(hass, STORE_KEY).async_save(data)
    return True


async def async_load(hass: HomeAssistant) -> dict[str, Any] | None:
    data = await HaufloStore(hass, STORE_KEY).async_load()
    return clean(data) if data else None


async def async_remove(hass: HomeAssistant) -> None:
    await HaufloStore(hass, STORE_KEY).async_remove()


def _date(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except ValueError:
        return str(iso or "")[:10]


_T = {
    "ru": {
        "none": "нет данных — Hauflo пришлёт их при следующем подключении",
        "no_history": "передач и доступа поддержки не было",
        "phone": "тел.",
        "company": "{at} — дом передан от «{frm}» компании «{to}» (передала прежняя компания)",
        "owner": "{at} — дом передан от «{frm}» компании «{to}» (подтвердил владелец в Home Assistant)",
        "support_view": "{at} — поддержка Hauflo: просмотр проекта до {until}",
        "support_edit": "{at} — поддержка Hauflo: правка проекта до {until}",
        "ended": " (закрыт {ended})",
    },
    "en": {
        "none": "no data yet — Hauflo sends it on the next connection",
        "no_history": "no transfers and no support access",
        "phone": "phone",
        "company": "{at} — the home moved from «{frm}» to «{to}» (handed over by the previous company)",
        "owner": "{at} — the home moved from «{frm}» to «{to}» (approved by the owner in Home Assistant)",
        "support_view": "{at} — Hauflo support: project view until {until}",
        "support_edit": "{at} — Hauflo support: project editing until {until}",
        "ended": " (closed {ended})",
    },
}


def describe(info: dict[str, Any] | None, language: str | None) -> tuple[str, str]:
    """(who services the home, the access history as markdown lines) in the HA user's language."""
    t = _T["ru" if str(language or "").lower().startswith("ru") else "en"]
    if not info:
        return t["none"], t["no_history"]
    who = f"«{info['company']}»"
    if c := info.get("contact"):
        parts = [f"{t['phone']} {c['phone']}"] if c.get("phone") else []
        if c.get("email"):
            parts.append(c["email"])
        named = f"{c['name']}, " if c.get("name") and c["name"] != info["company"] else ""
        who += f" — {named}{', '.join(parts)}"
    lines = []
    for h in info.get("history") or []:
        at = _date(h.get("at"))
        if h["kind"] == "support":
            line = t["support_edit" if h.get("mode") == "edit" else "support_view"].format(at=at, until=_date(h.get("until")))
            if h.get("ended"):
                line += t["ended"].format(ended=_date(h["ended"]))
        else:
            line = t[h["kind"]].format(at=at, frm=h.get("from", "—"), to=h.get("to", "—"))
        lines.append(f"- {line}")
    return who, ("\n" + "\n".join(lines)) if lines else t["no_history"]
