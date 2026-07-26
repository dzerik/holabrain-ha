"""The optional sidebar panel.

What matters here is the contract with the user: nothing appears in the sidebar unless
they ask for it, the toggle takes effect without reloading the appliances, and removing
the integration leaves no orphan panel behind. The frontend itself is plain Home
Assistant client code and is not exercised from Python.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.holabrain.const import (
    CONF_PANEL,
    PANEL_MODULE,
    PANEL_STATIC_PATH,
    PANEL_URL_PATH,
)

_PANELS = "frontend_panels"


def _panel_registered(hass: HomeAssistant) -> bool:
    return PANEL_URL_PATH in hass.data.get(_PANELS, {})


async def test_panel_is_opt_in(hass: HomeAssistant, setup_integration) -> None:
    """A fresh entry adds entities only — the sidebar stays untouched."""
    assert await setup_integration()

    assert not _panel_registered(hass)


async def test_panel_follows_the_option(
    hass: HomeAssistant, config_entry: MockConfigEntry, setup_integration
) -> None:
    """Flipping the option adds and removes the panel without reloading the entry."""
    assert await setup_integration()
    coordinator = config_entry.runtime_data.coordinator

    hass.config_entries.async_update_entry(config_entry, options={CONF_PANEL: True})
    await hass.async_block_till_done()
    assert _panel_registered(hass)

    hass.config_entries.async_update_entry(config_entry, options={CONF_PANEL: False})
    await hass.async_block_till_done()
    assert not _panel_registered(hass)
    # Same coordinator instance: the toggle must not tear the appliances down.
    assert config_entry.runtime_data.coordinator is coordinator


async def test_panel_removed_on_unload(
    hass: HomeAssistant, config_entry: MockConfigEntry, setup_integration
) -> None:
    """Removing the integration takes its sidebar entry with it."""
    assert await setup_integration()
    hass.config_entries.async_update_entry(config_entry, options={CONF_PANEL: True})
    await hass.async_block_till_done()
    assert _panel_registered(hass)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not _panel_registered(hass)


async def test_card_assets_served_without_the_panel(
    hass: HomeAssistant, setup_integration, hass_client
) -> None:
    """The dashboard card must work while the sidebar panel is switched off."""
    assert await setup_integration()
    assert not _panel_registered(hass)

    client = await hass_client()
    # Both entry points must be reachable: the panel module and the card module a user
    # adds as a dashboard resource.
    for module in (PANEL_MODULE, "holabrain-card.js"):
        response = await client.get(f"{PANEL_STATIC_PATH}/{module}")
        assert response.status == 200, module
        assert "define(" in await response.text(), module


async def test_options_flow_sets_the_option(
    hass: HomeAssistant, config_entry: MockConfigEntry, setup_integration
) -> None:
    """The option is reachable from the UI, not just from YAML-less internals.

    Options open on a menu, because scanning the account lives next to the panel toggle and
    must not be something the user can trip over while flipping a display setting.
    """
    assert await setup_integration()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] == "menu"
    assert set(result["menu_options"]) == {"panel", "scan", "add_appliance"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "panel"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_PANEL: True}
    )
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert config_entry.options[CONF_PANEL] is True
    assert _panel_registered(hass)
