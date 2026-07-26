"""DollinClient — the single façade most callers need.

Composes the transport, authentication and per-domain APIs, and builds the push (MQTT)
topics for a device. Construct it with :meth:`DollinClient.create`, injecting an
``httpx.AsyncClient`` and a :class:`TokenStore`.
"""

from __future__ import annotations

from typing import Any

import httpx

from .api.binding import BindingApi
from .api.capabilities import CapabilityApi
from .api.certificates import CertificateApi
from .api.devices import DeviceApi
from .auth.manager import AuthManager
from .auth.store import TokenStore
from .const import DEFAULT_REGION, MQTT_CHANNELS, MQTT_TOPIC_TEMPLATE
from .transport.http import HttpTransport


class DollinClient:
    """High-level entry point to the HolaBrain cloud."""

    def __init__(self, transport: HttpTransport, auth: AuthManager) -> None:
        self._transport = transport
        self._auth = auth
        self.devices = DeviceApi(auth)
        # Capability resolution may need a status snapshot (several appliance families
        # advertise features only by reporting the matching status key). The provider is
        # injected rather than imported so the resolver chain stays testable offline.
        self.capabilities = CapabilityApi(auth, status_provider=self._async_status)
        self.certificates = CertificateApi(auth)
        self.binding = BindingApi(auth)

    async def _async_status(self, thing_code: str) -> dict[str, Any]:
        """Status snapshot used by the presence-based capability resolver."""
        state = await self.devices.async_get_state(thing_code)
        return dict(state.attributes)

    @classmethod
    def create(
        cls,
        http_client: httpx.AsyncClient,
        store: TokenStore,
        *,
        region: str = DEFAULT_REGION,
        account: str = "",
        password: str = "",
        country: str = "RU",
        device_id: str | None = None,
        language: str = "en_US",
    ) -> DollinClient:
        transport = HttpTransport(
            http_client, region=region, device_id=device_id, language=language
        )
        auth = AuthManager(
            transport, store, account=account, password=password, country=country
        )
        return cls(transport, auth)

    @property
    def auth(self) -> AuthManager:
        return self._auth

    @property
    def region(self) -> str:
        return self._transport.region

    async def async_login(self) -> str:
        """Force a fresh login and return the access token.

        Used when the user has just typed credentials, so it bypasses the takeover
        cool-down: the request is explicit and expected to evict any other session.
        """
        return await self._auth.async_login()

    def dev_topic(self, thing_code: str) -> str:
        """The MQTT topic carrying this device's status frames."""
        return MQTT_TOPIC_TEMPLATE.format(
            region=self.region, thing_code=thing_code, channel="dev"
        )

    def push_topics(self, thing_code: str) -> list[str]:
        """All MQTT topics for a device (dev / will / hbt)."""
        return [
            MQTT_TOPIC_TEMPLATE.format(
                region=self.region, thing_code=thing_code, channel=channel
            )
            for channel in MQTT_CHANNELS
        ]
