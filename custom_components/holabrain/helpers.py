"""Shared platform-setup helpers.

Every platform builds its entities the same way: walk the account's devices, look up the
category spec, drop any descriptor whose capability gate the model does not satisfy, and
hand the survivors to a platform-specific factory.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HolabrainCoordinator
from .registry import CategorySpec, get_category

_Spec = TypeVar("_Spec")


def iter_native(
    coordinator: HolabrainCoordinator, platform: Platform
) -> Iterator[tuple[str, CategorySpec]]:
    """Yield ``(thing_code, category)`` for devices whose native platform is ``platform``."""
    for thing_code, device in coordinator.devices.items():
        category = get_category(device.device_type)
        if category is not None and category.primary_platform == platform:
            yield thing_code, category


def iter_configs(
    coordinator: HolabrainCoordinator, attribute: str
) -> Iterator[tuple[str, Any]]:
    """Yield ``(thing_code, config)`` for devices whose category defines ``attribute``.

    Used by composite categories (e.g. ``oven``) whose controls span several platforms.
    """
    for thing_code, device in coordinator.devices.items():
        category = get_category(device.device_type)
        config = getattr(category, attribute, None) if category is not None else None
        if config is not None:
            yield thing_code, config


def supports(coordinator: HolabrainCoordinator, thing_code: str, feature: str) -> bool:
    """Whether the device's resolved profile advertises ``feature``.

    Unknown capabilities (no profile) count as unsupported: better absent than broken.
    """
    profile = coordinator.capability_for(thing_code)
    return profile is not None and profile.supports(feature)


def _is_gated_out(spec: Any, coordinator: HolabrainCoordinator, thing_code: str) -> bool:
    capability = getattr(spec, "capability", None)
    return capability is not None and not supports(coordinator, thing_code, capability)


def iter_specs(
    coordinator: HolabrainCoordinator, attribute: str
) -> Iterator[tuple[str, Any]]:
    """Yield ``(thing_code, spec)`` for every capability-satisfied descriptor of a platform."""
    for thing_code, device in coordinator.devices.items():
        category = get_category(device.device_type)
        if category is None:
            continue
        for spec in getattr(category, attribute):
            if _is_gated_out(spec, coordinator, thing_code):
                continue
            yield thing_code, spec


def build_entities(
    coordinator: HolabrainCoordinator,
    attribute: str,
    factory: Callable[[HolabrainCoordinator, str, Any], Entity],
    seen: set[str] | None = None,
) -> list[Entity]:
    """Build the entity list for a platform from its category descriptors.

    ``seen`` holds the devices this platform has already produced entities for. Passing it
    makes the call incremental, which is what lets a device paired after setup appear
    without restarting Home Assistant.
    """
    return [
        factory(coordinator, thing_code, spec)
        for thing_code, spec in iter_specs(coordinator, attribute)
        if seen is None or thing_code not in seen
    ]


@callback
def async_add_with_discovery(
    entry: ConfigEntry,
    coordinator: HolabrainCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    builder: Callable[[set[str]], list[Entity]],
) -> None:
    """Add a platform's entities now, and again whenever the account gains a device.

    ``builder`` receives the set of devices already handled by this platform and must
    return entities only for the others. The set is updated here rather than in each
    builder, so a device that contributes no entity to this platform is still not
    reconsidered on every coordinator update.
    """
    seen: set[str] = set()

    @callback
    def _sync() -> None:
        entities = builder(seen)
        seen.update(coordinator.devices)
        if entities:
            async_add_entities(entities)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))
