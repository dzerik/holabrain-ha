"""Read-only fallback for an appliance category the integration does not model yet.

Without this, such an appliance is invisible: no device, no entity, nothing in the UI but a
repair issue. "My fridge is on the account and Home Assistant ignores it" then looks like a
broken setup rather than like a missing feature, and the user has no way to see whether the
integration can even talk to it.

So an unmodelled appliance still gets a device and one sensor per status key it reports.
Every one of them is diagnostic and disabled by default: the keys are raw cloud names whose
meaning and scale are unknown, so they are offered rather than presented. Enabling one is
the user saying "I recognise this number" — which is also exactly the evidence needed to
model the category properly.

Nothing here writes. A command built from a guessed key is not a feature, it is a way to put
an appliance into a state nobody intended.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import Entity

from .coordinator import HolabrainCoordinator
from .entity import HolabrainEntity
from .registry import get_category

#: Envelope and transport fields. They describe the message rather than the appliance, and
#: they change on every frame, so a sensor for one is pure noise.
_ENVELOPE = frozenset(
    {"messageNo", "clientId", "timeStamp", "online", "deviceType", "thingCode", "_cache"}
)

#: A guard against a firmware that reports something pathological. Well past any real
#: appliance (a fridge reports about forty), and it keeps a bad frame from filling the
#: entity registry.
MAX_GENERIC_SENSORS = 120


def generic_status_keys(state_keys: Any) -> list[str]:
    """The status keys worth offering as sensors, in a stable order."""
    keys = sorted(str(key) for key in state_keys if str(key) not in _ENVELOPE)
    return keys[:MAX_GENERIC_SENSORS]


def build_generic_entities(
    coordinator: HolabrainCoordinator, seen: set[str] | None = None
) -> list[Entity]:
    """One sensor per reported status key, for every appliance without a category."""
    entities: list[Entity] = []
    for thing_code, device in coordinator.devices.items():
        if seen is not None and thing_code in seen:
            continue
        if get_category(device.device_type) is not None:
            continue
        state = (coordinator.data or {}).get(thing_code)
        if state is None:
            continue
        entities.extend(
            HolabrainGenericSensor(coordinator, thing_code, key)
            for key in generic_status_keys(state.attributes)
        )
    return entities


class HolabrainGenericSensor(HolabrainEntity, SensorEntity):
    """One raw status value from an appliance the integration does not model."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    # No device class, no unit, no state class on purpose: all three would be a claim about
    # a value whose meaning is unknown, and a wrong unit is worse than none at all.
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: HolabrainCoordinator, thing_code: str, key: str
    ) -> None:
        super().__init__(coordinator, thing_code, key)
        # The cloud's own name for the field. It is not pretty, but it is the only honest
        # label available and it is what an issue report needs to quote.
        self._attr_name = key

    @property
    def native_value(self) -> Any:
        raw = self._value()
        # Long strings are payloads rather than readings, and Home Assistant rejects a state
        # over 255 characters outright, taking the whole update with it.
        return raw if raw is None or len(str(raw)) <= 255 else None
