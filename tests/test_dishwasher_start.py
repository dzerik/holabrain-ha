"""Starting a dishwasher cycle.

The appliance accepts a wash only as one whole instruction — programme, extra option, wash
zone and run state together. Anything sent piecemeal is ignored or refused, so the controls
stage their values and the start button submits them. These tests assert on the exact
instruction that reaches the cloud, because that is the part the appliance judges.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.holabrain.const import DEFAULT_SCAN_INTERVAL
from tests.conftest import DISHWASHER_CODE, FakeCloud

PROGRAM = "select.dishwasher_programme"
START = "button.dishwasher_start_cycle"

# A second appliance whose model advertises everything, used where the plain fixture model
# deliberately has nothing to offer.
FULL_CODE = "153931628400001"
FULL_MODEL = "FULLFEAT"
FULL_PROGRAM = "select.loaded_dishwasher_programme"
FULL_EXTRA = "select.loaded_dishwasher_extra_option"
FULL_ZONE = "select.loaded_dishwasher_wash_zone"
FULL_START = "button.loaded_dishwasher_start_cycle"

FULL_CAPABILITY = [
    "rinse_aid",
    {"rinse_aid_gear": "5"},
    "salt",
    {"salt_gear": "6"},
    "statistics",
    "auto_open",
    "extra_drying",
    "half",
    "power_wash",
    "turbo_speed",
    {"alternate_wash": ["upper", "lower", "upper_and_lower"]},
    # This model offers only three of the category's programmes.
    {
        "mode": ["eco", "rapid", "intensive"],
        "modelValue": [{"eco": "4"}, {"rapid": "7"}, {"intensive": "2"}],
    },
]


def _add_full_dishwasher(cloud: FakeCloud) -> None:
    cloud.add_dishwasher(
        thing_code=FULL_CODE,
        name="Loaded Dishwasher",
        model=FULL_MODEL,
        capability=FULL_CAPABILITY,
    )
    cloud.set_attr(FULL_CODE, doorstatus="1")


async def _select(hass: HomeAssistant, entity_id: str, option: str) -> None:
    await hass.services.async_call(
        "select", "select_option", {"entity_id": entity_id, "option": option}, blocking=True
    )
    await hass.async_block_till_done()


async def _press(hass: HomeAssistant, entity_id: str = START) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()


async def _poll(hass: HomeAssistant) -> None:
    """Force a status refresh so a change made on the cloud reaches the entities."""
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 5)
    )
    await hass.async_block_till_done()


def _last_instruction(cloud: FakeCloud) -> dict:
    assert cloud.instructions, "no instruction reached the cloud"
    return cloud.instructions[-1][1]


async def test_composing_a_cycle_sends_one_instruction(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """Programme, extra and zone must arrive together with the run state, not one by one."""
    _add_full_dishwasher(cloud)
    assert await setup_integration()

    await _select(hass, FULL_PROGRAM, "rapid")
    await _select(hass, FULL_EXTRA, "extra_drying")
    await _select(hass, FULL_ZONE, "upper")
    before = len(cloud.instructions)
    await _press(hass, FULL_START)

    # Staging is local: exactly one instruction is sent, by the button.
    assert len(cloud.instructions) == before + 1
    assert _last_instruction(cloud) == {
        "modeEU": "7",
        "addFuncEU": "1",
        "washArea": "1",
        "power": "3",
    }


async def test_selecting_a_programme_does_not_touch_the_appliance(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """Choosing a programme is a local decision until the user presses start.

    Sending it immediately would either be ignored or would change a running cycle by
    accident — the appliance has no notion of "selected but not started".
    """
    assert await setup_integration()
    before = len(cloud.instructions)

    await _select(hass, PROGRAM, "eco")

    assert len(cloud.instructions) == before
    assert hass.states.get(PROGRAM).state == "eco"


async def test_starting_without_a_programme_is_refused_locally(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """A start with no programme must fail with a clear message, not a cloud error."""
    assert await setup_integration()
    cloud.states[DISHWASHER_CODE].pop("modeEU", None)
    cloud.set_attr(DISHWASHER_CODE, doorstatus="1")
    await _poll(hass)

    before = len(cloud.instructions)
    with pytest.raises(ServiceValidationError):
        await _press(hass)

    assert len(cloud.instructions) == before


async def test_starting_with_the_door_open_is_refused_before_the_request(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """The appliance refuses to start with the door open; say so instead of asking it.

    Sending the command anyway costs an account request and returns an opaque cloud error
    seconds later, which the user cannot act on.
    """
    assert await setup_integration()
    cloud.set_attr(DISHWASHER_CODE, doorstatus="0")  # 0 means open
    await _poll(hass)

    await _select(hass, PROGRAM, "eco")
    before = len(cloud.instructions)

    with pytest.raises(ServiceValidationError):
        await _press(hass)

    assert len(cloud.instructions) == before


async def test_the_reported_programme_is_used_when_nothing_was_staged(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """Pressing start without choosing anything must repeat what the appliance shows.

    That is what the panel on the appliance itself does, and it keeps the button useful for
    "run the same cycle again".
    """
    assert await setup_integration()
    cloud.set_attr(DISHWASHER_CODE, modeEU="4", doorstatus="1")
    await _poll(hass)

    await _press(hass)

    assert _last_instruction(cloud)["modeEU"] == "4"


async def test_the_draft_is_dropped_once_the_appliance_owns_the_cycle(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, config_entry
) -> None:
    """After a successful start the appliance is the source of truth again.

    A draft left behind would keep showing the user's old choice over what is really
    running.
    """
    assert await setup_integration()
    cloud.set_attr(DISHWASHER_CODE, doorstatus="1")
    cloud.apply_instructions = True

    await _select(hass, PROGRAM, "intensive")
    await _press(hass)

    coordinator = config_entry.runtime_data.coordinator
    assert coordinator.draft(DISHWASHER_CODE) == {}  # nothing staged is left behind
    assert hass.states.get(PROGRAM).state == "intensive"  # reported by the appliance now


async def test_only_the_extras_the_model_advertises_are_offered(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """Offering an option the model lacks produces a command the appliance refuses."""
    _add_full_dishwasher(cloud)
    # The plain fixture model advertises none of the extras; the loaded one advertises all.
    cloud.capabilities[FULL_MODEL] = [
        item for item in FULL_CAPABILITY if item not in ("power_wash", "turbo_speed")
    ]
    assert await setup_integration()

    options = hass.states.get(FULL_EXTRA).attributes["options"]
    assert "none" in options
    assert "extra_drying" in options
    assert "half_load" in options
    assert "power_wash" not in options
    assert "turbo_speed" not in options


async def test_a_model_with_no_extras_has_no_extra_control(
    hass: HomeAssistant, setup_integration
) -> None:
    """An empty option list would be a dead control; the entity must simply not exist."""
    assert await setup_integration()

    assert hass.states.get("select.dishwasher_extra_option") is None


async def test_only_the_programmes_the_model_has_are_offered(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """The model's own programme table is a subset of the category's, and it wins.

    Offering a programme the appliance does not have produces a command it refuses, and the
    user has no way to tell which of the eleven entries are real.
    """
    _add_full_dishwasher(cloud)
    assert await setup_integration()

    options = hass.states.get(FULL_PROGRAM).attributes["options"]
    assert set(options) == {"eco", "rapid", "intensive"}


async def test_the_wash_zone_control_is_absent_without_the_option(
    hass: HomeAssistant, setup_integration
) -> None:
    """A machine without alternating wash has no zone control, and start omits the key.

    Sending an unsupported key is rejected outright, so it must not appear in the payload.
    """
    assert await setup_integration()

    assert hass.states.get("select.dishwasher_wash_zone") is None


async def test_the_zone_key_is_omitted_for_models_without_the_option(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """The composed instruction must not carry a key the model does not support."""
    assert await setup_integration()
    cloud.set_attr(DISHWASHER_CODE, doorstatus="1")

    await _select(hass, PROGRAM, "eco")
    await _press(hass)

    assert "washArea" not in _last_instruction(cloud)
