"""Registry-driven platforms (sensor, binary sensor, switch, number, select, button).

These platforms contain no per-device logic — they translate declarative descriptors into
entities — so the interesting failures are all about *values*: a code the enum map does not
know, a counter that overflows into a second byte, a status key the appliance omits, a
control whose range depends on the model, and a command that the cloud refuses.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.holabrain.const import DEFAULT_SCAN_INTERVAL, DOMAIN
from custom_components.holabrain.registry import (
    CATEGORIES,
    ButtonSpec,
    CategorySpec,
    SelectSpec,
)
from tests.conftest import DISHWASHER_CODE, FakeCloud


async def _poll(hass: HomeAssistant) -> None:
    """Advance past one polling interval and settle the loop."""
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 5)
    )
    await hass.async_block_till_done()


# --- sensors ------------------------------------------------------------------------------


async def test_enum_sensor_maps_known_codes_to_stable_labels(
    hass: HomeAssistant, setup_integration, entity_id_of
) -> None:
    """Enum sensors must publish the translated label, not the raw cloud code.

    The code is meaningless to a user and unusable in an automation; changing it later would
    also silently break every condition that matched on it.
    """
    assert await setup_integration()

    stage = hass.states.get(entity_id_of("sensor", f"{DISHWASHER_CODE}_washingState"))
    assert stage.state == "main_wash"
    assert "main_wash" in stage.attributes["options"]
    program = hass.states.get(entity_id_of("sensor", f"{DISHWASHER_CODE}_modeEU"))
    assert program.state == "eco"


@pytest.mark.parametrize("raw", ["99", "", "-1", "0x2", "null"])
async def test_enum_sensor_survives_a_code_outside_its_map(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of, raw: str
) -> None:
    """An unmapped code must become unknown, never an invalid enum state.

    Firmware updates introduce new programme and fault codes without warning. Passing one
    through would make Home Assistant reject the state (the value is not in ``options``) and
    log an error on every single update from then on.
    """
    assert await setup_integration()
    entity_id = entity_id_of("sensor", f"{DISHWASHER_CODE}_washingState")

    cloud.set_attr(DISHWASHER_CODE, washingState=raw)
    await _poll(hass)

    state = hass.states.get(entity_id)
    assert state.state == "unknown"
    assert raw not in state.attributes["options"]


async def test_a_status_key_the_appliance_omits_reads_unknown(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """A missing key must not fall back to the previous value.

    Sensors that keep their last reading through a key disappearing report a fault code that
    was cleared minutes ago — the single most misleading thing an appliance sensor can do.
    """
    assert await setup_integration()
    entity_id = entity_id_of("sensor", f"{DISHWASHER_CODE}_faultCode")
    assert hass.states.get(entity_id).state == "none"

    del cloud.states[DISHWASHER_CODE]["faultCode"]
    cloud.states[DISHWASHER_CODE] = dict(cloud.states[DISHWASHER_CODE])
    await _poll(hass)

    assert hass.states.get(entity_id).state == "unknown"


@pytest.mark.parametrize(
    ("high", "low", "expected"),
    [
        ("0", "95", "95"),  # under one byte
        ("1", "0", "256"),  # the low byte wrapped exactly once
        ("1", "200", "456"),  # wrapped, with a remainder
        ("9", "255", "2559"),  # a long delayed start
        (None, "42", "42"),  # models that only report the low byte
    ],
)
async def test_remaining_time_reassembles_both_bytes(
    hass: HomeAssistant,
    setup_integration,
    cloud: FakeCloud,
    entity_id_of,
    high,
    low,
    expected,
) -> None:
    """Remaining time is split across two keys and must be recombined, not truncated.

    Reading only the low byte makes a 4-hour eco programme report 40 minutes — and every
    countdown jumps back to 255 whenever the byte wraps.
    """
    assert await setup_integration()
    entity_id = entity_id_of("sensor", f"{DISHWASHER_CODE}_remainTimeL")

    state = dict(cloud.states[DISHWASHER_CODE])
    state.pop("remainTimeH", None)
    if high is not None:
        state["remainTimeH"] = high
    state["remainTimeL"] = low
    cloud.states[DISHWASHER_CODE] = state
    await _poll(hass)

    assert hass.states.get(entity_id).state == expected


@pytest.mark.parametrize("garbage", ["", "--", "n/a", "ff"])
async def test_remaining_time_rejects_non_numeric_input(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of, garbage: str
) -> None:
    """Non-numeric time fields must yield unknown instead of raising.

    A ``ValueError`` inside a property is not caught anywhere useful: it breaks the whole
    entity update, taking every other sensor of that device down with it.
    """
    assert await setup_integration()
    entity_id = entity_id_of("sensor", f"{DISHWASHER_CODE}_remainTimeL")

    cloud.set_attr(DISHWASHER_CODE, remainTimeL=garbage, remainTimeH=garbage)
    await _poll(hass)

    assert hass.states.get(entity_id).state == "unknown"


async def test_statistics_counters_exist_but_stay_disabled(
    hass: HomeAssistant, setup_integration, entity_id_of
) -> None:
    """Lifetime counters are registered yet disabled, so they cost nothing until wanted.

    Enabling them by default would add long-term-statistics rows for every user; not
    registering them at all would make them unreachable without a code change.
    """
    assert await setup_integration()
    entity_id = entity_id_of("sensor", f"{DISHWASHER_CODE}_totalWaterVol")

    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(entity_id) is None


# --- binary sensors -----------------------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [("0", "on"), ("1", "off")])
async def test_door_sensor_uses_the_appliance_polarity_not_the_intuitive_one(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of, raw, expected
) -> None:
    """The door reports 0 for *open*; inverting that is a one-character mistake.

    An inverted door sensor turns "notify me when the dishwasher is open" into a
    notification on every completed cycle, and disables the opposite automation entirely.
    """
    assert await setup_integration()
    entity_id = entity_id_of("binary_sensor", f"{DISHWASHER_CODE}_doorstatus")

    cloud.set_attr(DISHWASHER_CODE, doorstatus=raw)
    await _poll(hass)

    assert hass.states.get(entity_id).state == expected


async def test_binary_sensor_without_its_key_is_unknown_not_off(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """A missing problem flag must not read as "no problem".

    Defaulting a ``PROBLEM`` sensor to off is how a low-salt warning disappears the moment
    the appliance truncates its status.
    """
    assert await setup_integration()
    entity_id = entity_id_of("binary_sensor", f"{DISHWASHER_CODE}_salt")

    state = dict(cloud.states[DISHWASHER_CODE])
    state.pop("salt")
    cloud.states[DISHWASHER_CODE] = state
    await _poll(hass)

    assert hass.states.get(entity_id).state == "unknown"


# --- switches -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0", "off"), ("1", "on"), ("2", "on"), ("3", "on"), ("5", "on"), ("4", "off")],
)
async def test_power_switch_recognises_every_powered_on_code(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of, raw, expected
) -> None:
    """Power is not a boolean on this appliance — it is the state itself.

    ``0`` is off, ``1``/``5`` are standby, ``2`` is a delayed start and ``3`` is a running
    wash. Only ``0`` means off: an appliance counting down to a reserved start is switched
    on and waiting, and reporting it as off makes "turn the dishwasher off when it finishes"
    fire in the middle of the reservation.

    Treating only ``1`` as on would also make the switch flip itself off the moment a cycle
    starts, so every automation waiting for "power on" fires twice.
    """
    assert await setup_integration()
    entity_id = entity_id_of("switch", f"{DISHWASHER_CODE}_power")

    cloud.set_attr(DISHWASHER_CODE, power=raw)
    await _poll(hass)

    assert hass.states.get(entity_id).state == expected


async def test_switch_sends_the_exact_instruction_the_descriptor_declares(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """Off is not "the opposite of on" here: pausing uses its own code.

    The running switch turns off with ``runState: 2`` (pause); sending ``0`` would be either
    ignored or interpreted as something else entirely.
    """
    assert await setup_integration()
    entity_id = entity_id_of("switch", f"{DISHWASHER_CODE}_runState")

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    assert cloud.instructions[-1] == (DISHWASHER_CODE, {"runState": "2"})

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    assert cloud.instructions[-1] == (DISHWASHER_CODE, {"runState": "1"})


async def test_controls_of_an_offline_device_are_not_callable(
    hass: HomeAssistant, setup_integration, push, cloud: FakeCloud, entity_id_of
) -> None:
    """Commands must not reach an appliance that is known to be offline.

    Home Assistant skips unavailable entities when dispatching a service call, so this only
    holds while availability is wired to the device's online flag. Lose that wiring and every
    press against a powered-down appliance becomes a request that can only time out — and an
    optimistic state update that is simply wrong.
    """
    assert await setup_integration()
    entity_id = entity_id_of("switch", f"{DISHWASHER_CODE}_runState")
    await push(f"eu/eu_{DISHWASHER_CODE}/dev", {"onlineChange": {"online": 0}})
    assert hass.states.get(entity_id).state == "unavailable"
    sent = len(cloud.instructions)

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )

    assert len(cloud.instructions) == sent


# --- numbers ------------------------------------------------------------------------------


async def test_number_reports_the_current_gear_as_a_number(
    hass: HomeAssistant, setup_integration, entity_id_of
) -> None:
    """Gear values arrive as strings and must be published numerically."""
    assert await setup_integration()

    state = hass.states.get(entity_id_of("number", f"{DISHWASHER_CODE}_distributorGear"))
    assert float(state.state) == 3.0


async def test_number_sends_a_whole_gear_even_for_a_fractional_request(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """Gears are discrete; a fractional value must not reach the appliance.

    Home Assistant will happily pass ``2.5`` through a slider, and the appliance answers a
    non-integer with a rejection — or, worse, with an unrelated gear.
    """
    assert await setup_integration()
    entity_id = entity_id_of("number", f"{DISHWASHER_CODE}_distributorGear")

    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": 2.5}, blocking=True
    )

    assert cloud.instructions[-1] == (DISHWASHER_CODE, {"distributorGear": "2"})


@pytest.mark.parametrize("garbage", ["", "auto", "--"])
async def test_number_with_a_non_numeric_reading_is_unknown(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of, garbage: str
) -> None:
    """A gear the appliance reports as text must not raise inside the entity."""
    assert await setup_integration()
    entity_id = entity_id_of("number", f"{DISHWASHER_CODE}_distributorGear")

    cloud.set_attr(DISHWASHER_CODE, distributorGear=garbage)
    await _poll(hass)

    assert hass.states.get(entity_id).state == "unknown"


# --- select and button (generic platforms, exercised through a synthetic category) ---------

_SYNTHETIC_TYPE = "0x77"
_SYNTHETIC_CODE = "770011223344556"

_SYNTHETIC = CategorySpec(
    device_type=_SYNTHETIC_TYPE,
    category="synthetic",
    primary_platform=None,
    selects=(
        SelectSpec(
            key="mode",
            translation_key="program",
            command_key="mode",
            options={"eco": "4", "rapid": "7"},
            extra_command={"power": "1"},
        ),
        SelectSpec(
            key="altWash",
            translation_key="alternate_wash",
            command_key="altWash",
            options_from_capability="alternate_wash",
        ),
    ),
    buttons=(ButtonSpec(key="startCmd", translation_key="start", command={"runState": "1"}),),
)


@pytest.fixture
def synthetic_category(monkeypatch: pytest.MonkeyPatch):
    """Register a throwaway category so the generic select/button code is covered.

    The shipped categories currently declare no static select or button, yet both platforms
    are generic and must keep working the moment one does.
    """
    monkeypatch.setitem(CATEGORIES, _SYNTHETIC_TYPE, _SYNTHETIC)
    return _SYNTHETIC


@pytest.fixture
def cloud_with_synthetic(cloud: FakeCloud, synthetic_category):
    cloud.devices.clear()
    cloud.states.clear()
    cloud.devices.append(
        {
            "thingCode": _SYNTHETIC_CODE,
            "thingName": "Synthetic",
            "deviceType": _SYNTHETIC_TYPE,
            "model": "SYNTH001",
            "sn8": "SYNTH001",
            "online": 1,
            "firmwareVersion": "1",
        }
    )
    cloud.states[_SYNTHETIC_CODE] = {"mode": "4", "altWash": "upper", "online": 1}
    return cloud


async def test_select_publishes_labels_and_sends_codes(
    hass: HomeAssistant, cloud_with_synthetic: FakeCloud, setup_integration, entity_id_of
) -> None:
    """A select shows human labels and must translate them back to the cloud code.

    Sending the label instead of the code is silently accepted by the transport and rejected
    (or ignored) by the appliance.
    """
    assert await setup_integration()
    entity_id = entity_id_of("select", f"{_SYNTHETIC_CODE}_mode")

    state = hass.states.get(entity_id)
    assert state.state == "eco"
    assert state.attributes["options"] == ["eco", "rapid"]

    await hass.services.async_call(
        "select", "select_option", {"entity_id": entity_id, "option": "rapid"}, blocking=True
    )
    assert cloud_with_synthetic.instructions[-1] == (
        _SYNTHETIC_CODE,
        {"mode": "7", "power": "1"},
    )


async def test_select_reports_unknown_for_a_code_it_cannot_name(
    hass: HomeAssistant, cloud_with_synthetic: FakeCloud, setup_integration, entity_id_of
) -> None:
    """A programme outside the option list must read unknown, not the wrong label."""
    assert await setup_integration()
    entity_id = entity_id_of("select", f"{_SYNTHETIC_CODE}_mode")

    cloud_with_synthetic.set_attr(_SYNTHETIC_CODE, mode="12")
    await _poll(hass)

    assert hass.states.get(entity_id).state == "unknown"


async def test_capability_backed_select_offers_nothing_when_the_model_is_unknown(
    hass: HomeAssistant, cloud_with_synthetic: FakeCloud, setup_integration, entity_id_of
) -> None:
    """A select whose options come from the model must stay empty rather than guess.

    Publishing a default option list for an unknown model lets the user pick a programme the
    appliance does not have; an empty list is honest and harmless.
    """
    assert await setup_integration()

    state = hass.states.get(entity_id_of("select", f"{_SYNTHETIC_CODE}_altWash"))
    assert state.attributes["options"] == []
    assert state.state == "unknown"


async def test_button_sends_its_fixed_instruction_once(
    hass: HomeAssistant, cloud_with_synthetic: FakeCloud, setup_integration, entity_id_of
) -> None:
    """A button press must send exactly one instruction, exactly as declared."""
    assert await setup_integration()
    entity_id = entity_id_of("button", f"{_SYNTHETIC_CODE}_startCmd")

    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )

    assert cloud_with_synthetic.instructions == [(_SYNTHETIC_CODE, {"runState": "1"})]


# --- cross-platform invariants ------------------------------------------------------------


async def test_every_entity_of_an_offline_device_is_unavailable_at_once(
    hass: HomeAssistant, setup_integration, push, entity_id_of
) -> None:
    """Availability is defined on the shared base entity, so it must hold for all platforms.

    A platform that overrides ``available`` without calling up the chain keeps showing stale
    values while its siblings correctly report unavailable.
    """
    assert await setup_integration()
    registry = er.async_get(hass)
    holabrain_entities = [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.platform == DOMAIN and not entry.disabled_by
    ]
    assert len(holabrain_entities) >= 5

    await push(f"eu/eu_{DISHWASHER_CODE}/dev", {"onlineChange": {"online": 0}})

    assert {hass.states.get(eid).state for eid in holabrain_entities} == {"unavailable"}


async def test_unsupported_device_types_produce_no_entities_at_all(
    hass: HomeAssistant, cloud: FakeCloud, setup_integration
) -> None:
    """An appliance category that is not modelled must be skipped silently.

    Accounts contain devices this integration knows nothing about; raising (or inventing a
    default entity set) would break setup for everyone who owns one.
    """
    cloud.devices.append(
        {
            "thingCode": "000011112222333",
            "thingName": "Unknown Appliance",
            "deviceType": "0xFE",
            "model": "UNKN0001",
            "sn8": "UNKN0001",
            "online": 1,
        }
    )
    cloud.states["000011112222333"] = {"online": 1}

    assert await setup_integration()

    registry = er.async_get(hass)
    assert not [
        entry
        for entry in registry.entities.values()
        if entry.unique_id.startswith("000011112222333_")
    ]


async def test_native_platforms_stay_empty_for_a_composite_category(
    hass: HomeAssistant, setup_integration
) -> None:
    """A dishwasher must not accidentally acquire a light, climate or water-heater entity.

    The native platforms select devices by their category's primary platform; a wrong
    comparison there gives every appliance in the account a climate card.
    """
    assert await setup_integration()

    for platform in (Platform.LIGHT, Platform.CLIMATE, Platform.WATER_HEATER):
        assert hass.states.async_entity_ids(platform.value) == []
