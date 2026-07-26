"""Device descriptor as returned by the account's device list."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class Device:
    """One appliance bound to the account.

    ``device_type`` is the category token (e.g. ``"0xE1"``) and ``model`` is the 8-char
    model code (``sn8``) used to look up the device's capability profile. Families that ship
    a packed capability descriptor carry it in ``dynamic_attr``; ``metadata`` keeps the whole
    raw record so a capability resolver can read fields this DTO does not model yet.
    """

    thing_code: str
    name: str
    device_type: str
    model: str
    online: bool
    firmware_version: str = ""
    plugin_type: int = 0
    # Which command dialect the appliance speaks; see THING_PROTOCOL_DIRECT.
    thing_protocol: int = 1
    dynamic_attr: str = ""
    metadata: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Device:
        return cls(
            thing_code=str(data.get("thingCode") or data.get("applianceCode") or ""),
            name=str(data.get("thingName") or data.get("name") or ""),
            device_type=_normalize_type(data.get("deviceType")),
            model=str(data.get("sn8") or data.get("deviceSn8") or data.get("model") or ""),
            online=bool(data.get("online")),
            firmware_version=str(data.get("firmwareVersion") or ""),
            plugin_type=_as_int(data.get("pluginType"), 0),
            thing_protocol=_as_int(data.get("thingProtocol"), 1),
            dynamic_attr=str(data.get("dynamicAttr") or data.get("manualDynamicAttr") or ""),
            metadata=MappingProxyType(dict(data)),
        )


def _as_int(value: Any, default: int) -> int:
    """Coerce a numeric field, tolerating the shapes the cloud actually sends.

    The same field comes back as an int, as a string, as ``null`` and — on a bad day — as an
    empty string or a word. One unparsable field must not abort the whole device list, which
    would take every appliance on the account down with it.
    """
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_type(value: Any) -> str:
    """Normalize a device-type token to the canonical ``0x`` + upper-hex form."""
    text = str(value or "").strip()
    if text.lower().startswith("0x"):
        return "0x" + text[2:].upper()
    return text
