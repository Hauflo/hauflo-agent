"""Hauflo agent setup: the integrator types the pairing code from Hauflo (project → «Агент Hauflo»)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigEntryState, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.components import persistent_notification
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import BooleanSelector, SelectSelector, SelectSelectorConfig, SelectSelectorMode, TextSelector, TextSelectorConfig

from . import relinked as _relinked, service
from . import ranges as ranges_mod
from .const import CONF_INDEPENDENT, CONF_ACCEPT_PENDING, CONF_ALLOW_CHANGES, CONF_CODE, CONF_CONFIRM_TAKEOVER, CONF_RANGES, CONF_SECRET, CONF_SERVER, DATA_PENDING_RANGES, DATA_UNLINKED, DEFAULT_SERVER, DOMAIN, FALLBACK_SERVER, REGION_SERVERS
from .ranges import parse_ranges
from .ranges import range_key as _range_key


def _secure(server: str) -> bool:
    import ipaddress  # noqa: PLC0415
    from urllib.parse import urlparse  # noqa: PLC0415

    u = urlparse(server)
    if u.scheme == "https":
        return True
    if u.scheme != "http" or not u.hostname:
        return False
    host = u.hostname
    try:
        return ipaddress.ip_address(host).is_private  # a public address (IPv6 too) only by https
    except ValueError:
        return host == "localhost" or "." not in host  # a name in the house's network (dev: «hauflo-backend»)



def _pair_code(typed: str) -> str:
    """«73s xey w4d», «73S XEY-W4D», «ru 73s xey w4d» → «73S-XEY-W4D» / «RU-73S-XEY-W4D» (integrator run 25: spaces
    inside were refused as a wrong code)."""
    chars = "".join(ch for ch in typed.upper() if ch.isascii() and ch.isalnum())
    if len(chars) == 9:
        return f"{chars[:3]}-{chars[3:6]}-{chars[6:]}"
    if len(chars) == 11 and chars[:2].isalpha():
        return f"{chars[:2]}-{chars[2:5]}-{chars[5:8]}-{chars[8:]}"
    return typed.strip().upper()

class HaufloConfigFlow(ConfigFlow, domain=DOMAIN):
    """One Hauflo agent per Home Assistant."""

    VERSION = 1
    # The server said this HA is linked to another company's house: the next form asks for an explicit «yes».
    _takeover = False
    _typed_code = ""
    # The server asks the house owner whether a company takes the home over (service review 29.10, B): the company's
    # name for the question, and what to send again on «yes» (server, code, old secret).
    _transfer_company = ""
    _transfer: tuple[str, str, str | None] | None = None

    @staticmethod
    def _server_for(code: str, typed: str) -> str:
        """The server field left as it was: the code's region decides (owner 25.10: only the code to type)."""
        region = code.split("-", 1)[0]
        # Any of Hauflo's own servers counts as «as it was» — a re-link with an RU code from an AE entry goes to RU.
        if typed in ("", DEFAULT_SERVER, *REGION_SERVERS.values()) and region in REGION_SERVERS:
            return REGION_SERVERS[region]
        return typed

    @staticmethod
    def _plan_b(code: str, server: str, errors: dict[str, str]) -> bool:
        """Only Hauflo's international server, only a code without a known region, only «wrong code» or «no
        connection» — a typed own server or a regional code never goes anywhere else."""
        return server == DEFAULT_SERVER and code.split("-", 1)[0] not in REGION_SERVERS and bool(errors) and CONF_SERVER not in errors

    async def _pair(self, server: str, code: str, previous: str | None = None, confirm: bool = False, transfer: bool = False) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Trade a pairing code for the agent secret. Status first, body second (HA core review 25.10: a 404 page
        in HTML read as «no connection» instead of «wrong code»)."""
        if not _secure(server):
            return None, {CONF_SERVER: "https_required"}
        try:
            session = async_get_clientsession(self.hass)
            payload = {"code": code, **({"previous": previous} if previous else {}), **({"confirmTakeover": True} if confirm else {}), **({"confirmTransfer": True} if transfer else {})}
            async with session.post(f"{server}/api/agent/pair", json=payload, timeout=aiohttp.ClientTimeout(total=20)) as res:
                if res.status == 404:
                    return None, {CONF_CODE: "invalid_code"}
                if res.status == 409:
                    try:
                        why = await res.json(content_type=None)
                    except ValueError:
                        why = None
                    if isinstance(why, dict) and why.get("code") == "pair_transfer":
                        self._transfer_company = str(why.get("company") or "")[:120]
                        return None, {"base": "transfer_ask"}
                    return None, {"base": "takeover_confirm"}
                if res.status >= 400:
                    return None, {"base": "cannot_connect"}
                body = await res.json(content_type=None)
                if not isinstance(body, dict) or not body.get("secret"):
                    return None, {"base": "cannot_connect"}
                return body, {}
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return None, {"base": "cannot_connect"}

    def _schema(self, server: str) -> vol.Schema:
        extra: dict[Any, Any] = {}
        if self._takeover:
            # The explicit «yes», off by default: this Home Assistant leaves the other account's house.
            extra[vol.Optional(CONF_CONFIRM_TAKEOVER, default=False)] = bool
        # The server folded away under «Дополнительно»: the region in the code picks it (security re-review round 4:
        # «type this address» is how someone else's server would get an admin tunnel into the house).
        return vol.Schema(
            {
                vol.Required(CONF_CODE, **({"default": self._typed_code} if self._typed_code else {})): str,
                **extra,
                vol.Optional("advanced"): section(vol.Schema({vol.Required(CONF_SERVER, default=server): str}), {"collapsed": True}),
            }
        )

    async def _code_step(self, step_id: str, user_input: dict[str, Any] | None, server: str, entry: Any = None) -> tuple[dict[str, Any] | None, str, dict[str, str]]:
        errors: dict[str, str] = {}
        if user_input is not None:
            code = _pair_code(user_input[CONF_CODE])
            confirm = user_input.get(CONF_CONFIRM_TAKEOVER) is True
            self._typed_code = user_input[CONF_CODE]
            typed = (user_input.get("advanced") or {}).get(CONF_SERVER, server)
            server = self._server_for(code, str(typed).strip().rstrip("/"))
            # The old secret only to the server that issued it: there the old project lets this agent go.
            previous = entry.data.get(CONF_SECRET) if entry is not None and entry.data.get(CONF_SERVER) == server else None
            body, errors = await self._pair(server, code, previous, confirm)
            self._takeover = errors.get("base") == "takeover_confirm"
            if errors.get("base") == "transfer_ask":
                self._transfer = (server, code, previous)
            if body is None and self._plan_b(code, server, errors):
                # A code without a region: an older Russian code, or the international server blocked — try Russia.
                prev_b = entry.data.get(CONF_SECRET) if entry is not None and entry.data.get(CONF_SERVER) == FALLBACK_SERVER else None
                body_b, errors_b = await self._pair(FALLBACK_SERVER, code, prev_b, confirm)
                if body_b is not None:
                    return body_b, FALLBACK_SERVER, {}
                if errors_b.get(CONF_CODE) or errors_b.get("base") in ("takeover_confirm", "transfer_ask"):
                    errors = errors_b
                    self._takeover = errors.get("base") == "takeover_confirm"
                    if errors.get("base") == "transfer_ask":
                        self._transfer = (FALLBACK_SERVER, code, prev_b)
            if body is not None:
                return body, server, {}
        return None, server, errors

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # One agent per HA: manifest single_config_entry; re-linking goes through reauth / reconfigure (HA review round 4).
        body, server, errors = await self._code_step("user", user_input, DEFAULT_SERVER)
        if body is not None:
            self._paired = (body, server)
            return await self.async_step_ranges()
        return self.async_show_form(step_id="user", data_schema=self._schema(server), errors=errors)

    async def async_step_ranges(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Which group addresses Hauflo may send to — confirmed here, in the house, not by the cloud (narrow round
        25.10, acceptance B9: an address no HA entity uses — a gate actuator on the bus — was writable)."""
        body, server = self._paired
        # The addresses Hauflo's entities write, as spans (KNX review 27.10); an older Hauflo sends only main/middle groups.
        proposed = ", ".join(parse_ranges(body.get("addresses") or body.get("ranges")))
        if user_input is not None:
            ranges = parse_ranges(user_input.get(CONF_RANGES, ""))
            return self.async_create_entry(title=f"Hauflo — {body.get('project', '')}", data={CONF_SERVER: server, CONF_SECRET: body["secret"]}, options={CONF_RANGES: ranges})
        return self.async_show_form(
            step_id="ranges",
            data_schema=vol.Schema({vol.Optional(CONF_RANGES, default=proposed): TextSelector(TextSelectorConfig(multiline=True))}),
            description_placeholders={"project": str(body.get("project", "")), "ranges": proposed or "—"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HaufloOptionsFlow()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Linked to another project, or again after an unlink: a new code updates this entry (its HA user stays)."""
        entry = self._get_reconfigure_entry()
        body, server, errors = await self._code_step("reconfigure", user_input, entry.data.get(CONF_SERVER, DEFAULT_SERVER), entry)
        if body is not None:
            return await self._relinked(entry, body, server)
        if errors.get("base") == "transfer_ask":
            return await self.async_step_transfer()
        return self.async_show_form(step_id="reconfigure", data_schema=self._schema(server), errors=errors)

    async def _relinked(self, entry: ConfigEntry, body: dict[str, Any], server: str) -> ConfigFlowResult:
        """Linked to a project with a new code: its addresses are asked for here, the old project's never kept
        silently (integrator review 29.10: a takeover kept them, the plan said «ошибка: 23»). An older Hauflo that
        sends none — the allowed list stays as it was."""
        self._paired = (body, server)
        self._entry = entry
        if not parse_ranges(body.get("addresses") or body.get("ranges")):
            return self._finish_relink(None)
        return await self.async_step_relink_ranges()

    def _finish_relink(self, allowed: list[str] | None) -> ConfigFlowResult:
        body, server = self._paired
        self.hass.data.pop(DATA_UNLINKED, None)
        if allowed is not None:
            ranges_mod.reset(self.hass)
        entry = self._entry
        # Updated here, reloaded by the entry's update listener when the link changed (HA 2026.12: no
        # async_update_reload_and_abort next to an update listener). A link the running agent already has, or an entry
        # that isn't running (no listener), is reloaded from here — every re-link starts the agent afresh, as before.
        result = self.async_update_and_abort(
            entry,
            title=f"Hauflo — {body.get('project', '')}",
            data_updates={CONF_SERVER: server, CONF_SECRET: body["secret"]},
            **({"options": {**entry.options, CONF_RANGES: allowed}} if allowed is not None else {}),
        )
        agent = getattr(entry, "runtime_data", None) if entry.state is ConfigEntryState.LOADED else None
        if agent is None or not entry.update_listeners or not _relinked(agent, entry.data):
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return result

    async def async_step_relink_ranges(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The new project's addresses, prefilled, and what goes of the old allowed list — said, never kept silently."""
        body, _server = self._paired
        proposed = parse_ranges(body.get("addresses") or body.get("ranges"))
        if user_input is not None:
            return self._finish_relink(parse_ranges(user_input.get(CONF_RANGES, "")))
        old = [str(r) for r in self._entry.options.get(CONF_RANGES) or []]
        want = ranges_mod.expand(proposed)
        removed = [r for r in parse_ranges(old) if not (ranges_mod.expand([r]) & want)]
        text = ", ".join(proposed)
        return self.async_show_form(
            step_id="relink_ranges",
            data_schema=vol.Schema({vol.Optional(CONF_RANGES, default=text): TextSelector(TextSelectorConfig(multiline=True))}),
            description_placeholders={"project": str(body.get("project", "")), "ranges": text or "—", "removed": ", ".join(removed) or "—"},
        )

    async def async_step_transfer(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The plain question to the house owner (Loxone / Control4 / Crestron: the end customer approves a dealer
        change): this company gets a copy of the home's project, the previous company loses access. Only this «yes»,
        sent with the old agent secret, lets Hauflo make the copy — the company holding the code alone can't. Order:
        «move from another account» → this question → the new project's addresses (relink_ranges)."""
        return self.async_show_menu(step_id="transfer", menu_options=["transfer_yes", "transfer_no"], description_placeholders={"company": self._transfer_company or "—"})

    async def async_step_transfer_yes(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        reauth = self.source == SOURCE_REAUTH
        entry = self._get_reauth_entry() if reauth else self._get_reconfigure_entry()
        if self._transfer is None:
            return self.async_abort(reason="transfer_declined")
        server, code, previous = self._transfer
        body, errors = await self._pair(server, code, previous, confirm=True, transfer=True)
        if body is not None:
            return await self._relinked(entry, body, server)
        # The code expired meanwhile, or no connection: back to the code form, nothing changed.
        self._takeover = False
        return self.async_show_form(step_id="reauth_confirm" if reauth else "reconfigure", data_schema=self._schema(server), errors=errors or {"base": "cannot_connect"})

    async def async_step_transfer_no(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_abort(reason="transfer_declined")

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Hauflo no longer knows this agent (unlinked, project deleted): HA's own «re-configure» with a new code."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        body, server, errors = await self._code_step("reauth_confirm", user_input, entry.data.get(CONF_SERVER, DEFAULT_SERVER), entry)
        if body is not None:
            return await self._relinked(entry, body, server)
        if errors.get("base") == "transfer_ask":
            return await self.async_step_transfer()
        return self.async_show_form(step_id="reauth_confirm", data_schema=self._schema(server), errors=errors)


class HaufloOptionsFlow(OptionsFlow):
    """The owner's switches for Hauflo in this house — changed only here, in HA (the tunnel can't reach this flow):
    whether Hauflo may change the setup at all, and the group addresses it may send to. No secrets, no notification
    settings — those live in Hauflo (owner 28.10)."""

    _shown_pending: list[str] | None = None
    _saved: dict[str, Any] | None = None
    _report: str = ""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            # What Hauflo asked for is allowed only ticked one by one (security review 28.10: prefilled into the allowed
            # list, a Submit for the owner lock granted the gate's 7/7/1); the rest of what was shown is declined.
            shown_pending = self._shown_pending or []
            ticked = user_input.get(CONF_ACCEPT_PENDING) or []
            accepted = [r for r in ticked if r in shown_pending] if isinstance(ticked, list) else []
            ranges = parse_ranges(user_input.get(CONF_RANGES, ""))
            pending = self.hass.data.get(DATA_PENDING_RANGES)
            if isinstance(pending, set):
                pending -= set(shown_pending)
                ranges_mod.sync(self.hass)
            options = {
                **self.config_entry.options,
                CONF_ALLOW_CHANGES: user_input.get(CONF_ALLOW_CHANGES) is not False,
                CONF_RANGES: parse_ranges(ranges + accepted),
            }
            if user_input.get(CONF_INDEPENDENT) is True:
                # Its own step: what it does and what stops, then the owner confirms (service review 29.10).
                self._saved = options
                return await self.async_step_independent()
            return self.async_create_entry(data=options)
        current = list(self.config_entry.options.get(CONF_RANGES) or [])
        pending = sorted(self.hass.data.get(DATA_PENDING_RANGES, set()), key=_range_key)
        self._shown_pending = pending
        shown = ", ".join(sorted(current, key=_range_key))
        extra: dict[Any, Any] = {}
        if pending:
            # Opt-in boxes, all off: nothing Hauflo asked for is granted by a Submit made for anything else.
            extra[vol.Optional(CONF_ACCEPT_PENDING, default=[])] = SelectSelector(SelectSelectorConfig(options=pending, multiple=True, mode=SelectSelectorMode.LIST))
        server = str(self.config_entry.data.get(CONF_SERVER) or "")
        # Who services this home and who had access (service.py) — read only, for the owner.
        who, history = service.describe(await service.async_load(self.hass), self.hass.config.language)
        title = self.config_entry.title or ""
        project = title.split(" — ", 1)[1] if title.startswith("Hauflo — ") else title
        # Two texts, not a «—» for nothing pending: HA picks the step's text in the viewer's language (owner 28.10).
        return self.async_show_form(
            step_id="init_pending" if pending else "init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ALLOW_CHANGES, default=self.config_entry.options.get(CONF_ALLOW_CHANGES, True) is True): BooleanSelector(),
                    vol.Optional(CONF_RANGES, default=shown): TextSelector(TextSelectorConfig(multiline=True)),
                    **extra,
                    vol.Optional(CONF_INDEPENDENT, default=False): BooleanSelector(),
                }
            ),
            description_placeholders={
                "pending": ", ".join(pending),
                "project": project or "—",
                "server": urlparse(server).hostname or server or "—",
                "url": server,
                "service": who,
                "history": history,
            },
        )

    async def async_step_init_pending(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_init(user_input)

    async def async_step_independent(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """«Сделать дом независимым»: told first, done on the owner's confirm (independent.py). Deletes nothing."""
        if user_input is None:
            return self.async_show_form(step_id="independent", data_schema=vol.Schema({}))
        from . import independent  # noqa: PLC0415

        agent = getattr(self.config_entry, "runtime_data", None)
        engine = getattr(agent, "engine", None)
        ru = str(self.hass.config.language or "").startswith("ru")
        result = await independent.async_export(self.hass, engine)
        self._report = independent.summary(result, ru)
        persistent_notification.async_create(self.hass, self._report, "Hauflo", "hauflo_independent")
        return await self.async_step_independent_done()

    async def async_step_independent_done(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="independent_done", data_schema=vol.Schema({}), description_placeholders={"report": self._report})
        return self.async_create_entry(data=self._saved if self._saved is not None else dict(self.config_entry.options))
