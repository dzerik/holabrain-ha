"""Live device state.

Status arrives from two sources with different shapes:

* a full snapshot from ``appliance/query`` (all attribute keys at once), and
* incremental push frames on MQTT (often a single changed group, e.g.
  ``{"onlineChange": {"online": 1}}``).

:class:`DeviceState` stores a flat attribute map and knows how to fold a push delta into a
new immutable state so the coordinator never mutates shared data.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class DeviceState:
    """Immutable snapshot of one device's reported attributes."""

    thing_code: str
    attributes: MappingProxyType
    online: bool = True

    @classmethod
    def from_query(cls, thing_code: str, data: dict[str, Any]) -> DeviceState:
        """Build a state from an ``appliance/query`` payload.

        The status body is sometimes nested under a ``query`` key and sometimes returned
        flat; both are accepted. A sibling ``fault`` object (when present) is folded into
        the same flat attribute map, because fault codes are reported there rather than
        inside the status body.
        """
        attrs = data.get("query") if isinstance(data.get("query"), dict) else data
        merged = dict(attrs)
        fault = data.get("fault")
        if isinstance(fault, dict):
            for key, value in fault.items():
                merged.setdefault(key, value)
        online = _coerce_online(merged.get("online"), default=True)
        return cls(thing_code, MappingProxyType(merged), online=online)

    def get(self, key: str, default: Any = None) -> Any:
        return self.attributes.get(key, default)

    def apply_push(self, payload: dict[str, Any]) -> DeviceState:
        """Fold an MQTT push frame into a new state.

        Push frames wrap their content in a group key (``onlineChange``, ``status`` …). We
        flatten one level of nesting so ``{"onlineChange": {"online": 1}}`` updates the
        ``online`` flag and ``{"status": {"power": "1"}}`` updates ``power``. Scalar or
        already-flat frames are merged as-is.
        """
        flat: dict[str, Any] = {}
        online = self.online
        for key, value in payload.items():
            if key == "onlineChange" and isinstance(value, dict):
                online = _coerce_online(value.get("online"), default=online)
            elif isinstance(value, dict):
                flat.update(value)
            else:
                flat[key] = value
        if not flat and online == self.online:
            return self
        if "online" in flat:
            online = _coerce_online(flat["online"], default=online)
        merged = {**self.attributes, **flat}
        return replace(self, attributes=MappingProxyType(merged), online=online)


def _coerce_online(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value not in ("0", "", "false", "False")
    return bool(value)
