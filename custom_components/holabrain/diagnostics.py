"""Diagnostics for a HolaBrain config entry and its devices.

Two audiences are served by the same dump:

* a user reporting a problem, who needs the state the integration actually saw, and
* the maintainer adding support for an appliance category, who needs the raw cloud payload
  for that appliance — the device record as the account returns it, the resolved capability
  profile and every status key the appliance reports.

Everything that identifies the account or the hardware is redacted. Appliance ids are not
merely dropped but replaced by a stable per-report pseudonym, so a report stays internally
consistent (a device section can be matched to its status) without carrying a serial number
that can be traced back to a household.
"""

from __future__ import annotations

import hashlib
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from . import HolabrainConfigEntry
from .const import DOMAIN
from .coordinator import HolabrainCoordinator
from .registry import get_category

# Config-entry keys plus every credential-ish key seen in cloud payloads. The set is
# matched by key name at any depth, so a field this integration does not model yet is
# still redacted as long as it is named recognisably.
TO_REDACT = {
    "access_token",
    "accessToken",
    "account",
    "address",
    "applianceCode",
    "barcode",
    "bssid",
    "certificatePem",
    "deviceId",
    "device_id",
    "email",
    "gatewayId",
    "ip",
    "latitude",
    "longitude",
    "mac",
    "macAddress",
    "mobile",
    "password",
    "phone",
    "privateKey",
    "refreshToken",
    "serialNumber",
    "session",
    "sn",
    "ssid",
    "thingCode",
    "token",
    "uid",
    "userId",
    "wifi_password",
    "wifiName",
}


def _pseudonym(thing_code: str) -> str:
    """Stable, non-reversible stand-in for an appliance id.

    Hashing rather than numbering keeps the same appliance recognisable across two reports
    from the same user, which is what makes a "it broke again after the update" report
    useful.
    """
    return f"appliance-{hashlib.sha256(thing_code.encode()).hexdigest()[:8]}"


def _device_report(coordinator: HolabrainCoordinator, thing_code: str) -> dict[str, Any]:
    """Everything known about one appliance: identity, capabilities and live status."""
    device = coordinator.devices[thing_code]
    category = get_category(device.device_type)
    profile = coordinator.capability_for(thing_code)
    state = (coordinator.data or {}).get(thing_code)

    return {
        "device_type": device.device_type,
        # Whether the category is modelled at all is the first question for any report
        # about a missing entity, and the first thing to look at in a report about an
        # appliance the integration does not support yet.
        "category": category.category if category is not None else None,
        "supported": category is not None,
        "model": device.model,
        "firmware_version": device.firmware_version,
        "plugin_type": device.plugin_type,
        "thing_protocol": device.thing_protocol,
        "online": device.online,
        # The account record verbatim: the only place a not-yet-modelled field can show up.
        "raw": async_redact_data(dict(device.metadata), TO_REDACT),
        "capabilities": profile.to_cache() if profile is not None else None,
        "status": async_redact_data(dict(state.attributes), TO_REDACT) if state else None,
        "staged": dict(coordinator.drafts.get(thing_code, {})),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HolabrainConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the account and every appliance on it."""
    coordinator = entry.runtime_data.coordinator
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            # Push health explains the two reports that look identical otherwise: stale
            # values because the channel died, and stale values because polling is off.
            "push_connected": coordinator.push_connected,
            "last_push": (
                coordinator.last_push.isoformat() if coordinator.last_push else None
            ),
            "device_count": len(coordinator.devices),
            "mode": coordinator.mode,
        },
        # What the ecosystem calls each appliance type. Included because it is what turns a
        # report about an unsupported appliance into something actionable: the type code
        # alone says nothing, and the model list is how its plugin is addressed.
        "catalog": coordinator.catalog.to_dict(),
        "devices": {
            _pseudonym(thing_code): _device_report(coordinator, thing_code)
            for thing_code in coordinator.devices
        },
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: HolabrainConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a single appliance."""
    coordinator = entry.runtime_data.coordinator
    thing_code = next(
        (
            identifier
            for domain, identifier in device.identifiers
            if domain == DOMAIN and identifier in coordinator.devices
        ),
        None,
    )
    if thing_code is None:
        # The device registry entry outlived the appliance (unbound while unavailable).
        return {"error": "this appliance is no longer on the account"}
    return {
        "id": _pseudonym(thing_code),
        **_device_report(coordinator, thing_code),
    }
