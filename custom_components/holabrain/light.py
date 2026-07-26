"""Light platform — native mapping for lamp categories.

Brightness and colour temperature use the cloud's 0-255 device scale; effects map named
scenes onto the ``mode`` instruction key. Power is folded into every command so setting
brightness or a scene also turns the lamp on.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolabrainConfigEntry
from .entity import HolabrainEntity
from .helpers import async_add_with_discovery, iter_native
from .registry import LightConfig

_DEVICE_SCALE = 255


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
            HolabrainLight(coordinator, thing_code, category.light)
            for thing_code, category in iter_native(coordinator, Platform.LIGHT)
            if category.light is not None and thing_code not in seen
        ],
    )


class HolabrainLight(HolabrainEntity, LightEntity):
    """A dimmable, tunable-white lamp with scene effects."""

    def __init__(self, coordinator, thing_code: str, config: LightConfig) -> None:
        super().__init__(coordinator, thing_code, "light")
        self._cfg = config
        self._attr_name = None  # the device name is the light's name

        if config.color_temp_key:
            self._attr_supported_color_modes = {ColorMode.COLOR_TEMP}
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_min_color_temp_kelvin = config.warm_kelvin
            self._attr_max_color_temp_kelvin = config.cool_kelvin
        elif config.brightness_key:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF

        if config.effects:
            self._attr_supported_features = LightEntityFeature.EFFECT
            self._attr_effect_list = list(config.effects)
            self._effect_by_code = {code: name for name, code in config.effects.items()}

    @property
    def is_on(self) -> bool | None:
        raw = self._value(self._cfg.power_key)
        return None if raw is None else str(raw) == self._cfg.power_on

    @property
    def brightness(self) -> int | None:
        if not self._cfg.brightness_key:
            return None
        return _to_int(self._value(self._cfg.brightness_key))

    @property
    def color_temp_kelvin(self) -> int | None:
        if not self._cfg.color_temp_key:
            return None
        device = _to_int(self._value(self._cfg.color_temp_key))
        if device is None:
            return None
        span = self._cfg.cool_kelvin - self._cfg.warm_kelvin
        return round(self._cfg.warm_kelvin + (device / _DEVICE_SCALE) * span)

    @property
    def effect(self) -> str | None:
        if not self._cfg.effects:
            return None
        raw = self._value(self._cfg.effect_key)
        return self._effect_by_code.get(str(raw)) if raw is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        instruction: dict[str, str] = {self._cfg.power_key: self._cfg.power_on}
        if ATTR_BRIGHTNESS in kwargs and self._cfg.brightness_key:
            instruction[self._cfg.brightness_key] = str(int(kwargs[ATTR_BRIGHTNESS]))
        if ATTR_COLOR_TEMP_KELVIN in kwargs and self._cfg.color_temp_key:
            kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            instruction[self._cfg.color_temp_key] = str(self._kelvin_to_device(kelvin))
        if ATTR_EFFECT in kwargs and self._cfg.effects:
            code = self._cfg.effects.get(kwargs[ATTR_EFFECT])
            if code is not None:
                instruction[self._cfg.effect_key] = code
        await self._async_send(instruction)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_send({self._cfg.power_key: self._cfg.power_off})

    def _kelvin_to_device(self, kelvin: int) -> int:
        span = self._cfg.cool_kelvin - self._cfg.warm_kelvin
        clamped = min(max(kelvin, self._cfg.warm_kelvin), self._cfg.cool_kelvin)
        return round((clamped - self._cfg.warm_kelvin) / span * _DEVICE_SCALE)


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
