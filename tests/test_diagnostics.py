"""Diagnostics: what a user is asked to attach to an issue must be safe to attach.

Two things matter here and nothing else does: the dump has to contain the raw appliance
payload (that is the only reason to ask for it), and it must not contain the account, the
password, the session token or the appliance's identity.
"""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
    get_diagnostics_for_device,
)

from custom_components.holabrain.const import DOMAIN
from tests.conftest import DISHWASHER_CODE, DISHWASHER_MODEL, FakeCloud, device_entry

# An appliance category the registry does not model. Its raw record is exactly what a
# diagnostics report is for.
UNKNOWN_CODE = "770011223344556"


async def test_config_entry_diagnostics_are_useful_and_redacted(
    hass: HomeAssistant,
    hass_client,
    setup_integration,
    config_entry: MockConfigEntry,
    cloud: FakeCloud,
) -> None:
    """The dump keeps the appliance payload and drops everything identifying."""
    assert await setup_integration()

    result = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)
    dumped = json.dumps(result)

    # Useful: the raw account record, the resolved capabilities and the live status are all
    # there, which is what makes a report actionable without a round trip.
    (appliance,) = result["devices"].values()
    assert appliance["device_type"] == "0xE1"
    assert appliance["supported"] is True
    assert appliance["model"] == DISHWASHER_MODEL
    assert "rinse_aid" in appliance["capabilities"]["features"]
    assert appliance["status"]["washingState"] == "2"
    assert appliance["raw"]["pluginType"] == 1

    # Safe: no credential, no session, no appliance id, anywhere in the document.
    assert cloud.password not in dumped
    assert cloud.account not in dumped
    assert cloud.token not in dumped
    assert DISHWASHER_CODE not in dumped
    assert result["entry"]["data"]["password"] == "**REDACTED**"
    assert result["entry"]["data"]["account"] == "**REDACTED**"
    assert result["entry"]["data"]["session"] == "**REDACTED**"
    assert appliance["raw"]["thingCode"] == "**REDACTED**"


async def test_diagnostics_report_an_unsupported_appliance(
    hass: HomeAssistant, hass_client, setup_integration, config_entry: MockConfigEntry,
    cloud: FakeCloud,
) -> None:
    """An appliance with no category must still be described in full.

    This is the case the report exists for: the appliance produces no device and no entity,
    so its raw record is the only thing that can be used to add support for it.
    """
    cloud.devices.append(device_entry(UNKNOWN_CODE, "Mystery", "0xFF", "UNKN0001"))
    cloud.capabilities["UNKN0001"] = []
    cloud.states[UNKNOWN_CODE] = {"power": "1", "online": 1, "someUnknownKey": "7"}

    assert await setup_integration()

    result = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)
    unknown = next(
        report for report in result["devices"].values() if report["device_type"] == "0xFF"
    )
    assert unknown["supported"] is False
    assert unknown["category"] is None
    assert unknown["status"]["someUnknownKey"] == "7"


async def test_device_diagnostics_cover_one_appliance(
    hass: HomeAssistant, hass_client, setup_integration, config_entry: MockConfigEntry,
    cloud: FakeCloud,
) -> None:
    """A per-device dump must describe that appliance and no other."""
    cloud.add_lamp()
    assert await setup_integration()

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, DISHWASHER_CODE)})
    assert device is not None

    result = await get_diagnostics_for_device(hass, hass_client, config_entry, device)

    assert result["device_type"] == "0xE1"
    assert result["status"]["washingState"] == "2"
    assert DISHWASHER_CODE not in json.dumps(result)
    # The pseudonym is stable, so a device report can be matched against the entry report.
    entry_report = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)
    assert entry_report["devices"][result["id"]]["device_type"] == "0xE1"
