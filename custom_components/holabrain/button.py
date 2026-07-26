"""Button platform — generic, driven by the category registry."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolabrainConfigEntry
from .dishwasher import build_dishwasher_entities
from .entity import HolabrainEntity
from .helpers import async_add_with_discovery, build_entities
from .oven import build_oven_entities
from .registry import ButtonSpec

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
            build_entities(coordinator, "buttons", HolabrainButton, seen)
            + build_oven_entities(coordinator, Platform.BUTTON, seen)
            + build_dishwasher_entities(coordinator, Platform.BUTTON, seen)
        ),
    )


class HolabrainButton(HolabrainEntity, ButtonEntity):
    """A momentary control that sends a fixed instruction."""

    def __init__(self, coordinator, thing_code: str, spec: ButtonSpec) -> None:
        super().__init__(coordinator, thing_code, spec.key)
        self._spec = spec
        self._attr_translation_key = spec.translation_key
        self._attr_entity_category = spec.entity_category

    async def async_press(self) -> None:
        await self._async_send(dict(self._spec.command))
