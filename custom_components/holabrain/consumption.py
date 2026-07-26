"""Water and energy consumption sensors, fed by the cloud's own aggregation.

The appliance reports lifetime counters in its status, but the vendor's app never reads
them — it asks a separate endpoint that returns the figures already bucketed by day, month
and year, in kilowatt-hours and litres. That source is better in every way that matters
here: correct units without guesswork, history that survives re-pairing, and no dependency
on the appliance being switched on.

It costs one account request, so it is fetched only when there is something new to fetch:
when a cycle finishes, and once at start-up. Between those, the numbers cannot have moved —
the buckets are calendar days.
"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .aiodollin.api.statistics import PERIOD_MONTH, PERIOD_YEAR
from .coordinator import HolabrainCoordinator
from .entity import HolabrainEntity
from .registry import METERED_CATEGORIES, get_category


def build_consumption_entities(
    coordinator: HolabrainCoordinator, seen: set[str] | None = None
) -> list[Entity]:
    """One energy and one water sensor per metering appliance, for month and year."""
    entities: list[Entity] = []
    for thing_code, device in coordinator.devices.items():
        if seen is not None and thing_code in seen:
            continue
        category = get_category(device.device_type)
        if category is None or category.category not in METERED_CATEGORIES:
            continue
        for period in (PERIOD_MONTH, PERIOD_YEAR):
            entities.append(HolabrainEnergySensor(coordinator, thing_code, period))
            entities.append(HolabrainWaterSensor(coordinator, thing_code, period))
    return entities


class _ConsumptionSensor(HolabrainEntity, SensorEntity):
    """Shared plumbing for a consumption total over one reporting window."""

    # TOTAL rather than TOTAL_INCREASING: these reset when the calendar rolls over, and
    # TOTAL_INCREASING would read each reset as a meter replacement and spike the graph.
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(
        self, coordinator: HolabrainCoordinator, thing_code: str, period: str
    ) -> None:
        super().__init__(coordinator, thing_code, "@consumption", uid=f"{self.KIND}_{period}")
        self._period = period
        self._attr_translation_key = f"{self.KIND}_{period}"

    @property
    def available(self) -> bool:
        # Consumption comes from the account rather than from the appliance, so it stays
        # readable while the appliance itself is unreachable.
        return self.coordinator.last_update_success


class HolabrainEnergySensor(_ConsumptionSensor):
    """Electricity used over the reporting window."""

    KIND = "energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    @property
    def native_value(self) -> float | None:
        report = self.coordinator.consumption(self._thing_code, self._period)
        return None if report is None else report.total_energy_kwh


class HolabrainWaterSensor(_ConsumptionSensor):
    """Water used over the reporting window."""

    KIND = "water"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS

    @property
    def native_value(self) -> float | None:
        report = self.coordinator.consumption(self._thing_code, self._period)
        return None if report is None else report.total_water_litres


async def async_setup_consumption(
    hass: HomeAssistant,
    coordinator: HolabrainCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the consumption sensors for every metering appliance on the account."""
    async_add_entities(build_consumption_entities(coordinator))
