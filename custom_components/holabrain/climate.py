"""Climate platform — native mapping for air-conditioner categories.

Power and mode are separate cloud keys: ``power=0`` is HVAC *off*, otherwise the ``mode``
code selects cool/heat/dry/auto/fan-only. Setting any active HVAC mode also powers the unit
on.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolabrainConfigEntry
from .entity import HolabrainEntity
from .helpers import async_add_with_discovery, iter_native
from .registry import ClimateConfig

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
            HolabrainClimate(coordinator, thing_code, category.climate)
            for thing_code, category in iter_native(coordinator, Platform.CLIMATE)
            if category.climate is not None and thing_code not in seen
        ],
    )


class HolabrainClimate(HolabrainEntity, ClimateEntity):
    """An air conditioner with HVAC modes, a target temperature and fan speeds."""

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, thing_code: str, config: ClimateConfig) -> None:
        super().__init__(coordinator, thing_code, "climate")
        self._cfg = config
        self._attr_min_temp = config.min_temp
        self._attr_max_temp = config.max_temp
        self._attr_target_temperature_step = 1

        self._mode_to_hvac = {
            code: HVACMode(value) for code, value in config.hvac_mode_codes.items()
        }
        self._hvac_to_mode = {hvac: code for code, hvac in self._mode_to_hvac.items()}
        self._attr_hvac_modes = [HVACMode.OFF, *self._mode_to_hvac.values()]

        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if config.fan_modes:
            features |= ClimateEntityFeature.FAN_MODE
            self._attr_fan_modes = list(config.fan_modes)
            self._fan_by_code = {code: label for label, code in config.fan_modes.items()}
        self._attr_supported_features = features

    @property
    def hvac_mode(self) -> HVACMode | None:
        if str(self._value(self._cfg.power_key)) == "0":
            return HVACMode.OFF
        raw = self._value(self._cfg.mode_key)
        return self._mode_to_hvac.get(str(raw))

    @property
    def current_temperature(self) -> float | None:
        if not self._cfg.current_temp_key:
            return None
        return _to_float(self._value(self._cfg.current_temp_key))

    @property
    def target_temperature(self) -> float | None:
        return _to_float(self._value(self._cfg.target_temp_key))

    @property
    def fan_mode(self) -> str | None:
        if not self._cfg.fan_modes:
            return None
        raw = self._value(self._cfg.fan_key)
        return self._fan_by_code.get(str(raw)) if raw is not None else None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self._async_send({self._cfg.power_key: "0"})
            return
        code = self._hvac_to_mode.get(hvac_mode)
        if code is not None:
            await self._async_send({self._cfg.power_key: "1", self._cfg.mode_key: code})

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is not None:
            await self._async_send({self._cfg.target_temp_key: str(int(temperature))})

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        code = self._cfg.fan_modes.get(fan_mode)
        if code is not None:
            await self._async_send({self._cfg.fan_key: code})

    async def async_turn_on(self) -> None:
        await self._async_send({self._cfg.power_key: "1"})

    async def async_turn_off(self) -> None:
        await self._async_send({self._cfg.power_key: "0"})


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
