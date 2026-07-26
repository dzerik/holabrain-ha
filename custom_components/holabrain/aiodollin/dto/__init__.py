"""Plain data-transfer objects for the HolaBrain cloud API.

These are pure dataclasses with no behaviour beyond parsing/merging their own data. Any
mapping of a device onto Home Assistant entities lives in the HA adapter, not here.
"""

from __future__ import annotations

from .capability import CapabilityProfile, parse_capability
from .certificate import Certificate
from .device import Device
from .state import DeviceState

__all__ = [
    "CapabilityProfile",
    "Certificate",
    "Device",
    "DeviceState",
    "parse_capability",
]
