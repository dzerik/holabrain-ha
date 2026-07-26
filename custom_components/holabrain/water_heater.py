"""Water heater platform — native mapping for boiler categories.

Operation modes (normal / eco / smart / high-temp) are flag combinations on the cloud, so
setting a mode writes several keys at once and the current mode is read back by scanning
the flags in priority order.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolabrainConfigEntry
from .entity import HolabrainEntity
from .helpers import async_add_with_discovery, iter_native
from .registry import WaterHeaterConfig

# The coordinator owns every read, and writes are single instructions the cloud
# serialises anyway; entities never fan out requests of their own.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HolabrainConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_with_discovery(
        entry,
        coordinator,
        async_add_entities,
        lambda seen: [
            HolabrainWaterHeater(coordinator, thing_code, category.water_heater)
            for thing_code, category in iter_native(coordinator, Platform.WATER_HEATER)
            if category.water_heater is not None and thing_code not in seen
        ],
    )


class HolabrainWaterHeater(HolabrainEntity, WaterHeaterEntity):
    """A water heater with a target temperature and flag-based operation modes."""

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
        | WaterHeaterEntityFeature.ON_OFF
    )

    def __init__(self, coordinator, thing_code: str, config: WaterHeaterConfig) -> None:
        super().__init__(coordinator, thing_code, "water_heater")
        self._cfg = config
        self._attr_min_temp = config.min_temp
        self._attr_max_temp = config.max_temp
        self._attr_operation_list = list(config.operations)

    @property
    def current_temperature(self) -> float | None:
        for key in self._cfg.current_temp_keys:
            value = self._value(key)
            if value not in (None, ""):
                return _to_float(value)
        return None

    @property
    def target_temperature(self) -> float | None:
        return _to_float(self._value(self._cfg.target_temp_key))

    @property
    def current_operation(self) -> str | None:
        for flag, mode in self._cfg.operation_flags:
            if str(self._value(flag)) == "1":
                return mode
        return self._cfg.default_operation

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self._async_send({self._cfg.target_temp_key: str(int(temperature))})

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        instruction = self._cfg.operations.get(operation_mode)
        if instruction:
            await self._async_send(dict(instruction))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_send({self._cfg.power_key: "1"})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_send({self._cfg.power_key: "0"})


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
