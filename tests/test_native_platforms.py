"""Native platforms: light, climate and water heater.

Unlike the registry-driven platforms these translate between two different vocabularies —
Home Assistant's (kelvin, HVAC modes, operation modes) and the appliance's (a 0-255 byte,
a power flag plus a mode code, three independent boolean flags). Every test below targets a
place where that translation can be lossy, asymmetric or simply inverted.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.components.water_heater import WaterHeaterEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.holabrain.const import DEFAULT_SCAN_INTERVAL, DOMAIN
from tests.conftest import AC_CODE, BOILER_CODE, LAMP_CODE, FakeCloud


async def _poll(hass: HomeAssistant) -> None:
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 5)
    )
    await hass.async_block_till_done()


@pytest.fixture
def lamp_cloud(cloud: FakeCloud) -> FakeCloud:
    cloud.devices.clear()
    cloud.states.clear()
    cloud.add_lamp()
    return cloud


@pytest.fixture
def boiler_cloud(cloud: FakeCloud) -> FakeCloud:
    cloud.devices.clear()
    cloud.states.clear()
    cloud.add_boiler()
    return cloud


@pytest.fixture
def ac_cloud(cloud: FakeCloud) -> FakeCloud:
    cloud.devices.clear()
    cloud.states.clear()
    cloud.add_air_conditioner()
    return cloud


# =========================================================================================
# Light
# =========================================================================================


async def test_lamp_exposes_a_usable_colour_mode_and_kelvin_window(
    hass: HomeAssistant, lamp_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """A light with an empty or missing colour-mode set is rejected outright by newer cores.

    The failure is not graceful: the entity is dropped with a validation error, so the light
    simply never appears.
    """
    assert await setup_integration()

    state = hass.states.get(entity_id_of("light", f"{LAMP_CODE}_light"))
    assert state.attributes["supported_color_modes"] == ["color_temp"]
    assert state.attributes["min_color_temp_kelvin"] == 2700
    assert state.attributes["max_color_temp_kelvin"] == 6500


@pytest.mark.parametrize(
    ("device_value", "kelvin"), [("0", 2700), ("255", 6500), ("128", 4607)]
)
async def test_colour_temperature_maps_the_device_byte_onto_kelvin(
    hass: HomeAssistant,
    lamp_cloud: FakeCloud,
    setup_integration,
    entity_id_of,
    device_value: str,
    kelvin: int,
) -> None:
    """The appliance reports a 0-255 byte; both ends of the scale must land exactly.

    An off-by-one at the ends is what makes a lamp that is set to its warmest still report a
    few hundred kelvin above the advertised minimum — which Home Assistant then clamps,
    producing a value the user never chose.
    """
    assert await setup_integration()
    entity_id = entity_id_of("light", f"{LAMP_CODE}_light")

    lamp_cloud.set_attr(LAMP_CODE, colorTemp=device_value)
    await _poll(hass)

    assert hass.states.get(entity_id).attributes["color_temp_kelvin"] == kelvin


@pytest.mark.parametrize(
    ("kelvin", "device_value"),
    [
        (2700, "0"),
        (6500, "255"),
        (4600, "128"),
        (1000, "0"),  # below the window — must clamp, not go negative
        (9000, "255"),  # above the window — must clamp, not overflow the byte
    ],
)
async def test_setting_a_colour_temperature_clamps_into_the_device_byte(
    hass: HomeAssistant,
    lamp_cloud: FakeCloud,
    setup_integration,
    entity_id_of,
    kelvin: int,
    device_value: str,
) -> None:
    """Kelvin outside the lamp's range must clamp to the byte range, never wrap.

    Home Assistant clamps to the advertised window in most paths, but scripts and other
    integrations can hand over anything; a negative or >255 byte is accepted by the
    transport and interpreted by the appliance as an entirely different setting.
    """
    assert await setup_integration()
    entity_id = entity_id_of("light", f"{LAMP_CODE}_light")

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": entity_id, "color_temp_kelvin": kelvin},
        blocking=True,
    )

    _, instruction = lamp_cloud.instructions[-1]
    assert instruction["colorTemp"] == device_value


async def test_turning_the_lamp_on_with_brightness_also_powers_it(
    hass: HomeAssistant, lamp_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """Brightness alone does not switch a lamp on; the power flag must ride along.

    Otherwise "turn on at 40%" leaves the lamp dark and Home Assistant showing it as on.
    """
    assert await setup_integration()
    entity_id = entity_id_of("light", f"{LAMP_CODE}_light")

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id, "brightness": 77}, blocking=True
    )

    assert lamp_cloud.instructions[-1] == (LAMP_CODE, {"power": "1", "bright": "77"})


async def test_selecting_a_scene_also_powers_the_lamp_on(
    hass: HomeAssistant, lamp_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """Scenes are the main way this lamp is used; picking one must turn it on too."""
    assert await setup_integration()
    entity_id = entity_id_of("light", f"{LAMP_CODE}_light")

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id, "effect": "cinema"}, blocking=True
    )

    assert lamp_cloud.instructions[-1] == (LAMP_CODE, {"power": "1", "mode": "5"})


async def test_an_unknown_scene_code_does_not_break_the_light(
    hass: HomeAssistant, lamp_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """A scene the integration does not know must read as no effect, not as an invalid one.

    Home Assistant validates ``effect`` against ``effect_list``; a value outside it makes the
    state invalid and the entity unusable in the UI.
    """
    assert await setup_integration()
    entity_id = entity_id_of("light", f"{LAMP_CODE}_light")

    lamp_cloud.set_attr(LAMP_CODE, mode="42")
    await _poll(hass)

    state = hass.states.get(entity_id)
    assert state.attributes["effect"] is None
    assert "42" not in state.attributes["effect_list"]


async def test_a_non_numeric_brightness_does_not_take_the_light_down(
    hass: HomeAssistant, lamp_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """Garbage in a numeric field must degrade to "unknown brightness", not raise."""
    assert await setup_integration()
    entity_id = entity_id_of("light", f"{LAMP_CODE}_light")

    lamp_cloud.set_attr(LAMP_CODE, bright="--")
    await _poll(hass)

    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["brightness"] is None


async def test_turning_the_lamp_off_sends_only_the_power_flag(
    hass: HomeAssistant, lamp_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """Switching off must not also rewrite brightness or scene.

    Appliances that persist the last brightness rely on it not being overwritten with 0; a
    lamp turned off at 1% comes back at 1% the next morning.
    """
    assert await setup_integration()
    entity_id = entity_id_of("light", f"{LAMP_CODE}_light")

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": entity_id}, blocking=True
    )

    assert lamp_cloud.instructions[-1] == (LAMP_CODE, {"power": "0"})


# =========================================================================================
# Climate
# =========================================================================================


async def test_air_conditioner_reports_mode_temperatures_and_fan(
    hass: HomeAssistant, ac_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """The baseline mapping: mode code, both temperatures and the fan speed code."""
    assert await setup_integration()

    state = hass.states.get(entity_id_of("climate", f"{AC_CODE}_climate"))
    assert state.state == HVACMode.COOL
    assert state.attributes["temperature"] == 23
    assert state.attributes["current_temperature"] == 27
    assert state.attributes["fan_mode"] == "medium"


async def test_power_off_wins_over_whatever_mode_is_still_reported(
    hass: HomeAssistant, ac_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """Power and mode are separate keys, and a powered-off unit keeps its last mode code.

    Reading the mode without checking power shows a unit as heating while it is off — and
    every "is the AC running" automation misfires.
    """
    assert await setup_integration()
    entity_id = entity_id_of("climate", f"{AC_CODE}_climate")

    ac_cloud.set_attr(AC_CODE, power="0", mode="4")
    await _poll(hass)

    assert hass.states.get(entity_id).state == HVACMode.OFF


async def test_selecting_a_mode_powers_the_unit_on_in_the_same_command(
    hass: HomeAssistant, ac_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """Setting heat on a powered-off unit must actually start it.

    Sending only the mode leaves the unit off with a new mode stored, and Home Assistant
    optimistically showing "heat".
    """
    assert await setup_integration()
    entity_id = entity_id_of("climate", f"{AC_CODE}_climate")
    ac_cloud.set_attr(AC_CODE, power="0")
    await _poll(hass)

    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": entity_id, "hvac_mode": HVACMode.HEAT},
        blocking=True,
    )

    assert ac_cloud.instructions[-1] == (AC_CODE, {"power": "1", "mode": "4"})


async def test_switching_off_uses_the_power_key_alone(
    hass: HomeAssistant, ac_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """"Off" is not an HVAC mode on this appliance; it must not be sent as one."""
    assert await setup_integration()
    entity_id = entity_id_of("climate", f"{AC_CODE}_climate")

    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": entity_id, "hvac_mode": HVACMode.OFF},
        blocking=True,
    )

    assert ac_cloud.instructions[-1] == (AC_CODE, {"power": "0"})


async def test_an_unknown_mode_code_does_not_produce_an_invalid_hvac_mode(
    hass: HomeAssistant, ac_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """A mode code outside the map must read unknown rather than crash the entity.

    ``HVACMode("9")`` raises, and it raises inside a property — taking the whole climate
    entity offline for a value the appliance is perfectly happy with.
    """
    assert await setup_integration()
    entity_id = entity_id_of("climate", f"{AC_CODE}_climate")

    ac_cloud.set_attr(AC_CODE, mode="9")
    await _poll(hass)

    assert hass.states.get(entity_id).state == "unknown"


async def test_a_fractional_setpoint_is_sent_as_a_whole_degree(
    hass: HomeAssistant, ac_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """Half-degree requests come from thermostat cards and generic climate automations.

    The appliance takes whole degrees; a fractional value is rejected by the cloud or
    silently truncated somewhere less predictable.
    """
    assert await setup_integration()
    entity_id = entity_id_of("climate", f"{AC_CODE}_climate")

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity_id, "temperature": 21.5},
        blocking=True,
    )

    assert ac_cloud.instructions[-1] == (AC_CODE, {"temp": "21"})


async def test_an_unknown_fan_code_reads_as_no_fan_mode(
    hass: HomeAssistant, ac_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """Stepless fan units report speeds outside the named set; that must not be an error."""
    assert await setup_integration()
    entity_id = entity_id_of("climate", f"{AC_CODE}_climate")

    ac_cloud.set_attr(AC_CODE, windSpeed="55")
    await _poll(hass)

    assert hass.states.get(entity_id).attributes["fan_mode"] is None


async def test_a_non_numeric_temperature_reading_is_dropped_not_raised(
    hass: HomeAssistant, ac_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """An empty indoor-temperature field is common while the unit is starting up."""
    assert await setup_integration()
    entity_id = entity_id_of("climate", f"{AC_CODE}_climate")

    ac_cloud.set_attr(AC_CODE, indoorTemp="")
    await _poll(hass)

    assert hass.states.get(entity_id).attributes["current_temperature"] is None


# =========================================================================================
# Water heater
# =========================================================================================


async def test_water_heater_reports_temperatures_and_the_default_mode(
    hass: HomeAssistant, boiler_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """With no mode flag matching, the appliance still reports a real mode — not no mode.

    ``current_operation = None`` renders as unknown and cannot be used in an automation.
    """
    assert await setup_integration()

    state = hass.states.get(entity_id_of("water_heater", f"{BOILER_CODE}_water_heater"))
    assert state.attributes["temperature"] == 60
    assert state.attributes["current_temperature"] == 48
    assert state.state == "double"


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ({"cloudSmart": "0", "bodyNum": "1"}, "single"),
        ({"cloudSmart": "0", "bodyNum": "2"}, "double"),
        ({"cloudSmart": "1", "bodyNum": "0"}, "smart"),
        # cloudSmart wins even over a stale bodyNum from before Smart was selected — this is
        # the exact combination a real unit reports for a moment after switching to Smart.
        ({"cloudSmart": "1", "bodyNum": "1"}, "smart"),
    ],
)
async def test_operation_mode_is_resolved_by_flag_priority(
    hass: HomeAssistant,
    boiler_cloud: FakeCloud,
    setup_integration,
    entity_id_of,
    flags: dict,
    expected: str,
) -> None:
    """``cloudSmart`` and ``bodyNum`` are read together, so priority decides the answer.

    Reading them in the wrong order reports "single" for an appliance that has actually
    handed control to Smart mode, hiding that its setpoint is no longer the one you set.
    """
    assert await setup_integration()
    entity_id = entity_id_of("water_heater", f"{BOILER_CODE}_water_heater")

    boiler_cloud.set_attr(BOILER_CODE, **flags)
    await _poll(hass)

    assert hass.states.get(entity_id).state == expected


async def test_selecting_a_mode_clears_the_flags_of_the_other_modes(
    hass: HomeAssistant, boiler_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """Setting a mode must write every flag the mode needs, not just the changed one.

    Leaving ``cloudSmart`` set from a prior Smart selection is what produces the
    conflicting states above — the appliance ends up in a combination nobody chose.
    """
    assert await setup_integration()
    entity_id = entity_id_of("water_heater", f"{BOILER_CODE}_water_heater")
    boiler_cloud.set_attr(BOILER_CODE, cloudSmart="1", bodyNum="0")
    await _poll(hass)

    await hass.services.async_call(
        "water_heater",
        "set_operation_mode",
        {"entity_id": entity_id, "operation_mode": "single"},
        blocking=True,
    )

    assert boiler_cloud.instructions[-1] == (
        BOILER_CODE,
        {"cloudSmart": "0", "bodyNum": "1"},
    )


@pytest.mark.parametrize("target_by", ["entity_id", "device_id"])
async def test_smart_mode_locks_out_manual_temperature(
    hass: HomeAssistant, boiler_cloud: FakeCloud, setup_integration, entity_id_of, target_by: str
) -> None:
    """The real appliance picks its own setpoint in Smart mode and refuses a manual one.

    Confirmed on hardware: switching to Smart jumped the setpoint straight to the
    appliance's max, unprompted, and the vendor app itself disables temperature entry
    while it is active. Home Assistant must refuse it too rather than silently sending an
    instruction the appliance will ignore.

    Both targeting forms are exercised because the refusal must come from our own guard.
    Withdrawing ``TARGET_TEMPERATURE`` instead would raise for an explicit ``entity_id``
    and quietly skip the entity for a device target — the automation would be told it
    succeeded while nothing was sent.
    """
    assert await setup_integration()
    entity_id = entity_id_of("water_heater", f"{BOILER_CODE}_water_heater")
    boiler_cloud.set_attr(BOILER_CODE, cloudSmart="1", bodyNum="0")
    await _poll(hass)

    state = hass.states.get(entity_id)
    assert state.attributes["supported_features"] & WaterHeaterEntityFeature.TARGET_TEMPERATURE

    if target_by == "entity_id":
        target = {"entity_id": entity_id}
    else:
        device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, BOILER_CODE)})
        target = {"device_id": device.id}

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "water_heater",
            "set_temperature",
            {**target, "temperature": 50},
            blocking=True,
        )

    assert err.value.translation_key == "smart_mode_locked"
    assert boiler_cloud.instructions == []


async def test_current_temperature_falls_back_to_the_setpoint_key(
    hass: HomeAssistant, boiler_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """Not every model reports a measured temperature; the fallback must be exercised.

    Without it these models show no current temperature at all, which hides the only number
    a water heater is actually watched for.
    """
    assert await setup_integration()
    entity_id = entity_id_of("water_heater", f"{BOILER_CODE}_water_heater")

    state = dict(boiler_cloud.states[BOILER_CODE])
    state.pop("cur_temperature")
    boiler_cloud.states[BOILER_CODE] = state
    await _poll(hass)

    assert hass.states.get(entity_id).attributes["current_temperature"] == 60


async def test_current_temperature_prefers_target_temp_over_the_setpoint(
    hass: HomeAssistant, boiler_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """``targetTemp`` is the measured tank temperature, not the setpoint — swapped names.

    Confirmed on real hardware (Terma AquaPro WiFi, 51020ED8) by changing the setpoint and
    observing which field moved: ``temp`` tracked the new value, ``targetTemp`` stayed at
    the panel's current-temperature reading. A model with no ``cur_temperature`` key — as
    this one has — must read ``targetTemp`` rather than falling all the way through to the
    setpoint.
    """
    assert await setup_integration()
    entity_id = entity_id_of("water_heater", f"{BOILER_CODE}_water_heater")

    state = dict(boiler_cloud.states[BOILER_CODE])
    state.pop("cur_temperature")
    state["targetTemp"] = "58"
    state["temp"] = "65"
    boiler_cloud.states[BOILER_CODE] = state
    await _poll(hass)

    attrs = hass.states.get(entity_id).attributes
    assert attrs["current_temperature"] == 58
    assert attrs["temperature"] == 65


async def test_an_empty_temperature_field_is_not_read_as_zero(
    hass: HomeAssistant, boiler_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """An empty string must not become 0 °C.

    A water heater reporting 0 °C looks like a fault and triggers freeze-protection
    automations in the middle of a normal heating cycle.
    """
    assert await setup_integration()
    entity_id = entity_id_of("water_heater", f"{BOILER_CODE}_water_heater")

    boiler_cloud.set_attr(BOILER_CODE, cur_temperature="", temp="")
    await _poll(hass)

    state = hass.states.get(entity_id)
    assert state.attributes["current_temperature"] is None
    assert state.attributes["temperature"] is None


async def test_setting_a_target_temperature_sends_a_whole_degree(
    hass: HomeAssistant, boiler_cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """Water heaters take whole degrees; the entity must round before sending."""
    assert await setup_integration()
    entity_id = entity_id_of("water_heater", f"{BOILER_CODE}_water_heater")

    await hass.services.async_call(
        "water_heater",
        "set_temperature",
        {"entity_id": entity_id, "temperature": 54.5},
        blocking=True,
    )

    assert boiler_cloud.instructions[-1] == (BOILER_CODE, {"temp": "54"})


async def test_each_native_category_claims_only_its_own_devices(
    hass: HomeAssistant, cloud: FakeCloud, setup_integration
) -> None:
    """Three native platforms in one account must not pick up each other's appliances.

    They all iterate the same device list; a wrong platform comparison gives the lamp a
    climate entity and the boiler a light.
    """
    cloud.add_lamp()
    cloud.add_boiler()
    cloud.add_air_conditioner()

    assert await setup_integration()

    assert hass.states.async_entity_ids("light") == [
        state.entity_id for state in hass.states.async_all("light")
    ]
    assert len(hass.states.async_all("light")) == 1
    assert len(hass.states.async_all("climate")) == 1
    assert len(hass.states.async_all("water_heater")) == 1
