"""Switch platform — generic, driven by the category registry."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolabrainConfigEntry
from .const import DOMAIN
from .entity import HolabrainEntity
from .helpers import async_add_with_discovery, build_entities
from .oven import build_oven_entities
from .registry import SwitchSpec

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
            build_entities(coordinator, "switches", HolabrainSwitch, seen)
            + build_oven_entities(coordinator, Platform.SWITCH, seen)
        ),
    )


class HolabrainSwitch(HolabrainEntity, SwitchEntity):
    """A two-state control that sends distinct on/off instructions."""

    def __init__(self, coordinator, thing_code: str, spec: SwitchSpec) -> None:
        super().__init__(coordinator, thing_code, spec.key)
        self._spec = spec
        self._attr_translation_key = spec.translation_key
        self._attr_entity_category = spec.entity_category

    @property
    def is_on(self) -> bool | None:
        raw = self._value()
        if raw is None:
            return None
        return str(raw) in self._spec.on_values

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._check_writable()
        await self._async_send(dict(self._spec.on_command))

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._check_writable()
        await self._async_send(dict(self._spec.off_command))

    def _check_writable(self) -> None:
        """Refuse a write the appliance is currently guaranteed to reject."""
        blocked = self._spec.blocked_when
        if blocked is None:
            return
        key, values = blocked
        if str(self._value(key)) in values:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=self._spec.blocked_reason or "write_blocked",
            )
