"""Integration-level services.

``holabrain.scan_devices`` re-reads the account inventory so appliances paired after setup
appear. It is deliberately manual: listing the account needs the account session, and the
cloud allows only one — claiming it signs the vendor's mobile app out. Monitoring never does
that on its own, so scanning stays an explicit, user-initiated action.

The actions are registered once, in ``async_setup``, and stay registered while the
integration is installed. An automation that references one therefore keeps validating even
when no account is loaded; the call itself then fails with an explanation.

``holabrain.refresh_capabilities`` re-resolves what every appliance supports and rebuilds the
entity set if the answer changed. Capability profiles are cached and revalidated on a slow
timer, so this is the escape hatch for the two cases a timer cannot cover: a feature was
enabled (or the appliance was serviced) and the user does not want to wait, or a profile was
resolved while the cloud was degraded.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .aiodollin import DollinError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_REFRESH_CAPABILITIES = "refresh_capabilities"
SERVICE_SCAN_DEVICES = "scan_devices"
SERVICE_RENAME_DEVICE = "rename_device"
SERVICE_UNBIND_DEVICE = "unbind_device"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_DEVICE_ID = "device_id"
ATTR_NAME = "name"
ATTR_CONFIRM = "confirm"

_REFRESH_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_DEVICE_ID): cv.string,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services once, on the first config entry."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH_CAPABILITIES):
        return

    async def _async_refresh(call: ServiceCall) -> None:
        entries = _target_entries(hass, call)
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_loaded_account"
            )
        for entry in entries:
            coordinator = entry.runtime_data.coordinator
            changed = await coordinator.async_refresh_capabilities(force=True)
            # Logs end up in public issue reports, so entries are referenced by id: the
            # title is the account's e-mail address.
            _LOGGER.debug(
                "refreshed capabilities for entry %s: %s",
                entry.entry_id,
                "changed" if changed else "unchanged",
            )

    async def _async_scan(call: ServiceCall) -> None:
        entries = _target_entries(hass, call)
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_loaded_account"
            )
        for entry in entries:
            coordinator = entry.runtime_data.coordinator
            try:
                added, removed = await coordinator.async_scan_devices()
            except DollinError as err:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="scan_failed",
                    translation_placeholders={"account": entry.title, "error": str(err)},
                ) from err
            _LOGGER.info(
                "scanned entry %s: %s appliance(s) added, %s removed",
                entry.entry_id,
                added,
                removed,
            )

    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH_CAPABILITIES, _async_refresh, schema=_REFRESH_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SCAN_DEVICES, _async_scan, schema=_REFRESH_SCHEMA
    )

    async def _async_rename(call: ServiceCall) -> None:
        entry, thing_code = _target_appliance(hass, call)
        name = call.data[ATTR_NAME]
        try:
            await entry.runtime_data.client.binding.async_rename(thing_code, name)
        except DollinError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="rename_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        # The account is the source of the name, so re-read it rather than guessing.
        await entry.runtime_data.coordinator.async_scan_devices()

    async def _async_unbind(call: ServiceCall) -> None:
        # Unbinding cannot be undone from here: putting the appliance back needs its setup
        # mode, which is a button on the appliance itself. So it is opt-in twice.
        if not call.data.get(ATTR_CONFIRM):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="unbind_not_confirmed"
            )
        entry, thing_code = _target_appliance(hass, call)
        try:
            removed = await entry.runtime_data.client.binding.async_unbind([thing_code])
        except DollinError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unbind_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        if thing_code not in removed:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="unbind_refused"
            )
        await entry.runtime_data.coordinator.async_scan_devices()

    hass.services.async_register(
        DOMAIN,
        SERVICE_RENAME_DEVICE,
        _async_rename,
        schema=vol.Schema(
            {vol.Required(ATTR_DEVICE_ID): cv.string, vol.Required(ATTR_NAME): cv.string}
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UNBIND_DEVICE,
        _async_unbind,
        schema=vol.Schema(
            {
                vol.Required(ATTR_DEVICE_ID): cv.string,
                vol.Required(ATTR_CONFIRM, default=False): cv.boolean,
            }
        ),
    )


def _target_entries(hass: HomeAssistant, call: ServiceCall) -> list:
    """Resolve the call target to loaded config entries (all of them by default)."""
    entry_ids: set[str] = set()

    if entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID):
        entry_ids.add(entry_id)

    if device_id := call.data.get(ATTR_DEVICE_ID):
        device = dr.async_get(hass).async_get(device_id)
        if device is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_device",
                translation_placeholders={"device_id": device_id},
            )
        entry_ids.update(device.config_entries)

    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
        and (not entry_ids or entry.entry_id in entry_ids)
    ]
    return entries


def _target_appliance(hass: HomeAssistant, call: ServiceCall) -> tuple[Any, str]:
    """Resolve the call's device to ``(config entry, appliance id)``.

    These services act on one appliance, so an ambiguous target is refused rather than
    applied to whichever entry happened to be first.
    """
    device_id = call.data[ATTR_DEVICE_ID]
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_device",
            translation_placeholders={"device_id": device_id},
        )
    thing_code = next(
        (value for domain, value in device.identifiers if domain == DOMAIN), None
    )
    if thing_code is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_an_appliance",
            translation_placeholders={"device_id": device_id},
        )
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED and entry.entry_id in device.config_entries:
            return entry, thing_code
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="account_not_loaded"
    )
