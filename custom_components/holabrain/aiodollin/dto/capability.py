"""Capability profile — the normalized answer to "what can *this* appliance do?".

Different appliance families advertise their features in different ways (a cloud capability
dictionary, a packed descriptor blob, the mere presence of a status field, or a fixed
per-model table). :class:`CapabilityProfile` is the single shape all of those are folded
into by the resolver chain in ``api/capabilities.py``, so the Home Assistant adapter only
ever asks one question: *does this device support X?*

Shape
-----
* ``features``       — normalized capability flags (``"extra_drying"``, ``"eco"``, ``"swing"`` …)
* ``params``         — numbers / lists that qualify a feature (gears, mode subsets, temp range)
* ``present_fields`` — status keys the device has actually reported at least once

``present_fields`` grows monotonically: a truncated or offline status response must never
shrink the entity set, so fields are only ever unioned in.

For the capability dictionary family the payload is a heterogeneous list mixing:

* bare feature tokens — ``"rinse_aid"``, ``"power_wash"``, ``"extra_drying"`` …
* single-key dicts carrying a parameter — ``{"rinse_aid_gear": "5"}``, ``{"salt_gear": "6"}``,
  ``{"alternate_wash": ["upper", "lower", "upper_and_lower"]}``
* the mode table — ``{"mode": [...names...], "modelValue": [{"auto": "1"}, ...]}``
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Final

# Bumped whenever the resolvers or normalization tables change in a way that makes a cached
# profile wrong. The adapter re-resolves any cached profile with a lower version.
CAPABILITY_SCHEMA_VERSION: Final = 2

_EMPTY: Final[MappingProxyType] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class CapabilityProfile:
    """Flattened, queryable view of what one model supports."""

    model: str
    features: frozenset[str]
    params: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    device_type: str = ""
    present_fields: frozenset[str] = frozenset()
    sources: tuple[str, ...] = ()
    fetched_at: float = 0.0
    schema_version: int = CAPABILITY_SCHEMA_VERSION

    # -- queries -------------------------------------------------------------------------
    def supports(self, feature: str) -> bool:
        """True if the model advertises ``feature`` — or reports it as a status field.

        Falling back to ``present_fields`` lets a descriptor gate directly on a raw status
        key (``"probeTemp"``, ``"complianceDose"``) for families that have no capability
        negotiation at all, without a second gating vocabulary.
        """
        return feature in self.features or feature in self.present_fields

    def has_field(self, key: str) -> bool:
        """True if the device has reported ``key`` in its status at least once."""
        return key in self.present_fields

    def gear_max(self, feature: str) -> int | None:
        """Return the max gear for e.g. ``rinse_aid`` / ``salt`` if the model exposes it."""
        value = self.params.get(f"{feature}_gear")
        return value if isinstance(value, int) else None

    def mode_codes(self) -> dict[str, str]:
        """Return the ``mode-name -> cloud-code`` map, empty if the model has no modes."""
        codes = self.params.get("mode_codes")
        return dict(codes) if isinstance(codes, dict) else {}

    def option_values(self, feature: str) -> list[str]:
        """Return the list of option strings for a list-valued feature (e.g. alternate_wash)."""
        value = self.params.get(feature)
        return list(value) if isinstance(value, list) else []

    def param(self, key: str, default: Any = None) -> Any:
        """Raw parameter access for platform code that needs a qualified value."""
        return self.params.get(key, default)

    def mode_subset(self) -> list[str]:
        """Programs this specific model offers; empty means "no restriction"."""
        value = self.params.get("mode_subset")
        return [str(item) for item in value] if isinstance(value, list) else []

    def temperature_range(self, default: tuple[float, float]) -> tuple[float, float]:
        """Return ``(min, max)`` target temperature, falling back to ``default``."""
        low = self.params.get("temp_min")
        high = self.params.get("temp_max")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)) and low < high:
            return (float(low), float(high))
        return default

    def is_stale(self, now: float, ttl: float) -> bool:
        """True if the profile predates the schema in use or is older than ``ttl`` seconds."""
        if self.schema_version < CAPABILITY_SCHEMA_VERSION:
            return True
        return not self.fetched_at or (now - self.fetched_at) >= ttl

    def fingerprint(self) -> str:
        """Stable digest of the *gating-relevant* content (features + params).

        Status-field discovery is tracked separately, because the adapter decides on its own
        whether a newly seen field actually gates anything.
        """
        canonical = json.dumps(
            {
                "device_type": self.device_type,
                "model": self.model,
                "features": sorted(self.features),
                "params": _jsonable(dict(self.params)),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]

    # -- combination ---------------------------------------------------------------------
    def merge(self, other: CapabilityProfile) -> CapabilityProfile:
        """Fold another profile on top of this one (``other`` wins on conflicting params)."""
        params = dict(self.params)
        params.update(other.params)
        return CapabilityProfile(
            model=other.model or self.model,
            features=self.features | other.features,
            params=MappingProxyType(params),
            device_type=other.device_type or self.device_type,
            present_fields=self.present_fields | other.present_fields,
            sources=tuple(dict.fromkeys(self.sources + other.sources)),
            fetched_at=max(self.fetched_at, other.fetched_at),
            schema_version=CAPABILITY_SCHEMA_VERSION,
        )

    def with_fields(self, keys: Iterable[str]) -> CapabilityProfile:
        """Return a profile whose ``present_fields`` also contains ``keys`` (monotonic)."""
        merged = self.present_fields | frozenset(str(key) for key in keys)
        if merged == self.present_fields:
            return self
        return replace(self, present_fields=merged)

    # -- persistence ---------------------------------------------------------------------
    def to_cache(self) -> dict[str, Any]:
        """A JSON-serializable form for persisting the profile locally."""
        return {
            "features": sorted(self.features),
            "params": _jsonable(dict(self.params)),
            "device_type": self.device_type,
            "present_fields": sorted(self.present_fields),
            "sources": list(self.sources),
            "fetched_at": self.fetched_at,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_cache(cls, model: str, data: Mapping[str, Any]) -> CapabilityProfile:
        """Rebuild a profile previously produced by :meth:`to_cache`.

        Tolerates records written by older versions (which only stored features/params);
        those come back with ``schema_version`` 0 and are therefore treated as stale.
        """
        return cls(
            model=model,
            features=frozenset(data.get("features") or ()),
            params=MappingProxyType(dict(data.get("params") or {})),
            device_type=str(data.get("device_type") or ""),
            present_fields=frozenset(data.get("present_fields") or ()),
            sources=tuple(data.get("sources") or ()),
            fetched_at=float(data.get("fetched_at") or 0.0),
            schema_version=int(data.get("schema_version") or 0),
        )


def empty_profile(
    *, model: str = "", device_type: str = "", fetched_at: float = 0.0
) -> CapabilityProfile:
    """A profile that advertises nothing — the safe starting point of a resolver chain."""
    return CapabilityProfile(
        model=model,
        features=frozenset(),
        params=_EMPTY,
        device_type=device_type,
        fetched_at=fetched_at,
    )


def parse_capability(
    items: list[Any],
    *,
    model: str = "",
    device_type: str = "",
    extra_params: Mapping[str, Any] | None = None,
    fetched_at: float = 0.0,
) -> CapabilityProfile:
    """Build a :class:`CapabilityProfile` from a decoded capability-dictionary list."""
    features: set[str] = set()
    params: dict[str, Any] = {}

    for item in items:
        if isinstance(item, str):
            features.add(item)
        elif isinstance(item, dict):
            for key, value in item.items():
                features.add(key)
                params[key] = value

    # Fold the parallel mode / modelValue arrays into a single name -> code map.
    model_values = params.get("modelValue")
    if isinstance(model_values, list):
        codes: dict[str, str] = {}
        for entry in model_values:
            if isinstance(entry, dict):
                codes.update({str(k): str(v) for k, v in entry.items()})
        params["mode_codes"] = codes

    # Coerce "<feature>_gear" strings to ints so gear_max() is numeric.
    for key, value in list(params.items()):
        if key.endswith("_gear") and isinstance(value, str) and value.isdigit():
            params[key] = int(value)

    if extra_params:
        params.update(extra_params)

    return CapabilityProfile(
        model=model,
        features=frozenset(features),
        params=MappingProxyType(params),
        device_type=device_type,
        fetched_at=fetched_at,
    )


def _jsonable(value: Any) -> Any:
    """Best-effort conversion of nested mappings/sequences to plain JSON types."""
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_jsonable(v) for v in value]
        return sorted(items, key=str) if isinstance(value, (set, frozenset)) else items
    return value
