"""Number platform — generic, driven by the category registry."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolabrainConfigEntry
from .entity import HolabrainEntity
from .helpers import async_add_with_discovery, build_entities
from .oven import build_oven_entities
from .registry import NumberSpec

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
        lambda seen: (
            build_entities(coordinator, "numbers", HolabrainNumber, seen)
            + build_oven_entities(coordinator, Platform.NUMBER, seen)
        ),
    )


class HolabrainNumber(HolabrainEntity, NumberEntity):
    """An integer-gear control (rinse-aid dose, water softener, …).

    The upper bound comes from the model's capability profile when available, so a model
    that supports fewer gears never offers an out-of-range value.
    """

    def __init__(self, coordinator, thing_code: str, spec: NumberSpec) -> None:
        super().__init__(coordinator, thing_code, spec.key)
        self._spec = spec
        self._attr_translation_key = spec.translation_key
        self._attr_native_min_value = spec.native_min
        self._attr_native_step = spec.step
        self._attr_native_max_value = self._resolve_max()
        self._attr_entity_category = spec.entity_category

    def _resolve_max(self) -> float:
        if self._spec.max_from_gear:
            profile = self._capability
            if profile is not None:
                gear = profile.gear_max(self._spec.max_from_gear)
                if gear:
                    return gear
        return self._spec.native_max

    @property
    def native_value(self) -> float | None:
        raw = self._value()
        try:
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self._async_send({self._spec.command_key: str(int(value))})
