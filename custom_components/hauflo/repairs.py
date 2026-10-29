"""One-click fixes in HA's Repairs: «Hauflo asks to allow addresses …» — Allow / Decline (integrator review 29.10,
defect 4). The owner decides here, in the house; the tunnel can't reach this flow."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from . import issues, ranges
from .const import DOMAIN


class RangesFixFlow(RepairsFlow):
    """Exactly what is added and what goes, then one click — on what was shown, never on what waits by then."""

    _shown: tuple[list[str], list[str]] | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        st = ranges.state(self.hass)
        if not st["pending"]:
            return self.async_abort(reason="nothing_pending")
        self._shown = (list(st["pending"]), list(st["remove"]))
        return self.async_show_menu(
            step_id="confirm",
            menu_options=["allow", "decline"],
            description_placeholders={
                "project": str(self.hass.data.get(ranges.DATA_PENDING_PROJECT) or "—"),
                "ranges": ", ".join(st["pending"]),
                "count": str(st["pendingCount"]),
                "removed": ", ".join(st["remove"]) or "—",
            },
        )

    async def async_step_allow(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self._resolve(True)

    async def async_step_decline(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self._resolve(False)

    def _resolve(self, allow: bool) -> FlowResult:
        loaded = self.hass.config_entries.async_loaded_entries(DOMAIN)
        if not loaded or self._shown is None:
            return self.async_abort(reason="nothing_pending")
        entry = loaded[0]
        ranges.resolve(self.hass, entry, entry.runtime_data.allowed_ranges(), allow, *self._shown)
        if ranges.pending(self.hass):
            # Asked for more meanwhile: Repairs drops this issue as the flow ends — raised again right after.
            self.hass.loop.call_soon(ranges.sync, self.hass)
        return self.async_create_entry(data={})


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None) -> RepairsFlow:
    if issue_id == issues.RANGES_PENDING:
        return RangesFixFlow()
    from homeassistant.components.repairs import ConfirmRepairFlow  # noqa: PLC0415

    return ConfirmRepairFlow()
