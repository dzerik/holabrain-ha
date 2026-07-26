"""Oven (0xB1): programme composition, runtime gating and derived status.

The oven is the first composite category — no native platform, controls spread over five
platforms, and a cook instruction the cloud only accepts as a whole. These tests cover the
parts that would silently produce a wrong command or a control the appliance cannot honour.
"""

from types import MappingProxyType

import pytest
from homeassistant.const import Platform
from homeassistant.exceptions import ServiceValidationError

from custom_components.holabrain.aiodollin import CapabilityProfile, Device, DeviceState
from custom_components.holabrain.binary_sensor import HolabrainBinarySensor
from custom_components.holabrain.helpers import build_entities
from custom_components.holabrain.oven import build_cook_instruction, build_oven_entities
from custom_components.holabrain.registry import OVEN, OVEN_PROGRAMS
from custom_components.holabrain.sensor import HolabrainSensor

_DEVICE = Device(
    thing_code="o1", name="Oven", device_type="0xB1", model="OVEN0001", online=True
)
_CFG = OVEN.oven

# What the 0xB1 resolver chain lays down for every oven before presence rules run.
_BASE_FEATURES = ("menu_cook", "preheat", "degree", "child_lock")


def _profile(*extra_features: str, params: dict | None = None) -> CapabilityProfile:
    return CapabilityProfile(
        model=_DEVICE.model,
        features=frozenset(_BASE_FEATURES + extra_features),
        params=MappingProxyType(dict(params or {})),
        device_type="0xB1",
    )


class _FakeCoordinator:
    """Minimal stand-in for the real coordinator (no hass, no cloud)."""

    last_update_success = True

    def __init__(
        self,
        attributes: dict | None = None,
        *,
        online: bool = True,
        profile: CapabilityProfile | None = None,
    ) -> None:
        self.devices = {_DEVICE.thing_code: _DEVICE}
        self.data = {
            _DEVICE.thing_code: DeviceState(
                _DEVICE.thing_code, MappingProxyType(attributes or {}), online=online
            )
        }
        self.drafts: dict[str, dict[str, str]] = {}
        self._profile = profile if profile is not None else _profile()
        self.sent: list[tuple[str, dict]] = []

    def capability_for(self, thing_code):
        return self._profile

    def draft(self, thing_code):
        return self.drafts.setdefault(thing_code, {})

    def async_stage(self, thing_code, values):
        self.draft(thing_code).update(values)

    def async_update_listeners(self):
        pass

    def async_clear_draft(self, thing_code):
        self.drafts.pop(thing_code, None)

    async def async_send_instruction(self, thing_code, instruction):
        self.sent.append((thing_code, instruction))

    async def async_request_refresh(self):
        pass


def _entity(entities, translation_key):
    return next(e for e in entities if e.translation_key == translation_key)


# -- cook instruction ---------------------------------------------------------------------


def test_cook_instruction_is_a_single_menu_step_in_seconds():
    instruction = build_cook_instruction(
        _CFG, OVEN_PROGRAMS["conventional"],
        temperature=200, minutes=45, probe=None, preheat=True,
    )
    assert instruction["currentCook"] == "1"
    assert instruction["totalCook"] == "1"
    (step,) = instruction["menu"]
    assert step == {
        "preHeat": "1",
        "cookMode": "21249",
        "cookTime": "2700",  # 45 min as seconds
        "fire": "255",
        "size": "1",
        "nextAction": "1",
        "upperTemp": "200",
        "lowerTemp": "200",
        "probeTemp": "200",  # mirrors the setpoint when no probe target is set
    }


def test_time_only_programme_carries_no_temperature_and_no_preheat():
    (step,) = build_cook_instruction(
        _CFG, OVEN_PROGRAMS["defrost"],
        temperature=180, minutes=10, probe=90, preheat=True,
    )["menu"]
    assert not {"upperTemp", "lowerTemp", "probeTemp"} & set(step)
    assert step["preHeat"] == "0"  # defrost never pre-heats
    assert step["cookTime"] == "600"


def test_probe_target_replaces_the_mirrored_setpoint():
    (step,) = build_cook_instruction(
        _CFG, OVEN_PROGRAMS["convection"],
        temperature=180, minutes=30, probe=72, preheat=False,
    )["menu"]
    assert step["probeTemp"] == "72"
    assert step["upperTemp"] == step["lowerTemp"] == "180"


def test_every_programme_code_is_unique():
    codes = [program.code for program in OVEN_PROGRAMS.values()]
    assert len(set(codes)) == len(codes)


# -- programme composer -------------------------------------------------------------------


async def test_choosing_a_mode_seeds_a_setpoint_inside_that_mode_range():
    coordinator = _FakeCoordinator()
    select = _entity(build_oven_entities(coordinator, Platform.SELECT), "cook_mode")
    temperature = _entity(
        build_oven_entities(coordinator, Platform.NUMBER), "target_temperature"
    )

    coordinator.async_stage("o1", {"upperTemp": "230"})  # left over from another mode
    # keep_warm tops out at 100 °C, so the stale 230 must not survive the switch.
    await select.async_select_option("keep_warm")
    assert temperature.native_value == 75
    assert temperature.native_min_value == 60
    assert temperature.native_max_value == 100
    assert temperature.native_step == 1


def test_temperature_and_preheat_are_unavailable_for_a_time_only_programme():
    coordinator = _FakeCoordinator({"cookMode": "41984"})  # defrost, reported by device
    numbers = build_oven_entities(coordinator, Platform.NUMBER)
    switches = build_oven_entities(coordinator, Platform.SWITCH)
    assert _entity(numbers, "target_temperature").available is False
    assert _entity(switches, "preheat").available is False
    # Duration stays usable — defrost is a time-only programme.
    assert _entity(numbers, "cook_time").available is True


def test_preheat_is_unavailable_for_low_temperature_programmes():
    coordinator = _FakeCoordinator({"cookMode": "41472"})  # eco
    switches = build_oven_entities(coordinator, Platform.SWITCH)
    assert _entity(switches, "preheat").available is False
    assert _entity(build_oven_entities(coordinator, Platform.NUMBER),
                   "target_temperature").available is True


def test_controls_read_back_a_cycle_started_on_the_appliance():
    coordinator = _FakeCoordinator({"cookMode": "21505", "cookTime": "5400"})
    numbers = build_oven_entities(coordinator, Platform.NUMBER)
    select = _entity(build_oven_entities(coordinator, Platform.SELECT), "cook_mode")
    assert select.current_option == "convection"
    assert _entity(numbers, "cook_time").native_value == 90  # 5400 s


def test_food_probe_control_appears_only_on_probe_capable_models():
    plain = build_oven_entities(_FakeCoordinator({"upperTemp": "180"}), Platform.NUMBER)
    assert not [e for e in plain if e.translation_key == "probe_temperature"]

    probed = build_oven_entities(
        _FakeCoordinator({"probeTemp": "70"}, profile=_profile("food_probe")),
        Platform.NUMBER,
    )
    assert _entity(probed, "probe_temperature").native_value == 70


async def test_model_specific_mode_exclusions_override_the_defaults():
    # A model whose profile says convection takes no pre-heat must hide the toggle
    # and must not ask for one in the cook instruction either.
    coordinator = _FakeCoordinator(
        {"cookMode": "21505"},
        profile=_profile(params={"non_preheat_modes": ["convection"]}),
    )
    preheat = _entity(build_oven_entities(coordinator, Platform.SWITCH), "preheat")
    assert preheat.available is False

    coordinator.async_stage("o1", {"preHeat": "1"})  # e.g. left over from another mode
    await _entity(build_oven_entities(coordinator, Platform.BUTTON), "start").async_press()
    (_, instruction), = coordinator.sent
    assert instruction["menu"][0]["preHeat"] == "0"


async def test_start_submits_the_staged_programme_and_then_releases_the_draft():
    coordinator = _FakeCoordinator()
    numbers = build_oven_entities(coordinator, Platform.NUMBER)
    await _entity(build_oven_entities(coordinator, Platform.SELECT),
                  "cook_mode").async_select_option("pizza")
    await _entity(numbers, "target_temperature").async_set_native_value(210)
    await _entity(numbers, "cook_time").async_set_native_value(25)
    await _entity(build_oven_entities(coordinator, Platform.SWITCH), "preheat").async_turn_on()

    await _entity(build_oven_entities(coordinator, Platform.BUTTON), "start").async_press()

    (thing_code, instruction), = coordinator.sent
    assert thing_code == "o1"
    step = instruction["menu"][0]
    assert step["cookMode"] == "22272"
    assert step["upperTemp"] == "210"
    assert step["cookTime"] == "1500"  # 25 min
    assert step["preHeat"] == "1"
    # The appliance owns the programme now; the controls must follow its status again.
    assert not coordinator.drafts.get("o1")


async def test_start_resubmits_exactly_what_the_controls_show():
    # Nothing staged: the instruction must mirror the appliance's own values (and convert
    # the duration back to seconds), not fall back to defaults.
    coordinator = _FakeCoordinator(
        {"cookMode": "21505", "cookTime": "5400", "upperTemp": "170", "preHeat": "1"}
    )
    await _entity(build_oven_entities(coordinator, Platform.BUTTON), "start").async_press()
    (_, instruction), = coordinator.sent
    step = instruction["menu"][0]
    assert step["cookTime"] == "5400"
    assert step["upperTemp"] == "170"
    assert step["preHeat"] == "1"


async def test_start_without_a_programme_is_refused_instead_of_sending_junk():
    coordinator = _FakeCoordinator()
    button = _entity(build_oven_entities(coordinator, Platform.BUTTON), "start")
    with pytest.raises(ServiceValidationError):
        await button.async_press()
    assert coordinator.sent == []


# -- status readout -----------------------------------------------------------------------


def _sensor(coordinator, translation_key):
    return _entity(build_entities(coordinator, "sensors", HolabrainSensor), translation_key)


def test_machine_status_folds_the_preheat_phase_over_the_run_state():
    assert _sensor(_FakeCoordinator({"devstatus": "3", "preheatState": "1"}),
                   "machine_status").native_value == "preheating"
    assert _sensor(_FakeCoordinator({"devstatus": "3", "preheatState": "2"}),
                   "machine_status").native_value == "preheat_finish"
    assert _sensor(_FakeCoordinator({"devstatus": "3", "preheatState": "0"}),
                   "machine_status").native_value == "cooking"
    # A paused cycle reports pause even mid-preheat.
    assert _sensor(_FakeCoordinator({"devstatus": "6", "preheatState": "1"}),
                   "machine_status").native_value == "pause"
    assert _sensor(_FakeCoordinator({"devstatus": "3"}, online=False),
                   "machine_status").native_value == "offline"


def test_machine_status_options_cover_every_value_the_transform_can_return():
    sensor = _sensor(_FakeCoordinator({"devstatus": "2"}), "machine_status")
    for value in ("offline", "off", "standby", "cooking", "preheating",
                  "preheat_finish", "cook_complete", "delay", "pause"):
        assert value in sensor.options


def test_remaining_time_rounds_up_to_whole_minutes():
    sensor = _sensor(_FakeCoordinator({"cookRemainTime": "61"}), "remaining_time")
    assert sensor.native_value == 2  # never shows 0 while the cycle is still running


# -- flags --------------------------------------------------------------------------------


def _binary(coordinator, translation_key):
    return _entity(
        build_entities(coordinator, "binary_sensors", HolabrainBinarySensor), translation_key
    )


def test_fault_flag_is_on_for_any_code_other_than_zero():
    assert _binary(_FakeCoordinator({"faultCode": "0"}), "fault").is_on is False
    assert _binary(_FakeCoordinator({"faultCode": "5"}), "fault").is_on is True
    assert _sensor(_FakeCoordinator({"faultCode": "5"}), "oven_fault").native_value == "e72"


def test_steam_flags_only_exist_on_steam_models():
    dry = _FakeCoordinator({"doorStatus": "0"})
    keys = {
        e.translation_key for e in build_entities(dry, "binary_sensors", HolabrainBinarySensor)
    }
    assert not keys & {"water_tank", "water_low", "water_change"}

    steam = _FakeCoordinator(
        {"doorStatus": "0", "lackOfWaterStatus": "1"}, profile=_profile("steam")
    )
    assert _binary(steam, "water_low").is_on is True


def test_packed_status_integer_is_used_when_discrete_flags_are_absent():
    # bit1 = door open, bit5 = pre-heating, bit6 = pre-heat reached.
    coordinator = _FakeCoordinator({"transportStatusSummary1": str(0b1000010)})
    assert _binary(coordinator, "door").is_on is True
    assert _binary(coordinator, "preheating").is_on is False
    assert _binary(coordinator, "preheat_reached").is_on is True


def test_discrete_flags_win_over_the_packed_integer():
    coordinator = _FakeCoordinator({"doorStatus": "0", "transportStatusSummary1": "2"})
    assert _binary(coordinator, "door").is_on is False
