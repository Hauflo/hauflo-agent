"""Hauflo agent setup: the integrator types the pairing code from Hauflo (project → «Агент Hauflo»)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.components import persistent_notification
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import BooleanSelector, SelectSelector, SelectSelectorConfig, SelectSelectorMode, TextSelector, TextSelectorConfig

from .const import CONF_ACCEPT_PENDING, CONF_ALLOW_CHANGES, CONF_CODE, CONF_RANGES, CONF_SECRET, CONF_SERVER, DATA_PENDING_RANGES, DATA_UNLINKED, DEFAULT_SERVER, DOMAIN, FALLBACK_SERVER, REGION_SERVERS


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

    async def _pair(self, server: str, code: str, previous: str | None = None) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Trade a pairing code for the agent secret. Status first, body second (HA core review 25.10: a 404 page
        in HTML read as «no connection» instead of «wrong code»)."""
        if not _secure(server):
            return None, {CONF_SERVER: "https_required"}
        try:
            session = async_get_clientsession(self.hass)
            payload = {"code": code, **({"previous": previous} if previous else {})}
            async with session.post(f"{server}/api/agent/pair", json=payload, timeout=aiohttp.ClientTimeout(total=20)) as res:
                if res.status == 404:
                    return None, {CONF_CODE: "invalid_code"}
                if res.status >= 400:
                    return None, {"base": "cannot_connect"}
                body = await res.json(content_type=None)
                if not isinstance(body, dict) or not body.get("secret"):
                    return None, {"base": "cannot_connect"}
                return body, {}
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return None, {"base": "cannot_connect"}

    def _schema(self, server: str) -> vol.Schema:
        # The server folded away under «Дополнительно»: the region in the code picks it (security re-review round 4:
        # «type this address» is how someone else's server would get an admin tunnel into the house).
        return vol.Schema(
            {
                vol.Required(CONF_CODE): str,
                vol.Optional("advanced"): section(vol.Schema({vol.Required(CONF_SERVER, default=server): str}), {"collapsed": True}),
            }
        )

    async def _code_step(self, step_id: str, user_input: dict[str, Any] | None, server: str, entry: Any = None) -> tuple[dict[str, Any] | None, str, dict[str, str]]:
        errors: dict[str, str] = {}
        if user_input is not None:
            code = _pair_code(user_input[CONF_CODE])
            typed = (user_input.get("advanced") or {}).get(CONF_SERVER, server)
            server = self._server_for(code, str(typed).strip().rstrip("/"))
            # The old secret only to the server that issued it: there the old project lets this agent go.
            previous = entry.data.get(CONF_SECRET) if entry is not None and entry.data.get(CONF_SERVER) == server else None
            body, errors = await self._pair(server, code, previous)
            if body is None and self._plan_b(code, server, errors):
                # A code without a region: an older Russian code, or the international server blocked — try Russia.
                prev_b = entry.data.get(CONF_SECRET) if entry is not None and entry.data.get(CONF_SERVER) == FALLBACK_SERVER else None
                body_b, errors_b = await self._pair(FALLBACK_SERVER, code, prev_b)
                if body_b is not None:
                    return body_b, FALLBACK_SERVER, {}
                if errors_b.get(CONF_CODE):
                    errors = errors_b
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
            self.hass.data.pop(DATA_UNLINKED, None)
            return self.async_update_reload_and_abort(entry, title=f"Hauflo — {body.get('project', '')}", data_updates={CONF_SERVER: server, CONF_SECRET: body["secret"]})
        return self.async_show_form(step_id="reconfigure", data_schema=self._schema(server), errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Hauflo no longer knows this agent (unlinked, project deleted): HA's own «re-configure» with a new code."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        body, server, errors = await self._code_step("reauth_confirm", user_input, entry.data.get(CONF_SERVER, DEFAULT_SERVER), entry)
        if body is not None:
            self.hass.data.pop(DATA_UNLINKED, None)
            return self.async_update_reload_and_abort(entry, title=f"Hauflo — {body.get('project', '')}", data_updates={CONF_SERVER: server, CONF_SECRET: body["secret"]})
        return self.async_show_form(step_id="reauth_confirm", data_schema=self._schema(server), errors=errors)


class HaufloOptionsFlow(OptionsFlow):
    """The owner's switches for Hauflo in this house — changed only here, in HA (the tunnel can't reach this flow):
    whether Hauflo may change the setup at all, and the group addresses it may send to. No secrets, no notification
    settings — those live in Hauflo (owner 28.10)."""

    _shown_pending: list[str] | None = None

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
                if not pending:
                    persistent_notification.async_dismiss(self.hass, "hauflo_ranges")
            return self.async_create_entry(
                data={
                    **self.config_entry.options,
                    CONF_ALLOW_CHANGES: user_input.get(CONF_ALLOW_CHANGES) is not False,
                    CONF_RANGES: parse_ranges(ranges + accepted),
                }
            )
        current = list(self.config_entry.options.get(CONF_RANGES) or [])
        pending = sorted(self.hass.data.get(DATA_PENDING_RANGES, set()), key=_range_key)
        self._shown_pending = pending
        shown = ", ".join(sorted(current, key=_range_key))
        extra: dict[Any, Any] = {}
        if pending:
            # Opt-in boxes, all off: nothing Hauflo asked for is granted by a Submit made for anything else.
            extra[vol.Optional(CONF_ACCEPT_PENDING, default=[])] = SelectSelector(SelectSelectorConfig(options=pending, multiple=True, mode=SelectSelectorMode.LIST))
        server = str(self.config_entry.data.get(CONF_SERVER) or "")
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
                }
            ),
            description_placeholders={
                "pending": ", ".join(pending),
                "project": project or "—",
                "server": urlparse(server).hostname or server or "—",
                "url": server,
            },
        )

    async def async_step_init_pending(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_init(user_input)


def _range_key(r: str) -> tuple[int, ...]:
    """«main/middle[/first[-last]]» in bus order; a whole group before its addresses."""
    nums = [int(p) if p.isdigit() else 999 for p in re.split(r"[/-]", r)]
    return tuple((nums + [-1, -1, -1])[:3])


def parse_ranges(value: Any) -> list[str]:
    """«1/1, 1/2/5 7/0/1-21» or a list → ["1/1", "1/2/5", "7/0/1-21"]: a main/middle group, one address or a span of
    one middle group's addresses (KNX review 27.10: Hauflo proposes the addresses it writes, not whole groups); KNX
    limits 0–31 / 0–7 / 0–255; anything else is dropped."""
    items = value if isinstance(value, list) else re.split(r"[\s,;]+", str(value or ""))
    out = set()
    for x in items:
        m = re.fullmatch(r"(\d{1,2})/(\d)(?:/(\d{1,3})(?:\s*-\s*(\d{1,3}))?)?", str(x).strip())
        if not m or int(m.group(1)) > 31 or int(m.group(2)) > 7:
            continue
        group = f"{int(m.group(1))}/{int(m.group(2))}"
        if m.group(3) is None:
            out.add(group)
            continue
        lo = int(m.group(3))
        hi = int(m.group(4)) if m.group(4) is not None else lo
        if hi > 255 or lo > hi:
            continue
        out.add(f"{group}/{lo}" if lo == hi else f"{group}/{lo}-{hi}")
    return sorted(out, key=_range_key)
