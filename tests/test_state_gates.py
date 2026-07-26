"""State gates: a reading that stopped meaning anything must not look live.

An appliance reports every field it owns all the time. A switched-off dishwasher still
carries the last cycle's programme, stage and remaining time — the vendor's own app refuses
to display them and substitutes a table estimate instead. Surfacing them in Home Assistant
is worse than showing nothing: a stale number looks live, and automations act on it.

These tests drive the appliance through real states and assert on what the user sees.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.holabrain.conditions import Is, StateIs, resolve_state
from custom_components.holabrain.const import DEFAULT_SCAN_INTERVAL
from custom_components.holabrain.registry import DISHWASHER_STATES
from tests.conftest import DISHWASHER_CODE, FakeCloud


async def _poll(hass: HomeAssistant) -> None:
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 5)
    )
    await hass.async_block_till_done()


class _Ctx:
    """Bare condition context over a plain dict."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def value(self, key: str) -> str | None:
        return self._values.get(key)

    def program_allows(self, flag: str, exclusion_param: str | None) -> bool:
        return True


# --- the state machine itself -------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({"power": "0", "washingState": "2", "runState": "1"}, "power_off"),
        # A fault outranks a running cycle: the appliance has stopped, whatever runState says.
        ({"power": "3", "faultCode": "8", "runState": "1"}, "fault"),
        ({"power": "1", "runState": "2"}, "standby"),
        ({"power": "2"}, "delay"),
        ({"power": "3", "washingState": "5"}, "finished"),
        ({"power": "3", "washingState": "2", "runState": "1"}, "running"),
        ({"power": "3", "washingState": "2", "runState": "2"}, "pause"),
    ],
)
def test_the_state_machine_resolves_each_real_state(status, expected) -> None:
    """``power`` is the state, not a boolean, and it outranks the run keys."""
    assert resolve_state(DISHWASHER_STATES, _Ctx(status)) == expected


def test_a_frame_without_the_state_keys_decides_nothing() -> None:
    """Push frames carry a subset of the appliance's fields.

    A frame that happens to omit power and runState must leave the state undecided rather
    than fall through to a wrong arm — everything downstream then falls open instead of
    blanking half the device page.
    """
    assert resolve_state(DISHWASHER_STATES, _Ctx({"realTemp": "58"})) is None


def test_fault_zero_is_not_a_fault() -> None:
    """``faultCode`` is present on every frame; only its non-zero codes mean trouble."""
    assert resolve_state(DISHWASHER_STATES, _Ctx({"power": "3", "faultCode": "0",
                                                  "runState": "1"})) == "running"


# --- what the user actually sees ----------------------------------------------------------


async def test_a_switched_off_dishwasher_reports_no_remaining_time(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """The bug this mechanism exists for.

    A dishwasher that has been off for days kept reporting ``remainTimeL`` from its last
    wash, so Home Assistant showed hours "remaining" on an idle appliance — and any
    automation keyed on it fired on a cycle that ended long ago.
    """
    assert await setup_integration()
    remaining = entity_id_of("sensor", f"{DISHWASHER_CODE}_remainTimeL")
    stage = entity_id_of("sensor", f"{DISHWASHER_CODE}_washingState")

    # Mid-wash the countdown is real and must be reported.
    cloud.set_attr(DISHWASHER_CODE, power="3", runState="1", washingState="2",
                   remainTimeL="95")
    await _poll(hass)
    assert hass.states.get(remaining).state == "95"
    assert hass.states.get(stage).state == "main_wash"

    # Switched off, the appliance leaves both values standing. They are leftovers.
    cloud.set_attr(DISHWASHER_CODE, power="0", runState="2", washingState="0",
                   remainTimeL="95")
    await _poll(hass)
    assert hass.states.get(remaining).state == "unknown"
    assert hass.states.get(stage).state == "unknown"


async def test_a_reading_survives_a_frame_that_cannot_decide_the_state(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """Fail-open, and it matters more than it sounds.

    Gates are evaluated against whatever the last frame carried. If an incomplete frame
    blanked every gated reading, a device page would flicker to unknown each time the push
    channel delivered a partial update — far worse than the staleness being prevented.
    """
    assert await setup_integration()
    temperature = entity_id_of("sensor", f"{DISHWASHER_CODE}_realTemp")

    cloud.set_attr(DISHWASHER_CODE, power="3", runState="1", washingState="2",
                   realTemp="58")
    await _poll(hass)
    assert hass.states.get(temperature).state == "58"

    # Drop the keys the state machine needs; the temperature must stay.
    cloud.drop_attrs(DISHWASHER_CODE, "power", "runState", "washingState")
    await _poll(hass)
    assert hass.states.get(temperature).state == "58"


async def test_the_power_control_stays_usable_exactly_when_everything_is_blocked(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """Power is the one control that must ignore the guard.

    Every other command is refused while the appliance is off — but refusing to switch it
    on because it is off is a deadlock, which is why the vendor's own power button disables
    all four checks.
    """
    assert await setup_integration()
    power = entity_id_of("switch", f"{DISHWASHER_CODE}_power")

    cloud.set_attr(DISHWASHER_CODE, power="0", doorstatus="0")
    await _poll(hass)

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": power}, blocking=True
    )
    assert any(
        instruction.get("power") == "1"
        for _, instruction in cloud.instructions
    )


async def test_a_write_is_refused_with_a_reason_not_silently_dropped(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """The guard must raise, never mark the entity unavailable.

    Home Assistant drops unavailable entities from a service call's target list, so an
    automation would be told it succeeded while nothing happened. A refusal the user can
    read is the whole point.
    """
    assert await setup_integration()
    auto_open = entity_id_of("switch", f"{DISHWASHER_CODE}_autoDoorOpen")

    cloud.set_attr(DISHWASHER_CODE, power="0")
    await _poll(hass)

    assert hass.states.get(auto_open).state != "unavailable"
    before = len(cloud.instructions)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": auto_open}, blocking=True
        )
    assert len(cloud.instructions) == before


# --- registry integrity -------------------------------------------------------------------


def test_every_state_a_gate_names_is_a_state_the_machine_can_reach() -> None:
    """A mistyped state name is otherwise invisible.

    Gates fail open, so ``StateIs("runing")`` would not raise — it would silently never
    match, and the reading it guards would stay visible forever in states where it is
    meaningless. Catching it here is the only place it shows up.
    """
    from custom_components.holabrain.registry import CATEGORIES

    for category in CATEGORIES.values():
        declared = {rule.name for rule in category.states}
        referenced: set[str] = set()
        for group in (category.sensors, category.binary_sensors, category.switches,
                      category.selects, category.numbers, category.buttons):
            for spec in group:
                referenced |= spec.gates.state_names()
        for block in category.guard:
            referenced |= block.when.state_names()
        unknown = referenced - declared
        assert not unknown, (
            f"{category.category}: gates reference states the machine never produces: "
            f"{sorted(unknown)}"
        )


def test_a_gate_may_not_blank_a_lifetime_counter() -> None:
    """Long-term statistics read a gap as a counter reset and a reset as a spike."""
    from homeassistant.components.sensor import SensorStateClass

    from custom_components.holabrain.conditions import Gates
    from custom_components.holabrain.registry import SensorSpec

    with pytest.raises(ValueError, match="lifetime total"):
        SensorSpec("totalWaterVol", "total_water",
                   state_class=SensorStateClass.TOTAL_INCREASING,
                   gates=Gates(meaningful_when=StateIs("running")))


def test_a_read_only_sensor_may_not_declare_a_refusal() -> None:
    """Nothing writes to a sensor, so a write-time refusal there is a mistake."""
    from custom_components.holabrain.conditions import Block, Gates
    from custom_components.holabrain.registry import SensorSpec

    with pytest.raises(ValueError, match="read-only"):
        SensorSpec("realTemp", "temperature",
                   gates=Gates(blocks=(Block(Is("power", "0"), "appliance_off"),)))
