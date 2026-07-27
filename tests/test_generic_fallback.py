"""An appliance the integration does not model yet must not be invisible.

Without a fallback such an appliance produces no device and no entity — only a repair
issue — so "my fridge is on the account and Home Assistant ignores it" reads as a broken
setup rather than a missing feature, and the user cannot tell whether the integration can
even reach it.

The fallback is deliberately timid: raw keys, no units, no device classes, everything
disabled by default, and nothing writable. A guessed unit or a guessed command is worse
than no entity at all.
"""

from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.holabrain.const import DOMAIN
from custom_components.holabrain.generic import MAX_GENERIC_SENSORS, generic_status_keys
from tests.conftest import FakeCloud, device_entry

FRIDGE_CODE = "880011223344556"


def _add_fridge(cloud: FakeCloud, **status) -> None:
    cloud.devices.append(device_entry(FRIDGE_CODE, "Fridge", "0xCA", "310A056C"))
    cloud.capabilities["310A056C"] = []
    cloud.states[FRIDGE_CODE] = {
        "online": 1,
        "power": "1",
        "roomTemp": "4",
        "freezerTemp": "-18",
        **status,
    }


def test_envelope_fields_are_not_offered_as_readings() -> None:
    """They describe the message, not the appliance, and change on every single frame."""
    keys = generic_status_keys(
        {"power": "1", "messageNo": "abc", "clientId": "x", "timeStamp": "1", "online": 1}
    )

    assert keys == ["power"]


def test_a_pathological_frame_cannot_fill_the_entity_registry() -> None:
    """A firmware reporting hundreds of fields must not create hundreds of entities."""
    keys = generic_status_keys({f"field{i}": i for i in range(500)})

    assert len(keys) == MAX_GENERIC_SENSORS


async def test_an_unmodelled_appliance_still_becomes_a_device(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """The user has to be able to see that the appliance was found at all."""
    _add_fridge(cloud)

    assert await setup_integration()

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, FRIDGE_CODE)})
    assert device is not None
    assert device.name == "Fridge"


async def test_its_readings_are_offered_but_not_presented(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """Registered so they are reachable, disabled so they are not asserted.

    The keys are raw cloud names of unknown meaning and scale. Enabling one is the user
    saying "I recognise this number", which is also the evidence needed to model the
    category for real.
    """
    _add_fridge(cloud)
    assert await setup_integration()
    registry = er.async_get(hass)

    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{FRIDGE_CODE}_freezerTemp")
    assert entity_id is not None
    entry = registry.async_get(entity_id)
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entry.entity_category is EntityCategory.DIAGNOSTIC

    # No claim is made about what the number means.
    assert entry.unit_of_measurement is None
    assert entry.device_class is None
    assert entry.original_device_class is None


async def test_nothing_writable_is_created_for_it(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """A command built from a guessed key is a way to put an appliance into a bad state."""
    _add_fridge(cloud)
    assert await setup_integration()

    writable = [
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if FRIDGE_CODE in (entry.unique_id or "")
        and entry.domain in ("switch", "select", "number", "button", "climate", "water_heater")
    ]
    assert writable == []


async def test_a_modelled_appliance_gets_no_raw_duplicates(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """The fallback is for unknown categories only.

    Adding raw twins next to the modelled entities would double every device page and make
    "which of these two temperatures is right" a question users have to answer.
    """
    from tests.conftest import DISHWASHER_CODE

    assert await setup_integration()

    raw = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{DISHWASHER_CODE}_doorstatus"
    )
    assert raw is None


async def test_an_oversized_value_does_not_break_the_update(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """Home Assistant rejects a state over 255 characters, failing the whole update.

    Some appliances put a payload blob in the status; one of those must not take every
    other reading of that device down with it.
    """
    _add_fridge(cloud, blob="x" * 400)
    assert await setup_integration()
    registry = er.async_get(hass)

    blob = registry.async_get_entity_id("sensor", DOMAIN, f"{FRIDGE_CODE}_blob")
    temp = registry.async_get_entity_id("sensor", DOMAIN, f"{FRIDGE_CODE}_roomTemp")
    for entity_id in (blob, temp):
        registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(
        registry.async_get(temp).config_entry_id
    )
    await hass.async_block_till_done()

    assert hass.states.get(blob).state == "unknown"
    assert hass.states.get(temp).state == "4"
