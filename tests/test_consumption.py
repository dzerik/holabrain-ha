"""Consumption sensors: the cloud's own aggregation, not the appliance's counters.

The status payload carries lifetime counters, but the vendor's app never reads them — it
asks a dedicated endpoint that answers in kilowatt-hours and litres, bucketed by calendar
day, month and year. That source is better in every way that matters: the units are stated
rather than inferred, the history survives re-pairing, and the appliance does not have to be
switched on.

The figures below are shaped like a real reply from a dishwasher account.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant

from custom_components.holabrain.aiodollin.api.statistics import ConsumptionReport
from tests.conftest import DISHWASHER_CODE, FakeCloud

DEV_TOPIC = f"eu/eu_{DISHWASHER_CODE}/dev"


def test_a_report_is_parsed_into_totals_and_buckets() -> None:
    """One eco wash is about 0.87 kWh and 13 litres; the shape has to survive intact."""
    report = ConsumptionReport.from_dict(
        "day",
        {
            "totalEnergy": 3.07,
            "totalWater": 46.7,
            "energyDetail": [
                {"value": 1.1, "date": "2026-07-21"},
                {"value": 0, "date": "2026-07-22"},
                {"value": 0.87, "date": "2026-07-25"},
            ],
            "waterDetail": [
                {"value": 16.8, "date": "2026-07-21"},
                {"value": 0, "date": "2026-07-22"},
                {"value": 13.1, "date": "2026-07-25"},
            ],
        },
    )

    assert report.total_energy_kwh == 3.07
    assert report.total_water_litres == 46.7
    wash = report.value_on("2026-07-25")
    assert wash is not None
    assert (wash.energy_kwh, wash.water_litres) == (0.87, 13.1)


def test_a_model_that_meters_only_one_resource_still_reports_it() -> None:
    """Water-only and energy-only appliances both exist; neither may lose its series.

    The two series are returned independently, so pairing them positionally — or requiring
    both — would silently drop the one an appliance does report.
    """
    report = ConsumptionReport.from_dict(
        "month",
        {
            "totalEnergy": 16.91,
            "waterDetail": [{"value": 232.0, "date": "2026-07-01"}],
        },
    )

    assert report.total_energy_kwh == 16.91
    assert report.total_water_litres is None
    point = report.value_on("2026-07-01")
    assert point is not None
    assert point.water_litres == 232.0
    assert point.energy_kwh is None


def test_a_malformed_reply_does_not_raise() -> None:
    """The endpoint is undocumented; a shape change must degrade, not crash the platform."""
    assert ConsumptionReport.from_dict("day", None).total_energy_kwh is None
    assert ConsumptionReport.from_dict("day", {"totalEnergy": "n/a"}).total_energy_kwh is None
    assert ConsumptionReport.from_dict("day", {"energyDetail": "nope"}).points == ()


async def test_consumption_reaches_the_energy_dashboard(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """Device class and unit are what make these usable, not just visible.

    Without ``energy``/``kWh`` and ``water``/``L`` the values are decoration: Home
    Assistant's energy and water dashboards will not offer a sensor they cannot classify.
    """
    cloud.set_consumption(DISHWASHER_CODE, "month", energy=16.91, water=232.0)
    cloud.set_consumption(DISHWASHER_CODE, "year", energy=138.36, water=1964.7)
    assert await setup_integration()

    energy = hass.states.get(entity_id_of("sensor", f"{DISHWASHER_CODE}_energy_month"))
    assert energy.state == "16.91"
    assert energy.attributes["device_class"] == SensorDeviceClass.ENERGY
    assert energy.attributes["unit_of_measurement"] == UnitOfEnergy.KILO_WATT_HOUR
    assert energy.attributes["state_class"] == SensorStateClass.TOTAL

    water = hass.states.get(entity_id_of("sensor", f"{DISHWASHER_CODE}_water_year"))
    assert water.state == "1964.7"
    assert water.attributes["device_class"] == SensorDeviceClass.WATER
    assert water.attributes["unit_of_measurement"] == UnitOfVolume.LITERS


async def test_a_lamp_gets_no_consumption_sensors(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """A lamp does not meter itself, so those sensors would be unknown for ever."""
    cloud.add_lamp()
    assert await setup_integration()

    lamp_consumption = [
        state.entity_id
        for state in hass.states.async_all("sensor")
        if "lamp" in state.entity_id and ("energy" in state.entity_id or "water" in state.entity_id)
    ]
    assert lamp_consumption == []
