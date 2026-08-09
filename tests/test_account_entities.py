"""The account device: what it is called, and why that matters.

Its two entities describe the cloud connection rather than any appliance, so they need a
device of their own — hanging them off a dishwasher would be a lie, and they would vanish
the moment that dishwasher was removed from the account.
"""

from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.holabrain.const import DOMAIN
from tests.conftest import FakeCloud


async def test_the_account_device_is_not_named_after_the_email(
    hass: HomeAssistant, setup_integration, config_entry
) -> None:
    """Home Assistant builds entity ids from the device name.

    Naming this device after the account would produce
    ``switch.name_example_com_exclusive_mode`` — putting the user's e-mail address into
    every automation that references it, every log line that mentions it, and every
    screenshot attached to a bug report.
    """
    assert await setup_integration()

    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, f"account_{config_entry.entry_id}")}
    )
    assert device is not None
    assert "@" not in (device.name or "")
    assert config_entry.data["account"] not in (device.name or "")

    registry = er.async_get(hass)
    for uid in ("exclusive_mode", "refresh_now"):
        entity_id = registry.async_get_entity_id(
            "switch" if uid == "exclusive_mode" else "button",
            DOMAIN,
            f"{config_entry.entry_id}_{uid}",
        )
        assert entity_id is not None
        assert "example_com" not in entity_id


async def test_the_account_controls_do_not_belong_to_an_appliance(
    hass: HomeAssistant, setup_integration, config_entry
) -> None:
    """Removing an appliance must not take the connection's own controls with it."""
    assert await setup_integration()
    registry = er.async_get(hass)

    entity_id = registry.async_get_entity_id(
        "switch", DOMAIN, f"{config_entry.entry_id}_exclusive_mode"
    )
    entry = registry.async_get(entity_id)
    account_device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, f"account_{config_entry.entry_id}")}
    )
    assert entry.device_id == account_device.id


async def test_the_token_button_exists_but_is_not_offered_by_default(
    hass: HomeAssistant, setup_integration, config_entry
) -> None:
    """Pressing it signs the user out of the vendor's mobile app.

    That is a reasonable thing to ask for and an unreasonable thing to do by accident, so
    the button is registered but left disabled until someone goes looking for it.
    """
    assert await setup_integration()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "button", DOMAIN, f"{config_entry.entry_id}_refresh_token"
    )
    assert entity_id is not None

    entry = registry.async_get(entity_id)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entry.entity_category is EntityCategory.DIAGNOSTIC


async def test_pressing_the_token_button_reaches_the_same_path_as_the_service(
    hass: HomeAssistant, setup_integration, config_entry, cloud: FakeCloud
) -> None:
    """Existence and disabled-by-default are covered above; nothing proves pressing it
    actually does anything. Enabled like a user would from the entity settings, then pressed
    through the same `button.press` service call Home Assistant itself issues on a tap.
    """
    assert await setup_integration()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "button", DOMAIN, f"{config_entry.entry_id}_refresh_token"
    )
    registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    logins_before = cloud.logins
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )

    assert cloud.logins == logins_before + 1
