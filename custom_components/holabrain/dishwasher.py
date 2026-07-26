"""Dishwasher cycle composer.

Starting a wash is not a single toggle: the appliance accepts the programme, the extra
option, the wash zone and the run state as **one** instruction. Sending them separately does
nothing useful — an intermediate command either has no effect or is refused.

So the controls stage their values locally (in the coordinator's per-device draft) and the
*start* button submits them together, the same way the oven composes a cook. Everything the
model does not advertise is simply absent: a machine without an upper/lower option has no
zone control, and only the extras it supports appear in the list.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.components.select import SelectEntity
from homeassistant.const import Platform
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .coordinator import HolabrainCoordinator
from .entity import HolabrainEntity
from .helpers import iter_configs, supports
from .registry import DishwasherConfig

# The appliance refuses a start while the door is open, and the status reports it.
DOOR_KEY = "doorstatus"
DOOR_OPEN = "0"


def build_start_instruction(
    cfg: DishwasherConfig,
    *,
    program_code: str,
    extra_code: str,
    zone_code: str | None,
) -> dict[str, str]:
    """Assemble the one instruction that starts a wash.

    ``zone_code`` is ``None`` on models without the upper/lower option; the key is then left
    out entirely rather than sent with a default, because an unsupported key is rejected.
    """
    instruction = {
        cfg.mode_key: program_code,
        cfg.extra_key: extra_code,
        cfg.power_key: cfg.start_value,
    }
    if zone_code is not None:
        instruction[cfg.zone_key] = zone_code
    return instruction


def build_dishwasher_entities(
    coordinator: HolabrainCoordinator, platform: Platform, seen: set[str] | None = None
) -> list[Entity]:
    """Build the cycle-composer entities of one platform for every dishwasher."""
    entities: list[Entity] = []
    for thing_code, cfg in iter_configs(coordinator, "dishwasher"):
        if seen is not None and thing_code in seen:
            continue
        if platform is Platform.SELECT:
            entities.append(HolabrainDishwasherProgram(coordinator, thing_code, cfg))
            if _available_extras(coordinator, thing_code, cfg):
                entities.append(HolabrainDishwasherExtra(coordinator, thing_code, cfg))
            if supports(coordinator, thing_code, cfg.zone_capability):
                entities.append(HolabrainDishwasherZone(coordinator, thing_code, cfg))
        elif platform is Platform.BUTTON:
            entities.append(HolabrainDishwasherStart(coordinator, thing_code, cfg))
    return entities


def _available_extras(
    coordinator: HolabrainCoordinator, thing_code: str, cfg: DishwasherConfig
) -> dict[str, str]:
    """Extra options this model advertises, as ``label -> code``."""
    return {
        label: code
        for label, (code, capability) in cfg.extras.items()
        if supports(coordinator, thing_code, capability)
    }


class _DishwasherEntity(HolabrainEntity):
    """Shared access to the staged cycle."""

    def __init__(
        self,
        coordinator: HolabrainCoordinator,
        thing_code: str,
        cfg: DishwasherConfig,
        key: str,
        uid: str,
    ) -> None:
        super().__init__(coordinator, thing_code, key, uid=uid)
        self._cfg = cfg

    @property
    def _staged(self) -> dict[str, str]:
        return self.coordinator.draft(self._thing_code)

    def _staged_or_reported(self, key: str) -> str | None:
        raw = self._staged.get(key, self._value(key))
        return None if raw is None else str(raw)

    def _stage(self, values: dict[str, str]) -> None:
        self.coordinator.async_stage(self._thing_code, values)

    def _programs(self) -> dict[str, str]:
        """Programmes this model offers.

        The capability profile carries the model's own programme table, which is a subset of
        the category's: offering a programme the appliance does not have would produce a
        command it refuses.
        """
        profile = self._capability
        codes = profile.mode_codes() if profile is not None else {}
        available = {
            label: code for label, code in codes.items() if label in self._cfg.programs
        }
        return available or dict(self._cfg.programs)


class HolabrainDishwasherProgram(_DishwasherEntity, SelectEntity):
    """The wash programme to run."""

    _attr_translation_key = "program_select"

    def __init__(self, coordinator, thing_code: str, cfg: DishwasherConfig) -> None:
        super().__init__(coordinator, thing_code, cfg, cfg.mode_key, "program_select")

    @property
    def options(self) -> list[str]:
        return list(self._programs())

    @property
    def current_option(self) -> str | None:
        code = self._staged_or_reported(self._cfg.mode_key)
        if code is None:
            return None
        for label, value in self._programs().items():
            if value == code:
                return label
        return None

    async def async_select_option(self, option: str) -> None:
        code = self._programs().get(option)
        if code is not None:
            self._stage({self._cfg.mode_key: code})


class HolabrainDishwasherExtra(_DishwasherEntity, SelectEntity):
    """The extra option to run the programme with.

    The appliance takes one value here rather than a set of flags, so this is a choice, not
    a group of switches. ``none`` is always available — it is how you clear the option.
    """

    _attr_translation_key = "extra_option"

    def __init__(self, coordinator, thing_code: str, cfg: DishwasherConfig) -> None:
        super().__init__(coordinator, thing_code, cfg, cfg.extra_key, "extra_option")

    def _choices(self) -> dict[str, str]:
        return {
            "none": self._cfg.no_extra_code,
            **_available_extras(self.coordinator, self._thing_code, self._cfg),
        }

    @property
    def options(self) -> list[str]:
        return list(self._choices())

    @property
    def current_option(self) -> str | None:
        code = self._staged_or_reported(self._cfg.extra_key)
        if code is None:
            return None
        for label, value in self._choices().items():
            if value == code:
                return label
        # A code the model never advertised is reported as nothing rather than as a lie.
        return None

    async def async_select_option(self, option: str) -> None:
        code = self._choices().get(option)
        if code is not None:
            self._stage({self._cfg.extra_key: code})


class HolabrainDishwasherZone(_DishwasherEntity, SelectEntity):
    """Which basket to wash — only on models with the alternating-wash option."""

    _attr_translation_key = "wash_zone"

    def __init__(self, coordinator, thing_code: str, cfg: DishwasherConfig) -> None:
        super().__init__(coordinator, thing_code, cfg, cfg.zone_key, "wash_zone")
        self._attr_options = list(cfg.zones)

    @property
    def current_option(self) -> str | None:
        code = self._staged_or_reported(self._cfg.zone_key)
        if code is None:
            return None
        for label, value in self._cfg.zones.items():
            if value == code:
                return label
        return None

    async def async_select_option(self, option: str) -> None:
        code = self._cfg.zones.get(option)
        if code is not None:
            self._stage({self._cfg.zone_key: code})


class HolabrainDishwasherStart(_DishwasherEntity, ButtonEntity):
    """Submit the composed cycle as the one instruction that starts a wash."""

    _attr_translation_key = "start_cycle"

    def __init__(self, coordinator, thing_code: str, cfg: DishwasherConfig) -> None:
        super().__init__(coordinator, thing_code, cfg, cfg.mode_key, "start_cycle")

    async def async_press(self) -> None:
        program_code = self._staged_or_reported(self._cfg.mode_key)
        if program_code is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_program"
            )
        if str(self._value(DOOR_KEY)) == DOOR_OPEN:
            # The appliance refuses to start with the door open; saying so is far better
            # than a generic cloud error a few seconds later.
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="door_open"
            )

        zone_code = None
        if supports(self.coordinator, self._thing_code, self._cfg.zone_capability):
            zone_code = self._staged_or_reported(self._cfg.zone_key) or self._cfg.default_zone

        await self._async_send(
            build_start_instruction(
                self._cfg,
                program_code=program_code,
                extra_code=self._staged_or_reported(self._cfg.extra_key)
                or self._cfg.no_extra_code,
                zone_code=zone_code,
            )
        )
        # The appliance owns the cycle now: drop the draft and read the result back.
        self.coordinator.async_clear_draft(self._thing_code)
        await self.coordinator.async_request_refresh()
