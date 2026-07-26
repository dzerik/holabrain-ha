"""Managing which appliances belong to the account.

Everything here changes the account itself rather than an appliance's state, so each call is
user-initiated: they all need the account session, and claiming it signs the vendor's mobile
app out (the cloud allows one session per account).

Claiming a *new* appliance additionally requires it to be announcing itself to the cloud —
which happens only while it is in its setup mode. An appliance that is simply online is
recognized by the cloud but not claimable, so :meth:`BindingApi.async_find` reports that
distinctly instead of pretending the serial is unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..auth.manager import AuthManager
from ..const import (
    EP_AUTH_STATUS,
    EP_BIND,
    EP_BINDER_REMOVE,
    EP_LOCATION_SAVE,
    EP_NAME_UPDATE,
    EP_VERIFICATION,
)
from ..exceptions import ApiError
from ..pairing import encrypt_serial

# Business codes the verification endpoint answers with.
CODE_SERIAL_UNKNOWN = 1201
CODE_NOT_CLAIMABLE = 1210
# The bind endpoint's own refusal when the appliance is not reachable.
CODE_DEVICE_OFFLINE = 1204


@dataclass(frozen=True, slots=True)
class Claimable:
    """An appliance the cloud is willing to hand over, as returned by verification."""

    appliance_code: str
    verification_code: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Claimable:
        return cls(
            appliance_code=str(data.get("applianceCode", "")),
            verification_code=str(data.get("verificationCode", "")),
        )


class SerialUnknown(ApiError):
    """The cloud has never heard of this serial number."""


class NotClaimable(ApiError):
    """The serial is known, but the appliance is not offering itself right now.

    In practice this means it is not in setup mode: an appliance that is merely online and
    unbound still answers this way.
    """


class BindingApi:
    """Claim, release and label the appliances on the account."""

    def __init__(self, auth: AuthManager) -> None:
        self._auth = auth

    async def async_find(self, serial: str, verification_code: str) -> list[Claimable]:
        """Ask the cloud whether an appliance with this serial can be claimed.

        The serial is encrypted with the caller's own session, so this cannot be replayed
        from someone else's capture.
        """
        token = await self._auth.async_get_token()
        payload = {
            "sn": encrypt_serial(serial, token),
            "randomCode": verification_code,
        }
        try:
            data = await self._auth.tob(EP_VERIFICATION, payload)
        except ApiError as err:
            if err.code == CODE_SERIAL_UNKNOWN:
                raise SerialUnknown(str(err), code=err.code) from err
            if err.code == CODE_NOT_CLAIMABLE:
                raise NotClaimable(str(err), code=err.code) from err
            raise
        items = (data.get("data") or {}).get("applianceList")
        if not isinstance(items, list):
            return []
        return [Claimable.from_dict(item) for item in items if isinstance(item, dict)]

    async def async_bind(
        self,
        appliance_code: str,
        verification_code: str,
        appliance_type: str,
        *,
        time_zone_id: str = "",
    ) -> None:
        """Add an appliance to the account."""
        await self._auth.tob(
            EP_BIND,
            {
                "timeZoneID": time_zone_id,
                "applianceType": appliance_type,
                "applianceCode": appliance_code,
                "verificationCode": verification_code,
            },
        )

    async def async_unbind(self, thing_codes: list[str]) -> list[str]:
        """Remove appliances from the account.

        Irreversible without physical access: putting an appliance back needs its setup mode,
        which is a button on the appliance itself.
        """
        data = await self._auth.oem(EP_BINDER_REMOVE, {"thingCodes": thing_codes})
        removed = data.get("data")
        return [str(code) for code in removed] if isinstance(removed, list) else []

    async def async_rename(self, thing_code: str, name: str) -> None:
        """Rename an appliance in the account, as the vendor app shows it."""
        await self._auth.oem(EP_NAME_UPDATE, {"thingCode": thing_code, "thingName": name})

    async def async_set_location(
        self, thing_code: str, location: str, *, thing_protocol: str = "1"
    ) -> None:
        """Set the appliance's location string."""
        await self._auth.oem(
            EP_LOCATION_SAVE,
            {
                "thingCode": thing_code,
                "thingProtocol": thing_protocol,
                "location": location,
            },
        )

    async def async_auth_status(self, appliance_code: str) -> int | None:
        """Return the appliance's authorization status, or None if the cloud omitted it."""
        data = await self._auth.tob(EP_AUTH_STATUS, {"applianceCode": appliance_code})
        body = data.get("data")
        if not isinstance(body, dict):
            return None
        status = body.get("status")
        return int(status) if isinstance(status, (int, str)) and str(status).isdigit() else None
