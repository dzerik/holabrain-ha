"""Binary sensor platform — generic, driven by the category registry."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolabrainConfigEntry
from .entity import HolabrainEntity
from .helpers import async_add_with_discovery, build_entities
from .registry import BinarySensorSpec

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
        lambda seen: build_entities(coordinator, "binary_sensors", HolabrainBinarySensor, seen),
    )


class HolabrainBinarySensor(HolabrainEntity, BinarySensorEntity):
    """A boolean derived from whether a status key holds one of the ``on`` values."""

    def __init__(self, coordinator, thing_code: str, spec: BinarySensorSpec) -> None:
        super().__init__(coordinator, thing_code, spec.key, uid=spec.uid)
        self._spec = spec
        self._attr_translation_key = spec.translation_key
        self._attr_device_class = spec.device_class
        self._attr_entity_category = spec.entity_category
        self._attr_entity_registry_enabled_default = (
            spec.entity_registry_enabled_default
        )

    @property
    def is_on(self) -> bool | None:
        raw = self._value()
        if raw is None:
            return self._from_summary()
        matches = str(raw) in self._spec.on_values
        return not matches if self._spec.invert else matches

    def _from_summary(self) -> bool | None:
        """Read the flag out of the packed status integer, for models that report one."""
        spec = self._spec
        if spec.summary_key is None or spec.summary_bit is None:
            return None
        try:
            summary = int(self._value(spec.summary_key))
        except (TypeError, ValueError):
            return None
        return bool(summary >> spec.summary_bit & 1)
