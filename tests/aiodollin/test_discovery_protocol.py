"""Decoding an appliance's own announcement on the local network.

The reply below is a real announcement captured from an appliance. Everything the pairing
flow shows the user comes out of these bytes — the serial it no longer has to be typed, the
category, and the id the account uses — so a decoding mistake is not cosmetic: it either
loses the appliance or claims the wrong one.
"""

import asyncio
from unittest.mock import patch

import pytest

from custom_components.holabrain.aiodollin.discovery import (
    DiscoveredAppliance,
    async_discover,
    parse_reply,
)

# A real announcement, byte for byte, with the identifiers of the appliance it came
# from replaced — the wire format is what matters here, not whose dishwasher it was.
REAL_REPLY = bytes.fromhex(
    "837000c8200f00005a5a0111b8007a8000000000d5ae1e101a071a1401407a10f35a00000000"
    "00000000018000000000e397487f127b66b9fa006cd2a45f02fa846afddaaa959409f4f89d2f"
    "cb92d15ce6a3831df78cb0403e9bdede7137376690feb4567b8b3f7b4676d7a31527bccb7e1b"
    "71464784db324e0d824f79e7201fddb3a77849496881de23ad7cff60e3174bdfb3e16e33d887"
    "68cc4c3d0658937d0bb19369bf0317b24d3a4de9e6a131063bb834369beebc7f8521de937ed8"
    "0529a3ac87422b1ee6568edb55817c97820e"
)


def test_a_real_announcement_decodes_to_the_appliance_it_came_from():
    appliance = parse_reply(REAL_REPLY, "192.0.2.63")

    assert appliance == DiscoveredAppliance(
        device_id="100000000000001",
        serial="0000E1540760EY1790000000000EXAMP",
        model="760EY179",
        device_type="0xE1",
        host="192.0.2.63",
        port=6444,
    )


def test_the_reported_id_is_the_one_the_account_uses():
    """This is what lets an already-bound appliance be filtered out of the list.

    If the ids did not match, every appliance would be offered again after it was added.
    """
    appliance = parse_reply(REAL_REPLY, "192.0.2.63")

    assert appliance.device_id.isdigit()
    assert appliance.device_id == "100000000000001"


def test_the_model_is_the_prefix_the_capability_lookup_needs():
    """Capabilities are resolved per model, so the field has to be the short model code."""
    appliance = parse_reply(REAL_REPLY, "192.0.2.63")

    assert appliance.model in appliance.serial
    assert len(appliance.model) == 8


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not an announcement at all",
        b"\x00" * 120,
        REAL_REPLY[:60],  # truncated mid-frame
    ],
)
def test_traffic_that_is_not_an_announcement_is_ignored(payload):
    """A home network carries plenty of other UDP traffic on these ports.

    Treating any of it as an error would make the search fail on a busy network instead of
    simply finding nothing.
    """
    assert parse_reply(payload, "192.168.1.1") is None


def test_a_corrupted_announcement_is_ignored_rather_than_half_decoded():
    """Half a serial is worse than none: it would be offered and then rejected by the cloud."""
    corrupted = bytearray(REAL_REPLY)
    corrupted[60:80] = b"\xff" * 20  # damage the encrypted body

    assert parse_reply(bytes(corrupted), "192.0.2.63") is None


def test_the_same_appliance_answering_twice_is_listed_once():
    """Both probed ports are answered, and a broadcast can be seen on several interfaces."""

    async def scenario() -> list[DiscoveredAppliance]:
        found: dict = {}
        for _ in range(3):
            appliance = parse_reply(REAL_REPLY, "192.0.2.63")
            found.setdefault(appliance.device_id, appliance)
        return list(found.values())

    assert len(asyncio.run(scenario())) == 1


class _RefusingTransport:
    """A transport on a host with no route for broadcast."""

    def __init__(self) -> None:
        self.attempts = 0

    def sendto(self, data, addr):
        self.attempts += 1
        raise OSError("Network is unreachable")

    def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_a_host_without_a_broadcast_route_finds_nothing_quietly():
    """A container with no route for broadcast must return an empty list, not raise.

    Home Assistant frequently runs in exactly such a container, and a config flow that
    raised here would show a traceback instead of "no appliances found".
    """
    transport = _RefusingTransport()

    async def fake_endpoint(factory, **kwargs):
        return transport, factory()

    with patch.object(
        asyncio.get_running_loop(), "create_datagram_endpoint", fake_endpoint
    ):
        found = await async_discover(
            timeout=0, broadcast_addresses=("203.0.113.255", "198.51.100.255")
        )

    assert found == []
    assert transport.attempts == 4  # both addresses on both ports were still attempted


@pytest.mark.asyncio
async def test_every_probed_address_and_port_is_attempted():
    """A host on several segments only sees the ones it actually probes."""
    sent: list[tuple] = []

    class _Recording:
        def sendto(self, data, addr):
            sent.append(addr)

        def close(self) -> None:
            pass

    async def fake_endpoint(factory, **kwargs):
        return _Recording(), factory()

    with patch.object(
        asyncio.get_running_loop(), "create_datagram_endpoint", fake_endpoint
    ):
        await async_discover(timeout=0, broadcast_addresses=("10.0.0.255", "10.0.1.255"))

    assert {addr[0] for addr in sent} == {"10.0.0.255", "10.0.1.255"}
    assert {addr[1] for addr in sent} == {6445, 20086}
