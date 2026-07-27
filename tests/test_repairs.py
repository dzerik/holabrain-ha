"""Repair issues for appliances the integration cannot model yet.

An unsupported appliance is otherwise invisible: no device, no entity, no log line anyone
reads. The issue is the only thing that tells the user their oven was seen and skipped —
and it is what turns "nothing happened" into a report that can be acted on.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.holabrain.const import DOMAIN
from custom_components.holabrain.coordinator import ISSUE_UNSUPPORTED
from tests.conftest import DISHWASHER_CODE, FakeCloud, device_entry

UNKNOWN_CODE = "770011223344556"
ISSUE_ID = f"{ISSUE_UNSUPPORTED}_0xFF"


def _add_unknown(cloud: FakeCloud) -> None:
    cloud.devices.append(device_entry(UNKNOWN_CODE, "Mystery", "0xFF", "UNKN0001"))
    cloud.capabilities["UNKN0001"] = []
    cloud.states[UNKNOWN_CODE] = {"power": "1", "online": 1}


async def test_unsupported_appliance_raises_an_issue(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """The category token and the model end up in the issue, because that is what is needed."""
    _add_unknown(cloud)

    assert await setup_integration()

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_placeholders == {
        "device_type": "0xFF",
        "models": "UNKN0001",
    }
    # A modelled appliance on the same account must not produce one.
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_UNSUPPORTED}_0xE1") is None


async def test_the_issue_is_cleared_when_the_appliance_leaves_the_account(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """A warning about an appliance that is gone is worse than no warning at all."""
    _add_unknown(cloud)
    assert await setup_integration()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None

    cloud.devices = [
        device for device in cloud.devices if device["thingCode"] != UNKNOWN_CODE
    ]
    await hass.services.async_call(DOMAIN, "scan_devices", {}, blocking=True)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_an_appliance_paired_later_raises_the_issue_too(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """The check has to run on every inventory read, not only at setup."""
    assert await setup_integration()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None

    _add_unknown(cloud)
    await hass.services.async_call(DOMAIN, "scan_devices", {}, blocking=True)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None
    # The supported appliance is untouched by all of this.
    assert hass.states.async_entity_ids("sensor")
    assert DISHWASHER_CODE in cloud.states


async def test_a_known_type_is_named_in_the_issue_not_just_coded(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """"0xCA" tells the user nothing; "Refrigerator (0xCA)" tells them what was skipped.

    The cloud publishes an appliance-type catalogue, and this is what it is for: a repair
    issue naming a raw token reads like a malfunction rather than like a missing feature.
    """
    cloud.devices.append(device_entry("880011223344556", "Fridge", "0xCA", "310A056C"))
    cloud.capabilities["310A056C"] = []
    cloud.states["880011223344556"] = {"online": 1}

    assert await setup_integration()
    await hass.async_block_till_done()

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_UNSUPPORTED}_0xCA")
    assert issue is not None
    assert issue.translation_placeholders["device_type"] == "Refrigerator (0xCA)"


async def test_an_unavailable_catalogue_still_names_the_type_by_code(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """The catalogue is a nicety and must never be why the user is told nothing at all."""
    cloud.devices.append(device_entry("880011223344556", "Fridge", "0xCA", "310A056C"))
    cloud.capabilities["310A056C"] = []
    cloud.states["880011223344556"] = {"online": 1}
    cloud.fail_next(FakeCloud.CATALOG, TimeoutError("catalogue down"), times=5)

    assert await setup_integration()
    await hass.async_block_till_done()

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_UNSUPPORTED}_0xCA")
    assert issue is not None
    assert issue.translation_placeholders["device_type"] == "0xCA"
