"""Device listing, status and control."""

from __future__ import annotations

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


class DeviceApi:
    """List devices, read status, and send control instructions.

    Appliances speak one of two command dialects, announced per device as ``thingProtocol``.
    The payloads are identical; only the paths differ. The protocol of every listed device is
    remembered here so callers can keep passing a plain thing code.
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

    def _path(self, thing_code: str, direct: str, alternate: str) -> str:
        # Unknown devices fall back to the direct dialect: it is the one verified against
        # hardware, and an unlisted device is the exception rather than the rule.
        protocol = self._protocols.get(thing_code, THING_PROTOCOL_DIRECT)
        template = direct if protocol == THING_PROTOCOL_DIRECT else alternate
        return template.format(thing_code=thing_code)

    async def async_get_state(self, thing_code: str) -> DeviceState:
        """Read the full current status of one device."""
        data = await self._auth.tob(
            self._path(thing_code, EP_APPLIANCE_QUERY, EP_APPLIANCE_QUERY_ALT),
            {"query": "1"},
        )
        body = data.get("data")
        if not isinstance(body, dict):
            raise ApiError(f"query for {thing_code} returned no status body")
        return DeviceState.from_query(thing_code, body)

    async def async_send_instruction(
        self, thing_code: str, instruction: dict[str, Any]
    ) -> None:
        """Send a control instruction. Raises on a non-success cloud code."""
        await self._auth.tob(
            self._path(thing_code, EP_DEVICE_COMMAND, EP_DEVICE_COMMAND_ALT),
            {"instruction": instruction},
        )
