"""Device listing, status and control."""

from __future__ import annotations

import logging
from typing import Any

from ..auth.manager import AuthManager
from ..const import (
    EP_APPLIANCE_QUERY,
    EP_APPLIANCE_QUERY_ALT,
    EP_DEVICE_COMMAND,
    EP_DEVICE_COMMAND_ALT,
    EP_HOME_INDEX,
    THING_PROTOCOL_DIRECT,
)
from ..dto.device import Device
from ..dto.state import DeviceState
from ..exceptions import ApiError
from ..redact import pseudonym

_LOGGER = logging.getLogger(__name__)


class DeviceApi:
    """List devices, read status, and send control instructions.

    Appliances speak one of two command dialects, announced per device as ``thingProtocol``.
    Protocol 1 ("direct") is signed and sent through the ToB scheme; every other protocol
    uses a different pair of paths signed with the OEM scheme instead — this is not just a
    different URL, the signature and headers differ too, confirmed against the vendor's own
    per-model plugin bundle. The protocol of every listed device is remembered here so
    callers can keep passing a plain thing code.
    """

    def __init__(self, auth: AuthManager) -> None:
        self._auth = auth
        self._protocols: dict[str, int] = {}

    async def async_list(self) -> list[Device]:
        """Return every appliance bound to the account."""
        data = await self._auth.oem(EP_HOME_INDEX)
        items = data.get("data")
        if not isinstance(items, list):
            return []
        devices = [Device.from_dict(item) for item in items if isinstance(item, dict)]
        self._protocols = {d.thing_code: d.thing_protocol for d in devices}
        return devices

    def _dialect(self, thing_code: str) -> bool:
        """Return whether this device speaks the direct dialect, and record the choice.

        Which dialect a device gets is decided from a field the account listing reported,
        so when the answer is wrong nothing in the failure names it: the request simply goes
        to the wrong endpoint under the wrong signature. Logging the decision turns "the
        cloud could not be reached" into a one-step diagnosis.
        """
        direct = self._is_direct(thing_code)
        _LOGGER.debug(
            "%s speaks the %s dialect (thingProtocol %s), signing with %s",
            pseudonym(thing_code),
            "direct" if direct else "alternate",
            self._protocols.get(thing_code, "unreported"),
            "ToB" if direct else "OEM",
        )
        return direct

    def _is_direct(self, thing_code: str) -> bool:
        # Unknown devices fall back to the direct dialect: it is the one verified against
        # hardware, and an unlisted device is the exception rather than the rule.
        return self._protocols.get(thing_code, THING_PROTOCOL_DIRECT) == THING_PROTOCOL_DIRECT

    async def async_get_state(self, thing_code: str) -> DeviceState:
        """Read the full current status of one device."""
        if self._dialect(thing_code):
            path = EP_APPLIANCE_QUERY.format(thing_code=thing_code)
            data = await self._auth.tob(path, {"query": "1"})
        else:
            path = EP_APPLIANCE_QUERY_ALT.format(thing_code=thing_code)
            data = await self._auth.oem(path, {"query": "1"})
        body = data.get("data")
        if not isinstance(body, dict):
            raise ApiError(f"query for {pseudonym(thing_code)} returned no status body")
        return DeviceState.from_query(thing_code, body)

    async def async_send_instruction(
        self, thing_code: str, instruction: dict[str, Any]
    ) -> None:
        """Send a control instruction. Raises on a non-success cloud code."""
        if self._dialect(thing_code):
            path = EP_DEVICE_COMMAND.format(thing_code=thing_code)
            await self._auth.tob(path, {"instruction": instruction})
        else:
            path = EP_DEVICE_COMMAND_ALT.format(thing_code=thing_code)
            await self._auth.oem(path, {"instruction": instruction})
