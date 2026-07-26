"""Base entity shared by every HolaBrain platform."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .aiodollin import CapabilityProfile, Device, DeviceState
from .const import DOMAIN
from .coordinator import HolabrainCoordinator
from .registry import get_category


def _fallback_name(device: Device) -> str:
    """A readable device name for an appliance the account has no name for."""
    category = get_category(device.device_type)
    if category is not None:
        return category.category.replace("_", " ").capitalize()
    return device.model or "HolaBrain appliance"


class HolabrainEntity(CoordinatorEntity[HolabrainCoordinator]):
    """Common device_info, availability and value access for all entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HolabrainCoordinator,
        thing_code: str,
        key: str,
        uid: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._thing_code = thing_code
        self._key = key
        device = coordinator.devices[thing_code]
        # ``uid`` separates entities that read the same status key (e.g. two pre-heat flags).
        self._attr_unique_id = f"{thing_code}_{uid or key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, thing_code)},
            # An appliance added by the vendor app without being named comes back with an
            # empty name; with ``has_entity_name`` that would produce entities called
            # " door" rather than "Dishwasher door".
            name=device.name or _fallback_name(device),
            manufacturer="HolaBrain",
            model=device.model or None,
            sw_version=device.firmware_version or None,
        )

    @property
    def _state(self) -> DeviceState | None:
        return (self.coordinator.data or {}).get(self._thing_code)

    @property
    def _capability(self) -> CapabilityProfile | None:
        return self.coordinator.capability_for(self._thing_code)

    @property
    def available(self) -> bool:
        state = self._state
        return super().available and state is not None and state.online

    def _value(self, key: str | None = None) -> Any:
        state = self._state
        if state is None:
            return None
        return state.get(key or self._key)

    async def _async_send(self, instruction: dict[str, str]) -> None:
        await self.coordinator.async_send_instruction(self._thing_code, instruction)
