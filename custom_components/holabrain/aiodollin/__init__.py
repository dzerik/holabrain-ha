"""aiodollin — standalone async client for the HolaBrain (dollin) cloud API.

This package has **zero Home Assistant imports** and no global mutable state, so it can be
split out into its own PyPI distribution without changes. A compliance test
(``tests/aiodollin/test_no_ha_imports.py``) enforces this.
"""

from __future__ import annotations

from .api.binding import BindingApi, Claimable, NotClaimable, SerialUnknown
from .api.capabilities import (
    CapabilityApi,
    PresenceRule,
    ResolutionContext,
    ResolverChain,
    StaticVariant,
)
from .auth.store import InMemoryTokenStore, Session, TokenStore
from .bitfield import decode_bitfield
from .client import DollinClient
from .discovery import DiscoveredAppliance, async_discover
from .dto.capability import (
    CAPABILITY_SCHEMA_VERSION,
    CapabilityProfile,
    parse_capability,
)
from .dto.certificate import Certificate
from .dto.device import Device
from .dto.state import DeviceState
from .exceptions import (
    ApiError,
    AuthError,
    CredentialsRejectedError,
    DollinError,
    NetworkError,
    RateLimitError,
    SessionTakeoverError,
    TokenExpiredError,
)
from .pairing import derive_verification_code, encrypt_serial

__all__ = [
    "CAPABILITY_SCHEMA_VERSION",
    "ApiError",
    "AuthError",
    "BindingApi",
    "CapabilityApi",
    "CapabilityProfile",
    "Certificate",
    "Claimable",
    "CredentialsRejectedError",
    "Device",
    "DeviceState",
    "DiscoveredAppliance",
    "DollinClient",
    "DollinError",
    "InMemoryTokenStore",
    "NetworkError",
    "NotClaimable",
    "PresenceRule",
    "RateLimitError",
    "ResolutionContext",
    "ResolverChain",
    "SerialUnknown",
    "Session",
    "SessionTakeoverError",
    "StaticVariant",
    "TokenExpiredError",
    "TokenStore",
    "async_discover",
    "decode_bitfield",
    "derive_verification_code",
    "encrypt_serial",
    "parse_capability",
]
