"""Command/status routing by the appliance's announced protocol.

Appliances speak one of two command dialects. The payload is identical; the path and the
signing scheme differ, so a wrong route fails as an opaque cloud error rather than anything
obvious — and only on the appliance families that use the other dialect. These tests pin the
routing.
"""

import pytest

from custom_components.holabrain.aiodollin.api.devices import DeviceApi
from custom_components.holabrain.aiodollin.const import THING_PROTOCOL_DIRECT

DIRECT_CODE = "111111111111111"
ALT_CODE = "222222222222222"


def _device(thing_code: str, protocol: int) -> dict:
    return {
        "thingCode": thing_code,
        "thingName": f"Device {thing_code}",
        "deviceType": "0xE1" if protocol == THING_PROTOCOL_DIRECT else "0xDB",
        "sn8": "MODEL001",
        "online": 1,
        "thingProtocol": protocol,
    }


class FakeAuth:
    def __init__(self, listing: list[dict]) -> None:
        self._listing = listing
        self._listed = False
        self.tob_calls: list[tuple[str, dict]] = []
        self.oem_calls: list[tuple[str, dict]] = []

    async def oem(self, path, payload=None):
        # The account listing is an OEM call too, but it is not a routing decision — keep it
        # out of oem_calls so the per-device assertions below index what they mean.
        if not self._listed:
            self._listed = True
            return {"code": 0, "data": self._listing}
        self.oem_calls.append((path, payload))
        return {"code": 0, "data": {"power": "1"}}

    async def tob(self, path, payload=None):
        self.tob_calls.append((path, payload))
        return {"code": 0, "data": {"power": "1"}}


@pytest.mark.asyncio
async def test_direct_protocol_uses_the_verified_paths():
    auth = FakeAuth([_device(DIRECT_CODE, 1)])
    api = DeviceApi(auth)
    await api.async_list()

    await api.async_send_instruction(DIRECT_CODE, {"runState": "1"})
    await api.async_get_state(DIRECT_CODE)

    command_path, status_path = (call[0] for call in auth.tob_calls)
    assert command_path.endswith(f"/device/command/{DIRECT_CODE}")
    assert status_path.endswith(f"/appliance/query/{DIRECT_CODE}")


@pytest.mark.asyncio
async def test_other_protocol_uses_the_alternate_paths():
    """The alternate dialect changes the signer as well: ToB would be rejected outright."""
    auth = FakeAuth([_device(ALT_CODE, 2)])
    api = DeviceApi(auth)
    await api.async_list()

    await api.async_send_instruction(ALT_CODE, {"startPause": "1"})
    await api.async_get_state(ALT_CODE)

    assert auth.tob_calls == []
    command_path, status_path = (call[0] for call in auth.oem_calls)
    assert command_path == f"/v1/appliance/deviceCommands/requestNoReply/{ALT_CODE}"
    assert status_path == f"/v1/appliance/deviceCommands/query/{ALT_CODE}"


@pytest.mark.asyncio
async def test_the_payload_is_identical_on_both_dialects():
    # Only the route differs. If a refactor ever wrapped the body differently per dialect,
    # one appliance family would silently stop accepting commands.
    auth = FakeAuth([_device(DIRECT_CODE, 1), _device(ALT_CODE, 2)])
    api = DeviceApi(auth)
    await api.async_list()

    await api.async_send_instruction(DIRECT_CODE, {"power": "1"})
    await api.async_send_instruction(ALT_CODE, {"power": "1"})

    assert auth.tob_calls[0][1] == auth.oem_calls[0][1] == {"instruction": {"power": "1"}}


@pytest.mark.asyncio
async def test_mixed_account_routes_each_device_independently():
    """One account can hold both dialects; a shared route would break half of it."""
    auth = FakeAuth([_device(DIRECT_CODE, 1), _device(ALT_CODE, 2)])
    api = DeviceApi(auth)
    await api.async_list()

    await api.async_get_state(DIRECT_CODE)
    await api.async_get_state(ALT_CODE)

    assert auth.tob_calls[0][0].endswith(f"/appliance/query/{DIRECT_CODE}")
    assert auth.oem_calls[0][0] == f"/v1/appliance/deviceCommands/query/{ALT_CODE}"


@pytest.mark.asyncio
async def test_unknown_device_falls_back_to_the_verified_dialect():
    """A device that was never listed must not break: use the dialect known to work."""
    auth = FakeAuth([])
    api = DeviceApi(auth)

    await api.async_send_instruction("999999999999999", {"power": "0"})

    assert auth.tob_calls[0][0].endswith("/device/command/999999999999999")


@pytest.mark.asyncio
async def test_a_missing_protocol_field_is_treated_as_the_direct_dialect():
    """Older listings omit the field entirely; defaulting to the verified path is safest."""
    listing = _device(DIRECT_CODE, 1)
    del listing["thingProtocol"]
    auth = FakeAuth([listing])
    api = DeviceApi(auth)
    await api.async_list()

    await api.async_send_instruction(DIRECT_CODE, {"power": "1"})

    assert "/device/command/" in auth.tob_calls[0][0]
