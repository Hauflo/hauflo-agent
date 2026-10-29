"""Who services this home (service review 29.10, D): what Hauflo sends is kept to known fields and shown to the owner
read-only in the integration's options, in the owner's language."""

from homeassistant.core import HomeAssistant

from custom_components.hauflo import service

INFO = {
    "company": "Сервис-Дом",
    "contact": {"phone": "+7 900 000-00-02", "name": "Сервис-Дом"},
    "since": "2026-10-29T10:00:00.000Z",
    "history": [
        {"at": "2026-10-29T10:00:00.000Z", "kind": "owner", "from": "Volkov KNX", "to": "Сервис-Дом"},
        {"at": "2026-10-20T10:00:00.000Z", "kind": "support", "mode": "edit", "until": "2026-10-27T10:00:00.000Z", "ended": "2026-10-21T10:00:00.000Z"},
        {"at": "x", "kind": "root", "from": "<script>"},
        "junk",
    ],
}


def test_clean_keeps_known_fields_only() -> None:
    c = service.clean(INFO)
    assert c["company"] == "Сервис-Дом" and c["contact"] == {"phone": "+7 900 000-00-02", "name": "Сервис-Дом"}
    assert [h["kind"] for h in c["history"]] == ["owner", "support"]
    assert service.clean({"company": ""}) is None
    assert service.clean("x") is None
    many = service.clean({"company": "A" * 500, "contact": {"phone": 5}, "history": [{"kind": "company", "at": "2026-10-29"}] * 50})
    assert many == {"company": "A" * 120, "contact": None, "since": None, "history": [{"kind": "company", "at": "2026-10-29"}] * 20}


def test_describe_in_the_owners_language() -> None:
    who, hist = service.describe(service.clean(INFO), "ru")
    assert who == "«Сервис-Дом» — тел. +7 900 000-00-02"
    assert "29.10.2026 — дом передан от «Volkov KNX» компании «Сервис-Дом» (подтвердил владелец в Home Assistant)" in hist
    assert "поддержка Hauflo: правка проекта до 27.10.2026 (закрыт 21.10.2026)" in hist
    who_en, hist_en = service.describe(service.clean(INFO), "en-GB")
    assert "approved by the owner in Home Assistant" in hist_en and who_en.startswith("«Сервис-Дом»")
    assert service.describe(None, "en")[0].startswith("no data yet")
    # The company's e-mail (Org.contactEmail) with or without a phone.
    mail_only = service.clean({"company": "Сервис-Дом", "contact": {"email": "duty@servicedom.ru", "name": "Сервис-Дом"}})
    assert service.describe(mail_only, "ru")[0] == "«Сервис-Дом» — duty@servicedom.ru"
    both = service.clean({"company": "A", "contact": {"phone": "+7 1", "email": "a@b.c", "name": "Дежурный"}})
    assert service.describe(both, "en")[0] == "«A» — Дежурный, phone +7 1, a@b.c"
    assert service.describe({"company": "A", "contact": None, "history": []}, "ru")[1] == "передач и доступа поддержки не было"


async def test_saved_and_read_back(hass: HomeAssistant, hass_storage) -> None:
    assert await service.async_save(hass, INFO)
    assert hass_storage[service.STORE_KEY]["data"]["company"] == "Сервис-Дом"
    assert (await service.async_load(hass))["history"][0]["kind"] == "owner"
    assert not await service.async_save(hass, {"nope": 1})
    await service.async_remove(hass)
    assert await service.async_load(hass) is None
