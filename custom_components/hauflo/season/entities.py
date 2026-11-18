"""Season entities: recommended, current and automatic mode."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util
from ..const import DOMAIN
from ..device import device_info
from .common import MODES, RECOMMENDED, SIGNAL
if TYPE_CHECKING:
    from .engine import Season

class _SeasonEntity:
    _attr_should_poll = False
    _attr_has_entity_name = True

    def _setup(self, entry: Any, season: Season, domain: str, key: str) -> None:
        self.season = season
        self._attr_unique_id = f'{entry.entry_id}_season_{key}'
        self.entity_id = f'{domain}.{DOMAIN}_season_{key}'
        self._attr_device_info = device_info(entry.entry_id, entry)
        self._attr_translation_key = f"season_{key}_{season.config['labels']}"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL, self.async_write_ha_state))

class SeasonRecommended(_SeasonEntity, SensorEntity):
    """«Рекомендуемый сезон»: heat / cool / none / no_data — what the street says, whoever answered what."""
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(RECOMMENDED)
    _unrecorded_attributes = frozenset({'daily_means', 'below', 'above', 'days', 'days_back', 'sensor', 'auto_checked'})

    def __init__(self, entry: Any, season: Season) -> None:
        self._setup(entry, season, 'sensor', 'recommended')

    @property
    def native_value(self) -> str:
        return self.season.recommendation()[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        f = self.season.facts()
        return {k: f[k] for k in ('reason', 'daily_means', 'below', 'above', 'days', 'days_back', 'sensor', 'auto', 'auto_switched', 'auto_checked')}

class SeasonCurrent(_SeasonEntity, SensorEntity):
    """«Текущий сезон»: heat / cool — what the house's chosen thermostats do (none known yet: unknown)."""
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(MODES)

    def __init__(self, entry: Any, season: Season) -> None:
        self._setup(entry, season, 'sensor', 'current')

    @property
    def native_value(self) -> str | None:
        return self.season.current()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {'chosen': self.season.season, 'by': self.season.by, 'at': dt_util.utc_from_timestamp(self.season.at).isoformat() if self.season.at else None}

class SeasonAuto(_SeasonEntity, SwitchEntity):
    """«Переключать автоматически» — for staff (config): the same setting as Hauflo's «Зима/Лето» page."""
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: Any, season: Season) -> None:
        self._setup(entry, season, 'switch', 'auto')
        self._attr_translation_key = 'season_auto'

    @property
    def is_on(self) -> bool:
        return self.season.auto_on()

    async def _staff(self) -> None:
        uid = self._context.user_id if self._context else None
        user = await self.hass.auth.async_get_user(uid) if uid else None
        if user is not None and (not user.is_admin):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key='season_auto_staff')

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._staff()
        await self.season.set_auto(True, 'ha')

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._staff()
        await self.season.set_auto(False, 'ha')
