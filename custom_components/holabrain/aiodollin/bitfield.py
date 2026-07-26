"""Bit-packed capability descriptors.

Not every appliance family negotiates its features through the cloud capability dictionary.
Air conditioners (device type ``0xAC``) instead carry a compact base64 blob in their device
metadata (``dynamicAttr`` / ``manualDynamicAttr``): a bit-packed record whose fields appear
in a **fixed order with fixed widths**, followed by two 32-bit blocks holding the supported
target-temperature range.

This module is deliberately tiny and pure — no I/O, no state — so the layout can be unit
tested against a captured blob and reused by any resolver.

Decoding model
--------------
``payload`` is base64. The bytes are expanded into a bit stream (least-significant bit of
each byte first, which is how the cloud packs it), then each field consumes ``width`` bits
and is assembled least-significant bit first::

    b"\\x05"  ->  bits 1,0,1,0,0,0,0,0
    fields [("isEcoFunc", 2), ("is8DegreeHeatFunc", 2)] -> isEcoFunc=1, is8DegreeHeatFunc=1

A blob shorter than the layout is not an error: fields that ran out of bits are simply
**absent** from the result. Absent must be read as "unknown/unsupported" by callers — never
as an explicit zero — so a truncated payload can never fabricate a capability.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from .exceptions import ApiError

__all__ = [
    "AC_DYNAMIC_ATTR_LAYOUT",
    "BitField",
    "BitfieldLayout",
    "decode_bitfield",
]


@dataclass(frozen=True, slots=True)
class BitField:
    """One named field of a packed record."""

    name: str
    width: int


@dataclass(frozen=True, slots=True)
class BitfieldLayout:
    """Ordered field list plus any trailing fixed-width 32-bit values."""

    fields: tuple[BitField, ...]
    trailing_u32: tuple[str, ...] = ()

    @property
    def bit_length(self) -> int:
        return sum(f.width for f in self.fields) + 32 * len(self.trailing_u32)


# Field order and widths of the air-conditioner capability record. Reserved fields are kept
# so that the trailing temperature blocks stay correctly aligned.
AC_DYNAMIC_ATTR_LAYOUT: Final = BitfieldLayout(
    fields=(
        BitField("isEcoFunc", 2),
        BitField("is8DegreeHeatFunc", 2),
        BitField("isStrong", 2),
        BitField("iselectricityStauts", 2),
        BitField("isColdandWarm", 3),
        BitField("isSmoothWind", 3),
        BitField("isDecimalTemp", 2),
        BitField("isSwingWind", 4),
        BitField("isSelfCleaning", 2),
        BitField("isSmartEye", 2),
        BitField("isWindNOTBreezed", 2),
        BitField("isWindBreezed", 2),
        BitField("isLightControl", 2),
        BitField("isfourLouver", 2),
        BitField("isWaterTankControl", 3),
        BitField("iswindSoftCtrl", 3),
        BitField("isWetted", 2),
        BitField("isAnion", 2),
        BitField("isTwinsDevice", 2),
        BitField("iswindNoneCtrl", 4),
        BitField("notWindStatus", 4),
        BitField("isRoundWindStatus", 2),
        BitField("isUnDirectBlow", 2),
        BitField("isFilterScreen", 3),
        BitField("isFahrenheit", 2),
        BitField("isBuzzer", 2),
        BitField("isReserve1", 1),
        BitField("isReserve2", 8),
        BitField("isReserve3", 8),
        BitField("isReserve4", 8),
        BitField("isReserve5", 8),
    ),
    trailing_u32=("tempMinimum", "tempMaximum"),
)


def decode_bitfield(
    payload: str | bytes,
    layout: BitfieldLayout = AC_DYNAMIC_ATTR_LAYOUT,
    *,
    lsb_first: bool = True,
) -> dict[str, int]:
    """Decode a base64 (or raw ``bytes``) capability record into ``name -> value``.

    Only fields that were fully covered by the payload are returned. ``lsb_first`` selects
    the bit order inside each byte; it is exposed so a firmware variant that packs the other
    way round can be handled without touching the layout table.

    Raises :class:`ApiError` if the payload is not decodable base64.
    """
    data = _to_bytes(payload)
    bits = _iter_bits(data, lsb_first=lsb_first)
    result: dict[str, int] = {}

    for field in layout.fields:
        value = _take(bits, field.width)
        if value is None:
            return result
        result[field.name] = value

    for name in layout.trailing_u32:
        value = _take(bits, 32)
        if value is None:
            return result
        result[name] = value

    return result


def _to_bytes(payload: str | bytes) -> bytes:
    if isinstance(payload, bytes):
        return payload
    text = payload.strip()
    if not text:
        return b""
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.b64decode(padded)
    except (binascii.Error, ValueError) as err:
        raise ApiError("capability descriptor was not valid base64") from err


def _iter_bits(data: bytes, *, lsb_first: bool) -> Iterator[int]:
    for byte in data:
        for index in range(8):
            shift = index if lsb_first else 7 - index
            yield (byte >> shift) & 1


def _take(bits: Iterator[int], width: int) -> int | None:
    """Consume ``width`` bits, LSB first. Returns None if the stream ran out."""
    value = 0
    for index in range(width):
        bit = next(bits, None)
        if bit is None:
            return None
        value |= bit << index
    return value
