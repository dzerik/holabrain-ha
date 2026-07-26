"""Select platform — generic, driven by the category registry.

Options can be static (``spec.options`` label→code) or resolved from the model's capability
profile (``spec.options_from_capability``), so a model only ever offers programs it has.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolabrainConfigEntry
from .dishwasher import build_dishwasher_entities
from .entity import HolabrainEntity
from .helpers import async_add_with_discovery, build_entities
from .oven import build_oven_entities
from .registry import SelectSpec

# The coordinator owns every read, and writes are single instructions the cloud
# serialises anyway; entities never fan out requests of their own.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HolabrainConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_with_discovery(
        entry,
        coordinator,
        async_add_entities,
        lambda seen: (
            build_entities(coordinator, "selects", HolabrainSelect, seen)
            + build_oven_entities(coordinator, Platform.SELECT, seen)
            + build_dishwasher_entities(coordinator, Platform.SELECT, seen)
        ),
    )


class HolabrainSelect(HolabrainEntity, SelectEntity):
    """A control that maps a set of labels onto cloud codes for one instruction key."""

    def __init__(self, coordinator, thing_code: str, spec: SelectSpec) -> None:
        super().__init__(coordinator, thing_code, spec.key)
        self._spec = spec
        self._attr_translation_key = spec.translation_key
        self._attr_entity_category = spec.entity_category
        self._label_to_code = self._resolve_options()
        self._code_to_label = {code: label for label, code in self._label_to_code.items()}
        self._attr_options = list(self._label_to_code)

    def _resolve_options(self) -> dict[str, str]:
        source = self._spec.options_from_capability
        if source:
            profile = self._capability
            if profile is None:
                return {}
            if source == "mode":
                return dict(profile.mode_codes())
            return {value: value for value in profile.option_values(source)}
        return dict(self._spec.options)

    @property
    def current_option(self) -> str | None:
        raw = self._value()
        return self._code_to_label.get(str(raw)) if raw is not None else None

    async def async_select_option(self, option: str) -> None:
        code = self._label_to_code.get(option)
        if code is None:
            return
        instruction = {self._spec.command_key: code, **self._spec.extra_command}
        await self._async_send(instruction)
