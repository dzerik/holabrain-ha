"""The HolaBrain integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .aiodollin import DollinClient
from .aiodollin.transport.http import generate_device_id
from .const import (
    CONF_ACCOUNT,
    CONF_PANEL,
    CONF_REGION,
    CONFIG_ENTRY_VERSION,
    DEFAULT_PANEL,
    DEFAULT_REGION,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import HolabrainCoordinator, async_cleanup_entry_storage
from .http_client import async_create_http_client
from .panel import (
    async_register_panel,
    async_register_static_path,
    async_unregister_panel,
)
from .services import async_setup_services
from .store import ConfigEntryTokenStore

_LOGGER = logging.getLogger(__name__)

# The integration has no YAML configuration; declaring that keeps a stray `holabrain:`
# block in configuration.yaml from being silently accepted.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class HolabrainData:
    """Runtime objects stored on the config entry."""

    coordinator: HolabrainCoordinator
    client: DollinClient
    http: httpx.AsyncClient


type HolabrainConfigEntry = ConfigEntry[HolabrainData]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring an entry written by another version of the integration up to date.

    There is nothing to migrate yet — every released schema is version 1 — but the hook has
    to exist before it is needed, and it is what refuses an entry from a *newer* version.
    Without that refusal a downgrade would silently load data it cannot interpret; failing
    the migration leaves the entry alone and tells the user to upgrade back.
    """
    if entry.version > CONFIG_ENTRY_VERSION:
        _LOGGER.error(
            "Config entry version %s is newer than this integration supports (%s); "
            "downgrade the config entry or upgrade the integration",
            entry.version,
            CONFIG_ENTRY_VERSION,
        )
        return False
    return True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions.

    Actions are registered here, once, rather than per config entry: an automation that
    references one then still validates while no account is loaded, and calling it fails
    with an explanation instead of "action not found".
    """
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HolabrainConfigEntry) -> bool:
    """Set up HolaBrain from a config entry."""
    http = async_create_http_client()
    # The session is persisted in the config entry: logging in on every restart is slow and
    # is exactly the pattern a cloud throttles. The stored token is reused until the cloud
    # rejects it, at which point the auth manager logs in again.
    device_id = entry.data.get("device_id") or generate_device_id()
    if entry.data.get("device_id") != device_id:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "device_id": device_id}
        )
    client = DollinClient.create(
        http,
        ConfigEntryTokenStore(hass, entry),
        region=entry.data.get(CONF_REGION, DEFAULT_REGION),
        account=entry.data.get(CONF_ACCOUNT, ""),
        password=entry.data.get("password", ""),
        country=entry.data.get("country", "RU"),
        device_id=device_id,
    )
    coordinator = HolabrainCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        # The first refresh may fail *after* the coordinator has opened the push connection
        # and armed its timers — a cloud that answers the inventory but not the status is
        # exactly that case. Home Assistant retries the setup every 30 seconds, so leaving
        # them behind would pile up a TLS connection and a timer per attempt.
        await coordinator.async_shutdown()
        await http.aclose()
        raise

    entry.runtime_data = HolabrainData(coordinator=coordinator, client=client, http=http)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Frontend assets are served whenever the integration is loaded: the dashboard card
    # is a separate opt-in from the sidebar panel and must stay reachable either way.
    await async_register_static_path(hass)
    await _async_sync_panel(hass, extra=entry)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HolabrainConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        data = entry.runtime_data
        await data.coordinator.async_shutdown()
        await data.http.aclose()
        # The unloading entry no longer counts as loaded, so this drops the panel once
        # the last entry that asked for it is gone.
        await _async_sync_panel(hass)
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: HolabrainConfigEntry) -> None:
    """Apply an options change.

    Only the panel toggle lives in options, and it is independent of the entities, so
    there is nothing to reload — flipping the sidebar entry is enough.
    """
    await _async_sync_panel(hass, extra=entry)


async def _async_sync_panel(
    hass: HomeAssistant, *, extra: HolabrainConfigEntry | None = None
) -> None:
    """Show the panel while at least one loaded entry asks for it.

    ``extra`` covers the entry currently being set up, which Home Assistant does not
    report as loaded yet.
    """
    entries = list(hass.config_entries.async_loaded_entries(DOMAIN))
    if extra is not None and extra not in entries:
        entries.append(extra)
    if any(entry.options.get(CONF_PANEL, DEFAULT_PANEL) for entry in entries):
        await async_register_panel(hass)
    else:
        async_unregister_panel(hass)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete everything the entry left outside itself.

    The capability cache and the push client certificate live in ``.storage`` under the
    entry id, so nothing would ever clean them up again once the entry is gone — and the
    certificate is key material, which must not outlive the account it belongs to.
    """
    await async_cleanup_entry_storage(hass, entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: HolabrainConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting a device from the UI once the account no longer has it.

    Home Assistant only offers the delete button when an integration answers this, and it
    must answer *no* for a live appliance — otherwise a stray click would remove a device
    that is still there, and it would silently come back on the next scan, losing its
    customisations. An appliance the account no longer lists is safe to drop.
    """
    inventory = entry.runtime_data.coordinator.devices
    return not any(
        identifier[0] == DOMAIN and identifier[1] in inventory
        for identifier in device.identifiers
    )
