"""The two actions that change the account itself: rename and unbind.

Everything else the integration does is reversible from Home Assistant. These are not:
``unbind_device`` detaches the appliance from the account, and putting it back needs the
appliance's setup mode — a button on the appliance. So the guards around them are the
feature, and this is what tests them.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from custom_components.holabrain.const import DOMAIN
from tests.conftest import DISHWASHER_CODE, FakeCloud


def _device_id(hass: HomeAssistant, thing_code: str = DISHWASHER_CODE) -> str:
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, thing_code)})
    assert device is not None
    return device.id


async def test_rename_reaches_the_account_and_comes_back(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """Renaming must change the name in the account, not only in Home Assistant.

    A rename that stays local looks like it worked until the next scan silently reverts it.
    """
    assert await setup_integration()

    await hass.services.async_call(
        DOMAIN,
        "rename_device",
        {"device_id": _device_id(hass), "name": "Kitchen dishwasher"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert cloud.renamed == [(DISHWASHER_CODE, "Kitchen dishwasher")]


async def test_unbind_refuses_to_run_without_confirmation(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """The confirmation flag is the only thing between a mistyped automation and the loss
    of an appliance that then needs physical access to add back."""
    assert await setup_integration()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "unbind_device", {"device_id": _device_id(hass)}, blocking=True
        )

    assert cloud.unbound == []
    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, DISHWASHER_CODE)})


async def test_confirmed_unbind_removes_the_appliance_everywhere(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """A removed appliance must also disappear from Home Assistant.

    Leaving it behind produces a permanently unavailable device with a dozen dead entities
    that still show up in pickers and automations.
    """
    assert await setup_integration()
    device_id = _device_id(hass)

    await hass.services.async_call(
        DOMAIN,
        "unbind_device",
        {"device_id": device_id, "confirm": True},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert cloud.unbound == [DISHWASHER_CODE]
    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, DISHWASHER_CODE)}) is None


async def test_an_unbind_the_account_did_not_perform_is_reported_as_a_failure(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """The cloud answers with what it actually removed, which can be nothing.

    Treating the request as successful would delete a device from Home Assistant that is
    still bound to the account — and it would come back on the next scan, without its
    customisations.
    """
    assert await setup_integration()
    cloud.unbind_refuses = {DISHWASHER_CODE}

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "unbind_device",
            {"device_id": _device_id(hass), "confirm": True},
            blocking=True,
        )

    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, DISHWASHER_CODE)})


async def test_actions_reject_a_device_that_is_not_an_appliance(
    hass: HomeAssistant, setup_integration
) -> None:
    """A device id from another integration must not be acted upon."""
    assert await setup_integration()
    registry = dr.async_get(hass)
    entries = list(hass.config_entries.async_entries(DOMAIN))
    foreign = registry.async_get_or_create(
        config_entry_id=entries[0].entry_id,
        identifiers={("other_domain", "whatever")},
        name="Not ours",
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "rename_device",
            {"device_id": foreign.id, "name": "nope"},
            blocking=True,
        )
