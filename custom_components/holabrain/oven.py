"""Oven — composite programme composer.

Home Assistant has no oven platform, and the cloud API does not accept the cooking
parameters one at a time: mode, temperature, duration, food-probe target and pre-heat are
only meaningful together, submitted as a single cook instruction.

The controls here therefore *stage* their values on the coordinator (a per-device draft)
and the **start** button submits them. Until something is staged, every control shows what
the appliance reports, so the entities also work as a read-back of a cycle started on the
appliance itself. Drafts hold UI units (minutes, °C); :func:`build_cook_instruction`
converts them to the wire format.

Run control (resume / pause / stop / standby) and the child lock are plain instructions and
stay declarative in :mod:`registry`.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import Platform, UnitOfTemperature, UnitOfTime
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .coordinator import HolabrainCoordinator
from .entity import HolabrainEntity
from .helpers import iter_configs, supports
from .registry import OvenConfig, OvenProgram

# Step parameters the appliance expects on every cook instruction; their *values* are
# configurable per category, the key names are fixed by the cloud API.
_KEY_FIRE = "fire"
_KEY_SIZE = "size"
_KEY_NEXT_ACTION = "nextAction"

# Capability flags and profile parameters this category understands.
CAP_PREHEAT = "preheat"
CAP_FOOD_PROBE = "food_probe"
PARAM_NON_DEGREE = "non_degree_modes"
PARAM_NON_PREHEAT = "non_preheat_modes"


def build_cook_instruction(
    cfg: OvenConfig,
    program: OvenProgram,
    *,
    temperature: int,
    minutes: int,
    probe: int | None,
    preheat: bool,
    with_temperature: bool | None = None,
    with_preheat: bool | None = None,
) -> dict[str, Any]:
    """Assemble the single-programme cook instruction from staged values.

    ``with_temperature`` / ``with_preheat`` let the caller apply the model's own programme
    exclusions; they default to what the programme itself declares.
    """
    include_temp = program.supports_temp if with_temperature is None else with_temperature
    allow_preheat = program.supports_preheat if with_preheat is None else with_preheat
    step: dict[str, str] = {
        cfg.preheat_key: "1" if preheat and allow_preheat else "0",
        cfg.mode_key: program.code,
        cfg.time_key: str(max(minutes, 0) * 60),
        _KEY_FIRE: cfg.fire,
        _KEY_SIZE: cfg.size,
        _KEY_NEXT_ACTION: cfg.next_action,
    }
    if include_temp:
        # Time-only programmes must not carry temperature keys at all.
        step[cfg.temp_key] = str(temperature)
        step[cfg.lower_temp_key] = str(temperature)
        step[cfg.probe_key] = str(temperature if probe is None else probe)
    return {"currentCook": "1", "totalCook": "1", "menu": [step]}


def build_oven_entities(
    coordinator: HolabrainCoordinator, platform: Platform, seen: set[str] | None = None
) -> list[Entity]:
    """Build the programme-composer entities of one platform for every oven.

    ``seen`` skips ovens this platform already covers, so the builder can be re-run when the
    account gains a device.
    """
    entities: list[Entity] = []
    for thing_code, cfg in iter_configs(coordinator, "oven"):
        if seen is not None and thing_code in seen:
            continue
        if platform is Platform.SELECT:
            entities.append(HolabrainOvenMode(coordinator, thing_code, cfg))
        elif platform is Platform.NUMBER:
            entities.append(HolabrainOvenTemperature(coordinator, thing_code, cfg))
            entities.append(HolabrainOvenDuration(coordinator, thing_code, cfg))
            # The food probe is optional hardware: offer its target only where reported.
            if supports(coordinator, thing_code, CAP_FOOD_PROBE):
                entities.append(HolabrainOvenProbe(coordinator, thing_code, cfg))
        elif platform is Platform.SWITCH:
            if supports(coordinator, thing_code, CAP_PREHEAT):
                entities.append(HolabrainOvenPreheat(coordinator, thing_code, cfg))
        elif platform is Platform.BUTTON:
            entities.append(HolabrainOvenStart(coordinator, thing_code, cfg))
    return entities


class _OvenEntity(HolabrainEntity):
    """Shared programme lookup and staged-value access."""

    def __init__(
        self,
        coordinator: HolabrainCoordinator,
        thing_code: str,
        cfg: OvenConfig,
        key: str,
        uid: str,
    ) -> None:
        super().__init__(coordinator, thing_code, key, uid=uid)
        self._cfg = cfg
        self._by_code = {program.code: label for label, program in cfg.programs.items()}

    @property
    def _staged(self) -> dict[str, str]:
        return self.coordinator.draft(self._thing_code)

    def _staged_int(self, key: str) -> int | None:
        """The staged value for ``key``, falling back to what the appliance reports."""
        raw = self._staged.get(key, self._value(key))
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return None

    @property
    def _label(self) -> str | None:
        """Label of the programme being composed: staged if chosen, else the appliance's."""
        code = self._staged.get(self._cfg.mode_key, self._value(self._cfg.mode_key))
        return self._by_code.get(str(code))

    @property
    def _program(self) -> OvenProgram | None:
        label = self._label
        return self._cfg.programs.get(label) if label else None

    def _mode_allows(self, param: str, fallback: bool) -> bool:
        """Programme gate; the model's own exclusion list wins when the profile carries one."""
        profile = self._capability
        excluded = profile.param(param) if profile is not None else None
        label = self._label
        if isinstance(excluded, list) and label is not None:
            return label not in excluded
        return fallback

    @property
    def _temp_allowed(self) -> bool:
        program = self._program
        return program is not None and self._mode_allows(
            PARAM_NON_DEGREE, program.supports_temp
        )

    @property
    def _preheat_allowed(self) -> bool:
        program = self._program
        return program is not None and self._mode_allows(
            PARAM_NON_PREHEAT, program.supports_preheat
        )

    @property
    def _minutes(self) -> int:
        """Effective duration in minutes: staged (minutes) or reported (seconds)."""
        staged = self._staged.get(self._cfg.time_key)
        if staged is not None:
            return _to_int(staged) or 0
        seconds = _to_int(self._value(self._cfg.time_key))
        return self._cfg.default_minutes if seconds is None else seconds // 60

    @property
    def _preheat_on(self) -> bool | None:
        raw = self._staged.get(self._cfg.preheat_key, self._value(self._cfg.preheat_key))
        return None if raw is None else str(raw) == "1"

    def _stage(self, values: dict[str, str]) -> None:
        self.coordinator.async_stage(self._thing_code, values)


class HolabrainOvenMode(_OvenEntity, SelectEntity):
    """The cooking programme; picking one also seeds its default temperature."""

    _attr_translation_key = "cook_mode"

    def __init__(self, coordinator, thing_code: str, cfg: OvenConfig) -> None:
        super().__init__(coordinator, thing_code, cfg, cfg.mode_key, "oven_mode")
        self._attr_options = list(cfg.programs)

    @property
    def current_option(self) -> str | None:
        return self._label

    async def async_select_option(self, option: str) -> None:
        program = self._cfg.programs.get(option)
        if program is None:
            return
        staged = {self._cfg.mode_key: program.code}
        if program.supports_temp:
            # Ranges differ per programme; start from a setpoint that is always in range.
            staged[self._cfg.temp_key] = str(program.default_temp)
        self._stage(staged)


class HolabrainOvenTemperature(_OvenEntity, NumberEntity):
    """Target temperature, bounded by the selected programme."""

    _attr_translation_key = "target_temperature"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, thing_code: str, cfg: OvenConfig) -> None:
        super().__init__(coordinator, thing_code, cfg, cfg.temp_key, "oven_temperature")

    @property
    def available(self) -> bool:
        # Time-only programmes (e.g. defrost) take no temperature at all.
        return super().available and self._temp_allowed

    @property
    def native_min_value(self) -> float:
        program = self._program
        return program.min_temp if program else 30

    @property
    def native_max_value(self) -> float:
        program = self._program
        return program.max_temp if program else 250

    @property
    def native_step(self) -> float:
        program = self._program
        return program.temp_step if program else 5

    @property
    def native_value(self) -> float | None:
        value = self._staged_int(self._cfg.temp_key)
        if value is not None:
            return value
        program = self._program
        return program.default_temp if program else None

    async def async_set_native_value(self, value: float) -> None:
        self._stage({self._cfg.temp_key: str(int(value))})


class HolabrainOvenDuration(_OvenEntity, NumberEntity):
    """Cook duration in minutes; the instruction carries seconds."""

    _attr_translation_key = "cook_time"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 0
    _attr_native_step = 1

    def __init__(self, coordinator, thing_code: str, cfg: OvenConfig) -> None:
        super().__init__(coordinator, thing_code, cfg, cfg.time_key, "oven_duration")

    @property
    def native_max_value(self) -> float:
        program = self._program
        return program.max_minutes if program else 300

    @property
    def native_value(self) -> float | None:
        return self._minutes

    async def async_set_native_value(self, value: float) -> None:
        self._stage({self._cfg.time_key: str(int(value))})


class HolabrainOvenProbe(_OvenEntity, NumberEntity):
    """Food-probe target temperature (probe-equipped models only)."""

    _attr_translation_key = "probe_temperature"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = 1

    def __init__(self, coordinator, thing_code: str, cfg: OvenConfig) -> None:
        super().__init__(coordinator, thing_code, cfg, cfg.probe_key, "oven_probe")
        self._attr_native_min_value = cfg.probe_min_temp
        self._attr_native_max_value = cfg.probe_max_temp

    @property
    def available(self) -> bool:
        return super().available and self._temp_allowed

    @property
    def native_value(self) -> float | None:
        value = self._staged_int(self._cfg.probe_key)
        return self._cfg.probe_default_temp if value is None else value

    async def async_set_native_value(self, value: float) -> None:
        self._stage({self._cfg.probe_key: str(int(value))})


class HolabrainOvenPreheat(_OvenEntity, SwitchEntity):
    """Whether the cycle starts with a pre-heat phase."""

    _attr_translation_key = "preheat"

    def __init__(self, coordinator, thing_code: str, cfg: OvenConfig) -> None:
        super().__init__(coordinator, thing_code, cfg, cfg.preheat_key, "oven_preheat")

    @property
    def available(self) -> bool:
        # Low-temperature and defrost programmes never pre-heat.
        return super().available and self._preheat_allowed

    @property
    def is_on(self) -> bool | None:
        return self._preheat_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._stage({self._cfg.preheat_key: "1"})

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._stage({self._cfg.preheat_key: "0"})


class HolabrainOvenStart(_OvenEntity, ButtonEntity):
    """Submit the composed programme as one cook instruction."""

    _attr_translation_key = "start"

    def __init__(self, coordinator, thing_code: str, cfg: OvenConfig) -> None:
        super().__init__(coordinator, thing_code, cfg, cfg.mode_key, "oven_start")

    async def async_press(self) -> None:
        program = self._program
        if program is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_cook_mode"
            )
        instruction = build_cook_instruction(
            self._cfg,
            program,
            temperature=self._staged_int(self._cfg.temp_key) or program.default_temp,
            minutes=self._minutes,
            probe=self._staged_int(self._cfg.probe_key),
            preheat=bool(self._preheat_on),
            with_temperature=self._temp_allowed,
            with_preheat=self._preheat_allowed,
        )
        await self._async_send(instruction)
        # The appliance now owns the programme: drop the draft and read the result back.
        self.coordinator.async_clear_draft(self._thing_code)
        await self.coordinator.async_request_refresh()


def _to_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
