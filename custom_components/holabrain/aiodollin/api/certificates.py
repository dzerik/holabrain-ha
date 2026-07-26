"""Cloud-push (MQTT) certificate minting."""

from __future__ import annotations

from ..auth.manager import AuthManager
from ..const import EP_CERT_CREATE
from ..dto.certificate import Certificate
from ..exceptions import ApiError


class CertificateApi:
    """Mint the client certificate used for the push (MQTT) connection."""

    def __init__(self, auth: AuthManager) -> None:
        self._auth = auth

    async def async_create(self) -> Certificate:
        """Create a fresh client certificate and return its key, cert and broker endpoint."""
        data = await self._auth.oem(EP_CERT_CREATE, {})
        body = data.get("data")
        if not isinstance(body, dict) or "privateKey" not in body:
            raise ApiError("certificate endpoint returned no certificate material")
        return Certificate.from_dict(body)
