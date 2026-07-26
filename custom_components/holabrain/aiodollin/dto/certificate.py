"""Cloud-push (MQTT) client certificate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..const import MQTT_PORT


@dataclass(frozen=True, slots=True)
class Certificate:
    """A client certificate minted by the cloud for the push (MQTT) connection."""

    private_key: str
    certificate_pem: str
    endpoint: str
    port: int = MQTT_PORT

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Certificate:
        return cls(
            private_key=str(data["privateKey"]),
            certificate_pem=str(data["certificatePem"]),
            endpoint=str(data["endpoint"]),
            port=int(data.get("port") or MQTT_PORT),
        )
