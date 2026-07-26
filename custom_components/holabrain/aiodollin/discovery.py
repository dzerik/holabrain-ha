"""Finding appliances on the local network.

Appliances answer a UDP broadcast with their own identity — serial, model, category and the
id the cloud knows them by — without any authentication at all. That makes this the cheapest
useful thing in the whole client:

* the user never has to read a serial off a label and retype it;
* the reported id is the same one the account uses, so an appliance that is already bound can
  be recognized and left out of the list; and
* none of it touches the account session, so it cannot sign the vendor's mobile app out.

It only sees appliances on the same broadcast domain as the host, which is the normal case
for a home network and the only case where the following pairing step could work anyway.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

DISCOVERY_PORTS: tuple[int, ...] = (6445, 20086)
DEFAULT_TIMEOUT = 4.0

# Fixed probe the appliances answer to. Its contents are a constant of the protocol.
_PROBE = bytes.fromhex(
    "5a5a01114800920000000000000000000000000000000000000000000000000000000000000000"
    "007f75bd6b3e4f8b762e849c6e578d6590036e9d4342a50f1f569eb8ec918e92e5"
)
# Every appliance decrypts its announcement with the same built-in key: the reply carries no
# secrets, only the identity the appliance is happy to shout on the LAN.
_ANNOUNCE_KEY = bytes.fromhex("6a92ef406bad2f0359baad994171ea6d")

_MIN_REPLY = 104
_HEADER_V2 = b"\x5a\x5a"
_HEADER_V3 = b"\x83\x70"


@dataclass(frozen=True, slots=True)
class DiscoveredAppliance:
    """An appliance that answered on the local network."""

    device_id: str
    serial: str
    model: str
    device_type: str
    host: str
    port: int

    @property
    def is_known_to(self) -> str:
        """The id the account uses for this appliance, if it is bound."""
        return self.device_id


def _decrypt(payload: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    decryptor = Cipher(algorithms.AES(_ANNOUNCE_KEY), modes.ECB()).decryptor()
    plain = decryptor.update(payload) + decryptor.finalize()
    padding = plain[-1] if plain else 0
    return plain[:-padding] if 0 < padding <= 16 else plain


def parse_reply(data: bytes, host: str) -> DiscoveredAppliance | None:
    """Decode one announcement, or return ``None`` if it is not one.

    Anything else on those ports — other vendors, stray traffic — is simply skipped rather
    than treated as an error, because a home network carries plenty of both.
    """
    if len(data) < _MIN_REPLY:
        return None
    body = data
    if data[:2] == _HEADER_V3 and data[8:10] == _HEADER_V2:
        body = data[8:-16]
    elif data[:2] != _HEADER_V2:
        return None

    try:
        device_id = int.from_bytes(body[20:26], "little")
        reply = _decrypt(body[40:-16])
        if len(reply) < 41:
            return None
        port = int.from_bytes(reply[4:8], "little")
        serial = reply[8:40].decode("utf-8")
        model = reply[17:25].decode("utf-8")
        ssid = reply[41 : 41 + reply[40]].decode("utf-8")
        # The SSID an appliance announces is "<family>_<type>_<suffix>"; the middle field is
        # the category, in the same hex notation the account uses.
        device_type = "0x" + ssid.split("_")[1].upper()
    except (IndexError, UnicodeDecodeError, ValueError) as err:
        _LOGGER.debug("ignoring an announcement from %s: %s", host, err)
        return None

    return DiscoveredAppliance(
        device_id=str(device_id),
        serial=serial,
        model=model,
        device_type=device_type,
        host=host,
        port=port,
    )


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.found: dict[str, DiscoveredAppliance] = {}

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        appliance = parse_reply(data, addr[0])
        if appliance is not None:
            self.found.setdefault(appliance.device_id, appliance)


async def async_discover(
    *,
    timeout: float = DEFAULT_TIMEOUT,
    ports: tuple[int, ...] = DISCOVERY_PORTS,
    broadcast_addresses: tuple[str, ...] = ("255.255.255.255",),
) -> list[DiscoveredAppliance]:
    """Broadcast a probe and collect the appliances that answer within ``timeout``.

    Several broadcast addresses can be given because a host with more than one network sees
    only the segment it probes; the caller knows its own topology, this module does not.
    """
    loop = asyncio.get_running_loop()
    # An explicit local address is required: without a bound socket the replies, which come
    # from the appliance's own port rather than the one probed, are never delivered.
    transport, protocol = await loop.create_datagram_endpoint(
        _Protocol,
        family=socket.AF_INET,
        allow_broadcast=True,
        local_addr=("0.0.0.0", 0),
    )
    try:
        for address in broadcast_addresses:
            for port in ports:
                try:
                    transport.sendto(_PROBE, (address, port))
                except OSError as err:
                    # A host with no route for broadcast simply finds nothing; that is not a
                    # failure worth propagating to a config flow.
                    _LOGGER.debug("could not probe %s:%s: %s", address, port, err)
        await asyncio.sleep(timeout)
    finally:
        transport.close()
    return sorted(protocol.found.values(), key=lambda item: item.device_id)
