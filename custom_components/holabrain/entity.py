"""Base entity shared by every HolaBrain platform."""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .aiodollin import CapabilityProfile, Device, DeviceState
from .conditions import KEY_STAGED, KEY_STATE, NO_GATES, Gates, holds_or
from .const import DOMAIN
from .coordinator import HolabrainCoordinator
from .registry import get_category


def _fallback_name(device: Device) -> str:
    """A readable device name for an appliance the account has no name for."""
    category = get_category(device.device_type)
    if category is not None:
        return category.category.replace("_", " ").capitalize()
    return device.model or "HolaBrain appliance"


class _EntityGateContext:
    """Answers a condition's questions about one entity's device.

    Status keys come from the snapshot, ``@state`` from the category's state machine (the
    coordinator resolves it once per snapshot rather than once per entity), and
    ``@staged:`` from the composer draft — a value the user has chosen but not submitted.
    """

    __slots__ = ("_entity",)

    def __init__(self, entity: HolabrainEntity) -> None:
        self._entity = entity

    def value(self, key: str) -> str | None:
        entity = self._entity
        if key == KEY_STATE:
            return entity.coordinator.machine_state(entity._thing_code)
        if key.startswith(KEY_STAGED):
            raw = entity.coordinator.draft(entity._thing_code).get(key[len(KEY_STAGED) :])
        else:
            raw = entity._value(key)
        return None if raw is None else str(raw)

    def program_allows(self, flag: str, exclusion_param: str | None) -> bool:
        return self._entity._program_allows(flag, exclusion_param)


class HolabrainEntity(CoordinatorEntity[HolabrainCoordinator]):
    """Common device_info, availability and value access for all entities."""

    _attr_has_entity_name = True

    #: Set by platforms from the descriptor. Says when this entity's reading carries no
    #: meaning, and which writes the appliance would refuse.
    _gates: Gates = NO_GATES

    def __init__(
        self,
        coordinator: HolabrainCoordinator,
        thing_code: str,
        key: str,
        uid: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._thing_code = thing_code
        self._key = key
        device = coordinator.devices[thing_code]
        # ``uid`` separates entities that read the same status key (e.g. two pre-heat flags).
        self._attr_unique_id = f"{thing_code}_{uid or key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, thing_code)},
            # An appliance added by the vendor app without being named comes back with an
            # empty name; with ``has_entity_name`` that would produce entities called
            # " door" rather than "Dishwasher door".
            name=device.name or _fallback_name(device),
            manufacturer="HolaBrain",
            model=device.model or None,
            sw_version=device.firmware_version or None,
        )

    @property
    def _state(self) -> DeviceState | None:
        return (self.coordinator.data or {}).get(self._thing_code)

    @property
    def _capability(self) -> CapabilityProfile | None:
        return self.coordinator.capability_for(self._thing_code)

    @property
    def available(self) -> bool:
        state = self._state
        return super().available and state is not None and state.online

    def _value(self, key: str | None = None) -> Any:
        state = self._state
        if state is None:
            return None
        return state.get(key or self._key)

    # -- state gates -------------------------------------------------------------------
    @property
    def _gate_context(self) -> _EntityGateContext:
        return _EntityGateContext(self)

    @property
    def _is_meaningful(self) -> bool:
        """Whether this entity's reading means anything in the appliance's current state.

        A switched-off dishwasher keeps reporting the last cycle's programme and remaining
        time; the vendor's own app refuses to show them. Platforms report ``None`` here so
        Home Assistant says "unknown" rather than presenting a leftover as live.

        Unknown falls open: a push frame carries a subset of the appliance's fields, and a
        frame that happens to omit the state keys must not blank half the device page.
        """
        return holds_or(self._gates.meaningful_when, self._gate_context, default=True)

    def _program_allows(self, flag: str, exclusion_param: str | None) -> bool:
        """Whether the composed programme accepts ``flag``. Composers override this."""
        return True

    def _check_writable(self) -> None:
        """Refuse a write the appliance is currently guaranteed to reject.

        The category's guard runs first and in order — the vendor plugins have exactly one
        such component per appliance, and its order is what decides which reason the user
        is told. A control opts out of individual reasons through ``Gates.exempt``: the
        power control must stay usable precisely when the appliance is off or faulted.

        Refusing is deliberate rather than marking the entity unavailable: Home Assistant
        drops unavailable entities from a service call's targets, so an automation would be
        told it succeeded while nothing happened.
        """
        category = get_category(self.coordinator.devices[self._thing_code].device_type)
        guard = category.guard if category is not None else ()
        context = self._gate_context
        for block in (*guard, *self._gates.blocks):
            if block.reason in self._gates.exempt:
                continue
            # Unknown never refuses: an incomplete frame is not evidence of a problem.
            if holds_or(block.when, context, default=False):
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key=block.reason
                )

    async def _async_send(self, instruction: dict[str, str]) -> None:
        await self.coordinator.async_send_instruction(self._thing_code, instruction)
