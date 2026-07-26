"""Sensor platform — generic, driven by the category registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolabrainConfigEntry
from .aiodollin import DeviceState
from .entity import HolabrainEntity
from .helpers import async_add_with_discovery, build_entities
from .registry import (
    TRANSFORM_OVEN_STATUS,
    TRANSFORM_REMAIN_MINUTES,
    TRANSFORM_SECONDS_MINUTES,
    SensorSpec,
)

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
        lambda seen: build_entities(coordinator, "sensors", HolabrainSensor, seen),
    )


def _to_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _remain_minutes(state: DeviceState, spec: SensorSpec) -> int | None:
    """Two-byte remaining time: high byte * 256 + low byte."""
    low = _to_int(state.get("remainTimeL"))
    return None if low is None else (_to_int(state.get("remainTimeH")) or 0) * 256 + low


def _seconds_minutes(state: DeviceState, spec: SensorSpec) -> int | None:
    """Whole seconds → minutes, rounded up so a running countdown never reaches 0 early."""
    seconds = _to_int(state.get(spec.key))
    return None if seconds is None else -(-max(seconds, 0) // 60)


_OVEN_RUN_STATE = {
    "1": "off", "2": "standby", "3": "cooking",
    "4": "cook_complete", "5": "delay", "6": "pause",
}


def _oven_status(state: DeviceState, spec: SensorSpec) -> str | None:
    """Fold the pre-heat phase over the appliance run state into one readable value."""
    if not state.online:
        return "offline"
    run_state = _OVEN_RUN_STATE.get(str(state.get(spec.key)))
    if run_state != "cooking":
        return run_state
    preheat = str(state.get("preheatState") or "0")
    if preheat == "1":
        return "preheating"
    if preheat in ("2", "3"):
        return "preheat_finish"
    return "cooking"


_TRANSFORMS: dict[str, Callable[[DeviceState, SensorSpec], Any]] = {
    TRANSFORM_REMAIN_MINUTES: _remain_minutes,
    TRANSFORM_SECONDS_MINUTES: _seconds_minutes,
    TRANSFORM_OVEN_STATUS: _oven_status,
}


class HolabrainSensor(HolabrainEntity, SensorEntity):
    """A read-only status value, optionally enum-mapped or transformed."""

    def __init__(self, coordinator, thing_code: str, spec: SensorSpec) -> None:
        super().__init__(coordinator, thing_code, spec.key)
        self._spec = spec
        self._gates = spec.gates
        self._attr_translation_key = spec.translation_key
        self._attr_device_class = spec.device_class
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_state_class = spec.state_class
        self._attr_entity_category = spec.entity_category
        self._attr_entity_registry_enabled_default = spec.entity_registry_enabled_default
        if spec.device_class == SensorDeviceClass.ENUM:
            if spec.options is not None:
                # Labels produced by a transform rather than by a code → label map.
                self._attr_options = list(spec.options)
            elif spec.value_map:
                # Options are the distinct mapped labels, order-preserved.
                self._attr_options = list(dict.fromkeys(spec.value_map.values()))

    @property
    def native_value(self):
        # The appliance keeps reporting fields that stopped meaning anything when the cycle
        # ended; reporting them as unknown is the honest answer, and it is what the vendor's
        # own app does rather than showing last cycle's leftovers as live.
        if not self._is_meaningful:
            return None
        transform = _TRANSFORMS.get(self._spec.transform or "")
        if transform is not None:
            state = self._state
            return transform(state, self._spec) if state is not None else None
        raw = self._value()
        if raw is None:
            return None
        if self._spec.value_map is not None:
            # Unknown codes map to None so the enum sensor never reports an invalid option.
            return self._spec.value_map.get(str(raw))
        return raw
